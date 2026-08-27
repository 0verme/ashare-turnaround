from __future__ import annotations

import pandas as pd

from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.query.duckdb import DuckDBQuery
from ashare_turnaround.storage.parquet import RawParquetStore


def _write_daily_basic(data_dir) -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH", "000001.SZ"],
            "trade_date": ["20240102", "20240103", "20240102"],
            "pe": [10.0, 20.0, 5.0],
        }
    )
    RawParquetStore(data_dir).write("daily_basic", frame, get_dataset_spec("daily_basic"))


def test_duckdb_reads_filters_sorts_and_aggregates_parquet(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _write_daily_basic(data_dir)

    with DuckDBQuery(data_dir) as query:
        filtered = query.read_parquet(
            "daily_basic",
            where="ts_code = ?",
            parameters=["600000.SH"],
            order_by="trade_date",
        )
        smoke = query.smoke_queries("daily_basic", ts_code="600000.SH")
        percentile = query.historical_pe_percentile("600000.SH")

    assert len(filtered) == 2
    assert filtered["trade_date"].astype(str).tolist() == ["20240102", "20240103"]
    assert len(smoke["aggregate"]) == 2
    assert len(smoke["window"]) == 3
    assert percentile["pe_historical_percentile"].tolist() == [0.0, 1.0]


def test_duckdb_can_register_a_canonical_view(tmp_path) -> None:
    with DuckDBQuery(tmp_path / "data") as query:
        frame = pd.DataFrame({"report_period": pd.to_datetime(["2025-12-31"]), "value": [1.0]})
        query.register_frame_as_view("income_pit", frame)
        result = query.execute("SELECT * FROM income_pit")

    assert result.to_dict("records")[0]["value"] == 1.0
