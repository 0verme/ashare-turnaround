from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround.features import compute_fundamental_features
from ashare_turnaround.pit.comparable import (
    COMPARABLE_PERIOD_CONTRACT_VERSION,
    CUMULATIVE_YTD,
    INVALID_DENOMINATOR,
    NEGATIVE_DENOMINATOR,
    POINT_IN_TIME,
    SINGLE_QUARTER,
    growth_from_match,
    match_comparable_period,
    ttm_from_series,
    validated_single_quarter_series,
)
from ashare_turnaround.pit.financial import canonicalize_financial_frame, derive_single_quarter

CODE = "600000.SH"


def _frame(
    periods: list[str],
    values: list[float],
    *,
    dataset: str = "income",
    report_type: str | list[str] | None = "1",
    available: list[str] | None = None,
    scope: str | list[str] | None = None,
    unit: str | list[str] | None = None,
    update_flag: str | list[str] | None = "0",
    field: str = "revenue",
) -> pd.DataFrame:
    count = len(periods)
    data: dict[str, object] = {
        "ts_code": [CODE] * count,
        "end_date": periods,
        field: values,
    }
    if dataset in {"income", "cashflow", "balancesheet"}:
        dates = available or ["20260101"] * count
        data.update({"ann_date": dates, "f_ann_date": dates})
    if report_type is not None:
        data["report_type"] = (
            report_type if isinstance(report_type, list) else [report_type] * count
        )
    if update_flag is not None:
        data["update_flag"] = (
            update_flag if isinstance(update_flag, list) else [update_flag] * count
        )
    if scope is not None:
        data["scope"] = scope if isinstance(scope, list) else [scope] * count
    if unit is not None:
        data["unit"] = unit if isinstance(unit, list) else [unit] * count
    return pd.DataFrame(data)


def test_canonical_identity_distinguishes_duration_and_balance_point_in_time() -> None:
    income = canonicalize_financial_frame(
        "income",
        _frame(["20250630"], [220.0], available=["20250830"]),
    )
    single = canonicalize_financial_frame(
        "income",
        _frame(["20250630"], [120.0], report_type="2", available=["20250830"]),
    )
    balance = canonicalize_financial_frame(
        "balancesheet",
        _frame(["20250630"], [500.0], available=["20250830"], field="total_assets"),
    )

    assert income.loc[0, "fiscal_year"] == 2025
    assert income.loc[0, "fiscal_period"] == "H1"
    assert income.loc[0, "quarter"] == 2
    assert income.loc[0, "duration_semantics"] == CUMULATIVE_YTD
    assert single.loc[0, "duration_semantics"] == SINGLE_QUARTER
    assert income.loc[0, "source_version_identity"] != single.loc[0, "source_version_identity"]
    assert balance.loc[0, "duration_semantics"] == POINT_IN_TIME
    assert balance.loc[0, "statement_type"] == "BALANCE_SHEET"
    assert income.loc[0, "comparable_period_contract_version"] == (
        COMPARABLE_PERIOD_CONTRACT_VERSION
    )


def test_yoy_matches_same_economic_period_not_adjacent_report() -> None:
    raw = _frame(
        ["20240930", "20250630", "20250930"],
        [250.0, 220.0, 300.0],
        available=["20241030", "20250830", "20251030"],
    )
    canonical = canonicalize_financial_frame("income", raw)
    current = canonical.iloc[2]

    match = match_comparable_period(
        canonical,
        current,
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20251101",
    )

    assert match.status == "known"
    assert match.current_period == "20250930"
    assert match.comparison_period == "20240930"
    assert match.comparison_period != "20250630"

    # The only adjacent H1 observation is never accepted as a YoY denominator.
    no_prior_year = canonical.iloc[1:].reset_index(drop=True)
    rejected = match_comparable_period(
        no_prior_year,
        no_prior_year.iloc[1],
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20251101",
    )
    assert rejected.status == "unknown"
    assert rejected.reason == "missing_comparable_period"


def test_fundamental_yoy_accepts_same_cumulative_period_without_calling_it_qoq() -> None:
    raw = _frame(
        ["20240930", "20250630", "20250930"],
        [250.0, 220.0, 300.0],
        available=["20241030", "20250830", "20251030"],
    )
    vector = compute_fundamental_features({"income": raw}, CODE, "20251101")

    assert vector.values["revenue_yoy"] == 0.2
    evidence = vector.evidence["revenue_yoy"]
    assert evidence.current_period == "20250930"
    assert evidence.comparison_period == "20240930"
    assert evidence.period_semantics == CUMULATIVE_YTD
    assert evidence.current_raw_value == 300.0
    assert evidence.comparison_raw_value == 250.0


