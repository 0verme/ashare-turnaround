from __future__ import annotations

import json

import pandas as pd
import pytest

from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.evaluation import run_ablation
from ashare_turnaround.scanner.score import (
    ABLATION_VARIANT_GROUPS,
    ScoreConfig,
    ablation_score_configs,
    rank_scores,
    score_feature_vector,
)
from ashare_turnaround.scanner.stability import (
    StabilityConfig,
    StabilityDecisionRule,
    analyze_feature_stability,
    write_stability_report,
)


def _complete_vector() -> FeatureVector:
    vector = FeatureVector(ts_code="600000.SH", as_of_date="20250630")
    vector.values.update(
        {
            "revenue_yoy": 0.2,
            "net_profit_yoy": 0.3,
            "operating_profit_yoy": 0.25,
            "gross_margin": 0.3,
            "operating_margin": 0.2,
            "net_margin": 0.1,
            "yoy_acceleration": 2.0,
            "qoq_acceleration": 1.0,
            "consecutive_improvement": 3,
            "sign_transition": True,
            "margin_inflection": 0.02,
            "quality_score": 10.0,
            "attention_score": 20.0,
            "expectation_score": 30.0,
        }
    )
    vector.risk_flags.extend(
        ["profit_dominated_by_non_recurring_items", "already_repriced_or_crowded"]
    )
    vector.rejected_reasons.append("profit_dominated_by_non_recurring_items")
    return vector


def test_score_feature_groups_are_independently_switchable() -> None:
    vector = _complete_vector()
    default = score_feature_vector(vector)
    fundamental_only = score_feature_vector(
        vector,
        config=ScoreConfig(enabled_groups=("fundamental", "trend")),
    )
    attention_only = score_feature_vector(
        vector,
        config=ScoreConfig(enabled_groups=("attention",)),
    )

    assert default.rejected is True
    assert set(default.penalties) == {
        "profit_dominated_by_non_recurring_items",
        "already_repriced_or_crowded",
    }
    assert fundamental_only.enabled_groups == ("fundamental", "trend")
    assert fundamental_only.weights["quality_score"] == 0.0
    assert fundamental_only.weights["attention_score"] == 0.0
    assert fundamental_only.weights["expectation_score"] == 0.0
    assert fundamental_only.penalties == {}
    assert fundamental_only.rejected is False
    assert attention_only.turnaround_score == 20.0
    assert set(attention_only.components) == set(default.components)


def test_required_ablation_configs_are_cumulative_and_versioned() -> None:
    variants = ablation_score_configs()

    assert tuple(variants) == tuple(ABLATION_VARIANT_GROUPS)
    assert variants["fundamental_only"].enabled_groups == ("fundamental", "trend")
    assert variants["quality_added"].enabled_groups[-1] == "quality"
    assert variants["attention_added"].enabled_groups[-1] == "attention"
    assert variants["expectation_added"].enabled_groups[-1] == "expectation"
    assert variants["attention_added"].version == "score-v2/attention_added"
    assert variants["attention_added"].trend_contract_version == "turnaround-trend-v2"
    with pytest.raises(ValueError, match="unknown feature groups"):
        ScoreConfig(enabled_groups=("fundamental", "sentiment"))


def test_top_n_never_promotes_a_higher_scoring_rejected_candidate() -> None:
    rejected_vector = _complete_vector()
    accepted_vector = _complete_vector()
    accepted_vector.ts_code = "600001.SH"
    accepted_vector.risk_flags.clear()
    accepted_vector.rejected_reasons.clear()
    accepted_vector.values["quality_score"] = 1.0

    ranked = rank_scores(
        [score_feature_vector(rejected_vector), score_feature_vector(accepted_vector)],
        top_n=1,
    )

    assert ranked["ts_code"].tolist() == ["600001.SH"]
    assert ranked["rejected"].tolist() == [False]


def _evaluation_observations(variant: str) -> pd.DataFrame:
    dates = (
        ("20240329", "bull"),
        ("20240628", "range"),
        ("20240930", "bear"),
        ("20250331", "bull"),
        ("20250630", "range"),
        ("20250930", "bear"),
    )
    codes = {
        "fundamental_only": ("A.SH", "B.SH"),
        "quality_added": ("A.SH", "B.SH"),
        "attention_added": ("B.SH", "C.SH"),
        "expectation_added": ("C.SH", "D.SH"),
    }[variant]
    base_return = {"bull": 0.08, "range": 0.01, "bear": -0.06}
    attention_delta = {"bull": 0.05, "range": 0.02, "bear": -0.03}
    rows: list[dict[str, object]] = []
    for as_of_date, regime in dates:
        for horizon in (20, 60):
            for rank, code in enumerate(codes, start=1):
                value = base_return[regime] + (0.005 if horizon == 60 else 0.0)
                if variant in {"attention_added", "expectation_added"}:
                    value += attention_delta[regime]
                if variant == "expectation_added":
                    value += 0.02
                rows.append(
                    {
                        "ts_code": code,
                        "as_of_date": as_of_date,
                        "rank": rank,
                        "snapshot_id": f"pit-{as_of_date}",
                        "score_config_fingerprint": f"score-{variant}",
                        "_artifact_evaluation_config": '{"horizons":[20,60]}',
                        "horizon": horizon,
                        "forward_return": value,
                        "benchmark_return": base_return[regime],
                        "excess_return": value - base_return[regime],
                        "regime": regime,
                        "market_cap_bucket": "small" if rank == 1 else "large",
                        "industry": "technology" if rank == 1 else "financials",
                    }
                )
    return pd.DataFrame(rows)


