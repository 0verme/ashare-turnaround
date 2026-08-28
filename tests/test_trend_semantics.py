from __future__ import annotations

import math

import pandas as pd
import pytest

from ashare_turnaround.features import compute_trend_features
from ashare_turnaround.features.trend import (
    COMPARABLE_PERIOD_CONTRACT_VERSION,
    DISCONTINUOUS,
    NEGATIVE_TO_POSITIVE,
    POSITIVE_TO_NEGATIVE,
    STRONG_TURNAROUND,
    calculate_trend,
)
from ashare_turnaround.pit.comparable import (
    INVALID_DENOMINATOR,
    NEGATIVE_DENOMINATOR,
)

CODE = "600000.SH"
AS_OF = "20251101"


def _single_frame(
    periods: list[str],
    values: list[float],
    *,
    available: list[str] | None = None,
    field: str = "revenue",
    report_type: str | list[str] = "2",
) -> pd.DataFrame:
    dates = available or ["20240101"] * len(periods)
    report_types = report_type if isinstance(report_type, list) else [report_type] * len(periods)
    return pd.DataFrame(
        {
            "ts_code": [CODE] * len(periods),
            "end_date": periods,
            "ann_date": dates,
            "f_ann_date": dates,
            "report_type": report_types,
            "update_flag": ["0"] * len(periods),
            field: values,
        }
    )


def _adversarial_growth_frame(current_values: list[float]) -> pd.DataFrame:
    periods = [
        "20230331",
        "20230630",
        "20230930",
        "20240331",
        "20240630",
        "20240930",
    ]
    values = [100.0, 100.0, 100.0, *current_values]
    frame = _single_frame(periods, values)
    frame["n_income_attr_p"] = values
    frame["operate_profit"] = values
    frame["gross_profit"] = [value / 2 for value in values]
    return frame


def _observations(values: list[float], periods: list[str] | None = None) -> list[dict[str, object]]:
    labels = periods or [f"2024Q{index}" for index in range(1, len(values) + 1)]
    return [
        {
            "period": period,
            "growth_rate": value,
            "status": "valid",
            "comparable_period_contract_version": COMPARABLE_PERIOD_CONTRACT_VERSION,
        }
        for period, value in zip(labels, values)
    ]


def test_case_a_turnaround_separates_change_acceleration_and_sign_transition() -> None:
    vector = compute_trend_features(
        {"income": _adversarial_growth_frame([80.0, 95.0, 110.0])}, CODE, AS_OF
    )

    assert vector.values["revenue_yoy_level"] == 0.10
    assert vector.values["revenue_yoy_change"] == 15.0
    assert vector.values["revenue_yoy_previous_change"] == 15.0
    assert vector.values["revenue_yoy_acceleration"] == 0.0
    assert vector.values["revenue_yoy_sign_transition"] == NEGATIVE_TO_POSITIVE
    assert vector.values["revenue_yoy_improvement_count"] == 2
    assert vector.values["revenue_yoy_persistence"] == "improving"
    assert vector.values["revenue_yoy_state"] == STRONG_TURNAROUND
    assert vector.values["revenue_yoy_turnaround_evidence"] == "positive"
    assert vector.values["yoy_acceleration"] == 0.0

    assert vector.evidence["revenue_yoy_level"].provenance["unit"] == "ratio"
    evidence = vector.evidence["revenue_yoy_change"]
    assert evidence.provenance["unit"] == "percentage_points"
    assert evidence.trend_contract_version == "turnaround-trend-v2"
    assert evidence.provenance["trend_contract_version"] == "turnaround-trend-v2"
    assert evidence.provenance["comparable_period_contract_version"] == (
        COMPARABLE_PERIOD_CONTRACT_VERSION
    )
    latest = evidence.provenance["observations"][-1]
    assert {
        "period",
        "comparison_period",
        "growth_rate",
        "status",
        "availability_date",
        "source_version",
        "comparable_period_contract_version",
    }.issubset(latest)


def test_case_b_high_growth_can_still_be_deteriorating() -> None:
    vector = compute_trend_features(
        {"income": _adversarial_growth_frame([140.0, 135.0, 130.0])}, CODE, AS_OF
    )

    assert vector.values["revenue_yoy_level"] == 0.30
    assert vector.values["revenue_yoy_change"] == -5.0
    assert vector.values["revenue_yoy_previous_change"] == -5.0
    assert vector.values["revenue_yoy_acceleration"] == 0.0
    assert vector.values["revenue_yoy_sign_transition"] == "NONE"
    assert vector.values["revenue_yoy_improvement_count"] == 0
    assert vector.values["revenue_yoy_persistence"] == "deteriorating"
    assert vector.values["revenue_yoy_state"] == "DETERIORATING"
    assert vector.values["revenue_yoy_turnaround_evidence"] == "negative"


