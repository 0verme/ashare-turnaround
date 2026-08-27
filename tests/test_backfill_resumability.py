"""Resumable backfill acceptance tests for #5.

These tests prove the orchestration's durable invariants against synthetic
providers and never contact a real endpoint or touch real NAS data:

- the disk guard stops before the emergency threshold and never silently
  proceeds after a hard stop,
- a fully PASS run is idempotent: a resume rerun performs zero source calls
  and leaves period files byte-identical,
- a failed dataset/period unit is isolated: it never overwrites unrelated
  completed period files or checkpoints,
- provider retry/backoff is applied inside the worker path and the period
  still completes PASS.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter

import pandas as pd
import pytest
import requests

import ashare_turnaround.datasets.bootstrap as bootstrap_module
import ashare_turnaround.providers.tushare as provider_module
from ashare_turnaround.datasets.bootstrap import (
    bootstrap_datasets,
    format_dataset_progress,
)
from ashare_turnaround.providers.tushare import ProviderError, TushareProvider
from ashare_turnaround.storage.guards import EMERGENCY_STOP_FREE_BYTES, DiskSpaceCheck
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import BootstrapCheckpointStore


class FakeFinancialProvider:
    """A minimal in-memory provider matching the bootstrap call surface."""

    def __init__(self, *, latency: float = 0.0, fail_periods: set[str] | None = None) -> None:
        self.latency = latency
        self.fail_periods = set(fail_periods or ())
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._lock = threading.Lock()

    def call(self, api_name: str, **params: object) -> pd.DataFrame:
        period = str(params["period"])
        with self._lock:
            self.calls.append((api_name, dict(params)))
        if period in self.fail_periods:
            raise ProviderError(api_name, "timeout", f"temporary {period}", attempts=3)
        if self.latency:
            time.sleep(self.latency)
        return pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "ann_date": [period],
                "end_date": [period],
                "report_type": ["1"],
                "comp_type": ["1"],
                "end_type": ["4"],
                "update_flag": ["0"],
                "value": [1.0],
            }
        )


def _run(
    tmp_path,
    provider,
    *,
    workers: int = 1,
    requests_per_minute: float = 1_000_000.0,
    resume: bool = False,
):
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    checkpoints = BootstrapCheckpointStore(data_dir / "state" / "bootstrap.json")
    return bootstrap_datasets(
        provider,
        store,
        checkpoints,
        datasets=("balancesheet",),
        start_year=2024,
        end_year=2024,
        resume=resume,
        page_size=5000,
        max_pages=2,
        workers=workers,
        requests_per_minute=requests_per_minute,
    )


def _file_hashes(store: RawParquetStore, dataset: str) -> dict[str, str]:
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.parquet_files(dataset)
    }


def _stop_disk(path) -> DiskSpaceCheck:
    return DiskSpaceCheck(
        path=str(path),
        total_bytes=10**12,
        used_bytes=0,
        free_bytes=EMERGENCY_STOP_FREE_BYTES - 1,
        recommendation="STOP: less than 15 GiB free",
    )


def _ok_disk(path) -> DiskSpaceCheck:
    return DiskSpaceCheck(
        path=str(path),
        total_bytes=10**12,
        used_bytes=0,
        free_bytes=10 * EMERGENCY_STOP_FREE_BYTES,
        recommendation="PASS",
    )


# --------------------------------------------------------------------------- #
# Disk guard                                                                  #
# --------------------------------------------------------------------------- #


def test_disk_guard_stops_before_emergency_threshold_and_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeFinancialProvider()
    monkeypatch.setattr(bootstrap_module, "check_disk_space", _stop_disk)

    summary = _run(tmp_path, provider, workers=4)

    # Every unit is marked as a hard failure; nothing was fetched, written, or
    # checkpointed. The run never silently proceeds past the emergency stop.
    assert summary.task_count == 4
    assert summary.completed_count == 0
    assert Counter(result.status for result in summary.results) == Counter({"FAILED": 4})
    assert all(result.error and "disk emergency stop" in result.error for result in summary.results)
    assert provider.calls == []
    store = RawParquetStore(tmp_path / "data")
    assert store.parquet_files("balancesheet") == []
    assert not (tmp_path / "data" / "state" / "bootstrap.json").exists()


def test_disk_guard_stops_mid_run_and_preserves_already_completed_periods(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeFinancialProvider(latency=0.0)
    # Disk is healthy for the first check only, then drops below the emergency
    # threshold. Workers=1 makes the per-task gate deterministic.
    state = {"remaining_ok": 1}

    def gated_disk(path):
        if state["remaining_ok"] > 0:
            state["remaining_ok"] -= 1
            return _ok_disk(path)
        return _stop_disk(path)

    monkeypatch.setattr(bootstrap_module, "check_disk_space", gated_disk)

    summary = _run(tmp_path, provider, workers=1)

    statuses = Counter(result.status for result in summary.results)
    assert statuses == Counter({"PASS": 1, "FAILED": 3})
    assert all(
        "disk emergency stop" in (result.error or "")
        for result in summary.results
        if result.status == "FAILED"
    )
    # Only the completed period was written and checkpointed; the hard stop did
    # not silently commit any later period.
    store = RawParquetStore(tmp_path / "data")
    assert len(store.parquet_files("balancesheet")) == 1
    records = BootstrapCheckpointStore(
        tmp_path / "data" / "state" / "bootstrap.json"
    ).records()
    assert [record["status"] for record in records] == ["PASS"]


# --------------------------------------------------------------------------- #
# Idempotency                                                                 #
# --------------------------------------------------------------------------- #


def test_idempotent_rerun_skips_all_pass_periods_without_source_calls(tmp_path) -> None:
    first_provider = FakeFinancialProvider()
    first = _run(tmp_path, first_provider, workers=1)

    assert first.completed_count == 4
    assert not first.failures
    before_hashes = _file_hashes(RawParquetStore(tmp_path / "data"), "balancesheet")
    assert len(before_hashes) == 4

    second_provider = FakeFinancialProvider()
    second = _run(tmp_path, second_provider, workers=1, resume=True)

    # A rerun must not redownload a durable PASS whose file is still present.
    assert second.skipped_count == 4
    assert second.completed_count == 0
    assert not second.failures
    assert second_provider.calls == []
    after_hashes = _file_hashes(RawParquetStore(tmp_path / "data"), "balancesheet")
    assert before_hashes == after_hashes


# --------------------------------------------------------------------------- #
# Failure isolation                                                           #
# --------------------------------------------------------------------------- #


def test_failed_period_does_not_overwrite_unrelated_completed_files(tmp_path) -> None:
    first_provider = FakeFinancialProvider(fail_periods={"20240630"})
    first = _run(tmp_path, first_provider, workers=4)

    assert Counter(result.status for result in first.results) == Counter({"PASS": 3, "FAILED": 1})
    store = RawParquetStore(tmp_path / "data")
    pass_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.parquet_files("balancesheet")
    }
    assert len(pass_hashes) == 3
    # The failed period left no partition file.
    assert not store.period_exists("balancesheet", "20240630")
    records = BootstrapCheckpointStore(
        tmp_path / "data" / "state" / "bootstrap.json"
    ).records()
    assert Counter(record["status"] for record in records) == Counter({"PASS": 3, "FAILED": 1})

    second_provider = FakeFinancialProvider()
    second = _run(tmp_path, second_provider, workers=4, resume=True)

    # Only the failed period was retried; the three PASS files are byte-identical
    # and were not rewritten.
    assert second.skipped_count == 3
    assert second.completed_count == 1
    assert not second.failures
    assert [str(call[1]["period"]) for call in second_provider.calls] == ["20240630"]
    after_pass_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in store.parquet_files("balancesheet")
        if "period=20240630" not in str(path)
    }
    assert after_pass_hashes == pass_hashes
    assert len(store.parquet_files("balancesheet")) == 4


# --------------------------------------------------------------------------- #
# Retry / backoff applied inside the worker path                              #
# --------------------------------------------------------------------------- #


class _TransientClient:
    """A Tushare SDK stand-in that fails the first attempt then succeeds."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._fail_once = True

    def query(self, api_name: str, **params: object) -> pd.DataFrame:
        self.calls.append((api_name, dict(params)))
        if self._fail_once:
            self._fail_once = False
            raise requests.Timeout("temporary bootstrap failure")
        period = str(params.get("period"))
        return pd.DataFrame(
            {
                "ts_code": ["600000.SH"],
                "ann_date": [period],
                "end_date": [period],
                "report_type": ["1"],
                "comp_type": ["1"],
                "end_type": ["4"],
                "update_flag": ["0"],
                "value": [1.0],
            }
        )


