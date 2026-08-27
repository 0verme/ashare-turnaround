"""PIT-safe low-attention and low-expectation/crowding proxies."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..scanner.contracts import FeatureVector
from .common import add_known, add_unknown, new_vector, numeric


def _market_history(
    frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    lookback: int = 252,
) -> pd.DataFrame:
    if (
        frame is None
        or frame.empty
        or "ts_code" not in frame.columns
        or "trade_date" not in frame.columns
    ):
        return pd.DataFrame()
    as_of = pd.Timestamp(pd.to_datetime(as_of_date, errors="raise")).normalize()
    result = frame.loc[frame["ts_code"].astype("string").eq(str(code))].copy()
    dates = normalize_date_series(result["trade_date"])
    result = result.loc[dates.notna() & dates.le(as_of)].copy()
    if "actual_available_date" in result.columns:
        available = normalize_date_series(result["actual_available_date"])
        result = result.loc[available.isna() | available.le(as_of)].copy()
    result["_date"] = normalize_date_series(result["trade_date"])
    return result.sort_values("_date").tail(lookback).reset_index(drop=True)


def _percentile(values: pd.Series, current: Any) -> float | None:
    current_value = numeric(current)
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if current_value is None or clean.empty:
        return None
    return float((clean <= current_value).mean())


def _return(values: pd.Series, periods: int) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) <= periods:
        return None
    previous = numeric(clean.iloc[-periods - 1])
    current = numeric(clean.iloc[-1])
    if previous in {None, 0.0} or current is None:
        return None
    return (current - previous) / abs(previous)


def compute_attention_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
) -> FeatureVector:
    vector = new_vector(code, as_of_date)
    history = _market_history(market_frame, code, as_of_date, lookback)
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


def compute_crowding_features(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    lookback: int = 252,
) -> FeatureVector:
    vector = new_vector(code, as_of_date)
    history = _market_history(market_frame, code, as_of_date, lookback)
    if history.empty:
        for name in (
            "recent_excess_return",
            "distance_52w_high",
            "momentum_60d",
            "volume_spike",
            "crowding_penalty",
            "expectation_score",
        ):
            add_unknown(
                vector,
                name,
                datasets=("daily", "daily_basic"),
                fields=(),
                reason="no market history",
            )
        return vector
    close = history.get("close", pd.Series(dtype="float64"))
    close_clean = pd.to_numeric(close, errors="coerce").dropna()
    current_close = numeric(close_clean.iloc[-1]) if not close_clean.empty else None
    high = numeric(close_clean.max()) if not close_clean.empty else None
    distance = (
        1.0 - current_close / high
        if current_close is not None and high not in {None, 0.0}
        else None
    )
    momentum = _return(close, 60)
    recent = _return(close, 20)
    volume = pd.to_numeric(history.get("vol", pd.Series(dtype="float64")), errors="coerce").dropna()
    volume_spike = None
    if len(volume) >= 21 and volume.iloc[-21:-1].median() not in {0, None}:
        volume_spike = float(volume.iloc[-1] / volume.iloc[-21:-1].median())
    add_known(
        vector,
        "recent_excess_return",
        recent,
        datasets=("daily",),
        fields=("close", "pct_chg"),
        history=history,
    )
    add_known(
        vector,
        "distance_52w_high",
        distance,
        datasets=("daily",),
        fields=("close",),
        history=history,
    )
    add_known(
        vector, "momentum_60d", momentum, datasets=("daily",), fields=("close",), history=history
    )
    add_known(
        vector, "volume_spike", volume_spike, datasets=("daily",), fields=("vol",), history=history
    )
    penalty_parts: list[float] = []
    if recent is not None:
        penalty_parts.append(min(max(recent, 0.0) / 1.0, 1.0))
    if distance is not None:
        penalty_parts.append(min(max(1.0 - distance, 0.0), 1.0))
    if volume_spike is not None:
        penalty_parts.append(min(max(volume_spike - 1.0, 0.0) / 2.0, 1.0))
    penalty = 100.0 * sum(penalty_parts) / len(penalty_parts) if penalty_parts else None
    add_known(
        vector,
        "crowding_penalty",
        penalty,
        datasets=("daily", "daily_basic"),
        fields=("close", "vol", "pct_chg"),
        history=history,
    )
    add_known(
        vector,
        "expectation_score",
        100.0 - penalty if penalty is not None else None,
        datasets=("daily", "daily_basic"),
        fields=("close", "vol", "pct_chg"),
        history=history,
        reason="insufficient crowding proxies" if penalty is None else None,
    )
    if penalty is not None and penalty >= 70:
        vector.risk_flags.append("already_repriced_or_crowded")
    return vector
