"""Reproducible market and fundamental outcomes for frozen scanner selections.

The module intentionally keeps selection and outcome data on separate paths.  A
scanner snapshot is an input; future prices and future reports are never used
to create, rank, or modify that snapshot.  ``run_ablation`` remains here as the
shared, downstream stability helper, but it is not used by the baseline
campaign.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from ..dates import normalize_date_series

BASELINE_EVALUATION_CONTRACT_VERSION = "baseline-evaluation-contract-v1"
EVALUATION_ENGINE_VERSION = "evaluation-v3"
BASELINE_BENCHMARK_CODE = "000300.SH"
BASELINE_HORIZONS = (20, 60, 120, 250)
BASELINE_TOP_N = 20
# Frozen before observing any baseline outcome.  This is a simple round-trip
# deduction, not a per-side amount and not a sensitivity sweep.
BASELINE_TRANSACTION_COST_BPS = 30.0
BASELINE_PRICE_ADJUSTMENT_CONVENTION = "adjusted_close_adj_factor_v1"
BASELINE_FUNDAMENTAL_REVISION_POLICY = "first_available_version_after_snapshot"
BASELINE_FUNDAMENTAL_METRICS = (
    "revenue_yoy",
    "profit_yoy",
    "margin",
    "cfo_cash_conversion",
)


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Versioned assumptions for one evaluation run.

    The defaults are conservative and suitable for the calibrated baseline.
    A small legacy compatibility path remains available when callers omit
    explicit calendar, benchmark-index, and adjustment frames; it is marked in
    provenance and is not baseline-ready.
    """

    version: str = EVALUATION_ENGINE_VERSION
    horizons: tuple[int, ...] = BASELINE_HORIZONS
    top_n: int = BASELINE_TOP_N
    benchmark_code: str | None = BASELINE_BENCHMARK_CODE
    holding_convention: str = "as_of_close_to_nth_future_market_close"
    benchmark_convention: str = "index_daily_raw_close_same_calendar"
    portfolio_convention: str = "independent_overlapping_equal_weight_cohorts"
    turnover_convention: str = "jaccard_top_n"
    hit_rate_convention: str = "positive_absolute_and_benchmark_excess_return"
    delisted_return: float = -1.0
    transaction_cost_bps: float = BASELINE_TRANSACTION_COST_BPS
    transaction_cost_convention: str = "round_trip_total_deduction"
    price_adjustment_convention: str = BASELINE_PRICE_ADJUSTMENT_CONVENTION
    require_adjustment_factor: bool = False
    market_calendar_convention: str = "trade_cal_open_sessions"
    market_cap_bucket_convention: str = "as_of_cross_section_tercile"
    industry_fallback_convention: str = "frozen_or_dated_only"
    fundamental_metrics: tuple[str, ...] = BASELINE_FUNDAMENTAL_METRICS
    fundamental_min_delta: float = 0.0
    fundamental_min_available_metrics: int = 2
    fundamental_revision_policy: str = BASELINE_FUNDAMENTAL_REVISION_POLICY
    fundamental_window_convention: str = "next_distinct_report_periods_after_snapshot"

    def declared(self) -> dict[str, Any]:
        """Return a stable, machine-readable declaration of all assumptions."""

        return asdict(self)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.declared(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def frozen_baseline_evaluation_config() -> EvaluationConfig:
    """Return the frozen baseline contract configuration.

    This constructor is the only configuration used by the campaign driver.
    It makes the contract explicit rather than relying on command-line defaults.
    """

    return EvaluationConfig(
        version=BASELINE_EVALUATION_CONTRACT_VERSION,
        horizons=BASELINE_HORIZONS,
        top_n=BASELINE_TOP_N,
        benchmark_code=BASELINE_BENCHMARK_CODE,
        benchmark_convention="index_daily_raw_close_same_calendar",
        transaction_cost_bps=BASELINE_TRANSACTION_COST_BPS,
        transaction_cost_convention="round_trip_total_deduction",
        price_adjustment_convention=BASELINE_PRICE_ADJUSTMENT_CONVENTION,
        require_adjustment_factor=True,
        market_calendar_convention="trade_cal_open_sessions",
        market_cap_bucket_convention="as_of_cross_section_tercile",
        industry_fallback_convention="frozen_or_dated_only",
        fundamental_metrics=BASELINE_FUNDAMENTAL_METRICS,
        fundamental_min_delta=0.0,
        fundamental_min_available_metrics=2,
        fundamental_revision_policy=BASELINE_FUNDAMENTAL_REVISION_POLICY,
        fundamental_window_convention="next_distinct_report_periods_after_snapshot",
    )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    config_version: str
    status: str
    summary: pd.DataFrame
    observations: pd.DataFrame
    warnings: tuple[str, ...] = ()
    configuration: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    market_outcomes: pd.DataFrame = field(default_factory=pd.DataFrame)
    fundamental_outcomes: pd.DataFrame = field(default_factory=pd.DataFrame)
    fundamental_summary: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True, slots=True)
class _ForwardObservation:
    value: float | None
    end_date: str | None
    drawdown: float | None
    status: str
    reason: str | None = None
    entry_date: str | None = None
    raw_entry_price: float | None = None
    raw_exit_price: float | None = None
    adjusted_entry_price: float | None = None
    adjusted_exit_price: float | None = None


_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue_yoy": ("revenue_yoy", "tr_yoy", "or_yoy"),
    "profit_yoy": ("profit_yoy", "net_profit_yoy", "netprofit_yoy"),
    "margin": ("margin", "net_margin", "netprofit_margin"),
    "cfo_cash_conversion": (
        "cfo_cash_conversion",
        "cfo_to_profit",
        "q_ocf_to_sales",
        "ocf_to_sales",
    ),
}


def _as_of(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid evaluation date: {value!r}")
    return pd.Timestamp(parsed).normalize()


def _date_text(value: Any) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).normalize().strftime("%Y%m%d")


def _finite_number(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    number = float(parsed)
    return number if math.isfinite(number) else None


def _bool_value(value: Any) -> bool | None:
    if value is None or value is pd.NA or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "t"}:
        return True
    if text in {"0", "false", "no", "n", "f"}:
        return False
    return None


def _price_history(
    daily: pd.DataFrame,
    code: str,
    *,
    adj_factor: pd.DataFrame | None = None,
    require_adjustment: bool = False,
) -> pd.DataFrame:
    """Build one exact-date price history without carrying prices over gaps."""

    if daily.empty or not {"ts_code", "trade_date", "close"}.issubset(daily.columns):
        return pd.DataFrame()
    frame = daily.loc[daily["ts_code"].astype("string").eq(code)].copy()
    frame["_date"] = normalize_date_series(frame["trade_date"])
    frame["_raw_close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[
        frame["_date"].notna() & frame["_raw_close"].notna() & frame["_raw_close"].gt(0)
    ].copy()
    if frame.empty:
        return frame
    frame = (
        frame.sort_values(["_date"], kind="stable")
        .drop_duplicates("_date", keep="last")
        .reset_index(drop=True)
    )
    if adj_factor is None:
        if require_adjustment:
            frame["_adj_factor"] = pd.NA
            frame["_adjustment_source"] = "missing_adj_factor_input"
        else:
            frame["_adj_factor"] = 1.0
            frame["_adjustment_source"] = "raw_close_legacy_compatibility"
    elif adj_factor.empty or not {"ts_code", "trade_date", "adj_factor"}.issubset(
        adj_factor.columns
    ):
        frame["_adj_factor"] = pd.NA
        frame["_adjustment_source"] = "missing_adj_factor_input"
    else:
        factor = adj_factor.loc[adj_factor["ts_code"].astype("string").eq(code)].copy()
        factor["_date"] = normalize_date_series(factor["trade_date"])
        factor["_adj_factor"] = pd.to_numeric(factor["adj_factor"], errors="coerce")
        factor = factor.loc[
            factor["_date"].notna() & factor["_adj_factor"].notna() & factor["_adj_factor"].gt(0)
        ].copy()
        # Exact duplicate rows are a storage concern, not a reason to choose a
        # different endpoint.  Keep the deterministic last row; conflicting
        # factors are marked below so a baseline return cannot use them.
        conflicting = (
            factor.groupby("_date")["_adj_factor"].nunique(dropna=True).gt(1)
            if not factor.empty
            else pd.Series(dtype=bool)
        )
        factor = factor.sort_values("_date", kind="stable").drop_duplicates("_date", keep="last")
        factor = factor[["_date", "_adj_factor"]]
        frame = frame.merge(factor, on="_date", how="left", sort=False)
        frame["_adjustment_source"] = "adj_factor"
        if not conflicting.empty:
            frame.loc[frame["_date"].isin(conflicting[conflicting].index), "_adj_factor"] = pd.NA
            frame.loc[frame["_date"].isin(conflicting[conflicting].index), "_adjustment_source"] = (
                "ambiguous_adj_factor"
            )
    frame["_close"] = frame["_raw_close"] * pd.to_numeric(frame["_adj_factor"], errors="coerce")
    frame.loc[~frame["_close"].gt(0), "_close"] = pd.NA
    return frame.reset_index(drop=True)


