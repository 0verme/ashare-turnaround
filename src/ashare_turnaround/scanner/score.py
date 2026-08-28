"""Transparent Turnaround Score v2 with frozen weights."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any

import pandas as pd

from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION
from .contracts import TURNAROUND_TREND_CONTRACT_VERSION, FeatureVector

FEATURE_GROUP_COMPONENTS: dict[str, str] = {
    "fundamental": "fundamental_score",
    "trend": "trend_score",
    "quality": "quality_score",
    "attention": "attention_score",
    "expectation": "expectation_score",
}
DEFAULT_FEATURE_GROUPS = tuple(FEATURE_GROUP_COMPONENTS)
ABLATION_VARIANT_GROUPS: dict[str, tuple[str, ...]] = {
    "fundamental_only": ("fundamental", "trend"),
    "quality_added": ("fundamental", "trend", "quality"),
    "attention_added": ("fundamental", "trend", "quality", "attention"),
    "expectation_added": DEFAULT_FEATURE_GROUPS,
}

_QUALITY_RISK_FLAGS = {
    "profit_dominated_by_non_recurring_items",
    "non_operating_income_dominates_profit",
    "negative_operating_cash_flow",
    "inventory_pressure",
    "receivables_pressure",
    "impairment_effect",
}
_EXPECTATION_RISK_FLAGS = {"already_repriced_or_crowded"}
_POSITIVE_SIGN_TRANSITIONS = {"NEGATIVE_TO_POSITIVE", "ZERO_TO_POSITIVE"}
_NEGATIVE_SIGN_TRANSITIONS = {"POSITIVE_TO_NEGATIVE", "ZERO_TO_NEGATIVE"}


@dataclass(frozen=True, slots=True)
class ScoreConfig:
    # v2 records that legacy trend inputs now use the independently versioned
    # turnaround-trend-v2 semantics rather than adjacent-row placeholders.
    version: str = "score-v2"
    fundamental_weight: float = 0.30
    trend_weight: float = 0.20
    quality_weight: float = 0.20
    attention_weight: float = 0.15
    expectation_weight: float = 0.15
    trend_contract_version: str = TURNAROUND_TREND_CONTRACT_VERSION
    risk_penalty_cap: float = 50.0
    enabled_groups: tuple[str, ...] = DEFAULT_FEATURE_GROUPS

    def __post_init__(self) -> None:
        unknown = set(self.enabled_groups) - set(FEATURE_GROUP_COMPONENTS)
        if unknown:
            raise ValueError(f"unknown feature groups: {','.join(sorted(unknown))}")
        if len(self.enabled_groups) != len(set(self.enabled_groups)):
            raise ValueError("enabled_groups must not contain duplicates")
        if not self.enabled_groups:
            raise ValueError("at least one feature group must be enabled")
        if self.risk_penalty_cap < 0:
            raise ValueError("risk_penalty_cap must be non-negative")
        configured = {
            "fundamental": self.fundamental_weight,
            "trend": self.trend_weight,
            "quality": self.quality_weight,
            "attention": self.attention_weight,
            "expectation": self.expectation_weight,
        }
        if any(weight < 0 for weight in configured.values()):
            raise ValueError("score weights must be non-negative")
        if not any(configured[group] > 0 for group in self.enabled_groups):
            raise ValueError("at least one enabled feature group must have a positive weight")

    @property
    def weights(self) -> dict[str, float]:
        configured = {
            "fundamental": self.fundamental_weight,
            "trend": self.trend_weight,
            "quality": self.quality_weight,
            "attention": self.attention_weight,
            "expectation": self.expectation_weight,
        }
        enabled = set(self.enabled_groups)
        return {
            component: configured[group] if group in enabled else 0.0
            for group, component in FEATURE_GROUP_COMPONENTS.items()
        }

    def with_feature_groups(
        self, groups: tuple[str, ...], *, variant: str | None = None
    ) -> ScoreConfig:
        """Return an isolated score variant without mutating weights or feature inputs."""

        version = f"{self.version}/{variant}" if variant else self.version
        return replace(self, version=version, enabled_groups=groups)

    def declared(self) -> dict[str, Any]:
        """Return the complete versioned score configuration."""

        return asdict(self)

    @property
    def fingerprint(self) -> str:
        """Identify behavior-changing score settings independently of filenames."""

        payload = json.dumps(self.declared(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ablation_score_configs(config: ScoreConfig | None = None) -> dict[str, ScoreConfig]:
    """Build the four required cumulative, independently reproducible variants."""

    base = config or ScoreConfig()
    return {
        name: base.with_feature_groups(groups, variant=name)
        for name, groups in ABLATION_VARIANT_GROUPS.items()
    }


@dataclass(frozen=True, slots=True)
class ScoreResult:
    ts_code: str
    as_of_date: str
    score_version: str
    enabled_groups: tuple[str, ...]
    turnaround_score: float | None
    components: dict[str, float | None]
    weights: dict[str, float]
    penalties: dict[str, float]
    risk_flags: tuple[str, ...]
    unknown_flags: tuple[str, ...]
    rejected: bool
    rejected_reasons: tuple[str, ...]
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    trend_contract_version: str = TURNAROUND_TREND_CONTRACT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded(value: float | int | None, scale: float = 1.0) -> float | None:
    if value is None:
        return None
    return 50.0 + 50.0 * math.tanh(float(value) * scale)


def _mean_known(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and pd.notna(value)]
    return sum(clean) / len(clean) if clean else None


def _fundamental_score(values: dict[str, Any]) -> float | None:
    growth = [
        _bounded(values.get(name), 1.5)
        for name in ("revenue_yoy", "net_profit_yoy", "operating_profit_yoy")
    ]
    margins = [
        _bounded(values.get(name), 2.0)
        for name in ("gross_margin", "operating_margin", "net_margin")
    ]
    return _mean_known([*growth, *margins])


def _trend_score(values: dict[str, Any]) -> float | None:
    acceleration = _bounded(values.get("yoy_acceleration"), 0.02)
    qoq = _bounded(values.get("qoq_acceleration"), 0.02)
    persistence = values.get("consecutive_improvement")
    persistence_score = min(100.0, float(persistence) * 25.0) if persistence is not None else None
    sign_transition = values.get("sign_transition")
    transition = (
        80.0
        if sign_transition is True
        or sign_transition in _POSITIVE_SIGN_TRANSITIONS
        else 20.0
        if sign_transition is False
        or sign_transition in _NEGATIVE_SIGN_TRANSITIONS
        else 50.0
        if sign_transition == "NONE"
        else None
    )
    margin = _bounded(values.get("margin_inflection"), 5.0)
    return _mean_known([acceleration, qoq, persistence_score, transition, margin])


def _risk_flag_group(flag: str) -> str | None:
    if flag in _QUALITY_RISK_FLAGS:
        return "quality"
    if flag in _EXPECTATION_RISK_FLAGS:
        return "expectation"
    return None


def score_feature_vector(
    vector: FeatureVector,
    *,
    config: ScoreConfig | None = None,
) -> ScoreResult:
    """Score one vector with explicit component weights and missingness."""

    settings = config or ScoreConfig()
    if settings.trend_contract_version != vector.trend_contract_version:
        raise ValueError("score and feature vectors use different trend contract versions")
    values = vector.values
    components: dict[str, float | None] = {
        "fundamental_score": _fundamental_score(values),
        "trend_score": _trend_score(values),
        "quality_score": float(values["quality_score"])
        if values.get("quality_score") is not None
        else None,
        "attention_score": float(values["attention_score"])
        if values.get("attention_score") is not None
        else None,
        "expectation_score": float(values["expectation_score"])
        if values.get("expectation_score") is not None
        else None,
    }
    enabled = set(settings.enabled_groups)
    penalties: dict[str, float] = {}
    for flag in vector.risk_flags:
        group = _risk_flag_group(flag)
        if group is not None and group not in enabled:
            continue
        penalties[flag] = min(
            settings.risk_penalty_cap, 10.0 if flag.endswith("pressure") else 15.0
        )
    usable = [
        (name, value, settings.weights[name])
        for name, value in components.items()
        if value is not None
    ]
    total_weight = sum(weight for _, _, weight in usable)
    raw_score = (
        sum(float(value) * weight for _, value, weight in usable) / total_weight
        if total_weight
        else None
    )
    penalty = min(settings.risk_penalty_cap, sum(penalties.values()))
    score = max(0.0, min(100.0, raw_score - penalty)) if raw_score is not None else None
    rejected_reasons = tuple(
        reason
        for reason in vector.rejected_reasons
        if _risk_flag_group(reason) is None or _risk_flag_group(reason) in enabled
    )
    return ScoreResult(
        ts_code=vector.ts_code,
        as_of_date=vector.as_of_date,
        score_version=settings.version,
        enabled_groups=settings.enabled_groups,
        turnaround_score=score,
        components=components,
        weights=settings.weights,
        penalties=penalties,
        risk_flags=tuple(vector.risk_flags),
        unknown_flags=tuple(vector.unknown_features),
        rejected=bool(rejected_reasons),
        rejected_reasons=rejected_reasons,
        comparable_period_contract_version=vector.comparable_period_contract_version,
        trend_contract_version=vector.trend_contract_version,
    )


def rank_scores(
    scores: list[ScoreResult] | tuple[ScoreResult, ...], top_n: int | None = None
) -> pd.DataFrame:
    """Rank deterministically, placing unknown/rejected candidates after scored rows."""

    rows: list[dict[str, Any]] = []
    for result in scores:
        row = {
            "ts_code": result.ts_code,
            "as_of_date": result.as_of_date,
            "score_version": result.score_version,
            "comparable_period_contract_version": result.comparable_period_contract_version,
            "trend_contract_version": result.trend_contract_version,
            "enabled_groups": "|".join(result.enabled_groups),
            "turnaround_score": result.turnaround_score,
            "risk_flags": "|".join(result.risk_flags),
            "unknown_flags": "|".join(result.unknown_flags),
            "rejected": result.rejected,
            "rejected_reasons": "|".join(result.rejected_reasons),
        }
        row.update(result.components)
        row["score_penalties"] = sum(result.penalties.values())
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["_score_sort"] = pd.to_numeric(frame["turnaround_score"], errors="coerce").fillna(
        -float("inf")
    )
    frame = frame.sort_values(
        ["rejected", "_score_sort", "ts_code"], ascending=[True, False, True], kind="stable"
    )
    frame["rank"] = range(1, len(frame) + 1)
    frame = frame.drop(columns="_score_sort").reset_index(drop=True)
    if top_n is not None:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        scored = pd.to_numeric(frame["turnaround_score"], errors="coerce").notna()
        frame = frame.loc[~frame["rejected"] & scored].head(top_n).reset_index(drop=True)
        frame["rank"] = range(1, len(frame) + 1)
    return frame
