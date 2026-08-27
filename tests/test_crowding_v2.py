"""Benchmark-relative expectation/crowding v2 tests (issue #30).

The old ``recent_excess_return`` was the stock's own 20-session close-to-close
return.  These tests prove the corrected contract:

- ``recent_excess_return`` is stock return minus benchmark return over the
  exact same trading-session window;
- trading-session semantics (weekend/holiday/suspension/alignment) hold;
- a missing/stale/misaligned benchmark makes features unknown with a reason
  and never falls back to stock-only returns;
- future market rows, future availability dates, and future disclosures cannot
  change an earlier as-of result;
- fundamental and crowding signals are structurally independent outputs;
- identical inputs give identical outputs.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround.features import CrowdingConfig
from ashare_turnaround.features.fundamental import compute_fundamental_features
from ashare_turnaround.features.market import compute_crowding_features
from ashare_turnaround.features.quality import compute_quality_features
from ashare_turnaround.features.trend import compute_trend_features
from ashare_turnaround.scanner.score import score_feature_vector

STOCK = "600000.SH"
BENCH = "000300.SH"
AS_OF = "20251231"

DATES = list(pd.bdate_range("2024-09-02", "2025-12-31"))
assert len(DATES) >= 300, len(DATES)


def fmt(dates: list) -> list[str]:
    return [value.strftime("%Y%m%d") for value in dates]


def make_market(
    dates,
    closes,
    *,
    bench_close=100.0,
    vol=None,
    turnover=None,
    pe=None,
    bench_dates=None,
    bench_closes=None,
    avail_dates=None,
) -> pd.DataFrame:
    """Build a merged daily/daily_basic-style frame for stock and benchmark."""
    rows = []
    stock_dates = list(dates)
    for index, (day, close) in enumerate(zip(stock_dates, closes)):
        row = {
            "ts_code": STOCK,
            "trade_date": day.strftime("%Y%m%d"),
            "close": close,
        }
        if vol is not None:
            row["vol"] = vol[index] if isinstance(vol, (list, tuple)) else vol
        if turnover is not None:
            row["turnover_rate"] = (
                turnover[index] if isinstance(turnover, (list, tuple)) else turnover
            )
        if pe is not None:
            row["pe_ttm"] = pe[index] if isinstance(pe, (list, tuple)) else pe
        if avail_dates is not None:
            row["actual_available_date"] = avail_dates[index]
        rows.append(row)
    bench_dates_used = bench_dates if bench_dates is not None else stock_dates
    if bench_closes is not None:
        bench_values = bench_closes
    elif bench_close is None:
        bench_values = []
    else:
        bench_values = [bench_close] * len(bench_dates_used)
    for day, close in zip(bench_dates_used, bench_values):
        rows.append(
            {
                "ts_code": BENCH,
                "trade_date": day.strftime("%Y%m%d"),
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def flat_closes(level: float, dates: list = DATES) -> list[float]:
    return [level] * len(dates)


def ramp_tail(level: float, ramp_sessions: int, final: float, dates: list = DATES) -> list[float]:
    closes = [level] * (len(dates) - ramp_sessions)
    for step in range(1, ramp_sessions + 1):
        closes.append(level + (final - level) * step / ramp_sessions)
    return closes


def calendar_frame(dates: list, as_of: str | None = None) -> pd.DataFrame:
    """trade_cal frame: open on every session date, closed on weekends/beyond."""
    rows = []
    open_dates = {pd.Timestamp(value) for value in dates}
    start = min(open_dates) - pd.Timedelta(days=2)
    end = max(open_dates) + pd.Timedelta(days=2)
    for day in pd.date_range(start, end, freq="D"):
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": day.strftime("%Y%m%d"),
                "is_open": 1 if day in open_dates else 0,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Benchmark: excess return contract
# ---------------------------------------------------------------------------


def test_excess_return_is_stock_minus_benchmark() -> None:
    closes = ramp_tail(10.0, 20, 11.0)  # +10% over the last 20 sessions
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_return_20d"] == pytest.approx(0.10)
    assert vector.values["benchmark_return_20d"] == pytest.approx(0.0)
    assert vector.values["recent_excess_return"] == pytest.approx(0.10)
    evidence = vector.evidence["recent_excess_return"]
    assert evidence.status == "known"
    assert evidence.components["stock_return"] == pytest.approx(0.10)
    assert evidence.components["benchmark_return"] == pytest.approx(0.0)
    assert evidence.components["excess_return"] == pytest.approx(0.10)
    assert "R_bench" in (evidence.formula or "")
    assert evidence.config["benchmark_id"] == BENCH
    assert evidence.semantic_version == "crowding-v2"
    assert evidence.config["version"] == "benchmark-v1"


def test_absolute_stock_return_is_not_excess_return() -> None:
    # benchmark falls 10% while the stock rises 10%: excess is 20%, stock return 10%
    closes = ramp_tail(10.0, 20, 11.0)
    bench = ramp_tail(100.0, 20, 90.0)
    market = make_market(DATES, closes, bench_closes=bench, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_return_20d"] == pytest.approx(0.10)
    assert vector.values["benchmark_return_20d"] == pytest.approx(-0.10)
    assert vector.values["recent_excess_return"] == pytest.approx(0.20)
    assert vector.values["recent_excess_return"] != vector.values["recent_return_20d"]


def test_20d_and_60d_boundaries_use_trading_sessions_not_calendar_days() -> None:
    # Remove one trading session (holiday) inside the window: session counting
    # must still pick the L-th prior open session, not as_of - L calendar days.
    dates = list(pd.bdate_range("2025-01-02", periods=120))
    holiday = dates[108]  # between t-20 and t so calendar-day counting differs
    open_dates = [value for value in dates if value != holiday]
    closes = flat_closes(10.0, open_dates)
    # mark the 20th and 60th prior open sessions
    closes[-21] = 9.0
    closes[-61] = 8.0
    market = make_market(open_dates, closes, bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(
        market, STOCK, open_dates[-1].strftime("%Y%m%d"), calendar_frame=calendar_frame(open_dates)
    )

    expected_start_20 = open_dates[-21]
    expected_start_60 = open_dates[-61]
    assert vector.evidence["recent_excess_return"].components["window_start_session"] == (
        expected_start_20.strftime("%Y%m%d")
    )
    assert vector.values["recent_excess_return"] == pytest.approx(10.0 / 9.0 - 1.0)
    assert vector.evidence["excess_return_60d"].components["window_start_session"] == (
        expected_start_60.strftime("%Y%m%d")
    )
    assert vector.values["excess_return_60d"] == pytest.approx(10.0 / 8.0 - 1.0)
    # a naive as_of - 20 calendar-days window would land on a different session
    calendar_day_start = dates[-21]
    assert calendar_day_start != expected_start_20


def test_suspension_at_anchor_is_unknown() -> None:
    dates = list(DATES)
    closes = flat_closes(10.0)
    market = make_market(
        dates[:-3], closes[:-3], bench_close=100.0, bench_dates=dates, vol=1000.0, turnover=1.0
    )
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].status == "unknown"
    assert vector.evidence["recent_excess_return"].reason == "stock_no_quote_at_anchor_session"
    assert vector.values["expectation_score"] is None


def test_suspension_at_window_start_is_unknown() -> None:
    closes = flat_closes(10.0)
    # drop the stock quote exactly at t-20 (session offset 20 from the end)
    stock_dates = list(DATES[: -21]) + list(DATES[-20:])
    stock_closes = closes[:-21] + closes[-20:]
    market = make_market(
        stock_dates,
        stock_closes,
        bench_close=100.0,
        bench_dates=list(DATES),
        vol=1000.0,
        turnover=1.0,
    )
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.evidence["recent_excess_return"].status == "unknown"
    assert vector.evidence["recent_excess_return"].reason == "stock_missing_at_window_start"


def test_mid_window_suspension_uses_endpoints() -> None:
    # A gap inside the window is documented as endpoint-safe: both endpoints
    # exist, so the 20D return remains the endpoint ratio.
    dates = list(DATES)
    closes = flat_closes(10.0)
    gap = dates[-15:-12]
    stock_dates = [value for value in dates if value not in gap]
    stock_closes = [closes[dates.index(value)] for value in stock_dates]
    market = make_market(
        stock_dates, stock_closes, bench_close=100.0, bench_dates=dates, vol=1000.0, turnover=1.0
    )
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_excess_return"] == pytest.approx(0.0)
    assert vector.values["momentum_60d"] == pytest.approx(0.0)


def test_missing_benchmark_is_unknown_and_never_falls_back() -> None:
    market = make_market(DATES, flat_closes(10.0), bench_close=None, bench_dates=[])
    vector = compute_crowding_features(market, STOCK, AS_OF)

    # fail-closed: without a benchmark there is no session axis and every
    # crowding-v2 feature is unknown; the stock-only return is NOT published
    # under the excess name and no feature quietly degenerates to it
    assert vector.values["recent_return_20d"] is None
    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].reason == "benchmark_unavailable"
    assert vector.values["momentum_60d"] is None
    assert vector.values["expectation_score"] is None
    assert vector.values["crowding_penalty"] is None
    assert "already_repriced_or_crowded" not in vector.risk_flags


def test_stale_benchmark_is_unknown() -> None:
    dates = list(DATES)
    market = make_market(
        dates,
        flat_closes(10.0),
        bench_close=100.0,
        bench_dates=dates[:-10],
        vol=1000.0,
        turnover=1.0,
    )
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].reason == "benchmark_stale_at_as_of"


def test_missing_benchmark_anchor_session_with_calendar_is_unknown() -> None:
    dates = list(DATES)
    market = make_market(
        dates,
        flat_closes(10.0),
        bench_close=100.0,
        bench_dates=dates[:-1],
        vol=1000.0,
        turnover=1.0,
    )
    vector = compute_crowding_features(
        market, STOCK, AS_OF, calendar_frame=calendar_frame(dates)
    )

    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].reason in {
        "benchmark_stale_at_as_of",
        "benchmark_missing_at_anchor_session",
    }


def test_holiday_as_of_anchors_to_previous_open_session() -> None:
    dates = list(pd.bdate_range("2025-01-02", periods=60))
    as_of = dates[-1] + pd.Timedelta(days=1)  # a Saturday
    market = make_market(dates, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(
        market, STOCK, as_of.strftime("%Y%m%d"), calendar_frame=calendar_frame(dates)
    )

    assert vector.values["recent_excess_return"] == pytest.approx(0.0)
    assert vector.evidence["recent_excess_return"].components["anchor_session"] == (
        dates[-1].strftime("%Y%m%d")
    )


def test_calendar_stale_is_unknown() -> None:
    dates = list(DATES)
    stale_calendar = calendar_frame(dates[: -30])
    market = make_market(dates, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF, calendar_frame=stale_calendar)

    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].reason == "calendar_stale"


def test_insufficient_history_is_unknown() -> None:
    short = DATES[-15:]
    market = make_market(short, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, short[-1].strftime("%Y%m%d"))

    assert vector.values["recent_excess_return"] is None
    assert vector.evidence["recent_excess_return"].reason == "insufficient_benchmark_history"
    assert vector.evidence["excess_return_60d"].reason == "insufficient_benchmark_history"


# ---------------------------------------------------------------------------
# Crowding: repricing, 52W high, volume/turnover, outliers
# ---------------------------------------------------------------------------


def test_recent_price_doubling_saturates_repricing_and_flags() -> None:
    closes = [5.0] * (len(DATES) - 21) + [5.0 + 5.0 * step / 20 for step in range(0, 21)]
    vol = [1000.0] * (len(DATES) - 1) + [3000.0]
    market = make_market(DATES, closes, bench_close=100.0, vol=vol, turnover=1.0, pe=10.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_excess_return"] == pytest.approx(1.0)
    assert vector.values["repricing_20d"] == pytest.approx(1.0)
    assert vector.values["crowding_penalty"] == pytest.approx(80.0)
    assert "already_repriced_or_crowded" in vector.risk_flags
    assert vector.values["expectation_score"] == pytest.approx(20.0)


def test_normal_repricing_scales_with_threshold() -> None:
    closes = ramp_tail(10.0, 20, 10.3)  # +3% excess vs flat benchmark
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_excess_return"] == pytest.approx(0.03)
    assert vector.values["repricing_20d"] == pytest.approx(0.03 / 0.15)


def test_52w_high_proximity_and_distance() -> None:
    # Stock at its 52-week high
    closes = [8.0] * (len(DATES) - 21) + ramp_tail(8.0, 20, 10.0)[-21:]
    at_high = compute_crowding_features(
        make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0), STOCK, AS_OF
    )
    assert at_high.values["distance_52w_high"] == pytest.approx(0.0)
    assert at_high.values["high_proximity"] == pytest.approx(1.0)

    # Stock 20% below its 52-week high (high reached earlier, then pulled back)
    high_series = [10.0] * (len(DATES) - 100) + [12.0] * 80 + [10.0] * 20
    vector = compute_crowding_features(
        make_market(DATES, high_series, bench_close=100.0, vol=1000.0, turnover=1.0), STOCK, AS_OF
    )
    assert vector.values["distance_52w_high"] == pytest.approx(1.0 - 10.0 / 12.0)
    assert vector.values["high_proximity"] == pytest.approx(10.0 / 12.0)


def test_52w_high_evidence_records_window_details() -> None:
    closes = ramp_tail(10.0, 20, 11.0)
    vector = compute_crowding_features(
        make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0), STOCK, AS_OF
    )
    evidence = vector.evidence["distance_52w_high"]
    assert evidence.components["current_price"] == pytest.approx(11.0)
    assert evidence.components["high"] == pytest.approx(11.0)
    assert evidence.components["distance"] == pytest.approx(0.0)
    assert evidence.components["observation_count"] == 252
    assert vector.values["high_52w"] == pytest.approx(11.0)
    assert vector.values["current_price"] == pytest.approx(11.0)
    assert vector.values["high_52w_obs_count"] == 252
    assert vector.values["high_52w_window_end"] == AS_OF
    assert vector.values["high_52w_window_start"] < AS_OF


def test_insufficient_52w_history_is_unknown() -> None:
    short = DATES[-100:]
    market = make_market(short, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, short[-1].strftime("%Y%m%d"))

    assert vector.evidence["distance_52w_high"].status == "unknown"
    assert vector.evidence["distance_52w_high"].reason == "insufficient_52w_history"
    assert vector.evidence["high_proximity"].status == "unknown"
    # 20D/60D remain computable with 100 sessions
    assert vector.values["recent_excess_return"] is not None
    assert vector.values["crowding_penalty"] is not None


def test_abnormal_volume_penalty() -> None:
    vol = [1000.0] * (len(DATES) - 1) + [3000.0]
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=vol, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["volume_spike"] == pytest.approx(3.0)
    assert vector.values["volume_spike_penalty"] == pytest.approx(1.0)


def test_abnormal_turnover_penalty() -> None:
    turnover = [1.0] * (len(DATES) - 1) + [4.0]
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=turnover)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["turnover_spike"] == pytest.approx(4.0)
    assert vector.values["turnover_spike_penalty"] == pytest.approx(1.0)


def test_extreme_outlier_saturates_penalty() -> None:
    closes = [1.0] * (len(DATES) - 21) + [1.0 + 9.0 * step / 20 for step in range(0, 21)]
    vol = [1000.0] * (len(DATES) - 1) + [10000.0]
    turnover = [1.0] * (len(DATES) - 1) + [5.0]
    market = make_market(DATES, closes, bench_close=100.0, vol=vol, turnover=turnover)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["crowding_penalty"] == pytest.approx(100.0)
    assert vector.values["expectation_score"] == pytest.approx(0.0)
    assert "already_repriced_or_crowded" in vector.risk_flags


def test_single_session_limit_move_is_repricing() -> None:
    closes = [10.0] * (len(DATES) - 1) + [11.0]  # one +10% limit-up session at the anchor
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["recent_return_20d"] == pytest.approx(0.10)
    assert vector.values["recent_excess_return"] == pytest.approx(0.10)
    assert vector.values["repricing_20d"] == pytest.approx(0.10 / 0.15)
    assert vector.values["repricing_60d"] == pytest.approx(0.10 / 0.30)


def test_ordinary_low_attention_security_has_no_crowding_flag() -> None:
    # steady, quiet stock: repricing and activity components are zero
    closes = flat_closes(10.0)
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0, pe=10.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["repricing_20d"] == pytest.approx(0.0)
    assert vector.values["repricing_60d"] == pytest.approx(0.0)
    assert vector.values["volume_spike_penalty"] == pytest.approx(0.0)
    assert vector.values["turnover_spike_penalty"] == pytest.approx(0.0)
    assert "already_repriced_or_crowded" not in vector.risk_flags
    assert vector.values["crowding_penalty"] is not None


def test_missing_valuation_is_unknown_and_excluded_from_penalty() -> None:
    # no pe columns at all
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(market, STOCK, AS_OF)

    assert vector.values["valuation_percentile"] is None
    assert vector.evidence["valuation_percentile"].reason == "valuation_unavailable"
    assert vector.values["valuation_penalty"] is None

    # valuation percentile works when pe_ttm exists but stays out of the penalty
    pe = [10.0] * (len(DATES) - 1) + [30.0]
    with_pe = make_market(
        DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0, pe=pe
    )
    vector2 = compute_crowding_features(with_pe, STOCK, AS_OF)
    assert vector2.values["valuation_percentile"] == pytest.approx(1.0)
    assert (
        vector2.evidence["valuation_penalty"].reason
        == "valuation_excluded_from_penalty_by_config"
    )

    # enabling valuation in the penalty changes the composition
    enabled = CrowdingConfig(include_valuation_in_penalty=True)
    vector3 = compute_crowding_features(with_pe, STOCK, AS_OF, config=enabled)
    assert vector3.values["valuation_penalty"] == pytest.approx(0.0)


def test_missing_evidence_is_fully_unknown() -> None:
    vector = compute_crowding_features(pd.DataFrame(), STOCK, AS_OF)

    assert vector.values["recent_excess_return"] is None
    assert vector.values["expectation_score"] is None
    assert vector.values["crowding_penalty"] is None
    assert vector.evidence["recent_excess_return"].reason == "no_market_history"
    assert "already_repriced_or_crowded" not in vector.risk_flags
    assert set(vector.unknown_features) == {
        "recent_return_20d",
        "benchmark_return_20d",
        "recent_excess_return",
        "momentum_60d",
        "benchmark_return_60d",
        "excess_return_60d",
        "distance_52w_high",
        "high_52w",
        "current_price",
        "high_52w_window_start",
        "high_52w_window_end",
        "high_52w_obs_count",
        "volume_spike",
        "turnover_spike",
        "valuation_percentile",
        "repricing_20d",
        "repricing_60d",
        "high_proximity",
        "volume_spike_penalty",
        "turnover_spike_penalty",
        "valuation_penalty",
        "disclosure_reaction_excess",
        "disclosure_availability_date",
        "disclosure_event_date",
        "disclosure_reaction_window_start",
        "disclosure_reaction_window_end",
        "disclosure_reaction_penalty",
        "crowding_penalty",
        "expectation_score",
    }


# ---------------------------------------------------------------------------
# PIT: no leakage from future market rows, availability dates, or disclosures
# ---------------------------------------------------------------------------


def base_vector():
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    return compute_crowding_features(market, STOCK, AS_OF)


def test_future_market_rows_do_not_leak() -> None:
    before = base_vector()
    future = pd.date_range("2026-01-05", periods=10)
    rows = []
    for day in future:
        rows.append({"ts_code": STOCK, "trade_date": day.strftime("%Y%m%d"), "close": 500.0})
        rows.append({"ts_code": BENCH, "trade_date": day.strftime("%Y%m%d"), "close": 0.1})
    market = pd.concat(
        [
            make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0),
            pd.DataFrame(rows),
        ],
        ignore_index=True,
    )
    after = compute_crowding_features(market, STOCK, AS_OF)
    assert after.as_dict() == before.as_dict()


def test_future_actual_available_date_rows_do_not_leak() -> None:
    before = base_vector()
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    # a retroactively loaded row visible only after as-of must stay invisible
    rows = [{"ts_code": STOCK, "trade_date": AS_OF, "close": 999.0,
             "actual_available_date": "20260105"}]
    market = pd.concat([market, pd.DataFrame(rows)], ignore_index=True)
    after = compute_crowding_features(market, STOCK, AS_OF)
    assert after.as_dict() == before.as_dict()


def disclosure_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_future_disclosure_does_not_leak() -> None:
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    before = compute_crowding_features(
        market, STOCK, AS_OF, disclosure_frame=disclosure_frame([])
    )
    later = disclosure_frame(
        [
            {
                "ts_code": STOCK,
                "ann_date": "20260115",
                "actual_date": "20260115",
                "end_date": "20251231",
            }
        ]
    )
    after = compute_crowding_features(market, STOCK, AS_OF, disclosure_frame=later)
    assert after.as_dict() == before.as_dict()


def test_later_disclosure_never_changes_an_earlier_as_of() -> None:
    event_day = DATES[-40]
    reaction_start = DATES[-39]  # first session strictly after the event
    reaction_end = DATES[-35]  # fifth session after the event
    later_event = DATES[-25]  # disclosed between as_of_1 and as_of_2
    closes = flat_closes(10.0)
    # stock dips in the first reaction window, benchmark flat
    closes[DATES.index(reaction_start)] = 10.0
    closes[DATES.index(reaction_end)] = 9.0
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0)

    as_of_1 = DATES[-30].strftime("%Y%m%d")
    first_disclosure = disclosure_frame(
        [
            {
                "ts_code": STOCK,
                "ann_date": event_day.strftime("%Y%m%d"),
                "actual_date": event_day.strftime("%Y%m%d"),
                "end_date": "20250930",
            }
        ]
    )
    # as-of before the event: nothing is visible
    before_event = compute_crowding_features(
        market, STOCK, DATES[-45].strftime("%Y%m%d"), disclosure_frame=first_disclosure
    )
    assert before_event.evidence["disclosure_reaction_excess"].status == "unknown"
    assert (
        before_event.evidence["disclosure_reaction_excess"].reason
        == "no_disclosure_before_as_of"
    )

    # as_of_1 sees the reaction to the first event
    at_event = compute_crowding_features(market, STOCK, as_of_1, disclosure_frame=first_disclosure)
    assert at_event.values["disclosure_reaction_excess"] == pytest.approx(-0.10)
    assert at_event.values["disclosure_availability_date"] == event_day.strftime("%Y%m%d")
    assert at_event.values["disclosure_reaction_window_start"] == reaction_start.strftime("%Y%m%d")
    assert at_event.values["disclosure_reaction_window_end"] == reaction_end.strftime("%Y%m%d")

    # adding a later disclosure (still after as_of_1) must not change as_of_1
    extended = disclosure_frame(
        [
            {
                "ts_code": STOCK,
                "ann_date": event_day.strftime("%Y%m%d"),
                "actual_date": event_day.strftime("%Y%m%d"),
                "end_date": "20250930",
            },
            {
                "ts_code": STOCK,
                "ann_date": later_event.strftime("%Y%m%d"),
                "actual_date": later_event.strftime("%Y%m%d"),
                "end_date": "20251231",
            },
        ]
    )
    replayed_at_event = compute_crowding_features(market, STOCK, as_of_1, disclosure_frame=extended)
    assert replayed_at_event.as_dict() == at_event.as_dict()

    # an as-of after the later disclosure resolves the NEWER event instead
    as_of_2 = DATES[-20]
    after_later = compute_crowding_features(
        market, STOCK, as_of_2.strftime("%Y%m%d"), disclosure_frame=extended
    )
    assert after_later.values["disclosure_availability_date"] == later_event.strftime("%Y%m%d")
    assert after_later.values["disclosure_reaction_window_start"] == DATES[-24].strftime("%Y%m%d")


def test_disclosure_reaction_uses_only_post_event_sessions() -> None:
    event_day = DATES[-40]
    closes = flat_closes(10.0)
    closes[DATES.index(DATES[-39])] = 10.0
    closes[DATES.index(DATES[-35])] = 9.5
    market = make_market(DATES, closes, bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(
        market,
        STOCK,
        AS_OF,
        disclosure_frame=disclosure_frame(
            [
                {
                    "ts_code": STOCK,
                    "ann_date": event_day.strftime("%Y%m%d"),
                    "actual_date": event_day.strftime("%Y%m%d"),
                    "end_date": "20250930",
                }
            ]
        ),
    )
    evidence = vector.evidence["disclosure_reaction_excess"]
    assert evidence.status == "known"
    assert vector.values["disclosure_reaction_excess"] == pytest.approx(9.5 / 10.0 - 1.0)
    assert evidence.components["window_start"] == DATES[-39].strftime("%Y%m%d")
    assert evidence.components["window_end"] == DATES[-35].strftime("%Y%m%d")
    # the event session itself is not part of the reaction window
    assert evidence.components["window_start"] > event_day.strftime("%Y%m%d")


def test_disclosure_timing_unprovable_is_unknown() -> None:
    market = make_market(DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0)
    vector = compute_crowding_features(
        market,
        STOCK,
        AS_OF,
        disclosure_frame=disclosure_frame(
            [{"ts_code": STOCK, "ann_date": None, "actual_date": None, "end_date": "20250930"}]
        ),
    )
    assert vector.evidence["disclosure_reaction_excess"].status == "unknown"
    assert vector.evidence["disclosure_reaction_excess"].reason == "disclosure_timing_unprovable"


# ---------------------------------------------------------------------------
# Structural separation between fundamental and crowding signals
# ---------------------------------------------------------------------------


def financial_frames() -> dict[str, pd.DataFrame]:
    periods = ["20240331", "20240630", "20240930", "20241231"]
    available = ["20240430", "20240830", "20241030", "20250330"]
    common = {
        "ts_code": [STOCK] * 4,
        "end_date": periods,
        "ann_date": available,
        "f_ann_date": available,
        "report_type": ["1"] * 4,
        "update_flag": ["0"] * 4,
    }
    return {
        "income": pd.DataFrame(
            {
                **common,
                "revenue": [100.0, 115.0, 135.0, 160.0],
                "n_income_attr_p": [4.0, 7.0, 11.0, 17.0],
                "operate_profit": [8.0, 11.0, 16.0, 23.0],
                "gross_profit": [25.0, 30.0, 38.0, 48.0],
                "total_profit": [5.0, 8.0, 12.0, 18.0],
                "non_oper_income": [0.2, 0.2, 0.3, 0.3],
                "assets_impair_loss": [0.1, 0.1, 0.1, 0.1],
            }
        ),
        "balancesheet": pd.DataFrame(
            {
                **common,
                "total_assets": [200.0, 205.0, 210.0, 220.0],
                "total_hldr_eqy_inc_min_int": [100.0, 102.0, 105.0, 110.0],
                "total_liab": [100.0, 103.0, 105.0, 110.0],
                "inventories": [20.0, 21.0, 22.0, 23.0],
                "accounts_receiv": [18.0, 19.0, 20.0, 21.0],
            }
        ),
        "cashflow": pd.DataFrame(
            {
                **common,
                "n_cashflow_act": [6.0, 8.0, 12.0, 18.0],
            }
        ),
    }


def test_fundamental_and_crowding_outputs_are_independent() -> None:
    frames = financial_frames()
    fundamental = compute_fundamental_features(frames, STOCK, AS_OF)
    trend = compute_trend_features(frames, STOCK, AS_OF)
    compute_quality_features(frames, STOCK, AS_OF)  # quality stays independent too
    assert fundamental.values["revenue_yoy"] is not None
    assert trend.values["consecutive_improvement"] == 3

    crowded_market = make_market(
        DATES, ramp_tail(10.0, 20, 15.0), bench_close=100.0, vol=1000.0, turnover=1.0
    )
    quiet_market = make_market(
        DATES, flat_closes(10.0), bench_close=100.0, vol=1000.0, turnover=1.0
    )
    crowded_crowding = compute_crowding_features(crowded_market, STOCK, AS_OF)
    quiet_crowding = compute_crowding_features(quiet_market, STOCK, AS_OF)

    # crowding outputs differ
    assert crowded_crowding.values["recent_excess_return"] == pytest.approx(0.5)
    assert quiet_crowding.values["recent_excess_return"] == pytest.approx(0.0)

    # merged scoring keeps the fundamental component identical
    def merged_with(crowding):
        return (
            compute_fundamental_features(frames, STOCK, AS_OF)
            .merge(compute_trend_features(frames, STOCK, AS_OF))
            .merge(compute_quality_features(frames, STOCK, AS_OF))
            .merge(crowding)
        )

    score_crowded = score_feature_vector(merged_with(crowded_crowding))
    score_quiet = score_feature_vector(merged_with(quiet_crowding))
    assert (
        score_crowded.components["fundamental_score"]
        == score_quiet.components["fundamental_score"]
    )
    assert (
        score_crowded.components["expectation_score"]
        < score_quiet.components["expectation_score"]
    )
    # fundamental/trend/quality component values are untouched by the penalty
    for name in ("fundamental_score", "trend_score", "quality_score"):
        assert score_crowded.components[name] == score_quiet.components[name]


def test_penalty_flag_belongs_to_expectation_group_not_fundamental() -> None:
    frames = financial_frames()
    fundamental = compute_fundamental_features(frames, STOCK, AS_OF)
    closes = [5.0] * (len(DATES) - 21) + [5.0 + 5.0 * step / 20 for step in range(0, 21)]
    vol = [1000.0] * (len(DATES) - 1) + [3000.0]
    turnover = [1.0] * (len(DATES) - 1) + [3.0]
    crowded_market = make_market(DATES, closes, bench_close=100.0, vol=vol, turnover=turnover)
    crowded = fundamental.merge(compute_crowding_features(crowded_market, STOCK, AS_OF))
    scored = score_feature_vector(crowded)

    assert "already_repriced_or_crowded" in scored.penalties
    assert scored.penalties["already_repriced_or_crowded"] == 15.0
    # the flag is attributed to the expectation group; the fundamental
    # component remains purely fundamental (no repricing inside it)
    assert fundamental.values["revenue_yoy"] is not None
    assert scored.components["fundamental_score"] == score_feature_vector(
        compute_fundamental_features(frames, STOCK, AS_OF)
    ).components["fundamental_score"]


def test_outputs_are_deterministic() -> None:
    market = make_market(
        DATES, ramp_tail(10.0, 20, 12.0), bench_close=100.0, vol=1000.0, turnover=1.0
    )
    first = compute_crowding_features(market, STOCK, AS_OF)
    second = compute_crowding_features(market, STOCK, AS_OF)
    assert first.as_dict() == second.as_dict()