def _build_price_histories(
    daily: pd.DataFrame,
    codes: set[str],
    *,
    adj_factor: pd.DataFrame | None,
    require_adjustment: bool,
) -> dict[str, pd.DataFrame]:
    if daily.empty or "ts_code" not in daily.columns:
        return {}
    histories: dict[str, pd.DataFrame] = {}
    selected = daily.loc[daily["ts_code"].astype("string").isin(codes)].copy()
    factor = adj_factor
    if factor is not None and not factor.empty and "ts_code" in factor.columns:
        factor = factor.loc[factor["ts_code"].astype("string").isin(codes)].copy()
    for code, group in selected.groupby(selected["ts_code"].astype("string"), sort=False):
        histories[str(code)] = _price_history(
            group,
            str(code),
            adj_factor=factor,
            require_adjustment=require_adjustment,
        )
    return histories


def _price_drawdown(prices: list[float]) -> float | None:
    if not prices or prices[0] <= 0:
        return None
    peak = prices[0]
    drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            drawdown = min(drawdown, price / peak - 1.0)
    return drawdown


def _forward_observation(
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    target_date: pd.Timestamp | None,
    *,
    require_adjustment: bool = False,
    suspended_dates: set[pd.Timestamp] | None = None,
    missing_history_status: str = "missing_price_history",
    missing_entry_status: str = "missing_entry_price",
    missing_endpoint_status: str = "missing_horizon_price",
) -> _ForwardObservation:
    if target_date is None:
        return _ForwardObservation(
            None, None, None, "incomplete_market_window", "missing_market_window"
        )
    if history.empty:
        return _ForwardObservation(None, _date_text(target_date), None, missing_history_status)
    start = history.loc[history["_date"].eq(as_of)]
    if start.empty:
        return _ForwardObservation(None, _date_text(target_date), None, missing_entry_status)
    start_row = start.iloc[-1]
    raw_start = _finite_number(start_row.get("_raw_close"))
    start_price = _finite_number(start_row.get("_close"))
    if raw_start is None or raw_start <= 0:
        return _ForwardObservation(None, _date_text(target_date), None, "invalid_entry_price")
    if require_adjustment and start_price is None:
        return _ForwardObservation(
            None,
            _date_text(target_date),
            None,
            "missing_adjustment_factor_entry",
            str(start_row.get("_adjustment_source") or "missing_adj_factor"),
            _date_text(as_of),
            raw_start,
        )
    if start_price is None or start_price <= 0:
        return _ForwardObservation(None, _date_text(target_date), None, "invalid_entry_price")
    end = history.loc[history["_date"].eq(target_date)]
    if end.empty:
        status = (
            "suspended_at_exit"
            if suspended_dates and target_date in suspended_dates
            else missing_endpoint_status
        )
        reason = "suspended_at_exit" if status == "suspended_at_exit" else None
        return _ForwardObservation(
            None,
            _date_text(target_date),
            None,
            status,
            reason,
            _date_text(as_of),
            raw_start,
            None,
            start_price,
            None,
        )
    end_row = end.iloc[-1]
    raw_end = _finite_number(end_row.get("_raw_close"))
    end_price = _finite_number(end_row.get("_close"))
    if raw_end is None or raw_end <= 0:
        return _ForwardObservation(None, _date_text(target_date), None, "invalid_exit_price")
    if require_adjustment and end_price is None:
        return _ForwardObservation(
            None,
            _date_text(target_date),
            None,
            "missing_adjustment_factor_exit",
            str(end_row.get("_adjustment_source") or "missing_adj_factor"),
            _date_text(as_of),
            raw_start,
            raw_end,
            start_price,
        )
    if end_price is None or end_price <= 0:
        return _ForwardObservation(None, _date_text(target_date), None, "invalid_exit_price")
    path = history.loc[history["_date"].between(as_of, target_date, inclusive="both"), "_close"]
    prices = pd.to_numeric(path, errors="coerce").dropna().astype(float).tolist()
    if not require_adjustment and str(start_row.get("_adjustment_source")) == (
        "raw_close_legacy_compatibility"
    ):
        forward_value = (end_price - start_price) / abs(start_price)
    else:
        forward_value = end_price / start_price - 1.0
    return _ForwardObservation(
        forward_value,
        _date_text(target_date),
        _price_drawdown(prices),
        "observed",
        None,
        _date_text(as_of),
        raw_start,
        raw_end,
        start_price,
        end_price,
    )


def _forward_return(
    daily: pd.DataFrame,
    code: str,
    as_of: pd.Timestamp,
    horizon: int,
) -> tuple[float | None, str | None]:
    """Backward-compatible raw-close helper for small callers/tests."""

    dates = pd.DatetimeIndex(
        normalize_date_series(daily.get("trade_date", pd.Series(dtype=object)))
        .dropna()
        .drop_duplicates()
        .sort_values()
    )
    target_date = _market_target_date(dates, as_of, horizon)
    observation = _forward_observation(
        _price_history(daily, code), as_of, target_date, require_adjustment=False
    )
    return observation.value, observation.end_date


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    wealth = 1.0
    peak = 1.0
    drawdowns: list[float] = []
    for value in values:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        drawdowns.append(wealth / peak - 1.0)
    return min(drawdowns)


def _validate_config(settings: EvaluationConfig) -> None:
    if settings.top_n <= 0:
        raise ValueError("top_n must be positive")
    if not settings.horizons or any(horizon <= 0 for horizon in settings.horizons):
        raise ValueError("horizons must contain positive trading-day counts")
    if len(set(settings.horizons)) != len(settings.horizons):
        raise ValueError("horizons must be unique")
    if settings.holding_convention != "as_of_close_to_nth_future_market_close":
        raise ValueError(f"unsupported holding_convention: {settings.holding_convention}")
    if settings.benchmark_convention not in {
        "same_as_of_and_horizon",
        "index_daily_raw_close_same_calendar",
    }:
        raise ValueError(f"unsupported benchmark_convention: {settings.benchmark_convention}")
    if settings.portfolio_convention != "independent_overlapping_equal_weight_cohorts":
        raise ValueError(f"unsupported portfolio_convention: {settings.portfolio_convention}")
    if settings.turnover_convention not in {"jaccard_top_n", "one_way_top_n"}:
        raise ValueError(f"unsupported turnover_convention: {settings.turnover_convention}")
    if settings.hit_rate_convention not in {
        "positive_forward_return",
        "positive_absolute_and_benchmark_excess_return",
    }:
        raise ValueError(f"unsupported hit_rate_convention: {settings.hit_rate_convention}")
    if not -1.0 <= settings.delisted_return <= 0.0:
        raise ValueError("delisted_return must be between -1.0 and 0.0")
    if settings.transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps cannot be negative")
    if settings.transaction_cost_convention != "round_trip_total_deduction":
        raise ValueError(
            f"unsupported transaction_cost_convention: {settings.transaction_cost_convention}"
        )
    if settings.price_adjustment_convention not in {
        "adjusted_close_adj_factor_v1",
        "raw_close_legacy",
    }:
        raise ValueError(
            f"unsupported price_adjustment_convention: {settings.price_adjustment_convention}"
        )
    if settings.require_adjustment_factor and settings.price_adjustment_convention != (
        "adjusted_close_adj_factor_v1"
    ):
        raise ValueError("require_adjustment_factor requires adjusted_close_adj_factor_v1")
    if not settings.fundamental_metrics:
        raise ValueError("fundamental_metrics cannot be empty")
    if settings.fundamental_min_available_metrics <= 0:
        raise ValueError("fundamental_min_available_metrics must be positive")
    if settings.fundamental_revision_policy != BASELINE_FUNDAMENTAL_REVISION_POLICY:
        raise ValueError(
            f"unsupported fundamental_revision_policy: {settings.fundamental_revision_policy}"
        )


def _selected_scans(scans: pd.DataFrame, top_n: int) -> pd.DataFrame:
    selected = scans.copy()
    if "rejected" in selected.columns:
        rejected = selected["rejected"].map(_bool_value).fillna(False)
        selected = selected.loc[~rejected].copy()
    if "ranking_eligible" in selected.columns:
        eligible = selected["ranking_eligible"].map(_bool_value)
        selected = selected.loc[eligible.eq(True)].copy()
    required = {"as_of_date", "ts_code"}
    if not required.issubset(selected.columns):
        return pd.DataFrame()
    selected["_as_of"] = selected["as_of_date"].map(_as_of)
    selected["_code"] = selected["ts_code"].astype("string").str.strip()
    if selected["_code"].isna().any() or selected["_code"].eq("").any():
        raise ValueError("scan rows require non-empty ts_code")
    selected["_input_order"] = range(len(selected))
    sort_columns = ["_as_of"]
    ascending = [True]
    if "rank" in selected.columns:
        selected["_selection_order"] = pd.to_numeric(selected["rank"], errors="coerce")
        sort_columns.extend(["_selection_order", "_code", "_input_order"])
        ascending.extend([True, True, True])
    elif "turnaround_score" in selected.columns:
        selected["_selection_order"] = pd.to_numeric(selected["turnaround_score"], errors="coerce")
        sort_columns.extend(["_selection_order", "_code", "_input_order"])
        ascending.extend([False, True, True])
    else:
        sort_columns.append("_input_order")
        ascending.append(True)
    selected = selected.sort_values(
        sort_columns, ascending=ascending, na_position="last", kind="stable"
    )
    selected = selected.drop_duplicates(["_as_of", "_code"], keep="first")
    selected = selected.groupby("_as_of", sort=True, group_keys=False).head(top_n)
    return selected.reset_index(drop=True)