def test_first_change_and_acceleration_cover_positive_negative_and_zero() -> None:
    positive = calculate_trend(_observations([-.20, -.05, .10]))
    negative = calculate_trend(_observations([.40, .35, .30]))
    accelerating = calculate_trend(_observations([0.0, .10, .30]))
    decelerating = calculate_trend(_observations([.30, .20, 0.0]))
    stable = calculate_trend(_observations([.10, .10, .10]))

    assert positive.change == 15.0
    assert positive.acceleration == 0.0
    assert negative.change == -5.0
    assert negative.acceleration == 0.0
    assert accelerating.change == 20.0
    assert accelerating.acceleration == 10.0
    assert decelerating.change == -20.0
    assert decelerating.acceleration == -10.0
    assert stable.change == 0.0
    assert stable.acceleration == 0.0


def test_sign_transition_is_independent_of_acceleration() -> None:
    assert calculate_trend(_observations([-.1, .1])).sign_transition == NEGATIVE_TO_POSITIVE
    assert calculate_trend(_observations([.1, -.1])).sign_transition == POSITIVE_TO_NEGATIVE
    assert calculate_trend(_observations([-.1, -.2])).sign_transition == "NONE"
    assert calculate_trend(_observations([.1, .2])).sign_transition == "NONE"


def test_persistence_does_not_bridge_unknown_or_missing_period() -> None:
    improving = calculate_trend(_observations([-.30, -.20, -.05, .08]))
    assert improving.improvement_count == 3
    assert improving.persistence == "improving"

    unknown_middle = calculate_trend(
        [
            *_observations([-.20]),
            {
                "period": "2024Q2",
                "growth_rate": None,
                "status": "unknown",
                "reason": "missing_value",
            },
            {
                "period": "2024Q3",
                "growth_rate": .10,
                "status": "valid",
            },
        ]
    )
    assert unknown_middle.change is None
    assert unknown_middle.improvement_count is None
    assert unknown_middle.change_status == "unknown"

    missing_period = calculate_trend(
        _observations([-.20, -.05], periods=["2024Q1", "2024Q3"])
    )
    assert missing_period.change is None
    assert missing_period.change_status == DISCONTINUOUS
    assert missing_period.change_reason == "discontinuous_periods"


def test_minimum_history_and_contract_gate_fail_closed() -> None:
    one = calculate_trend(_observations([.10]))
    two = calculate_trend(_observations([.10, .20]))
    three = calculate_trend(_observations([.10, .20, .30]))
    invalid_contract = calculate_trend(
        [
            {"period": "2024Q1", "growth_rate": .1, "status": "valid", "contract_version": "v0"},
            {"period": "2024Q2", "growth_rate": .2, "status": "valid", "contract_version": "v0"},
        ]
    )

    assert one.level == .10
    assert one.change is None
    assert one.change_status == "insufficient_history"
    assert two.change == 10.0
    assert two.acceleration is None
    assert two.acceleration_status == "insufficient_history"
    assert three.acceleration == 0.0
    assert invalid_contract.level is None
    assert invalid_contract.status == "unsupported"
    assert invalid_contract.change_status == "unsupported"


def test_margin_trend_keeps_each_margin_metric_separate() -> None:
    periods = ["20240331", "20240630", "20240930"]
    frame = _single_frame(periods, [100.0, 100.0, 100.0])
    frame["gross_profit"] = [8.0, 9.5, 11.0]
    frame["operate_profit"] = [10.0, 11.0, 12.0]
    frame["n_income_attr_p"] = [5.0, 6.0, 7.0]

    vector = compute_trend_features({"income": frame}, CODE, AS_OF)

    assert vector.values["gross_margin_change"] == 1.5
    assert vector.values["gross_margin_acceleration"] == 0.0
    assert vector.values["operating_margin_change"] == 1.0
    assert vector.values["net_margin_change"] == 1.0
    assert vector.evidence["gross_margin_change"].provenance["unit"] == (
        "percentage_points"
    )


