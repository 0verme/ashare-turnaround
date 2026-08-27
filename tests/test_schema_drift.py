from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.query.duckdb import DuckDBQuery
from ashare_turnaround.storage.parquet import RawParquetStore


def test_duckdb_union_by_name_handles_columns_added_in_a_later_partition(tmp_path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    spec = get_dataset_spec("daily_basic")
    store.write(
        "daily_basic",
        pd.DataFrame(
            {"ts_code": ["600000.SH"], "trade_date": ["20240102"], "pe": [10.0]}
        ),
        spec,
    )
    store.write(
        "daily_basic",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "trade_date": ["20240103"],
                "pe": [11.0],
                "pb": [1.2],
            }
        ),
        spec,
    )

    with DuckDBQuery(data_dir) as query:
        result = query.read_parquet(
            "daily_basic",
            columns=["trade_date", "pe", "pb"],
            order_by="trade_date",
        )

    assert result["trade_date"].astype(str).tolist() == ["20240102", "20240103"]
    assert pd.isna(result.iloc[0]["pb"])
    assert result.iloc[1]["pb"] == pytest.approx(1.2)


def test_schema_inspection_does_not_load_all_rows_into_pandas(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / "data"
    RawParquetStore(data_dir).write(
        "daily_basic",
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600000.SH"],
                "trade_date": ["20240102", "20240103"],
                "pe": [10.0, 11.0],
            }
        ),
        get_dataset_spec("daily_basic"),
    )

    with DuckDBQuery(data_dir) as query:
        monkeypatch.setattr(
            query.store,
            "read",
            lambda *_: (_ for _ in ()).throw(AssertionError("full pandas scan")),
        )
        result = query.historical_pe_percentile("600000.SH")

    assert len(result) == 2


def test_order_by_and_where_reject_statement_injection(tmp_path) -> None:
    data_dir = tmp_path / "data"
    RawParquetStore(data_dir).write(
        "daily_basic",
        pd.DataFrame(
            {"ts_code": ["600000.SH"], "trade_date": ["20240102"], "pe": [10.0]}
        ),
        get_dataset_spec("daily_basic"),
    )

    with DuckDBQuery(data_dir) as query:
        with pytest.raises(ValueError, match="unsafe SQL order"):
            query.read_parquet("daily_basic", order_by="trade_date; DROP TABLE x")
        with pytest.raises(ValueError, match="unsafe SQL where"):
            query.read_parquet("daily_basic", where="1=1; DROP TABLE x")
