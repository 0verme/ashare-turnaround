"""Resumable full-history bootstrap for Market / Reference data.

Financial P0 uses report-period units and ``bootstrap.py``.  This module is a
separate orchestration path because market APIs have different query semantics:
calendar/reference data are snapshots or ranges, while price data are fetched
by month.  Workers only fetch; one coordinator atomically writes a unit and
then appends its durable checkpoint.
"""

from __future__ import annotations

import calendar
import logging
import math
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any

import pandas as pd

from ..config import DEFAULT_BENCHMARK_CODE, SOURCE_NAME
from ..dates import normalize_date_series
from ..providers.rate_limit import RateLimiter
from ..providers.tushare import ProviderError, TushareProvider
from ..quality import check_frame_quality
from ..storage.guards import EMERGENCY_STOP_FREE_BYTES, check_disk_space
from ..storage.parquet import RawParquetStore
from ..storage.state import MarketBootstrapCheckpoint, MarketCheckpointStore
from .specs import get_dataset_spec
from .sync import (
    PaginatedFetch,
    PaginationError,
    _schema_hash,
    fetch_paginated_audited,
    utc_now,
)

LOGGER = logging.getLogger(__name__)

RESEARCH_START_DATE = "20120101"

# This is deliberately a small, explicit corpus.  ``namechange`` and
# ``suspend_d`` are included because a current stock_basic status cannot prove
# historical ST/name or suspension state.  No ownership, margin, analyst, or
# intraday endpoints are pulled by this phase.
MARKET_BOOTSTRAP_DATASETS: tuple[str, ...] = (
    "trade_cal",
    "stock_basic",
    "index_basic",
    "namechange",
    "suspend_d",
    "daily",
    "daily_basic",
    "index_daily",
)
# The live namechange endpoint was probed before implementation.  Its
# compatible response exposes repeated identical rows without a stable source
# identity, so it remains an explicit opt-in dataset rather than being allowed
# to block the core corpus.  The final report records that PIT gap.
DEFAULT_MARKET_BOOTSTRAP_DATASETS: tuple[str, ...] = (
    "trade_cal",
    "stock_basic",
    "index_basic",
    "suspend_d",
    "daily",
    "daily_basic",
    "index_daily",
)
DEFAULT_MARKET_EXCHANGES: tuple[str, ...] = ("SSE", "SZSE")
REFERENCE_DATASETS = {"trade_cal", "stock_basic", "index_basic", "namechange", "suspend_d"}
PRICE_DATASETS = {"daily", "daily_basic", "index_daily"}

# Explicit fields prevent the default endpoint projection from silently
# omitting list/delist status or exchange information from the reference
# snapshot.  The values remain a current snapshot; they are not historical
# status observations.
_STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,cnspell,market,exchange,list_date,"
    "delist_date,list_status,is_hs,act_name,act_ent_type"
)
_INDEX_BASIC_FIELDS = "ts_code,name,market,publisher,category,base_date,base_point,list_date"

# Compact Zstandard row-width fallbacks used only by the dry-run/capacity
# estimate.  A real capacity report can replace them with bounded probe
# measurements; they are intentionally conservative rather than source facts.
_DEFAULT_BYTES_PER_ROW: dict[str, float] = {
    "trade_cal": 64.0,
    "stock_basic": 320.0,
    "index_basic": 220.0,
    "namechange": 180.0,
    "suspend_d": 90.0,
    "daily": 72.0,
    "daily_basic": 125.0,
    "index_daily": 72.0,
}


def _normalized_date(value: str | date | datetime | pd.Timestamp, *, name: str) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid {name}: {value!r}")
    return pd.Timestamp(parsed).normalize().strftime("%Y%m%d")


def default_market_end_date(today: date | None = None) -> str:
    """Return the same conservative annual boundary used by Financial P0.

    With the current project clock this resolves to ``20251231``.  It is not a
    hard-coded production endpoint: operators can pass an explicit end date
    when a newer financial/research boundary is approved.
    """

    reference = today or datetime.now(UTC).date()
    return f"{reference.year - 1:04d}1231"


def _validate_benchmark_code(value: str) -> str:
    code = str(value).strip().upper()
    if len(code) != 9 or code[6] != "." or not code[:6].isdigit() or not code[7:].isalpha():
        raise ValueError(f"invalid benchmark code: {value!r}")
    return code


def _month_units(start: str, end: str) -> Iterable[tuple[str, str, str]]:
    first = pd.Timestamp(start).date()
    last = pd.Timestamp(end).date()
    cursor = date(first.year, first.month, 1)
    while cursor <= last:
        month_last = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]
        )
        unit_start = max(first, cursor)
        unit_end = min(last, month_last)
        yield (
            f"{cursor.year:04d}-{cursor.month:02d}",
            unit_start.strftime("%Y%m%d"),
            unit_end.strftime("%Y%m%d"),
        )
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _estimated_open_days(start: str, end: str, trading_days_per_year: int = 245) -> int:
    days = (pd.Timestamp(end).date() - pd.Timestamp(start).date()).days + 1
    return max(1, math.ceil(days * trading_days_per_year / 365.25))