def _reference_row(
    reference_data: pd.DataFrame | None, code: str, as_of: pd.Timestamp
) -> pd.Series | None:
    if reference_data is None or reference_data.empty or "ts_code" not in reference_data.columns:
        return None
    matched = reference_data.loc[reference_data["ts_code"].astype("string").eq(code)].copy()
    if matched.empty:
        return None
    for field_name in ("as_of_date", "trade_date", "effective_date"):
        if field_name not in matched.columns:
            continue
        matched["_reference_date"] = normalize_date_series(matched[field_name])
        dated = matched.loc[matched["_reference_date"].notna()]
        available = dated.loc[dated["_reference_date"].le(as_of)]
        if not available.empty:
            return available.sort_values("_reference_date", kind="stable").iloc[-1]
        return None
    return matched.iloc[-1]


def _normalized_optional_date(value: Any) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).normalize()


def _historical_universe_status(
    selection: pd.Series, reference: pd.Series | None, as_of: pd.Timestamp
) -> str:
    for field_name in ("historical_universe_member", "universe_member"):
        if field_name in selection.index and pd.notna(selection[field_name]):
            value = _bool_value(selection[field_name])
            return "member_from_snapshot" if value is True else "not_member"
    if reference is None:
        return "unknown_missing_history"
    list_date = _normalized_optional_date(reference.get("list_date", reference.get("listing_date")))
    if list_date is not None and list_date > as_of:
        return "not_listed_by_as_of"
    delist_date = _normalized_optional_date(reference.get("delist_date"))
    if delist_date is not None and delist_date <= as_of:
        return "delisted_by_as_of"
    status = str(reference.get("list_status", reference.get("status", ""))).upper()
    if status in {"D", "DELISTED"} and delist_date is None:
        return "unknown_missing_delist_date"
    return "member_from_history"


