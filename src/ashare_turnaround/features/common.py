"""Private helpers shared by the independent feature groups."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..pit.financial import canonicalize_financial_frame, select_financial_as_of
from ..scanner.contracts import FeatureVector


def as_of(value: str | date | datetime | pd.Timestamp) -> tuple[str, pd.Timestamp]:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid feature as_of_date: {value!r}")
    timestamp = pd.Timestamp(parsed).normalize()
    return timestamp.strftime("%Y%m%d"), timestamp


def new_vector(code: str, as_of_date: str | date | datetime | pd.Timestamp) -> FeatureVector:
    text, _ = as_of(as_of_date)
    return FeatureVector(ts_code=str(code), as_of_date=text)


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(parsed) else float(parsed)


def safe_change(current: Any, previous: Any) -> float | None:
    current_value, previous_value = numeric(current), numeric(previous)
    if current_value is None or previous_value is None or previous_value == 0:
        return None
    return (current_value - previous_value) / abs(previous_value)


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    numerator_value, denominator_value = numeric(numerator), numeric(denominator)
    if numerator_value is None or denominator_value in {None, 0.0}:
        return None
    return numerator_value / denominator_value


def first_value(row: pd.Series | None, *columns: str) -> tuple[float | None, str | None]:
    if row is None:
        return None, None
    for column in columns:
        if column in row.index:
            value = numeric(row[column])
            if value is not None:
                return value, column
    return None, None


def canonical_history(
    dataset: str,
    frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    disclosure_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    canonical = (
        frame.copy()
        if {"report_period", "actual_available_date"}.issubset(frame.columns)
        else canonicalize_financial_frame(dataset, frame, disclosure_frame=disclosure_frame)
    )
    selected = select_financial_as_of(
        canonical,
        ts_code=str(code),
        as_of_date=as_of_date,
    )
    if selected.empty or "report_period" not in selected.columns:
        return selected
    selected = selected.copy()
    selected["report_period"] = normalize_date_series(selected["report_period"])
    return (
        selected.loc[selected["report_period"].notna()]
        .sort_values("report_period")
        .reset_index(drop=True)
    )


def latest_and_previous(history: pd.DataFrame) -> tuple[pd.Series | None, pd.Series | None]:
    if history.empty:
        return None, None
    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) >= 2 else None
    return latest, previous


def period_texts(history: pd.DataFrame) -> tuple[str, ...]:
    if history.empty or "report_period" not in history.columns:
        return ()
    return tuple(value.strftime("%Y%m%d") for value in history["report_period"] if pd.notna(value))


def availability_texts(history: pd.DataFrame) -> tuple[str, ...]:
    if history.empty or "actual_available_date" not in history.columns:
        return ()
    values = pd.to_datetime(history["actual_available_date"], errors="coerce").dropna()
    return tuple(value.strftime("%Y%m%d") for value in values)


def add_known(
    vector: FeatureVector,
    name: str,
    value: Any,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    history: pd.DataFrame | None = None,
    reason: str | None = None,
) -> None:
    parsed = numeric(value)
    vector.add(
        name,
        parsed,
        status="known" if parsed is not None else "unknown",
        source_datasets=datasets,
        source_fields=fields,
        periods=period_texts(history if history is not None else pd.DataFrame()),
        availability_dates=availability_texts(history if history is not None else pd.DataFrame()),
        reason=reason if parsed is None else None,
    )


def add_unknown(
    vector: FeatureVector,
    name: str,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    reason: str,
    history: pd.DataFrame | None = None,
) -> None:
    add_known(
        vector,
        name,
        None,
        datasets=datasets,
        fields=fields,
        history=history,
        reason=reason,
    )
