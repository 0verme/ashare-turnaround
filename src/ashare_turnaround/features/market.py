"""PIT-safe low-attention and low-expectation/crowding proxies.

Attention (``compute_attention_features``) and crowding/expectation
(``compute_crowding_features``) are independent feature groups.  Crowding v2 is
benchmark-relative by construction: every repricing feature is defined as
``stock return - benchmark return`` over the same trading-session window, and a
missing/unresolvable benchmark makes the affected features ``unknown`` with an
explicit reason (never a quiet fallback to stock-only returns).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..scanner.contracts import FeatureVector
from .benchmark import (
    BenchmarkConfig,
    _column_at,
    high_window,
    prior_baseline,
    resolve_benchmark,
    window_return,
)
from .common import add_known, add_unknown, market_history, new_vector, numeric

# Names that crowding v2 always emits (known or unknown with a reason).
_CROWDING_FEATURES: tuple[str, ...] = (
    "recent_return_20d",
    "benchmark_return_20d",
    "recent_excess_return",
    "momentum_60d",
    "benchmark_return_60d",
    "excess_return_60d",
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


def _anchor_unknowns(
    vector: FeatureVector,
    reason: str,
    *,
    history: pd.DataFrame | None = None,
    semantic_version: str,
) -> None:
    for name in _CROWDING_FEATURES:
        add_unknown(
            vector,
            name,
            datasets=("daily", "daily_basic"),
            fields=(),
            reason=reason,
            history=history,
            semantic_version=semantic_version,
        )


def _percentile(values: pd.Series, current: Any) -> float | None:
    current_value = numeric(current)
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if current_value is None or clean.empty:
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
) -> None:
    vector.add(
        name,
        value,
        status="known" if value is not None else "unknown",
        source_datasets=datasets,
        source_fields=fields,
        reason=reason if value is None else None,
        semantic_version=semantic_version,
        components=components,
    )


def compute_attention_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
) -> FeatureVector:
    """Low-attention proxies (attention group, untouched by crowding v2)."""

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
                vector, name, datasets=("daily_basic",), fields=(), reason="no market history"
            )
        return vector
    latest = history.iloc[-1]
    turnover = _percentile(
        history.get("turnover_rate", pd.Series(dtype="float64")), latest.get("turnover_rate")
    )
    amount = _percentile(history.get("amount", pd.Series(dtype="float64")), latest.get("amount"))
    volume = pd.to_numeric(history.get("vol", pd.Series(dtype="float64")), errors="coerce").dropna()
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
    known = [value for value in (turnover, amount, abnormal) if value is not None]
    score = None
    if known:
        score = 100.0 * (
            (1.0 - (turnover or 0.5)) * 0.4
            + (1.0 - (amount or 0.5)) * 0.4
            + (1.0 - min(abnormal or 1.0, 3.0) / 3.0) * 0.2
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
    """Versioned configuration of the crowding/expectation v2 feature group.

    Thresholds are feature-level calibration constants recorded in every
    penalty's evidence.  They do not change ``scanner.score.ScoreConfig``.
    """

    version: str = "crowding-v2"
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    repricing_20d_threshold: float = 0.15
    repricing_60d_threshold: float = 0.30
    volume_spike_threshold: float = 2.0
    turnover_spike_threshold: float = 2.0
    include_valuation_in_penalty: bool = False
    include_disclosure_in_penalty: bool = False
    disclosure_reaction_sessions: int = 5
    disclosure_reaction_threshold: float = 0.10
    crowding_flag_threshold: float = 70.0
    history_lookback: int = 400

    def declared(self) -> dict[str, Any]:
        return asdict(self)


def _penalty_value(ratio: float | None, threshold: float) -> float | None:
    if ratio is None:
        return None
    return min(max(ratio, 0.0) / threshold, 1.0)


def _spike_penalty(ratio: float | None, threshold: float) -> float | None:
    if ratio is None:
        return None
    denominator = max(threshold - 1.0, 0.0001)
    return min(max(ratio - 1.0, 0.0) / denominator, 1.0)


def _disclosure_before_as_of(
    disclosure_frame: pd.DataFrame | None,
    code: str,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
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
    availability = pd.Series(pd.NaT, index=matched.index, dtype="datetime64[ns]")
    for field_name in ("actual_date", "ann_date"):
        if field_name in matched.columns:
            availability = availability.fillna(
                pd.to_datetime(matched[field_name], errors="coerce")
            )
    matched["_availability"] = availability
    matched["_event"] = pd.NaT
    for field_name in ("ann_date", "actual_date"):
        if field_name in matched.columns:
            matched["_event"] = matched["_event"].fillna(
                pd.to_datetime(matched[field_name], errors="coerce")
            )
    return matched.loc[
        matched["_availability"].notna() & matched["_availability"].dt.normalize().le(as_of)
    ].sort_values("_availability")


def _disclosure_reaction(
    market_frame: pd.DataFrame | None,
    disclosure_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    benchmark_id: str = "000300.SH",
    reaction_sessions: int = 5,
    session_lookback: int = 400,
) -> dict[str, Any]:
    """Benchmark-relative stock reaction over the first post-event sessions.

    The reaction window only consumes sessions strictly after the disclosure
    is available and at or before as-of; disclosures disclosed after as-of are
    invisible.
    """

    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid disclosure as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    context = resolve_benchmark(
        market_frame,
        code,
        benchmark_id,
        as_of,
        session_lookback=session_lookback,
    )
    if not context.known:
        return {"status": "unknown", "reason": context.reason}
    matched = (
        disclosure_frame.loc[disclosure_frame["ts_code"].astype("string").eq(str(code))].copy()
        if disclosure_frame is not None and not disclosure_frame.empty
        else pd.DataFrame()
    )
    if matched.empty:
        return {"status": "unknown", "reason": "no_disclosure_before_as_of"}
    provable = (
        matched["actual_date"].notna()
        if "actual_date" in matched.columns
        else pd.Series(False, index=matched.index)
    )
    if "ann_date" in matched.columns:
        provable = provable | matched["ann_date"].notna()
    if not provable.any():
        return {"status": "unknown", "reason": "disclosure_timing_unprovable"}
    disclosures = _disclosure_before_as_of(disclosure_frame, code, as_of)
    if disclosures.empty:
        return {"status": "unknown", "reason": "no_disclosure_before_as_of"}
    latest = disclosures.iloc[-1]
    availability = latest["_availability"]
    if pd.isna(availability):
        return {"status": "unknown", "reason": "disclosure_timing_unprovable"}
    availability_text = pd.Timestamp(availability).strftime("%Y%m%d")
    event = latest.get("_event")
    event_text = pd.Timestamp(event).strftime("%Y%m%d") if pd.notna(event) else None
    axis = list(context.axis)
    after = [
        session for session in axis if session > pd.Timestamp(availability).normalize()
    ]
    if len(after) < reaction_sessions:
        return {
            "status": "unknown",
            "reason": "insufficient_reaction_window",
            "availability": availability_text,
            "event": event_text,
            "after_sessions": len(after),
        }
    window_start = after[0]
    window_end = after[reaction_sessions - 1]
    stock_start = _column_at(context.stock_history, window_start, "close")
    stock_end = _column_at(context.stock_history, window_end, "close")
    benchmark_start = _column_at(context.benchmark_history, window_start, "close")
    benchmark_end = _column_at(context.benchmark_history, window_end, "close")
    if (
        stock_start in {None, 0.0}
        or stock_end is None
        or benchmark_start in {None, 0.0}
        or benchmark_end is None
    ):
        return {
            "status": "unknown",
            "reason": "stock_missing_at_reaction_window",
            "availability": availability_text,
            "event": event_text,
            "window_start": window_start.strftime("%Y%m%d"),
            "window_end": window_end.strftime("%Y%m%d"),
        }
    stock_return = stock_end / stock_start - 1.0
    benchmark_return = benchmark_end / benchmark_start - 1.0
    return {
        "status": "known",
        "reason": None,
        "excess": stock_return - benchmark_return,
        "stock_return": stock_return,
        "benchmark_return": benchmark_return,
        "availability": availability_text,
        "event": event_text,
        "window_start": window_start.strftime("%Y%m%d"),
        "window_end": window_end.strftime("%Y%m%d"),
    }


def compute_crowding_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
    config: CrowdingConfig | None = None,
    calendar_frame: pd.DataFrame | None = None,
    disclosure_frame: pd.DataFrame | None = None,
) -> FeatureVector:
    """Benchmark-relative crowding/expectation v2 features.

    ``market_frame`` must contain rows for both the stock and the benchmark
    (``benchmark.benchmark_id``).  Every benchmark-relative feature is
    ``unknown`` with a reason when the benchmark cannot be resolved.
    """

    settings = config or CrowdingConfig()
    version = settings.version
    benchmark = settings.benchmark
    vector = new_vector(code, as_of_date)
    vector.version = version
    session_lookback = max(lookback, settings.history_lookback)

    if (
        market_frame is None
        or market_frame.empty
        or "ts_code" not in market_frame.columns
        or "trade_date" not in market_frame.columns
    ):
        _anchor_unknowns(vector, "no_market_history", semantic_version=version)
        return vector

    context = resolve_benchmark(
        market_frame,
        code,
        benchmark.benchmark_id,
        as_of_date,
        session_lookback=session_lookback,
        calendar_frame=calendar_frame,
    )
    if not context.known:
        _anchor_unknowns(
            vector,
            str(context.reason),
            history=context.stock_history,
            semantic_version=version,
        )
        return vector

    benchmark_config = benchmark.declared()
    stock_history = context.stock_history

    # --- 20D / 60D benchmark-relative returns -------------------------------
    return_fields = ("close",)
    for label, window in (("20", window_return(context, 20)), ("60", window_return(context, 60))):
        suffix = "20d" if label == "20" else "60d"
        stock_name = "recent_return_20d" if label == "20" else "momentum_60d"
        excess_name = "recent_excess_return" if label == "20" else f"excess_return_{suffix}"
        bench_name = f"benchmark_return_{suffix}"
        formula = (
            f"R_stock(t,{label}) = close(t)/close(t-{label}) - 1"
            f"; R_bench(t,{label}) = B(t)/B(t-{label}) - 1"
            "; excess = R_stock - R_bench"
        )
        if window.status != "known":
            reason = window.reason or f"unknown_{suffix}_return"
            for name in (stock_name, bench_name, excess_name):
                add_unknown(
                    vector,
                    name,
                    datasets=("daily",),
                    fields=return_fields,
                    reason=reason,
                    history=stock_history,
                    semantic_version=version,
                )
            continue
        components = {
            "stock_return": window.stock_return,
            "benchmark_return": window.benchmark_return,
            "excess_return": window.excess_return,
            "anchor_session": window.anchor.strftime("%Y%m%d"),
            "window_start_session": window.window_start.strftime("%Y%m%d"),
            "sessions": window.sessions,
        }
        for name, value in (
            (stock_name, window.stock_return),
            (bench_name, window.benchmark_return),
            (excess_name, window.excess_return),
        ):
            add_known(
                vector,
                name,
                value,
                datasets=("daily",),
                fields=return_fields,
                history=stock_history,
                semantic_version=version,
                formula=formula,
                components=components,
                config=benchmark_config,
            )

    # --- 52-week high window --------------------------------------------------
    high = high_window(
        context,
        window_sessions=benchmark.high_window_sessions,
        include_as_of=benchmark.high_include_as_of,
        min_sessions=benchmark.high_min_sessions,
    )
    high_config = {
        **benchmark_config,
        "high_window_sessions": benchmark.high_window_sessions,
        "high_include_as_of": benchmark.high_include_as_of,
        "high_min_sessions": benchmark.high_min_sessions,
    }
    if high.status != "known":
        for name in (
            "distance_52w_high",
            "high_52w",
            "current_price",
            "high_52w_window_start",
            "high_52w_window_end",
            "high_52w_obs_count",
        ):
            add_unknown(
                vector,
                name,
                datasets=("daily",),
                fields=("close",),
                reason=high.reason or "insufficient_52w_history",
                history=stock_history,
                semantic_version=version,
            )
    else:
        high_components = {
            "current_price": high.current_close,
            "high": high.high,
            "distance": high.distance,
            "window_start": high.window_start.strftime("%Y%m%d"),
            "window_end": high.window_end.strftime("%Y%m%d"),
            "session_count": high.session_count,
            "observation_count": high.observation_count,
        }
        add_known(
            vector,
            "high_52w",
            high.high,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=version,
            formula="high_52w = max(close over 52-week session window)",
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
            semantic_version=version,
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
            semantic_version=version,
            formula="distance_52w_high = 1 - close(t) / max(close over window)",
            components=high_components,
            config=high_config,
        )
        _add_text(
            vector,
            "high_52w_window_start",
            high.window_start.strftime("%Y%m%d"),
            datasets=("daily",),
            fields=("close",),
            semantic_version=version,
            components=high_components,
        )
        _add_text(
            vector,
            "high_52w_window_end",
            high.window_end.strftime("%Y%m%d"),
            datasets=("daily",),
            fields=("close",),
            semantic_version=version,
            components=high_components,
        )
        add_known(
            vector,
            "high_52w_obs_count",
            float(high.observation_count),
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=version,
            components=high_components,
            config=high_config,
        )

    # --- volume / turnover spikes ----------------------------------------------
    volume_baseline = prior_baseline(context, "vol", window=60, min_observations=20)
    if volume_baseline.status != "known" or volume_baseline.baseline in {None, 0.0}:
        add_unknown(
            vector,
            "volume_spike",
            datasets=("daily",),
            fields=("vol",),
            reason=volume_baseline.reason or "volume_unavailable",
            history=stock_history,
            semantic_version=version,
        )
    else:
        ratio = volume_baseline.current / volume_baseline.baseline
        add_known(
            vector,
            "volume_spike",
            ratio,
            datasets=("daily",),
            fields=("vol",),
            history=stock_history,
            semantic_version=version,
            formula="volume_spike = vol(t) / median(vol over prior 60 sessions)",
            components={
                "volume_at_anchor": volume_baseline.current,
                "baseline_median": volume_baseline.baseline,
                "baseline_sessions": volume_baseline.observations,
                "window_start": volume_baseline.window_start.strftime("%Y%m%d"),
                "window_end": volume_baseline.window_end.strftime("%Y%m%d"),
            },
            config=benchmark_config,
        )

    turnover_baseline = prior_baseline(context, "turnover_rate", window=60, min_observations=20)
    if turnover_baseline.status != "known" or turnover_baseline.baseline in {None, 0.0}:
        add_unknown(
            vector,
            "turnover_spike",
            datasets=("daily_basic",),
            fields=("turnover_rate",),
            reason=turnover_baseline.reason or "turnover_unavailable",
            history=stock_history,
            semantic_version=version,
        )
    else:
        ratio = turnover_baseline.current / turnover_baseline.baseline
        add_known(
            vector,
            "turnover_spike",
            ratio,
            datasets=("daily_basic",),
            fields=("turnover_rate",),
            history=stock_history,
            semantic_version=version,
            formula=(
                "turnover_spike = turnover_rate(t) / median(turnover_rate over prior 60 sessions)"
            ),
            components={
                "turnover_at_anchor": turnover_baseline.current,
                "baseline_median": turnover_baseline.baseline,
                "baseline_sessions": turnover_baseline.observations,
                "window_start": turnover_baseline.window_start.strftime("%Y%m%d"),
                "window_end": turnover_baseline.window_end.strftime("%Y%m%d"),
            },
            config=benchmark_config,
        )

    # --- valuation percentile (evidence only; unreliable -> unknown) ----------
    valuation_column = "pe_ttm" if "pe_ttm" in stock_history.columns else "pe"
    if valuation_column not in stock_history.columns:
        add_unknown(
            vector,
            "valuation_percentile",
            datasets=("daily_basic",),
            fields=("pe_ttm", "pe"),
            reason="valuation_unavailable",
            history=stock_history,
            semantic_version=version,
        )
    else:
        axis = list(context.axis)
        try:
            position = axis.index(context.anchor)
        except ValueError:
            position = -1
        window = axis[max(0, position - 251) : position + 1] if position >= 0 else []
        values = [
            value
            for value in (
                _column_at(stock_history, session, valuation_column) for session in window
            )
            if value is not None
        ]
        current_valuation = _column_at(stock_history, context.anchor, valuation_column)
        percentile = _percentile(pd.Series(values, dtype="float64"), current_valuation)
        add_known(
            vector,
            "valuation_percentile",
            percentile,
            datasets=("daily_basic",),
            fields=(valuation_column,),
            history=stock_history,
            semantic_version=version,
            formula=(
                "valuation_percentile = P(pe <= pe(t)) over trailing 252 sessions; "
                f"field={valuation_column}"
            ),
            components={"current": current_valuation, "observations": len(values)},
            config=benchmark_config,
            reason="valuation_unavailable" if percentile is None else None,
        )

    # --- disclosure reaction (PIT-provable only; evidence by default) ----------
    reaction = _disclosure_reaction(
        market_frame,
        disclosure_frame,
        code,
        as_of_date,
        benchmark_id=benchmark.benchmark_id,
        reaction_sessions=settings.disclosure_reaction_sessions,
        session_lookback=session_lookback,
    )
    disclosure_reason = reaction.get("reason") or "disclosure_unavailable"
    add_unknown(
        vector,
        "disclosure_reaction_excess",
        datasets=("disclosure_date", "daily"),
        fields=("actual_date", "ann_date", "close"),
        reason=disclosure_reason,
        history=stock_history,
        semantic_version=version,
    )
    add_unknown(
        vector,
        "disclosure_reaction_penalty",
        datasets=("disclosure_date", "daily"),
        fields=("actual_date", "ann_date", "close"),
        reason=disclosure_reason,
        history=stock_history,
        semantic_version=version,
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
            datasets=("disclosure_date", "daily"),
            fields=("actual_date", "ann_date", "trade_date"),
            reason=disclosure_reason,
            semantic_version=version,
        )
    if reaction.get("status") == "known" and reaction.get("excess") is not None:
        reaction_fields = ("actual_date", "ann_date", "close")
        reaction_components = {
            "stock_return": reaction.get("stock_return"),
            "benchmark_return": reaction.get("benchmark_return"),
            "event_date": reaction.get("event"),
            "availability_date": reaction.get("availability"),
            "window_start": reaction.get("window_start"),
            "window_end": reaction.get("window_end"),
        }
        add_known(
            vector,
            "disclosure_reaction_excess",
            reaction["excess"],
            datasets=("disclosure_date", "daily"),
            fields=reaction_fields,
            history=stock_history,
            semantic_version=version,
            formula=(
                "disclosure_reaction_excess = R_stock(event..event+5 sessions)"
                " - R_benchmark(same window)"
            ),
            components=reaction_components,
            config={
                **benchmark_config,
                "reaction_sessions": settings.disclosure_reaction_sessions,
            },
        )
        _add_text(
            vector,
            "disclosure_availability_date",
            reaction.get("availability"),
            datasets=("disclosure_date",),
            fields=("actual_date", "ann_date"),
            semantic_version=version,
            components=reaction_components,
        )
        _add_text(
            vector,
            "disclosure_event_date",
            reaction.get("event"),
            datasets=("disclosure_date",),
            fields=("ann_date", "actual_date"),
            semantic_version=version,
            components=reaction_components,
        )
        _add_text(
            vector,
            "disclosure_reaction_window_start",
            reaction.get("window_start"),
            datasets=("daily",),
            fields=("trade_date",),
            semantic_version=version,
            components=reaction_components,
        )
        _add_text(
            vector,
            "disclosure_reaction_window_end",
            reaction.get("window_end"),
            datasets=("daily",),
            fields=("trade_date",),
            semantic_version=version,
            components=reaction_components,
        )
        if settings.include_disclosure_in_penalty:
            add_known(
                vector,
                "disclosure_reaction_penalty",
                _penalty_value(reaction["excess"], settings.disclosure_reaction_threshold),
                datasets=("disclosure_date", "daily"),
                fields=("actual_date", "ann_date", "close"),
                history=stock_history,
                semantic_version=version,
                formula=(
                    "disclosure_reaction_penalty = min(max(excess,0)/"
                    f"{settings.disclosure_reaction_threshold},1)"
                ),
                components={"excess": reaction["excess"]},
                config={
                    **benchmark_config,
                    "threshold": settings.disclosure_reaction_threshold,
                },
            )

    # --- penalty composition -----------------------------------------------------
    penalty_parts: list[tuple[str, float, str]] = []
    recent_excess = vector.values.get("recent_excess_return")
    excess_60 = vector.values.get("excess_return_60d")
    distance = vector.values.get("distance_52w_high")
    volume_spike = vector.values.get("volume_spike")
    turnover_spike = vector.values.get("turnover_spike")
    valuation_percentile = vector.values.get("valuation_percentile")

    repricing_20 = _penalty_value(recent_excess, settings.repricing_20d_threshold)
    repricing_60 = _penalty_value(excess_60, settings.repricing_60d_threshold)
    proximity = None if distance is None else min(max(1.0 - distance, 0.0), 1.0)
    volume_penalty = _spike_penalty(volume_spike, settings.volume_spike_threshold)
    turnover_penalty = _spike_penalty(turnover_spike, settings.turnover_spike_threshold)

    if repricing_20 is not None:
        add_known(
            vector,
            "repricing_20d",
            repricing_20,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=version,
            formula=(
                "repricing_20d = min(max(recent_excess_return,0)/"
                f"{settings.repricing_20d_threshold},1)"
            ),
            components={"recent_excess_return": recent_excess},
            config={**benchmark_config, "threshold": settings.repricing_20d_threshold},
        )
        penalty_parts.append(
            ("repricing_20d", repricing_20, "20D excess return above threshold")
        )
    else:
        add_unknown(
            vector,
            "repricing_20d",
            datasets=("daily",),
            fields=("close",),
            reason="recent_excess_return unknown",
            history=stock_history,
            semantic_version=version,
        )
    if repricing_60 is not None:
        add_known(
            vector,
            "repricing_60d",
            repricing_60,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=version,
            formula=(
                "repricing_60d = min(max(excess_return_60d,0)/"
                f"{settings.repricing_60d_threshold},1)"
            ),
            components={"excess_return_60d": excess_60},
            config={**benchmark_config, "threshold": settings.repricing_60d_threshold},
        )
        penalty_parts.append(
            ("repricing_60d", repricing_60, "60D excess return above threshold")
        )
    else:
        add_unknown(
            vector,
            "repricing_60d",
            datasets=("daily",),
            fields=("close",),
            reason="excess_return_60d unknown",
            history=stock_history,
            semantic_version=version,
        )
    if proximity is not None:
        add_known(
            vector,
            "high_proximity",
            proximity,
            datasets=("daily",),
            fields=("close",),
            history=stock_history,
            semantic_version=version,
            formula="high_proximity = min(max(1 - distance_52w_high,0),1) == close/high",
            components={"distance_52w_high": distance},
            config={
                **benchmark_config,
                "high_window_sessions": benchmark.high_window_sessions,
            },
        )
        penalty_parts.append(("high_proximity", proximity, "price sits at 52-week high"))
    else:
        add_unknown(
            vector,
            "high_proximity",
            datasets=("daily",),
            fields=("close",),
            reason="distance_52w_high unknown",
            history=stock_history,
            semantic_version=version,
        )
    if volume_penalty is not None:
        add_known(
            vector,
            "volume_spike_penalty",
            volume_penalty,
            datasets=("daily",),
            fields=("vol",),
            history=stock_history,
            semantic_version=version,
            formula=(
                "volume_spike_penalty = min(max(volume_spike-1,0)/"
                f"{settings.volume_spike_threshold - 1.0},1)"
            ),
            components={"volume_spike": volume_spike},
            config={**benchmark_config, "threshold": settings.volume_spike_threshold},
        )
        penalty_parts.append(("volume_spike_penalty", volume_penalty, "abnormal volume"))
    else:
        add_unknown(
            vector,
            "volume_spike_penalty",
            datasets=("daily",),
            fields=("vol",),
            reason="volume_spike unknown",
            history=stock_history,
            semantic_version=version,
        )
    if turnover_penalty is not None:
        add_known(
            vector,
            "turnover_spike_penalty",
            turnover_penalty,
            datasets=("daily_basic",),
            fields=("turnover_rate",),
            history=stock_history,
            semantic_version=version,
            formula=(
                "turnover_spike_penalty = min(max(turnover_spike-1,0)/"
                f"{settings.turnover_spike_threshold - 1.0},1)"
            ),
            components={"turnover_spike": turnover_spike},
            config={**benchmark_config, "threshold": settings.turnover_spike_threshold},
        )
        penalty_parts.append(("turnover_spike_penalty", turnover_penalty, "abnormal turnover"))
    else:
        add_unknown(
            vector,
            "turnover_spike_penalty",
            datasets=("daily_basic",),
            fields=("turnover_rate",),
            reason="turnover_spike unknown",
            history=stock_history,
            semantic_version=version,
        )
    if settings.include_valuation_in_penalty and valuation_percentile is not None:
        valuation_penalty = 1.0 - valuation_percentile
        add_known(
            vector,
            "valuation_penalty",
            valuation_penalty,
            datasets=("daily_basic",),
            fields=(valuation_column,),
            history=stock_history,
            semantic_version=version,
            formula="valuation_penalty = 1 - valuation_percentile",
            components={"valuation_percentile": valuation_percentile},
            config={**benchmark_config, "include_valuation_in_penalty": True},
        )
        penalty_parts.append(("valuation_penalty", valuation_penalty, "expensive valuation"))
    else:
        add_unknown(
            vector,
            "valuation_penalty",
            datasets=("daily_basic",),
            fields=("pe_ttm", "pe"),
            reason=(
                "valuation_unavailable"
                if valuation_percentile is None
                else "valuation_excluded_from_penalty_by_config"
            ),
            history=stock_history,
            semantic_version=version,
        )

    if penalty_parts:
        penalty_value = 100.0 * sum(part[1] for part in penalty_parts) / len(penalty_parts)
        configuration = {**settings.declared(), "benchmark": benchmark_config}
        add_known(
            vector,
            "crowding_penalty",
            penalty_value,
            datasets=("daily", "daily_basic"),
            fields=("close", "vol", "turnover_rate"),
            history=stock_history,
            semantic_version=version,
            formula="crowding_penalty = 100 * mean(penalty components)",
            components={name: value for name, value, _ in penalty_parts},
            config=configuration,
        )
        add_known(
            vector,
            "expectation_score",
            100.0 - penalty_value,
            datasets=("daily", "daily_basic"),
            fields=("close", "vol", "turnover_rate"),
            history=stock_history,
            semantic_version=version,
            formula="expectation_score = 100 - crowding_penalty",
            components={"crowding_penalty": penalty_value},
            config=configuration,
        )
        if penalty_value >= settings.crowding_flag_threshold:
            vector.risk_flags.append("already_repriced_or_crowded")
    else:
        reason = "no_crowding_evidence"
        for name in ("crowding_penalty", "expectation_score"):
            add_unknown(
                vector,
                name,
                datasets=("daily", "daily_basic"),
                fields=("close", "vol", "turnover_rate"),
                reason=reason,
                history=stock_history,
                semantic_version=version,
            )
    return vector