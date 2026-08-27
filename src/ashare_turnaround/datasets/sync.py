"""Small, bounded sample synchronization routines."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from ..config import SOURCE_NAME
from ..providers.rate_limit import RateLimiter
from ..providers.tushare import ProviderError, TushareProvider
from ..storage.parquet import RawParquetStore, StoredFile
from ..storage.state import SyncRecord, SyncStateStore
from .specs import API_VALIDATION_ORDER, get_dataset_spec

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchResult:
    dataset: str
    params: dict[str, Any]
    frame: pd.DataFrame
    status: str
    rows: int
    error_type: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SampleSyncSummary:
    results: tuple[FetchResult, ...]
    stored_files: tuple[StoredFile, ...]
    storage_errors: tuple[tuple[str, str], ...] = ()

    @property
    def failures(self) -> tuple[FetchResult, ...]:
        return tuple(result for result in self.results if result.status in {"failed", "partial"})


@dataclass(frozen=True, slots=True)
class DailySyncResult:
    """Outcome for one dataset in an idempotent date-scoped synchronization."""

    dataset: str
    source_api: str
    requested_date: str
    effective_date: str | None
    status: str
    rows: int = 0
    stored_files: tuple[StoredFile, ...] = ()
    error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DailySyncSummary:
    requested_date: str
    effective_date: str | None
    generated_at: str
    results: tuple[DailySyncResult, ...]

    @property
    def failures(self) -> tuple[DailySyncResult, ...]:
        return tuple(result for result in self.results if result.status in {"failed", "partial"})

    @property
    def status(self) -> str:
        statuses = {result.status for result in self.results}
        if "failed" in statuses:
            return "failed"
        if "partial" in statuses or "pending" in statuses:
            return "partial"
        if statuses and statuses <= {"not_due"}:
            return "not_due"
        return "success"


@dataclass(frozen=True, slots=True)
class PageAudit:
    """Operational facts for one request in a paginated API read."""

    page_number: int
    offset: int
    rows: int
    elapsed_seconds: float
    schema_hash: str
    first_ts_code: str | None = None
    last_ts_code: str | None = None
    signature: str | None = None
    total_rows: int | None = None


@dataclass(frozen=True, slots=True)
class PaginatedFetch:
    """A paginated response plus enough evidence to audit completeness."""

    frame: pd.DataFrame
    status: str
    pages: tuple[PageAudit, ...]
    elapsed_seconds: float
    duplicate_count: int = 0
    schema_hash: str = ""
    first_ts_code: str | None = None
    last_ts_code: str | None = None
    schema_hashes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    total_rows: int | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)


class PaginationError(RuntimeError):
    """Raised when a paginated response cannot be proven complete."""

    def __init__(
        self,
        message: str,
        *,
        partial: PaginatedFetch,
    ) -> None:
        self.partial = partial
        super().__init__(message)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _schema_hash(columns: Any) -> str:
    payload = json.dumps(sorted(str(column) for column in columns), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _page_signature(frame: pd.DataFrame) -> str:
    """Hash the complete page, not only its first and last rows."""

    if frame.empty:
        return "empty"
    try:
        values = pd.util.hash_pandas_object(frame, index=False).to_numpy(copy=False)
        payload = _schema_hash(frame.columns).encode("ascii") + b":" + values.tobytes()
    except (TypeError, ValueError):
        payload = json.dumps(
            frame.to_dict(orient="records"),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _code_at(frame: pd.DataFrame, position: int) -> str | None:
    if "ts_code" not in frame.columns or frame.empty:
        return None
    value = frame.iloc[position]["ts_code"]
    if pd.isna(value):
        return None
    return str(value)


def _call_page(
    provider: TushareProvider,
    dataset: str,
    params: dict[str, Any],
    rate_limiter: RateLimiter | None,
) -> pd.DataFrame:
    """Use an external limiter only when the provider does not own this one."""

    if rate_limiter is not None and getattr(provider, "rate_limiter", None) is not rate_limiter:
        rate_limiter.acquire()
    return provider.call(dataset, **params)


def _duplicate_identity_count(dataset: str, frame: pd.DataFrame) -> int:
    spec = get_dataset_spec(dataset)
    if not spec.primary_keys or not set(spec.primary_keys).issubset(frame.columns):
        return 0
    return int(frame.duplicated(list(spec.primary_keys), keep=False).sum())


def _build_paginated_fetch(
    dataset: str,
    frames: list[pd.DataFrame],
    pages: list[PageAudit],
    started: float,
    *,
    status: str,
    warnings: list[str] | None = None,
    total_rows: int | None = None,
) -> PaginatedFetch:
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    schema_hashes = tuple(dict.fromkeys(page.schema_hash for page in pages if page.schema_hash))
    all_warnings = list(warnings or [])
    if len(schema_hashes) > 1 and "schema_drift" not in all_warnings:
        all_warnings.append("schema_drift")
    return PaginatedFetch(
        frame=frame,
        status=status,
        pages=tuple(pages),
        elapsed_seconds=time.monotonic() - started,
        duplicate_count=_duplicate_identity_count(dataset, frame),
        schema_hash=_schema_hash(frame.columns) if not frame.empty else "",
        first_ts_code=_code_at(frame, 0),
        last_ts_code=_code_at(frame, -1),
        schema_hashes=schema_hashes,
        warnings=tuple(all_warnings),
        total_rows=total_rows,
    )


def fetch_paginated_audited(
    provider: TushareProvider,
    dataset: str,
    params: dict[str, Any],
    *,
    page_size: int = 5000,
    max_pages: int = 100,
    rate_limiter: RateLimiter | None = None,
) -> PaginatedFetch:
    """Fetch pages and fail closed when API pagination is not provably complete."""

    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    query = dict(params)
    requested_limit = int(query.get("limit", page_size))
    if requested_limit <= 0:
        raise ValueError("limit must be positive")
    offset = int(query.get("offset", 0))
    if offset < 0:
        raise ValueError("offset must be non-negative")
    frames: list[pd.DataFrame] = []
    pages: list[PageAudit] = []
    seen_signatures: set[str] = set()
    started = time.monotonic()

    for page_number in range(1, max_pages + 1):
        expected_offset = offset
        page_params = dict(query)
        page_params["limit"] = requested_limit
        page_params["offset"] = expected_offset
        page_started = time.monotonic()
        try:
            page = _call_page(provider, dataset, page_params, rate_limiter)
        except ProviderError as exc:
            raise ProviderError(
                dataset,
                exc.error_type,
                f"page={page_number} offset={expected_offset}: {exc.error_message}",
                attempts=exc.attempts,
            ) from exc
        page_elapsed = time.monotonic() - page_started
        page_hash = _schema_hash(page.columns) if not page.empty else ""
        page_audit = PageAudit(
            page_number=page_number,
            offset=expected_offset,
            rows=len(page),
            elapsed_seconds=page_elapsed,
            schema_hash=page_hash,
            first_ts_code=_code_at(page, 0),
            last_ts_code=_code_at(page, -1),
            signature=_page_signature(page),
        )
        pages.append(page_audit)
        LOGGER.info(
            "pagination_page dataset=%s page=%d offset=%d rows=%d elapsed=%.3f",
            dataset,
            page_number,
            expected_offset,
            len(page),
            page_elapsed,
        )

        if page.empty:
            if not frames:
                return _build_paginated_fetch(
                    dataset, frames, pages, started, status="EMPTY", warnings=["empty_response"]
                )
            # An empty page after a full page is the normal terminal sentinel.
            if len(frames[-1]) == requested_limit:
                return _build_paginated_fetch(
                    dataset,
                    frames,
                    pages,
                    started,
                    status="PASS",
                    warnings=["terminal_empty_after_full_page"],
                )
            raise PaginationError(
                "unexpected empty page after a short page",
                partial=_build_paginated_fetch(
                    dataset,
                    frames,
                    pages,
                    started,
                    status="PARTIAL",
                    warnings=["unexpected_empty_page"],
                ),
            )

        if len(page) > requested_limit:
            raise PaginationError(
                f"page {page_number} returned more rows than requested limit",
                partial=_build_paginated_fetch(
                    dataset,
                    frames,
                    pages,
                    started,
                    status="PARTIAL",
                    warnings=["page_exceeds_limit"],
                ),
            )
        signature = page_audit.signature or ""
        if signature in seen_signatures:
            raise PaginationError(
                f"duplicate page signature at page {page_number}",
                partial=_build_paginated_fetch(
                    dataset,
                    frames,
                    pages,
                    started,
                    status="PARTIAL",
                    warnings=["duplicate_page"],
                ),
            )
        seen_signatures.add(signature)
        frames.append(page)
        if len(page) < requested_limit:
            return _build_paginated_fetch(dataset, frames, pages, started, status="PASS")
        offset = expected_offset + requested_limit

    return _build_paginated_fetch(
        dataset,
        frames,
        pages,
        started,
        status="PARTIAL",
        warnings=["max_pages_reached"],
    )


def fetch_paginated(
    provider: TushareProvider,
    dataset: str,
    params: dict[str, Any],
    *,
    page_size: int = 5000,
    max_pages: int = 100,
    rate_limiter: RateLimiter | None = None,
) -> pd.DataFrame:
    """Compatibility wrapper returning only a complete paginated frame."""

    result = fetch_paginated_audited(
        provider,
        dataset,
        params,
        page_size=page_size,
        max_pages=max_pages,
        rate_limiter=rate_limiter,
    )
    if result.status == "PARTIAL":
        raise PaginationError("paginated response is partial", partial=result)
    return result.frame


def _record_fetch(
    state: SyncStateStore,
    dataset: str,
    params: dict[str, Any],
    started_at: str,
    status: str,
    *,
    rows: int = 0,
    error_type: str | None = None,
    error_message: str | None = None,
    warnings: tuple[str, ...] = (),
) -> None:
    state.append(
        SyncRecord(
            dataset=dataset,
            params=params,
            started_at=started_at,
            finished_at=utc_now(),
            status=status,
            rows=rows,
            error_type=error_type,
            error_message=error_message,
            warnings=warnings,
        )
    )


def _fetch_and_record(
    provider: TushareProvider,
    dataset: str,
    params: dict[str, Any],
    state: SyncStateStore,
    *,
    page_size: int,
    max_pages: int,
) -> FetchResult:
    started_at = utc_now()
    try:
        fetched = fetch_paginated_audited(
            provider,
            dataset,
            params,
            page_size=page_size,
            max_pages=max_pages,
        )
        if fetched.status == "PARTIAL":
            raise PaginationError("paginated response is partial", partial=fetched)
        frame = fetched.frame
        status = "empty" if fetched.status == "EMPTY" else "success"
        _record_fetch(
            state,
            dataset,
            params,
            started_at,
            status,
            rows=len(frame),
            warnings=fetched.warnings,
        )
        return FetchResult(
            dataset,
            params,
            frame,
            status,
            len(frame),
            warnings=fetched.warnings,
        )
    except PaginationError as exc:
        partial = exc.partial
        _record_fetch(
            state,
            dataset,
            params,
            started_at,
            "partial",
            rows=len(partial.frame),
            error_type="pagination",
            error_message=str(exc),
            warnings=partial.warnings,
        )
        return FetchResult(
            dataset,
            params,
            partial.frame,
            "partial",
            len(partial.frame),
            "pagination",
            str(exc),
            partial.warnings,
        )
    except ProviderError as exc:
        _record_fetch(
            state,
            dataset,
            params,
            started_at,
            "failed",
            error_type=exc.error_type,
            error_message=exc.error_message,
        )
        return FetchResult(
            dataset,
            params,
            pd.DataFrame(),
            "failed",
            0,
            exc.error_type,
            exc.error_message,
        )


def _with_provenance(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["retrieved_at"] = utc_now()
    output["source"] = SOURCE_NAME
    return output


def _sample_requests(
    codes: tuple[str, ...], start_date: str, end_date: str, limit: int
) -> dict[str, list[dict[str, Any]]]:
    requests: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for code in codes:
        requests["stock_basic"].append({"ts_code": code, "limit": 1})
    requests["trade_cal"].append(
        {"exchange": "SSE", "start_date": start_date, "end_date": end_date, "limit": 500}
    )
    for dataset in API_VALIDATION_ORDER:
        if dataset in {"stock_basic", "trade_cal"}:
            continue
        for code in codes:
            params: dict[str, Any] = {"ts_code": code, "limit": limit}
            if dataset == "fina_mainbz":
                params["period"] = end_date
            requests[dataset].append(params)
    return dict(requests)


def sample_request_plan(
    codes: tuple[str, ...],
    start_date: str = "20240101",
    end_date: str = "20241231",
    limit: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    """Build the bounded sample plan without constructing a provider."""

    if not 3 <= len(codes) <= 5:
        raise ValueError("sample must contain 3 to 5 stock codes")
    if limit <= 0:
        raise ValueError("limit must be positive")
    return _sample_requests(codes, start_date, end_date, limit)


def sync_sample(
    provider: TushareProvider,
    store: RawParquetStore,
    state: SyncStateStore,
    *,
    codes: tuple[str, ...],
    start_date: str = "20240101",
    end_date: str = "20241231",
    limit: int = 100,
    max_pages: int = 1,
) -> SampleSyncSummary:
    """Fetch a few codes and replace each dataset's sample partitions once."""

    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    request_groups = sample_request_plan(codes, start_date, end_date, limit)
    results: list[FetchResult] = []
    stored_files: list[StoredFile] = []
    storage_errors: list[tuple[str, str]] = []

    for dataset, requests in request_groups.items():
        spec = get_dataset_spec(dataset)
        dataset_results: list[FetchResult] = []
        frames: list[pd.DataFrame] = []
        for params in requests:
            result = _fetch_and_record(
                provider,
                dataset,
                params,
                state,
                page_size=limit,
                max_pages=max_pages,
            )
            results.append(result)
            dataset_results.append(result)
            if result.status == "success":
                frames.append(_with_provenance(result.frame))

        # Never replace an existing partition with a partial sample.  A caller
        # can inspect the failed request and retry the complete dataset later.
        if any(result.status in {"failed", "partial"} for result in dataset_results):
            LOGGER.warning("sample_dataset_not_stored dataset=%s reason=incomplete_fetch", dataset)
            continue
        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True, sort=False)
        try:
            stored_files.extend(store.write(dataset, combined, spec, source_api=dataset))
            state.append(
                SyncRecord(
                    dataset=dataset,
                    params={"operation": "write_sample", "request_count": len(requests)},
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    status="stored",
                    rows=len(combined),
                )
            )
        except Exception as exc:
            message = str(exc)
            storage_errors.append((dataset, message))
            state.append(
                SyncRecord(
                    dataset=dataset,
                    params={"operation": "write_sample", "request_count": len(requests)},
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    status="failed",
                    rows=0,
                    error_type="storage",
                    error_message=message,
                )
            )
            LOGGER.exception("sample_dataset_storage_failed dataset=%s", dataset)
    return SampleSyncSummary(tuple(results), tuple(stored_files), tuple(storage_errors))


