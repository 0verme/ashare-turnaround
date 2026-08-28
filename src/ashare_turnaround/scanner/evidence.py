"""Evidence coverage and confidence gating for scanner score results.

This module deliberately does not calculate or calibrate the turnaround score.
It describes whether the declared feature evidence is complete enough to make
that score eligible for the formal ranking.  Score magnitude and evidence
completeness therefore remain separate dimensions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .contracts import FeatureEvidence, FeatureVector

EVIDENCE_CONFIDENCE_CONTRACT_VERSION = "evidence-confidence-v1"
FEATURE_GROUP_REGISTRY_VERSION = "feature-group-registry-v1"

FEATURE_GROUP_ORDER: tuple[str, ...] = (
    "fundamental",
    "trend",
    "quality",
    "attention",
    "expectation_crowding",
)

# ``expectation`` is the pre-#31 ScoreConfig spelling.  The registry uses the
# descriptive group name while the score configuration remains backward
# compatible with the existing score-v2 API.
FEATURE_GROUP_ALIASES: dict[str, str] = {"expectation": "expectation_crowding"}

VALID_EVIDENCE_STATUSES = frozenset({"known", "valid"})
UNKNOWN_EVIDENCE_STATUSES = (
    "unknown",
    "missing",
    "stale",
    "unsupported",
    "insufficient_history",
    "insufficient_data",
    "discontinuous",
    "invalid",
    "future_unsafe",
    "future-unsafe",
    "pit_warning",
    "pit_unsafe",
    "unsupported_pit",
    "pit_unsupported",
)
_MISSING_STATUSES = frozenset({"unknown", "missing"})
_INSUFFICIENT_STATUSES = frozenset(
    {"insufficient_history", "insufficient_data", "discontinuous", "stale"}
)
_UNSUPPORTED_STATUSES = frozenset({"unsupported", "unsupported_pit", "pit_unsupported"})

_CONFIDENCE_ORDER: dict[str, int] = {
    "INSUFFICIENT": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}

@dataclass(frozen=True, slots=True)
class FeatureGroupSpec:
    """Versioned field contract for one score feature group."""

    name: str
    component: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    critical: bool = False
    valid_statuses: tuple[str, ...] = ("known", "valid")
    unknown_statuses: tuple[str, ...] = UNKNOWN_EVIDENCE_STATUSES
    field_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in FEATURE_GROUP_ORDER:
            raise ValueError(f"unknown feature group: {self.name}")
        if not self.required_fields:
            raise ValueError(f"feature group {self.name} must declare required fields")
        required = set(self.required_fields)
        optional = set(self.optional_fields)
        if len(required) != len(self.required_fields):
            raise ValueError(f"feature group {self.name} has duplicate required fields")
        if len(optional) != len(self.optional_fields):
            raise ValueError(f"feature group {self.name} has duplicate optional fields")
        if required & optional:
            raise ValueError(f"feature group {self.name} has required/optional overlap")
        if not self.valid_statuses or not set(self.valid_statuses) <= VALID_EVIDENCE_STATUSES:
            raise ValueError(f"feature group {self.name} has invalid valid_statuses")
        if not self.unknown_statuses:
            raise ValueError(f"feature group {self.name} must declare unknown statuses")
        for field_name, aliases in self.field_aliases.items():
            if field_name not in required and field_name not in optional:
                raise ValueError(
                    f"field alias is declared for an unknown field: {self.name}.{field_name}"
                )
            if len(set(aliases)) != len(aliases):
                raise ValueError(f"duplicate aliases for {self.name}.{field_name}")

    def candidates(self, field_name: str) -> tuple[str, ...]:
        """Return the explicit serialized names accepted for one contract field."""

        return (field_name, *self.field_aliases.get(field_name, ()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "component": self.component,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "critical": self.critical,
            "valid_statuses": list(self.valid_statuses),
            "unknown_statuses": list(self.unknown_statuses),
            "field_aliases": {
                key: list(value) for key, value in self.field_aliases.items()
            },
        }


# These fields are the actual production score primitives.  Fields emitted by
# the feature groups but not consumed by score.py are explicitly optional so a
# missing diagnostic does not masquerade as a missing score primitive.
FEATURE_GROUP_REGISTRY: dict[str, FeatureGroupSpec] = {}

# Fundamental/trend fields are the aliases consumed by the frozen score-v2
# formula.  Quality's aggregate and hard gate are both required; its detailed
# diagnostics are optional because they are not independently score weighted.
FEATURE_GROUP_REGISTRY.update(
    {
        "fundamental": FeatureGroupSpec(
            name="fundamental",
            component="fundamental_score",
            required_fields=(
                "revenue_yoy",
                "net_profit_yoy",
                "operating_profit_yoy",
                "gross_margin",
                "operating_margin",
                "net_margin",
            ),
            optional_fields=(
                "revenue_level",
                "net_profit_level",
                "operating_cash_flow",
                "operating_cash_flow_change",
                "cfo_to_profit",
                "roe",
                "roa",
                "inventory_yoy",
                "receivables_yoy",
                "asset_turnover",
                "gross_margin_yoy_change",
                "operating_margin_yoy_change",
                "net_margin_yoy_change",
                "fundamental_data_status",
            ),
            critical=True,
        ),
        "trend": FeatureGroupSpec(
            name="trend",
            component="trend_score",
            required_fields=(
                "yoy_acceleration",
                "qoq_acceleration",
                "consecutive_improvement",
                "sign_transition",
                "margin_inflection",
            ),
            optional_fields=(
                "ttm_trend",
                "turnaround_state",
                "turnaround_evidence",
                "yoy_level",
                "yoy_change",
                "yoy_previous_change",
                "yoy_sign_transition",
                "yoy_improvement_count",
                "yoy_persistence",
                "yoy_state",
                "yoy_turnaround_evidence",
                "qoq_level",
                "qoq_change",
                "qoq_previous_change",
                "qoq_sign_transition",
                "qoq_improvement_count",
                "qoq_persistence",
                "qoq_state",
                "qoq_turnaround_evidence",
                "ttm_level",
                "ttm_change",
                "ttm_previous_change",
                "ttm_acceleration",
                "ttm_state",
                "ttm_turnaround_evidence",
            ),
            critical=True,
        ),
        "quality": FeatureGroupSpec(
            name="quality",
            component="quality_score",
            required_fields=("quality_score", "quality_gate_status"),
            optional_fields=(
                "quality_profit",
                "adjusted_profit",
                "quality_cfo",
                "quality_cfo_to_profit",
                "quality_non_operating_ratio",
                "quality_impairment_ratio",
                "quality_inventory_change",
                "quality_receivables_change",
                "quality_leverage",
            ),
            # Quality has a separate hard rejection surface.  It is not a
            # confidence-critical completeness gate by default, but an unknown
            # quality score still lowers overall coverage and confidence.
            critical=False,
        ),
        "attention": FeatureGroupSpec(
            name="attention",
            component="attention_score",
            required_fields=(
                "turnover_percentile",
                "amount_percentile",
                "abnormal_volume",
                "attention_score",
            ),
            optional_fields=(
                # Low Attention v2 is research-only and does not replace the
                # v1 production score input.  Its declared evidence remains
                # visible and reportable without silently changing score-v2.
                "session_status",
                "liquidity_eligible",
                "liquidity_average_amount",
                "self_turnover_percentile",
                "self_amount_percentile",
                "self_volume_percentile",
                "low_attention_v2_abnormal_volume",
                "attention_baseline_change",
                "cross_section_turnover_percentile",
                "cross_section_amount_percentile",
                "cross_section_volume_percentile",
                "attention_surge",
                "low_attention_v2_score",
                "low_attention_v2_opportunity",
            ),
            field_aliases={
                "abnormal_volume": ("low_attention_v2_abnormal_volume",),
            },
            critical=True,
        ),
        "expectation_crowding": FeatureGroupSpec(
            name="expectation_crowding",
            component="expectation_score",
            required_fields=(
                "repricing_20d",
                "repricing_60d",
                "high_proximity",
                "volume_spike_penalty",
                "turnover_spike_penalty",
                "expectation_score",
            ),
            optional_fields=(
                "crowding_penalty",
                "stock_return_20d",
                "benchmark_return_20d",
                "excess_return_20d",
                "recent_return_20d",
                "recent_excess_return",
                "stock_return_60d",
                "benchmark_return_60d",
                "excess_return_60d",
                "momentum_60d",
                "distance_to_52w_high",
                "distance_52w_high",
                "high_52w",
                "current_price",
                "high_52w_window_start",
                "high_52w_window_end",
                "high_52w_obs_count",
                "volume_spike",
                "turnover_spike",
                "valuation_percentile",
                "valuation_penalty",
                "disclosure_reaction_excess",
                "disclosure_availability_date",
                "disclosure_event_date",
                "disclosure_reaction_window_start",
                "disclosure_reaction_window_end",
                "disclosure_reaction_penalty",
            ),
            critical=True,
        ),
    }
)

# Explicit field-to-group mapping used by the coverage evaluator and available
# to artifact consumers.  Aliases are listed too; no prefix/string heuristic is
# used to assign a field to a group.
FIELD_TO_GROUP: dict[str, str] = {}
for _group_name in FEATURE_GROUP_ORDER:
    _group_spec = FEATURE_GROUP_REGISTRY[_group_name]
    for _field_name in (*_group_spec.required_fields, *_group_spec.optional_fields):
        FIELD_TO_GROUP[_field_name] = _group_name
    for _field_name, _aliases in _group_spec.field_aliases.items():
        for _alias in _aliases:
            FIELD_TO_GROUP[_alias] = _group_name


@dataclass(frozen=True, slots=True)
class EvidenceConfidenceConfig:
    """Versioned, non-performance-tuned confidence and ranking policy."""

    version: str = EVIDENCE_CONFIDENCE_CONTRACT_VERSION
    registry_version: str = FEATURE_GROUP_REGISTRY_VERSION
    critical_groups: tuple[str, ...] = (
        "fundamental",
        "trend",
        "attention",
        "expectation_crowding",
    )
    high_coverage: float = 0.90
    medium_coverage: float = 0.75
    minimum_sufficient_coverage: float = 0.25
    minimum_ranking_coverage: float = 0.50
    minimum_ranking_confidence: str = "LOW"
    allow_unknown_critical_groups: bool = False

    def __post_init__(self) -> None:
        if not str(self.version).strip():
            raise ValueError("evidence confidence contract version must not be empty")
        if self.registry_version != FEATURE_GROUP_REGISTRY_VERSION:
            raise ValueError("unsupported feature group registry version")
        canonical = tuple(canonical_group_name(name) for name in self.critical_groups)
        if len(canonical) != len(set(canonical)):
            raise ValueError("critical_groups must not contain duplicates")
        unknown = set(canonical) - set(FEATURE_GROUP_ORDER)
        if unknown:
            raise ValueError(f"unknown critical feature group(s): {','.join(sorted(unknown))}")
        object.__setattr__(self, "critical_groups", canonical)
        for name in (
            "high_coverage",
            "medium_coverage",
            "minimum_sufficient_coverage",
            "minimum_ranking_coverage",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.high_coverage < self.medium_coverage:
            raise ValueError("high_coverage must be at least medium_coverage")
        if self.medium_coverage < self.minimum_sufficient_coverage:
            raise ValueError("medium_coverage must be at least minimum_sufficient_coverage")
        if self.minimum_ranking_confidence not in _CONFIDENCE_ORDER:
            raise ValueError("minimum_ranking_confidence must be a declared confidence level")

    @property
    def critical_group_set(self) -> frozenset[str]:
        return frozenset(self.critical_groups)

    def declared(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "registry_version": self.registry_version,
            "critical_groups": list(self.critical_groups),
            "high_coverage": self.high_coverage,
            "medium_coverage": self.medium_coverage,
            "minimum_sufficient_coverage": self.minimum_sufficient_coverage,
            "minimum_ranking_coverage": self.minimum_ranking_coverage,
            "minimum_ranking_confidence": self.minimum_ranking_confidence,
            "allow_unknown_critical_groups": self.allow_unknown_critical_groups,
            "coverage_unit": "ratio",
            "coverage_range": [0.0, 1.0],
            "overall_formula": "valid required fields / all required fields",
            "group_formula": "valid required fields in group / required fields in group",
            "feature_groups": {
                name: FEATURE_GROUP_REGISTRY[name].as_dict() for name in FEATURE_GROUP_ORDER
            },
            "field_to_group": dict(FIELD_TO_GROUP),
        }


def canonical_group_name(name: str) -> str:
    """Normalize the legacy score spelling to the registry spelling."""

    return FEATURE_GROUP_ALIASES.get(str(name), str(name))


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {
            "",
            "null",
            "nan",
            "nat",
            "na",
            "n/a",
            "unknown",
            "unsupported",
            "stale",
            "missing",
            "invalid",
            "insufficient_history",
            "insufficient_data",
            "discontinuous",
            "future_unsafe",
            "future-unsafe",
            "pit_warning",
            "pit_unsafe",
            "no_data",
            "suspended_session",
        }
    if isinstance(value, bool):
        return False
    try:
        missing = pd.isna(value)
        if missing is pd.NA:
            return True
        if isinstance(missing, bool) and missing:
            return True
        try:
            if bool(missing):
                return True
        except (TypeError, ValueError):
            pass
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return not math.isfinite(float(value))
    return False


def _normalized_status(evidence: FeatureEvidence | None) -> str:
    return (
        str(evidence.status if evidence is not None else "missing")
        .strip()
        .casefold()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _normalized_reason(evidence: FeatureEvidence | None) -> str:
    if evidence is None:
        return ""
    return str(evidence.reason or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _contains_pit_warning(value: Any, *, key: str = "") -> bool:
    """Detect explicit PIT warnings without treating ordinary provenance as bad."""

    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in {"pit_warning", "pit_unsafe", "future_unsafe", "future_unsafe_warning"}:
        return bool(value)
    if "pit" in normalized_key and ("warning" in normalized_key or "unsafe" in normalized_key):
        return bool(value)
    if "safe" in normalized_key and "pit" in normalized_key and value is False:
        return True
    if normalized_key in {"warnings", "warning", "reason"}:
        if isinstance(value, str):
            text = value.casefold().replace("-", "_").replace(" ", "_")
            return any(token in text for token in ("pit_warning", "pit_unsafe", "future_unsafe"))
        if isinstance(value, (list, tuple, set)):
            return any(_contains_pit_warning(item, key="warning") for item in value)
    if isinstance(value, Mapping):
        return any(
            _contains_pit_warning(item, key=str(item_key)) for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_pit_warning(item, key=normalized_key) for item in value)
    return False


def _field_category(
    evidence: FeatureEvidence | None,
    value: Any,
) -> tuple[bool, str, str | None]:
    """Return valid/category/reason for one explicit evidence record."""

    if evidence is None:
        return False, "missing", "missing_evidence"
    status = _normalized_status(evidence)
    reason = _normalized_reason(evidence)
    if _contains_pit_warning(evidence.metadata) or _contains_pit_warning(evidence.provenance):
        return False, "invalid", evidence.reason or "pit_warning"
    if _contains_pit_warning(evidence.components) or _contains_pit_warning(evidence.config):
        return False, "invalid", evidence.reason or "pit_warning"
    if (
        status in _UNSUPPORTED_STATUSES
        or "unsupported" in status
        or "unsupported" in reason
    ):
        return False, "unsupported", evidence.reason or status
    if any(token in reason for token in ("unsupported",)):
        return False, "unsupported", evidence.reason or "unsupported"
    if any(
        token in reason
        for token in (
            "pit_warning",
            "pit_unsafe",
            "future_unsafe",
            "stale",
            "insufficient",
            "discontinuous",
            "invalid",
            "denominator",
            "zero_baseline",
        )
    ):
        return False, "invalid", evidence.reason or "invalid_evidence"
    if status in {"stale", "future_unsafe", "future-unsafe", "pit_warning", "pit_unsafe"}:
        return False, "invalid", evidence.reason or status
    if _is_missing_value(value) or _is_missing_value(evidence.value):
        if status in _INSUFFICIENT_STATUSES or any(
            token in reason
            for token in (
                "insufficient",
                "discontinuous",
                "stale",
                "pit_warning",
                "future_unsafe",
                "invalid",
                "denominator",
                "zero_baseline",
            )
        ):
            return False, "invalid", evidence.reason or status
        return False, "missing", evidence.reason or status or "missing_value"
    if status in VALID_EVIDENCE_STATUSES:
        return True, "valid", None
    if status in _MISSING_STATUSES:
        if any(token in reason for token in ("unsupported",)):
            return False, "unsupported", evidence.reason or status
        if any(
            token in reason
            for token in (
                "insufficient",
                "discontinuous",
                "stale",
                "pit_warning",
                "future_unsafe",
                "invalid",
                "denominator",
                "zero_baseline",
            )
        ):
            return False, "invalid", evidence.reason or status
        return False, "missing", evidence.reason or status
    if status in _INSUFFICIENT_STATUSES:
        return False, "invalid", evidence.reason or status
    # An unrecognised status is not evidence.  It is intentionally not treated
    # as valid merely because a numeric value happens to be present.
    return False, "invalid", evidence.reason or f"unrecognised_status:{evidence.status}"


def _resolve_evidence(
    vector: FeatureVector,
    spec: FeatureGroupSpec,
    field_name: str,
) -> tuple[str | None, FeatureEvidence | None, Any]:
    for candidate in spec.candidates(field_name):
        if candidate in vector.evidence:
            return candidate, vector.evidence[candidate], vector.values.get(
                candidate, vector.evidence[candidate].value
            )
    # Values without FeatureEvidence are deliberately not accepted as valid.
    return None, None, vector.values.get(field_name)


def _status_for_field(
    vector: FeatureVector,
    spec: FeatureGroupSpec,
    field_name: str,
) -> dict[str, Any]:
    resolved, evidence, value = _resolve_evidence(vector, spec, field_name)
    valid, category, reason = _field_category(evidence, value)
    return {
        "field": field_name,
        "resolved_field": resolved,
        "value": value,
        "status": str(evidence.status) if evidence is not None else "missing",
        "reason": reason or (evidence.reason if evidence is not None else "missing_evidence"),
        "valid": valid,
        "category": category,
    }


def _group_status(valid_count: int, required_count: int, states: list[dict[str, Any]]) -> str:
    if valid_count == required_count:
        return "COMPLETE"
    if valid_count > 0:
        return "PARTIAL"
    categories = {state["category"] for state in states}
    if categories & {"invalid", "unsupported"}:
        return "INSUFFICIENT"
    return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    """Complete coverage/confidence/eligibility result for one vector."""

    evidence_confidence_contract_version: str
    feature_group_registry_version: str
    evidence_coverage: float
    group_coverage: dict[str, float]
    group_status: dict[str, str]
    coverage: dict[str, dict[str, Any]]
    confidence: str
    unknown_groups: tuple[str, ...]
    incomplete_groups: tuple[str, ...]
    missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]
    unsupported_fields: tuple[str, ...]
    ranking_eligible: bool
    eligibility_reason: str
    policy: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "evidence_confidence_contract_version": self.evidence_confidence_contract_version,
            "feature_group_registry_version": self.feature_group_registry_version,
            "evidence_coverage": self.evidence_coverage,
            "group_coverage": dict(self.group_coverage),
            "group_status": dict(self.group_status),
            "coverage": self.coverage,
            "confidence": self.confidence,
            "unknown_groups": list(self.unknown_groups),
            "incomplete_groups": list(self.incomplete_groups),
            "missing_fields": list(self.missing_fields),
            "invalid_fields": list(self.invalid_fields),
            "unsupported_fields": list(self.unsupported_fields),
            "ranking_eligible": self.ranking_eligible,
            "eligibility_reason": self.eligibility_reason,
            "policy": dict(self.policy),
        }
        for group_name, coverage in self.group_coverage.items():
            payload[f"{group_name}_coverage"] = coverage
        return payload


def _eligibility_reason(
    *,
    assessment: EvidenceAssessment,
    turnaround_score: float | None,
    rejected: bool,
    rejected_reasons: tuple[str, ...],
    config: EvidenceConfidenceConfig,
) -> tuple[bool, str]:
    if rejected:
        detail = ",".join(rejected_reasons) or "unspecified_rejection"
        return False, f"rejected:{detail}"
    if turnaround_score is None or _is_missing_value(turnaround_score):
        return False, "turnaround_score_unknown"
    if (
        not config.allow_unknown_critical_groups
        and any(assessment.group_coverage.get(name, 0.0) <= 0.0 for name in config.critical_groups)
    ):
        unknown = [
            name
            for name in config.critical_groups
            if assessment.group_coverage.get(name, 0.0) <= 0.0
        ]
        return False, "critical_group_unknown:" + ",".join(unknown)
    if assessment.evidence_coverage < config.minimum_ranking_coverage:
        return (
            False,
            "evidence_coverage_below_minimum:"
            f"{assessment.evidence_coverage:.6f}<{config.minimum_ranking_coverage:.6f}",
        )
    if (
        _CONFIDENCE_ORDER[assessment.confidence]
        < _CONFIDENCE_ORDER[config.minimum_ranking_confidence]
    ):
        return False, f"confidence_below_minimum:{assessment.confidence}"
    return True, "eligible"


def assess_evidence_coverage(
    vector: FeatureVector,
    *,
    config: EvidenceConfidenceConfig | None = None,
    turnaround_score: float | None = None,
    rejected: bool = False,
    rejected_reasons: tuple[str, ...] = (),
) -> EvidenceAssessment:
    """Evaluate explicit field evidence under the frozen v1 contract.

    Coverage is a plain required-field ratio.  It is never weighted by score
    weights and never inferred from the presence of keys in ``values`` alone.
    ``known`` and ``valid`` are the only valid statuses.  PIT warnings,
    unsupported values, stale observations, and insufficient history cannot
    contribute a valid field.
    """

    settings = config or EvidenceConfidenceConfig()
    group_coverage: dict[str, float] = {}
    group_status: dict[str, str] = {}
    coverage: dict[str, dict[str, Any]] = {}
    missing_fields: list[str] = []
    invalid_fields: list[str] = []
    unsupported_fields: list[str] = []

    total_required = 0
    total_valid = 0
    for group_name in FEATURE_GROUP_ORDER:
        spec = FEATURE_GROUP_REGISTRY[group_name]
        required_states = [
            _status_for_field(vector, spec, field_name) for field_name in spec.required_fields
        ]
        optional_states = [
            _status_for_field(vector, spec, field_name) for field_name in spec.optional_fields
        ]
        valid_count = sum(1 for state in required_states if state["valid"])
        required_count = len(required_states)
        group_coverage[group_name] = valid_count / required_count
        status = _group_status(valid_count, required_count, required_states)
        group_status[group_name] = status
        total_required += required_count
        total_valid += valid_count
        group_missing = [
            state["field"] for state in required_states if state["category"] == "missing"
        ]
        group_invalid = [
            state["field"] for state in required_states if state["category"] == "invalid"
        ]
        group_unsupported = [
            state["field"] for state in required_states if state["category"] == "unsupported"
        ]
        optional_missing = [
            state["field"] for state in optional_states if state["category"] == "missing"
        ]
        optional_invalid = [
            state["field"] for state in optional_states if state["category"] == "invalid"
        ]
        optional_unsupported = [
            state["field"] for state in optional_states if state["category"] == "unsupported"
        ]
        for state in required_states:
            if state["category"] == "missing":
                missing_fields.append(state["field"])
            elif state["category"] == "invalid":
                invalid_fields.append(state["field"])
            elif state["category"] == "unsupported":
                unsupported_fields.append(state["field"])
        coverage[group_name] = {
            "group": group_name,
            "component": spec.component,
            "critical": spec.critical,
            "status": status,
            "required_count": required_count,
            "valid_count": valid_count,
            "field_coverage": group_coverage[group_name],
            "required_fields": list(spec.required_fields),
            "optional_fields": list(spec.optional_fields),
            "field_statuses": required_states,
            "optional_field_statuses": optional_states,
            "missing_fields": group_missing,
            "invalid_fields": group_invalid,
            "unsupported_fields": group_unsupported,
            "optional_missing_fields": optional_missing,
            "optional_invalid_fields": optional_invalid,
            "optional_unsupported_fields": optional_unsupported,
        }

    overall = total_valid / total_required if total_required else 0.0
    coverage["overall"] = {
        "required_count": total_required,
        "valid_count": total_valid,
        "field_coverage": overall,
        "coverage_unit": "ratio",
        "formula": "valid required fields / all required fields",
        "missing_fields": list(missing_fields),
        "invalid_fields": list(invalid_fields),
        "unsupported_fields": list(unsupported_fields),
    }
    unknown_groups = tuple(
        name for name in FEATURE_GROUP_ORDER if group_coverage[name] <= 0.0
    )
    incomplete_groups = tuple(
        name for name in FEATURE_GROUP_ORDER if group_coverage[name] < 1.0
    )
    unknown_critical = tuple(
        name for name in settings.critical_groups if group_coverage[name] <= 0.0
    )
    if overall < settings.minimum_sufficient_coverage or not total_valid:
        confidence = "INSUFFICIENT"
    elif unknown_critical and not settings.allow_unknown_critical_groups:
        confidence = "INSUFFICIENT"
    elif overall >= settings.high_coverage and all(
        group_coverage[name] >= 1.0 for name in settings.critical_groups
    ):
        confidence = "HIGH"
    elif overall >= settings.medium_coverage and not unknown_critical:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    policy = settings.declared()
    provisional = EvidenceAssessment(
        evidence_confidence_contract_version=settings.version,
        feature_group_registry_version=settings.registry_version,
        evidence_coverage=overall,
        group_coverage=group_coverage,
        group_status=group_status,
        coverage=coverage,
        confidence=confidence,
        unknown_groups=unknown_groups,
        incomplete_groups=incomplete_groups,
        missing_fields=tuple(missing_fields),
        invalid_fields=tuple(invalid_fields),
        unsupported_fields=tuple(unsupported_fields),
        ranking_eligible=False,
        eligibility_reason="not_evaluated",
        policy=policy,
    )
    eligible, reason = _eligibility_reason(
        assessment=provisional,
        turnaround_score=turnaround_score,
        rejected=rejected,
        rejected_reasons=rejected_reasons,
        config=settings,
    )
    return EvidenceAssessment(
        evidence_confidence_contract_version=provisional.evidence_confidence_contract_version,
        feature_group_registry_version=provisional.feature_group_registry_version,
        evidence_coverage=provisional.evidence_coverage,
        group_coverage=provisional.group_coverage,
        group_status=provisional.group_status,
        coverage=provisional.coverage,
        confidence=provisional.confidence,
        unknown_groups=provisional.unknown_groups,
        incomplete_groups=provisional.incomplete_groups,
        missing_fields=provisional.missing_fields,
        invalid_fields=provisional.invalid_fields,
        unsupported_fields=provisional.unsupported_fields,
        ranking_eligible=eligible,
        eligibility_reason=reason,
        policy=provisional.policy,
    )


# Friendly aliases for callers that prefer the nouns used by the issue text.
evaluate_evidence_confidence = assess_evidence_coverage
compute_evidence_coverage = assess_evidence_coverage


__all__ = [
    "EVIDENCE_CONFIDENCE_CONTRACT_VERSION",
    "FEATURE_GROUP_REGISTRY_VERSION",
    "FEATURE_GROUP_ORDER",
    "FEATURE_GROUP_ALIASES",
    "FEATURE_GROUP_REGISTRY",
    "FIELD_TO_GROUP",
    "FeatureGroupSpec",
    "EvidenceConfidenceConfig",
    "EvidenceAssessment",
    "VALID_EVIDENCE_STATUSES",
    "UNKNOWN_EVIDENCE_STATUSES",
    "assess_evidence_coverage",
    "compute_evidence_coverage",
    "evaluate_evidence_confidence",
    "canonical_group_name",
]
