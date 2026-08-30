"""Snapshot-local, point-in-time replay indexes.

The cache in this module is scoped to one replay invocation through a
``ContextVar``.  It is an execution aid only: no index, normalized column, or
cache marker is serialized into a replay result or its semantic fingerprint.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pandas as pd

from .dates import normalize_date_series

_CURRENT_CACHE: ContextVar[ReplaySnapshotCache | None] = ContextVar(
    "ashare_turnaround_replay_snapshot_cache",
    default=None,
)


def _code_positions(frame: pd.DataFrame | None) -> Mapping[str, tuple[int, ...]]:
    if frame is None or frame.empty or "ts_code" not in frame.columns:
        return MappingProxyType({})
    codes = frame["ts_code"].astype("string")
    grouped = codes.groupby(codes, sort=False, dropna=True)
    return MappingProxyType(
        {
            str(code): tuple(int(position) for position in positions)
            for code, positions in grouped.indices.items()
            if pd.notna(code)
        }
    )


def _daily_basic_histories(
    frame: pd.DataFrame | None,
    *,
    as_of: pd.Timestamp,
    lookback: int,
) -> Mapping[str, pd.DataFrame] | None:
    if (
        frame is None
        or frame.empty
        or not {"ts_code", "trade_date"}.issubset(frame.columns)
        or frame.duplicated(["ts_code", "trade_date"], keep=False).any()
    ):
        return None
    codes = frame["ts_code"].astype("string")
    dates = normalize_date_series(frame["trade_date"])
    visible = frame.loc[codes.notna() & dates.notna() & dates.le(as_of)].copy()
    if visible.empty:
        return MappingProxyType({})
    visible["_cache_code"] = codes.loc[visible.index].astype("string")
    visible["_cache_date"] = dates.loc[visible.index]
    visible = visible.sort_values(["_cache_code", "_cache_date"], kind="stable")
    histories = {
        str(code): group.drop(columns=["_cache_code", "_cache_date"]).tail(lookback).copy()
        for code, group in visible.groupby("_cache_code", sort=False, dropna=True)
    }
    return MappingProxyType(histories)


def _suspended_codes(
    frame: pd.DataFrame | None,
    *,
    as_of: pd.Timestamp,
) -> frozenset[str]:
    if (
        frame is None
        or frame.empty
        or not {"ts_code", "trade_date"}.issubset(frame.columns)
    ):
        return frozenset()
    codes = frame["ts_code"].astype("string")
    mask = codes.notna()
    if "suspend_type" in frame.columns:
        mask &= frame["suspend_type"].astype("string").str.upper().eq("S")
    dates = normalize_date_series(frame["trade_date"])
    mask &= dates.notna() & dates.eq(as_of)
    return frozenset(str(code) for code in codes.loc[mask].tolist())


@dataclass(slots=True)
class ReplaySnapshotCache:
    """Read-only indexes and memoized histories for one ``as_of`` snapshot."""

    as_of: pd.Timestamp
    daily_basic_frame_id: int | None = None
    daily_basic_positions: Mapping[str, tuple[int, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    daily_basic_history_by_code: Mapping[str, pd.DataFrame] | None = None
    daily_basic_history_lookback: int = 0
    market_frame_id: int | None = None
    market_positions: Mapping[str, tuple[int, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    financial_frame_ids: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    financial_positions: Mapping[str, Mapping[str, tuple[int, ...]]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    canonical_histories: dict[tuple[str, str, int, int], pd.DataFrame] = field(
        default_factory=dict
    )
    single_quarter_histories: dict[
        tuple[int, str, tuple[str, ...], str], tuple[pd.DataFrame, dict[str, str]]
    ] = field(default_factory=dict)
    trend_series: dict[tuple[int, str], tuple[pd.DataFrame, str | None]] = field(
        default_factory=dict
    )
    semantic_period_indexes: dict[
        tuple[int, str], dict[tuple[Any, ...], tuple[int, ...]]
    ] = field(default_factory=dict)
    semantic_period_rows: dict[
        tuple[int, str], tuple[dict[str, Any], ...]
    ] = field(default_factory=dict)
    period_identities: dict[tuple[str, tuple[Any, ...]], Any] = field(default_factory=dict)
    period_identities_by_version: dict[
        tuple[str, str, tuple[str, ...]], Any
    ] = field(default_factory=dict)
    benchmark_contexts: dict[tuple[Any, ...], Any] = field(default_factory=dict)
    market_column_values: dict[tuple[int, str], dict[pd.Timestamp, Any]] = field(
        default_factory=dict
    )
    suspension_frame_id: int | None = None
    suspended_codes: frozenset[str] = frozenset()

    @classmethod
    def from_frames(
        cls,
        frames: Mapping[str, pd.DataFrame],
        *,
        as_of: pd.Timestamp,
        daily_basic_lookback: int = 20,
    ) -> ReplaySnapshotCache:
        financial_ids: dict[str, int] = {}
        financial_positions: dict[str, Mapping[str, tuple[int, ...]]] = {}
        for dataset in ("income", "balancesheet", "cashflow", "fina_indicator"):
            frame = frames.get(dataset)
            if frame is None:
                continue
            financial_ids[dataset] = id(frame)
            financial_positions[dataset] = _code_positions(frame)
        suspension = frames.get("suspend_d")
        daily_basic = frames.get("daily_basic")
        normalized_as_of = pd.Timestamp(as_of).normalize()
        return cls(
            as_of=normalized_as_of,
            daily_basic_frame_id=id(daily_basic) if daily_basic is not None else None,
            daily_basic_positions=_code_positions(daily_basic),
            daily_basic_history_by_code=_daily_basic_histories(
                daily_basic,
                as_of=normalized_as_of,
                lookback=daily_basic_lookback,
            ),
            daily_basic_history_lookback=daily_basic_lookback,
            financial_frame_ids=MappingProxyType(financial_ids),
            financial_positions=MappingProxyType(financial_positions),
            suspension_frame_id=id(suspension) if suspension is not None else None,
            suspended_codes=_suspended_codes(suspension, as_of=as_of),
        )

    def set_market_frame(self, frame: pd.DataFrame) -> None:
        self.market_frame_id = id(frame)
        self.market_positions = _code_positions(frame)

    @staticmethod
    def _subset(
        frame: pd.DataFrame,
        positions: Mapping[str, tuple[int, ...]],
        code: str,
    ) -> pd.DataFrame:
        values = positions.get(str(code), ())
        return frame.iloc[list(values)].copy() if values else frame.iloc[0:0].copy()

    def daily_basic_history_for_code(
        self,
        frame: pd.DataFrame | None,
        code: str,
        *,
        as_of: pd.Timestamp,
        lookback: int,
    ) -> pd.DataFrame | None:
        if (
            frame is None
            or id(frame) != self.daily_basic_frame_id
            or pd.Timestamp(as_of).normalize() != self.as_of
            or lookback > self.daily_basic_history_lookback
        ):
            return None
        if self.daily_basic_history_by_code is not None:
            history = self.daily_basic_history_by_code.get(str(code))
            return (
                history.tail(lookback).copy()
                if history is not None
                else frame.iloc[0:0].copy()
            )
        return self._subset(frame, self.daily_basic_positions, code)

    def market_for_code(self, frame: pd.DataFrame | None, code: str) -> pd.DataFrame | None:
        if frame is None or id(frame) != self.market_frame_id:
            return None
        return self._subset(frame, self.market_positions, code)

    def financial_for_code(
        self,
        dataset: str,
        frame: pd.DataFrame | None,
        code: str,
    ) -> pd.DataFrame | None:
        if frame is None or self.financial_frame_ids.get(dataset) != id(frame):
            return None
        positions = self.financial_positions.get(dataset, {})
        return self._subset(frame, positions, code)

    def has_suspension_on_as_of(self, frame: pd.DataFrame | None, code: str) -> bool | None:
        if frame is None or id(frame) != self.suspension_frame_id:
            return None
        return str(code) in self.suspended_codes

    def column_values_for(
        self, history: pd.DataFrame, column: str
    ) -> dict[pd.Timestamp, Any] | None:
        if "_date" not in history.columns or column not in history.columns:
            return None
        key = (id(history), column)
        values = self.market_column_values.get(key)
        if values is None:
            values = {
                pd.Timestamp(observed_date): value
                for observed_date, value in zip(history["_date"], history[column])
            }
            self.market_column_values[key] = values
        return values

    def clear_candidate_state(self) -> None:
        """Drop histories derived for the completed candidate only.

        Feature vectors retain values and provenance dictionaries, not these
        working frames.  Clearing them prevents a long replay from retaining
        every candidate's temporary pandas objects while preserving all
        snapshot-level indexes.
        """

        self.canonical_histories.clear()
        self.single_quarter_histories.clear()
        self.trend_series.clear()
        self.semantic_period_indexes.clear()
        self.semantic_period_rows.clear()
        self.period_identities.clear()
        self.period_identities_by_version.clear()
        self.benchmark_contexts.clear()
        self.market_column_values.clear()

    def canonical_history_key(
        self,
        dataset: str,
        frame: pd.DataFrame,
        code: str,
        disclosure_frame: pd.DataFrame | None,
    ) -> tuple[str, str, int, int]:
        return (dataset, str(code), id(frame), id(disclosure_frame))


@contextmanager
def replay_cache_scope(cache: ReplaySnapshotCache) -> Iterator[ReplaySnapshotCache]:
    token: Token[ReplaySnapshotCache | None] = _CURRENT_CACHE.set(cache)
    try:
        yield cache
    finally:
        _CURRENT_CACHE.reset(token)


def current_replay_cache() -> ReplaySnapshotCache | None:
    return _CURRENT_CACHE.get()


__all__ = [
    "ReplaySnapshotCache",
    "replay_cache_scope",
    "current_replay_cache",
]