def test_quarterization_contract_covers_q1_q2_q3_q4() -> None:
    raw = _frame(
        ["20250331", "20250630", "20250930", "20251231"],
        [10.0, 23.0, 35.0, 50.0],
        available=None,
    ).drop(columns=["ann_date", "f_ann_date"])
    result = derive_single_quarter(raw, "revenue")

    assert result["single_quarter"].tolist() == [10.0, 13.0, 12.0, 15.0]
    assert result["single_quarter_status"].tolist() == ["known"] * 4
    assert result["duration_semantics"].tolist() == [SINGLE_QUARTER] * 4
    assert result["single_quarter_source_periods"].tolist() == [
        "20250331",
        "20250630|20250331",
        "20250930|20250630",
        "20251231|20250930",
    ]


def test_adversarial_q3_cumulative_minus_h1_is_single_quarter_not_qoq() -> None:
    raw = _frame(
        ["20250331", "20250630", "20250930"],
        [100.0, 220.0, 300.0],
    ).drop(columns=["ann_date", "f_ann_date"])
    result = derive_single_quarter(raw, "revenue")
    assert result.loc[2, "single_quarter"] == 80.0
    assert result.loc[2, "single_quarter_status"] == "known"

    qoq = match_comparable_period(
        result,
        result.iloc[2],
        comparison="qoq",
        dataset="income",
        value_column="comparable_value",
    )
    assert qoq.status == "known"
    assert qoq.current_value == 80.0
    assert qoq.comparison_value == 120.0
    assert qoq.comparison_period == "20250630"


def test_quarterization_missing_chain_returns_unknown_with_reason() -> None:
    raw = _frame(
        ["20250331", "20250930", "20251231"],
        [10.0, 35.0, 50.0],
    ).drop(columns=["ann_date", "f_ann_date"])
    result = derive_single_quarter(raw, "revenue")
    by_period = result.set_index("end_date")

    assert pd.isna(by_period.loc["20250930", "single_quarter"])
    assert by_period.loc["20250930", "single_quarter_reason"] == (
        "missing_preceding_cumulative_period"
    )
    # FY-Q3 is still independently validated because the required Q3
    # cumulative predecessor exists; the missing H1 only invalidates Q3 itself.
    assert by_period.loc["20251231", "single_quarter"] == 15.0


def test_quarterization_rejects_unit_and_scope_mismatch() -> None:
    unit_mismatch = _frame(
        ["20250331", "20250630"],
        [10.0, 220.0],
        unit=["CNY", "thousand CNY"],
    ).drop(columns=["ann_date", "f_ann_date"])
    scope_mismatch = _frame(
        ["20250331", "20250630"],
        [10.0, 220.0],
        scope=["consolidated", "parent-only"],
    ).drop(columns=["ann_date", "f_ann_date"])

    unit_result = derive_single_quarter(unit_mismatch, "revenue")
    scope_result = derive_single_quarter(scope_mismatch, "revenue")
    assert unit_result.loc[1, "single_quarter_reason"] == "unit_mismatch"
    assert scope_result.loc[1, "single_quarter_reason"] == "scope_mismatch"
    assert pd.isna(unit_result.loc[1, "single_quarter"])
    assert pd.isna(scope_result.loc[1, "single_quarter"])


def test_unsupported_period_semantics_stays_unknown() -> None:
    raw = _frame(
        ["20250331"],
        [100.0],
        report_type=None,
    )
    raw["duration_semantics"] = "not-a-proven-semantic"
    canonical = canonicalize_financial_frame("income", raw)
    assert canonical.loc[0, "duration_semantics"] == "UNKNOWN"
    assert canonical.loc[0, "period_semantics_status"] == "unknown"
    assert canonical.loc[0, "period_semantics_reason"] == "unsupported_duration_semantics"
    result = derive_single_quarter(raw.drop(columns=["ann_date", "f_ann_date"]), "revenue")
    assert result.loc[0, "single_quarter_status"] == "unknown"
    assert result.loc[0, "single_quarter_reason"] == "unsupported_duration_semantics"


