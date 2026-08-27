"""Versioned benchmark-relative return contract (``benchmark-v1``).

Excess return is defined only as::

    R_stock(t, L)     = P_stock(t) / P_stock(t-L) - 1
    R_benchmark(t, L) = B(t) / B(t-L) - 1
    excess_return(t,L)= R_stock(t, L) - R_benchmark(t, L)

where ``t`` is the as-of anchor trading session and ``t-L`` is the ``L``-th
prior open session on the session axis.  Excess return is *never* approximated
with stock-only returns: when the benchmark cannot be resolved at the required
sessions the affected features are ``unknown`` with an explicit reason
(fail-closed).

The benchmark is stored as index rows inside the ``daily`` dataset
(``ts_code == benchmark_id``), which is the same convention the forward
evaluation layer already uses for ``EvaluationConfig.benchmark_code``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from .common import market_history, numeric

DEFAULT_BENCHMARK_ID = "000300.SH"
DEFAULT_BENCHMARK_NAME = "CSI 300 Index"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Versioned, explicit benchmark identity and resolution conventions.

    Every field is recorded in feature evidence so any consumer can reproduce
    what a benchmark-relative value means.
    """

    version: str = "benchmark-v1"
    benchmark_id: str = DEFAULT_BENCHMARK_ID
    benchmark_name: str = DEFAULT_BENCHMARK_NAME
    data_source: str = (
        "tushare 'daily' dataset rows with ts_code == benchmark_id; "
        "same storage convention as scanner.evaluation.benchmark_code"
    )
    price_convention: str = "close"
    adjustment_convention: str = "unadjusted close exactly as stored in the PIT snapshot"
    trading_calendar: str = (
        "open sessions of the SSE/SZSE calendar (trade_cal is_open=1) when supplied; "
        "otherwise the benchmark rows themselves are the session authority"
    )
    lookbacks: tuple[int, ...] = (20, 60)
    endpoint_inclusion: str = (
        "as-of session t is the inclusive anchor; window start is the L-th prior open session"
    )
    high_window_sessions: int = 252
    high_include_as_of: bool = True
    high_min_sessions: int = 60
    missing_benchmark_policy: str = "unknown + reason; never fall back to stock-only return"

    def declared(self) -> dict[str, Any]:
        return asdict(self)


