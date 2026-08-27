"""Adversarial point-in-time financial validation for #7.

These tests prove the future-function barrier: at as-of ``T`` a scan can only
use financial information that was publicly disclosed by ``T``, including
corrected or revised disclosures. All fixtures are synthetic and never depend
on a real token or on real NAS data.

Canonicalization alone is **not** future-safe; only the disclosure/knowledge
time selection in ``select_financial_as_of`` enforces the barrier.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround import __main__
from ashare_turnaround.pit.financial import (
    PIT_MAPPINGS,
    canonicalize_financial_frame,
    derive_single_quarter,
    query_financial_as_of,
    select_financial_as_of,
)


def _row(
    period_end: str,
    ann: str,
    update_flag: str,
    revenue: float,
    *,
    ts: str = "600000.SH",
    report_type: str = "1",
    comp_type: str = "1",
    end_type: str = "4",
) -> dict:
    return {
        "ts_code": ts,
        "ann_date": ann,
        "f_ann_date": ann,
        "end_date": period_end,
        "report_type": report_type,
        "comp_type": comp_type,
        "end_type": end_type,
        "update_flag": update_flag,
        "total_revenue": revenue,
    }


def _frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _revenue(frame: pd.DataFrame) -> list[float]:
    assert len(frame) == 1, f"expected one selected version, got {len(frame)}"
    return [float(frame.iloc[0]["total_revenue"])]


# --------------------------------------------------------------------------- #
# Case A: first disclosure -> normal query                                   #
# --------------------------------------------------------------------------- #


def test_case_a_first_disclosure_visible_only_on_or_after_availability() -> None:
    frame = _frame(_row("20251231", "20260320", "1", 100.0))

    assert query_financial_as_of("income", "600000.SH", "20260301", frame=frame).empty
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260320", frame=frame)
    ) == [100.0]
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260325", frame=frame)
    ) == [100.0]


# --------------------------------------------------------------------------- #
# Case B: original report -> subsequent revision                             #
# --------------------------------------------------------------------------- #


def test_case_b_revision_invisible_before_its_availability_then_selected() -> None:
    frame = _frame(
        _row("20251231", "20260320", "1", 100.0),
        _row("20251231", "20260415", "2", 110.0),
    )

    # Before the revision is disclosed, the original value is selected.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260401", frame=frame)
    ) == [100.0]
    # On and after the revision's availability date, the revised value wins.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260415", frame=frame)
    ) == [110.0]
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260420", frame=frame)
    ) == [110.0]


# --------------------------------------------------------------------------- #
# Case C: same report period, multiple same-day versions (tie)               #
# --------------------------------------------------------------------------- #


def test_case_c_same_day_versions_resolve_to_latest_update_flag() -> None:
    frame = _frame(
        _row("20251231", "20260320", "1", 100.0),
        _row("20251231", "20260320", "2", 110.0),
    )

    before = query_financial_as_of("income", "600000.SH", "20260319", frame=frame)
    on_day = query_financial_as_of("income", "600000.SH", "20260320", frame=frame)

    assert before.empty
    # Only one version is selected for the report identity; the earlier version
    # is not duplicated into the as-of result.
    assert len(on_day) == 1
    assert _revenue(on_day) == [110.0]


# --------------------------------------------------------------------------- #
# Case D: a future correction must not pollute a past as-of                  #
# --------------------------------------------------------------------------- #


def test_case_d_future_correction_is_invisible_to_a_past_as_of() -> None:
    frame = _frame(
        _row("20251231", "20260320", "1", 100.0),
        _row("20251231", "20270501", "3", 80.0),
    )

    # A correction disclosed in 2027 cannot be read at any 2026 as-of date.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260401", frame=frame)
    ) == [100.0]
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260420", frame=frame)
    ) == [100.0]
    # Only once the correction's availability date is reached does it apply.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20270501", frame=frame)
    ) == [80.0]


# --------------------------------------------------------------------------- #
# Case E: as-of boundary immediately before and on the first available date  #
# --------------------------------------------------------------------------- #


def test_case_e_boundary_before_and_on_first_and_revision_availability() -> None:
    frame = _frame(_row("20251231", "20260320", "1", 100.0))

    # Immediately before the first available date: empty.
    assert query_financial_as_of("income", "600000.SH", "20260319", frame=frame).empty
    # On the first available date: visible.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260320", frame=frame)
    ) == [100.0]

    revised = _frame(
        _row("20251231", "20260320", "1", 100.0),
        _row("20251231", "20260415", "2", 110.0),
    )
    # Boundary on the revision availability date: the day before keeps the
    # original; the revision date switches to the revised value.
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260414", frame=revised)
    ) == [100.0]
    assert _revenue(
        query_financial_as_of("income", "600000.SH", "20260415", frame=revised)
    ) == [110.0]


# --------------------------------------------------------------------------- #
# Cross-report-period: a later report period cannot satisfy an earlier as-of #
# --------------------------------------------------------------------------- #


def test_later_report_period_disclosed_after_as_of_is_invisible() -> None:
    frame = _frame(
        _row("20241231", "20250320", "1", 200.0),  # FY2024 disclosed 2025-03-20
        _row("20251231", "20260320", "1", 300.0),  # FY2025 disclosed 2026-03-20
    )

    early = query_financial_as_of("income", "600000.SH", "20250401", frame=frame)
    assert len(early) == 1
    assert _revenue(early) == [200.0]  # only FY2024; FY2025 not yet disclosed
    assert not early["end_date"].astype(str).eq("20251231").any()

    after = query_financial_as_of("income", "600000.SH", "20260320", frame=frame)
    assert len(after) == 2  # both report periods now visible
    assert set(after["total_revenue"].astype(float)) == {200.0, 300.0}


# --------------------------------------------------------------------------- #
# canonicalize != future-safe by itself                                      #
# --------------------------------------------------------------------------- #


def test_canonicalize_keeps_future_versions_but_selection_enforces_the_barrier() -> None:
    frame = _frame(
        _row("20251231", "20260320", "1", 100.0),
        _row("20251231", "20270501", "3", 80.0),
    )

    canonical = canonicalize_financial_frame("income", frame, retrieved_at="now")
    # Canonicalization keeps every raw version, including the future correction;
    # it does not, by itself, provide the future-function barrier.
    assert len(canonical) == 2
    assert canonical["actual_available_date"].dt.strftime("%Y%m%d").tolist() == [
        "20260320",
        "20270501",
    ]

    # The barrier is enforced only by disclosure-time selection.
    past = select_financial_as_of(canonical, ts_code="600000.SH", as_of_date="20260401")
    assert len(past) == 1
    assert _revenue(past) == [100.0]


# --------------------------------------------------------------------------- #
# Mapping evidence: status is recorded and a missing date is never invented #
# --------------------------------------------------------------------------- #


def test_canonical_mapping_records_evidence_status_and_never_invents_a_date() -> None:
    # income is a confirmed mapping with f_ann_date as the availability source.
    assert PIT_MAPPINGS["income"].semantic_status == "confirmed"
    assert PIT_MAPPINGS["income"].available_candidates == (
        "actual_available_date",
        "f_ann_date",
        "ann_date",
    )
    # fina_mainbz and disclosure_date.actual_date remain semantically unknown;
    # they are not silently upgraded to availability.
    assert PIT_MAPPINGS["fina_mainbz"].disclosure_fallback is True
    assert PIT_MAPPINGS["fina_mainbz"].semantic_status != "confirmed"
    assert "unknown" in PIT_MAPPINGS["disclosure_date"].notes.lower()

    frame = _frame(
        _row("20251231", "20260320", "1", 100.0),
    )
    canonical = canonicalize_financial_frame("income", frame)
    assert canonical["available_date_source"].tolist() == ["f_ann_date"]

    # A row with no usable availability date is excluded rather than fabricated.
    no_date = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "end_date": "20251231",
                "report_type": "1",
                "comp_type": "1",
                "end_type": "4",
                "update_flag": "1",
                "total_revenue": 100.0,
            }
        ]
    )
    canonical_no_date = canonicalize_financial_frame("income", no_date)
    assert pd.isna(canonical_no_date["actual_available_date"].iloc[0])
    assert query_financial_as_of(
        "income", "600000.SH", "20261231", frame=no_date
    ).empty


def test_fina_mainbz_without_disclosure_join_refuses_to_invent_availability() -> None:
    raw = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "end_date": ["20251231"],
            "bz_item": ["main"],
            "bz_sales": [10.0],
        }
    )
    canonical = canonicalize_financial_frame("fina_mainbz", raw)
    assert pd.isna(canonical["actual_available_date"].iloc[0])
    # With no disclosure join, the record cannot be selected at any as-of date.
    assert query_financial_as_of(
        "fina_mainbz", "600000.SH", "20261231", frame=raw
    ).empty


# --------------------------------------------------------------------------- #
# Cumulative quarterization: Q1/H1-Q1/Q3-H1/FY-Q3 and missing periods       #
# --------------------------------------------------------------------------- #


def _quarter_frame(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * len(values),
            "end_date": list(values.keys()),
            "revenue": list(values.values()),
        }
    )


def test_quarterization_full_year_q1_h1_minus_q1_q3_minus_h1_fy_minus_q3() -> None:
    frame = _quarter_frame(
        {"20250331": 10.0, "20250630": 23.0, "20250930": 35.0, "20251231": 50.0}
    )
    result = derive_single_quarter(frame, "revenue")
    assert result["single_quarter"].tolist() == [10.0, 13.0, 12.0, 15.0]


def test_quarterization_missing_q3_leaves_fy_single_quarter_unknown() -> None:
    frame = _quarter_frame(
        {"20250331": 10.0, "20250630": 23.0, "20251231": 50.0}
    )
    result = derive_single_quarter(frame, "revenue").sort_values("end_date", kind="stable")
    by_period = dict(zip(result["end_date"].astype(str), result["single_quarter"]))
    assert by_period["20250331"] == 10.0
    assert by_period["20250630"] == 13.0
    assert pd.isna(by_period["20251231"])  # FY-Q3 needs Q3


def test_quarterization_missing_q1_leaves_h1_single_quarter_unknown() -> None:
    frame = _quarter_frame(
        {"20250630": 23.0, "20250930": 35.0, "20251231": 50.0}
    )
    result = derive_single_quarter(frame, "revenue").sort_values("end_date", kind="stable")
    by_period = dict(zip(result["end_date"].astype(str), result["single_quarter"]))
    assert pd.isna(by_period["20250630"])  # H1-Q1 needs Q1
    assert by_period["20250930"] == 12.0  # Q3 - H1
    assert by_period["20251231"] == 15.0  # FY - Q3


def test_quarterization_rejects_duplicate_cumulative_periods() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20250331", "20250331"],
            "revenue": [10.0, 11.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate cumulative rows"):
        derive_single_quarter(frame, "revenue")


# --------------------------------------------------------------------------- #
# Reproducible PIT validation report                                         #
# --------------------------------------------------------------------------- #


def test_pit_validation_report_is_reproducible_and_documents_limitations(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASHARE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    assert __main__.main(["pit-check"]) == 0
    first = (tmp_path / "docs" / "pit-validation.md").read_text(encoding="utf-8")
    assert __main__.main(["pit-check"]) == 0
    second = (tmp_path / "docs" / "pit-validation.md").read_text(encoding="utf-8")

    assert first == second  # reproducible from synthetic + empty local store
    assert "PIT" in first
    assert "Synthetic version-chain" in first  # raw-field provenance documented
    assert "bounded" in first.lower()  # limitations are stated