_DAILY_DATASETS: tuple[str, ...] = (
    "stock_basic",
    "trade_cal",
    "daily",
    "daily_basic",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)
_DAILY_MARKET_DATASETS = {"daily", "daily_basic"}
_DAILY_FINANCIAL_DATASETS = {"income", "balancesheet", "cashflow", "fina_indicator"}


def _normalized_date(value: str | date | pd.Timestamp) -> tuple[str, pd.Timestamp]:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid sync date: {value!r}")
    timestamp = pd.Timestamp(parsed).normalize()
    return timestamp.strftime("%Y%m%d"), timestamp


def latest_valid_trading_date(
    calendar: pd.DataFrame,
    requested_date: str | date | pd.Timestamp,
) -> str | None:
    """Return the requested date when the calendar explicitly marks it open."""

    requested, timestamp = _normalized_date(requested_date)
    if calendar.empty or not {"cal_date", "is_open"}.issubset(calendar.columns):
        return None
    dates = pd.to_datetime(calendar["cal_date"], errors="coerce").dt.normalize()
    open_mask = pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)
    if bool((dates.eq(timestamp) & open_mask).any()):
        return requested
    return None


def _daily_params(dataset: str, requested: str, effective: str | None) -> dict[str, Any]:
    if dataset == "stock_basic":
        return {"list_status": "L", "limit": 5000}
    if dataset == "trade_cal":
        return {"exchange": "SSE", "start_date": requested, "end_date": requested, "limit": 10}
    if dataset in _DAILY_MARKET_DATASETS:
        return {"trade_date": effective, "limit": 5000}
    if dataset in _DAILY_FINANCIAL_DATASETS:
        # The provider contract intentionally uses the disclosure date as the
        # incremental boundary, while the PIT layer retains report periods and
        # revisions in the raw rows.
        return {"ann_date": requested, "limit": 5000}
    raise KeyError(f"unsupported daily dataset: {dataset}")


