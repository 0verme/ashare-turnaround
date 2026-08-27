from __future__ import annotations

import json

import pandas as pd
import pytest

import ashare_turnaround.storage.parquet as parquet_module
import ashare_turnaround.storage.state as state_module
from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import SyncRecord, SyncStateStore


def _daily_frame(close: float, trade_date: str = "20240102") -> pd.DataFrame:
    return pd.DataFrame(
        {"ts_code": ["600000.SH"], "trade_date": [trade_date], "close": [close]}
    )


def test_failed_parquet_write_keeps_the_previous_partition(monkeypatch, tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    spec = get_dataset_spec("daily")
    store.write("daily", _daily_frame(10.0), spec)

    def fail_write(*_: object, **__: object) -> None:
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(parquet_module.pq, "write_table", fail_write)
    with pytest.raises(OSError, match="simulated disk write failure"):
        store.write("daily", _daily_frame(99.0), spec)

    loaded = store.read("daily")
    assert loaded["close"].tolist() == [10.0]
    assert not list(store.dataset_dir("daily").rglob("*.tmp"))


def test_atomic_rename_fsyncs_the_parent_directory(monkeypatch, tmp_path) -> None:
    calls: list[object] = []
    monkeypatch.setattr(parquet_module, "fsync_directory", calls.append)

    store = RawParquetStore(tmp_path / "data")
    stored = store.write("daily", _daily_frame(10.0), get_dataset_spec("daily"))

    assert calls == [stored[0].path.parent]


def test_failed_state_replace_keeps_the_previous_json(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state" / "sync-log.json"
    state = SyncStateStore(path)
    record = SyncRecord("income", {}, "now", "now", "success", rows=1)
    state.append(record)

    def fail_replace(*_: object, **__: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        state.append(SyncRecord("income", {}, "now", "now", "success", rows=2))

    assert json.loads(path.read_text(encoding="utf-8"))[0]["rows"] == 1
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_partition_key_fails_closed_without_unknown_partition(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = _daily_frame(10.0, trade_date="not-a-date")

    with pytest.raises(ValueError, match="refusing to write an unknown partition"):
        store.write("daily", frame, get_dataset_spec("daily"))

    assert not list(store.dataset_dir("daily").rglob("*.parquet"))


def test_integer_shaped_float_dates_use_the_same_partition(tmp_path) -> None:
    store = RawParquetStore(tmp_path / "data")
    frame = _daily_frame(10.0)
    frame["trade_date"] = [20240102.0]

    stored = store.write("daily", frame, get_dataset_spec("daily"))

    assert "trade_date=20240102" in str(stored[0].path)
