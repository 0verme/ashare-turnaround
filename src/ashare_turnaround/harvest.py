"""Cold-archive orchestration for high-value Tushare history.

This module deliberately stops at RAW.  It probes the configured provider,
plans bounded partitions, and writes source-shaped Parquet with provenance.  It
does not import or modify scanner score/feature code and does not infer PIT
semantics from a successful download.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from .config import SOURCE_NAME
from .datasets.specs import DatasetSpec
from .datasets.sync import PageAudit, PaginationError, fetch_paginated_audited
from .dates import date_text, normalize_date_series
from .providers.rate_limit import RateLimiter
from .providers.tushare import ProviderError, TushareProvider
from .quality import check_frame_quality
from .security import redact_text
from .storage.inventory import schema_hash
from .storage.parquet import RawParquetStore

LOGGER = logging.getLogger(__name__)

# User-requested guards.  These are intentionally separate from the older
# financial-bootstrap emergency constant so existing callers keep their API.
SOFT_FREE_SPACE = 120 * 1024**3
HARD_FREE_SPACE = 80 * 1024**3

ARTIFACT_DIR = Path("artifacts/data-harvest")
CHECKPOINT_FILENAME = "state/harvest-checkpoints.json"
DEFAULT_START_DATE = "20120101"

_PRIORITY_RANK = {"P0-A": 0, "P0-B": 1, "P1": 2, "P2": 3}
_PARTITION_COMPONENT = re.compile(r"^[A-Za-z0-9_.=-]+$")
_DATE_COLUMNS = (
    "trade_date",
    "cal_date",
    "ann_date",
    "f_ann_date",
    "actual_date",
    "pub_date",
    "report_date",
    "end_date",
    "first_ann_date",
    "float_date",
    "imp_date",
    "pre_date",
    "out_date",
    "in_date",
)


@dataclass(frozen=True, slots=True)
class HarvestSpec:
    """A logical dataset and its conservative source/query contract."""

    dataset: str
    api: str
    category: str
    priority: str
    query_mode: str
    partition_strategy: str
    start_date: str = DEFAULT_START_DATE
    end_date: str | None = None
    fixed_params: tuple[tuple[str, Any], ...] = ()
    probe_params: tuple[tuple[str, Any], ...] = ()
    probe_fallback_params: tuple[tuple[str, Any], ...] = ()
    fallback_is_full_market: bool = False
    full_market_query_supported: bool = True
    date_fields: tuple[str, ...] = ()
    primary_keys: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    pit_status: str = "PIT_REQUIRES_VALIDATION"
    notes: str = ""
    heavy: bool = False
    current_only: bool = False
    allow_tiny: bool = False
    # Existing partial samples are never overwritten.  They are archived under
    # a separate namespace while keeping the logical dataset name in reports.
    storage_dataset: str | None = None

    @property
    def storage_name(self) -> str:
        return self.storage_dataset or self.dataset

    @property
    def fixed(self) -> dict[str, Any]:
        return dict(self.fixed_params)

    @property
    def probe(self) -> dict[str, Any]:
        return dict(self.probe_params)

    @property
    def probe_fallback(self) -> dict[str, Any]:
        return dict(self.probe_fallback_params)


@dataclass(frozen=True, slots=True)
class LocalDatasetState:
    dataset: str
    storage_dataset: str
    files: int
    rows: int
    size_bytes: int
    date_min: str | None
    date_max: str | None
    symbols: int
    schemas: tuple[str, ...]
    checkpoint_status: str
    status: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApiInventoryResult:
    dataset: str
    provider: str
    api_name: str
    category: str
    priority: str
    reachable: bool
    permission_ok: bool | None
    permission: str
    probe_status: str
    result: str
    sample_request: dict[str, Any]
    sample_rows: int
    fields: tuple[str, ...]
    earliest_date_if_known: str | None
    latest_date_if_known: str | None
    estimated_partition_strategy: str
    estimated_request_count: int | None
    estimated_volume: str
    local_status: str
    raw_path: str
    pit_status: str
    reason: str
    error_type: str | None = None
    error_message: str | None = None
    fallback_used: bool = False
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ApiInventory:
    generated_at: str
    data_dir: str
    provider: str
    endpoint_kind: str
    token_configured: bool
    deadline: str | None
    catalog_version: str
    apis: tuple[ApiInventoryResult, ...]


@dataclass(frozen=True, slots=True)
class HarvestUnit:
    dataset: str
    api: str
    unit: str
    query: dict[str, Any]
    partition: str
    storage_dataset: str
    priority: str
    heavy: bool


@dataclass(frozen=True, slots=True)
class PlanEntry:
    dataset: str
    provider: str
    api: str
    category: str
    priority: str
    planned_range: str
    planned_units: int
    existing_units: int
    remaining_units: int
    estimated_requests: int
    estimated_rows: int
    estimated_size_bytes: int
    worker_count: int
    rate_limit: float
    partition_strategy: str
    checkpoint_namespace: str
    raw_path: str
    inventory_result: str
    permission: str
    pit_status: str
    status: str
    estimation_basis: str
    known_limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    generated_at: str
    data_dir: str
    artifact_dir: str
    start_date: str
    end_date: str
    worker_count: int
    rate_limit: float
    soft_free_space: int
    hard_free_space: int
    checkpoint_namespace: str
    datasets: tuple[PlanEntry, ...]


@dataclass(frozen=True, slots=True)
class HarvestCheckpoint:
    dataset: str
    storage_dataset: str
    api: str
    unit: str
    query: dict[str, Any]
    partition: str
    started_at: str
    finished_at: str
    status: str
    page_count: int = 0
    request_count: int = 0
    rows: int = 0
    files: int = 0
    size_bytes: int = 0
    schema_hash: str | None = None
    schema_hashes: tuple[str, ...] = ()
    date_min: str | None = None
    date_max: str | None = None
    stored_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HarvestUnitResult:
    dataset: str
    api: str
    unit: str
    partition: str
    status: str
    rows: int = 0
    files: int = 0
    size_bytes: int = 0
    page_count: int = 0
    request_count: int = 0
    date_min: str | None = None
    date_max: str | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    stored_paths: tuple[str, ...] = ()
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class HarvestRunSummary:
    generated_at: str
    results: tuple[HarvestUnitResult, ...]
    workers: int
    rate_limit: float
    api_requests: int
    elapsed_seconds: float
    soft_guard_bytes: int
    hard_guard_bytes: int

    @property
    def failures(self) -> tuple[HarvestUnitResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.status not in {"PASS", "SKIPPED_EXISTING_COMPLETE"}
        )

    @property
    def rows(self) -> int:
        return sum(result.rows for result in self.results)

    @property
    def size_bytes(self) -> int:
        return sum(result.size_bytes for result in self.results)


@dataclass(frozen=True, slots=True)
class DiskGuardDecision:
    free_bytes: int
    soft_guard: bool
    hard_guard: bool
    action: str
    reason: str


class DiskGuard:
    """Filesystem guard with explicit soft/heavy and hard/non-critical gates."""

    def __init__(
        self,
        path: str | Path,
        *,
        soft_free_bytes: int = SOFT_FREE_SPACE,
        hard_free_bytes: int = HARD_FREE_SPACE,
    ) -> None:
        if soft_free_bytes < 0 or hard_free_bytes < 0:
            raise ValueError("disk guard thresholds must be non-negative")
        if hard_free_bytes > soft_free_bytes:
            raise ValueError("hard disk threshold must not exceed soft threshold")
        self.path = Path(path).expanduser()
        self.soft_free_bytes = int(soft_free_bytes)
        self.hard_free_bytes = int(hard_free_bytes)

    def check(self) -> DiskGuardDecision:
        target = self.path if self.path.exists() else self.path.parent
        free = int(shutil.disk_usage(target).free)
        if free < self.hard_free_bytes:
            return DiskGuardDecision(
                free,
                free < self.soft_free_bytes,
                True,
                "HARD_STOP_NONCRITICAL",
                f"free space {free} is below hard guard {self.hard_free_bytes}",
            )
        if free < self.soft_free_bytes:
            return DiskGuardDecision(
                free,
                True,
                False,
                "PAUSE_HEAVY",
                f"free space {free} is below soft guard {self.soft_free_bytes}",
            )
        return DiskGuardDecision(free, False, False, "PASS", "free space above guards")

    def allows(self, spec: HarvestSpec) -> tuple[bool, str]:
        decision = self.check()
        if decision.hard_guard and not _is_critical(spec):
            return False, decision.reason
        if decision.soft_guard and spec.heavy:
            return False, decision.reason
        return True, decision.reason


def _is_critical(spec: HarvestSpec) -> bool:
    """P0 non-heavy work remains eligible until the hard guard."""

    return spec.priority in {"P0-A", "P0-B"} and not spec.heavy


class DeadlineGuard:
    """Apply the three deadline modes without writing a secret expiry value."""

    def __init__(self, value: str | None = None) -> None:
        raw = value if value is not None else os.getenv("ASHARE_HARVEST_DEADLINE")
        self.raw = raw.strip() if raw and raw.strip() else None
        self.deadline = self._parse(self.raw) if self.raw else None

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            if value.isdigit():
                return datetime.fromtimestamp(float(value), tz=UTC)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("ASHARE_HARVEST_DEADLINE must be ISO-8601 or Unix seconds") from exc
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    @property
    def seconds_remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return (self.deadline - datetime.now(UTC)).total_seconds()

    @property
    def mode(self) -> str:
        remaining = self.seconds_remaining
        if remaining is None:
            return "NO_DEADLINE"
        if remaining > 36 * 3600:
            return "OPEN"
        if remaining > 12 * 3600:
            return "NO_NEW_HEAVY"
        return "GAP_CLOSING"

    def allows(
        self,
        spec: HarvestSpec,
        *,
        dataset_started: bool,
    ) -> tuple[bool, str]:
        mode = self.mode
        if mode == "NO_DEADLINE":
            return True, mode
        if mode == "OPEN":
            return True, mode
        if mode == "NO_NEW_HEAVY" and spec.heavy:
            return False, "deadline mode NO_NEW_HEAVY"
        if mode == "GAP_CLOSING" and not dataset_started:
            return False, "deadline mode GAP_CLOSING blocks new dataset"
        return True, mode


class HarvestCheckpointStore:
    """Atomic append-only checkpoints for logical harvest units."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = RLock()

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError(f"harvest checkpoint must be a JSON list: {self.path}")
            return value

    def append(self, checkpoint: HarvestCheckpoint) -> None:
        with self._lock:
            values = self.records()
            values.append(asdict(checkpoint))
            _atomic_json_write(self.path, values)

    def latest(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in self.records():
            key = (
                str(record.get("dataset", "")),
                str(record.get("api", "")),
                str(record.get("unit", "")),
            )
            if all(key):
                latest[key] = record
        return latest

    def completed_units(
        self,
        dataset: str,
        api: str,
        storage_dataset: str | None = None,
    ) -> set[str]:
        return {
            unit
            for (record_dataset, record_api, unit), record in self.latest().items()
            if record_dataset == dataset
            and record_api == api
            and str(record.get("status", "")).upper() == "PASS"
            and (storage_dataset is None or record.get("storage_dataset") == storage_dataset)
        }

    def started_datasets(self) -> set[str]:
        return {
            str(record.get("dataset"))
            for record in self.records()
            if record.get("dataset") and str(record.get("status", "")).upper() != "STOPPED"
        }


# ---------------------------------------------------------------------------
# Candidate catalog
# ---------------------------------------------------------------------------


def _pairs(value: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    return tuple((str(key), item) for key, item in (value or {}).items())


def _spec(
    dataset: str,
    api: str,
    category: str,
    priority: str,
    query_mode: str,
    partition_strategy: str,
    *,
    fixed: Mapping[str, Any] | None = None,
    probe: Mapping[str, Any] | None = None,
    fallback: Mapping[str, Any] | None = None,
    fallback_is_full_market: bool = False,
    full_market_query_supported: bool = True,
    date_fields: Sequence[str] = (),
    primary_keys: Sequence[str] = (),
    required_fields: Sequence[str] = (),
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    pit_status: str = "PIT_REQUIRES_VALIDATION",
    notes: str = "",
    heavy: bool = False,
    current_only: bool = False,
    allow_tiny: bool = False,
    storage_dataset: str | None = None,
) -> HarvestSpec:
    probe_values = dict(probe or {})
    probe_values.setdefault("limit", 3)
    return HarvestSpec(
        dataset=dataset,
        api=api,
        category=category,
        priority=priority,
        query_mode=query_mode,
        partition_strategy=partition_strategy,
        start_date=start_date,
        end_date=end_date,
        fixed_params=_pairs(fixed),
        probe_params=_pairs(probe_values),
        probe_fallback_params=_pairs(fallback),
        fallback_is_full_market=fallback_is_full_market,
        full_market_query_supported=full_market_query_supported,
        date_fields=tuple(date_fields),
        primary_keys=tuple(primary_keys),
        required_fields=tuple(required_fields),
        pit_status=pit_status,
        notes=notes,
        heavy=heavy,
        current_only=current_only,
        allow_tiny=allow_tiny,
        storage_dataset=storage_dataset,
    )


def _existing(
    dataset: str,
    api: str,
    category: str,
    *,
    notes: str,
    pit_status: str = "PIT_SAFE",
) -> HarvestSpec:
    return _spec(
        dataset,
        api,
        category,
        "P0-B",
        "none",
        "existing",
        notes=notes,
        pit_status=pit_status,
        current_only=True,
    )


# Keep this catalog explicit and reviewable.  It is intentionally a candidate
# inventory rather than a claim that every API is supported by the proxy.
HARVEST_SPECS: tuple[HarvestSpec, ...] = (
    _spec(
        "report_rc",
        "report_rc",
        "analyst/research",
        "P0-A",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("report_date", "ann_date"),
        primary_keys=("ts_code", "report_date", "org_name", "author_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Sell-side earnings/target/rating source schema is retained verbatim.",
    ),
    _spec(
        "cyq_perf",
        "cyq_perf",
        "chip",
        "P0-A",
        "trade_date_batch",
        "year_month",
        start_date="20180101",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="High-value chip performance history; no feature integration in this run.",
    ),
    _spec(
        "cyq_chips",
        "cyq_chips",
        "chip",
        "P0-A",
        "trade_date",
        "year_month_trade_date",
        probe={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date", "price"),
        pit_status="DERIVED_VENDOR_DATA",
        notes="Heavyweight price-distribution data; independently gated and resumable.",
        full_market_query_supported=False,
        heavy=True,
    ),
    _spec(
        "stk_factor",
        "stk_factor",
        "vendor factor",
        "P0-A",
        "trade_date",
        "year_month_trade_date",
        probe={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="DERIVED_VENDOR_DATA",
        notes="Vendor-derived factor history; raw archive only.",
        heavy=True,
    ),
    _spec(
        "stk_factor_pro",
        "stk_factor_pro",
        "vendor factor",
        "P0-A",
        "trade_date",
        "year_month_trade_date",
        probe={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="DERIVED_VENDOR_DATA",
        notes="Wide vendor-derived factor history; raw archive only.",
        heavy=True,
    ),
    _spec(
        "adj_factor",
        "adj_factor",
        "PIT/reference",
        "P0-B",
        "month_range",
        "year_month",
        probe={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        required_fields=("ts_code", "trade_date", "adj_factor"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Adjustment basis for future return/price research; not used by Scanner here.",
    ),
    _spec(
        "stock_st",
        "stock_st",
        "PIT/reference",
        "P0-B",
        "month_range",
        "year_month",
        start_date="20151201",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        fallback_is_full_market=True,
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes=(
            "Historical daily ST state; source publication semantics require validation. "
            "Prior limit=5000 files remain page-cap-unvalidated evidence."
        ),
        storage_dataset="stock_st_archive",
    ),
    _spec(
        "st",
        "st",
        "PIT/reference",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("pub_date", "imp_date"),
        primary_keys=("ts_code", "pub_date", "imp_date"),
        pit_status="UNSUPPORTED_PIT",
        notes="ST event-detail alias; retained separately from stock_st if exposed.",
        allow_tiny=True,
    ),
    _spec(
        "bak_basic",
        "bak_basic",
        "PIT/reference",
        "P0-B",
        "trade_date_batch",
        "year_month",
        start_date="20160901",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes=(
            "Historical reference snapshot; do not substitute its financial fields for PIT corpus. "
            "Earlier month-range responses are retained as invalid-query evidence."
        ),
        storage_dataset="bak_basic_archive",
    ),
    _spec(
        "namechange",
        "namechange",
        "PIT/reference",
        "P0-B",
        "year_range",
        "year",
        probe={"ts_code": "600000.SH"},
        date_fields=("ann_date", "start_date", "end_date"),
        primary_keys=("ts_code", "name", "start_date"),
        pit_status="PARTIAL_OR_UNSUPPORTED",
        notes="Raw history only; download does not resolve historical identity/version semantics.",
        allow_tiny=True,
    ),
    _spec(
        "stock_company",
        "stock_company",
        "PIT/reference",
        "P0-B",
        "snapshot",
        "snapshot",
        fixed={"ts_code": "600000.SH"},
        probe={"ts_code": "600000.SH"},
        date_fields=(),
        primary_keys=("ts_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        notes="Current company reference snapshot, not a historical lifecycle table.",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "new_share",
        "new_share",
        "PIT/reference",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        date_fields=("ipo_date", "issue_date", "上市日期"),
        primary_keys=("ts_code",),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "stk_limit",
        "stk_limit",
        "PIT/reference",
        "P0-B",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "forecast_archive",
        "forecast",
        "financial supplementary",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "period": "20241231"},
        date_fields=("ann_date", "end_date", "first_ann_date"),
        primary_keys=("ts_code", "end_date", "type", "ann_date", "update_flag"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes=(
            "Existing raw/forecast is a small sample; archive is isolated and never overwrites it."
        ),
        full_market_query_supported=False,
        storage_dataset="forecast_archive",
    ),
    _spec(
        "express_archive",
        "express",
        "financial supplementary",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "period": "20241231"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date", "ann_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes=(
            "Existing raw/express is a small sample; archive is isolated and "
            "preserves source schema."
        ),
        full_market_query_supported=False,
        storage_dataset="express_archive",
    ),
    _spec(
        "fina_audit_archive",
        "fina_audit",
        "financial supplementary",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "period": "20241231"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date", "ann_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Existing raw/fina_audit is a small sample; archive is isolated.",
        full_market_query_supported=False,
        storage_dataset="fina_audit_archive",
        allow_tiny=True,
    ),
    _spec(
        "fina_mainbz_archive",
        "fina_mainbz",
        "financial supplementary",
        "P0-B",
        "report_period",
        "year",
        probe={"period": "20241231"},
        fallback={"ts_code": "600000.SH", "period": "20241231"},
        date_fields=("end_date",),
        primary_keys=("ts_code", "end_date", "bz_item", "curr_type", "update_flag"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Existing raw/fina_mainbz is a small sample; archive is isolated.",
        full_market_query_supported=False,
        storage_dataset="fina_mainbz_archive",
        allow_tiny=True,
    ),
    _spec(
        "disclosure_date_archive",
        "disclosure_date",
        "financial supplementary",
        "P0-B",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "end_date", "pre_date", "actual_date", "modify_date"),
        primary_keys=("ts_code", "end_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Event dates remain distinct from financial-record availability dates.",
        storage_dataset="disclosure_date_archive",
        allow_tiny=True,
    ),
    _spec(
        "stk_surv",
        "stk_surv",
        "institutional survey",
        "P0-A",
        "year_range",
        "year",
        start_date="20210101",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("surv_date", "survey_date", "survey_time", "ann_date"),
        primary_keys=("ts_code", "survey_date", "org_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Institutional/research survey source, including raw attention fields.",
        allow_tiny=True,
    ),
    _spec(
        "broker_recommend",
        "broker_recommend",
        "analyst/research",
        "P0-A",
        "month",
        "year_month",
        start_date="20210101",
        probe={"month": "202401"},
        fallback={"month": "202412"},
        date_fields=("recommend_date", "ann_date"),
        primary_keys=("ts_code", "recommend_date", "broker"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Broker recommendation/gold-stock candidate API if exposed by the proxy.",
        allow_tiny=True,
    ),
    _spec(
        "share_float",
        "share_float",
        "ownership/governance",
        "P1",
        "month_range",
        "year_month",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "float_date"),
        primary_keys=("ts_code", "ann_date", "float_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes=(
            "Earlier range-query files are retained as unvalidated evidence; archive uses ann_date."
        ),
        storage_dataset="share_float_archive",
        allow_tiny=True,
    ),
    _spec(
        "stk_holdernumber",
        "stk_holdernumber",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "ann_date", "end_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "top10_holders",
        "top10_holders",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"ann_date": "20240131"},
        fallback={"ts_code": "600000.SH", "period": "20231231"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date", "holder_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "top10_floatholders",
        "top10_floatholders",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"ann_date": "20240131"},
        fallback={"ts_code": "600000.SH", "period": "20231231"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date", "holder_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        full_market_query_supported=False,
        allow_tiny=True,
    ),
    _spec(
        "stk_holdertrade",
        "stk_holdertrade",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "trade_date"),
        primary_keys=("ts_code", "ann_date", "holder_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "pledge_stat",
        "pledge_stat",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "pledge_detail",
        "pledge_detail",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "pledge_date", "release_date"),
        primary_keys=("ts_code", "ann_date", "holder_name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "repurchase",
        "repurchase",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "ann_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "dividend",
        "dividend",
        "ownership/governance",
        "P1",
        "year_range",
        "year",
        probe={"ann_date": "20240131"},
        fallback={"ts_code": "600000.SH", "end_date": "20231231"},
        date_fields=("ann_date", "end_date", "ex_date", "imp_ann_date"),
        primary_keys=("ts_code", "end_date", "ann_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        full_market_query_supported=False,
        allow_tiny=True,
    ),
    _spec(
        "block_trade",
        "block_trade",
        "event/market behavior",
        "P1",
        "month_range",
        "year_month",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date", "price", "vol"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Prior limit=5000 files remain page-cap-unvalidated evidence.",
        storage_dataset="block_trade_archive",
        allow_tiny=True,
    ),
    _spec(
        "moneyflow",
        "moneyflow",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "moneyflow_ths",
        "moneyflow_ths",
        "flow/crowding",
        "P1",
        "trade_date_batch",
        "year_month",
        probe={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        fallback_is_full_market=True,
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="THS provider namespace is kept separate from ordinary moneyflow.",
    ),
    _spec(
        "moneyflow_dc",
        "moneyflow_dc",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="DC provider namespace is kept separate from ordinary moneyflow.",
    ),
    _spec(
        "margin",
        "margin",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("trade_date", "exchange_id"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "margin_detail",
        "margin_detail",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "margin_secs",
        "margin_secs",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "hk_hold",
        "hk_hold",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        probe={"ts_code": "600000.SH", "start_date": "20240101", "end_date": "20240131"},
        fallback={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "ggt_top10",
        "ggt_top10",
        "flow/crowding",
        "P1",
        "trade_date_batch",
        "year_month",
        start_date="20160101",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("trade_date", "ts_code"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "ggt_daily",
        "ggt_daily",
        "flow/crowding",
        "P1",
        "month_range",
        "year_month",
        start_date="20141101",
        probe={"start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("trade_date",),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "index_member_all",
        "index_member_all",
        "industry/index",
        "P0-B",
        "index_snapshot",
        "index_snapshot",
        fixed={"index_code": "801010.SI"},
        probe={"index_code": "801010.SI"},
        date_fields=("in_date", "out_date"),
        primary_keys=("index_code", "ts_code", "in_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Historical membership in/out dates are retained as source fields.",
        allow_tiny=True,
    ),
    _spec(
        "index_member",
        "index_member",
        "industry/index",
        "P1",
        "index_snapshot",
        "index_snapshot",
        fixed={"index_code": "000300.SH"},
        probe={"index_code": "000300.SH"},
        date_fields=("in_date", "out_date"),
        primary_keys=("index_code", "con_code", "in_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "index_weight",
        "index_weight",
        "industry/index",
        "P1",
        "index_year",
        "index_year",
        fixed={"index_code": "000300.SH"},
        probe={"index_code": "000300.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("index_code", "con_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "index_daily_benchmarks",
        "index_daily",
        "industry/index",
        "P1",
        "index_month",
        "index_month",
        fixed={
            "index_codes": (
                "000001.SH",
                "399001.SZ",
                "399006.SZ",
                "000905.SH",
                "000852.SH",
                "000688.SH",
            )
        },
        probe={"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Additional benchmarks only; existing primary 000300.SH is not redownloaded.",
    ),
    _spec(
        "index_classify",
        "index_classify",
        "industry/index",
        "P1",
        "snapshot",
        "snapshot",
        probe={},
        date_fields=(),
        primary_keys=("index_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        notes="Industry taxonomy snapshot; historical membership is a separate archive.",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "sw_daily",
        "sw_daily",
        "industry/index",
        "P1",
        "month_range",
        "year_month",
        fixed={"ts_code": "801010.SI"},
        probe={"ts_code": "801010.SI", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "sw_member",
        "sw_member",
        "industry/index",
        "P1",
        "index_snapshot",
        "index_snapshot",
        fixed={"index_code": "801010.SI"},
        probe={"index_code": "801010.SI"},
        date_fields=("in_date", "out_date"),
        primary_keys=("index_code", "con_code", "in_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "ci_index",
        "ci_index",
        "industry/index",
        "P1",
        "snapshot",
        "snapshot",
        probe={},
        primary_keys=("index_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "ci_member",
        "ci_member",
        "industry/index",
        "P1",
        "index_snapshot",
        "index_snapshot",
        fixed={"index_code": "CI005001.CI"},
        probe={"index_code": "CI005001.CI"},
        date_fields=("in_date", "out_date"),
        primary_keys=("index_code", "con_code", "in_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "ci_daily",
        "ci_daily",
        "industry/index",
        "P1",
        "month_range",
        "year_month",
        fixed={"ts_code": "CI005001.CI"},
        probe={"ts_code": "CI005001.CI", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "ths_index",
        "ths_index",
        "alternative/attention",
        "P1",
        "snapshot",
        "snapshot",
        probe={},
        primary_keys=("ts_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "ths_member",
        "ths_member",
        "alternative/attention",
        "P1",
        "index_snapshot",
        "index_snapshot",
        fixed={"ts_code": "885001.TI"},
        probe={"ts_code": "885001.TI"},
        primary_keys=("ts_code", "con_code"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "ths_daily",
        "ths_daily",
        "alternative/attention",
        "P1",
        "month_range",
        "year_month",
        fixed={"ts_code": "885001.TI"},
        probe={"ts_code": "885001.TI", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "ths_hot",
        "ths_hot",
        "alternative/attention",
        "P1",
        "snapshot",
        "snapshot",
        probe={},
        pit_status="CURRENT_SNAPSHOT_ONLY",
        notes="Hot-list endpoint is recorded but not historicalized without stable date coverage.",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "ths_hot_rank",
        "ths_hot_rank",
        "alternative/attention",
        "P1",
        "none",
        "current",
        probe={},
        pit_status="CURRENT_SNAPSHOT_ONLY",
        notes="Current snapshot only unless inventory proves historical date support.",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "dc_index",
        "dc_index",
        "alternative/attention",
        "P1",
        "none",
        "current",
        probe={},
        primary_keys=("ts_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "dc_member",
        "dc_member",
        "alternative/attention",
        "P1",
        "index_snapshot",
        "index_snapshot",
        fixed={"ts_code": "BK00001.DC"},
        probe={"ts_code": "BK00001.DC"},
        primary_keys=("ts_code", "con_code"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "dc_daily",
        "dc_daily",
        "alternative/attention",
        "P1",
        "month_range",
        "year_month",
        fixed={"ts_code": "BK00001.DC"},
        probe={"ts_code": "BK00001.DC", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "dc_hot",
        "dc_hot",
        "alternative/attention",
        "P1",
        "none",
        "current",
        probe={},
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "dc_hot_rank",
        "dc_hot_rank",
        "alternative/attention",
        "P1",
        "none",
        "current",
        probe={},
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "top_list",
        "top_list",
        "event/market behavior",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date", "name"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "top_inst",
        "top_inst",
        "event/market behavior",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date", "exalter"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "limit_list_d",
        "limit_list_d",
        "event/market behavior",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "limit_list_ths",
        "limit_list_ths",
        "event/market behavior",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "limit_list",
        "limit_list",
        "event/market behavior",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "stk_auction",
        "stk_auction",
        "event/market behavior",
        "P1",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "stk_auction_c",
        "stk_auction_c",
        "event/market behavior",
        "P1",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "600000.SH", "trade_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        allow_tiny=True,
    ),
    _spec(
        "fund_basic",
        "fund_basic",
        "fund/ownership",
        "P2",
        "snapshot",
        "snapshot",
        probe={"market": "E"},
        primary_keys=("ts_code",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "fund_portfolio",
        "fund_portfolio",
        "fund/ownership",
        "P2",
        "report_period",
        "year",
        probe={"ts_code": "510300.SH", "period": "20241231"},
        date_fields=("ann_date", "end_date"),
        primary_keys=("ts_code", "end_date", "symbol", "amount"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Institutional attention/crowding evidence; not a fund strategy input.",
        allow_tiny=True,
    ),
    _spec(
        "fund_share",
        "fund_share",
        "fund/ownership",
        "P2",
        "trade_date_batch",
        "year_month",
        probe={"trade_date": "20240131"},
        fallback={"ts_code": "510300.SH", "start_date": "20240101", "end_date": "20240131"},
        fallback_is_full_market=True,
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _spec(
        "fund_manager",
        "fund_manager",
        "fund/ownership",
        "P2",
        "snapshot",
        "snapshot",
        probe={"ts_code": "510300.SH"},
        date_fields=("ann_date", "begin_date", "end_date"),
        primary_keys=("ts_code", "name", "begin_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "fund_company",
        "fund_company",
        "fund/ownership",
        "P2",
        "snapshot",
        "snapshot",
        probe={},
        primary_keys=("name",),
        pit_status="CURRENT_SNAPSHOT_ONLY",
        current_only=True,
        allow_tiny=True,
    ),
    _spec(
        "fund_nav",
        "fund_nav",
        "fund/ownership",
        "P2",
        "month_range",
        "year_month",
        probe={"ts_code": "510300.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("nav_date",),
        primary_keys=("ts_code", "nav_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
        notes="Large and lower-priority NAV history; no strategy use in this run.",
        full_market_query_supported=False,
    ),
    _spec(
        "fund_daily",
        "fund_daily",
        "fund/ownership",
        "P2",
        "month_range",
        "year_month",
        probe={"ts_code": "510300.SH", "start_date": "20240101", "end_date": "20240131"},
        date_fields=("trade_date",),
        primary_keys=("ts_code", "trade_date"),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _existing(
        "trade_cal",
        "trade_cal",
        "existing market/reference",
        notes="Existing 2012-2025 SSE/SZSE calendar is not redownloaded.",
    ),
    _existing(
        "daily",
        "daily",
        "existing market/reference",
        notes="Existing 2012-2025 daily corpus is not redownloaded.",
    ),
    _existing(
        "daily_basic",
        "daily_basic",
        "existing market/reference",
        notes=(
            "Existing 2012-2025 corpus is protected; one prior duplicate checkpoint remains a gap."
        ),
        pit_status="PIT_REQUIRES_VALIDATION",
    ),
    _existing(
        "suspend_d",
        "suspend_d",
        "existing market/reference",
        notes="Existing 2012-2025 suspension corpus is not redownloaded.",
    ),
    _existing(
        "index_basic",
        "index_basic",
        "existing market/reference",
        notes="Existing primary benchmark reference snapshot is not redownloaded.",
        pit_status="CURRENT_SNAPSHOT_ONLY",
    ),
    _existing(
        "index_daily",
        "index_daily",
        "existing market/reference",
        notes=(
            "Existing primary 000300.SH history is not redownloaded; extra benchmarks are separate."
        ),
    ),
    _existing(
        "income",
        "income_vip",
        "existing financial P0",
        notes="Existing validated VIP history is protected.",
    ),
    _existing(
        "balancesheet",
        "balancesheet_vip",
        "existing financial P0",
        notes="Existing validated VIP history is protected.",
    ),
    _existing(
        "cashflow",
        "cashflow_vip",
        "existing financial P0",
        notes="Existing validated VIP history is protected.",
    ),
    _existing(
        "fina_indicator",
        "fina_indicator_vip",
        "existing financial P0",
        notes="Existing validated VIP history is protected.",
    ),
)


# The four core financial and the verified market datasets are explicitly
# protected.  Other files in raw/ are samples and are archived under suffixes.
PROTECTED_EXISTING_DATASETS = frozenset(
    {
        "trade_cal",
        "daily",
        "daily_basic",
        "suspend_d",
        "index_basic",
        "index_daily",
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "stock_basic",
    }
)


def catalog_by_dataset() -> dict[str, HarvestSpec]:
    return {spec.dataset: spec for spec in HARVEST_SPECS}


# ---------------------------------------------------------------------------
# Local inventory and API probes
# ---------------------------------------------------------------------------


def _atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _end_date(spec: HarvestSpec, end_date: str | None) -> str:
    value = end_date or spec.end_date or date.today().strftime("%Y%m%d")
    parsed = date_text(value)
    if parsed is None:
        raise ValueError(f"invalid harvest end date: {value!r}")
    if parsed < spec.start_date:
        raise ValueError(f"harvest end date {parsed} precedes start date {spec.start_date}")
    return parsed


def _date_range(start: str, end: str) -> tuple[str, str]:
    start_text = date_text(start)
    end_text = date_text(end)
    if start_text is None or end_text is None or end_text < start_text:
        raise ValueError(f"invalid date range {start!r}..{end!r}")
    return start_text, end_text


def _latest_checkpoint_status(data_dir: Path, dataset: str) -> str:
    paths = (
        data_dir / "state" / "harvest-checkpoints.json",
        data_dir / "state" / "bootstrap-checkpoints.json",
        data_dir / "state" / "market-bootstrap-checkpoints.json",
    )
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, list):
            records.extend(
                record
                for record in value
                if isinstance(record, dict) and record.get("dataset") == dataset
            )
    if not records:
        return "UNKNOWN"
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        unit = str(record.get("unit") or record.get("period") or record.get("period_key") or "")
        if unit:
            latest[unit] = record
    statuses = {str(record.get("status", "")).upper() for record in latest.values()}
    if statuses == {"PASS"}:
        return "COMPLETE"
    if "PASS" in statuses:
        return "PARTIAL"
    return "PARTIAL"


def local_dataset_state(
    data_dir: str | Path,
    spec: HarvestSpec,
    *,
    read_values: bool = False,
) -> LocalDatasetState:
    """Inspect local Parquet metadata; load columns only for an explicit audit."""

    root = Path(data_dir).expanduser()
    dataset_dir = root / "raw" / spec.storage_name
    paths = sorted(dataset_dir.rglob("*.parquet")) if dataset_dir.exists() else []
    errors: list[str] = []
    warnings: list[str] = []
    total_rows = 0
    total_size = 0
    schemas: set[str] = set()
    symbols: set[str] = set()
    dates: list[pd.Timestamp] = []
    date_fields = tuple(dict.fromkeys((*spec.date_fields, *_DATE_COLUMNS)))

    for path in paths:
        if path.stat().st_size == 0:
            errors.append(f"zero_byte:{path}")
            continue
        try:
            parquet = pq.ParquetFile(path)
            total_rows += int(parquet.metadata.num_rows)
            total_size += path.stat().st_size
            columns = tuple(str(column) for column in parquet.schema_arrow.names)
            schemas.add(schema_hash(columns))
            if read_values:
                read_columns = [column for column in ("ts_code", *date_fields) if column in columns]
                if read_columns:
                    frame = pd.read_parquet(path, columns=read_columns)
                    if "ts_code" in frame:
                        symbols.update(frame["ts_code"].dropna().astype(str).unique())
                    for column in date_fields:
                        if column not in frame:
                            continue
                        parsed = normalize_date_series(frame[column]).dropna()
                        if not parsed.empty:
                            dates.extend([parsed.min(), parsed.max()])
        except Exception as exc:  # pragma: no cover - concrete engines vary
            errors.append(f"unreadable:{path}:{type(exc).__name__}:{exc}")

    checkpoint_status = _latest_checkpoint_status(root, spec.dataset)
    if errors:
        status = "UNREADABLE"
    elif not paths:
        status = "MISSING"
    elif spec.dataset in PROTECTED_EXISTING_DATASETS:
        status = "SKIP_EXISTING_COMPLETE" if checkpoint_status == "COMPLETE" else "PARTIAL_EXISTING"
    else:
        status = "EXISTING_LOCAL"
    if len(schemas) > 1:
        warnings.append("schema_drift")
    if paths and read_values and not dates:
        warnings.append("no_recognized_date_column")
    return LocalDatasetState(
        dataset=spec.dataset,
        storage_dataset=spec.storage_name,
        files=len(paths),
        rows=total_rows,
        size_bytes=total_size,
        date_min=min(dates).strftime("%Y%m%d") if dates else None,
        date_max=max(dates).strftime("%Y%m%d") if dates else None,
        symbols=len(symbols),
        schemas=tuple(sorted(schemas)),
        checkpoint_status=checkpoint_status,
        status=status,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _frame_dates(frame: pd.DataFrame, fields: Iterable[str]) -> tuple[str | None, str | None]:
    values: list[pd.Timestamp] = []
    for field_name in fields:
        if field_name not in frame:
            continue
        parsed = normalize_date_series(frame[field_name]).dropna()
        if not parsed.empty:
            values.extend([parsed.min(), parsed.max()])
    if not values:
        return None, None
    return min(values).strftime("%Y%m%d"), max(values).strftime("%Y%m%d")


def _safe_error(exc: BaseException) -> tuple[str | None, str]:
    if isinstance(exc, ProviderError):
        return exc.error_type, exc.error_message
    return type(exc).__name__, str(exc)


def probe_api_inventory(
    provider: TushareProvider | None,
    data_dir: str | Path,
    *,
    specs: Sequence[HarvestSpec] = HARVEST_SPECS,
    deadline: str | None = None,
    rate_limiter: RateLimiter | None = None,
) -> ApiInventory:
    """Probe each catalog API with at most one bounded request plus fallback."""

    root = Path(data_dir).expanduser()
    selected = tuple(dict.fromkeys(specs))
    limiter = rate_limiter
    if provider is not None and limiter is None:
        limiter = getattr(provider, "rate_limiter", None) or RateLimiter(60.0)
        provider.set_rate_limiter(limiter)
    results: list[ApiInventoryResult] = []

    for spec in selected:
        local = local_dataset_state(root, spec)
        sample_request = spec.probe
        fields: tuple[str, ...] = ()
        sample_rows = 0
        earliest = latest = None
        probe_status = "NOT_RUN"
        result_status = "AVAILABLE_NOT_ARCHIVED"
        reachable: bool = False
        permission_ok: bool | None = None
        permission = "NOT_PROBED"
        reason = "provider not configured"
        error_type: str | None = None
        error_message: str | None = None
        fallback_used = False

        if provider is None:
            probe_status = "SKIPPED_NO_TOKEN"
            permission = "UNKNOWN"
            result_status = (
                "SKIPPED_EXISTING_COMPLETE"
                if local.status == "SKIP_EXISTING_COMPLETE"
                else "UNKNOWN"
            )
        else:
            attempts: list[tuple[dict[str, Any], bool]] = [(spec.probe, False)]
            if spec.probe_fallback:
                attempts.append((spec.probe_fallback, True))
            for request, used_fallback in attempts:
                sample_request = dict(request)
                sample_request.setdefault("limit", 3)
                try:
                    frame = provider.call(spec.api, **sample_request)
                except ProviderError as exc:
                    error_type, error_message = _safe_error(exc)
                    reachable = error_type not in {"connection", "timeout"}
                    permission_ok = False if error_type == "permission" else None
                    permission = "DENIED" if error_type == "permission" else "UNKNOWN"
                    probe_status = error_type.upper()
                    reason = error_message
                    if used_fallback:
                        fallback_used = True
                    # A parameter/compatibility error may be recoverable via
                    # the stock-code fallback; permission/not-found is not.
                    if (
                        not used_fallback
                        and spec.probe_fallback
                        and error_type
                        in {
                            "compatibility",
                            "http_error",
                            "unknown",
                        }
                    ):
                        continue
                    break
                except Exception as exc:  # provider normally wraps these
                    error_type, error_message = _safe_error(exc)
                    probe_status = error_type.upper() if error_type else "FAILED"
                    reason = error_message
                    break
                else:
                    fallback_used = used_fallback
                    reachable = True
                    permission_ok = True
                    permission = "OK"
                    sample_rows = len(frame)
                    fields = tuple(str(column) for column in frame.columns)
                    earliest, latest = _frame_dates(frame, (*spec.date_fields, *_DATE_COLUMNS))
                    probe_status = "PASS" if not frame.empty else "EMPTY"
                    reason = (
                        "bounded response returned; historical coverage requires download audit"
                        if not frame.empty
                        else "bounded response was empty; no historical completeness claim"
                    )
                    if frame.empty and not used_fallback and spec.probe_fallback:
                        continue
                    break

        if local.status == "SKIP_EXISTING_COMPLETE":
            result_status = "SKIPPED_EXISTING_COMPLETE"
        elif spec.current_only:
            result_status = "CURRENT_ONLY" if probe_status in {"PASS", "EMPTY"} else probe_status
        elif permission_ok is False and probe_status in {"PERMISSION", "NOT_FOUND"}:
            result_status = "UNSUPPORTED" if probe_status == "NOT_FOUND" else "PERMISSION_DENIED"
        elif probe_status == "PASS":
            result_status = "AVAILABLE_NOT_ARCHIVED"
        elif probe_status == "EMPTY":
            result_status = "AVAILABLE_NOT_ARCHIVED"
        elif probe_status == "SKIPPED_NO_TOKEN":
            result_status = "UNKNOWN"
        else:
            result_status = probe_status

        results.append(
            ApiInventoryResult(
                dataset=spec.dataset,
                provider="tushare",
                api_name=spec.api,
                category=spec.category,
                priority=spec.priority,
                reachable=reachable,
                permission_ok=permission_ok,
                permission=permission,
                probe_status=probe_status,
                result=result_status,
                sample_request=sample_request,
                sample_rows=sample_rows,
                fields=fields,
                earliest_date_if_known=earliest,
                latest_date_if_known=latest,
                estimated_partition_strategy=spec.partition_strategy,
                estimated_request_count=None,
                estimated_volume=("heavy/unknown" if spec.heavy else "bounded sample only"),
                local_status=local.status,
                raw_path=f"data/raw/{spec.storage_name}",
                pit_status=spec.pit_status,
                reason=reason,
                error_type=error_type,
                error_message=error_message,
                fallback_used=fallback_used,
                notes=spec.notes,
            )
        )

    endpoint_kind = provider.endpoint_kind if provider is not None else "not_configured"
    return ApiInventory(
        generated_at=datetime.now(UTC).isoformat(),
        data_dir=str(root),
        provider="tushare",
        endpoint_kind=endpoint_kind,
        token_configured=provider is not None,
        deadline=deadline or os.getenv("ASHARE_HARVEST_DEADLINE") or None,
        catalog_version="2026-archive-v1",
        apis=tuple(results),
    )


def inventory_dict(inventory: ApiInventory) -> dict[str, Any]:
    return {
        "generated_at": inventory.generated_at,
        "data_dir": inventory.data_dir,
        "provider": inventory.provider,
        "endpoint_kind": inventory.endpoint_kind,
        "token_configured": inventory.token_configured,
        "deadline": inventory.deadline,
        "catalog_version": inventory.catalog_version,
        "apis": [asdict(value) for value in inventory.apis],
    }


def write_api_inventory(
    inventory: ApiInventory, directory: str | Path = ARTIFACT_DIR
) -> tuple[Path, Path]:
    root = Path(directory)
    json_path = root / "api-inventory.json"
    md_path = root / "api-inventory.md"
    _atomic_json_write(json_path, inventory_dict(inventory))
    lines = [
        "# Tushare API inventory",
        "",
        f"- Generated at (UTC): `{inventory.generated_at}`",
        f"- Data directory: `{inventory.data_dir}`",
        f"- Provider: `{inventory.provider}`",
        f"- Endpoint kind: `{inventory.endpoint_kind}` (private endpoint value is never recorded)",
        f"- Token configured: `{inventory.token_configured}`",
        f"- Deadline configured: `{bool(inventory.deadline)}`",
        f"- Catalog version: `{inventory.catalog_version}`",
        "",
        (
            "| API | Dataset | Permission | Category | Priority | Probe | Result | Rows | "
            "Earliest | Latest | Partition | Local | PIT |"
        ),
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for value in inventory.apis:
        lines.append(
            "| "
            + " | ".join(
                (
                    value.api_name,
                    value.dataset,
                    value.permission,
                    value.category,
                    value.priority,
                    value.probe_status,
                    value.result,
                    str(value.sample_rows),
                    value.earliest_date_if_known or "-",
                    value.latest_date_if_known or "-",
                    value.estimated_partition_strategy,
                    value.local_status,
                    value.pit_status,
                )
            )
            + " |"
        )
    lines.extend(["", "## Probe notes", ""])
    for value in inventory.apis:
        if value.reason or value.notes:
            lines.append(f"### `{value.api_name}`")
            lines.append("")
            lines.append(f"- Request: `{json.dumps(value.sample_request, ensure_ascii=False)}`")
            lines.append(f"- Reason: {value.reason}")
            if value.error_type:
                lines.append(f"- Error type: `{value.error_type}`")
            if value.notes:
                lines.append(f"- Notes: {value.notes}")
            lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# Dry-run planning
# ---------------------------------------------------------------------------


def _month_units(start: str, end: str) -> tuple[str, ...]:
    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    values = pd.period_range(start_date, end_date, freq="M")
    return tuple(str(value).replace("-", "") for value in values)


def _year_units(start: str, end: str) -> tuple[str, ...]:
    return tuple(str(year) for year in range(int(start[:4]), int(end[:4]) + 1))


def _calendar_trade_dates(data_dir: Path, start: str, end: str) -> tuple[str, ...]:
    dates: set[str] = set()
    calendar_dir = data_dir / "raw" / "trade_cal"
    for path in sorted(calendar_dir.rglob("*.parquet")) if calendar_dir.exists() else []:
        try:
            frame = pd.read_parquet(path, columns=["cal_date", "is_open"])
        except Exception:
            continue
        parsed = normalize_date_series(frame["cal_date"])
        opened = pd.to_numeric(frame["is_open"], errors="coerce").eq(1)
        for value in parsed[opened].dropna():
            text = value.strftime("%Y%m%d")
            if start <= text <= end:
                dates.add(text)
    # The verified local calendar ends in 2025.  Weekdays are only a planning
    # fallback for the current tail and are never represented as confirmed
    # source coverage until the target API returns rows.
    if not dates or max(dates) < end:
        for value in pd.date_range(
            max(pd.Timestamp(start), pd.Timestamp(max(dates) if dates else start)), end, freq="B"
        ):
            dates.add(value.strftime("%Y%m%d"))
    return tuple(sorted(dates))


def _units_for_spec(data_dir: Path, spec: HarvestSpec, start: str, end: str) -> tuple[str, ...]:
    mode = spec.query_mode
    if mode == "none":
        return ()
    if mode in {"year_range", "index_year"}:
        base = _year_units(start, end)
        if mode == "index_year":
            codes = spec.fixed.get("index_codes", (spec.fixed.get("index_code", ""),))
            return tuple(f"{code}|{year}" for code in codes for year in base)
        return base
    if mode == "report_period":
        suffixes = ("0331", "0630", "0930", "1231")
        return tuple(
            f"{year}{suffix}"
            for year in range(int(start[:4]), int(end[:4]) + 1)
            for suffix in suffixes
            if f"{year}{suffix}" <= end
        )
    if mode in {"month", "month_range", "trade_date_batch"}:
        return _month_units(start, end)
    if mode == "trade_date":
        return _calendar_trade_dates(data_dir, start, end)
    if mode in {"snapshot", "index_snapshot"}:
        return ("snapshot",)
    if mode == "index_month":
        months = _month_units(start, end)
        codes = spec.fixed.get(
            "index_codes", (spec.fixed.get("index_code") or spec.fixed.get("ts_code") or "",)
        )
        return tuple(f"{code}|{month}" for code in codes for month in months if code)
    raise ValueError(f"unsupported harvest query mode: {mode}")


def _query_for_unit(
    data_dir: Path,
    spec: HarvestSpec,
    unit: str,
    *,
    end_date: str | None = None,
) -> dict[str, Any]:
    query = spec.fixed.copy()
    query.pop("index_codes", None)
    if spec.query_mode in {"year_range", "index_year"}:
        if "|" in unit:
            code, year = unit.split("|", 1)
            query.setdefault("index_code", code)
        else:
            year = unit
        query_end = f"{year}1231"
        if end_date and year == end_date[:4]:
            query_end = min(query_end, end_date)
        query.update(start_date=f"{year}0101", end_date=query_end)
    elif spec.query_mode == "report_period":
        query["period"] = unit
    elif spec.query_mode == "month":
        query["month"] = unit
    elif spec.query_mode in {"month_range", "index_month"}:
        if "|" in unit:
            code, month = unit.split("|", 1)
            if spec.api in {"index_weight", "index_member", "index_member_all"}:
                query.setdefault("index_code", code)
            else:
                query.setdefault("ts_code", code)
        else:
            month = unit
        period = pd.Period(month, freq="M")
        query_end = period.end_time.strftime("%Y%m%d")
        if end_date and period.start_time.strftime("%Y%m") == end_date[:6]:
            query_end = min(query_end, end_date)
        query.update(
            start_date=period.start_time.strftime("%Y%m%d"),
            end_date=query_end,
        )
    elif spec.query_mode == "trade_date":
        query["trade_date"] = unit
    elif spec.query_mode == "trade_date_batch":
        # A month is a durable unit; subqueries are expanded by _subqueries.
        period = pd.Period(unit, freq="M")
        query_end = period.end_time.strftime("%Y%m%d")
        if end_date and period.start_time.strftime("%Y%m") == end_date[:6]:
            query_end = min(query_end, end_date)
        query.update(
            start_date=period.start_time.strftime("%Y%m%d"),
            end_date=query_end,
        )
    elif spec.query_mode in {"snapshot", "index_snapshot"}:
        # The date is a local partition label, not an invented API parameter.
        pass
    return query


def _subqueries(
    data_dir: Path, spec: HarvestSpec, unit: str, query: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    if spec.api in {
        "report_rc",
        "share_float",
        "stk_holdernumber",
        "top10_holders",
        "stk_holdertrade",
        "pledge_detail",
        "repurchase",
    } and {"start_date", "end_date"}.issubset(query):
        # Several annual event/ownership APIs reject offsets beyond their
        # server window.  Keep the durable unit as one year while using a
        # bounded date window inside it, so COMPLETE still means every window
        # succeeded.  share_float can exceed the limit even in a short range,
        # so it uses exact announcement-date queries; the other APIs use
        # calendar months.
        start = str(query["start_date"])
        end = str(query["end_date"])
        if spec.api in {"share_float", "top10_holders"}:
            base = {key: query[key] for key in query if key not in {"start_date", "end_date"}}
            return tuple(
                {**base, "ann_date": value.strftime("%Y%m%d")}
                for value in pd.date_range(start, end, freq="D")
            )
        months = _month_units(start, end)
        return tuple(
            {
                **query,
                "start_date": f"{month}01",
                "end_date": pd.Period(month, freq="M").end_time.strftime("%Y%m%d"),
            }
            for month in months
        )
    if spec.api == "adj_factor" and {"start_date", "end_date"}.issubset(query):
        # Recent months can exceed the provider's maximum offset.  Daily
        # subqueries keep the month partition durable while avoiding that API
        # ceiling and preserve the exact trade-date rows.
        start = str(query["start_date"])
        end = str(query["end_date"])
        dates = _calendar_trade_dates(data_dir, start, end)
        base = {key: value for key, value in query.items() if key not in {"start_date", "end_date"}}
        return tuple({**base, "trade_date": value} for value in dates) or (query,)
    if spec.query_mode != "trade_date_batch":
        return (query,)
    start = str(query["start_date"])
    end = str(query["end_date"])
    dates = _calendar_trade_dates(data_dir, start, end)
    if not dates:
        return (query,)
    base = {key: value for key, value in query.items() if key not in {"start_date", "end_date"}}
    return tuple({**base, "trade_date": value} for value in dates)


def _partition_for_unit(spec: HarvestSpec, unit: str) -> str:
    if spec.partition_strategy == "year":
        year = unit.split("|")[-1][:4]
        return f"year={year}"
    if spec.partition_strategy == "year_month":
        month = unit.split("|")[-1]
        return f"year={month[:4]}/month={month[:6]}"
    if spec.partition_strategy == "year_month_trade_date":
        value = unit.split("|")[-1]
        return f"year={value[:4]}/month={value[:6]}/trade_date={value}"
    if spec.partition_strategy == "index_year":
        code, year = unit.split("|", 1)
        return f"index={code}/year={year[:4]}"
    if spec.partition_strategy == "index_month":
        code, month = unit.split("|", 1)
        return f"index={code}/year={month[:4]}/month={month[:6]}"
    if spec.partition_strategy == "index_snapshot":
        return f"snapshot={date.today().strftime('%Y%m%d')}"
    if spec.partition_strategy == "snapshot":
        return f"snapshot={date.today().strftime('%Y%m%d')}"
    if spec.partition_strategy == "current":
        return f"snapshot={date.today().strftime('%Y%m%d')}"
    raise ValueError(f"unsupported partition strategy: {spec.partition_strategy}")


def _inventory_by_dataset(inventory: ApiInventory | None) -> dict[str, ApiInventoryResult]:
    return {value.dataset: value for value in inventory.apis} if inventory else {}


def build_download_plan(
    data_dir: str | Path,
    *,
    inventory: ApiInventory | None = None,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    workers: int = 4,
    rate_limit: float = 60.0,
    soft_free_space: int = SOFT_FREE_SPACE,
    hard_free_space: int = HARD_FREE_SPACE,
    specs: Sequence[HarvestSpec] = HARVEST_SPECS,
    checkpoint_namespace: str = "data/state/harvest-checkpoints.json",
) -> DownloadPlan:
    root = Path(data_dir).expanduser()
    start, end = _date_range(start_date, end_date or date.today().strftime("%Y%m%d"))
    if workers <= 0 or not math.isfinite(rate_limit) or rate_limit <= 0:
        raise ValueError("workers and rate_limit must be positive")
    inv = _inventory_by_dataset(inventory)
    checkpoint_store = HarvestCheckpointStore(root / CHECKPOINT_FILENAME)
    entries: list[PlanEntry] = []
    for spec in sorted(specs, key=lambda value: (_PRIORITY_RANK[value.priority], value.dataset)):
        local = local_dataset_state(root, spec)
        inventory_value = inv.get(spec.dataset)
        unit_start = max(spec.start_date, start)
        units = _units_for_spec(root, spec, unit_start, end)
        completed_units = checkpoint_store.completed_units(
            spec.dataset, spec.api, spec.storage_name
        )
        existing_units = len(set(units).intersection(completed_units))
        if local.status == "SKIP_EXISTING_COMPLETE" and units:
            existing_units = len(units)
        remaining_units = max(0, len(units) - existing_units)
        sample_rows = inventory_value.sample_rows if inventory_value else 0
        field_count = len(inventory_value.fields) if inventory_value else 0
        # This is explicitly a lower-bound planning estimate from a limit=3
        # probe, not a promise of full-market size.
        estimated_rows = max(0, sample_rows * remaining_units)
        estimated_size = estimated_rows * max(256, field_count * 16)
        if units:
            # Count planned subqueries, not pagination pages; this remains a
            # lower bound because a full response can require many pages.
            first_query = _query_for_unit(root, spec, units[0], end_date=end)
            subquery_count = len(_subqueries(root, spec, units[0], first_query))
        else:
            subquery_count = 1
        estimated_requests = remaining_units * max(1, subquery_count)
        if inventory_value is None:
            status = "NOT_INVENTORIED"
            permission = "UNKNOWN"
            inv_result = "UNKNOWN"
        elif local.status == "SKIP_EXISTING_COMPLETE":
            status = "SKIP_EXISTING_COMPLETE"
            permission = inventory_value.permission
            inv_result = inventory_value.result
        elif spec.current_only:
            status = "CURRENT_ONLY"
            permission = inventory_value.permission
            inv_result = inventory_value.result
        elif not spec.full_market_query_supported:
            status = "AVAILABLE_NOT_ARCHIVED"
            permission = inventory_value.permission
            inv_result = inventory_value.result
            remaining_units = 0
            estimated_requests = 0
            estimated_rows = 0
            estimated_size = 0
        elif inventory_value.permission_ok is False:
            status = (
                "UNSUPPORTED" if inventory_value.result == "UNSUPPORTED" else "PERMISSION_DENIED"
            )
            permission = inventory_value.permission
            inv_result = inventory_value.result
            remaining_units = 0
            estimated_requests = 0
            estimated_rows = 0
            estimated_size = 0
        elif inventory_value.probe_status == "PASS":
            status = "READY"
            permission = inventory_value.permission
            inv_result = inventory_value.result
        elif inventory_value.probe_status == "EMPTY":
            status = "AVAILABLE_NOT_ARCHIVED"
            permission = inventory_value.permission
            inv_result = inventory_value.result
            remaining_units = 0
            estimated_requests = 0
            estimated_rows = 0
            estimated_size = 0
        else:
            status = inventory_value.result or inventory_value.probe_status
            permission = inventory_value.permission
            inv_result = inventory_value.result
            remaining_units = 0
            estimated_requests = 0
            estimated_rows = 0
            estimated_size = 0
        limitations = [
            item for item in (spec.notes, inventory_value.reason if inventory_value else "") if item
        ]
        if not spec.full_market_query_supported:
            limitations.append(
                "planned query requires per-security-code retrieval; not archived due volume"
            )
        if inventory_value and inventory_value.probe_status == "EMPTY":
            limitations.append("bounded probe was empty; no completeness claim")
        entries.append(
            PlanEntry(
                dataset=spec.dataset,
                provider="tushare",
                api=spec.api,
                category=spec.category,
                priority=spec.priority,
                planned_range=f"{unit_start}..{end}",
                planned_units=len(units),
                existing_units=existing_units,
                remaining_units=remaining_units,
                estimated_requests=estimated_requests,
                estimated_rows=estimated_rows,
                estimated_size_bytes=estimated_size,
                worker_count=workers,
                rate_limit=float(rate_limit),
                partition_strategy=spec.partition_strategy,
                checkpoint_namespace=checkpoint_namespace,
                raw_path=f"data/raw/{spec.storage_name}",
                inventory_result=inv_result,
                permission=permission,
                pit_status=spec.pit_status,
                status=status,
                estimation_basis=(
                    "bounded limit=3 probe lower bound; revise after first complete partition"
                ),
                known_limitations=tuple(dict.fromkeys(limitations)),
            )
        )
    return DownloadPlan(
        generated_at=datetime.now(UTC).isoformat(),
        data_dir=str(root),
        artifact_dir=str(ARTIFACT_DIR),
        start_date=start,
        end_date=end,
        worker_count=workers,
        rate_limit=float(rate_limit),
        soft_free_space=int(soft_free_space),
        hard_free_space=int(hard_free_space),
        checkpoint_namespace=checkpoint_namespace,
        datasets=tuple(entries),
    )


def plan_dict(plan: DownloadPlan) -> dict[str, Any]:
    return {
        "generated_at": plan.generated_at,
        "data_dir": plan.data_dir,
        "artifact_dir": plan.artifact_dir,
        "start_date": plan.start_date,
        "end_date": plan.end_date,
        "worker_count": plan.worker_count,
        "rate_limit": plan.rate_limit,
        "soft_free_space_bytes": plan.soft_free_space,
        "hard_free_space_bytes": plan.hard_free_space,
        "checkpoint_namespace": plan.checkpoint_namespace,
        "datasets": [asdict(value) for value in plan.datasets],
    }


def write_download_plan(
    plan: DownloadPlan, directory: str | Path = ARTIFACT_DIR
) -> tuple[Path, Path]:
    root = Path(directory)
    json_path = root / "download-plan.json"
    md_path = root / "download-plan.md"
    _atomic_json_write(json_path, plan_dict(plan))
    lines = [
        "# Historical RAW download plan",
        "",
        f"- Generated at (UTC): `{plan.generated_at}`",
        f"- Range: `{plan.start_date}..{plan.end_date}`",
        f"- Workers: `{plan.worker_count}`",
        f"- Global rate limit: `{plan.rate_limit:g}/min`",
        f"- Soft guard: `{plan.soft_free_space}` bytes; hard guard: `{plan.hard_free_space}` bytes",
        f"- Checkpoint namespace: `{plan.checkpoint_namespace}`",
        "",
        (
            "| Dataset | API | Priority | Permission | Status | Planned range | "
            "Planned units | Existing | Remaining | Estimated requests | Estimated rows | "
            "Estimated size | Partition | RAW path | PIT |"
        ),
        (
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | "
            "---: | ---: | ---: | --- | --- | --- |"
        ),
    ]
    for value in plan.datasets:
        lines.append(
            "| "
            + " | ".join(
                (
                    value.dataset,
                    value.api,
                    value.priority,
                    value.permission,
                    value.status,
                    value.planned_range,
                    f"{value.planned_units:,}",
                    f"{value.existing_units:,}",
                    f"{value.remaining_units:,}",
                    f"{value.estimated_requests:,}",
                    f"{value.estimated_rows:,}",
                    str(value.estimated_size_bytes),
                    value.partition_strategy,
                    value.raw_path,
                    value.pit_status,
                )
            )
            + " |"
        )
    lines.extend(["", "## Estimation and safety notes", ""])
    lines.append(
        "Estimated rows/bytes are lower-bound planning figures from bounded limit=3 "
        "probes; they are not a storage promise. Actual partition results and disk "
        "guards control execution."
    )
    for value in plan.datasets:
        if value.known_limitations:
            lines.append(f"- `{value.dataset}`: {'; '.join(value.known_limitations)}")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def load_api_inventory(path: str | Path) -> ApiInventory:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return ApiInventory(
        generated_at=str(value["generated_at"]),
        data_dir=str(value["data_dir"]),
        provider=str(value.get("provider", "tushare")),
        endpoint_kind=str(value.get("endpoint_kind", "unknown")),
        token_configured=bool(value.get("token_configured")),
        deadline=value.get("deadline"),
        catalog_version=str(value.get("catalog_version", "unknown")),
        apis=tuple(ApiInventoryResult(**item) for item in value.get("apis", [])),
    )


def load_download_plan(path: str | Path) -> DownloadPlan:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return DownloadPlan(
        generated_at=str(value["generated_at"]),
        data_dir=str(value["data_dir"]),
        artifact_dir=str(value.get("artifact_dir", ARTIFACT_DIR)),
        start_date=str(value["start_date"]),
        end_date=str(value["end_date"]),
        worker_count=int(value["worker_count"]),
        rate_limit=float(value["rate_limit"]),
        soft_free_space=int(value["soft_free_space_bytes"]),
        hard_free_space=int(value["hard_free_space_bytes"]),
        checkpoint_namespace=str(value["checkpoint_namespace"]),
        datasets=tuple(PlanEntry(**item) for item in value.get("datasets", [])),
    )


# ---------------------------------------------------------------------------
# Fetch, commit, resume, and scheduling
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    unit: HarvestUnit
    status: str
    frames: tuple[pd.DataFrame, ...] = ()
    pages: tuple[PageAudit, ...] = ()
    schema_hashes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None
    started_at: str = ""
    request_count: int = 0


def _fetch_unit(
    provider: TushareProvider,
    data_dir: Path,
    unit: HarvestUnit,
    *,
    page_size: int,
    max_pages: int,
    rate_limiter: RateLimiter,
) -> _FetchOutcome:
    started = datetime.now(UTC).isoformat()
    frames: list[pd.DataFrame] = []
    pages: list[PageAudit] = []
    hashes: list[str] = []
    warnings: list[str] = []
    requests_before = rate_limiter.current_thread_request_count
    subqueries = _subqueries(data_dir, _SPEC_BY_DATASET[unit.dataset], unit.unit, unit.query)
    try:
        for subquery in subqueries:
            fetched = fetch_paginated_audited(
                provider,
                unit.api,
                subquery,
                page_size=page_size,
                max_pages=max_pages,
                rate_limiter=rate_limiter,
            )
            pages.extend(fetched.pages)
            hashes.extend(fetched.schema_hashes)
            warnings.extend(fetched.warnings)
            if fetched.status == "EMPTY":
                warnings.append("empty_subquery")
                continue
            if fetched.status != "PASS":
                return _FetchOutcome(
                    unit,
                    "PARTIAL",
                    tuple(frames),
                    tuple(pages),
                    tuple(dict.fromkeys(hashes)),
                    tuple(dict.fromkeys(warnings)),
                    "pagination did not prove complete for every subquery",
                    started,
                    max(0, rate_limiter.current_thread_request_count - requests_before),
                )
            frames.append(fetched.frame)
    except ProviderError as exc:
        return _FetchOutcome(
            unit,
            "FAILED",
            tuple(frames),
            tuple(pages),
            tuple(dict.fromkeys(hashes)),
            tuple(dict.fromkeys(warnings)),
            f"{exc.error_type}: {exc.error_message}",
            started,
            max(0, rate_limiter.current_thread_request_count - requests_before),
        )
    except PaginationError as exc:
        partial = exc.partial
        pages.extend(partial.pages)
        hashes.extend(partial.schema_hashes)
        warnings.extend(partial.warnings)
        return _FetchOutcome(
            unit,
            "PARTIAL",
            tuple(frames),
            tuple(pages),
            tuple(dict.fromkeys(hashes)),
            tuple(dict.fromkeys(warnings)),
            str(exc),
            started,
            max(0, rate_limiter.current_thread_request_count - requests_before),
        )
    if not frames:
        return _FetchOutcome(
            unit,
            "EMPTY",
            (),
            tuple(pages),
            tuple(dict.fromkeys(hashes)),
            tuple(dict.fromkeys(warnings)),
            "all subqueries returned zero rows; completeness is unknown",
            started,
            max(0, rate_limiter.current_thread_request_count - requests_before),
        )
    return _FetchOutcome(
        unit,
        "PASS",
        tuple(frames),
        tuple(pages),
        tuple(dict.fromkeys(hashes)),
        tuple(dict.fromkeys(warnings)),
        None,
        started,
        max(0, rate_limiter.current_thread_request_count - requests_before),
    )


def _dynamic_spec(spec: HarvestSpec) -> DatasetSpec:
    return DatasetSpec(
        name=spec.dataset,
        api_name=spec.api,
        primary_keys=spec.primary_keys,
        partition_strategy="none",
        date_fields=spec.date_fields,
        pit_fields=(),
        required_fields=spec.required_fields,
    )


def _result_from_checkpoint(
    record: Mapping[str, Any], *, skipped: bool = False
) -> HarvestUnitResult:
    return HarvestUnitResult(
        dataset=str(record.get("dataset", "")),
        api=str(record.get("api", "")),
        unit=str(record.get("unit", "")),
        partition=str(record.get("partition", "")),
        status="SKIPPED_EXISTING_COMPLETE" if skipped else str(record.get("status", "PARTIAL")),
        rows=int(record.get("rows", 0)),
        files=int(record.get("files", 0)),
        size_bytes=int(record.get("size_bytes", 0)),
        page_count=int(record.get("page_count", 0)),
        request_count=int(record.get("request_count", 0)),
        date_min=record.get("date_min"),
        date_max=record.get("date_max"),
        warnings=tuple(record.get("warnings", ())),
        error=record.get("error"),
        stored_paths=tuple(record.get("stored_paths", ())),
        skipped=skipped,
    )


def _commit_outcome(
    outcome: _FetchOutcome,
    spec: HarvestSpec,
    store: RawParquetStore,
    checkpoints: HarvestCheckpointStore,
) -> HarvestUnitResult:
    unit = outcome.unit
    frame = (
        pd.concat(outcome.frames, ignore_index=True, sort=False)
        if outcome.frames
        else pd.DataFrame()
    )
    warnings = list(outcome.warnings)
    if outcome.status == "FAILED":
        checkpoint = HarvestCheckpoint(
            dataset=unit.dataset,
            storage_dataset=unit.storage_dataset,
            api=unit.api,
            unit=unit.unit,
            query=unit.query,
            partition=unit.partition,
            started_at=outcome.started_at,
            finished_at=datetime.now(UTC).isoformat(),
            status="FAILED",
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            warnings=tuple(dict.fromkeys(warnings)),
            error=outcome.error,
        )
        checkpoints.append(checkpoint)
        return HarvestUnitResult(
            unit.dataset,
            unit.api,
            unit.unit,
            unit.partition,
            "FAILED",
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            warnings=tuple(dict.fromkeys(warnings)),
            error=outcome.error,
        )
    if outcome.status == "EMPTY":
        warnings.append("empty_response")
        checkpoint = HarvestCheckpoint(
            dataset=unit.dataset,
            storage_dataset=unit.storage_dataset,
            api=unit.api,
            unit=unit.unit,
            query=unit.query,
            partition=unit.partition,
            started_at=outcome.started_at,
            finished_at=datetime.now(UTC).isoformat(),
            status="PARTIAL",
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            warnings=tuple(dict.fromkeys(warnings)),
            error=outcome.error or "empty response is not treated as complete",
        )
        checkpoints.append(checkpoint)
        return HarvestUnitResult(
            unit.dataset,
            unit.api,
            unit.unit,
            unit.partition,
            "PARTIAL",
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            warnings=tuple(dict.fromkeys(warnings)),
            error=checkpoint.error,
        )
    if outcome.status != "PASS":
        checkpoint = HarvestCheckpoint(
            dataset=unit.dataset,
            storage_dataset=unit.storage_dataset,
            api=unit.api,
            unit=unit.unit,
            query=unit.query,
            partition=unit.partition,
            started_at=outcome.started_at,
            finished_at=datetime.now(UTC).isoformat(),
            status="PARTIAL",
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            rows=len(frame),
            schema_hash=schema_hash(tuple(str(column) for column in frame.columns))
            if not frame.empty
            else None,
            warnings=tuple(dict.fromkeys(warnings)),
            error=outcome.error or "partial fetch",
        )
        checkpoints.append(checkpoint)
        return HarvestUnitResult(
            unit.dataset,
            unit.api,
            unit.unit,
            unit.partition,
            "PARTIAL",
            rows=len(frame),
            page_count=len(outcome.pages),
            request_count=outcome.request_count,
            warnings=tuple(dict.fromkeys(warnings)),
            error=checkpoint.error,
        )

    quality = check_frame_quality(unit.dataset, frame, _dynamic_spec(spec))
    warnings.extend(quality.warnings)
    if len(outcome.schema_hashes) > 1:
        warnings.append("schema_drift")
    status = "PASS"
    if quality.missing_required or len(outcome.schema_hashes) > 1:
        status = "PARTIAL"
    date_min, date_max = _frame_dates(frame, (*spec.date_fields, *_DATE_COLUMNS))
    stored_paths: tuple[str, ...] = ()
    size_bytes = 0
    files = 0
    error: str | None = None
    try:
        stored = store.write_partition(
            unit.storage_dataset,
            unit.partition,
            frame,
            retrieved_at=datetime.now(UTC).isoformat(),
            source=SOURCE_NAME,
            source_api=unit.api,
        )
        stored_paths = tuple(str(item.path) for item in stored)
        size_bytes = sum(item.size_bytes for item in stored)
        files = len(stored)
    except Exception as exc:  # checkpoint the storage failure, never COMPLETE
        status = "PARTIAL"
        error = f"storage: {type(exc).__name__}: {exc}"
        warnings.append("storage_failed")

    checkpoint = HarvestCheckpoint(
        dataset=unit.dataset,
        storage_dataset=unit.storage_dataset,
        api=unit.api,
        unit=unit.unit,
        query=unit.query,
        partition=unit.partition,
        started_at=outcome.started_at,
        finished_at=datetime.now(UTC).isoformat(),
        status=status,
        page_count=len(outcome.pages),
        request_count=outcome.request_count,
        rows=len(frame),
        files=files,
        size_bytes=size_bytes,
        schema_hash=schema_hash(tuple(str(column) for column in frame.columns))
        if not frame.empty
        else None,
        schema_hashes=tuple(outcome.schema_hashes),
        date_min=date_min,
        date_max=date_max,
        stored_paths=stored_paths,
        warnings=tuple(dict.fromkeys(warnings)),
        error=error,
    )
    checkpoints.append(checkpoint)
    return HarvestUnitResult(
        unit.dataset,
        unit.api,
        unit.unit,
        unit.partition,
        status,
        rows=len(frame),
        files=files,
        size_bytes=size_bytes,
        page_count=len(outcome.pages),
        request_count=outcome.request_count,
        date_min=date_min,
        date_max=date_max,
        warnings=tuple(dict.fromkeys(warnings)),
        error=error,
        stored_paths=stored_paths,
    )


def _stopped_result(unit: HarvestUnit, reason: str) -> HarvestUnitResult:
    return HarvestUnitResult(
        unit.dataset,
        unit.api,
        unit.unit,
        unit.partition,
        "STOPPED",
        error=reason,
    )


# populated once after catalog declaration; keeping specs immutable makes task
# workers independent of mutable CLI state.
_SPEC_BY_DATASET: dict[str, HarvestSpec] = catalog_by_dataset()


_DATASET_ORDER = {
    # P0-A / P0-B safety and expectation data.
    "report_rc": 0,
    "stk_surv": 1,
    "broker_recommend": 2,
    "cyq_perf": 3,
    "adj_factor": 10,
    "stock_st": 11,
    "bak_basic": 12,
    "namechange": 13,
    "new_share": 14,
    "st": 15,
    "stk_limit": 16,
    # P1 ownership and flow before index/alternative and event families.
    "share_float": 20,
    "stk_holdernumber": 21,
    "top10_holders": 22,
    "stk_holdertrade": 23,
    "pledge_detail": 24,
    "pledge_stat": 25,
    "repurchase": 26,
    "moneyflow": 30,
    "moneyflow_dc": 31,
    "margin": 32,
    "margin_detail": 33,
    "margin_secs": 34,
    "hk_hold": 35,
    "index_member_all": 40,
    "index_member": 41,
    "index_weight": 42,
    "index_daily_benchmarks": 43,
    "sw_daily": 44,
    "ci_daily": 45,
    "ths_daily": 46,
    "dc_daily": 47,
    # Slow/sparse event and cross-market families last in P1/P2.
    "ggt_daily": 60,
    "ggt_top10": 61,
    "stk_auction_c": 62,
    "top_list": 63,
    "top_inst": 64,
    "limit_list_d": 65,
    "limit_list_ths": 66,
    "limit_list": 67,
}


def _task_sort_key(unit: HarvestUnit) -> tuple[int, int, int, str, str]:
    # Heavy queue is always last, even when its logical priority is P0-A.
    queue = 4 if unit.heavy else _PRIORITY_RANK[unit.priority]
    dataset_order = _DATASET_ORDER.get(unit.dataset, 100)
    return queue, _PRIORITY_RANK[unit.priority], dataset_order, unit.dataset, unit.unit


def run_harvest(
    provider: TushareProvider,
    plan: DownloadPlan,
    *,
    inventory: ApiInventory | None = None,
    page_size: int = 5000,
    max_pages: int = 500,
    workers: int | None = None,
    rate_limiter: RateLimiter | None = None,
    checkpoint_path: str | Path | None = None,
    soft_free_space: int | None = None,
    hard_free_space: int | None = None,
    deadline: DeadlineGuard | None = None,
    progress: Callable[[str], None] | None = None,
) -> HarvestRunSummary:
    """Run all eligible plan units with bounded workers and coordinator writes."""

    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    worker_count = workers or plan.worker_count
    if worker_count <= 0:
        raise ValueError("workers must be positive")
    root = Path(plan.data_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    selected = {entry.dataset: entry for entry in plan.datasets}
    inv = _inventory_by_dataset(inventory)
    cp_path = Path(checkpoint_path or (root / CHECKPOINT_FILENAME))
    checkpoints = HarvestCheckpointStore(cp_path)
    guard = DiskGuard(
        root,
        soft_free_bytes=plan.soft_free_space if soft_free_space is None else soft_free_space,
        hard_free_bytes=plan.hard_free_space if hard_free_space is None else hard_free_space,
    )
    deadline_guard = deadline or DeadlineGuard()
    limiter = (
        rate_limiter or getattr(provider, "rate_limiter", None) or RateLimiter(plan.rate_limit)
    )
    provider.set_rate_limiter(limiter)
    limiter_before = limiter.request_count
    store = RawParquetStore(root)
    tasks: list[HarvestUnit] = []
    result_values: list[HarvestUnitResult] = []
    started_datasets = checkpoints.started_datasets()
    queued: dict[int, list[tuple[int, str, list[HarvestUnit]]]] = {}

    ordered_specs = sorted(
        HARVEST_SPECS,
        key=lambda value: (
            4 if value.heavy else _PRIORITY_RANK[value.priority],
            _PRIORITY_RANK[value.priority],
            _DATASET_ORDER.get(value.dataset, 100),
            value.dataset,
        ),
    )
    latest_checkpoints = checkpoints.latest()
    for spec in ordered_specs:
        entry = selected.get(spec.dataset)
        if entry is None or entry.status not in {
            "READY",
            "NOT_INVENTORIED",
            "CURRENT_ONLY",
        }:
            continue
        inventory_value = inv.get(spec.dataset)
        if inventory_value is not None and inventory_value.permission_ok is not True:
            continue
        unit_start = max(spec.start_date, plan.start_date)
        units = _units_for_spec(root, spec, unit_start, plan.end_date)
        completed = checkpoints.completed_units(spec.dataset, spec.api, spec.storage_name)
        pending_units: list[HarvestUnit] = []
        for unit_name in units:
            unit = HarvestUnit(
                dataset=spec.dataset,
                api=spec.api,
                unit=unit_name,
                query=_query_for_unit(root, spec, unit_name, end_date=plan.end_date),
                partition=_partition_for_unit(spec, unit_name),
                storage_dataset=spec.storage_name,
                priority=spec.priority,
                heavy=spec.heavy,
            )
            if unit_name in completed:
                record = latest_checkpoints.get((spec.dataset, spec.api, unit_name))
                if record is not None:
                    result_values.append(_result_from_checkpoint(record, skipped=True))
                continue
            pending_units.append(unit)
        if pending_units:
            queue = 4 if spec.heavy else _PRIORITY_RANK[spec.priority]
            queued.setdefault(queue, []).append(
                (_DATASET_ORDER.get(spec.dataset, 100), spec.dataset, pending_units)
            )

    # Fairness within a queue prevents one slow history (for example
    # share_float's high-volume announcement days) from blocking other P1
    # ownership/flow/index datasets.  Heavy work remains an independent final
    # queue and never competes with P0/P1.
    for queue in sorted(queued):
        groups = sorted(queued[queue], key=lambda value: (value[0], value[1]))
        while any(units for _, _, units in groups):
            for _, _, units in groups:
                if units:
                    tasks.append(units.pop(0))
    run_started = time.monotonic()
    emit = progress or (lambda message: LOGGER.info(message))

    def report_result(result: HarvestUnitResult) -> None:
        result_values.append(result)
        failures = sum(
            item.status not in {"PASS", "SKIPPED_EXISTING_COMPLETE"} for item in result_values
        )
        emit(
            "harvest "
            f"dataset={result.dataset} unit={result.unit} status={result.status} "
            f"rows={result.rows} files={result.files} size={result.size_bytes} "
            f"requests={result.request_count} failures={failures}"
            + (f" error={result.error}" if result.error else "")
        )

    effective_page_size = min(page_size, 1000)

    def execute(unit: HarvestUnit) -> _FetchOutcome:
        return _fetch_unit(
            provider,
            root,
            unit,
            page_size=effective_page_size,
            max_pages=max_pages,
            rate_limiter=limiter,
        )

    if worker_count == 1:
        for unit in tasks:
            spec = _SPEC_BY_DATASET[unit.dataset]
            allowed_disk, disk_reason = guard.allows(spec)
            allowed_deadline, deadline_reason = deadline_guard.allows(
                spec, dataset_started=unit.dataset in started_datasets
            )
            if not allowed_disk or not allowed_deadline:
                report_result(_stopped_result(unit, f"{disk_reason}; {deadline_reason}"))
                continue
            outcome = execute(unit)
            result = _commit_outcome(outcome, spec, store, checkpoints)
            started_datasets.add(unit.dataset)
            report_result(result)
    else:
        pending: dict[Future[_FetchOutcome], HarvestUnit] = {}
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="raw-harvest")
        next_position = 0

        def submit_available() -> None:
            nonlocal next_position
            while next_position < len(tasks) and len(pending) < worker_count:
                unit = tasks[next_position]
                spec = _SPEC_BY_DATASET[unit.dataset]
                allowed_disk, disk_reason = guard.allows(spec)
                allowed_deadline, deadline_reason = deadline_guard.allows(
                    spec, dataset_started=unit.dataset in started_datasets
                )
                if not allowed_disk or not allowed_deadline:
                    report_result(_stopped_result(unit, f"{disk_reason}; {deadline_reason}"))
                    next_position += 1
                    continue
                next_position += 1
                pending[executor.submit(execute, unit)] = unit
                started_datasets.add(unit.dataset)

        try:
            submit_available()
            while pending:
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    unit = pending.pop(future)
                    spec = _SPEC_BY_DATASET[unit.dataset]
                    try:
                        outcome = future.result()
                    except Exception as exc:  # isolate one unit and keep queue moving
                        result = _stopped_result(unit, f"worker {type(exc).__name__}: {exc}")
                    else:
                        result = _commit_outcome(outcome, spec, store, checkpoints)
                    report_result(result)
                submit_available()
        except KeyboardInterrupt:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            emit("harvest interrupted; completed atomic partitions/checkpoints were preserved")
            raise
        else:
            executor.shutdown(wait=True)

    return HarvestRunSummary(
        generated_at=datetime.now(UTC).isoformat(),
        results=tuple(result_values),
        workers=worker_count,
        rate_limit=limiter.requests_per_minute,
        api_requests=max(0, limiter.request_count - limiter_before),
        elapsed_seconds=time.monotonic() - run_started,
        soft_guard_bytes=guard.soft_free_bytes,
        hard_guard_bytes=guard.hard_free_bytes,
    )


def _sanitize_failure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_failure(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_failure(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _checkpoint_failures(checkpoint_path: str | Path) -> list[Any]:
    return [
        _sanitize_failure(record)
        for record in HarvestCheckpointStore(checkpoint_path).records()
        if str(record.get("status", "")).upper() != "PASS"
    ]


def write_checkpoint_failures(
    checkpoint_path: str | Path,
    path: str | Path = ARTIFACT_DIR / "failures.json",
) -> Path:
    """Write a failure artifact when a runner was interrupted before finalization."""

    failures = _checkpoint_failures(checkpoint_path)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary_failures": len(failures),
        "failures": failures,
    }
    destination = Path(path)
    _atomic_json_write(destination, payload)
    return destination


def write_failures(
    summary: HarvestRunSummary,
    checkpoint_path: str | Path,
    path: str | Path = ARTIFACT_DIR / "failures.json",
) -> Path:
    failures = _checkpoint_failures(checkpoint_path)
    failures.extend(
        _sanitize_failure(asdict(result))
        for result in summary.failures
        if result.status == "STOPPED"
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary_failures": len(summary.failures),
        "failures": failures,
    }
    destination = Path(path)
    _atomic_json_write(destination, payload)
    return destination


# ---------------------------------------------------------------------------
# Coverage and RAW integrity audit
# ---------------------------------------------------------------------------


def _spec_for_storage_dataset(storage_dataset: str) -> HarvestSpec | None:
    for spec in HARVEST_SPECS:
        if spec.storage_name == storage_dataset:
            return spec
    return None


def _coverage_status(
    spec: HarvestSpec,
    local: LocalDatasetState,
    units: tuple[str, ...],
    latest: Mapping[tuple[str, str, str], Mapping[str, Any]],
    inventory_value: ApiInventoryResult | None,
) -> tuple[str, tuple[str, ...]]:
    limitations: list[str] = []
    if local.status == "SKIP_EXISTING_COMPLETE":
        return "SKIPPED_EXISTING_COMPLETE", tuple(limitations)
    if spec.current_only:
        return "CURRENT_ONLY", tuple(limitations)
    if inventory_value is not None:
        if inventory_value.result == "PERMISSION_DENIED":
            return "UNSUPPORTED", ("permission denied",)
        if inventory_value.result == "UNSUPPORTED":
            return "UNSUPPORTED", (inventory_value.reason,)
    statuses = [
        str(latest.get((spec.dataset, spec.api, unit), {}).get("status", "")) for unit in units
    ]
    passes = sum(status == "PASS" for status in statuses)
    partial = sum(status in {"PARTIAL", "FAILED", "STOPPED"} for status in statuses)
    if units and passes == len(units):
        status = "COMPLETE"
    elif passes:
        status = "PARTIAL"
    elif partial:
        status = "FAILED" if all(status == "FAILED" for status in statuses if status) else "PARTIAL"
    elif inventory_value and inventory_value.probe_status in {"PASS", "EMPTY"}:
        status = "AVAILABLE_NOT_ARCHIVED"
    else:
        status = "UNKNOWN"
    if inventory_value and inventory_value.probe_status == "EMPTY":
        limitations.append("probe_empty")
    if not units:
        limitations.append("no_historical_units")
    return status, tuple(limitations)


def build_coverage(
    data_dir: str | Path,
    *,
    inventory: ApiInventory | None = None,
    plan: DownloadPlan | None = None,
    checkpoint_path: str | Path | None = None,
    specs: Sequence[HarvestSpec] = HARVEST_SPECS,
) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    cp = HarvestCheckpointStore(checkpoint_path or (root / CHECKPOINT_FILENAME))
    latest = cp.latest()
    inv = _inventory_by_dataset(inventory)
    end = plan.end_date if plan else date.today().strftime("%Y%m%d")
    values: list[dict[str, Any]] = []
    for spec in specs:
        local = local_dataset_state(root, spec, read_values=True)
        unit_start = max(spec.start_date, plan.start_date if plan else DEFAULT_START_DATE)
        units = _units_for_spec(root, spec, unit_start, end)
        spec_latest = {
            key: record
            for key, record in latest.items()
            if key[0] == spec.dataset
            and key[1] == spec.api
            and record.get("storage_dataset") == spec.storage_name
        }
        status, limitations = _coverage_status(
            spec, local, units, spec_latest, inv.get(spec.dataset)
        )
        records = [
            spec_latest.get((spec.dataset, spec.api, unit), {})
            for unit in units
            if spec_latest.get((spec.dataset, spec.api, unit))
        ]
        pass_records = [record for record in records if str(record.get("status")) == "PASS"]
        warnings = list(local.warnings)
        if any(str(record.get("status")) != "PASS" for record in records):
            warnings.append("incomplete_or_failed_units")
        values.append(
            {
                "dataset": spec.dataset,
                "provider": "tushare",
                "api": spec.api,
                "permission": inv[spec.dataset].permission if spec.dataset in inv else "UNKNOWN",
                "category": spec.category,
                "priority": spec.priority,
                "raw_path": f"data/raw/{spec.storage_name}",
                "partition_strategy": spec.partition_strategy,
                "date_min": local.date_min
                or (
                    min(
                        (
                            record.get("date_min")
                            for record in pass_records
                            if record.get("date_min")
                        ),
                        default=None,
                    )
                ),
                "date_max": local.date_max
                or (
                    max(
                        (
                            record.get("date_max")
                            for record in pass_records
                            if record.get("date_max")
                        ),
                        default=None,
                    )
                ),
                "files": local.files,
                "rows": local.rows,
                "size_bytes": local.size_bytes,
                "requests": sum(int(record.get("request_count", 0)) for record in records),
                "planned_units": len(units),
                "completed_units": len(pass_records),
                "missing_units": [
                    unit
                    for unit in units
                    if str(spec_latest.get((spec.dataset, spec.api, unit), {}).get("status", ""))
                    != "PASS"
                ],
                "checkpoint": str(checkpoint_path or (root / CHECKPOINT_FILENAME)),
                "raw_status": status,
                "pit_status": spec.pit_status,
                "known_limitations": list(
                    dict.fromkeys(item for item in (*limitations, spec.notes) if item)
                ),
                "warnings": list(dict.fromkeys(warnings)),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_dir": str(root),
        "deadline_mode": DeadlineGuard().mode,
        "datasets": values,
    }


def write_coverage_artifacts(
    coverage: Mapping[str, Any],
    directory: str | Path = ARTIFACT_DIR,
) -> tuple[Path, Path]:
    root = Path(directory)
    json_path = root / "coverage.json"
    md_path = root / "coverage.md"
    _atomic_json_write(json_path, coverage)
    lines = [
        "# RAW archive coverage",
        "",
        f"- Generated at (UTC): `{coverage.get('generated_at')}`",
        f"- Deadline mode at audit: `{coverage.get('deadline_mode')}`",
        "",
        (
            "| Dataset | API | Priority | Raw status | PIT status | Range | Rows | Files | "
            "Size bytes | Requests | Completed/planned | Missing units | Raw path |"
        ),
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for value in coverage.get("datasets", []):
        lines.append(
            "| "
            + " | ".join(
                (
                    str(value["dataset"]),
                    str(value["api"]),
                    str(value["priority"]),
                    str(value["raw_status"]),
                    str(value["pit_status"]),
                    f"{value.get('date_min') or '-'}..{value.get('date_max') or '-'}",
                    f"{value.get('rows', 0):,}",
                    str(value.get("files", 0)),
                    str(value.get("size_bytes", 0)),
                    str(value.get("requests", 0)),
                    f"{value.get('completed_units', 0)}/{value.get('planned_units', 0)}",
                    str(len(value.get("missing_units", []))),
                    str(value["raw_path"]),
                )
            )
            + " |"
        )
    lines.extend(["", "## Limitations and gaps", ""])
    for value in coverage.get("datasets", []):
        limitations = [*value.get("known_limitations", []), *value.get("warnings", [])]
        if limitations:
            lines.append(
                f"- `{value['dataset']}`: {'; '.join(dict.fromkeys(map(str, limitations)))}"
            )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return json_path, md_path


def _raw_files(data_dir: Path) -> list[Path]:
    raw = data_dir / "raw"
    return sorted(path for path in raw.rglob("*.parquet") if path.is_file()) if raw.exists() else []


def build_raw_integrity(
    data_dir: str | Path,
    *,
    checkpoint_path: str | Path | None = None,
    specs: Sequence[HarvestSpec] = HARVEST_SPECS,
) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    raw = root / "raw"
    files = _raw_files(root)
    zero_byte: list[str] = []
    unreadable: list[str] = []
    temporary: list[str] = []
    schema_by_dataset: dict[str, set[str]] = {}
    rows_by_dataset: Counter[str] = Counter()
    bytes_by_dataset: Counter[str] = Counter()
    duplicate_identity: list[dict[str, Any]] = []
    suspicious_small: list[dict[str, Any]] = []
    spec_by_storage = {spec.storage_name: spec for spec in specs}

    if raw.exists():
        temporary = [
            str(path)
            for path in raw.rglob("*")
            if path.is_file() and (path.name.endswith(".tmp") or ".tmp" in path.name)
        ]
    for path in files:
        relative = path.relative_to(raw)
        storage_dataset = relative.parts[0] if relative.parts else ""
        spec = spec_by_storage.get(storage_dataset)
        if path.stat().st_size == 0:
            zero_byte.append(str(path))
            continue
        try:
            parquet = pq.ParquetFile(path)
            row_count = int(parquet.metadata.num_rows)
            columns = tuple(str(column) for column in parquet.schema_arrow.names)
            dataset_schemas = schema_by_dataset.setdefault(storage_dataset, set())
            dataset_schemas.add(schema_hash(columns))
            rows_by_dataset[storage_dataset] += row_count
            bytes_by_dataset[storage_dataset] += path.stat().st_size
            if row_count < 50 and not (spec and spec.allow_tiny):
                suspicious_small.append(
                    {"path": str(path), "rows": row_count, "reason": "below 50 rows"}
                )
            if spec and spec.primary_keys and set(spec.primary_keys).issubset(columns):
                frame = pd.read_parquet(path, columns=list(spec.primary_keys))
                duplicate_count = int(frame.duplicated(list(spec.primary_keys), keep=False).sum())
                if duplicate_count:
                    duplicate_identity.append(
                        {
                            "path": str(path),
                            "dataset": spec.dataset,
                            "storage_dataset": storage_dataset,
                            "duplicate_identity_rows": duplicate_count,
                        }
                    )
        except Exception as exc:  # pragma: no cover - parquet engine detail
            unreadable.append(f"{path}: {type(exc).__name__}: {exc}")

    checkpoint_file = Path(checkpoint_path or (root / CHECKPOINT_FILENAME))
    checkpoint_records: list[dict[str, Any]] = []
    if checkpoint_file.exists():
        try:
            value = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            if isinstance(value, list):
                checkpoint_records = [record for record in value if isinstance(record, dict)]
        except (OSError, ValueError) as exc:
            unreadable.append(f"checkpoint:{checkpoint_file}: {type(exc).__name__}: {exc}")
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in checkpoint_records:
        key = (
            str(record.get("dataset", "")),
            str(record.get("api", "")),
            str(record.get("unit", "")),
        )
        if all(key):
            latest[key] = record
    checkpoint_path_mismatch: list[dict[str, Any]] = []
    checkpoint_row_mismatch: list[dict[str, Any]] = []
    duplicate_checkpoint_identity: list[dict[str, Any]] = []
    seen_checkpoint_paths: dict[str, tuple[str, str, str]] = {}
    for key, record in latest.items():
        if str(record.get("status", "")).upper() != "PASS":
            continue
        stored_paths = tuple(record.get("stored_paths", ()))
        if not stored_paths:
            checkpoint_path_mismatch.append({"key": key, "reason": "PASS without stored_paths"})
            continue
        for raw_path in stored_paths:
            path = Path(raw_path)
            if not path.is_absolute():
                # RawParquetStore preserves the caller's spelling.  A CLI
                # invoked from the repository root therefore records
                # ``data/raw/...``, while an absolute data_dir records an
                # absolute path.  Reconcile both forms without treating the
                # data-dir prefix as a second ``data/`` component.
                candidates = (root / path, root.parent / path)
                path = next(
                    (candidate for candidate in candidates if candidate.is_file()),
                    candidates[0],
                )
            path_key = str(path.resolve())
            previous = seen_checkpoint_paths.get(path_key)
            if previous and previous != key:
                duplicate_checkpoint_identity.append(
                    {"path": str(path), "first": previous, "second": key}
                )
            seen_checkpoint_paths[path_key] = key
            if not path.is_file():
                checkpoint_path_mismatch.append(
                    {"key": key, "path": str(path), "reason": "missing"}
                )
                continue
            try:
                actual_rows = int(pq.ParquetFile(path).metadata.num_rows)
            except Exception as exc:
                checkpoint_path_mismatch.append({"key": key, "path": str(path), "reason": str(exc)})
                continue
            expected_rows = int(record.get("rows", 0))
            if actual_rows != expected_rows:
                checkpoint_row_mismatch.append(
                    {
                        "key": key,
                        "path": str(path),
                        "checkpoint_rows": expected_rows,
                        "actual_rows": actual_rows,
                    }
                )

    schema_drift = {
        dataset: sorted(values) for dataset, values in schema_by_dataset.items() if len(values) > 1
    }
    hard_errors = (
        zero_byte
        or temporary
        or unreadable
        or checkpoint_path_mismatch
        or checkpoint_row_mismatch
        or duplicate_checkpoint_identity
    )
    warnings = duplicate_identity or suspicious_small or list(schema_drift)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_dir": str(root),
        "raw_path": str(raw),
        "status": "PASS" if not hard_errors else "FAIL",
        "files": len(files),
        "rows": sum(rows_by_dataset.values()),
        "size_bytes": sum(bytes_by_dataset.values()),
        "zero_byte_files": zero_byte,
        "temporary_files": temporary,
        "unreadable_parquet": unreadable,
        "schema_drift": schema_drift,
        "duplicate_identity_rows": duplicate_identity,
        "duplicate_checkpoint_identity": duplicate_checkpoint_identity,
        "checkpoint_path_mismatch": checkpoint_path_mismatch,
        "checkpoint_row_count_mismatch": checkpoint_row_mismatch,
        "suspicious_small_partitions": suspicious_small,
        "warnings_count": len(warnings),
        "dataset_totals": {
            dataset: {
                "rows": rows_by_dataset[dataset],
                "size_bytes": bytes_by_dataset[dataset],
                "schema_count": len(schema_by_dataset.get(dataset, set())),
            }
            for dataset in sorted(rows_by_dataset)
        },
    }


def write_raw_integrity_artifacts(
    integrity: Mapping[str, Any],
    directory: str | Path = ARTIFACT_DIR,
) -> tuple[Path, Path]:
    root = Path(directory)
    json_path = root / "raw-integrity.json"
    md_path = root / "raw-integrity.md"
    _atomic_json_write(json_path, dict(integrity))
    lines = [
        "# RAW integrity audit",
        "",
        f"- Generated at (UTC): `{integrity.get('generated_at')}`",
        f"- Status: `{integrity.get('status')}`",
        f"- Files: `{integrity.get('files', 0):,}`",
        f"- Rows: `{integrity.get('rows', 0):,}`",
        f"- Size: `{integrity.get('size_bytes', 0):,}` bytes",
        "",
        "| Check | Count | Status |",
        "| --- | ---: | --- |",
    ]
    checks = (
        ("zero-byte files", "zero_byte_files"),
        ("temporary files", "temporary_files"),
        ("unreadable parquet", "unreadable_parquet"),
        ("schema drift datasets", "schema_drift"),
        ("duplicate identity rows", "duplicate_identity_rows"),
        ("duplicate checkpoint paths", "duplicate_checkpoint_identity"),
        ("checkpoint/path mismatch", "checkpoint_path_mismatch"),
        ("checkpoint/row-count mismatch", "checkpoint_row_count_mismatch"),
        ("suspicious small partitions", "suspicious_small_partitions"),
    )
    for label, key in checks:
        value = integrity.get(key, {})
        count = len(value) if hasattr(value, "__len__") else 0
        lines.append(f"| {label} | {count} | {'PASS' if count == 0 else 'WARN/FAIL'} |")
    lines.extend(["", "## Details", ""])
    for label, key in checks:
        value = integrity.get(key)
        if value:
            lines.append(f"### {label}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(value, ensure_ascii=False, indent=2, default=str))
            lines.append("```")
            lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return json_path, md_path


def render_final_report(
    *,
    baseline: Mapping[str, Any],
    inventory: ApiInventory | None,
    plan: DownloadPlan | None,
    summary: HarvestRunSummary | None,
    coverage: Mapping[str, Any],
    integrity: Mapping[str, Any],
    disk_before: Mapping[str, Any] | None = None,
    disk_after: Mapping[str, Any] | None = None,
    tests: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        "# A-share historical RAW cold archive final report",
        "",
        "> This run archives RAW only. `RAW_ARCHIVED` is not `PIT_VALIDATED` "
        "and is not `FEATURE_APPROVED`.",
        "",
        "## Baseline",
        "",
    ]
    for key, value in baseline.items():
        lines.append(f"- {key}: `{value}`")
    if disk_before:
        lines.append(f"- disk before: `{disk_before}`")
    if disk_after:
        lines.append(f"- disk after: `{disk_after}`")
    lines.extend(["", "## API inventory", ""])
    if inventory:
        lines.extend(
            [
                "| API | Permission | Category | Priority | Result | Probe |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for value in inventory.apis:
            lines.append(
                f"| {value.api_name} | {value.permission} | {value.category} | "
                f"{value.priority} | {value.result} | {value.probe_status} |"
            )
    else:
        lines.append("Inventory not loaded.")
    lines.extend(
        [
            "",
            "## Archived datasets",
            "",
            "| Dataset | Range | Rows | Files | Size | RAW status | PIT status |",
            "| --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for value in coverage.get("datasets", []):
        if value.get("raw_status") in {"COMPLETE", "PARTIAL", "FAILED", "AVAILABLE_NOT_ARCHIVED"}:
            lines.append(
                f"| {value['dataset']} | {value.get('date_min') or '-'}.."
                f"{value.get('date_max') or '-'} | {value.get('rows', 0):,} | "
                f"{value.get('files', 0)} | {value.get('size_bytes', 0):,} | "
                f"{value.get('raw_status')} | {value.get('pit_status')} |"
            )
    lines.extend(["", "## Existing data skipped", ""])
    for value in coverage.get("datasets", []):
        if value.get("raw_status") == "SKIPPED_EXISTING_COMPLETE":
            lines.append(
                f"- `{value['dataset']}`: `SKIP_EXISTING_COMPLETE` ({value.get('raw_path')})"
            )
    lines.extend(["", "## Heavy dataset status", ""])
    for dataset in ("cyq_chips", "stk_factor", "stk_factor_pro"):
        value = next(
            (item for item in coverage.get("datasets", []) if item.get("dataset") == dataset), None
        )
        if value:
            lines.append(
                f"- `{dataset}`: `{value.get('raw_status')}`; "
                f"missing units={len(value.get('missing_units', []))}"
            )
        else:
            lines.append(f"- `{dataset}`: `NOT_IN_CATALOG_AUDIT`")
    lines.extend(["", "## Gap report", ""])
    for value in coverage.get("datasets", []):
        status = value.get("raw_status")
        if status not in {"COMPLETE", "SKIPPED_EXISTING_COMPLETE"}:
            lines.append(
                f"- `{value['dataset']}`: `{status}`; "
                f"missing={len(value.get('missing_units', []))}; "
                f"limitations={'; '.join(value.get('known_limitations', [])) or '-'}"
            )
    lines.extend(
        [
            "",
            "## RAW integrity",
            "",
            f"- status: `{integrity.get('status')}`",
            f"- zero-byte files: `{len(integrity.get('zero_byte_files', []))}`",
            f"- temporary files: `{len(integrity.get('temporary_files', []))}`",
            f"- unreadable Parquet: `{len(integrity.get('unreadable_parquet', []))}`",
            f"- checkpoint/path mismatches: `{len(integrity.get('checkpoint_path_mismatch', []))}`",
            "- checkpoint/row-count mismatches: "
            f"`{len(integrity.get('checkpoint_row_count_mismatch', []))}`",
            "- suspicious small partitions: "
            f"`{len(integrity.get('suspicious_small_partitions', []))}`",
            "",
            "## Capacity",
            "",
            f"- disk before: `{disk_before or {}}`",
            f"- disk after: `{disk_after or {}}`",
            f"- new result bytes recorded by runner: `{summary.size_bytes if summary else 0}`",
            "",
            "## Tests",
            "",
        ]
    )
    if tests:
        for key, value in tests.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- Not recorded by archive runner.")
    if plan:
        lines.extend(
            [
                "",
                "## Scheduler summary",
                "",
                f"- workers: `{plan.worker_count}`",
                f"- rate limit: `{plan.rate_limit:g}/min`",
                f"- range: `{plan.start_date}..{plan.end_date}`",
            ]
        )
    if summary:
        lines.extend(
            [
                f"- requests: `{summary.api_requests}`",
                f"- rows committed: `{summary.rows}`",
                f"- bytes committed: `{summary.size_bytes}`",
                f"- failures: `{len(summary.failures)}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Next step",
            "",
            "> 本轮只完成 RAW 历史数据归档。下一步不要马上接入 Score；"
            "应逐数据集进行 PIT semantics、coverage、feature usefulness validation。",
            "",
        ]
    )
    return "\n".join(lines)


def write_final_report(text: str, path: str | Path = ARTIFACT_DIR / "final-report.md") -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


__all__ = [
    "ARTIFACT_DIR",
    "CHECKPOINT_FILENAME",
    "DEFAULT_START_DATE",
    "HARVEST_SPECS",
    "HARD_FREE_SPACE",
    "SOFT_FREE_SPACE",
    "ApiInventory",
    "ApiInventoryResult",
    "DeadlineGuard",
    "DiskGuard",
    "DownloadPlan",
    "HarvestCheckpointStore",
    "HarvestRunSummary",
    "HarvestSpec",
    "HarvestUnitResult",
    "build_coverage",
    "build_download_plan",
    "build_raw_integrity",
    "local_dataset_state",
    "probe_api_inventory",
    "render_final_report",
    "run_harvest",
    "write_api_inventory",
    "write_checkpoint_failures",
    "write_coverage_artifacts",
    "write_download_plan",
    "write_failures",
    "write_final_report",
    "write_raw_integrity_artifacts",
]
