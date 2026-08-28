"""Deterministic, provenance-first candidate report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .replay import ReplayResult

_CROWDING_FEATURE_NAMES = frozenset(
    {
        "stock_return_20d",
        "benchmark_return_20d",
        "excess_return_20d",
        "recent_return_20d",
        "recent_excess_return",
        "stock_return_60d",
        "benchmark_return_60d",
        "excess_return_60d",
        "momentum_60d",
        "distance_to_52w_high",
        "distance_52w_high",
        "high_52w",
        "current_price",
        "high_52w_window_start",
        "high_52w_window_end",
        "high_52w_obs_count",
        "volume_spike",
        "turnover_spike",
        "valuation_percentile",
        "repricing_20d",
        "repricing_60d",
        "high_proximity",
        "volume_spike_penalty",
        "turnover_spike_penalty",
        "valuation_penalty",
        "disclosure_reaction_excess",
        "disclosure_availability_date",
        "disclosure_event_date",
        "disclosure_reaction_window_start",
        "disclosure_reaction_window_end",
        "disclosure_reaction_penalty",
        "crowding_penalty",
        "expectation_score",
    }
)
_FUNDAMENTAL_FEATURE_NAMES = frozenset(
    {
        "revenue_level",
        "revenue_yoy",
        "net_profit_level",
        "net_profit_yoy",
        "operating_profit_yoy",
        "gross_margin",
        "gross_margin_yoy_change",
        "operating_margin",
        "operating_margin_yoy_change",
        "net_margin",
        "net_margin_yoy_change",
        "operating_cash_flow",
        "operating_cash_flow_change",
        "cfo_to_profit",
        "roe",
        "roa",
        "inventory_yoy",
        "receivables_yoy",
        "asset_turnover",
        "fundamental_data_status",
    }
)


def candidate_report(result: ReplayResult, ts_code: str) -> dict[str, Any]:
    vectors = {vector.ts_code: vector for vector in result.vectors}
    scores = {score.ts_code: score for score in result.scores}
    vector = vectors.get(ts_code)
    score = scores.get(ts_code)
    if vector is None or score is None:
        raise KeyError(f"candidate not found in replay: {ts_code}")
    evidence = {key: value.as_dict() for key, value in vector.evidence.items()}
    fundamental_names = set(evidence).intersection(_FUNDAMENTAL_FEATURE_NAMES)
    crowding_names = set(evidence).intersection(_CROWDING_FEATURE_NAMES)
    return {
        "metadata": result.metadata(),
        "comparable_period_contract_version": result.comparable_period_contract_version,
        "trend_contract_version": result.trend_contract_version,
        "expectation_crowding_contract_version": result.expectation_crowding_contract_version,
        "benchmark": dict(result.benchmark_metadata),
        "report_metadata": {
            "attention_contract_version": result.attention_contract_version,
            "low_attention_version": result.attention_contract_version,
            "attention_feature_fields": list(result.attention_feature_fields),
            "trend_contract_version": result.trend_contract_version,
            "expectation_crowding_contract_version": (
                result.expectation_crowding_contract_version
            ),
            "benchmark_id": result.benchmark_metadata.get("benchmark_id"),
            "benchmark_contract_version": result.benchmark_metadata.get(
                "benchmark_contract_version", result.benchmark_metadata.get("version")
            ),
            "evidence_confidence_contract_version": (
                score.evidence_confidence_contract_version
            ),
            "feature_group_registry_version": score.feature_group_registry_version,
            "critical_groups": list(
                score.evidence_confidence_policy.get("critical_groups", ())
            ),
            "research_only": True,
        },
        "ts_code": ts_code,
        "evidence_confidence_contract_version": (
            score.evidence_confidence_contract_version
        ),
        "feature_group_registry_version": score.feature_group_registry_version,
        "selected": score.ranking_eligible,
        "turnaround_score": score.turnaround_score,
        "evidence_coverage": score.evidence_coverage,
        "group_coverage": dict(score.group_coverage),
        **{
            f"{group_name}_coverage": coverage
            for group_name, coverage in score.group_coverage.items()
        },
        "group_status": dict(score.group_status),
        "confidence": score.confidence,
        "unknown_groups": list(score.unknown_groups),
        "incomplete_groups": list(score.incomplete_groups),
        "missing_fields": list(score.missing_fields),
        "invalid_fields": list(score.invalid_fields),
        "unsupported_fields": list(score.unsupported_fields),
        "ranking_eligible": score.ranking_eligible,
        "eligibility_reason": score.eligibility_reason,
        "score_is_partial": score.score_is_partial,
        "observed_weight": score.observed_weight,
        "missing_weight": score.missing_weight,
        "score": score.as_dict(),
        "score_input_metadata": dict(score.input_metadata),
        "features": dict(vector.values),
        "feature_metadata": dict(vector.metadata),
        "evidence": evidence,
        "attention_v2_evidence": dict(vector.metadata.get("low_attention_v2_evidence", {})),
        "fundamental_evidence": {
            key: evidence[key] for key in sorted(fundamental_names)
        },
        "crowding_evidence": {key: evidence[key] for key in sorted(crowding_names)},
        "expectation_penalties": {
            key: evidence[key]
            for key in sorted(evidence)
            if "penalty" in key or key.startswith("repricing_") or key == "high_proximity"
        },
        "risk_flags": list(vector.risk_flags),
        "rejected_reasons": list(vector.rejected_reasons),
    }


def candidate_report_markdown(report: dict[str, Any]) -> str:
    score = report["score"]
    contract_version = report["metadata"].get("comparable_period_contract_version", "unknown")
    trend_contract_version = report["metadata"].get("trend_contract_version", "unknown")
    crowding_version = report["metadata"].get(
        "expectation_crowding_contract_version", "unknown"
    )
    benchmark_id = report["metadata"].get("benchmark_id", "unknown")
    unknown_groups = ", ".join(report.get("unknown_groups", ())) or "none"
    missing_fields = ", ".join(report.get("missing_fields", ())) or "none"
    invalid_fields = ", ".join(report.get("invalid_fields", ())) or "none"
    unsupported_fields = ", ".join(report.get("unsupported_fields", ())) or "none"
    lines = [
        f"# Turnaround candidate report: {report['ts_code']}",
        "",
        f"- As of: `{report['metadata']['as_of_date']}`",
        f"- Selected: `{report['selected']}`",
        f"- Turnaround Score: `{report['turnaround_score']}`",
        f"- Evidence Coverage: `{report['evidence_coverage']:.2%}`",
        f"- Confidence: `{report['confidence']}`",
        f"- Ranking Eligible: `{'YES' if report['ranking_eligible'] else 'NO'}`",
        f"- Eligibility Reason: `{report['eligibility_reason']}`",
        f"- Unknown Groups: `{unknown_groups}`",
        f"- Missing Required Fields: `{missing_fields}`",
        f"- Invalid Required Fields: `{invalid_fields}`",
        f"- Unsupported Required Fields: `{unsupported_fields}`",
        f"- Score Is Partial: `{report['score_is_partial']}`",
        f"- Observed Weight: `{report['observed_weight']}`; "
        f"Missing Weight: `{report['missing_weight']}`",
        f"- Score version: `{score['score_version']}`",
        f"- Evidence-confidence contract: `{score['evidence_confidence_contract_version']}`",
        f"- Low-attention contract: `{report['report_metadata']['attention_contract_version']}`",
        f"- Comparable-period contract: `{contract_version}`",
        f"- Trend contract: `{trend_contract_version}`",
        f"- Expectation/crowding contract: `{crowding_version}`",
        f"- Primary benchmark: `{benchmark_id}`",
        f"- Risk flags: `{', '.join(report['risk_flags']) or 'none'}`",
        f"- Rejected reasons: `{', '.join(report['rejected_reasons']) or 'none'}`",
        "",
        "## Evidence coverage by group",
        "",
        "| Group | Coverage | Status | Critical |",
        "| --- | ---: | --- | --- |",
        *[
            "| {group} | {coverage:.2%} | {status} | {critical} |".format(
                group=group,
                coverage=report["group_coverage"].get(group, 0.0),
                status=report["group_status"].get(group, "UNKNOWN"),
                critical=(
                    "yes"
                    if report["score"]["coverage"].get(group, {}).get("critical", False)
                    else "no"
                ),
            )
            for group in report["group_coverage"]
        ],
        "",
        "## Score breakdown",
        "",
        "| Component | Score | Weight |",
        "| --- | ---: | ---: |",
    ]
    for name in sorted(score["components"]):
        lines.append(f"| {name} | {score['components'][name]} | {score['weights'][name]} |")
    lines.extend(
        [
            "",
            "## Evidence and provenance",
            "",
            "| Feature | Value | Dataset | Field | Periods | Status |",
            "| --- | --- | --- | --- | --- | --- |",
            *_evidence_lines_from_dict(report["evidence"]),
            "",
            "This is a deterministic research audit artifact, not investment advice.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evidence_lines_from_dict(evidence_map: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for name in sorted(evidence_map):
        evidence = evidence_map[name]
        value = "unknown" if evidence.get("value") is None else str(evidence.get("value"))
        source = ",".join(evidence.get("source_datasets", [])) or "unknown-source"
        fields = ",".join(evidence.get("source_fields", [])) or "unknown-field"
        periods = ",".join(evidence.get("periods", [])) or "-"
        status = str(evidence.get("status", "unknown"))
        lines.append(f"| {name} | {value} | {source} | {fields} | {periods} | {status} |")
    return lines


def write_candidate_reports(
    result: ReplayResult,
    directory: str | Path,
    *,
    codes: tuple[str, ...] | None = None,
) -> tuple[Path, Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    if codes is not None:
        selected_codes = codes
    elif result.ranked.empty:
        selected_codes = ()
    else:
        selected_codes = tuple(result.ranked.head(20)["ts_code"].astype(str))
    reports = [candidate_report(result, code) for code in selected_codes]
    json_path = destination / f"candidate-reports-{result.as_of_date}.json"
    markdown_path = destination / f"candidate-reports-{result.as_of_date}.md"
    json_path.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(
        "\n".join(candidate_report_markdown(report) for report in reports), encoding="utf-8"
    )
    return json_path, markdown_path
