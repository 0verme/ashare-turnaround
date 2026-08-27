from __future__ import annotations

import pandas as pd

from ashare_turnaround.features import (
    compute_attention_features,
    compute_fundamental_features,
    compute_quality_features,
    compute_trend_features,
)
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.replay import ReplayConfig, run_replay_frames
from ashare_turnaround.scanner.report import candidate_report, candidate_report_markdown
from ashare_turnaround.scanner.score import score_feature_vector
from ashare_turnaround.scanner.universe import UniverseConfig, build_investable_universe

AS_OF = "20250630"
CODE = "600000.SH"


def _financial_frames() -> dict[str, pd.DataFrame]:
    periods = ["20240331", "20240630", "20240930", "20241231"]
    available = ["20240430", "20240830", "20241030", "20250330"]
    common = {
        "ts_code": [CODE] * 4,
        "end_date": periods,
        "ann_date": available,
        "f_ann_date": available,
        "report_type": ["1"] * 4,
        "update_flag": ["0"] * 4,
    }
    income = pd.DataFrame(
        {
            **common,
            "revenue": [100.0, 115.0, 135.0, 160.0],
            "n_income_attr_p": [4.0, 7.0, 11.0, 17.0],
            "operate_profit": [8.0, 11.0, 16.0, 23.0],
            "gross_profit": [25.0, 30.0, 38.0, 48.0],
            "total_profit": [5.0, 8.0, 12.0, 18.0],
            "non_oper_income": [0.2, 0.2, 0.3, 0.3],
            "assets_impair_loss": [0.1, 0.1, 0.1, 0.1],
        }
    )
    balance = pd.DataFrame(
        {
            **common,
            "total_assets": [200.0, 205.0, 210.0, 220.0],
            "total_hldr_eqy_inc_min_int": [100.0, 102.0, 105.0, 110.0],
            "total_liab": [100.0, 103.0, 105.0, 110.0],
            "inventories": [20.0, 21.0, 22.0, 23.0],
            "accounts_receiv": [18.0, 19.0, 20.0, 21.0],
        }
    )
    cashflow = pd.DataFrame(
        {
            **common,
            "n_cashflow_act": [6.0, 8.0, 12.0, 18.0],
        }
    )
    return {"income": income, "balancesheet": balance, "cashflow": cashflow}


def _market_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2025-03-01", AS_OF, freq="B")
    close = [10.0 + index * 0.03 for index in range(len(dates))]
    daily = pd.DataFrame(
        {
            "ts_code": [CODE] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "close": close,
            "vol": [1000.0 + index * 2 for index in range(len(dates))],
            "pct_chg": [0.2] * len(dates),
        }
    )
    daily_basic = pd.DataFrame(
        {
            "ts_code": [CODE] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "turnover_rate": [1.0 + (index % 5) * 0.1 for index in range(len(dates))],
            "amount": [10000.0 + index * 10 for index in range(len(dates))],
        }
    )
    return daily, daily_basic


def _frames() -> dict[str, pd.DataFrame]:
    daily, daily_basic = _market_frames()
    frames = _financial_frames()
    frames.update(
        {
            "stock_basic": pd.DataFrame(
                {
                    "ts_code": [CODE],
                    "symbol": ["600000"],
                    "name": ["Synthetic Bank"],
                    "list_date": ["20100101"],
                    "list_status": ["L"],
                }
            ),
            "daily": daily,
            "daily_basic": daily_basic,
            "fina_indicator": pd.DataFrame(),
        }
    )
    return frames


def test_universe_records_policy_exclusions_and_feature_groups_are_pit_safe() -> None:
    stock_basic = pd.DataFrame(
        {
            "ts_code": [CODE, "000001.SZ", "430001.BJ"],
            "name": ["Normal", "ST Example", "BSE Example"],
            "list_date": ["20100101", "20100101", "20100101"],
            "list_status": ["L", "L", "L"],
        }
    )
    universe = build_investable_universe(
        stock_basic,
        as_of_date=AS_OF,
        financial_frames=_financial_frames(),
        config=UniverseConfig(min_financial_periods=4),
    )

    assert universe.included["ts_code"].tolist() == [CODE]
    assert {value.reason for value in universe.excluded} == {"st_status", "bse_excluded_by_policy"}

    frames = _financial_frames()
    fundamental = compute_fundamental_features(frames, CODE, AS_OF)
    trend = compute_trend_features(frames, CODE, AS_OF)
    quality = compute_quality_features(frames, CODE, AS_OF)
    daily, daily_basic = _market_frames()
    attention = compute_attention_features(
        daily.merge(daily_basic, on=["ts_code", "trade_date"]), CODE, AS_OF
    )

    assert fundamental.values["revenue_yoy"] is not None
    assert trend.values["consecutive_improvement"] == 3
    assert quality.values["quality_gate_status"] == "pass"
    assert attention.values["attention_score"] is not None
    assert fundamental.evidence["revenue_yoy"].periods == tuple(
        period.strftime("%Y%m%d") for period in pd.to_datetime(
            ["20240331", "20240630", "20240930", "20241231"]
        )
    )


def test_replay_score_and_candidate_report_are_deterministic() -> None:
    result = run_replay_frames(
        _frames(),
        as_of_date=AS_OF,
        config=ReplayConfig(
            top_n=5,
            universe=UniverseConfig(min_financial_periods=4),
        ),
    )

    assert result.status == "PASS"
    assert result.ranked["ts_code"].tolist() == [CODE]
    assert result.ranked.iloc[0]["rank"] == 1
    report = candidate_report(result, CODE)
    markdown = candidate_report_markdown(report)
    assert report["selected"] is True
    assert "Evidence and provenance" in markdown
    assert "revenue_yoy" in markdown


def test_hard_quality_reject_is_reflected_in_score() -> None:
    vector = FeatureVector(ts_code=CODE, as_of_date=AS_OF)
    vector.add("quality_score", 80.0)
    vector.add("attention_score", 60.0)
    vector.add("expectation_score", 60.0)
    vector.rejected_reasons.append("profit_dominated_by_non_recurring_items")
    vector.risk_flags.append("profit_dominated_by_non_recurring_items")

    score = score_feature_vector(vector)

    assert score.rejected is True
    assert score.turnaround_score is not None
    assert "profit_dominated_by_non_recurring_items" in score.rejected_reasons
