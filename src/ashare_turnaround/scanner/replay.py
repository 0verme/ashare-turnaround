"""Point-in-time replay of the complete scanner pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..features import (
    EXPECTATION_CROWDING_CONTRACT_VERSION,
    LOW_ATTENTION_V2_FIELDS,
    LOW_ATTENTION_V2_VERSION,
    CrowdingConfig,
    LowAttentionConfig,
    build_cross_section_population,
    compute_attention_features,
    compute_crowding_features,
    compute_fundamental_features,
    compute_low_attention_v2,
    compute_quality_features,
    compute_trend_features,
)
from ..features.financial_context import FinancialSemanticContext
from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION
from ..replay_cache import ReplaySnapshotCache, current_replay_cache, replay_cache_scope
from ..storage.inventory import build_coverage_report
from ..storage.parquet import RawParquetStore
from .artifacts import (
    deterministic_replay_digests,
    normalize_replay_artifact,
    serialized_json_bytes,
    write_json_artifact,
)
from .contracts import TURNAROUND_TREND_CONTRACT_VERSION, FeatureVector
from .evidence import (
    EVIDENCE_CONFIDENCE_CONTRACT_VERSION,
    FEATURE_GROUP_REGISTRY_VERSION,
    EvidenceConfidenceConfig,
)
from .score import (
    ScoreConfig,
    ScoreResult,
    ablation_score_configs,
    rank_scores,
    score_feature_vector,
)
from .universe import (
    HISTORICAL_UNIVERSE_CONTRACT_VERSION,
    UniverseConfig,
    UniverseDecision,
    _financial_period_counts,
    build_investable_universe,
)


@dataclass(slots=True)
class _DiagnosticPhase:
    calls: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "total_seconds": self.total_seconds,
            "max_seconds": self.max_seconds,
        }


class _DiagnosticTimer:
    def __init__(self, diagnostics: ReplayDiagnostics, name: str) -> None:
        self.diagnostics = diagnostics
        self.name = name
        self.started_at = 0.0

    def __enter__(self) -> _DiagnosticTimer:
        self.started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        elapsed = time.perf_counter() - self.started_at
        phase = self.diagnostics.phases.setdefault(self.name, _DiagnosticPhase())
        phase.calls += 1
        phase.total_seconds += elapsed
        phase.max_seconds = max(phase.max_seconds, elapsed)


@dataclass(slots=True)
class ReplayDiagnostics:
    """Out-of-band timings for one replay invocation.

    Diagnostics are deliberately not part of :class:`ReplayConfig` or any
    result payload.  ``candidate_limit`` is accepted only here, making a
    bounded run explicitly diagnostic rather than a replay-validation result.
    """

    candidate_limit: int | None = None
    checkpoint_every: int = 100
    logger: Callable[[dict[str, Any]], None] | None = None
    candidate_sink: Callable[[FeatureVector, ScoreResult], None] | None = None
    candidate_validator: Callable[[FeatureVector], Iterable[str]] | None = None
    candidate_validation_violations: list[str] = field(default_factory=list)
    retain_vectors: bool = True
    started_at: float = field(default_factory=time.perf_counter)
    phases: dict[str, _DiagnosticPhase] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    candidate_total: int = 0
    candidate_processed: int = 0
    candidate_started_at: float | None = None
    candidate_finished_at: float | None = None
    peak_rss_bytes: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.candidate_limit is not None and self.candidate_limit <= 0:
            raise ValueError("diagnostic candidate_limit must be positive")
        if self.checkpoint_every <= 0:
            raise ValueError("diagnostic checkpoint_every must be positive")
        self.peak_rss_bytes = self.current_rss_bytes()

    @staticmethod
    def current_rss_bytes() -> int | None:
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError, AttributeError):
            return None

    def phase(self, name: str) -> _DiagnosticTimer:
        return _DiagnosticTimer(self, name)

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.logger is None:
            return
        try:
            self.logger(payload)
        except Exception:
            # Diagnostics must never change replay correctness or status.
            return

    def start_candidates(self, total: int) -> None:
        self.candidate_total = int(total)
        self.candidate_processed = 0
        self.candidate_started_at = time.perf_counter()
        self.candidate_finished_at = None
        self.record_candidate(0, force=True)

    def finish_candidates(self) -> None:
        if self.candidate_started_at is not None and self.candidate_finished_at is None:
            self.candidate_finished_at = time.perf_counter()

    def record_candidate(self, processed: int, *, force: bool = False) -> None:
        self.candidate_processed = int(processed)
        if not force and processed != 0 and processed % self.checkpoint_every != 0:
            return
        now = time.perf_counter()
        started = self.candidate_started_at or self.started_at
        elapsed = max(0.0, now - started)
        current_rss = self.current_rss_bytes()
        if current_rss is not None:
            self.peak_rss_bytes = max(self.peak_rss_bytes or 0, current_rss)
        checkpoint = {
            "event": "candidate_progress",
            "processed": self.candidate_processed,
            "total": self.candidate_total,
            "elapsed_seconds": elapsed,
            "run_elapsed_seconds": max(0.0, now - self.started_at),
            "stocks_per_second": (
                self.candidate_processed / elapsed if elapsed > 0 else None
            ),
            "current_rss_bytes": current_rss,
            "peak_rss_bytes": self.peak_rss_bytes,
            "phase_totals": {
                name: phase.as_dict() for name, phase in sorted(self.phases.items())
            },
        }
        if not self.checkpoints or self.checkpoints[-1]["processed"] != processed:
            self.checkpoints.append(checkpoint)
            self._emit(checkpoint)

    def summary(self) -> dict[str, Any]:
        self.finish_candidates()
        current_rss = self.current_rss_bytes()
        if current_rss is not None:
            self.peak_rss_bytes = max(self.peak_rss_bytes or 0, current_rss)
        now = time.perf_counter()
        wall_seconds = max(0.0, now - self.started_at)
        candidate_seconds = (
            max(
                0.0,
                (self.candidate_finished_at or now) - self.candidate_started_at,
            )
            if self.candidate_started_at is not None
            else 0.0
        )
        return {
            "candidate_limit": self.candidate_limit,
            "candidate_total": self.candidate_total,
            "candidate_processed": self.candidate_processed,
            "candidate_validation_violation_count": len(
                self.candidate_validation_violations
            ),
            "candidate_checkpoints": list(self.checkpoints),
            "candidate_seconds": candidate_seconds,
            "candidate_seconds_per_candidate": (
                candidate_seconds / self.candidate_processed
                if self.candidate_processed
                else None
            ),
            "wall_seconds": wall_seconds,
            "peak_rss_bytes": self.peak_rss_bytes,
            "phases": {
                name: phase.as_dict() for name, phase in sorted(self.phases.items())
            },
        }

    def emit_summary(self) -> dict[str, Any]:
        payload = {"event": "replay_diagnostics_summary", **self.summary()}
        self._emit(payload)
        return payload


def replay_performance_profile(
    diagnostics: ReplayDiagnostics,
    *,
    full_candidate_count: int | None = None,
) -> dict[str, Any]:
    """Return the bounded single-thread performance audit for one replay."""

    summary = diagnostics.summary()
    phases = summary["phases"]
    names = (
        "fundamental",
        "trend",
        "quality",
        "attention",
        "crowding",
        "low_attention",
        "scoring",
    )
    phase_seconds = {
        name: float(phases.get(f"candidate.{name}", {}).get("total_seconds", 0.0))
        for name in names
    }
    phase_seconds["PIT validation"] = float(
        phases.get("pit_validation", {}).get("total_seconds", 0.0)
        + phases.get("candidate.pit_validation", {}).get("total_seconds", 0.0)
    )
    phase_seconds["artifact serialization"] = float(
        phases.get("artifact_serialization", {}).get("total_seconds", 0.0)
    )
    processed = int(summary.get("candidate_processed", 0))
    candidate_seconds = float(summary.get("candidate_seconds", 0.0))
    per_candidate = candidate_seconds / processed if processed else None
    population = int(full_candidate_count or summary.get("candidate_total", 0))
    feature_seconds = sum(phase_seconds[name] for name in names)
    pit_seconds = phase_seconds["PIT validation"]
    artifact_seconds = phase_seconds["artifact serialization"]
    fixed_seconds = max(0.0, float(summary.get("wall_seconds", 0.0)) - candidate_seconds)
    eta = fixed_seconds + per_candidate * population if per_candidate is not None else None
    return {
        "profile_version": "pit-replay-performance-audit-v1",
        "single_threaded": True,
        "candidate_total": int(summary.get("candidate_total", 0)),
        "candidate_processed": processed,
        "total_wall_seconds": float(summary.get("wall_seconds", 0.0)),
        "candidate_loop_seconds": candidate_seconds,
        "candidate_seconds_per_candidate": per_candidate,
        "candidate_seconds/candidate": per_candidate,
        "feature_seconds": feature_seconds,
        "feature_seconds_per_candidate": feature_seconds / processed if processed else None,
        "pit_validation_seconds_per_candidate": pit_seconds / processed if processed else None,
        "artifact_serialization_seconds_per_candidate": (
            artifact_seconds / processed if processed else None
        ),
        "full_candidate_count_for_eta": population,
        "fixed_overhead_seconds": fixed_seconds,
        "full_replay_eta_seconds": eta,
        "full_replay_eta_hours": eta / 3600.0 if eta is not None else None,
        "phase_seconds": phase_seconds,
        "rss_peak_bytes": summary.get("peak_rss_bytes"),
        "rss_peak_gib": (
            summary["peak_rss_bytes"] / 1024**3
            if summary.get("peak_rss_bytes") is not None
            else None
        ),
        "raw_diagnostics": summary,
    }


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    top_n: int = 20
    universe: UniverseConfig = field(
        default_factory=lambda: UniverseConfig(
            version=HISTORICAL_UNIVERSE_CONTRACT_VERSION,
            pit_safe_only=True,
        )
    )
    score: ScoreConfig = field(default_factory=ScoreConfig)
    crowding: CrowdingConfig = field(default_factory=CrowdingConfig)
    low_attention: LowAttentionConfig = field(default_factory=LowAttentionConfig)
    evidence_confidence: EvidenceConfidenceConfig = field(default_factory=EvidenceConfidenceConfig)

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")

    def declared(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "comparable_period_contract_version": COMPARABLE_PERIOD_CONTRACT_VERSION,
            "trend_contract_version": TURNAROUND_TREND_CONTRACT_VERSION,
            "attention_contract_version": self.low_attention.version,
            "low_attention_version": self.low_attention.version,
            "expectation_crowding_contract_version": self.crowding.version,
            "benchmark": self.crowding.benchmark.declared(),
            "expectation_crowding": self.crowding.declared(),
            "universe": asdict(self.universe),
            "score": self.score.declared(),
            "low_attention": self.low_attention.declared(),
            "evidence_confidence_contract_version": self.evidence_confidence.version,
            "feature_group_registry_version": self.evidence_confidence.registry_version,
            "evidence_confidence": self.evidence_confidence.declared(),
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.declared(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    as_of_date: str
    snapshot_id: str
    universe_version: str
    feature_version: str
    score_version: str
    config_fingerprint: str
    run_id: str
    configuration: dict[str, Any]
    input_rows: dict[str, int]
    status: str
    ranked: pd.DataFrame
    vectors: tuple[FeatureVector, ...]
    scores: tuple[ScoreResult, ...]
    warnings: tuple[str, ...] = ()
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    trend_contract_version: str = TURNAROUND_TREND_CONTRACT_VERSION
    attention_contract_version: str = LOW_ATTENTION_V2_VERSION
    attention_feature_fields: tuple[str, ...] = LOW_ATTENTION_V2_FIELDS
    expectation_crowding_contract_version: str = EXPECTATION_CROWDING_CONTRACT_VERSION
    benchmark_metadata: dict[str, Any] = field(default_factory=dict)
    # Full diagnostic ordering is retained separately from the formal Top-N
    # ``ranked`` frame, whose rows are eligibility-gated.
    diagnostic_ranked: pd.DataFrame | None = None
    evidence_confidence_contract_version: str = EVIDENCE_CONFIDENCE_CONTRACT_VERSION
    feature_group_registry_version: str = FEATURE_GROUP_REGISTRY_VERSION
    # The complete universe decision log is retained for PIT audit. Formal
    # ranking rows alone cannot explain why a security never entered a
    # candidate set.
    universe_decisions: tuple[UniverseDecision, ...] = ()
    universe_warnings: tuple[str, ...] = ()
    universe_source_evidence: dict[str, Any] = field(default_factory=dict)
    universe_pit_safe: bool = False
    universe_limitations: tuple[str, ...] = ()

    @property
    def full_ranked(self) -> pd.DataFrame:
        """Return all candidates retained for diagnostics/audit."""

        return self.diagnostic_ranked if self.diagnostic_ranked is not None else self.ranked

    def metadata(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "snapshot_id": self.snapshot_id,
            "universe_version": self.universe_version,
            "feature_version": self.feature_version,
            "score_version": self.score_version,
            "config_fingerprint": self.config_fingerprint,
            "run_id": self.run_id,
            "configuration": self.configuration,
            "input_rows": self.input_rows,
            "status": self.status,
            "warnings": list(self.warnings),
            "comparable_period_contract_version": self.comparable_period_contract_version,
            "trend_contract_version": self.trend_contract_version,
            "attention_contract_version": self.attention_contract_version,
            "low_attention_version": self.attention_contract_version,
            "attention_feature_fields": list(self.attention_feature_fields),
            "attention_v2_research_only": True,
            "production_score_attention_input": "attention_score",
            "expectation_crowding_contract_version": self.expectation_crowding_contract_version,
            "crowding_contract_version": self.expectation_crowding_contract_version,
            "benchmark": dict(self.benchmark_metadata),
            "benchmark_config": dict(self.benchmark_metadata),
            "benchmark_id": self.benchmark_metadata.get("benchmark_id"),
            "benchmark_name": self.benchmark_metadata.get("benchmark_name"),
            "benchmark_contract_version": self.benchmark_metadata.get(
                "benchmark_contract_version", self.benchmark_metadata.get("version")
            ),
            "benchmark_source_dataset": self.benchmark_metadata.get("source_dataset"),
            "evidence_confidence_contract_version": self.evidence_confidence_contract_version,
            "feature_group_registry_version": self.feature_group_registry_version,
            "evidence_confidence": self.configuration.get("evidence_confidence", {}),
            "universe": {
                "version": self.universe_version,
                "as_of_date": self.as_of_date,
                "pit_safe": self.universe_pit_safe,
                "decision_count": len(self.universe_decisions),
                "included_count": sum(
                    1 for decision in self.universe_decisions if decision.included
                ),
                "excluded_count": sum(
                    1 for decision in self.universe_decisions if not decision.included
                ),
                "warnings": list(self.universe_warnings),
                "source_evidence": dict(self.universe_source_evidence),
                "limitations": list(self.universe_limitations),
            },
            "historical_universe_pit_safe": self.universe_pit_safe,
            "historical_universe_limitations": list(self.universe_limitations),
            "critical_groups": self.configuration.get("evidence_confidence", {}).get(
                "critical_groups", []
            ),
            "ranking_eligible_count": int(
                self.full_ranked["ranking_eligible"].sum()
                if "ranking_eligible" in self.full_ranked.columns
                else len(self.ranked)
            ),
            "formal_ranked_count": int(self.ranked.shape[0]),
            "diagnostic_candidate_count": int(self.full_ranked.shape[0]),
            "feature_contracts": {
                "comparable_period": self.comparable_period_contract_version,
                "trend": self.trend_contract_version,
                "low_attention": self.attention_contract_version,
                "expectation_crowding": self.expectation_crowding_contract_version,
                "evidence_confidence": self.evidence_confidence_contract_version,
                "feature_group_registry": self.feature_group_registry_version,
                "benchmark": self.benchmark_metadata.get(
                    "benchmark_contract_version", self.benchmark_metadata.get("version")
                ),
            },
        }

    def artifact_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata(),
            "ranked": self.ranked.to_dict(orient="records"),
            "diagnostic_ranked": (
                self.diagnostic_ranked.to_dict(orient="records")
                if self.diagnostic_ranked is not None
                else self.ranked.to_dict(orient="records")
            ),
            "vectors": [vector.as_dict() for vector in self.vectors],
            "scores": [score.as_dict() for score in self.scores],
            "universe": {
                "as_of_date": self.as_of_date,
                "version": self.universe_version,
                "pit_safe": self.universe_pit_safe,
                "included": [
                    decision.ts_code for decision in self.universe_decisions if decision.included
                ],
                "decisions": [decision.as_dict() for decision in self.universe_decisions],
                "warnings": list(self.universe_warnings),
                "source_evidence": dict(self.universe_source_evidence),
                "limitations": list(self.universe_limitations),
            },
        }

    def normalized_artifact_dict(self) -> dict[str, Any]:
        """Return the lossless physical artifact layout for this replay."""

        payload = normalize_replay_artifact(self.artifact_dict())
        payload["deterministic_digests"] = self.deterministic_digests()
        return payload

    def deterministic_digests(
        self,
        *,
        input_manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return semantic digests without diagnostic timing or object identity."""

        return deterministic_replay_digests(self, input_manifest=input_manifest)


