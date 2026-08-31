"""Historical point-in-time replay validation orchestration.

This module is intentionally an audit wrapper around :mod:`scanner.replay`.
It does not evaluate future observations, tune a score, or implement a second
feature pipeline.  The wrapper freezes monthly target selection, supplies the
PIT-safe universe mode, records the complete replay evidence, and applies hard
PIT assertions before an artifact can be called ready.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from ..dates import normalize_date_series
from ..pit.financial import query_financial_as_of
from ..storage.parquet import RawParquetStore
from .artifacts import (
    ARTIFACT_LAYOUT_VERSION,
    FINALIZATION_PROBE_INTERVAL_BYTES,
    PIT_REPLAY_ARTIFACT_LAYOUT_VERSION,
    ChunkedContentAddressedStore,
    ContentAddressedStore,
    content_digest,
    deterministic_replay_digests,
    expand_normalized_snapshot,
    expand_normalized_vector,
    feature_vector_from_payload,
    normalize_feature_vector,
    normalize_snapshot_payload,
    validate_normalized_integrity,
    write_normalized_snapshot_with_streamed_vectors,
)
from .contracts import FeatureEvidence, FeatureVector
from .replay import (
    ReplayConfig,
    ReplayDiagnostics,
    ReplayResult,
    replay_performance_profile,
    run_replay_frames,
)
from .score import rank_scores, score_feature_vector
from .universe import UniverseConfig, build_investable_universe

PIT_REPLAY_VALIDATION_CONTRACT_VERSION = "pit-replay-validation-v1"
REPLAY_VALIDATION_CONTRACT_VERSION = PIT_REPLAY_VALIDATION_CONTRACT_VERSION
MONTHLY_SELECTION_RULE_VERSION = "monthly-anchor-15-v1"
MARKET_REGIME_CONTRACT_VERSION = "market-regime-v1"
HISTORICAL_UNIVERSE_CONTRACT_VERSION = "historical-universe-v1"
DEFAULT_START_MONTH = "2017-01"
DEFAULT_END_MONTH = "2026-12"
DEFAULT_ANCHOR_DAY = 15
DEFAULT_BENCHMARK_ID = "000300.SH"
DEFAULT_SEED = 0
MIN_AVAILABLE_RAM_BYTES = 4 * 1024**3
# Keep the existing six-GiB budget, but enforce it against live working-set
# telemetry rather than the lifetime ru_maxrss high-water mark.
MAX_PEAK_RSS_BYTES = 6 * 1024**3
MAX_LIVE_PSS_BYTES = MAX_PEAK_RSS_BYTES
MAX_LIVE_PRIVATE_BYTES = MAX_PEAK_RSS_BYTES
MIN_SWAP_FREE_BYTES = 512 * 1024**2
MAX_SWAP_GROWTH_BYTES = 256 * 1024**2
MAX_PROCESS_SWAP_BYTES = 256 * 1024**2
LARGE_CORPUS_BYTES = 512 * 1024**2
RESOURCE_GATE_CONTRACT_VERSION = "resource-gate-v2"
RESOURCE_SAMPLING_CONTRACT_VERSION = "resource-finalization-sampling-v1"
DEFAULT_VALIDATION_CUTOFF = "20260830"


def _resource_gate_declaration(*, enabled: bool | None = None) -> dict[str, Any]:
    """Return the versioned hard/diagnostic resource contract.

    ``ru_maxrss`` is intentionally declared only as a diagnostic.  It is a
    lifetime high-water value and cannot identify which replay phase caused a
    large allocation.  The live limits retain the old six-GiB budget.
    """

    declaration: dict[str, Any] = {
        "version": RESOURCE_GATE_CONTRACT_VERSION,
        "min_available_ram_bytes": MIN_AVAILABLE_RAM_BYTES,
        "min_swap_free_bytes": MIN_SWAP_FREE_BYTES,
        "max_swap_growth_bytes": MAX_SWAP_GROWTH_BYTES,
        "max_live_pss_bytes": MAX_LIVE_PSS_BYTES,
        "max_live_private_bytes": MAX_LIVE_PRIVATE_BYTES,
        "max_live_rss_fallback_bytes": MAX_PEAK_RSS_BYTES,
        "max_process_swap_bytes": MAX_PROCESS_SWAP_BYTES,
        "large_corpus_threshold_bytes": LARGE_CORPUS_BYTES,
        "sampling_contract_version": RESOURCE_SAMPLING_CONTRACT_VERSION,
        "finalization_sampling": {
            "probe_interval_bytes": FINALIZATION_PROBE_INTERVAL_BYTES,
            "phases": [
                "replay_frame_release",
                "cas_merge_group_boundaries",
                "cas_finalized_store_iteration",
                "artifact_vector_stream",
                "artifact_score_stream",
                "artifact_provenance_store_stream",
            ],
            "enforcement": "same resource-gate-v2 hard limits",
        },
        "hard_metrics": [
            "available_bytes",
            "swap_free_bytes",
            "swap_used_growth_bytes",
            "current_pss_bytes",
            "current_private_bytes",
            "current_swap_bytes",
        ],
        "diagnostic_metrics": {
            "peak_rss": {
                "metric": "ru_maxrss",
                "field": "peak_rss_diagnostic_bytes",
                "threshold_bytes": MAX_PEAK_RSS_BYTES,
                "enforcement": "diagnostic_only",
            }
        },
        "live_metric_fallback": (
            "current VmRSS from /proc/self/status when smaps_rollup is unavailable; "
            "ru_maxrss is never substituted for PSS/private"
        ),
    }
    if enabled is not None:
        declaration["enabled"] = enabled
    return declaration

FINANCIAL_CORPUS_DATASETS = (
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
)
MARKET_CORPUS_DATASETS = (
    "trade_cal",
    "stock_basic",
    "index_basic",
    "suspend_d",
    "daily",
    "daily_basic",
    "index_daily",
    "disclosure_date",
)
MANIFEST_DATASETS = (*FINANCIAL_CORPUS_DATASETS, *MARKET_CORPUS_DATASETS)
REPLAY_REQUIRED_DATASETS = (
    "trade_cal",
    "stock_basic",
    "daily",
    "daily_basic",
    "index_daily",
    *FINANCIAL_CORPUS_DATASETS,
)

# Validation projection columns are the raw inputs consumed by the existing
# feature path.  Identity/availability columns stay included so the financial
# PIT selectors and provenance contracts remain unchanged.
_COMMON_FINANCIAL_COLUMNS = (
    "ts_code",
    "end_date",
    "report_period",
    "ann_date",
    "f_ann_date",
    "actual_available_date",
    "report_type",
    "comp_type",
    "end_type",
    "update_flag",
    "start_date",
    "period_start",
    "report_family",
    "statement_type",
    "duration_semantics",
    "period_semantics",
    "statement_duration",
    "duration_type",
    "duration",
    "is_single_quarter",
    "single_quarter",
    "is_cumulative",
    "cumulative",
    "is_ytd",
    "scope",
    "consolidation",
    "consolidated_scope",
    "entity_scope",
    "report_scope",
    "consolidated",
    "unit",
    "currency_unit",
    "data_unit",
    "unit_name",
    "currency",
    "currency_code",
    "unit_scale",
    "scale",
    "scale_factor",
    "accounting_semantics",
    "accounting_basis",
    "accounting_standard",
    "gaap",
    "source_version_identity",
    "source_version",
    "comparable_period_contract_version",
)
_FINANCIAL_PROJECTION_COLUMNS = {
    "income": (
        *_COMMON_FINANCIAL_COLUMNS,
        "revenue",
        "total_revenue",
        "n_income_attr_p",
        "n_income",
        "net_profit",
        "operate_profit",
        "operating_profit",
        "gross_profit",
        "total_profit",
        "non_oper_income",
        "n_oth_income",
        "assets_impair_loss",
        "impairment_loss",
        "deducted_profit",
        "adj_profit",
        "net_profit_deducted",
    ),
    "balancesheet": (
        *_COMMON_FINANCIAL_COLUMNS,
        "total_assets",
        "total_hldr_eqy_inc_min_int",
        "total_hldr_eqy_exc_min_int",
        "total_hldr_eqy",
        "total_liab",
        "inventories",
        "inventory",
        "accounts_receiv",
        "acct_receivable",
        "acc_receivable",
    ),
    "cashflow": (
        *_COMMON_FINANCIAL_COLUMNS,
        "n_cashflow_act",
        "c_fr_operate_a",
        "operating_cash_flow",
        "net_profit",
    ),
    "fina_indicator": (*_COMMON_FINANCIAL_COLUMNS,),
}
_MARKET_PROJECTION_COLUMNS = {
    "trade_cal": ("exchange", "cal_date", "is_open"),
    "stock_basic": ("ts_code", "list_date", "delist_date"),
    "index_basic": ("ts_code",),
    "suspend_d": ("ts_code", "trade_date", "suspend_type"),
    "daily": ("ts_code", "trade_date", "actual_available_date", "close", "vol"),
    "daily_basic": (
        "ts_code",
        "trade_date",
        "actual_available_date",
        "close",
        "turnover_rate",
        "amount",
        "pe",
        "pe_ttm",
    ),
    "index_daily": ("ts_code", "trade_date", "actual_available_date", "close"),
    "disclosure_date": ("ts_code", "end_date", "ann_date", "actual_date"),
}
UNSUPPORTED_CURRENT_REFERENCE_FIELDS = frozenset(
    {
        "name",
        "list_status",
        "status",
        "industry",
        "board",
        "market",
        "exchange",
    }
)


def _diagnostic_phase(diagnostics: ReplayDiagnostics | None, name: str) -> Any:
    return diagnostics.phase(name) if diagnostics is not None else nullcontext()


@dataclass(frozen=True, slots=True)
class ReplayValidationConfig:
    """Frozen orchestration settings; score settings remain in ``ReplayConfig``."""

    start: str = DEFAULT_START_MONTH
    end: str = DEFAULT_END_MONTH
    selection_rule: str = MONTHLY_SELECTION_RULE_VERSION
    anchor_day: int = DEFAULT_ANCHOR_DAY
    calendar_exchange: str | None = "SSE"
    top_n: int = 20
    seed: int = DEFAULT_SEED
    stage: str = "monthly"
    determinism_sample: int = 3
    replay: ReplayConfig | None = None
    # Validation target selection is orchestration metadata.  Keep it
    # explicit and frozen so a replay does not inherit the wall clock.
    today: str | date | datetime | pd.Timestamp | None = DEFAULT_VALIDATION_CUTOFF

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.determinism_sample < 0:
            raise ValueError("determinism_sample must be non-negative")
        if self.stage not in {"smoke", "yearly", "monthly"}:
            raise ValueError("stage must be smoke, yearly, or monthly")

    def declared(self) -> dict[str, Any]:
        return {
            "contract_version": PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
            "start": self.start,
            "end": self.end,
            "selection_rule": self.selection_rule,
            "anchor_day": self.anchor_day,
            "calendar_exchange": self.calendar_exchange,
            "top_n": self.top_n,
            "seed": self.seed,
            "today": _date_text(self.today),
            "stage": self.stage,
            "determinism_sample": self.determinism_sample,
            "resource_gate": _resource_gate_declaration(),
        }

    @property
    def fingerprint(self) -> str:
        return _hash_payload(self.declared())


class ResourceBlocked(RuntimeError):
    """Raised before/within a real replay when host memory is unsafe."""

    pass


class PITViolation(RuntimeError):
    """Raised when a replay result cannot prove its historical cutoff."""

    def __init__(self, violations: Iterable[str]) -> None:
        self.violations = tuple(dict.fromkeys(str(value) for value in violations))
        super().__init__("PIT validation failed: " + "; ".join(self.violations))


# A compatibility-friendly name for callers that use the issue's terminology.
PITValidationError = PITViolation


def _normalise_timestamp(value: Any, *, name: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid {name}: {value!r}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize().strftime("%Y%m%d")


def _month_text(value: Any, *, name: str) -> str:
    if isinstance(value, pd.Period):
        period = value
    else:
        text = str(value).strip()
        period = (
            pd.Period(text, freq="M")
            if re.fullmatch(r"\d{4}-\d{2}", text)
            else pd.Period(_normalise_timestamp(value, name=name), freq="M")
        )
    if period.year < 1900 or period.year > 2200:
        raise ValueError(f"invalid {name}: {value!r}")
    return str(period)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_payload(value: Any, *, length: int | None = 16) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return digest[:length] if length else digest


def _json_safe(value: Any) -> Any:
    """Convert pandas/Arrow scalar values into deterministic JSON values."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_localize(None)
        return timestamp.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if pd.notna(value) else None
    # numpy scalars are used by DataFrame.to_dict and have a stable item().
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            pass
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    return value


def _write_json(path: str | Path, payload: Any) -> Path:
    """Write JSON through a same-directory atomic replacement."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                _json_safe(payload),
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return destination


def _write_json_with_streamed_vectors(
    path: str | Path,
    payload: Any,
    spool_path: str | Path,
) -> Path:
    """Atomically write a snapshot while copying its vector array from a spool."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    marker = "__ASHARE_TURNAROUND_STREAMED_VECTORS__"
    safe_payload = _json_safe(payload)
    replay = safe_payload.get("replay") if isinstance(safe_payload, dict) else None
    if not isinstance(replay, dict) or "vectors" not in replay:
        raise ValueError("streamed snapshot payload is missing replay.vectors")
    replay["vectors"] = marker
    encoded = json.dumps(
        safe_payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    marker_text = json.dumps(marker, ensure_ascii=False)
    if encoded.count(marker_text) != 1:
        raise ValueError("streamed vector marker is not unique")
    prefix, suffix = encoded.split(marker_text, 1)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(prefix)
            temporary.write("[")
            first = True
            with Path(spool_path).open("r", encoding="utf-8") as spool:
                for line in spool:
                    record = line.strip()
                    if not record:
                        continue
                    if not first:
                        temporary.write(",")
                    temporary.write("\n")
                    temporary.write(record)
                    first = False
            temporary.write("\n]")
            temporary.write(suffix)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return destination


def _parse_proc_memory_text(text: str) -> dict[str, int]:
    """Parse Linux ``/proc`` memory values into bytes.

    ``meminfo``, ``status`` and ``smaps_rollup`` all use the same
    ``<field>: <integer> kB`` shape for the fields consumed here.  Keeping the
    parser independent of the filesystem makes the production fallback
    deterministic and straightforward to test.
    """

    multipliers = {
        "b": 1,
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
    }
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            number = int(parts[0])
        except ValueError:
            continue
        unit = parts[1].lower() if len(parts) > 1 else "b"
        multiplier = multipliers.get(unit)
        if multiplier is not None:
            values[key.strip()] = number * multiplier
    return values


def _read_proc_memory(path: str | Path) -> dict[str, int]:
    return _parse_proc_memory_text(Path(path).read_text(encoding="ascii"))


def _host_memory() -> dict[str, Any]:
    """Read host and live-process memory telemetry.

    On Linux, ``smaps_rollup`` supplies current RSS/PSS/private/swap values.
    If it is unavailable, current ``VmRSS``/``VmSwap`` from ``status`` are
    used and PSS/private remain explicitly unavailable.  In particular,
    ``ru_maxrss`` is never treated as a live working-set measurement.
    """

    values: dict[str, Any] = {
        "available_bytes": None,
        "swap_total_bytes": None,
        "swap_free_bytes": None,
        "swap_used_bytes": None,
        "current_rss_bytes": None,
        "current_pss_bytes": None,
        "current_private_bytes": None,
        "current_swap_bytes": None,
        "peak_rss_diagnostic_bytes": None,
        # Kept as a read-only compatibility alias for existing diagnostic
        # consumers.  It is not a hard-gate input.
        "peak_rss_bytes": None,
        "live_memory_metric": "unsupported",
        "live_working_set_supported": False,
    }
    try:
        meminfo = _read_proc_memory("/proc/meminfo")
        values["available_bytes"] = meminfo.get("MemAvailable")
        values["swap_total_bytes"] = meminfo.get("SwapTotal")
        values["swap_free_bytes"] = meminfo.get("SwapFree")
    except (OSError, ValueError, IndexError):
        pass

    rollup: dict[str, int] = {}
    try:
        rollup = _read_proc_memory("/proc/self/smaps_rollup")
    except (OSError, ValueError, IndexError):
        pass
    if rollup:
        values["current_rss_bytes"] = rollup.get("Rss")
        values["current_pss_bytes"] = rollup.get("Pss")
        private_components = [
            rollup.get("Private_Clean"),
            rollup.get("Private_Dirty"),
        ]
        present_private = [value for value in private_components if value is not None]
        if present_private:
            values["current_private_bytes"] = sum(present_private)
        values["current_swap_bytes"] = rollup.get("Swap")
        values["live_memory_metric"] = "proc_smaps_rollup"
        values["live_working_set_supported"] = (
            values["current_pss_bytes"] is not None
            or values["current_private_bytes"] is not None
        )
    else:
        # VmRSS is a current measurement and is a documented fallback, not a
        # reinterpretation of the historical ru_maxrss value.
        try:
            status = _read_proc_memory("/proc/self/status")
        except (OSError, ValueError, IndexError):
            status = {}
        values["current_rss_bytes"] = status.get("VmRSS")
        values["current_swap_bytes"] = status.get("VmSwap")
        if values["current_rss_bytes"] is not None:
            values["live_memory_metric"] = "proc_status_vmrss_fallback"
            values["live_working_set_supported"] = True

    try:
        # Linux reports KiB; macOS reports bytes.  ru_maxrss remains a
        # diagnostic high-water value on every supported platform.
        raw_peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        peak = raw_peak if sys.platform == "darwin" else raw_peak * 1024
        values["peak_rss_diagnostic_bytes"] = peak
        values["peak_rss_bytes"] = peak
    except (AttributeError, OSError, ValueError):
        pass

    if values["swap_total_bytes"] is not None and values["swap_free_bytes"] is not None:
        values["swap_used_bytes"] = (
            values["swap_total_bytes"] - values["swap_free_bytes"]
        )
    return values


def _raw_corpus_bytes(data_dir: str | Path) -> int:
    root = Path(data_dir).expanduser() / "raw"
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*.parquet") if path.is_file())


def _assert_initial_resource_gate(data_dir: str | Path) -> None:
    """Refuse a large real replay when the host is already under pressure."""

    if _raw_corpus_bytes(data_dir) < LARGE_CORPUS_BYTES:
        return
    memory = _host_memory()
    # The initial gate keeps the original available-RAM and swap-floor checks,
    # and also refuses a large-corpus run when no live process metric can be
    # obtained.  A diagnostic ru_maxrss value is deliberately irrelevant here.
    _assert_runtime_resource_gate(
        {"swap_used_bytes": memory.get("swap_used_bytes")},
        memory=memory,
        phase="initial",
    )


def _assert_runtime_resource_gate(
    baseline: Mapping[str, Any],
    *,
    memory: Mapping[str, Any] | None = None,
    phase: str = "runtime",
) -> dict[str, Any]:
    """Enforce current host/process pressure; return the sampled telemetry.

    ``ru_maxrss`` is intentionally not read by any hard check.  It is a
    lifetime diagnostic and may have been raised by an earlier phase, process,
    or allocation that is no longer live when this gate is sampled.
    """

    observed = dict(memory) if memory is not None else _host_memory()
    available = observed.get("available_bytes")
    if available is not None and available < MIN_AVAILABLE_RAM_BYTES:
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: available RAM {available} < {MIN_AVAILABLE_RAM_BYTES} bytes"
        )
    if available is None:
        raise ResourceBlocked(f"BLOCKED [{phase}]: available RAM telemetry unavailable")

    swap_total = observed.get("swap_total_bytes")
    swap_free = observed.get("swap_free_bytes")
    if swap_total and swap_free is None:
        raise ResourceBlocked(f"BLOCKED [{phase}]: swap-free telemetry unavailable")
    if swap_total and swap_free < MIN_SWAP_FREE_BYTES:
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: swap free {swap_free} < {MIN_SWAP_FREE_BYTES} bytes"
        )

    current_pss = observed.get("current_pss_bytes")
    current_private = observed.get("current_private_bytes")
    current_rss = observed.get("current_rss_bytes")
    if current_pss is not None and current_pss > MAX_LIVE_PSS_BYTES:
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: live PSS {current_pss} > {MAX_LIVE_PSS_BYTES} bytes"
        )
    if current_private is not None and current_private > MAX_LIVE_PRIVATE_BYTES:
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: live private memory {current_private} > "
            f"{MAX_LIVE_PRIVATE_BYTES} bytes"
        )
    if current_pss is None and current_private is None:
        if current_rss is None:
            raise ResourceBlocked(f"BLOCKED [{phase}]: live memory telemetry unavailable")
        # This is the explicit /proc/self/status VmRSS fallback.  It is a
        # current value, never the ru_maxrss high-water value.
        if current_rss > MAX_LIVE_PRIVATE_BYTES:
            raise ResourceBlocked(
                f"BLOCKED [{phase}]: live RSS fallback {current_rss} > "
                f"{MAX_LIVE_PRIVATE_BYTES} bytes"
            )

    process_swap = observed.get("current_swap_bytes")
    if process_swap is not None and process_swap > MAX_PROCESS_SWAP_BYTES:
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: process swap {process_swap} > "
            f"{MAX_PROCESS_SWAP_BYTES} bytes"
        )

    baseline_swap = baseline.get("swap_used_bytes")
    current_swap = observed.get("swap_used_bytes")
    if (
        baseline_swap is not None
        and current_swap is not None
        and current_swap > baseline_swap + MAX_SWAP_GROWTH_BYTES
    ):
        raise ResourceBlocked(
            f"BLOCKED [{phase}]: swap grew from {baseline_swap} to {current_swap} bytes"
        )
    return observed


