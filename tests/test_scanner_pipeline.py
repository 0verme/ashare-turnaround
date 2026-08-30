from __future__ import annotations

import pandas as pd

from ashare_turnaround.features import (
    compute_attention_features,
    compute_crowding_features,
    compute_fundamental_features,
    compute_low_attention_v2,
    compute_quality_features,
    compute_trend_features,
)
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.replay import ReplayConfig, ReplayDiagnostics, run_replay_frames
from ashare_turnaround.scanner.report import candidate_report, candidate_report_markdown
from ashare_turnaround.scanner.score import ablation_score_configs, score_feature_vector
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
    benchmark_dates = daily["trade_date"].tolist()
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
            "index_basic": pd.DataFrame(
                {
                    "ts_code": ["000300.SH"],
                    "name": ["CSI 300"],
                    "list_date": ["20050101"],
                }
            ),
            "index_daily": pd.DataFrame(
                {
                    "ts_code": ["000300.SH"] * len(benchmark_dates),
                    "trade_date": benchmark_dates,
                    "close": [100.0 + index * 0.02 for index in range(len(benchmark_dates))],
                }
            ),
            "trade_cal": pd.DataFrame(
                {
                    "exchange": ["SSE"] * len(benchmark_dates),
                    "cal_date": benchmark_dates,
                    "is_open": [1] * len(benchmark_dates),
                }
            ),
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

    # FY2024 has no FY2023 comparator in this fixture; adjacent Q3 is not a
    # valid YoY denominator under the comparable-period contract.
    assert fundamental.values["revenue_yoy"] is None
    assert fundamental.evidence["revenue_yoy"].reason == "missing_comparable_period"
    assert trend.values["consecutive_improvement"] is None
    assert trend.evidence["consecutive_improvement"].status == "insufficient_history"
    assert trend.evidence["consecutive_improvement"].reason == "insufficient_history"
    assert quality.values["quality_gate_status"] == "pass"
    assert attention.values["attention_score"] is not None
    assert fundamental.evidence["revenue_yoy"].periods == ("20241231",)


def test_historical_universe_keeps_a_later_delisted_security_before_delist_date() -> None:
    stock_basic = pd.DataFrame(
        {
            "ts_code": [CODE],
            "name": ["Historical member"],
            "list_status": ["D"],
            "list_date": ["20100101"],
            "delist_date": ["20250715"],
        }
    )

    before = build_investable_universe(
        stock_basic,
        as_of_date="20250630",
        financial_frames=_financial_frames(),
        config=UniverseConfig(min_financial_periods=4),
    )
    after = build_investable_universe(
        stock_basic,
        as_of_date="20250715",
        financial_frames=_financial_frames(),
        config=UniverseConfig(min_financial_periods=4),
    )

    assert before.included["ts_code"].tolist() == [CODE]
    assert after.included.empty
    assert after.excluded[0].reason == "delisted_by_as_of"


def test_workers_two_restores_order_and_matches_serial_payload() -> None:
    frames = _frames()
    codes = ("600000.SH", "000001.SZ", "300001.SZ")
    for dataset in ("income", "balancesheet", "cashflow", "daily", "daily_basic"):
        source = frames[dataset]
        frames[dataset] = pd.concat(
            [source.assign(ts_code=code) for code in codes], ignore_index=True
        )
    frames["stock_basic"] = pd.DataFrame(
        {
            "ts_code": list(codes),
            "symbol": [code.split(".")[0] for code in codes],
            "name": [f"Synthetic {index}" for index in range(len(codes))],
            "list_date": ["20100101"] * len(codes),
            "list_status": ["L"] * len(codes),
        }
    )
    config = ReplayConfig(top_n=5, universe=UniverseConfig(min_financial_periods=4))
    serial = run_replay_frames(
        frames,
        as_of_date=AS_OF,
        config=config,
        diagnostics=ReplayDiagnostics(workers=1),
    )
    parallel = run_replay_frames(
        frames,
        as_of_date=AS_OF,
        config=config,
        diagnostics=ReplayDiagnostics(workers=2, max_in_flight=2),
    )

    assert [vector.ts_code for vector in parallel.vectors] == [
        vector.ts_code for vector in serial.vectors
    ]
    assert parallel.artifact_dict() == serial.artifact_dict()
    assert parallel.deterministic_digests() == serial.deterministic_digests()


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
    assert report["metadata"]["expectation_crowding_contract_version"] == (
        "expectation-crowding-v2"
    )
    assert report["metadata"]["benchmark_id"] == "000300.SH"
    assert "expectation_penalties" in report
    assert "Evidence and provenance" in markdown
    assert "revenue_yoy" in markdown
    assert result.ranked["snapshot_id"].tolist() == [result.snapshot_id]
    assert result.ranked["historical_universe_member"].tolist() == [True]
    assert result.metadata()["config_fingerprint"] == result.config_fingerprint
    assert result.metadata()["comparable_period_contract_version"] == ("comparable-period-v1")
    assert result.metadata()["trend_contract_version"] == "turnaround-trend-v2"
    assert result.metadata()["expectation_crowding_contract_version"] == (
        "expectation-crowding-v2"
    )
    assert result.metadata()["attention_contract_version"] == "low-attention-v2.0.0"
    assert result.metadata()["benchmark_id"] == "000300.SH"
    assert result.metadata()["benchmark_source_dataset"] == "index_basic + index_daily"
    assert result.ranked.iloc[0]["comparable_period_contract_version"] == ("comparable-period-v1")
    assert result.ranked.iloc[0]["trend_contract_version"] == "turnaround-trend-v2"
    assert result.ranked.iloc[0]["expectation_crowding_contract_version"] == (
        "expectation-crowding-v2"
    )
    assert result.scores[0].trend_contract_version == "turnaround-trend-v2"
    assert report["trend_contract_version"] == "turnaround-trend-v2"
    assert "Trend contract" in markdown


