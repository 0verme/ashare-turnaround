from __future__ import annotations

from pathlib import Path

import pandas as pd

from ashare_turnaround.datasets.market_bootstrap import (
    bootstrap_market_data,
    build_market_bootstrap_plan,
    render_market_bootstrap_dry_run,
)
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import MarketBootstrapRunLock, MarketCheckpointStore


class FakeMarketProvider:
    def __init__(self, *, duplicate_daily: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.duplicate_daily = duplicate_daily

    def call(self, api_name: str, **params: object) -> pd.DataFrame:
        self.calls.append((api_name, dict(params)))
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 5000))
        if api_name == "trade_cal":
            rows = [
                {
                    "exchange": params["exchange"],
                    "cal_date": "20240102",
                    "is_open": 1,
                },
                {
                    "exchange": params["exchange"],
                    "cal_date": "20240103",
                    "is_open": 1,
                },
            ]
        elif api_name == "stock_basic":
            rows = (
                [
                    {
                        "ts_code": "600000.SH",
                        "symbol": "600000",
                        "name": "浦发银行",
                        "market": "主板",
                        "exchange": "SSE",
                        "list_date": "19991110",
                        "delist_date": None,
                        "list_status": params["list_status"],
                    }
                ]
                if params["list_status"] == "L"
                else []
            )
        elif api_name == "index_basic":
            rows = [
                {
                    "ts_code": "000300.SH",
                    "name": "沪深300",
                    "market": "SSE",
                    "list_date": "20050408",
                }
            ]
        elif api_name == "index_daily":
            rows = [
                {"ts_code": "000300.SH", "trade_date": "20240102", "close": 100.0},
                {"ts_code": "000300.SH", "trade_date": "20240103", "close": 101.0},
            ]
        else:
            rows = [
                {"ts_code": "600000.SH", "trade_date": "20240102", "close": 10.0},
                {"ts_code": "600000.SH", "trade_date": "20240103", "close": 11.0},
            ]
            if self.duplicate_daily and api_name == "daily":
                rows.append(rows[-1].copy())
        return pd.DataFrame(rows[offset : offset + limit])


def _objects(tmp_path: Path) -> tuple[RawParquetStore, MarketCheckpointStore]:
    data_dir = tmp_path / "data"
    return RawParquetStore(data_dir), MarketCheckpointStore(data_dir / "state" / "market.json")


def test_market_plan_uses_month_units_and_dry_run_has_no_side_effects(tmp_path) -> None:
    store, checkpoints = _objects(tmp_path)
    summary = bootstrap_market_data(
        None,
        store,
        checkpoints,
        start_date="20120101",
        end_date="20251231",
        datasets=("daily", "daily_basic", "index_daily"),
        snapshot_date="20260827",
        dry_run=True,
        workers=4,
    )

    assert summary.planned_units == 168 * 3
    assert {unit.partition_strategy for unit in summary.units} == {"month", "benchmark-month"}
    assert summary.remaining_units == summary.planned_units
    rendered = render_market_bootstrap_dry_run(summary)
    assert "remote_requests=false" in rendered
    assert "estimated_size" in rendered
    assert not (tmp_path / "data").exists()


def test_market_bootstrap_writes_atomic_units_and_resumes_only_passes(tmp_path) -> None:
    store, checkpoints = _objects(tmp_path)
    datasets = ("trade_cal", "stock_basic", "index_basic", "daily", "daily_basic", "index_daily")
    provider = FakeMarketProvider()
    first = bootstrap_market_data(
        provider,
        store,
        checkpoints,
        start_date="20240101",
        end_date="20240103",
        datasets=datasets,
        benchmark_code="000300.SH",
        snapshot_date="20240104",
        page_size=2,
        max_pages=10,
        workers=2,
        requests_per_minute=1_000_000.0,
    )

    assert not first.failures
    assert first.completed_count == 7  # two exchanges plus five other units
    assert len(checkpoints.records()) == 7
    assert len(store.parquet_files("daily")) == 1
    assert store.parquet_files("daily")[0].parts[-3:] == (
        "year=2024",
        "month=202401",
        "data.parquet",
    )
    assert {call[0] for call in provider.calls} >= {
        "trade_cal",
        "stock_basic",
        "index_basic",
        "daily",
        "daily_basic",
        "index_daily",
    }

    second_provider = FakeMarketProvider()
    second = bootstrap_market_data(
        second_provider,
        store,
        checkpoints,
        start_date="20240101",
        end_date="20240103",
        datasets=datasets,
        benchmark_code="000300.SH",
        snapshot_date="20240104",
        page_size=2,
        max_pages=10,
        workers=2,
        requests_per_minute=1_000_000.0,
        resume=True,
    )
    assert second.skipped_count == 7
    assert second_provider.calls == []


def test_duplicate_market_identity_is_partial_and_not_materialized(tmp_path) -> None:
    store, checkpoints = _objects(tmp_path)
    provider = FakeMarketProvider(duplicate_daily=True)
    summary = bootstrap_market_data(
        provider,
        store,
        checkpoints,
        start_date="20240101",
        end_date="20240103",
        datasets=("daily",),
        page_size=10,
        max_pages=2,
        workers=1,
        requests_per_minute=1_000_000.0,
    )

    assert len(summary.failures) == 1
    assert summary.results[0].status == "PARTIAL"
    assert summary.results[0].duplicate_count == 2
    assert store.parquet_files("daily") == []
    assert checkpoints.latest("daily", "2024-01")["status"] == "PARTIAL"


def test_market_run_lock_rejects_a_second_active_writer(tmp_path) -> None:
    lock_path = tmp_path / "state" / "market.lock"
    with MarketBootstrapRunLock(lock_path):
        try:
            with MarketBootstrapRunLock(lock_path):
                raise AssertionError("second writer was accepted")
        except RuntimeError as exc:
            assert "another Market" in str(exc)
    assert not lock_path.exists()


def test_plan_is_side_effect_free_and_has_explicit_benchmark() -> None:
    units = build_market_bootstrap_plan(
        "20120101",
        "20120131",
        datasets=("index_daily",),
        benchmark_code="000905.SH",
    )
    assert len(units) == 1
    assert units[0].params["ts_code"] == "000905.SH"
    assert units[0].partition_strategy == "benchmark-month"
