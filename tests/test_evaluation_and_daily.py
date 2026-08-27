from __future__ import annotations

import json

import pandas as pd

from ashare_turnaround import __main__
from ashare_turnaround.datasets.sync import sync_daily
from ashare_turnaround.scanner.daily import latest_completed_trading_date
from ashare_turnaround.scanner.evaluation import EvaluationConfig, evaluate_scans
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import SyncStateStore


def test_latest_completed_trading_date_uses_open_calendar_only() -> None:
    calendar = pd.DataFrame(
        {
            "cal_date": ["20250627", "20250628", "20250630"],
            "is_open": [1, 0, 1],
        }
    )

    assert latest_completed_trading_date(calendar, today="20250630") == "20250630"
    assert latest_completed_trading_date(calendar, today="20250628") == "20250627"


def test_evaluation_uses_only_prices_strictly_after_as_of() -> None:
    scans = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "as_of_date": ["20250627"],
            "rank": [1],
        }
    )
    daily = pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * 4,
            "trade_date": ["20250626", "20250627", "20250630", "20250701"],
            "close": [10.0, 11.0, 12.0, 13.0],
        }
    )

    result = evaluate_scans(
        scans,
        daily,
        config=EvaluationConfig(horizons=(1, 2)),
    )

    observations = result.observations.sort_values("horizon")
    assert observations["forward_return"].tolist() == [1 / 11, 2 / 11]
    assert observations["end_date"].tolist() == ["20250630", "20250701"]


def test_evaluation_returns_empty_when_every_frozen_row_is_rejected() -> None:
    scans = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "as_of_date": ["20250627"],
            "rank": [1],
            "rejected": [True],
        }
    )

    result = evaluate_scans(scans, pd.DataFrame())

    assert result.status == "EMPTY"
    assert result.observations.empty
    assert result.warnings == ("no_eligible_scan_rows", "rejected_scan_rows_excluded")


class _DailyProvider:
    def __init__(self, *, is_open: int = 1) -> None:
        self.is_open = is_open
        self.calls: list[str] = []
        self.stock_statuses: list[str] = []

    def call(self, dataset: str, **params: object) -> pd.DataFrame:
        self.calls.append(dataset)
        if dataset == "trade_cal":
            return pd.DataFrame(
                {"exchange": ["SSE"], "cal_date": ["20250630"], "is_open": [self.is_open]}
            )
        if dataset == "daily":
            return pd.DataFrame(
                {"ts_code": ["600000.SH"], "trade_date": ["20250630"], "close": [10.0]}
            )
        if dataset == "daily_basic":
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH"],
                    "trade_date": ["20250630"],
                    "amount": [1000.0],
                    "turnover_rate": [1.0],
                }
            )
        if dataset == "stock_basic":
            status = str(params["list_status"])
            self.stock_statuses.append(status)
            code = {"L": "600000.SH", "D": "600001.SH", "P": "600002.SH"}[status]
            return pd.DataFrame(
                {
                    "ts_code": [code],
                    "symbol": [code.split(".")[0]],
                    "name": [f"Status {status}"],
                    "list_status": [status],
                    "list_date": ["20100101"],
                    "delist_date": ["20250701" if status == "D" else None],
                }
            )
        raise AssertionError(f"unexpected dataset: {dataset}")