def test_balance_sheet_is_point_in_time_and_never_quarterized() -> None:
    raw = _frame(
        ["20250331", "20250630"],
        [100.0, 120.0],
        dataset="balancesheet",
        field="total_assets",
    )
    canonical = canonicalize_financial_frame("balancesheet", raw)
    assert set(canonical["duration_semantics"]) == {POINT_IN_TIME}
    result = derive_single_quarter(raw, "total_assets", dataset_kind="balancesheet")
    assert result["single_quarter"].isna().all()
    assert set(result["single_quarter_reason"]) == {"unsupported_statement_type"}


def test_qoq_requires_single_quarter_series() -> None:
    cumulative = canonicalize_financial_frame(
        "income",
        _frame(
            ["20250630", "20250930"],
            [220.0, 300.0],
            available=["20250830", "20251030"],
        ),
    )
    rejected = match_comparable_period(
        cumulative,
        cumulative.iloc[1],
        comparison="qoq",
        dataset="income",
        value_column="revenue",
        as_of_date="20251101",
    )
    assert rejected.status == "unknown"
    assert rejected.reason == "qoq_requires_validated_single_quarter"

    single = canonicalize_financial_frame(
        "income",
        _frame(
            ["20250630", "20250930"],
            [120.0, 80.0],
            report_type="2",
            available=["20250830", "20251030"],
        ),
    )
    accepted = match_comparable_period(
        single,
        single.iloc[1],
        comparison="qoq",
        dataset="income",
        value_column="revenue",
        as_of_date="20251101",
    )
    assert accepted.status == "known"
    assert accepted.comparison_period == "20250630"
    assert growth_from_match(accepted, metric="revenue_qoq").value == -1 / 3


def test_ttm_requires_four_consecutive_validated_quarters() -> None:
    raw = _frame(
        [
            "20240331",
            "20240630",
            "20240930",
            "20241231",
            "20250331",
            "20250630",
            "20250930",
        ],
        [10.0, 20.0, 30.0, 40.0, 11.0, 21.0, 31.0],
    ).drop(columns=["ann_date", "f_ann_date"])
    series = validated_single_quarter_series(raw, "revenue")
    ttm = ttm_from_series(series, dataset="income")

    assert ttm.status == "known"
    assert ttm.value == 41.0
    assert ttm.current_period == "20250930"
    assert ttm.provenance["source_quarters"] == [
        "20241231",
        "20250331",
        "20250630",
        "20250930",
    ]
    assert len(ttm.source_versions) >= 4  # Q2/Q3 chains reuse validated predecessors.

    incomplete = series.loc[series["end_date"] != "20250630"].reset_index(drop=True)
    missing = ttm_from_series(incomplete, dataset="income")
    assert missing.status == "unknown"
    assert missing.reason == "missing_quarter"

    mixed_unit = series.copy()
    mixed_unit.loc[mixed_unit["end_date"] == "20250630", "unit"] = "thousand CNY"
    mixed = ttm_from_series(mixed_unit, dataset="income")
    assert mixed.status == "unknown"
    assert mixed.reason == "unit_mismatch"


def test_ttm_as_of_ignores_a_later_quarter_disclosure() -> None:
    raw = _frame(
        [
            "20240331",
            "20240630",
            "20240930",
            "20241231",
            "20250331",
            "20250630",
            "20250930",
            "20251231",
        ],
        [10.0, 20.0, 30.0, 70.0, 11.0, 21.0, 31.0, 82.0],
        available=[
            "20240401",
            "20240701",
            "20241001",
            "20250401",
            "20250402",
            "20250702",
            "20251002",
            "20260401",
        ],
    )
    series = validated_single_quarter_series(raw, "revenue")
    ttm = ttm_from_series(series, dataset="income", as_of_date="20251101")

    assert ttm.status == "known"
    assert ttm.current_period == "20250930"
    assert ttm.value == 71.0


