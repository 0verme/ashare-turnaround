"""Reproducible forward evaluation and feature-group ablation reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from ..dates import normalize_date_series


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    version: str = "evaluation-v2"
    horizons: tuple[int, ...] = (20, 60, 120, 250)
    top_n: int = 20
    benchmark_code: str | None = None
    holding_convention: str = "as_of_close_to_nth_future_market_close"
    benchmark_convention: str = "same_as_of_and_horizon"
    portfolio_convention: str = "independent_overlapping_equal_weight_cohorts"
    turnover_convention: str = "jaccard_top_n"
    hit_rate_convention: str = "positive_forward_return"
    delisted_return: float = -1.0
    transaction_cost_bps: float = 0.0
    fundamental_metrics: tuple[str, ...] = (
        "revenue_yoy",
        "net_profit_yoy",
        "operating_profit_yoy",
    )
    fundamental_min_delta: float = 0.0

    def declared(self) -> dict[str, Any]:
        """Return a stable, machine-readable declaration of all assumptions."""

        return asdict(self)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.declared(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


@dataclass(frozen=True, slots=True)
class _ForwardObservation:
    value: float | None
    end_date: str | None
    drawdown: float | None
    status: str


def _as_of(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid evaluation date: {value!r}")
    return pd.Timestamp(parsed).normalize()


def _price_history(daily: pd.DataFrame, code: str) -> pd.DataFrame:
    if daily.empty or not {"ts_code", "trade_date", "close"}.issubset(daily.columns):
        return pd.DataFrame()
    frame = daily.loc[daily["ts_code"].astype("string").eq(code)].copy()
    frame["_date"] = normalize_date_series(frame["trade_date"])
    frame["_close"] = pd.to_numeric(frame["close"], errors="coerce")
    return (
        frame.loc[frame["_date"].notna() & frame["_close"].notna()]
        .sort_values("_date")
        .drop_duplicates("_date", keep="last")
        .reset_index(drop=True)
    )


def _price_drawdown(prices: list[float]) -> float | None:
    if not prices or prices[0] == 0:
        return None
    peak = prices[0]
    drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak != 0:
            drawdown = min(drawdown, price / peak - 1.0)
    return drawdown


def _forward_observation(
    daily: pd.DataFrame,
    code: str,
    as_of: pd.Timestamp,
    target_date: pd.Timestamp | None,
) -> _ForwardObservation:
    if target_date is None:
        return _ForwardObservation(None, None, None, "incomplete_market_window")
    history = _price_history(daily, code)
    if history.empty:
        return _ForwardObservation(None, None, None, "missing_price_history")
    start = history.loc[history["_date"] == as_of]
    if start.empty:
        return _ForwardObservation(None, None, None, "missing_entry_price")
    start_price = float(start.iloc[-1]["_close"])
    if start_price == 0:
        return _ForwardObservation(None, None, None, "invalid_entry_price")
    end = history.loc[history["_date"] == target_date]
    if end.empty:
        return _ForwardObservation(
            None,
            target_date.strftime("%Y%m%d"),
            None,
            "missing_horizon_price",
        )
    end_row = end.iloc[-1]
    end_price = float(end_row["_close"])
    path = history.loc[
        history["_date"].between(as_of, target_date, inclusive="both"), "_close"
    ].astype(float).tolist()
    return _ForwardObservation(
        (end_price - start_price) / abs(start_price),
        target_date.strftime("%Y%m%d"),
        _price_drawdown(path),
        "observed",
    )


def _forward_return(
    daily: pd.DataFrame, code: str, as_of: pd.Timestamp, horizon: int
) -> tuple[float | None, str | None]:
    if daily.empty or "trade_date" not in daily.columns:
        target_date = None
    else:
        market_dates = pd.DatetimeIndex(
            normalize_date_series(daily["trade_date"])
            .dropna()
            .drop_duplicates()
            .sort_values()
        )
        target_date = _market_target_date(market_dates, as_of, horizon)
    observation = _forward_observation(daily, code, as_of, target_date)
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
    if settings.benchmark_convention != "same_as_of_and_horizon":
        raise ValueError(f"unsupported benchmark_convention: {settings.benchmark_convention}")
    if settings.portfolio_convention != "independent_overlapping_equal_weight_cohorts":
        raise ValueError(f"unsupported portfolio_convention: {settings.portfolio_convention}")
    if settings.turnover_convention not in {"jaccard_top_n", "one_way_top_n"}:
        raise ValueError(f"unsupported turnover_convention: {settings.turnover_convention}")
    if settings.hit_rate_convention != "positive_forward_return":
        raise ValueError(f"unsupported hit_rate_convention: {settings.hit_rate_convention}")
    if not -1.0 <= settings.delisted_return <= 0.0:
        raise ValueError("delisted_return must be between -1.0 and 0.0")
    if settings.transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps cannot be negative")
    if not settings.fundamental_metrics:
        raise ValueError("fundamental_metrics cannot be empty")


def _selected_scans(scans: pd.DataFrame, top_n: int) -> pd.DataFrame:
    selected = scans.copy()
    if "rejected" in selected.columns:
        rejected = selected["rejected"].astype("string").str.lower().isin({"1", "true", "yes"})
        selected = selected.loc[~rejected].copy()
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
        selected["_selection_order"] = pd.to_numeric(
            selected["turnaround_score"], errors="coerce"
        )
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
    if (
        reference_data is None
        or reference_data.empty
        or "ts_code" not in reference_data.columns
    ):
        return None
    matched = reference_data.loc[
        reference_data["ts_code"].astype("string").eq(code)
    ].copy()
    if matched.empty:
        return None
    for field_name in ("as_of_date", "trade_date", "effective_date"):
        if field_name not in matched.columns:
            continue
        matched["_reference_date"] = normalize_date_series(matched[field_name])
        dated = matched.loc[matched["_reference_date"].notna()]
        available = dated.loc[dated["_reference_date"] <= as_of]
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
            value = str(selection[field_name]).strip().lower()
            return "member_from_snapshot" if value in {"1", "true", "yes"} else "not_member"
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
) -> tuple[str | None, float | None, str, str]:
    industry = _first_known(selection, ("industry",))
    market_cap = _first_known(selection, ("total_mv", "market_cap"))
    industry_source = "frozen_scan"
    market_cap_source = "frozen_scan"
    if industry is None:
        industry = _first_known(exposure_reference, ("industry",))
        industry_source = "dated_exposure"
    if industry is None:
        industry = _first_known(universe_reference, ("industry",))
        industry_source = "stock_basic_fallback"
    if market_cap is None:
        market_cap = _first_known(exposure_reference, ("total_mv", "market_cap"))
        market_cap_source = "dated_exposure"
    numeric_market_cap = pd.to_numeric(market_cap, errors="coerce")
    if industry is None:
        industry_source = "missing"
    if pd.isna(numeric_market_cap):
        market_cap_source = "missing"
    return (
        str(industry) if industry is not None else None,
        float(numeric_market_cap) if pd.notna(numeric_market_cap) else None,
        industry_source,
        market_cap_source,
    )


def _prepare_fundamentals(fundamentals: pd.DataFrame | None) -> pd.DataFrame:
    if fundamentals is None or fundamentals.empty or "ts_code" not in fundamentals.columns:
        return pd.DataFrame()
    prepared = fundamentals.copy()
    prepared["_code"] = prepared["ts_code"].astype("string")
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
    return prepared.loc[prepared["_available_date"].notna()].copy()


def _fundamental_improvement(
    prepared: pd.DataFrame,
    selection: pd.Series,
    code: str,
    as_of: pd.Timestamp,
    end_date: str | None,
    settings: EvaluationConfig,
) -> dict[str, Any]:
    empty = {
        "fundamental_improved": None,
        "fundamental_status": "missing_input",
        "fundamental_metric_count": 0,
        "fundamental_baseline_date": None,
        "fundamental_observation_date": None,
        "fundamental_deltas": {},
    }
    if prepared.empty:
        return empty
    end = _normalized_optional_date(end_date)
    if end is None:
        return {**empty, "fundamental_status": "missing_forward_window"}
    history = prepared.loc[prepared["_code"].eq(code)].copy()
    if history.empty:
        return {**empty, "fundamental_status": "missing_company_history"}
    baseline_values = {
        metric: float(value)
        for metric in settings.fundamental_metrics
        if (value := pd.to_numeric(selection.get(metric), errors="coerce")) is not None
        and pd.notna(value)
    }
    baseline_date: pd.Timestamp | None = as_of if baseline_values else None
    baseline_period: pd.Timestamp | None = None
    if not baseline_values:
        baseline_rows = history.loc[history["_available_date"] <= as_of].sort_values(
            ["_report_period", "_available_date"], na_position="first", kind="stable"
        )
        if baseline_rows.empty:
            return {**empty, "fundamental_status": "missing_pit_baseline"}
        baseline_row = baseline_rows.iloc[-1]
        baseline_date = pd.Timestamp(baseline_row["_available_date"])
        baseline_period = _normalized_optional_date(baseline_row["_report_period"])
        baseline_values = {
            metric: float(value)
            for metric in settings.fundamental_metrics
            if (value := pd.to_numeric(baseline_row.get(metric), errors="coerce")) is not None
            and pd.notna(value)
        }
    future = history.loc[
        history["_available_date"].gt(as_of) & history["_available_date"].le(end)
    ].copy()
    if baseline_period is not None:
        future = future.loc[
            future["_report_period"].isna() | future["_report_period"].gt(baseline_period)
        ]
    if future.empty:
        return {
            **empty,
            "fundamental_status": "missing_subsequent_pit_observation",
            "fundamental_baseline_date": baseline_date.strftime("%Y%m%d")
            if baseline_date is not None
            else None,
        }
    first_available = future["_available_date"].min()
    future_row = future.loc[future["_available_date"].eq(first_available)].sort_values(
        "_report_period", na_position="first", kind="stable"
    ).iloc[-1]
    deltas: dict[str, float] = {}
    for metric, baseline in baseline_values.items():
        future_value = pd.to_numeric(future_row.get(metric), errors="coerce")
        if pd.notna(future_value):
            deltas[metric] = float(future_value) - baseline
    if not deltas:
        return {
            **empty,
            "fundamental_status": "missing_comparable_metrics",
            "fundamental_baseline_date": baseline_date.strftime("%Y%m%d")
            if baseline_date is not None
            else None,
            "fundamental_observation_date": pd.Timestamp(first_available).strftime("%Y%m%d"),
        }
    mean_delta = sum(deltas.values()) / len(deltas)
    return {
        "fundamental_improved": bool(mean_delta > settings.fundamental_min_delta),
        "fundamental_status": "observed",
        "fundamental_metric_count": len(deltas),
        "fundamental_baseline_date": baseline_date.strftime("%Y%m%d")
        if baseline_date is not None
        else None,
        "fundamental_observation_date": pd.Timestamp(first_available).strftime("%Y%m%d"),
        "fundamental_deltas": deltas,
    }


def _industry_exposure(frame: pd.DataFrame) -> dict[str, float]:
    known = frame["industry"].dropna().astype(str)
    if known.empty:
        return {}
    counts = known.value_counts(sort=False)
    return {name: float(counts[name] / len(known)) for name in sorted(counts.index)}


def _market_cap_exposure(frame: pd.DataFrame) -> dict[str, float | int]:
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


def evaluate_scans(
    scans: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    config: EvaluationConfig | None = None,
    stock_basic: pd.DataFrame | None = None,
    exposures: pd.DataFrame | None = None,
    fundamentals: pd.DataFrame | None = None,
) -> EvaluationResult:
    """Evaluate frozen top-N selections against strictly subsequent observations.

    ``fundamentals`` is an optional PIT feature history. It must carry ``ts_code``,
    at least one declared fundamental metric, and an availability field. Only the
    first observation available after the as-of date and inside the price horizon
    is compared with the frozen/baseline value.
    """

    settings = config or EvaluationConfig()
    _validate_config(settings)
    configuration = settings.declared()
    provenance = {
        "evaluation_config_fingerprint": settings.fingerprint,
        "scan_digest": _frame_digest(scans),
        "daily_digest": _frame_digest(daily),
        "stock_basic_digest": _frame_digest(stock_basic),
        "exposures_digest": _frame_digest(exposures),
        "fundamentals_digest": _frame_digest(fundamentals),
        "scan_snapshot_ids": _source_values(scans, "snapshot_id"),
        "scan_run_ids": _source_values(scans, "run_id"),
        "score_config_fingerprints": _source_values(scans, "score_config_fingerprint"),
        "input_scan_rows": int(len(scans)),
    }
    limitations = (
        "Price returns use close values as supplied; corporate-action adjustment quality "
        "depends on the input dataset.",
        "Each as-of date is an independent overlapping equal-weight cohort, not a "
        "capital-constrained live portfolio.",
        "A missing delisted horizon is assigned the declared delisted_return; other failed "
        "or incomplete observations remain missing.",
        "Fundamental improvement depends on PIT availability dates and compares the first "
        "subsequent observation using the declared metric mean-delta rule.",
        "Industry and market-cap values prefer frozen scan columns; market cap otherwise "
        "requires a dated exposure row, while industry may fall back to stock_basic.",
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
    if selected.empty:
        empty_warnings = ["no_eligible_scan_rows"]
        if "rejected" in scans.columns:
            rejected = scans["rejected"].astype("string").str.lower().isin(
                {"1", "true", "yes"}
            )
            if rejected.any():
                empty_warnings.append("rejected_scan_rows_excluded")
        return EvaluationResult(
            settings.version,
            "EMPTY",
            pd.DataFrame(),
            pd.DataFrame(),
            tuple(empty_warnings),
            configuration,
            limitations,
            provenance,
        )
    prepared_fundamentals = _prepare_fundamentals(fundamentals)
    if daily.empty or "trade_date" not in daily.columns:
        market_dates = pd.DatetimeIndex([])
    else:
        dates = normalize_date_series(daily["trade_date"]).dropna().drop_duplicates().sort_values()
        market_dates = pd.DatetimeIndex(dates)
    observations: list[dict[str, Any]] = []
    benchmark_cache: dict[tuple[str, int], _ForwardObservation] = {}
    for _, selection in selected.iterrows():
        code = str(selection["_code"])
        as_of = pd.Timestamp(selection["_as_of"])
        rank_value = pd.to_numeric(selection.get("rank"), errors="coerce")
        rank = int(rank_value) if pd.notna(rank_value) else None
        reference = _reference_row(stock_basic, code, as_of)
        exposure_reference = _reference_row(exposures, code, as_of)
        universe_status = _historical_universe_status(selection, reference, as_of)
        industry, market_cap, industry_source, market_cap_source = _exposure_values(
            selection, reference, exposure_reference
        )
        for horizon in settings.horizons:
            target_date = _market_target_date(market_dates, as_of, horizon)
            candidate = _delist_adjusted_observation(
                _forward_observation(daily, code, as_of, target_date),
                reference,
                as_of,
                target_date,
                settings.delisted_return,
            )
            benchmark = _ForwardObservation(None, None, None, "not_configured")
            if settings.benchmark_code:
                key = (as_of.strftime("%Y%m%d"), horizon)
                if key not in benchmark_cache:
                    benchmark_cache[key] = _forward_observation(
                        daily, settings.benchmark_code, as_of, target_date
                    )
                benchmark = benchmark_cache[key]
            fundamental = _fundamental_improvement(
                prepared_fundamentals,
                selection,
                code,
                as_of,
                candidate.end_date,
                settings,
            )
            round_trip_cost = settings.transaction_cost_bps * 2.0 / 10_000.0
            observations.append(
                {
                    "ts_code": code,
                    "as_of_date": as_of.strftime("%Y%m%d"),
                    "rank": rank,
                    "snapshot_id": selection.get("snapshot_id"),
                    "run_id": selection.get("run_id"),
                    "score_version": selection.get("score_version"),
                    "score_config_fingerprint": selection.get(
                        "score_config_fingerprint"
                    ),
                    "horizon": horizon,
                    "holding_days": horizon,
                    "forward_return": candidate.value,
                    "net_forward_return": candidate.value - round_trip_cost
                    if candidate.value is not None
                    else None,
                    "benchmark_return": benchmark.value,
                    "excess_return": candidate.value - benchmark.value
                    if candidate.value is not None and benchmark.value is not None
                    else None,
                    "net_excess_return": candidate.value - benchmark.value - round_trip_cost
                    if candidate.value is not None and benchmark.value is not None
                    else None,
                    "end_date": candidate.end_date,
                    "forward_max_drawdown": candidate.drawdown,
                    "observation_status": candidate.status,
                    "benchmark_status": benchmark.status,
                    "historical_universe_status": universe_status,
                    "industry": industry,
                    "market_cap": market_cap,
                    "industry_source": industry_source,
                    "market_cap_source": market_cap_source,
                    **fundamental,
                }
            )
    observation_frame = pd.DataFrame(observations)
    summaries: list[dict[str, Any]] = []
    turnover = _turnover(selected, settings.top_n, convention=settings.turnover_convention)
    for horizon in settings.horizons:
        horizon_frame = observation_frame.loc[observation_frame["horizon"].eq(horizon)].copy()
        returns = pd.to_numeric(horizon_frame["forward_return"], errors="coerce").dropna().tolist()
        net_returns = (
            pd.to_numeric(horizon_frame["net_forward_return"], errors="coerce").dropna().tolist()
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
        industry_exposure = _industry_exposure(horizon_frame)
        market_cap_exposure = _market_cap_exposure(horizon_frame)
        statuses = horizon_frame["observation_status"].value_counts()
        benchmark_statuses = horizon_frame["benchmark_status"].value_counts()
        fundamental_statuses = horizon_frame["fundamental_status"].value_counts()
        universe_statuses = horizon_frame["historical_universe_status"].value_counts()
        missingness = {
            str(reason): int(count)
            for reason, count in statuses.items()
            if reason not in {"observed", "delisted_assumption"}
        }
        benchmark_missingness = {
            str(reason): int(count)
            for reason, count in benchmark_statuses.items()
            if reason != "observed"
        }
        fundamental_missingness = {
            str(reason): int(count)
            for reason, count in fundamental_statuses.items()
            if reason != "observed"
        }
        universe_known = horizon_frame["historical_universe_status"].isin(
            {"member_from_snapshot", "member_from_history"}
        )
        summaries.append(
            {
                "horizon": horizon,
                "candidate_count": int(len(horizon_frame)),
                "snapshot_count": int(horizon_frame["as_of_date"].nunique()),
                "observed_count": len(returns),
                "missing_count": int(len(horizon_frame) - len(returns)),
                "coverage": len(returns) / len(horizon_frame) if len(horizon_frame) else 0.0,
                "mean_return": sum(returns) / len(returns) if returns else None,
                "mean_top_n_return": sum(cohort_returns) / len(cohort_returns)
                if cohort_returns
                else None,
                "mean_net_return": sum(net_returns) / len(net_returns) if net_returns else None,
                "median_return": float(pd.Series(returns).median()) if returns else None,
                "hit_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "mean_excess_return": sum(excess) / len(excess) if excess else None,
                "mean_net_excess_return": sum(net_excess) / len(net_excess)
                if net_excess
                else None,
                "excess_hit_rate": sum(value > 0 for value in excess) / len(excess)
                if excess
                else None,
                "max_drawdown": _max_drawdown(cohort_returns),
                "mean_candidate_drawdown": float(candidate_drawdowns.mean())
                if not candidate_drawdowns.empty
                else None,
                "worst_candidate_drawdown": float(candidate_drawdowns.min())
                if not candidate_drawdowns.empty
                else None,
                "delisted_count": int(statuses.get("delisted_assumption", 0)),
                "failed_observation_count": int(
                    statuses.get("missing_price_history", 0)
                    + statuses.get("missing_entry_price", 0)
                    + statuses.get("invalid_entry_price", 0)
                    + statuses.get("missing_horizon_price", 0)
                ),
                "incomplete_window_count": int(statuses.get("incomplete_market_window", 0)),
                "price_missingness": missingness,
                "benchmark_missingness": benchmark_missingness,
                "historical_universe_member_count": int(universe_known.sum()),
                "historical_universe_missing_count": int((~universe_known).sum()),
                "historical_universe_status_counts": {
                    str(reason): int(count) for reason, count in universe_statuses.items()
                },
                "fundamental_observed_count": int(len(improved)),
                "fundamental_missing_count": int(len(horizon_frame) - len(improved)),
                "fundamental_improved_count": int(improved.sum()),
                "fundamental_improvement_rate": float(improved.mean())
                if not improved.empty
                else None,
                "fundamental_missingness": fundamental_missingness,
                "industry_count": len(industry_exposure),
                "industry_missing_count": int(horizon_frame["industry"].isna().sum()),
                "industry_exposure": industry_exposure,
                "market_cap_mean": market_cap_exposure.get("mean"),
                "market_cap_missing_count": int(horizon_frame["market_cap"].isna().sum()),
                "market_cap_exposure": market_cap_exposure,
                "turnover": turnover,
                "benchmark_code": settings.benchmark_code,
                "holding_convention": settings.holding_convention,
                "benchmark_convention": settings.benchmark_convention,
                "portfolio_convention": settings.portfolio_convention,
                "turnover_convention": settings.turnover_convention,
                "transaction_cost_bps": settings.transaction_cost_bps,
                "delisted_return_assumption": settings.delisted_return,
                "fundamental_metrics": list(settings.fundamental_metrics),
                "fundamental_min_delta": settings.fundamental_min_delta,
            }
        )
    summary = pd.DataFrame(summaries)
    warnings: list[str] = []
    if observation_frame["forward_return"].isna().any():
        warnings.append("forward_window_missing_for_some_candidates")
    if settings.benchmark_code is None:
        warnings.append("benchmark_not_configured")
    elif observation_frame["benchmark_return"].isna().any():
        warnings.append("benchmark_window_missing_for_some_candidates")
    if observation_frame["historical_universe_status"].str.startswith("unknown").any():
        warnings.append("historical_universe_missing_for_some_candidates")
    if stock_basic is None or stock_basic.empty:
        warnings.append("delisting_reference_not_provided")
    if observation_frame["historical_universe_status"].isin(
        {"not_member", "not_listed_by_as_of", "delisted_by_as_of"}
    ).any():
        warnings.append("selection_outside_historical_universe")
    if prepared_fundamentals.empty:
        warnings.append("fundamental_observations_not_provided")
    elif observation_frame["fundamental_improved"].isna().any():
        warnings.append("fundamental_observation_missing_for_some_candidates")
    if len(selected) < len(scans.drop_duplicates(["as_of_date", "ts_code"])):
        warnings.append("candidates_beyond_top_n_not_evaluated")
    if len(scans) != len(scans.drop_duplicates(["as_of_date", "ts_code"])):
        warnings.append("duplicate_scan_rows_deduplicated")
    if "rejected" in scans.columns:
        rejected = scans["rejected"].astype("string").str.lower().isin({"1", "true", "yes"})
        if rejected.any():
            warnings.append("rejected_scan_rows_excluded")
    if observation_frame["industry"].isna().any():
        warnings.append("industry_exposure_missing_for_some_candidates")
    if observation_frame["market_cap"].isna().any():
        warnings.append("market_cap_exposure_missing_for_some_candidates")
    status = (
        "PASS"
        if not observation_frame.empty and not warnings
        else "PARTIAL"
        if not observation_frame.empty
        else "EMPTY"
    )
    return EvaluationResult(
        settings.version,
        status,
        summary,
        observation_frame,
        tuple(warnings),
        configuration,
        limitations,
        provenance,
    )


def _turnover(
    scans: pd.DataFrame, top_n: int, *, convention: str = "jaccard_top_n"
) -> float | None:
    if "as_of_date" not in scans.columns or "ts_code" not in scans.columns:
        return None
    sets: list[set[str]] = []
    for _, group in scans.groupby("as_of_date", sort=True):
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
