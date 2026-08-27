"""Resumable, period-scoped historical RAW bootstrap."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Lock

import pandas as pd

from ..config import SOURCE_NAME
from ..providers.rate_limit import RateLimiter
from ..providers.tushare import ProviderError, TushareProvider
from ..quality import check_frame_quality
from ..storage.guards import EMERGENCY_STOP_FREE_BYTES, check_disk_space
from ..storage.parquet import RawParquetStore
from ..storage.state import BootstrapCheckpoint, BootstrapCheckpointStore
from .periods import report_periods
from .production import route_for_dataset
from .specs import get_dataset_spec
from .sync import PaginatedFetch, PaginationError, fetch_paginated_audited, utc_now

P0_DATASETS: tuple[str, ...] = (
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BootstrapUnitResult:
    dataset: str
    period: str
    source_api: str
    status: str
    page_count: int = 0
    rows: int = 0
    elapsed_seconds: float = 0.0
    duplicate_count: int = 0
    schema_hash: str = ""
    first_ts_code: str | None = None
    last_ts_code: str | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    skipped: bool = False
    stored_path: str | None = None


@dataclass(frozen=True, slots=True)
class BootstrapRunSummary:
    datasets: tuple[str, ...]
    start_year: int
    end_year: int
    results: tuple[BootstrapUnitResult, ...]
    dry_run: bool = False
    workers: int = 1
    requests_per_minute: float | None = None
    api_requests: int = 0
    elapsed_seconds: float = 0.0

    @property
    def requested_periods(self) -> int:
        return len(report_periods(self.start_year, self.end_year))

    @property
    def failures(self) -> tuple[BootstrapUnitResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.status in {"FAILED", "PARTIAL", "UNKNOWN_EMPTY", "EMPTY"}
        )

    @property
    def task_count(self) -> int:
        return len(self.results)

    @property
    def completed_count(self) -> int:
        return sum(result.status == "PASS" and not result.skipped for result in self.results)

    @property
    def skipped_count(self) -> int:
        return sum(result.skipped for result in self.results)

    @property
    def row_count(self) -> int:
        return sum(result.rows for result in self.results)


@dataclass(frozen=True, slots=True)
class _BootstrapTask:
    index: int
    dataset: str
    period: str
    source_api: str


@dataclass(frozen=True, slots=True)
class _PeriodFetchOutcome:
    dataset: str
    period: str
    source_api: str
    started_at: str
    status: str
    fetched: PaginatedFetch | None = None
    error: str | None = None


def _checkpoint_from_fetch(
    dataset: str,
    period: str,
    source_api: str,
    started_at: str,
    fetched: PaginatedFetch,
    *,
    status: str,
    finished_at: str,
    error: str | None = None,
    warnings: tuple[str, ...] = (),
) -> BootstrapCheckpoint:
    return BootstrapCheckpoint(
        dataset=dataset,
        period=period,
        source_api=source_api,
        started_at=started_at,
        finished_at=finished_at,
        page_count=fetched.page_count,
        row_count=len(fetched.frame),
        status=status,
        error=error,
        schema_hash=fetched.schema_hash or None,
        duplicate_count=fetched.duplicate_count,
        first_ts_code=fetched.first_ts_code,
        last_ts_code=fetched.last_ts_code,
        warnings=tuple(dict.fromkeys((*fetched.warnings, *warnings))),
    )


def _period_values(frame: pd.DataFrame) -> set[str]:
    if "end_date" not in frame.columns:
        return set()
    values = frame["end_date"].dropna().astype(str).str.replace("-", "", regex=False)
    return {value.removesuffix(".0") for value in values}


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
    else:
        LOGGER.info(message)


def _locked_progress(
    progress: Callable[[str], None] | None,
) -> Callable[[str], None] | None:
    if progress is None:
        return None
    lock = Lock()

    def emit(message: str) -> None:
        with lock:
            progress(message)

    return emit


def _log_pages(
    progress: Callable[[str], None] | None,
    dataset: str,
    period: str,
    fetched: PaginatedFetch,
) -> None:
    for page in fetched.pages:
        _emit(
            progress,
            f"[{dataset}][{period}] page={page.page_number} "
            f"offset={page.offset} rows={page.rows}",
        )


def _fetch_period(
    provider: TushareProvider,
    dataset: str,
    period: str,
    *,
    page_size: int,
    max_pages: int,
    rate_limiter: RateLimiter | None = None,
    progress: Callable[[str], None] | None = None,
) -> _PeriodFetchOutcome:
    source_api = route_for_dataset(dataset)
    started_at = utc_now()
    _emit(progress, f"[{dataset}][{period}] start")
    try:
        fetched = fetch_paginated_audited(
            provider,
            source_api,
            {"period": period},
            page_size=page_size,
            max_pages=max_pages,
            rate_limiter=rate_limiter,
        )
    except ProviderError as exc:
        error = f"{exc.error_type}: {exc.error_message} attempts={exc.attempts}"
        _emit(progress, f"[{dataset}][{period}] failed error={error}")
        return _PeriodFetchOutcome(
            dataset,
            period,
            source_api,
            started_at,
            "FAILED",
            error=error,
        )
    except PaginationError as exc:
        _log_pages(progress, dataset, period, exc.partial)
        return _PeriodFetchOutcome(
            dataset,
            period,
            source_api,
            started_at,
            "PARTIAL",
            fetched=exc.partial,
            error=str(exc),
        )

    _log_pages(progress, dataset, period, fetched)
    return _PeriodFetchOutcome(
        dataset,
        period,
        source_api,
        started_at,
        fetched.status,
        fetched=fetched,
    )


def _commit_period(
    outcome: _PeriodFetchOutcome,
    store: RawParquetStore,
    checkpoints: BootstrapCheckpointStore,
) -> BootstrapUnitResult:
    """Write and checkpoint one fetched period on the coordinator thread."""

    dataset = outcome.dataset
    period = outcome.period
    source_api = outcome.source_api
    started_at = outcome.started_at
    fetched = outcome.fetched
    if fetched is None:
        error = outcome.error or "provider failed without an error message"
        checkpoints.append(
            BootstrapCheckpoint(
                dataset=dataset,
                period=period,
                source_api=source_api,
                started_at=started_at,
                finished_at=utc_now(),
                status="FAILED",
                error=error,
            )
        )
        return BootstrapUnitResult(
            dataset=dataset,
            period=period,
            source_api=source_api,
            status="FAILED",
            error=error,
        )

    if fetched.status == "EMPTY":
        error = "zero rows; historical availability could not be confirmed"
        checkpoints.append(
            _checkpoint_from_fetch(
                dataset,
                period,
                source_api,
                started_at,
                fetched,
                status="UNKNOWN_EMPTY",
                finished_at=utc_now(),
                error=error,
            )
        )
        return BootstrapUnitResult(
            dataset=dataset,
            period=period,
            source_api=source_api,
            status="UNKNOWN_EMPTY",
            page_count=fetched.page_count,
            elapsed_seconds=fetched.elapsed_seconds,
            warnings=fetched.warnings,
            error=error,
        )

    if fetched.status != "PASS":
        error = outcome.error or "pagination did not reach a terminal short page"
        checkpoints.append(
            _checkpoint_from_fetch(
                dataset,
                period,
                source_api,
                started_at,
                fetched,
                status="PARTIAL",
                finished_at=utc_now(),
                error=error,
            )
        )
        return BootstrapUnitResult(
            dataset=dataset,
            period=period,
            source_api=source_api,
            status="PARTIAL",
            page_count=fetched.page_count,
            rows=len(fetched.frame),
            elapsed_seconds=fetched.elapsed_seconds,
            duplicate_count=fetched.duplicate_count,
            schema_hash=fetched.schema_hash,
            first_ts_code=fetched.first_ts_code,
            last_ts_code=fetched.last_ts_code,
            warnings=fetched.warnings,
            error=error,
        )

    spec = get_dataset_spec(dataset)
    quality = check_frame_quality(dataset, fetched.frame, spec)
    warnings = list(fetched.warnings) + list(quality.warnings)
    if _period_values(fetched.frame) and _period_values(fetched.frame) != {period}:
        warnings.append("period_coverage_mismatch")
    status = "PASS"
    if quality.missing_required or "period_coverage_mismatch" in warnings:
        status = "PARTIAL"
    stored_path: str | None = None
    storage_error: str | None = None
    try:
        stored = store.write_period(
            dataset,
            period,
            fetched.frame,
            spec,
            retrieved_at=utc_now(),
            source=SOURCE_NAME,
            source_api=source_api,
        )
        if stored:
            stored_path = str(stored[0].path)
    except Exception as exc:
        status = "PARTIAL"
        storage_error = f"storage: {exc}"
        warnings.append("storage_failed")
    error = storage_error
    checkpoints.append(
        _checkpoint_from_fetch(
            dataset,
            period,
            source_api,
            started_at,
            fetched,
            status=status,
            finished_at=utc_now(),
            error=error,
            warnings=tuple(warnings),
        )
    )
    return BootstrapUnitResult(
        dataset=dataset,
        period=period,
        source_api=source_api,
        status=status,
        page_count=fetched.page_count,
        rows=len(fetched.frame),
        elapsed_seconds=fetched.elapsed_seconds,
        duplicate_count=fetched.duplicate_count,
        schema_hash=fetched.schema_hash,
        first_ts_code=fetched.first_ts_code,
        last_ts_code=fetched.last_ts_code,
        warnings=tuple(dict.fromkeys(warnings)),
        error=error,
        stored_path=stored_path,
    )


def _download_period(
    provider: TushareProvider,
    store: RawParquetStore,
    checkpoints: BootstrapCheckpointStore,
    dataset: str,
    period: str,
    *,
    page_size: int,
    max_pages: int,
    rate_limiter: RateLimiter | None = None,
    progress: Callable[[str], None] | None = None,
) -> BootstrapUnitResult:
    """Compatibility wrapper for callers that still request one serial period."""

    outcome = _fetch_period(
        provider,
        dataset,
        period,
        page_size=page_size,
        max_pages=max_pages,
        rate_limiter=rate_limiter,
        progress=progress,
    )
    return _commit_period(outcome, store, checkpoints)


def _unexpected_failure(
    task: _BootstrapTask,
    checkpoints: BootstrapCheckpointStore,
    exc: Exception,
) -> BootstrapUnitResult:
    error = f"unexpected {type(exc).__name__}: {exc}"
    try:
        checkpoints.append(
            BootstrapCheckpoint(
                dataset=task.dataset,
                period=task.period,
                source_api=task.source_api,
                started_at=utc_now(),
                finished_at=utc_now(),
                status="FAILED",
                error=error,
            )
        )
    except Exception as checkpoint_exc:
        error = f"{error}; checkpoint error: {checkpoint_exc}"
    return BootstrapUnitResult(
        dataset=task.dataset,
        period=task.period,
        source_api=task.source_api,
        status="FAILED",
        error=error,
    )


def _disk_stop_result(task: _BootstrapTask) -> BootstrapUnitResult:
    return BootstrapUnitResult(
        dataset=task.dataset,
        period=task.period,
        source_api=task.source_api,
        status="FAILED",
        error="disk emergency stop: less than 15 GiB free",
    )


def _emit_result(
    progress: Callable[[str], None] | None,
    result: BootstrapUnitResult,
) -> None:
    suffix = f" error={result.error}" if result.error else ""
    _emit(
        progress,
        f"[{result.dataset}][{result.period}] complete status={result.status} "
        f"rows={result.rows} pages={result.page_count}{suffix}",
    )


def bootstrap_datasets(
    provider: TushareProvider | None,
    store: RawParquetStore,
    checkpoints: BootstrapCheckpointStore,
    *,
    datasets: tuple[str, ...] = P0_DATASETS,
    start_year: int = 2012,
    end_year: int,
    resume: bool = True,
    dry_run: bool = False,
    page_size: int = 5000,
    max_pages: int = 100,
    workers: int = 4,
    requests_per_minute: float = 60.0,
    rate_limiter: RateLimiter | None = None,
    progress: Callable[[str], None] | None = None,
) -> BootstrapRunSummary:
    """Download independent report periods with bounded concurrency.

    Workers only fetch data.  The coordinator thread is the sole writer of
    Parquet files and checkpoint records, so a completed checkpoint always
    follows an atomic period-file write.
    """

    if not datasets:
        raise ValueError("at least one dataset is required")
    if workers <= 0:
        raise ValueError("workers must be positive")
    if not math.isfinite(requests_per_minute) or requests_per_minute <= 0:
        raise ValueError("requests_per_minute must be a finite positive number")
    periods = report_periods(start_year, end_year)
    if not dry_run and provider is None:
        raise ValueError("provider is required unless dry_run=True")

    normalized_datasets = tuple(dict.fromkeys(datasets))
    emit = _locked_progress(progress)
    run_started = time.monotonic()
    results_by_index: dict[int, BootstrapUnitResult] = {}
    download_tasks: list[_BootstrapTask] = []
    task_index = 0

    for dataset in normalized_datasets:
        source_api = route_for_dataset(dataset)
        completed = checkpoints.completed_periods(dataset, source_api) if resume else set()
        for period in periods:
            latest = checkpoints.latest(dataset, period) if resume else None
            if resume and period in completed and store.period_exists(dataset, period):
                result = BootstrapUnitResult(
                    dataset=dataset,
                    period=period,
                    source_api=source_api,
                    status="PASS",
                    page_count=int(latest.get("page_count", 0)) if latest else 0,
                    rows=int(latest.get("row_count", 0)) if latest else 0,
                    duplicate_count=int(latest.get("duplicate_count", 0)) if latest else 0,
                    schema_hash=str(latest.get("schema_hash") or "") if latest else "",
                    first_ts_code=latest.get("first_ts_code") if latest else None,
                    last_ts_code=latest.get("last_ts_code") if latest else None,
                    warnings=("RESUMED_SKIP",),
                    skipped=True,
                    stored_path=str(store.period_file(dataset, period)),
                )
                results_by_index[task_index] = result
                _emit(emit, f"[{dataset}][{period}] skip(resume)")
            elif dry_run:
                results_by_index[task_index] = BootstrapUnitResult(
                    dataset=dataset,
                    period=period,
                    source_api=source_api,
                    status="NEEDS_DOWNLOAD",
                )
            else:
                download_tasks.append(
                    _BootstrapTask(task_index, dataset, period, source_api)
                )
            task_index += 1

    if dry_run:
        return BootstrapRunSummary(
            normalized_datasets,
            start_year,
            end_year,
            tuple(results_by_index[index] for index in range(task_index)),
            dry_run=True,
            workers=workers,
            requests_per_minute=requests_per_minute,
            elapsed_seconds=time.monotonic() - run_started,
        )

    assert provider is not None
    limiter = rate_limiter or getattr(provider, "rate_limiter", None)
    if limiter is None:
        limiter = RateLimiter(requests_per_minute)
    limiter_start_requests = int(getattr(limiter, "request_count", 0))
    if isinstance(provider, TushareProvider):
        provider.set_rate_limiter(limiter)

    def record_result(task: _BootstrapTask, result: BootstrapUnitResult) -> None:
        results_by_index[task.index] = result
        _emit_result(emit, result)

    def disk_available() -> bool:
        return check_disk_space(store.data_dir).free_bytes >= EMERGENCY_STOP_FREE_BYTES

    def mark_disk_stopped(start: int) -> None:
        for task in download_tasks[start:]:
            result = _disk_stop_result(task)
            results_by_index[task.index] = result
            _emit_result(emit, result)

    def commit_outcome(task: _BootstrapTask, outcome: _PeriodFetchOutcome) -> None:
        try:
            result = _commit_period(outcome, store, checkpoints)
        except Exception as exc:
            result = _unexpected_failure(task, checkpoints, exc)
        record_result(task, result)

    if workers == 1:
        for position, task in enumerate(download_tasks):
            if not disk_available():
                mark_disk_stopped(position)
                break
            outcome = _fetch_period(
                provider,
                task.dataset,
                task.period,
                page_size=page_size,
                max_pages=max_pages,
                rate_limiter=limiter,
                progress=emit,
            )
            commit_outcome(task, outcome)
    else:
        pending: dict[Future[_PeriodFetchOutcome], _BootstrapTask] = {}
        next_position = 0
        executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="financial-bootstrap",
        )

        def submit_available() -> None:
            nonlocal next_position
            while next_position < len(download_tasks) and len(pending) < workers:
                if not disk_available():
                    mark_disk_stopped(next_position)
                    next_position = len(download_tasks)
                    return
                task = download_tasks[next_position]
                next_position += 1
                future = executor.submit(
                    _fetch_period,
                    provider,
                    task.dataset,
                    task.period,
                    page_size=page_size,
                    max_pages=max_pages,
                    rate_limiter=limiter,
                    progress=emit,
                )
                pending[future] = task

        try:
            submit_available()
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    task = pending.pop(future)
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        record_result(task, _unexpected_failure(task, checkpoints, exc))
                    else:
                        commit_outcome(task, outcome)
                submit_available()
        except KeyboardInterrupt:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            _emit(emit, "bootstrap interrupted; running fetches were cancelled where possible")
            raise
        else:
            executor.shutdown(wait=True)

    limiter_requests = int(getattr(limiter, "request_count", 0))
    return BootstrapRunSummary(
        normalized_datasets,
        start_year,
        end_year,
        tuple(results_by_index[index] for index in range(task_index)),
        dry_run=False,
        workers=workers,
        requests_per_minute=float(getattr(limiter, "requests_per_minute", requests_per_minute)),
        api_requests=max(0, limiter_requests - limiter_start_requests),
        elapsed_seconds=time.monotonic() - run_started,
    )


def render_bootstrap_dry_run(summary: BootstrapRunSummary) -> str:
    periods = report_periods(summary.start_year, summary.end_year)
    rate = (
        f"{summary.requests_per_minute:g} requests/minute"
        if summary.requests_per_minute is not None
        else "not configured"
    )
    lines = [
        "Historical bootstrap dry-run",
        f"datasets={','.join(summary.datasets)}",
        f"periods={len(periods)} ({periods[0]}..{periods[-1]})",
        f"requests_planned={sum(result.status == 'NEEDS_DOWNLOAD' for result in summary.results)}",
        f"workers={summary.workers}; global rate limit={rate}",
        "estimated_time=bounded concurrent requests; actual latency depends on the endpoint",
        "",
        "dataset | period | source_api | status",
        "--- | --- | --- | ---",
    ]
    for result in summary.results:
        lines.append(
            f"{result.dataset} | {result.period} | {result.source_api} | {result.status}"
        )
    return "\n".join(lines)