def test_growth_denominator_policy_distinguishes_zero_and_negative_base() -> None:
    negative = canonicalize_financial_frame(
        "income",
        _frame(
            ["20240331", "20250331"],
            [-1.0, 1.0],
            report_type="2",
            available=["20240430", "20250430"],
        ),
    )
    negative_match = match_comparable_period(
        negative,
        negative.iloc[1],
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20250501",
    )
    negative_growth = growth_from_match(negative_match, metric="revenue_yoy")
    assert negative_growth.value is None
    assert negative_growth.reason == NEGATIVE_DENOMINATOR
    assert negative_growth.provenance["sign_transition"] == "negative_to_positive"

    zero = canonicalize_financial_frame(
        "income",
        _frame(
            ["20240331", "20250331"],
            [0.0, 1.0],
            report_type="2",
            available=["20240430", "20250430"],
        ),
    )
    zero_match = match_comparable_period(
        zero,
        zero.iloc[1],
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20250501",
    )
    zero_growth = growth_from_match(zero_match, metric="revenue_yoy")
    assert zero_growth.value is None
    assert zero_growth.reason == INVALID_DENOMINATOR
    assert zero_growth.provenance["sign_transition"] == "zero_to_nonzero"

    sign_change = canonicalize_financial_frame(
        "income",
        _frame(
            ["20240331", "20250331"],
            [1.0, -1.0],
            report_type="2",
            available=["20240430", "20250430"],
        ),
    )
    sign_match = match_comparable_period(
        sign_change,
        sign_change.iloc[1],
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20250501",
    )
    sign_growth = growth_from_match(sign_match, metric="revenue_yoy")
    assert sign_growth.value is None
    assert sign_growth.reason == "sign_transition"
    assert sign_growth.provenance["sign_transition"] == "positive_to_negative"


def test_margin_yoy_is_difference_of_period_margins_not_ratio_of_deltas() -> None:
    periods = ["20240331", "20250331"]
    common = _frame(
        periods,
        [100.0, 200.0],
        report_type="2",
        available=["20240430", "20250430"],
    )
    common["n_income_attr_p"] = [10.0, 60.0]
    common["operate_profit"] = [20.0, 80.0]
    common["gross_profit"] = [30.0, 140.0]
    vector = compute_fundamental_features({"income": common}, CODE, "20250501")

    assert vector.values["operating_margin"] == 0.4
    assert vector.values["operating_margin_yoy_change"] == 0.2
    assert vector.values["net_margin"] == 0.3
    assert vector.values["net_margin_yoy_change"] == pytest.approx(0.2)
    assert vector.evidence["operating_margin_yoy_change"].current_period == "20250331"
    assert vector.evidence["operating_margin_yoy_change"].comparison_period == "20240331"

    zero_revenue = _frame(
        periods,
        [0.0, 0.0],
        report_type="2",
        available=["20240430", "20250430"],
    )
    zero_revenue["operate_profit"] = [1.0, 2.0]
    zero_vector = compute_fundamental_features({"income": zero_revenue}, CODE, "20250501")
    assert zero_vector.values["operating_margin"] is None
    assert zero_vector.evidence["operating_margin"].reason == "invalid_denominator"


def test_revisions_are_selected_at_as_of_and_later_versions_do_not_retroactively_leak() -> None:
    raw = _frame(
        ["20240331", "20250331", "20250331"],
        [50.0, 100.0, 120.0],
        report_type="2",
        available=["20240430", "20250420", "20250510"],
        update_flag=["0", "0", "1"],
    )
    before = compute_fundamental_features({"income": raw}, CODE, "20250501")
    after = compute_fundamental_features({"income": raw}, CODE, "20250510")

    assert before.values["revenue_level"] == 100.0
    assert after.values["revenue_level"] == 120.0
    assert before.values["revenue_yoy"] == 1.0
    assert after.values["revenue_yoy"] == 1.4
    assert before.evidence["revenue_yoy"].current_availability_date == "20250420"
    assert "20240430" in before.evidence["revenue_yoy"].availability_dates
    assert after.evidence["revenue_yoy"].current_availability_date == "20250510"
    assert "20250510" in after.evidence["revenue_yoy"].availability_dates


def test_feature_provenance_and_determinism_include_contract_version_and_source_chain() -> None:
    raw = _frame(
        ["20240331", "20250630", "20250930"],
        [100.0, 220.0, 300.0],
        available=["20240430", "20250830", "20251030"],
    )
    first = compute_fundamental_features({"income": raw}, CODE, "20251101")
    second = compute_fundamental_features({"income": raw}, CODE, "20251101")
    evidence = first.evidence["revenue_yoy"]

    assert first.as_dict() == second.as_dict()
    assert first.comparable_period_contract_version == COMPARABLE_PERIOD_CONTRACT_VERSION
    assert evidence.contract_version == COMPARABLE_PERIOD_CONTRACT_VERSION
    assert evidence.current_period == "20250930"
    assert evidence.comparison_period is None  # 2024Q3 is absent in this fixture.
    assert evidence.current_raw_value == 300.0
    assert evidence.current_availability_date == "20251030"
    assert evidence.reason == "missing_comparable_period"
    assert evidence.provenance["contract_version"] == COMPARABLE_PERIOD_CONTRACT_VERSION
