"""In-process DuckDB access to local Parquet data."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..storage.parquet import RawParquetStore

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ORDER_TERM = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*|\d+)"
    r"(?:\s+(?:ASC|DESC))?"
    r"(?:\s+NULLS\s+(?:FIRST|LAST))?$",
    re.IGNORECASE,
)


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def _order_by(value: str) -> str:
    terms = [term.strip() for term in value.split(",")]
    if not terms or any(not term or not _ORDER_TERM.fullmatch(term) for term in terms):
        raise ValueError(f"unsafe SQL order expression: {value!r}")
    return ", ".join(terms)


def _where_fragment(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("where must be a non-empty SQL fragment")
    if any(marker in value for marker in (";", "--", "/*", "*/")):
        raise ValueError("unsafe SQL where fragment")
    return value


class DuckDBQuery:
    """A lightweight connection wrapper; no DuckDB service is required."""

    def __init__(self, data_dir: str | Path, database: str | Path = ":memory:") -> None:
        self.store = RawParquetStore(data_dir)
        self.database = str(database)
        self.connection = duckdb.connect(self.database)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DuckDBQuery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> pd.DataFrame:
        return self.connection.execute(sql, list(parameters or [])).fetchdf()

    def read_parquet(
        self,
        dataset: str,
        *,
        columns: Sequence[str] | None = None,
        where: str | None = None,
        parameters: Sequence[Any] | None = None,
        order_by: str | None = None,
    ) -> pd.DataFrame:
        """Read one dataset using DuckDB's recursive ``read_parquet`` table function."""

        if not self.store.parquet_files(dataset):
            return pd.DataFrame()
        selected_columns = "*"
        if columns:
            selected_columns = ", ".join(_identifier(column) for column in columns)
        sql = (
            f"SELECT {selected_columns} FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=false)"
        )
        query_parameters: list[Any] = [self.store.parquet_glob(dataset)]
        if where:
            sql += f" WHERE {_where_fragment(where)}"
            query_parameters.extend(parameters or [])
        elif parameters:
            raise ValueError("parameters require a where clause")
        if order_by:
            sql += f" ORDER BY {_order_by(order_by)}"
        return self.connection.execute(sql, query_parameters).fetchdf()

    def historical_pe_percentile(self, ts_code: str | None = None) -> pd.DataFrame:
        """Run a small window-function example over ``daily_basic.pe``."""

        dataset = "daily_basic"
        files = self.store.parquet_files(dataset)
        if not files:
            return pd.DataFrame()
        available_columns = set(self.store.schema_columns(dataset))
        required = {"ts_code", "trade_date", "pe"}
        if not required.issubset(available_columns):
            return pd.DataFrame()

        sql = """
            SELECT
                ts_code,
                trade_date,
                pe,
                PERCENT_RANK() OVER (
                    PARTITION BY ts_code ORDER BY pe
                ) AS pe_historical_percentile
            FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
            WHERE pe IS NOT NULL
        """
        parameters: list[Any] = [self.store.parquet_glob(dataset)]
        if ts_code is not None:
            sql += " AND ts_code = ?"
            parameters.append(ts_code)
        sql += " ORDER BY ts_code, trade_date"
        return self.connection.execute(sql, parameters).fetchdf()

    def smoke_queries(
        self,
        dataset: str,
        *,
        ts_code: str | None = None,
        report_period: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Exercise filter, sort, aggregate and window operations on one dataset."""

        if not self.store.parquet_files(dataset):
            return {
                "filtered": pd.DataFrame(),
                "sorted": pd.DataFrame(),
                "aggregate": pd.DataFrame(),
                "window": pd.DataFrame(),
            }
        columns = set(self.store.schema_columns(dataset))
        predicates: list[str] = []
        parameters: list[Any] = []
        if ts_code is not None and "ts_code" in columns:
            predicates.append("ts_code = ?")
            parameters.append(ts_code)
        if report_period is not None and "end_date" in columns:
            predicates.append("end_date = ?")
            parameters.append(report_period)
        where = " AND ".join(predicates) if predicates else None
        filtered = self.read_parquet(dataset, where=where, parameters=parameters, order_by="1")

        sortable = (
            "trade_date"
            if "trade_date" in columns
            else "end_date"
            if "end_date" in columns
            else None
        )
        sorted_frame = (
            self.read_parquet(dataset, where=where, parameters=parameters, order_by=sortable)
            if sortable
            else filtered
        )
        group_column = "ts_code" if "ts_code" in columns else None
        if group_column:
            aggregate_sql = (
                f"SELECT {group_column}, COUNT(*) AS rows "
                f"FROM read_parquet(?, union_by_name=true, hive_partitioning=false) "
                f"GROUP BY {group_column} ORDER BY {group_column}"
            )
            aggregate = self.execute(aggregate_sql, [self.store.parquet_glob(dataset)])
            window_sql = (
                f"SELECT {group_column}, "
                f"COUNT(*) OVER (PARTITION BY {group_column}) AS partition_rows "
                "FROM read_parquet(?, union_by_name=true, hive_partitioning=false)"
            )
            window = self.execute(window_sql, [self.store.parquet_glob(dataset)])
        else:
            aggregate = pd.DataFrame()
            window = pd.DataFrame()
        return {
            "filtered": filtered,
            "sorted": sorted_frame,
            "aggregate": aggregate,
            "window": window,
        }

    def register_frame_as_view(self, view_name: str, frame: pd.DataFrame) -> None:
        """Register a pandas frame as a temporary canonical view for inspection."""

        safe_name = _identifier(view_name)
        registration_name = f"_{safe_name}_frame"
        self.connection.register(registration_name, frame)
        self.connection.execute(
            f"CREATE OR REPLACE TEMP VIEW {safe_name} AS SELECT * FROM {registration_name}"
        )