def _daily_param_sets(
    dataset: str, requested: str, effective: str | None
) -> tuple[dict[str, Any], ...]:
    if dataset == "stock_basic":
        return tuple({"list_status": status, "limit": 5000} for status in ("L", "D", "P"))
    return (_daily_params(dataset, requested, effective),)


def _combine_daily_fetches(fetches: list[PaginatedFetch]) -> PaginatedFetch:
    frames = [fetch.frame for fetch in fetches if not fetch.frame.empty]
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if not frame.empty and "ts_code" in frame.columns:
        frame = frame.drop_duplicates("ts_code", keep="last", ignore_index=True)
    statuses = {fetch.status for fetch in fetches}
    status = "PASS" if statuses <= {"PASS", "EMPTY"} and not frame.empty else "EMPTY"
    warnings = tuple(
        dict.fromkeys(warning for fetch in fetches for warning in fetch.warnings)
    )
    return PaginatedFetch(
        frame=frame,
        status=status,
        pages=tuple(page for fetch in fetches for page in fetch.pages),
        elapsed_seconds=sum(fetch.elapsed_seconds for fetch in fetches),
        duplicate_count=sum(fetch.duplicate_count for fetch in fetches),
        schema_hash=_schema_hash(frame.columns),
        first_ts_code=str(frame.iloc[0]["ts_code"])
        if not frame.empty and "ts_code" in frame.columns
        else None,
        last_ts_code=str(frame.iloc[-1]["ts_code"])
        if not frame.empty and "ts_code" in frame.columns
        else None,
        schema_hashes=tuple(
            dict.fromkeys(value for fetch in fetches for value in fetch.schema_hashes)
        ),
        warnings=warnings,
        total_rows=len(frame),
    )


