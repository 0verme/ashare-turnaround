"""RAW Parquet inventory and lightweight manifest generation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from ..datasets.specs import DATASET_SPECS, DatasetSpec, get_dataset_spec
from .state import BootstrapCheckpointStore, MarketCheckpointStore

_PARTITION_VALUE = re.compile(r"(?:period|trade_date|year)=(\d{4,8})")


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    dataset: str
    path: str
    rows: int
    size_bytes: int
    earliest: str | None
    latest: str | None
    schema_hash: str


@dataclass(frozen=True, slots=True)
class InventoryDataset:
    dataset: str
    files: int
    rows: int
    size_bytes: int
    earliest: str | None
    latest: str | None
    completeness: str


@dataclass(frozen=True, slots=True)
class RawManifest:
    generated_at: str
    data_dir: str
    total_files: int
    total_rows: int
    total_bytes: int
    datasets: tuple[InventoryDataset, ...]
    files: tuple[ManifestEntry, ...]


@dataclass(frozen=True, slots=True)
class DatasetCoverage:
    """Machine-readable coverage and integrity findings for one dataset."""

    dataset: str
    row_count: int
    file_count: int
    symbol_count: int
    min_date: str | None
    max_date: str | None
    latest_expected_date: str | None
    missing_partitions: tuple[str, ...]
    duplicate_rows: int
    status: str
    bytes: int
    schema_hash: str
    checkpoint_completeness: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Complete deterministic audit result for the configured local data lake."""

    generated_at: str
    data_dir: str
    as_of_date: str | None
    datasets: tuple[DatasetCoverage, ...]


def _date_values(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    for column in columns:
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce")
            if values.notna().any():
                return values.dropna().dt.normalize()
    return pd.Series(dtype="datetime64[ns]")


def _checkpoint_completeness_for(dataset: str, checkpoint_path: Path) -> tuple[str, set[str]]:
    market_path = checkpoint_path.parent / "market-bootstrap-checkpoints.json"
    if market_path.exists():
        checkpoints = MarketCheckpointStore(market_path).latest_for_dataset(dataset)
        if checkpoints:
            statuses = {str(value.get("status", "")).upper() for value in checkpoints.values()}
            completeness = "COMPLETE" if statuses == {"PASS"} else "PARTIAL"
            return completeness, set(checkpoints)
    if not checkpoint_path.exists():
        return "UNKNOWN", set()
    checkpoints = BootstrapCheckpointStore(checkpoint_path).latest_for_dataset(dataset)
    if not checkpoints:
        return "UNKNOWN", set()
    statuses = {str(value.get("status", "")).upper() for value in checkpoints.values()}
    completeness = "COMPLETE" if statuses == {"PASS"} else "PARTIAL"
    return completeness, set(checkpoints)


def _expected_trade_dates(data_path: Path, as_of: pd.Timestamp | None) -> set[str]:
    calendar_dir = data_path / "raw" / "trade_cal"
    files = sorted(calendar_dir.rglob("*.parquet")) if calendar_dir.exists() else []
    if not files:
        return set()
    frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True, sort=False)
    if not {"cal_date", "is_open"}.issubset(frame.columns):
        return set()
    dates = pd.to_datetime(frame["cal_date"], errors="coerce")
    opened = frame.loc[dates.notna() & pd.to_numeric(frame["is_open"], errors="coerce").eq(1)]
    dates = pd.to_datetime(opened["cal_date"], errors="coerce").dropna().dt.normalize()
    if as_of is not None:
        dates = dates[dates.le(as_of)]
    return {value.strftime("%Y%m%d") for value in dates}


def _storage_partition_key(value: str | Path) -> str:
    """Normalize a storage path to the partition identity used by inventory."""

    components = [part for part in Path(value).parts if "=" in part and part != "data.parquet"]
    for marker in ("period=", "trade_date="):
        matches = [part for part in components if part.startswith(marker)]
        if matches:
            return matches[-1]
    if any(part.startswith("month=") for part in components):
        return "/".join(components)
    return "/".join(components)


def _market_checkpoint_records(data_path: Path, dataset: str) -> dict[str, dict[str, object]]:
    path = data_path / "state" / "market-bootstrap-checkpoints.json"
    if not path.exists():
        return {}
    return MarketCheckpointStore(path).latest_for_dataset(dataset)