def _valid_close_rows(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or "_date" not in history.columns or "close" not in history.columns:
        return pd.DataFrame()
    closes = pd.to_numeric(history["close"], errors="coerce")
    return history.loc[closes.notna()].copy()


def _calendar_sessions(
    calendar_frame: pd.DataFrame | None, as_of: pd.Timestamp
) -> pd.DatetimeIndex | None:
    """Return open sessions from trade_cal up to as-of, if usable."""
    if (
        calendar_frame is None
        or calendar_frame.empty
        or "cal_date" not in calendar_frame.columns
        or "is_open" not in calendar_frame.columns
    ):
        return None
    frame = calendar_frame.copy()
    open_rows = pd.to_numeric(frame["is_open"], errors="coerce").fillna(0).eq(1)
    frame = frame.loc[open_rows].copy()
    if frame.empty:
        return None
    dates = normalize_date_series(frame["cal_date"])
    sessions = pd.DatetimeIndex(
        dates.loc[dates.notna() & dates.le(as_of)].drop_duplicates().sort_values()
    )
    if len(sessions) == 0:
        return None
    return pd.DatetimeIndex(sessions)


def _column_at(history: pd.DataFrame, session: pd.Timestamp, column: str) -> float | None:
    if history.empty or "_date" not in history.columns or column not in history.columns:
        return None
    rows = history.loc[history["_date"] == session, column]
    if rows.empty:
        return None
    return numeric(pd.to_numeric(rows, errors="coerce").dropna().iloc[-1]) if len(rows) else None


def _column_at_sessions(
    history: pd.DataFrame, sessions: list[pd.Timestamp], column: str
) -> list[float]:
    values: list[float] = []
    for session in sessions:
        value = _column_at(history, session, column)
        if value is not None:
            values.append(value)
    return values


@dataclass(frozen=True, slots=True)
class BenchmarkContext:
    """Resolved anchor session plus the session axis and PIT histories.

    ``status == \"known\"`` means the anchor session ``t`` exists and both the
    stock and the benchmark have a valid close at ``t``.  Anything else carries
    a machine-readable ``reason`` and must be treated as unknown end-to-end.
    """

    status: str
    reason: str | None
    anchor: pd.Timestamp | None
    axis: tuple[pd.Timestamp, ...]
    axis_source: str
    stock_history: pd.DataFrame
    benchmark_history: pd.DataFrame
    stock_close: float | None
    benchmark_close: float | None

    @property
    def known(self) -> bool:
        return self.status == "known" and self.anchor is not None


def _unknown_context(
    reason: str,
    *,
    stock_history: pd.DataFrame | None = None,
    benchmark_history: pd.DataFrame | None = None,
) -> BenchmarkContext:
    return BenchmarkContext(
        status="unknown",
        reason=reason,
        anchor=None,
        axis=(),
        axis_source="",
        stock_history=(
            stock_history
            if stock_history is not None and not stock_history.empty
            else pd.DataFrame()
        ),
        benchmark_history=(
            benchmark_history
            if benchmark_history is not None and not benchmark_history.empty
            else pd.DataFrame()
        ),
        stock_close=None,
        benchmark_close=None,
    )


def resolve_benchmark(
    market_frame: pd.DataFrame | None,
    stock_code: str,
    benchmark_id: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    session_lookback: int = 400,
    calendar_frame: pd.DataFrame | None = None,
) -> BenchmarkContext:
    """Resolve the shared as-of anchor session for stock and benchmark.

    Session axis priority:

    1. ``trade_cal`` open sessions (``is_open == 1``) when supplied and when
       the calendar reaches as-of; a stale calendar (its last open session is
       before the stock's last quote) is reported as ``calendar_stale``.
    2. The benchmark's own valid-close rows (the recorded trading sessions).

    Anchor, stock quote and benchmark quote must all agree on the same session.
    Misalignment is never resolved by shifting one side quietly.
    """

    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid benchmark as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()

    stock_history = market_history(market_frame, stock_code, as_of, session_lookback)
    benchmark_history = market_history(market_frame, benchmark_id, as_of, session_lookback)
    benchmark_clean = _valid_close_rows(benchmark_history)
    if benchmark_history.empty or benchmark_clean.empty:
        return _unknown_context(
            "benchmark_unavailable",
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )
    if stock_history.empty:
        return _unknown_context(
            "stock_no_market_history",
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )

    stock_closes = _valid_close_rows(stock_history)
    stock_last = (
        pd.Timestamp(stock_closes["_date"].iloc[-1]) if not stock_closes.empty else None
    )
    if stock_last is None:
        return _unknown_context(
            "stock_no_quote_at_anchor_session",
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )

    calendar_sessions = _calendar_sessions(calendar_frame, as_of)
    if calendar_sessions is not None:
        axis = tuple(calendar_sessions)
        axis_source = "trade_cal"
        anchor = axis[-1]
        if pd.Timestamp(axis[-1]) < stock_last:
            return _unknown_context(
                "calendar_stale",
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )
    else:
        axis = tuple(pd.Timestamp(value) for value in benchmark_clean["_date"])
        axis_source = "benchmark_rows"
        anchor = axis[-1]
        benchmark_last = axis[-1]
        if benchmark_last < stock_last:
            return _unknown_context(
                "benchmark_stale_at_as_of",
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )
        if stock_last < benchmark_last:
            return _unknown_context(
                "stock_no_quote_at_anchor_session",
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )

    stock_close = _column_at(stock_history, anchor, "close")
    if stock_close is None:
        return _unknown_context(
            "stock_no_quote_at_anchor_session",
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )
    benchmark_close = _column_at(benchmark_history, anchor, "close")
    if benchmark_close is None:
        if not benchmark_clean.empty and pd.Timestamp(benchmark_clean["_date"].iloc[-1]) < anchor:
            reason = "benchmark_stale_at_as_of"
        else:
            reason = "benchmark_missing_at_anchor_session"
        return _unknown_context(
            reason, stock_history=stock_history, benchmark_history=benchmark_history
        )
    return BenchmarkContext(
        status="known",
        reason=None,
        anchor=anchor,
        axis=axis,
        axis_source=axis_source,
        stock_history=stock_history,
        benchmark_history=benchmark_history,
        stock_close=stock_close,
        benchmark_close=benchmark_close,
    )


@dataclass(frozen=True, slots=True)
class WindowReturn:
    """Stock, benchmark and excess return over the same L-session window."""

    status: str
    reason: str | None
    lookback: int
    anchor: pd.Timestamp | None
    window_start: pd.Timestamp | None
    sessions: int
    stock_return: float | None
    benchmark_return: float | None
    excess_return: float | None
    stock_close_start: float | None
    stock_close_end: float | None
    benchmark_close_start: float | None
    benchmark_close_end: float | None


def window_return(ctx: BenchmarkContext, lookback: int) -> WindowReturn:
    """Return over the window ``[t-L, t]`` on the shared session axis."""

    if not ctx.known:
        return WindowReturn(
            "unknown", ctx.reason, lookback, None, None, 0, None, None, None, None, None, None, None
        )
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return WindowReturn(
            "unknown", "anchor_not_on_session_axis", lookback, ctx.anchor, None, 0,
            None, None, None, None, None, None, None,
        )
    if position < lookback:
        return WindowReturn(
            "unknown", "insufficient_benchmark_history", lookback, ctx.anchor, None, 0,
            None, None, None, None, None, None, None,
        )
    window_start = axis[position - lookback]
    stock_start = _column_at(ctx.stock_history, window_start, "close")
    if stock_start is None:
        return WindowReturn(
            "unknown", "stock_missing_at_window_start", lookback, ctx.anchor, window_start,
            lookback + 1, None, None, None, None, ctx.stock_close, None, None,
        )
    benchmark_start = _column_at(ctx.benchmark_history, window_start, "close")
    if benchmark_start is None:
        return WindowReturn(
            "unknown", "benchmark_missing_at_window_start", lookback, ctx.anchor, window_start,
            lookback + 1, None, None, None, ctx.stock_close, ctx.stock_close, None, None,
        )
    stock_return = (
        ctx.stock_close / stock_start - 1.0 if stock_start not in {None, 0.0} else None
    )
    benchmark_return = (
        ctx.benchmark_close / benchmark_start - 1.0
        if benchmark_start not in {None, 0.0}
        else None
    )
    if stock_return is None or benchmark_return is None:
        reason = "invalid_window_price"
    else:
        reason = None
    excess = stock_return - benchmark_return if reason is None else None
    return WindowReturn(
        "unknown" if reason is not None else "known",
        reason,
        lookback,
        ctx.anchor,
        window_start,
        lookback + 1,
        stock_return,
        benchmark_return,
        excess,
        stock_start,
        ctx.stock_close,
        benchmark_start,
        ctx.benchmark_close,
    )


@dataclass(frozen=True, slots=True)
class HighWindow:
    """52-week-high statistics over a bounded session window ending at anchor."""

    status: str
    reason: str | None
    window_start: pd.Timestamp | None
    window_end: pd.Timestamp | None
    session_count: int
    observation_count: int
    high: float | None
    current_close: float | None
    distance: float | None


def high_window(
    ctx: BenchmarkContext,
    *,
    window_sessions: int = 252,
    include_as_of: bool = True,
    min_sessions: int = 60,
) -> HighWindow:
    """Distance from the high over the trailing ``window_sessions`` sessions.

    ``include_as_of=True`` counts the anchor session inside the window; the
    window then spans ``window_sessions + 1`` sessions ending at the anchor.
    The distance is ``1 - close(t) / max(close over window)``.
    """

    if not ctx.known:
        return HighWindow("unknown", ctx.reason, None, None, 0, 0, None, None, None)
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return HighWindow(
            "unknown", "anchor_not_on_session_axis", None, None, 0, 0, None, None, None
        )
    offset = window_sessions - 1 if include_as_of else window_sessions
    if position < offset:
        return HighWindow(
            "unknown", "insufficient_52w_history", None, None, 0, 0, None, None, None
        )
    start = position - offset
    window = axis[start : position + 1]
    closes = _column_at_sessions(ctx.stock_history, window, "close")
    if len(closes) < min_sessions:
        return HighWindow(
            "unknown",
            "insufficient_52w_history",
            window[0],
            window[-1],
            len(window),
            len(closes),
            None,
            None,
            None,
        )
    high = max(closes)
    if high in {None, 0.0}:
        return HighWindow(
            "unknown",
            "invalid_high_price",
            window[0],
            window[-1],
            len(window),
            len(closes),
            None,
            None,
            None,
        )
    distance = 1.0 - ctx.stock_close / high
    return HighWindow(
        "known",
        None,
        window[0],
        window[-1],
        len(window),
        len(closes),
        high,
        ctx.stock_close,
        distance,
    )


@dataclass(frozen=True, slots=True)
class PriorBaseline:
    """Median of a column over the ``window`` sessions strictly before anchor."""

    status: str
    reason: str | None
    baseline: float | None
    current: float | None
    observations: int
    window_start: pd.Timestamp | None
    window_end: pd.Timestamp | None


def prior_baseline(
    ctx: BenchmarkContext,
    column: str,
    *,
    window: int = 60,
    min_observations: int = 20,
) -> PriorBaseline:
    """Median of ``column`` over the ``window`` sessions before the anchor."""

    if not ctx.known:
        return PriorBaseline("unknown", ctx.reason, None, None, 0, None, None)
    if column not in ctx.stock_history.columns:
        return PriorBaseline("unknown", f"{column}_unavailable", None, None, 0, None, None)
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return PriorBaseline(
            "unknown", "anchor_not_on_session_axis", None, None, 0, None, None
        )
    if position < window:
        return PriorBaseline(
            "unknown", "insufficient_price_history", None, None, 0, None, None
        )
    baseline_sessions = axis[position - window : position]
    values = _column_at_sessions(ctx.stock_history, baseline_sessions, column)
    current = _column_at(ctx.stock_history, ctx.anchor, column)
    if len(values) < min_observations:
        return PriorBaseline(
            "insufficient_data",
            "insufficient_baseline_window",
            None,
            current,
            len(values),
            baseline_sessions[0],
            baseline_sessions[-1],
        )
    median_value = float(pd.Series(values).median())
    return PriorBaseline(
        "known",
        None,
        median_value,
        current,
        len(values),
        baseline_sessions[0],
        baseline_sessions[-1],
    )


def session_axis(ctx: BenchmarkContext) -> tuple[pd.Timestamp, ...]:
    return ctx.axis