def test_bootstrap_applies_provider_retry_and_backoff_per_worker_attempt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _TransientClient()
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)
    delays: list[float] = []
    provider = TushareProvider(
        "secret-token",
        max_retries=1,
        backoff_seconds=0.0,
        sleep=delays.append,
    )

    summary = bootstrap_datasets(
        provider,
        RawParquetStore(tmp_path / "data"),
        BootstrapCheckpointStore(tmp_path / "data" / "state" / "bootstrap.json"),
        datasets=("balancesheet",),
        start_year=2024,
        end_year=2024,
        resume=False,
        page_size=5000,
        max_pages=2,
        workers=1,
        requests_per_minute=1_000_000.0,
    )

    # The transient failure was retried inside the worker path and the period
    # still completed PASS; the global limiter counted every attempt.
    assert not summary.failures
    assert summary.completed_count == 4
    assert len(delays) == 1  # one bounded exponential backoff
    assert summary.api_requests == 5  # 2 attempts for the first period + 3 others
    assert len(client.calls) == 5


def test_dataset_progress_makes_dataset_level_completion_observable(tmp_path) -> None:
    provider = FakeFinancialProvider(fail_periods={"20240930"})
    summary = _run(tmp_path, provider, workers=4)

    progress = summary.dataset_progress
    assert len(progress) == 1
    balancesheet = progress[0]
    assert balancesheet.dataset == "balancesheet"
    assert balancesheet.total == 4
    assert balancesheet.completed == 3
    assert balancesheet.failed == 1
    assert balancesheet.skipped == 0
    assert balancesheet.partial == 0
    table = format_dataset_progress(summary)
    assert "balancesheet" in table
    assert "completed" in table
