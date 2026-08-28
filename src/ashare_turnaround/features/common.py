"""Private helpers shared by the independent feature groups."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..pit.comparable import (
    COMPARABLE_PERIOD_CONTRACT_VERSION,
    DerivedMetric,
    validated_single_quarter_series,
)
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
    """Return a finite numeric value, rejecting NaN and infinities."""

    if value is None:
        return None
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return None
    result = float(parsed)
    return result if math.isfinite(result) else None


def safe_change(current: Any, previous: Any) -> float | None:
    """Return ordinary growth only for a strictly positive denominator."""

    current_value, previous_value = numeric(current), numeric(previous)
    if current_value is None or previous_value is None or previous_value <= 0 or current_value < 0:
        return None
    result = (current_value - previous_value) / previous_value
    return result if math.isfinite(result) else None


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    numerator_value, denominator_value = numeric(numerator), numeric(denominator)
    if numerator_value is None or denominator_value in {None, 0.0}:
        return None
    result = numerator_value / denominator_value
    return result if math.isfinite(result) else None


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
    canonical = canonicalize_financial_frame(
        dataset,
        frame,
        disclosure_frame=disclosure_frame,
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


def market_history(
    frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    lookback: int = 252,
) -> pd.DataFrame:
    """PIT market rows for one security (or index) up to and including as-of.

    Rows are restricted to ``trade_date <= as_of`` and, when the frame carries
    ``actual_available_date``, to rows available on or before as-of.  The
    returned frame is sorted by ``_date`` with at most ``lookback`` rows.
    """

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
    result = result.loc[result["_date"].notna()].copy()
    if result.empty:
        return pd.DataFrame()
    # A valid source partition has one row per security/session.  If a
    # synthetic or partially repaired input violates that invariant, choose a
    # deterministic row rather than letting input order decide the endpoint.
    key_columns = sorted(column for column in result.columns if column != "_row_key")
    result["_row_key"] = (
        result[key_columns].astype("string").fillna("<NA>").agg("\x1f".join, axis=1)
    )
    result = (
        result.sort_values(["_date", "_row_key"], kind="mergesort")
        .drop_duplicates("_date", keep="last")
        .drop(columns="_row_key")
    )
    return result.tail(lookback).reset_index(drop=True)


def add_known(
    vector: FeatureVector,
    name: str,
    value: Any,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    history: pd.DataFrame | None = None,
    reason: str | None = None,
    current_period: str | None = None,
    comparison_period: str | None = None,
    current_raw_value: Any = None,
    comparison_raw_value: Any = None,
    period_semantics: str | None = None,
    source_versions: tuple[str, ...] = (),
    provenance: dict[str, Any] | None = None,
    status: str | None = None,
    semantic_version: str = "features-v1",
    formula: str | None = None,
    components: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    parsed = numeric(value)
    evidence_components = dict(components or {})
    evidence_config = dict(config or {})
    if semantic_version.startswith("expectation-crowding"):
        evidence_components.setdefault("as_of", vector.as_of_date)
        evidence_config.setdefault("as_of", vector.as_of_date)
    vector.add(
        name,
        parsed,
        status=(status or "known") if parsed is not None else (status or "unknown"),
        source_datasets=datasets,
        source_fields=fields,
        periods=period_texts(history if history is not None else pd.DataFrame()),
        availability_dates=availability_texts(history if history is not None else pd.DataFrame()),
        reason=(reason or "invalid_numeric_value") if parsed is None else None,
        current_period=current_period,
        comparison_period=comparison_period,
        current_raw_value=current_raw_value,
        comparison_raw_value=comparison_raw_value,
        period_semantics=period_semantics,
        source_versions=source_versions,
        contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        provenance=provenance or {},
        semantic_version=semantic_version,
        formula=formula,
        components=evidence_components,
        config=evidence_config,
    )


def add_metric(
    vector: FeatureVector,
    name: str,
    result: DerivedMetric,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    history: pd.DataFrame | None = None,
) -> None:
    """Add a semantic result while retaining period/version provenance."""

    periods = tuple(
        value for value in (result.current_period, result.comparison_period) if value is not None
    )
    if not periods:
        periods = tuple(
            record.period for record in result.source_chain if record.period is not None
        )
    provenance = dict(result.provenance)
    provenance.setdefault("metric", result.metric)
    provenance.setdefault("contract_version", result.contract_version)
    provenance.setdefault("source_chain", [record.as_dict() for record in result.source_chain])
    vector.add(
        name,
        result.value,
        status=result.status,
        source_datasets=result.source_datasets or datasets,
        source_fields=result.source_fields or fields,
        periods=tuple(dict.fromkeys(periods)),
        availability_dates=result.availability_dates
        or availability_texts(history if history is not None else pd.DataFrame()),
        reason=result.reason,
        current_period=result.current_period,
        comparison_period=result.comparison_period,
        current_availability_date=result.current_availability_date,
        comparison_availability_date=result.comparison_availability_date,
        current_raw_value=result.current_raw_value,
        comparison_raw_value=result.comparison_raw_value,
        period_semantics=result.period_semantics,
        source_versions=result.source_versions,
        contract_version=result.contract_version,
        provenance=provenance,
    )


def single_quarter_history(
    history: pd.DataFrame,
    dataset: str,
    fields: tuple[str, ...],
    *,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Build one aligned validated single-quarter frame for several fields."""

    available = tuple(field_name for field_name in fields if field_name in history.columns)
    if history.empty or not available:
        return pd.DataFrame(), {}
    result: pd.DataFrame | None = None
    field_columns: dict[str, str] = {}
    key = "source_version_identity"
    for field_name in available:
        quarterized = validated_single_quarter_series(
            history,
            field_name,
            dataset_kind=dataset,
            as_of_date=as_of_date,
        )
        value_name = f"__comparable_{field_name}"
        field_columns[field_name] = value_name
        extra = quarterized[
            [
                key,
                "comparable_value",
                "comparable_status",
                "comparable_reason",
                field_name,
            ]
        ].copy()
        extra = extra.rename(
            columns={
                "comparable_value": value_name,
                "comparable_status": f"{value_name}_status",
                "comparable_reason": f"{value_name}_reason",
                field_name: f"{value_name}_raw",
            }
        )
        if result is None:
            result = quarterized.copy()
            result = result.drop(
                columns=["comparable_value", "comparable_status", "comparable_reason"],
                errors="ignore",
            ).merge(extra, on=key, how="left", validate="one_to_one")
        else:
            result = result.merge(extra, on=key, how="outer", validate="one_to_one")
    if result is None:
        return pd.DataFrame(), {}
    result = result.sort_values(
        ["report_period", "quarter", key], kind="stable", na_position="last"
    ).reset_index(drop=True)
    first_value = field_columns[available[0]]
    result["comparable_value"] = result[first_value]
    result["comparable_raw_value"] = result[f"{first_value}_raw"]
    result["comparable_status"] = result[f"{first_value}_status"]
    result["comparable_reason"] = result[f"{first_value}_reason"]
    return result, field_columns


