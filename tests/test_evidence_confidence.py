from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from ashare_turnaround.features import compute_attention_features
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.evidence import (
    EVIDENCE_CONFIDENCE_CONTRACT_VERSION,
    FEATURE_GROUP_ORDER,
    FEATURE_GROUP_REGISTRY,
    EvidenceConfidenceConfig,
    assess_evidence_coverage,
)
from ashare_turnaround.scanner.replay import ReplayConfig, ReplayResult
from ashare_turnaround.scanner.report import candidate_report, candidate_report_markdown
from ashare_turnaround.scanner.score import ScoreConfig, rank_scores, score_feature_vector

AS_OF = "20250630"


_FUNDAMENTAL = (
    "revenue_yoy",
    "net_profit_yoy",
    "operating_profit_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
)
_TREND = (
    "yoy_acceleration",
    "qoq_acceleration",
    "consecutive_improvement",
    "sign_transition",
    "margin_inflection",
)
_ATTENTION = (
    "turnover_percentile",
    "amount_percentile",
    "abnormal_volume",
    "attention_score",
)
_CROWDING = (
    "repricing_20d",
    "repricing_60d",
    "high_proximity",
    "volume_spike_penalty",
    "turnover_spike_penalty",
    "expectation_score",
)


def _add(vector: FeatureVector, name: str, value: object, **kwargs: object) -> None:
    vector.add(name, value, **kwargs)


def _complete_vector(code: str = "600000.SH") -> FeatureVector:
    vector = FeatureVector(ts_code=code, as_of_date=AS_OF)
    for name in _FUNDAMENTAL:
        _add(vector, name, 2.0 if name.endswith("yoy") else 0.5)
    for name in _TREND:
        _add(vector, name, "NEGATIVE_TO_POSITIVE" if name == "sign_transition" else 2.0)
    _add(vector, "quality_score", 100.0)
    _add(vector, "quality_gate_status", "pass")
    for name in _ATTENTION:
        _add(vector, name, 0.1 if name != "attention_score" else 95.0)
    for name in _CROWDING:
        _add(vector, name, 0.0 if name != "expectation_score" else 95.0)
    return vector


def test_registry_is_explicit_and_has_all_five_groups() -> None:
    assert tuple(FEATURE_GROUP_REGISTRY) == FEATURE_GROUP_ORDER
    for name in FEATURE_GROUP_ORDER:
        spec = FEATURE_GROUP_REGISTRY[name]
        assert spec.required_fields
        assert set(spec.required_fields).isdisjoint(spec.optional_fields)
    assert "low_attention_v2_score" in FEATURE_GROUP_REGISTRY["attention"].optional_fields
    assert "expectation_score" in FEATURE_GROUP_REGISTRY["expectation_crowding"].required_fields


def test_complete_evidence_is_high_and_eligible() -> None:
    score = score_feature_vector(_complete_vector())

    assert score.evidence_confidence_contract_version == EVIDENCE_CONFIDENCE_CONTRACT_VERSION
    assert score.turnaround_score is not None and score.turnaround_score > 90.0
    assert score.evidence_coverage == pytest.approx(1.0)
    assert all(value == pytest.approx(1.0) for value in score.group_coverage.values())
    assert score.confidence == "HIGH"
    assert score.unknown_groups == ()
    assert score.ranking_eligible is True
    assert score.eligibility_reason == "eligible"
    assert score.score_is_partial is False
    payload = score.as_dict()
    assert payload["fundamental_coverage"] == pytest.approx(1.0)
    assert payload["expectation_crowding_coverage"] == pytest.approx(1.0)


def test_high_diagnostic_score_with_attention_and_crowding_unknown_is_gated() -> None:
    vector = _complete_vector("600001.SH")
    for name in (*_ATTENTION, *_CROWDING):
        vector.values.pop(name)
        vector.evidence.pop(name)

    score = score_feature_vector(vector)

    assert score.turnaround_score is not None and score.turnaround_score > 80.0
    assert score.evidence_coverage < 0.60
    assert score.confidence == "INSUFFICIENT"
    assert score.unknown_groups == ("attention", "expectation_crowding")
    assert score.ranking_eligible is False
    assert "critical_group_unknown:attention,expectation_crowding" == score.eligibility_reason
    assert score.observed_weight == pytest.approx(0.70)
    assert score.missing_weight == pytest.approx(0.30)
    assert score.score_is_partial is True


