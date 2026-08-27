"""Reproducible feature-ablation and stability analysis from saved artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .score import ABLATION_VARIANT_GROUPS


@dataclass(frozen=True, slots=True)
class StabilityDecisionRule:
    """Pre-committed evidence thresholds; no best-period parameter selection."""

    min_total_observations: int = 100
    min_segment_observations: int = 20
    min_coverage: float = 0.80
    min_years: int = 2
    min_regimes: int = 2
    min_horizons: int = 2
    min_positive_segment_share: float = 2 / 3
    min_median_return_delta: float = 0.0
    max_allowed_segment_regression: float = -0.02
    redundant_rank_overlap: float = 0.95
    redundant_return_tolerance: float = 0.002

    def __post_init__(self) -> None:
        if self.min_total_observations <= 0 or self.min_segment_observations <= 0:
            raise ValueError("observation thresholds must be positive")
        if self.min_years <= 0 or self.min_regimes <= 0 or self.min_horizons <= 0:
            raise ValueError("segment-count thresholds must be positive")
        for name in ("min_coverage", "min_positive_segment_share", "redundant_rank_overlap"):
            value = float(getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.redundant_return_tolerance < 0:
            raise ValueError("redundant_return_tolerance must be non-negative")


@dataclass(frozen=True, slots=True)
class StabilityConfig:
    version: str = "stability-v1"
    baseline: str = "fundamental_only"
    variant_order: tuple[str, ...] = tuple(ABLATION_VARIANT_GROUPS)
    top_n: int = 20
    market_cap_cutoffs: tuple[float, float] = (1_000_000.0, 5_000_000.0)
    market_cap_labels: tuple[str, str, str] = ("small", "mid", "large")
    regime_return_threshold: float = 0.05
    decision_rule: StabilityDecisionRule = field(default_factory=StabilityDecisionRule)

    def __post_init__(self) -> None:
        required = set(ABLATION_VARIANT_GROUPS)
        missing = required - set(self.variant_order)
        if missing:
            names = ",".join(sorted(missing))
            raise ValueError(f"variant_order missing required variants: {names}")
        if self.baseline not in self.variant_order:
            raise ValueError("baseline must be present in variant_order")
        if len(self.variant_order) != len(set(self.variant_order)):
            raise ValueError("variant_order must not contain duplicates")
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        if len(self.market_cap_cutoffs) != 2 or len(self.market_cap_labels) != 3:
            raise ValueError("market-cap bucketing requires two cutoffs and three labels")
        if self.market_cap_cutoffs[0] >= self.market_cap_cutoffs[1]:
            raise ValueError("market_cap_cutoffs must be strictly increasing")
        if self.regime_return_threshold < 0:
            raise ValueError("regime_return_threshold must be non-negative")


@dataclass(frozen=True, slots=True)
class StabilityReport:
    config: StabilityConfig
    source_digest: str
    status: str
    segments: pd.DataFrame
    feature_assessments: pd.DataFrame
    warnings: tuple[str, ...] = ()

    def artifact_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config.version,
            "source_digest": self.source_digest,
            "status": self.status,
            "warnings": list(self.warnings),
            "configuration": {
                "baseline": self.config.baseline,
                "variant_order": list(self.config.variant_order),
                "top_n": self.config.top_n,
                "market_cap_cutoffs": list(self.config.market_cap_cutoffs),
                "market_cap_labels": list(self.config.market_cap_labels),
                "regime_return_threshold": self.config.regime_return_threshold,
                "decision_rule": asdict(self.config.decision_rule),
            },
            "segments": _records(self.segments),
            "feature_assessments": _records(self.feature_assessments),
        }


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    serializable = frame.astype(object).where(frame.notna(), None)
    return serializable.to_dict(orient="records")


def _frame_from_source(source: Any) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source.copy()
    if hasattr(source, "observations"):
        observations = source.observations
        if isinstance(observations, pd.DataFrame):
            return observations.copy()
    if isinstance(source, Mapping):
        payload: Any = source
    else:
        path = Path(source)
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix != ".json":
            raise ValueError(f"unsupported evaluation artifact: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    metadata: dict[str, Any] = {}
    if isinstance(payload, Mapping) and "observations" in payload:
        configuration = payload.get("configuration", {})
        provenance = payload.get("provenance", {})
        if isinstance(configuration, Mapping):
            metadata["_artifact_evaluation_config"] = json.dumps(
                configuration, sort_keys=True, separators=(",", ":")
            )
        if isinstance(provenance, Mapping):
            metadata["_artifact_evaluation_fingerprint"] = provenance.get(
                "evaluation_config_fingerprint"
            )
            metadata["_artifact_snapshot_ids"] = json.dumps(
                sorted(str(value) for value in provenance.get("scan_snapshot_ids", [])),
                separators=(",", ":"),
            )
        payload = payload["observations"]
    if not isinstance(payload, (list, tuple)):
        raise ValueError("evaluation artifact must contain an observations array")
    frame = pd.DataFrame(payload)
    for name, value in metadata.items():
        frame[name] = value
    return frame


def load_evaluation_artifacts(artifacts: Mapping[str, Any]) -> pd.DataFrame:
    """Load immutable evaluation observations and label each score variant."""

    frames: list[pd.DataFrame] = []
    for variant, source in artifacts.items():
        frame = _frame_from_source(source)
        frame["variant"] = str(variant)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _market_cap_bucket(frame: pd.DataFrame, config: StabilityConfig) -> pd.Series:
    if "market_cap_bucket" in frame.columns:
        supplied = frame["market_cap_bucket"].astype("string")
    else:
        supplied = pd.Series(pd.NA, index=frame.index, dtype="string")
    source_name = next((name for name in ("market_cap", "total_mv") if name in frame), None)
    if source_name is None:
        return supplied.fillna("unknown")
    values = pd.to_numeric(frame[source_name], errors="coerce")
    derived = pd.cut(
        values,
        bins=[-float("inf"), *config.market_cap_cutoffs, float("inf")],
        labels=config.market_cap_labels,
        include_lowest=True,
        right=False,
    ).astype("string")
    return supplied.fillna(derived).fillna("unknown")


def _regime(frame: pd.DataFrame, config: StabilityConfig) -> pd.Series:
    if "regime" in frame.columns:
        result = frame["regime"].astype("string")
    else:
        result = pd.Series(pd.NA, index=frame.index, dtype="string")
    if "benchmark_return" not in frame.columns:
        return result.fillna("unknown")
    benchmark = pd.to_numeric(frame["benchmark_return"], errors="coerce")
    threshold = config.regime_return_threshold
    derived = pd.Series("range", index=frame.index, dtype="string")
    derived.loc[benchmark.gt(threshold)] = "bull"
    derived.loc[benchmark.lt(-threshold)] = "bear"
    derived.loc[benchmark.isna()] = pd.NA
    return result.fillna(derived).fillna("unknown")


def _prepare_observations(
    observations: pd.DataFrame, config: StabilityConfig
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    required = {"variant", "ts_code", "as_of_date", "rank", "horizon", "forward_return"}
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"evaluation observations missing columns: {','.join(sorted(missing))}")
    frame = observations.copy()
    missing_variants = set(config.variant_order) - set(frame["variant"].astype(str))
    if missing_variants:
        raise ValueError(
            f"evaluation artifacts missing variants: {','.join(sorted(missing_variants))}"
        )
    frame = frame.loc[frame["variant"].astype(str).isin(config.variant_order)].copy()
    frame["variant"] = frame["variant"].astype(str)
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["_as_of"] = pd.to_datetime(frame["as_of_date"], errors="coerce")
    frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
    frame["horizon"] = pd.to_numeric(frame["horizon"], errors="coerce")
    if frame[["_as_of", "rank", "horizon"]].isna().any().any():
        raise ValueError("evaluation observations contain invalid dates, ranks, or horizons")
    frame["as_of_date"] = frame["_as_of"].dt.strftime("%Y%m%d")
    frame["year"] = frame["_as_of"].dt.year.astype(str)
    frame["horizon"] = frame["horizon"].astype(int)
    frame["forward_return"] = pd.to_numeric(frame["forward_return"], errors="coerce")
    if "excess_return" in frame.columns:
        frame["excess_return"] = pd.to_numeric(frame["excess_return"], errors="coerce")
    frame["regime"] = _regime(frame, config)
    frame["market_cap_bucket"] = _market_cap_bucket(frame, config)
    if "industry" in frame.columns:
        frame["industry"] = frame["industry"].astype("string").fillna("unknown")
    else:
        frame["industry"] = "unknown"
    identity = ["variant", "ts_code", "as_of_date", "horizon"]
    if frame.duplicated(identity).any():
        raise ValueError(
            "evaluation observations contain duplicate variant/security/date/horizon rows"
        )
    warnings: list[str] = []
    if "snapshot_id" in frame.columns:
        snapshot_sets = {
            variant: tuple(sorted(group["snapshot_id"].dropna().astype(str).unique()))
            for variant, group in frame.groupby("variant", sort=True)
        }
        populated = {value for value in snapshot_sets.values() if value}
        if len(populated) > 1:
            raise ValueError("ablation variants do not share the same PIT snapshot ids")
        if len(populated) == 0:
            warnings.append("pit_snapshot_provenance_missing")
    else:
        warnings.append("pit_snapshot_provenance_missing")
    if "_artifact_evaluation_config" in frame.columns:
        configurations = frame["_artifact_evaluation_config"].dropna().astype(str).unique()
        if len(configurations) > 1:
            raise ValueError("ablation variants use different evaluation configurations")
        if len(configurations) == 0:
            warnings.append("evaluation_config_provenance_missing")
    else:
        warnings.append("evaluation_config_provenance_missing")
    if "score_config_fingerprint" in frame.columns:
        per_variant = frame.groupby("variant", sort=True)["score_config_fingerprint"].nunique()
        if per_variant.gt(1).any():
            raise ValueError("one ablation variant contains multiple score configurations")
        known = frame["score_config_fingerprint"].dropna().astype(str)
        if known.empty:
            warnings.append("score_config_provenance_missing")
    else:
        warnings.append("score_config_provenance_missing")
    if frame["regime"].eq("unknown").any():
        warnings.append("regime_unavailable_for_some_observations")
    if frame["market_cap_bucket"].eq("unknown").any():
        warnings.append("market_cap_bucket_unavailable_for_some_observations")
    if frame["industry"].eq("unknown").any():
        warnings.append("industry_unavailable_for_some_observations")
    if frame["forward_return"].isna().any():
        warnings.append("forward_window_missing_for_some_candidates")
    return frame.drop(columns="_as_of"), tuple(warnings)


def _source_digest(frame: pd.DataFrame) -> str:
    columns = sorted(frame.columns)
    ordered = frame.sort_values(
        ["variant", "as_of_date", "horizon", "rank", "ts_code"], kind="stable"
    ).reset_index(drop=True)
    payload = ordered[columns].to_json(orient="split", date_format="iso", default_handler=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rank_overlap(
    current: pd.DataFrame, reference: pd.DataFrame, top_n: int
) -> tuple[float | None, int]:
    keys = ["as_of_date", "horizon"]

    def top_sets(frame: pd.DataFrame) -> dict[tuple[Any, ...], set[str]]:
        result: dict[tuple[Any, ...], set[str]] = {}
        for values, group in frame.groupby(keys, sort=True):
            ordered = group.sort_values(["rank", "ts_code"], kind="stable")
            result[values] = set(ordered["ts_code"].head(top_n))
        return result

    left = top_sets(current)
    right = top_sets(reference)
    comparable = sorted(set(left) & set(right))
    if not comparable:
        return None, 0
    overlaps = [
        len(left[key] & right[key]) / max(1, len(left[key] | right[key]))
        for key in comparable
    ]
    return sum(overlaps) / len(overlaps), len(overlaps)


def _performance_metrics(frame: pd.DataFrame) -> dict[str, float | int | None]:
    returns = frame["forward_return"].dropna()
    sample_count = int(len(frame))
    observed_count = int(len(returns))
    result: dict[str, float | int | None] = {
        "sample_count": sample_count,
        "observed_count": observed_count,
        "coverage": observed_count / sample_count if sample_count else 0.0,
        "mean_return": float(returns.mean()) if observed_count else None,
        "median_return": float(returns.median()) if observed_count else None,
        "hit_rate": float(returns.gt(0).mean()) if observed_count else None,
        "performance_dispersion": float(returns.std(ddof=0))
        if observed_count >= 2
        else None,
        "performance_iqr": float(returns.quantile(0.75) - returns.quantile(0.25))
        if observed_count >= 2
        else None,
        "return_min": float(returns.min()) if observed_count else None,
        "return_max": float(returns.max()) if observed_count else None,
    }
    excess = frame["excess_return"].dropna() if "excess_return" in frame else pd.Series()
    result["mean_excess_return"] = float(excess.mean()) if len(excess) else None
    return result


def _segment_frame(frame: pd.DataFrame, dimension: str, value: Any) -> pd.DataFrame:
    if dimension == "overall":
        return frame
    return frame.loc[frame[dimension].astype(str).eq(str(value))]


def _build_segments(frame: pd.DataFrame, config: StabilityConfig) -> pd.DataFrame:
    dimensions = ("year", "regime", "market_cap_bucket", "industry", "horizon")
    specifications: list[tuple[str, str]] = [("overall", "all")]
    for dimension in dimensions:
        values = sorted(frame[dimension].dropna().astype(str).unique())
        specifications.extend((dimension, value) for value in values)
    rows: list[dict[str, Any]] = []
    for variant in config.variant_order:
        variant_frame = frame.loc[frame["variant"].eq(variant)]
        baseline_frame = frame.loc[frame["variant"].eq(config.baseline)]
        for dimension, value in specifications:
            current = _segment_frame(variant_frame, dimension, value)
            if current.empty:
                continue
            reference = _segment_frame(baseline_frame, dimension, value)
            metrics = _performance_metrics(current)
            overlap, overlap_pairs = _rank_overlap(current, reference, config.top_n)
            baseline_mean = _performance_metrics(reference)["mean_return"]
            mean_return = metrics["mean_return"]
            rows.append(
                {
                    "variant": variant,
                    "dimension": dimension,
                    "segment": value,
                    **metrics,
                    "rank_overlap": overlap,
                    "rank_overlap_pairs": overlap_pairs,
                    "baseline_mean_return": baseline_mean,
                    "mean_return_delta": mean_return - baseline_mean
                    if mean_return is not None and baseline_mean is not None
                    else None,
                }
            )
    return pd.DataFrame(rows)


def _qualified_deltas(
    frame: pd.DataFrame,
    current: str,
    reference: str,
    dimension: str,
    rule: StabilityDecisionRule,
) -> dict[str, float]:
    values = sorted(frame[dimension].dropna().astype(str).unique())
    if dimension == "regime":
        values = [value for value in values if value != "unknown"]
    deltas: dict[str, float] = {}
    for value in values:
        left = _segment_frame(frame.loc[frame["variant"].eq(current)], dimension, value)
        right = _segment_frame(frame.loc[frame["variant"].eq(reference)], dimension, value)
        left_metrics = _performance_metrics(left)
        right_metrics = _performance_metrics(right)
        if (
            left_metrics["observed_count"] < rule.min_segment_observations
            or right_metrics["observed_count"] < rule.min_segment_observations
            or left_metrics["coverage"] < rule.min_coverage
            or right_metrics["coverage"] < rule.min_coverage
        ):
            continue
        left_mean = left_metrics["mean_return"]
        right_mean = right_metrics["mean_return"]
        if left_mean is not None and right_mean is not None:
            deltas[value] = float(left_mean - right_mean)
    return deltas


def _positive_share(values: list[float]) -> float | None:
    return sum(value > 0 for value in values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return float(pd.Series(values).median()) if values else None


def _assessment(
    frame: pd.DataFrame,
    variant: str,
    reference: str,
    feature_group: str,
    config: StabilityConfig,
) -> dict[str, Any]:
    rule = config.decision_rule
    current = frame.loc[frame["variant"].eq(variant)]
    previous = frame.loc[frame["variant"].eq(reference)]
    overall = _performance_metrics(current)
    overlap, overlap_pairs = _rank_overlap(current, previous, config.top_n)
    years = _qualified_deltas(frame, variant, reference, "year", rule)
    regimes = _qualified_deltas(frame, variant, reference, "regime", rule)
    horizons = _qualified_deltas(frame, variant, reference, "horizon", rule)
    year_values = list(years.values())
    regime_values = list(regimes.values())
    horizon_values = list(horizons.values())
    core_deltas = [*year_values, *regime_values, *horizon_values]
    median_delta = _median(core_deltas)
    positive_year_share = _positive_share(year_values)
    positive_regime_share = _positive_share(regime_values)
    positive_horizon_share = _positive_share(horizon_values)
    worst_delta = min(core_deltas) if core_deltas else None
    best_delta = max(core_deltas) if core_deltas else None
    enough_breadth = (
        len(years) >= rule.min_years
        and len(regimes) >= rule.min_regimes
        and len(horizons) >= rule.min_horizons
    )
    shares_pass = all(
        value is not None and value >= rule.min_positive_segment_share
        for value in (positive_year_share, positive_regime_share, positive_horizon_share)
    )
    promotion_eligible = bool(
        overall["observed_count"] >= rule.min_total_observations
        and overall["coverage"] >= rule.min_coverage
        and enough_breadth
        and shares_pass
        and median_delta is not None
        and median_delta > rule.min_median_return_delta
        and worst_delta is not None
        and worst_delta >= rule.max_allowed_segment_regression
    )
    regime_signs = {value > 0 for value in regime_values if value != 0}
    redundant = bool(
        overlap is not None
        and overlap >= rule.redundant_rank_overlap
        and median_delta is not None
        and abs(median_delta) <= rule.redundant_return_tolerance
    )
    if promotion_eligible:
        classification = "stable_positive"
    elif redundant:
        classification = "redundant"
    elif len(regime_signs) > 1:
        classification = "highly_regime_dependent"
    elif enough_breadth and median_delta is not None and median_delta <= 0:
        classification = "ineffective"
    elif core_deltas:
        classification = "unstable"
    else:
        classification = "insufficient_evidence"
    reasons: list[str] = []
    if overall["observed_count"] < rule.min_total_observations:
        reasons.append("insufficient_total_observations")
    if overall["coverage"] < rule.min_coverage:
        reasons.append("coverage_below_threshold")
    if not enough_breadth:
        reasons.append("insufficient_year_regime_or_horizon_breadth")
    if not shares_pass:
        reasons.append("positive_effect_not_broadly_repeated")
    if worst_delta is not None and worst_delta < rule.max_allowed_segment_regression:
        reasons.append("material_segment_regression")
    if not reasons and promotion_eligible:
        reasons.append("precommitted_cross_segment_rule_passed")
    single_best_only = bool(best_delta is not None and best_delta > 0 and not promotion_eligible)
    return {
        "variant": variant,
        "reference_variant": reference,
        "feature_group": feature_group,
        "classification": classification,
        "promotion_eligible": promotion_eligible,
        "single_best_result_only": single_best_only,
        "sample_count": overall["sample_count"],
        "observed_count": overall["observed_count"],
        "coverage": overall["coverage"],
        "rank_overlap": overlap,
        "rank_overlap_pairs": overlap_pairs,
        "years_supported": len(years),
        "regimes_supported": len(regimes),
        "horizons_supported": len(horizons),
        "positive_year_share": positive_year_share,
        "positive_regime_share": positive_regime_share,
        "positive_horizon_share": positive_horizon_share,
        "median_segment_return_delta": median_delta,
        "segment_delta_dispersion": float(pd.Series(core_deltas).std(ddof=0))
        if len(core_deltas) >= 2
        else None,
        "best_segment_return_delta": best_delta,
        "worst_segment_return_delta": worst_delta,
        "decision_reasons": "|".join(reasons),
    }


def _build_assessments(frame: pd.DataFrame, config: StabilityConfig) -> pd.DataFrame:
    feature_names = {
        "quality_added": "quality",
        "attention_added": "attention",
        "expectation_added": "expectation",
    }
    rows: list[dict[str, Any]] = []
    for index, variant in enumerate(config.variant_order):
        if index == 0:
            continue
        reference = config.variant_order[index - 1]
        rows.append(
            _assessment(
                frame,
                variant,
                reference,
                feature_names.get(variant, variant),
                config,
            )
        )
    return pd.DataFrame(rows)


def analyze_feature_stability(
    artifacts: Mapping[str, Any] | pd.DataFrame,
    *,
    config: StabilityConfig | None = None,
) -> StabilityReport:
    """Analyze saved variant evaluations without rerunning replay or forward pricing."""

    settings = config or StabilityConfig()
    observations = (
        artifacts.copy()
        if isinstance(artifacts, pd.DataFrame)
        else load_evaluation_artifacts(artifacts)
    )
    if observations.empty:
        return StabilityReport(
            settings,
            hashlib.sha256(b"").hexdigest(),
            "EMPTY",
            pd.DataFrame(),
            pd.DataFrame(),
            ("missing_evaluation_observations",),
        )
    prepared, warnings = _prepare_observations(observations, settings)
    segments = _build_segments(prepared, settings)
    assessments = _build_assessments(prepared, settings)
    status = "PASS" if not warnings else "PARTIAL"
    return StabilityReport(
        settings,
        _source_digest(prepared),
        status,
        segments,
        assessments,
        warnings,
    )


def write_stability_report(report: StabilityReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.artifact_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return destination
