"""Deterministic, provenance-first candidate report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .replay import ReplayResult


def candidate_report(result: ReplayResult, ts_code: str) -> dict[str, Any]:
    vectors = {vector.ts_code: vector for vector in result.vectors}
    scores = {score.ts_code: score for score in result.scores}
    vector = vectors.get(ts_code)
    score = scores.get(ts_code)
    if vector is None or score is None:
        raise KeyError(f"candidate not found in replay: {ts_code}")
    return {
        "metadata": result.metadata(),
        "comparable_period_contract_version": result.comparable_period_contract_version,
        "trend_contract_version": result.trend_contract_version,
        "ts_code": ts_code,
        "selected": not score.rejected and score.turnaround_score is not None,
        "score": score.as_dict(),
        "features": dict(vector.values),
        "evidence": {key: value.as_dict() for key, value in vector.evidence.items()},
        "risk_flags": list(vector.risk_flags),
        "rejected_reasons": list(vector.rejected_reasons),
    }


def candidate_report_markdown(report: dict[str, Any]) -> str:
    score = report["score"]
    contract_version = report["metadata"].get("comparable_period_contract_version", "unknown")
    trend_contract_version = report["metadata"].get("trend_contract_version", "unknown")
    lines = [
        f"# Turnaround candidate report: {report['ts_code']}",
        "",
        f"- As of: `{report['metadata']['as_of_date']}`",
        f"- Selected: `{report['selected']}`",
        f"- Turnaround score: `{score['turnaround_score']}`",
        f"- Score version: `{score['score_version']}`",
        f"- Comparable-period contract: `{contract_version}`",
        f"- Trend contract: `{trend_contract_version}`",
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
