"""Full-market VIP period validation before historical bootstrap."""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import SOURCE_NAME, Settings
from ..providers.tushare import ProviderError, TushareProvider
from ..quality import check_frame_quality
from ..storage.parquet import RawParquetStore, StoredFile
from ..storage.state import BootstrapCheckpoint, BootstrapCheckpointStore
from .specs import get_dataset_spec
from .sync import (
    PageAudit,
    PaginatedFetch,
    PaginationError,
    fetch_paginated_audited,
    utc_now,
)

PRODUCTION_PERIOD = "20251231"
PRODUCTION_DATASETS: tuple[str, ...] = (
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)
VIP_ROUTES: dict[str, str] = {
    "income": "income_vip",
    "balancesheet": "balancesheet_vip",
    "cashflow": "cashflow_vip",
    "fina_indicator": "fina_indicator_vip",
    "fina_mainbz": "fina_mainbz_vip",
    "forecast": "forecast_vip",
    # express_vip is deliberately excluded: Phase 1C found update_flag missing.
    "express": "express",
    "fina_audit": "fina_audit",
    "disclosure_date": "disclosure_date",
    "daily": "daily",
    "daily_basic": "daily_basic",
}


@dataclass(frozen=True, slots=True)
class OrdinaryCrossCheck:
    status: str
    requested_codes: tuple[str, ...] = ()
    checked_codes: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionDatasetResult:
    dataset: str
    source_api: str
    period: str
    status: str
    page_count: int
    rows: int
    elapsed_seconds: float
    duplicate_count: int
    schema_hash: str
    first_ts_code: str | None
    last_ts_code: str | None
    schema_hashes: tuple[str, ...] = ()
    schema_drift: bool = False
    pit_fields: tuple[str, ...] = ()
    missing_pit_fields: tuple[str, ...] = ()
    ordinary_cross_check: OrdinaryCrossCheck = field(
        default_factory=lambda: OrdinaryCrossCheck("NOT_RUN")
    )
    stored_files: tuple[StoredFile, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None
    total_rows: int | None = None
    pages: tuple[PageAudit, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionValidationSummary:
    period: str
    generated_at: str
    results: tuple[ProductionDatasetResult, ...]
    sample_size: int
    safe_to_bootstrap: bool

    @property
    def failures(self) -> tuple[ProductionDatasetResult, ...]:
        return tuple(result for result in self.results if result.status != "PASS")


def route_for_dataset(dataset: str) -> str:
    """Return the approved source route; never silently use express_vip."""

    return VIP_ROUTES.get(dataset, dataset)


def _frame_value(value: object) -> str:
    if value is None:
        return "<NULL>"
    try:
        if bool(pd.isna(value)):
            return "<NULL>"
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        if math.isnan(value):
            return "<NULL>"
        return f"{value:.15g}"
    return str(value)


def _row_counter(frame: pd.DataFrame, columns: list[str]) -> Counter[tuple[str, ...]]:
    return Counter(
        tuple(_frame_value(row[column]) for column in columns)
        for row in frame[columns].to_dict(orient="records")
    )


def _compare_code_frames(
    dataset: str,
    vip_frame: pd.DataFrame,
    ordinary_frame: pd.DataFrame,
    code: str,
) -> str | None:
    vip = vip_frame[vip_frame["ts_code"].astype("string").eq(code)].copy()
    ordinary = ordinary_frame[ordinary_frame["ts_code"].astype("string").eq(code)].copy()
    if set(vip.columns) != set(ordinary.columns):
        return f"{code}: schema fields differ"
    if len(vip) != len(ordinary):
        return f"{code}: row count vip={len(vip)} ordinary={len(ordinary)}"
    # All common raw fields are compared. This includes ann_date, f_ann_date,
    # report_type, update_flag, and the financial values, rather than silently
    # checking only one headline number.
    columns = sorted(set(vip.columns))
    if _row_counter(vip, columns) != _row_counter(ordinary, columns):
        return f"{code}: raw field/value multiset differs"
    return None


def _cross_check(
    provider: TushareProvider,
    dataset: str,
    period: str,
    vip_frame: pd.DataFrame,
    *,
    sample_size: int,
    page_size: int,
    max_pages: int,
) -> OrdinaryCrossCheck:
    if "ts_code" not in vip_frame.columns or vip_frame.empty:
        return OrdinaryCrossCheck("UNKNOWN", notes=("VIP response has no sampleable ts_code",))
    codes = sorted(vip_frame["ts_code"].dropna().astype(str).unique())
    if not codes:
        return OrdinaryCrossCheck("UNKNOWN", notes=("VIP response has no non-null ts_code",))
    rng = random.Random(f"{dataset}:{period}")
    requested = tuple(sorted(rng.sample(codes, min(sample_size, len(codes)))))
    checked: list[str] = []
    mismatches: list[str] = []
    for code in requested:
        try:
            ordinary_result = fetch_paginated_audited(
                provider,
                dataset,
                {"ts_code": code, "period": period},
                page_size=page_size,
                max_pages=max_pages,
            )
        except ProviderError as exc:
            mismatches.append(f"{code}: ordinary {exc.error_type}")
            continue
        except PaginationError as exc:
            mismatches.append(f"{code}: ordinary pagination {exc}")
            continue
        if ordinary_result.status != "PASS":
            mismatches.append(f"{code}: ordinary status {ordinary_result.status}")
            continue
        mismatch = _compare_code_frames(dataset, vip_frame, ordinary_result.frame, code)
        checked.append(code)
        if mismatch:
            mismatches.append(mismatch)
    status = "PASS" if not mismatches and len(checked) == len(requested) else "FAIL"
    return OrdinaryCrossCheck(
        status=status,
        requested_codes=requested,
        checked_codes=tuple(checked),
        mismatches=tuple(mismatches),
        notes=("all common raw fields compared",),
    )


def _result_from_fetch(
    dataset: str,
    source_api: str,
    period: str,
    fetched: PaginatedFetch,
) -> ProductionDatasetResult:
    spec = get_dataset_spec(dataset)
    frame = fetched.frame
    quality = check_frame_quality(dataset, frame, spec)
    missing_pit = tuple(field for field in spec.pit_fields if field not in frame.columns)
    warnings = list(fetched.warnings) + list(quality.warnings)
    if not frame.empty and "end_date" in frame.columns:
        periods = frame["end_date"].dropna().astype(str).str.replace("-", "", regex=False)
        if any(value != period for value in periods):
            warnings.append("period_coverage_mismatch")
    if fetched.status == "EMPTY":
        status = "EMPTY"
    elif fetched.status != "PASS":
        status = "PARTIAL"
    elif quality.missing_required or missing_pit:
        status = "SCHEMA_MISMATCH"
    elif "period_coverage_mismatch" in warnings:
        status = "PARTIAL"
    else:
        status = "PASS"
    return ProductionDatasetResult(
        dataset=dataset,
        source_api=source_api,
        period=period,
        status=status,
        page_count=fetched.page_count,
        rows=len(frame),
        elapsed_seconds=fetched.elapsed_seconds,
        duplicate_count=fetched.duplicate_count,
        schema_hash=fetched.schema_hash,
        first_ts_code=fetched.first_ts_code,
        last_ts_code=fetched.last_ts_code,
        schema_hashes=fetched.schema_hashes,
        schema_drift=len(fetched.schema_hashes) > 1,
        pit_fields=tuple(field for field in spec.pit_fields if field in frame.columns),
        missing_pit_fields=missing_pit,
        warnings=tuple(dict.fromkeys(warnings)),
        total_rows=fetched.total_rows,
        pages=fetched.pages,
    )


def run_vip_production_validation(
    settings: Settings,
    *,
    period: str = PRODUCTION_PERIOD,
    datasets: tuple[str, ...] = PRODUCTION_DATASETS,
    page_size: int = 5000,
    max_pages: int = 100,
    sample_size: int = 10,
    persist: bool = True,
) -> ProductionValidationSummary:
    """Validate one full-market VIP period and persist it only after cross-check."""

    if len(period) != 8 or not period.isdigit():
        raise ValueError("period must be an 8-digit YYYYMMDD string")
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    if not settings.token_configured:
        raise ValueError("TUSHARE_TOKEN is required for production validation")

    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
        backoff_jitter_seconds=settings.backoff_jitter_seconds,
    )
    frames: dict[str, pd.DataFrame] = {}
    results: list[ProductionDatasetResult] = []
    for dataset in datasets:
        source_api = route_for_dataset(dataset)
        try:
            fetched = fetch_paginated_audited(
                provider,
                source_api,
                {"period": period},
                page_size=page_size,
                max_pages=max_pages,
            )
            frames[dataset] = fetched.frame
            results.append(
                _result_from_fetch(dataset, source_api, period, fetched)
            )
        except ProviderError as exc:
            results.append(
                ProductionDatasetResult(
                    dataset=dataset,
                    source_api=source_api,
                    period=period,
                    status="FAILED",
                    page_count=0,
                    rows=0,
                    elapsed_seconds=0.0,
                    duplicate_count=0,
                    schema_hash="",
                    first_ts_code=None,
                    last_ts_code=None,
                    error=f"{exc.error_type}: {exc.error_message}",
                )
            )
        except PaginationError as exc:
            partial = exc.partial
            results.append(
                ProductionDatasetResult(
                    dataset=dataset,
                    source_api=source_api,
                    period=period,
                    status="PARTIAL",
                    page_count=partial.page_count,
                    rows=len(partial.frame),
                    elapsed_seconds=partial.elapsed_seconds,
                    duplicate_count=partial.duplicate_count,
                    schema_hash=partial.schema_hash,
                    first_ts_code=partial.first_ts_code,
                    last_ts_code=partial.last_ts_code,
                    schema_hashes=partial.schema_hashes,
                    schema_drift=len(partial.schema_hashes) > 1,
                    warnings=partial.warnings,
                    error=str(exc),
                    pages=partial.pages,
                )
            )

    # Ordinary cross-checks happen only after each VIP frame is fully paginated.
    updated: list[ProductionDatasetResult] = []
    for result in results:
        if result.status != "PASS":
            updated.append(result)
            continue
        check = _cross_check(
            provider,
            result.dataset,
            period,
            frames[result.dataset],
            sample_size=sample_size,
            page_size=page_size,
            max_pages=max_pages,
        )
        status = result.status if check.status == "PASS" else "CROSS_CHECK_FAILED"
        updated.append(replace(result, status=status, ordinary_cross_check=check))

    final_results = updated
    safe = bool(final_results) and all(result.status == "PASS" for result in final_results)
    if safe and persist:
        store = RawParquetStore(settings.data_dir)
        persisted: list[ProductionDatasetResult] = []
        for result in final_results:
            stored = store.write_period(
                result.dataset,
                period,
                frames[result.dataset],
                get_dataset_spec(result.dataset),
                retrieved_at=utc_now(),
                source=SOURCE_NAME,
                source_api=result.source_api,
            )
            persisted.append(replace(result, stored_files=tuple(stored)))
        final_results = persisted
        checkpoint_store = BootstrapCheckpointStore(
            settings.data_dir / "state" / "bootstrap-checkpoints.json",
            secret=settings.token,
        )
        for result in final_results:
            checkpoint_store.append(
                BootstrapCheckpoint(
                    dataset=result.dataset,
                    period=result.period,
                    source_api=result.source_api,
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    page_count=result.page_count,
                    row_count=result.rows,
                    status="PASS",
                    schema_hash=result.schema_hash or None,
                    duplicate_count=result.duplicate_count,
                    first_ts_code=result.first_ts_code,
                    last_ts_code=result.last_ts_code,
                    warnings=result.warnings,
                )
            )

    summary = ProductionValidationSummary(
        period=period,
        generated_at=utc_now(),
        results=tuple(final_results),
        sample_size=sample_size,
        safe_to_bootstrap=safe,
    )
    _write_validation_state(settings, summary)
    return summary


def _write_validation_state(settings: Settings, summary: ProductionValidationSummary) -> None:
    path = settings.data_dir / "state" / "vip-production-validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "period": summary.period,
        "generated_at": summary.generated_at,
        "sample_size": summary.sample_size,
        "safe_to_bootstrap": summary.safe_to_bootstrap,
        "results": [],
    }
    for result in summary.results:
        value = {
            "dataset": result.dataset,
            "source_api": result.source_api,
            "period": result.period,
            "status": result.status,
            "page_count": result.page_count,
            "row_count": result.rows,
            "elapsed_seconds": round(result.elapsed_seconds, 6),
            "duplicate_count": result.duplicate_count,
            "schema_hash": result.schema_hash,
            "first_ts_code": result.first_ts_code,
            "last_ts_code": result.last_ts_code,
            "schema_hashes": list(result.schema_hashes),
            "schema_drift": result.schema_drift,
            "pit_fields": list(result.pit_fields),
            "missing_pit_fields": list(result.missing_pit_fields),
            "ordinary_cross_check": {
                "status": result.ordinary_cross_check.status,
                "requested_codes": list(result.ordinary_cross_check.requested_codes),
                "checked_codes": list(result.ordinary_cross_check.checked_codes),
                "mismatches": list(result.ordinary_cross_check.mismatches),
                "notes": list(result.ordinary_cross_check.notes),
            },
            "stored_files": [str(file.path) for file in result.stored_files],
            "warnings": list(result.warnings),
            "error": result.error,
            "total_rows": result.total_rows,
            "pages": [
                {
                    "page_number": page.page_number,
                    "offset": page.offset,
                    "rows": page.rows,
                    "elapsed_seconds": round(page.elapsed_seconds, 6),
                    "schema_hash": page.schema_hash,
                    "first_ts_code": page.first_ts_code,
                    "last_ts_code": page.last_ts_code,
                }
                for page in result.pages
            ],
        }
        payload["results"].append(value)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _format_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_production_validation_markdown(summary: ProductionValidationSummary) -> str:
    lines = [
        "# VIP production period validation",
        "",
        f"- Generated at (UTC): `{summary.generated_at}`",
        f"- Period: `{summary.period}`",
        f"- Ordinary cross-check sample size: `{summary.sample_size}` "
        "(deterministic random sample)",
        "- VIP calls use the official Tushare Python SDK through `TushareProvider`.",
        "- Credentials and private endpoint configuration are never recorded.",
        "- Page size is recorded in the command/run log; no smoke-test limit was used.",
        "",
        "## Full-market results",
        "",
        "| Dataset | API | Period | Pages | Rows | Elapsed (s) | Duplicate identities | "
        "Schema hash | First ts_code | Last ts_code | PIT fields | Ordinary cross-check | Result |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for result in summary.results:
        pit = ", ".join(result.pit_fields) or "-"
        if result.missing_pit_fields:
            pit += " (missing: " + ", ".join(result.missing_pit_fields) + ")"
        lines.append(
            "| "
            + " | ".join(
                (
                    result.dataset,
                    result.source_api,
                    result.period,
                    str(result.page_count),
                    str(result.rows),
                    f"{result.elapsed_seconds:.3f}",
                    str(result.duplicate_count),
                    _format_cell(result.schema_hash or "-"),
                    _format_cell(result.first_ts_code or "-"),
                    _format_cell(result.last_ts_code or "-"),
                    _format_cell(pit),
                    _format_cell(result.ordinary_cross_check.status),
                    result.status,
                )
            )
            + " |"
        )

    lines.extend(["", "## Ordinary cross-check details", ""])
    for result in summary.results:
        check = result.ordinary_cross_check
        lines.append(f"### {result.dataset}")
        lines.append("")
        lines.append(f"- Requested codes: `{', '.join(check.requested_codes) or '-'}`")
        lines.append(f"- Checked codes: `{', '.join(check.checked_codes) or '-'}`")
        lines.append(f"- Status: `{check.status}`")
        if check.mismatches:
            lines.append("- Mismatches: " + "; ".join(check.mismatches))
        if check.notes:
            lines.append("- Notes: " + "; ".join(check.notes))
        lines.append("")

    lines.extend(
        [
            "## Pagination audit",
            "",
            "Each request uses offsets `0, page_size, 2*page_size, ...`; a repeated page",
            "signature, over-limit page, unexpected empty page, or exhausted max-pages bound",
            "is marked PARTIAL rather than treated as HTTP-200 success. The SDK response",
            "surface does not expose a separate API total field, so `total_rows` is recorded",
            "as unavailable unless a future provider adapter exposes it.",
            "",
        ]
    )
    for result in summary.results:
        if not result.pages:
            continue
        lines.append(f"### {result.dataset} page log")
        lines.append("")
        lines.append("| Page | Offset | Rows | Elapsed (s) | Schema hash | First | Last |")
        lines.append("| ---: | ---: | ---: | ---: | --- | --- | --- |")
        for page in result.pages:
            lines.append(
                f"| {page.page_number} | {page.offset} | {page.rows} | "
                f"{page.elapsed_seconds:.3f} | {page.schema_hash or '-'} | "
                f"{page.first_ts_code or '-'} | {page.last_ts_code or '-'} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Gate",
            "",
            f"- Safe to start P0 historical bootstrap: "
            f"`{'YES' if summary.safe_to_bootstrap else 'NO'}`",
            "- A failed ordinary cross-check, missing PIT field, EMPTY response, "
            "PARTIAL pagination,",
            "  or provider failure blocks VIP bootstrap. Raw rows are never latest-only compacted.",
            "",
        ]
    )
    return "\n".join(lines)


def write_production_validation_report(
    summary: ProductionValidationSummary, path: str | Path
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_production_validation_markdown(summary), encoding="utf-8")
