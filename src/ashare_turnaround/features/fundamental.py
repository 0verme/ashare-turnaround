"""Versioned v1 fundamental turnaround feature registry."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..scanner.contracts import FeatureVector
from .common import (
    add_known,
    add_unknown,
    canonical_history,
    first_value,
    latest_and_previous,
    new_vector,
    safe_change,
    safe_ratio,
)


def _history(
    frames: dict[str, pd.DataFrame], dataset: str, code: str, as_of_date: object
) -> pd.DataFrame:
    return canonical_history(dataset, frames.get(dataset), code, as_of_date)


def compute_fundamental_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    """Compute a compact PIT-safe feature vector from financial history."""

    vector = new_vector(code, as_of_date)
    income = _history(financial_frames, "income", code, as_of_date)
    balance = _history(financial_frames, "balancesheet", code, as_of_date)
    cashflow = _history(financial_frames, "cashflow", code, as_of_date)
    latest_income, previous_income = latest_and_previous(income)
    latest_balance, previous_balance = latest_and_previous(balance)
    latest_cashflow, previous_cashflow = latest_and_previous(cashflow)

    revenue, revenue_field = first_value(latest_income, "revenue", "total_revenue")
    previous_revenue, _ = first_value(previous_income, "revenue", "total_revenue")
    net_profit, profit_field = first_value(
        latest_income, "n_income_attr_p", "n_income", "net_profit"
    )
    previous_profit, _ = first_value(previous_income, "n_income_attr_p", "n_income", "net_profit")
    operating_profit, operating_field = first_value(
        latest_income, "operate_profit", "operating_profit"
    )
    previous_operating_profit, _ = first_value(
        previous_income, "operate_profit", "operating_profit"
    )
    gross_profit, gross_field = first_value(latest_income, "gross_profit")
    cfo, cfo_field = first_value(
        latest_cashflow, "n_cashflow_act", "c_fr_operate_a", "operating_cash_flow"
    )
    previous_cfo, _ = first_value(
        previous_cashflow, "n_cashflow_act", "c_fr_operate_a", "operating_cash_flow"
    )
    assets, assets_field = first_value(latest_balance, "total_assets")
    equity, equity_field = first_value(
        latest_balance,
        "total_hldr_eqy_inc_min_int",
        "total_hldr_eqy_exc_min_int",
        "total_hldr_eqy",
    )
    inventory, inventory_field = first_value(latest_balance, "inventories", "inventory")
    previous_inventory, _ = first_value(previous_balance, "inventories", "inventory")
    receivables, receivables_field = first_value(
        latest_balance,
        "accounts_receiv",
        "acct_receivable",
        "acc_receivable",
    )
    previous_receivables, _ = first_value(
        previous_balance,
        "accounts_receiv",
        "acct_receivable",
        "acc_receivable",
    )

    fields = (revenue_field,) if revenue_field else ("revenue", "total_revenue")
    add_known(vector, "revenue_level", revenue, datasets=("income",), fields=fields, history=income)
    add_known(
        vector,
        "revenue_yoy",
        safe_change(revenue, previous_revenue),
        datasets=("income",),
        fields=fields,
        history=income,
    )
    profit_fields = (profit_field,) if profit_field else ("n_income_attr_p", "n_income")
    add_known(
        vector,
        "net_profit_level",
        net_profit,
        datasets=("income",),
        fields=profit_fields,
        history=income,
    )
    add_known(
        vector,
        "net_profit_yoy",
        safe_change(net_profit, previous_profit),
        datasets=("income",),
        fields=profit_fields,
        history=income,
    )
    operating_fields = (
        (operating_field,) if operating_field else ("operate_profit", "operating_profit")
    )
    add_known(
        vector,
        "operating_profit_yoy",
        safe_change(operating_profit, previous_operating_profit),
        datasets=("income",),
        fields=operating_fields,
        history=income,
    )
    add_known(
        vector,
        "gross_margin",
        safe_ratio(gross_profit, revenue),
        datasets=("income",),
        fields=((gross_field,) if gross_field else ("gross_profit",)) + fields,
        history=income,
    )
    add_known(
        vector,
        "operating_margin",
        safe_ratio(operating_profit, revenue),
        datasets=("income",),
        fields=operating_fields + fields,
        history=income,
    )
    add_known(
        vector,
        "net_margin",
        safe_ratio(net_profit, revenue),
        datasets=("income",),
        fields=profit_fields + fields,
        history=income,
    )
    add_known(
        vector,
        "operating_cash_flow",
        cfo,
        datasets=("cashflow",),
        fields=((cfo_field,) if cfo_field else ("n_cashflow_act", "c_fr_operate_a")),
        history=cashflow,
    )
    add_known(
        vector,
        "operating_cash_flow_change",
        safe_change(cfo, previous_cfo),
        datasets=("cashflow",),
        fields=((cfo_field,) if cfo_field else ("n_cashflow_act",)),
        history=cashflow,
    )
    add_known(
        vector,
        "cfo_to_profit",
        safe_ratio(cfo, net_profit),
        datasets=("cashflow", "income"),
        fields=((cfo_field,) if cfo_field else ("n_cashflow_act",)) + profit_fields,
        history=cashflow,
    )
    add_known(
        vector,
        "roe",
        safe_ratio(net_profit, equity),
        datasets=("income", "balancesheet"),
        fields=profit_fields + ((equity_field,) if equity_field else ("total_hldr_eqy",)),
        history=balance,
    )
    add_known(
        vector,
        "roa",
        safe_ratio(net_profit, assets),
        datasets=("income", "balancesheet"),
        fields=profit_fields + ((assets_field,) if assets_field else ("total_assets",)),
        history=balance,
    )
    add_known(
        vector,
        "inventory_yoy",
        safe_change(inventory, previous_inventory),
        datasets=("balancesheet",),
        fields=((inventory_field,) if inventory_field else ("inventories", "inventory")),
        history=balance,
    )
    add_known(
        vector,
        "receivables_yoy",
        safe_change(receivables, previous_receivables),
        datasets=("balancesheet",),
        fields=(
            (receivables_field,) if receivables_field else ("accounts_receiv", "acct_receivable")
        ),
        history=balance,
    )
    add_known(
        vector,
        "asset_turnover",
        safe_ratio(revenue, assets),
        datasets=("income", "balancesheet"),
        fields=fields + ((assets_field,) if assets_field else ("total_assets",)),
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
    return vector