def _estimated_requests(rows: float, page_size: int) -> int:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if rows <= 0:
        return 1
    pages = math.ceil(rows / page_size)
    # A response exactly equal to the requested limit needs a terminal request
    # to prove that there is no next page.
    return max(1, pages + (1 if rows % page_size == 0 else 0))


@dataclass(frozen=True, slots=True)
class MarketBootstrapUnit:
    """One durable market/reference request unit."""

    dataset: str
    unit: str
    source_api: str
    params: dict[str, Any]
    storage_parts: tuple[str, ...]
    expected_start: str | None
    expected_end: str | None
    estimated_rows: int
    estimated_size_bytes: int
    estimated_requests: int
    partition_strategy: str

    @property
    def storage_key(self) -> str:
        return "/".join(self.storage_parts)


@dataclass(frozen=True, slots=True)
class MarketBootstrapUnitResult:
    dataset: str
    unit: str
    source_api: str
    status: str
    requested_start: str | None = None
    requested_end: str | None = None
    page_count: int = 0
    request_count: int = 0
    rows: int = 0
    size_bytes: int = 0
    schema_hash: str = ""
    duplicate_count: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None
    skipped: bool = False
    stored_path: str | None = None


@dataclass(frozen=True, slots=True)
class MarketBootstrapSummary:
    start_date: str
    end_date: str
    benchmark_code: str
    snapshot_date: str
    datasets: tuple[str, ...]
    units: tuple[MarketBootstrapUnit, ...]
    results: tuple[MarketBootstrapUnitResult, ...]
    dry_run: bool = False
    workers: int = 1
    requests_per_minute: float | None = None
    api_requests: int = 0
    elapsed_seconds: float = 0.0

    @property
    def planned_units(self) -> int:
        return len(self.units)

    @property
    def existing_units(self) -> int:
        return sum(result.skipped for result in self.results)

    @property
    def remaining_units(self) -> int:
        return sum(
            not result.skipped and result.status == "NEEDS_DOWNLOAD" for result in self.results
        )

    @property
    def requested_units(self) -> int:
        return self.planned_units

    @property
    def request_estimate(self) -> int:
        return sum(unit.estimated_requests for unit in self.units if not self._is_skipped(unit))

    def _is_skipped(self, unit: MarketBootstrapUnit) -> bool:
        for result in self.results:
            if result.dataset == unit.dataset and result.unit == unit.unit:
                return result.skipped
        return False

    @property
    def downloaded_rows(self) -> int:
        return sum(result.rows for result in self.results if not result.skipped)

    @property
    def total_rows(self) -> int:
        return sum(result.rows for result in self.results)

    @property
    def failures(self) -> tuple[MarketBootstrapUnitResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.status in {"FAILED", "PARTIAL", "UNKNOWN_EMPTY"}
        )

    @property
    def completed_count(self) -> int:
        return sum(result.status == "PASS" and not result.skipped for result in self.results)

    @property
    def skipped_count(self) -> int:
        return sum(result.skipped for result in self.results)


def _unit(
    dataset: str,
    unit: str,
    source_api: str,
    params: dict[str, Any],
    storage_parts: tuple[str, ...],
    *,
    expected_start: str | None,
    expected_end: str | None,
    estimated_rows: int,
    estimated_requests: int | None = None,
    bytes_per_row: float | None = None,
    partition_strategy: str,
) -> MarketBootstrapUnit:
    row_width = bytes_per_row or _DEFAULT_BYTES_PER_ROW[dataset]
    return MarketBootstrapUnit(
        dataset=dataset,
        unit=unit,
        source_api=source_api,
        params=dict(params),
        storage_parts=storage_parts,
        expected_start=expected_start,
        expected_end=expected_end,
        estimated_rows=max(0, int(estimated_rows)),
        estimated_size_bytes=max(0, math.ceil(max(0, estimated_rows) * row_width)),
        estimated_requests=estimated_requests
        if estimated_requests is not None
        else _estimated_requests(estimated_rows, 5000),
        partition_strategy=partition_strategy,
    )


