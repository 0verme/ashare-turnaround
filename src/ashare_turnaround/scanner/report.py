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
        "expectation_crowding_contract_version": result.expectation_crowding_contract_version,
        "benchmark": dict(result.benchmark_metadata),
        "ts_code": ts_code,
        "selected": not score.rejected and score.turnaround_score is not None,
        "score": score.as_dict(),
        "features": dict(vector.values),
        "evidence": evidence,
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
    crowding_version = report["metadata"].get(
        "expectation_crowding_contract_version", "unknown"
    )
    benchmark_id = report["metadata"].get("benchmark_id", "unknown")
    lines = [
        f"# Turnaround candidate report: {report['ts_code']}",
        "",
        f"- As of: `{report['metadata']['as_of_date']}`",
        f"- Selected: `{report['selected']}`",
        f"- Turnaround score: `{score['turnaround_score']}`",
        f"- Score version: `{score['score_version']}`",
        f"- Comparable-period contract: `{contract_version}`",
        f"- Expectation/crowding contract: `{crowding_version}`",
        f"- Primary benchmark: `{benchmark_id}`",
        f"- Risk flags: `{', '.join(report['risk_flags']) or 'none'}`",
        f"- Rejected reasons: `{', '.join(report['rejected_reasons']) or 'none'}`",
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
