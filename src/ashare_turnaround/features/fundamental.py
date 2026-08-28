"""Versioned fundamental features built from comparable financial periods."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..pit.comparable import (
    COMPARABLE_PERIOD_CONTRACT_VERSION,
    POINT_IN_TIME,
    DerivedMetric,
    SourceRecord,
    growth_from_match,
    level_from_row,
    margin_from_row,
    margin_yoy_from_match,
    match_comparable_period,
    period_identity,
)
from ..scanner.contracts import FeatureVector
from .common import (
    add_metric,
    add_unknown,
    canonical_history,
    latest_validated_row,
    new_vector,
    numeric,
    safe_ratio,
    single_quarter_history,
)


def _history(
    frames: dict[str, pd.DataFrame], dataset: str, code: str, as_of_date: object
) -> pd.DataFrame:
    return canonical_history(dataset, frames.get(dataset), code, as_of_date)


def _field(history: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in history.columns and pd.to_numeric(history[name], errors="coerce").notna().any():
            return name
    return None


def _unknown(
    metric: str,
    *,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    reason: str,
) -> DerivedMetric:
    return DerivedMetric(
        metric=metric,
        value=None,
        status="unknown",
        reason=reason,
        source_datasets=datasets,
        source_fields=fields,
        contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
    )


def _with_current_provenance(
    result: DerivedMetric,
    row: pd.Series,
    *,
    dataset: str,
    value_column: str,
    fields: tuple[str, ...],
) -> DerivedMetric:
    current = level_from_row(
        row,
        dataset=dataset,
        value_column=value_column,
        metric=result.metric,
        fields=fields,
    )
    return replace(
        result,
        current_identity=result.current_identity or current.current_identity,
        current_raw_value=(
            result.current_raw_value
            if result.current_raw_value is not None
            else current.current_raw_value
        ),
        period_semantics=result.period_semantics or current.period_semantics,
        source_datasets=tuple(dict.fromkeys((*current.source_datasets, *result.source_datasets))),
        source_fields=fields,
        source_versions=tuple(dict.fromkeys((*current.source_versions, *result.source_versions))),
        availability_dates=tuple(
            dict.fromkeys((*current.availability_dates, *result.availability_dates))
        ),
        source_chain=tuple((*current.source_chain, *result.source_chain)),
    )


def _single_field_result(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    dataset: str,
    metric: str,
    fields: tuple[str, ...],
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[DerivedMetric, pd.DataFrame, str | None]:
    """Return the latest source-period value without changing its duration."""

    del as_of_date  # ``history`` has already been selected by the PIT engine.
    if field_name is None:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            pd.DataFrame(),
            None,
        )
    if history.empty or field_name not in history.columns:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            history,
            field_name,
        )
    row, row_reason = latest_validated_row(history, value_column=field_name)
    if row is None:
        return (
            _unknown(
                metric,
                datasets=(dataset,),
                fields=fields,
                reason=row_reason or "missing_current_period",
            ),
            history,
            field_name,
        )
    result = level_from_row(
        row,
        dataset=dataset,
        value_column=field_name,
        metric=metric,
        fields=fields,
    )
    if row_reason is not None:
        result = replace(result, value=None, status="unknown", reason=row_reason)
    return result, history, field_name


def _growth_result(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    dataset: str,
    metric: str,
    fields: tuple[str, ...],
    comparison: str = "yoy",
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[DerivedMetric, pd.DataFrame, str | None]:
    """Calculate growth using source-period YoY or validated-single QoQ."""

    if field_name is None:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            pd.DataFrame(),
            None,
        )
    if comparison == "qoq":
        source, columns = single_quarter_history(
            history,
            dataset,
            (field_name,),
            as_of_date=as_of_date,
        )
        value_column = columns.get(field_name)
        if value_column is not None:
            source = source.copy()
            source["comparable_value"] = source[value_column]
            source["comparable_raw_value"] = source[f"{value_column}_raw"]
            source["comparable_status"] = source[f"{value_column}_status"]
            source["comparable_reason"] = source[f"{value_column}_reason"]
    else:
        source = history
        value_column = field_name
    if source.empty or value_column is None or value_column not in source.columns:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            source,
            value_column,
        )
    row, row_reason = latest_validated_row(
        source,
        value_column=(value_column if comparison != "qoq" else value_column),
    )
    if row is None:
        return (
            _unknown(
                metric,
                datasets=(dataset,),
                fields=fields,
                reason=row_reason or "missing_current_period",
            ),
            source,
            value_column,
        )
    if row_reason is not None:
        level = level_from_row(
            row,
            dataset=dataset,
            value_column=value_column,
            metric=metric,
            fields=fields,
        )
        return replace(level, value=None, status="unknown", reason=row_reason), source, value_column
    if comparison == "qoq":
        match_value_column = "comparable_value"
    else:
        match_value_column = value_column
    match = match_comparable_period(
        source,
        row,
        comparison=comparison,
        dataset=dataset,
        value_column=match_value_column,
        as_of_date=as_of_date,
    )
    result = growth_from_match(match, metric=metric)
    if result.current_raw_value is None:
        result = _with_current_provenance(
            result,
            row,
            dataset=dataset,
            value_column=value_column,
            fields=fields,
        )
    return replace(result, source_fields=fields), source, value_column


def _margin_result(
    history: pd.DataFrame,
    numerator_field: str | None,
    denominator_field: str | None,
    *,
    dataset: str,
    metric: str,
    fields: tuple[str, ...],
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    comparison: bool = False,
) -> tuple[DerivedMetric, pd.DataFrame, dict[str, str]]:
    """Calculate a margin inside each source-comparable report period."""

    if numerator_field is None or denominator_field is None:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            pd.DataFrame(),
            {},
        )
    row, row_reason = latest_validated_row(history)
    if row is None:
        return (
            _unknown(
                metric,
                datasets=(dataset,),
                fields=fields,
                reason=row_reason or "missing_current_period",
            ),
            history,
            {},
        )
    if row_reason is not None:
        return _unknown(metric, datasets=(dataset,), fields=fields, reason=row_reason), history, {}
    if numeric(row.get(numerator_field)) is None or numeric(row.get(denominator_field)) is None:
        return (
            _unknown(metric, datasets=(dataset,), fields=fields, reason="missing_value"),
            history,
            {},
        )
    result = margin_from_row(
        row,
        dataset=dataset,
        numerator_column=numerator_field,
        denominator_column=denominator_field,
        metric=metric,
    )
    result = replace(result, source_fields=fields)
    if not comparison:
        return (
            result,
            history,
            {numerator_field: numerator_field, denominator_field: denominator_field},
        )

    match = match_comparable_period(
        history,
        row,
        comparison="yoy",
        dataset=dataset,
        value_column=None,
        as_of_date=as_of_date,
    )
    change = margin_yoy_from_match(
        match,
        dataset=dataset,
        numerator_column=numerator_field,
        denominator_column=denominator_field,
        metric=f"{metric}_yoy_change",
    )
    if change.current_raw_value is None:
        change = _with_current_provenance(
            change,
            row,
            dataset=dataset,
            value_column=numerator_field,
            fields=fields,
        )
    return (
        replace(change, source_fields=fields),
        history,
        {
            numerator_field: numerator_field,
            denominator_field: denominator_field,
        },
    )


def _same_period(left: pd.Series, right: pd.Series) -> bool:
    same_identity = all(
        str(left.get(column)) == str(right.get(column))
        for column in (
            "fiscal_year",
            "quarter",
            "report_family",
            "scope",
            "unit",
            "accounting_semantics",
        )
    )
    left_duration = str(left.get("duration_semantics"))
    right_duration = str(right.get("duration_semantics"))
    compatible_duration = left_duration == right_duration or POINT_IN_TIME in {
        left_duration,
        right_duration,
    }
    return (
        same_identity
        and compatible_duration
        and pd.Timestamp(left["report_period"]) == pd.Timestamp(right["report_period"])
    )


def _ratio_metric(
    metric: str,
    numerator: Any,
    denominator: Any,
    *,
    current: pd.Series | None,
    datasets: tuple[str, ...],
    fields: tuple[str, ...],
    source_rows: tuple[tuple[str, pd.Series], ...] = (),
) -> DerivedMetric:
    value = safe_ratio(numerator, denominator)
    reason = (
        None
        if value is not None
        else "invalid_denominator"
        if numeric(denominator) == 0
        else "missing_value"
    )
    if current is None:
        reason = reason or "missing_current_period"
    availability_values: list[str] = []
    version_values: list[str] = []
    source_chain: list[SourceRecord] = []
    semantics = None
    current_identity = None
    provenance: dict[str, Any] = {}
    if current is not None:
        primary_dataset = "income" if "income" in datasets else datasets[0]
        current_identity = period_identity(current.to_dict(), dataset=primary_dataset)
        semantics = current_identity.duration_semantics
        source_items = ((primary_dataset, current), *source_rows)
        identities: dict[str, dict[str, Any]] = {}
        for source_dataset, source_row in source_items:
            source_identity = period_identity(source_row.to_dict(), dataset=source_dataset)
            if source_identity.source_version is not None:
                version_values.append(source_identity.source_version)
            if source_identity.availability_date is not None:
                availability_values.append(source_identity.availability_date)
            source_chain.append(
                SourceRecord(
                    period=source_identity.report_period,
                    availability_date=source_identity.availability_date,
                    source_version=source_identity.source_version,
                    value=numeric(source_row.get(fields[0])) if fields else None,
                )
            )
            identities[source_dataset] = source_identity.as_dict()
        provenance["source_period_identities"] = identities
    availability = tuple(dict.fromkeys(availability_values))
    versions = tuple(dict.fromkeys(version_values))
    return DerivedMetric(
        metric=metric,
        value=value if reason is None else None,
        status="known" if reason is None else "unknown",
        reason=reason,
        current_identity=current_identity,
        current_raw_value=numeric(numerator),
        comparison_raw_value=numeric(denominator),
        period_semantics=semantics,
        source_datasets=datasets,
        source_fields=fields,
        source_versions=versions,
        availability_dates=availability,
        source_chain=tuple(source_chain),
        contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        provenance=provenance,
    )


def _point_growth_result(
    history: pd.DataFrame,
    field_name: str | None,
    *,
    metric: str,
    fields: tuple[str, ...],
    as_of_date: str | date | datetime | pd.Timestamp | None,
) -> DerivedMetric:
    if field_name is None or history.empty:
        return _unknown(metric, datasets=("balancesheet",), fields=fields, reason="missing_value")
    row, row_reason = latest_validated_row(history, value_column=field_name)
    if row is None:
        return _unknown(
            metric,
            datasets=("balancesheet",),
            fields=fields,
            reason=row_reason or "missing_current_period",
        )
    if row_reason is not None:
        return _unknown(metric, datasets=("balancesheet",), fields=fields, reason=row_reason)
    match = match_comparable_period(
        history,
        row,
        comparison="yoy",
        dataset="balancesheet",
        value_column=field_name,
        as_of_date=as_of_date,
    )
    result = growth_from_match(match, metric=metric)
    if result.current_raw_value is None:
        result = _with_current_provenance(
            result,
            row,
            dataset="balancesheet",
            value_column=field_name,
            fields=fields,
        )
    return replace(result, source_fields=fields)


def compute_fundamental_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    """Compute fundamental values only from validated comparable periods."""

    vector = new_vector(code, as_of_date)
    income = _history(financial_frames, "income", code, as_of_date)
    balance = _history(financial_frames, "balancesheet", code, as_of_date)
    cashflow = _history(financial_frames, "cashflow", code, as_of_date)

    revenue_field = _field(income, "revenue", "total_revenue")
    profit_field = _field(income, "n_income_attr_p", "n_income", "net_profit")
    operating_field = _field(income, "operate_profit", "operating_profit")
    gross_field = _field(income, "gross_profit")
    cfo_field = _field(cashflow, "n_cashflow_act", "c_fr_operate_a", "operating_cash_flow")
    assets_field = _field(balance, "total_assets")
    equity_field = _field(
        balance,
        "total_hldr_eqy_inc_min_int",
        "total_hldr_eqy_exc_min_int",
        "total_hldr_eqy",
    )
    inventory_field = _field(balance, "inventories", "inventory")
    receivables_field = _field(balance, "accounts_receiv", "acct_receivable", "acc_receivable")

    revenue_level, revenue_single, revenue_column = _single_field_result(
        income,
        revenue_field,
        dataset="income",
        metric="revenue_level",
        fields=(revenue_field,) if revenue_field else ("revenue", "total_revenue"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "revenue_level",
        revenue_level,
        datasets=("income",),
        fields=(),
        history=revenue_single,
    )
    revenue_yoy, _, _ = _growth_result(
        income,
        revenue_field,
        dataset="income",
        metric="revenue_yoy",
        fields=(revenue_field,) if revenue_field else ("revenue", "total_revenue"),
        as_of_date=as_of_date,
    )
    add_metric(vector, "revenue_yoy", revenue_yoy, datasets=("income",), fields=(), history=income)

    profit_level, profit_single, profit_column = _single_field_result(
        income,
        profit_field,
        dataset="income",
        metric="net_profit_level",
        fields=(profit_field,) if profit_field else ("n_income_attr_p", "n_income", "net_profit"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "net_profit_level",
        profit_level,
        datasets=("income",),
        fields=(),
        history=profit_single,
    )
    profit_yoy, _, _ = _growth_result(
        income,
        profit_field,
        dataset="income",
        metric="net_profit_yoy",
        fields=(profit_field,) if profit_field else ("n_income_attr_p", "n_income", "net_profit"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector, "net_profit_yoy", profit_yoy, datasets=("income",), fields=(), history=income
    )

    operating_yoy, operating_single, operating_column = _growth_result(
        income,
        operating_field,
        dataset="income",
        metric="operating_profit_yoy",
        fields=(operating_field,) if operating_field else ("operate_profit", "operating_profit"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "operating_profit_yoy",
        operating_yoy,
        datasets=("income",),
        fields=(),
        history=operating_single,
    )

    revenue_for_margin = revenue_field
    gross_margin, _, _ = _margin_result(
        income,
        gross_field,
        revenue_for_margin,
        dataset="income",
        metric="gross_margin",
        fields=((gross_field,) if gross_field else ("gross_profit",))
        + ((revenue_field,) if revenue_field else ("revenue", "total_revenue")),
        as_of_date=as_of_date,
    )
    add_metric(
        vector, "gross_margin", gross_margin, datasets=("income",), fields=(), history=income
    )
    gross_margin_change = replace(
        gross_margin,
        metric="gross_margin_yoy_change",
        value=None,
        status="unknown",
        reason="missing_comparable_period",
    )
    if gross_field and revenue_field:
        gross_margin_change, _, _ = _margin_result(
            income,
            gross_field,
            revenue_for_margin,
            dataset="income",
            metric="gross_margin",
            fields=(gross_field, revenue_field),
            as_of_date=as_of_date,
            comparison=True,
        )
    add_metric(
        vector,
        "gross_margin_yoy_change",
        gross_margin_change,
        datasets=("income",),
        fields=(),
        history=income,
    )

    operating_margin, _, _ = _margin_result(
        income,
        operating_field,
        revenue_for_margin,
        dataset="income",
        metric="operating_margin",
        fields=((operating_field,) if operating_field else ("operate_profit", "operating_profit"))
        + ((revenue_field,) if revenue_field else ("revenue", "total_revenue")),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "operating_margin",
        operating_margin,
        datasets=("income",),
        fields=(),
        history=income,
    )
    operating_margin_change = _margin_result(
        income,
        operating_field,
        revenue_for_margin,
        dataset="income",
        metric="operating_margin",
        fields=(operating_field, revenue_field) if operating_field and revenue_field else (),
        as_of_date=as_of_date,
        comparison=True,
    )[0]
    operating_margin_change = replace(operating_margin_change, metric="operating_margin_yoy_change")
    add_metric(
        vector,
        "operating_margin_yoy_change",
        operating_margin_change,
        datasets=("income",),
        fields=(),
        history=income,
    )

    net_margin, _, _ = _margin_result(
        income,
        profit_field,
        revenue_for_margin,
        dataset="income",
        metric="net_margin",
        fields=((profit_field,) if profit_field else ("n_income_attr_p", "n_income"))
        + ((revenue_field,) if revenue_field else ("revenue", "total_revenue")),
        as_of_date=as_of_date,
    )
    add_metric(vector, "net_margin", net_margin, datasets=("income",), fields=(), history=income)
    net_margin_change = _margin_result(
        income,
        profit_field,
        revenue_for_margin,
        dataset="income",
        metric="net_margin",
        fields=(profit_field, revenue_field) if profit_field and revenue_field else (),
        as_of_date=as_of_date,
        comparison=True,
    )[0]
    net_margin_change = replace(net_margin_change, metric="net_margin_yoy_change")
    add_metric(
        vector,
        "net_margin_yoy_change",
        net_margin_change,
        datasets=("income",),
        fields=(),
        history=income,
    )

    cfo_level, cfo_single, cfo_column = _single_field_result(
        cashflow,
        cfo_field,
        dataset="cashflow",
        metric="operating_cash_flow",
        fields=(cfo_field,) if cfo_field else ("n_cashflow_act", "c_fr_operate_a"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "operating_cash_flow",
        cfo_level,
        datasets=("cashflow",),
        fields=(),
        history=cfo_single,
    )
    cfo_change, _, _ = _growth_result(
        cashflow,
        cfo_field,
        dataset="cashflow",
        metric="operating_cash_flow_change",
        fields=(cfo_field,) if cfo_field else ("n_cashflow_act",),
        comparison="qoq",
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "operating_cash_flow_change",
        cfo_change,
        datasets=("cashflow",),
        fields=(),
        history=cashflow,
    )

    latest_income, _ = latest_validated_row(income) if not income.empty else (None, None)
    latest_cashflow, _ = latest_validated_row(cashflow) if not cashflow.empty else (None, None)
    if (
        latest_income is not None
        and latest_cashflow is not None
        and _same_period(latest_income, latest_cashflow)
    ):
        cfo_value = numeric(latest_cashflow.get(cfo_column)) if cfo_column else None
        profit_value = numeric(latest_income.get(profit_column)) if profit_column else None
        cfo_profit = _ratio_metric(
            "cfo_to_profit",
            cfo_value,
            profit_value,
            current=latest_income,
            datasets=("cashflow", "income"),
            fields=tuple(field for field in (cfo_field, profit_field) if field),
            source_rows=(("cashflow", latest_cashflow),),
        )
    else:
        cfo_profit = _unknown(
            "cfo_to_profit",
            datasets=("cashflow", "income"),
            fields=tuple(field for field in (cfo_field, profit_field) if field),
            reason="period_alignment_mismatch",
        )
    add_metric(
        vector,
        "cfo_to_profit",
        cfo_profit,
        datasets=("cashflow", "income"),
        fields=(),
        history=cashflow,
    )

    balance_latest, balance_reason = (
        latest_validated_row(balance, value_column=assets_field)
        if not balance.empty
        else (None, "missing_current_period")
    )
    if (
        latest_income is not None
        and balance_latest is not None
        and _same_period(latest_income, balance_latest)
    ):
        net_profit_value = numeric(latest_income.get(profit_column)) if profit_column else None
        assets_value = numeric(balance_latest.get(assets_field)) if assets_field else None
        equity_value = numeric(balance_latest.get(equity_field)) if equity_field else None
        roe = _ratio_metric(
            "roe",
            net_profit_value,
            equity_value,
            current=latest_income,
            datasets=("income", "balancesheet"),
            fields=tuple(field for field in (profit_field, equity_field) if field),
            source_rows=(("balancesheet", balance_latest),),
        )
        roa = _ratio_metric(
            "roa",
            net_profit_value,
            assets_value,
            current=latest_income,
            datasets=("income", "balancesheet"),
            fields=tuple(field for field in (profit_field, assets_field) if field),
            source_rows=(("balancesheet", balance_latest),),
        )
        asset_turnover = _ratio_metric(
            "asset_turnover",
            numeric(latest_income.get(revenue_column)) if revenue_column else None,
            assets_value,
            current=latest_income,
            datasets=("income", "balancesheet"),
            fields=tuple(field for field in (revenue_field, assets_field) if field),
            source_rows=(("balancesheet", balance_latest),),
        )
    else:
        alignment_reason = balance_reason or "period_alignment_mismatch"
        roe = _unknown(
            "roe", datasets=("income", "balancesheet"), fields=(), reason=alignment_reason
        )
        roa = _unknown(
            "roa", datasets=("income", "balancesheet"), fields=(), reason=alignment_reason
        )
        asset_turnover = _unknown(
            "asset_turnover",
            datasets=("income", "balancesheet"),
            fields=(),
            reason=alignment_reason,
        )
    add_metric(vector, "roe", roe, datasets=("income", "balancesheet"), fields=(), history=balance)
    add_metric(vector, "roa", roa, datasets=("income", "balancesheet"), fields=(), history=balance)

    inventory_yoy = _point_growth_result(
        balance,
        inventory_field,
        metric="inventory_yoy",
        fields=(inventory_field,) if inventory_field else ("inventories", "inventory"),
        as_of_date=as_of_date,
    )
    receivables_yoy = _point_growth_result(
        balance,
        receivables_field,
        metric="receivables_yoy",
        fields=(receivables_field,)
        if receivables_field
        else ("accounts_receiv", "acct_receivable"),
        as_of_date=as_of_date,
    )
    add_metric(
        vector,
        "inventory_yoy",
        inventory_yoy,
        datasets=("balancesheet",),
        fields=(),
        history=balance,
    )
    add_metric(
        vector,
        "receivables_yoy",
        receivables_yoy,
        datasets=("balancesheet",),
        fields=(),
        history=balance,
    )
    add_metric(
        vector,
        "asset_turnover",
        asset_turnover,
        datasets=("income", "balancesheet"),
        fields=(),
        history=balance,
    )

    if income.empty:
        add_unknown(
            vector,
            "fundamental_data_status",
            datasets=("income",),
            fields=(),
            reason="no PIT income history",
        )
    else:
        vector.add(
            "fundamental_data_status",
            "known",
            status="known",
            source_datasets=("income",),
            source_fields=(),
            periods=tuple(
                value.strftime("%Y%m%d") for value in income["report_period"] if pd.notna(value)
            ),
            availability_dates=tuple(
                value.strftime("%Y%m%d")
                for value in pd.to_datetime(
                    income["actual_available_date"], errors="coerce"
                ).dropna()
            ),
            contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        )
    return vector
