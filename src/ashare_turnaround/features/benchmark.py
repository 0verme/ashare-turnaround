"""Versioned benchmark-relative market-session calculations.

The benchmark contract is deliberately small and fail-closed.  A stock return
is never published as an excess return when the configured benchmark endpoint
is unavailable or is on a different session.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from .common import market_history, numeric

BENCHMARK_CONTRACT_VERSION = "benchmark-v1"
DEFAULT_BENCHMARK_ID = "000300.SH"
DEFAULT_BENCHMARK_NAME = "CSI 300 Index"
BENCHMARK_SOURCE_DATASET = "index_basic + index_daily"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Explicit identity and conventions for the primary benchmark."""

    version: str = BENCHMARK_CONTRACT_VERSION
    benchmark_id: str = DEFAULT_BENCHMARK_ID
    benchmark_name: str = DEFAULT_BENCHMARK_NAME
    source_dataset: str = BENCHMARK_SOURCE_DATASET
    definition_dataset: str = "index_basic"
    price_dataset: str = "index_daily"
    price_convention: str = "close"
    adjustment_convention: str = (
        "raw/unadjusted close exactly as stored; corporate-action adjustment is not proven"
    )
    trading_calendar: str = (
        "trade_cal is_open=1 sessions when supplied; otherwise union of stock/index_daily sessions"
    )
    lookbacks: tuple[int, ...] = (20, 60)
    endpoint_inclusion: str = (
        "inclusive anchor t and the L-th prior open session t-L; exact endpoint alignment"
    )
    stock_suspension_policy: str = (
        "market-session axis; missing stock endpoint is unknown, interior gaps are retained"
    )
    high_window_sessions: int = 252
    high_include_as_of: bool = False
    high_min_sessions: int = 60
    missing_benchmark_policy: str = "unknown + reason; never fall back to stock-only return"

    def __post_init__(self) -> None:
        benchmark_id = str(self.benchmark_id).strip().upper()
        if not benchmark_id or "." not in benchmark_id:
            raise ValueError("benchmark_id must be a non-empty Tushare symbol")
        object.__setattr__(self, "benchmark_id", benchmark_id)
        if not self.lookbacks or any(int(value) <= 0 for value in self.lookbacks):
            raise ValueError("benchmark lookbacks must be positive")
        if self.high_window_sessions <= 0 or self.high_min_sessions <= 0:
            raise ValueError("52-week window settings must be positive")
        if self.high_min_sessions > self.high_window_sessions:
            raise ValueError("high_min_sessions must not exceed high_window_sessions")

    @property
    def benchmark_contract_version(self) -> str:
        """Readable alias used by report consumers."""

        return self.version

    @property
    def data_source(self) -> str:
        """Backward-compatible name for the declared source dataset."""

        return self.source_dataset

    def declared(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark_contract_version"] = self.version
        payload["data_source"] = self.source_dataset
        return payload


def _valid_close_rows(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or "_date" not in history.columns or "close" not in history.columns:
        return pd.DataFrame()
    closes = pd.to_numeric(history["close"], errors="coerce")
    finite = closes.replace([float("inf"), -float("inf")], pd.NA).notna()
    return history.loc[finite & closes.gt(0)].copy()


def _calendar_sessions(
    calendar_frame: pd.DataFrame | None, as_of: pd.Timestamp
) -> pd.DatetimeIndex | None:
    """Return unique open trade-calendar sessions through ``as_of``."""

    if (
        calendar_frame is None
        or calendar_frame.empty
        or "cal_date" not in calendar_frame.columns
        or "is_open" not in calendar_frame.columns
    ):
        return None
    open_mask = pd.to_numeric(calendar_frame["is_open"], errors="coerce").eq(1)
    dates = normalize_date_series(calendar_frame["cal_date"])
    values = dates.loc[open_mask & dates.notna() & dates.le(as_of)].drop_duplicates()
    if values.empty:
        return None
    return pd.DatetimeIndex(values.sort_values())


def _column_at(history: pd.DataFrame, session: pd.Timestamp, column: str) -> float | None:
    if history.empty or "_date" not in history.columns or column not in history.columns:
        return None
    rows = history.loc[history["_date"].eq(session), column]
    if rows.empty:
        return None
    clean = pd.to_numeric(rows, errors="coerce").dropna()
    return numeric(clean.iloc[-1]) if not clean.empty else None


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
    """PIT histories resolved to one shared market-session anchor."""

    status: str
    reason: str | None
    anchor: pd.Timestamp | None
    axis: tuple[pd.Timestamp, ...]
    axis_source: str
    stock_history: pd.DataFrame
    benchmark_history: pd.DataFrame
    stock_close: float | None
    benchmark_close: float | None
    benchmark_id: str = DEFAULT_BENCHMARK_ID

    @property
    def known(self) -> bool:
        return self.status == "known" and self.anchor is not None


def _unknown_context(
    reason: str,
    benchmark_id: str,
    *,
    stock_history: pd.DataFrame | None = None,
    benchmark_history: pd.DataFrame | None = None,
) -> BenchmarkContext:
    return BenchmarkContext(
        status="unknown",
        reason=reason,
        benchmark_id=benchmark_id,
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


def _contains_code(frame: pd.DataFrame | None, code: str) -> bool:
    return bool(
        frame is not None
        and not frame.empty
        and "ts_code" in frame.columns
        and frame["ts_code"].astype("string").eq(str(code)).any()
    )


def resolve_benchmark(
    market_frame: pd.DataFrame | None,
    stock_code: str,
    benchmark_id: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    benchmark_frame: pd.DataFrame | None = None,
    benchmark_definition_frame: pd.DataFrame | None = None,
    suspension_frame: pd.DataFrame | None = None,
    session_lookback: int = 400,
    calendar_frame: pd.DataFrame | None = None,
) -> BenchmarkContext:
    """Resolve one exact stock/benchmark session axis.

    ``benchmark_frame`` is the preferred separate ``index_daily`` input.  For
    compatibility with small synthetic fixtures, when it is omitted and the
    supplied market frame contains the benchmark code, that frame is used as a
    combined input.  Production replay passes an explicit (possibly empty)
    ``index_daily`` frame and therefore never falls back to stock ``daily``.
    When supplied, ``suspension_frame`` removes explicitly suspended stock
    sessions before endpoint resolution.
    """

    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid benchmark as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    benchmark_code = str(benchmark_id).strip().upper()
    if session_lookback <= 0:
        raise ValueError("session_lookback must be positive")

    stock_history = market_history(market_frame, stock_code, as_of, session_lookback)
    if suspension_frame is not None and not suspension_frame.empty:
        required = {"ts_code", "trade_date"}
        if required.issubset(suspension_frame.columns) and not stock_history.empty:
            suspended = suspension_frame.loc[
                suspension_frame["ts_code"].astype("string").eq(str(stock_code))
            ]
            suspended_dates = normalize_date_series(suspended["trade_date"]).dropna()
            stock_history = stock_history.loc[
                ~stock_history["_date"].isin(set(suspended_dates))
            ].reset_index(drop=True)
    if benchmark_frame is None:
        benchmark_input = market_frame if _contains_code(market_frame, benchmark_code) else None
    else:
        benchmark_input = benchmark_frame
    benchmark_history = market_history(
        benchmark_input, benchmark_code, as_of, session_lookback
    )

    # A supplied definition snapshot is optional for synthetic unit fixtures,
    # but if it contains rows it must identify the configured benchmark.
    if (
        benchmark_definition_frame is not None
        and not benchmark_definition_frame.empty
        and "ts_code" in benchmark_definition_frame.columns
        and not _contains_code(benchmark_definition_frame, benchmark_code)
    ):
        return _unknown_context(
            "benchmark_definition_missing",
            benchmark_code,
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )

    benchmark_clean = _valid_close_rows(benchmark_history)
    stock_clean = _valid_close_rows(stock_history)
    if benchmark_clean.empty:
        return _unknown_context(
            "missing_benchmark_endpoint",
            benchmark_code,
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )
    if stock_clean.empty:
        return _unknown_context(
            "stock_no_market_history",
            benchmark_code,
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )

    stock_last = pd.Timestamp(stock_clean["_date"].iloc[-1])
    benchmark_last = pd.Timestamp(benchmark_clean["_date"].iloc[-1])
    calendar_sessions = _calendar_sessions(calendar_frame, as_of)
    if calendar_sessions is not None:
        axis = tuple(pd.Timestamp(value) for value in calendar_sessions)
        axis_source = "trade_cal"
        anchor = axis[-1]
        if axis[-1] < stock_last or axis[-1] < benchmark_last:
            return _unknown_context(
                "calendar_stale",
                benchmark_code,
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )
    else:
        session_values = {pd.Timestamp(value) for value in benchmark_clean["_date"]}
        session_values.update(pd.Timestamp(value) for value in stock_clean["_date"])
        axis = tuple(sorted(session_values))
        axis_source = "market_rows_union"
        anchor = axis[-1]
        if benchmark_last < stock_last:
            return _unknown_context(
                "benchmark_stale_at_as_of",
                benchmark_code,
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )
        if stock_last < benchmark_last:
            return _unknown_context(
                "stock_no_quote_at_anchor_session",
                benchmark_code,
                stock_history=stock_history,
                benchmark_history=benchmark_history,
            )

    stock_close = _column_at(stock_history, anchor, "close")
    if stock_close is None or stock_close <= 0:
        return _unknown_context(
            "stock_no_quote_at_anchor_session",
            benchmark_code,
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )
    benchmark_close = _column_at(benchmark_history, anchor, "close")
    if benchmark_close is None or benchmark_close <= 0:
        reason = (
            "benchmark_stale_at_as_of"
            if benchmark_last < anchor
            else "benchmark_missing_at_anchor_session"
        )
        return _unknown_context(
            reason,
            benchmark_code,
            stock_history=stock_history,
            benchmark_history=benchmark_history,
        )
    return BenchmarkContext(
        status="known",
        reason=None,
        benchmark_id=benchmark_code,
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
    """Stock, benchmark, and excess return over the same L-session window."""

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
    """Calculate returns from exact ``t-L`` to exact ``t`` sessions."""

    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if not ctx.known:
        return WindowReturn(
            "unknown",
            ctx.reason,
            lookback,
            None,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return WindowReturn(
            "unknown",
            "anchor_not_on_session_axis",
            lookback,
            ctx.anchor,
            None,
            0,
            None,
            None,
            None,
            None,
            ctx.stock_close,
            None,
            ctx.benchmark_close,
        )
    if position < lookback:
        return WindowReturn(
            "unknown",
            "insufficient_benchmark_history",
            lookback,
            ctx.anchor,
            None,
            0,
            None,
            None,
            None,
            None,
            ctx.stock_close,
            None,
            ctx.benchmark_close,
        )

    window_start = axis[position - lookback]
    stock_start = _column_at(ctx.stock_history, window_start, "close")
    benchmark_start = _column_at(ctx.benchmark_history, window_start, "close")
    if stock_start is None:
        reason = "stock_missing_at_window_start"
    elif benchmark_start is None:
        reason = "benchmark_missing_at_window_start"
    elif stock_start <= 0 or benchmark_start <= 0:
        reason = "invalid_window_price"
    else:
        reason = None
    stock_return = (
        ctx.stock_close / stock_start - 1.0
        if reason not in {"stock_missing_at_window_start", "invalid_window_price"}
        and stock_start not in {None, 0.0}
        else None
    )
    benchmark_return = (
        ctx.benchmark_close / benchmark_start - 1.0
        if reason not in {"benchmark_missing_at_window_start", "invalid_window_price"}
        and benchmark_start not in {None, 0.0}
        else None
    )
    if reason is None and (stock_return is None or benchmark_return is None):
        reason = "invalid_window_price"
    if reason is None and any(
        value is None or not math.isfinite(float(value))
        for value in (stock_return, benchmark_return, stock_return - benchmark_return)
    ):
        reason = "invalid_window_price"
    if reason == "invalid_window_price":
        stock_return = numeric(stock_return)
        benchmark_return = numeric(benchmark_return)
    excess = stock_return - benchmark_return if reason is None else None
    return WindowReturn(
        "known" if reason is None else "unknown",
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
    """Price versus a bounded prior/high window ending before or at anchor."""

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
    include_as_of: bool = False,
    min_sessions: int = 60,
) -> HighWindow:
    """Calculate ``current_close / prior_high - 1``.

    The default is the recommended prior-252-session rule: the current session
    is excluded from the reference high, so a new high has a positive distance
    rather than contaminating its own denominator.
    """

    if window_sessions <= 0 or min_sessions <= 0:
        raise ValueError("high window settings must be positive")
    if not ctx.known:
        return HighWindow("unknown", ctx.reason, None, None, 0, 0, None, None, None)
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return HighWindow(
            "unknown", "anchor_not_on_session_axis", None, None, 0, 0, None, None, None
        )
    if include_as_of:
        if position + 1 < window_sessions:
            return HighWindow(
                "unknown", "insufficient_52w_history", None, None, 0, 0, None, None, None
            )
        start = position - window_sessions + 1
        end = position + 1
    else:
        if position < window_sessions:
            return HighWindow(
                "unknown", "insufficient_52w_history", None, None, 0, 0, None, None, None
            )
        start = position - window_sessions
        end = position
    window = axis[start:end]
    closes = [
        value
        for session in window
        for value in (_column_at(ctx.stock_history, session, "close"),)
        if value is not None and value > 0
    ]
    if len(closes) < min_sessions:
        return HighWindow(
            "unknown",
            "insufficient_52w_history",
            window[0] if window else None,
            window[-1] if window else None,
            len(window),
            len(closes),
            None,
            ctx.stock_close,
            None,
        )
    high = max(closes)
    if high <= 0:
        return HighWindow(
            "unknown",
            "invalid_high_price",
            window[0],
            window[-1],
            len(window),
            len(closes),
            None,
            ctx.stock_close,
            None,
        )
    distance = ctx.stock_close / high - 1.0
    if not math.isfinite(distance):
        return HighWindow(
            "unknown",
            "invalid_high_price",
            window[0],
            window[-1],
            len(window),
            len(closes),
            high,
            ctx.stock_close,
            None,
        )
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
    """Median of a metric over sessions strictly before the anchor."""

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
    """Calculate a past-only baseline without filling missing stock sessions."""

    if window <= 0 or min_observations <= 0:
        raise ValueError("baseline settings must be positive")
    if not ctx.known:
        return PriorBaseline("unknown", ctx.reason, None, None, 0, None, None)
    if column not in ctx.stock_history.columns:
        return PriorBaseline("unknown", f"{column}_unavailable", None, None, 0, None, None)
    axis = list(ctx.axis)
    try:
        position = axis.index(ctx.anchor)
    except ValueError:
        return PriorBaseline("unknown", "anchor_not_on_session_axis", None, None, 0, None, None)
    if position < window:
        return PriorBaseline("unknown", "insufficient_price_history", None, None, 0, None, None)
    baseline_sessions = axis[position - window : position]
    values = [
        value
        for value in _column_at_sessions(ctx.stock_history, baseline_sessions, column)
        if value > 0
    ]
    current = _column_at(ctx.stock_history, ctx.anchor, column)
    if current is None or current <= 0:
        return PriorBaseline(
            "unknown",
            f"{column}_missing_at_anchor",
            None,
            current,
            len(values),
            baseline_sessions[0],
            baseline_sessions[-1],
        )
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
    baseline = float(pd.Series(values, dtype="float64").median())
    if baseline <= 0:
        return PriorBaseline(
            "unknown",
            f"{column}_invalid_baseline",
            None,
            current,
            len(values),
            baseline_sessions[0],
            baseline_sessions[-1],
        )
    return PriorBaseline(
        "known",
        None,
        baseline,
        current,
        len(values),
        baseline_sessions[0],
        baseline_sessions[-1],
    )


def session_axis(ctx: BenchmarkContext) -> tuple[pd.Timestamp, ...]:
    """Expose the resolved market-session axis for evidence/test consumers."""

    return ctx.axis