def latest_validated_row(
    history: pd.DataFrame,
    *,
    value_column: str | None = None,
) -> tuple[pd.Series | None, str | None]:
    """Return the latest economic period, refusing same-period ambiguity."""

    if history.empty or "report_period" not in history.columns:
        return None, "missing_current_period"
    dates = normalize_date_series(history["report_period"])
    dated = history.loc[dates.notna()].copy()
    if dated.empty:
        return None, "unsupported_report_period"
    latest_date = dates.loc[dated.index].max()
    candidates = dated.loc[dates.loc[dated.index].eq(latest_date)]
    identity_columns = [
        column
        for column in (
            "fiscal_year",
            "quarter",
            "duration_semantics",
            "report_family",
            "statement_type",
            "scope",
            "unit",
            "accounting_semantics",
        )
        if column in candidates.columns
    ]
    if len(candidates.drop_duplicates(identity_columns)) > 1:
        return None, "ambiguous_period_chain"
    row = candidates.iloc[-1]
    if value_column is not None:
        value = numeric(row.get(value_column))
        status = str(row.get(f"{value_column}_status", row.get("comparable_status", "known")))
        if value is None or status != "known":
            return row, str(
                row.get(f"{value_column}_reason") or row.get("comparable_reason") or "missing_value"
            )
    return row, None


def add_unknown(
    vector: FeatureVector,
    name: str,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    reason: str,
    history: pd.DataFrame | None = None,
    semantic_version: str = "features-v1",
    formula: str | None = None,
    components: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    status: str = "unknown",
) -> None:
    add_known(
        vector,
        name,
        None,
        datasets=datasets,
        fields=fields,
        history=history,
        reason=reason,
        semantic_version=semantic_version,
        formula=formula,
        components=components,
        config=config,
        status=status,
    )
