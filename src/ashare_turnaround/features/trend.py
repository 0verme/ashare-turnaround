"""Existing trend feature seam with comparable-period input protection."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION
from ..scanner.contracts import FeatureVector
from .common import (
    add_unknown,
    canonical_history,
    latest_validated_row,
    new_vector,
    single_quarter_history,
)

_TREND_REDESIGN_REASON = "trend_redesign_out_of_scope"


def _field(history: pd.DataFrame, *fields: str) -> str | None:
    for field_name in fields:
        if (
            field_name in history.columns
            and pd.to_numeric(history[field_name], errors="coerce").notna().any()
        ):
            return field_name
    return None


def _valid_values(frame: pd.DataFrame, value_column: str | None) -> pd.Series:
    if value_column is None or value_column not in frame.columns:
        return pd.Series(dtype="float64")
    status = frame.get(f"{value_column}_status", pd.Series("known", index=frame.index))
    values = pd.to_numeric(frame[value_column], errors="coerce")
    return values.where(status.astype("string").eq("known"))


def _add_trend_unknowns(
    vector: FeatureVector, fields: tuple[str, ...], history: pd.DataFrame
) -> None:
    for name in ("yoy_acceleration", "qoq_acceleration", "margin_inflection", "ttm_trend"):
        add_unknown(
            vector,
            name,
            datasets=("income",),
            fields=fields,
            history=history,
            reason=_TREND_REDESIGN_REASON,
        )


def compute_trend_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    """Keep #28 calculations out while refusing invalid adjacent periods.

    Sign-transition input is restricted to validated single quarters.  The
    persistence/acceleration/margin-inflection/TTM trend redesign remains
    explicit ``UNKNOWN`` until the separately scoped #28 work.
    """

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
                vector,
                name,
                datasets=("income",),
                fields=(),
                reason="no PIT income history",
            )
        return vector

    profit_field = _field(income, "n_income_attr_p", "n_income", "net_profit")
    revenue_field = _field(income, "revenue", "total_revenue")
    operating_field = _field(income, "operate_profit", "operating_profit")
    fields = tuple(
        field_name
        for field_name in (profit_field, revenue_field, operating_field)
        if field_name is not None
    )
    single, columns = single_quarter_history(
        income,
        "income",
        fields,
        as_of_date=as_of_date,
    )
    if single.empty or profit_field is None:
        _add_trend_unknowns(vector, fields, single)
        add_unknown(
            vector,
            "consecutive_improvement",
            datasets=("income",),
            fields=fields,
            history=single,
            reason=_TREND_REDESIGN_REASON,
        )
        vector.add(
            "sign_transition",
            None,
            status="insufficient_data",
            source_datasets=("income",),
            source_fields=fields,
            periods=(),
            availability_dates=(),
            reason="missing_value",
            contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        )
        return vector

    profit_column = columns[profit_field]
    profit_values = _valid_values(single, profit_column)
    fields = (profit_field,)
    _add_trend_unknowns(vector, fields, single)
    add_unknown(
        vector,
        "consecutive_improvement",
        datasets=("income",),
        fields=fields,
        history=single,
        reason=_TREND_REDESIGN_REASON,
    )

    clean_profit = profit_values.dropna()
    sign_transition = None
    if len(clean_profit) >= 2:
        sign_transition = bool(clean_profit.iloc[-2] <= 0 < clean_profit.iloc[-1])
    latest, latest_reason = latest_validated_row(single, value_column=profit_column)
    periods = ()
    availability = ()
    if latest is not None and pd.notna(latest.get("report_period")):
        periods = (pd.Timestamp(latest["report_period"]).strftime("%Y%m%d"),)
        available = pd.to_datetime(latest.get("actual_available_date"), errors="coerce")
        if pd.notna(available):
            availability = (pd.Timestamp(available).strftime("%Y%m%d"),)
    vector.add(
        "sign_transition",
        sign_transition,
        status="known" if sign_transition is not None else "insufficient_data",
        source_datasets=("income",),
        source_fields=fields,
        periods=periods,
        availability_dates=availability,
        reason=latest_reason if sign_transition is None else None,
        current_period=periods[0] if periods else None,
        period_semantics="SINGLE_QUARTER",
        contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
    )
    return vector