def test_qoq_consumes_quarterized_single_quarters_not_cumulative_rows() -> None:
    periods = ["20240331", "20240630", "20240930"]
    frame = _single_frame(periods, [100.0, 220.0, 300.0], report_type="1")
    frame["n_income_attr_p"] = [100.0, 220.0, 300.0]
    frame["operate_profit"] = [10.0, 22.0, 30.0]
    frame["gross_profit"] = [50.0, 110.0, 150.0]

    vector = compute_trend_features({"income": frame}, CODE, AS_OF)
    evidence = vector.evidence["revenue_qoq_level"]
    period_match = evidence.provenance["observations"][-1]["provenance"]["period_match"]

    assert vector.values["revenue_qoq_level"] == pytest.approx(80.0 / 120.0 - 1.0)
    assert period_match["current_value"] == 80.0
    assert period_match["comparison_value"] == 120.0
    assert period_match["comparison_period"]["report_period"] == "20240630"
    assert "20240930" in evidence.periods
    assert "20240630" in evidence.periods


def test_ttm_trend_requires_validated_endpoints_and_missing_quarter_stays_unknown() -> None:
    periods = pd.date_range("2023-03-31", "2024-12-31", freq="QE-DEC").strftime("%Y%m%d").tolist()
    frame = _single_frame(periods, list(range(1, len(periods) + 1)))
    vector = compute_trend_features({"income": frame}, CODE, AS_OF)

    assert vector.values["revenue_ttm_level"] == 26.0
    assert vector.values["revenue_ttm_change"] == 4.0
    assert vector.values["revenue_ttm_acceleration"] == 0.0
    assert vector.values["ttm_level"] == 26.0
    ttm_evidence = vector.evidence["revenue_ttm_level"]
    assert ttm_evidence.provenance["observations"][-1]["provenance"]["source_quarters"] == [
        "20240331",
        "20240630",
        "20240930",
        "20241231",
    ]

    incomplete = frame.loc[frame["end_date"] != "20240930"].reset_index(drop=True)
    missing = compute_trend_features({"income": incomplete}, CODE, AS_OF)
    assert missing.values["revenue_ttm_level"] is None
    assert missing.evidence["revenue_ttm_level"].reason == "missing_quarter"


def test_revision_boundary_changes_current_trend_only_after_revision_is_visible() -> None:
    frame = _adversarial_growth_frame([80.0, 95.0, 110.0])
    revision = frame.iloc[[-1]].copy()
    revision["revenue"] = 130.0
    revision["n_income_attr_p"] = 130.0
    revision["operate_profit"] = 130.0
    revision["gross_profit"] = 65.0
    revision["ann_date"] = "20241110"
    revision["f_ann_date"] = "20241110"
    revision["update_flag"] = "1"
    raw = pd.concat([frame, revision], ignore_index=True)

    before = compute_trend_features({"income": raw}, CODE, "20241109")
    after = compute_trend_features({"income": raw}, CODE, "20241110")

    assert before.values["revenue_yoy_level"] == .10
    assert after.values["revenue_yoy_level"] == .30
    assert before.evidence["revenue_yoy_level"].current_availability_date == "20240101"
    assert after.evidence["revenue_yoy_level"].current_availability_date == "20241110"


def test_negative_and_zero_denominators_do_not_create_growth_percentages() -> None:
    periods = ["20240331", "20250331"]
    negative = _single_frame(periods, [-1.0, 1.0], field="revenue")
    zero = _single_frame(periods, [0.0, 1.0], field="revenue")

    negative_vector = compute_trend_features({"income": negative}, CODE, AS_OF)
    zero_vector = compute_trend_features({"income": zero}, CODE, AS_OF)

    assert negative_vector.values["revenue_yoy_level"] is None
    assert negative_vector.evidence["revenue_yoy_level"].reason == NEGATIVE_DENOMINATOR
    assert negative_vector.values["revenue_yoy_change"] is None
    assert negative_vector.values["revenue_yoy_sign_transition"] == NEGATIVE_TO_POSITIVE
    assert zero_vector.values["revenue_yoy_level"] is None
    assert zero_vector.evidence["revenue_yoy_level"].reason == INVALID_DENOMINATOR
    assert zero_vector.values["revenue_yoy_change"] is None


def test_outlier_is_finite_and_output_is_deterministic() -> None:
    observations = _observations([.05, .07, 2.0, .08])
    first = calculate_trend(observations)
    second = calculate_trend(observations)

    assert first.as_dict() == second.as_dict()
    assert math.isfinite(first.change)
    assert math.isfinite(first.acceleration)
    assert first.state == "DETERIORATING"
