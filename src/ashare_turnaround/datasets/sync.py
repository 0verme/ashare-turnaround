"""Small, bounded sample synchronization routines."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import SOURCE_NAME
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


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def fetch_paginated(
    provider: TushareProvider,
    dataset: str,
    params: dict[str, Any],
    *,
    page_size: int = 5000,
    max_pages: int = 100,
) -> pd.DataFrame:
    """Fetch bounded pages using Tushare's conventional limit/offset params."""

    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    query = dict(params)
    requested_limit = int(query.get("limit", page_size))
    if requested_limit <= 0:
        raise ValueError("limit must be positive")
    offset = int(query.get("offset", 0))
    frames: list[pd.DataFrame] = []
    seen_signatures: set[tuple[Any, ...]] = set()

    for _ in range(max_pages):
        page_params = dict(query)
        page_params["limit"] = requested_limit
        page_params["offset"] = offset
        page = provider.call(dataset, **page_params)
        if page.empty:
            break
        signature = (
            len(page),
            tuple(str(value) for value in page.iloc[0].tolist()),
            tuple(str(value) for value in page.iloc[-1].tolist()),
        )
        if signature in seen_signatures:
            break
        seen_signatures.add(signature)
        frames.append(page)
        if len(page) < requested_limit:
            break
        offset += requested_limit
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


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
