"""Small argparse CLI for the Phase 0/1 workflow."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from .config import SOURCE_NAME, Settings, load_settings
from .datasets.bootstrap import (
    P0_DATASETS,
    bootstrap_datasets,
    format_dataset_progress,
    render_bootstrap_dry_run,
)
from .datasets.periods import latest_complete_annual_year
from .datasets.production import (
    PRODUCTION_DATASETS,
    PRODUCTION_PERIOD,
    run_vip_production_validation,
    write_production_validation_report,
)
from .datasets.specs import get_dataset_spec
from .datasets.sync import sample_request_plan, sync_daily, sync_sample
from .dates import normalize_date_series
from .pit.financial import (
    canonicalize_financial_frame,
    derive_single_quarter,
    find_financial_revision_candidates,
    query_financial_as_of,
    validate_revision_candidate,
)
from .providers.tushare import TushareProvider
from .scanner.daily import (
    compare_scan_snapshots,
    read_scan_snapshot,
    scan_data,
    write_scan_snapshot,
)
from .scanner.evaluation import EvaluationConfig, evaluate_scans
from .scanner.replay import (
    ReplayConfig,
    run_replay,
    run_replay_variants,
    write_replay_artifacts,
    write_replay_variant_artifacts,
)
from .scanner.report import write_candidate_reports
from .scanner.stability import StabilityConfig, analyze_feature_stability, write_stability_report
from .storage.guards import check_disk_space
from .storage.inventory import (
    build_coverage_report,
    build_raw_manifest,
    format_coverage,
    format_inventory,
    write_coverage_report,
    write_raw_manifest,
)
from .storage.parquet import RawParquetStore
from .storage.planning import build_capacity_plan, write_capacity_plan
from .storage.state import BootstrapCheckpointStore, SyncStateStore
from .validation import (
    validate_source,
    write_pit_mapping_report,
    write_validation_report,
    write_vip_evaluation_report,
)

DEFAULT_CODES = ("000001.SZ", "600000.SH", "300001.SZ", "688001.SH")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ashare_turnaround")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("preflight", help="show local configuration without secrets")

    validate = subparsers.add_parser("validate-source", help="run the small API validation matrix")
    validate.add_argument("--sample-code", default="600000.SH")
    validate.add_argument("--vip", action="store_true", help="also test bounded VIP period probes")
    validate.add_argument("--report", default="docs/data-source-validation.md")

    sync = subparsers.add_parser("sync-sample", help="fetch a bounded 3-5 code sample")
    sync.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    sync.add_argument("--start-date", default="20240101")
    sync.add_argument("--end-date", default="20241231")
    sync.add_argument("--limit", type=int, default=100)
    sync.add_argument("--max-pages", type=int, default=1)
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="print the bounded request plan without token/API/state/file side effects",
    )

    subparsers.add_parser("pit-check", help="run synthetic and available-sample PIT checks")

    plan = subparsers.add_parser(
        "storage-plan", help="estimate 10/15-year RAW Parquet storage needs"
    )
    plan.add_argument("--company-count", type=int, default=5500)
    plan.add_argument("--company-count-source", default="planning estimate")
    plan.add_argument("--report", default="docs/storage-capacity-plan.md")

    production = subparsers.add_parser(
        "validate-vip-production", help="validate one full-market VIP report period"
    )
    production.add_argument("--period", default=PRODUCTION_PERIOD)
    production.add_argument("--page-size", type=int, default=5000)
    production.add_argument("--max-pages", type=int, default=100)
    production.add_argument("--sample-size", type=int, default=10)
    production.add_argument("--no-persist", action="store_true")
    production.add_argument("--report", default="docs/vip-production-validation.md")

    bootstrap = subparsers.add_parser(
        "bootstrap-financials", help="resumable period-scoped historical RAW bootstrap"
    )
    bootstrap.add_argument(
        "--dataset",
        nargs="+",
        choices=(*P0_DATASETS, "all"),
        default=["all"],
    )
    bootstrap.add_argument("--start-year", type=int, default=2012)
    bootstrap.add_argument("--end-year", type=int, default=None)
    resume_group = bootstrap.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="resume", action="store_true")
    resume_group.add_argument("--no-resume", dest="resume", action="store_false")
    bootstrap.set_defaults(resume=True)
    bootstrap.add_argument("--dry-run", action="store_true")
    bootstrap.add_argument("--page-size", type=int, default=5000)
    bootstrap.add_argument("--max-pages", type=int, default=100)
    bootstrap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent financial bootstrap workers. Use 1 for serial execution.",
    )
    bootstrap.add_argument(
        "--requests-per-minute",
        type=float,
        default=None,
        help="Global financial bootstrap API limit; defaults to TUSHARE_REQUESTS_PER_MINUTE.",
    )

    inventory = subparsers.add_parser("inventory", help="inventory raw Parquet and write manifest")
    inventory.add_argument("--manifest", default="data/state/raw-manifest.json")
    inventory.add_argument("--coverage", default="data/state/data-coverage.json")
    inventory.add_argument("--as-of", default=None, help="bound expected trading-date coverage")

    daily = subparsers.add_parser("sync-daily", help="synchronize one requested trading date")
    daily.add_argument("--date", dest="requested_date", default=None)
    daily.add_argument("--page-size", type=int, default=5000)
    daily.add_argument("--max-pages", type=int, default=100)

    replay = subparsers.add_parser("replay", help="run the scanner at a historical as-of date")
    replay.add_argument("--data-dir", default="data")
    replay.add_argument("--as-of", required=True)
    replay.add_argument("--top", type=int, default=20)

    replay_variants = subparsers.add_parser(
        "replay-variants",
        help="run all predeclared score variants against one historical snapshot",
    )
    replay_variants.add_argument("--data-dir", default="data")
    replay_variants.add_argument("--as-of", required=True)
    replay_variants.add_argument("--top", type=int, default=20)
    replay_variants.add_argument("--directory", default=None)

    scan = subparsers.add_parser("scan", help="run and persist the daily Top-N scanner")
    scan.add_argument("--data-dir", default="data")
    scan.add_argument("--as-of", default=None)
    scan.add_argument("--top", type=int, default=20)

    compare = subparsers.add_parser("scan-compare", help="compare two persisted scanner snapshots")
    compare.add_argument("left")
    compare.add_argument("right")

    evaluate = subparsers.add_parser("evaluate", help="evaluate frozen scanner snapshots")
    evaluate.add_argument("--scans", nargs="+", required=True)
    evaluate.add_argument("--data-dir", default="data")
    evaluate.add_argument("--benchmark-code", default=None)
    evaluate.add_argument("--horizons", nargs="+", type=int, default=[20, 60, 120, 250])
    evaluate.add_argument("--top", type=int, default=20)
    evaluate.add_argument("--transaction-cost-bps", type=float, default=0.0)
    evaluate.add_argument("--delisted-return", type=float, default=-1.0)
    evaluate.add_argument("--historical-universe", default=None)
    evaluate.add_argument("--exposures", default=None)
    evaluate.add_argument("--fundamentals", default=None)
    evaluate.add_argument("--report", default="data/reports/evaluation.json")

    ablate = subparsers.add_parser(
        "ablate", help="analyze feature stability from saved variant evaluations"
    )
    ablate.add_argument("variants", nargs="+", help="variant=evaluation.json")
    ablate.add_argument("--top", type=int, default=20)
    ablate.add_argument("--report", default="data/reports/feature-stability.json")

    report = subparsers.add_parser("report", help="generate deterministic candidate reports")
    report.add_argument("--data-dir", default="data")
    report.add_argument("--as-of", required=True)
    report.add_argument("--top", type=int, default=20)
    report.add_argument("--code", action="append", default=None)
    report.add_argument("--directory", default="data/reports")
    return parser


def _settings_without_secret(settings: Settings) -> str:
    base = "configured" if settings.base_url else "official-default"
    return (
        f"data_dir={settings.data_dir}\n"
        f"token_configured={settings.token_configured}\n"
        f"base_url={base}\n"
        f"timeout={settings.timeout}\n"
        f"max_retries={settings.max_retries}\n"
        f"requests_per_minute={settings.requests_per_minute}"
    )


def _preflight(_: argparse.Namespace) -> int:
    settings = load_settings()
    print(_settings_without_secret(settings))
    print("provider_client=official tushare Python SDK")
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
    write_vip_evaluation_report(report, Path("docs/vip-api-evaluation.md"))
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
    codes = tuple(args.codes)
    if not 3 <= len(codes) <= 5:
        print("sync-sample requires 3 to 5 codes", file=sys.stderr)
        return 2
    if args.limit <= 0 or args.max_pages <= 0:
        print("sync-sample --limit and --max-pages must be positive", file=sys.stderr)
        return 2

    if args.dry_run:
        plan = sample_request_plan(
            codes,
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
        )
        print("sync-sample dry-run")
        print(f"codes={','.join(codes)}")
        print(
            f"request_groups={len(plan)} "
            f"requests_planned={sum(len(requests) for requests in plan.values())}"
        )
        for dataset, requests in plan.items():
            print(f"dataset={dataset} requests={len(requests)}")
        print("remote_requests=false")
        print("parquet_writes=false")
        print("state_changes=false")
        return 0

    if not settings.token_configured:
        print("TUSHARE_TOKEN is not configured; sync-sample was not run", file=sys.stderr)
        return 2
    settings.ensure_data_dirs()
    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
        backoff_jitter_seconds=settings.backoff_jitter_seconds,
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
    print(
        f"requests={len(summary.results)} failed={len(summary.failures)} "
        f"storage_errors={len(summary.storage_errors)} "
        f"stored_files={len(summary.stored_files)}"
    )
    for result in summary.results:
        if result.status in {"failed", "partial"}:
            print(
                f"failed dataset={result.dataset} status={result.status} "
                f"error_type={result.error_type or '-'} message={result.error_message or '-'}",
                file=sys.stderr,
            )
    for dataset, message in summary.storage_errors:
        print(f"storage_failed dataset={dataset} message={message}", file=sys.stderr)
    for path in summary.stored_files:
        print(f"stored={path.path} rows={path.rows} bytes={path.size_bytes}")
    return 2 if summary.failures or summary.storage_errors else 0


def _storage_plan(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_data_dirs()
    plan = build_capacity_plan(
        settings.data_dir,
        company_count=args.company_count,
        company_count_source=args.company_count_source,
    )
    report_path = Path(args.report)
    write_capacity_plan(plan, report_path)
    print(f"storage_plan={report_path}")
    print(f"initial_free_bytes={plan.initial_free_bytes}")
    print(f"initial_data_bytes={plan.initial_data_bytes}")
    for years in (10, 15):
        values = [item for item in plan.estimates if item.horizon_years == years]
        print(
            f"estimate_{years}y={sum(item.estimated_size_bytes for item in values)} "
            f"upper_bound={sum(item.conservative_upper_bound_bytes for item in values)}"
        )
    return 0


def _validate_vip_production(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.token_configured:
        print("TUSHARE_TOKEN is not configured; production validation was not run", file=sys.stderr)
        return 2
    settings.ensure_data_dirs()
    disk = check_disk_space(settings.data_dir)
    print(f"free_bytes={disk.free_bytes} disk_gate={disk.recommendation}")
    if disk.hard_stop:
        print("disk gate blocked production validation", file=sys.stderr)
        return 2
    try:
        summary = run_vip_production_validation(
            settings,
            period=args.period,
            datasets=PRODUCTION_DATASETS,
            page_size=args.page_size,
            max_pages=args.max_pages,
            sample_size=args.sample_size,
            persist=not args.no_persist,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"production validation failed before completion: {exc}", file=sys.stderr)
        return 2
    report_path = Path(args.report)
    write_production_validation_report(summary, report_path)
    print(f"production_validation_report={report_path}")
    for result in summary.results:
        print(
            f"{result.dataset}: status={result.status} api={result.source_api} "
            f"period={result.period} pages={result.page_count} rows={result.rows} "
            f"elapsed={result.elapsed_seconds:.3f}s duplicate_count={result.duplicate_count} "
            f"ordinary_cross_check={result.ordinary_cross_check.status}"
        )
    print(f"safe_to_bootstrap={summary.safe_to_bootstrap}")
    return 0 if summary.safe_to_bootstrap else 2


def _bootstrap_financials(args: argparse.Namespace) -> int:
    settings = load_settings()
    end_year = args.end_year or latest_complete_annual_year()
    datasets = tuple(dict.fromkeys(P0_DATASETS if "all" in args.dataset else args.dataset))
    requests_per_minute = (
        settings.requests_per_minute
        if args.requests_per_minute is None
        else args.requests_per_minute
    )
    checkpoints = BootstrapCheckpointStore(
        settings.data_dir / "state" / "bootstrap-checkpoints.json",
        secret=settings.token,
    )
    store = RawParquetStore(settings.data_dir)
    if args.dry_run:
        try:
            summary = bootstrap_datasets(
                None,
                store,
                checkpoints,
                datasets=datasets,
                start_year=args.start_year,
                end_year=end_year,
                resume=args.resume,
                dry_run=True,
                page_size=args.page_size,
                max_pages=args.max_pages,
                workers=args.workers,
                requests_per_minute=requests_per_minute,
            )
        except (ValueError, RuntimeError) as exc:
            print(f"bootstrap dry-run failed: {exc}", file=sys.stderr)
            return 2
        print(render_bootstrap_dry_run(summary))
        return 0
    settings.ensure_data_dirs()
    if not settings.token_configured:
        print("TUSHARE_TOKEN is not configured; bootstrap was not run", file=sys.stderr)
        return 2
    disk = check_disk_space(settings.data_dir)
    print(f"free_bytes={disk.free_bytes} disk_gate={disk.recommendation}")
    if disk.hard_stop:
        print("disk gate blocked historical bootstrap", file=sys.stderr)
        return 2
    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
        backoff_jitter_seconds=settings.backoff_jitter_seconds,
    )
    try:
        summary = bootstrap_datasets(
            provider,
            store,
            checkpoints,
            datasets=datasets,
            start_year=args.start_year,
            end_year=end_year,
            resume=args.resume,
            dry_run=False,
            page_size=args.page_size,
            max_pages=args.max_pages,
            workers=args.workers,
            requests_per_minute=requests_per_minute,
            progress=lambda message: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print(
            "bootstrap interrupted; completed period files/checkpoints were preserved; "
            "resume can continue unfinished periods",
            file=sys.stderr,
        )
        return 130
    except (ValueError, RuntimeError) as exc:
        print(f"bootstrap failed before completion: {exc}", file=sys.stderr)
        return 2

    for result in summary.results:
        error = f" error={result.error}" if result.error else ""
        print(
            f"{result.dataset} {result.period} {result.status} rows={result.rows} "
            f"pages={result.page_count} skipped={result.skipped}{error}"
        )
    print(format_dataset_progress(summary))
    failed = len(summary.failures)
    print(
        f"datasets={','.join(summary.datasets)} tasks={summary.task_count} "
        f"completed={summary.completed_count} skipped(resume)={summary.skipped_count} "
        f"failed={failed} workers={summary.workers} requests={summary.api_requests} "
        f"rows={summary.row_count} elapsed={summary.elapsed_seconds:.3f}s"
    )
    return 2 if failed else 0


def _inventory(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_data_dirs()
    manifest = build_raw_manifest(settings.data_dir)
    write_raw_manifest(manifest, args.manifest)
    coverage = build_coverage_report(settings.data_dir, as_of_date=args.as_of)
    write_coverage_report(coverage, args.coverage)
    print(format_inventory(manifest))
    print(f"manifest={args.manifest}")
    print(format_coverage(coverage))
    print(f"coverage={args.coverage}")
    return 2 if any(value.status == "FAIL" for value in coverage.datasets) else 0


def _sync_daily(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.token_configured:
        print("TUSHARE_TOKEN is not configured; sync-daily was not run", file=sys.stderr)
        return 2
    settings.ensure_data_dirs()
    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
        backoff_jitter_seconds=settings.backoff_jitter_seconds,
    )
    requested = args.requested_date or pd.Timestamp.now().strftime("%Y%m%d")
    store = RawParquetStore(settings.data_dir)
    state = SyncStateStore(settings.data_dir / "state" / "sync-log.json", secret=settings.token)
    try:
        summary = sync_daily(
            provider,
            store,
            state,
            requested_date=requested,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"sync-daily failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"requested_date={summary.requested_date} effective_date={summary.effective_date or '-'} "
        f"status={summary.status}"
    )
    for result in summary.results:
        error = f" error={result.error}" if result.error else ""
        print(f"{result.dataset} {result.status} rows={result.rows}{error}")
    return 2 if summary.failures or summary.status == "partial" else 0


def _replay(args: argparse.Namespace) -> int:
    try:
        result = run_replay(
            args.data_dir,
            as_of_date=args.as_of,
            config=ReplayConfig(top_n=args.top),
        )
        data_path, metadata_path = write_replay_artifacts(
            result,
            Path(args.data_dir) / "derived" / "replays",
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"replay_status={result.status} as_of={result.as_of_date} candidates={len(result.ranked)}"
    )
    print(f"replay_data={data_path}")
    print(f"replay_metadata={metadata_path}")
    for warning in result.warnings:
        print(f"warning={warning}")
    return 0 if result.status == "PASS" else 2


def _replay_variants(args: argparse.Namespace) -> int:
    try:
        results = run_replay_variants(
            args.data_dir,
            as_of_date=args.as_of,
            config=ReplayConfig(top_n=args.top),
        )
        directory = Path(args.directory or Path(args.data_dir) / "derived" / "replays")
        artifacts = write_replay_variant_artifacts(results, directory)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"replay-variants failed: {exc}", file=sys.stderr)
        return 2
    for name, result in results.items():
        data_path, metadata_path = artifacts[name]
        print(
            f"variant={name} status={result.status} snapshot_id={result.snapshot_id} "
            f"candidates={len(result.ranked)} data={data_path} metadata={metadata_path}"
        )
    return 0 if all(result.status == "PASS" for result in results.values()) else 2


def _scan(args: argparse.Namespace) -> int:
    try:
        snapshot = scan_data(
            args.data_dir,
            as_of_date=args.as_of,
            top_n=args.top,
        )
        snapshot = write_scan_snapshot(
            snapshot, args.data_dir
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"scan failed: {exc}", file=sys.stderr)
        return 2
    result = snapshot.result
    print(f"scan_status={result.status} as_of={result.as_of_date} candidates={len(result.ranked)}")
    if snapshot.data_path:
        print(f"snapshot={snapshot.data_path}")
    if snapshot.metadata_path:
        print(f"metadata={snapshot.metadata_path}")
    return 0 if result.status == "PASS" else 2


def _scan_compare(args: argparse.Namespace) -> int:
    try:
        compared = compare_scan_snapshots(
            read_scan_snapshot(args.left), read_scan_snapshot(args.right)
        )
    except (OSError, ValueError) as exc:
        print(f"scan-compare failed: {exc}", file=sys.stderr)
        return 2
    print(
        compared.to_json(orient="records", force_ascii=False, indent=2)
        if not compared.empty
        else "[]"
    )
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    try:
        scans = pd.concat(
            (read_scan_snapshot(path) for path in args.scans), ignore_index=True, sort=False
        )
        store = RawParquetStore(args.data_dir)
        stock_basic = (
            _read_research_frame(args.historical_universe)
            if args.historical_universe
            else store.read("stock_basic")
        )
        exposures = (
            _read_research_frame(args.exposures)
            if args.exposures
            else store.read("daily_basic")
        )
        fundamentals = (
            _read_research_frame(args.fundamentals) if args.fundamentals else None
        )
        result = evaluate_scans(
            scans,
            store.read("daily"),
            config=EvaluationConfig(
                horizons=tuple(args.horizons),
                top_n=args.top,
                benchmark_code=args.benchmark_code,
                transaction_cost_bps=args.transaction_cost_bps,
                delisted_return=args.delisted_return,
            ),
            stock_basic=stock_basic,
            exposures=exposures,
            fundamentals=fundamentals,
        )
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "config_version": result.config_version,
                    "status": result.status,
                    "warnings": list(result.warnings),
                    "configuration": result.configuration,
                    "limitations": list(result.limitations),
                    "provenance": result.provenance,
                    "summary": result.summary.to_dict(orient="records"),
                    "observations": result.observations.to_dict(orient="records"),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"evaluate failed: {exc}", file=sys.stderr)
        return 2
    print(f"evaluation_status={result.status} report={args.report}")
    return 0 if result.status == "PASS" else 2


def _read_research_frame(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(source)
    if suffix == ".csv":
        return pd.read_csv(source)
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ("rows", "observations", "ranked"):
                if key in payload:
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            raise ValueError(f"JSON research frame must contain an array: {source}")
        return pd.DataFrame(payload)
    raise ValueError(f"unsupported research frame: {source}")


def _ablate(args: argparse.Namespace) -> int:
    variants: dict[str, Path] = {}
    try:
        for item in args.variants:
            name, separator, path = item.partition("=")
            if not separator or not name or not path:
                raise ValueError("variants must use name=path format")
            if name in variants:
                raise ValueError(f"duplicate ablation variant: {name}")
            variants[name] = Path(path)
        result = analyze_feature_stability(
            variants,
            config=StabilityConfig(top_n=args.top),
        )
        write_stability_report(result, args.report)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ablate failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"ablation_status={result.status} segments={len(result.segments)} "
        f"assessments={len(result.feature_assessments)} report={args.report}"
    )
    return 0 if result.status == "PASS" else 2


def _report(args: argparse.Namespace) -> int:
    try:
        result = run_replay(
            args.data_dir,
            as_of_date=args.as_of,
            config=ReplayConfig(top_n=args.top),
        )
        json_path, markdown_path = write_candidate_reports(
            result,
            args.directory,
            codes=tuple(args.code) if args.code else None,
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"report failed: {exc}", file=sys.stderr)
        return 2
    print(f"report_json={json_path}")
    print(f"report_markdown={markdown_path}")
    return 0 if result.status == "PASS" else 2


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


def _format_value(value: object) -> str:
    try:
        if pd.isna(value):
            return "NA"
    except (TypeError, ValueError):
        pass
    return str(value).replace("|", "\\|").replace("\n", " ")


def _cumulative_semantics_evidence(store: RawParquetStore) -> dict[str, dict[str, object]]:
    """Run a bounded Q1/H1/Q3/FY bridge audit on the local sample only."""

    output: dict[str, dict[str, object]] = {}
    definitions = {
        "income": ("revenue", "n_income"),
        "cashflow": ("n_cashflow_act", "net_profit"),
    }
    quarter_labels = ("03-31", "06-30", "09-30", "12-31")
    for dataset, fields in definitions.items():
        frame = store.read(dataset)
        if frame.empty or "end_date" not in frame.columns or "ts_code" not in frame.columns:
            output[dataset] = {
                "status": "UNKNOWN",
                "semantics": "unknown",
                "company_years": 0,
                "field_checks": {},
            }
            continue
        frame = frame.copy()
        frame["_period"] = normalize_date_series(frame["end_date"])
        frame = frame[frame["_period"].dt.strftime("%m-%d").isin(quarter_labels)].copy()
        availability_field = "f_ann_date" if "f_ann_date" in frame.columns else "ann_date"
        frame["_available"] = normalize_date_series(frame[availability_field])
        frame["_update_rank"] = pd.to_numeric(
            frame.get("update_flag", pd.Series(pd.NA, index=frame.index)), errors="coerce"
        ).fillna(-1)
        identity = ["ts_code"]
        for column in ("report_type", "comp_type"):
            if column in frame.columns:
                identity.append(column)
        frame = frame.sort_values(["_available", "_update_rank"], kind="stable")
        frame = frame.drop_duplicates(identity + ["_period"], keep="last")
        frame["_year"] = frame["_period"].dt.year

        complete_groups: list[tuple[str, int, pd.DataFrame]] = []
        for code in sorted(frame["ts_code"].dropna().astype(str).unique()):
            code_frame = frame[frame["ts_code"].astype(str).eq(code)]
            code_groups: list[tuple[int, pd.DataFrame]] = []
            group_keys = [column for column in identity if column != "ts_code"] + ["_year"]
            grouped = (
                code_frame.groupby(group_keys, dropna=False)
                if group_keys
                else [(None, code_frame)]
            )
            for key, group in grouped:
                if not set(group["_period"].dt.strftime("%m-%d")) >= set(quarter_labels):
                    continue
                year = int(group["_year"].iloc[0])
                code_groups.append((year, group))
            for year, group in sorted(code_groups)[:2]:
                complete_groups.append((code, year, group))
            if len({item[0] for item in complete_groups}) >= 3:
                break

        field_checks: dict[str, dict[str, int | str]] = {}
        for field in fields:
            available = 0
            bridge_pass = 0
            for _, _, group in complete_groups:
                rows = {
                    period.strftime("%m-%d"): row
                    for period, row in group.set_index("_period").iterrows()
                }
                if not all(label in rows for label in quarter_labels) or field not in group.columns:
                    continue
                values = {
                    label: pd.to_numeric(rows[label][field], errors="coerce")
                    for label in quarter_labels
                }
                if any(pd.isna(value) for value in values.values()):
                    continue
                available += 1
                single_quarters = (
                    values["03-31"],
                    values["06-30"] - values["03-31"],
                    values["09-30"] - values["06-30"],
                    values["12-31"] - values["09-30"],
                )
                reconstructed = sum(single_quarters)
                tolerance = max(1e-6, abs(float(values["12-31"])) * 1e-8)
                if abs(float(reconstructed - values["12-31"])) <= tolerance:
                    bridge_pass += 1
            field_checks[field] = {
                "complete_company_years": available,
                "single_quarter_bridge_pass": bridge_pass,
                "status": "PASS" if available and available == bridge_pass else "UNKNOWN",
            }

        complete_count = len(complete_groups)
        checked = [value for value in field_checks.values() if value["complete_company_years"]]
        all_pass = bool(checked) and all(value["status"] == "PASS" for value in checked)
        # Income has two fully populated statement fields in this sample.  Cash
        # flow's n_cashflow_act is populated consistently, while net_profit is
        # endpoint-sparse; retain that distinction in the report.
        semantics = "confirmed" if dataset == "income" and all_pass else "suspected"
        output[dataset] = {
            "status": "PASS" if complete_count >= 2 and all_pass else "PARTIAL",
            "semantics": semantics,
            "company_years": complete_count,
            "field_checks": field_checks,
        }
    return output


def _disclosure_date_evidence(store: RawParquetStore) -> str:
    """Compare disclosure events with statement dates without equating them."""

    disclosure = store.read("disclosure_date")
    required = {"ts_code", "end_date", "actual_date"}
    if disclosure.empty or not required.issubset(disclosure.columns):
        return "UNKNOWN: disclosure_date sample is incomplete"
    disclosure = disclosure[list(required)].dropna().copy()
    disclosure["end_date"] = disclosure["end_date"].astype("string")
    disclosure["actual_date"] = disclosure["actual_date"].astype("string")
    summaries: list[str] = []
    for dataset in ("income", "balancesheet", "cashflow"):
        frame = store.read(dataset)
        statement_required = {"ts_code", "end_date", "f_ann_date"}
        if frame.empty or not statement_required.issubset(frame.columns):
            summaries.append(f"{dataset}: UNKNOWN")
            continue
        statement = frame[list(statement_required)].dropna().copy()
        statement["end_date"] = statement["end_date"].astype("string")
        statement["f_ann_date"] = statement["f_ann_date"].astype("string")
        merged = statement.merge(disclosure, on=["ts_code", "end_date"], how="inner")
        if merged.empty:
            summaries.append(f"{dataset}: 0 matched")
            continue
        same = merged["f_ann_date"].eq(merged["actual_date"])
        summaries.append(f"{dataset}: {int(same.sum())}/{len(merged)} matched")
    return "; ".join(summaries)


def _real_revision_evidence(store: RawParquetStore) -> tuple[object, object] | None:
    for dataset in ("income", "balancesheet", "cashflow"):
        frame = store.read(dataset)
        candidates = find_financial_revision_candidates(dataset, frame, max_candidates=20)
        for candidate in candidates:
            check = validate_revision_candidate(candidate)
            if check.status == "PASS":
                return candidate, check
    return None


def _render_pit_check(
    real_rows: int,
    synthetic: dict[str, bool],
    *,
    mapping_status: str,
    real_revision: tuple[object, object] | None,
    cumulative: dict[str, dict[str, object]],
    disclosure_evidence: str,
) -> str:
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
            "## Live PIT evidence",
            "",
            "| Check | Status | Evidence |",
            "| --- | --- | --- |",
            f"| real basic PIT mapping | {mapping_status} | "
            "income live schema fields and canonical date mapping |",
        ]
    )
    if real_revision is None:
        lines.append(
            "| real revision candidate | UNKNOWN | NOT FOUND IN BOUNDED SEARCH across "
            "local income/balancesheet/cashflow sample |"
        )
        lines.append("| real revision chain | UNKNOWN | no candidate was selected |")
    else:
        candidate, check = real_revision
        period = candidate.report_period.strftime("%Y-%m-%d")
        first_date = check.first_available_date.strftime("%Y-%m-%d")
        revision_date = check.revision_available_date.strftime("%Y-%m-%d")
        evidence = (
            f"{candidate.dataset} {candidate.ts_code} report_period={period}; "
            f"{check.value_column}: {first_date}={_format_value(check.first_value)}, "
            f"{revision_date}={_format_value(check.revised_value)}"
        )
        lines.append(f"| real revision candidate | PASS | {evidence} |")
        lines.append(
            f"| real revision chain | {check.status} | "
            f"as-of boundary checks: {check.checks} |"
        )

    lines.extend(
        [
            "",
            "## Financial period semantics",
            "",
            "The audit is limited to at most three local companies and two complete years "
            "per dataset. It calculates Q1, H1-Q1, Q3-H1, and FY-Q3; it does not create "
            "factors.",
            "",
            "| Dataset | Status | Semantic status | Complete company-years | Field bridge checks |",
            "| --- | --- | --- | ---: | --- |",
        ]
    )
    for dataset, evidence in cumulative.items():
        checks = evidence["field_checks"]
        check_text = ", ".join(
            f"{field}={value['single_quarter_bridge_pass']}/{value['complete_company_years']}"
            for field, value in checks.items()
        ) or "-"
        lines.append(
            f"| {dataset} | {evidence['status']} | {evidence['semantics']} | "
            f"{evidence['company_years']} | {check_text} |"
        )
    lines.extend(
        [
            "",
            "## Date field interpretation",
            "",
            "- `income`, `balancesheet`, and `cashflow`: live `ann_date`, `f_ann_date`, "
            "`end_date`, `report_type`, and `update_flag` were observed; `f_ann_date` is "
            "preferred for "
            "record availability, with `ann_date` as an explicit fallback.",
            "- `disclosure_date.actual_date` was observed as an event date and is not silently "
            "substituted for a specific financial record's `f_ann_date`.",
            f"- Bounded joins observed {disclosure_evidence}; agreement is evidence of "
            "correlation, not proof that the event field replaces record availability.",
            "",
            "Real revision search is bounded to already synchronized local data; no broad "
            "stock scan or historical bootstrap is performed by `pit-check`.",
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
    income_spec = get_dataset_spec("income")
    mapping_status = (
        "PASS"
        if not real_frame.empty and set(income_spec.required_fields).issubset(real_frame.columns)
        else "UNKNOWN"
    )
    real_revision = _real_revision_evidence(store) if not real_frame.empty else None
    cumulative = _cumulative_semantics_evidence(store)
    disclosure_evidence = _disclosure_date_evidence(store)
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
    print(f"real_basic_pit_mapping={mapping_status}")
    print(f"disclosure_date_comparison={disclosure_evidence}")
    if real_revision is None:
        print("real_revision_chain=UNKNOWN bounded_search_not_found")
    else:
        candidate, check = real_revision
        print(
            f"real_revision_chain={check.status} dataset={candidate.dataset} "
            f"ts_code={candidate.ts_code} report_period={candidate.report_period.date()}"
        )
    for dataset, evidence in cumulative.items():
        print(
            f"cumulative_{dataset}={evidence['status']} "
            f"semantics={evidence['semantics']} company_years={evidence['company_years']}"
        )
    report_path = Path("docs/pit-validation.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render_pit_check(
            len(real_frame),
            checks,
            mapping_status=mapping_status,
            real_revision=real_revision,
            cumulative=cumulative,
            disclosure_evidence=disclosure_evidence,
        ),
        encoding="utf-8",
    )
    real_revision_failed = real_revision is not None and real_revision[1].status != "PASS"
    return 0 if all(checks.values()) and not real_revision_failed else 2


def _configure_logging() -> None:
    level_name = os.getenv("ASHARE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure_logging()
    handlers = {
        "preflight": _preflight,
        "validate-source": _validate_source,
        "sync-sample": _sync_sample,
        "pit-check": _pit_check,
        "storage-plan": _storage_plan,
        "validate-vip-production": _validate_vip_production,
        "bootstrap-financials": _bootstrap_financials,
        "inventory": _inventory,
        "sync-daily": _sync_daily,
        "replay": _replay,
        "replay-variants": _replay_variants,
        "scan": _scan,
        "scan-compare": _scan_compare,
        "evaluate": _evaluate,
        "ablate": _ablate,
        "report": _report,
    }
    try:
        return handlers[args.command](args)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
