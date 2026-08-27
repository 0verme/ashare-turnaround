"""Atomic, local RAW Parquet storage."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..datasets.specs import DatasetSpec, get_dataset_spec
from ..dates import date_text
from .atomic import fsync_directory

_DATASET_NAME = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class StoredFile:
    path: Path
    rows: int
    size_bytes: int


def _date_text(value: Any) -> str | None:
    """Compatibility wrapper around the shared date parser."""

    return date_text(value)


class RawParquetStore:
    """Store raw API frames below ``<data_dir>/raw/<dataset>``.

    Each deterministic partition is replaced through a same-directory
    temporary file followed by ``os.replace``.  A failed write therefore does
    not truncate an existing partition.  Replacement, rather than implicit
    latest-row deduplication, keeps repeated sample runs predictable while
    retaining all versions returned by the API in the incoming frame.
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()

    @staticmethod
    def _validate_dataset(dataset: str) -> None:
        if not _DATASET_NAME.fullmatch(dataset):
            raise ValueError(f"invalid dataset name: {dataset!r}")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    def dataset_dir(self, dataset: str) -> Path:
        self._validate_dataset(dataset)
        return self.raw_dir / dataset

    def parquet_files(self, dataset: str) -> list[Path]:
        directory = self.dataset_dir(dataset)
        if not directory.exists():
            return []
        return sorted(path for path in directory.rglob("*.parquet") if path.is_file())

    def parquet_glob(self, dataset: str) -> str:
        """Return a DuckDB-compatible recursive glob for one dataset."""

        return str(self.dataset_dir(dataset) / "**" / "*.parquet")

    def write(
        self,
        dataset: str,
        frame: pd.DataFrame,
        spec: DatasetSpec | None = None,
        *,
        retrieved_at: str | None = None,
        source: str | None = None,
    ) -> list[StoredFile]:
        """Write a frame, preserving columns and adding optional provenance."""

        selected_spec = spec or get_dataset_spec(dataset)
        if selected_spec.name != dataset:
            raise ValueError("spec.name must match dataset")
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")

        output = frame.reset_index(drop=True).copy()
        if retrieved_at is not None:
            output["retrieved_at"] = retrieved_at
        if source is not None:
            output["source"] = source
        if output.empty:
            return []

        stored: list[StoredFile] = []
        for partition, chunk in self._partitions(output, selected_spec):
            path = self._partition_path(dataset, selected_spec, partition)
            stored.append(self._atomic_write(path, chunk))
        return stored

    def write_period(
        self,
        dataset: str,
        period: str,
        frame: pd.DataFrame,
        spec: DatasetSpec | None = None,
        *,
        retrieved_at: str | None = None,
        source: str | None = None,
        source_api: str | None = None,
    ) -> list[StoredFile]:
        """Write one report period without replacing another period in its year.

        The original Phase 1 year partition remains supported for samples.  A
        historical bootstrap must use a period sub-partition because four
        report periods share one year and replacing ``year=YYYY/data.parquet``
        would silently discard earlier downloads.
        """

        selected_spec = spec or get_dataset_spec(dataset)
        if selected_spec.name != dataset:
            raise ValueError("spec.name must match dataset")
        if not re.fullmatch(r"\d{8}", str(period)):
            raise ValueError("period must be an 8-digit YYYYMMDD string")
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")

        output = frame.reset_index(drop=True).copy()
        if retrieved_at is not None:
            output["retrieved_at"] = retrieved_at
        if source is not None:
            output["source"] = source
        if source_api is not None:
            output["source_api"] = source_api
        if output.empty:
            return []

        path = (
            self.dataset_dir(dataset)
            / f"year={str(period)[:4]}"
            / f"period={period}"
            / "data.parquet"
        )
        return [self._atomic_write(path, output)]

    def period_file(self, dataset: str, period: str) -> Path:
        """Return the deterministic period path used by bootstrap checkpoints."""

        self._validate_dataset(dataset)
        if not re.fullmatch(r"\d{8}", str(period)):
            raise ValueError("period must be an 8-digit YYYYMMDD string")
        return (
            self.dataset_dir(dataset)
            / f"year={str(period)[:4]}"
            / f"period={period}"
            / "data.parquet"
        )

    def period_exists(self, dataset: str, period: str) -> bool:
        return self.period_file(dataset, period).is_file()

    def schema_columns(self, dataset: str) -> tuple[str, ...]:
        """Return the union of Parquet columns using metadata only."""

        columns: list[str] = []
        seen: set[str] = set()
        for path in self.parquet_files(dataset):
            for column in pq.ParquetFile(path).schema_arrow.names:
                name = str(column)
                if name not in seen:
                    seen.add(name)
                    columns.append(name)
        return tuple(columns)

    def read(self, dataset: str) -> pd.DataFrame:
        """Read all partitions for a dataset, unioning columns in Python."""

        files = self.parquet_files(dataset)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(path) for path in files]
        return pd.concat(frames, ignore_index=True, sort=False)

    def _partitions(
        self, frame: pd.DataFrame, spec: DatasetSpec
    ) -> list[tuple[str | None, pd.DataFrame]]:
        strategy = spec.partition_strategy
        if strategy == "none" or not spec.partition_field:
            return [(None, frame)]
        if strategy not in {"date", "year"}:
            raise ValueError(f"unsupported partition strategy: {strategy}")
        if spec.partition_field not in frame.columns:
            raise ValueError(
                f"partition field {spec.partition_field!r} is missing for {spec.name}"
            )

        partitions: dict[str, list[int]] = {}
        for index, value in frame[spec.partition_field].items():
            normalized = _date_text(value)
            if normalized is None:
                raise ValueError(
                    f"invalid {spec.partition_field} at row {index!r}; "
                    "refusing to write an unknown partition"
                )
            if strategy == "year":
                key = f"year={normalized[:4]}"
            else:
                key = f"{spec.partition_field}={normalized}"
            partitions.setdefault(key, []).append(index)
        return [
            (key, frame.loc[indices].reset_index(drop=True)) for key, indices in partitions.items()
        ]

    def _partition_path(self, dataset: str, spec: DatasetSpec, partition: str | None) -> Path:
        directory = self.dataset_dir(dataset)
        if partition:
            directory /= partition
        return directory / "data.parquet"

    @staticmethod
    def _atomic_write(path: Path, frame: pd.DataFrame) -> StoredFile:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        metadata = dict(table.schema.metadata or {})
        metadata[b"ashare_turnaround.schema"] = b"raw-parquet-v1"
        table = table.replace_schema_metadata(metadata)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            pq.write_table(table, temporary_path, compression="zstd")
            with temporary_path.open("rb") as written:
                os.fsync(written.fileno())
            os.replace(temporary_path, path)
            # The rename is atomic for readers; fsyncing the parent directory
            # also makes the new name durable across a sudden power loss on
            # filesystems that support it.
            fsync_directory(path.parent)
            return StoredFile(path=path, rows=len(frame), size_bytes=path.stat().st_size)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
