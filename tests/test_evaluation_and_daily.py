from __future__ import annotations

import pandas as pd

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


class _DailyProvider:
    def __init__(self, *, is_open: int = 1) -> None:
        self.is_open = is_open
        self.calls: list[str] = []

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