def test_merged_contract_stack_keeps_trend_attention_and_crowding_provenance() -> None:
    frames = _frames()
    daily, daily_basic = _market_frames()
    market = daily.merge(daily_basic, on=["ts_code", "trade_date"])
    vector = compute_fundamental_features(_financial_frames(), CODE, AS_OF)
    vector.merge(compute_trend_features(_financial_frames(), CODE, AS_OF))
    vector.merge(compute_attention_features(market, CODE, AS_OF))
    vector.merge(
        compute_crowding_features(
            market,
            CODE,
            AS_OF,
            benchmark_frame=frames["index_daily"],
            benchmark_definition_frame=frames["index_basic"],
        )
    )
    vector.merge(compute_low_attention_v2(market, CODE, AS_OF))

    assert vector.comparable_period_contract_version == "comparable-period-v1"
    assert vector.trend_contract_version == "turnaround-trend-v2"
    assert vector.feature_contract_versions["expectation_crowding"] == (
        "expectation-crowding-v2"
    )
    assert vector.metadata["low_attention_v2"]["attention_contract_version"] == (
        "low-attention-v2.0.0"
    )
    assert vector.metadata["expectation_crowding_v2"]["contract_version"] == (
        "expectation-crowding-v2"
    )
    assert vector.benchmark_metadata["benchmark_id"] == "000300.SH"
    assert "abnormal_volume" in vector.values
    assert "low_attention_v2_abnormal_volume" in vector.values
    assert vector.evidence["low_attention_v2_abnormal_volume"].feature == (
        "low_attention_v2_abnormal_volume"
    )

    score = score_feature_vector(vector)
    assert score.input_metadata["trend_contract_version"] == "turnaround-trend-v2"
    assert score.input_metadata["attention_contract_version"] == "low-attention-v2.0.0"
    assert score.input_metadata["expectation_crowding_contract_version"] == (
        "expectation-crowding-v2"
    )
    assert score.input_metadata["benchmark_id"] == "000300.SH"


def test_ablation_score_configs_share_data_snapshot_but_have_distinct_run_ids() -> None:
    variants = ablation_score_configs()
    fundamental = run_replay_frames(
        _frames(),
        as_of_date=AS_OF,
        config=ReplayConfig(
            top_n=5,
            universe=UniverseConfig(min_financial_periods=4),
            score=variants["fundamental_only"],
        ),
    )
    expectation = run_replay_frames(
        _frames(),
        as_of_date=AS_OF,
        config=ReplayConfig(
            top_n=5,
            universe=UniverseConfig(min_financial_periods=4),
            score=variants["expectation_added"],
        ),
    )

    assert fundamental.snapshot_id == expectation.snapshot_id
    assert fundamental.run_id != expectation.run_id
    assert fundamental.config_fingerprint != expectation.config_fingerprint
    assert (
        fundamental.ranked.iloc[0]["score_config_fingerprint"]
        == variants["fundamental_only"].fingerprint
    )


def test_replay_snapshot_id_ignores_observations_unavailable_after_as_of() -> None:
    frames = _frames()
    original = run_replay_frames(
        frames,
        as_of_date=AS_OF,
        config=ReplayConfig(universe=UniverseConfig(min_financial_periods=4)),
    )
    with_future = {name: frame.copy() for name, frame in frames.items()}
    with_future["daily"] = pd.concat(
        [
            with_future["daily"],
            pd.DataFrame(
                {
                    "ts_code": [CODE],
                    "trade_date": ["20250701"],
                    "close": [99.0],
                    "vol": [1.0],
                    "pct_chg": [50.0],
                }
            ),
        ],
        ignore_index=True,
    ).sample(frac=1.0, random_state=7)

    repeated = run_replay_frames(
        with_future,
        as_of_date=AS_OF,
        config=ReplayConfig(universe=UniverseConfig(min_financial_periods=4)),
    )

    assert repeated.snapshot_id == original.snapshot_id
    assert repeated.run_id == original.run_id


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
