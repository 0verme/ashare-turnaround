"""RAW Parquet inventory and lightweight manifest generation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from .state import BootstrapCheckpointStore

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
