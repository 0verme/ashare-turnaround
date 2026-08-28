"""Versioned, provenance-first turnaround trend semantics.

The trend layer deliberately does not identify financial periods.  It consumes
only the period identities and validated comparable primitives produced by
``comparable-period-v1``.  In particular, a second difference of an absolute
profit series is *not* a YoY acceleration.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..pit.comparable import (
    COMPARABLE_PERIOD_CONTRACT_VERSION,
    DerivedMetric,
    growth_from_match,
    margin_from_row,
    match_comparable_period,
    period_identity,
    ttm_from_series,
)
from ..scanner.contracts import TURNAROUND_TREND_CONTRACT_VERSION, FeatureVector
from .common import (
    canonical_history,
    new_vector,
    single_quarter_history,
)

TREND_CONTRACT_VERSION = TURNAROUND_TREND_CONTRACT_VERSION

# Evidence statuses intentionally remain lower-case to match the existing
# FeatureEvidence convention.  The public constants make the contract values
# explicit for callers that do not want to repeat string literals.
VALID = "valid"
UNKNOWN_STATUS = "unknown"
UNKNOWN = UNKNOWN_STATUS
INSUFFICIENT_HISTORY = "insufficient_history"
DISCONTINUOUS = "discontinuous"
UNSUPPORTED = "unsupported"

NEGATIVE_TO_POSITIVE = "NEGATIVE_TO_POSITIVE"
POSITIVE_TO_NEGATIVE = "POSITIVE_TO_NEGATIVE"
ZERO_TO_POSITIVE = "ZERO_TO_POSITIVE"
ZERO_TO_NEGATIVE = "ZERO_TO_NEGATIVE"
TO_ZERO = "TO_ZERO"
NONE = "NONE"

STRONG_TURNAROUND = "STRONG_TURNAROUND"
IMPROVING = "IMPROVING"
STABLE = "STABLE"
DETERIORATING = "DETERIORATING"
INSUFFICIENT = "INSUFFICIENT"

RATE_UNIT = "ratio"
PERCENTAGE_POINT_UNIT = "percentage_points"
SOURCE_UNIT = "source_units"
PERIOD_UNIT = "periods"

_UNSUPPORTED_REASONS = {
    "unsupported_comparison",
    "unsupported_report_period",
    "unsupported_duration_semantics",
    "unsupported_report_family",
    "unsupported_statement_type",
    "unsupported_quarterization",
    "unknown_scope",
    "unknown_unit",
    "unknown_accounting_semantics",
    "missing_source_version",
    "invalid_comparable_period_contract",
}

_PERIOD_RE = re.compile(r"^(?P<year>\d{4})\s*(?:Q(?P<quarter>[1-4])|H1|FY)$", re.I)


@dataclass(frozen=True, slots=True)
class ValidatedTrendObservation:
    """One already-validated observation consumed by the trend contract.

    ``value`` is a growth/margin ratio for ``value_kind='rate'`` and an
    absolute TTM value for ``value_kind='absolute'``.  A raw observation is
    never accepted as evidence merely because it has a date: its status and
    comparable-period contract version are carried alongside it.
    """

    period: str | None
    value: float | None
    comparison_period: str | None = None
    status: str = VALID
    reason: str | None = None
    current_raw_value: float | None = None
    comparison_raw_value: float | None = None
    underlying_sign_transition: str | None = None
    availability_dates: tuple[str, ...] = ()
    source_versions: tuple[str, ...] = ()
    source_datasets: tuple[str, ...] = ()
    source_fields: tuple[str, ...] = ()
    source_chain: tuple[Mapping[str, Any], ...] = ()
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def growth_rate(self) -> float | None:
        """Compatibility name for rate observations."""

        return self.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "comparison_period": self.comparison_period,
            "value": self.value,
            "growth_rate": self.value,
            "status": self.status,
            "reason": self.reason,
            "current_raw_value": self.current_raw_value,
            "comparison_raw_value": self.comparison_raw_value,
            "underlying_sign_transition": self.underlying_sign_transition,
            "availability_date": self.availability_dates[0]
            if self.availability_dates
            else None,
            "source_version": self.source_versions[0] if self.source_versions else None,
            "source_dataset": self.source_datasets[0] if self.source_datasets else None,
            "source_field": self.source_fields[0] if self.source_fields else None,
            "availability_dates": list(self.availability_dates),
            "source_versions": list(self.source_versions),
            "source_datasets": list(self.source_datasets),
            "source_fields": list(self.source_fields),
            "source_chain": [dict(record) for record in self.source_chain],
            "comparable_period_contract_version": self.comparable_period_contract_version,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class TrendSummary:
    """Deterministic result for one level/change/acceleration series."""

    metric: str
    value_kind: str
    unit: str
    status: str
    reason: str | None
    level: float | None
    previous_level: float | None
    older_level: float | None
    change: float | None
    previous_change: float | None
    acceleration: float | None
    sign_transition: str | None
    source_sign_transition: str | None
    improvement_count: int | None
    deterioration_count: int | None
    persistence: str | None
    state: str
    turnaround_evidence: str
    level_status: str
    level_reason: str | None
    change_status: str
    change_reason: str | None
    acceleration_status: str
    acceleration_reason: str | None
    sign_transition_status: str
    sign_transition_reason: str | None
    persistence_status: str
    persistence_reason: str | None
    current_period: str | None
    previous_period: str | None
    older_period: str | None
    current_availability_date: str | None
    comparison_availability_date: str | None
    availability_dates: tuple[str, ...]
    source_versions: tuple[str, ...]
    source_datasets: tuple[str, ...]
    source_fields: tuple[str, ...]
    source_chain: tuple[Mapping[str, Any], ...]
    observations: tuple[ValidatedTrendObservation, ...]
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    trend_contract_version: str = TREND_CONTRACT_VERSION
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def level_unit(self) -> str:
        return RATE_UNIT if self.value_kind == "rate" else SOURCE_UNIT

    def component_unit(self, name: str) -> str:
        if name == "level":
            return self.level_unit
        if name == "improvement_count":
            return PERIOD_UNIT
        if name in {"sign_transition", "persistence", "state", "turnaround_evidence"}:
            return "category"
        return self.unit

    def component(self, name: str) -> tuple[Any, str, str | None]:
        """Return value/status/reason for a feature component."""

        if name == "level":
            return self.level, self.level_status, self.level_reason
        if name == "change":
            return self.change, self.change_status, self.change_reason
        if name == "previous_change":
            return (
                self.previous_change,
                self.acceleration_status,
                self.acceleration_reason,
            )
        if name == "acceleration":
            return self.acceleration, self.acceleration_status, self.acceleration_reason
        if name == "sign_transition":
            return (
                self.sign_transition,
                self.sign_transition_status,
                self.sign_transition_reason,
            )
        if name == "improvement_count":
            return (
                self.improvement_count,
                self.persistence_status,
                self.persistence_reason,
            )
        if name == "persistence":
            return self.persistence, self.persistence_status, self.persistence_reason
        if name == "state":
            state_status = self.change_status
            return self.state, state_status, self.change_reason
        if name == "turnaround_evidence":
            return self.turnaround_evidence, self.change_status, self.change_reason
        raise KeyError(f"unknown trend component: {name}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value_kind": self.value_kind,
            "unit": self.unit,
            "level_unit": self.level_unit,
            "status": self.status,
            "reason": self.reason,
            "level": self.level,
            "previous_level": self.previous_level,
            "older_level": self.older_level,
            "current_value": self.level,
            "previous_value": self.previous_level,
            "older_value": self.older_level,
            "change": self.change,
            "previous_change": self.previous_change,
            "acceleration": self.acceleration,
            "sign_transition": self.sign_transition,
            "source_sign_transition": self.source_sign_transition,
            "improvement_count": self.improvement_count,
            "deterioration_count": self.deterioration_count,
            "persistence": self.persistence,
            "state": self.state,
            "turnaround_evidence": self.turnaround_evidence,
            "level_status": self.level_status,
            "level_reason": self.level_reason,
            "change_status": self.change_status,
            "change_reason": self.change_reason,
            "acceleration_status": self.acceleration_status,
            "acceleration_reason": self.acceleration_reason,
            "sign_transition_status": self.sign_transition_status,
            "sign_transition_reason": self.sign_transition_reason,
            "persistence_status": self.persistence_status,
            "persistence_reason": self.persistence_reason,
            "current_period": self.current_period,
            "previous_period": self.previous_period,
            "older_period": self.older_period,
            "current_growth": self.level,
            "previous_growth": self.previous_level,
            "older_growth": self.older_level,
            "current_change": self.change,
            "current_availability_date": self.current_availability_date,
            "comparison_availability_date": self.comparison_availability_date,
            "availability_date": self.current_availability_date,
            "source_version": self.source_versions[0] if self.source_versions else None,
            "source_dataset": self.source_datasets[0] if self.source_datasets else None,
            "source_field": self.source_fields[0] if self.source_fields else None,
            "availability_dates": list(self.availability_dates),
            "source_versions": list(self.source_versions),
            "source_datasets": list(self.source_datasets),
            "source_fields": list(self.source_fields),
            "source_chain": [dict(record) for record in self.source_chain],
            "observations": [observation.as_dict() for observation in self.observations],
            "comparable_period_contract_version": self.comparable_period_contract_version,
            "trend_contract_version": self.trend_contract_version,
            "provenance": dict(self.provenance),
        }


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return None
    result = float(parsed)
    return result if math.isfinite(result) else None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).strftime("%Y%m%d")


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part for part in value.split("|") if part and part.lower() != "nan")
    try:
        return tuple(str(part) for part in value if part is not None and str(part) != "nan")
    except TypeError:
        return (str(value),)


def _period_number(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().upper().replace(" ", "")
    match = _PERIOD_RE.fullmatch(text)
    if match:
        quarter = int(match.group("quarter") or (2 if "H1" in text else 4))
        return int(match.group("year")) * 4 + quarter - 1
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        digits = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
        if digits:
            parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        return None
    quarter = (pd.Timestamp(parsed).month - 1) // 3 + 1
    return int(pd.Timestamp(parsed).year) * 4 + quarter - 1


def _period_sort_key(observation: ValidatedTrendObservation, index: int) -> tuple[int, int]:
    number = _period_number(observation.period)
    return (number if number is not None else 10**12, index)


def _normalise_status(status: Any) -> str:
    text = str(status or VALID).strip().lower()
    if text in {"known", VALID}:
        return VALID
    if text in {"insufficient_data", INSUFFICIENT_HISTORY}:
        return INSUFFICIENT_HISTORY
    if text == DISCONTINUOUS:
        return DISCONTINUOUS
    if text == UNSUPPORTED:
        return UNSUPPORTED
    return UNKNOWN_STATUS


def _normalise_source_transition(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    aliases = {
        "negative_to_positive": NEGATIVE_TO_POSITIVE,
        "positive_to_negative": POSITIVE_TO_NEGATIVE,
        "zero_to_positive": ZERO_TO_POSITIVE,
        "zero_to_negative": ZERO_TO_NEGATIVE,
        "nonzero_to_zero": TO_ZERO,
    }
    known = {
        NEGATIVE_TO_POSITIVE,
        POSITIVE_TO_NEGATIVE,
        ZERO_TO_POSITIVE,
        ZERO_TO_NEGATIVE,
        TO_ZERO,
        NONE,
    }
    return aliases.get(text.lower(), text if text in known else None)


def _observation_from_mapping(
    value: ValidatedTrendObservation | Mapping[str, Any],
    *,
    value_kind: str,
) -> ValidatedTrendObservation:
    if isinstance(value, ValidatedTrendObservation):
        observation = value
    else:
        period = value.get(
            "period",
            value.get("current_period", value.get("report_period")),
        )
        raw_value = value.get(
            "value",
            value.get(
                "growth_rate",
                value.get(
                    "growth",
                    value.get("level", value.get("ttm_value", value.get("current_value"))),
                ),
            ),
        )
        input_unit = str(value.get("unit", value.get("value_unit", ""))).lower()
        parsed_value = _finite(raw_value)
        # Financial growth primitives use ratios (0.10 == 10%).  Accept an
        # explicit percent input for the standalone semantic helper without
        # guessing the unit of an unlabelled primitive.
        if parsed_value is not None and value_kind == "rate" and input_unit in {
            "percent",
            "%",
        }:
            parsed_value /= 100.0
        contract = str(
            value.get(
                "comparable_period_contract_version",
                value.get(
                    "source_contract_version",
                    value.get("contract_version", COMPARABLE_PERIOD_CONTRACT_VERSION),
                ),
            )
        )
        status = _normalise_status(value.get("status", VALID))
        reason = value.get("reason")
        if contract != COMPARABLE_PERIOD_CONTRACT_VERSION:
            status, reason = UNSUPPORTED, "invalid_comparable_period_contract"
        elif status == VALID and parsed_value is None:
            status, reason = UNKNOWN_STATUS, reason or "missing_value"
        observation = ValidatedTrendObservation(
            period=None if period is None else str(period),
            value=parsed_value if status == VALID else None,
            comparison_period=(
                None
                if value.get("comparison_period") is None
                else str(value.get("comparison_period"))
            ),
            status=status,
            reason=reason,
            current_raw_value=_finite(value.get("current_raw_value")),
            comparison_raw_value=_finite(value.get("comparison_raw_value")),
            underlying_sign_transition=_normalise_source_transition(
                value.get("underlying_sign_transition", value.get("source_sign_transition"))
            ),
            availability_dates=_as_tuple(
                value.get("availability_dates", value.get("availability_date"))
            ),
            source_versions=_as_tuple(
                value.get("source_versions", value.get("source_version"))
            ),
            source_datasets=_as_tuple(
                value.get("source_datasets", value.get("source_dataset"))
            ),
            source_fields=_as_tuple(value.get("source_fields", value.get("source_field"))),
            source_chain=tuple(
                record for record in value.get("source_chain", ()) if isinstance(record, Mapping)
            ),
            comparable_period_contract_version=contract,
            provenance=dict(value.get("provenance", {})),
        )
        if observation.period is None:
            return replace(
                observation,
                value=None,
                status=UNSUPPORTED,
                reason="unsupported_report_period",
            )
    status = _normalise_status(observation.status)
    parsed_value = _finite(observation.value)
    reason = observation.reason
    if observation.comparable_period_contract_version != COMPARABLE_PERIOD_CONTRACT_VERSION:
        status, parsed_value, reason = (
            UNSUPPORTED,
            None,
            "invalid_comparable_period_contract",
        )
    elif observation.period is None:
        status, parsed_value, reason = UNSUPPORTED, None, "unsupported_report_period"
    elif status == VALID and parsed_value is None:
        status, reason = UNKNOWN_STATUS, reason or "missing_value"
    return replace(
        observation,
        value=parsed_value if status == VALID else None,
        status=status,
        reason=reason,
    )


def _invalid_observation(
    *,
    metric: str,
    reason: str,
    period: str | None = None,
    status: str = UNKNOWN_STATUS,
    value_kind: str = "rate",
) -> ValidatedTrendObservation:
    del metric, value_kind
    return ValidatedTrendObservation(
        period=period,
        value=None,
        status=status,
        reason=reason,
        comparable_period_contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
    )


def _difference(current: float, previous: float, *, scale: float) -> float | None:
    raw = current - previous
    if not math.isfinite(raw):
        return None
    result = raw * scale
    return round(result, 12) if math.isfinite(result) else None


def _pair_issue(
    previous: ValidatedTrendObservation,
    current: ValidatedTrendObservation,
) -> tuple[str | None, str | None]:
    if (
        previous.comparable_period_contract_version != COMPARABLE_PERIOD_CONTRACT_VERSION
        or current.comparable_period_contract_version != COMPARABLE_PERIOD_CONTRACT_VERSION
    ):
        return UNSUPPORTED, "invalid_comparable_period_contract"
    if previous.status != VALID:
        return previous.status, previous.reason or "unknown_observation"
    if current.status != VALID:
        return current.status, current.reason or "unknown_observation"
    if previous.value is None or current.value is None:
        return UNKNOWN_STATUS, "missing_value"
    old_number = _period_number(previous.period)
    new_number = _period_number(current.period)
    if old_number is None or new_number is None:
        return DISCONTINUOUS, "unsupported_period_sequence"
    if new_number != old_number + 1:
        return DISCONTINUOUS, "discontinuous_periods"
    return None, None


def _aggregate_source(
    observations: Sequence[ValidatedTrendObservation],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[Mapping[str, Any], ...],
]:
    availability: list[str] = []
    versions: list[str] = []
    datasets: list[str] = []
    fields: list[str] = []
    chain: list[Mapping[str, Any]] = []
    for observation in observations:
        for value, target in (
            (observation.availability_dates, availability),
            (observation.source_versions, versions),
            (observation.source_datasets, datasets),
            (observation.source_fields, fields),
        ):
            for item in value:
                if item not in target:
                    target.append(item)
        chain.extend(observation.source_chain)
    return tuple(availability), tuple(versions), tuple(datasets), tuple(fields), tuple(chain)


def _empty_summary(
    metric: str,
    *,
    value_kind: str,
    status: str,
    reason: str,
    observations: Sequence[ValidatedTrendObservation] = (),
) -> TrendSummary:
    unit = PERCENTAGE_POINT_UNIT if value_kind == "rate" else SOURCE_UNIT
    availability, versions, datasets, fields, chain = _aggregate_source(observations)
    return TrendSummary(
        metric=metric,
        value_kind=value_kind,
        unit=unit,
        status=status,
        reason=reason,
        level=None,
        previous_level=None,
        older_level=None,
        change=None,
        previous_change=None,
        acceleration=None,
        sign_transition=None,
        source_sign_transition=None,
        improvement_count=None,
        deterioration_count=None,
        persistence=None,
        state=INSUFFICIENT,
        turnaround_evidence="unknown",
        level_status=status,
        level_reason=reason,
        change_status=status,
        change_reason=reason,
        acceleration_status=status,
        acceleration_reason=reason,
        sign_transition_status=status,
        sign_transition_reason=reason,
        persistence_status=status,
        persistence_reason=reason,
        current_period=observations[-1].period if observations else None,
        previous_period=observations[-2].period if len(observations) >= 2 else None,
        older_period=observations[-3].period if len(observations) >= 3 else None,
        current_availability_date=(
            observations[-1].availability_dates[0]
            if observations and observations[-1].availability_dates
            else None
        ),
        comparison_availability_date=(
            observations[-2].availability_dates[0]
            if len(observations) >= 2 and observations[-2].availability_dates
            else None
        ),
        availability_dates=availability,
        source_versions=versions,
        source_datasets=datasets,
        source_fields=fields,
        source_chain=chain,
        observations=tuple(observations),
        provenance={
            "unit": unit,
            "observations": [observation.as_dict() for observation in observations],
        },
    )


def _sign_transition(previous: float, current: float) -> str:
    if previous < 0 < current:
        return NEGATIVE_TO_POSITIVE
    if previous > 0 > current:
        return POSITIVE_TO_NEGATIVE
    if previous == 0 < current:
        return ZERO_TO_POSITIVE
    if previous == 0 > current:
        return ZERO_TO_NEGATIVE
    if previous != 0 and current == 0:
        return TO_ZERO
    return NONE


def calculate_trend(
    observations: Sequence[ValidatedTrendObservation | Mapping[str, Any]] | pd.DataFrame,
    *,
    metric: str = "trend",
    value_kind: str = "rate",
) -> TrendSummary:
    """Calculate level, first change, acceleration, and persistence.

    For a rate series the input values are ratios and all changes are emitted
    in percentage points.  Acceleration is the *change of first change* and
    therefore may be exactly zero for a strong monotonic turnaround.  The
    function requires consecutive fiscal-quarter observations; gaps and
    invalid observations fail closed rather than being skipped.
    """

    value_kind_aliases = {
        "rate": "rate",
        "growth": "rate",
        "growth_rate": "rate",
        "margin": "rate",
        "absolute": "absolute",
        "ttm": "absolute",
        "value": "absolute",
    }
    if value_kind not in value_kind_aliases:
        raise ValueError("value_kind must be a rate/growth/margin or absolute/ttm value")
    value_kind = value_kind_aliases[value_kind]
    if isinstance(observations, pd.DataFrame):
        raw_observations: Sequence[ValidatedTrendObservation | Mapping[str, Any]] = (
            observations.to_dict(orient="records")
        )
    else:
        raw_observations = observations
    normalised = [
        _observation_from_mapping(observation, value_kind=value_kind)
        for observation in raw_observations
    ]
    ordered = tuple(
        observation
        for _, observation in sorted(
            enumerate(normalised), key=lambda pair: _period_sort_key(pair[1], pair[0])
        )
    )
    if not ordered:
        return _empty_summary(
            metric,
            value_kind=value_kind,
            status=UNKNOWN_STATUS,
            reason="missing_observation",
        )
    if any(
        observation.comparable_period_contract_version
        != COMPARABLE_PERIOD_CONTRACT_VERSION
        for observation in ordered
    ):
        return _empty_summary(
            metric,
            value_kind=value_kind,
            status=UNSUPPORTED,
            reason="invalid_comparable_period_contract",
            observations=ordered,
        )

    current = ordered[-1]
    level = current.value if current.status == VALID else None
    level_status = VALID if level is not None else current.status
    level_reason = None if level_status == VALID else current.reason or "missing_value"
    valid_count = sum(observation.status == VALID for observation in ordered)
    unsupported_reasons = tuple(
        observation.reason
        for observation in ordered
        if observation.status == UNSUPPORTED and observation.reason
    )
    has_unsupported = bool(unsupported_reasons)
    unsupported_reason = (
        unsupported_reasons[0] if unsupported_reasons else "unsupported_observation"
    )
    scale = 100.0 if value_kind == "rate" else 1.0
    unit = PERCENTAGE_POINT_UNIT if value_kind == "rate" else SOURCE_UNIT

    change = None
    change_status: str
    change_reason: str | None
    if len(ordered) < 2 or valid_count < 2:
        change_status, change_reason = (
            (UNSUPPORTED, unsupported_reason)
            if has_unsupported
            else (INSUFFICIENT_HISTORY, "insufficient_history")
        )
    else:
        pair_status, pair_reason = _pair_issue(ordered[-2], current)
        if pair_status is None:
            change = _difference(float(current.value), float(ordered[-2].value), scale=scale)
            if change is None:
                change_status, change_reason = UNKNOWN_STATUS, "numeric_overflow"
            else:
                change_status, change_reason = VALID, None
        else:
            change_status, change_reason = pair_status, pair_reason

    previous_change = None
    acceleration = None
    if len(ordered) < 3 or valid_count < 3:
        acceleration_status, acceleration_reason = (
            (UNSUPPORTED, unsupported_reason)
            if has_unsupported
            else (INSUFFICIENT_HISTORY, "insufficient_history")
        )
    else:
        older_status, older_reason = _pair_issue(ordered[-3], ordered[-2])
        latest_status, latest_reason = _pair_issue(ordered[-2], current)
        if older_status is not None:
            acceleration_status, acceleration_reason = older_status, older_reason
        elif latest_status is not None:
            acceleration_status, acceleration_reason = latest_status, latest_reason
        else:
            previous_change = _difference(
                float(ordered[-2].value), float(ordered[-3].value), scale=scale
            )
            acceleration = (
                None
                if previous_change is None or change is None
                else _difference(float(change), float(previous_change), scale=1.0)
            )
            if previous_change is None or acceleration is None:
                acceleration_status, acceleration_reason = UNKNOWN_STATUS, "numeric_overflow"
            else:
                acceleration_status, acceleration_reason = VALID, None

    source_sign_transition = current.underlying_sign_transition
    sign_transition = None
    sign_transition_status = change_status
    sign_transition_reason = change_reason
    if change_status == VALID:
        sign_transition = _sign_transition(float(ordered[-2].value), float(current.value))
        sign_transition_status, sign_transition_reason = VALID, None
    elif source_sign_transition is not None:
        # A #27 primitive may know that the underlying financial values
        # crossed zero even when ordinary growth is UNKNOWN because its
        # denominator is zero/negative.  Preserve that sign evidence, but do
        # not invent a percentage growth or change.
        sign_transition = source_sign_transition
        sign_transition_status, sign_transition_reason = VALID, None

    # Count only a contiguous run from the current pair backwards.  A gap or
    # UNKNOWN stops the run; it is never silently bridged.
    improvement_count: int | None = None
    deterioration_count: int | None = None
    persistence: str | None = None
    persistence_status = change_status
    persistence_reason = change_reason
    if change_status == VALID:
        improvement_count = 0
        deterioration_count = 0
        direction: str | None = None
        for index in range(len(ordered) - 1, 0, -1):
            pair_status, pair_reason = _pair_issue(ordered[index - 1], ordered[index])
            if pair_status is not None:
                if index == len(ordered) - 1:
                    improvement_count = None
                    deterioration_count = None
                    persistence_status, persistence_reason = pair_status, pair_reason
                break
            delta = float(ordered[index].value) - float(ordered[index - 1].value)
            if delta > 0:
                if direction not in {None, "improving"}:
                    break
                direction = "improving"
                improvement_count += 1
            elif delta < 0:
                if direction not in {None, "deteriorating"}:
                    break
                direction = "deteriorating"
                deterioration_count += 1
            else:
                break
        if persistence_status == VALID:
            if direction == "improving":
                persistence = "improving"
            elif direction == "deteriorating":
                persistence = "deteriorating"
            else:
                persistence = "stable"

    if change_status == VALID and change is not None:
        if change > 0:
            state = STRONG_TURNAROUND if sign_transition == NEGATIVE_TO_POSITIVE else IMPROVING
            turnaround_evidence = "positive"
        elif change < 0:
            state = DETERIORATING
            turnaround_evidence = "negative"
        else:
            state = STABLE
            turnaround_evidence = "neutral"
    else:
        state = INSUFFICIENT
        turnaround_evidence = "unknown"

    availability, versions, datasets, fields, chain = _aggregate_source(ordered)
    summary_status = level_status
    summary_reason = level_reason
    if summary_status == VALID and change_status != VALID:
        summary_status, summary_reason = change_status, change_reason
    provenance = {
        "unit": unit,
        "level_definition": "latest validated observation",
        "first_change_definition": "current level - previous level",
        "acceleration_definition": "current change - previous change",
        "rate_input_definition": "validated comparable growth/margin ratio",
        "observations": [observation.as_dict() for observation in ordered],
        "component_statuses": {
            "level": {"status": level_status, "reason": level_reason},
            "change": {"status": change_status, "reason": change_reason},
            "acceleration": {
                "status": acceleration_status,
                "reason": acceleration_reason,
            },
            "sign_transition": {
                "status": sign_transition_status,
                "reason": sign_transition_reason,
            },
            "persistence": {
                "status": persistence_status,
                "reason": persistence_reason,
            },
        },
    }
    return TrendSummary(
        metric=metric,
        value_kind=value_kind,
        unit=unit,
        status=summary_status,
        reason=summary_reason,
        level=level,
        previous_level=(
            ordered[-2].value if len(ordered) >= 2 and ordered[-2].status == VALID else None
        ),
        older_level=(
            ordered[-3].value if len(ordered) >= 3 and ordered[-3].status == VALID else None
        ),
        change=change,
        previous_change=previous_change,
        acceleration=acceleration,
        sign_transition=sign_transition,
        source_sign_transition=source_sign_transition,
        improvement_count=improvement_count,
        deterioration_count=deterioration_count,
        persistence=persistence,
        state=state,
        turnaround_evidence=turnaround_evidence,
        level_status=level_status,
        level_reason=level_reason,
        change_status=change_status,
        change_reason=change_reason,
        acceleration_status=acceleration_status,
        acceleration_reason=acceleration_reason,
        sign_transition_status=sign_transition_status,
        sign_transition_reason=sign_transition_reason,
        persistence_status=persistence_status,
        persistence_reason=persistence_reason,
        current_period=current.period,
        previous_period=ordered[-2].period if len(ordered) >= 2 else None,
        older_period=ordered[-3].period if len(ordered) >= 3 else None,
        current_availability_date=(
            current.availability_dates[0] if current.availability_dates else None
        ),
        comparison_availability_date=(
            ordered[-2].availability_dates[0]
            if len(ordered) >= 2 and ordered[-2].availability_dates
            else None
        ),
        availability_dates=availability,
        source_versions=versions,
        source_datasets=datasets,
        source_fields=fields,
        source_chain=chain,
        observations=ordered,
        provenance=provenance,
    )


# Explicit aliases make the pure semantic helper discoverable without making
# callers depend on the implementation name used by the feature group.
compute_trend_summary = calculate_trend
derive_trend_summary = calculate_trend


def _field(history: pd.DataFrame, *fields: str) -> str | None:
    for field_name in fields:
        if (
            field_name in history.columns
            and pd.to_numeric(history[field_name], errors="coerce").notna().any()
        ):
            return field_name
    return None


def _declared_contract_is_invalid(frame: pd.DataFrame | None) -> bool:
    """Reject an explicitly declared upstream version that is not #27."""

    if frame is None or frame.empty or "comparable_period_contract_version" not in frame.columns:
        return False
    versions = frame["comparable_period_contract_version"]
    return bool(
        versions.isna().any()
        or not versions.astype(str).eq(COMPARABLE_PERIOD_CONTRACT_VERSION).all()
    )


