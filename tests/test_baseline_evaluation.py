from __future__ import annotations

import json

import pandas as pd

from ashare_turnaround.scanner.baseline_campaign import (
    project_artifact_top_n,
    run_lightweight_snapshot_campaign,
)
from ashare_turnaround.scanner.evaluation import (
    BASELINE_EVALUATION_CONTRACT_VERSION,
    EvaluationConfig,
    evaluate_scans,
    frozen_baseline_evaluation_config,
)


def _market_inputs(*, split: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH", "000300.SH", "000300.SH"],
            "trade_date": ["20250101", "20250103", "20250101", "20250103"],
            "close": [10.0, 5.0 if split else 11.0, 100.0, 102.0],
        }
    )
    calendar = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE", "SSE"],
            "cal_date": ["20250101", "20250102", "20250103"],
            "is_open": [1, 0, 1],
        }
    )
    factors = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "trade_date": ["20250101", "20250103"],
            "adj_factor": [1.0, 2.0 if split else 1.0],
        }
    )
    return daily, calendar, factors


def _scan() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH"],
            "as_of_date": ["20250101", "20250101"],
            "rank": [1, 2],
            "ranking_eligible": [False, True],
            "historical_universe_member": [True, True],
            "snapshot_id": ["snapshot", "snapshot"],
            "run_id": ["run", "run"],
            "score_config_fingerprint": ["score", "score"],
            "turnaround_score": [99.0, 80.0],
            "revenue_yoy": [0.1, 0.1],
            "profit_yoy": [0.1, 0.1],
            "margin": [0.1, 0.1],
            "cfo_cash_conversion": [0.1, 0.1],
            "fundamental_report_period": ["20241231", "20241231"],
        }
    )


def _fundamentals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600001.SH", "600001.SH", "600001.SH", "600001.SH"],
            "report_period": ["20241231", "20250331", "20250331", "20250630"],
            "actual_available_date": ["20241231", "20250430", "20250501", "20250730"],
            "disclosure_version": ["initial", "initial", "revision", "initial"],
            "revenue_yoy": [0.1, 0.2, 9.0, 0.3],
            "profit_yoy": [0.1, 0.3, 9.0, 0.4],
            "margin": [0.1, 0.15, 9.0, 0.2],
            "cfo_cash_conversion": [0.1, 0.2, 9.0, 0.3],
        }
    )


def test_baseline_config_is_frozen_before_observation() -> None:
    config = frozen_baseline_evaluation_config()

    assert config.version == BASELINE_EVALUATION_CONTRACT_VERSION
    assert config.top_n == 20
    assert config.horizons == (20, 60, 120, 250)
    assert config.benchmark_code == "000300.SH"
    assert config.transaction_cost_bps == 30.0
    assert config.require_adjustment_factor is True


def test_adjustment_aware_return_removes_split_mechanical_move() -> None:
    daily, calendar, factors = _market_inputs(split=True)
    scans = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "as_of_date": ["20250101"],
            "rank": [1],
            "historical_universe_member": [True],
        }
    )
    index = daily.loc[daily["ts_code"].eq("000300.SH")].copy()
    result = evaluate_scans(
        scans,
        daily.loc[daily["ts_code"].eq("600000.SH")],
        config=EvaluationConfig(
            version=BASELINE_EVALUATION_CONTRACT_VERSION,
            horizons=(1,),
            top_n=20,
            benchmark_code="000300.SH",
            require_adjustment_factor=True,
        ),
        index_daily=index,
        trade_calendar=calendar,
        adj_factor=factors,
    )

    row = result.market_outcomes.iloc[0]
    assert row["raw_entry_price"] == 10.0
    assert row["raw_exit_price"] == 5.0
    assert row["adjusted_entry_price"] == row["adjusted_exit_price"] == 10.0
    assert row["forward_return"] == 0.0
    assert row["benchmark_return"] == 0.02
    assert row["excess_return"] == -0.02


