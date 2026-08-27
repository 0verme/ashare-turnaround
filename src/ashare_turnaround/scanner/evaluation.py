"""Reproducible forward evaluation and feature-group ablation reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..dates import normalize_date_series


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    version: str = "evaluation-v1"
    horizons: tuple[int, ...] = (20, 60, 120, 250)
    top_n: int = 20
    benchmark_code: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    config_version: str
    status: str
    summary: pd.DataFrame
    observations: pd.DataFrame
    warnings: tuple[str, ...] = ()


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
        .reset_index(drop=True)
    )


def _forward_return(
    daily: pd.DataFrame, code: str, as_of: pd.Timestamp, horizon: int
) -> tuple[float | None, str | None]:
    history = _price_history(daily, code)
    if history.empty:
        return None, None
    after = history.loc[history["_date"] > as_of].reset_index(drop=True)
    if len(after) <= horizon - 1:
        return None, None
    start = history.loc[history["_date"] <= as_of]
    if start.empty:
        return None, None
    start_price = float(start.iloc[-1]["_close"])
    end_row = after.iloc[horizon - 1]
    end_price = float(end_row["_close"])
    if start_price == 0:
        return None, None
    return (end_price - start_price) / abs(start_price), end_row["_date"].strftime("%Y%m%d")


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


def evaluate_scans(
    scans: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    config: EvaluationConfig | None = None,
    stock_basic: pd.DataFrame | None = None,
) -> EvaluationResult:
    """Evaluate frozen selections only against dates strictly after each as-of."""

    settings = config or EvaluationConfig()
    required = {"ts_code", "as_of_date"}
    if scans.empty or not required.issubset(scans.columns):
        return EvaluationResult(
            settings.version, "EMPTY", pd.DataFrame(), pd.DataFrame(), ("missing_scan_rows",)
        )
    observations: list[dict[str, Any]] = []
    benchmark_cache: dict[tuple[str, int], float | None] = {}
    for _, selection in scans.iterrows():
        code = str(selection["ts_code"])
        as_of = _as_of(selection["as_of_date"])
        rank = int(selection.get("rank", 0) or 0)
        for horizon in settings.horizons:
            forward, end_date = _forward_return(daily, code, as_of, horizon)
            benchmark = None
            if settings.benchmark_code:
                key = (as_of.strftime("%Y%m%d"), horizon)
                if key not in benchmark_cache:
                    benchmark_cache[key], _ = _forward_return(
                        daily, settings.benchmark_code, as_of, horizon
                    )
                benchmark = benchmark_cache[key]
            observations.append(
                {
                    "ts_code": code,
                    "as_of_date": as_of.strftime("%Y%m%d"),
                    "rank": rank,
                    "horizon": horizon,
                    "forward_return": forward,
                    "benchmark_return": benchmark,
                    "excess_return": forward - benchmark
                    if forward is not None and benchmark is not None
                    else None,
                    "end_date": end_date,
                }
            )
    observation_frame = pd.DataFrame(observations)
    summaries: list[dict[str, Any]] = []
    exposures = (
        stock_basic.set_index("ts_code")
        if stock_basic is not None and "ts_code" in stock_basic.columns
        else None
    )
    for horizon in settings.horizons:
        horizon_frame = observation_frame.loc[observation_frame["horizon"].eq(horizon)].copy()
        returns = pd.to_numeric(horizon_frame["forward_return"], errors="coerce").dropna().tolist()
        excess = pd.to_numeric(horizon_frame["excess_return"], errors="coerce").dropna().tolist()
        selected_codes = horizon_frame.loc[horizon_frame["forward_return"].notna(), "ts_code"]
        market_caps: list[float] = []
        industries: set[str] = set()
        if exposures is not None:
            for code in selected_codes:
                if code not in exposures.index:
                    continue
                row = exposures.loc[code]
                value = pd.to_numeric(row.get("total_mv"), errors="coerce")
                if pd.notna(value):
                    market_caps.append(float(value))
                if pd.notna(row.get("industry")):
                    industries.add(str(row["industry"]))
        summaries.append(
            {
                "horizon": horizon,
                "candidate_count": int(len(horizon_frame)),
                "observed_count": len(returns),
                "coverage": len(returns) / len(horizon_frame) if len(horizon_frame) else 0.0,
                "mean_return": sum(returns) / len(returns) if returns else None,
                "median_return": float(pd.Series(returns).median()) if returns else None,
                "hit_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "mean_excess_return": sum(excess) / len(excess) if excess else None,
                "max_drawdown": _max_drawdown(returns),
                "industry_count": len(industries),
                "market_cap_mean": sum(market_caps) / len(market_caps) if market_caps else None,
            }
        )
    turnover = _turnover(scans, settings.top_n)
    summary = pd.DataFrame(summaries)
    if not summary.empty:
        summary["turnover"] = turnover
    warnings: list[str] = []
    if observation_frame["forward_return"].isna().any():
        warnings.append("forward_window_missing_for_some_candidates")
    status = (
        "PASS"
        if not observation_frame.empty and not warnings
        else "PARTIAL"
        if not observation_frame.empty
        else "EMPTY"
    )
    return EvaluationResult(settings.version, status, summary, observation_frame, tuple(warnings))


def _turnover(scans: pd.DataFrame, top_n: int) -> float | None:
    if "as_of_date" not in scans.columns or "ts_code" not in scans.columns:
        return None
    sets: list[set[str]] = []
    for _, group in scans.groupby("as_of_date", sort=True):
        if "rank" in group.columns:
            group = group.sort_values("rank")
        sets.append(set(group["ts_code"].astype(str).head(top_n)))
    if len(sets) < 2:
        return 0.0
    changes = [
        1.0 - len(left & right) / max(1, len(left | right)) for left, right in zip(sets, sets[1:])
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
    base = set(_top_codes(baseline_frame, top_n))
    rows: list[dict[str, Any]] = []
    for name, frame in variants.items():
        if "ts_code" not in frame.columns:
            raise ValueError(f"ablation variant missing ts_code: {name}")
        current = set(_top_codes(frame, top_n))
        rank_overlap = len(base & current) / max(1, len(base | current))
        rows.append(
            {
                "variant": name,
                "baseline": baseline,
                "candidate_count": len(current),
                "rank_overlap": rank_overlap,
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