def _expected_partitions(
    data_path: Path,
    spec: DatasetSpec,
    checkpoint_periods: set[str],
    as_of: pd.Timestamp | None,
) -> tuple[set[str], str | None]:
    market_records = _market_checkpoint_records(data_path, spec.name)
    if market_records:
        expected = {
            _storage_partition_key(str(value.get("storage_path", "")))
            for value in market_records.values()
            if value.get("storage_path")
        }
        expected.discard("")
        ends = {
            str(value.get("requested_end"))
            for value in market_records.values()
            if value.get("requested_end")
        }
        latest = max(ends) if ends else None
        return expected, latest
    if spec.partition_strategy == "date" and spec.partition_field == "trade_date":
        values = _expected_trade_dates(data_path, as_of)
        latest = max(values) if values else None
        return {f"trade_date={value}" for value in values}, latest
    if spec.partition_strategy == "year" and checkpoint_periods:
        periods = {value for value in checkpoint_periods if re.fullmatch(r"\d{8}", value)}
        latest = max(periods) if periods else None
        return {f"period={value}" for value in periods}, latest
    return set(), None


def _partition_keys(paths: list[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        components = [part for part in path.parts if "=" in part]
        if any(part.startswith("period=") for part in components):
            keys.update(part for part in components if part.startswith("period="))
        elif any(part.startswith("trade_date=") for part in components):
            keys.update(part for part in components if part.startswith("trade_date="))
        elif any(part.startswith("month=") for part in components):
            keys.add("/".join(components))
        else:
            # Keep the existing year/snapshot/range behavior for low-cardinality
            # and named units.
            keys.add("/".join(components))
    return keys


def _coverage_for_dataset(
    data_path: Path,
    dataset: str,
    *,
    as_of: pd.Timestamp | None,
    checkpoint_path: Path,
) -> DatasetCoverage:
    spec = get_dataset_spec(dataset)
    paths = sorted((data_path / "raw" / dataset).rglob("*.parquet"))
    paths = [path for path in paths if path.is_file()]
    checkpoint_status, checkpoint_periods = _checkpoint_completeness_for(dataset, checkpoint_path)
    expected, latest_expected = _expected_partitions(data_path, spec, checkpoint_periods, as_of)
    warnings: list[str] = []
    if not paths:
        status = "UNKNOWN" if not expected else "EMPTY"
        if expected:
            warnings.append("all_expected_partitions_missing")
        return DatasetCoverage(
            dataset=dataset,
            row_count=0,
            file_count=0,
            symbol_count=0,
            min_date=None,
            max_date=None,
            latest_expected_date=latest_expected,
            missing_partitions=tuple(sorted(expected)),
            duplicate_rows=0,
            status=status,
            bytes=0,
            schema_hash="",
            checkpoint_completeness=checkpoint_status,
            warnings=tuple(warnings),
        )

    frames: list[pd.DataFrame] = []
    schemas: set[str] = set()
    total_bytes = 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        columns = tuple(str(name) for name in parquet.schema_arrow.names)
        schemas.add(schema_hash(columns))
        total_bytes += path.stat().st_size
        frames.append(pd.read_parquet(path))
    frame = pd.concat(frames, ignore_index=True, sort=False)
    if len(schemas) > 1:
        warnings.append("schema_drift")
    missing_keys = [key for key in spec.primary_keys if key not in frame.columns]
    duplicate_rows = 0
    if missing_keys:
        warnings.append(f"missing_identity_fields={','.join(missing_keys)}")
    elif not frame.empty:
        duplicate_rows = int(frame.duplicated(list(spec.primary_keys), keep=False).sum())
        if duplicate_rows:
            warnings.append(f"duplicate_identity_rows={duplicate_rows}")

    date_values = _date_values(frame, spec.date_fields or (spec.partition_field or "",))
    min_date = date_values.min().strftime("%Y%m%d") if not date_values.empty else None
    max_date = date_values.max().strftime("%Y%m%d") if not date_values.empty else None
    symbols = frame["ts_code"].dropna().astype(str).nunique() if "ts_code" in frame.columns else 0
    present = _partition_keys(paths)
    missing = tuple(sorted(expected.difference(present)))
    if missing:
        warnings.append("missing_partitions")
    if latest_expected and max_date and max_date < latest_expected:
        warnings.append("stale_latest_date")
    if checkpoint_status == "PARTIAL":
        warnings.append("checkpoint_partial")
    if frame.empty:
        warnings.append("empty_partition")

    status = "PASS"
    if missing or duplicate_rows or "schema_drift" in warnings:
        status = "FAIL"
    elif warnings or checkpoint_status == "PARTIAL":
        status = "PARTIAL"
    elif not expected:
        status = "UNKNOWN"
    return DatasetCoverage(
        dataset=dataset,
        row_count=len(frame),
        file_count=len(paths),
        symbol_count=symbols,
        min_date=min_date,
        max_date=max_date,
        latest_expected_date=latest_expected,
        missing_partitions=missing,
        duplicate_rows=duplicate_rows,
        status=status,
        bytes=total_bytes,
        schema_hash=next(iter(schemas)) if len(schemas) == 1 else "mixed",
        checkpoint_completeness=checkpoint_status,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_coverage_report(
    data_dir: str | Path,
    *,
    as_of_date: str | pd.Timestamp | None = None,
    checkpoint_path: str | Path | None = None,
) -> CoverageReport:
    """Audit configured datasets without repairing, deleting, or deduplicating data."""

    data_path = Path(data_dir).expanduser()
    as_of = None
    if as_of_date is not None:
        as_of = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(as_of):
            raise ValueError(f"invalid coverage as_of_date: {as_of_date!r}")
        as_of = pd.Timestamp(as_of).normalize()
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path
        else data_path / "state" / "bootstrap-checkpoints.json"
    )
    raw_dir = data_path / "raw"
    discovered = (
        {
            path.relative_to(raw_dir).parts[0]
            for path in raw_dir.rglob("*.parquet")
            if path.is_file() and path.relative_to(raw_dir).parts
        }
        if raw_dir.exists()
        else set()
    )
    datasets = tuple(sorted(set(DATASET_SPECS).union(discovered)))
    return CoverageReport(
        generated_at=datetime.now(UTC).isoformat(),
        data_dir=str(data_path),
        as_of_date=as_of.strftime("%Y%m%d") if as_of is not None else None,
        datasets=tuple(
            _coverage_for_dataset(
                data_path,
                dataset,
                as_of=as_of,
                checkpoint_path=checkpoint,
            )
            for dataset in datasets
        ),
    )


def coverage_dict(report: CoverageReport) -> dict[str, object]:
    return {
        "generated_at": report.generated_at,
        "data_dir": report.data_dir,
        "as_of_date": report.as_of_date,
        "datasets": [asdict(dataset) for dataset in report.datasets],
    }


def write_coverage_report(report: CoverageReport, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(coverage_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_coverage(report: CoverageReport) -> str:
    lines = [
        "dataset | status | rows | files | symbols | min_date | max_date | "
        "expected | missing | duplicates",
        "--- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---:",
    ]
    for dataset in report.datasets:
        lines.append(
            f"{dataset.dataset} | {dataset.status} | {dataset.row_count:,} | "
            f"{dataset.file_count} | {dataset.symbol_count} | {dataset.min_date or '-'} | "
            f"{dataset.max_date or '-'} | {dataset.latest_expected_date or '-'} | "
            f"{len(dataset.missing_partitions)} | {dataset.duplicate_rows}"
        )
    return "\n".join(lines)


def schema_hash(columns: list[str] | tuple[str, ...]) -> str:
    payload = json.dumps(sorted(str(column) for column in columns), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _partition_date(path: Path) -> str | None:
    values = _PARTITION_VALUE.findall(str(path))
    return values[-1] if values else None


def _file_entry(dataset: str, path: Path, raw_dir: Path) -> ManifestEntry:
    parquet = pq.ParquetFile(path)
    columns = [str(name) for name in parquet.schema_arrow.names]
    partition_value = _partition_date(path)
    earliest = latest = partition_value
    if partition_value and len(partition_value) == 4:
        date_field = "end_date" if "end_date" in columns else None
        if date_field is None and "trade_date" in columns:
            date_field = "trade_date"
        if date_field:
            try:
                values = pq.read_table(path, columns=[date_field]).column(0).to_pylist()
                normalized = sorted(
                    str(value).replace("-", "")
                    for value in values
                    if value is not None and str(value).strip()
                )
                if normalized:
                    earliest, latest = normalized[0], normalized[-1]
            except (OSError, ValueError, TypeError):
                pass
    return ManifestEntry(
        dataset=dataset,
        path=str(path.relative_to(raw_dir.parent.parent)),
        rows=int(parquet.metadata.num_rows),
        size_bytes=path.stat().st_size,
        earliest=earliest,
        latest=latest,
        schema_hash=schema_hash(columns),
    )


def _completeness(dataset: str, checkpoint_path: Path) -> str:
    if not checkpoint_path.exists():
        return "UNKNOWN"
    checkpoints = BootstrapCheckpointStore(checkpoint_path).latest_for_dataset(dataset)
    if not checkpoints:
        return "UNKNOWN"
    statuses = {str(value.get("status", "")).upper() for value in checkpoints.values()}
    return "COMPLETE" if statuses == {"PASS"} else "PARTIAL"


def build_raw_manifest(
    data_dir: str | Path,
    *,
    checkpoint_path: str | Path | None = None,
) -> RawManifest:
    """Scan ignored raw files without loading full business frames into pandas."""

    data_path = Path(data_dir).expanduser()
    raw_dir = data_path / "raw"
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path
        else data_path / "state" / "bootstrap-checkpoints.json"
    )
    entries: list[ManifestEntry] = []
    if raw_dir.exists():
        for dataset_dir in sorted(path for path in raw_dir.iterdir() if path.is_dir()):
            for path in sorted(dataset_dir.rglob("*.parquet")):
                if path.is_file():
                    entries.append(_file_entry(dataset_dir.name, path, raw_dir))

    datasets: list[InventoryDataset] = []
    for dataset in sorted({entry.dataset for entry in entries}):
        values = [entry for entry in entries if entry.dataset == dataset]
        earliest_values = [entry.earliest for entry in values if entry.earliest]
        latest_values = [entry.latest for entry in values if entry.latest]
        datasets.append(
            InventoryDataset(
                dataset=dataset,
                files=len(values),
                rows=sum(entry.rows for entry in values),
                size_bytes=sum(entry.size_bytes for entry in values),
                earliest=min(earliest_values) if earliest_values else None,
                latest=max(latest_values) if latest_values else None,
                completeness=_completeness(dataset, checkpoint),
            )
        )
    return RawManifest(
        generated_at=datetime.now(UTC).isoformat(),
        data_dir=str(data_path),
        total_files=len(entries),
        total_rows=sum(entry.rows for entry in entries),
        total_bytes=sum(entry.size_bytes for entry in entries),
        datasets=tuple(datasets),
        files=tuple(entries),
    )


def manifest_dict(manifest: RawManifest) -> dict[str, object]:
    return {
        "generated_at": manifest.generated_at,
        "data_dir": manifest.data_dir,
        "total_files": manifest.total_files,
        "total_rows": manifest.total_rows,
        "total_bytes": manifest.total_bytes,
        "datasets": [asdict(value) for value in manifest.datasets],
        "files": [asdict(value) for value in manifest.files],
    }


def write_raw_manifest(manifest: RawManifest, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest_dict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_inventory(manifest: RawManifest) -> str:
    lines = [
        "dataset | coverage | rows | files | size | earliest | latest",
        "--- | --- | ---: | ---: | ---: | --- | ---",
    ]
    for value in manifest.datasets:
        size = _format_bytes(value.size_bytes)
        lines.append(
            f"{value.dataset} | {value.completeness} | {value.rows:,} | {value.files} | "
            f"{size} | {value.earliest or '-'} | {value.latest or '-'}"
        )
    lines.extend(
        [
            "",
            f"total files={manifest.total_files}",
            f"total rows={manifest.total_rows:,}",
            f"total parquet size={_format_bytes(manifest.total_bytes)}",
        ]
    )
    return "\n".join(lines)


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TiB"
