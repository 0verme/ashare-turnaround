"""Small, bounded sample synchronization routines."""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import SOURCE_NAME
from ..providers.rate_limit import RateLimiter
from ..providers.tushare import ProviderError, TushareProvider
from ..storage.parquet import RawParquetStore, StoredFile
from ..storage.state import SyncRecord, SyncStateStore
from .specs import API_VALIDATION_ORDER, get_dataset_spec


@dataclass(frozen=True, slots=True)
class FetchResult:
    dataset: str
    params: dict[str, Any]
    frame: pd.DataFrame
    status: str
    rows: int
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class SampleSyncSummary:
    results: tuple[FetchResult, ...]
    stored_files: tuple[StoredFile, ...]


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
    if frame.empty:
        return "empty"
    sample = {
        "columns": [str(column) for column in frame.columns],
        "rows": [frame.iloc[0].to_dict(), frame.iloc[-1].to_dict()],
    }
    payload = json.dumps(sample, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    hashes = tuple(dict.fromkeys(page.schema_hash for page in pages if page.schema_hash))
    all_warnings = list(warnings or [])
    if len(hashes) > 1 and "SCHEMA_DRIFT" not in all_warnings:
        all_warnings.append("SCHEMA_DRIFT")
    return PaginatedFetch(
        frame=frame,
        status=status,
        pages=tuple(pages),
        elapsed_seconds=time.monotonic() - started,
        duplicate_count=_duplicate_identity_count(dataset, frame),
        schema_hash=_schema_hash(frame.columns) if not frame.empty else "",
        first_ts_code=_code_at(frame, 0),
        last_ts_code=_code_at(frame, -1),
        schema_hashes=hashes,
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

        if page.empty:
            if not frames:
                return _build_paginated_fetch(
                    dataset,
                    frames,
                    pages,
                    started,
                    status="EMPTY",
                    warnings=["empty_response"],
                )
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
        frame = fetch_paginated(
            provider,
            dataset,
            params,
            page_size=page_size,
            max_pages=max_pages,
        )
        finished_at = utc_now()
        status = "empty" if frame.empty else "success"
        state.append(
            SyncRecord(
                dataset=dataset,
                params=params,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                rows=len(frame),
            )
        )
        return FetchResult(dataset, params, frame, status, len(frame))
    except ProviderError as exc:
        finished_at = utc_now()
        state.append(
            SyncRecord(
                dataset=dataset,
                params=params,
                started_at=started_at,
                finished_at=finished_at,
                status="failed",
                error_type=exc.error_type,
                error_message=exc.error_message,
            )
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

    if not 3 <= len(codes) <= 5:
        raise ValueError("sample must contain 3 to 5 stock codes")
    request_groups = _sample_requests(codes, start_date, end_date, limit)
    results: list[FetchResult] = []
    stored_files: list[StoredFile] = []

    for dataset, requests in request_groups.items():
        spec = get_dataset_spec(dataset)
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
            if result.status == "success":
                frames.append(_with_provenance(result.frame))
        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True, sort=False)
        try:
            stored_files.extend(store.write(dataset, combined, spec))
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
            state.append(
                SyncRecord(
                    dataset=dataset,
                    params={"operation": "write_sample", "request_count": len(requests)},
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    status="failed",
                    rows=0,
                    error_type="storage",
                    error_message=str(exc),
                )
            )
    return SampleSyncSummary(tuple(results), tuple(stored_files))
