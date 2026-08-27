from __future__ import annotations

import json

import pandas as pd
import pytest

import ashare_turnaround.datasets.sync as sync_module
from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.datasets.sync import sync_sample
from ashare_turnaround.providers.tushare import ProviderError
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import SyncRecord, SyncStateStore


def test_sync_state_records_required_fields_without_secret(tmp_path) -> None:
    path = tmp_path / "data" / "state" / "sync-log.json"
    state = SyncStateStore(path, secret="secret-token")
    state.append(
        SyncRecord(
            dataset="income",
            params={"ts_code": "600000.SH", "token": "secret-token"},
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:01Z",
            status="failed",
            error_type="permission",
            error_message="bad secret-token",
        )
    )

    raw = path.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    value = json.loads(raw)[0]
    assert value["dataset"] == "income"
    assert value["params"]["ts_code"] == "600000.SH"
    assert value["params"]["token"] == "<redacted>"
    assert value["error_type"] == "permission"


def test_sample_does_not_replace_existing_data_after_one_request_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    spec = get_dataset_spec("daily_basic")
    store.write(
        "daily_basic",
        pd.DataFrame(
            {"ts_code": ["600000.SH"], "trade_date": ["20240102"], "pe": [5.0]}
        ),
        spec,
    )

    monkeypatch.setattr(
        sync_module,
        "_sample_requests",
        lambda *_args, **_kwargs: {
            "daily_basic": [
                {"ts_code": "600000.SH", "limit": 1},
                {"ts_code": "000001.SZ", "limit": 1},
            ]
        },
    )

    class PartiallyFailingProvider:
        def call(self, dataset: str, **params: object) -> pd.DataFrame:
            if params["ts_code"] == "000001.SZ":
                raise ProviderError(dataset, "connection", "unavailable", attempts=1)
            if int(params["offset"]) == 0:
                return pd.DataFrame(
                    {"ts_code": ["600000.SH"], "trade_date": ["20240103"], "pe": [6.0]}
                )
            return pd.DataFrame()

    state = SyncStateStore(data_dir / "state" / "sync-log.json")
    summary = sync_sample(
        PartiallyFailingProvider(),
        store,
        state,
        codes=("600000.SH", "000001.SZ", "300001.SZ"),
        limit=1,
        max_pages=2,
    )

    assert len(summary.failures) == 1
    assert summary.stored_files == ()
    assert store.read("daily_basic")["pe"].tolist() == [5.0]
    assert state.latest("daily_basic")["status"] == "failed"
