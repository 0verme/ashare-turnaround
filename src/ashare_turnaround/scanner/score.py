"""Transparent Turnaround Score v1."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from .contracts import FeatureVector


@dataclass(frozen=True, slots=True)
class ScoreConfig:
    version: str = "score-v1"
    fundamental_weight: float = 0.30
    trend_weight: float = 0.20
    quality_weight: float = 0.20
    attention_weight: float = 0.15
    expectation_weight: float = 0.15
    risk_penalty_cap: float = 50.0

    @property
    def weights(self) -> dict[str, float]:
        return {
            "fundamental_score": self.fundamental_weight,
            "trend_score": self.trend_weight,
            "quality_score": self.quality_weight,
            "attention_score": self.attention_weight,
            "expectation_score": self.expectation_weight,
        }


@dataclass(frozen=True, slots=True)
class ScoreResult:
    ts_code: str
    as_of_date: str
    score_version: str
    turnaround_score: float | None
    components: dict[str, float | None]
    weights: dict[str, float]
    penalties: dict[str, float]
    risk_flags: tuple[str, ...]
    unknown_flags: tuple[str, ...]
    rejected: bool
    rejected_reasons: tuple[str, ...]

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
    transition = (
        80.0
        if values.get("sign_transition") is True
        else 50.0
        if values.get("sign_transition") is False
        else None
    )
    margin = _bounded(values.get("margin_inflection"), 5.0)
    return _mean_known([acceleration, qoq, persistence_score, transition, margin])


def score_feature_vector(
    vector: FeatureVector,
    *,
    config: ScoreConfig | None = None,
) -> ScoreResult:
    """Score one vector with explicit component weights and missingness."""

    settings = config or ScoreConfig()
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
    penalties: dict[str, float] = {}
    for flag in vector.risk_flags:
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
    return ScoreResult(
        ts_code=vector.ts_code,
        as_of_date=vector.as_of_date,
        score_version=settings.version,
        turnaround_score=score,
        components=components,
        weights=settings.weights,
        penalties=penalties,
        risk_flags=tuple(vector.risk_flags),
        unknown_flags=tuple(vector.unknown_features),
        rejected=vector.rejected,
        rejected_reasons=tuple(vector.rejected_reasons),
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
        ["_score_sort", "rejected", "ts_code"], ascending=[False, True, True], kind="stable"
    )
    frame["rank"] = range(1, len(frame) + 1)
    frame = frame.drop(columns="_score_sort").reset_index(drop=True)
    if top_n is not None:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        frame = frame.head(top_n).reset_index(drop=True)
    return frame