def build_market_bootstrap_plan(
    start_date: str | date | datetime | pd.Timestamp = RESEARCH_START_DATE,
    end_date: str | date | datetime | pd.Timestamp | None = None,
    *,
    datasets: tuple[str, ...] = DEFAULT_MARKET_BOOTSTRAP_DATASETS,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    exchanges: tuple[str, ...] = DEFAULT_MARKET_EXCHANGES,
    snapshot_date: str | date | datetime | pd.Timestamp | None = None,
    page_size: int = 5000,
    estimated_company_count: int = 5500,
    trading_days_per_year: int = 245,
) -> tuple[MarketBootstrapUnit, ...]:
    """Build a side-effect-free market/reference plan.

    Daily and daily_basic are deliberately month units.  This keeps the
    durable retry granularity at roughly 168 units over 2012–2025 instead of
    creating one tiny file per trading day, while preserving a date field for
    coverage checks and DuckDB filtering.
    """

    start = _normalized_date(start_date, name="start_date")
    end = _normalized_date(end_date or default_market_end_date(), name="end_date")
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if estimated_company_count <= 0:
        raise ValueError("estimated_company_count must be positive")
    if trading_days_per_year <= 0:
        raise ValueError("trading_days_per_year must be positive")
    benchmark = _validate_benchmark_code(benchmark_code)
    selected = tuple(dict.fromkeys(datasets))
    unknown = sorted(set(selected).difference(MARKET_BOOTSTRAP_DATASETS))
    if unknown:
        raise ValueError(f"unknown market bootstrap dataset(s): {', '.join(unknown)}")
    selected_exchanges = tuple(dict.fromkeys(str(value).strip().upper() for value in exchanges))
    if not selected_exchanges or any(not value for value in selected_exchanges):
        raise ValueError("at least one exchange is required")
    snap = _normalized_date(snapshot_date or datetime.now(UTC).date(), name="snapshot_date")
    units: list[MarketBootstrapUnit] = []
    calendar_days = (pd.Timestamp(end).date() - pd.Timestamp(start).date()).days + 1
    range_factor = max(0.02, calendar_days / 365.25)
    if "trade_cal" in selected:
        for exchange in selected_exchanges:
            units.append(
                _unit(
                    "trade_cal",
                    f"{exchange}:{start}-{end}",
                    "trade_cal",
                    {"exchange": exchange, "start_date": start, "end_date": end},
                    (f"exchange={exchange}", f"range={start}-{end}"),
                    expected_start=start,
                    expected_end=end,
                    estimated_rows=calendar_days,
                    estimated_requests=_estimated_requests(calendar_days, page_size),
                    partition_strategy="exchange-range",
                )
            )

    if "stock_basic" in selected:
        # The one durable unit consists of the L/D/P status snapshots.  The
        # request estimate counts their independent pagination surfaces.
        estimated_live = math.ceil(estimated_company_count * 0.92)
        estimated_delisted = max(1, math.ceil(estimated_company_count * 0.08))
        requests = sum(
            _estimated_requests(rows, page_size) for rows in (estimated_live, estimated_delisted, 0)
        )
        units.append(
            _unit(
                "stock_basic",
                f"snapshot:{snap}",
                "stock_basic",
                {"snapshot_date": snap, "fields": _STOCK_BASIC_FIELDS},
                (f"snapshot={snap}",),
                expected_start=None,
                expected_end=None,
                estimated_rows=estimated_live + estimated_delisted,
                estimated_requests=requests,
                partition_strategy="snapshot",
            )
        )

    if "index_basic" in selected:
        units.append(
            _unit(
                "index_basic",
                f"snapshot:{snap}:{benchmark}",
                "index_basic",
                {
                    "ts_code": benchmark,
                    "snapshot_date": snap,
                    "fields": _INDEX_BASIC_FIELDS,
                },
                (f"snapshot={snap}",),
                expected_start=None,
                expected_end=None,
                estimated_rows=1,
                estimated_requests=1,
                partition_strategy="snapshot",
            )
        )

    if "namechange" in selected:
        units.append(
            _unit(
                "namechange",
                f"range:{start}-{end}",
                "namechange",
                {"start_date": start, "end_date": end},
                (f"range={start}-{end}",),
                expected_start=start,
                expected_end=end,
                estimated_rows=max(1, math.ceil(estimated_company_count * 8 * range_factor)),
                estimated_requests=_estimated_requests(
                    estimated_company_count * 8 * range_factor, page_size
                ),
                partition_strategy="effective-date-range",
            )
        )

    if "suspend_d" in selected:
        units.append(
            _unit(
                "suspend_d",
                f"range:{start}-{end}",
                "suspend_d",
                {"start_date": start, "end_date": end},
                (f"range={start}-{end}",),
                expected_start=start,
                expected_end=end,
                estimated_rows=max(1, math.ceil(estimated_company_count * 35 * range_factor)),
                estimated_requests=_estimated_requests(
                    estimated_company_count * 35 * range_factor, page_size
                ),
                partition_strategy="trade-date-range",
            )
        )

    for dataset in ("daily", "daily_basic"):
        if dataset not in selected:
            continue
        for month, month_start, month_end in _month_units(start, end):
            estimated_rows = math.ceil(
                estimated_company_count
                * _estimated_open_days(month_start, month_end, trading_days_per_year)
            )
            units.append(
                _unit(
                    dataset,
                    month,
                    dataset,
                    {"start_date": month_start, "end_date": month_end},
                    (f"year={month[:4]}", f"month={month.replace('-', '')}"),
                    expected_start=month_start,
                    expected_end=month_end,
                    estimated_rows=estimated_rows,
                    estimated_requests=_estimated_requests(estimated_rows, page_size),
                    partition_strategy="month",
                )
            )

    if "index_daily" in selected:
        for month, month_start, month_end in _month_units(start, end):
            units.append(
                _unit(
                    "index_daily",
                    f"{benchmark}:{month}",
                    "index_daily",
                    {"ts_code": benchmark, "start_date": month_start, "end_date": month_end},
                    (
                        f"ts_code={benchmark}",
                        f"year={month[:4]}",
                        f"month={month.replace('-', '')}",
                    ),
                    expected_start=month_start,
                    expected_end=month_end,
                    estimated_rows=_estimated_open_days(
                        month_start, month_end, trading_days_per_year
                    ),
                    estimated_requests=1,
                    partition_strategy="benchmark-month",
                )
            )

    return tuple(units)