def test_low_diagnostic_score_with_complete_evidence_remains_eligible() -> None:
    score = replace(score_feature_vector(_complete_vector()), turnaround_score=10.0)

    assert score.turnaround_score == 10.0
    assert score.evidence_coverage == pytest.approx(1.0)
    assert score.confidence == "HIGH"
    assert score.ranking_eligible is True


def test_missing_optional_field_does_not_make_group_unknown() -> None:
    vector = _complete_vector()
    vector.add("adjusted_profit", None, status="unknown", reason="missing_current_period")

    score = score_feature_vector(vector)
    quality = score.coverage["quality"]

    assert score.evidence_coverage == pytest.approx(1.0)
    assert score.group_status["quality"] == "COMPLETE"
    assert "adjusted_profit" in quality["optional_missing_fields"]
    assert score.confidence == "HIGH"


def test_non_critical_quality_group_can_be_configured_without_changing_score() -> None:
    vector = _complete_vector()
    for name in ("quality_score", "quality_gate_status"):
        vector.values.pop(name)
        vector.evidence.pop(name)

    score = score_feature_vector(vector)
    permissive = score_feature_vector(
        vector,
        evidence_config=EvidenceConfidenceConfig(critical_groups=(
            "fundamental",
            "trend",
            "attention",
            "expectation_crowding",
        )),
    )

    assert score.group_status["quality"] == "UNKNOWN"
    assert score.unknown_groups == ("quality",)
    assert score.confidence == "HIGH"
    assert score.ranking_eligible is True
    assert permissive.turnaround_score == pytest.approx(score.turnaround_score)


@pytest.mark.parametrize(
    ("status", "reason", "category"),
    [
        ("stale", "stale_market_data", "invalid"),
        ("unsupported", "unsupported_pit_field", "unsupported"),
        ("insufficient_history", "insufficient_trend_history", "invalid"),
        ("insufficient_data", "insufficient_data", "invalid"),
        ("discontinuous", "discontinuous_periods", "invalid"),
        ("unknown", "invalid_denominator", "invalid"),
        ("unknown", "negative_denominator", "invalid"),
    ],
)
def test_non_valid_statuses_reduce_coverage(
    status: str, reason: str, category: str
) -> None:
    vector = _complete_vector()
    vector.add("revenue_yoy", 0.2, status=status, reason=reason)

    score = score_feature_vector(vector)
    detail = next(
        item
        for item in score.coverage["fundamental"]["field_statuses"]
        if item["field"] == "revenue_yoy"
    )

    assert score.evidence_coverage < 1.0
    assert detail["valid"] is False
    assert detail["category"] == category
    if category == "unsupported":
        assert "revenue_yoy" in score.unsupported_fields
    else:
        assert "revenue_yoy" in score.invalid_fields


def test_pit_warning_is_not_valid_evidence() -> None:
    vector = _complete_vector()
    vector.add(
        "revenue_yoy",
        0.2,
        status="known",
        metadata={"pit_warning": "availability_not_proven"},
    )

    score = score_feature_vector(vector)

    assert score.evidence_coverage < 1.0
    assert "revenue_yoy" in score.invalid_fields
    assert score.coverage["fundamental"]["field_statuses"][0]["valid"] is False


def test_v1_attention_does_not_fill_missing_proxy_with_neutral_value() -> None:
    dates = pd.date_range("20250626", periods=3, freq="B")
    frame = pd.DataFrame(
        {
            "ts_code": ["600003.SH"] * 3,
            "trade_date": dates.strftime("%Y%m%d"),
            "close": [10.0, 10.1, 10.2],
            "vol": [100.0, 110.0, 120.0],
            "turnover_rate": [1.0, 1.1, None],
            "amount": [1000.0, 1100.0, 1200.0],
        }
    )

    vector = compute_attention_features(frame, "600003.SH", "20250630")

    assert vector.values["attention_score"] is None
    assert vector.evidence["attention_score"].status == "unknown"
    assert vector.evidence["attention_score"].reason == "insufficient attention proxies"


def test_missing_value_and_unreported_value_are_not_valid() -> None:
    vector = _complete_vector()
    vector.add("net_profit_yoy", None, status="unknown", reason="missing_value")
    vector.values.pop("operating_profit_yoy")
    vector.evidence.pop("operating_profit_yoy")

    score = score_feature_vector(vector)

    assert score.evidence_coverage < 1.0
    assert "net_profit_yoy" in score.missing_fields
    assert "operating_profit_yoy" in score.missing_fields
    assert score.group_status["fundamental"] == "PARTIAL"


