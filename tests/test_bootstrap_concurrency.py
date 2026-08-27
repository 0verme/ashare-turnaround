from __future__ import annotations

import threading
import time
from collections import Counter

import pandas as pd
import pytest

import ashare_turnaround.datasets.bootstrap as bootstrap_module
from ashare_turnaround.datasets.bootstrap import bootstrap_datasets
from ashare_turnaround.providers.tushare import ProviderError
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import BootstrapCheckpointStore


class FakeFinancialProvider:
    def __init__(self, *, latency: float = 0.0, fail_periods: set[str] | None = None) -> None:
        self.latency = latency
        self.fail_periods = set(fail_periods or ())
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.call_times: list[float] = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()

    def call(self, api_name: str, **params: object) -> pd.DataFrame:
        period = str(params["period"])
        with self._lock:
            self.calls.append((api_name, dict(params)))
            self.call_times.append(time.monotonic())
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
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
        finally:
            with self._lock:
                self._active -= 1


def _run(
    tmp_path,
    provider: FakeFinancialProvider,
    *,
    workers: int,
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


def test_workers_one_keeps_period_fetches_serial_and_writes_each_partition(tmp_path) -> None:
    provider = FakeFinancialProvider(latency=0.01)

    summary = _run(tmp_path, provider, workers=1)

    assert summary.task_count == 4
    assert summary.completed_count == 4
    assert summary.skipped_count == 0
    assert not summary.failures
    assert provider.max_active == 1
    assert len(provider.calls) == 4
    assert len(RawParquetStore(tmp_path / "data").parquet_files("balancesheet")) == 4
    checkpoint_records = BootstrapCheckpointStore(
        tmp_path / "data" / "state" / "bootstrap.json"
    ).records()
    assert len(checkpoint_records) == 4
    assert {record["status"] for record in checkpoint_records} == {"PASS"}


def test_workers_four_fetches_independent_periods_concurrently(tmp_path) -> None:
    provider = FakeFinancialProvider(latency=0.1)

    summary = _run(tmp_path, provider, workers=4)

    assert not summary.failures
    assert provider.max_active >= 2
    assert summary.api_requests == 4
    assert summary.elapsed_seconds < 0.8
    assert len(
        BootstrapCheckpointStore(tmp_path / "data" / "state" / "bootstrap.json").records()
    ) == 4


def test_all_workers_share_one_global_rate_limiter(tmp_path) -> None:
    provider = FakeFinancialProvider()

    summary = _run(tmp_path, provider, workers=4, requests_per_minute=600.0)

    assert not summary.failures
    assert len(provider.call_times) == 4
    call_times = sorted(provider.call_times)
    intervals = [right - left for left, right in zip(call_times, call_times[1:])]
    assert min(intervals) >= 0.07


def test_interrupt_does_not_commit_in_flight_fetched_periods(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeFinancialProvider(latency=0.05)

    def raise_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap_module, "wait", raise_interrupt)

    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, provider, workers=4)

    time.sleep(0.1)
    store = RawParquetStore(tmp_path / "data")
    assert not store.parquet_files("balancesheet")
    assert not (tmp_path / "data" / "state" / "bootstrap.json").exists()


def test_resume_skips_only_durable_pass_periods_and_retries_failed_period(tmp_path) -> None:
    first_provider = FakeFinancialProvider(fail_periods={"20240630"})
    first = _run(tmp_path, first_provider, workers=4)

    assert Counter(result.status for result in first.results) == Counter({"PASS": 3, "FAILED": 1})
    assert len(RawParquetStore(tmp_path / "data").parquet_files("balancesheet")) == 3

    second_provider = FakeFinancialProvider()
    second = _run(tmp_path, second_provider, workers=4, resume=True)

    assert second.skipped_count == 3
    assert second.completed_count == 1
    assert not second.failures
    assert [str(call[1]["period"]) for call in second_provider.calls] == ["20240630"]
    final_store = RawParquetStore(tmp_path / "data")
    assert len(final_store.parquet_files("balancesheet")) == 4
    loaded = final_store.read("balancesheet")
    identity = ["ts_code", "end_date", "report_type", "comp_type", "end_type", "update_flag"]
    assert len(loaded) == len(loaded.drop_duplicates(identity))