def _frame_digest(frame: pd.DataFrame | None) -> str | None:
    if frame is None:
        return None
    ordered = frame.reindex(sorted(frame.columns), axis=1).reset_index(drop=True)
    try:
        hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy(copy=True)
        hashes.sort()
        payload = hashes.tobytes()
    except (TypeError, ValueError):
        records = [
            json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
            for record in ordered.to_dict(orient="records")
        ]
        payload = "\n".join(sorted(records)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_values(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame.columns:
        return []
    return sorted(frame[column].dropna().astype(str).unique())


def _market_target_date(
    market_dates: pd.DatetimeIndex, as_of: pd.Timestamp, horizon: int
) -> pd.Timestamp | None:
    after = market_dates[market_dates > as_of]
    return pd.Timestamp(after[horizon - 1]).normalize() if len(after) >= horizon else None


def _calendar_sessions(
    trade_calendar: pd.DataFrame | None,
    daily: pd.DataFrame,
    benchmark_daily: pd.DataFrame,
) -> tuple[pd.DatetimeIndex, str]:
    if trade_calendar is not None and not trade_calendar.empty:
        if not {"cal_date", "is_open"}.issubset(trade_calendar.columns):
            raise ValueError("trade calendar requires cal_date and is_open")
        calendar = trade_calendar.copy()
        if "exchange" in calendar.columns:
            exchange = calendar["exchange"].astype("string").str.upper()
            if exchange.eq("SSE").any():
                calendar = calendar.loc[exchange.eq("SSE")]
        dates = normalize_date_series(calendar["cal_date"])
        opened = calendar.loc[
            dates.notna() & pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)
        ]
        return pd.DatetimeIndex(
            dates.loc[opened.index].drop_duplicates().sort_values()
        ), "trade_cal"
    date_frames = []
    for frame in (daily, benchmark_daily):
        if frame is not None and not frame.empty and "trade_date" in frame.columns:
            date_frames.append(normalize_date_series(frame["trade_date"]))
    dates = (
        pd.concat(date_frames, ignore_index=True).dropna().drop_duplicates().sort_values()
        if date_frames
        else pd.Series(dtype="datetime64[ns]")
    )
    return pd.DatetimeIndex(dates), "union_fallback"


def _delist_adjusted_observation(
    observation: _ForwardObservation,
    reference: pd.Series | None,
    as_of: pd.Timestamp,
    target_date: pd.Timestamp | None,
    delisted_return: float,
) -> _ForwardObservation:
    if observation.value is not None or target_date is None or reference is None:
        return observation
    delist_date = _normalized_optional_date(reference.get("delist_date"))
    if delist_date is None or not as_of < delist_date <= target_date:
        return observation
    return _ForwardObservation(
        delisted_return,
        delist_date.strftime("%Y%m%d"),
        min(0.0, delisted_return),
        "delisted_assumption",
        "dated_delisting_inside_holding_window",
        _date_text(as_of),
    )


def _first_known(row: pd.Series | None, names: tuple[str, ...]) -> Any:
    if row is None:
        return None
    for name in names:
        if name in row.index and pd.notna(row[name]):
            return row[name]
    return None


def _exposure_values(
    selection: pd.Series,
    universe_reference: pd.Series | None,
    exposure_reference: pd.Series | None,
    *,
    allow_stock_basic_industry: bool,
) -> tuple[str | None, float | None, str, str]:
    industry = _first_known(selection, ("industry",))
    market_cap = _first_known(selection, ("total_mv", "market_cap"))
    industry_source = "frozen_scan"
    market_cap_source = "frozen_scan"
    if industry is None:
        industry = _first_known(exposure_reference, ("industry",))
        industry_source = "dated_exposure"
    if industry is None and allow_stock_basic_industry:
        industry = _first_known(universe_reference, ("industry",))
        industry_source = "stock_basic_fallback"
    if market_cap is None:
        market_cap = _first_known(exposure_reference, ("total_mv", "market_cap"))
        market_cap_source = "dated_exposure"
    numeric_market_cap = _finite_number(market_cap)
    if industry is None:
        industry_source = "missing"
    if numeric_market_cap is None:
        market_cap_source = "missing"
    return (
        str(industry) if industry is not None else None,
        numeric_market_cap,
        industry_source,
        market_cap_source,
    )


def _market_cap_buckets(
    exposures: pd.DataFrame | None,
    dates: list[pd.Timestamp],
) -> dict[tuple[str, pd.Timestamp], str]:
    """Assign deterministic as-of cross-sectional terciles without outcomes."""

    result: dict[tuple[str, pd.Timestamp], str] = {}
    if (
        exposures is None
        or exposures.empty
        or not {"ts_code", "trade_date"}.issubset(exposures.columns)
    ):
        return result
    cap_column = "total_mv" if "total_mv" in exposures.columns else "market_cap"
    if cap_column not in exposures.columns:
        return result
    requested_dates = set(dates)
    normalized_dates = normalize_date_series(exposures["trade_date"])
    frame = exposures.loc[normalized_dates.isin(requested_dates), ["ts_code", cap_column]].copy()
    frame["_date"] = normalized_dates.loc[frame.index].to_numpy()
    frame["_cap"] = pd.to_numeric(frame[cap_column], errors="coerce")
    frame = frame.loc[frame["_date"].notna() & frame["_cap"].notna() & frame["_cap"].gt(0)]
    for date_value in sorted(set(dates)):
        current = frame.loc[frame["_date"].eq(date_value)].copy()
        if current.empty:
            continue
        current = current.sort_values(["_cap", "ts_code"], kind="stable").drop_duplicates(
            "ts_code", keep="last"
        )
        count = len(current)
        for position, (_, row) in enumerate(current.iterrows()):
            fraction = (position + 0.5) / count
            bucket = "small" if fraction <= 1 / 3 else "mid" if fraction <= 2 / 3 else "large"
            result[(str(row["ts_code"]), date_value)] = bucket
    return result


def _exact_reference_row(
    reference_data: pd.DataFrame | None, code: str, as_of: pd.Timestamp
) -> pd.Series | None:
    """Return only a reference observation recorded on the exact as-of date."""

    if reference_data is None or reference_data.empty or "ts_code" not in reference_data.columns:
        return None
    matched = reference_data.loc[reference_data["ts_code"].astype("string").eq(code)].copy()
    if matched.empty:
        return None
    for field_name in ("as_of_date", "trade_date", "effective_date"):
        if field_name not in matched.columns:
            continue
        dates = normalize_date_series(matched[field_name])
        exact = matched.loc[dates.eq(as_of)]
        return exact.sort_index(kind="stable").iloc[-1] if not exact.empty else None
    return matched.iloc[-1]


def _metric_value(row: pd.Series | None, metric: str) -> float | None:
    if row is None:
        return None
    for name in _METRIC_ALIASES.get(metric, (metric,)):
        if name in row.index:
            value = _finite_number(row[name])
            if value is not None:
                return value
    return None


def _metric_status(
    row: pd.Series | None, metric: str, value: float | None
) -> tuple[str, str | None]:
    if row is None:
        return "missing_metric", "missing_report"
    status_names = (f"{metric}_status", f"{metric}_observation_status")
    reason_names = (f"{metric}_reason", f"{metric}_observation_reason")
    explicit_status = next(
        (
            str(row[name]).strip().lower()
            for name in status_names
            if name in row.index and pd.notna(row[name])
        ),
        None,
    )
    explicit_reason = next(
        (
            str(row[name]).strip()
            for name in reason_names
            if name in row.index and pd.notna(row[name])
        ),
        None,
    )
    if explicit_status and explicit_status not in {"known", "valid", "observed"}:
        return explicit_status, explicit_reason or explicit_status
    if value is None:
        return "missing_metric", explicit_reason or "missing_metric"
    denominator = next(
        (
            _finite_number(row[name])
            for name in (f"{metric}_denominator", "denominator")
            if name in row.index
        ),
        None,
    )
    if denominator is not None and denominator == 0:
        return "invalid_denominator", "invalid_denominator"
    if denominator is not None and denominator < 0:
        return "negative_denominator", "negative_denominator"
    return "observed", explicit_reason


def _sign_transition(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return "UNKNOWN"
    if left < 0 < right:
        return "NEGATIVE_TO_POSITIVE"
    if left > 0 > right:
        return "POSITIVE_TO_NEGATIVE"
    if left == 0 and right > 0:
        return "ZERO_TO_POSITIVE"
    if left == 0 and right < 0:
        return "ZERO_TO_NEGATIVE"
    if right == 0 and left != 0:
        return "TO_ZERO"
    return "NONE"


def _report_version(row: pd.Series) -> str:
    for name in ("disclosure_version", "source_version", "source_version_identity", "update_flag"):
        if name in row.index and pd.notna(row[name]) and str(row[name]).strip():
            return str(row[name]).strip()
    values = {
        str(key): str(value)
        for key, value in row.items()
        if key not in {"retrieved_at", "source", "source_api"} and pd.notna(value)
    }
    return (
        "row-"
        + hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:12]
    )


def _prepare_fundamentals(fundamentals: pd.DataFrame | None) -> pd.DataFrame:
    if fundamentals is None or fundamentals.empty or "ts_code" not in fundamentals.columns:
        return pd.DataFrame()
    prepared = fundamentals.copy()
    prepared["_code"] = prepared["ts_code"].astype("string").str.strip()
    prepared["_available_date"] = pd.NaT
    for field_name in ("actual_available_date", "available_date", "f_ann_date", "ann_date"):
        if field_name not in prepared.columns:
            continue
        candidate = normalize_date_series(prepared[field_name])
        prepared["_available_date"] = prepared["_available_date"].fillna(candidate)
    prepared["_report_period"] = pd.NaT
    for field_name in ("report_period", "end_date"):
        if field_name in prepared.columns:
            prepared["_report_period"] = prepared["_report_period"].fillna(
                normalize_date_series(prepared[field_name])
            )
    prepared = prepared.loc[
        prepared["_code"].notna()
        & prepared["_available_date"].notna()
        & prepared["_report_period"].notna()
    ].copy()
    if prepared.empty:
        return prepared
    prepared["_disclosure_version"] = prepared.apply(_report_version, axis=1)
    prepared["_input_order"] = range(len(prepared))
    return prepared


def build_fundamental_history(indicators: pd.DataFrame | None) -> pd.DataFrame:
    """Build the evaluation-only fundamental history from ``fina_indicator``.

    Tushare indicator percentage fields are converted from percentage points to
    ratios once here.  ``q_ocf_to_sales`` is the declared CFO/cash-conversion
    proxy when no direct CFO-to-profit field is supplied.  This frame is never
    passed to scanner feature or score code.
    """

    if indicators is None or indicators.empty or "ts_code" not in indicators.columns:
        return pd.DataFrame()
    required_columns = {
        "ts_code",
        "end_date",
        "ann_date",
        "update_flag",
        "tr_yoy",
        "or_yoy",
        "netprofit_yoy",
        "dt_netprofit_yoy",
        "op_yoy",
        "netprofit_margin",
        "q_ocf_to_sales",
        "ocf_to_sales",
    }
    columns = [column for column in indicators.columns if column in required_columns]
    frame = indicators.loc[:, columns].copy()
    output = pd.DataFrame(index=frame.index)
    output["ts_code"] = frame["ts_code"]
    output["report_period"] = frame.get("end_date", pd.Series(pd.NaT, index=frame.index))
    output["actual_available_date"] = frame.get("ann_date", pd.Series(pd.NaT, index=frame.index))
    if "update_flag" in frame.columns:
        output["disclosure_version"] = frame["update_flag"]
    source_map = {
        "revenue_yoy": ("tr_yoy", "or_yoy"),
        "profit_yoy": ("netprofit_yoy", "dt_netprofit_yoy"),
        "operating_profit_yoy": ("op_yoy",),
        "margin": ("netprofit_margin",),
        "cfo_cash_conversion": ("q_ocf_to_sales", "ocf_to_sales"),
    }
    for target, sources in source_map.items():
        source = next((name for name in sources if name in frame.columns), None)
        if source is None:
            output[target] = pd.NA
        else:
            # These named Tushare fields are percentage-point fields.  Keep a
            # separate source marker for audit and avoid re-scaling canonical
            # input fields in the general evaluator.
            output[target] = pd.to_numeric(frame[source], errors="coerce") / 100.0
            output[f"{target}_source_field"] = source
    return output.reset_index(drop=True)


def _select_future_report_rows(
    history: pd.DataFrame,
    code: str,
    as_of: pd.Timestamp,
    baseline_period: pd.Timestamp | None,
) -> list[dict[str, Any]]:
    company = history.loc[history["_code"].eq(code)].copy()
    if baseline_period is not None:
        company = company.loc[company["_report_period"].gt(baseline_period)]
    if company.empty:
        return []
    candidates: list[dict[str, Any]] = []
    for report_period, group in company.groupby("_report_period", sort=True):
        ordered = group.sort_values(
            ["_available_date", "_disclosure_version", "_input_order"], kind="stable"
        )
        first_available = ordered["_available_date"].iloc[0]
        # A revision published after T does not turn a report already visible
        # at T into a future report.  This is the critical report-period rule.
        if pd.Timestamp(first_available) <= as_of:
            continue
        selected = ordered.iloc[0]
        candidates.append(
            {
                "row": selected,
                "report_period": pd.Timestamp(report_period).normalize(),
                "revision_count": max(0, len(ordered) - 1),
                "all_versions": ordered["_disclosure_version"].astype(str).tolist(),
            }
        )
    return candidates


def _aggregate_fundamental_report(
    baseline_values: dict[str, float],
    row: pd.Series | None,
    settings: EvaluationConfig,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    improved_count = 0
    failed_count = 0
    available_count = 0
    for metric in settings.fundamental_metrics:
        future_value = _metric_value(row, metric)
        future_status, future_reason = _metric_status(row, metric, future_value)
        baseline_value = baseline_values.get(metric)
        baseline_status = "observed" if baseline_value is not None else "missing_metric"
        delta = (
            future_value - baseline_value
            if future_status in {"observed", "known", "valid"}
            and baseline_status == "observed"
            and future_value is not None
            and baseline_value is not None
            else None
        )
        metric_improved = delta > settings.fundamental_min_delta if delta is not None else None
        if delta is not None:
            available_count += 1
            if metric_improved:
                improved_count += 1
            else:
                failed_count += 1
        details[metric] = {
            "value": future_value,
            "status": future_status,
            "reason": future_reason,
            "baseline_value": baseline_value,
            "baseline_status": baseline_status,
            "delta": delta,
            "improved": metric_improved,
            "sign_transition": _sign_transition(baseline_value, future_value),
        }
    if available_count < settings.fundamental_min_available_metrics:
        follow = None
        status = "insufficient_metric_coverage"
        reason = "missing_metric"
    else:
        follow = improved_count > failed_count
        status = "observed"
        reason = None
    return {
        "details": details,
        "available_count": available_count,
        "improved_count": improved_count,
        "failed_count": failed_count,
        "fundamental_improved": follow,
        "status": status,
        "reason": reason,
    }


def _fundamental_evaluation(
    prepared: pd.DataFrame,
    selection: pd.Series,
    code: str,
    as_of: pd.Timestamp,
    settings: EvaluationConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    empty = {
        "fundamental_improved": None,
        "fundamental_status": "missing_input",
        "fundamental_metric_count": 0,
        "fundamental_baseline_date": None,
        "fundamental_baseline_period": None,
        "fundamental_observation_date": None,
        "fundamental_deltas": {},
        "fundamental_next_two_improved": None,
        "fundamental_persistence": None,
        "false_turnaround": None,
        "fundamental_next_report_period": None,
        "fundamental_next_report_disclosure_version": None,
        "fundamental_next_two_report_periods": [],
        "fundamental_revision_policy": settings.fundamental_revision_policy,
    }
    if prepared.empty:
        rows = [
            {
                "ts_code": code,
                "as_of_date": _date_text(as_of),
                "window": window,
                "status": "missing_input",
                "reason": "fundamental_history_not_provided",
                "observation_used": False,
                "report_period": None,
                "disclosure_version": None,
                "actual_available_date": None,
            }
            for window in ("next_report", "next_two_reports")
        ]
        return empty, rows
    company = prepared.loc[prepared["_code"].eq(code)].copy()
    if company.empty:
        values = {**empty, "fundamental_status": "missing_company_history"}
        rows = [
            {
                "ts_code": code,
                "as_of_date": _date_text(as_of),
                "window": window,
                "status": "missing_company_history",
                "reason": "missing_company_history",
                "observation_used": False,
                "report_period": None,
                "disclosure_version": None,
                "actual_available_date": None,
            }
            for window in ("next_report", "next_two_reports")
        ]
        return values, rows

    baseline_values = {
        metric: _metric_value(selection, metric) for metric in settings.fundamental_metrics
    }
    baseline_values = {key: value for key, value in baseline_values.items() if value is not None}
    baseline_source = "frozen_scan" if baseline_values else "pit_history"
    visible = company.loc[company["_available_date"].le(as_of)].sort_values(
        ["_report_period", "_available_date", "_disclosure_version"], kind="stable"
    )
    baseline_row = visible.iloc[-1] if not visible.empty else None
    baseline_period = _normalized_optional_date(
        selection.get("fundamental_report_period", selection.get("report_period"))
    )
    if baseline_period is None and baseline_row is not None:
        baseline_period = pd.Timestamp(baseline_row["_report_period"]).normalize()
    if not baseline_values and baseline_row is not None:
        baseline_values = {
            metric: _metric_value(baseline_row, metric) for metric in settings.fundamental_metrics
        }
        baseline_values = {
            key: value for key, value in baseline_values.items() if value is not None
        }
    baseline_date = (
        as_of
        if baseline_source == "frozen_scan" and baseline_values
        else pd.Timestamp(baseline_row["_available_date"]).normalize()
        if baseline_row is not None
        else None
    )
    if not baseline_values:
        values = {
            **empty,
            "fundamental_status": "missing_pit_baseline",
            "fundamental_baseline_date": _date_text(baseline_date),
            "fundamental_baseline_period": _date_text(baseline_period),
        }
        rows = [
            {
                "ts_code": code,
                "as_of_date": _date_text(as_of),
                "window": window,
                "status": "missing_pit_baseline",
                "reason": "missing_pit_baseline",
                "observation_used": False,
                "report_period": None,
                "disclosure_version": None,
                "actual_available_date": None,
                "baseline_period": _date_text(baseline_period),
            }
            for window in ("next_report", "next_two_reports")
        ]
        return values, rows

    future = _select_future_report_rows(prepared, code, as_of, baseline_period)
    rows: list[dict[str, Any]] = []
    if not future:
        values = {
            **empty,
            "fundamental_status": "missing_report",
            "fundamental_baseline_date": _date_text(baseline_date),
            "fundamental_baseline_period": _date_text(baseline_period),
        }
        rows = [
            {
                "ts_code": code,
                "as_of_date": _date_text(as_of),
                "window": window,
                "status": "missing_report",
                "reason": "missing_next_report_period",
                "observation_used": False,
                "report_period": None,
                "disclosure_version": None,
                "actual_available_date": None,
                "baseline_period": _date_text(baseline_period),
                "baseline_source": baseline_source,
            }
            for window in ("next_report", "next_two_reports")
        ]
        return values, rows

    first = future[0]
    first_row = first["row"]
    first_aggregate = _aggregate_fundamental_report(baseline_values, first_row, settings)
    first_details = first_aggregate["details"]
    first_outcome = {
        "ts_code": code,
        "as_of_date": _date_text(as_of),
        "window": "next_report",
        "status": first_aggregate["status"],
        "reason": first_aggregate["reason"],
        "fundamental_improved": first_aggregate["fundamental_improved"],
        "available_metric_count": first_aggregate["available_count"],
        "improved_metric_count": first_aggregate["improved_count"],
        "failed_metric_count": first_aggregate["failed_count"],
        "report_period": _date_text(first["report_period"]),
        "disclosure_version": str(first_row["_disclosure_version"]),
        "actual_available_date": _date_text(first_row["_available_date"]),
        "observation_used": True,
        "revision_count_not_used": first["revision_count"],
        "all_disclosure_versions": first["all_versions"],
        "baseline_period": _date_text(baseline_period),
        "baseline_date": _date_text(baseline_date),
        "baseline_source": baseline_source,
        "revision_policy": settings.fundamental_revision_policy,
    }
    for metric, detail in first_details.items():
        for suffix, key in (
            ("value", f"{metric}_value"),
            ("baseline_value", f"{metric}_baseline_value"),
            ("delta", f"{metric}_delta"),
            ("improved", f"{metric}_improved"),
            ("status", f"{metric}_status"),
            ("reason", f"{metric}_reason"),
            ("sign_transition", f"{metric}_sign_transition"),
        ):
            first_outcome[key] = detail[suffix]
    rows.append(first_outcome)

    next_two = future[:2]
    if len(next_two) < 2:
        second_outcome = {
            "ts_code": code,
            "as_of_date": _date_text(as_of),
            "window": "next_two_reports",
            "status": "missing_second_report",
            "reason": "missing_second_report_period",
            "fundamental_persistence": None,
            "observation_used": False,
            "report_periods": [_date_text(first["report_period"])],
            "disclosure_versions": [str(first_row["_disclosure_version"])],
            "actual_available_dates": [_date_text(first_row["_available_date"])],
            "baseline_period": _date_text(baseline_period),
            "baseline_source": baseline_source,
            "revision_policy": settings.fundamental_revision_policy,
        }
        rows.append(second_outcome)
        values = {
            **empty,
            "fundamental_improved": first_aggregate["fundamental_improved"],
            "fundamental_status": first_aggregate["status"],
            "fundamental_metric_count": first_aggregate["available_count"],
            "fundamental_baseline_date": _date_text(baseline_date),
            "fundamental_baseline_period": _date_text(baseline_period),
            "fundamental_observation_date": _date_text(first_row["_available_date"]),
            "fundamental_deltas": {
                metric: detail["delta"]
                for metric, detail in first_details.items()
                if detail["delta"] is not None
            },
            "false_turnaround": (
                first_aggregate["fundamental_improved"] is False
                if first_aggregate["status"] == "observed"
                else None
            ),
            "fundamental_next_report_period": _date_text(first["report_period"]),
            "fundamental_next_report_disclosure_version": str(first_row["_disclosure_version"]),
            "fundamental_next_two_report_periods": [_date_text(first["report_period"])],
        }
        return values, rows

    second = next_two[1]
    second_row = second["row"]
    second_aggregate = _aggregate_fundamental_report(baseline_values, second_row, settings)
    persistence = (
        bool(first_aggregate["fundamental_improved"] and second_aggregate["fundamental_improved"])
        if first_aggregate["status"] == "observed" and second_aggregate["status"] == "observed"
        else None
    )
    second_outcome = {
        "ts_code": code,
        "as_of_date": _date_text(as_of),
        "window": "next_two_reports",
        "status": "observed" if persistence is not None else "insufficient_metric_coverage",
        "reason": None if persistence is not None else "missing_metric",
        "fundamental_persistence": persistence,
        "fundamental_improved": persistence,
        "observation_used": True,
        "report_periods": [_date_text(first["report_period"]), _date_text(second["report_period"])],
        "disclosure_versions": [
            str(first_row["_disclosure_version"]),
            str(second_row["_disclosure_version"]),
        ],
        "actual_available_dates": [
            _date_text(first_row["_available_date"]),
            _date_text(second_row["_available_date"]),
        ],
        "revision_counts_not_used": [first["revision_count"], second["revision_count"]],
        "baseline_period": _date_text(baseline_period),
        "baseline_source": baseline_source,
        "revision_policy": settings.fundamental_revision_policy,
        "next_report_follow_through": first_aggregate["fundamental_improved"],
        "next_second_report_follow_through": second_aggregate["fundamental_improved"],
    }
    for prefix, details in (
        ("next_report", first_details),
        ("second_report", second_aggregate["details"]),
    ):
        for metric, detail in details.items():
            second_outcome[f"{prefix}_{metric}_value"] = detail["value"]
            second_outcome[f"{prefix}_{metric}_delta"] = detail["delta"]
            second_outcome[f"{prefix}_{metric}_improved"] = detail["improved"]
            second_outcome[f"{prefix}_{metric}_status"] = detail["status"]
    rows.append(second_outcome)
    values = {
        **empty,
        "fundamental_improved": first_aggregate["fundamental_improved"],
        "fundamental_status": first_aggregate["status"],
        "fundamental_metric_count": first_aggregate["available_count"],
        "fundamental_baseline_date": _date_text(baseline_date),
        "fundamental_baseline_period": _date_text(baseline_period),
        "fundamental_observation_date": _date_text(first_row["_available_date"]),
        "fundamental_deltas": {
            metric: detail["delta"]
            for metric, detail in first_details.items()
            if detail["delta"] is not None
        },
        "fundamental_next_two_improved": persistence,
        "fundamental_persistence": persistence,
        "false_turnaround": (
            first_aggregate["fundamental_improved"] is False
            if first_aggregate["status"] == "observed"
            else None
        ),
        "fundamental_next_report_period": _date_text(first["report_period"]),
        "fundamental_next_report_disclosure_version": str(first_row["_disclosure_version"]),
        "fundamental_next_two_report_periods": [
            _date_text(first["report_period"]),
            _date_text(second["report_period"]),
        ],
    }
    return values, rows


def _fundamental_improvement(
    prepared: pd.DataFrame,
    selection: pd.Series,
    code: str,
    as_of: pd.Timestamp,
    end_date: str | None,
    settings: EvaluationConfig,
) -> dict[str, Any]:
    """Compatibility wrapper retaining the old internal helper signature."""

    del end_date
    values, _ = _fundamental_evaluation(prepared, selection, code, as_of, settings)
    return values


def _industry_exposure(frame: pd.DataFrame) -> dict[str, float]:
    if "industry" not in frame.columns:
        return {}
    known = frame["industry"].dropna().astype(str)
    if known.empty:
        return {}
    counts = known.value_counts(sort=False)
    return {name: float(counts[name] / len(known)) for name in sorted(counts.index)}


def _market_cap_exposure(frame: pd.DataFrame) -> dict[str, float | int]:
    if "market_cap" not in frame.columns:
        return {}
    values = pd.to_numeric(frame["market_cap"], errors="coerce").dropna()
    if values.empty:
        return {}
    return {
        "count": int(len(values)),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def _iqr(values: list[float]) -> float | None:
    if not values:
        return None
    series = pd.Series(values, dtype=float)
    return float(series.quantile(0.75) - series.quantile(0.25))


def _reason_counts(series: pd.Series, *, observed: set[str] = frozenset()) -> dict[str, int]:
    counts = series.fillna("missing").astype(str).value_counts()
    return {str(reason): int(count) for reason, count in counts.items() if reason not in observed}


def _fundamental_summary(outcomes: pd.DataFrame, settings: EvaluationConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if outcomes.empty:
        return pd.DataFrame()
    for window in ("next_report", "next_two_reports"):
        subset = outcomes.loc[outcomes["window"].eq(window)].copy()
        if subset.empty:
            continue
        aggregate_column = (
            "fundamental_improved" if window == "next_report" else "fundamental_persistence"
        )
        aggregate = (
            subset[aggregate_column].dropna()
            if aggregate_column in subset.columns
            else pd.Series(dtype=bool)
        )
        rows.append(
            {
                "window": window,
                "metric": "aggregate",
                "eligible_count": int(len(subset)),
                "available_count": int(len(aggregate)),
                "missing_count": int(len(subset) - len(aggregate)),
                "coverage": float(len(aggregate) / len(subset)) if len(subset) else 0.0,
                "improved_count": int(aggregate.astype(bool).sum()) if not aggregate.empty else 0,
                "follow_through_rate": float(aggregate.astype(bool).mean())
                if not aggregate.empty
                else None,
                "false_turnaround_count": int((~aggregate.astype(bool)).sum())
                if window == "next_report" and not aggregate.empty
                else None,
                "reason_codes": _reason_counts(subset["status"], observed={"observed"}),
            }
        )
        for metric in settings.fundamental_metrics:
            status_column = f"{metric}_status"
            improved_column = f"{metric}_improved"
            if window == "next_two_reports":
                # The two-report row carries the first and second report
                # fields separately.  Persistence is reported in aggregate;
                # metric-level persistence is deliberately not invented.
                continue
            if status_column not in subset.columns:
                continue
            available = subset.loc[subset[status_column].isin(["observed", "known", "valid"])]
            improved = (
                available[improved_column].dropna()
                if improved_column in available.columns
                else pd.Series(dtype=bool)
            )
            rows.append(
                {
                    "window": window,
                    "metric": metric,
                    "eligible_count": int(len(subset)),
                    "available_count": int(len(improved)),
                    "missing_count": int(len(subset) - len(improved)),
                    "coverage": float(len(improved) / len(subset)) if len(subset) else 0.0,
                    "improved_count": int(improved.astype(bool).sum()) if not improved.empty else 0,
                    "follow_through_rate": float(improved.astype(bool).mean())
                    if not improved.empty
                    else None,
                    "false_turnaround_count": int((~improved.astype(bool)).sum())
                    if not improved.empty
                    else None,
                    "reason_codes": _reason_counts(
                        subset[status_column], observed={"observed", "known", "valid"}
                    ),
                }
            )
    return pd.DataFrame(rows)


def evaluate_scans(
    scans: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    config: EvaluationConfig | None = None,
    stock_basic: pd.DataFrame | None = None,
    exposures: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
    index_daily: pd.DataFrame | None = None,
    trade_calendar: pd.DataFrame | None = None,
    adj_factor: pd.DataFrame | None = None,
    suspensions: pd.DataFrame | None = None,
) -> EvaluationResult:
    """Evaluate frozen Top-N selections against later market/report outcomes.

    ``market_outcomes`` and ``fundamental_outcomes`` are independent output
    tables.  The legacy combined ``observations`` table is retained as a
    compatibility view and carries the same future-outcome fields.
    """

    settings = config or EvaluationConfig()
    _validate_config(settings)
    benchmark_frame = daily if index_daily is None else index_daily
    legacy_compatibility = trade_calendar is None and index_daily is None and adj_factor is None
    configuration = settings.declared()
    configuration["legacy_compatibility_mode"] = legacy_compatibility
    provenance = {
        "evaluation_engine_version": EVALUATION_ENGINE_VERSION,
        "evaluation_contract_version": settings.version,
        "evaluation_config_fingerprint": settings.fingerprint,
        "scan_digest": _frame_digest(scans),
        "daily_digest": _frame_digest(daily),
        "index_daily_digest": _frame_digest(index_daily),
        "trade_calendar_digest": _frame_digest(trade_calendar),
        "adj_factor_digest": _frame_digest(adj_factor),
        "stock_basic_digest": _frame_digest(stock_basic),
        "exposures_digest": _frame_digest(exposures),
        "fundamentals_digest": _frame_digest(fundamentals),
        "scan_snapshot_ids": _source_values(scans, "snapshot_id"),
        "scan_run_ids": _source_values(scans, "run_id"),
        "score_config_fingerprints": _source_values(scans, "score_config_fingerprint"),
        "input_scan_rows": int(len(scans)),
        "selection_outcome_separation": True,
        "future_fundamental_evaluation_only": True,
    }
    limitations = (
        "Stock returns use close multiplied by the supplied adj_factor under "
        "adjusted_close_adj_factor_v1; baseline rows without an exact positive "
        "factor are unavailable.",
        "Benchmark returns use raw index_daily close for 000300.SH; benchmark "
        "and stock endpoints share the trade_cal open-session axis.",
        "Each as-of date is an independent overlapping equal-weight cohort, "
        "not a capital-constrained live portfolio.",
        "A dated delisting inside a holding window receives the frozen "
        "delisted_return assumption; other missing endpoints remain "
        "reason-coded and are not dropped.",
        "Future fundamental reports are selected by distinct report period and "
        "use the first version whose initial availability is after the scanner "
        "snapshot; later revisions are recorded but not used.",
        "Industry exposure uses frozen scan or dated exposure fields only; "
        "current stock_basic industry is not a baseline fallback.",
        "Fundamental follow-through is a strict majority of available metric "
        "deltas with at least the configured minimum metric count; missing is "
        "not failure.",
    )
    required = {"ts_code", "as_of_date"}
    if scans.empty or not required.issubset(scans.columns):
        return EvaluationResult(
            settings.version,
            "EMPTY",
            pd.DataFrame(),
            pd.DataFrame(),
            ("missing_scan_rows",),
            configuration,
            limitations,
            provenance,
        )
    selected = _selected_scans(scans, settings.top_n)
    provenance["selected_scan_rows"] = int(len(selected))
    provenance["selected_snapshot_count"] = (
        int(selected["_as_of"].nunique()) if not selected.empty else 0
    )
    provenance["ranking_eligible_required"] = "ranking_eligible" in scans.columns
    provenance["ranking_eligible_selected_count"] = int(len(selected))
    if selected.empty:
        warnings = ["no_eligible_scan_rows"]
        if "rejected" in scans.columns and scans["rejected"].map(_bool_value).fillna(False).any():
            warnings.append("rejected_scan_rows_excluded")
        return EvaluationResult(
            settings.version,
            "EMPTY",
            pd.DataFrame(),
            pd.DataFrame(),
            tuple(warnings),
            configuration,
            limitations,
            provenance,
        )

    prepared_fundamentals = _prepare_fundamentals(fundamentals)
    market_dates, calendar_source = _calendar_sessions(trade_calendar, daily, benchmark_frame)
    provenance["calendar_source"] = calendar_source
    codes = set(selected["_code"].astype(str))
    if settings.benchmark_code:
        codes.add(str(settings.benchmark_code).upper())
    histories = _build_price_histories(
        daily,
        codes,
        adj_factor=adj_factor,
        require_adjustment=settings.require_adjustment_factor,
    )
    benchmark_history = (
        _price_history(
            benchmark_frame,
            str(settings.benchmark_code).upper(),
            require_adjustment=False,
        )
        if settings.benchmark_code
        else pd.DataFrame()
    )
    suspended_by_code: dict[str, set[pd.Timestamp]] = {}
    if (
        suspensions is not None
        and not suspensions.empty
        and {"ts_code", "trade_date"}.issubset(suspensions.columns)
    ):
        suspension_frame = suspensions.copy()
        if "suspend_type" in suspension_frame.columns:
            suspension_frame = suspension_frame.loc[
                suspension_frame["suspend_type"].astype("string").str.upper().eq("S")
            ]
        suspension_frame["_date"] = normalize_date_series(suspension_frame["trade_date"])
        for code, group in suspension_frame.loc[suspension_frame["_date"].notna()].groupby(
            suspension_frame["ts_code"].astype("string"), sort=False
        ):
            suspended_by_code[str(code)] = set(
                pd.Timestamp(value).normalize() for value in group["_date"]
            )
    exposure_dates = [pd.Timestamp(value).normalize() for value in selected["_as_of"]]
    cap_buckets = _market_cap_buckets(exposures, exposure_dates)
    exposure_lookup = exposures
    if (
        exposures is not None
        and not exposures.empty
        and "ts_code" in exposures.columns
        and "trade_date" in exposures.columns
    ):
        exposure_dates_series = normalize_date_series(exposures["trade_date"])
        exposure_mask = exposures["ts_code"].astype("string").isin(
            codes
        ) & exposure_dates_series.le(max(exposure_dates))
        exposure_lookup = exposures.loc[exposure_mask].copy()
    fundamental_rows: list[dict[str, Any]] = []
    fundamental_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    market_rows: list[dict[str, Any]] = []
    cost = settings.transaction_cost_bps / 10_000.0

    for _, selection in selected.iterrows():
        code = str(selection["_code"])
        as_of = pd.Timestamp(selection["_as_of"])
        rank_value = pd.to_numeric(selection.get("rank"), errors="coerce")
        rank = int(rank_value) if pd.notna(rank_value) else None
        reference = _reference_row(stock_basic, code, as_of)
        exposure_reference = (
            _exact_reference_row(exposure_lookup, code, as_of)
            if settings.market_cap_bucket_convention == "as_of_cross_section_tercile"
            else _reference_row(exposure_lookup, code, as_of)
        )
        universe_status = _historical_universe_status(selection, reference, as_of)
        industry, market_cap, industry_source, market_cap_source = _exposure_values(
            selection,
            reference,
            exposure_reference,
            allow_stock_basic_industry=legacy_compatibility,
        )
        fundamental, rows = _fundamental_evaluation(
            prepared_fundamentals, selection, code, as_of, settings
        )
        fundamental_by_key[(code, _date_text(as_of) or "")] = fundamental
        fundamental_rows.extend(rows)
        candidate_history = histories.get(code, pd.DataFrame())
        for horizon in settings.horizons:
            target_date = _market_target_date(market_dates, as_of, horizon)
            candidate = _forward_observation(
                candidate_history,
                as_of,
                target_date,
                require_adjustment=settings.require_adjustment_factor,
                suspended_dates=suspended_by_code.get(code),
            )
            candidate = _delist_adjusted_observation(
                candidate, reference, as_of, target_date, settings.delisted_return
            )
            if settings.benchmark_code:
                benchmark = _forward_observation(
                    benchmark_history,
                    as_of,
                    target_date,
                    require_adjustment=False,
                    missing_history_status="missing_benchmark_history",
                    missing_entry_status="missing_benchmark_entry",
                    missing_endpoint_status="missing_benchmark_endpoint",
                )
            else:
                benchmark = _ForwardObservation(
                    None, None, None, "not_configured", "benchmark_not_configured"
                )
            gross = candidate.value
            net = max(-1.0, gross - cost) if gross is not None else None
            excess = (
                gross - benchmark.value
                if gross is not None and benchmark.value is not None
                else None
            )
            net_excess = excess - cost if excess is not None else None
            row = {
                "ts_code": code,
                "as_of_date": _date_text(as_of),
                "rank": rank,
                "snapshot_id": selection.get("snapshot_id"),
                "run_id": selection.get("run_id"),
                "score_version": selection.get("score_version"),
                "score_config_fingerprint": selection.get("score_config_fingerprint"),
                "snapshot_regime": selection.get("market_regime", selection.get("regime")),
                "horizon": horizon,
                "holding_days": horizon,
                "entry_date": _date_text(as_of),
                "end_date": candidate.end_date,
                "forward_return": gross,
                "net_forward_return": net,
                "raw_entry_price": candidate.raw_entry_price,
                "raw_exit_price": candidate.raw_exit_price,
                "adjusted_entry_price": candidate.adjusted_entry_price,
                "adjusted_exit_price": candidate.adjusted_exit_price,
                "benchmark_return": benchmark.value,
                "benchmark_entry_date": benchmark.entry_date,
                "benchmark_end_date": benchmark.end_date,
                "excess_return": excess,
                "net_excess_return": net_excess,
                "forward_max_drawdown": candidate.drawdown,
                "observation_status": candidate.status,
                "observation_reason": candidate.reason or candidate.status,
                "benchmark_status": benchmark.status,
                "benchmark_reason": benchmark.reason or benchmark.status,
                "historical_universe_status": universe_status,
                "industry": industry,
                "market_cap": market_cap,
                "market_cap_bucket": cap_buckets.get((code, as_of)),
                "industry_source": industry_source,
                "market_cap_source": market_cap_source,
                **fundamental,
            }
            market_rows.append(row)

    market_frame = pd.DataFrame(market_rows)
    fundamental_frame = pd.DataFrame(fundamental_rows)
    summaries: list[dict[str, Any]] = []
    turnover = _turnover(selected, settings.top_n, convention=settings.turnover_convention)
    for horizon in settings.horizons:
        horizon_frame = market_frame.loc[market_frame["horizon"].eq(horizon)].copy()
        returns = pd.to_numeric(horizon_frame["forward_return"], errors="coerce").dropna().tolist()
        net_returns = (
            pd.to_numeric(horizon_frame["net_forward_return"], errors="coerce").dropna().tolist()
        )
        benchmark_returns = (
            pd.to_numeric(horizon_frame["benchmark_return"], errors="coerce").dropna().tolist()
        )
        excess = pd.to_numeric(horizon_frame["excess_return"], errors="coerce").dropna().tolist()
        net_excess = (
            pd.to_numeric(horizon_frame["net_excess_return"], errors="coerce").dropna().tolist()
        )
        cohort_returns = (
            horizon_frame.groupby("as_of_date", sort=True)["forward_return"]
            .mean()
            .dropna()
            .tolist()
        )
        candidate_drawdowns = pd.to_numeric(
            horizon_frame["forward_max_drawdown"], errors="coerce"
        ).dropna()
        improved = horizon_frame["fundamental_improved"].dropna().astype(bool)
        statuses = horizon_frame["observation_status"].value_counts()
        universe_statuses = horizon_frame["historical_universe_status"].value_counts()
        industry_exposure = _industry_exposure(horizon_frame)
        market_cap_exposure = _market_cap_exposure(horizon_frame)
        worst = (
            horizon_frame.loc[
                pd.to_numeric(horizon_frame["forward_return"], errors="coerce").notna(),
                ["ts_code", "as_of_date", "rank", "forward_return", "observation_status"],
            ]
            .sort_values("forward_return", kind="stable")
            .head(5)
        )
        observed_excess = [value for value in excess if value == value]
        summary = {
            "horizon": horizon,
            "candidate_count": int(len(horizon_frame)),
            "snapshot_count": int(horizon_frame["as_of_date"].nunique()),
            "observed_count": len(returns),
            "missing_count": int(len(horizon_frame) - len(returns)),
            "missing_rate": float((len(horizon_frame) - len(returns)) / len(horizon_frame))
            if len(horizon_frame)
            else 0.0,
            "coverage": len(returns) / len(horizon_frame) if len(horizon_frame) else 0.0,
            "mean_return": sum(returns) / len(returns) if returns else None,
            "mean_top_n_return": sum(cohort_returns) / len(cohort_returns)
            if cohort_returns
            else None,
            "mean_net_return": sum(net_returns) / len(net_returns) if net_returns else None,
            "median_return": float(pd.Series(returns).median()) if returns else None,
            "median_net_return": float(pd.Series(net_returns).median()) if net_returns else None,
            "return_iqr": _iqr(returns),
            "hit_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "positive_absolute_hit_rate": sum(value > 0 for value in returns) / len(returns)
            if returns
            else None,
            "mean_benchmark_return": sum(benchmark_returns) / len(benchmark_returns)
            if benchmark_returns
            else None,
            "median_benchmark_return": float(pd.Series(benchmark_returns).median())
            if benchmark_returns
            else None,
            "benchmark_observed_count": len(benchmark_returns),
            "benchmark_missing_count": int(len(horizon_frame) - len(benchmark_returns)),
            "mean_excess_return": sum(excess) / len(excess) if excess else None,
            "mean_net_excess_return": sum(net_excess) / len(net_excess) if net_excess else None,
            "median_excess_return": float(pd.Series(observed_excess).median())
            if observed_excess
            else None,
            "excess_iqr": _iqr(observed_excess),
            "excess_hit_rate": sum(value > 0 for value in excess) / len(excess) if excess else None,
            "positive_benchmark_excess_hit_rate": sum(value > 0 for value in excess) / len(excess)
            if excess
            else None,
            "max_drawdown": _max_drawdown(cohort_returns),
            "mean_candidate_drawdown": float(candidate_drawdowns.mean())
            if not candidate_drawdowns.empty
            else None,
            "worst_candidate_drawdown": float(candidate_drawdowns.min())
            if not candidate_drawdowns.empty
            else None,
            "worst_observations": worst.to_dict(orient="records"),
            "delisted_count": int(statuses.get("delisted_assumption", 0)),
            "reason_codes": _reason_counts(
                horizon_frame["observation_status"], observed={"observed", "delisted_assumption"}
            ),
            "price_missingness": _reason_counts(
                horizon_frame["observation_status"], observed={"observed", "delisted_assumption"}
            ),
            "benchmark_missingness": _reason_counts(
                horizon_frame["benchmark_status"], observed={"observed"}
            ),
            "historical_universe_member_count": int(
                horizon_frame["historical_universe_status"]
                .isin({"member_from_snapshot", "member_from_history"})
                .sum()
            ),
            "historical_universe_missing_count": int(
                (
                    ~horizon_frame["historical_universe_status"].isin(
                        {"member_from_snapshot", "member_from_history"}
                    )
                ).sum()
            ),
            "historical_universe_status_counts": {
                str(reason): int(count) for reason, count in universe_statuses.items()
            },
            "fundamental_observed_count": int(len(improved)),
            "fundamental_missing_count": int(len(horizon_frame) - len(improved)),
            "fundamental_improved_count": int(improved.sum()),
            "fundamental_improvement_rate": float(improved.mean()) if not improved.empty else None,
            "fundamental_next_two_observed_count": int(
                horizon_frame["fundamental_persistence"].notna().sum()
            ),
            "fundamental_persistence_rate": float(
                horizon_frame["fundamental_persistence"].dropna().astype(bool).mean()
            )
            if horizon_frame["fundamental_persistence"].notna().any()
            else None,
            "industry_count": len(industry_exposure),
            "industry_missing_count": int(horizon_frame["industry"].isna().sum()),
            "industry_exposure": industry_exposure,
            "market_cap_mean": market_cap_exposure.get("mean"),
            "market_cap_missing_count": int(horizon_frame["market_cap"].isna().sum()),
            "market_cap_exposure": market_cap_exposure,
            "market_cap_bucket_counts": {
                str(key): int(value)
                for key, value in horizon_frame["market_cap_bucket"]
                .fillna("missing")
                .value_counts()
                .items()
            },
            "turnover": turnover,
            "benchmark_code": settings.benchmark_code,
            "holding_convention": settings.holding_convention,
            "benchmark_convention": settings.benchmark_convention,
            "portfolio_convention": settings.portfolio_convention,
            "turnover_convention": settings.turnover_convention,
            "transaction_cost_bps": settings.transaction_cost_bps,
            "transaction_cost_convention": settings.transaction_cost_convention,
            "delisted_return_assumption": settings.delisted_return,
            "price_adjustment_convention": settings.price_adjustment_convention,
            "fundamental_metrics": list(settings.fundamental_metrics),
            "fundamental_min_delta": settings.fundamental_min_delta,
            "fundamental_min_available_metrics": settings.fundamental_min_available_metrics,
        }
        summaries.append(summary)
    summary_frame = pd.DataFrame(summaries)
    warnings: list[str] = []
    if not legacy_compatibility and calendar_source != "trade_cal":
        warnings.append("trade_calendar_not_provided_or_unusable")
    if settings.benchmark_code and benchmark_frame.empty:
        warnings.append("benchmark_dataset_not_provided")
    if settings.benchmark_code and market_frame["benchmark_return"].isna().any():
        warnings.append("benchmark_outcome_missing_for_some_candidates")
    if market_frame["forward_return"].isna().any():
        warnings.append("market_outcome_missing_for_some_candidates")
    if prepared_fundamentals.empty:
        warnings.append("fundamental_history_not_provided")
    elif not legacy_compatibility and fundamental_frame["fundamental_improved"].isna().any():
        warnings.append("fundamental_outcome_missing_for_some_candidates")
    if stock_basic is None or stock_basic.empty:
        warnings.append("delisting_reference_not_provided")
    if market_frame["industry"].isna().any():
        warnings.append("industry_exposure_missing_for_some_candidates")
    if market_frame["market_cap"].isna().any():
        warnings.append("market_cap_exposure_missing_for_some_candidates")
    # Legacy tests and consumers retain PASS for their intentionally incomplete
    # small fixtures.  A baseline-configured run is PARTIAL when evidence is
    # unavailable; missing observations are still preserved in both schemas.
    baseline_strict = (
        settings.require_adjustment_factor
        or settings.version == BASELINE_EVALUATION_CONTRACT_VERSION
    )
    status = "PARTIAL" if baseline_strict and warnings else "PASS"
    market_columns = [
        "ts_code",
        "as_of_date",
        "rank",
        "snapshot_id",
        "run_id",
        "score_version",
        "score_config_fingerprint",
        "snapshot_regime",
        "horizon",
        "holding_days",
        "entry_date",
        "end_date",
        "forward_return",
        "net_forward_return",
        "raw_entry_price",
        "raw_exit_price",
        "adjusted_entry_price",
        "adjusted_exit_price",
        "benchmark_return",
        "benchmark_entry_date",
        "benchmark_end_date",
        "excess_return",
        "net_excess_return",
        "forward_max_drawdown",
        "observation_status",
        "observation_reason",
        "benchmark_status",
        "benchmark_reason",
        "historical_universe_status",
        "industry",
        "market_cap",
        "market_cap_bucket",
        "industry_source",
        "market_cap_source",
    ]
    market_outcomes = market_frame.loc[:, market_columns].copy()
    fundamental_summary = _fundamental_summary(fundamental_frame, settings)
    provenance["market_outcome_rows"] = int(len(market_outcomes))
    provenance["fundamental_outcome_rows"] = int(len(fundamental_frame))
    provenance["outcome_availability"] = {
        str(horizon): {
            "available": int(
                (
                    market_frame.loc[market_frame["horizon"].eq(horizon), "forward_return"].notna()
                ).sum()
            ),
            "missing": int(
                (
                    market_frame.loc[market_frame["horizon"].eq(horizon), "forward_return"].isna()
                ).sum()
            ),
        }
        for horizon in settings.horizons
    }
    return EvaluationResult(
        settings.version,
        status,
        summary_frame,
        market_frame,
        tuple(dict.fromkeys(warnings)),
        configuration,
        limitations,
        provenance,
        market_outcomes,
        fundamental_frame,
        fundamental_summary,
    )


def _turnover(
    scans: pd.DataFrame, top_n: int, *, convention: str = "jaccard_top_n"
) -> float | None:
    if "as_of_date" not in scans.columns or "ts_code" not in scans.columns:
        return None
    sets: list[set[str]] = []
    for _, group in scans.groupby("as_of_date", sort=True):
        if "ranking_eligible" in group.columns:
            group = group.loc[group["ranking_eligible"].map(_bool_value).eq(True)]
        if "rank" in group.columns:
            group = group.sort_values("rank")
        sets.append(set(group["ts_code"].astype(str).head(top_n)))
    if len(sets) < 2:
        return 0.0
    if convention == "one_way_top_n":
        changes = [
            1.0 - len(left & right) / max(1, len(left), len(right))
            for left, right in zip(sets, sets[1:])
        ]
    else:
        changes = [
            1.0 - len(left & right) / max(1, len(left | right))
            for left, right in zip(sets, sets[1:])
        ]
    return sum(changes) / len(changes)


def run_ablation(
    variants: dict[str, pd.DataFrame],
    *,
    top_n: int = 20,
    baseline: str = "fundamental_only",
) -> pd.DataFrame:
    """Compare independently switchable feature-group variants."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if not variants:
        return pd.DataFrame()
    baseline_frame = variants.get(baseline)
    if baseline_frame is None:
        baseline = next(iter(variants))
        baseline_frame = variants[baseline]
    required = {"ts_code"}
    if not required.issubset(baseline_frame.columns):
        raise ValueError("ablation frames require ts_code")
    baseline_sets = _top_code_sets(baseline_frame, top_n)
    rows: list[dict[str, Any]] = []
    for name, frame in variants.items():
        if "ts_code" not in frame.columns:
            raise ValueError(f"ablation variant missing ts_code: {name}")
        current_sets = _top_code_sets(frame, top_n)
        comparable = sorted(set(baseline_sets) & set(current_sets))
        overlaps = [
            len(baseline_sets[key] & current_sets[key])
            / max(1, len(baseline_sets[key] | current_sets[key]))
            for key in comparable
        ]
        base = set().union(*(baseline_sets[key] for key in comparable)) if comparable else set()
        current = set().union(*(current_sets[key] for key in comparable)) if comparable else set()
        rows.append(
            {
                "variant": name,
                "baseline": baseline,
                "snapshot_count": len(current_sets),
                "comparison_count": len(comparable),
                "candidate_count": sum(len(value) for value in current_sets.values()),
                "rank_overlap": sum(overlaps) / len(overlaps) if overlaps else None,
                "added_candidates": "|".join(sorted(current - base)),
                "removed_candidates": "|".join(sorted(base - current)),
            }
        )
    return pd.DataFrame(rows).sort_values("variant", kind="stable").reset_index(drop=True)


def _top_codes(frame: pd.DataFrame, top_n: int) -> list[str]:
    ordered = frame.copy()
    if "ranking_eligible" in ordered.columns:
        ordered = ordered.loc[ordered["ranking_eligible"].map(_bool_value).eq(True)]
    if "rank" in ordered.columns:
        ordered["_rank"] = pd.to_numeric(ordered["rank"], errors="coerce")
        ordered = ordered.sort_values(["_rank", "ts_code"], na_position="last", kind="stable")
    elif "turnaround_score" in ordered.columns:
        ordered["_score"] = pd.to_numeric(ordered["turnaround_score"], errors="coerce")
        ordered = ordered.sort_values(
            ["_score", "ts_code"], ascending=[False, True], na_position="last", kind="stable"
        )
    return ordered["ts_code"].astype(str).head(top_n).tolist()


def _top_code_sets(frame: pd.DataFrame, top_n: int) -> dict[str, set[str]]:
    if "as_of_date" not in frame.columns:
        return {"all": set(_top_codes(frame, top_n))}
    result: dict[str, set[str]] = {}
    for as_of_date, group in frame.groupby("as_of_date", sort=True, dropna=False):
        result[str(as_of_date)] = set(_top_codes(group, top_n))
    return result


__all__ = [
    "BASELINE_BENCHMARK_CODE",
    "BASELINE_EVALUATION_CONTRACT_VERSION",
    "BASELINE_FUNDAMENTAL_METRICS",
    "BASELINE_HORIZONS",
    "BASELINE_PRICE_ADJUSTMENT_CONVENTION",
    "BASELINE_TOP_N",
    "BASELINE_TRANSACTION_COST_BPS",
    "EvaluationConfig",
    "EvaluationResult",
    "build_fundamental_history",
    "evaluate_scans",
    "frozen_baseline_evaluation_config",
    "run_ablation",
]
