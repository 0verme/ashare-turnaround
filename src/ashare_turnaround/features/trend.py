"""Trend, persistence, and acceleration features."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..pit.financial import derive_single_quarter
from ..scanner.contracts import FeatureVector
from .common import add_known, add_unknown, canonical_history, new_vector, period_texts


def _series(history: pd.DataFrame, *fields: str) -> tuple[pd.Series, str | None]:
    if history.empty:
        return pd.Series(dtype="float64"), None
    for field in fields:
        if field in history.columns:
            values = pd.to_numeric(history[field], errors="coerce")
            if values.notna().any():
                return values, field
    return pd.Series(dtype="float64"), None


def _acceleration(values: pd.Series) -> float | None:
    values = values.dropna()
    if len(values) < 3:
        return None
    first_change = float(values.iloc[-2] - values.iloc[-3])
    second_change = float(values.iloc[-1] - values.iloc[-2])
    return second_change - first_change


def _improvement_count(values: pd.Series) -> int | None:
    values = values.dropna()
    if len(values) < 3:
        return None
    count = 0
    for left, right in zip(values.iloc[-1:0:-1], values.iloc[-2::-1]):
        if left > right:
            count += 1
        else:
            break
    return count


def compute_trend_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    vector = new_vector(code, as_of_date)
    income = canonical_history("income", financial_frames.get("income"), code, as_of_date)
    if income.empty:
        for name in (
            "yoy_acceleration",
            "qoq_acceleration",
            "consecutive_improvement",
            "sign_transition",
            "margin_inflection",
            "ttm_trend",
        ):
            add_unknown(
                vector, name, datasets=("income",), fields=(), reason="no PIT income history"
            )
        return vector
    income = income.copy()
    income["end_date"] = income["report_period"]
    profit_values, profit_field = _series(income, "n_income_attr_p", "n_income", "net_profit")
    revenue_values, revenue_field = _series(income, "revenue", "total_revenue")
    operating_values, operating_field = _series(income, "operate_profit", "operating_profit")
    try:
        profit_quarters = derive_single_quarter(
            income.assign(**{profit_field or "net_profit": profit_values}),
            profit_field or "net_profit",
        )["single_quarter"]
    except (KeyError, ValueError):
        profit_quarters = pd.Series(dtype="float64")
    periods = period_texts(income)
    source_fields = (profit_field,) if profit_field else ("n_income_attr_p", "n_income")
    add_known(
        vector,
        "yoy_acceleration",
        _acceleration(profit_values),
        datasets=("income",),
        fields=source_fields,
        history=income,
    )
    add_known(
        vector,
        "qoq_acceleration",
        _acceleration(profit_quarters),
        datasets=("income",),
        fields=source_fields,
        history=income,
    )
    add_known(
        vector,
        "consecutive_improvement",
        _improvement_count(profit_values),
        datasets=("income",),
        fields=source_fields,
        history=income,
    )
    sign_transition = None
    clean_profit = profit_values.dropna()
    if len(clean_profit) >= 2:
        sign_transition = bool(clean_profit.iloc[-2] <= 0 < clean_profit.iloc[-1])
    vector.add(
        "sign_transition",
        sign_transition,
        status="known" if sign_transition is not None else "insufficient_data",
        source_datasets=("income",),
        source_fields=source_fields,
        periods=periods,
        reason=None if sign_transition is not None else "at least two comparable periods required",
    )
    margins = pd.Series(dtype="float64")
    if not revenue_values.empty and not operating_values.empty:
        margins = operating_values / revenue_values.replace(0, pd.NA)
    add_known(
        vector,
        "margin_inflection",
        _acceleration(margins),
        datasets=("income",),
        fields=tuple(value for value in (operating_field, revenue_field) if value),
        history=income,
    )
    if len(profit_quarters.dropna()) >= 8:
        ttm = profit_quarters.rolling(4).sum().dropna()
        ttm_trend = float(ttm.iloc[-1] - ttm.iloc[-5]) if len(ttm) >= 5 else None
    else:
        ttm_trend = None
    add_known(
        vector,
        "ttm_trend",
        ttm_trend,
        datasets=("income",),
        fields=source_fields,
        history=income,
    )
    return vector