def _contract_is_valid(frame: pd.DataFrame) -> bool:
    if frame.empty or "comparable_period_contract_version" not in frame.columns:
        return False
    versions = frame["comparable_period_contract_version"]
    if bool(versions.isna().any()):
        return False
    return bool(versions.astype(str).eq(COMPARABLE_PERIOD_CONTRACT_VERSION).all())


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    identity = period_identity(row)
    return tuple(
        str(value)
        for value in (
            identity.report_family,
            identity.statement_type,
            identity.duration_semantics,
            identity.scope,
            identity.unit,
            identity.accounting_semantics,
        )
    )


def _choose_series(
    frame: pd.DataFrame,
    *,
    dataset: str,
) -> tuple[pd.DataFrame, str | None]:
    """Select one unambiguous semantic series; never combine report families."""

    del dataset
    if frame.empty or "report_period" not in frame.columns:
        return pd.DataFrame(), "missing_observation"
    dated = frame.loc[pd.to_datetime(frame["report_period"], errors="coerce").notna()].copy()
    if dated.empty:
        return pd.DataFrame(), "unsupported_report_period"
    dated["report_period"] = pd.to_datetime(dated["report_period"], errors="coerce")
    latest = dated["report_period"].max()
    latest_rows = dated.loc[dated["report_period"].eq(latest)]
    keys = {_semantic_key(row.to_dict()) for _, row in latest_rows.iterrows()}
    if len(keys) != 1:
        return pd.DataFrame(), "ambiguous_period_chain"
    key = next(iter(keys))
    selected = dated.loc[
        dated.apply(lambda row: _semantic_key(row.to_dict()) == key, axis=1)
    ].copy()
    duplicate_periods = selected["report_period"].duplicated(keep=False)
    if bool(duplicate_periods.any()):
        return pd.DataFrame(), "ambiguous_period_chain"
    return selected.sort_values("report_period", kind="stable").reset_index(drop=True), None


