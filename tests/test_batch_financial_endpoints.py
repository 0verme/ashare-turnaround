from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround.pit.comparable import (
    PreparedComparableSeries,
    match_comparable_period,
    match_comparable_period_series,
    ttm_from_series,
    ttm_from_series_batch,
    validated_single_quarter_series,
)
from ashare_turnaround.pit.financial import canonicalize_financial_frame

CODE = "600000.SH"


def _single_quarters(periods: list[str], values: list[float]) -> pd.DataFrame:
    available = [
        (pd.Timestamp(period) + pd.Timedelta(days=30)).strftime("%Y%m%d")
        for period in periods
    ]
    return canonicalize_financial_frame(
        "income",
        pd.DataFrame(
            {
                "ts_code": [CODE] * len(periods),
                "end_date": periods,
                "revenue": values,
                "report_type": ["2"] * len(periods),
                "ann_date": available,
                "f_ann_date": available,
                "update_flag": ["0"] * len(periods),
            }
        ),
    )


def test_batch_yoy_and_qoq_equal_scalar_at_every_endpoint() -> None:
    frame = _single_quarters(
        [
            "20230331", "20230630", "20230930", "20231231",
            "20240331", "20240630", "20240930", "20241231", "20250331",
        ],
        [8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 13.0, 15.0, 16.0],
    )
    rows = frame.to_dict(orient="records")
    for kind in ("yoy", "qoq"):
        batch = match_comparable_period_series(
            frame,
            rows,
            comparison=kind,
            dataset="income",
            value_column="revenue",
            as_of_date="20250501",
        )
        scalar = tuple(
            match_comparable_period(
                frame,
                row,
                comparison=kind,
                dataset="income",
                value_column="revenue",
                as_of_date="20250501",
            )
            for row in rows
        )
        assert [item.as_dict() for item in batch] == [item.as_dict() for item in scalar]


def test_batch_falls_back_exactly_for_revision_future_and_semantic_mismatch() -> None:
    frame = _single_quarters(
        ["20240331", "20240630", "20250331", "20250630"],
        [10.0, 20.0, 15.0, 25.0],
    )
    revision = frame.iloc[[0]].copy()
    revision["revenue"] = 11.0
    revision["source_version_identity"] = "income:revision"
    revision["actual_available_date"] = pd.Timestamp("2025-07-01")
    combined = pd.concat([frame, revision], ignore_index=True)
    combined.loc[combined["report_period"].eq(pd.Timestamp("2024-06-30")), "unit"] = "cny:1000"
    rows = combined.to_dict(orient="records")
    batch = match_comparable_period_series(
        combined,
        rows,
        comparison="yoy",
        dataset="income",
        value_column="revenue",
        as_of_date="20250601",
    )
    scalar = [
        match_comparable_period(
            combined,
            row,
            comparison="yoy",
            dataset="income",
            value_column="revenue",
            as_of_date="20250601",
        )
        for row in rows
    ]
    assert [item.as_dict() for item in batch] == [item.as_dict() for item in scalar]


def test_batch_ttm_equals_scalar_at_every_endpoint() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": [CODE] * 9,
            "end_date": [
                "20230331", "20230630", "20230930", "20231231",
                "20240331", "20240630", "20240930", "20241231", "20250331",
            ],
            "revenue": [10.0, 25.0, 42.0, 60.0, 12.0, 29.0, 48.0, 70.0, 14.0],
            "report_type": ["1"] * 9,
            "ann_date": ["20250501"] * 9,
            "f_ann_date": ["20250501"] * 9,
            "update_flag": ["0"] * 9,
        }
    )
    series = validated_single_quarter_series(raw, "revenue", as_of_date="20250601")
    rows = series.to_dict(orient="records")
    batch = ttm_from_series_batch(
        series,
        dataset="income",
        ends=rows,
        as_of_date="20250601",
        metric="revenue_ttm",
    )
    scalar = tuple(
        ttm_from_series(
            series,
            dataset="income",
            end=row,
            as_of_date="20250601",
            metric="revenue_ttm",
        )
        for row in rows
    )
    assert [item.as_dict() for item in batch] == [item.as_dict() for item in scalar]


def test_prepared_rows_are_immutable_and_reuse_references() -> None:
    frame = _single_quarters(
        ["20240331", "20240630", "20240930", "20241231", "20250331"],
        [10.0, 20.0, 30.0, 40.0, 15.0],
    )
    prepared = PreparedComparableSeries.prepare(
        frame, dataset="income", value_column="revenue"
    )
    with pytest.raises(TypeError):
        prepared.rows[0].row["revenue"] = 0.0  # type: ignore[index]

    rows = frame.to_dict(orient="records")
    matches = match_comparable_period_series(
        frame,
        rows,
        comparison="qoq",
        dataset="income",
        value_column="revenue",
        prepared_series=prepared,
    )
    assert matches[2].current_reference is not None
    assert matches[2].current_reference.as_dict() == prepared.rows[2].reference.as_dict()
    assert [item.as_dict() for item in matches] == [
        match_comparable_period(
            frame, row, comparison="qoq", dataset="income", value_column="revenue"
        ).as_dict()
        for row in rows
    ]