def _stability_config() -> StabilityConfig:
    return StabilityConfig(
        top_n=2,
        decision_rule=StabilityDecisionRule(
            min_total_observations=20,
            min_segment_observations=2,
            min_coverage=0.9,
            min_years=2,
            min_regimes=3,
            min_horizons=2,
            min_positive_segment_share=2 / 3,
            max_allowed_segment_regression=-0.02,
        ),
    )


def test_stability_report_segments_and_classifies_incremental_features(tmp_path) -> None:
    artifacts = {
        name: _evaluation_observations(name)
        for name in ABLATION_VARIANT_GROUPS
    }

    report = analyze_feature_stability(artifacts, config=_stability_config())

    assert report.status == "PASS"
    assert set(report.segments["dimension"]) == {
        "overall",
        "year",
        "regime",
        "market_cap_bucket",
        "industry",
        "horizon",
    }
    overall = report.segments.loc[
        report.segments["dimension"].eq("overall")
        & report.segments["variant"].eq("expectation_added")
    ].iloc[0]
    assert overall["sample_count"] == 24
    assert overall["coverage"] == 1.0
    assert overall["performance_dispersion"] > 0
    assert 0 <= overall["rank_overlap"] <= 1

    assessments = report.feature_assessments.set_index("feature_group")
    assert assessments.loc["quality", "classification"] == "redundant"
    assert assessments.loc["attention", "classification"] == "highly_regime_dependent"
    assert bool(assessments.loc["attention", "promotion_eligible"]) is False
    assert bool(assessments.loc["attention", "single_best_result_only"]) is True
    assert assessments.loc["expectation", "classification"] == "stable_positive"
    assert bool(assessments.loc["expectation", "promotion_eligible"]) is True
    assert assessments.loc["expectation", "years_supported"] == 2
    assert assessments.loc["expectation", "regimes_supported"] == 3
    assert assessments.loc["expectation", "horizons_supported"] == 2

    repeated = analyze_feature_stability(
        dict(reversed(tuple(artifacts.items()))), config=_stability_config()
    )
    assert repeated.source_digest == report.source_digest

    path = write_stability_report(report, tmp_path / "stability.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_digest"] == report.source_digest
    assert payload["configuration"]["decision_rule"]["min_years"] == 2
    assert len(payload["segments"]) == len(report.segments)


def test_stability_can_replay_saved_evaluation_json_and_derive_segments(tmp_path) -> None:
    paths = {}
    for variant in ABLATION_VARIANT_GROUPS:
        frame = _evaluation_observations(variant).drop(
            columns=["regime", "market_cap_bucket"]
        )
        frame["total_mv"] = frame["rank"].map({1: 500_000.0, 2: 6_000_000.0})
        path = tmp_path / f"{variant}.json"
        path.write_text(
            json.dumps({"observations": frame.to_dict(orient="records")}),
            encoding="utf-8",
        )
        paths[variant] = path

    report = analyze_feature_stability(paths, config=_stability_config())

    assert report.status == "PASS"
    regime_segments = set(
        report.segments.loc[report.segments["dimension"].eq("regime"), "segment"]
    )
    cap_segments = set(
        report.segments.loc[
            report.segments["dimension"].eq("market_cap_bucket"), "segment"
        ]
    )
    assert regime_segments == {"bull", "bear", "range"}
    assert cap_segments == {"small", "large"}


def test_stability_requires_all_precommitted_variants() -> None:
    with pytest.raises(ValueError, match="missing variants"):
        analyze_feature_stability(
            {"fundamental_only": _evaluation_observations("fundamental_only")},
            config=_stability_config(),
        )


def test_stability_rejects_variants_from_different_pit_snapshots() -> None:
    artifacts = {
        name: _evaluation_observations(name) for name in ABLATION_VARIANT_GROUPS
    }
    artifacts["attention_added"]["snapshot_id"] = "different-snapshot"

    with pytest.raises(ValueError, match="same PIT snapshot ids"):
        analyze_feature_stability(artifacts, config=_stability_config())


def test_quick_rank_ablation_compares_top_n_within_each_snapshot() -> None:
    baseline = pd.DataFrame(
        {
            "as_of_date": ["20240101", "20240101", "20240201", "20240201"],
            "ts_code": ["A", "B", "C", "D"],
            "rank": [1, 2, 1, 2],
        }
    )
    variant = pd.DataFrame(
        {
            "as_of_date": ["20240101", "20240101", "20240201", "20240201"],
            "ts_code": ["A", "X", "C", "Y"],
            "rank": [1, 2, 1, 2],
        }
    )

    report = run_ablation(
        {"fundamental_only": baseline, "attention_added": variant},
        top_n=2,
    ).set_index("variant")

    assert report.loc["attention_added", "comparison_count"] == 2
    assert report.loc["attention_added", "rank_overlap"] == 1 / 3
