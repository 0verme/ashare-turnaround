from __future__ import annotations

import pandas as pd

from ashare_turnaround.pit.financial import (
    canonicalize_financial_frame,
    derive_single_quarter,
    find_financial_revision_candidates,
    query_financial_as_of,
    validate_revision_candidate,
)


def _versions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "ann_date": "20260320",
                "f_ann_date": "20260320",
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "1",
                "total_revenue": 100.0,
            },
            {
                "ts_code": "600000.SH",
                "ann_date": "20260415",
                "f_ann_date": "20260415",
                "end_date": "20251231",
                "report_type": "1",
                "update_flag": "2",
                "total_revenue": 110.0,
            },
        ]
    )


def test_pit_hides_future_and_selects_revision_by_as_of_date() -> None:
    raw = _versions()
    canonical = canonicalize_financial_frame("income", raw, retrieved_at="now")

    assert canonical["report_period"].dt.strftime("%Y-%m-%d").tolist() == ["2025-12-31"] * 2
    assert canonical["available_date_source"].tolist() == ["f_ann_date", "f_ann_date"]
    assert query_financial_as_of("income", "600000.SH", "20260301", frame=raw).empty

    first = query_financial_as_of("income", "600000.SH", "20260325", frame=raw)
    before_revision = query_financial_as_of("income", "600000.SH", "20260401", frame=raw)
    after_revision = query_financial_as_of("income", "600000.SH", "20260420", frame=raw)
    assert first["total_revenue"].tolist() == [100.0]
    assert before_revision["total_revenue"].tolist() == [100.0]
    assert after_revision["total_revenue"].tolist() == [110.0]


def test_pit_can_use_disclosure_date_for_main_business_data() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "end_date": ["20251231"],
            "bz_item": ["main"],
            "bz_sales": [10.0],
        }
    )
    disclosure = pd.DataFrame(
        {"ts_code": ["600000.SH"], "end_date": ["20251231"], "actual_date": ["20260320"]}
    )

    result = query_financial_as_of(
        "fina_mainbz", "600000.SH", "20260325", frame=raw, disclosure_frame=disclosure
    )
    assert len(result) == 1
    assert result["available_date_source"].tolist() == ["disclosure_date.actual_date"]


def test_single_quarter_bridge_for_cumulative_values() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * 4,
            "end_date": ["20250331", "20250630", "20250930", "20251231"],
            "revenue": [10.0, 23.0, 35.0, 50.0],
        }
    )

    result = derive_single_quarter(frame, "revenue")
    assert result["single_quarter"].tolist() == [10.0, 13.0, 12.0, 15.0]


def test_real_style_revision_candidate_passes_all_as_of_boundaries() -> None:
    frame = pd.DataFrame(
        [
            {
                "ts_code": "300001.SZ",
                "ann_date": "20170417",
                "f_ann_date": "20170417",
                "end_date": "20161231",
                "report_type": "1",
                "comp_type": "1",
                "end_type": "4",
                "update_flag": "0",
                "net_profit": 194235271.97,
            },
            {
                "ts_code": "300001.SZ",
                "ann_date": "20170417",
                "f_ann_date": "20211217",
                "end_date": "20161231",
                "report_type": "1",
                "comp_type": "1",
                "end_type": "4",
                "update_flag": "1",
                "net_profit": 167226201.78,
            },
        ]
    )

    candidates = find_financial_revision_candidates("cashflow", frame)
    check = validate_revision_candidate(candidates[0], value_column="net_profit")

    assert len(candidates) == 1
    assert candidates[0].available_dates[0].strftime("%Y-%m-%d") == "2017-04-17"
    assert candidates[0].available_dates[1].strftime("%Y-%m-%d") == "2021-12-17"
    assert check.status == "PASS"
    assert check.checks == {
        "before_first": True,
        "after_first": True,
        "before_revision": True,
        "after_revision": True,
    }
    assert check.first_value == 194235271.97
    assert check.revised_value == 167226201.78