_SNAPSHOT_DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "trade_cal": ("cal_date",),
    "daily": ("trade_date",),
    "daily_basic": ("trade_date",),
    "index_daily": ("trade_date",),
    "suspend_d": ("trade_date",),
    "index_basic": ("reference_snapshot_date", "list_date"),
    "income": ("actual_available_date", "f_ann_date", "ann_date"),
    "balancesheet": ("actual_available_date", "f_ann_date", "ann_date"),
    "cashflow": ("actual_available_date", "f_ann_date", "ann_date"),
    "fina_indicator": ("actual_available_date", "ann_date"),
    "disclosure_date": ("actual_date", "ann_date"),
}


def _visible_snapshot_frame(dataset: str, frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    candidates = _SNAPSHOT_DATE_FIELDS.get(dataset, ())
    available = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    found = False
    for field_name in candidates:
        if field_name not in frame.columns:
            continue
        found = True
        available = available.fillna(pd.to_datetime(frame[field_name], errors="coerce"))
    if not found:
        return frame
    return frame.loc[available.notna() & available.dt.normalize().le(as_of)]


def _snapshot_id(frames: dict[str, pd.DataFrame], as_of_date: str) -> str:
    digest = hashlib.sha256()
    digest.update(as_of_date.encode("ascii"))
    as_of = pd.Timestamp(as_of_date)
    for dataset in sorted(frames):
        frame = _visible_snapshot_frame(dataset, frames[dataset], as_of)
        digest.update(dataset.encode("utf-8"))
        ordered = frame.reindex(sorted(frame.columns), axis=1)
        digest.update(str(len(ordered)).encode("ascii"))
        digest.update(",".join(map(str, ordered.columns)).encode("utf-8"))
        try:
            values = pd.util.hash_pandas_object(ordered, index=False).to_numpy(copy=True)
            values.sort()
            digest.update(values.tobytes())
        except (TypeError, ValueError):
            records = [
                json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
                for record in ordered.to_dict(orient="records")
            ]
            digest.update("\n".join(sorted(records)).encode("utf-8"))
    return digest.hexdigest()[:16]


def _market_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    daily = frames.get("daily", pd.DataFrame())
    basic = frames.get("daily_basic", pd.DataFrame())
    if daily.empty:
        return basic.copy()
    if basic.empty:
        return daily.copy()
    keys = [
        key for key in ("ts_code", "trade_date") if key in daily.columns and key in basic.columns
    ]
    if not keys:
        return pd.concat([daily, basic], ignore_index=True, sort=False)
    return daily.merge(basic, on=keys, how="outer", suffixes=("", "_basic"))


def _diagnostic_phase(
    diagnostics: ReplayDiagnostics | None, name: str
) -> _DiagnosticTimer | Any:
    return diagnostics.phase(name) if diagnostics is not None else nullcontext()


def _frames_from_store(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    store = RawParquetStore(data_dir)
    datasets = (
        "stock_basic",
        "trade_cal",
        "index_basic",
        "suspend_d",
        "daily",
        "daily_basic",
        "index_daily",
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "disclosure_date",
    )
    return {dataset: store.read(dataset) for dataset in datasets}


def _attach_low_attention_evidence(vector: FeatureVector, attention: FeatureVector) -> None:
    """Attach v2 evidence without overwriting the v1 ``abnormal_volume`` key.

    Replay vectors also carry the production v1 market group.  The two APIs
    historically used the same name for abnormal volume, so a blind merge
    would silently replace v1 evidence.  Non-colliding v2 fields are exposed
    directly; a colliding field is explicitly namespaced and the complete v2
    evidence is retained in vector metadata.
    """

    attached_evidence: dict[str, dict[str, Any]] = {}
    namespace = str(attention.metadata.get("namespace") or "low_attention_v2").strip()
    for name, evidence in attention.evidence.items():
        target = name
        if target in vector.values:
            target = f"{namespace}_{name}"
            suffix = 2
            while target in vector.values:
                target = f"{namespace}_{name}_{suffix}"
                suffix += 1
        vector.values[target] = attention.values.get(name)
        vector.evidence[target] = evidence if target == name else replace(evidence, feature=target)
        attached_evidence[target] = vector.evidence[target].as_dict()
        if (
            evidence.status
            in {
                "unknown",
                "missing",
                "insufficient_data",
                "insufficient_history",
                "discontinuous",
                "stale",
                "invalid",
                "future_unsafe",
                "future-unsafe",
                "pit_warning",
                "unsupported_pit",
                "pit_unsupported",
                "unsupported",
            }
            and target not in vector.unknown_features
        ):
            vector.unknown_features.append(target)

    declared = dict(attention.metadata.get("low_attention_v2", {}))
    existing_declared = vector.metadata.setdefault("low_attention_v2", {})
    if isinstance(existing_declared, dict):
        for key, value in declared.items():
            if key not in existing_declared:
                existing_declared[key] = value
            elif isinstance(existing_declared[key], dict) and isinstance(value, dict):
                existing_declared[key].update(
                    {
                        nested_key: nested_value
                        for nested_key, nested_value in value.items()
                        if nested_key not in existing_declared[key]
                    }
                )
    vector.metadata.setdefault("low_attention_v2_evidence", {}).update(attached_evidence)
    vector.metadata.setdefault("low_attention_v2_risk_flags", list(attention.risk_flags))
    vector.metadata.setdefault("low_attention_v2_source_version", attention.version)


def run_replay_frames(
    frames: dict[str, pd.DataFrame],
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayResult:
    """Run one replay inside a snapshot-local, non-serialized index scope."""

    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid replay as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    cache_settings = config or ReplayConfig()
    with _diagnostic_phase(diagnostics, "snapshot_local_index_build"):
        cache = ReplaySnapshotCache.from_frames(
            frames,
            as_of=as_of,
            daily_basic_lookback=cache_settings.universe.liquidity_lookback,
        )
    with replay_cache_scope(cache):
        return _run_replay_frames_with_cache(
            frames,
            as_of_date=as_of_date,
            config=config,
            diagnostics=diagnostics,
        )


def _run_replay_frames_with_cache(
    frames: dict[str, pd.DataFrame],
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayResult:
    """Run the scanner against supplied frames, making tests independent of I/O."""

    settings = config or ReplayConfig()
    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid replay as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    as_of_text = as_of.strftime("%Y%m%d")
    financial_frames = {
        key: frames.get(key, pd.DataFrame())
        for key in ("income", "balancesheet", "cashflow", "fina_indicator")
    }
    with _diagnostic_phase(diagnostics, "financial_period_counts"):
        financial_period_counts = _financial_period_counts(financial_frames, as_of)
    with _diagnostic_phase(diagnostics, "build_investable_universe"):
        universe = build_investable_universe(
            frames.get("stock_basic", pd.DataFrame()),
            as_of_date=as_of,
            daily_basic=frames.get("daily_basic"),
            financial_frames=financial_frames,
            financial_period_counts=financial_period_counts,
            suspension_frame=frames.get("suspend_d"),
            config=settings.universe,
        )
    with _diagnostic_phase(diagnostics, "market_frame_merge"):
        market = _market_frame(frames)
    cache = current_replay_cache()
    if cache is not None:
        with _diagnostic_phase(diagnostics, "snapshot_market_index_build"):
            cache.set_market_frame(market)
    vectors: list[FeatureVector] = []
    scores: list[ScoreResult] = []
    warnings = list(universe.warnings)
    investable_codes = set(universe.included["ts_code"].astype(str))
    with _diagnostic_phase(diagnostics, "low_attention_cross_section_population"):
        low_attention_population = build_cross_section_population(
            market,
            as_of_date=as_of,
            config=settings.low_attention.cross_section,
            investable_codes=investable_codes,
        )
    candidate_total = len(universe.included)
    candidate_limit = candidate_total
    if diagnostics is not None:
        if diagnostics.candidate_limit is not None:
            candidate_limit = min(candidate_total, diagnostics.candidate_limit)
        diagnostics.start_candidates(candidate_total)
    for candidate_position, (_, row) in enumerate(universe.included.iterrows()):
        if candidate_position >= candidate_limit:
            break
        code = str(row["ts_code"])
        with _diagnostic_phase(diagnostics, "candidate.financial_semantic_prepare"):
            financial_context = FinancialSemanticContext.prepare(
                financial_frames, code, as_of
            )
        with _diagnostic_phase(diagnostics, "candidate.fundamental"):
            vector = compute_fundamental_features(
                financial_frames,
                code,
                as_of,
                _semantic_context=financial_context,
            )
        with _diagnostic_phase(diagnostics, "candidate.trend"):
            vector.merge(
                compute_trend_features(
                    financial_frames,
                    code,
                    as_of,
                    _semantic_context=financial_context,
                )
            )
        with _diagnostic_phase(diagnostics, "candidate.quality"):
            vector.merge(compute_quality_features(financial_frames, code, as_of))
        with _diagnostic_phase(diagnostics, "candidate.attention"):
            vector.merge(compute_attention_features(market, code, as_of))
        with _diagnostic_phase(diagnostics, "candidate.crowding"):
            vector.merge(
                compute_crowding_features(
                    market,
                    code,
                    as_of,
                    config=settings.crowding,
                    calendar_frame=frames.get("trade_cal"),
                    disclosure_frame=frames.get("disclosure_date"),
                    benchmark_frame=frames.get("index_daily"),
                    benchmark_definition_frame=frames.get("index_basic"),
                    suspension_frame=frames.get("suspend_d"),
                )
            )
        with _diagnostic_phase(diagnostics, "candidate.low_attention"):
            low_attention = compute_low_attention_v2(
                market,
                code,
                as_of,
                config=settings.low_attention,
                list_date=row.get("list_date"),
                investable_codes=investable_codes,
                population_frame=low_attention_population,
            )
            _attach_low_attention_evidence(vector, low_attention)
        with _diagnostic_phase(diagnostics, "candidate.scoring"):
            score = score_feature_vector(
                vector,
                config=settings.score,
                evidence_config=settings.evidence_confidence,
            )
            scores.append(score)
        if diagnostics is not None and diagnostics.candidate_sink is not None:
            with _diagnostic_phase(diagnostics, "artifact_serialization"):
                diagnostics.candidate_sink(vector, score)
        if diagnostics is not None and diagnostics.candidate_validator is not None:
            with _diagnostic_phase(diagnostics, "pit_validation"):
                diagnostics.candidate_validation_violations.extend(
                    str(value) for value in diagnostics.candidate_validator(vector)
                )
        if diagnostics is None or diagnostics.retain_vectors:
            vectors.append(vector)
        elif diagnostics.candidate_sink is not None and not vectors:
            # Keep one representative for manual-review checks; the sink
            # carries the complete vector array for streamed artifacts.
            vectors.append(vector)
        if diagnostics is not None:
            diagnostics.record_candidate(len(scores))
        if cache is not None:
            cache.clear_candidate_state()
    if diagnostics is not None:
        diagnostics.record_candidate(len(scores), force=True)
        diagnostics.finish_candidates()
    with _diagnostic_phase(diagnostics, "ranking"):
        diagnostic_ranked = rank_scores(scores, top_n=None)
        ranked = rank_scores(scores, top_n=settings.top_n)
        if not universe.included.empty:
            names = (
                universe.included[["ts_code", "name"]].drop_duplicates("ts_code")
                if "name" in universe.included.columns
                else pd.DataFrame(columns=["ts_code", "name"])
            )
            for frame_name, frame in (("ranked", ranked), ("diagnostic", diagnostic_ranked)):
                if frame.empty:
                    continue
                named = frame.merge(names, on="ts_code", how="left")
                named = named.sort_values("rank", kind="stable").reset_index(drop=True)
                if frame_name == "ranked":
                    ranked = named
                else:
                    diagnostic_ranked = named
    required = {"stock_basic", "income", "daily_basic"}
    missing = sorted(dataset for dataset in required if frames.get(dataset, pd.DataFrame()).empty)
    if missing:
        warnings.append(f"missing_required_datasets={','.join(missing)}")
    if frames.get("index_daily", pd.DataFrame()).empty:
        warnings.append("missing_benchmark_dataset=index_daily")
    status = (
        "PASS"
        if diagnostic_ranked.shape[0] > 0 and not missing
        else "PARTIAL"
        if vectors
        else "EMPTY"
    )
    if diagnostics is not None and diagnostics.candidate_limit is not None:
        status = "DIAGNOSTIC_PARTIAL"
    with _diagnostic_phase(diagnostics, "snapshot_id_hash"):
        snapshot_id = _snapshot_id(frames, as_of_text)
    config_fingerprint = settings.fingerprint
    run_id = hashlib.sha256(f"{snapshot_id}:{config_fingerprint}".encode("ascii")).hexdigest()[:16]
    for frame in (ranked, diagnostic_ranked):
        frame["historical_universe_member"] = True
        frame["snapshot_id"] = snapshot_id
        frame["run_id"] = run_id
        frame["universe_version"] = universe.version
        frame["feature_version"] = "features-v1"
        frame["expectation_crowding_contract_version"] = settings.crowding.version
        frame["benchmark_id"] = settings.crowding.benchmark.benchmark_id
        frame["benchmark_contract_version"] = settings.crowding.benchmark.version
        frame["benchmark_source_dataset"] = settings.crowding.benchmark.source_dataset
        frame["comparable_period_contract_version"] = COMPARABLE_PERIOD_CONTRACT_VERSION
        frame["trend_contract_version"] = TURNAROUND_TREND_CONTRACT_VERSION
        frame["attention_contract_version"] = settings.low_attention.version
        frame["low_attention_version"] = settings.low_attention.version
        frame["attention_v2_research_only"] = True
        frame["evidence_confidence_contract_version"] = settings.evidence_confidence.version
        frame["feature_group_registry_version"] = settings.evidence_confidence.registry_version
        frame["critical_groups"] = "|".join(settings.evidence_confidence.critical_groups)
        frame["score_config_fingerprint"] = settings.score.fingerprint
    return ReplayResult(
        as_of_date=as_of_text,
        snapshot_id=snapshot_id,
        universe_version=universe.version,
        feature_version="features-v1",
        score_version=settings.score.version,
        config_fingerprint=config_fingerprint,
        run_id=run_id,
        configuration=settings.declared(),
        input_rows={name: int(len(frame)) for name, frame in sorted(frames.items())},
        status=status,
        ranked=ranked,
        vectors=tuple(vectors),
        scores=tuple(scores),
        warnings=tuple(dict.fromkeys(warnings)),
        comparable_period_contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        trend_contract_version=TURNAROUND_TREND_CONTRACT_VERSION,
        attention_contract_version=settings.low_attention.version,
        attention_feature_fields=LOW_ATTENTION_V2_FIELDS,
        expectation_crowding_contract_version=settings.crowding.version,
        benchmark_metadata=settings.crowding.benchmark.declared(),
        diagnostic_ranked=diagnostic_ranked,
        evidence_confidence_contract_version=settings.evidence_confidence.version,
        feature_group_registry_version=settings.evidence_confidence.registry_version,
        universe_decisions=universe.decisions,
        universe_warnings=universe.warnings,
        universe_source_evidence=universe.source_evidence,
        universe_pit_safe=settings.universe.pit_safe_only,
        universe_limitations=universe.limitations,
    )


def run_replay(
    data_dir: str | Path,
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
    diagnostics: ReplayDiagnostics | None = None,
) -> ReplayResult:
    frames = _frames_from_store(data_dir)
    result = run_replay_frames(
        frames,
        as_of_date=as_of_date,
        config=config,
        diagnostics=diagnostics,
    )
    coverage = build_coverage_report(data_dir, as_of_date=result.as_of_date)
    if any(dataset.status in {"FAIL", "PARTIAL", "UNKNOWN"} for dataset in coverage.datasets):
        result = replace(
            result,
            status="PARTIAL" if result.status == "PASS" else result.status,
            warnings=tuple(dict.fromkeys((*result.warnings, "coverage_not_complete"))),
        )
    return result


def run_replay_variants(
    data_dir: str | Path,
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
) -> dict[str, ReplayResult]:
    """Run the required score variants against one immutable input snapshot."""

    base = config or ReplayConfig()
    frames = _frames_from_store(data_dir)
    results = {
        name: run_replay_frames(
            frames,
            as_of_date=as_of_date,
            config=replace(base, score=score_config),
        )
        for name, score_config in ablation_score_configs(base.score).items()
    }
    snapshot_ids = {result.snapshot_id for result in results.values()}
    if len(snapshot_ids) != 1:
        raise RuntimeError("ablation variants did not share one PIT snapshot")
    coverage = build_coverage_report(data_dir, as_of_date=next(iter(results.values())).as_of_date)
    if any(dataset.status in {"FAIL", "PARTIAL", "UNKNOWN"} for dataset in coverage.datasets):
        results = {
            name: replace(
                result,
                status="PARTIAL" if result.status == "PASS" else result.status,
                warnings=tuple(dict.fromkeys((*result.warnings, "coverage_not_complete"))),
            )
            for name, result in results.items()
        }
    return results


def write_replay_artifacts(
    result: ReplayResult,
    directory: str | Path,
    *,
    label: str | None = None,
    normalized: bool = True,
) -> tuple[Path, Path]:
    """Write the ranking Parquet and a lossless normalized JSON artifact.

    ``normalized=False`` remains available for legacy consumers and tests; it
    is not the default physical layout for new artifacts.
    """

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if label is not None:
        normalized_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.")
        if not normalized_label:
            raise ValueError("artifact label must contain a filename-safe character")
        suffix = f"-{normalized_label}"
    data_path = destination / f"replay-{result.as_of_date}{suffix}.parquet"
    metadata_path = destination / f"replay-{result.as_of_date}{suffix}.json"
    result.ranked.to_parquet(data_path, index=False)
    payload = result.normalized_artifact_dict() if normalized else result.artifact_dict()
    if normalized:
        write_json_artifact(metadata_path, payload)
    else:
        metadata_path.write_bytes(serialized_json_bytes(payload))
    return data_path, metadata_path


def write_replay_variant_artifacts(
    results: dict[str, ReplayResult],
    directory: str | Path,
    *,
    normalized: bool = True,
) -> dict[str, tuple[Path, Path]]:
    return {
        name: write_replay_artifacts(result, directory, label=name, normalized=normalized)
        for name, result in results.items()
    }
