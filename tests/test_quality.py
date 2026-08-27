from __future__ import annotations

import pandas as pd

from ashare_turnaround.datasets.specs import get_dataset_spec
from ashare_turnaround.quality import check_frame_quality, compare_field_sets


def test_quality_checks_required_identity_partition_and_dates() -> None:
    frame = pd.DataFrame(
        {
            "exchange": ["SSE", "SSE"],
            "cal_date": ["20240101", "not-a-date"],
            "is_open": [1, 1],
        }
    )

    result = check_frame_quality("trade_cal", frame, get_dataset_spec("trade_cal"))

    assert result.duplicate_identity_rows == 0
    assert result.null_partition_rows == 0
    assert result.bad_date_values == (("cal_date", 1),)
    assert "bad_dates=cal_date:1" in result.warnings


def test_quality_reports_duplicate_and_schema_relation() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "trade_date": ["20240101", "20240101"],
            "close": [1.0, 1.0],
            "extra": [1, 2],
        }
    )

    result = check_frame_quality(
        "daily_basic",
        frame,
        get_dataset_spec("daily_basic"),
        expected_fields={"ts_code", "trade_date", "close"},
    )

    assert result.duplicate_identity_rows == 2
    assert result.schema_relation == "superset"
    assert "duplicate_identity_rows=2" in result.warnings
    assert "schema_drift=superset" in result.warnings


def test_quality_reports_null_identity_keys_without_repairing_rows() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", None],
            "trade_date": ["20240101", "20240102"],
            "close": [1.0, 2.0],
        }
    )

    result = check_frame_quality("daily_basic", frame, get_dataset_spec("daily_basic"))

    assert result.null_identity_rows == 1
    assert result.null_key_counts == (("ts_code", 1),)
    assert "null_identity_rows=1" in result.warnings


def test_compare_field_sets_distinguishes_all_drift_shapes() -> None:
    assert compare_field_sets({"a"}, {"a"}) == "same"
    assert compare_field_sets({"a", "b"}, {"a"}) == "superset"
    assert compare_field_sets({"a"}, {"a", "b"}) == "subset"
    assert compare_field_sets({"a", "b"}, {"a", "c"}) == "different"