def _reason_status(result: DerivedMetric) -> tuple[str, str | None]:
    if result.status == "known" and result.value is not None:
        return VALID, None
    reason = result.reason or "unknown_observation"
    if reason in _UNSUPPORTED_REASONS or reason.startswith("unsupported_"):
        return UNSUPPORTED, reason
    return UNKNOWN_STATUS, reason


def _source_transition_from_result(result: DerivedMetric) -> str | None:
    source_transition = str(result.provenance.get("sign_transition", ""))
    if source_transition.lower() == "zero_to_nonzero":
        current = _finite(result.current_raw_value)
        if current is not None and current > 0:
            return ZERO_TO_POSITIVE
        if current is not None and current < 0:
            return ZERO_TO_NEGATIVE
    return _normalise_source_transition(result.provenance.get("sign_transition"))


def _result_observation(
    result: DerivedMetric,
    *,
    metric: str,
    value_kind: str,
    fallback_row: Mapping[str, Any] | None = None,
    source_fields: tuple[str, ...] = (),
) -> ValidatedTrendObservation:
    period = result.current_period
    if period is None and fallback_row is not None:
        period = period_identity(fallback_row).report_period
    status, reason = _reason_status(result)
    source_chain = tuple(record.as_dict() for record in result.source_chain)
    if not source_chain and fallback_row is not None:
        source_chain = (
            {
                "period": period,
                "availability_date": _date_text(fallback_row.get("actual_available_date")),
                "source_version": str(fallback_row.get("source_version_identity"))
                if fallback_row.get("source_version_identity") is not None
                else None,
                "value": _finite(
                    fallback_row.get("comparable_value", fallback_row.get(source_fields[0]))
                    if source_fields
                    else fallback_row.get("comparable_value")
                ),
            },
        )
    availability = result.availability_dates
    versions = result.source_versions
    if fallback_row is not None:
        available = fallback_row.get("actual_available_date")
        if not availability and available is not None and not pd.isna(available):
            availability = (pd.Timestamp(available).strftime("%Y%m%d"),)
        version = fallback_row.get("source_version_identity")
        if not versions and version is not None and not pd.isna(version):
            versions = (str(version),)
    return ValidatedTrendObservation(
        period=period,
        value=_finite(result.value) if status == VALID else None,
        comparison_period=result.comparison_period,
        status=status,
        reason=reason,
        current_raw_value=(
            _finite(result.value)
            if value_kind == "absolute"
            else _finite(result.current_raw_value)
        ),
        comparison_raw_value=_finite(result.comparison_raw_value),
        underlying_sign_transition=_source_transition_from_result(result),
        availability_dates=availability,
        source_versions=versions,
        source_datasets=result.source_datasets or ("income",),
        source_fields=result.source_fields or source_fields,
        source_chain=source_chain,
        comparable_period_contract_version=result.contract_version,
        provenance={
            "metric": metric,
            "value_kind": value_kind,
            "period_semantics": result.period_semantics,
            **dict(result.provenance),
        },
    )