def test_baseline_selection_uses_ranking_eligible_and_future_reports_by_period() -> None:
    daily, calendar, factors = _market_inputs()
    index = daily.loc[daily["ts_code"].eq("000300.SH")].copy()
    exposures = pd.DataFrame(
        {
            "ts_code": ["600001.SH"],
            "trade_date": ["20250101"],
            "total_mv": [100_000.0],
            "industry": ["Bank"],
        }
    )
    stock_basic = pd.DataFrame(
        {
            "ts_code": ["600001.SH"],
            "list_date": ["20100101"],
            "delist_date": [None],
            "list_status": ["L"],
        }
    )
    selected_daily = pd.DataFrame(
        {
            "ts_code": ["600001.SH", "600001.SH"],
            "trade_date": ["20250101", "20250103"],
            "close": [10.0, 11.0],
        }
    )
    selected_factors = pd.DataFrame(
        {
            "ts_code": ["600001.SH", "600001.SH"],
            "trade_date": ["20250101", "20250103"],
            "adj_factor": [1.0, 1.0],
        }
    )
    result = evaluate_scans(
        _scan(),
        selected_daily,
        config=frozen_baseline_evaluation_config().__class__(
            **{
                **frozen_baseline_evaluation_config().declared(),
                "horizons": (1,),
            }
        ),
        stock_basic=stock_basic,
        exposures=exposures,
        fundamentals=_fundamentals(),
        index_daily=index,
        trade_calendar=calendar,
        adj_factor=selected_factors,
    )

    assert result.status == "PASS"
    assert len(result.market_outcomes) == 1
    assert result.market_outcomes.iloc[0]["ts_code"] == "600001.SH"
    next_report = result.fundamental_outcomes.loc[
        result.fundamental_outcomes["window"].eq("next_report")
    ].iloc[0]
    assert next_report["report_period"] == "20250331"
    assert next_report["disclosure_version"] == "initial"
    assert next_report["revision_count_not_used"] == 1
    assert bool(next_report["fundamental_improved"]) is True
    next_two = result.fundamental_outcomes.loc[
        result.fundamental_outcomes["window"].eq("next_two_reports")
    ].iloc[0]
    assert bool(next_two["fundamental_persistence"]) is True
    assert result.provenance["selection_outcome_separation"] is True


def test_missing_benchmark_is_reason_coded_without_absolute_return_fallback() -> None:
    daily, calendar, factors = _market_inputs()
    result = evaluate_scans(
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "as_of_date": ["20250101"],
                "rank": [1],
                "historical_universe_member": [True],
            }
        ),
        daily.loc[daily["ts_code"].eq("600000.SH")],
        config=EvaluationConfig(
            version=BASELINE_EVALUATION_CONTRACT_VERSION,
            horizons=(1,),
            benchmark_code="000300.SH",
            require_adjustment_factor=True,
        ),
        index_daily=pd.DataFrame(),
        trade_calendar=calendar,
        adj_factor=factors,
    )

    row = result.market_outcomes.iloc[0]
    assert row["forward_return"] is not None
    assert row["benchmark_return"] is None
    assert row["excess_return"] is None
    assert row["benchmark_status"] == "missing_benchmark_history"
    assert "benchmark_outcome_missing_for_some_candidates" in result.warnings


def test_lightweight_campaign_projects_and_checkpoints_without_full_json_copy(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact = artifact_root / "issue32-sample-2025-01" / "snapshots" / "20250115-ready.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "replay": {
                    "diagnostic_ranked": [
                        {
                            "ts_code": "600000.SH",
                            "rank": 1,
                            "ranking_eligible": True,
                            "turnaround_score": 80.0,
                            "snapshot_id": "snapshot",
                            "run_id": "run",
                            "score_config_fingerprint": "score",
                        },
                        {
                            "ts_code": "600001.SH",
                            "rank": 2,
                            "ranking_eligible": False,
                            "turnaround_score": 90.0,
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    frame, _ = project_artifact_top_n(
        artifact,
        target_month="2025-01",
        as_of_date="20250115",
        regime="range",
    )
    assert frame["ts_code"].tolist() == ["600000.SH"]

    schedule = tmp_path / "validation-targets.json"
    schedule.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "target_month": "2025-01",
                        "selected_trading_date": "20250115",
                        "availability_status": "AVAILABLE",
                        "regime_label": "range",
                    },
                    {
                        "target_month": "2025-02",
                        "availability_status": "UNAVAILABLE_DATA",
                        "unavailable_reason": "missing",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    result = run_lightweight_snapshot_campaign(
        schedule_path=schedule,
        artifact_root=artifact_root,
        output_dir=tmp_path / "campaign",
    )
    assert result.completed_count == 1
    assert result.reused_count == 1
    assert result.unavailable_count == 0
    assert result.pit_violation_count == 0
    assert result.scans["ts_code"].tolist() == ["600000.SH"]
    assert (tmp_path / "campaign" / "checkpoint.json").is_file()
