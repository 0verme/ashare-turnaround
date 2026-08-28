"""PIT-safe attention and expectation/crowding feature groups.

The attention group is intentionally independent from crowding.  Crowding v2
uses exact stock/benchmark endpoint arithmetic and never turns a stock-only
return into an ``excess_return`` when the benchmark is missing.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..scanner.contracts import FeatureVector
from .benchmark import (
    BenchmarkConfig,
    BenchmarkContext,
    _column_at,
    high_window,
    prior_baseline,
    resolve_benchmark,
    window_return,
)
from .common import add_known, add_unknown, market_history, new_vector, numeric

EXPECTATION_CROWDING_CONTRACT_VERSION = "expectation-crowding-v2"

# Kept as a private compatibility seam for callers of the pre-v2 module.
_market_history = market_history

_CROWDING_SOURCE_DATASETS = ("daily", "daily_basic", "index_basic", "index_daily")
_CROWDING_FEATURES: tuple[str, ...] = (
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
    "repricing_20d",
    "repricing_60d",
    "high_proximity",
    "volume_spike_penalty",
    "turnover_spike_penalty",
    "valuation_penalty",
    "disclosure_reaction_excess",
    "disclosure_availability_date",
    "disclosure_event_date",
    "disclosure_reaction_window_start",
    "disclosure_reaction_window_end",
    "disclosure_reaction_penalty",
    "crowding_penalty",
    "expectation_score",
)


def _percentile(values: pd.Series, current: Any) -> float | None:
    """Empirical percentile over finite, positive valuation observations."""

    current_value = numeric(current)
    clean = pd.to_numeric(values, errors="coerce").dropna()
    clean = clean.loc[clean.gt(0) & clean.replace([float("inf"), -float("inf")], pd.NA).notna()]
    if current_value is None or current_value <= 0 or clean.empty:
        return None
    return float((clean <= current_value).mean())


def _add_text(
    vector: FeatureVector,
    name: str,
    value: str | None,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    reason: str | None = None,
    semantic_version: str = "features-v1",
    components: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    evidence_components = dict(components or {})
    evidence_config = dict(config or {})
    if semantic_version.startswith("expectation-crowding"):
        evidence_components.setdefault("as_of", vector.as_of_date)
        evidence_config.setdefault("as_of", vector.as_of_date)
    vector.add(
        name,
        value,
        status="known" if value is not None else "unknown",
        source_datasets=datasets,
        source_fields=fields,
        reason=reason if value is None else None,
        semantic_version=semantic_version,
        components=evidence_components,
        config=evidence_config,
    )


def compute_attention_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
) -> FeatureVector:
    """Compute low-attention proxies without consuming crowding features."""

    vector = new_vector(code, as_of_date)
    history = market_history(market_frame, code, as_of_date, lookback)
    if history.empty:
        for name in (
            "turnover_percentile",
            "amount_percentile",
            "abnormal_volume",
            "attention_score",
        ):
            add_unknown(
                vector,
                name,
                datasets=("daily_basic",),
                fields=(),
                reason="no market history",
            )
        return vector
    latest = history.iloc[-1]
    turnover = _percentile(
        history.get("turnover_rate", pd.Series(dtype="float64")), latest.get("turnover_rate")
    )
    amount = _percentile(history.get("amount", pd.Series(dtype="float64")), latest.get("amount"))
    volume = pd.to_numeric(history.get("vol", pd.Series(dtype="float64")), errors="coerce").dropna()
    volume = volume.loc[volume.gt(0)]
    current_volume = numeric(latest.get("vol"))
    baseline = numeric(volume.iloc[-61:-1].median()) if len(volume) > 1 else None
    abnormal = (
        current_volume / baseline
        if current_volume is not None and baseline not in {None, 0.0}
        else None
    )
    add_known(
        vector,
        "turnover_percentile",
        turnover,
        datasets=("daily_basic",),
        fields=("turnover_rate",),
        history=history,
    )
    add_known(
        vector,
        "amount_percentile",
        amount,
        datasets=("daily_basic",),
        fields=("amount",),
        history=history,
    )
    add_known(
        vector,
        "abnormal_volume",
        abnormal,
        datasets=("daily", "daily_basic"),
        fields=("vol", "amount"),
        history=history,
    )
    # A production component is only published when all of its declared v1
    # primitives are known.  Missing data is not an average/neutral attention
    # view; the score layer can disclose the omitted component explicitly.
    score = None
    if turnover is not None and amount is not None and abnormal is not None:
        score = 100.0 * (
            (1.0 - turnover) * 0.4
            + (1.0 - amount) * 0.4
            + (1.0 - min(abnormal, 3.0) / 3.0) * 0.2
        )
    add_known(
        vector,
        "attention_score",
        score,
        datasets=("daily", "daily_basic"),
        fields=("turnover_rate", "amount", "vol"),
        history=history,
        reason="insufficient attention proxies" if score is None else None,
    )
    return vector


@dataclass(frozen=True, slots=True)
class CrowdingConfig:
    """Versioned expectation/crowding v2 feature configuration.

    These are feature calibration constants, not ``ScoreConfig`` weights.  The
    full declaration is attached to each derived penalty and replay metadata.
    """

    version: str = EXPECTATION_CROWDING_CONTRACT_VERSION
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    repricing_20d_threshold: float = 0.15
    repricing_60d_threshold: float = 0.30
    volume_spike_threshold: float = 2.0
    turnover_spike_threshold: float = 2.0
    baseline_lookback_sessions: int = 60
    baseline_min_observations: int = 20
    valuation_lookback_sessions: int = 252
    valuation_min_observations: int = 20
    include_valuation_in_penalty: bool = False
    include_disclosure_in_penalty: bool = False
    disclosure_reaction_sessions: int = 5
    disclosure_reaction_threshold: float = 0.10
    crowding_flag_threshold: float = 70.0
    history_lookback: int = 400

    def __post_init__(self) -> None:
        for name in (
            "repricing_20d_threshold",
            "repricing_60d_threshold",
            "volume_spike_threshold",
            "turnover_spike_threshold",
            "disclosure_reaction_threshold",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "baseline_lookback_sessions",
            "baseline_min_observations",
            "valuation_lookback_sessions",
            "valuation_min_observations",
            "disclosure_reaction_sessions",
            "history_lookback",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.baseline_min_observations > self.baseline_lookback_sessions:
            raise ValueError("baseline_min_observations must not exceed baseline_lookback_sessions")
        if self.valuation_min_observations > self.valuation_lookback_sessions:
            raise ValueError(
                "valuation_min_observations must not exceed valuation_lookback_sessions"
            )
        if not math.isfinite(float(self.crowding_flag_threshold)) or float(
            self.crowding_flag_threshold
        ) < 0:
            raise ValueError("crowding_flag_threshold must be finite and non-negative")

    def declared(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark"] = self.benchmark.declared()
        payload["expectation_crowding_contract_version"] = self.version
        return payload


def _benchmark_config(settings: CrowdingConfig) -> dict[str, Any]:
    return settings.benchmark.declared()


def _feature_config(settings: CrowdingConfig, **extra: Any) -> dict[str, Any]:
    config = settings.declared()
    config["benchmark"] = _benchmark_config(settings)
    config["benchmark_id"] = settings.benchmark.benchmark_id
    config["benchmark_contract_version"] = settings.benchmark.version
    config["benchmark_source_dataset"] = settings.benchmark.source_dataset
    config.update(extra)
    if "lookback_sessions" in extra:
        config.setdefault("lookback", extra["lookback_sessions"])
    return config


def _anchor_unknowns(
    vector: FeatureVector,
    reason: str,
    *,
    settings: CrowdingConfig,
    history: pd.DataFrame | None = None,
) -> None:
    config = _feature_config(settings)
    components = {
        "start_session": None,
        "end_session": None,
        "stock_start": None,
        "stock_end": None,
        "benchmark_start": None,
        "benchmark_end": None,
        "benchmark_id": settings.benchmark.benchmark_id,
        "stock_return": None,
        "benchmark_return": None,
        "excess_return": None,
    }
    for name in _CROWDING_FEATURES:
        add_unknown(
            vector,
            name,
            datasets=_CROWDING_SOURCE_DATASETS,
            fields=(),
            reason=reason,
            history=history,
            semantic_version=settings.version,
            components=components,
            config=config,
        )


def _window_components(
    window: Any,
    *,
    benchmark_id: str,
    axis_source: str,
) -> dict[str, Any]:
    def text(value: pd.Timestamp | None) -> str | None:
        return value.strftime("%Y%m%d") if value is not None else None

    return {
        # Names required by the research contract.
        "start_session": text(window.window_start),
        "end_session": text(window.anchor),
        "stock_start": window.stock_close_start,
        "stock_end": window.stock_close_end,
        "benchmark_start": window.benchmark_close_start,
        "benchmark_end": window.benchmark_close_end,
        "benchmark_id": benchmark_id,
        "stock_return": window.stock_return,
        "benchmark_return": window.benchmark_return,
        "excess_return": window.excess_return,
        "session_count": window.sessions,
        "lookback_sessions": window.lookback,
        "endpoint_observation_count": window.sessions,
        # Compatibility/readability aliases used by prior reports.
        "anchor_session": text(window.anchor),
        "window_start_session": text(window.window_start),
        "sessions": window.sessions,
        "axis_source": axis_source,
    }


def _add_return_features(
    vector: FeatureVector,
    window: Any,
    *,
    lookback: int,
    settings: CrowdingConfig,
    stock_name: str,
    benchmark_name: str,
    excess_name: str,
    stock_alias: str | None = None,
    excess_alias: str | None = None,
    history: pd.DataFrame,
    axis_source: str,
) -> None:
    benchmark_config = _benchmark_config(settings)
    benchmark_config["expectation_crowding_contract_version"] = settings.version
    benchmark_config["lookback"] = lookback
    formula = (
        f"R_stock(t,{lookback}) = stock_close(t) / stock_close(t-{lookback}) - 1; "
        f"R_benchmark(t,{lookback}) = benchmark_close(t) / "
        f"benchmark_close(t-{lookback}) - 1; excess_return_{lookback}d = "
        "R_stock - R_benchmark"
    )
    components = _window_components(
        window,
        benchmark_id=settings.benchmark.benchmark_id,
        axis_source=axis_source,
    )
    names = (stock_name, benchmark_name, excess_name, stock_alias, excess_alias)
    names = tuple(name for name in names if name is not None)
    if window.status != "known":
        for name in names:
            add_unknown(
                vector,
                name,
                datasets=("daily", "index_daily"),
                fields=("close",),
                reason=window.reason or "unknown_benchmark_relative_return",
                history=history,
                semantic_version=settings.version,
                formula=formula,
                components=components,
                config=benchmark_config,
            )
        return
    for name, value in (
        (stock_name, window.stock_return),
        (benchmark_name, window.benchmark_return),
        (excess_name, window.excess_return),
        (stock_alias, window.stock_return),
        (excess_alias, window.excess_return),
    ):
        if name is None:
            continue
        alias_config = dict(benchmark_config)
        if name in {stock_alias, excess_alias}:
            alias_config["alias_of"] = stock_name if name == stock_alias else excess_name
        add_known(
            vector,
            name,
            value,
            datasets=("daily", "index_daily"),
            fields=("close",),
            history=history,
            semantic_version=settings.version,
            formula=formula,
            components=components,
            config=alias_config,
        )


def _disclosure_before_as_of(
    disclosure_frame: pd.DataFrame | None,
    code: str,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Return disclosures whose availability date is provably <= as-of."""

    if (
        disclosure_frame is None
        or disclosure_frame.empty
        or "ts_code" not in disclosure_frame.columns
    ):
        return pd.DataFrame()
    matched = disclosure_frame.loc[
        disclosure_frame["ts_code"].astype("string").eq(str(code))
    ].copy()
    if matched.empty:
        return matched
    actual = (
        pd.to_datetime(matched["actual_date"], errors="coerce")
        if "actual_date" in matched.columns
        else pd.Series(pd.NaT, index=matched.index, dtype="datetime64[ns]")
    )
    announced = (
        pd.to_datetime(matched["ann_date"], errors="coerce")
        if "ann_date" in matched.columns
        else pd.Series(pd.NaT, index=matched.index, dtype="datetime64[ns]")
    )
    # actual_date is preferred.  ann_date is the documented announcement-date
    # fallback; the selected source is preserved in the returned frame.
    availability = actual.fillna(announced)
    matched["_availability"] = availability.dt.normalize()
    matched["_availability_source"] = pd.Series("", index=matched.index, dtype="string")
    matched.loc[actual.notna(), "_availability_source"] = "actual_date"
    matched.loc[actual.isna() & announced.notna(), "_availability_source"] = "ann_date"
    event = announced.fillna(actual)
    matched["_event"] = event.dt.normalize()
    visible = matched.loc[
        matched["_availability"].notna() & matched["_availability"].le(as_of)
    ].copy()
    if visible.empty:
        return visible
    key_columns = sorted(
        column
        for column in visible.columns
        if column not in {"_availability", "_availability_source", "_event", "_row_key"}
    )
    visible["_row_key"] = (
        visible[key_columns].astype("string").fillna("<NA>").agg("\x1f".join, axis=1)
    )
    return visible.sort_values(
        ["_availability", "_availability_source", "_row_key"], kind="mergesort"
    )


