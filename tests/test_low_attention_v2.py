"""Low Attention v2 contract tests (issue #29).

These tests prove the semantic calibration, not just that "tests pass":

* self-history percentiles never include the current observation in their
  own baseline;
* cross-sectional percentiles are PIT-safe, resolved against a declared
  population at the same session, and tie-handled deterministically;
* abnormal volume uses a strictly prior baseline;
* extreme inactivity (low liquidity) is never promoted into a low-attention
  opportunity;
* missing data is an explicit ``unknown`` state, never low attention;
* the v1 ``attention_score`` (and therefore the production Turnaround Score
  v1) is untouched by v2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ashare_turnaround.features import compute_attention_features, compute_low_attention_v2
from ashare_turnaround.features.low_attention import (
    SAMPLE_CLASS_A,
    SAMPLE_CLASS_B,
    SAMPLE_CLASS_C,
    SAMPLE_CLASS_NA,
    AbnormalVolumeConfig,
    CrossSectionConfig,
    LowAttentionConfig,
    SelfWindowConfig,
    build_cross_section_population,
    classify_low_attention_case,
    low_attention_sample_report,
    low_attention_sample_report_markdown,
)
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.replay import ReplayConfig, run_replay_frames
from ashare_turnaround.scanner.report import candidate_report
from ashare_turnaround.scanner.score import score_feature_vector
from ashare_turnaround.scanner.universe import UniverseConfig, build_investable_universe

AS_OF = "20250630"
SEMANTIC = "low-attention-v2.0.0"


def _dates(end: str = AS_OF, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="B")


@np.errstate(all="ignore")
def _symbol_profile(i: int, n: int) -> dict[str, np.ndarray]:
    """Deterministic activity profile per synthetic symbol."""
    k = np.arange(n, dtype=float)
    if i == 0:  # A: low attention, declining, but investable
        return {
            "vol": 900.0 - 1.0 * k,
            "turnover": 0.20 - 0.0003 * k,
            "amount": 20000.0 - 8.0 * k,
        }
    if i == 1:  # B: extreme illiquid garbage (amount below research floor)
        return {
            "vol": np.full(n, 1.0),
            "turnover": np.full(n, 0.0001),
            "amount": np.full(n, 0.02),
        }
    if i == 2:  # C: missing turnover/amount everywhere
        return {
            "vol": np.full(n, 100.0),
            "turnover": np.full(n, np.nan),
            "amount": np.full(n, np.nan),
        }
    if i == 3:  # busy, high attention
        return {
            "vol": 40000.0 + 5.0 * k,
            "turnover": 4.0 + 0.001 * k,
            "amount": 150000.0 + 10.0 * k,
        }
    base = 1000.0 * (i % 7) + 2000.0
    return {
        "vol": base * (1.0 + 0.002 * np.sin(k)),
        "turnover": (0.5 + 0.1 * (i % 5)) * (1.0 + 0.001 * k),
        "amount": np.full(n, base * 12.0),
    }


def _market_frame(
    n_symbols: int = 34,
    *,
    end: str = AS_OF,
    drop_session: tuple[str, int] | None = None,
    add_future_rows: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Synthetic daily/daily_basic market snapshot.

    vol belongs to ``daily``, turnover_rate/amount to ``daily_basic``.
    """
    dates = _dates(end=end)
    rows: list[tuple[str, str, float, float | None, float | None]] = []
    for i in range(n_symbols):
        code = f"{600000 + i:06d}.SH"
        profile = _symbol_profile(i, len(dates))
        for j, ts in enumerate(dates):
            if drop_session is not None and i == drop_session[0] and j == drop_session[1]:
                continue  # symbol does not trade the final session (suspension)
            turnover = profile["turnover"][j]
            amount = profile["amount"][j]
            rows.append(
                (
                    code,
                    ts.strftime("%Y%m%d"),
                    float(profile["vol"][j]),
                    None if pd.isna(turnover) else float(turnover),
                    None if pd.isna(amount) else float(amount),
                )
            )
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    if add_future_rows is not None and not add_future_rows.empty:
        frame = pd.concat([frame, add_future_rows], ignore_index=True, sort=False)
    return frame


def _code(i: int) -> str:
    return f"{600000 + i:06d}.SH"


