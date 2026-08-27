"""Small argparse CLI for the Phase 0/1 workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .config import SOURCE_NAME, Settings, load_settings
from .datasets.sync import sync_sample
from .pit.financial import (
    canonicalize_financial_frame,
    derive_single_quarter,
    query_financial_as_of,
)
from .providers.tushare import TushareProvider
from .storage.parquet import RawParquetStore
from .storage.state import SyncStateStore
from .validation import (
    validate_source,
    write_pit_mapping_report,
    write_validation_report,
)

DEFAULT_CODES = ("000001.SZ", "600000.SH", "300001.SZ", "688001.SH")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ashare_turnaround")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight", help="show local configuration without secrets")

    validate = subparsers.add_parser("validate-source", help="run the small API validation matrix")
    validate.add_argument("--sample-code", default="600000.SH")
    validate.add_argument("--vip", action="store_true", help="also test *_vip API names")
    validate.add_argument("--report", default="docs/data-source-validation.md")

    sync = subparsers.add_parser("sync-sample", help="fetch a bounded 3-5 code sample")
    sync.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    sync.add_argument("--start-date", default="20240101")
    sync.add_argument("--end-date", default="20241231")
    sync.add_argument("--limit", type=int, default=100)
    sync.add_argument("--max-pages", type=int, default=1)

    subparsers.add_parser("pit-check", help="run synthetic and available-sample PIT checks")
    return parser


def _settings_without_secret(settings: Settings) -> str:
    base = "configured" if settings.base_url else "official-default"
    return (
        f"data_dir={settings.data_dir}\n"
        f"token_configured={settings.token_configured}\n"
        f"base_url={base}\n"
        f"timeout={settings.timeout}\n"
        f"max_retries={settings.max_retries}"
    )


def _preflight(_: argparse.Namespace) -> int:
    settings = load_settings()
    print(_settings_without_secret(settings))
    print("provider_client=official tushare Python SDK")
    print("mcp_data_chain=false")
    return 0


def _validate_source(args: argparse.Namespace) -> int:
    settings = load_settings()
    report = validate_source(
        settings,
        sample_code=args.sample_code,
        include_vip=args.vip,
    )
    report_path = Path(args.report)
    write_validation_report(report, report_path)
    write_pit_mapping_report(report, Path("docs/pit-field-mapping.md"))
    print(f"validation_report={report_path}")
    for result in report.results:
        print(
            f"{result.api}: {result.status} rows={result.rows} "
            f"duration={result.duration_seconds:.3f}s"
        )
    if report.core_failures:
        print(
            "core API validation blocked: do not proceed to historical synchronization",
            file=sys.stderr,
        )
        return 2
    return 0


def _sync_sample(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.token_configured:
        print("TUSHARE_TOKEN is not configured; sync-sample was not run", file=sys.stderr)
        return 2
    codes = tuple(args.codes)
    if not 3 <= len(codes) <= 5:
        print("sync-sample requires 3 to 5 codes", file=sys.stderr)
        return 2
    settings.ensure_data_dirs()
    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
    )
    store = RawParquetStore(settings.data_dir)
    state = SyncStateStore(settings.data_dir / "state" / "sync-log.json", secret=settings.token)
    summary = sync_sample(
        provider,
        store,
        state,
        codes=codes,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        max_pages=args.max_pages,
    )
    failed = [result for result in summary.results if result.status == "failed"]
    print(
        f"requests={len(summary.results)} failed={len(failed)} "
        f"stored_files={len(summary.stored_files)}"
    )
    for path in summary.stored_files:
        print(f"stored={path.path} rows={path.rows} bytes={path.size_bytes}")
    return 2 if failed and not summary.stored_files else 0


def _synthetic_pit_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "ann_date": "20260320",
                "f_ann_date": "20260320",
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "1",
                "total_revenue": 100.0,
            },
            {
                "ts_code": "600000.SH",
                "ann_date": "20260415",
                "f_ann_date": "20260415",
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "2",
                "total_revenue": 110.0,
            },
        ]
    )


def _render_pit_check(real_rows: int, synthetic: dict[str, bool]) -> str:
    lines = [
        "# PIT prototype check",
        "",
        "Synthetic version-chain checks are intentionally separate from live-data checks.",
        "",
        f"- Live income rows available: `{real_rows}`",
        "",
        "| Scenario | Synthetic result |",
        "| --- | --- |",
    ]
    labels = {
        "before_first": "公告前不得可见",
        "after_first": "首次公告后可见首次版本",
        "before_revision": "修订前不得可见修订版本",
        "after_revision": "修订后可见修订版本",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {'PASS' if synthetic[key] else 'FAIL'} |")
    lines.extend(
        [
            "",
            "Live checks are reported only when a local sample has been synchronized; "
            "no live rows were available in this run if the count above is zero.",
            "",
        ]
    )
    return "\n".join(lines)


def _pit_check(_: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_data_dirs()
    synthetic_raw = _synthetic_pit_frame()
    canonical = canonicalize_financial_frame("income", synthetic_raw, source=SOURCE_NAME)
    checks = {
        "before_first": query_financial_as_of(
            "income", "600000.SH", "20260301", frame=synthetic_raw
        ).empty,
        "after_first": (
            len(query_financial_as_of("income", "600000.SH", "20260325", frame=synthetic_raw)) == 1
            and float(
                query_financial_as_of("income", "600000.SH", "20260325", frame=synthetic_raw).iloc[
                    0
                ]["total_revenue"]
            )
            == 100.0
        ),
        "before_revision": (
            len(query_financial_as_of("income", "600000.SH", "20260401", frame=synthetic_raw)) == 1
            and float(
                query_financial_as_of("income", "600000.SH", "20260401", frame=synthetic_raw).iloc[
                    0
                ]["total_revenue"]
            )
            == 100.0
        ),
        "after_revision": (
            len(query_financial_as_of("income", "600000.SH", "20260420", frame=synthetic_raw)) == 1
            and float(
                query_financial_as_of("income", "600000.SH", "20260420", frame=synthetic_raw).iloc[
                    0
                ]["total_revenue"]
            )
            == 110.0
        ),
    }
    store = RawParquetStore(settings.data_dir)
    real_frame = store.read("income")
    if not real_frame.empty:
        real_canonical = canonicalize_financial_frame("income", real_frame)
        with_dates = real_canonical.dropna(subset=["actual_available_date"])
        if not with_dates.empty:
            earliest = with_dates["actual_available_date"].min().date()
            print(f"live_income_rows={len(real_canonical)} earliest_available={earliest}")
    else:
        print("live_income_rows=0")
    quarter_frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * 4,
            "end_date": ["20250331", "20250630", "20250930", "20251231"],
            "net_value": [10.0, 23.0, 35.0, 50.0],
        }
    )
    quarterized = derive_single_quarter(quarter_frame, "net_value")
    print(f"canonical_columns={','.join(canonical.columns)}")
    print(f"pit_synthetic={checks}")
    print(f"single_quarter={quarterized['single_quarter'].tolist()}")
    report_path = Path("docs/pit-validation.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_pit_check(len(real_frame), checks), encoding="utf-8")
    return 0 if all(checks.values()) else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "preflight": _preflight,
        "validate-source": _validate_source,
        "sync-sample": _sync_sample,
        "pit-check": _pit_check,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