@dataclass(frozen=True, slots=True)
class _MarketFetchOutcome:
    unit: MarketBootstrapUnit
    started_at: str
    status: str
    fetched: PaginatedFetch | None = None
    error: str | None = None


def _code_at(frame: pd.DataFrame, position: int) -> str | None:
    if frame.empty or "ts_code" not in frame.columns:
        return None
    value = frame.iloc[position]["ts_code"]
    return None if pd.isna(value) else str(value)


def _duplicate_count(dataset: str, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    keys = get_dataset_spec(dataset).primary_keys
    if not keys or not set(keys).issubset(frame.columns):
        return 0
    return int(frame.duplicated(list(keys), keep=False).sum())


def _combine_fetches(
    dataset: str, fetches: list[PaginatedFetch], warnings: Iterable[str] = ()
) -> PaginatedFetch:
    frames = [fetch.frame for fetch in fetches if not fetch.frame.empty]
    frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    pages = tuple(page for fetch in fetches for page in fetch.pages)
    schema_hashes = tuple(
        dict.fromkeys(value for fetch in fetches for value in fetch.schema_hashes if value)
    )
    all_warnings = list(warnings)
    all_warnings.extend(warning for fetch in fetches for warning in fetch.warnings)
    if len(schema_hashes) > 1:
        all_warnings.append("schema_drift")
    status = "PASS" if frame is not None and not frame.empty else "EMPTY"
    if any(fetch.status not in {"PASS", "EMPTY"} for fetch in fetches):
        status = "PARTIAL"
    return PaginatedFetch(
        frame=frame,
        status=status,
        pages=pages,
        elapsed_seconds=sum(fetch.elapsed_seconds for fetch in fetches),
        duplicate_count=_duplicate_count(dataset, frame),
        schema_hash=_schema_hash(frame.columns) if not frame.empty else "",
        first_ts_code=_code_at(frame, 0),
        last_ts_code=_code_at(frame, -1),
        schema_hashes=schema_hashes,
        warnings=tuple(dict.fromkeys(all_warnings)),
        total_rows=len(frame),
    )


def _fetch_stock_basic(
    provider: TushareProvider,
    unit: MarketBootstrapUnit,
    *,
    page_size: int,
    max_pages: int,
    rate_limiter: RateLimiter | None,
) -> _MarketFetchOutcome:
    started = utc_now()
    fetches: list[PaginatedFetch] = []
    warnings: list[str] = []
    try:
        for requested_status in ("L", "D", "P"):
            params = dict(unit.params)
            params.pop("snapshot_date", None)
            params["list_status"] = requested_status
            fetched = fetch_paginated_audited(
                provider,
                "stock_basic",
                params,
                page_size=page_size,
                max_pages=max_pages,
                rate_limiter=rate_limiter,
            )
            if fetched.status == "EMPTY":
                warnings.append(f"stock_basic_status_empty={requested_status}")
            elif fetched.status != "PASS":
                return _MarketFetchOutcome(
                    unit,
                    started,
                    "PARTIAL",
                    fetched=_combine_fetches("stock_basic", fetches + [fetched], warnings),
                    error=f"stock_basic status query {requested_status} was {fetched.status}",
                )
            if not fetched.frame.empty:
                frame = fetched.frame.copy()
                if "snapshot_list_status" not in frame.columns:
                    frame["snapshot_list_status"] = requested_status
                else:
                    mismatch = frame["snapshot_list_status"].astype("string").ne(requested_status)
                    if bool(mismatch.any()):
                        warnings.append(f"stock_basic_status_mismatch={requested_status}")
                fetches.append(
                    PaginatedFetch(
                        frame=frame,
                        status=fetched.status,
                        pages=fetched.pages,
                        elapsed_seconds=fetched.elapsed_seconds,
                        duplicate_count=fetched.duplicate_count,
                        schema_hash=_schema_hash(frame.columns),
                        first_ts_code=_code_at(frame, 0),
                        last_ts_code=_code_at(frame, -1),
                        schema_hashes=(_schema_hash(frame.columns),),
                        warnings=fetched.warnings,
                        total_rows=len(frame),
                    )
                )
            else:
                fetches.append(fetched)
    except ProviderError as exc:
        return _MarketFetchOutcome(
            unit,
            started,
            "FAILED",
            error=f"{exc.error_type}: {exc.error_message}",
        )
    except PaginationError as exc:
        return _MarketFetchOutcome(
            unit,
            started,
            "PARTIAL",
            fetched=_combine_fetches("stock_basic", fetches + [exc.partial], warnings),
            error=str(exc),
        )

    combined = _combine_fetches("stock_basic", fetches, warnings)
    if combined.status == "EMPTY":
        return _MarketFetchOutcome(
            unit,
            started,
            "EMPTY",
            fetched=combined,
            error="all stock_basic status snapshots returned no rows",
        )
    return _MarketFetchOutcome(unit, started, combined.status, fetched=combined)


def _fetch_unit(
    provider: TushareProvider,
    unit: MarketBootstrapUnit,
    *,
    page_size: int,
    max_pages: int,
    rate_limiter: RateLimiter | None,
    progress: Callable[[str], None] | None,
) -> _MarketFetchOutcome:
    _emit(progress, f"[{unit.dataset}][{unit.unit}] start")
    if unit.dataset == "stock_basic":
        outcome = _fetch_stock_basic(
            provider,
            unit,
            page_size=page_size,
            max_pages=max_pages,
            rate_limiter=rate_limiter,
        )
    else:
        started = utc_now()
        try:
            params = dict(unit.params)
            params.pop("snapshot_date", None)
            fetched = fetch_paginated_audited(
                provider,
                unit.source_api,
                params,
                page_size=page_size,
                max_pages=max_pages,
                rate_limiter=rate_limiter,
            )
            outcome = _MarketFetchOutcome(unit, started, fetched.status, fetched=fetched)
        except ProviderError as exc:
            outcome = _MarketFetchOutcome(
                unit,
                started,
                "FAILED",
                error=f"{exc.error_type}: {exc.error_message}",
            )
        except PaginationError as exc:
            outcome = _MarketFetchOutcome(
                unit,
                started,
                "PARTIAL",
                fetched=exc.partial,
                error=str(exc),
            )
    if outcome.fetched is not None:
        for page in outcome.fetched.pages:
            _emit(
                progress,
                f"[{unit.dataset}][{unit.unit}] page={page.page_number} "
                f"offset={page.offset} rows={page.rows}",
            )
    return outcome


def _in_expected_range(unit: MarketBootstrapUnit, frame: pd.DataFrame) -> bool:
    if unit.expected_start is None or unit.expected_end is None:
        return True
    # ``namechange`` is filtered by the provider's effective/announcement-date
    # semantics, which are not identical across compatible endpoints.  Retain
    # the raw response and let the coverage audit select intersecting effective
    # intervals; do not reject a valid row merely because the API returned a
    # boundary change whose announcement date falls in the query range.
    if unit.dataset == "namechange":
        return True
    field = "cal_date" if unit.dataset == "trade_cal" else "trade_date"
    if field not in frame.columns:
        return False
    values = normalize_date_series(frame[field]).dropna()
    if values.empty:
        return False
    start = pd.Timestamp(unit.expected_start)
    end = pd.Timestamp(unit.expected_end)
    return bool(values.ge(start).all() and values.le(end).all())


def _fatal_quality_warnings(
    unit: MarketBootstrapUnit,
    frame: pd.DataFrame,
    fetched: PaginatedFetch,
) -> list[str]:
    quality = check_frame_quality(unit.dataset, frame, get_dataset_spec(unit.dataset))
    warnings = list(fetched.warnings) + list(quality.warnings)
    if unit.dataset == "stock_basic":
        expected = {"ts_code", "symbol", "name", "market", "exchange", "list_date", "list_status"}
        missing = sorted(expected.difference(frame.columns))
        if missing:
            warnings.append("missing_reference_fields=" + ",".join(missing))
    if not _in_expected_range(unit, frame):
        warnings.append("date_range_mismatch")
    fatal_prefixes = (
        "missing_required=",
        "missing_reference_fields=",
        "duplicate_identity_rows=",
        "null_identity_rows=",
        "null_partition_rows=",
        "bad_dates=",
        "partition_field_missing=",
        "date_range_mismatch",
        "schema_drift",
    )
    return list(dict.fromkeys(value for value in warnings if value.startswith(fatal_prefixes)))


def _checkpoint(
    unit: MarketBootstrapUnit,
    outcome: _MarketFetchOutcome,
    *,
    status: str,
    error: str | None = None,
    warnings: Iterable[str] = (),
    size_bytes: int = 0,
) -> MarketBootstrapCheckpoint:
    fetched = outcome.fetched
    warning_values = tuple(dict.fromkeys((*(fetched.warnings if fetched else ()), *warnings)))
    return MarketBootstrapCheckpoint(
        dataset=unit.dataset,
        unit=unit.unit,
        source_api=unit.source_api,
        requested_start=unit.expected_start,
        requested_end=unit.expected_end,
        storage_path="/".join((*unit.storage_parts, "data.parquet")),
        started_at=outcome.started_at,
        finished_at=utc_now(),
        status=status,
        page_count=fetched.page_count if fetched else 0,
        row_count=len(fetched.frame) if fetched else 0,
        request_count=fetched.page_count if fetched else 0,
        error=error,
        schema_hash=fetched.schema_hash if fetched else None,
        duplicate_count=fetched.duplicate_count if fetched else 0,
        warnings=warning_values,
    )


def _commit_unit(
    unit: MarketBootstrapUnit,
    outcome: _MarketFetchOutcome,
    store: RawParquetStore,
    checkpoints: MarketCheckpointStore,
) -> MarketBootstrapUnitResult:
    fetched = outcome.fetched
    if fetched is None:
        error = outcome.error or "provider failed without an error message"
        checkpoints.append(_checkpoint(unit, outcome, status="FAILED", error=error))
        return MarketBootstrapUnitResult(
            unit.dataset,
            unit.unit,
            unit.source_api,
            "FAILED",
            unit.expected_start,
            unit.expected_end,
            error=error,
        )
    if fetched.status == "EMPTY" or fetched.frame.empty:
        error = outcome.error or "zero rows; historical availability could not be confirmed"
        checkpoints.append(_checkpoint(unit, outcome, status="UNKNOWN_EMPTY", error=error))
        return MarketBootstrapUnitResult(
            unit.dataset,
            unit.unit,
            unit.source_api,
            "UNKNOWN_EMPTY",
            unit.expected_start,
            unit.expected_end,
            page_count=fetched.page_count,
            request_count=fetched.page_count,
            warnings=fetched.warnings,
            error=error,
        )
    if fetched.status != "PASS":
        error = outcome.error or "pagination did not prove a complete response"
        checkpoints.append(_checkpoint(unit, outcome, status="PARTIAL", error=error))
        return MarketBootstrapUnitResult(
            unit.dataset,
            unit.unit,
            unit.source_api,
            "PARTIAL",
            unit.expected_start,
            unit.expected_end,
            page_count=fetched.page_count,
            request_count=fetched.page_count,
            rows=len(fetched.frame),
            schema_hash=fetched.schema_hash,
            duplicate_count=fetched.duplicate_count,
            warnings=fetched.warnings,
            error=error,
        )

    frame = fetched.frame.copy()
    if unit.dataset in {"stock_basic", "index_basic"}:
        # ``snapshot_date`` in a plan is a deterministic storage label.  The
        # actual source observation is current at retrieval time; never copy an
        # operator-supplied historical label into a PIT field.
        frame["reference_snapshot_date"] = datetime.now(UTC).strftime("%Y%m%d")
        frame["reference_semantics"] = "current_snapshot"
    fatal = _fatal_quality_warnings(unit, frame, fetched)
    warnings = list(dict.fromkeys((*fetched.warnings, *fatal)))
    if fatal:
        error = "quality gate failed: " + ",".join(fatal)
        checkpoints.append(
            _checkpoint(unit, outcome, status="PARTIAL", error=error, warnings=warnings)
        )
        return MarketBootstrapUnitResult(
            unit.dataset,
            unit.unit,
            unit.source_api,
            "PARTIAL",
            unit.expected_start,
            unit.expected_end,
            page_count=fetched.page_count,
            request_count=fetched.page_count,
            rows=len(frame),
            schema_hash=fetched.schema_hash,
            duplicate_count=fetched.duplicate_count,
            warnings=tuple(warnings),
            error=error,
        )

    try:
        stored = store.write_unit(
            unit.dataset,
            unit.storage_parts,
            frame,
            get_dataset_spec(unit.dataset),
            retrieved_at=utc_now(),
            source=SOURCE_NAME,
            source_api=unit.source_api,
        )
        if not stored:
            raise OSError("empty frame was not materialized")
    except Exception as exc:
        error = f"storage: {exc}"
        checkpoints.append(
            _checkpoint(unit, outcome, status="PARTIAL", error=error, warnings=warnings)
        )
        return MarketBootstrapUnitResult(
            unit.dataset,
            unit.unit,
            unit.source_api,
            "PARTIAL",
            unit.expected_start,
            unit.expected_end,
            page_count=fetched.page_count,
            request_count=fetched.page_count,
            rows=len(frame),
            schema_hash=fetched.schema_hash,
            duplicate_count=fetched.duplicate_count,
            warnings=tuple(warnings),
            error=error,
        )

    stored_file = stored[0]
    checkpoints.append(
        _checkpoint(
            unit,
            outcome,
            status="PASS",
            warnings=warnings,
            size_bytes=stored_file.size_bytes,
        )
    )
    return MarketBootstrapUnitResult(
        unit.dataset,
        unit.unit,
        unit.source_api,
        "PASS",
        unit.expected_start,
        unit.expected_end,
        page_count=fetched.page_count,
        request_count=fetched.page_count,
        rows=len(frame),
        size_bytes=stored_file.size_bytes,
        schema_hash=fetched.schema_hash,
        duplicate_count=fetched.duplicate_count,
        warnings=tuple(warnings),
        stored_path=str(stored_file.path),
    )


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
    else:
        LOGGER.info(message)


def _locked_progress(progress: Callable[[str], None] | None) -> Callable[[str], None] | None:
    if progress is None:
        return None
    lock = Lock()

    def emit(message: str) -> None:
        with lock:
            progress(message)

    return emit


def _skipped_result(
    unit: MarketBootstrapUnit, checkpoint: dict[str, Any]
) -> MarketBootstrapUnitResult:
    return MarketBootstrapUnitResult(
        dataset=unit.dataset,
        unit=unit.unit,
        source_api=unit.source_api,
        status="PASS",
        requested_start=unit.expected_start,
        requested_end=unit.expected_end,
        page_count=int(checkpoint.get("page_count", 0)),
        request_count=int(checkpoint.get("request_count", checkpoint.get("page_count", 0))),
        rows=int(checkpoint.get("row_count", 0)),
        schema_hash=str(checkpoint.get("schema_hash") or ""),
        duplicate_count=int(checkpoint.get("duplicate_count", 0)),
        warnings=("RESUMED_SKIP",),
        skipped=True,
    )


def bootstrap_market_data(
    provider: TushareProvider | None,
    store: RawParquetStore,
    checkpoints: MarketCheckpointStore,
    *,
    start_date: str | date | datetime | pd.Timestamp = RESEARCH_START_DATE,
    end_date: str | date | datetime | pd.Timestamp | None = None,
    datasets: tuple[str, ...] = DEFAULT_MARKET_BOOTSTRAP_DATASETS,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    exchanges: tuple[str, ...] = DEFAULT_MARKET_EXCHANGES,
    snapshot_date: str | date | datetime | pd.Timestamp | None = None,
    resume: bool = True,
    dry_run: bool = False,
    page_size: int = 5000,
    max_pages: int = 100,
    workers: int = 4,
    requests_per_minute: float = 60.0,
    rate_limiter: RateLimiter | None = None,
    progress: Callable[[str], None] | None = None,
) -> MarketBootstrapSummary:
    """Run the full market/reference bootstrap with durable month/range units."""

    if workers <= 0:
        raise ValueError("workers must be positive")
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    if not math.isfinite(requests_per_minute) or requests_per_minute <= 0:
        raise ValueError("requests_per_minute must be a finite positive number")
    if not dry_run and provider is None:
        raise ValueError("provider is required unless dry_run=True")
    units = build_market_bootstrap_plan(
        start_date,
        end_date,
        datasets=datasets,
        benchmark_code=benchmark_code,
        exchanges=exchanges,
        snapshot_date=snapshot_date,
        page_size=page_size,
    )
    if not units:
        raise ValueError("at least one market bootstrap dataset is required")
    selected_datasets = tuple(dict.fromkeys(unit.dataset for unit in units))
    normalized_start = _normalized_date(start_date, name="start_date")
    normalized_end = _normalized_date(end_date or default_market_end_date(), name="end_date")
    normalized_snapshot = _normalized_date(
        snapshot_date or datetime.now(UTC).date(), name="snapshot_date"
    )
    benchmark = _validate_benchmark_code(benchmark_code)
    emit = _locked_progress(progress)
    started = time.monotonic()
    result_by_key: dict[tuple[str, str], MarketBootstrapUnitResult] = {}
    tasks: list[MarketBootstrapUnit] = []

    completed_by_dataset = {
        dataset: checkpoints.completed_units(dataset, unit_source)
        for dataset, unit_source in {unit.dataset: unit.source_api for unit in units}.items()
    }
    for unit in units:
        latest = checkpoints.latest(unit.dataset, unit.unit) if resume else None
        completed = completed_by_dataset.get(unit.dataset, set())
        path_exists = store.unit_file(unit.dataset, unit.storage_parts).is_file()
        if resume and unit.unit in completed and path_exists and latest is not None:
            result = _skipped_result(unit, latest)
            result_by_key[(unit.dataset, unit.unit)] = result
            _emit(emit, f"[{unit.dataset}][{unit.unit}] skip(resume)")
        elif dry_run:
            result_by_key[(unit.dataset, unit.unit)] = MarketBootstrapUnitResult(
                unit.dataset,
                unit.unit,
                unit.source_api,
                "NEEDS_DOWNLOAD",
                unit.expected_start,
                unit.expected_end,
            )
        else:
            tasks.append(unit)

    if dry_run:
        return MarketBootstrapSummary(
            normalized_start,
            normalized_end,
            benchmark,
            normalized_snapshot,
            selected_datasets,
            units,
            tuple(result_by_key[(unit.dataset, unit.unit)] for unit in units),
            dry_run=True,
            workers=workers,
            requests_per_minute=requests_per_minute,
            elapsed_seconds=time.monotonic() - started,
        )

    assert provider is not None
    limiter = (
        rate_limiter or getattr(provider, "rate_limiter", None) or RateLimiter(requests_per_minute)
    )
    before_requests = int(getattr(limiter, "request_count", 0))
    if isinstance(provider, TushareProvider):
        provider.set_rate_limiter(limiter)

    def disk_ok() -> bool:
        disk = check_disk_space(store.data_dir)
        return disk.free_bytes >= EMERGENCY_STOP_FREE_BYTES

    def record(unit: MarketBootstrapUnit, result: MarketBootstrapUnitResult) -> None:
        result_by_key[(unit.dataset, unit.unit)] = result
        suffix = f" error={result.error}" if result.error else ""
        _emit(
            emit,
            f"[{unit.dataset}][{unit.unit}] complete status={result.status} "
            f"rows={result.rows} pages={result.page_count}{suffix}",
        )

    def commit(unit: MarketBootstrapUnit, outcome: _MarketFetchOutcome) -> None:
        try:
            result = _commit_unit(unit, outcome, store, checkpoints)
        except Exception as exc:
            error = f"unexpected {type(exc).__name__}: {exc}"
            checkpoints.append(
                MarketBootstrapCheckpoint(
                    dataset=unit.dataset,
                    unit=unit.unit,
                    source_api=unit.source_api,
                    requested_start=unit.expected_start,
                    requested_end=unit.expected_end,
                    storage_path="/".join((*unit.storage_parts, "data.parquet")),
                    started_at=outcome.started_at,
                    finished_at=utc_now(),
                    status="FAILED",
                    error=error,
                )
            )
            result = MarketBootstrapUnitResult(
                unit.dataset,
                unit.unit,
                unit.source_api,
                "FAILED",
                unit.expected_start,
                unit.expected_end,
                error=error,
            )
        record(unit, result)

    def disk_stopped(remaining: Iterable[MarketBootstrapUnit]) -> None:
        for unit in remaining:
            record(
                unit,
                MarketBootstrapUnitResult(
                    unit.dataset,
                    unit.unit,
                    unit.source_api,
                    "FAILED",
                    unit.expected_start,
                    unit.expected_end,
                    error="disk emergency stop: less than 15 GiB free",
                ),
            )

    if workers == 1:
        for position, unit in enumerate(tasks):
            if not disk_ok():
                disk_stopped(tasks[position:])
                break
            commit(
                unit,
                _fetch_unit(
                    provider,
                    unit,
                    page_size=page_size,
                    max_pages=max_pages,
                    rate_limiter=limiter,
                    progress=emit,
                ),
            )
    else:
        pending: dict[Future[_MarketFetchOutcome], MarketBootstrapUnit] = {}
        next_position = 0
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="market-bootstrap")

        def submit_available() -> None:
            nonlocal next_position
            while next_position < len(tasks) and len(pending) < workers:
                if not disk_ok():
                    disk_stopped(tasks[next_position:])
                    next_position = len(tasks)
                    return
                unit = tasks[next_position]
                next_position += 1
                future = executor.submit(
                    _fetch_unit,
                    provider,
                    unit,
                    page_size=page_size,
                    max_pages=max_pages,
                    rate_limiter=limiter,
                    progress=emit,
                )
                pending[future] = unit

        try:
            submit_available()
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    unit = pending.pop(future)
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        outcome = _MarketFetchOutcome(
                            unit,
                            utc_now(),
                            "FAILED",
                            error=f"unexpected {type(exc).__name__}: {exc}",
                        )
                    commit(unit, outcome)
                submit_available()
        except KeyboardInterrupt:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            _emit(emit, "market bootstrap interrupted; completed units were preserved")
            raise
        else:
            executor.shutdown(wait=True)

    after_requests = int(getattr(limiter, "request_count", 0))
    return MarketBootstrapSummary(
        normalized_start,
        normalized_end,
        benchmark,
        normalized_snapshot,
        selected_datasets,
        units,
        tuple(result_by_key[(unit.dataset, unit.unit)] for unit in units),
        dry_run=False,
        workers=workers,
        requests_per_minute=float(getattr(limiter, "requests_per_minute", requests_per_minute)),
        api_requests=max(0, after_requests - before_requests),
        elapsed_seconds=time.monotonic() - started,
    )


