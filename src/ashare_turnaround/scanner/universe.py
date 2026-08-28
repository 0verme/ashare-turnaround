"""Point-in-time investable-universe rules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..pit.financial import canonicalize_financial_frame, select_financial_as_of

HISTORICAL_UNIVERSE_CONTRACT_VERSION = "historical-universe-v1"


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
    # Historical replay may only use stable identifiers and dated listing /
    # delisting events.  Current stock_basic name/status/industry/board fields
    # are deliberately ignored in this mode (see market-reference-history).
    pit_safe_only: bool = False


@dataclass(frozen=True, slots=True)
class UniverseDecision:
    ts_code: str
    included: bool
    reason: str
    warnings: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "included": self.included,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class UniverseResult:
    as_of_date: str
    version: str
    included: pd.DataFrame
    decisions: tuple[UniverseDecision, ...]
    warnings: tuple[str, ...] = ()
    source_evidence: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "version": self.version,
            "status": self.status,
            "included": self.included.to_dict(orient="records"),
            "decisions": [decision.as_dict() for decision in self.decisions],
            "warnings": list(self.warnings),
            "source_evidence": dict(self.source_evidence),
            "limitations": list(self.limitations),
        }


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


def _is_bse_from_identifier(code: str) -> bool:
    """Use only the stable symbol namespace for the historical BSE policy."""

    return str(code).upper().endswith(".BJ")


def _suspended_on_date(
    code: str,
    suspension_frame: pd.DataFrame | None,
    as_of: pd.Timestamp,
) -> bool:
    """Return whether a dated suspension observation covers the decision day."""

    if suspension_frame is None or suspension_frame.empty:
        return False
    required = {"ts_code", "trade_date"}
    if not required.issubset(suspension_frame.columns):
        return False
    rows = suspension_frame.loc[suspension_frame["ts_code"].astype("string").eq(str(code))]
    if "suspend_type" in rows.columns:
        rows = rows.loc[rows["suspend_type"].astype("string").str.upper().eq("S")]
    dates = normalize_date_series(rows["trade_date"])
    return bool(dates.notna().eq(True).any() and dates.eq(as_of).any())


def _historical_source_evidence() -> tuple[dict[str, Any], tuple[str, ...]]:
    """Describe the intentionally narrow reference surface used by PIT replay."""

    return (
        {
            "dataset": "stock_basic",
            "semantics": "current_snapshot_with_static_event_fields_only",
            "safe_fields": ["ts_code", "list_date", "delist_date"],
            "used_fields": ["ts_code", "list_date", "delist_date"],
            "ignored_fields": [
                "name",
                "list_status",
                "status",
                "industry",
                "market",
                "board",
                "exchange",
                "is_hs",
                "act_name",
                "act_ent_type",
            ],
            "listing_rule": "list_date <= as_of; listing age is measured from list_date",
            "delisting_rule": "exclude only when a supplied delist_date <= as_of",
            "bse_rule": "stable ts_code .BJ namespace only; current board fields ignored",
            "status_rule": "current name/status/industry/board are never consulted",
        },
        (
            "stock_basic_name_status_industry_board_unsupported_pit",
            "historical_universe_uses_static_listing_boundaries_only",
        ),
    )


def _financial_period_counts(
    financial_frames: dict[str, pd.DataFrame] | None,
    as_of: pd.Timestamp,
) -> dict[str, int]:
    """Count visible report periods for all symbols with vectorized filters."""

    if not financial_frames:
        return {}
    periods_by_code: dict[str, set[str]] = {}
    for frame in financial_frames.values():
        if frame.empty or "ts_code" not in frame.columns:
            continue
        period = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
        for field_name in ("report_period", "end_date"):
            if field_name in frame.columns:
                period = period.fillna(normalize_date_series(frame[field_name]))
        available = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
        for field_name in ("actual_available_date", "f_ann_date", "ann_date"):
            if field_name in frame.columns:
                available = available.fillna(normalize_date_series(frame[field_name]))
        visible = (
            frame["ts_code"].notna()
            & period.notna()
            & available.notna()
            & available.le(as_of)
        )
        if not bool(visible.any()):
            continue
        pairs = pd.DataFrame(
            {
                "ts_code": frame.loc[visible, "ts_code"].astype(str).to_numpy(),
                "report_period": period.loc[visible].dt.strftime("%Y%m%d").to_numpy(),
            }
        ).drop_duplicates()
        for code, values in pairs.groupby("ts_code", sort=False)["report_period"]:
            periods_by_code.setdefault(str(code), set()).update(values.tolist())
    return {code: len(periods) for code, periods in periods_by_code.items()}


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
        scoped = frame.loc[frame["ts_code"].astype("string").eq(str(code))].copy()
        if scoped.empty:
            continue
        try:
            canonical = (
                scoped
                if {"report_period", "actual_available_date"}.issubset(scoped.columns)
                else canonicalize_financial_frame(dataset, scoped)
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


def build_historical_investable_universe(
    stock_basic: pd.DataFrame,
    *,
    as_of_date: str | date | pd.Timestamp,
    daily_basic: pd.DataFrame | None = None,
    financial_frames: dict[str, pd.DataFrame] | None = None,
    financial_period_counts: dict[str, int] | None = None,
    suspension_frame: pd.DataFrame | None = None,
    config: UniverseConfig | None = None,
) -> UniverseResult:
    """Build the replay-only universe without current reference-state fields."""

    settings = replace(
        config or UniverseConfig(),
        version=HISTORICAL_UNIVERSE_CONTRACT_VERSION,
        pit_safe_only=True,
    )
    return build_investable_universe(
        stock_basic,
        as_of_date=as_of_date,
        daily_basic=daily_basic,
        financial_frames=financial_frames,
        financial_period_counts=financial_period_counts,
        suspension_frame=suspension_frame,
        config=settings,
    )


def build_investable_universe(
    stock_basic: pd.DataFrame,
    *,
    as_of_date: str | date | pd.Timestamp,
    daily_basic: pd.DataFrame | None = None,
    financial_frames: dict[str, pd.DataFrame] | None = None,
    financial_period_counts: dict[str, int] | None = None,
    suspension_frame: pd.DataFrame | None = None,
    config: UniverseConfig | None = None,
) -> UniverseResult:
    """Apply explicit dated eligibility rules and return every exclusion reason."""

    settings = config or UniverseConfig()
    as_of_text, as_of = _as_of(as_of_date)
    source_evidence, limitations = (
        _historical_source_evidence()
        if settings.pit_safe_only
        else (
            {
                "dataset": "stock_basic",
                "semantics": "caller_supplied_reference_frame",
                "safe_fields": ["ts_code", "list_date", "delist_date"],
                "status_fields_consulted": bool(settings.exclude_st),
                "current_fields_are_pit_safe": False,
            },
            (),
        )
    )
    if stock_basic.empty or "ts_code" not in stock_basic.columns:
        return UniverseResult(
            as_of_text,
            settings.version,
            pd.DataFrame(),
            (),
            ("missing_stock_basic",),
            source_evidence,
            limitations,
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
        # In historical PIT mode these fields are intentionally not read at
        # all.  A current stock_basic row is only a carrier for the stable
        # identifier and dated listing/delisting event fields.
        name = "" if settings.pit_safe_only else str(_value(row, "name") or "")
        status = (
            "L"
            if settings.pit_safe_only
            else str(_value(row, "list_status", "status") or "L").upper()
        )
        delist_date = _value(row, "delist_date")
        parsed_delist = (
            pd.to_datetime(delist_date, errors="coerce")
            if delist_date is not None and pd.notna(delist_date)
            else pd.NaT
        )
        if (
            not settings.pit_safe_only
            and settings.exclude_st
            and name.upper().lstrip("*").startswith("ST")
        ):
            included, reason = False, "st_status"
        elif (
            settings.exclude_delisting
            and pd.notna(parsed_delist)
            and pd.Timestamp(parsed_delist).normalize() <= as_of
        ):
            included, reason = False, "delisted_by_as_of"
        elif (
            settings.pit_safe_only
            and settings.exclude_delisting
            and pd.isna(parsed_delist)
            and _market_history(code, daily_basic, as_of, settings.liquidity_lookback).empty
        ):
            # Without a dated delisting event or a visible market observation
            # there is no historical proof that the current reference row was
            # investable on this date.  Exclude rather than project status.
            included, reason = False, "historical_listing_status_unknown"
        elif (
            not settings.pit_safe_only
            and settings.exclude_delisting
            and status
            in {
                "D",
                "DELISTED",
            }
            and pd.isna(parsed_delist)
        ):
            included, reason = False, "delisting_status_missing_date"
        elif (
            not settings.pit_safe_only
            and settings.exclude_delisting
            and status
            not in {
                "L",
                "LISTED",
                "NORMAL",
                "D",
                "DELISTED",
            }
        ):
            included, reason = False, "delisting_or_non_listed_status"
        if (
            included
            and (_is_bse_from_identifier(code) if settings.pit_safe_only else _is_bse(code, row))
            and not settings.include_bse
        ):
            included, reason = (
                False,
                (
                    "bse_excluded_by_identifier_policy"
                    if settings.pit_safe_only
                    else "bse_excluded_by_policy"
                ),
            )
        list_date = _value(row, "list_date", "listing_date")
        parsed_list = pd.NaT
        if included and settings.pit_safe_only and (list_date is None or pd.isna(list_date)):
            included, reason = False, "listing_date_unknown"
        if included and list_date is not None and pd.notna(list_date):
            parsed_list = pd.to_datetime(list_date, errors="coerce")
            if pd.isna(parsed_list):
                if settings.pit_safe_only:
                    included, reason = False, "listing_date_unknown"
            else:
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
        if included and settings.exclude_suspended:
            if settings.pit_safe_only:
                if _suspended_on_date(code, suspension_frame, as_of):
                    included, reason = False, "suspended_on_as_of"
            elif "is_suspend" in row.index and str(row["is_suspend"]).lower() in {
                "1",
                "true",
                "yes",
                "suspended",
            }:
                included, reason = False, "long_term_suspension"
        periods = (
            int(financial_period_counts.get(code, 0))
            if financial_period_counts is not None
            else _financial_period_count(code, financial_frames, as_of)
        )
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
            if settings.pit_safe_only:
                included_row = {
                    field_name: row.get(field_name)
                    for field_name in ("ts_code", "list_date", "delist_date")
                    if field_name in row.index
                }
                included_row["universe_reason"] = reason
                included_row["listing_boundary_source"] = "stock_basic.list_date"
                included_row["delisting_boundary_source"] = (
                    "stock_basic.delist_date" if pd.notna(parsed_delist) else None
                )
                included_row["status_semantics"] = "UNSUPPORTED_PIT"
            else:
                included_row = row.to_dict()
                included_row["universe_reason"] = reason
            included_rows.append(included_row)
        decision_evidence = {
            "as_of_date": as_of_text,
            "source_dataset": "stock_basic",
            "fields_used": (
                ["ts_code", "list_date", "delist_date"]
                if settings.pit_safe_only
                else ["ts_code", "name", "list_status", "list_date", "delist_date"]
            ),
            "list_date": (
                pd.Timestamp(parsed_list).normalize().strftime("%Y%m%d")
                if pd.notna(parsed_list)
                else None
            ),
            "delist_date": (
                pd.Timestamp(parsed_delist).normalize().strftime("%Y%m%d")
                if pd.notna(parsed_delist)
                else None
            ),
            "status_semantics": "UNSUPPORTED_PIT" if settings.pit_safe_only else "caller_reference",
        }
        decisions.append(
            UniverseDecision(code, included, reason, tuple(warnings), decision_evidence)
        )
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
        source_evidence,
        limitations,
    )