def _disclosure_reaction(
    market_frame: pd.DataFrame | None,
    benchmark_frame: pd.DataFrame | None,
    disclosure_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    benchmark_id: str,
    reaction_sessions: int,
    session_lookback: int,
    calendar_frame: pd.DataFrame | None = None,
    benchmark_definition_frame: pd.DataFrame | None = None,
    suspension_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Calculate only a fully observable, benchmark-relative reaction window."""

    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid disclosure as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    context = resolve_benchmark(
        market_frame,
        code,
        benchmark_id,
        as_of,
        benchmark_frame=benchmark_frame,
        benchmark_definition_frame=benchmark_definition_frame,
        suspension_frame=suspension_frame,
        session_lookback=session_lookback,
        calendar_frame=calendar_frame,
    )
    if not context.known:
        return {"status": "unknown", "reason": context.reason}
    if disclosure_frame is None or disclosure_frame.empty:
        return {"status": "unknown", "reason": "no_disclosure_before_as_of"}
    disclosures = _disclosure_before_as_of(disclosure_frame, code, as_of)
    if disclosures.empty:
        has_timing = any(
            column in disclosure_frame.columns
            and pd.to_datetime(disclosure_frame[column], errors="coerce").notna().any()
            for column in ("actual_date", "ann_date")
        )
        return {
            "status": "unknown",
            "reason": (
                "no_disclosure_before_as_of" if has_timing else "disclosure_timing_unprovable"
            ),
        }
    latest = disclosures.iloc[-1]
    availability = pd.Timestamp(latest["_availability"]).normalize()
    event = latest.get("_event")
    axis = list(context.axis)
    after = [session for session in axis if availability < session <= as_of]
    base = {
        "availability": availability.strftime("%Y%m%d"),
        "availability_source": str(latest.get("_availability_source") or "unknown"),
        "event": pd.Timestamp(event).strftime("%Y%m%d") if pd.notna(event) else None,
    }
    if len(after) < reaction_sessions:
        return {
            "status": "unknown",
            "reason": "insufficient_reaction_window",
            "after_sessions": len(after),
            **base,
        }
    observed = after[:reaction_sessions]
    # context histories are already PIT filtered through as-of.  The explicit
    # check below documents the hard cutoff and guards future-dated fixtures.
    if observed[-1] > as_of:
        return {"status": "unknown", "reason": "reaction_window_after_as_of", **base}
    window_start, window_end = observed[0], observed[-1]
    stock_start = _column_at(context.stock_history, window_start, "close")
    stock_end = _column_at(context.stock_history, window_end, "close")
    benchmark_start = _column_at(context.benchmark_history, window_start, "close")
    benchmark_end = _column_at(context.benchmark_history, window_end, "close")
    if stock_start is None or stock_end is None:
        return {
            "status": "unknown",
            "reason": "missing_stock_endpoint",
            "window_start": window_start.strftime("%Y%m%d"),
            "window_end": window_end.strftime("%Y%m%d"),
            **base,
        }
    if benchmark_start is None or benchmark_end is None:
        return {
            "status": "unknown",
            "reason": "missing_benchmark_endpoint",
            "window_start": window_start.strftime("%Y%m%d"),
            "window_end": window_end.strftime("%Y%m%d"),
            **base,
        }
    if min(stock_start, stock_end, benchmark_start, benchmark_end) <= 0:
        return {
            "status": "unknown",
            "reason": "invalid_reaction_price",
            "window_start": window_start.strftime("%Y%m%d"),
            "window_end": window_end.strftime("%Y%m%d"),
            **base,
        }
    stock_return = stock_end / stock_start - 1.0
    benchmark_return = benchmark_end / benchmark_start - 1.0
    excess = stock_return - benchmark_return
    if any(
        pd.isna(value) or value in {float("inf"), -float("inf")}
        for value in (stock_return, benchmark_return, excess)
    ):
        return {
            "status": "unknown",
            "reason": "invalid_reaction_price",
            **base,
        }
    return {
        "status": "known",
        "reason": None,
        "excess": excess,
        "stock_return": stock_return,
        "benchmark_return": benchmark_return,
        "window_start": window_start.strftime("%Y%m%d"),
        "window_end": window_end.strftime("%Y%m%d"),
        "observation_sessions": [value.strftime("%Y%m%d") for value in observed],
        **base,
    }


def _valuation(
    context: BenchmarkContext,
    *,
    candidates: tuple[str, ...],
    lookback: int,
    min_observations: int,
) -> tuple[str, float | None, str | None, dict[str, Any]]:
    """Return field, percentile, reason, and auditable population details."""

    if not context.known:
        return candidates[0], None, context.reason, {"observations": 0}
    axis = list(context.axis)
    try:
        position = axis.index(context.anchor)
    except ValueError:
        return candidates[0], None, "anchor_not_on_session_axis", {"observations": 0}
    population_sessions = axis[max(0, position - lookback) : position]
    fallback: tuple[str, str, dict[str, Any]] | None = None
    for column in candidates:
        if column not in context.stock_history.columns:
            continue
        current = _column_at(context.stock_history, context.anchor, column)
        values = [
            value
            for session in population_sessions
            for value in (_column_at(context.stock_history, session, column),)
            if value is not None and value > 0
        ]
        details = {
            "field": column,
            "current": current,
            "population": "prior_sessions_only",
            "lookback_sessions": lookback,
            "population_start": (
                population_sessions[0].strftime("%Y%m%d") if population_sessions else None
            ),
            "population_end": (
                population_sessions[-1].strftime("%Y%m%d") if population_sessions else None
            ),
            "observations": len(values),
        }
        if current is None:
            continue
        if current <= 0:
            fallback = (column, "valuation_non_positive", details)
            continue
        if len(values) < min_observations:
            fallback = (column, "valuation_insufficient_history", details)
            continue
        return column, float((pd.Series(values) <= current).mean()), None, details
    if fallback is not None:
        return fallback[0], None, fallback[1], fallback[2]
    return candidates[0], None, "valuation_unavailable", {"observations": 0}


def _add_unknown_penalty(
    vector: FeatureVector,
    name: str,
    *,
    settings: CrowdingConfig,
    history: pd.DataFrame,
    reason: str,
    formula: str,
    fields: tuple[str, ...],
    datasets: tuple[str, ...] = ("daily", "daily_basic"),
    extra_config: dict[str, Any] | None = None,
    components: dict[str, Any] | None = None,
) -> None:
    config = _feature_config(settings, **(extra_config or {}))
    evidence_components = dict(components or {})
    evidence_components.setdefault("raw_value", dict(components or {}))
    evidence_components.setdefault("normalized_value", None)
    evidence_components.setdefault("penalty", None)
    add_unknown(
        vector,
        name,
        datasets=datasets,
        fields=fields,
        reason=reason,
        history=history,
        semantic_version=settings.version,
        formula=formula,
        components=evidence_components,
        config=config,
    )


def compute_crowding_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
    config: CrowdingConfig | None = None,
    calendar_frame: pd.DataFrame | None = None,
    disclosure_frame: pd.DataFrame | None = None,
    benchmark_frame: pd.DataFrame | None = None,
    benchmark_definition_frame: pd.DataFrame | None = None,
    suspension_frame: pd.DataFrame | None = None,
    index_daily_frame: pd.DataFrame | None = None,
    index_basic_frame: pd.DataFrame | None = None,
    daily_basic_frame: pd.DataFrame | None = None,
) -> FeatureVector:
    """Compute expectation/crowding v2 with explicit benchmark inputs.

    Preferred production inputs are stock ``daily``/``daily_basic`` merged as
    ``market_frame``, ``index_daily_frame`` as ``benchmark_frame``, and
    ``index_basic_frame`` as the definition snapshot.  A combined market frame
    remains accepted for small legacy/synthetic fixtures only.
    """

    settings = config or CrowdingConfig()
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if index_daily_frame is not None and benchmark_frame is None:
        benchmark_frame = index_daily_frame
    if index_basic_frame is not None and benchmark_definition_frame is None:
        benchmark_definition_frame = index_basic_frame
    vector = new_vector(code, as_of_date)
    vector.version = settings.version
    vector.feature_contract_versions["expectation_crowding"] = settings.version
    vector.benchmark_metadata = _benchmark_config(settings)
    vector.metadata["namespace"] = "expectation_crowding_v2"
    vector.metadata["expectation_crowding_v2"] = {
        "namespace": "expectation_crowding_v2",
        "contract_version": settings.version,
        "expectation_crowding_contract_version": settings.version,
        "config": settings.declared(),
        "benchmark": dict(vector.benchmark_metadata),
        "benchmark_metadata": dict(vector.benchmark_metadata),
        "benchmark_id": settings.benchmark.benchmark_id,
        "benchmark_contract_version": settings.benchmark.version,
        "benchmark_source_dataset": settings.benchmark.source_dataset,
        "source_datasets": list(_CROWDING_SOURCE_DATASETS),
        "as_of_date": vector.as_of_date,
    }

    if daily_basic_frame is not None:
        if market_frame is None or market_frame.empty:
            market_frame = daily_basic_frame.copy()
        elif not daily_basic_frame.empty:
            keys = [
                key
                for key in ("ts_code", "trade_date")
                if key in market_frame.columns and key in daily_basic_frame.columns
            ]
            market_frame = (
                market_frame.merge(
                    daily_basic_frame,
                    on=keys,
                    how="outer",
                    suffixes=("", "_basic"),
                )
                if keys
                else pd.concat([market_frame, daily_basic_frame], ignore_index=True, sort=False)
            )

    required_market_history = max(
        int(lookback),
        settings.history_lookback,
        60,
        max(settings.benchmark.lookbacks),
        settings.benchmark.high_window_sessions
        + (0 if settings.benchmark.high_include_as_of else 1),
        settings.baseline_lookback_sessions + 1,
        settings.valuation_lookback_sessions + 1,
    )
    session_lookback = required_market_history
    if (
        market_frame is None
        or market_frame.empty
        or "ts_code" not in market_frame.columns
        or "trade_date" not in market_frame.columns
    ):
        _anchor_unknowns(vector, "no_market_history", settings=settings)
        return vector

    context = resolve_benchmark(
        market_frame,
        code,
        settings.benchmark.benchmark_id,
        as_of_date,
        benchmark_frame=benchmark_frame,
        benchmark_definition_frame=benchmark_definition_frame,
        suspension_frame=suspension_frame,
        session_lookback=session_lookback,
        calendar_frame=calendar_frame,
    )
    stock_history = context.stock_history
    if not context.known:
        _anchor_unknowns(vector, str(context.reason), history=stock_history, settings=settings)
        return vector

    # --- exact 20D / 60D return contract -------------------------------------
    twenty = window_return(context, 20)
    sixty = window_return(context, 60)
    _add_return_features(
        vector,
        twenty,
        lookback=20,
        settings=settings,
        stock_name="stock_return_20d",
        benchmark_name="benchmark_return_20d",
        excess_name="excess_return_20d",
        stock_alias="recent_return_20d",
        excess_alias="recent_excess_return",
        history=stock_history,
        axis_source=context.axis_source,
    )
    _add_return_features(
        vector,
        sixty,
        lookback=60,
        settings=settings,
        stock_name="stock_return_60d",
        benchmark_name="benchmark_return_60d",
        excess_name="excess_return_60d",
        stock_alias="momentum_60d",
        history=stock_history,
        axis_source=context.axis_source,
    )

    # --- prior 252-session high ----------------------------------------------
    high = high_window(
        context,
        window_sessions=settings.benchmark.high_window_sessions,
        include_as_of=settings.benchmark.high_include_as_of,
        min_sessions=settings.benchmark.high_min_sessions,
    )
    high_config = _feature_config(
        settings,
        high_window_sessions=settings.benchmark.high_window_sessions,
        lookback_sessions=settings.benchmark.high_window_sessions,
        high_include_as_of=settings.benchmark.high_include_as_of,
        high_min_sessions=settings.benchmark.high_min_sessions,
    )
    high_components = {
        "current_price": high.current_close,
        "high": high.high,
        "distance": high.distance,
        "window_start": high.window_start.strftime("%Y%m%d") if high.window_start else None,
        "window_end": high.window_end.strftime("%Y%m%d") if high.window_end else None,
        "session_count": high.session_count,
        "observation_count": high.observation_count,
        "include_as_of": settings.benchmark.high_include_as_of,
    }
    high_names = (
        "distance_to_52w_high",
        "distance_52w_high",
        "high_52w",
        "current_price",
        "high_52w_window_start",
        "high_52w_window_end",
        "high_52w_obs_count",
    )
    if high.status != "known":
        for name in high_names:
            add_unknown(
                vector,
                name,
                datasets=("daily",),
                fields=("close",),
                reason=high.reason or "insufficient_52w_history",
                history=stock_history,
                semantic_version=settings.version,
                config=high_config,
                components=high_components,
            )
    else:
        add_known(
            vector,
            "distance_to_52w_high",
            high.distance,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=settings.version,
            formula="distance_to_52w_high = current_close / prior_252_session_high - 1",
            components=high_components,
            config=high_config,
        )
        add_known(
            vector,
            "distance_52w_high",
            high.distance,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=settings.version,
            formula="distance_52w_high is an alias of distance_to_52w_high",
            components=high_components,
            config={**high_config, "alias_of": "distance_to_52w_high"},
        )
        add_known(
            vector,
            "high_52w",
            high.high,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=settings.version,
            formula="high_52w = max(close over prior 252 trading sessions)",
            components=high_components,
            config=high_config,
        )
        add_known(
            vector,
            "current_price",
            high.current_close,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=settings.version,
            formula="current_price = close at end_session",
            components=high_components,
            config=high_config,
        )
        for name, value in (
            ("high_52w_window_start", high.window_start),
            ("high_52w_window_end", high.window_end),
        ):
            _add_text(
                vector,
                name,
                value.strftime("%Y%m%d") if value else None,
                datasets=("daily",),
                fields=("trade_date", "close"),
                semantic_version=settings.version,
                components=high_components,
                config=high_config,
            )
        add_known(
            vector,
            "high_52w_obs_count",
            high.observation_count,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=settings.version,
            formula="count(valid stock close observations in high window)",
            components=high_components,
            config=high_config,
        )

    # --- past-only volume and turnover spikes -------------------------------
    baseline_config = _feature_config(
        settings,
        lookback_sessions=settings.baseline_lookback_sessions,
        baseline_lookback_sessions=settings.baseline_lookback_sessions,
        baseline_min_observations=settings.baseline_min_observations,
    )
    volume_base = prior_baseline(
        context,
        "vol",
        window=settings.baseline_lookback_sessions,
        min_observations=settings.baseline_min_observations,
    )
    turnover_base = prior_baseline(
        context,
        "turnover_rate",
        window=settings.baseline_lookback_sessions,
        min_observations=settings.baseline_min_observations,
    )

    def add_spike(
        name: str,
        result: Any,
        field_name: str,
        dataset: str,
        threshold: float,
    ) -> None:
        formula = f"{name} = {field_name}(t) / median({field_name} over prior 60 sessions)"
        components = {
            "current": result.current,
            "baseline_median": result.baseline,
            "baseline_sessions": result.observations,
            "start_session": (
                result.window_start.strftime("%Y%m%d") if result.window_start else None
            ),
            "end_session": result.window_end.strftime("%Y%m%d") if result.window_end else None,
        }
        if result.status != "known" or result.baseline in {None, 0.0} or result.current is None:
            add_unknown(
                vector,
                name,
                datasets=(dataset,),
                fields=(field_name,),
                reason=result.reason or f"{field_name}_unavailable",
                history=stock_history,
                semantic_version=settings.version,
                formula=formula,
                components=components,
                config=baseline_config,
            )
            return
        add_known(
            vector,
            name,
            result.current / result.baseline,
            datasets=(dataset,),
            fields=(field_name,),
            history=stock_history,
            semantic_version=settings.version,
            formula=formula,
            components=components,
            config={**baseline_config, "threshold": threshold},
        )

    add_spike("volume_spike", volume_base, "vol", "daily", settings.volume_spike_threshold)
    add_spike(
        "turnover_spike",
        turnover_base,
        "turnover_rate",
        "daily_basic",
        settings.turnover_spike_threshold,
    )

    # --- valuation evidence (positive PE only; evidence-only by default) -----
    valuation_field, valuation_percentile, valuation_reason, valuation_details = _valuation(
        context,
        candidates=("pe_ttm", "pe"),
        lookback=settings.valuation_lookback_sessions,
        min_observations=settings.valuation_min_observations,
    )
    valuation_config = _feature_config(
        settings,
        valuation_field=valuation_field,
        lookback_sessions=settings.valuation_lookback_sessions,
        valuation_lookback_sessions=settings.valuation_lookback_sessions,
        valuation_min_observations=settings.valuation_min_observations,
        valuation_population="prior_sessions_only",
        valuation_positive_only=True,
    )
    valuation_formula = (
        "valuation_percentile = P(positive PE <= PE(t)) over prior valuation sessions"
    )
    if valuation_percentile is None:
        add_unknown(
            vector,
            "valuation_percentile",
            datasets=("daily_basic",),
            fields=("pe_ttm", "pe"),
            reason=valuation_reason or "valuation_unavailable",
            history=stock_history,
            semantic_version=settings.version,
            formula=valuation_formula,
            components=valuation_details,
            config=valuation_config,
        )
    else:
        add_known(
            vector,
            "valuation_percentile",
            valuation_percentile,
            datasets=("daily_basic",),
            fields=(valuation_field,),
            history=stock_history,
            semantic_version=settings.version,
            formula=valuation_formula,
            components=valuation_details,
            config=valuation_config,
        )

    # --- disclosure reaction evidence ----------------------------------------
    reaction = _disclosure_reaction(
        market_frame,
        benchmark_frame,
        disclosure_frame,
        code,
        as_of_date,
        benchmark_id=settings.benchmark.benchmark_id,
        reaction_sessions=settings.disclosure_reaction_sessions,
        session_lookback=session_lookback,
        calendar_frame=calendar_frame,
        benchmark_definition_frame=benchmark_definition_frame,
        suspension_frame=suspension_frame,
    )
    reaction_config = _feature_config(
        settings,
        reaction_sessions=settings.disclosure_reaction_sessions,
        lookback_sessions=settings.disclosure_reaction_sessions,
        availability_rule="actual_date preferred, ann_date fallback; <= as_of",
        reaction_window_rule="first open sessions strictly after availability and <= as_of",
    )
    reaction_reason = reaction.get("reason") or "disclosure_unavailable"
    reaction_components = {
        "stock_return": reaction.get("stock_return"),
        "benchmark_return": reaction.get("benchmark_return"),
        "excess_return": reaction.get("excess"),
        "benchmark_id": settings.benchmark.benchmark_id,
        "availability_date": reaction.get("availability"),
        "availability_source": reaction.get("availability_source"),
        "event_date": reaction.get("event"),
        "start_session": reaction.get("window_start"),
        "end_session": reaction.get("window_end"),
        "window_start": reaction.get("window_start"),
        "window_end": reaction.get("window_end"),
        "observation_sessions": reaction.get("observation_sessions", []),
    }
    if reaction.get("status") == "known":
        add_known(
            vector,
            "disclosure_reaction_excess",
            reaction.get("excess"),
            datasets=("disclosure_date", "daily", "index_daily"),
            fields=("actual_date", "ann_date", "close"),
            history=stock_history,
            semantic_version=settings.version,
            formula="disclosure_reaction_excess = R_stock(post-event) - R_benchmark(post-event)",
            components=reaction_components,
            config=reaction_config,
        )
        for name, key in (
            ("disclosure_availability_date", "availability"),
            ("disclosure_event_date", "event"),
            ("disclosure_reaction_window_start", "window_start"),
            ("disclosure_reaction_window_end", "window_end"),
        ):
            _add_text(
                vector,
                name,
                reaction.get(key),
                datasets=("disclosure_date", "daily", "index_daily"),
                fields=("actual_date", "ann_date", "trade_date", "close"),
                semantic_version=settings.version,
                components=reaction_components,
                config=reaction_config,
            )
    else:
        add_unknown(
            vector,
            "disclosure_reaction_excess",
            datasets=("disclosure_date", "daily", "index_daily"),
            fields=("actual_date", "ann_date", "close"),
            reason=reaction_reason,
            history=stock_history,
            semantic_version=settings.version,
            formula="disclosure_reaction_excess = R_stock(post-event) - R_benchmark(post-event)",
            components=reaction_components,
            config=reaction_config,
        )
        for name in (
            "disclosure_availability_date",
            "disclosure_event_date",
            "disclosure_reaction_window_start",
            "disclosure_reaction_window_end",
        ):
            _add_text(
                vector,
                name,
                None,
                datasets=("disclosure_date", "daily", "index_daily"),
                fields=("actual_date", "ann_date", "trade_date", "close"),
                reason=reaction_reason,
                semantic_version=settings.version,
                components=reaction_components,
                config=reaction_config,
            )

    # --- explicit crowding penalties -----------------------------------------
    def positive_penalty(value: Any, threshold: float) -> float | None:
        parsed = numeric(value)
        if parsed is None:
            return None
        return min(max(parsed, 0.0) / threshold, 1.0)

    def spike_penalty(value: Any, threshold: float) -> float | None:
        parsed = numeric(value)
        if parsed is None:
            return None
        return min(max(parsed - 1.0, 0.0) / max(threshold - 1.0, 0.0001), 1.0)

    excess_20 = vector.values.get("excess_return_20d")
    excess_60 = vector.values.get("excess_return_60d")
    volume_spike = vector.values.get("volume_spike")
    turnover_spike = vector.values.get("turnover_spike")
    repricing_20 = positive_penalty(excess_20, settings.repricing_20d_threshold)
    repricing_60 = positive_penalty(excess_60, settings.repricing_60d_threshold)
    current_price = numeric(vector.values.get("current_price"))
    prior_high = numeric(vector.values.get("high_52w"))
    high_proximity = (
        min(max(current_price / prior_high, 0.0), 1.0)
        if current_price is not None and prior_high not in {None, 0.0}
        else None
    )
    volume_penalty = spike_penalty(volume_spike, settings.volume_spike_threshold)
    turnover_penalty = spike_penalty(turnover_spike, settings.turnover_spike_threshold)
    return_20_components = dict(
        vector.evidence["excess_return_20d"].components
    )
    return_60_components = dict(
        vector.evidence["excess_return_60d"].components
    )
    high_penalty_components = {
        **high_components,
        "current_price": current_price,
        "prior_high": prior_high,
        "observation_sessions": {
            "start_session": high_components.get("window_start"),
            "end_session": high_components.get("window_end"),
            "count": high_components.get("observation_count"),
        },
    }
    volume_penalty_components = {
        "volume_spike": volume_spike,
        "observation_sessions": {
            "start_session": volume_base.window_start.strftime("%Y%m%d")
            if volume_base.window_start
            else None,
            "end_session": volume_base.window_end.strftime("%Y%m%d")
            if volume_base.window_end
            else None,
            "count": volume_base.observations,
        },
    }
    turnover_penalty_components = {
        "turnover_spike": turnover_spike,
        "observation_sessions": {
            "start_session": turnover_base.window_start.strftime("%Y%m%d")
            if turnover_base.window_start
            else None,
            "end_session": turnover_base.window_end.strftime("%Y%m%d")
            if turnover_base.window_end
            else None,
            "count": turnover_base.observations,
        },
    }

    def add_penalty(
        name: str,
        value: float | None,
        formula: str,
        fields: tuple[str, ...],
        components: dict[str, Any],
        extra_config: dict[str, Any],
        missing_reason: str,
        datasets: tuple[str, ...] = ("daily", "daily_basic"),
    ) -> None:
        penalty_components = dict(components)
        penalty_components.setdefault("raw_value", dict(components))
        penalty_components["normalized_value"] = value
        penalty_components["penalty"] = value
        if value is None:
            _add_unknown_penalty(
                vector,
                name,
                settings=settings,
                history=stock_history,
                reason=missing_reason,
                formula=formula,
                fields=fields,
                datasets=datasets,
                extra_config=extra_config,
                components=penalty_components,
            )
        else:
            add_known(
                vector,
                name,
                value,
                datasets=datasets,
                fields=fields,
                history=stock_history,
                semantic_version=settings.version,
                formula=formula,
                components=penalty_components,
                config=_feature_config(settings, **extra_config),
            )

    add_penalty(
        "repricing_20d",
        repricing_20,
        "repricing_20d = min(max(excess_return_20d, 0) / threshold, 1)",
        ("close",),
        {
            "excess_return_20d": excess_20,
            "observation_sessions": return_20_components,
        },
        {
            "threshold": settings.repricing_20d_threshold,
            "lookback_sessions": 20,
        },
        "excess_return_20d_unknown",
        datasets=("daily", "index_daily"),
    )
    add_penalty(
        "repricing_60d",
        repricing_60,
        "repricing_60d = min(max(excess_return_60d, 0) / threshold, 1)",
        ("close",),
        {
            "excess_return_60d": excess_60,
            "observation_sessions": return_60_components,
        },
        {
            "threshold": settings.repricing_60d_threshold,
            "lookback_sessions": 60,
        },
        "excess_return_60d_unknown",
        datasets=("daily", "index_daily"),
    )
    add_penalty(
        "high_proximity",
        high_proximity,
        "high_proximity = min(max(current_price / prior_high, 0), 1)",
        ("close",),
        high_penalty_components,
        {
            "high_window_sessions": settings.benchmark.high_window_sessions,
            "lookback_sessions": settings.benchmark.high_window_sessions,
        },
        "distance_to_52w_high_unknown",
        datasets=("daily",),
    )
    add_penalty(
        "volume_spike_penalty",
        volume_penalty,
        "volume_spike_penalty = min(max(volume_spike - 1, 0) / (threshold - 1), 1)",
        ("vol",),
        volume_penalty_components,
        {
            "threshold": settings.volume_spike_threshold,
            "lookback_sessions": settings.baseline_lookback_sessions,
        },
        "volume_spike_unknown",
        datasets=("daily",),
    )
    add_penalty(
        "turnover_spike_penalty",
        turnover_penalty,
        "turnover_spike_penalty = min(max(turnover_spike - 1, 0) / (threshold - 1), 1)",
        ("turnover_rate",),
        turnover_penalty_components,
        {
            "threshold": settings.turnover_spike_threshold,
            "lookback_sessions": settings.baseline_lookback_sessions,
        },
        "turnover_spike_unknown",
        datasets=("daily_basic",),
    )

    valuation_penalty: float | None = None
    if settings.include_valuation_in_penalty:
        valuation_penalty = numeric(vector.values.get("valuation_percentile"))
        add_penalty(
            "valuation_penalty",
            valuation_penalty,
            "valuation_penalty = valuation_percentile",
            (valuation_field,),
            {
                **valuation_details,
                "valuation_percentile": valuation_penalty,
                "observation_sessions": {
                    "start_session": valuation_details.get("population_start"),
                    "end_session": valuation_details.get("population_end"),
                    "count": valuation_details.get("observations"),
                },
            },
            {
                "valuation_field": valuation_field,
                "include_valuation_in_penalty": True,
                "lookback_sessions": settings.valuation_lookback_sessions,
            },
            "valuation_percentile_unknown",
        )
    else:
        _add_unknown_penalty(
            vector,
            "valuation_penalty",
            settings=settings,
            history=stock_history,
            reason="valuation_excluded_from_penalty_by_config",
            formula="valuation_penalty = valuation_percentile (opt-in only)",
            fields=(valuation_field,),
            extra_config={
                "include_valuation_in_penalty": False,
                "lookback_sessions": settings.valuation_lookback_sessions,
            },
            components={"valuation_percentile": vector.values.get("valuation_percentile")},
        )

    disclosure_penalty: float | None = None
    if settings.include_disclosure_in_penalty:
        disclosure_penalty = positive_penalty(
            vector.values.get("disclosure_reaction_excess"), settings.disclosure_reaction_threshold
        )
        add_penalty(
            "disclosure_reaction_penalty",
            disclosure_penalty,
            "disclosure_reaction_penalty = min(max(excess, 0) / threshold, 1)",
            ("actual_date", "ann_date", "close"),
            {
                "disclosure_reaction_excess": vector.values.get(
                    "disclosure_reaction_excess"
                ),
                "observation_sessions": reaction_components.get("observation_sessions", []),
            },
            {
                "threshold": settings.disclosure_reaction_threshold,
                "include_disclosure_in_penalty": True,
                "lookback_sessions": settings.disclosure_reaction_sessions,
            },
            "disclosure_reaction_excess_unknown",
        )
    else:
        _add_unknown_penalty(
            vector,
            "disclosure_reaction_penalty",
            settings=settings,
            history=stock_history,
            reason="disclosure_excluded_from_penalty_by_config",
            formula="disclosure_reaction_penalty = positive reaction (opt-in only)",
            fields=("actual_date", "ann_date", "close"),
            extra_config={
                "include_disclosure_in_penalty": False,
                "lookback_sessions": settings.disclosure_reaction_sessions,
            },
            components={
                "disclosure_reaction_excess": vector.values.get("disclosure_reaction_excess")
            },
        )

    mandatory = {
        "repricing_20d": repricing_20,
        "repricing_60d": repricing_60,
        "high_proximity": high_proximity,
        "volume_spike_penalty": volume_penalty,
        "turnover_spike_penalty": turnover_penalty,
    }
    missing = [name for name, value in mandatory.items() if value is None]
    if settings.include_valuation_in_penalty and valuation_penalty is None:
        missing.append("valuation_penalty")
    if settings.include_disclosure_in_penalty and disclosure_penalty is None:
        missing.append("disclosure_reaction_penalty")

    if missing:
        reason = f"missing_penalty_component:{missing[0]}"
        for name in ("crowding_penalty", "expectation_score"):
            _add_unknown_penalty(
                vector,
                name,
                settings=settings,
                history=stock_history,
                reason=reason,
                formula=(
                    "crowding_penalty = 100 * mean(known penalty components); "
                    "all mandatory components are required"
                    if name == "crowding_penalty"
                    else "expectation_score = 100 - crowding_penalty"
                ),
                fields=("close", "vol", "turnover_rate"),
                extra_config={
                    "mandatory_components": tuple(mandatory),
                    "lookback_sessions": (20, 60, 252, settings.baseline_lookback_sessions),
                },
                components={"missing_components": missing},
            )
    else:
        parts = list(mandatory.items())
        if settings.include_valuation_in_penalty:
            parts.append(("valuation_penalty", valuation_penalty))
        if settings.include_disclosure_in_penalty:
            parts.append(("disclosure_reaction_penalty", disclosure_penalty))
        penalty = 100.0 * sum(float(value) for _, value in parts) / len(parts)
        aggregate_config = _feature_config(
            settings,
            mandatory_components=tuple(mandatory),
            lookback_sessions=(
                20,
                60,
                settings.benchmark.high_window_sessions,
                settings.baseline_lookback_sessions,
            ),
            included_optional_components=tuple(
                name
                for name, enabled in (
                    ("valuation_penalty", settings.include_valuation_in_penalty),
                    ("disclosure_reaction_penalty", settings.include_disclosure_in_penalty),
                )
                if enabled
            ),
        )
        aggregate_components = {
            "penalty_components": {name: value for name, value in parts},
            "normalized_value": penalty,
            "penalty": penalty,
            "raw_value": {name: value for name, value in parts},
            "observation_sessions": {
                "end_session": context.anchor.strftime("%Y%m%d"),
                "benchmark_id": settings.benchmark.benchmark_id,
            },
        }
        add_known(
            vector,
            "crowding_penalty",
            penalty,
            datasets=_CROWDING_SOURCE_DATASETS,
            fields=("close", "vol", "turnover_rate"),
            history=stock_history,
            semantic_version=settings.version,
            formula="crowding_penalty = 100 * mean(penalty components)",
            components=aggregate_components,
            config=aggregate_config,
        )
        add_known(
            vector,
            "expectation_score",
            100.0 - penalty,
            datasets=_CROWDING_SOURCE_DATASETS,
            fields=("close", "vol", "turnover_rate"),
            history=stock_history,
            semantic_version=settings.version,
            formula="expectation_score = 100 - crowding_penalty",
            components={"crowding_penalty": penalty},
            config=aggregate_config,
        )
        if penalty >= settings.crowding_flag_threshold:
            vector.risk_flags.append("already_repriced_or_crowded")

    return vector