def render_market_bootstrap_dry_run(summary: MarketBootstrapSummary) -> str:
    """Render a dry-run without exposing any provider credentials."""

    rate = (
        f"{summary.requests_per_minute:g} requests/minute"
        if summary.requests_per_minute is not None
        else "not configured"
    )
    by_dataset: dict[str, list[MarketBootstrapUnit]] = {}
    for unit in summary.units:
        by_dataset.setdefault(unit.dataset, []).append(unit)
    lines = [
        "Market / Reference historical bootstrap dry-run",
        f"window={summary.start_date}..{summary.end_date}",
        f"benchmark={summary.benchmark_code}",
        f"snapshot_date={summary.snapshot_date} (reference snapshot only)",
        f"datasets={','.join(summary.datasets)}",
        f"planned_units={summary.planned_units}",
        f"partition_count={summary.planned_units}",
        f"workers={summary.workers}; global rate limit={rate}",
        f"estimated_size_bytes={sum(unit.estimated_size_bytes for unit in summary.units)}",
        f"request_estimate_remaining={summary.request_estimate}",
        f"existing_completed_units={summary.existing_units}",
        f"remaining_units={summary.remaining_units}",
        "remote_requests=false",
        "parquet_writes=false",
        "state_changes=false",
        "",
        "dataset | unit/range | partition strategy | estimated rows | estimated size | "
        "request units | existing | remaining",
        "--- | --- | --- | ---: | ---: | ---: | --- | ---:",
    ]
    for dataset, units in by_dataset.items():
        for unit in units:
            result = next(
                value
                for value in summary.results
                if value.dataset == unit.dataset and value.unit == unit.unit
            )
            existing = "YES" if result.skipped else "NO"
            lines.append(
                f"{unit.dataset} | {unit.unit} "
                f"({unit.expected_start or '-'}..{unit.expected_end or '-'}) | "
                f"{unit.partition_strategy} | {unit.estimated_rows:,} | "
                f"{unit.estimated_size_bytes:,} | {unit.estimated_requests} | {existing} | "
                f"{0 if result.skipped else 1}"
            )
    return "\n".join(lines)


def format_market_dataset_progress(summary: MarketBootstrapSummary) -> str:
    """Render dataset-level counts for an operational run log."""

    lines = [
        "dataset | total | completed | skipped | failed | partial",
        "--- | ---: | ---: | ---: | ---: | ---:",
    ]
    for dataset in summary.datasets:
        values = [result for result in summary.results if result.dataset == dataset]
        lines.append(
            f"{dataset} | {len(values)} | "
            f"{sum(result.status == 'PASS' and not result.skipped for result in values)} | "
            f"{sum(result.skipped for result in values)} | "
            f"{sum(result.status == 'FAILED' for result in values)} | "
            f"{sum(result.status in {'PARTIAL', 'UNKNOWN_EMPTY'} for result in values)}"
        )
    return "\n".join(lines)
