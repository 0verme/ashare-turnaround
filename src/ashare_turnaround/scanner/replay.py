"""Point-in-time replay of the complete scanner pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
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
from ..storage.inventory import build_coverage_report
from ..storage.parquet import RawParquetStore
from .contracts import FeatureVector
from .score import ScoreConfig, ScoreResult, rank_scores, score_feature_vector
from .universe import UniverseConfig, build_investable_universe


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    top_n: int = 20
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    as_of_date: str
    snapshot_id: str
    universe_version: str
    feature_version: str
    score_version: str
    status: str
    ranked: pd.DataFrame
    vectors: tuple[FeatureVector, ...]
    scores: tuple[ScoreResult, ...]
    warnings: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "snapshot_id": self.snapshot_id,
            "universe_version": self.universe_version,
            "feature_version": self.feature_version,
            "score_version": self.score_version,
            "status": self.status,
            "warnings": list(self.warnings),
        }

    def artifact_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata(),
            "ranked": self.ranked.to_dict(orient="records"),
            "vectors": [vector.as_dict() for vector in self.vectors],
            "scores": [score.as_dict() for score in self.scores],
        }


def _snapshot_id(frames: dict[str, pd.DataFrame], as_of_date: str) -> str:
    digest = hashlib.sha256()
    digest.update(as_of_date.encode("ascii"))
    for dataset in sorted(frames):
        frame = frames[dataset]
        digest.update(dataset.encode("utf-8"))
        ordered = frame.reindex(sorted(frame.columns), axis=1)
        digest.update(str(len(ordered)).encode("ascii"))
        digest.update(",".join(map(str, ordered.columns)).encode("utf-8"))
        try:
            values = pd.util.hash_pandas_object(ordered, index=True).to_numpy(copy=False)
            digest.update(values.tobytes())
        except (TypeError, ValueError):
            digest.update(
                ordered.to_json(orient="split", date_format="iso", default_handler=str).encode(
                    "utf-8"
                )
            )
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
    return ReplayResult(
        as_of_date=as_of_text,
        snapshot_id=_snapshot_id(frames, as_of_text),
        universe_version=universe.version,
        feature_version="features-v1",
        score_version=settings.score.version,
        status=status,
        ranked=ranked,
        vectors=tuple(vectors),
        scores=tuple(scores),
        warnings=tuple(dict.fromkeys(warnings)),
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
            warnings=tuple(dict.fromkeys((*result.warnings, "coverage_not_complete"))),
        )
    return result


def write_replay_artifacts(result: ReplayResult, directory: str | Path) -> tuple[Path, Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    data_path = destination / f"replay-{result.as_of_date}.parquet"
    metadata_path = destination / f"replay-{result.as_of_date}.json"
    result.ranked.to_parquet(data_path, index=False)
    metadata_path.write_text(
        json.dumps(result.artifact_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return data_path, metadata_path