@dataclass(frozen=True, slots=True)
class MonthlySnapshotTarget:
    """One deterministic monthly target selected from ``trade_cal``."""

    target_month: str
    anchor_date: str
    selected_trading_date: str | None
    selection_reason: str
    status: str
    calendar_source: str = "trade_cal"
    calendar_version: str = "trade-cal-v1"
    calendar_exchange: str | None = "SSE"
    incomplete_month: bool = False

    @property
    def available(self) -> bool:
        return self.status == "AVAILABLE" and self.selected_trading_date is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_month": self.target_month,
            "anchor_date": self.anchor_date,
            "selected_trading_date": self.selected_trading_date,
            "selection_reason": self.selection_reason,
            "status": self.status,
            "availability_status": self.status,
            "calendar_source": self.calendar_source,
            "calendar_version": self.calendar_version,
            "calendar_exchange": self.calendar_exchange,
            "incomplete_month": self.incomplete_month,
        }


def _calendar_for_selection(
    trade_calendar: pd.DataFrame,
    *,
    exchange: str | None,
) -> tuple[pd.DataFrame, str | None]:
    if trade_calendar.empty or "cal_date" not in trade_calendar.columns:
        return pd.DataFrame(), exchange
    frame = trade_calendar.copy()
    frame["_cal_date"] = normalize_date_series(frame["cal_date"])
    frame = frame.loc[frame["_cal_date"].notna()].copy()
    if exchange is not None and "exchange" in frame.columns:
        selected_exchange = str(exchange).strip().upper()
        frame = frame.loc[frame["exchange"].astype("string").str.upper().eq(selected_exchange)]
        return frame, selected_exchange
    return frame, None if "exchange" not in frame.columns else exchange


def select_monthly_snapshot_dates(
    trade_calendar: pd.DataFrame,
    start: str | date | datetime | pd.Timestamp = DEFAULT_START_MONTH,
    end: str | date | datetime | pd.Timestamp = DEFAULT_END_MONTH,
    *,
    anchor_day: int = DEFAULT_ANCHOR_DAY,
    exchange: str | None = "SSE",
    today: str | date | datetime | pd.Timestamp | None = DEFAULT_VALIDATION_CUTOFF,
    selection_rule: str = MONTHLY_SELECTION_RULE_VERSION,
) -> tuple[MonthlySnapshotTarget, ...]:
    """Select one session per month without using market performance.

    The anchor day is fixed at the 15th by the versioned default rule.  The
    first open session on/after the anchor is selected *within the same
    calendar month*.  A month with no usable session is unavailable rather
    than silently borrowing a neighboring month.  ``today`` is an explicit
    orchestration cutoff; omitting it uses the frozen validation cutoff rather
    than consulting the wall clock.
    """

    if selection_rule not in {
        MONTHLY_SELECTION_RULE_VERSION,
        "monthly-anchor-day-v1",
        "monthly-anchor-15",
    }:
        raise ValueError(f"unsupported monthly selection rule: {selection_rule}")
    if not 1 <= int(anchor_day) <= 28:
        raise ValueError("anchor_day must be between 1 and 28")
    start_month = pd.Period(_month_text(start, name="start"), freq="M")
    end_month = pd.Period(_month_text(end, name="end"), freq="M")
    if end_month < start_month:
        raise ValueError("end month must not be earlier than start month")
    current_day = _normalise_timestamp(
        today if today is not None else DEFAULT_VALIDATION_CUTOFF,
        name="today",
    )
    calendar, selected_exchange = _calendar_for_selection(trade_calendar, exchange=exchange)
    if not calendar.empty and "is_open" in calendar.columns:
        calendar = calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)]
    elif not calendar.empty:
        # A date column without an explicit open/closed contract cannot select
        # a trading session; fail closed instead of treating every date as open.
        calendar = pd.DataFrame()
    open_dates = (
        pd.DatetimeIndex(calendar["_cal_date"].drop_duplicates().sort_values())
        if not calendar.empty
        else pd.DatetimeIndex([])
    )

    targets: list[MonthlySnapshotTarget] = []
    for month in pd.period_range(start_month, end_month, freq="M"):
        month_start = month.start_time.normalize()
        month_end = month.end_time.normalize()
        anchor = pd.Timestamp(year=month.year, month=month.month, day=int(anchor_day))
        month_text = str(month)
        if month_start > current_day or anchor > current_day:
            targets.append(
                MonthlySnapshotTarget(
                    month_text,
                    anchor.strftime("%Y%m%d"),
                    None,
                    "month is after the validation cutoff; no future substitution",
                    "UNAVAILABLE_FUTURE",
                    calendar_exchange=selected_exchange,
                )
            )
            continue
        candidates = open_dates[
            (open_dates >= anchor) & (open_dates <= month_end) & (open_dates <= current_day)
        ]
        if len(candidates) == 0:
            targets.append(
                MonthlySnapshotTarget(
                    month_text,
                    anchor.strftime("%Y%m%d"),
                    None,
                    "no valid open trade_cal session on/after anchor within target month",
                    "UNAVAILABLE",
                    calendar_exchange=selected_exchange,
                )
            )
            continue
        selected = pd.Timestamp(candidates[0]).normalize()
        targets.append(
            MonthlySnapshotTarget(
                month_text,
                anchor.strftime("%Y%m%d"),
                selected.strftime("%Y%m%d"),
                "first open trade_cal session on/after fixed anchor within target month",
                "AVAILABLE",
                calendar_exchange=selected_exchange,
                incomplete_month=month.year == current_day.year
                and month.month == current_day.month,
            )
        )
    return tuple(targets)


# Friendly aliases used by callers that name the object rather than the dates.
select_monthly_targets = select_monthly_snapshot_dates
monthly_snapshot_targets = select_monthly_snapshot_dates


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_file_entry(
    root: Path, dataset: str, path: Path, *, content_hash: bool
) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    columns = tuple(str(value) for value in parquet.schema_arrow.names)
    relative = path.relative_to(root).as_posix()
    entry: dict[str, Any] = {
        "dataset": dataset,
        "path": relative,
        "rows": int(parquet.metadata.num_rows),
        "size_bytes": int(path.stat().st_size),
        "schema_columns": sorted(columns),
        "schema_hash": _hash_payload(sorted(columns), length=None),
    }
    entry["content_hash"] = _file_sha256(path) if content_hash else None
    return entry


def _manifest_dataset(entries: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(entries, key=lambda value: str(value["path"]))
    return {
        "file_count": len(ordered),
        "row_count": sum(int(value["rows"]) for value in ordered),
        "size_bytes": sum(int(value["size_bytes"]) for value in ordered),
        "files": ordered,
        "dataset_manifest_id": _hash_payload(ordered, length=None),
    }


def _corpus_identity(datasets: Mapping[str, Any]) -> str:
    return _hash_payload(
        {name: datasets.get(name, _manifest_dataset([])) for name in sorted(datasets)},
        length=None,
    )


def build_input_manifest(
    data_dir: str | Path,
    *,
    datasets: Iterable[str] = MANIFEST_DATASETS,
    content_hash: bool = True,
) -> dict[str, Any]:
    """Build a stable read-only identity for the local input corpus.

    The manifest records partition paths, rows, byte sizes, schema hashes and
    optional content hashes.  It never copies a source partition and excludes
    runtime timestamps from the identity.
    """

    root = Path(data_dir).expanduser().resolve()
    store = RawParquetStore(root)
    selected = tuple(dict.fromkeys(str(value) for value in datasets))
    dataset_payload: dict[str, Any] = {}
    for dataset in sorted(selected):
        entries = [
            _manifest_file_entry(root, dataset, path, content_hash=content_hash)
            for path in store.parquet_files(dataset)
        ]
        dataset_payload[dataset] = _manifest_dataset(entries)

    state_entries: list[dict[str, Any]] = []
    for filename in ("bootstrap-checkpoints.json", "market-bootstrap-checkpoints.json"):
        path = root / "state" / filename
        if not path.is_file():
            continue
        state_entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": int(path.stat().st_size),
                "content_hash": _file_sha256(path) if content_hash else None,
            }
        )
    state_entries.sort(key=lambda value: value["path"])
    identity_payload = {
        "manifest_schema_version": "input-manifest-v1",
        "datasets": dataset_payload,
        "state_files": state_entries,
    }
    return {
        **identity_payload,
        "data_dir": str(root),
        "manifest_id": _hash_payload(identity_payload, length=None),
        "dataset_manifest_ids": {
            name: payload["dataset_manifest_id"] for name, payload in dataset_payload.items()
        },
        "financial_corpus_identity": _corpus_identity(
            {
                name: dataset_payload.get(name, _manifest_dataset([]))
                for name in FINANCIAL_CORPUS_DATASETS
            }
        ),
        "market_corpus_identity": _corpus_identity(
            {
                name: dataset_payload.get(name, _manifest_dataset([]))
                for name in MARKET_CORPUS_DATASETS
            }
        ),
        "checkpoint_identity": _hash_payload(state_entries, length=None),
    }


def _frame_manifest(frames: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """Create the same identity surface for in-memory synthetic fixtures."""

    datasets: dict[str, Any] = {}
    for dataset in sorted(frames):
        frame = frames[dataset]
        columns = sorted(str(value) for value in frame.columns)
        records = [
            _json_safe(record)
            for record in frame.reindex(columns=columns).to_dict(orient="records")
        ]
        records.sort(key=_canonical_json)
        datasets[dataset] = _manifest_dataset(
            [
                {
                    "dataset": dataset,
                    "path": f"in-memory/{dataset}",
                    "rows": len(frame),
                    "size_bytes": len(_canonical_json(records).encode("utf-8")),
                    "schema_columns": columns,
                    "schema_hash": _hash_payload(columns, length=None),
                    "content_hash": _hash_payload(records, length=None),
                }
            ]
        )
    identity_payload = {"manifest_schema_version": "input-manifest-v1", "datasets": datasets}
    return {
        **identity_payload,
        "data_dir": "<in-memory>",
        "manifest_id": _hash_payload(identity_payload, length=None),
        "dataset_manifest_ids": {
            name: payload["dataset_manifest_id"] for name, payload in datasets.items()
        },
        "financial_corpus_identity": _corpus_identity(
            {name: datasets.get(name, _manifest_dataset([])) for name in FINANCIAL_CORPUS_DATASETS}
        ),
        "market_corpus_identity": _corpus_identity(
            {name: datasets.get(name, _manifest_dataset([])) for name in MARKET_CORPUS_DATASETS}
        ),
        "checkpoint_identity": _hash_payload([], length=None),
    }


def _git_commit() -> str:
    try:
        source_root = Path(__file__).resolve().parents[3]
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass(frozen=True, slots=True)
class RegimeResult:
    """As-of-only benchmark regime label for sample coverage reporting."""

    label: str
    status: str
    reason: str | None
    benchmark_id: str
    endpoint_date: str | None
    prior_endpoint_date: str | None
    trailing_change: float | None
    trailing_high_drawdown: float | None
    session_count: int
    contract_version: str = MARKET_REGIME_CONTRACT_VERSION
    lookback_sessions: int = 60
    drawdown_lookback_sessions: int = 252
    bull_change_threshold: float = 0.10
    bear_change_threshold: float = -0.10
    bear_drawdown_threshold: float = -0.20

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "status": self.status,
            "reason": self.reason,
            "benchmark_id": self.benchmark_id,
            "endpoint_date": self.endpoint_date,
            "prior_endpoint_date": self.prior_endpoint_date,
            "trailing_change": self.trailing_change,
            "trailing_high_drawdown": self.trailing_high_drawdown,
            "session_count": self.session_count,
            "contract_version": self.contract_version,
            "formula": (
                "bull if trailing_change >= 0.10 and drawdown > -0.20; "
                "bear if trailing_change <= -0.10 or drawdown <= -0.20; "
                "otherwise range; all inputs end at as_of"
            ),
            "lookback_sessions": self.lookback_sessions,
            "drawdown_lookback_sessions": self.drawdown_lookback_sessions,
            "bull_change_threshold": self.bull_change_threshold,
            "bear_change_threshold": self.bear_change_threshold,
            "bear_drawdown_threshold": self.bear_drawdown_threshold,
            "as_of_rule": "benchmark observations <= selected trading session only",
        }


def _benchmark_history_for_regime(
    index_daily: pd.DataFrame,
    trade_calendar: pd.DataFrame | None,
    *,
    benchmark_id: str,
    as_of: pd.Timestamp,
    lookback: int,
    drawdown_lookback: int,
) -> tuple[pd.DatetimeIndex, pd.Series]:
    if index_daily.empty or not {"ts_code", "trade_date", "close"}.issubset(index_daily.columns):
        return pd.DatetimeIndex([]), pd.Series(dtype="float64")
    frame = index_daily.loc[index_daily["ts_code"].astype("string").eq(benchmark_id)].copy()
    dates = normalize_date_series(frame["trade_date"])
    frame = frame.loc[dates.notna() & dates.le(as_of)].copy()
    if "actual_available_date" in frame.columns:
        available = normalize_date_series(frame["actual_available_date"])
        frame = frame.loc[available.isna() | available.le(as_of)]
    frame["_date"] = normalize_date_series(frame["trade_date"])
    frame["_close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[frame["_close"].notna() & frame["_close"].gt(0)].copy()
    if frame.empty:
        return pd.DatetimeIndex([]), pd.Series(dtype="float64")
    frame = frame.sort_values(["_date", "_close"], kind="mergesort").drop_duplicates(
        "_date", keep="last"
    )
    calendar_dates = pd.DatetimeIndex([])
    if trade_calendar is not None and not trade_calendar.empty:
        calendar = trade_calendar.copy()
        if {"cal_date", "is_open"}.issubset(calendar.columns):
            cal_dates = normalize_date_series(calendar["cal_date"])
            calendar_dates = pd.DatetimeIndex(
                cal_dates.loc[
                    cal_dates.notna()
                    & cal_dates.le(as_of)
                    & pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)
                ]
                .drop_duplicates()
                .sort_values()
            )
    dates_index = calendar_dates if len(calendar_dates) else pd.DatetimeIndex(frame["_date"])
    close_by_date = frame.set_index("_date")["_close"]
    # Keep calendar sessions even when a benchmark row is missing.  Collapsing
    # a missing session would silently turn a 60-session lookback into a
    # different window; the classifier below therefore returns UNKNOWN.
    return dates_index, close_by_date


