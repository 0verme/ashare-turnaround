"""Point-in-time replay of the complete scanner pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..features import (
    compute_attention_features,
    compute_crowding_features,
    compute_fundamental_features,
    compute_quality_features,
    compute_trend_features,
)
from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION
from ..storage.inventory import build_coverage_report
from ..storage.parquet import RawParquetStore
from .contracts import TURNAROUND_TREND_CONTRACT_VERSION, FeatureVector
from .score import (
    ScoreConfig,
    ScoreResult,
    ablation_score_configs,
    rank_scores,
    score_feature_vector,
)
from .universe import UniverseConfig, build_investable_universe


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    top_n: int = 20
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")

    def declared(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "comparable_period_contract_version": COMPARABLE_PERIOD_CONTRACT_VERSION,
            "trend_contract_version": TURNAROUND_TREND_CONTRACT_VERSION,
            "universe": asdict(self.universe),
            "score": self.score.declared(),
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.declared(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    as_of_date: str
    snapshot_id: str
    universe_version: str
    feature_version: str
    score_version: str
    config_fingerprint: str
    run_id: str
    configuration: dict[str, Any]
    input_rows: dict[str, int]
    status: str
    ranked: pd.DataFrame
    vectors: tuple[FeatureVector, ...]
    scores: tuple[ScoreResult, ...]
    warnings: tuple[str, ...] = ()
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    trend_contract_version: str = TURNAROUND_TREND_CONTRACT_VERSION

    def metadata(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "snapshot_id": self.snapshot_id,
            "universe_version": self.universe_version,
            "feature_version": self.feature_version,
            "score_version": self.score_version,
            "config_fingerprint": self.config_fingerprint,
            "run_id": self.run_id,
            "configuration": self.configuration,
            "input_rows": self.input_rows,
            "status": self.status,
            "warnings": list(self.warnings),
            "comparable_period_contract_version": self.comparable_period_contract_version,
            "trend_contract_version": self.trend_contract_version,
        }

    def artifact_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata(),
            "ranked": self.ranked.to_dict(orient="records"),
            "vectors": [vector.as_dict() for vector in self.vectors],
            "scores": [score.as_dict() for score in self.scores],
        }


_SNAPSHOT_DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "trade_cal": ("cal_date",),
    "daily": ("trade_date",),
    "daily_basic": ("trade_date",),
    "income": ("actual_available_date", "f_ann_date", "ann_date"),
    "balancesheet": ("actual_available_date", "f_ann_date", "ann_date"),
    "cashflow": ("actual_available_date", "f_ann_date", "ann_date"),
    "fina_indicator": ("actual_available_date", "ann_date"),
    "disclosure_date": ("actual_date", "ann_date"),
}


def _visible_snapshot_frame(dataset: str, frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    candidates = _SNAPSHOT_DATE_FIELDS.get(dataset, ())
    available = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    found = False
    for field_name in candidates:
        if field_name not in frame.columns:
            continue
        found = True
        available = available.fillna(pd.to_datetime(frame[field_name], errors="coerce"))
    if not found:
        return frame
    return frame.loc[available.notna() & available.dt.normalize().le(as_of)]


def _snapshot_id(frames: dict[str, pd.DataFrame], as_of_date: str) -> str:
    digest = hashlib.sha256()
    digest.update(as_of_date.encode("ascii"))
    as_of = pd.Timestamp(as_of_date)
    for dataset in sorted(frames):
        frame = _visible_snapshot_frame(dataset, frames[dataset], as_of)
        digest.update(dataset.encode("utf-8"))
        ordered = frame.reindex(sorted(frame.columns), axis=1)
        digest.update(str(len(ordered)).encode("ascii"))
        digest.update(",".join(map(str, ordered.columns)).encode("utf-8"))
        try:
            values = pd.util.hash_pandas_object(ordered, index=False).to_numpy(copy=True)
            values.sort()
            digest.update(values.tobytes())
        except (TypeError, ValueError):
            records = [
                json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
                for record in ordered.to_dict(orient="records")
            ]
            digest.update("\n".join(sorted(records)).encode("utf-8"))
    return digest.hexdigest()[:16]


def _market_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    daily = frames.get("daily", pd.DataFrame())
    basic = frames.get("daily_basic", pd.DataFrame())
    if daily.empty:
        return basic.copy()
    if basic.empty:
        return daily.copy()
    keys = [
        key for key in ("ts_code", "trade_date") if key in daily.columns and key in basic.columns
    ]
    if not keys:
        return pd.concat([daily, basic], ignore_index=True, sort=False)
    return daily.merge(basic, on=keys, how="outer", suffixes=("", "_basic"))


def _frames_from_store(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    store = RawParquetStore(data_dir)
    datasets = (
        "stock_basic",
        "trade_cal",
        "daily",
        "daily_basic",
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "disclosure_date",
    )
    return {dataset: store.read(dataset) for dataset in datasets}


def run_replay_frames(
    frames: dict[str, pd.DataFrame],
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
) -> ReplayResult:
    """Run the scanner against supplied frames, making tests independent of I/O."""

    settings = config or ReplayConfig()
    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid replay as_of_date: {as_of_date!r}")
    as_of = pd.Timestamp(parsed).normalize()
    as_of_text = as_of.strftime("%Y%m%d")
    universe = build_investable_universe(
        frames.get("stock_basic", pd.DataFrame()),
        as_of_date=as_of,
        daily_basic=frames.get("daily_basic"),
        financial_frames={
            key: frames.get(key, pd.DataFrame())
            for key in ("income", "balancesheet", "cashflow", "fina_indicator")
        },
        config=settings.universe,
    )
    market = _market_frame(frames)
    financial_frames = {
        key: frames.get(key, pd.DataFrame())
        for key in ("income", "balancesheet", "cashflow", "fina_indicator")
    }
    vectors: list[FeatureVector] = []
    scores: list[ScoreResult] = []
    warnings = list(universe.warnings)
    for _, row in universe.included.iterrows():
        code = str(row["ts_code"])
        vector = compute_fundamental_features(financial_frames, code, as_of)
        vector.merge(compute_trend_features(financial_frames, code, as_of))
        vector.merge(compute_quality_features(financial_frames, code, as_of))
        vector.merge(compute_attention_features(market, code, as_of))
        vector.merge(compute_crowding_features(market, code, as_of))
        vectors.append(vector)
        scores.append(score_feature_vector(vector, config=settings.score))
    ranked = rank_scores(scores, top_n=settings.top_n)
    if not ranked.empty and not universe.included.empty:
        names = (
            universe.included[["ts_code", "name"]].drop_duplicates("ts_code")
            if "name" in universe.included.columns
            else pd.DataFrame(columns=["ts_code", "name"])
        )
        ranked = ranked.merge(names, on="ts_code", how="left")
        ranked = ranked.sort_values("rank", kind="stable").reset_index(drop=True)
    required = {"stock_basic", "income", "daily_basic"}
    missing = sorted(dataset for dataset in required if frames.get(dataset, pd.DataFrame()).empty)
    if missing:
        warnings.append(f"missing_required_datasets={','.join(missing)}")
    status = "PASS" if ranked.shape[0] > 0 and not missing else "PARTIAL" if vectors else "EMPTY"
    snapshot_id = _snapshot_id(frames, as_of_text)
    config_fingerprint = settings.fingerprint
    run_id = hashlib.sha256(f"{snapshot_id}:{config_fingerprint}".encode("ascii")).hexdigest()[:16]
    ranked["historical_universe_member"] = True
    ranked["snapshot_id"] = snapshot_id
    ranked["run_id"] = run_id
    ranked["universe_version"] = universe.version
    ranked["feature_version"] = "features-v1"
    ranked["comparable_period_contract_version"] = COMPARABLE_PERIOD_CONTRACT_VERSION
    ranked["trend_contract_version"] = TURNAROUND_TREND_CONTRACT_VERSION
    ranked["score_config_fingerprint"] = settings.score.fingerprint
    return ReplayResult(
        as_of_date=as_of_text,
        snapshot_id=snapshot_id,
        universe_version=universe.version,
        feature_version="features-v1",
        score_version=settings.score.version,
        config_fingerprint=config_fingerprint,
        run_id=run_id,
        configuration=settings.declared(),
        input_rows={name: int(len(frame)) for name, frame in sorted(frames.items())},
        status=status,
        ranked=ranked,
        vectors=tuple(vectors),
        scores=tuple(scores),
        warnings=tuple(dict.fromkeys(warnings)),
        comparable_period_contract_version=COMPARABLE_PERIOD_CONTRACT_VERSION,
        trend_contract_version=TURNAROUND_TREND_CONTRACT_VERSION,
    )


def run_replay(
    data_dir: str | Path,
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
) -> ReplayResult:
    frames = _frames_from_store(data_dir)
    result = run_replay_frames(frames, as_of_date=as_of_date, config=config)
    coverage = build_coverage_report(data_dir, as_of_date=result.as_of_date)
    if any(dataset.status in {"FAIL", "PARTIAL", "UNKNOWN"} for dataset in coverage.datasets):
        result = replace(
            result,
            status="PARTIAL" if result.status == "PASS" else result.status,
            warnings=tuple(dict.fromkeys((*result.warnings, "coverage_not_complete"))),
        )
    return result


def run_replay_variants(
    data_dir: str | Path,
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: ReplayConfig | None = None,
) -> dict[str, ReplayResult]:
    """Run the required score variants against one immutable input snapshot."""

    base = config or ReplayConfig()
    frames = _frames_from_store(data_dir)
    results = {
        name: run_replay_frames(
            frames,
            as_of_date=as_of_date,
            config=replace(base, score=score_config),
        )
        for name, score_config in ablation_score_configs(base.score).items()
    }
    snapshot_ids = {result.snapshot_id for result in results.values()}
    if len(snapshot_ids) != 1:
        raise RuntimeError("ablation variants did not share one PIT snapshot")
    coverage = build_coverage_report(data_dir, as_of_date=next(iter(results.values())).as_of_date)
    if any(dataset.status in {"FAIL", "PARTIAL", "UNKNOWN"} for dataset in coverage.datasets):
        results = {
            name: replace(
                result,
                status="PARTIAL" if result.status == "PASS" else result.status,
                warnings=tuple(dict.fromkeys((*result.warnings, "coverage_not_complete"))),
            )
            for name, result in results.items()
        }
    return results


def write_replay_artifacts(
    result: ReplayResult,
    directory: str | Path,
    *,
    label: str | None = None,
) -> tuple[Path, Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if label is not None:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.")
        if not normalized:
            raise ValueError("artifact label must contain a filename-safe character")
        suffix = f"-{normalized}"
    data_path = destination / f"replay-{result.as_of_date}{suffix}.parquet"
    metadata_path = destination / f"replay-{result.as_of_date}{suffix}.json"
    result.ranked.to_parquet(data_path, index=False)
    metadata_path.write_text(
        json.dumps(result.artifact_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return data_path, metadata_path


def write_replay_variant_artifacts(
    results: dict[str, ReplayResult], directory: str | Path
) -> dict[str, tuple[Path, Path]]:
    return {
        name: write_replay_artifacts(result, directory, label=name)
        for name, result in results.items()
    }
