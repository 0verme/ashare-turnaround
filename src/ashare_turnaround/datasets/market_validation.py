"""Coverage and integrity verification for the Market / Reference corpus.

The verifier is deliberately independent of scanner/replay execution.  It uses
Parquet metadata and bounded DuckDB aggregates for large daily datasets, then
writes an auditable machine-readable report with explicit PIT limitations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..dates import normalize_date_series
from ..storage.parquet import RawParquetStore
from ..storage.state import MarketCheckpointStore
from .market_bootstrap import (
    DEFAULT_BENCHMARK_CODE,
    DEFAULT_MARKET_BOOTSTRAP_DATASETS,
    DEFAULT_MARKET_EXCHANGES,
    MARKET_BOOTSTRAP_DATASETS,
    build_market_bootstrap_plan,
    default_market_end_date,
)

_DATE_DATASETS = {"daily", "daily_basic", "index_daily"}
_MIN_SYMBOL_COUNT = 500
_EXPECTED_CROSS_SECTION_YEARS = (2013, 2016, 2018, 2020, 2022, 2024, 2025)
_SAFE_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# These columns are added by RawParquetStore after the source-frame schema is
# checkpointed.  Exclude them when reconciling a checkpoint to its stored file.
_STORAGE_PROVENANCE_COLUMNS = frozenset(
    {
        "retrieved_at",
        "source",
        "source_api",
        "reference_snapshot_date",
        "reference_semantics",
    }
)


@dataclass(frozen=True, slots=True)
class MarketDatasetCoverage:
    dataset: str
    expected_range: str
    actual_range: str
    expected_units: int
    pass_units: int
    failed_units: int
    row_count: int
    size_bytes: int
    symbol_count: int
    expected_trading_days: int
    present_trading_days: int
    missing_trading_days: tuple[str, ...]
    duplicate_rows: int
    unreadable_files: tuple[str, ...]
    schema_drift: bool
    tiny_partitions: tuple[str, ...]
    checkpoint_status: str
    status: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CrossSectionCoverage:
    trade_date: str
    daily_symbols: int
    daily_basic_symbols: int
    join_symbols: int
    daily_only: int
    daily_basic_only: int
    join_coverage: float
    status: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoricalSymbolCoverage:
    ts_code: str
    list_date: str | None
    exchange: str | None
    market: str | None
    daily_earliest: str | None
    daily_latest: str | None
    daily_rows: int
    daily_basic_earliest: str | None
    daily_basic_latest: str | None
    daily_basic_rows: int


@dataclass(frozen=True, slots=True)
class BenchmarkCoverage:
    benchmark_code: str
    earliest_date: str | None
    latest_date: str | None
    expected_sessions: int
    actual_sessions: int
    missing_sessions: tuple[str, ...]
    sample_trade_date: str | None
    sample_stock_code: str | None
    stock_return_20d: float | None
    benchmark_return_20d: float | None
    excess_return_20d: float | None
    status: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ForwardWindowCoverage:
    horizon: int
    available_as_of_count: int
    right_censored_count: int
    earliest_eligible_as_of: str | None
    latest_eligible_as_of: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ReferencePITFinding:
    dataset: str
    field: str
    source: str
    availability: str
    historical_semantics: str
    pit_confidence: str
    notes: str


@dataclass(frozen=True, slots=True)
class MarketIntegrity:
    unreadable_files: tuple[str, ...]
    zero_byte_files: tuple[str, ...]
    temporary_files: tuple[str, ...]
    schema_drift_datasets: tuple[str, ...]
    duplicate_rows: dict[str, int]
    checkpoint_mismatch: tuple[str, ...]
    unexpected_tiny_partitions: tuple[str, ...]

    @property
    def status(self) -> str:
        if (
            self.unreadable_files
            or self.zero_byte_files
            or self.temporary_files
            or self.schema_drift_datasets
            or any(self.duplicate_rows.values())
            or self.checkpoint_mismatch
            or self.unexpected_tiny_partitions
        ):
            return "FAIL"
        return "PASS"


@dataclass(frozen=True, slots=True)
class MarketCoverageReport:
    generated_at: str
    data_dir: str
    start_date: str
    end_date: str
    benchmark_code: str
    datasets: tuple[MarketDatasetCoverage, ...]
    cross_section: tuple[CrossSectionCoverage, ...]
    historical_symbols: tuple[HistoricalSymbolCoverage, ...]
    benchmark: BenchmarkCoverage
    forward_windows: tuple[ForwardWindowCoverage, ...]
    reference_pit: tuple[ReferencePITFinding, ...]
    integrity: MarketIntegrity
    status: str
    warnings: tuple[str, ...] = ()
    remaining_gaps: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "READY"


def _normalized_date(value: str | date | datetime | pd.Timestamp, name: str) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid {name}: {value!r}")
    return pd.Timestamp(parsed).normalize().strftime("%Y%m%d")


def _schema_hash(columns: list[str] | tuple[str, ...]) -> str:
    payload = json.dumps(sorted(map(str, columns)), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _duckdb_query(
    data_dir: Path,
    dataset: str,
    query: str,
    parameters: list[Any] | None = None,
) -> pd.DataFrame:
    store = RawParquetStore(data_dir)
    if not store.parquet_files(dataset):
        return pd.DataFrame()
    connection = duckdb.connect(":memory:")
    try:
        sql = query.replace(
            "__PARQUET__", "read_parquet(?, union_by_name=true, hive_partitioning=false)"
        )
        return connection.execute(sql, [store.parquet_glob(dataset), *(parameters or [])]).fetchdf()
    finally:
        connection.close()


def _date_counts(data_dir: Path, dataset: str) -> dict[str, int]:
    frame = _duckdb_query(
        data_dir,
        dataset,
        """
        SELECT trade_date AS raw_date, COUNT(*) AS row_count
        FROM __PARQUET__
        WHERE trade_date IS NOT NULL
        GROUP BY trade_date
        """,
    )
    if frame.empty:
        return {}
    parsed = normalize_date_series(frame["raw_date"])
    result: dict[str, int] = {}
    for value, count in zip(parsed, frame["row_count"], strict=False):
        if pd.notna(value):
            key = pd.Timestamp(value).strftime("%Y%m%d")
            result[key] = result.get(key, 0) + int(count)
    return result


def _calendar_frame(data_dir: Path, start: str, end: str) -> pd.DataFrame:
    store = RawParquetStore(data_dir)
    if not store.parquet_files("trade_cal"):
        return pd.DataFrame()
    frame = store.read("trade_cal")
    required = {"cal_date", "is_open"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    dates = normalize_date_series(frame["cal_date"])
    result = frame.loc[
        dates.notna() & dates.ge(pd.Timestamp(start)) & dates.le(pd.Timestamp(end))
    ].copy()
    result["_cal_date"] = normalize_date_series(result["cal_date"])
    result["_is_open"] = pd.to_numeric(result["is_open"], errors="coerce")
    return result


def _expected_open_dates(calendar_frame: pd.DataFrame) -> set[str]:
    if calendar_frame.empty:
        return set()
    values = calendar_frame.loc[calendar_frame["_is_open"].eq(1), "_cal_date"]
    return {pd.Timestamp(value).strftime("%Y%m%d") for value in values.dropna().drop_duplicates()}


def _expected_calendar_dates(calendar_frame: pd.DataFrame, exchange: str) -> set[str]:
    if calendar_frame.empty:
        return set()
    values = calendar_frame
    if "exchange" in values.columns:
        exchange_values = values["exchange"].astype("string").str.upper()
        selected = values.loc[exchange_values.eq(exchange.upper()), "_cal_date"]
        if not selected.empty:
            values = values.loc[exchange_values.eq(exchange.upper())]
    return {
        pd.Timestamp(value).strftime("%Y%m%d")
        for value in values["_cal_date"].dropna().drop_duplicates()
    }


def _duplicate_rows(data_dir: Path, dataset: str) -> int:
    keys = {
        "daily": ("ts_code", "trade_date"),
        "daily_basic": ("ts_code", "trade_date"),
        "index_daily": ("ts_code", "trade_date"),
        "trade_cal": ("exchange", "cal_date"),
        "stock_basic": ("ts_code",),
        "index_basic": ("ts_code",),
        "namechange": ("ts_code", "start_date", "name", "change_reason"),
        "suspend_d": ("ts_code", "trade_date", "suspend_type"),
    }
    selected = keys.get(dataset)
    if selected is None or not RawParquetStore(data_dir).parquet_files(dataset):
        return 0
    if any(not _SAFE_SQL_IDENTIFIER.fullmatch(key) for key in selected):
        return 0
    group = ", ".join(selected)
    result = _duckdb_query(
        data_dir,
        dataset,
        f"""
        SELECT COALESCE(SUM(group_count), 0) AS duplicate_rows
        FROM (
            SELECT {group}, COUNT(*) AS group_count
            FROM __PARQUET__
            GROUP BY {group}
            HAVING COUNT(*) > 1
        )
        """,
    )
    return int(result.iloc[0]["duplicate_rows"]) if not result.empty else 0


def _file_metadata(
    data_dir: Path, dataset: str
) -> tuple[list[Path], list[str], set[str], int, int]:
    directory = data_dir / "raw" / dataset
    paths = sorted(directory.rglob("*.parquet")) if directory.exists() else []
    readable: list[Path] = []
    unreadable: list[str] = []
    schemas: set[str] = set()
    rows = 0
    size = 0
    for path in paths:
        try:
            parquet = pq.ParquetFile(path)
            rows += int(parquet.metadata.num_rows)
            size += path.stat().st_size
            schemas.add(_schema_hash(tuple(str(name) for name in parquet.schema_arrow.names)))
            readable.append(path)
        except (OSError, ValueError, RuntimeError, pa.ArrowException) as exc:
            unreadable.append(f"{path}: {type(exc).__name__}: {exc}")
    return readable, unreadable, schemas, rows, size


def _bad_files(
    data_dir: Path, dataset: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    directory = data_dir / "raw" / dataset
    if not directory.exists():
        return (), (), ()
    zero: list[str] = []
    temporary: list[str] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.stat().st_size == 0:
            zero.append(str(path))
        if path.name.endswith((".tmp", ".partial")) or ".tmp." in path.name:
            temporary.append(str(path))
    return tuple(zero), tuple(temporary), ()


def _latest_snapshot_date(checkpoints: MarketCheckpointStore, data_dir: Path | None = None) -> str:
    values: list[str] = []
    if data_dir is not None:
        for dataset in ("stock_basic", "index_basic"):
            for path in RawParquetStore(data_dir).parquet_files(dataset):
                try:
                    columns = set(pq.ParquetFile(path).schema_arrow.names)
                    if "reference_snapshot_date" not in columns:
                        continue
                    value = pd.read_parquet(path, columns=["reference_snapshot_date"])
                    values.extend(
                        normalize_date_series(value["reference_snapshot_date"])
                        .dropna()
                        .dt.strftime("%Y%m%d")
                        .tolist()
                    )
                except (OSError, ValueError, RuntimeError, pa.ArrowException):
                    continue
    if values:
        return max(values)
    for dataset in ("stock_basic", "index_basic"):
        for unit in checkpoints.latest_for_dataset(dataset):
            match = re.search(r"snapshot:(\d{8})", unit)
            if match:
                values.append(match.group(1))
    return max(values) if values else datetime.now(UTC).strftime("%Y%m%d")


def _checkpoint_file_mismatches(
    data_dir: Path,
    dataset: str,
    unit: Any,
    checkpoint: dict[str, Any],
) -> tuple[str, ...]:
    """Reconcile durable checkpoint metadata with the named Parquet unit."""

    expected_storage_path = "/".join((*unit.storage_parts, "data.parquet"))
    mismatches: list[str] = []
    if str(checkpoint.get("source_api", "")) != str(unit.source_api):
        mismatches.append(
            f"{dataset}:{unit.unit}:source_api={checkpoint.get('source_api')!r}:"
            f"expected={unit.source_api!r}"
        )
    if str(checkpoint.get("storage_path", "")) != expected_storage_path:
        mismatches.append(
            f"{dataset}:{unit.unit}:storage_path={checkpoint.get('storage_path')!r}:"
            f"expected={expected_storage_path!r}"
        )
    for checkpoint_field, unit_field in (
        ("requested_start", "expected_start"),
        ("requested_end", "expected_end"),
    ):
        observed = checkpoint.get(checkpoint_field)
        expected = getattr(unit, unit_field)
        if observed != expected:
            mismatches.append(
                f"{dataset}:{unit.unit}:{checkpoint_field}={observed!r}:"
                f"expected={expected!r}"
            )

    path = RawParquetStore(data_dir).unit_file(dataset, unit.storage_parts)
    try:
        parquet = pq.ParquetFile(path)
        actual_rows = int(parquet.metadata.num_rows)
        actual_columns = tuple(str(name) for name in parquet.schema_arrow.names)
    except (OSError, TypeError, ValueError, RuntimeError, pa.ArrowException) as exc:
        return (
            *mismatches,
            f"{dataset}:{unit.unit}:checkpoint_file_metadata_error={type(exc).__name__}",
        )

    try:
        checkpoint_rows = int(checkpoint["row_count"])
    except (KeyError, TypeError, ValueError):
        mismatches.append(f"{dataset}:{unit.unit}:invalid_checkpoint_row_count")
    else:
        if checkpoint_rows != actual_rows:
            mismatches.append(
                f"{dataset}:{unit.unit}:row_count={checkpoint_rows}:actual={actual_rows}"
            )
    if actual_rows == 0:
        mismatches.append(f"{dataset}:{unit.unit}:stored_file_is_empty")

    expected_schema = str(checkpoint.get("schema_hash") or "")
    actual_schema = _schema_hash(
        tuple(name for name in actual_columns if name not in _STORAGE_PROVENANCE_COLUMNS)
    )
    if not expected_schema:
        mismatches.append(f"{dataset}:{unit.unit}:missing_checkpoint_schema_hash")
    elif expected_schema != actual_schema:
        mismatches.append(
            f"{dataset}:{unit.unit}:schema_hash={expected_schema}:actual={actual_schema}"
        )

    try:
        checkpoint_duplicates = int(checkpoint["duplicate_count"])
    except (KeyError, TypeError, ValueError):
        mismatches.append(f"{dataset}:{unit.unit}:invalid_checkpoint_duplicate_count")
    else:
        if checkpoint_duplicates != 0:
            mismatches.append(
                f"{dataset}:{unit.unit}:checkpoint_duplicate_count={checkpoint_duplicates}"
            )
    return tuple(mismatches)


def _checkpoint_info(
    data_dir: Path,
    dataset: str,
    units: list[Any],
    checkpoints: MarketCheckpointStore,
) -> tuple[int, int, str, tuple[str, ...]]:
    passed = 0
    failed = 0
    mismatch: list[str] = []
    statuses: set[str] = set()
    store = RawParquetStore(data_dir)
    for unit in units:
        latest = checkpoints.latest(dataset, unit.unit)
        status = str(latest.get("status", "UNKNOWN")).upper() if latest else "UNKNOWN"
        statuses.add(status)
        path_exists = store.unit_file(dataset, unit.storage_parts).is_file()
        if status == "PASS" and path_exists and latest is not None:
            unit_mismatches = _checkpoint_file_mismatches(data_dir, dataset, unit, latest)
            if unit_mismatches:
                mismatch.extend(unit_mismatches)
            else:
                passed += 1
        else:
            if status in {"FAILED", "PARTIAL", "UNKNOWN_EMPTY"}:
                failed += 1
            mismatch.append(f"{dataset}:{unit.unit}:status={status}:file={path_exists}")
    if not units:
        checkpoint_status = "UNKNOWN"
    elif passed == len(units):
        checkpoint_status = "COMPLETE"
    elif statuses:
        checkpoint_status = "PARTIAL"
    else:
        checkpoint_status = "UNKNOWN"
    return passed, failed, checkpoint_status, tuple(mismatch)


def _actual_range(values: set[str]) -> str:
    if not values:
        return "-"
    return f"{min(values)}..{max(values)}"


def _target_dates_for_dataset(
    dataset: str,
    calendar_frame: pd.DataFrame,
    start: str,
    end: str,
) -> set[str]:
    if dataset in _DATE_DATASETS:
        return _expected_open_dates(calendar_frame)
    if dataset == "trade_cal":
        return _expected_calendar_dates(calendar_frame, "SSE")
    return set()


def _tiny_expected_partitions(
    data_dir: Path,
    dataset: str,
    units: list[Any],
    *,
    min_rows: int = 1000,
) -> tuple[str, ...]:
    if dataset not in _DATE_DATASETS:
        return ()
    # The benchmark has one row per session, so monthly files with tens of
    # rows are expected rather than tiny-file failures.  Stock-market monthly
    # partitions should contain a broad cross-section.
    threshold = 1000 if dataset in {"daily", "daily_basic"} else 1
    store = RawParquetStore(data_dir)
    tiny: list[str] = []
    for unit in units:
        path = store.unit_file(dataset, unit.storage_parts)
        if not path.is_file():
            continue
        try:
            rows = int(pq.ParquetFile(path).metadata.num_rows)
        except (OSError, ValueError, RuntimeError, pa.ArrowException):
            continue
        if rows < threshold:
            tiny.append(f"{path} rows={rows}")
    return tuple(tiny)


def _dataset_coverage(
    data_dir: Path,
    dataset: str,
    units: list[Any],
    *,
    start: str,
    end: str,
    calendar_frame: pd.DataFrame,
    checkpoints: MarketCheckpointStore,
) -> tuple[MarketDatasetCoverage, tuple[str, ...]]:
    paths, unreadable, schemas, row_count, size_bytes = _file_metadata(data_dir, dataset)
    zero, temporary, _ = _bad_files(data_dir, dataset)
    date_counts = _date_counts(data_dir, dataset) if dataset in _DATE_DATASETS else {}
    expected_dates = _target_dates_for_dataset(dataset, calendar_frame, start, end)
    present_dates = (
        set(date_counts).intersection(expected_dates) if expected_dates else set(date_counts)
    )
    missing_dates = tuple(sorted(expected_dates.difference(present_dates)))
    if dataset == "trade_cal":
        # The new range unit stores both open and closed dates.  Use the SSE
        # calendar for the date-level gate; the per-exchange units are checked
        # separately through their checkpoints.
        raw = _duckdb_query(
            data_dir,
            dataset,
            "SELECT cal_date AS raw_date FROM __PARQUET__ WHERE cal_date IS NOT NULL",
        )
        parsed = (
            normalize_date_series(raw["raw_date"])
            if not raw.empty
            else pd.Series(dtype="datetime64[ns]")
        )
        present_dates = {
            pd.Timestamp(value).strftime("%Y%m%d")
            for value in parsed.dropna()
            if pd.Timestamp(start) <= pd.Timestamp(value) <= pd.Timestamp(end)
        }
        missing_dates = tuple(sorted(expected_dates.difference(present_dates)))
    passed, failed, checkpoint_status, checkpoint_mismatch = _checkpoint_info(
        data_dir, dataset, units, checkpoints
    )
    tiny = _tiny_expected_partitions(data_dir, dataset, units)
    warnings: list[str] = []
    if missing_dates:
        warnings.append("missing_trading_days")
    if unreadable:
        warnings.append("unreadable_parquet")
    if zero:
        warnings.append("zero_byte_file")
    if temporary:
        warnings.append("tmp_or_partial_file")
    duplicate = _duplicate_rows(data_dir, dataset)
    if duplicate:
        warnings.append(f"duplicate_identity_rows={duplicate}")
    schema_drift = len(schemas) > 1
    if schema_drift:
        warnings.append("schema_drift")
    if tiny:
        warnings.append("unexpected_tiny_partitions")
    if checkpoint_mismatch:
        warnings.append("checkpoint_file_mismatch")
    # Stock reference is complete as a current snapshot but not a historical
    # status snapshot.  The distinction is intentional and reaches the final
    # readiness verdict as a documented UNSUPPORTED_PIT limitation.  It must
    # still fail ordinary integrity gates such as duplicate identities.
    integrity_clean = not (
        unreadable or zero or temporary or duplicate or schema_drift or tiny or checkpoint_mismatch
    )
    if dataset == "stock_basic" and checkpoint_status == "COMPLETE" and integrity_clean:
        status = "UNSUPPORTED_PIT"
    elif not paths:
        status = "UNKNOWN"
    elif checkpoint_status == "UNKNOWN":
        status = "UNKNOWN"
    elif (
        missing_dates
        or unreadable
        or zero
        or temporary
        or duplicate
        or schema_drift
        or tiny
        or checkpoint_mismatch
    ):
        status = "FAIL"
    elif checkpoint_status == "PARTIAL":
        status = "PARTIAL"
    else:
        status = "COMPLETE"
    target_range = set(present_dates)
    if dataset in {"namechange", "suspend_d"} and paths:
        field = "start_date" if dataset == "namechange" else "trade_date"
        raw = _duckdb_query(
            data_dir,
            dataset,
            f"SELECT {field} AS raw_date FROM __PARQUET__ WHERE {field} IS NOT NULL",
        )
        parsed = (
            normalize_date_series(raw["raw_date"])
            if not raw.empty
            else pd.Series(dtype="datetime64[ns]")
        )
        target_range = {
            pd.Timestamp(value).strftime("%Y%m%d")
            for value in parsed.dropna()
            if pd.Timestamp(start) <= pd.Timestamp(value) <= pd.Timestamp(end)
        }
    if dataset == "stock_basic":
        snapshot = _latest_snapshot_date(checkpoints, data_dir)
        actual = snapshot if paths else "-"
        expected_text = f"snapshot@{snapshot}"
    elif dataset == "index_basic":
        snapshot = _latest_snapshot_date(checkpoints, data_dir)
        actual = snapshot if paths else "-"
        expected_text = f"snapshot@{snapshot}"
    else:
        actual = _actual_range(target_range)
        expected_text = f"{start}..{end}"
    symbols = 0
    if dataset in _DATE_DATASETS:
        result = _duckdb_query(
            data_dir,
            dataset,
            """
            SELECT COUNT(DISTINCT ts_code) AS symbol_count
            FROM __PARQUET__
            WHERE ts_code IS NOT NULL
            """,
        )
        symbols = int(result.iloc[0]["symbol_count"]) if not result.empty else 0
    elif dataset in {"stock_basic", "index_basic", "namechange", "suspend_d"}:
        result = _duckdb_query(
            data_dir,
            dataset,
            "SELECT COUNT(DISTINCT ts_code) AS symbol_count "
            "FROM __PARQUET__ WHERE ts_code IS NOT NULL",
        )
        symbols = int(result.iloc[0]["symbol_count"]) if not result.empty else 0
    coverage = MarketDatasetCoverage(
        dataset=dataset,
        expected_range=expected_text,
        actual_range=actual,
        expected_units=len(units),
        pass_units=passed,
        failed_units=failed,
        row_count=row_count,
        size_bytes=size_bytes,
        symbol_count=symbols,
        expected_trading_days=len(expected_dates),
        present_trading_days=len(present_dates),
        missing_trading_days=missing_dates,
        duplicate_rows=duplicate,
        unreadable_files=tuple(unreadable),
        schema_drift=schema_drift,
        tiny_partitions=tiny,
        checkpoint_status=checkpoint_status,
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return coverage, (*checkpoint_mismatch, *zero, *temporary)


def _symbols_on_date(data_dir: Path, dataset: str, trade_date: str) -> set[str]:
    alternatives = (
        trade_date,
        f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
        f"{trade_date}.0",
    )
    frame = _duckdb_query(
        data_dir,
        dataset,
        """
        SELECT DISTINCT ts_code
        FROM __PARQUET__
        WHERE CAST(trade_date AS VARCHAR) IN (?, ?, ?)
          AND ts_code IS NOT NULL
        """,
        list(alternatives),
    )
    return set(frame["ts_code"].dropna().astype(str)) if not frame.empty else set()


def _sample_trade_dates(calendar_frame: pd.DataFrame) -> tuple[str, ...]:
    if calendar_frame.empty:
        return ()
    opened = calendar_frame.loc[calendar_frame["_is_open"].eq(1)].copy()
    opened["_year"] = opened["_cal_date"].dt.year
    values: list[str] = []
    for year in _EXPECTED_CROSS_SECTION_YEARS:
        group = opened.loc[opened["_year"].eq(year)].sort_values("_cal_date")
        if not group.empty:
            values.append(group.iloc[0]["_cal_date"].strftime("%Y%m%d"))
    return tuple(values)


def _cross_section(data_dir: Path, dates: tuple[str, ...]) -> tuple[CrossSectionCoverage, ...]:
    values: list[CrossSectionCoverage] = []
    for trade_date in dates:
        daily = _symbols_on_date(data_dir, "daily", trade_date)
        basic = _symbols_on_date(data_dir, "daily_basic", trade_date)
        join = daily.intersection(basic)
        denominator = max(len(daily), len(basic))
        coverage = len(join) / denominator if denominator else 0.0
        warnings: list[str] = []
        if min(len(daily), len(basic)) < _MIN_SYMBOL_COUNT:
            warnings.append("abnormally_low_symbol_count")
        if denominator and coverage < 0.98:
            warnings.append("low_join_coverage")
        status = "PASS" if not warnings else "FAIL"
        values.append(
            CrossSectionCoverage(
                trade_date=trade_date,
                daily_symbols=len(daily),
                daily_basic_symbols=len(basic),
                join_symbols=len(join),
                daily_only=len(daily - basic),
                daily_basic_only=len(basic - daily),
                join_coverage=coverage,
                status=status,
                warnings=tuple(warnings),
            )
        )
    return tuple(values)


def _aggregate_symbols(data_dir: Path, dataset: str) -> pd.DataFrame:
    return _duckdb_query(
        data_dir,
        dataset,
        """
        SELECT ts_code, MIN(trade_date) AS earliest, MAX(trade_date) AS latest, COUNT(*) AS rows
        FROM __PARQUET__
        WHERE ts_code IS NOT NULL AND trade_date IS NOT NULL
        GROUP BY ts_code
        """,
    )


def _reference_frame(data_dir: Path) -> pd.DataFrame:
    store = RawParquetStore(data_dir)
    if not store.parquet_files("stock_basic"):
        return pd.DataFrame()
    frame = store.read("stock_basic")
    if "ts_code" not in frame.columns:
        return pd.DataFrame()
    if "reference_snapshot_date" in frame.columns:
        snapshot = normalize_date_series(frame["reference_snapshot_date"])
        frame = frame.assign(_snapshot=snapshot).sort_values("_snapshot", na_position="first")
    return frame.drop_duplicates("ts_code", keep="last")


def _date_or_none(value: object) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).strftime("%Y%m%d")


def _historical_symbols(data_dir: Path) -> tuple[HistoricalSymbolCoverage, ...]:
    daily = _aggregate_symbols(data_dir, "daily")
    basic = _aggregate_symbols(data_dir, "daily_basic")
    if daily.empty:
        return ()
    for frame in (daily, basic):
        if not frame.empty:
            frame["_earliest"] = normalize_date_series(frame["earliest"])
            frame["_latest"] = normalize_date_series(frame["latest"])
    reference = _reference_frame(data_dir)
    ref_by_code = (
        reference.set_index("ts_code").to_dict(orient="index") if not reference.empty else {}
    )
    daily_by_code = daily.set_index("ts_code").to_dict(orient="index")
    basic_by_code = basic.set_index("ts_code").to_dict(orient="index") if not basic.empty else {}
    selected: list[str] = []

    def add(code: object) -> None:
        value = str(code)
        if value and value != "nan" and value in daily_by_code and value not in selected:
            selected.append(value)

    oldest = daily.sort_values(["_earliest", "ts_code"], na_position="last", kind="stable")
    newest_listing = daily.sort_values(
        ["_earliest", "ts_code"], ascending=[False, True], na_position="last", kind="stable"
    )
    if not oldest.empty:
        add(oldest.iloc[0]["ts_code"])
    if not newest_listing.empty:
        add(newest_listing.iloc[0]["ts_code"])
    if not reference.empty:
        ref = reference.copy()
        ref["_list"] = normalize_date_series(ref.get("list_date", pd.Series(index=ref.index)))
        for column in ("exchange", "market"):
            if column not in ref.columns:
                continue
            groups = ref.dropna(subset=[column]).groupby(column, sort=True)
            for _, group in groups:
                ordered = group.sort_values(["_list", "ts_code"], na_position="last", kind="stable")
                before = len(selected)
                for code in ordered["ts_code"]:
                    add(code)
                    if len(selected) > before:
                        break
                if len(selected) >= 16:
                    break
            if len(selected) >= 16:
                break
    for code in sorted(daily_by_code):
        add(code)
        if len(selected) >= 16:
            break

    rows: list[HistoricalSymbolCoverage] = []
    for code in selected[:16]:
        d = daily_by_code[code]
        b = basic_by_code.get(code, {})
        ref = ref_by_code.get(code, {})
        rows.append(
            HistoricalSymbolCoverage(
                ts_code=code,
                list_date=_date_or_none(ref.get("list_date")),
                exchange=str(ref.get("exchange")) if pd.notna(ref.get("exchange")) else None,
                market=str(ref.get("market")) if pd.notna(ref.get("market")) else None,
                daily_earliest=_date_or_none(d.get("_earliest")),
                daily_latest=_date_or_none(d.get("_latest")),
                daily_rows=int(d.get("rows", 0)),
                daily_basic_earliest=_date_or_none(b.get("_earliest")),
                daily_basic_latest=_date_or_none(b.get("_latest")),
                daily_basic_rows=int(b.get("rows", 0)),
            )
        )
    return tuple(rows)


def _price_series(data_dir: Path, dataset: str, code: str) -> pd.DataFrame:
    frame = _duckdb_query(
        data_dir,
        dataset,
        """
        SELECT trade_date, close
        FROM __PARQUET__
        WHERE ts_code = ? AND close IS NOT NULL
        """,
        [code],
    )
    if frame.empty:
        return frame
    frame["_date"] = normalize_date_series(frame["trade_date"])
    frame["_close"] = pd.to_numeric(frame["close"], errors="coerce")
    return (
        frame.loc[frame["_date"].notna() & frame["_close"].notna()]
        .sort_values("_date")
        .drop_duplicates("_date", keep="last")
        .reset_index(drop=True)
    )


def _benchmark(
    data_dir: Path,
    benchmark_code: str,
    expected_dates: set[str],
    symbols: tuple[HistoricalSymbolCoverage, ...],
) -> BenchmarkCoverage:
    frame = _price_series(data_dir, "index_daily", benchmark_code)
    if frame.empty:
        return BenchmarkCoverage(
            benchmark_code,
            None,
            None,
            len(expected_dates),
            0,
            tuple(sorted(expected_dates)),
            None,
            None,
            None,
            None,
            None,
            "UNKNOWN",
            ("benchmark_history_missing",),
        )
    dates = {value.strftime("%Y%m%d") for value in frame["_date"]}
    target = dates.intersection(expected_dates)
    missing = tuple(sorted(expected_dates.difference(target)))
    stock_code: str | None = None
    stock_return: float | None = None
    benchmark_return: float | None = None
    excess: float | None = None
    sample_date: str | None = None
    # Prefer the dynamically selected oldest symbol, then find a common 21-
    # session window.  No fixed security is used for this gate.
    for candidate in symbols:
        stock = _price_series(data_dir, "daily", candidate.ts_code)
        if stock.empty:
            continue
        stock_dates = {value.strftime("%Y%m%d") for value in stock["_date"]}
        common = sorted(stock_dates.intersection(dates).intersection(expected_dates))
        if len(common) < 21:
            continue
        sample_date = common[-1]
        stock_window = stock.loc[stock["_date"].dt.strftime("%Y%m%d").isin(common)].tail(21)
        benchmark_window = frame.loc[frame["_date"].dt.strftime("%Y%m%d").isin(common)].tail(21)
        if len(stock_window) < 21 or len(benchmark_window) < 21:
            continue
        first_stock = float(stock_window.iloc[0]["_close"])
        last_stock = float(stock_window.iloc[-1]["_close"])
        first_benchmark = float(benchmark_window.iloc[0]["_close"])
        last_benchmark = float(benchmark_window.iloc[-1]["_close"])
        if first_stock == 0 or first_benchmark == 0:
            continue
        stock_code = candidate.ts_code
        stock_return = last_stock / first_stock - 1.0
        benchmark_return = last_benchmark / first_benchmark - 1.0
        excess = stock_return - benchmark_return
        break
    warnings: list[str] = []
    if missing:
        warnings.append("missing_benchmark_sessions")
    if stock_return is None:
        warnings.append("benchmark_excess_sample_unavailable")
    status = "PASS" if not missing and stock_return is not None else "FAIL"
    return BenchmarkCoverage(
        benchmark_code=benchmark_code,
        earliest_date=min(target) if target else None,
        latest_date=max(target) if target else None,
        expected_sessions=len(expected_dates),
        actual_sessions=len(target),
        missing_sessions=missing,
        sample_trade_date=sample_date,
        sample_stock_code=stock_code,
        stock_return_20d=stock_return,
        benchmark_return_20d=benchmark_return,
        excess_return_20d=excess,
        status=status,
        warnings=tuple(warnings),
    )


def _forward_windows(dates: set[str]) -> tuple[ForwardWindowCoverage, ...]:
    ordered = sorted(dates)
    values: list[ForwardWindowCoverage] = []
    for horizon in (20, 60, 120, 250):
        eligible = ordered[:-horizon] if len(ordered) > horizon else []
        right_censored = min(horizon, len(ordered)) if ordered else 0
        values.append(
            ForwardWindowCoverage(
                horizon=horizon,
                available_as_of_count=len(eligible),
                right_censored_count=right_censored,
                earliest_eligible_as_of=eligible[0] if eligible else None,
                latest_eligible_as_of=eligible[-1] if eligible else None,
                status="PASS" if eligible else "UNKNOWN",
            )
        )
    return tuple(values)


def reference_pit_findings() -> tuple[ReferencePITFinding, ...]:
    """Return the frozen field-level honesty contract for this corpus."""

    return (
        ReferencePITFinding(
            "stock_basic",
            "ts_code/symbol",
            "stock_basic",
            "historical identifier",
            "stable identifier",
            "PIT_SAFE",
            "not a status observation",
        ),
        ReferencePITFinding(
            "stock_basic",
            "list_date",
            "stock_basic",
            "historical event field",
            "listing boundary",
            "PIT_SAFE",
            "usable only for list_date <= as_of",
        ),
        ReferencePITFinding(
            "stock_basic",
            "delist_date",
            "stock_basic",
            "historical event field",
            "delisting boundary",
            "PIT_SAFE",
            "usable only when source supplies a non-null date",
        ),
        ReferencePITFinding(
            "stock_basic",
            "exchange",
            "stock_basic",
            "current snapshot",
            "current reference classification",
            "SNAPSHOT_ONLY",
            "not a dated historical reassignment log",
        ),
        ReferencePITFinding(
            "stock_basic",
            "name",
            "stock_basic",
            "current snapshot",
            "historical name/ST state unavailable",
            "UNSUPPORTED_PIT",
            "never use the current display name as historical ST/name state",
        ),
        ReferencePITFinding(
            "stock_basic",
            "status/list_status",
            "stock_basic",
            "current snapshot",
            "historical listing/status state unavailable",
            "UNSUPPORTED_PIT",
            "do not project the current status into past dates",
        ),
        ReferencePITFinding(
            "stock_basic",
            "industry",
            "stock_basic",
            "current snapshot",
            "historical industry state unavailable",
            "UNSUPPORTED_PIT",
            "do not project the current industry into past dates",
        ),
        ReferencePITFinding(
            "stock_basic",
            "board/market",
            "stock_basic",
            "current snapshot",
            "historical board/security category unavailable",
            "UNSUPPORTED_PIT",
            "do not project today's board into past dates",
        ),
        ReferencePITFinding(
            "stock_basic",
            "is_hs/actual-control",
            "stock_basic",
            "current snapshot",
            "current holding/control classification",
            "SNAPSHOT_ONLY",
            "not historical PIT state",
        ),
        ReferencePITFinding(
            "namechange",
            "name/start_date/end_date",
            "namechange",
            "partial/unstable source response",
            "historical name interval not approved",
            "UNSUPPORTED_PIT",
            "opt-in only: the compatible endpoint exposes no stable source identity; "
            "do not deduplicate repeated rows into a history",
        ),
        ReferencePITFinding(
            "namechange",
            "change_reason",
            "namechange",
            "partial/unstable source response",
            "historical reason evidence not approved",
            "UNSUPPORTED_PIT",
            "source identity is unstable; does not by itself prove an eligibility rule",
        ),
        ReferencePITFinding(
            "suspend_d",
            "trade_date/suspend_type",
            "suspend_d",
            "dated market observation",
            "suspension on the trade date",
            "PIT_BY_TRADE_DATE",
            "no separate publication timestamp is exposed",
        ),
        ReferencePITFinding(
            "index_basic",
            "all fields",
            "index_basic",
            "current snapshot",
            "benchmark definition snapshot",
            "SNAPSHOT_ONLY",
            "benchmark identity is explicit; definition history is not claimed",
        ),
    )


def verify_market_corpus(
    data_dir: str | Path,
    *,
    start_date: str | date | datetime | pd.Timestamp = "20120101",
    end_date: str | date | datetime | pd.Timestamp | None = None,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    datasets: tuple[str, ...] = DEFAULT_MARKET_BOOTSTRAP_DATASETS,
    exchanges: tuple[str, ...] = DEFAULT_MARKET_EXCHANGES,
    snapshot_date: str | date | datetime | pd.Timestamp | None = None,
    checkpoint_path: str | Path | None = None,
) -> MarketCoverageReport:
    """Verify market/reference coverage without running scanner/replay code."""

    data_path = Path(data_dir).expanduser()
    start = _normalized_date(start_date, "start_date")
    end = _normalized_date(end_date or default_market_end_date(), "end_date")
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")
    selected = tuple(dict.fromkeys(datasets))
    unknown = sorted(set(selected).difference(MARKET_BOOTSTRAP_DATASETS))
    if unknown:
        raise ValueError(f"unknown market dataset(s): {', '.join(unknown)}")
    checkpoint = MarketCheckpointStore(
        checkpoint_path or data_path / "state" / "market-bootstrap-checkpoints.json"
    )
    snap = (
        _normalized_date(snapshot_date, "snapshot_date")
        if snapshot_date
        else _latest_snapshot_date(checkpoint, data_path)
    )
    plan = build_market_bootstrap_plan(
        start,
        end,
        datasets=selected,
        benchmark_code=benchmark_code,
        exchanges=exchanges,
        snapshot_date=snap,
    )
    units_by_dataset: dict[str, list[Any]] = {dataset: [] for dataset in selected}
    for unit in plan:
        units_by_dataset.setdefault(unit.dataset, []).append(unit)
    calendar_frame = _calendar_frame(data_path, start, end)
    coverage_values: list[MarketDatasetCoverage] = []
    integrity_mismatch: list[str] = []
    for dataset in selected:
        coverage, mismatch = _dataset_coverage(
            data_path,
            dataset,
            units_by_dataset.get(dataset, []),
            start=start,
            end=end,
            calendar_frame=calendar_frame,
            checkpoints=checkpoint,
        )
        coverage_values.append(coverage)
        integrity_mismatch.extend(mismatch)

    sample_dates = _sample_trade_dates(calendar_frame)
    cross = (
        _cross_section(data_path, sample_dates)
        if {"daily", "daily_basic"}.issubset(selected)
        else ()
    )
    symbols = _historical_symbols(data_path) if "daily" in selected else ()
    expected_open = _expected_open_dates(calendar_frame)
    benchmark = (
        _benchmark(data_path, benchmark_code, expected_open, symbols)
        if "index_daily" in selected
        else BenchmarkCoverage(
            benchmark_code,
            None,
            None,
            len(expected_open),
            0,
            tuple(sorted(expected_open)),
            None,
            None,
            None,
            None,
            None,
            "UNKNOWN",
            ("index_daily_not_requested",),
        )
    )
    windows = _forward_windows(set(_date_counts(data_path, "daily"))) if "daily" in selected else ()

    unreadable: list[str] = []
    zero: list[str] = []
    temporary: list[str] = []
    schema_drift: list[str] = []
    duplicates: dict[str, int] = {}
    tiny: list[str] = []
    for coverage in coverage_values:
        unreadable.extend(coverage.unreadable_files)
        duplicates[coverage.dataset] = coverage.duplicate_rows
        if coverage.schema_drift:
            schema_drift.append(coverage.dataset)
        tiny.extend(coverage.tiny_partitions)
        dataset_dir = data_path / "raw" / coverage.dataset
        if dataset_dir.exists():
            for path in dataset_dir.rglob("*"):
                if not path.is_file():
                    continue
                if path.stat().st_size == 0:
                    zero.append(str(path))
                if path.name.endswith((".tmp", ".partial")) or ".tmp." in path.name:
                    temporary.append(str(path))
    integrity = MarketIntegrity(
        unreadable_files=tuple(dict.fromkeys(unreadable)),
        zero_byte_files=tuple(dict.fromkeys(zero)),
        temporary_files=tuple(dict.fromkeys(temporary)),
        schema_drift_datasets=tuple(dict.fromkeys(schema_drift)),
        duplicate_rows={key: value for key, value in duplicates.items() if value},
        checkpoint_mismatch=tuple(dict.fromkeys(integrity_mismatch)),
        unexpected_tiny_partitions=tuple(dict.fromkeys(tiny)),
    )
    remaining_gaps: list[str] = []
    stock_reference = next(
        (value for value in coverage_values if value.dataset == "stock_basic"), None
    )
    if stock_reference is not None and stock_reference.status == "UNSUPPORTED_PIT":
        remaining_gaps.append(
            "stock_basic name/status/industry/board fields are current-snapshot-only; "
            "do not project them into historical replay"
        )
    if "namechange" not in selected:
        remaining_gaps.append(
            "namechange is not in the core download: the compatible endpoint probe "
            "returned repeated identical rows without a stable exposed source identity; "
            "historical ST/name state remains unsupported"
        )
    else:
        namechange = next(
            (value for value in coverage_values if value.dataset == "namechange"), None
        )
        if namechange is not None and namechange.status != "COMPLETE":
            remaining_gaps.append("namechange historical ST/name state is not complete/PIT-safe")

    warnings: list[str] = []
    if any(value.status == "FAIL" for value in coverage_values):
        warnings.append("dataset_coverage_failure")
    if any(value.status in {"UNKNOWN", "PARTIAL"} for value in coverage_values):
        warnings.append("dataset_coverage_incomplete")
    if any(value.status == "UNSUPPORTED_PIT" for value in coverage_values):
        warnings.append("reference_current_snapshot_limits_historical_pit")
    if any(value.status == "FAIL" for value in cross):
        warnings.append("cross_section_coverage_failure")
    if benchmark.status != "PASS":
        warnings.append("benchmark_coverage_failure")
    if integrity.status != "PASS":
        warnings.append("raw_integrity_failure")
    critical = {
        "trade_cal",
        "daily",
        "daily_basic",
        "index_daily",
        "stock_basic",
        "index_basic",
        "suspend_d",
    }
    selected_names = set(selected)
    selected_critical = [value for value in coverage_values if value.dataset in critical]
    if not critical.issubset(selected_names):
        warnings.append("critical_dataset_not_requested")
    complete_enough = (
        critical.issubset(selected_names)
        and len(selected_critical) == len(critical)
        and all(value.status in {"COMPLETE", "UNSUPPORTED_PIT"} for value in selected_critical)
    )
    cross_ok = bool(cross) and all(value.status == "PASS" for value in cross)
    status = (
        "READY"
        if complete_enough
        and cross_ok
        and benchmark.status == "PASS"
        and integrity.status == "PASS"
        else "NOT_READY"
    )
    return MarketCoverageReport(
        generated_at=datetime.now(UTC).isoformat(),
        data_dir=str(data_path),
        start_date=start,
        end_date=end,
        benchmark_code=str(benchmark_code).upper(),
        datasets=tuple(coverage_values),
        cross_section=tuple(cross),
        historical_symbols=symbols,
        benchmark=benchmark,
        forward_windows=windows,
        reference_pit=reference_pit_findings(),
        integrity=integrity,
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
        remaining_gaps=tuple(dict.fromkeys(remaining_gaps)),
    )


def market_coverage_dict(report: MarketCoverageReport) -> dict[str, Any]:
    return asdict(report)


def write_market_coverage_report(report: MarketCoverageReport, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(market_coverage_dict(report), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _format_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TiB"


def render_market_coverage_markdown(report: MarketCoverageReport) -> str:
    lines = [
        "# Market / Reference historical corpus coverage",
        "",
        f"- Generated at (UTC): `{report.generated_at}`",
        f"- Data directory: `{report.data_dir}`",
        f"- Research window: `{report.start_date}..{report.end_date}`",
        f"- Main benchmark: `{report.benchmark_code}`",
        f"- Verdict: **`{report.status}`**",
        (
            "- This report performs coverage/integrity checks only; it does not run "
            "scanner, replay, evaluation, ablation, or candidate reporting."
        ),
        "",
        "## Dataset completion matrix",
        "",
        (
            "| Dataset | Expected range | Actual range | Expected units | PASS units | "
            "Rows | Size | Missing trading days | Status |"
        ),
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for value in report.datasets:
        lines.append(
            f"| {value.dataset} | {value.expected_range} | {value.actual_range} | "
            f"{value.expected_units} | {value.pass_units} | {value.row_count:,} | "
            f"{_format_size(value.size_bytes)} | "
            f"{len(value.missing_trading_days)} | {value.status} |"
        )
    lines.extend(
        [
            "",
            "## Daily cross-sectional coverage gate",
            "",
            (
                "| Trade date | daily symbols | daily_basic symbols | join symbols | "
                "join coverage | missing side | Status |"
            ),
            "| --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for value in report.cross_section:
        missing = f"daily-only={value.daily_only}; daily_basic-only={value.daily_basic_only}"
        lines.append(
            f"| {value.trade_date} | {value.daily_symbols:,} | {value.daily_basic_symbols:,} | "
            f"{value.join_symbols:,} | {value.join_coverage:.4f} | {missing} | {value.status} |"
        )
    lines.extend(
        [
            "",
            "## Benchmark coverage gate",
            "",
            f"- Benchmark: `{report.benchmark.benchmark_code}`",
            f"- Range: `{report.benchmark.earliest_date or '-'}.."
            f"{report.benchmark.latest_date or '-'}`",
            f"- Sessions: `{report.benchmark.actual_sessions}/"
            f"{report.benchmark.expected_sessions}`",
            f"- Missing sessions: `{len(report.benchmark.missing_sessions)}`",
            f"- 20D sample: stock=`{report.benchmark.sample_stock_code or '-'}` "
            f"date=`{report.benchmark.sample_trade_date or '-'}`",
            f"- 20D stock return: `{report.benchmark.stock_return_20d}`",
            f"- 20D benchmark return: `{report.benchmark.benchmark_return_20d}`",
            f"- 20D excess difference: `{report.benchmark.excess_return_20d}`",
            f"- Status: `{report.benchmark.status}`",
            "",
            "## Forward evaluation data gate",
            "",
            (
                "| Horizon | Eligible as-of sessions | Right-censored tail | "
                "Earliest eligible | Latest eligible | Status |"
            ),
            "| ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for value in report.forward_windows:
        lines.append(
            f"| {value.horizon}D | {value.available_as_of_count} | {value.right_censored_count} | "
            f"{value.earliest_eligible_as_of or '-'} | "
            f"{value.latest_eligible_as_of or '-'} | {value.status} |"
        )
    lines.extend(
        [
            "",
            "## Dynamic historical symbol sample",
            "",
            (
                "| ts_code | list_date | exchange | market | daily range/rows | "
                "daily_basic range/rows |"
            ),
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for value in report.historical_symbols:
        lines.append(
            f"| {value.ts_code} | {value.list_date or '-'} | "
            f"{value.exchange or '-'} | {value.market or '-'} | "
            f"{value.daily_earliest or '-'}..{value.daily_latest or '-'} / "
            f"{value.daily_rows:,} | "
            f"{value.daily_basic_earliest or '-'}.."
            f"{value.daily_basic_latest or '-'} / {value.daily_basic_rows:,} |"
        )
    lines.extend(
        [
            "",
            "## RAW integrity",
            "",
            f"- Unreadable Parquet: `{len(report.integrity.unreadable_files)}`",
            f"- Zero-byte files: `{len(report.integrity.zero_byte_files)}`",
            f"- tmp/partial files: `{len(report.integrity.temporary_files)}`",
            f"- Schema-drift datasets: `"
            f"{', '.join(report.integrity.schema_drift_datasets) or 'NONE'}`",
            f"- Duplicate identities: `{report.integrity.duplicate_rows or 'NONE'}`",
            f"- Checkpoint/file mismatches: `{len(report.integrity.checkpoint_mismatch)}`",
            f"- Unexpected tiny partitions: `{len(report.integrity.unexpected_tiny_partitions)}`",
            f"- Integrity status: `{report.integrity.status}`",
            "",
            "## Reference PIT findings",
            "",
            "| Dataset | Field | Availability | Historical semantics | PIT confidence | Notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for value in report.reference_pit:
        lines.append(
            f"| {value.dataset} | {value.field} | {value.availability} | "
            f"{value.historical_semantics} | {value.pit_confidence} | {value.notes} |"
        )
    lines.extend(
        [
            "",
            "## Remaining gaps",
            "",
            *(["- " + gap for gap in report.remaining_gaps] or ["- NONE"]),
            "",
            "## Warnings",
            "",
            *(["- " + warning for warning in report.warnings] or ["- NONE"]),
            "",
            "## Interpretation",
            "",
            (
                "`UNSUPPORTED_PIT` for `stock_basic` means the snapshot is complete "
                "and useful for static identifiers/listing boundaries, but current "
                "name/status/industry/board fields are not projected backward. "
                "Historical name intervals require `namechange` plus an "
                "announcement-date cutoff; a current snapshot must never be "
                "substituted for a historical state."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def format_market_coverage(report: MarketCoverageReport) -> str:
    lines = [
        (
            "dataset | status | rows | size | expected units | PASS units | "
            "expected sessions | present sessions | missing | duplicates"
        ),
        "--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:",
    ]
    for value in report.datasets:
        lines.append(
            f"{value.dataset} | {value.status} | {value.row_count:,} | "
            f"{_format_size(value.size_bytes)} | "
            f"{value.expected_units} | {value.pass_units} | {value.expected_trading_days} | "
            f"{value.present_trading_days} | "
            f"{len(value.missing_trading_days)} | {value.duplicate_rows}"
        )
    lines.append(f"verdict={report.status}")
    return "\n".join(lines)
