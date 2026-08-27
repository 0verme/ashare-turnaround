from __future__ import annotations

import pandas as pd

from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.storage.parquet import RawParquetStore


def test_partitioned_parquet_is_atomic_in_shape_and_idempotent(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "000001.SZ"],
            "trade_date": ["20240102", "20240103"],
            "pe": [8.0, 12.0],
        }
    )
    spec = get_dataset_spec("daily_basic")

    first = store.write(
        "daily_basic",
        frame,
        spec,
        retrieved_at="2026-01-01T00:00:00+00:00",
        source="tushare-compatible",
    )
    second = store.write(
        "daily_basic",
        frame,
        spec,
        retrieved_at="2026-01-02T00:00:00+00:00",
        source="tushare-compatible",
    )

    assert len(first) == 2
    assert len(second) == 2
    assert len(store.parquet_files("daily_basic")) == 2
    loaded = store.read("daily_basic").sort_values("trade_date")
    assert len(loaded) == 2
    assert set(loaded["source"]) == {"tushare-compatible"}
    assert set(loaded["retrieved_at"]) == {"2026-01-02T00:00:00+00:00"}
    assert all("trade_date=" in str(path) for path in store.parquet_files("daily_basic"))


def test_financial_rows_partition_by_report_year(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20231231", "20241231"],
            "ann_date": ["20240320", "20250320"],
        }
    )

    store.write(
        "income",
        frame,
        get_dataset_spec("income"),
        retrieved_at="2026-08-27T00:00:00+00:00",
    )
    paths = {str(path) for path in store.parquet_files("income")}
    assert any("year=2023" in path for path in paths)
    assert any("year=2024" in path for path in paths)


def test_fina_mainbz_raw_duplicate_identity_is_preserved(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20241231", "20241231"],
            "bz_item": ["main", "main"],
            "curr_type": ["CNY", "CNY"],
            "update_flag": ["1", "1"],
            "bz_sales": [10.0, 11.0],
        }
    )

    store.write("fina_mainbz", frame, get_dataset_spec("fina_mainbz"))

    loaded = store.read("fina_mainbz")
    assert len(loaded) == 2
    assert sorted(loaded["bz_sales"].tolist()) == [10.0, 11.0]


def test_trade_cal_uses_one_low_cardinality_year_partition(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE", "SSE"],
            "cal_date": ["20240101", "20240102", "20241231"],
            "is_open": [1, 1, 0],
        }
    )

    stored = store.write("trade_cal", frame, get_dataset_spec("trade_cal"))

    assert len(stored) == 1
    assert len(store.parquet_files("trade_cal")) == 1
    assert "year=2024" in str(stored[0].path)
    assert len(store.read("trade_cal")) == 3