def _invalid_series_observations(
    frame: pd.DataFrame,
    *,
    metric: str,
    reason: str,
    status: str = UNKNOWN_STATUS,
) -> list[ValidatedTrendObservation]:
    if frame.empty:
        return [_invalid_observation(metric=metric, reason=reason, status=status)]
    observations: list[ValidatedTrendObservation] = []
    for _, row in frame.iterrows():
        identity = period_identity(row.to_dict())
        observations.append(
            _invalid_observation(
                metric=metric,
                reason=reason,
                period=identity.report_period,
                status=status,
            )
        )
    return observations


def _yoy_observations(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    metric: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> list[ValidatedTrendObservation]:
    if field_name is None:
        return [_invalid_observation(metric=metric, reason="missing_value")]
    if not _contract_is_valid(history):
        return _invalid_series_observations(
            history,
            metric=metric,
            reason="invalid_comparable_period_contract",
            status=UNSUPPORTED,
        )
    source, series_reason = _choose_series(history, dataset="income")
    if series_reason is not None:
        return _invalid_series_observations(
            source if not source.empty else history,
            metric=metric,
            reason=series_reason,
            status=UNKNOWN_STATUS,
        )
    observations: list[ValidatedTrendObservation] = []
    for _, row in source.iterrows():
        match = match_comparable_period(
            history,
            row,
            comparison="yoy",
            dataset="income",
            value_column=field_name,
            as_of_date=as_of_date,
        )
        result = growth_from_match(match, metric=metric)
        observations.append(
            _result_observation(
                result,
                metric=metric,
                value_kind="rate",
                fallback_row=row.to_dict(),
                source_fields=(field_name,),
            )
        )
    return observations


def _qoq_observations(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    metric: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> list[ValidatedTrendObservation]:
    if field_name is None:
        return [_invalid_observation(metric=metric, reason="missing_value")]
    if not _contract_is_valid(history):
        return _invalid_series_observations(
            history,
            metric=metric,
            reason="invalid_comparable_period_contract",
            status=UNSUPPORTED,
        )
    source, columns = single_quarter_history(
        history,
        "income",
        (field_name,),
        as_of_date=as_of_date,
    )
    if source.empty or not columns:
        return [_invalid_observation(metric=metric, reason="qoq_requires_validated_single_quarter")]
    source, series_reason = _choose_series(source, dataset="income")
    if series_reason is not None:
        return _invalid_series_observations(
            source if not source.empty else history,
            metric=metric,
            reason=series_reason,
        )
    observations: list[ValidatedTrendObservation] = []
    for _, row in source.iterrows():
        match = match_comparable_period(
            source,
            row,
            comparison="qoq",
            dataset="income",
            value_column="comparable_value",
            as_of_date=as_of_date,
        )
        result = growth_from_match(match, metric=metric)
        observations.append(
            _result_observation(
                result,
                metric=metric,
                value_kind="rate",
                fallback_row=row.to_dict(),
                source_fields=(field_name,),
            )
        )
    return observations


def _margin_observations(
    history: pd.DataFrame,
    numerator_field: str | None,
    denominator_field: str | None,
    *,
    metric: str,
) -> list[ValidatedTrendObservation]:
    if numerator_field is None or denominator_field is None:
        return [_invalid_observation(metric=metric, reason="missing_value")]
    if not _contract_is_valid(history):
        return _invalid_series_observations(
            history,
            metric=metric,
            reason="invalid_comparable_period_contract",
            status=UNSUPPORTED,
        )
    source, series_reason = _choose_series(history, dataset="income")
    if series_reason is not None:
        return _invalid_series_observations(
            source if not source.empty else history,
            metric=metric,
            reason=series_reason,
        )
    observations: list[ValidatedTrendObservation] = []
    for _, row in source.iterrows():
        result = margin_from_row(
            row,
            dataset="income",
            numerator_column=numerator_field,
            denominator_column=denominator_field,
            metric=metric,
        )
        observations.append(
            _result_observation(
                result,
                metric=metric,
                value_kind="rate",
                fallback_row=row.to_dict(),
                source_fields=(numerator_field, denominator_field),
            )
        )
    return observations


def _ttm_observations(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    metric: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> list[ValidatedTrendObservation]:
    if field_name is None:
        return [_invalid_observation(metric=metric, reason="missing_value")]
    if not _contract_is_valid(history):
        return _invalid_series_observations(
            history,
            metric=metric,
            reason="invalid_comparable_period_contract",
            status=UNSUPPORTED,
        )
    source, columns = single_quarter_history(
        history,
        "income",
        (field_name,),
        as_of_date=as_of_date,
    )
    if source.empty or not columns:
        return [_invalid_observation(metric=metric, reason="ttm_requires_validated_single_quarter")]
    source, series_reason = _choose_series(source, dataset="income")
    if series_reason is not None:
        return _invalid_series_observations(
            source if not source.empty else history,
            metric=metric,
            reason=series_reason,
        )
    observations: list[ValidatedTrendObservation] = []
    for _, row in source.iterrows():
        result = ttm_from_series(
            source,
            dataset="income",
            value_column="comparable_value",
            end=row,
            as_of_date=as_of_date,
            metric=metric,
        )
        observations.append(
            _result_observation(
                result,
                metric=metric,
                value_kind="absolute",
                fallback_row=row.to_dict(),
                source_fields=(field_name,),
            )
        )
    return observations


def _add_summary(
    vector: FeatureVector,
    prefix: str,
    summary: TrendSummary,
) -> None:
    """Expose a summary's components with one complete evidence chain each."""

    components = (
        ("level", f"{prefix}_level"),
        ("change", f"{prefix}_change"),
        ("previous_change", f"{prefix}_previous_change"),
        ("acceleration", f"{prefix}_acceleration"),
        ("sign_transition", f"{prefix}_sign_transition"),
        ("improvement_count", f"{prefix}_improvement_count"),
        ("persistence", f"{prefix}_persistence"),
        ("state", f"{prefix}_state"),
        ("turnaround_evidence", f"{prefix}_turnaround_evidence"),
    )
    for component, name in components:
        value, status, reason = summary.component(component)
        provenance = summary.as_dict()
        provenance.update(
            {
                "component": component,
                "metric": summary.metric,
                "unit": summary.component_unit(component),
                "comparable_period_contract_version": COMPARABLE_PERIOD_CONTRACT_VERSION,
                "trend_contract_version": TREND_CONTRACT_VERSION,
            }
        )
        vector.add(
            name,
            value,
            status=status,
            source_datasets=summary.source_datasets,
            source_fields=summary.source_fields,
            periods=tuple(
                period
                for period in (
                    observation.period for observation in summary.observations
                )
                if period is not None
            ),
            availability_dates=summary.availability_dates,
            reason=reason,
            current_period=summary.current_period,
            comparison_period=summary.previous_period,
            current_availability_date=summary.current_availability_date,
            comparison_availability_date=summary.comparison_availability_date,
            current_raw_value=summary.level,
            comparison_raw_value=summary.previous_level,
            period_semantics=(
                summary.observations[-1].provenance.get("period_semantics")
                if summary.observations
                else None
            ),
            source_versions=summary.source_versions,
            contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
            trend_contract_version=TREND_CONTRACT_VERSION,
            provenance=provenance,
        )


def _add_alias(
    vector: FeatureVector,
    name: str,
    summary: TrendSummary,
    component: str,
    *,
    alias_for: str,
) -> None:
    value, status, reason = summary.component(component)
    provenance = summary.as_dict()
    provenance.update(
        {
            "component": component,
            "unit": summary.component_unit(component),
            "alias_for": alias_for,
            "deprecated": True,
            "trend_contract_version": TREND_CONTRACT_VERSION,
            "comparable_period_contract_version": COMPARABLE_PERIOD_CONTRACT_VERSION,
        }
    )
    vector.add(
        name,
        value,
        status=status,
        source_datasets=summary.source_datasets,
        source_fields=summary.source_fields,
        periods=tuple(
            observation.period for observation in summary.observations if observation.period
        ),
        availability_dates=summary.availability_dates,
        reason=reason,
        current_period=summary.current_period,
        comparison_period=summary.previous_period,
        current_availability_date=summary.current_availability_date,
        comparison_availability_date=summary.comparison_availability_date,
        current_raw_value=summary.level,
        comparison_raw_value=summary.previous_level,
        period_semantics=(
            summary.observations[-1].provenance.get("period_semantics")
            if summary.observations
            else None
        ),
        source_versions=summary.source_versions,
        contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        trend_contract_version=TREND_CONTRACT_VERSION,
        provenance=provenance,
    )


def _unknown_summary(metric: str, reason: str, *, value_kind: str = "rate") -> TrendSummary:
    return _empty_summary(metric, value_kind=value_kind, status=UNKNOWN_STATUS, reason=reason)


def compute_trend_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    """Compute the versioned trend contract from validated #27 primitives.

    YoY values are matched by ``match_comparable_period``; QoQ and TTM values
    are built only from ``single_quarter_history`` and ``ttm_from_series``;
    margins are calculated by ``margin_from_row``.  No period matching,
    quarterization, or rolling-four-quarter logic is implemented locally.
    """

    vector = new_vector(code, as_of_date)
    raw_income = financial_frames.get("income")
    input_contract_invalid = _declared_contract_is_invalid(raw_income)
    income = canonical_history("income", raw_income, code, as_of_date)
    if input_contract_invalid and not income.empty:
        # ``canonicalize_financial_frame`` necessarily writes the current
        # #27 version.  Preserve an explicitly supplied incompatible version
        # here so this consumer cannot silently upgrade it.
        income = income.copy()
        income["comparable_period_contract_version"] = "invalid-upstream-contract"

    revenue_field = _field(income, "revenue", "total_revenue")
    profit_field = _field(income, "n_income_attr_p", "n_income", "net_profit")
    operating_field = _field(income, "operate_profit", "operating_profit")
    gross_field = _field(income, "gross_profit")

    yoy_specs = (
        ("revenue_yoy", revenue_field),
        ("net_profit_yoy", profit_field),
        ("operating_profit_yoy", operating_field),
    )
    yoy: dict[str, TrendSummary] = {}
    for metric, field_name in yoy_specs:
        observations = (
            _yoy_observations(
                income,
                field_name,
                metric=metric,
                as_of_date=as_of_date,
            )
            if not income.empty
            else [_invalid_observation(metric=metric, reason="no PIT income history")]
        )
        yoy[metric] = calculate_trend(observations, metric=metric, value_kind="rate")
        _add_summary(vector, metric, yoy[metric])

    qoq_specs = (
        ("revenue_qoq", revenue_field),
        ("net_profit_qoq", profit_field),
        ("operating_profit_qoq", operating_field),
    )
    qoq: dict[str, TrendSummary] = {}
    for metric, field_name in qoq_specs:
        observations = (
            _qoq_observations(
                income,
                field_name,
                metric=metric,
                as_of_date=as_of_date,
            )
            if not income.empty
            else [_invalid_observation(metric=metric, reason="no PIT income history")]
        )
        qoq[metric] = calculate_trend(observations, metric=metric, value_kind="rate")
        _add_summary(vector, metric, qoq[metric])

    margin_specs = (
        ("gross_margin", gross_field, revenue_field),
        ("operating_margin", operating_field, revenue_field),
        ("net_margin", profit_field, revenue_field),
    )
    margins: dict[str, TrendSummary] = {}
    for metric, numerator, denominator in margin_specs:
        observations = (
            _margin_observations(
                income,
                numerator,
                denominator,
                metric=metric,
            )
            if not income.empty
            else [_invalid_observation(metric=metric, reason="no PIT income history")]
        )
        margins[metric] = calculate_trend(observations, metric=metric, value_kind="rate")
        _add_summary(vector, metric, margins[metric])

    ttm_specs = (
        ("revenue_ttm", revenue_field),
        ("net_profit_ttm", profit_field),
        ("operating_profit_ttm", operating_field),
    )
    ttm: dict[str, TrendSummary] = {}
    for metric, field_name in ttm_specs:
        observations = (
            _ttm_observations(
                income,
                field_name,
                metric=metric,
                as_of_date=as_of_date,
            )
            if not income.empty
            else [_invalid_observation(metric=metric, reason="no PIT income history")]
        )
        ttm[metric] = calculate_trend(observations, metric=metric, value_kind="absolute")
        _add_summary(vector, metric, ttm[metric])

    # Schema-compatible legacy fields.  They now point to explicitly defined
    # primitives rather than absolute-profit second differences.  The aliases
    # are documented as deprecated and retain the full new evidence chain.
    primary_yoy = yoy["net_profit_yoy"] if profit_field else yoy["revenue_yoy"]
    primary_qoq = qoq["net_profit_qoq"] if profit_field else qoq["revenue_qoq"]
    primary_margin = next(
        (
            margins[name]
            for name in ("operating_margin", "net_margin", "gross_margin")
            if margins[name].source_fields
        ),
        margins["operating_margin"],
    )
    primary_ttm = ttm["net_profit_ttm"] if profit_field else ttm["revenue_ttm"]
    _add_alias(
        vector,
        "yoy_acceleration",
        primary_yoy,
        "acceleration",
        alias_for=f"{primary_yoy.metric}_acceleration",
    )
    _add_alias(
        vector,
        "qoq_acceleration",
        primary_qoq,
        "acceleration",
        alias_for=f"{primary_qoq.metric}_acceleration",
    )
    _add_alias(
        vector,
        "consecutive_improvement",
        primary_yoy,
        "improvement_count",
        alias_for=f"{primary_yoy.metric}_improvement_count",
    )
    _add_alias(
        vector,
        "sign_transition",
        primary_yoy,
        "sign_transition",
        alias_for=f"{primary_yoy.metric}_sign_transition",
    )
    _add_alias(
        vector,
        "margin_inflection",
        primary_margin,
        "acceleration",
        alias_for=f"{primary_margin.metric}_acceleration",
    )
    _add_alias(
        vector,
        "ttm_trend",
        primary_ttm,
        "state",
        alias_for=f"{primary_ttm.metric}_state",
    )
    _add_alias(
        vector,
        "turnaround_state",
        primary_yoy,
        "state",
        alias_for=f"{primary_yoy.metric}_state",
    )
    _add_alias(
        vector,
        "turnaround_evidence",
        primary_yoy,
        "turnaround_evidence",
        alias_for=f"{primary_yoy.metric}_turnaround_evidence",
    )

    # Generic names are retained for consumers that have one primary trend
    # series, while the metric-qualified fields above remain authoritative.
    for prefix, summary in (("yoy", primary_yoy), ("qoq", primary_qoq)):
        for component in (
            "level",
            "change",
            "previous_change",
            "acceleration",
            "sign_transition",
            "improvement_count",
            "persistence",
            "state",
            "turnaround_evidence",
        ):
            _add_alias(
                vector,
                f"{prefix}_{component}",
                summary,
                component,
                alias_for=f"{summary.metric}_{component}",
            )
    for component in (
        "level",
        "change",
        "previous_change",
        "acceleration",
        "state",
        "turnaround_evidence",
    ):
        _add_alias(
            vector,
            f"ttm_{component}",
            primary_ttm,
            component,
            alias_for=f"{primary_ttm.metric}_{component}",
        )
    _add_alias(
        vector,
        "ttm_value",
        primary_ttm,
        "level",
        alias_for=f"{primary_ttm.metric}_level",
    )
    return vector


__all__ = [
    "TREND_CONTRACT_VERSION",
    "TURNAROUND_TREND_CONTRACT_VERSION",
    "VALID",
    "UNKNOWN_STATUS",
    "UNKNOWN",
    "INSUFFICIENT_HISTORY",
    "DISCONTINUOUS",
    "UNSUPPORTED",
    "NEGATIVE_TO_POSITIVE",
    "POSITIVE_TO_NEGATIVE",
    "NONE",
    "STRONG_TURNAROUND",
    "IMPROVING",
    "STABLE",
    "DETERIORATING",
    "INSUFFICIENT",
    "ValidatedTrendObservation",
    "TrendSummary",
    "calculate_trend",
    "compute_trend_summary",
    "derive_trend_summary",
    "compute_trend_features",
]