def test_daily_sync_is_idempotent_and_does_not_fetch_market_data_on_holiday(tmp_path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    state = SyncStateStore(data_dir / "state" / "sync-log.json")
    provider = _DailyProvider()

    summary = sync_daily(
        provider,
        store,
        state,
        requested_date="20250630",
        datasets=("daily", "daily_basic"),
    )

    assert summary.status == "success"
    assert summary.effective_date == "20250630"
    assert provider.calls == ["trade_cal", "daily", "daily_basic"]
    assert len(store.read("daily")) == 1
    assert {record["status"] for record in state.records()} == {"success"}

    holiday_provider = _DailyProvider(is_open=0)
    holiday = sync_daily(
        holiday_provider,
        store,
        state,
        requested_date="20250630",
        datasets=("daily",),
    )

    assert holiday.effective_date is None
    assert holiday_provider.calls == ["trade_cal"]
    assert next(value for value in holiday.results if value.dataset == "daily").status == "not_due"


def test_daily_sync_preserves_listed_delisted_and_prelisting_reference_rows(tmp_path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    state = SyncStateStore(data_dir / "state" / "sync-log.json")
    provider = _DailyProvider()

    summary = sync_daily(
        provider,
        store,
        state,
        requested_date="20250630",
        datasets=("stock_basic",),
    )

    assert summary.status == "success"
    assert provider.stock_statuses == ["L", "D", "P"]
    reference = store.read("stock_basic")
    assert set(reference["list_status"]) == {"L", "D", "P"}
    assert set(reference["ts_code"]) == {"600000.SH", "600001.SH", "600002.SH"}


def test_evaluation_reports_aligned_benchmark_delisting_exposure_and_fundamentals() -> None:
    as_of = "20250627"
    target = "20250701"
    scans = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH"],
            "as_of_date": [as_of, as_of],
            "rank": [1, 2],
            "snapshot_id": ["pit-snapshot", "pit-snapshot"],
            "run_id": ["run-a", "run-a"],
            "score_version": ["score-v1", "score-v1"],
            "score_config_fingerprint": ["score-config", "score-config"],
            "historical_universe_member": [True, True],
            "revenue_yoy": [0.10, 0.05],
            "net_profit_yoy": [0.08, 0.04],
            "operating_profit_yoy": [0.06, 0.03],
        }
    )
    daily = pd.DataFrame(
        [
            {"ts_code": code, "trade_date": date, "close": close}
            for code, values in {
                "600000.SH": [(as_of, 10.0), ("20250630", 11.0), (target, 12.0)],
                "600001.SH": [(as_of, 20.0), ("20250630", 18.0)],
                "000300.SH": [(as_of, 100.0), ("20250630", 101.0), (target, 102.0)],
            }.items()
            for date, close in values
        ]
    )
    stock_basic = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH"],
            "list_status": ["L", "D"],
            "list_date": ["20100101", "20100101"],
            "delist_date": [None, target],
            "industry": ["Bank", "Industrial"],
        }
    )
    exposures = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH"],
            "trade_date": [as_of, as_of],
            "total_mv": [1_000_000.0, 2_000_000.0],
        }
    )
    fundamentals = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600001.SH"],
            "f_ann_date": ["20250630", "20250630"],
            "end_date": ["20250630", "20250630"],
            "revenue_yoy": [0.20, 0.06],
            "net_profit_yoy": [0.18, 0.05],
            "operating_profit_yoy": [0.16, 0.04],
        }
    )

    result = evaluate_scans(
        scans,
        daily,
        config=EvaluationConfig(
            horizons=(2,),
            top_n=2,
            benchmark_code="000300.SH",
            transaction_cost_bps=10.0,
        ),
        stock_basic=stock_basic,
        exposures=exposures,
        fundamentals=fundamentals,
    )

    assert result.status == "PASS"
    assert result.warnings == ()
    assert result.provenance["scan_snapshot_ids"] == ["pit-snapshot"]
    observations = result.observations.set_index("ts_code")
    assert observations.loc["600000.SH", "end_date"] == target
    assert observations.loc["600000.SH", "benchmark_return"] == 0.02
    assert observations.loc["600001.SH", "observation_status"] == "delisted_assumption"
    assert observations.loc["600001.SH", "forward_return"] == -1.0
    assert observations["fundamental_improved"].astype(bool).all()
    summary = result.summary.iloc[0]
    assert summary["delisted_count"] == 1
    assert summary["historical_universe_missing_count"] == 0
    assert summary["industry_exposure"] == {"Bank": 0.5, "Industrial": 0.5}
    assert summary["market_cap_exposure"]["count"] == 2


def test_evaluate_cli_persists_declared_configuration_and_provenance(tmp_path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    as_of = "20250627"
    store.write_incremental(
        "daily",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600000.SH", "000300.SH", "000300.SH"],
                "trade_date": [as_of, "20250630", as_of, "20250630"],
                "close": [10.0, 11.0, 100.0, 101.0],
            }
        ),
    )
    store.write_incremental(
        "daily_basic",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": [as_of],
                "total_mv": [1_000_000.0],
            }
        ),
    )
    store.write_incremental(
        "stock_basic",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "symbol": ["600000"],
                "name": ["Example"],
                "list_status": ["L"],
                "list_date": ["20100101"],
                "industry": ["Bank"],
            }
        ),
    )
    scan_path = tmp_path / "scan.parquet"
    pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "as_of_date": [as_of],
            "rank": [1],
            "snapshot_id": ["pit-cli"],
            "run_id": ["run-cli"],
            "score_config_fingerprint": ["score-cli"],
            "historical_universe_member": [True],
            "revenue_yoy": [0.10],
            "net_profit_yoy": [0.10],
            "operating_profit_yoy": [0.10],
        }
    ).to_parquet(scan_path, index=False)
    fundamentals_path = tmp_path / "fundamentals.parquet"
    pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "f_ann_date": ["20250630"],
            "end_date": ["20250630"],
            "revenue_yoy": [0.20],
            "net_profit_yoy": [0.20],
            "operating_profit_yoy": [0.20],
        }
    ).to_parquet(fundamentals_path, index=False)
    report_path = tmp_path / "evaluation.json"

    exit_code = __main__.main(
        [
            "evaluate",
            "--scans",
            str(scan_path),
            "--data-dir",
            str(data_dir),
            "--benchmark-code",
            "000300.SH",
            "--horizons",
            "1",
            "--fundamentals",
            str(fundamentals_path),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["configuration"]["holding_convention"].startswith("as_of_close")
    assert payload["provenance"]["scan_snapshot_ids"] == ["pit-cli"]
    assert payload["summary"][0]["fundamental_improvement_rate"] == 1.0
