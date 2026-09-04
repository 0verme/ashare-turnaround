from __future__ import annotations

import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from ashare_turnaround.features.financial_context import FinancialSemanticContext
from ashare_turnaround.pit.comparable import validated_single_quarter_series
from ashare_turnaround.replay_cache import ReplaySnapshotCache, replay_cache_scope

CODE = "600000.SH"
AS_OF = pd.Timestamp("2025-05-01")


def _income() -> pd.DataFrame:
    periods = [
        "20230331",
        "20230630",
        "20230930",
        "20231231",
        "20240331",
        "20240630",
        "20240930",
        "20241231",
    ]
    return pd.DataFrame(
        {
            "ts_code": [CODE] * len(periods),
            "end_date": periods,
            "ann_date": ["20250401"] * len(periods),
            "f_ann_date": ["20250401"] * len(periods),
            "report_type": ["1"] * len(periods),
            "update_flag": ["0"] * len(periods),
            "revenue": [10, 25, 45, 70, 12, 30, 54, 84],
            "n_income_attr_p": [-2, -1, 2, 5, -1, 2, 6, 11],
            "operate_profit": [0, 2, 5, 9, 1, 4, 8, 14],
        }
    )


def test_wide_quarterization_equals_existing_field_contract() -> None:
    frames = {"income": _income()}
    cache = ReplaySnapshotCache.from_frames(frames, as_of=AS_OF)
    with replay_cache_scope(cache):
        context = FinancialSemanticContext.prepare(frames, CODE, AS_OF)
        wide, columns = context.single_quarter_history(
            "income", ("revenue", "n_income_attr_p", "operate_profit")
        )
        history = context.history("income")
        for field_name, wide_column in columns.items():
            old = validated_single_quarter_series(
                history,
                field_name,
                dataset_kind="income",
                as_of_date=AS_OF,
            )
            assert_series_equal(
                wide["source_version_identity"],
                old["source_version_identity"],
                check_names=False,
            )
            for suffix, old_column in (
                ("", "comparable_value"),
                ("_status", "comparable_status"),
                ("_reason", "comparable_reason"),
                ("_raw", field_name),
            ):
                assert_series_equal(
                    wide[f"{wide_column}{suffix}"], old[old_column], check_names=False
                )


def test_context_frames_and_wide_projection_are_mutation_safe() -> None:
    frames = {"income": _income()}
    cache = ReplaySnapshotCache.from_frames(frames, as_of=AS_OF)
    with replay_cache_scope(cache):
        context = FinancialSemanticContext.prepare(frames, CODE, AS_OF)
        history_before = context.history("income").copy(deep=True)
        prepared = context.single_quarter_history(
            "income", ("revenue", "n_income_attr_p")
        )
        wide_before = prepared[0].copy(deep=True)

        revenue = context.single_quarter_projection(prepared, "revenue")
        profit = context.single_quarter_projection(prepared, "n_income_attr_p")

        assert revenue is context.single_quarter_projection(prepared, "revenue")
        assert profit is context.single_quarter_projection(prepared, "n_income_attr_p")
        assert revenue is not profit
        assert_frame_equal(context.history("income"), history_before)
        assert_frame_equal(prepared[0], wide_before)
        assert revenue["comparable_source_field"].eq("revenue").all()
        assert profit["comparable_source_field"].eq("n_income_attr_p").all()


def test_candidate_clear_drops_dataframe_identity_caches() -> None:
    cache = ReplaySnapshotCache.from_frames({"income": _income()}, as_of=AS_OF)
    cache.trend_series[(123, "income")] = (pd.DataFrame({"stale": [1]}), None)
    cache.clear_candidate_state()
    assert cache.trend_series == {}