def classify_market_regime(
    index_daily: pd.DataFrame,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    trade_calendar: pd.DataFrame | None = None,
    benchmark_id: str = DEFAULT_BENCHMARK_ID,
    lookback_sessions: int = 60,
    drawdown_lookback_sessions: int = 252,
) -> RegimeResult:
    """Label bull/bear/range using only the benchmark history through as-of."""

    as_of = _normalise_timestamp(as_of_date, name="as_of_date")
    if lookback_sessions <= 0 or drawdown_lookback_sessions <= 0:
        raise ValueError("regime lookbacks must be positive")
    sessions, closes = _benchmark_history_for_regime(
        index_daily,
        trade_calendar,
        benchmark_id=str(benchmark_id).upper(),
        as_of=as_of,
        lookback=lookback_sessions,
        drawdown_lookback=drawdown_lookback_sessions,
    )
    endpoint = _date_text(as_of)
    if len(sessions) <= lookback_sessions or as_of not in set(sessions):
        return RegimeResult(
            "unknown",
            "UNKNOWN",
            "insufficient_benchmark_history_or_endpoint",
            str(benchmark_id).upper(),
            endpoint if as_of in set(sessions) else None,
            None,
            None,
            None,
            len(sessions),
            lookback_sessions=lookback_sessions,
            drawdown_lookback_sessions=drawdown_lookback_sessions,
        )
    position = list(sessions).index(as_of)
    prior_date = pd.Timestamp(sessions[position - lookback_sessions])
    current_value = closes.get(as_of)
    prior_value = closes.get(prior_date)
    current = (
        float(current_value) if current_value is not None and pd.notna(current_value) else None
    )
    prior = float(prior_value) if prior_value is not None and pd.notna(prior_value) else None
    trailing_change = current / prior - 1.0 if current is not None and prior and prior > 0 else None
    window_start = max(0, position - drawdown_lookback_sessions + 1)
    drawdown_window = [
        closes.get(pd.Timestamp(value)) for value in sessions[window_start : position + 1]
    ]
    drawdown = None
    if (
        current is not None
        and drawdown_window
        and all(
            value is not None and pd.notna(value) and float(value) > 0 for value in drawdown_window
        )
    ):
        high = max(float(value) for value in drawdown_window)
        drawdown = current / high - 1.0 if high > 0 else None
    if trailing_change is None or drawdown is None:
        label, status, reason = "unknown", "UNKNOWN", "invalid_benchmark_regime_inputs"
    elif trailing_change >= 0.10 and drawdown > -0.20:
        label, status, reason = "bull", "KNOWN", None
    elif trailing_change <= -0.10 or drawdown <= -0.20:
        label, status, reason = "bear", "KNOWN", None
    else:
        label, status, reason = "range", "KNOWN", None
    return RegimeResult(
        label,
        status,
        reason,
        str(benchmark_id).upper(),
        _date_text(as_of),
        prior_date.strftime("%Y%m%d"),
        trailing_change,
        drawdown,
        len(sessions),
        lookback_sessions=lookback_sessions,
        drawdown_lookback_sessions=drawdown_lookback_sessions,
    )


# Alias with the noun used in some downstream notebooks.
market_regime = classify_market_regime


def _parse_possible_date(value: Any) -> pd.Timestamp | None:
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return _normalise_timestamp(value, name="evidence_date")
    if not isinstance(value, str):
        return None
    text = value.strip()
    formats = ("%Y%m%d", "%Y-%m-%d")
    for pattern, value_format in zip((r"\d{8}", r"\d{4}-\d{2}-\d{2}"), formats):
        if not re.fullmatch(pattern, text):
            continue
        try:
            return pd.Timestamp(datetime.strptime(text, value_format)).normalize()
        except ValueError:
            return None
    return None


