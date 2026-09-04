from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from ashare_turnaround.harvest import (
    HARD_FREE_SPACE,
    HARVEST_SPECS,
    SOFT_FREE_SPACE,
    DeadlineGuard,
    DiskGuard,
    HarvestCheckpointStore,
    _query_for_unit,
    build_download_plan,
    build_raw_integrity,
    run_harvest,
)
from ashare_turnaround.providers.tushare import ProviderError


class FakeArchiveProvider:
    def __init__(self, *, partial: bool = False, drift: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.partial = partial
        self.drift = drift
        self.rate_limiter = None

    def set_rate_limiter(self, limiter: object) -> None:
        self.rate_limiter = limiter

    def call(self, api_name: str, **params: object) -> pd.DataFrame:
        self.calls.append({"api": api_name, **params})
        offset = int(params.get("offset", 0))
        if self.partial and offset >= 2:
            raise ProviderError(api_name, "timeout", "later page failed", attempts=1)
        if offset >= 2:
            return pd.DataFrame(
                {
                    "ts_code": ["600002.SH"],
                    "trade_date": ["20240103"],
                    "adj_factor": [1.2],
                }
            )
        frame = pd.DataFrame(
            {
                "ts_code": ["600000.SH", "600001.SZ"],
                "trade_date": ["20240101", "20240102"],
                "adj_factor": [1.0, 1.1],
            }
        )
        if self.drift:
            frame["new_vendor_field"] = 1
        return frame


def _plan(tmp_path):
    return build_download_plan(
        tmp_path / "data",
        inventory=None,
        start_date="20240101",
        end_date="20240131",
        workers=1,
        rate_limit=1_000_000,
        soft_free_space=0,
        hard_free_space=0,
        specs=tuple(spec for spec in HARVEST_SPECS if spec.dataset == "adj_factor"),
    )


def test_year_and_month_queries_are_capped_at_plan_end(tmp_path) -> None:
    year_spec = next(spec for spec in HARVEST_SPECS if spec.dataset == "repurchase")
    month_spec = next(spec for spec in HARVEST_SPECS if spec.dataset == "moneyflow")

    year_query = _query_for_unit(
        tmp_path / "data", year_spec, "2026", end_date="20260831"
    )
    month_query = _query_for_unit(
        tmp_path / "data", month_spec, "202608", end_date="20260831"
    )

    assert year_query["end_date"] == "20260831"
    assert month_query["end_date"] == "20260831"


def test_harvest_writes_then_checkpoints_and_resume_skips(tmp_path) -> None:
    plan = _plan(tmp_path)
    first_provider = FakeArchiveProvider()
    first = run_harvest(
        first_provider,
        plan,
        workers=1,
        page_size=2,
        max_pages=3,
        soft_free_space=0,
        hard_free_space=0,
    )

    assert [result.status for result in first.results] == ["PASS"]
    assert len(first_provider.calls) > 2
    path = (
        tmp_path
        / "data"
        / "raw"
        / "adj_factor"
        / "year=2024"
        / "month=202401"
        / "data.parquet"
    )
    assert path.is_file()
    checkpoint_path = tmp_path / "data" / "state" / "harvest-checkpoints.json"
    record = HarvestCheckpointStore(checkpoint_path).records()[0]
    assert record["status"] == "PASS"
    assert record["rows"] == 69
    assert record["stored_paths"] == [str(path)]

    second_provider = FakeArchiveProvider()
    second = run_harvest(
        second_provider,
        plan,
        workers=1,
        page_size=2,
        max_pages=3,
        soft_free_space=0,
        hard_free_space=0,
    )
    assert [result.status for result in second.results] == ["SKIPPED_EXISTING_COMPLETE"]
    assert second_provider.calls == []
    assert build_raw_integrity(tmp_path / "data")["checkpoint_row_count_mismatch"] == []


def test_harvest_later_page_failure_never_marks_partition_complete(tmp_path) -> None:
    plan = _plan(tmp_path)
    provider = FakeArchiveProvider(partial=True)
    summary = run_harvest(
        provider,
        plan,
        workers=1,
        page_size=2,
        max_pages=3,
        soft_free_space=0,
        hard_free_space=0,
    )

    assert summary.results[0].status == "FAILED"
    assert not list((tmp_path / "data" / "raw" / "adj_factor").rglob("*.parquet"))
    records = HarvestCheckpointStore(
        tmp_path / "data" / "state" / "harvest-checkpoints.json"
    ).records()
    assert records[-1]["status"] == "FAILED"


def test_disk_guard_and_deadline_are_explicit() -> None:
    guard = DiskGuard("/tmp", soft_free_bytes=SOFT_FREE_SPACE, hard_free_bytes=HARD_FREE_SPACE)
    assert guard.soft_free_bytes == SOFT_FREE_SPACE
    assert guard.hard_free_bytes == HARD_FREE_SPACE

    open_mode = DeadlineGuard(
        (datetime.now(UTC) + timedelta(hours=48)).isoformat()
    )
    assert open_mode.mode == "OPEN"
    assert open_mode.allows(
        HARVEST_SPECS[0],
        dataset_started=False,
    )[0]


def test_schema_drift_is_written_but_not_called_complete(tmp_path) -> None:
    plan = _plan(tmp_path)
    summary = run_harvest(
        FakeArchiveProvider(drift=True),
        plan,
        workers=1,
        page_size=2,
        max_pages=3,
        soft_free_space=0,
        hard_free_space=0,
    )

    assert summary.results[0].status == "PARTIAL"
    records = HarvestCheckpointStore(
        tmp_path / "data" / "state" / "harvest-checkpoints.json"
    ).records()
    assert records[-1]["status"] == "PARTIAL"
    assert "schema_drift" in records[-1]["warnings"]
