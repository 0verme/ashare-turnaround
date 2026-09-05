"""Checkpointed lightweight snapshot campaign for the frozen baseline.

The full PIT replay artifacts from Issue #32 are accepted as reusable input;
only their formal Top-N rows are projected into small Parquet snapshots.  When
an artifact is not available, the campaign records an explicit unavailable
state unless the operator opts into an exact replay with ``run_missing``.
There is no approximate ranking path.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
from typing import Any

import pandas as pd

from .evaluation import BASELINE_EVALUATION_CONTRACT_VERSION, BASELINE_TOP_N

BASELINE_SNAPSHOT_CAMPAIGN_VERSION = "baseline-lightweight-snapshot-campaign-v1"


@dataclass(frozen=True, slots=True)
class SnapshotCampaignResult:
    scans: pd.DataFrame
    records: tuple[dict[str, Any], ...]
    target_count: int
    available_target_count: int
    completed_count: int
    reused_count: int
    unavailable_count: int
    failed_count: int
    pit_violation_count: int
    checkpoint_path: Path
    summary_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_contract_version": BASELINE_SNAPSHOT_CAMPAIGN_VERSION,
            "evaluation_contract_version": BASELINE_EVALUATION_CONTRACT_VERSION,
            "target_count": self.target_count,
            "available_target_count": self.available_target_count,
            "completed_count": self.completed_count,
            "reused_count": self.reused_count,
            "unavailable_count": self.unavailable_count,
            "failed_count": self.failed_count,
            "pit_violation_count": self.pit_violation_count,
            "checkpoint": str(self.checkpoint_path),
            "summary": str(self.summary_path),
            "records": list(self.records),
        }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _date_text(value: Any) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).normalize().strftime("%Y%m%d")


def _open_json(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _iter_array_objects(
    path: Path, key: str, *, chunk_size: int = 1024 * 1024
) -> Iterator[dict[str, Any]]:
    """Yield objects from one large JSON array without materializing its parent."""

    decoder = JSONDecoder()
    # Text mode is used for decoding, while the marker is searched in UTF-8
    # bytes only conceptually.  A text marker avoids splitting a multibyte
    # character at the search boundary.
    marker_text = json.dumps(key, ensure_ascii=False)
    with _open_json(path) as source:
        buffer = ""
        found = False
        while not found:
            chunk = source.read(chunk_size)
            if not chunk:
                raise ValueError(f"JSON array key not found: {key}")
            buffer += chunk
            position = buffer.find(marker_text)
            if position < 0:
                buffer = buffer[-max(len(marker_text), 32) :]
                continue
            array_start = buffer.find("[", position + len(marker_text))
            if array_start < 0:
                continue
            buffer = buffer[array_start + 1 :]
            found = True
        while True:
            buffer = buffer.lstrip()
            if buffer.startswith("]"):
                return
            if not buffer:
                chunk = source.read(chunk_size)
                if not chunk:
                    raise ValueError(f"unterminated JSON array: {key}")
                buffer += chunk
                continue
            try:
                value, end = decoder.raw_decode(buffer)
            except JSONDecodeError:
                chunk = source.read(chunk_size)
                if not chunk:
                    raise ValueError(f"invalid or truncated JSON array: {key}")
                buffer += chunk
                continue
            if not isinstance(value, dict):
                raise ValueError(f"JSON array {key} contains a non-object")
            yield value
            buffer = buffer[end:]
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:]
            elif buffer.startswith("]"):
                return
            elif not buffer:
                continue
            else:
                # The next token may simply be split across chunks.
                if buffer and not buffer.startswith((",", "]")):
                    raise ValueError(f"invalid separator in JSON array: {key}")


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_candidates(artifact_root: Path, month: str, as_of: str) -> list[Path]:
    candidates = [
        artifact_root / f"issue32-sample-{month}" / "snapshots" / f"{as_of}-ready.json.gz",
        artifact_root / f"issue32-sample-{month}" / "snapshots" / f"{as_of}-ready.json",
    ]
    if month == "2025-06":
        candidates.extend(
            [
                artifact_root
                / "issue32-resource-v3-full-baseline1"
                / "snapshots"
                / f"{as_of}-ready.json.gz",
                artifact_root
                / "issue32-resource-v3-full-baseline1"
                / "snapshots"
                / f"{as_of}-ready.json",
            ]
        )
    return candidates


def _logical_path(path: Path, artifact_root: Path) -> str:
    try:
        return path.relative_to(artifact_root).as_posix()
    except ValueError:
        return path.name


def _artifact_sidecar(path: Path) -> dict[str, Any]:
    directory = path.parent.parent
    merged: dict[str, Any] = {}
    for name in ("machine-audit.json", "summary.json", "driver-result.json"):
        sidecar = _json_object(directory / name)
        if not sidecar:
            continue
        for key, value in sidecar.items():
            if key not in merged:
                merged[key] = value
        if isinstance(sidecar.get("summary"), dict):
            summary = merged.setdefault("summary", {})
            if isinstance(summary, dict):
                for key, value in sidecar["summary"].items():
                    summary.setdefault(key, value)
    return merged


def _sidecar_value(sidecar: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = sidecar
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def project_artifact_top_n(
    artifact_path: str | Path,
    *,
    target_month: str,
    as_of_date: str,
    regime: str | None,
    top_n: int = BASELINE_TOP_N,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Project formal ranking rows from a normalized Issue #32 artifact."""

    path = Path(artifact_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    # ``diagnostic_ranked`` is ordered by the same eligibility/rank contract as
    # formal ranking.  Reading it avoids parsing the huge evidence arrays.
    try:
        iterator = _iter_array_objects(path, "diagnostic_ranked")
        for row in iterator:
            if _truthy(row.get("ranking_eligible")):
                rows.append(row)
                if len(rows) >= top_n:
                    break
    except ValueError:
        iterator = _iter_array_objects(path, "ranked")
        for row in iterator:
            if _truthy(row.get("ranking_eligible", True)):
                rows.append(row)
                if len(rows) >= top_n:
                    break
    if not rows:
        raise ValueError(f"artifact has no formal ranking rows: {path}")
    rows = sorted(
        rows,
        key=lambda row: (
            pd.to_numeric(row.get("rank"), errors="coerce")
            if pd.notna(pd.to_numeric(row.get("rank"), errors="coerce"))
            else float("inf"),
            str(row.get("ts_code", "")),
        ),
    )[:top_n]
    fields = (
        "ts_code",
        "rank",
        "turnaround_score",
        "ranking_eligible",
        "confidence",
        "evidence_coverage",
        "score_version",
        "score_config_fingerprint",
        "snapshot_id",
        "run_id",
        "universe_version",
        "feature_version",
        "comparable_period_contract_version",
        "trend_contract_version",
        "attention_contract_version",
        "expectation_crowding_contract_version",
        "evidence_confidence_contract_version",
        "feature_group_registry_version",
        "historical_universe_member",
        "revenue_yoy",
        "net_profit_yoy",
        "operating_profit_yoy",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "cfo_to_profit",
        "fundamental_report_period",
    )
    projected: list[dict[str, Any]] = []
    for row in rows:
        projected_row = {key: row.get(key) for key in fields if key in row}
        projected_row.update(
            {
                "target_month": target_month,
                "as_of_date": _date_text(row.get("as_of_date")) or as_of_date,
                "market_regime": regime,
                "snapshot_source": "issue32_full_replay_reuse",
            }
        )
        projected_row["ranking_eligible"] = _truthy(projected_row.get("ranking_eligible"))
        projected_row["historical_universe_member"] = _truthy(
            projected_row.get("historical_universe_member", True)
        )
        projected.append(projected_row)
    frame = pd.DataFrame(projected)
    sidecar = _artifact_sidecar(path)
    audit = sidecar.get("pit", {}) if isinstance(sidecar.get("pit"), dict) else {}
    metadata = {
        "status": _sidecar_value(sidecar, ("status",), ("gate_status",)) or "READY",
        "candidate_count": _sidecar_value(
            sidecar,
            ("candidate_count",),
            ("summary", "diagnostic_candidate_count"),
        ),
        "ranking_eligible_count": _sidecar_value(
            sidecar,
            ("eligible_count",),
            ("summary", "ranking_eligible_count"),
        ),
        "source_formal_top_n_count": _sidecar_value(
            sidecar,
            ("formal_top_n_count",),
            ("summary", "top_n_candidate_count"),
        ),
        "projected_top_n_count": len(rows),
        "pit_violations": len(audit.get("violations", [])) if isinstance(audit, dict) else 0,
        "input_manifest_id": _sidecar_value(sidecar, ("input_manifest_id",)),
        "artifact_status": "REUSED_COMPATIBLE_FULL_REPLAY",
        "artifact_reference": str(path),
        "selected_date": as_of_date,
    }
    return frame, metadata


def _load_schedule(path: str | Path) -> list[dict[str, Any]]:
    payload = _json_object(Path(path))
    targets = payload.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("schedule targets must be an array")
    return [target for target in targets if isinstance(target, dict)]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def run_lightweight_snapshot_campaign(
    *,
    schedule_path: str | Path,
    artifact_root: str | Path,
    output_dir: str | Path,
    data_dir: str | Path | None = None,
    top_n: int = BASELINE_TOP_N,
    run_missing: bool = False,
    max_new_snapshots: int = 0,
) -> SnapshotCampaignResult:
    """Build/resume lightweight snapshots; never approximate a missing rank."""

    if top_n != BASELINE_TOP_N:
        raise ValueError("baseline snapshot campaign freezes Top-20")
    if max_new_snapshots < 0:
        raise ValueError("max_new_snapshots must be non-negative")
    schedule = _load_schedule(schedule_path)
    artifact_base = Path(artifact_root)
    output = Path(output_dir)
    snapshots_dir = output / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "checkpoint.json"
    summary_path = output / "campaign-summary.json"
    checkpoint = _json_object(checkpoint_path)
    previous = checkpoint.get("records", {}) if isinstance(checkpoint.get("records"), dict) else {}
    records: dict[str, dict[str, Any]] = {}
    scan_frames: list[pd.DataFrame] = []
    new_runs = 0
    pit_violations = 0

    for target in schedule:
        month = str(target.get("target_month", ""))
        status = str(target.get("availability_status", target.get("status", "")))
        as_of = _date_text(target.get("selected_trading_date"))
        regime = target.get("regime_label")
        if not month:
            continue
        if status != "AVAILABLE" or not as_of:
            records[month] = {
                "target_month": month,
                "status": "SCHEDULE_UNAVAILABLE",
                "schedule_status": status,
                "reason": target.get("unavailable_reason") or "no_selected_trading_date",
            }
            continue
        snapshot_path = snapshots_dir / f"snapshot-{month}.parquet"
        old = previous.get(month, {})
        if snapshot_path.is_file() and old.get("status") in {
            "COMPLETED_REUSED",
            "COMPLETED_EXACT_REPLAY",
        }:
            frame = pd.read_parquet(snapshot_path)
            scan_frames.append(frame)
            records[month] = {
                **old,
                "status": old["status"],
                "reuse": "checkpoint",
                "projected_top_n_count": len(frame),
            }
            pit_violations += int(old.get("pit_violations", 0) or 0)
            continue
        candidates = [
            candidate
            for candidate in _artifact_candidates(artifact_base, month, as_of)
            if candidate.is_file()
        ]
        if candidates:
            try:
                frame, metadata = project_artifact_top_n(
                    candidates[0],
                    target_month=month,
                    as_of_date=as_of,
                    regime=str(regime) if regime is not None else None,
                    top_n=top_n,
                )
                frame.to_parquet(snapshot_path, index=False)
                record = {
                    "target_month": month,
                    "as_of_date": as_of,
                    "regime": regime,
                    "status": "COMPLETED_REUSED",
                    "reuse": "issue32_compatible_full_artifact",
                    "snapshot_path": str(snapshot_path),
                    "logical_artifact_reference": _logical_path(candidates[0], artifact_base),
                    "candidate_count": metadata.get("candidate_count"),
                    "ranking_eligible_count": metadata.get("ranking_eligible_count"),
                    "source_formal_top_n_count": metadata.get("source_formal_top_n_count"),
                    "projected_top_n_count": metadata.get("projected_top_n_count"),
                    "snapshot_id": str(frame["snapshot_id"].dropna().iloc[0])
                    if "snapshot_id" in frame and frame["snapshot_id"].notna().any()
                    else None,
                    "run_id": str(frame["run_id"].dropna().iloc[0])
                    if "run_id" in frame and frame["run_id"].notna().any()
                    else None,
                    "score_config_fingerprint": str(
                        frame["score_config_fingerprint"].dropna().iloc[0]
                    )
                    if "score_config_fingerprint" in frame
                    and frame["score_config_fingerprint"].notna().any()
                    else None,
                    "input_manifest_id": metadata.get("input_manifest_id"),
                    "pit_violations": int(metadata.get("pit_violations", 0) or 0),
                    "warnings": [],
                }
                records[month] = record
                scan_frames.append(frame)
                pit_violations += record["pit_violations"]
                continue
            except (OSError, ValueError, TypeError, JSONDecodeError) as exc:
                records[month] = {
                    "target_month": month,
                    "as_of_date": as_of,
                    "regime": regime,
                    "status": "FAILED",
                    "reason": f"artifact_projection_failed:{type(exc).__name__}:{exc}",
                }
                continue
        if run_missing and new_runs < max_new_snapshots:
            # Exact replay support is intentionally opt-in and imported lazily
            # so a projection-only campaign never initializes the scanner.
            from .replay import ReplayConfig, run_replay
            from .replay_validation import validate_replay_pit

            try:
                replay = run_replay(
                    data_dir or Path(schedule_path).parents[2],
                    as_of_date=as_of,
                    config=ReplayConfig(top_n=top_n),
                )
                if replay.status not in {"PASS", "PARTIAL"}:
                    raise RuntimeError(f"replay_status={replay.status}")
                violations = validate_replay_pit(
                    replay,
                    benchmark_id="000300.SH",
                )
                if violations:
                    raise RuntimeError("PIT violation: " + "; ".join(violations))
                frame = replay.ranked.copy()
                frame["target_month"] = month
                frame["market_regime"] = regime
                frame.to_parquet(snapshot_path, index=False)
                record = {
                    "target_month": month,
                    "as_of_date": as_of,
                    "regime": regime,
                    "status": "COMPLETED_EXACT_REPLAY",
                    "reuse": "none",
                    "snapshot_path": str(snapshot_path),
                    "snapshot_id": replay.snapshot_id,
                    "run_id": replay.run_id,
                    "score_config_fingerprint": replay.config_fingerprint,
                    "pit_violations": 0,
                    "warnings": list(replay.warnings),
                }
                records[month] = record
                scan_frames.append(frame)
                new_runs += 1
                continue
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                records[month] = {
                    "target_month": month,
                    "as_of_date": as_of,
                    "regime": regime,
                    "status": "FAILED",
                    "reason": f"exact_replay_failed:{type(exc).__name__}:{exc}",
                }
                continue
        records[month] = {
            "target_month": month,
            "as_of_date": as_of,
            "regime": regime,
            "status": "UNAVAILABLE_COMPATIBLE_SNAPSHOT",
            "reason": "no_existing_compatible_full_replay_artifact; exact_replay_not_requested",
        }

    available_months = {
        str(target.get("target_month"))
        for target in schedule
        if str(target.get("availability_status", target.get("status", ""))) == "AVAILABLE"
    }
    completed_records = [
        record for record in records.values() if record.get("status", "").startswith("COMPLETED_")
    ]
    reused_count = sum(record.get("status") == "COMPLETED_REUSED" for record in completed_records)
    unavailable_count = sum(
        record.get("status") == "UNAVAILABLE_COMPATIBLE_SNAPSHOT" for record in records.values()
    )
    failed_count = sum(record.get("status") == "FAILED" for record in records.values())
    scans = pd.concat(scan_frames, ignore_index=True, sort=False) if scan_frames else pd.DataFrame()
    campaign = {
        "campaign_contract_version": BASELINE_SNAPSHOT_CAMPAIGN_VERSION,
        "evaluation_contract_version": BASELINE_EVALUATION_CONTRACT_VERSION,
        "top_n": top_n,
        "schedule_path": str(schedule_path),
        "artifact_root": "external_issue32_artifact_root",
        "target_count": len(schedule),
        "available_target_count": len(available_months),
        "completed_count": len(completed_records),
        "reused_count": reused_count,
        "unavailable_count": unavailable_count,
        "failed_count": failed_count,
        "pit_violation_count": pit_violations,
        "checkpoint_resume": True,
        "run_missing": run_missing,
        "max_new_snapshots": max_new_snapshots,
        "records": records,
    }
    _write_json(checkpoint_path, campaign)
    _write_json(summary_path, campaign)
    return SnapshotCampaignResult(
        scans=scans,
        records=tuple(records[month] for month in sorted(records)),
        target_count=len(schedule),
        available_target_count=len(available_months),
        completed_count=len(completed_records),
        reused_count=reused_count,
        unavailable_count=unavailable_count,
        failed_count=failed_count,
        pit_violation_count=pit_violations,
        checkpoint_path=checkpoint_path,
        summary_path=summary_path,
    )


__all__ = [
    "BASELINE_SNAPSHOT_CAMPAIGN_VERSION",
    "SnapshotCampaignResult",
    "project_artifact_top_n",
    "run_lightweight_snapshot_campaign",
]