def _daily_state(
    state: SyncStateStore,
    result: DailySyncResult,
) -> None:
    state.append(
        SyncRecord(
            dataset=result.dataset,
            params={
                "operation": "daily_sync",
                "requested_date": result.requested_date,
                "effective_date": result.effective_date,
                "source_api": result.source_api,
            },
            started_at=utc_now(),
            finished_at=utc_now(),
            status=result.status,
            rows=result.rows,
            error_type="daily_sync" if result.error else None,
            error_message=result.error,
            warnings=result.warnings,
        )
    )


def sync_daily(
    provider: TushareProvider,
    store: RawParquetStore,
    state: SyncStateStore,
    *,
    requested_date: str | date | pd.Timestamp,
    datasets: tuple[str, ...] = _DAILY_DATASETS,
    page_size: int = 5000,
    max_pages: int = 100,
) -> DailySyncSummary:
    """Synchronize one requested date and expose incomplete source states.

    A non-trading day is a successful calendar decision but is not represented
    as missing market data.  Empty or partial source responses remain visible
    as ``pending``/``partial`` and are never treated as a complete run.
    """

    requested, _ = _normalized_date(requested_date)
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    selected = tuple(dict.fromkeys(datasets))
    if "trade_cal" not in selected:
        selected = ("trade_cal", *selected)
    results: list[DailySyncResult] = []

    calendar_params = _daily_params("trade_cal", requested, requested)
    calendar_params["limit"] = page_size
    try:
        calendar_fetch = fetch_paginated_audited(
            provider,
            "trade_cal",
            calendar_params,
            page_size=page_size,
            max_pages=max_pages,
        )
    except ProviderError as exc:
        result = DailySyncResult(
            "trade_cal", "trade_cal", requested, None, "failed", error=exc.error_message
        )
        _daily_state(state, result)
        return DailySyncSummary(requested, None, utc_now(), (result,))
    except PaginationError as exc:
        result = DailySyncResult(
            "trade_cal",
            "trade_cal",
            requested,
            None,
            "partial",
            rows=len(exc.partial.frame),
            error=str(exc),
            warnings=exc.partial.warnings,
        )
        _daily_state(state, result)
        return DailySyncSummary(requested, None, utc_now(), (result,))

    calendar_status = (
        "success"
        if calendar_fetch.status == "PASS" and not calendar_fetch.frame.empty
        else "pending"
    )
    effective = (
        latest_valid_trading_date(calendar_fetch.frame, requested)
        if calendar_status == "success"
        else None
    )
    calendar_files: tuple[StoredFile, ...] = ()
    calendar_error: str | None = None
    if calendar_status == "success":
        try:
            calendar_files = tuple(
                store.write_incremental(
                    "trade_cal",
                    calendar_fetch.frame,
                    get_dataset_spec("trade_cal"),
                    retrieved_at=utc_now(),
                    source=SOURCE_NAME,
                    source_api="trade_cal",
                )
            )
        except Exception as exc:
            calendar_status = "failed"
            calendar_error = f"storage: {exc}"
    elif calendar_fetch.status == "EMPTY":
        calendar_error = "trade calendar returned no rows"
    calendar_result = DailySyncResult(
        "trade_cal",
        "trade_cal",
        requested,
        effective,
        calendar_status,
        rows=len(calendar_fetch.frame),
        stored_files=calendar_files,
        error=calendar_error,
        warnings=calendar_fetch.warnings,
    )
    _daily_state(state, calendar_result)
    results.append(calendar_result)

    for dataset in selected:
        if dataset == "trade_cal":
            continue
        source_api = dataset
        if dataset in _DAILY_MARKET_DATASETS and effective is None:
            result = DailySyncResult(dataset, source_api, requested, None, "not_due")
            _daily_state(state, result)
            results.append(result)
            continue
        param_sets = _daily_param_sets(dataset, requested, effective)
        for params in param_sets:
            params["limit"] = page_size
        try:
            fetches = [
                fetch_paginated_audited(
                    provider,
                    source_api,
                    params,
                    page_size=page_size,
                    max_pages=max_pages,
                )
                for params in param_sets
            ]
            fetched = _combine_daily_fetches(fetches) if len(fetches) > 1 else fetches[0]
        except ProviderError as exc:
            result = DailySyncResult(
                dataset, source_api, requested, effective, "failed", error=exc.error_message
            )
            _daily_state(state, result)
            results.append(result)
            continue
        except PaginationError as exc:
            result = DailySyncResult(
                dataset,
                source_api,
                requested,
                effective,
                "partial",
                rows=len(exc.partial.frame),
                error=str(exc),
                warnings=exc.partial.warnings,
            )
            _daily_state(state, result)
            results.append(result)
            continue

        if fetched.status == "EMPTY":
            result = DailySyncResult(
                dataset,
                source_api,
                requested,
                effective,
                "pending",
                error="source returned no rows; completeness is unknown",
                warnings=fetched.warnings,
            )
            _daily_state(state, result)
            results.append(result)
            continue
        if fetched.status != "PASS":
            result = DailySyncResult(
                dataset,
                source_api,
                requested,
                effective,
                "partial",
                rows=len(fetched.frame),
                error="pagination did not prove a complete response",
                warnings=fetched.warnings,
            )
            _daily_state(state, result)
            results.append(result)
            continue
        try:
            frame_to_store = fetched.frame.copy()
            if dataset == "stock_basic":
                # The API returns a current snapshot even when an operator
                # requests a historical sync date.  Never label it with that
                # requested date and thereby imply historical PIT status.
                frame_to_store["reference_snapshot_date"] = datetime.now(UTC).strftime("%Y%m%d")
                frame_to_store["reference_semantics"] = "current_snapshot"
            stored = tuple(
                store.write_incremental(
                    dataset,
                    frame_to_store,
                    get_dataset_spec(dataset),
                    retrieved_at=utc_now(),
                    source=SOURCE_NAME,
                    source_api=source_api,
                )
            )
            result = DailySyncResult(
                dataset,
                source_api,
                requested,
                effective,
                "success",
                rows=len(fetched.frame),
                stored_files=stored,
                warnings=fetched.warnings,
            )
        except Exception as exc:
            result = DailySyncResult(
                dataset,
                source_api,
                requested,
                effective,
                "failed",
                rows=len(fetched.frame),
                error=f"storage: {exc}",
            )
        _daily_state(state, result)
        results.append(result)
    return DailySyncSummary(requested, effective, utc_now(), tuple(results))