def test_rank_top_n_excludes_ineligible_high_score_but_full_diagnostics_retains_it() -> None:
    eligible = score_feature_vector(_complete_vector("B.SH"))
    ineligible_vector = _complete_vector("A.SH")
    for name in (*_ATTENTION, *_CROWDING):
        ineligible_vector.values.pop(name)
        ineligible_vector.evidence.pop(name)
    ineligible = score_feature_vector(ineligible_vector)
    # Keep the ranking counterexample independent from score calibration: the
    # high diagnostic score is deliberately retained, but eligibility is not.
    ineligible = replace(ineligible, turnaround_score=90.0)
    eligible = replace(eligible, turnaround_score=75.0)

    formal = rank_scores([ineligible, eligible], top_n=1)
    diagnostic = rank_scores([ineligible, eligible], top_n=None)

    assert formal["ts_code"].tolist() == ["B.SH"]
    assert set(diagnostic["ts_code"]) == {"A.SH", "B.SH"}
    assert not bool(
        diagnostic.loc[diagnostic["ts_code"].eq("A.SH"), "ranking_eligible"].item()
    )


def test_rejected_candidate_is_not_eligible_even_with_complete_evidence() -> None:
    vector = _complete_vector()
    vector.rejected_reasons.append("profit_dominated_by_non_recurring_items")
    score = score_feature_vector(vector)

    assert score.rejected is True
    assert score.evidence_coverage == pytest.approx(1.0)
    assert score.confidence == "HIGH"
    assert score.ranking_eligible is False
    assert score.eligibility_reason.startswith("rejected:")


def test_coverage_assessment_is_deterministic_and_policy_is_versioned() -> None:
    vector = _complete_vector()
    first = assess_evidence_coverage(vector, turnaround_score=80.0)
    second = assess_evidence_coverage(vector, turnaround_score=80.0)

    assert first.as_dict() == second.as_dict()
    assert first.policy["version"] == EVIDENCE_CONFIDENCE_CONTRACT_VERSION
    assert first.policy["coverage_unit"] == "ratio"
    assert list(first.group_coverage) == list(FEATURE_GROUP_ORDER)


def test_report_and_json_surface_use_the_same_gate_facts() -> None:
    vector = _complete_vector("600002.SH")
    for name in (*_ATTENTION, *_CROWDING):
        vector.values.pop(name)
        vector.evidence.pop(name)
    score = score_feature_vector(vector)
    config = ReplayConfig(top_n=5)
    formal = rank_scores([score], top_n=5)
    diagnostic = rank_scores([score], top_n=None)
    result = ReplayResult(
        as_of_date=AS_OF,
        snapshot_id="snapshot",
        universe_version="universe-v1",
        feature_version="features-v1",
        score_version=score.score_version,
        config_fingerprint=config.fingerprint,
        run_id="run",
        configuration=config.declared(),
        input_rows={},
        status="PASS",
        ranked=formal,
        vectors=(vector,),
        scores=(score,),
        diagnostic_ranked=diagnostic,
    )

    report = candidate_report(result, vector.ts_code)
    markdown = candidate_report_markdown(report)

    assert report["turnaround_score"] == report["score"]["turnaround_score"]
    assert report["evidence_coverage"] == report["score"]["evidence_coverage"]
    assert report["confidence"] == report["score"]["confidence"]
    assert report["unknown_groups"] == ["attention", "expectation_crowding"]
    assert report["ranking_eligible"] is False
    assert "Evidence Coverage: `" in markdown
    assert "Confidence: `INSUFFICIENT`" in markdown
    assert "Ranking Eligible: `NO`" in markdown
    assert "attention, expectation_crowding" in markdown
    assert "neutral" not in markdown.lower()
    artifact = result.artifact_dict()
    assert artifact["ranked"] == []
    assert len(artifact["diagnostic_ranked"]) == 1
    assert artifact["scores"][0]["ranking_eligible"] is False
    assert artifact["scores"][0]["evidence_confidence_contract_version"] == (
        EVIDENCE_CONFIDENCE_CONTRACT_VERSION
    )


def test_score_weights_remain_frozen_and_contract_is_additive() -> None:
    settings = ScoreConfig()

    assert settings.weights == {
        "fundamental_score": 0.30,
        "trend_score": 0.20,
        "quality_score": 0.20,
        "attention_score": 0.15,
        "expectation_score": 0.15,
    }
    assert settings.enabled_groups == (
        "fundamental",
        "trend",
        "quality",
        "attention",
        "expectation",
    )
    assert ScoreConfig(enabled_groups=("expectation_crowding",)).enabled_groups == (
        "expectation",
    )