def _default_config(**kwargs) -> LowAttentionConfig:
    return LowAttentionConfig(
        self_window=kwargs.pop(
            "self_window", SelfWindowConfig(window=252, min_valid=21, min_listing_days=120)
        ),
        cross_section=kwargs.pop(
            "cross_section",
            CrossSectionConfig(
                population_scope="tradable_market",
                tie_convention="inclusive",
                min_population=2,
            ),
        ),
        abnormal_volume=kwargs.pop(
            "abnormal_volume",
            AbnormalVolumeConfig(
                baseline_window=60,
                min_observations=20,
                max_abnormal_cap=10.0,
                max_staleness_days=10,
            ),
        ),
        low_attention_cross_percentile=kwargs.pop("low_attention_cross_percentile", 0.30),
        **kwargs,
    )


def _everyone(
    frame: pd.DataFrame, config: LowAttentionConfig | None = None
) -> dict[str, FeatureVector]:
    config = config or _default_config()
    return {
        code: compute_low_attention_v2(frame, code, AS_OF, config=config)
        for code in sorted(frame["ts_code"].drop_duplicates().astype(str))
    }


# ---------------------------------------------------------------------------
# self-history
# ---------------------------------------------------------------------------


def test_self_percentile_excludes_current_session_from_its_own_baseline() -> None:
    dates = _dates()
    prior_turnover = [0.10, 0.20, 0.30, 0.40, 0.50]
    # current 0.4: if baseline includes current, (<= 0.4) over 6 values = 5/6
    # if baseline strictly prior, (<= 0.4) over 5 prior values = 4/5 = 0.8
    turnover = [*prior_turnover, 0.40]
    rows = [
        (
            "600999.SH",
            ts.strftime("%Y%m%d"),
            1000.0,
            value,
            10_000.0,
        )
        for ts, value in zip(dates[: len(turnover)], turnover)
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config(
        self_window=SelfWindowConfig(window=10, min_valid=5, min_listing_days=0)
    )
    vector = compute_low_attention_v2(
        frame, "600999.SH", dates[6].strftime("%Y%m%d"), config=config
    )
    assert vector.values["self_turnover_percentile"] == pytest.approx(0.8)
    ev = vector.evidence["self_turnover_percentile"]
    assert ev.metadata["valid_observation_count"] == 5


def test_insufficient_self_history_is_unknown() -> None:
    dates = _dates()
    rows = [
        ("600998.SH", ts.strftime("%Y%m%d"), 100.0, 1.0, 5000.0)
        for ts in dates[:5]
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config(self_window=SelfWindowConfig(window=252, min_valid=25))
    vector = compute_low_attention_v2(frame, "600998.SH", AS_OF, config=config)
    assert vector.values["self_turnover_percentile"] is None
    assert (
        vector.evidence["self_turnover_percentile"].metadata["warnings"]
        == "insufficient_self_history"
    )


def test_new_listing_is_unknown() -> None:
    frame = _market_frame()
    config = _default_config(self_window=SelfWindowConfig(window=252, min_valid=21))
    # listed only 30 days before as-of -> below min_listing_days=120
    list_date = "20250601"
    vector = compute_low_attention_v2(frame, _code(4), AS_OF, config=config, list_date=list_date)
    assert vector.values["self_turnover_percentile"] is None
    assert vector.evidence["self_turnover_percentile"].metadata["warnings"] == "new_listing"
    cls, reasons = classify_low_attention_case(vector, config=config)
    assert cls == SAMPLE_CLASS_B
    assert "new_listing" in vector.evidence["liquidity_eligible"].metadata["reasons"]


# ---------------------------------------------------------------------------
# session status / suspension / stale / missing
# ---------------------------------------------------------------------------


def test_suspended_stock_has_explicit_session_status_and_no_cross_section() -> None:
    dates = _dates()
    last_index = len(dates) - 1
    # symbol 600001.SH does not trade the final session
    frame = _market_frame(drop_session=(1, last_index))
    vector = compute_low_attention_v2(frame, _code(1), AS_OF, config=_default_config())
    assert vector.values["session_status"] == "suspended_session"
    assert vector.values["cross_section_turnover_percentile"] is None
    assert (
        vector.evidence["cross_section_turnover_percentile"].metadata["warnings"]
        == "no_observation_at_session"
    )
    # current-session metrics must not be silently computed from stale volume
    assert vector.values["abnormal_volume"] is None
    assert vector.values["attention_baseline_change"] is None
    assert (
        vector.evidence["abnormal_volume"].metadata["warnings"] == "suspended_session"
    )
    assert vector.values["liquidity_eligible"] is False
    cls, reasons = classify_low_attention_case(vector)
    assert cls == SAMPLE_CLASS_B


def test_stale_data_is_flagged() -> None:
    frame = _market_frame()
    stale_code = _code(5)
    stale_rows = frame.loc[frame["ts_code"].eq(stale_code)].copy()
    # chop the last 60 sessions so the latest observation is old
    cut = stale_rows.sort_values("trade_date").iloc[:-60]
    frame = pd.concat(
        [frame.loc[~frame["ts_code"].eq(stale_code)], cut], ignore_index=True, sort=False
    )
    vector = compute_low_attention_v2(frame, stale_code, AS_OF, config=_default_config())
    assert vector.values["session_status"] == "stale"
    assert vector.evidence["session_status"].metadata["staleness_days"] > 10
    assert vector.values["abnormal_volume"] is None
    assert vector.evidence["abnormal_volume"].metadata["warnings"] == "stale_data"
    assert vector.values["liquidity_eligible"] is False
    cls, reasons = classify_low_attention_case(vector)
    assert cls == SAMPLE_CLASS_B


def test_missing_observations_are_explicit_unknown_not_low_attention() -> None:
    frame = _market_frame()
    vector = compute_low_attention_v2(frame, _code(2), AS_OF, config=_default_config())
    assert vector.values["self_turnover_percentile"] is None
    assert vector.values["cross_section_turnover_percentile"] is None
    assert vector.values["low_attention_v2_score"] is None
    cls, reasons = classify_low_attention_case(vector)
    assert cls == SAMPLE_CLASS_C
    assert "insufficient_attention_evidence" in reasons


def test_no_data_at_all() -> None:
    frame = pd.DataFrame(columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    vector = compute_low_attention_v2(frame, _code(0), AS_OF, config=_default_config())
    assert vector.values["session_status"] == "no_data"
    assert vector.values["low_attention_v2_score"] is None
    (cls, reasons) = classify_low_attention_case(vector)
    assert cls == SAMPLE_CLASS_C


# ---------------------------------------------------------------------------
# cross-sectional
# ---------------------------------------------------------------------------


def test_cross_section_population_is_the_as_of_session_and_investable_scope_filters() -> None:
    frame = _market_frame()
    config = _default_config()
    population = build_cross_section_population(
        frame, as_of_date=AS_OF, config=config.cross_section
    )
    last_session = sorted(frame["trade_date"].unique())[-1]
    assert set(population["trade_date"]) == {last_session}
    assert population["ts_code"].nunique() == population.shape[0]  # one row per symbol

    investable = {_code(0), _code(1), _code(3)}
    restricted = build_cross_section_population(
        frame,
        as_of_date=AS_OF,
        config=CrossSectionConfig(
            population_scope="investable_universe", tie_convention="inclusive", min_population=2
        ),
        investable_codes=investable,
    )
    assert set(restricted["ts_code"]) == investable


def test_cross_section_ties_share_inclusive_percentile_deterministically() -> None:
    frame = _market_frame()
    config = _default_config()
    population = build_cross_section_population(
        frame, as_of_date=AS_OF, config=config.cross_section
    )
    n = len(population)
    # pick the symbol with the lowest turnover and duplicate another symbol's value
    low_code = _code(0)
    v_a = compute_low_attention_v2(frame, low_code, AS_OF, config=config)
    v_b = compute_low_attention_v2(frame, low_code, AS_OF, config=config)
    assert v_a.as_dict() == v_b.as_dict()  # deterministic
    assert (
        v_a.values["cross_section_turnover_percentile"]
        == v_b.values["cross_section_turnover_percentile"]
    )
    # ties: two symbols sharing the same turnover percentile is a fixed integer multiple
    p = v_a.values["cross_section_turnover_percentile"]
    assert 0.0 <= p <= 1.0
    # inclusive convention: identical values must map to identical percentiles
    assert v_a.evidence["cross_section_turnover_percentile"].metadata[
        "population_count"
    ] == n - 1  # 34 symbols, one carries NaN turnover (missing) at the session

    # inclusive convention: identical values must map to identical percentiles
    # duplicate symbol 3's turnover value onto a second symbol's final row
    duplicated = frame.copy()
    high_code = _code(3)
    high_value = duplicated.loc[
        (duplicated["ts_code"].eq(high_code)) & (duplicated["trade_date"].eq(AS_OF)),
        "turnover_rate",
    ].iloc[0]
    duplicated.loc[
        (duplicated["ts_code"].eq(_code(9))) & (duplicated["trade_date"].eq(AS_OF)),
        "turnover_rate",
    ] = high_value
    v_high = compute_low_attention_v2(duplicated, high_code, AS_OF, config=config)
    v_twin = compute_low_attention_v2(duplicated, _code(9), AS_OF, config=config)
    assert (
        v_high.values["cross_section_turnover_percentile"]
        == v_twin.values["cross_section_turnover_percentile"]
    )


def test_historical_universe_state_is_point_in_time() -> None:
    frame = _market_frame(drop_session=(5, len(_dates()) - 1))  # 600005.SH suspended at session
    config = _default_config(
        cross_section=CrossSectionConfig(
            population_scope="investable_universe", tie_convention="inclusive", min_population=2
        )
    )
    # as-of universe: only symbols 0,1,3 are investable; 600005 is not in it
    investable = {_code(0), _code(1), _code(3)}
    vector = compute_low_attention_v2(
        frame, _code(0), AS_OF, config=config, investable_codes=investable
    )
    assert vector.evidence["cross_section_turnover_percentile"].metadata["population_count"] == 3
    # adding a non-trading member to the universe changes nothing: the
    # population is anchored at the as-of session, rows only count if they
    # exist at that session.
    investable_future = set(investable) | {_code(5)}  # trades nowhere near as-of
    vector_future = compute_low_attention_v2(
        frame, _code(0), AS_OF, config=config, investable_codes=investable_future
    )
    assert (
        vector_future.evidence["cross_section_turnover_percentile"].metadata["population_count"]
        == 3
    )


def test_cross_section_population_below_minimum_is_unknown() -> None:
    frame = _market_frame()
    config = _default_config(
        cross_section=CrossSectionConfig(
            population_scope="tradable_market", tie_convention="inclusive", min_population=1000
        )
    )
    vector = compute_low_attention_v2(frame, _code(0), AS_OF, config=config)
    assert vector.values["cross_section_turnover_percentile"] is None
    assert (
        vector.evidence["cross_section_turnover_percentile"].metadata["warnings"]
        == "insufficient_population"
    )


# ---------------------------------------------------------------------------
# PIT cutoff / future data
# ---------------------------------------------------------------------------


def test_pit_cutoff_ignores_future_observations() -> None:
    frame = _market_frame()
    config = _default_config()
    before = compute_low_attention_v2(frame, _code(0), AS_OF, config=config)

    future_dates = pd.date_range("20250701", "20250705", freq="B")
    future_rows = pd.DataFrame(
        {
            "ts_code": [_code(0)] * len(future_dates),
            "trade_date": [ts.strftime("%Y%m%d") for ts in future_dates],
            "vol": [9_999_999.0] * len(future_dates),
            "turnover_rate": [99.0] * len(future_dates),
            "amount": [99_999_999.0] * len(future_dates),
        }
    )
    after = compute_low_attention_v2(
        _market_frame(add_future_rows=future_rows), _code(0), AS_OF, config=config
    )
    assert after.values["self_turnover_percentile"] == before.values["self_turnover_percentile"]
    assert (
        after.values["cross_section_turnover_percentile"]
        == before.values["cross_section_turnover_percentile"]
    )
    assert after.values["session_status"] == before.values["session_status"] == "traded"


# ---------------------------------------------------------------------------
# abnormal volume
# ---------------------------------------------------------------------------


def test_abnormal_volume_uses_prior_baseline_only() -> None:
    dates = _dates()
    n = 80
    baseline_vol = 100.0
    vols = [baseline_vol] * (n - 1) + [800.0]  # current is 8x the prior baseline
    rows = [
        ("600997.SH", ts.strftime("%Y%m%d"), vol, 1.0, 5000.0)
        for ts, vol in zip(dates[:n], vols)
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config(
        abnormal_volume=AbnormalVolumeConfig(
            baseline_window=60, min_observations=20, max_abnormal_cap=10.0, max_staleness_days=10
        )
    )
    vector = compute_low_attention_v2(
        frame, "600997.SH", dates[n - 1].strftime("%Y%m%d"), config=config
    )
    assert vector.values["abnormal_volume"] == pytest.approx(8.0)
    meta = vector.evidence["abnormal_volume"].metadata
    assert meta["baseline_median_volume"] == pytest.approx(100.0)
    assert meta["current_volume"] == pytest.approx(800.0)
    assert meta["valid_observation_count"] == 60


def test_abnormal_volume_extreme_outlier_is_capped_and_flagged() -> None:
    dates = _dates()
    n = 80
    vols = [100.0] * (n - 1) + [5_000_000.0]
    rows = [
        ("600996.SH", ts.strftime("%Y%m%d"), vol, 1.0, 5000.0)
        for ts, vol in zip(dates[:n], vols)
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config()
    vector = compute_low_attention_v2(
        frame, "600996.SH", dates[n - 1].strftime("%Y%m%d"), config=config
    )
    assert vector.values["abnormal_volume"] == pytest.approx(10.0)
    assert vector.evidence["abnormal_volume"].metadata["capped"] is True
    assert "abnormal_volume_capped" in vector.risk_flags


def test_abnormal_volume_zero_baseline_is_unknown() -> None:
    dates = _dates()
    n = 80
    vols = [0.0] * (n - 1) + [100.0]
    rows = [
        ("600995.SH", ts.strftime("%Y%m%d"), vol, 1.0, 5000.0)
        for ts, vol in zip(dates[:n], vols)
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config()
    vector = compute_low_attention_v2(
        frame, "600995.SH", dates[n - 1].strftime("%Y%m%d"), config=config
    )
    assert vector.values["abnormal_volume"] is None
    assert vector.evidence["abnormal_volume"].metadata["warnings"] == "zero_baseline"


def test_attention_baseline_change_is_turnover_vs_own_baseline() -> None:
    dates = _dates()
    n = 80
    turnover = [1.0] * (n - 1) + [0.25]  # attention fading to 25% of baseline
    rows = [
        ("600994.SH", ts.strftime("%Y%m%d"), 100.0, t, 5000.0)
        for ts, t in zip(dates[:n], turnover)
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"])
    config = _default_config()
    vector = compute_low_attention_v2(
        frame, "600994.SH", dates[n - 1].strftime("%Y%m%d"), config=config
    )
    assert vector.values["attention_baseline_change"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# liquidity separation (issue #29 anti-bypass core invariant)
# ---------------------------------------------------------------------------


def test_extreme_inactivity_never_produces_a_low_attention_opportunity() -> None:
    frame = _market_frame()
    config = _default_config()
    vectors = _everyone(frame, config)

    garbage = vectors[_code(1)]
    normal_low = vectors[_code(0)]
    active = vectors[_code(3)]

    # the garbage stock looks "most inactive" by raw proxies
    assert garbage.values["cross_section_turnover_percentile"] < normal_low.values[
        "cross_section_turnover_percentile"
    ]
    assert garbage.values["liquidity_eligible"] is False
    # ... but it is labelled B (not eligible), never A (opportunity)
    assert classify_low_attention_case(garbage, config=config)[0] == SAMPLE_CLASS_B
    assert classify_low_attention_case(normal_low, config=config)[0] == SAMPLE_CLASS_A
    assert classify_low_attention_case(active, config=config)[0] == SAMPLE_CLASS_NA

    # the naive research aggregate may be high for the inactive stock; the
    # classification contract must keep it out of the opportunity bucket.
    assert garbage.values["low_attention_v2_score"] is not None
    assert garbage.values["low_attention_v2_score"] > 0.0
    assert garbage.values["low_attention_v2_opportunity"] is False
    assert garbage.metadata["low_attention_v2"]["gated_opportunity"] is False


def test_investable_universe_liquidity_floor_excludes_the_illiquid_name() -> None:
    frame = _market_frame()
    stock_basic = pd.DataFrame(
        {
            "ts_code": [_code(0), _code(1), _code(3)],
            "symbol": ["600000", "600001", "600003"],
            "name": ["LowAttn", "Garbage", "Active"],
            "list_date": ["20100101", "20100101", "20100101"],
            "list_status": ["L", "L", "L"],
        }
    )
    universe = build_investable_universe(
        stock_basic,
        as_of_date=AS_OF,
        daily_basic=frame[["ts_code", "trade_date", "amount"]].rename(
            columns={"amount": "amount"}
        ),
        config=UniverseConfig(min_financial_periods=0, min_average_amount=1.0),
    )
    decisions = {decision.ts_code: decision for decision in universe.decisions}
    assert decisions[_code(1)].included is False
    assert decisions[_code(1)].reason == "low_liquidity"
    assert set(universe.included["ts_code"]) == {_code(0), _code(3)}


# ---------------------------------------------------------------------------
# v1/v2 boundary
# ---------------------------------------------------------------------------


def test_v2_is_namespaced_and_never_shadows_v1_attention_score() -> None:
    frame = _market_frame()
    config = _default_config()
    v2 = compute_low_attention_v2(frame, _code(0), AS_OF, config=config)
    assert v2.version == SEMANTIC
    assert "attention_score" not in v2.values
    assert "self_turnover_percentile" in v2.values
    assert "cross_section_turnover_percentile" in v2.values

    market = frame.rename(columns={"turnover_rate": "turnover_rate"})
    v1 = compute_attention_features(market, _code(0), AS_OF)
    assert v1.version == "features-v1"
    assert v1.values["attention_score"] is not None

    # production score consumes v1 attention_score; v2 vector keeps boundary
    scored = score_feature_vector(v2)
    assert scored.components["attention_score"] is None
    # v1 vector still carries attention_score into the production score
    assert score_feature_vector(v1).components["attention_score"] is not None

    # Generic FeatureVector.merge also preserves the legacy colliding field;
    # v2 abnormal volume is explicitly namespaced.
    legacy_abnormal = v1.values["abnormal_volume"]
    v1.merge(v2)
    assert v1.values["abnormal_volume"] == legacy_abnormal
    assert "low_attention_v2_abnormal_volume" in v1.values
    assert v1.comparable_period_contract_version == "comparable-period-v1"
    assert v1.trend_contract_version == "turnaround-trend-v2"
    assert v1.feature_metadata["low_attention_v2"]["attention_contract_version"] == SEMANTIC
    assert score_feature_vector(v1).components["attention_score"] is not None


def test_v2_score_is_unknown_when_any_core_component_is_missing() -> None:
    frame = _market_frame()
    vector = compute_low_attention_v2(frame, _code(2), AS_OF, config=_default_config())
    assert vector.values["low_attention_v2_score"] is None


# ---------------------------------------------------------------------------
# evidence completeness
# ---------------------------------------------------------------------------


def test_every_proxy_carries_full_evidence_metadata() -> None:
    frame = _market_frame()
    vector = compute_low_attention_v2(frame, _code(0), AS_OF, config=_default_config())
    for name in (
        "self_turnover_percentile",
        "cross_section_turnover_percentile",
        "abnormal_volume",
        "attention_baseline_change",
    ):
        evidence = vector.evidence[name]
        meta = evidence.metadata
        assert meta["semantic_version"] == SEMANTIC
        assert meta["as_of_date"] == AS_OF
        assert meta["observation_date"] == AS_OF
        assert meta["source"]
        assert "semantic_version" in meta
        # valid/population counts and warnings are queryable
        assert "valid_observation_count" in meta or "population_count" in meta
    cross = vector.evidence["cross_section_turnover_percentile"].metadata
    assert cross["population_count"] > 0
    assert cross["tie_convention"] == "inclusive"
    assert cross["population_scope"] == "tradable_market"


# ---------------------------------------------------------------------------
# deterministic
# ---------------------------------------------------------------------------


def test_deterministic_output_across_runs() -> None:
    frame = _market_frame()
    config = _default_config()
    a = compute_low_attention_v2(frame, _code(0), AS_OF, config=config)
    b = compute_low_attention_v2(frame, _code(0), AS_OF, config=config)
    assert a.as_dict() == b.as_dict()


# ---------------------------------------------------------------------------
# sample report
# ---------------------------------------------------------------------------


def test_sample_report_buckets_a_b_c_and_no_trade_recommendation() -> None:
    frame = _market_frame()
    vectors = [
        compute_low_attention_v2(frame, code, AS_OF, config=_default_config())
        for code in (_code(0), _code(1), _code(2), _code(3))
    ]
    report = low_attention_sample_report(vectors, config=_default_config())
    assert set(report["class"]) == {
        SAMPLE_CLASS_A,
        SAMPLE_CLASS_B,
        SAMPLE_CLASS_C,
        SAMPLE_CLASS_NA,
    }
    row_a = report.loc[report["class"].eq(SAMPLE_CLASS_A)].iloc[0]
    assert bool(row_a["liquidity_eligible"]) is True
    assert row_a["cross_section_turnover_percentile"] <= 0.30
    row_b = report.loc[report["class"].eq(SAMPLE_CLASS_B)].iloc[0]
    assert bool(row_b["liquidity_eligible"]) is False
    markdown = low_attention_sample_report_markdown(report)
    assert "## Buckets" in markdown
    assert "not investment advice" in markdown.lower() or "not trading" in markdown.lower()
    assert "buy" not in markdown.lower()


def test_shuffled_and_duplicated_market_rows_have_deterministic_output() -> None:
    frame = _market_frame(n_symbols=4)
    duplicate = frame.loc[
        frame["ts_code"].eq(_code(0)) & frame["trade_date"].eq(AS_OF)
    ]
    with_duplicate = pd.concat([frame, duplicate], ignore_index=True)
    config = _default_config(
        self_window=SelfWindowConfig(window=20, min_valid=2, min_listing_days=0),
        abnormal_volume=AbnormalVolumeConfig(
            baseline_window=10, min_observations=2, max_abnormal_cap=10.0, max_staleness_days=10
        ),
    )
    original = compute_low_attention_v2(with_duplicate, _code(0), AS_OF, config=config)
    shuffled = compute_low_attention_v2(
        with_duplicate.sample(frac=1.0, random_state=29).reset_index(drop=True),
        _code(0),
        AS_OF,
        config=config,
    )
    assert original.as_dict() == shuffled.as_dict()


def test_future_published_market_row_cannot_define_effective_session() -> None:
    rows = []
    for code, multiplier in ((_code(0), 1.0), (_code(1), 2.0)):
        for trade_date in ("20250626", "20250627"):
            rows.append((code, trade_date, 100.0 * multiplier, 1.0 * multiplier, 1000.0))
        rows.append((code, "20250630", 100.0 * multiplier, 1.0 * multiplier, 1000.0))
    frame = pd.DataFrame(
        rows,
        columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"],
    )
    frame["actual_available_date"] = [
        "20250626",
        "20250627",
        "20250702",
        "20250626",
        "20250627",
        "20250702",
    ]
    config = _default_config(
        self_window=SelfWindowConfig(window=10, min_valid=2, min_listing_days=0),
        abnormal_volume=AbnormalVolumeConfig(
            baseline_window=10, min_observations=2, max_abnormal_cap=10.0, max_staleness_days=10
        ),
    )
    vector = compute_low_attention_v2(frame, _code(0), "20250630", config=config)
    assert vector.values["session_status"] == "traded"
    assert vector.evidence["session_status"].metadata["effective_session"] == "20250627"
    assert vector.evidence["cross_section_turnover_percentile"].metadata[
        "population_session"
    ] == "20250627"


def test_stale_observation_cannot_produce_self_attention_evidence() -> None:
    frame = _market_frame()
    code = _code(5)
    rows = frame.loc[frame["ts_code"].eq(code)].sort_values("trade_date").iloc[:-60]
    frame = pd.concat([frame.loc[~frame["ts_code"].eq(code)], rows], ignore_index=True)
    vector = compute_low_attention_v2(frame, code, AS_OF, config=_default_config())
    assert vector.values["session_status"] == "stale"
    for name in (
        "self_turnover_percentile",
        "self_amount_percentile",
        "self_volume_percentile",
        "abnormal_volume",
        "attention_baseline_change",
        "attention_surge",
        "low_attention_v2_score",
    ):
        assert vector.values[name] is None
        assert vector.evidence[name].metadata["warnings"] in {
            "stale_data",
            "insufficient_attention_evidence",
        }


def test_missing_daily_basic_is_unknown_not_a_low_attention_signal() -> None:
    daily = _market_frame(n_symbols=3)[["ts_code", "trade_date", "vol"]]
    config = _default_config(
        self_window=SelfWindowConfig(window=20, min_valid=2, min_listing_days=0),
        abnormal_volume=AbnormalVolumeConfig(
            baseline_window=10, min_observations=2, max_abnormal_cap=10.0, max_staleness_days=10
        ),
    )
    vector = compute_low_attention_v2(daily, _code(0), AS_OF, config=config)
    assert vector.values["session_status"] == "traded"
    assert vector.values["self_volume_percentile"] is not None
    assert vector.values["self_turnover_percentile"] is None
    assert vector.values["cross_section_volume_percentile"] is not None
    assert vector.values["cross_section_turnover_percentile"] is None
    assert classify_low_attention_case(vector, config=config)[0] == SAMPLE_CLASS_C


def test_volume_surge_is_higher_attention_not_low_attention() -> None:
    dates = pd.date_range("20250620", periods=5, freq="B")
    rows: list[tuple[str, str, float, float, float]] = []
    for code, volume, turnover, amount in (
        (_code(0), [100.0, 100.0, 100.0, 100.0, 1000.0], 1.0, 1000.0),
        (_code(1), [200.0] * 5, 2.0, 2000.0),
    ):
        rows.extend(
            (code, ts.strftime("%Y%m%d"), volume[index], turnover, amount)
            for index, ts in enumerate(dates)
        )
    frame = pd.DataFrame(
        rows, columns=["ts_code", "trade_date", "vol", "turnover_rate", "amount"]
    )
    config = _default_config(
        self_window=SelfWindowConfig(window=10, min_valid=2, min_listing_days=0),
        abnormal_volume=AbnormalVolumeConfig(
            baseline_window=4, min_observations=2, max_abnormal_cap=10.0, max_staleness_days=10
        ),
        low_attention_cross_percentile=0.5,
    )
    vector = compute_low_attention_v2(frame, _code(0), dates[-1].strftime("%Y%m%d"), config=config)
    assert vector.values["abnormal_volume"] == pytest.approx(10.0)
    assert vector.values["attention_surge"] is True
    assert classify_low_attention_case(vector, config=config) == (
        SAMPLE_CLASS_NA,
        ("higher_attention_observed",),
    )


def test_v2_metadata_uses_explicit_evidence_vocabulary() -> None:
    vector = compute_low_attention_v2(_market_frame(), _code(0), AS_OF, config=_default_config())
    required = {
        "name",
        "value",
        "status",
        "reason",
        "raw_value",
        "observation_date",
        "reference_type",
        "reference_window",
        "reference_population",
        "population_count",
        "history_count",
        "source_dataset",
        "source_fields",
        "as_of_date",
        "attention_contract_version",
    }
    for name in (
        "self_turnover_percentile",
        "cross_section_turnover_percentile",
        "abnormal_volume",
        "attention_baseline_change",
    ):
        metadata = vector.evidence[name].metadata
        assert required <= metadata.keys()
        assert metadata["name"] == name
        assert metadata["attention_contract_version"] == SEMANTIC
        assert metadata["value"] == vector.values[name]
    assert vector.metadata["low_attention_v2"]["attention_contract_version"] == SEMANTIC
    score = score_feature_vector(vector)
    assert score.input_metadata["attention_contract_version"] == SEMANTIC
    assert score.input_metadata["production_score_uses_low_attention_v2"] is False


def test_replay_and_candidate_report_carry_low_attention_contract_metadata() -> None:
    dates = pd.date_range("20250620", periods=3, freq="B")
    code = _code(0)
    daily = pd.DataFrame(
        {
            "ts_code": [code] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "close": [10.0, 10.1, 10.2],
            "vol": [100.0, 110.0, 120.0],
        }
    )
    daily_basic = pd.DataFrame(
        {
            "ts_code": [code] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "turnover_rate": [1.0, 1.1, 1.2],
            "amount": [1000.0, 1100.0, 1200.0],
        }
    )
    frames = {
        "stock_basic": pd.DataFrame(
            {
                "ts_code": [code],
                "name": ["Synthetic"],
                "list_date": ["20100101"],
                "list_status": ["L"],
            }
        ),
        "daily": daily,
        "daily_basic": daily_basic,
        "income": pd.DataFrame(),
        "balancesheet": pd.DataFrame(),
        "cashflow": pd.DataFrame(),
        "fina_indicator": pd.DataFrame(),
    }
    result = run_replay_frames(
        frames,
        as_of_date=dates[-1].strftime("%Y%m%d"),
        config=ReplayConfig(
            top_n=1,
            universe=UniverseConfig(min_financial_periods=0),
        ),
    )
    assert result.metadata()["attention_contract_version"] == SEMANTIC
    assert result.metadata()["attention_v2_research_only"] is True
    assert result.metadata()["trend_contract_version"] == "turnaround-trend-v2"
    # The candidate is retained in the full diagnostic output but is no
    # longer admitted to formal Top-N without the required evidence gate.
    assert result.ranked.empty
    assert result.full_ranked.iloc[0]["attention_contract_version"] == SEMANTIC
    assert result.full_ranked.iloc[0]["trend_contract_version"] == "turnaround-trend-v2"
    vector = result.vectors[0]
    assert vector.values["abnormal_volume"] is not None  # v1 field is preserved
    assert "low_attention_v2_abnormal_volume" in vector.values
    report = candidate_report(result, code)
    assert report["report_metadata"]["attention_contract_version"] == SEMANTIC
    assert report["score_input_metadata"]["attention_contract_version"] == SEMANTIC
    assert report["score"]["trend_contract_version"] == "turnaround-trend-v2"
    assert report["trend_contract_version"] == "turnaround-trend-v2"
    assert report["attention_v2_evidence"]
