"""Quality gate for obvious false or low-quality fundamental turnarounds."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..scanner.contracts import FeatureVector
from .common import (
    add_known,
    canonical_history,
    first_value,
    latest_and_previous,
    new_vector,
    safe_change,
    safe_ratio,
)


def compute_quality_features(
    financial_frames: dict[str, pd.DataFrame],
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> FeatureVector:
    """Return hard rejects, soft penalties, evidence, and a quality score."""

    vector = new_vector(code, as_of_date)
    income = canonical_history("income", financial_frames.get("income"), code, as_of_date)
    balance = canonical_history(
        "balancesheet", financial_frames.get("balancesheet"), code, as_of_date
    )
    cashflow = canonical_history("cashflow", financial_frames.get("cashflow"), code, as_of_date)
    latest_income, previous_income = latest_and_previous(income)
    latest_balance, previous_balance = latest_and_previous(balance)
    latest_cashflow, _ = latest_and_previous(cashflow)

    profit, profit_field = first_value(latest_income, "n_income_attr_p", "n_income", "net_profit")
    adjusted, adjusted_field = first_value(
        latest_income, "deducted_profit", "adj_profit", "net_profit_deducted"
    )
    cfo, cfo_field = first_value(
        latest_cashflow, "n_cashflow_act", "c_fr_operate_a", "operating_cash_flow"
    )
    total_profit, total_profit_field = first_value(latest_income, "total_profit")
    non_operating, non_operating_field = first_value(
        latest_income, "non_oper_income", "n_oth_income"
    )
    impairment, impairment_field = first_value(
        latest_income, "assets_impair_loss", "impairment_loss"
    )
    inventory, inventory_field = first_value(latest_balance, "inventories", "inventory")
    previous_inventory, _ = first_value(previous_balance, "inventories", "inventory")
    receivables, receivables_field = first_value(
        latest_balance, "accounts_receiv", "acct_receivable", "acc_receivable"
    )
    previous_receivables, _ = first_value(
        previous_balance, "accounts_receiv", "acct_receivable", "acc_receivable"
    )
    liabilities, liabilities_field = first_value(latest_balance, "total_liab")
    assets, assets_field = first_value(latest_balance, "total_assets")

    add_known(
        vector,
        "quality_profit",
        profit,
        datasets=("income",),
        fields=((profit_field,) if profit_field else ("n_income",)),
        history=income,
    )
    add_known(
        vector,
        "adjusted_profit",
        adjusted,
        datasets=("income",),
        fields=((adjusted_field,) if adjusted_field else ("deducted_profit", "adj_profit")),
        history=income,
    )
    add_known(
        vector,
        "quality_cfo",
        cfo,
        datasets=("cashflow",),
        fields=((cfo_field,) if cfo_field else ("n_cashflow_act",)),
        history=cashflow,
    )
    add_known(
        vector,
        "quality_cfo_to_profit",
        safe_ratio(cfo, profit),
        datasets=("cashflow", "income"),
        fields=((cfo_field,) if cfo_field else ("n_cashflow_act",))
        + ((profit_field,) if profit_field else ("n_income",)),
        history=cashflow,
    )
    add_known(
        vector,
        "quality_non_operating_ratio",
        safe_ratio(non_operating, total_profit),
        datasets=("income",),
        fields=tuple(value for value in (non_operating_field, total_profit_field) if value),
        history=income,
    )
    add_known(
        vector,
        "quality_impairment_ratio",
        safe_ratio(impairment, total_profit),
        datasets=("income",),
        fields=tuple(value for value in (impairment_field, total_profit_field) if value),
        history=income,
    )
    add_known(
        vector,
        "quality_inventory_change",
        safe_change(inventory, previous_inventory),
        datasets=("balancesheet",),
        fields=((inventory_field,) if inventory_field else ("inventories",)),
        history=balance,
    )
    add_known(
        vector,
        "quality_receivables_change",
        safe_change(receivables, previous_receivables),
        datasets=("balancesheet",),
        fields=((receivables_field,) if receivables_field else ("accounts_receiv",)),
        history=balance,
    )
    add_known(
        vector,
        "quality_leverage",
        safe_ratio(liabilities, assets),
        datasets=("balancesheet",),
        fields=tuple(value for value in (liabilities_field, assets_field) if value),
        history=balance,
    )

    penalties = 0.0
    reasons: list[str] = []
    if profit is not None and adjusted is not None and profit > 0 and adjusted / profit < 0.5:
        reasons.append("profit_dominated_by_non_recurring_items")
        penalties += 30
    if (
        total_profit not in {None, 0.0}
        and non_operating is not None
        and abs(non_operating / total_profit) >= 0.8
    ):
        reasons.append("non_operating_income_dominates_profit")
        penalties += 25
    if cfo is not None and profit is not None and profit > 0 and cfo < 0:
        reasons.append("negative_operating_cash_flow")
        penalties += 25
    if (
        safe_change(inventory, previous_inventory) is not None
        and safe_change(inventory, previous_inventory) > 0.5
    ):
        reasons.append("inventory_pressure")
        penalties += 10
    if (
        safe_change(receivables, previous_receivables) is not None
        and safe_change(receivables, previous_receivables) > 0.5
    ):
        reasons.append("receivables_pressure")
        penalties += 10
    if (
        impairment is not None
        and total_profit not in {None, 0.0}
        and abs(impairment / total_profit) >= 0.8
    ):
        reasons.append("impairment_effect")
        penalties += 20
    for reason in reasons:
        if reason not in vector.risk_flags:
            vector.risk_flags.append(reason)
    vector.rejected_reasons.extend(
        reason
        for reason in reasons
        if reason
        in {"profit_dominated_by_non_recurring_items", "non_operating_income_dominates_profit"}
    )
    status = "known" if income is not None and not income.empty else "unknown"
    vector.add(
        "quality_gate_status",
        "rejected" if vector.rejected else "pass" if status == "known" else "unknown",
        status=status,
        source_datasets=("income", "balancesheet", "cashflow"),
        source_fields=(),
        reason=None if status == "known" else "financial quality evidence is unavailable",
    )
    vector.add(
        "quality_score",
        max(0.0, 100.0 - penalties) if status == "known" else None,
        status=status,
        source_datasets=("income", "balancesheet", "cashflow"),
        source_fields=(),
        reason=None if status == "known" else "financial quality evidence is unavailable",
    )
    return vector
