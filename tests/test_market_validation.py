from __future__ import annotations

from pathlib import Path

import pandas as pd

from ashare_turnaround.datasets.market_bootstrap import build_market_bootstrap_plan
from ashare_turnaround.datasets.market_validation import (
    reference_pit_findings,
    verify_market_corpus,
)
from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.datasets.sync import _schema_hash
from ashare_turnaround.storage.inventory import build_raw_manifest
from ashare_turnaround.storage.parquet import RawParquetStore
from ashare_turnaround.storage.state import MarketBootstrapCheckpoint, MarketCheckpointStore


def _daily_frame(rows: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * rows,
            "trade_date": ["20240102", "20240103"][:rows],
            "close": [10.0, 11.0][:rows],
        }
    )


def _write_daily_checkpoint(
    data_dir: Path,
    *,
    row_count: int,
    schema_hash: str,
) -> None:
    unit = build_market_bootstrap_plan(
        "20240101",
        "20240103",
        datasets=("daily",),
        benchmark_code="000300.SH",
    )[0]
    checkpoints = MarketCheckpointStore(data_dir / "state" / "market-bootstrap-checkpoints.json")
    checkpoints.append(
        MarketBootstrapCheckpoint(
            dataset="daily",
            unit=unit.unit,
            source_api="daily",
            requested_start=unit.expected_start,
            requested_end=unit.expected_end,
            storage_path=f"{unit.storage_key}/data.parquet",
            started_at="2024-01-04T00:00:00+00:00",
            finished_at="2024-01-04T00:00:01+00:00",
            status="PASS",
            page_count=1,
            row_count=row_count,
            request_count=1,
            schema_hash=schema_hash,
            duplicate_count=0,
        )
    )


def test_raw_manifest_uses_market_checkpoint_namespace(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    frame = _daily_frame()
    unit = build_market_bootstrap_plan(
        "20240101",
        "20240103",
        datasets=("daily",),
        benchmark_code="000300.SH",
    )[0]
    store.write_unit("daily", unit.storage_parts, frame, get_dataset_spec("daily"))
    _write_daily_checkpoint(data_dir, row_count=len(frame), schema_hash=_schema_hash(frame.columns))

    manifest = build_raw_manifest(data_dir)
    coverage = next(value for value in manifest.datasets if value.dataset == "daily")

    assert coverage.completeness == "COMPLETE"
    assert coverage.files == 1
    assert coverage.rows == 2


def test_market_verifier_rejects_checkpoint_row_mismatch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = RawParquetStore(data_dir)
    unit = build_market_bootstrap_plan(
        "20240101",
        "20240103",
        datasets=("daily",),
        benchmark_code="000300.SH",
    )[0]
    original = _daily_frame()
    store.write_unit("daily", unit.storage_parts, original, get_dataset_spec("daily"))
    _write_daily_checkpoint(
        data_dir, row_count=len(original), schema_hash=_schema_hash(original.columns)
    )

    # A valid Parquet replacement with different metadata must not remain READY
    # merely because the checkpoint status still says PASS.
    store.write_unit("daily", unit.storage_parts, _daily_frame(rows=1), get_dataset_spec("daily"))
    report = verify_market_corpus(
        data_dir,
        start_date="20240101",
        end_date="20240103",
        benchmark_code="000300.SH",
        datasets=("daily",),
        exchanges=("SSE",),
        checkpoint_path=data_dir / "state" / "market-bootstrap-checkpoints.json",
    )
    coverage = report.datasets[0]

    assert coverage.status == "FAIL"
    assert coverage.checkpoint_status == "PARTIAL"
    assert any("row_count" in value for value in report.integrity.checkpoint_mismatch)
    assert report.status == "NOT_READY"


def test_reference_findings_keep_unreliable_fields_unsupported() -> None:
    findings = {(value.dataset, value.field): value for value in reference_pit_findings()}

    for field in ("name", "status/list_status", "industry", "board/market"):
        assert findings[("stock_basic", field)].pit_confidence == "UNSUPPORTED_PIT"
    for field in ("name/start_date/end_date", "change_reason"):
        finding = findings[("namechange", field)]
        assert finding.pit_confidence == "UNSUPPORTED_PIT"
        assert "stable source identity" in finding.notes or "source identity" in finding.notes
