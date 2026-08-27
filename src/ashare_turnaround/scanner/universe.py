"""Point-in-time investable-universe rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..pit.financial import canonicalize_financial_frame, select_financial_as_of


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    version: str = "universe-v1"
    min_listing_days: int = 120
    min_average_amount: float = 0.0
    liquidity_lookback: int = 20
    min_financial_periods: int = 4
    exclude_st: bool = True
    exclude_delisting: bool = True
    exclude_suspended: bool = True
    include_bse: bool = False
    require_reference_availability: bool = False


@dataclass(frozen=True, slots=True)
class UniverseDecision:
    ts_code: str
    included: bool
    reason: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UniverseResult:
    as_of_date: str
    version: str
    included: pd.DataFrame
    decisions: tuple[UniverseDecision, ...]
    warnings: tuple[str, ...] = ()

    @property
    def excluded(self) -> tuple[UniverseDecision, ...]:
        return tuple(value for value in self.decisions if not value.included)

    @property
    def status(self) -> str:
        if self.included.empty and self.decisions:
            return (
                "UNKNOWN" if any(value.reason == "unknown" for value in self.decisions) else "EMPTY"
            )
        return "PASS" if not self.warnings else "PARTIAL"


def _as_of(value: str | date | pd.Timestamp) -> tuple[str, pd.Timestamp]:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid universe as_of_date: {value!r}")
    timestamp = pd.Timestamp(parsed).normalize()
    return timestamp.strftime("%Y%m%d"), timestamp


def _value(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row.index and pd.notna(row[name]):
            return row[name]
    return None


def _is_bse(code: str, row: pd.Series) -> bool:
    market = str(_value(row, "market", "exchange") or "").upper()
    return code.upper().endswith(".BJ") or "BSE" in market or "北京" in market


def _financial_period_count(
    code: str,
    financial_frames: dict[str, pd.DataFrame] | None,
    as_of: pd.Timestamp,
) -> int:
    if not financial_frames:
        return 0
    periods: set[str] = set()
    for dataset, frame in financial_frames.items():
        if frame.empty or "ts_code" not in frame.columns:
            continue
        try:
            canonical = (
                frame.copy()
                if {"report_period", "actual_available_date"}.issubset(frame.columns)
                else canonicalize_financial_frame(dataset, frame)
            )
            values = select_financial_as_of(
                canonical,
                ts_code=code,
                as_of_date=as_of,
            )
        except (KeyError, ValueError):
            continue
        if "report_period" not in values.columns:
            continue
        dates = normalize_date_series(values["report_period"])
        periods.update(date.strftime("%Y%m%d") for date in dates.dropna().drop_duplicates())
    return len(periods)


def _market_history(
    code: str,
    daily_basic: pd.DataFrame | None,
    as_of: pd.Timestamp,
    lookback: int,
) -> pd.DataFrame:
    if daily_basic is None or daily_basic.empty or "ts_code" not in daily_basic.columns:
        return pd.DataFrame()
    frame = daily_basic.loc[daily_basic["ts_code"].astype("string").eq(code)].copy()
    if "trade_date" not in frame.columns:
        return pd.DataFrame()
    dates = normalize_date_series(frame["trade_date"])
    frame = frame.loc[dates.notna() & dates.le(as_of)].copy()
    return frame.sort_values("trade_date").tail(lookback)


def build_investable_universe(
    stock_basic: pd.DataFrame,
    *,
    as_of_date: str | date | pd.Timestamp,
    daily_basic: pd.DataFrame | None = None,
    financial_frames: dict[str, pd.DataFrame] | None = None,
    config: UniverseConfig | None = None,
) -> UniverseResult:
    """Apply explicit dated eligibility rules and return every exclusion reason."""

    settings = config or UniverseConfig()
    as_of_text, as_of = _as_of(as_of_date)
    if stock_basic.empty or "ts_code" not in stock_basic.columns:
        return UniverseResult(
            as_of_text, settings.version, pd.DataFrame(), (), ("missing_stock_basic",)
        )

    decisions: list[UniverseDecision] = []
    included_rows: list[dict[str, Any]] = []
    global_warnings: list[str] = []
    for _, row in stock_basic.sort_values("ts_code", kind="stable").iterrows():
        code = str(row.get("ts_code", "")).strip()
        if not code or code == "nan":
            continue
        warnings: list[str] = []
        reason = "eligible"
        included = True
        name = str(_value(row, "name") or "")
        status = str(_value(row, "list_status", "status") or "L").upper()
        delist_date = _value(row, "delist_date")
        parsed_delist = (
            pd.to_datetime(delist_date, errors="coerce")
            if delist_date is not None and pd.notna(delist_date)
            else pd.NaT
        )
        if settings.exclude_st and name.upper().lstrip("*").startswith("ST"):
            included, reason = False, "st_status"
        elif (
            settings.exclude_delisting
            and pd.notna(parsed_delist)
            and pd.Timestamp(parsed_delist).normalize() <= as_of
        ):
            included, reason = False, "delisted_by_as_of"
        elif settings.exclude_delisting and status in {"D", "DELISTED"} and pd.isna(
            parsed_delist
        ):
            included, reason = False, "delisting_status_missing_date"
        elif settings.exclude_delisting and status not in {
            "L",
            "LISTED",
            "NORMAL",
            "D",
            "DELISTED",
        }:
            included, reason = False, "delisting_or_non_listed_status"
        if included and _is_bse(code, row) and not settings.include_bse:
            included, reason = False, "bse_excluded_by_policy"
        list_date = _value(row, "list_date", "listing_date")
        if included and list_date is not None and pd.notna(list_date):
            parsed_list = pd.to_datetime(list_date, errors="coerce")
            if pd.notna(parsed_list):
                age = (as_of - pd.Timestamp(parsed_list).normalize()).days
                if age < 0:
                    included, reason = False, "listed_after_as_of"
                elif age < settings.min_listing_days:
                    included, reason = False, "new_listing"
        history = _market_history(code, daily_basic, as_of, settings.liquidity_lookback)
        if included and settings.min_average_amount > 0:
            if history.empty or "amount" not in history.columns:
                included, reason = False, "unknown_liquidity"
            else:
                average_amount = pd.to_numeric(history["amount"], errors="coerce").mean()
                if pd.isna(average_amount):
                    included, reason = False, "unknown_liquidity"
                elif average_amount < settings.min_average_amount:
                    included, reason = False, "low_liquidity"
        if included and settings.exclude_suspended and "is_suspend" in row.index:
            if str(row["is_suspend"]).lower() in {"1", "true", "yes", "suspended"}:
                included, reason = False, "long_term_suspension"
        periods = _financial_period_count(code, financial_frames, as_of)
        if included and financial_frames and periods < settings.min_financial_periods:
            included, reason = False, "insufficient_financial_history"
        for field_name in ("available_date", "actual_available_date"):
            if field_name in row.index and pd.notna(row[field_name]):
                available = pd.to_datetime(row[field_name], errors="coerce")
                if pd.notna(available) and pd.Timestamp(available).normalize() > as_of:
                    included, reason = False, "reference_not_available_at_as_of"
                    break
        if settings.require_reference_availability and "available_date" not in row.index:
            included, reason = False, "unknown_reference_availability"
        if included:
            if daily_basic is None or daily_basic.empty:
                warnings.append("market_history_not_loaded")
            included_row = row.to_dict()
            included_row["universe_reason"] = reason
            included_rows.append(included_row)
        decisions.append(UniverseDecision(code, included, reason, tuple(warnings)))
        global_warnings.extend(warnings)
    included_frame = pd.DataFrame(included_rows)
    if not included_frame.empty:
        included_frame = included_frame.sort_values("ts_code", kind="stable").reset_index(drop=True)
    return UniverseResult(
        as_of_text,
        settings.version,
        included_frame,
        tuple(decisions),
        tuple(dict.fromkeys(global_warnings)),
    )