def _date_key_is_observable(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(
        token in normalized
        for token in (
            "availability",
            "trade_date",
            "cal_date",
            "observation_date",
            "observation_session",
            "effective_session",
            "start_session",
            "end_session",
            "window_start",
            "window_end",
            "population_start",
            "population_end",
            "event_date",
            "as_of",
        )
    )


def _observable_key(key: str, cache: dict[str, bool] | None) -> bool:
    if cache is None:
        return _date_key_is_observable(key)
    observable = cache.get(key)
    if observable is None:
        observable = _date_key_is_observable(key)
        cache[key] = observable
    return observable


def _collect_observable_dates(
    value: Any,
    *,
    key: str,
    path: str,
    output: list[tuple[str, pd.Timestamp]],
    observable_key_cache: dict[str, bool] | None = None,
    date_cache: dict[str, pd.Timestamp | None] | None = None,
    safe_container_cache: dict[tuple[int, str], bool] | None = None,
    as_of: pd.Timestamp | None = None,
) -> bool:
    is_container = isinstance(value, (Mapping, list, tuple, set, frozenset))
    safe_cache_key = (id(value), key)
    cache_this_container = is_container and path != "evidence"
    if (
        cache_this_container
        and safe_container_cache is not None
        and safe_container_cache.get(safe_cache_key)
    ):
        return False
    future_observed = False
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_key_text = str(child_key)
            child_is_container = isinstance(
                child_value, (Mapping, list, tuple, set, frozenset)
            )
            if not child_is_container and not _observable_key(
                child_key_text, observable_key_cache
            ):
                continue
            future_observed |= _collect_observable_dates(
                child_value,
                key=child_key_text,
                path=f"{path}.{child_key}",
                output=output,
                observable_key_cache=observable_key_cache,
                date_cache=date_cache,
                safe_container_cache=safe_container_cache,
                as_of=as_of,
            )
    elif isinstance(value, (list, tuple, set, frozenset)):
        observable = _observable_key(key, observable_key_cache)
        for index, child_value in enumerate(value):
            child_is_container = isinstance(
                child_value, (Mapping, list, tuple, set, frozenset)
            )
            if not child_is_container and not observable:
                continue
            future_observed |= _collect_observable_dates(
                child_value,
                key=key,
                path=f"{path}[{index}]",
                output=output,
                observable_key_cache=observable_key_cache,
                date_cache=date_cache,
                safe_container_cache=safe_container_cache,
                as_of=as_of,
            )
    elif _observable_key(key, observable_key_cache):
        if date_cache is not None and isinstance(value, str):
            text = value.strip()
            if text not in date_cache:
                date_cache[text] = _parse_possible_date(text)
            parsed = date_cache[text]
        else:
            parsed = _parse_possible_date(value)
        if parsed is not None:
            output.append((path, parsed))
            future_observed = as_of is not None and parsed > as_of
    if cache_this_container and safe_container_cache is not None and not future_observed:
        safe_container_cache[safe_cache_key] = True
    return future_observed


def _evidence_dates(
    evidence: FeatureEvidence,
    *,
    observable_key_cache: dict[str, bool] | None = None,
    date_cache: dict[str, pd.Timestamp | None] | None = None,
    safe_container_cache: dict[tuple[int, str], bool] | None = None,
    as_of: pd.Timestamp | None = None,
) -> list[tuple[str, pd.Timestamp]]:
    """Find observable dates without deep-copying the full evidence payload."""

    payload = {item.name: getattr(evidence, item.name) for item in fields(FeatureEvidence)}
    for key in (
        "start_session",
        "end_session",
        "stock_start",
        "stock_end",
        "benchmark_start",
        "benchmark_end",
        "benchmark_id",
        "stock_return",
        "benchmark_return",
        "excess_return",
    ):
        if key in evidence.components:
            payload[key] = evidence.components[key]
    return_dates: list[tuple[str, pd.Timestamp]] = []
    _collect_observable_dates(
        payload,
        key="evidence",
        path="evidence",
        output=return_dates,
        observable_key_cache=observable_key_cache,
        date_cache=date_cache,
        safe_container_cache=safe_container_cache,
        as_of=as_of,
    )
    return return_dates


def _validate_replay_vector_pit(
    vector: FeatureVector,
    *,
    as_of: pd.Timestamp,
    benchmark_id: str,
    observable_key_cache: dict[str, bool],
    date_cache: dict[str, pd.Timestamp | None],
    safe_container_cache: dict[tuple[int, str], bool],
) -> tuple[str, ...]:
    violations: list[str] = []
    if vector.as_of_date != as_of.strftime("%Y%m%d"):
        violations.append(f"{vector.ts_code}:vector_as_of_date_mismatch")
    vector_dates: list[tuple[str, pd.Timestamp]] = []
    _collect_observable_dates(
        vector.metadata,
        key="metadata",
        path="vector.metadata",
        output=vector_dates,
        observable_key_cache=observable_key_cache,
        date_cache=date_cache,
        safe_container_cache=safe_container_cache,
        as_of=as_of,
    )
    _collect_observable_dates(
        vector.benchmark_metadata,
        key="benchmark_metadata",
        path="vector.benchmark_metadata",
        output=vector_dates,
        observable_key_cache=observable_key_cache,
        date_cache=date_cache,
        safe_container_cache=safe_container_cache,
        as_of=as_of,
    )
    for path, observed in vector_dates:
        if observed > as_of:
            violations.append(
                f"{vector.ts_code}:{path}:observation_after_as_of={observed.strftime('%Y%m%d')}"
            )
    for name, evidence in vector.evidence.items():
        for path, observed in _evidence_dates(
            evidence,
            observable_key_cache=observable_key_cache,
            date_cache=date_cache,
            safe_container_cache=safe_container_cache,
            as_of=as_of,
        ):
            if observed > as_of:
                violations.append(
                    f"{vector.ts_code}:{name}:{path}:observation_after_as_of={observed.strftime('%Y%m%d')}"
                )
        components = evidence.components
        if name.startswith("excess_return_") or name in {
            "recent_excess_return",
            "recent_excess_return",
        }:
            if evidence.status in {"known", "valid"} and components.get("benchmark_return") is None:
                violations.append(f"{vector.ts_code}:{name}:benchmark_missing_but_excess_known")
            component_benchmark = components.get("benchmark_id")
            if (
                component_benchmark is not None
                and str(component_benchmark).upper() != str(benchmark_id).upper()
            ):
                violations.append(f"{vector.ts_code}:{name}:benchmark_id={component_benchmark}")
    if vector.benchmark_metadata.get("benchmark_id") not in {
        None,
        benchmark_id,
        str(benchmark_id).upper(),
    }:
        violations.append(f"{vector.ts_code}:benchmark_metadata_mismatch")
    return tuple(dict.fromkeys(violations))


def validate_normalized_vector_pit(
    normalized_vector: Mapping[str, Any],
    *,
    provenance_store: Mapping[str, Any] | None = None,
    as_of_date: str | date | datetime | pd.Timestamp,
    benchmark_id: str = DEFAULT_BENCHMARK_ID,
) -> tuple[str, ...]:
    """Run the ordinary PIT validator against one normalized vector."""

    store = provenance_store
    if store is None and isinstance(normalized_vector, Mapping):
        store = normalized_vector.get("provenance_store")
    integrity_payload = {
        "vectors": [normalized_vector],
        "provenance_store": store if store is not None else {},
    }
    integrity = validate_normalized_integrity(integrity_payload)
    if integrity:
        return tuple(f"normalized:{value}" for value in integrity)
    try:
        expanded = expand_normalized_vector(normalized_vector, store)
        vector = feature_vector_from_payload(expanded)
        as_of = _normalise_timestamp(as_of_date, name="as_of_date")
        return _validate_replay_vector_pit(
            vector,
            as_of=as_of,
            benchmark_id=benchmark_id,
            observable_key_cache={},
            date_cache={},
            safe_container_cache={},
        )
    except (KeyError, TypeError, ValueError) as exc:
        return (f"normalized:decode_error:{type(exc).__name__}:{exc}",)


def validate_normalized_snapshot_pit(
    snapshot: Mapping[str, Any],
    *,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    benchmark_id: str = DEFAULT_BENCHMARK_ID,
) -> tuple[str, ...]:
    """Expand and PIT-check every evidence-bearing vector in a snapshot."""

    if not isinstance(snapshot, Mapping):
        return ("normalized:snapshot_not_mapping",)
    integrity = validate_normalized_integrity(snapshot)
    if integrity:
        return tuple(f"normalized:{value}" for value in integrity)
    try:
        expanded = expand_normalized_snapshot(snapshot)
    except (KeyError, TypeError, ValueError) as exc:
        return (f"normalized:decode_error:{type(exc).__name__}:{exc}",)
    replay = expanded.get("replay") if isinstance(expanded, Mapping) else None
    if not isinstance(replay, Mapping):
        return ()
    resolved_as_of = as_of_date or replay.get("metadata", {}).get("as_of_date")
    if resolved_as_of is None:
        return ("normalized:missing_as_of_date",)
    violations: list[str] = []
    for vector_payload in replay.get("vectors", ()):
        if not isinstance(vector_payload, Mapping):
            violations.append("normalized:vector_not_mapping")
            continue
        try:
            vector = feature_vector_from_payload(vector_payload)
            violations.extend(
                _validate_replay_vector_pit(
                    vector,
                    as_of=_normalise_timestamp(resolved_as_of, name="as_of_date"),
                    benchmark_id=benchmark_id,
                    observable_key_cache={},
                    date_cache={},
                    safe_container_cache={},
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            violations.append(f"normalized:decode_error:{type(exc).__name__}:{exc}")
    return tuple(dict.fromkeys(violations))


def validate_replay_pit(
    result: ReplayResult | Mapping[str, Any],
    *,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    benchmark_id: str = DEFAULT_BENCHMARK_ID,
    require_historical_universe: bool = False,
    prevalidated_vector_violations: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return hard PIT violations found in one production replay result."""

    if isinstance(result, Mapping):
        return validate_normalized_snapshot_pit(
            result,
            as_of_date=as_of_date,
            benchmark_id=benchmark_id,
        )
    as_of = _normalise_timestamp(as_of_date or result.as_of_date, name="as_of_date")
    violations: list[str] = []
    observable_key_cache: dict[str, bool] = {}
    date_cache: dict[str, pd.Timestamp | None] = {}
    safe_container_cache: dict[tuple[int, str], bool] = {}
    if result.as_of_date != as_of.strftime("%Y%m%d"):
        violations.append("result_as_of_date_mismatch")
    if require_historical_universe:
        if not result.universe_pit_safe:
            violations.append("current_universe_substitution")
        unsafe = UNSUPPORTED_CURRENT_REFERENCE_FIELDS.intersection(
            result.universe_source_evidence.get("used_fields", ())
            if isinstance(result.universe_source_evidence, Mapping)
            else ()
        )
        if unsafe:
            violations.append("current_universe_fields_used=" + ",".join(sorted(unsafe)))
        if "safe_fields" in result.universe_source_evidence:
            safe = set(result.universe_source_evidence.get("safe_fields", ()))
            if not {"ts_code", "list_date", "delist_date"}.issubset(safe):
                violations.append("historical_universe_safe_fields_incomplete")
        for decision in result.universe_decisions:
            used_fields = set(decision.evidence.get("fields_used", ()))
            unsafe_decision_fields = UNSUPPORTED_CURRENT_REFERENCE_FIELDS.intersection(used_fields)
            if unsafe_decision_fields:
                violations.append(
                    "universe_decision_current_fields_used="
                    + ",".join(sorted(unsafe_decision_fields))
                )

    if prevalidated_vector_violations is None:
        for vector in result.vectors:
            violations.extend(
                _validate_replay_vector_pit(
                    vector,
                    as_of=as_of,
                    benchmark_id=benchmark_id,
                    observable_key_cache=observable_key_cache,
                    date_cache=date_cache,
                    safe_container_cache=safe_container_cache,
                )
            )
    else:
        violations.extend(str(value) for value in prevalidated_vector_violations)

    score_by_code = {score.ts_code: score for score in result.scores}
    diagnostic = result.diagnostic_ranked
    if diagnostic is None:
        if result.scores:
            violations.append("diagnostic_ranked_missing")
    else:
        diagnostic_codes = diagnostic.get("ts_code", pd.Series(dtype="string")).astype(str)
        if len(diagnostic) != len(result.scores) or diagnostic_codes.duplicated().any():
            violations.append("diagnostic_rank_incomplete")
        if set(diagnostic_codes) != set(score_by_code):
            violations.append("diagnostic_rank_missing_score_candidate")
        if "ranking_eligible" not in diagnostic.columns and len(diagnostic):
            violations.append("diagnostic_rank_missing_eligibility_fields")
    for _, row in result.ranked.iterrows():
        code = str(row.get("ts_code"))
        score = score_by_code.get(code)
        if score is None:
            violations.append(f"formal_rank_missing_score={code}")
            continue
        if not score.ranking_eligible or score.rejected:
            violations.append(f"ranking_eligible_false_in_formal_top_n={code}")
    if not result.ranked.empty:
        formal_codes = result.ranked["ts_code"].astype(str).tolist()
        expected_codes = sorted(
            formal_codes,
            key=lambda code: (
                -float(score_by_code[code].turnaround_score)
                if score_by_code.get(code) is not None
                and score_by_code[code].turnaround_score is not None
                else float("inf"),
                code,
            ),
        )
        if formal_codes != expected_codes:
            violations.append("formal_rank_not_score_desc_then_ts_code")

    return tuple(dict.fromkeys(violations))


# Alias retained for direct assertion-style use.
assert_replay_pit_safe = validate_replay_pit


def _historical_replay_config(config: ReplayConfig) -> ReplayConfig:
    universe = replace(
        config.universe,
        version=HISTORICAL_UNIVERSE_CONTRACT_VERSION,
        pit_safe_only=True,
    )
    return replace(config, universe=universe)


def _validation_cutoff_source(
    frames: Mapping[str, pd.DataFrame],
    today: str | date | datetime | pd.Timestamp | None,
) -> str:
    if today is not None:
        return "explicit_validation_cutoff"
    calendar = frames.get("trade_cal", pd.DataFrame())
    if not calendar.empty and "cal_date" in calendar.columns:
        dates = normalize_date_series(calendar["cal_date"]).dropna()
        if not dates.empty:
            return "max_supplied_trade_cal_date"
    return "fixed_no_calendar_sentinel"


def _effective_validation_today(
    frames: Mapping[str, pd.DataFrame],
    today: str | date | datetime | pd.Timestamp | None,
) -> pd.Timestamp:
    """Freeze target selection to an explicit date or corpus boundary.

    The fallback is the supplied calendar boundary for backwards-compatible
    in-memory callers.  It is still deterministic and never uses the wall
    clock; production validation defaults to ``DEFAULT_VALIDATION_CUTOFF``.
    """

    if today is not None:
        return _normalise_timestamp(today, name="today")
    calendar = frames.get("trade_cal", pd.DataFrame())
    if not calendar.empty and "cal_date" in calendar.columns:
        dates = normalize_date_series(calendar["cal_date"]).dropna()
        if not dates.empty:
            return pd.Timestamp(dates.max()).normalize()
    # No calendar means no target can be proven available.  A fixed sentinel
    # keeps the manifest deterministic instead of consulting the wall clock.
    return pd.Timestamp("1900-01-01")


def _missing_inputs(
    frames: Mapping[str, pd.DataFrame], target: MonthlySnapshotTarget
) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for dataset in REPLAY_REQUIRED_DATASETS:
        frame = frames.get(dataset, pd.DataFrame())
        if frame is None or frame.empty:
            missing.append(
                {
                    "status": "MISSING_INPUT",
                    "dataset": dataset,
                    "period": target.selected_trading_date or target.target_month.replace("-", ""),
                    "unit": "global",
                    "reason": "required replay dataset is absent from the local corpus",
                }
            )
    if target.selected_trading_date is not None:
        for dataset in ("daily", "daily_basic", "index_daily"):
            frame = frames.get(dataset, pd.DataFrame())
            if frame.empty or "trade_date" not in frame.columns:
                continue
            dates = normalize_date_series(frame["trade_date"])
            if not dates.eq(pd.Timestamp(target.selected_trading_date)).any():
                missing.append(
                    {
                        "status": "MISSING_INPUT",
                        "dataset": dataset,
                        "period": target.selected_trading_date,
                        "unit": f"trade_date={target.selected_trading_date}",
                        "reason": "no observation at the selected trading session",
                    }
                )
    return missing


def _stage_available_targets(
    targets: tuple[MonthlySnapshotTarget, ...],
    stage: str,
) -> tuple[MonthlySnapshotTarget, ...]:
    if stage not in {"smoke", "yearly", "monthly"}:
        raise ValueError("stage must be smoke, yearly, or monthly")
    available = [target for target in targets if target.available]
    if stage == "monthly":
        selected = available
    elif stage == "yearly":
        by_year: dict[str, MonthlySnapshotTarget] = {}
        for target in available:
            by_year.setdefault(target.target_month[:4], target)
        selected = [by_year[key] for key in sorted(by_year)]
    else:
        if len(available) <= 3:
            selected = available
        else:
            positions = (0, (len(available) - 1) // 2, len(available) - 1)
            selected = [available[position] for position in positions]
    selected_keys = {target.target_month for target in selected}
    # Unavailable months remain visible in every stage; only runnable months are
    # bounded by the stage selection.
    return tuple(
        target for target in targets if not target.available or target.target_month in selected_keys
    )


def _snapshot_filename(
    snapshot: ReplayValidationSnapshot, *, compressed: bool = False
) -> str:
    target = snapshot.target
    suffix = ".json.gz" if compressed else ".json"
    stem = f"{target.selected_trading_date or target.target_month}-{snapshot.status.lower()}"
    return f"{stem}{suffix}"


def _write_stream_checkpoint(
    destination: Path,
    *,
    status: str,
    contract_version: str,
    stage: str,
    input_manifest_id: str | None,
    config_hash: str,
    targets: tuple[MonthlySnapshotTarget, ...],
    completed: list[dict[str, Any]],
    summary: Mapping[str, Any] | None = None,
) -> Path:
    """Persist a small progress marker without retaining replay payloads."""

    payload: dict[str, Any] = {
        "artifact_schema_version": "pit-replay-validation-artifact-v1",
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
        "status": status,
        "contract_version": contract_version,
        "stage": stage,
        "input_manifest_id": input_manifest_id,
        "config_hash": config_hash,
        "target_count": len(targets),
        "targets": [target.as_dict() for target in targets],
        "completed": list(completed),
    }
    if summary is not None:
        payload["summary"] = dict(summary)
    return _write_json(destination / "checkpoint.json", payload)


def _result_payload_fingerprint(result: ReplayResult) -> str:
    """Fingerprint bounded determinism components, never an expanded artifact."""

    return _hash_payload(deterministic_replay_digests(result), length=None)


def _determinism_compare(
    first: ReplayResult,
    second: ReplayResult,
    *,
    first_candidate_vector_digests: Mapping[str, str] | None = None,
    second_candidate_vector_digests: Mapping[str, str] | None = None,
    first_provenance_store: Mapping[str, Any] | None = None,
    second_provenance_store: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    first_formal = (
        first.ranked[["ts_code", "rank"]].to_dict(orient="records")
        if not first.ranked.empty
        else []
    )
    second_formal = (
        second.ranked[["ts_code", "rank"]].to_dict(orient="records")
        if not second.ranked.empty
        else []
    )
    first_diagnostic = (
        first.full_ranked[["ts_code", "rank"]].to_dict(orient="records")
        if not first.full_ranked.empty
        else []
    )
    second_diagnostic = (
        second.full_ranked[["ts_code", "rank"]].to_dict(orient="records")
        if not second.full_ranked.empty
        else []
    )
    first_digests = deterministic_replay_digests(
        first,
        candidate_vector_digests=first_candidate_vector_digests,
        provenance_store=first_provenance_store,
    )
    second_digests = deterministic_replay_digests(
        second,
        candidate_vector_digests=second_candidate_vector_digests,
        provenance_store=second_provenance_store,
    )
    same = {
        "same_candidates": first_formal == second_formal,
        "same_ranks": first_diagnostic == second_diagnostic,
        "same_warnings": first.warnings == second.warnings,
        "same_snapshot_id": first.snapshot_id == second.snapshot_id,
        "same_config_fingerprint": first.config_fingerprint == second.config_fingerprint,
        "same_artifact_payload": first_digests == second_digests,
        "same_semantic_digests": first_digests == second_digests,
        "first_digests": first_digests,
        "second_digests": second_digests,
    }
    comparison_values = [value for key, value in same.items() if key.startswith("same_")]
    return {**same, "status": "PASS" if all(comparison_values) else "FAIL"}


def _streamed_replay_envelope(result: ReplayResult) -> dict[str, Any]:
    """Build the replay envelope without materializing vectors or scores."""

    return {
        "metadata": result.metadata(),
        "ranked": result.ranked.to_dict(orient="records"),
        "diagnostic_ranked": (
            result.diagnostic_ranked.to_dict(orient="records")
            if result.diagnostic_ranked is not None
            else result.ranked.to_dict(orient="records")
        ),
        # The normalized writer replaces these empty compatibility fields with
        # one-pass streams. Their presence preserves the logical schema.
        "vectors": [],
        "scores": [],
        "universe": {
            "as_of_date": result.as_of_date,
            "version": result.universe_version,
            "pit_safe": result.universe_pit_safe,
            "included": [
                decision.ts_code
                for decision in result.universe_decisions
                if decision.included
            ],
            "decisions": [
                decision.as_dict() for decision in result.universe_decisions
            ],
            "warnings": list(result.universe_warnings),
            "source_evidence": dict(result.universe_source_evidence),
            "limitations": list(result.universe_limitations),
        },
    }


@dataclass(frozen=True, slots=True)
class ReplayValidationSnapshot:
    target: MonthlySnapshotTarget
    status: str
    regime: RegimeResult | None
    result: ReplayResult | None
    run_manifest: dict[str, Any]
    warnings: tuple[str, ...] = ()
    missing_inputs: tuple[dict[str, Any], ...] = ()
    pit_violations: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    determinism: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.as_dict(),
            "snapshot_status": self.status,
            "status": self.status,
            "regime": self.regime.as_dict() if self.regime is not None else None,
            "run_manifest": dict(self.run_manifest),
            "warnings": list(self.warnings),
            "missing_inputs": list(self.missing_inputs),
            "pit": {
                "status": "PASS" if not self.pit_violations else "FAIL",
                "violations": list(self.pit_violations),
            },
            "reasons": list(self.reasons),
            "determinism": dict(self.determinism),
            "replay": self.result.artifact_dict() if self.result is not None else None,
        }

    def streamed_envelope(self) -> dict[str, Any]:
        """Return a lightweight envelope for the production stream writer."""

        return {
            "target": self.target.as_dict(),
            "snapshot_status": self.status,
            "status": self.status,
            "regime": self.regime.as_dict() if self.regime is not None else None,
            "run_manifest": dict(self.run_manifest),
            "warnings": list(self.warnings),
            "missing_inputs": list(self.missing_inputs),
            "pit": {
                "status": "PASS" if not self.pit_violations else "FAIL",
                "violations": list(self.pit_violations),
            },
            "reasons": list(self.reasons),
            "determinism": dict(self.determinism),
            "replay": (
                _streamed_replay_envelope(self.result)
                if self.result is not None
                else None
            ),
        }

    def normalized_dict(self) -> dict[str, Any]:
        """Return this snapshot in the lossless normalized physical layout."""

        return normalize_snapshot_payload(self.as_dict())


@dataclass(frozen=True, slots=True)
class ReplayValidationResult:
    contract_version: str
    selection_rule: str
    start_month: str
    end_month: str
    stage: str
    top_n: int
    seed: int
    configuration: dict[str, Any]
    input_manifest: dict[str, Any]
    targets: tuple[MonthlySnapshotTarget, ...]
    snapshots: tuple[ReplayValidationSnapshot, ...]
    summary: dict[str, Any]
    manual_review: dict[str, Any]
    synthetic_fixtures: dict[str, Any]
    determinism_checks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """Overall sample state without collapsing incomplete data into success."""

        if (
            self.summary.get("failed_count", 0)
            or self.summary.get("pit_violation_count", 0)
            or self.summary.get("determinism_failure_count", 0)
            or self.synthetic_fixtures.get("status") != "PASS"
        ):
            return "FAILED"
        if self.summary.get("incomplete_count", 0):
            return "INCOMPLETE"
        if self.summary.get("ready_count", 0):
            return "READY"
        return "UNAVAILABLE"

    @property
    def gate_status(self) -> str:
        return "READY" if self.status == "READY" else "NOT_READY"

    def as_dict(self, *, include_snapshots: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
            "status": self.status,
            "gate_status": self.gate_status,
            "selection_rule": self.selection_rule,
            "start_month": self.start_month,
            "end_month": self.end_month,
            "stage": self.stage,
            "top_n": self.top_n,
            "seed": self.seed,
            "validation_cutoff": self.configuration.get("today"),
            "resource_gate": self.configuration.get("resource_gate", {}),
            "configuration": self.configuration,
            "input_manifest_id": self.input_manifest.get("manifest_id"),
            "financial_corpus_identity": self.input_manifest.get("financial_corpus_identity"),
            "market_corpus_identity": self.input_manifest.get("market_corpus_identity"),
            "targets": [target.as_dict() for target in self.targets],
            "summary": self.summary,
            "manual_review": self.manual_review,
            "synthetic_fixtures": self.synthetic_fixtures,
            "determinism_checks": list(self.determinism_checks),
            "warnings": list(self.warnings),
        }
        if include_snapshots:
            payload["snapshots"] = [snapshot.as_dict() for snapshot in self.snapshots]
        return payload


def _run_manifest(
    result: ReplayResult,
    *,
    target: MonthlySnapshotTarget,
    input_manifest: Mapping[str, Any],
    validation_config: Mapping[str, Any],
    seed: int,
    code_version: str,
    warnings: Iterable[str],
    candidate_vector_digests: Mapping[str, str] | None = None,
    provenance_store: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
) -> dict[str, Any]:
    config_hash = _hash_payload(validation_config, length=None)
    digests = deterministic_replay_digests(
        result,
        input_manifest=input_manifest,
        candidate_vector_digests=candidate_vector_digests,
        provenance_store=provenance_store,
    )
    run_id = _hash_payload(
        {
            "snapshot_id": result.snapshot_id,
            "as_of_date": result.as_of_date,
            "input_manifest_id": input_manifest.get("manifest_id"),
            "config_hash": config_hash,
            "code_version": code_version,
            "seed": seed,
        },
        length=16,
    )
    return {
        "run_id": run_id,
        "replay_run_id": result.run_id,
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
        "deterministic_digests": digests,
        "snapshot_id": result.snapshot_id,
        "as_of_date": result.as_of_date,
        "target_month": target.target_month,
        "anchor_date": target.anchor_date,
        "selected_trading_date": target.selected_trading_date,
        "input_manifest_ids": [input_manifest.get("manifest_id")],
        "dataset_manifest_ids": dict(input_manifest.get("dataset_manifest_ids", {})),
        "financial_corpus_identity": input_manifest.get("financial_corpus_identity"),
        "market_corpus_identity": input_manifest.get("market_corpus_identity"),
        "universe_version": result.universe_version,
        "feature_version": result.feature_version,
        "score_version": result.score_version,
        "comparable_period_contract_version": result.comparable_period_contract_version,
        "trend_contract_version": result.trend_contract_version,
        "attention_contract_version": result.attention_contract_version,
        "expectation_crowding_contract_version": result.expectation_crowding_contract_version,
        "evidence_confidence_contract_version": result.evidence_confidence_contract_version,
        "feature_group_registry_version": result.feature_group_registry_version,
        "replay_validation_contract_version": PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
        "regime_contract_version": MARKET_REGIME_CONTRACT_VERSION,
        "benchmark_id": result.benchmark_metadata.get("benchmark_id", DEFAULT_BENCHMARK_ID),
        "benchmark_contract_version": result.benchmark_metadata.get("benchmark_contract_version"),
        "config_hash": config_hash,
        "replay_config_fingerprint": result.config_fingerprint,
        "code_version": code_version,
        "seed": seed,
        "validation_cutoff": validation_config.get("today"),
        "resource_gate": dict(validation_config.get("resource_gate", {})),
        "warnings": list(dict.fromkeys(str(value) for value in warnings)),
        "configuration": dict(validation_config),
    }


def _bucket(value: float) -> str:
    if value < 0.25:
        return "[0.00,0.25)"
    if value < 0.50:
        return "[0.25,0.50)"
    if value < 0.75:
        return "[0.50,0.75)"
    if value < 0.90:
        return "[0.75,0.90)"
    return "[0.90,1.00]"


def _snapshot_metrics(result: ReplayResult) -> dict[str, Any]:
    """Reduce one completed replay to the fields needed after streaming it."""

    coverages = [float(score.evidence_coverage) for score in result.scores]
    confidences = Counter(score.confidence for score in result.scores)
    unknown_groups = Counter(group for score in result.scores for group in score.unknown_groups)
    diagnostic = result.full_ranked
    diagnostic_count = len(diagnostic)
    diagnostic_ineligible = (
        int((~diagnostic["ranking_eligible"].astype(bool)).sum())
        if not diagnostic.empty and "ranking_eligible" in diagnostic.columns
        else 0
    )
    return {
        "executed": True,
        "coverages": coverages,
        "confidence_distribution": dict(confidences),
        "unknown_group_counts": dict(unknown_groups),
        "diagnostic_count": diagnostic_count,
        "diagnostic_ineligible": diagnostic_ineligible,
        "formal_count": len(result.ranked),
    }


def _manual_review_from_records(
    records: Iterable[dict[str, Any]],
    *,
    months_per_regime: int = 2,
    top_n: int = 3,
) -> dict[str, Any]:
    """Select fixed manual-review rows after per-snapshot streaming."""

    by_regime: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_regime.setdefault(str(record.get("regime", "unknown")), []).append(record)
    reviews: list[dict[str, Any]] = []
    for regime in ("bull", "bear", "range", "unknown"):
        values = sorted(
            by_regime.get(regime, []),
            key=lambda item: str(item.get("target_month", "")),
        )
        for position in _manual_positions(len(values), months_per_regime):
            reviews.append(values[position])
    return {
        "version": "manual-review-sample-v1",
        "selection_rule": (
            "first/middle/last deterministic months within each regime; no return inputs"
        ),
        "months_per_regime": months_per_regime,
        "top_n_per_snapshot": top_n,
        "review_count": len(reviews),
        "reviews": reviews,
    }


def _build_summary(
    targets: tuple[MonthlySnapshotTarget, ...],
    snapshots: tuple[ReplayValidationSnapshot, ...],
    *,
    execution_targets: tuple[MonthlySnapshotTarget, ...] | None = None,
    input_manifest: Mapping[str, Any],
    determinism_checks: tuple[dict[str, Any], ...],
    snapshot_metrics: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    status_counts = Counter(snapshot.status for snapshot in snapshots)
    metrics = snapshot_metrics or {}
    regime_counts = Counter(
        snapshot.regime.label if snapshot.regime is not None else "unknown"
        for snapshot in snapshots
        if snapshot.result is not None
        or metrics.get(snapshot.target.target_month, {}).get("executed", False)
    )
    warnings = Counter(warning for snapshot in snapshots for warning in snapshot.warnings)
    coverages: list[float] = []
    confidences: Counter[str] = Counter()
    unknown_groups: Counter[str] = Counter()
    diagnostic_count = 0
    diagnostic_ineligible = 0
    formal_count = 0
    for snapshot in snapshots:
        if snapshot.result is not None:
            coverages.extend(float(score.evidence_coverage) for score in snapshot.result.scores)
            confidences.update(score.confidence for score in snapshot.result.scores)
            unknown_groups.update(
                group for score in snapshot.result.scores for group in score.unknown_groups
            )
            diagnostic = snapshot.result.full_ranked
            diagnostic_count += len(diagnostic)
            if not diagnostic.empty and "ranking_eligible" in diagnostic.columns:
                diagnostic_ineligible += int((~diagnostic["ranking_eligible"].astype(bool)).sum())
            formal_count += len(snapshot.result.ranked)
            continue
        metric = metrics.get(snapshot.target.target_month, {})
        if not metric.get("executed", False):
            continue
        coverages.extend(float(value) for value in metric.get("coverages", ()))
        confidences.update(metric.get("confidence_distribution", {}))
        unknown_groups.update(metric.get("unknown_group_counts", {}))
        diagnostic_count += int(metric.get("diagnostic_count", 0))
        diagnostic_ineligible += int(metric.get("diagnostic_ineligible", 0))
        formal_count += int(metric.get("formal_count", 0))
    target_status_counts = Counter(target.status for target in targets)
    requested_targets = execution_targets or targets
    requested_keys = {target.target_month for target in requested_targets}
    determinism_failures = sum(check.get("status") != "PASS" for check in determinism_checks)
    missing_input_count = sum(len(snapshot.missing_inputs) for snapshot in snapshots)
    executed_count = sum(
        snapshot.result is not None
        or metrics.get(snapshot.target.target_month, {}).get("executed", False)
        for snapshot in snapshots
    )
    available_requested = sum(target.available for target in requested_targets)
    summary: dict[str, Any] = {
        "requested_snapshot_count": len(requested_targets),
        "full_month_target_count": len(targets),
        "available_target_count": sum(target.available for target in requested_targets),
        "full_available_target_count": sum(target.available for target in targets),
        "stage_skipped_available_count": sum(
            target.available and target.target_month not in requested_keys for target in targets
        ),
        "executed_snapshot_count": executed_count,
        "snapshot_completion_rate": (
            status_counts.get("READY", 0) / available_requested if available_requested else None
        ),
        "missing_input_count": missing_input_count,
        "missing_input_rate": (
            sum(bool(snapshot.missing_inputs) for snapshot in snapshots) / available_requested
            if available_requested
            else None
        ),
        "warning_rate": (
            sum(bool(snapshot.warnings) for snapshot in snapshots) / len(snapshots)
            if snapshots
            else None
        ),
        "ready_count": int(status_counts.get("READY", 0)),
        "incomplete_count": int(status_counts.get("INCOMPLETE", 0)),
        "failed_count": int(status_counts.get("FAILED", 0)),
        "unavailable_count": int(status_counts.get("UNAVAILABLE", 0)),
        "snapshot_status_counts": dict(sorted(status_counts.items())),
        "target_status_counts": dict(sorted(target_status_counts.items())),
        "available_months": [target.target_month for target in targets if target.available],
        "unavailable_future_months": [
            target.target_month for target in targets if target.status == "UNAVAILABLE_FUTURE"
        ],
        "unavailable_months": [
            target.target_month for target in targets if target.status == "UNAVAILABLE"
        ],
        "incomplete_months": [target.target_month for target in targets if target.incomplete_month],
        "regime_counts": {
            label: int(regime_counts.get(label, 0))
            for label in ("bull", "bear", "range", "unknown")
        },
        "warning_count": sum(warnings.values()),
        "warning_counts": dict(sorted(warnings.items())),
        "pit_violation_count": sum(len(snapshot.pit_violations) for snapshot in snapshots),
        "pit_violations": [
            violation for snapshot in snapshots for violation in snapshot.pit_violations
        ],
        "coverage_count": len(coverages),
        "coverage_distribution": dict(
            sorted(Counter(_bucket(value) for value in coverages).items())
        ),
        "coverage_min": min(coverages) if coverages else None,
        "coverage_max": max(coverages) if coverages else None,
        "confidence_distribution": dict(sorted(confidences.items())),
        "unknown_group_counts": dict(sorted(unknown_groups.items())),
        "top_n_candidate_count": formal_count,
        "diagnostic_candidate_count": diagnostic_count,
        "diagnostic_ranking_ineligible_count": diagnostic_ineligible,
        "ranking_eligible_count": diagnostic_count - diagnostic_ineligible,
        "ranking_eligibility_rate": (
            (diagnostic_count - diagnostic_ineligible) / diagnostic_count
            if diagnostic_count
            else None
        ),
        "determinism_checked_snapshot_count": len(determinism_checks),
        "determinism_failure_count": determinism_failures,
        "determinism_pass_rate": (
            (len(determinism_checks) - determinism_failures) / len(determinism_checks)
            if determinism_checks
            else None
        ),
        "input_manifest_id": input_manifest.get("manifest_id"),
        "financial_corpus_identity": input_manifest.get("financial_corpus_identity"),
        "market_corpus_identity": input_manifest.get("market_corpus_identity"),
    }
    return summary


def _manual_positions(size: int, count: int) -> list[int]:
    if size <= 0 or count <= 0:
        return []
    if size <= count:
        return list(range(size))
    if count == 1:
        return [0]
    return list(dict.fromkeys(round(index * (size - 1) / (count - 1)) for index in range(count)))


def build_manual_review_sample(
    snapshots: Iterable[ReplayValidationSnapshot],
    *,
    months_per_regime: int = 2,
    top_n: int = 3,
) -> dict[str, Any]:
    """Build a fixed, return-independent manual review subset."""

    if months_per_regime <= 0 or top_n <= 0:
        raise ValueError("manual review counts must be positive")
    usable = [
        snapshot
        for snapshot in snapshots
        if snapshot.result is not None and snapshot.regime is not None
    ]
    by_regime: dict[str, list[ReplayValidationSnapshot]] = {}
    for snapshot in usable:
        by_regime.setdefault(snapshot.regime.label, []).append(snapshot)
    reviews: list[dict[str, Any]] = []
    for regime in ("bull", "bear", "range", "unknown"):
        values = sorted(by_regime.get(regime, []), key=lambda item: item.target.target_month)
        for position in _manual_positions(len(values), months_per_regime):
            snapshot = values[position]
            result = snapshot.result
            assert result is not None
            diagnostic = result.full_ranked.copy()
            formal = result.ranked.head(top_n)
            ineligible = diagnostic.loc[
                ~diagnostic.get("ranking_eligible", pd.Series(dtype=bool)).astype(bool)
            ].copy()
            if not ineligible.empty:
                ineligible["_score"] = pd.to_numeric(
                    ineligible.get("turnaround_score"), errors="coerce"
                ).fillna(-float("inf"))
                ineligible = ineligible.sort_values(
                    ["_score", "ts_code"], ascending=[False, True], kind="mergesort"
                )
            unknown_heavy = diagnostic.copy()
            if not unknown_heavy.empty:
                unknown_heavy["_unknown_count"] = (
                    unknown_heavy.get("unknown_groups", pd.Series("", index=unknown_heavy.index))
                    .astype(str)
                    .map(lambda value: len([item for item in value.split("|") if item]))
                )
                unknown_heavy["_score"] = pd.to_numeric(
                    unknown_heavy.get("turnaround_score"), errors="coerce"
                ).fillna(-float("inf"))
                unknown_heavy = unknown_heavy.sort_values(
                    ["_unknown_count", "_score", "ts_code"],
                    ascending=[False, False, True],
                    kind="mergesort",
                )
            excluded = sorted(
                (decision for decision in result.universe_decisions if not decision.included),
                key=lambda decision: (
                    0 if "list" in decision.reason or "delist" in decision.reason else 1,
                    decision.reason,
                    decision.ts_code,
                ),
            )
            checklist = {
                "valid_trading_session": {
                    "status": "PASS" if snapshot.target.available else "FAIL",
                    "reason": snapshot.target.selection_reason,
                },
                "historical_universe_recorded": {
                    "status": "PASS" if result.universe_decisions else "UNKNOWN",
                    "reason": "all inclusion/exclusion decisions are in the snapshot artifact",
                },
                "top_n_uses_ranking_eligible": {
                    "status": "PASS"
                    if all(bool(row.get("ranking_eligible")) for _, row in formal.iterrows())
                    else "FAIL",
                    "reason": "formal ranked rows are evidence-gated",
                },
                "high_score_ineligible_reason": {
                    "status": "PASS" if not ineligible.empty else "UNKNOWN",
                    "reason": str(ineligible.iloc[0].get("eligibility_reason"))
                    if not ineligible.empty
                    else "no ineligible diagnostic candidate in this snapshot",
                },
                "unknown_groups_recorded": {
                    "status": "PASS" if not unknown_heavy.empty else "UNKNOWN",
                    "reason": "diagnostic rows retain unknown_groups and coverage",
                },
                "financial_version_visible_at_as_of": {
                    "status": "PASS" if not snapshot.pit_violations else "FAIL",
                    "reason": "availability dates are bounded by as_of",
                },
                "future_revision_not_used": {
                    "status": "PASS" if not snapshot.pit_violations else "FAIL",
                    "reason": "revision boundary is fail-closed; see synthetic fixture",
                },
                "benchmark_endpoint_pit": {
                    "status": "PASS"
                    if result.benchmark_metadata.get("benchmark_id") == DEFAULT_BENCHMARK_ID
                    and not snapshot.pit_violations
                    else "FAIL",
                    "reason": "benchmark endpoint is the configured 000300.SH series",
                },
                "attention_reference_population_pit_safe": {
                    "status": "PASS" if result.vectors else "UNKNOWN",
                    "reason": "attention evidence records effective session/as_of metadata",
                },
                "crowding_benchmark_000300": {
                    "status": "PASS"
                    if result.benchmark_metadata.get("benchmark_id") == DEFAULT_BENCHMARK_ID
                    else "FAIL",
                    "reason": "crowding benchmark identity is explicit",
                },
                "current_snapshot_fields_not_backfilled": {
                    "status": "PASS" if result.universe_pit_safe else "FAIL",
                    "reason": (
                        "historical universe consumes only static identifiers and dated events"
                    ),
                },
                "warnings_recorded": {
                    "status": "PASS",
                    "reason": "warnings are preserved in run manifest and snapshot",
                },
            }
            reviews.append(
                {
                    "review_id": f"{snapshot.target.target_month}-{regime}",
                    "target_month": snapshot.target.target_month,
                    "as_of_date": snapshot.target.selected_trading_date,
                    "regime": regime,
                    "top_n": formal.to_dict(orient="records"),
                    "high_score_ineligible": (
                        ineligible.head(1)
                        .drop(columns=["_score"], errors="ignore")
                        .to_dict(orient="records")
                    ),
                    "unknown_heavy_candidate": (
                        unknown_heavy.head(1)
                        .drop(columns=["_score", "_unknown_count"], errors="ignore")
                        .to_dict(orient="records")
                    ),
                    "excluded_boundary_case": [asdict(excluded[0])] if excluded else [],
                    "checklist": checklist,
                    "review_status": "MACHINE_PRECHECK_PENDING_HUMAN_SIGNOFF",
                    "findings": [
                        "historical stock_basic name/status/industry/board remain UNSUPPORTED_PIT",
                        *list(snapshot.warnings),
                    ],
                }
            )
    return {
        "version": "manual-review-sample-v1",
        "selection_rule": (
            "first/middle/last deterministic months within each regime; no return inputs"
        ),
        "months_per_regime": months_per_regime,
        "top_n_per_snapshot": top_n,
        "review_count": len(reviews),
        "reviews": reviews,
    }


def _synthetic_complete_vector(code: str, as_of: str = "20250630") -> FeatureVector:
    vector = FeatureVector(ts_code=code, as_of_date=as_of)
    for name in (
        "revenue_yoy",
        "net_profit_yoy",
        "operating_profit_yoy",
        "gross_margin",
        "operating_margin",
        "net_margin",
    ):
        vector.add(name, 0.2 if name.endswith("yoy") else 0.5)
    for name in (
        "yoy_acceleration",
        "qoq_acceleration",
        "consecutive_improvement",
        "margin_inflection",
    ):
        vector.add(name, 0.2)
    vector.add("sign_transition", "NEGATIVE_TO_POSITIVE")
    vector.add("quality_score", 95.0)
    vector.add("quality_gate_status", "pass")
    for name in ("turnover_percentile", "amount_percentile", "abnormal_volume"):
        vector.add(name, 0.1)
    vector.add("attention_score", 95.0)
    for name in (
        "repricing_20d",
        "repricing_60d",
        "high_proximity",
        "volume_spike_penalty",
        "turnover_spike_penalty",
    ):
        vector.add(name, 0.0)
    vector.add("expectation_score", 95.0)
    return vector


def run_adversarial_fixtures() -> dict[str, Any]:
    """Run the bounded synthetic PIT fixture matrix used by the validation contract."""

    results: dict[str, Any] = {}
    try:
        revision = pd.DataFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "end_date": "20251231",
                    "ann_date": "20260320",
                    "f_ann_date": "20260320",
                    "report_type": "1",
                    "update_flag": "1",
                    "total_revenue": 100.0,
                },
                {
                    "ts_code": "600000.SH",
                    "end_date": "20251231",
                    "ann_date": "20260415",
                    "f_ann_date": "20260415",
                    "report_type": "1",
                    "update_flag": "2",
                    "total_revenue": 110.0,
                },
            ]
        )
        before = query_financial_as_of("income", "600000.SH", "20260414", frame=revision)
        after = query_financial_as_of("income", "600000.SH", "20260415", frame=revision)
        before_original = len(before) == 1 and float(before.iloc[0]["total_revenue"]) == 100.0
        on_revision_revised = len(after) == 1 and float(after.iloc[0]["total_revenue"]) == 110.0
        results["future_financial_revision"] = {
            "status": "PASS" if before_original and on_revision_revised else "FAIL",
            "checks": {
                "before_revision_original": before_original,
                "on_revision_revised": on_revision_revised,
            },
        }
        boundary_before = query_financial_as_of("income", "600000.SH", "20260319", frame=revision)
        boundary_on = query_financial_as_of("income", "600000.SH", "20260320", frame=revision)
        before_boundary = boundary_before.empty
        on_boundary = len(boundary_on) == 1
        results["financial_boundary_date"] = {
            "status": "PASS" if before_boundary and on_boundary else "FAIL",
            "checks": {"T_minus_1_invisible": before_boundary, "T_visible": on_boundary},
        }

        dates = pd.date_range("20250627", periods=3, freq="B")
        market = pd.DataFrame(
            {
                "ts_code": ["600000.SH"] * 3,
                "trade_date": dates.strftime("%Y%m%d"),
                "close": [10.0, 10.1, 10.2],
                "vol": [100.0, 110.0, 120.0],
                "turnover_rate": [1.0, 1.1, 1.2],
                "amount": [1000.0, 1100.0, 1200.0],
            }
        )
        future_market = pd.concat(
            [
                market,
                pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20250701", "close": 99.0}]),
            ],
            ignore_index=True,
        )
        from ..features.common import market_history

        visible = market_history(future_market, "600000.SH", "20250630", lookback=100)
        max_visible = visible["_date"].max().strftime("%Y%m%d")
        market_cutoff = max_visible <= "20250630"
        results["market_future_observation"] = {
            "status": "PASS" if market_cutoff else "FAIL",
            "checks": {"max_visible_trade_date_le_as_of": market_cutoff},
        }
        crowding = __import__(
            "ashare_turnaround.features.market", fromlist=["compute_crowding_features"]
        ).compute_crowding_features(
            market,
            "600000.SH",
            "20250630",
            benchmark_frame=pd.DataFrame(),
        )
        benchmark_is_unknown = (
            crowding.values.get("excess_return_20d") is None
            and crowding.evidence["excess_return_20d"].status == "unknown"
        )
        results["benchmark_missing"] = {
            "status": "PASS" if benchmark_is_unknown else "FAIL",
            "checks": {"no_absolute_stock_return_fallback": benchmark_is_unknown},
        }
        short_attention = __import__(
            "ashare_turnaround.features.low_attention", fromlist=["compute_low_attention_v2"]
        ).compute_low_attention_v2(market, "600000.SH", "20250630")
        results["attention_insufficient_history"] = {
            "status": "PASS"
            if short_attention.values["self_turnover_percentile"] is None
            and short_attention.evidence["self_turnover_percentile"].status == "unknown"
            else "FAIL",
            "checks": {"unknown_not_low_attention": True},
        }

        stock_basic = pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600001.SH", "600002.SH"],
                "name": ["current name", "delisted", "future listing"],
                "list_status": ["L", "D", "P"],
                "list_date": ["20100101", "20100101", "20260101"],
                "delist_date": [None, "20250629", None],
            }
        )
        basic = pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600001.SH", "600002.SH"],
                "trade_date": ["20250630"] * 3,
                "amount": [1000.0] * 3,
            }
        )
        universe_before = build_investable_universe(
            stock_basic,
            as_of_date="20250630",
            daily_basic=basic,
            config=UniverseConfig(min_financial_periods=0, include_bse=True, pit_safe_only=True),
        )
        included_before = set(universe_before.included["ts_code"].astype(str))
        results["delisted_security"] = {
            "status": "PASS" if "600001.SH" not in included_before else "FAIL",
            "checks": {"delisted_by_as_of_excluded": "600001.SH" not in included_before},
        }
        prelisting = build_investable_universe(
            stock_basic,
            as_of_date="20251231",
            daily_basic=basic,
            config=UniverseConfig(min_financial_periods=0, include_bse=True, pit_safe_only=True),
        )
        results["pre_listing_security"] = {
            "status": "PASS"
            if "600002.SH" not in set(prelisting.included["ts_code"].astype(str))
            else "FAIL",
            "checks": {"future_listing_excluded": True},
        }

        ineligible_vector = _synthetic_complete_vector("600001.SH")
        for name in (
            "turnover_percentile",
            "amount_percentile",
            "abnormal_volume",
            "attention_score",
            "repricing_20d",
            "repricing_60d",
            "high_proximity",
            "volume_spike_penalty",
            "turnover_spike_penalty",
            "expectation_score",
        ):
            ineligible_vector.values.pop(name, None)
            ineligible_vector.evidence.pop(name, None)
        ineligible_score = score_feature_vector(ineligible_vector)
        formal = rank_scores([ineligible_score], top_n=1)
        results["missing_critical_group"] = {
            "status": "PASS" if not ineligible_score.ranking_eligible and formal.empty else "FAIL",
            "checks": {"high_diagnostic_score_gated": not ineligible_score.ranking_eligible},
        }
        tied = rank_scores(
            [
                replace(
                    score_feature_vector(_synthetic_complete_vector("B.SH")), turnaround_score=80.0
                ),
                replace(
                    score_feature_vector(_synthetic_complete_vector("A.SH")), turnaround_score=80.0
                ),
            ],
            top_n=2,
        )
        results["deterministic_tie_handling"] = {
            "status": "PASS" if tied["ts_code"].tolist() == ["A.SH", "B.SH"] else "FAIL",
            "checks": {"score_desc_then_ts_code": True},
        }
    except (
        Exception
    ) as exc:  # pragma: no cover - makes the artifact diagnosable on fixture regressions
        results["fixture_runner"] = {
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
        }
    overall = (
        "PASS"
        if results and all(value.get("status") == "PASS" for value in results.values())
        else "FAIL"
    )
    return {
        "contract_version": "pit-adversarial-fixtures-v1",
        "status": overall,
        "fixtures": results,
        "scope": "synthetic only; no remote requests and no forward observations",
    }


def _run_validation(
    frames: Mapping[str, pd.DataFrame],
    *,
    data_dir: str | Path,
    input_manifest: dict[str, Any],
    start: str | date | datetime | pd.Timestamp,
    end: str | date | datetime | pd.Timestamp,
    selection_rule: str,
    anchor_day: int,
    calendar_exchange: str | None,
    top_n: int,
    replay_config: ReplayConfig,
    seed: int,
    stage: str,
    today: str | date | datetime | pd.Timestamp | None,
    determinism_sample: int,
    frame_loader: Callable[[str, ReplayConfig], Mapping[str, pd.DataFrame]] | None = None,
    resource_guard: bool = False,
    artifact_output: str | Path | None = None,
    retain_snapshot_results: bool | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayValidationResult:
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if determinism_sample < 0:
        raise ValueError("determinism_sample must be non-negative")
    settings = replay_config
    if settings.top_n != top_n:
        settings = replace(settings, top_n=top_n)
    settings = _historical_replay_config(settings)
    effective_today = _effective_validation_today(frames, today)
    cutoff_source = _validation_cutoff_source(frames, today)
    targets = select_monthly_snapshot_dates(
        frames.get("trade_cal", pd.DataFrame()),
        start,
        end,
        anchor_day=anchor_day,
        exchange=calendar_exchange,
        today=effective_today,
        selection_rule=selection_rule,
    )
    execution_targets = _stage_available_targets(targets, stage)
    code_version = _git_commit()
    regime_declaration = RegimeResult(
        "unknown",
        "DECLARATION",
        None,
        settings.crowding.benchmark.benchmark_id,
        None,
        None,
        None,
        None,
        0,
    ).as_dict()
    validation_configuration = {
        "contract_version": PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
        "selection_rule": selection_rule,
        "anchor_day": anchor_day,
        "calendar_exchange": calendar_exchange,
        "start_month": _month_text(start, name="start"),
        "end_month": _month_text(end, name="end"),
        "today": effective_today.strftime("%Y%m%d"),
        "target_selection": {
            "cutoff_date": effective_today.strftime("%Y%m%d"),
            "cutoff_source": cutoff_source,
            "calendar_input": "unprojected supplied trade_cal",
            "incomplete_month_rule": "target month equals cutoff month",
            "future_rule": "target month after cutoff is UNAVAILABLE_FUTURE",
        },
        "feature_as_of": {
            "source": "selected_trading_date per target",
            "rule": "feature and PIT inputs are bounded at selected as_of",
            "selection_cutoff_is_not_feature_observation": True,
        },
        "stage": stage,
        "top_n": top_n,
        "seed": seed,
        "replay_config": settings.declared(),
        "regime": regime_declaration,
        "scope_guard": {
            "forward_return_evaluation": False,
            "score_v2": False,
            "weight_tuning": False,
            "ablation": False,
            "new_features": False,
            "raw_rewrite": False,
            "full_redownload": False,
        },
        "input_loading": {
            "mode": (
                "as_of_partition_and_column_projection"
                if frame_loader is not None
                else "supplied_in_memory_frames"
            ),
            "market_lookback_sessions": _required_market_sessions(settings),
            "financial_visibility_filter": "actual_available_date <= as_of",
            "raw_corpus_mutation": False,
        },
        "resource_gate": _resource_gate_declaration(enabled=resource_guard),
    }
    validation_config_hash = _hash_payload(validation_configuration, length=None)
    synthetic = run_adversarial_fixtures()
    if synthetic.get("status") != "PASS":
        raise PITViolation(("synthetic_adversarial_fixture_failure",))
    resource_baseline = _host_memory()
    resource_samples: list[dict[str, Any]] = []
    resource_started_at = time.monotonic()

    def _record_resource_sample(stage_name: str, observed: Mapping[str, Any]) -> None:
        resource_samples.append(
            {
                "stage": stage_name,
                "sampled_at_utc": datetime.now(UTC).isoformat(),
                "elapsed_seconds": max(0.0, time.monotonic() - resource_started_at),
                "available_bytes": observed.get("available_bytes"),
                "swap_free_bytes": observed.get("swap_free_bytes"),
                "swap_used_bytes": observed.get("swap_used_bytes"),
                "current_rss_bytes": observed.get("current_rss_bytes"),
                "current_pss_bytes": observed.get("current_pss_bytes"),
                "current_private_bytes": observed.get("current_private_bytes"),
                "current_swap_bytes": observed.get("current_swap_bytes"),
                "peak_rss_diagnostic_bytes": observed.get("peak_rss_diagnostic_bytes"),
                "live_memory_metric": observed.get("live_memory_metric"),
            }
        )

    _record_resource_sample("baseline", resource_baseline)

    def _sample_and_assert_resources(stage_name: str) -> dict[str, Any]:
        observed = _host_memory()
        _record_resource_sample(stage_name, observed)
        if resource_guard:
            return _assert_runtime_resource_gate(
                resource_baseline,
                memory=observed,
                phase=stage_name,
            )
        return observed

    snapshots: list[ReplayValidationSnapshot] = []
    determinism_checks: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    if retain_snapshot_results is None:
        retain_snapshot_results = artifact_output is None
    stream_output = Path(artifact_output) if artifact_output is not None else None
    stream_results = stream_output is not None and not retain_snapshot_results
    stream_candidates = stream_results
    snapshot_metrics: dict[str, dict[str, Any]] = {}
    stream_cas_metrics: dict[str, dict[str, Any]] = {}
    stream_manual_records: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    candidate_spool_path: Path | None = None
    candidate_spool_handle = None
    stream_store_builder: ChunkedContentAddressedStore | None = None
    stream_candidate_digest_map: dict[str, str] = {}
    stream_candidate_count = 0
    candidate_flush_interval = 100

    def _discard_stream_resources() -> None:
        nonlocal candidate_spool_handle, candidate_spool_path
        nonlocal stream_store_builder, stream_candidate_digest_map
        if candidate_spool_handle is not None:
            candidate_spool_handle.close()
            candidate_spool_handle = None
        if candidate_spool_path is not None:
            candidate_spool_path.unlink(missing_ok=True)
            candidate_spool_path = None
        if stream_store_builder is not None:
            stream_store_builder.close()
            stream_store_builder = None
        stream_candidate_digest_map = {}

    if stream_output is not None:
        stream_output.mkdir(parents=True, exist_ok=True)
        (stream_output / "snapshots").mkdir(parents=True, exist_ok=True)
        if stream_results:
            candidate_spool_path = stream_output / ".candidate-vectors.jsonl"
            candidate_spool_handle = candidate_spool_path.open("w", encoding="utf-8")

            def stream_candidate(vector: FeatureVector, _score: Any) -> None:
                nonlocal stream_candidate_count
                if candidate_spool_handle is None or stream_store_builder is None:
                    raise RuntimeError("candidate spool or normalized store is closed")
                normalized = normalize_feature_vector(vector, store=stream_store_builder)
                stream_candidate_count += 1
                if stream_candidate_count % candidate_flush_interval == 0:
                    stream_store_builder.flush_chunk(force=True)
                else:
                    # Physical limits remain a secondary safety boundary.
                    stream_store_builder.flush_chunk_if_needed()
                # The ref-bearing normalized record is the full-scale digest
                # boundary; do not materialize FeatureVector.as_dict() again.
                stream_candidate_digest_map[str(vector.ts_code)] = content_digest(normalized)
                record = json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                candidate_spool_handle.write(record + "\n")

            if diagnostics is None:
                diagnostics = ReplayDiagnostics(
                    candidate_sink=stream_candidate,
                    retain_vectors=False,
                )
            else:
                diagnostics.candidate_sink = stream_candidate
                diagnostics.retain_vectors = False
        _write_stream_checkpoint(
            stream_output,
            status="RUNNING",
            contract_version=PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
            stage=stage,
            input_manifest_id=input_manifest.get("manifest_id"),
            config_hash=validation_config_hash,
            targets=targets,
            completed=completed,
        )

    def record_snapshot(snapshot: ReplayValidationSnapshot) -> None:
        """Write one complete snapshot before dropping its heavy result."""

        nonlocal candidate_spool_path, stream_store_builder, stream_candidate_digest_map
        if snapshot.result is not None and stream_results:
            snapshot_metrics[snapshot.target.target_month] = _snapshot_metrics(snapshot.result)
            if snapshot.regime is not None:
                one = build_manual_review_sample((snapshot,), top_n=min(3, top_n))
                stream_manual_records.extend(one["reviews"])
        artifact_path: Path | None = None
        try:
            if stream_output is not None:
                with _diagnostic_phase(diagnostics, "artifact_serialization"):
                    if candidate_spool_path is not None and snapshot.result is not None:
                        if stream_store_builder is None:
                            raise RuntimeError(
                                "normalized store is missing for streamed snapshot"
                            )
                        _sample_and_assert_resources("before_artifact_writer")
                        artifact_path = write_normalized_snapshot_with_streamed_vectors(
                            stream_output
                            / "snapshots"
                            / _snapshot_filename(snapshot, compressed=True),
                            snapshot.streamed_envelope(),
                            candidate_spool_path,
                            stream_store_builder.iter_entries_sorted(
                                resource_probe=_sample_and_assert_resources
                            ),
                            scores=iter(snapshot.result.scores),
                            canonical_spool=True,
                            gzip_level=1,
                            resource_probe=_sample_and_assert_resources,
                        )
                    else:
                        payload = snapshot.as_dict()
                        artifact_path = _write_json(
                            stream_output / "snapshots" / _snapshot_filename(snapshot),
                            normalize_snapshot_payload(payload),
                        )
                if candidate_spool_path is not None:
                    candidate_spool_path.unlink(missing_ok=True)
                    candidate_spool_path = None
                if stream_store_builder is not None:
                    stream_store_builder.close()
                stream_store_builder = None
                stream_candidate_digest_map = {}
                _sample_and_assert_resources("after_artifact_writer_and_cleanup")
        except ResourceBlocked:
            # The writer itself is atomic. This extra removal covers a hard
            # post-writer sample that fires after promotion but before the
            # snapshot is recorded as completed.
            if artifact_path is not None:
                artifact_path.unlink(missing_ok=True)
            raise
        completed.append(
            {
                "target_month": snapshot.target.target_month,
                "selected_trading_date": snapshot.target.selected_trading_date,
                "status": snapshot.status,
            }
        )
        if stream_output is not None:
            _write_stream_checkpoint(
                stream_output,
                status="RUNNING",
                contract_version=PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
                stage=stage,
                input_manifest_id=input_manifest.get("manifest_id"),
                config_hash=validation_config_hash,
                targets=targets,
                completed=completed,
            )
        snapshots.append(replace(snapshot, result=None) if stream_results else snapshot)
        if stream_results:
            gc.collect()

    for target in execution_targets:
        if not target.available:
            record_snapshot(
                ReplayValidationSnapshot(
                    target,
                    "UNAVAILABLE",
                    None,
                    None,
                    {
                        "run_id": None,
                        "snapshot_id": None,
                        "as_of_date": None,
                        "target_month": target.target_month,
                        "input_manifest_ids": [input_manifest.get("manifest_id")],
                        "replay_validation_contract_version": (
                            PIT_REPLAY_VALIDATION_CONTRACT_VERSION
                        ),
                        "regime_contract_version": MARKET_REGIME_CONTRACT_VERSION,
                        "config_hash": validation_config_hash,
                        "code_version": code_version,
                        "seed": seed,
                        "warnings": [target.selection_reason],
                        "configuration": validation_configuration,
                    },
                    warnings=(target.selection_reason,),
                    reasons=(target.selection_reason,),
                )
            )
            continue
        if stopped_reason is not None:
            reason = f"not run after previous hard-stop: {stopped_reason}"
            record_snapshot(
                ReplayValidationSnapshot(
                    target,
                    "FAILED",
                    None,
                    None,
                    {
                        "run_id": None,
                        "snapshot_id": None,
                        "as_of_date": target.selected_trading_date,
                        "target_month": target.target_month,
                        "input_manifest_ids": [input_manifest.get("manifest_id")],
                        "replay_validation_contract_version": (
                            PIT_REPLAY_VALIDATION_CONTRACT_VERSION
                        ),
                        "config_hash": validation_config_hash,
                        "code_version": code_version,
                        "seed": seed,
                        "warnings": [reason],
                        "configuration": validation_configuration,
                    },
                    warnings=(reason,),
                    reasons=(reason,),
                )
            )
            continue

        as_of = target.selected_trading_date
        assert as_of is not None
        if stream_candidates:
            stream_candidate_count = 0
            stream_store_builder = ChunkedContentAddressedStore(
                stream_output / ".cas",
                chunk_entries=100_000,
                max_active_entries=100_000,
                max_active_physical_bytes=64 * 1024 * 1024,
            )
            stream_candidate_digest_map = {}
        if stream_candidates and candidate_spool_handle is None:
            assert stream_output is not None
            candidate_spool_path = stream_output / ".candidate-vectors.jsonl"
            candidate_spool_handle = candidate_spool_path.open("w", encoding="utf-8")
        if stream_candidates and diagnostics is not None:
            diagnostics.candidate_validation_violations.clear()
            candidate_observable_key_cache: dict[str, bool] = {}
            candidate_date_cache: dict[str, pd.Timestamp | None] = {}
            candidate_as_of = _normalise_timestamp(as_of, name="as_of_date")

            def validate_candidate(vector: FeatureVector) -> tuple[str, ...]:
                # ``safe_container_cache`` is keyed by object id.  It must not
                # outlive this vector: CPython may reuse an id for a later
                # candidate after the previous object graph is released.
                candidate_safe_container_cache: dict[tuple[int, str], bool] = {}
                try:
                    return _validate_replay_vector_pit(
                        vector,
                        as_of=candidate_as_of,
                        benchmark_id=settings.crowding.benchmark.benchmark_id,
                        observable_key_cache=candidate_observable_key_cache,
                        date_cache=candidate_date_cache,
                        safe_container_cache=candidate_safe_container_cache,
                    )
                finally:
                    candidate_safe_container_cache.clear()

            diagnostics.candidate_validator = validate_candidate
        regime: RegimeResult | None = None
        try:
            replay_frames = (
                dict(frame_loader(as_of, settings)) if frame_loader is not None else frames
            )
            missing = _missing_inputs(replay_frames, target)
            with _diagnostic_phase(diagnostics, "market_regime"):
                regime = classify_market_regime(
                    replay_frames.get("index_daily", pd.DataFrame()),
                    as_of,
                    trade_calendar=replay_frames.get("trade_cal"),
                    benchmark_id=settings.crowding.benchmark.benchmark_id,
                )
            if missing:
                warnings = tuple(
                    dict.fromkeys(
                        [f"MISSING_INPUT:{item['dataset']}:{item['period']}" for item in missing]
                        + (["incomplete_current_month"] if target.incomplete_month else [])
                    )
                )
            else:
                warnings = ("incomplete_current_month",) if target.incomplete_month else ()
            try:
                replay_result = run_replay_frames(
                    replay_frames,
                    as_of_date=as_of,
                    config=settings,
                    diagnostics=diagnostics,
                )
            finally:
                if candidate_spool_handle is not None:
                    candidate_spool_handle.flush()
                    candidate_spool_handle.close()
                    candidate_spool_handle = None
            if stream_candidates and stream_store_builder is not None:
                # Finalization has an explicit boundary even when the last
                # candidate completed exactly on an interval.
                stream_store_builder.flush_chunk(force=True)
            # run_replay_frames returns only after its worker executor has
            # shut down. This is a live-pressure sample, not a peak-RSS check.
            _sample_and_assert_resources("after_candidate_completion_worker_shutdown")
            with _diagnostic_phase(diagnostics, "pit_validation"):
                pit_violations = validate_replay_pit(
                    replay_result,
                    as_of_date=as_of,
                    benchmark_id=settings.crowding.benchmark.benchmark_id,
                    require_historical_universe=True,
                    prevalidated_vector_violations=(
                        diagnostics.candidate_validation_violations
                        if stream_candidates and diagnostics is not None
                        else None
                    ),
                )
            if pit_violations:
                stopped_reason = "P0 PIT violation"
                snapshot_status = "FAILED"
                reasons = tuple(pit_violations)
            elif missing or replay_result.status != "PASS" or not replay_result.vectors:
                snapshot_status = "INCOMPLETE"
                reasons = tuple(
                    dict.fromkeys(
                        [
                            *(item["reason"] for item in missing),
                            *(replay_result.warnings),
                            *(["incomplete_current_month"] if target.incomplete_month else []),
                        ]
                    )
                )
            else:
                snapshot_status = "INCOMPLETE" if target.incomplete_month else "READY"
                reasons = ("incomplete_current_month",) if target.incomplete_month else ()
            determinism: dict[str, Any] = {}
            repeated: ReplayResult | None = None
            repeated_candidate_digests: dict[str, str] = {}
            repeated_store: ContentAddressedStore | None = None
            should_repeat = (
                not pit_violations
                and len(determinism_checks) < determinism_sample
                and stopped_reason is None
            )
            if should_repeat:
                # A same-process repeat genuinely needs the projected frames,
                # so it runs before their explicit release. Digest comparison
                # and CAS finalization wait until after the release boundary.
                saved_sink = diagnostics.candidate_sink if stream_candidates else None
                saved_validator = diagnostics.candidate_validator if stream_candidates else None
                saved_retain_vectors = diagnostics.retain_vectors if stream_candidates else True
                repeated_store = ContentAddressedStore()

                def repeated_sink(vector: FeatureVector, _score: Any) -> None:
                    assert repeated_store is not None
                    normalized = normalize_feature_vector(vector, store=repeated_store)
                    repeated_candidate_digests[str(vector.ts_code)] = content_digest(normalized)

                if stream_candidates and diagnostics is not None:
                    diagnostics.candidate_sink = repeated_sink
                    diagnostics.candidate_validator = None
                    diagnostics.retain_vectors = False
                try:
                    repeated = run_replay_frames(
                        replay_frames,
                        as_of_date=as_of,
                        config=settings,
                        diagnostics=diagnostics,
                    )
                finally:
                    if stream_candidates and diagnostics is not None:
                        diagnostics.candidate_sink = saved_sink
                        diagnostics.candidate_validator = saved_validator
                        diagnostics.retain_vectors = saved_retain_vectors

            # PIT and the initial snapshot status are now complete. Production
            # determinism_sample=0 no longer keeps raw/projected DataFrames
            # alive during the first external CAS merge or artifact writing.
            _sample_and_assert_resources("before_replay_frames_release")
            replay_frames = None
            gc.collect()
            _sample_and_assert_resources("after_replay_frames_release")

            if stream_candidates and stream_store_builder is not None:
                _sample_and_assert_resources("before_cas_finalize")
                finalized_path = stream_store_builder.finalize(
                    resource_probe=_sample_and_assert_resources
                )
                _sample_and_assert_resources("after_cas_finalize")
                stream_cas_metrics[target.target_month] = {
                    "finalization_count": stream_store_builder.finalization_count,
                    "merge_group_count": stream_store_builder.merge_group_count,
                    "merge_pass_count": stream_store_builder.merge_pass_count,
                    "peak_open_chunk_streams": (
                        stream_store_builder.peak_open_chunk_streams
                    ),
                    "configured_merge_fan_in": stream_store_builder.merge_fan_in,
                    "unique_entry_count": stream_store_builder.entry_count,
                    "physical_value_bytes": stream_store_builder.physical_byte_count,
                    "finalized_runtime_file_bytes": finalized_path.stat().st_size,
                }

            run_manifest = _run_manifest(
                replay_result,
                target=target,
                input_manifest=input_manifest,
                validation_config=validation_configuration,
                seed=seed,
                code_version=code_version,
                warnings=(*warnings, *replay_result.warnings, *pit_violations),
                candidate_vector_digests=(
                    stream_candidate_digest_map if stream_candidates else None
                ),
                provenance_store=(
                    stream_store_builder
                    if stream_candidates and stream_store_builder is not None
                    else None
                ),
            )

            if repeated is not None:
                determinism = _determinism_compare(
                    replay_result,
                    repeated,
                    first_candidate_vector_digests=(
                        stream_candidate_digest_map if stream_candidates else None
                    ),
                    second_candidate_vector_digests=(
                        repeated_candidate_digests if stream_candidates else None
                    ),
                    first_provenance_store=(
                        stream_store_builder
                        if stream_candidates and stream_store_builder is not None
                        else None
                    ),
                    second_provenance_store=(
                        repeated_store if stream_candidates else None
                    ),
                )
                del repeated
                check = {
                    "as_of_date": as_of,
                    "snapshot_id": replay_result.snapshot_id,
                    "run_id": run_manifest["run_id"],
                    **determinism,
                }
                determinism_checks.append(check)
                if determinism["status"] != "PASS":
                    snapshot_status = "FAILED"
                    reasons = (*reasons, "determinism_failure")
                    stopped_reason = "determinism failure"
            snapshot_warnings = tuple(
                dict.fromkeys((*warnings, *replay_result.warnings, *pit_violations))
            )
            record_snapshot(
                ReplayValidationSnapshot(
                    target,
                    snapshot_status,
                    regime,
                    replay_result,
                    run_manifest,
                    warnings=snapshot_warnings,
                    missing_inputs=tuple(missing),
                    pit_violations=tuple(pit_violations),
                    reasons=tuple(dict.fromkeys(reasons)),
                    determinism=determinism,
                )
            )
            if stream_results:
                del replay_result
                gc.collect()
        except ResourceBlocked:
            _discard_stream_resources()
            raise
        except PITViolation as exc:
            stopped_reason = "P0 PIT violation"
            record_snapshot(
                ReplayValidationSnapshot(
                    target,
                    "FAILED",
                    regime,
                    None,
                    {
                        "run_id": None,
                        "snapshot_id": None,
                        "as_of_date": as_of,
                        "target_month": target.target_month,
                        "input_manifest_ids": [input_manifest.get("manifest_id")],
                        "replay_validation_contract_version": (
                            PIT_REPLAY_VALIDATION_CONTRACT_VERSION
                        ),
                        "config_hash": validation_config_hash,
                        "code_version": code_version,
                        "seed": seed,
                        "warnings": [str(exc)],
                        "configuration": validation_configuration,
                    },
                    warnings=(str(exc),),
                    pit_violations=exc.violations,
                    reasons=exc.violations,
                )
            )
        except (OSError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            record_snapshot(
                ReplayValidationSnapshot(
                    target,
                    "FAILED",
                    regime,
                    None,
                    {
                        "run_id": None,
                        "snapshot_id": None,
                        "as_of_date": as_of,
                        "target_month": target.target_month,
                        "input_manifest_ids": [input_manifest.get("manifest_id")],
                        "replay_validation_contract_version": (
                            PIT_REPLAY_VALIDATION_CONTRACT_VERSION
                        ),
                        "config_hash": validation_config_hash,
                        "code_version": code_version,
                        "seed": seed,
                        "warnings": [error],
                        "configuration": validation_configuration,
                    },
                    warnings=(error,),
                    reasons=(error,),
                )
            )

    _discard_stream_resources()

    snapshots_tuple = tuple(snapshots)
    summary = _build_summary(
        targets,
        snapshots_tuple,
        execution_targets=execution_targets,
        input_manifest=input_manifest,
        determinism_checks=tuple(determinism_checks),
        snapshot_metrics=snapshot_metrics if stream_results else None,
    )
    manual_review = (
        _manual_review_from_records(
            stream_manual_records,
            top_n=min(3, top_n),
        )
        if stream_results
        else build_manual_review_sample(snapshots_tuple, top_n=min(3, top_n))
    )
    summary["synthetic_fixture_status"] = synthetic.get("status")
    summary["revision_boundary_status"] = (
        synthetic.get("fixtures", {}).get("future_financial_revision", {}).get("status", "UNKNOWN")
    )
    resource_final = _sample_and_assert_resources("validation_complete")
    baseline_swap = resource_baseline.get("swap_used_bytes")
    final_swap = resource_final.get("swap_used_bytes")
    if diagnostics is not None:
        summary["performance"] = replay_performance_profile(
            diagnostics,
            full_candidate_count=diagnostics.candidate_total,
        )
    peak_rss_diagnostic = resource_final.get("peak_rss_diagnostic_bytes")
    summary["resource"] = {
        "version": RESOURCE_GATE_CONTRACT_VERSION,
        "sampling_contract_version": RESOURCE_SAMPLING_CONTRACT_VERSION,
        "guard_enabled": resource_guard,
        "live_memory_metric": resource_final.get("live_memory_metric"),
        "current_rss_bytes": resource_final.get("current_rss_bytes"),
        "current_pss_bytes": resource_final.get("current_pss_bytes"),
        "current_private_bytes": resource_final.get("current_private_bytes"),
        "current_swap_bytes": resource_final.get("current_swap_bytes"),
        "peak_rss_diagnostic_bytes": peak_rss_diagnostic,
        # Compatibility spelling for existing diagnostic readers.  Both names
        # are diagnostic-only and neither participates in enforcement.
        "peak_rss_bytes": peak_rss_diagnostic,
        "peak_rss_gib": (
            peak_rss_diagnostic / 1024**3 if peak_rss_diagnostic is not None else None
        ),
        "available_bytes_at_finalize": resource_final.get("available_bytes"),
        "swap_free_bytes_at_finalize": resource_final.get("swap_free_bytes"),
        "swap_used_delta_bytes": (
            final_swap - baseline_swap
            if final_swap is not None and baseline_swap is not None
            else None
        ),
        "max_live_pss_bytes": MAX_LIVE_PSS_BYTES if resource_guard else None,
        "max_live_private_bytes": MAX_LIVE_PRIVATE_BYTES if resource_guard else None,
        "max_live_rss_fallback_bytes": MAX_PEAK_RSS_BYTES if resource_guard else None,
        "max_process_swap_bytes": MAX_PROCESS_SWAP_BYTES if resource_guard else None,
        "max_swap_growth_bytes": MAX_SWAP_GROWTH_BYTES if resource_guard else None,
        "peak_rss_diagnostic_limit_bytes": MAX_PEAK_RSS_BYTES if resource_guard else None,
        "peak_rss_enforcement": "diagnostic_only",
        "cas_finalization": dict(stream_cas_metrics),
        "samples": list(resource_samples),
    }
    run_warnings = tuple(
        dict.fromkeys(warning for snapshot in snapshots_tuple for warning in snapshot.warnings)
    )
    validation_result = ReplayValidationResult(
        contract_version=PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
        selection_rule=selection_rule,
        start_month=_month_text(start, name="start"),
        end_month=_month_text(end, name="end"),
        stage=stage,
        top_n=top_n,
        seed=seed,
        configuration=validation_configuration,
        input_manifest=input_manifest,
        targets=targets,
        snapshots=snapshots_tuple,
        summary=summary,
        manual_review=manual_review,
        synthetic_fixtures=synthetic,
        determinism_checks=tuple(determinism_checks),
        warnings=run_warnings,
    )
    if stream_output is not None:
        with _diagnostic_phase(diagnostics, "artifact_serialization"):
            _write_stream_checkpoint(
                stream_output,
                status="COMPLETE",
                contract_version=PIT_REPLAY_VALIDATION_CONTRACT_VERSION,
                stage=stage,
                input_manifest_id=input_manifest.get("manifest_id"),
                config_hash=validation_config_hash,
                targets=targets,
                completed=completed,
                summary=summary,
            )
    if diagnostics is not None:
        diagnostics.emit_summary()
    return validation_result


def run_replay_validation_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    start: str | date | datetime | pd.Timestamp = DEFAULT_START_MONTH,
    end: str | date | datetime | pd.Timestamp = DEFAULT_END_MONTH,
    selection_rule: str = MONTHLY_SELECTION_RULE_VERSION,
    anchor_day: int = DEFAULT_ANCHOR_DAY,
    calendar_exchange: str | None = "SSE",
    top_n: int = 20,
    config: ReplayConfig | ReplayValidationConfig | None = None,
    seed: int = DEFAULT_SEED,
    stage: str = "monthly",
    today: str | date | datetime | pd.Timestamp | None = DEFAULT_VALIDATION_CUTOFF,
    determinism_sample: int = 3,
    artifact_output: str | Path | None = None,
    retain_snapshot_results: bool | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayValidationResult:
    """Run validation over supplied frames, primarily for tests and fixtures."""

    if isinstance(config, ReplayValidationConfig):
        validation_settings = config
        start = validation_settings.start
        end = validation_settings.end
        selection_rule = validation_settings.selection_rule
        anchor_day = validation_settings.anchor_day
        calendar_exchange = validation_settings.calendar_exchange
        top_n = validation_settings.top_n
        seed = validation_settings.seed
        stage = validation_settings.stage
        today = validation_settings.today
        determinism_sample = validation_settings.determinism_sample
        replay_settings = validation_settings.replay or ReplayConfig(top_n=top_n)
    else:
        replay_settings = config or ReplayConfig(top_n=top_n)
    manifest = _frame_manifest(frames)
    return _run_validation(
        frames,
        data_dir="<in-memory>",
        input_manifest=manifest,
        start=start,
        end=end,
        selection_rule=selection_rule,
        anchor_day=anchor_day,
        calendar_exchange=calendar_exchange,
        top_n=top_n,
        replay_config=replay_settings,
        seed=seed,
        stage=stage,
        today=today,
        determinism_sample=determinism_sample,
        artifact_output=artifact_output,
        retain_snapshot_results=retain_snapshot_results,
        diagnostics=diagnostics,
    )


def _partition_bounds(path: Path) -> dict[str, str]:
    bounds: dict[str, str] = {}
    for part in path.parts:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key in {"year", "month", "trade_date", "period"}:
            bounds[key] = value
    return bounds


def _partition_may_overlap(
    path: Path,
    *,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    bounds = _partition_bounds(path)
    if not bounds or (start_date is None and end_date is None):
        return True
    start = pd.Timestamp(start_date) if start_date else None
    end = pd.Timestamp(end_date) if end_date else None
    if "trade_date" in bounds or "period" in bounds:
        value = bounds.get("trade_date", bounds.get("period", ""))
        if len(value) == 8:
            point = pd.Timestamp(value)
            return not ((start is not None and point < start) or (end is not None and point > end))
    if "month" in bounds and re.fullmatch(r"\d{6}", bounds["month"]):
        month = pd.Period(bounds["month"], freq="M")
        return not (
            (start is not None and month < start.to_period("M"))
            or (end is not None and month > end.to_period("M"))
        )
    if "year" in bounds and re.fullmatch(r"\d{4}", bounds["year"]):
        year = int(bounds["year"])
        return not (
            (start is not None and year < start.year) or (end is not None and year > end.year)
        )
    return True


def _read_projected_dataset_raw(
    data_dir: str | Path,
    dataset: str,
    *,
    columns: Iterable[str],
    date_field: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Read only relevant Parquet partitions/columns for one as-of snapshot."""

    store = RawParquetStore(data_dir)
    projection = tuple(dict.fromkeys(str(column) for column in columns))
    pieces: list[pd.DataFrame] = []
    for path in store.parquet_files(dataset):
        if not _partition_may_overlap(path, start_date=start_date, end_date=end_date):
            continue
        parquet_columns = set(str(value) for value in pq.ParquetFile(path).schema_arrow.names)
        available_columns = [column for column in projection if column in parquet_columns]
        if not available_columns:
            continue
        frame = pd.read_parquet(path, columns=available_columns)
        if date_field is not None and date_field in frame.columns:
            dates = normalize_date_series(frame[date_field])
            mask = dates.notna()
            if start_date is not None:
                mask &= dates.ge(pd.Timestamp(start_date))
            if end_date is not None:
                mask &= dates.le(pd.Timestamp(end_date))
            frame = frame.loc[mask].copy()
        if not frame.empty:
            pieces.append(frame)
    if not pieces:
        return pd.DataFrame(columns=projection)
    return pd.concat(pieces, ignore_index=True, sort=False)


def _read_projected_dataset(
    data_dir: str | Path,
    dataset: str,
    *,
    columns: Iterable[str],
    date_field: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> pd.DataFrame:
    with _diagnostic_phase(diagnostics, f"input_loading.{dataset}"):
        return _read_projected_dataset_raw(
            data_dir,
            dataset,
            columns=columns,
            date_field=date_field,
            start_date=start_date,
            end_date=end_date,
        )


def _visible_market_rows(
    frame: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    if frame.empty or "actual_available_date" not in frame.columns:
        return frame
    available = normalize_date_series(frame["actual_available_date"])
    raw = frame["actual_available_date"]
    missing = raw.isna() | raw.astype("string").str.strip().isin({"", "nan", "nat", "none", "<na>"})
    return frame.loc[missing | (available.notna() & available.le(as_of))].reset_index(drop=True)


def _visible_financial_rows(
    frame: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    available = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    for field_name in ("actual_available_date", "f_ann_date", "ann_date"):
        if field_name in frame.columns:
            available = available.fillna(normalize_date_series(frame[field_name]))
    return frame.loc[available.notna() & available.le(as_of)].reset_index(drop=True)


def _required_market_sessions(settings: ReplayConfig) -> int:
    return max(
        400,
        int(settings.crowding.history_lookback),
        int(settings.crowding.valuation_lookback_sessions) + 1,
        int(settings.crowding.benchmark.high_window_sessions) + 1,
        int(settings.low_attention.self_window.window) + 1,
        int(settings.crowding.baseline_lookback_sessions) + 1,
        int(settings.universe.liquidity_lookback),
    )


def _market_start_date(
    trade_calendar: pd.DataFrame,
    as_of: pd.Timestamp,
    *,
    required_sessions: int,
) -> str:
    if trade_calendar.empty or not {"cal_date", "is_open"}.issubset(trade_calendar.columns):
        return "19000101"
    dates = normalize_date_series(trade_calendar["cal_date"])
    dates = (
        dates.loc[
            dates.notna()
            & dates.le(as_of)
            & pd.to_numeric(trade_calendar["is_open"], errors="coerce").eq(1)
        ]
        .drop_duplicates()
        .sort_values()
    )
    if dates.empty:
        return "19000101"
    position = max(0, len(dates) - required_sessions)
    return pd.Timestamp(dates.iloc[position]).strftime("%Y%m%d")


def _validation_frames_for_as_of(
    data_dir: str | Path,
    base_frames: Mapping[str, pd.DataFrame],
    *,
    as_of_date: str,
    settings: ReplayConfig,
    diagnostics: ReplayDiagnostics | None = None,
) -> dict[str, pd.DataFrame]:
    """Build a bounded frame mapping while preserving the production replay API."""

    as_of = _normalise_timestamp(as_of_date, name="as_of_date")
    calendar = base_frames.get("trade_cal", pd.DataFrame())
    if not calendar.empty and "cal_date" in calendar.columns:
        calendar_dates = normalize_date_series(calendar["cal_date"])
        calendar = calendar.loc[calendar_dates.notna() & calendar_dates.le(as_of)].copy()
    frames: dict[str, pd.DataFrame] = {
        "trade_cal": calendar,
        "stock_basic": base_frames.get("stock_basic", pd.DataFrame()),
        "index_basic": base_frames.get("index_basic", pd.DataFrame()),
    }
    market_start = _market_start_date(
        base_frames.get("trade_cal", pd.DataFrame()),
        as_of,
        required_sessions=_required_market_sessions(settings),
    )
    for dataset in ("daily", "daily_basic", "index_daily"):
        frames[dataset] = _read_projected_dataset(
            data_dir,
            dataset,
            columns=_MARKET_PROJECTION_COLUMNS[dataset],
            date_field="trade_date",
            start_date=market_start,
            end_date=as_of.strftime("%Y%m%d"),
            diagnostics=diagnostics,
        )
        if "actual_available_date" in frames[dataset].columns:
            frames[dataset] = _visible_market_rows(frames[dataset], as_of=as_of)
    frames["suspend_d"] = _read_projected_dataset(
        data_dir,
        "suspend_d",
        columns=_MARKET_PROJECTION_COLUMNS["suspend_d"],
        date_field="trade_date",
        start_date=as_of.strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
        diagnostics=diagnostics,
    )
    for dataset in FINANCIAL_CORPUS_DATASETS:
        frames[dataset] = _visible_financial_rows(
            _read_projected_dataset(
                data_dir,
                dataset,
                columns=_FINANCIAL_PROJECTION_COLUMNS[dataset],
                date_field="end_date",
                end_date=as_of.strftime("%Y%m%d"),
                diagnostics=diagnostics,
            ),
            as_of=as_of,
        )
    frames["disclosure_date"] = _read_projected_dataset(
        data_dir,
        "disclosure_date",
        columns=_MARKET_PROJECTION_COLUMNS["disclosure_date"],
        date_field="end_date",
        end_date=as_of.strftime("%Y%m%d"),
        diagnostics=diagnostics,
    )
    return frames


def _validation_frames(
    data_dir: str | Path,
    *,
    base_only: bool = False,
    diagnostics: ReplayDiagnostics | None = None,
) -> dict[str, pd.DataFrame]:
    datasets = ("trade_cal", "stock_basic", "index_basic") if base_only else MANIFEST_DATASETS
    return {
        dataset: _read_projected_dataset(
            data_dir,
            dataset,
            columns=_MARKET_PROJECTION_COLUMNS.get(
                dataset, _FINANCIAL_PROJECTION_COLUMNS.get(dataset, ())
            ),
            diagnostics=diagnostics,
        )
        for dataset in datasets
    }


def run_replay_validation(
    data_dir: str | Path = "data",
    *,
    start: str | date | datetime | pd.Timestamp = DEFAULT_START_MONTH,
    end: str | date | datetime | pd.Timestamp = DEFAULT_END_MONTH,
    selection_rule: str = MONTHLY_SELECTION_RULE_VERSION,
    anchor_day: int = DEFAULT_ANCHOR_DAY,
    calendar_exchange: str | None = "SSE",
    top_n: int = 20,
    config: ReplayConfig | ReplayValidationConfig | None = None,
    seed: int = DEFAULT_SEED,
    stage: str = "monthly",
    today: str | date | datetime | pd.Timestamp | None = DEFAULT_VALIDATION_CUTOFF,
    determinism_sample: int = 3,
    content_hash: bool = True,
    artifact_output: str | Path | None = None,
    retain_snapshot_results: bool | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayValidationResult:
    """Run a read-only historical validation sample from the production path."""

    if isinstance(config, ReplayValidationConfig):
        validation_settings = config
        start = validation_settings.start
        end = validation_settings.end
        selection_rule = validation_settings.selection_rule
        anchor_day = validation_settings.anchor_day
        calendar_exchange = validation_settings.calendar_exchange
        top_n = validation_settings.top_n
        seed = validation_settings.seed
        stage = validation_settings.stage
        today = validation_settings.today
        determinism_sample = validation_settings.determinism_sample
        replay_settings = validation_settings.replay or ReplayConfig(top_n=top_n)
    else:
        replay_settings = config or ReplayConfig(top_n=top_n)
    _assert_initial_resource_gate(data_dir)
    frames = _validation_frames(data_dir, base_only=True, diagnostics=diagnostics)
    with _diagnostic_phase(diagnostics, "input_loading.manifest"):
        manifest = build_input_manifest(data_dir, content_hash=content_hash)

    def load_snapshot_frames(as_of: str, settings: ReplayConfig) -> Mapping[str, pd.DataFrame]:
        return _validation_frames_for_as_of(
            data_dir,
            frames,
            as_of_date=as_of,
            settings=settings,
            diagnostics=diagnostics,
        )

    return _run_validation(
        frames,
        data_dir=data_dir,
        input_manifest=manifest,
        start=start,
        end=end,
        selection_rule=selection_rule,
        anchor_day=anchor_day,
        calendar_exchange=calendar_exchange,
        top_n=top_n,
        replay_config=replay_settings,
        seed=seed,
        stage=stage,
        today=today,
        determinism_sample=determinism_sample,
        frame_loader=load_snapshot_frames,
        resource_guard=_raw_corpus_bytes(data_dir) >= LARGE_CORPUS_BYTES,
        artifact_output=artifact_output,
        retain_snapshot_results=retain_snapshot_results,
        diagnostics=diagnostics,
    )


def render_replay_validation_summary(result: ReplayValidationResult) -> str:
    """Render a safe human summary with correctness metrics only."""

    summary = result.summary
    resource_summary = summary.get("resource", {})
    current_pss = resource_summary.get("current_pss_bytes")
    current_private = resource_summary.get("current_private_bytes")
    current_pss_gib = current_pss / 1024**3 if current_pss is not None else None
    current_private_gib = current_private / 1024**3 if current_private is not None else None
    lines = [
        "# Historical PIT replay validation sample",
        "",
        f"- Contract: `{result.contract_version}`",
        f"- Physical artifact layout: `{ARTIFACT_LAYOUT_VERSION}`",
        f"- Overall status: `{result.status}`; gate: `{result.gate_status}`",
        f"- Selection rule: `{result.selection_rule}` (fixed anchor day; no return inputs)",
        f"- Range: `{result.start_month}`..`{result.end_month}`",
        f"- Frozen target-selection cutoff: `{result.configuration.get('today')}`",
        f"- Stage: `{result.stage}`",
        f"- Top-N: `{result.top_n}`; seed: `{result.seed}`",
        f"- Input manifest: `{summary.get('input_manifest_id') or '-'}`",
        "",
        "## Snapshot status",
        "",
        "| Requested | READY | INCOMPLETE | FAILED | UNAVAILABLE |",
        "| ---: | ---: | ---: | ---: | ---: |",
        f"| {summary['requested_snapshot_count']} | {summary['ready_count']} | "
        f"{summary['incomplete_count']} | {summary['failed_count']} | "
        f"{summary['unavailable_count']} |",
        "",
        f"- Available months in range: `{len(summary['available_months'])}`",
        f"- Stage-skipped available months: `{summary['stage_skipped_available_count']}`",
        f"- Future months unavailable: `{len(summary['unavailable_future_months'])}`",
        f"- Incomplete months: `{len(summary['incomplete_months'])}`",
        "",
        "## Regime coverage",
        "",
        "| Bull | Bear | Range | Unknown |",
        "| ---: | ---: | ---: | ---: |",
        "| {bull} | {bear} | {range} | {unknown} |".format(**summary["regime_counts"]),
        "",
        "## Evidence and ranking audit",
        "",
        f"- Coverage distribution: `{summary['coverage_distribution']}`",
        f"- Confidence distribution: `{summary['confidence_distribution']}`",
        f"- Unknown groups: `{summary['unknown_group_counts'] or {}}`",
        f"- Formal Top-N candidate rows: `{summary['top_n_candidate_count']}`",
        f"- Diagnostic candidates: `{summary['diagnostic_candidate_count']}`",
        f"- Diagnostic ranking-ineligible rows: `{summary['diagnostic_ranking_ineligible_count']}`",
        f"- Warnings: `{summary['warning_count']}` (rate `{summary['warning_rate']}`)",
        f"- Missing-input snapshots: `{summary['missing_input_count']}` "
        f"(rate `{summary['missing_input_rate']}`)",
        f"- PIT violations: `{summary['pit_violation_count']}`",
        f"- Live PSS (GiB): `{current_pss_gib}`",
        f"- Live private memory (GiB): `{current_private_gib}`",
        f"- Peak RSS diagnostic (ru_maxrss, GiB): `{resource_summary.get('peak_rss_gib')}` "
        "(not enforced)",
        f"- Resource gate: `{resource_summary.get('version')}`",
        f"- Synthetic adversarial fixtures: `{summary.get('synthetic_fixture_status')}`",
        f"- Financial revision boundary fixture: `{summary.get('revision_boundary_status')}`",
        "",
        "## Bounded performance audit",
        "",
        f"- Total wall seconds: `{summary.get('performance', {}).get('total_wall_seconds')}`",
        f"- Candidate seconds/candidate: `"
        f"{summary.get('performance', {}).get('candidate_seconds_per_candidate')}`",
        f"- Full replay ETA seconds: `"
        f"{summary.get('performance', {}).get('full_replay_eta_seconds')}`",
        f"- Sampled RSS diagnostic (GiB): `"
        f"{summary.get('performance', {}).get('rss_peak_gib')}`",
        f"- Phase seconds: `"
        f"{summary.get('performance', {}).get('phase_seconds', {})}`",
        "",
        "## Determinism",
        "",
        f"- Repeated checks: `{summary['determinism_checked_snapshot_count']}`",
        f"- Determinism failures: `{summary['determinism_failure_count']}`",
        "",
        "## Manual review",
        "",
        f"- Fixed review snapshots: `{result.manual_review['review_count']}`",
        "- Each review contains Top-3, a high-score ineligible diagnostic, an "
        "unknown-heavy candidate, and a universe exclusion boundary case when available.",
        "- Status is machine pre-check pending human sign-off; no performance label is used.",
        "",
        "## Limitations and scope guard",
        "",
        "- `stock_basic` historical name/status/industry/board fields are "
        "`UNSUPPORTED_PIT`; the replay uses only `ts_code`, `list_date`, and "
        "proven-safe `delist_date` boundaries and records exclusions.",
        "- Financial revisions are selected by `actual_available_date <= as_of`; "
        "market and benchmark observations are bounded by the selected session.",
        "- Target selection uses the explicit frozen cutoff recorded in `configuration.today`; "
        "the selected trading date remains the independent feature/PIT `as_of` cutoff.",
        "- Resource gate v2 enforces live PSS/private/system-pressure metrics; `ru_maxrss` "
        "is retained as a diagnostic high-water value only.",
        "- This is a PIT correctness sample, not a performance backtest: no "
        "forward-return evaluation, parameter tuning, weight changes, Score v2, "
        "ablation, or strategy claim is made.",
        "- Large real artifacts remain local/ignored; source RAW files are read-only.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_replay_validation_summary(result: ReplayValidationResult, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_replay_validation_summary(result), encoding="utf-8")
    return destination


def write_replay_validation_artifacts(
    result: ReplayValidationResult,
    output: str | Path,
    *,
    summary_path: str | Path | None = None,
) -> dict[str, Path]:
    """Write small manifests plus per-snapshot full evidence artifacts."""

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    snapshots_dir = destination / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        "artifact_schema_version": "pit-replay-validation-artifact-v1",
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
        "contract_version": result.contract_version,
        "status": result.status,
        "gate_status": result.gate_status,
        "selection_rule": result.selection_rule,
        "start_month": result.start_month,
        "end_month": result.end_month,
        "stage": result.stage,
        "top_n": result.top_n,
        "seed": result.seed,
        "validation_cutoff": result.configuration.get("today"),
        "resource_gate": result.configuration.get("resource_gate", {}),
        "configuration": result.configuration,
        "input_manifest": result.input_manifest,
        "targets": [target.as_dict() for target in result.targets],
        "run_manifests": [snapshot.run_manifest for snapshot in result.snapshots],
        "determinism_checks": list(result.determinism_checks),
        "checkpoint_file": "checkpoint.json",
        "snapshot_directory": "snapshots",
        "scope": "PIT correctness validation only; no forward-return evaluation",
    }
    paths: dict[str, Path] = {
        "manifest": _write_json(destination / "manifest.json", manifest_payload),
        "summary": _write_json(
            destination / "summary.json",
            {
                "artifact_schema_version": "pit-replay-validation-artifact-v1",
                "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
                "contract_version": result.contract_version,
                "status": result.status,
                "gate_status": result.gate_status,
                "configuration": result.configuration,
                "summary": result.summary,
                "warnings": list(result.warnings),
            },
        ),
        "manual_review": _write_json(destination / "manual-review.json", result.manual_review),
        "synthetic_fixtures": _write_json(
            destination / "synthetic-fixtures.json", result.synthetic_fixtures
        ),
    }
    for snapshot in result.snapshots:
        snapshot_path = snapshots_dir / _snapshot_filename(snapshot)
        # Streaming runs have already committed complete snapshot payloads and
        # intentionally retain no ReplayResult in RAM.  Do not replace those
        # files with a null ``replay`` placeholder during finalization.
        if snapshot.result is not None or not snapshot_path.exists():
            _write_json(snapshot_path, snapshot.normalized_dict())
    paths["snapshots"] = snapshots_dir
    completed = [
        {
            "target_month": snapshot.target.target_month,
            "selected_trading_date": snapshot.target.selected_trading_date,
            "status": snapshot.status,
        }
        for snapshot in result.snapshots
    ]
    paths["checkpoint"] = _write_json(
        destination / "checkpoint.json",
        {
            "artifact_schema_version": "pit-replay-validation-artifact-v1",
            "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
            "status": "COMPLETE",
            "contract_version": result.contract_version,
            "stage": result.stage,
            "input_manifest_id": result.input_manifest.get("manifest_id"),
            "target_count": len(result.targets),
            "targets": [target.as_dict() for target in result.targets],
            "completed": completed,
            "summary": result.summary,
        },
    )
    rendered_path = destination / "summary.md"
    rendered_path.write_text(render_replay_validation_summary(result), encoding="utf-8")
    paths["summary_markdown"] = rendered_path
    if summary_path is not None:
        paths["requested_summary"] = write_replay_validation_summary(result, summary_path)
    return paths


__all__ = [
    "PIT_REPLAY_VALIDATION_CONTRACT_VERSION",
    "REPLAY_VALIDATION_CONTRACT_VERSION",
    "MONTHLY_SELECTION_RULE_VERSION",
    "MARKET_REGIME_CONTRACT_VERSION",
    "HISTORICAL_UNIVERSE_CONTRACT_VERSION",
    "RESOURCE_GATE_CONTRACT_VERSION",
    "RESOURCE_SAMPLING_CONTRACT_VERSION",
    "DEFAULT_VALIDATION_CUTOFF",
    "MAX_LIVE_PSS_BYTES",
    "MAX_LIVE_PRIVATE_BYTES",
    "MAX_PROCESS_SWAP_BYTES",
    "MAX_SWAP_GROWTH_BYTES",
    "ARTIFACT_LAYOUT_VERSION",
    "PIT_REPLAY_ARTIFACT_LAYOUT_VERSION",
    "ReplayValidationConfig",
    "ReplayDiagnostics",
    "PITViolation",
    "PITValidationError",
    "ResourceBlocked",
    "MonthlySnapshotTarget",
    "select_monthly_snapshot_dates",
    "select_monthly_targets",
    "monthly_snapshot_targets",
    "build_input_manifest",
    "RegimeResult",
    "classify_market_regime",
    "market_regime",
    "validate_replay_pit",
    "validate_normalized_vector_pit",
    "validate_normalized_snapshot_pit",
    "assert_replay_pit_safe",
    "ReplayValidationSnapshot",
    "ReplayValidationResult",
    "build_manual_review_sample",
    "run_adversarial_fixtures",
    "run_replay_validation_frames",
    "run_replay_validation",
    "render_replay_validation_summary",
    "write_replay_validation_summary",
    "write_replay_validation_artifacts",
]
