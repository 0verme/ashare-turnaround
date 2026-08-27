from __future__ import annotations

import pandas as pd

from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.storage.inventory import build_coverage_report
from ashare_turnaround.storage.parquet import RawParquetStore


def test_incremental_daily_write_merges_identity_and_keeps_latest_row(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    spec = get_dataset_spec("daily")

    store.write_incremental(
        "daily",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": ["20250102"],
                "close": [10.0],
            }
        ),
        spec,
        retrieved_at="2025-01-03T00:00:00+00:00",
    )
    store.write_incremental(
        "daily",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "000001.SZ"],
                "trade_date": ["20250102", "20250102"],
                "close": [11.0, 20.0],
            }
        ),
        spec,
        retrieved_at="2025-01-04T00:00:00+00:00",
    )

    loaded = store.read("daily").sort_values("ts_code").reset_index(drop=True)
    assert loaded["ts_code"].tolist() == ["000001.SZ", "600000.SH"]
    assert loaded.loc[loaded["ts_code"].eq("600000.SH"), "close"].item() == 11.0
    assert loaded["retrieved_at"].tolist() == [
        "2025-01-04T00:00:00+00:00",
        "2025-01-04T00:00:00+00:00",
    ]


def test_coverage_report_detects_missing_open_trade_date_partition(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    store.write(
        "trade_cal",
        pd.DataFrame(
            {
                "exchange": ["SSE", "SSE"],
                "cal_date": ["20250102", "20250103"],
                "is_open": [1, 1],
            }
        ),
        get_dataset_spec("trade_cal"),
    )
    store.write(
        "daily",
        pd.DataFrame(
            {"ts_code": ["600000.SH"], "trade_date": ["20250102"], "close": [10.0]}
        ),
        get_dataset_spec("daily"),
    )

    report = build_coverage_report(tmp_path / "data", as_of_date="20250103")
    coverage = next(value for value in report.datasets if value.dataset == "daily")

    assert coverage.status == "FAIL"
    assert coverage.missing_partitions == ("trade_date=20250103",)
    assert coverage.min_date == "20250102"
    assert coverage.max_date == "20250102"
