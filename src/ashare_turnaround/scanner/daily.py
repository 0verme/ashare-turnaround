"""Daily Top-N scanner and snapshot comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..storage.parquet import RawParquetStore
from .replay import ReplayConfig, ReplayResult, run_replay


@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    result: ReplayResult
    data_path: Path | None = None
    metadata_path: Path | None = None


def latest_completed_trading_date(
    trade_calendar: pd.DataFrame,
    *,
    today: str | date | datetime | pd.Timestamp | None = None,
) -> str:
    """Choose the newest explicitly open calendar date not later than today."""

    if trade_calendar.empty or not {"cal_date", "is_open"}.issubset(trade_calendar.columns):
        raise ValueError("trade calendar is unavailable or missing required fields")
    boundary = pd.Timestamp(today or pd.Timestamp.now()).normalize()
    dates = normalize_date_series(trade_calendar["cal_date"])
    opened = trade_calendar.loc[
        dates.notna()
        & dates.le(boundary)
        & pd.to_numeric(trade_calendar["is_open"], errors="coerce").eq(1)
    ]
    if opened.empty:
        raise ValueError("trade calendar has no completed open date")
    return normalize_date_series(opened["cal_date"]).max().strftime("%Y%m%d")


def scan_data(
    data_dir: str | Path,
    *,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    top_n: int = 20,
    config: ReplayConfig | None = None,
) -> ScanSnapshot:
    store = RawParquetStore(data_dir)
    if as_of_date is None:
        as_of_date = latest_completed_trading_date(store.read("trade_cal"))
    settings = config or ReplayConfig(top_n=top_n)
    if config is not None and top_n != config.top_n:
        settings = ReplayConfig(top_n=top_n, universe=config.universe, score=config.score)
    result = run_replay(data_dir, as_of_date=as_of_date, config=settings)
    return ScanSnapshot(result)


def write_scan_snapshot(snapshot: ScanSnapshot, data_dir: str | Path) -> ScanSnapshot:
    destination = Path(data_dir) / "derived" / "scans"
    destination.mkdir(parents=True, exist_ok=True)
    result = snapshot.result
    data_path = destination / f"scan-{result.as_of_date}.parquet"
    metadata_path = destination / f"scan-{result.as_of_date}.json"
    result.ranked.to_parquet(data_path, index=False)
    metadata_path.write_text(
        pd.Series(result.metadata()).to_json(force_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ScanSnapshot(result, data_path, metadata_path)


def read_scan_snapshot(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    return pd.read_parquet(source)


def compare_scan_snapshots(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """Show membership, rank, score, and reason changes between two snapshots."""

    if left.empty and right.empty:
        return pd.DataFrame()
    if "ts_code" not in left.columns or "ts_code" not in right.columns:
        raise ValueError("scan snapshots require ts_code")
    left_frame = left.copy().set_index("ts_code")
    right_frame = right.copy().set_index("ts_code")
    codes = sorted(set(left_frame.index.astype(str)) | set(right_frame.index.astype(str)))
    rows: list[dict[str, Any]] = []
    for code in codes:
        old = left_frame.loc[code] if code in left_frame.index else None
        new = right_frame.loc[code] if code in right_frame.index else None
        old_rank = int(old["rank"]) if old is not None and pd.notna(old.get("rank")) else None
        new_rank = int(new["rank"]) if new is not None and pd.notna(new.get("rank")) else None
        old_score = (
            pd.to_numeric(old.get("turnaround_score"), errors="coerce") if old is not None else None
        )
        new_score = (
            pd.to_numeric(new.get("turnaround_score"), errors="coerce") if new is not None else None
        )
        old_reason = str(old.get("risk_flags", "")) if old is not None else ""
        new_reason = str(new.get("risk_flags", "")) if new is not None else ""
        if old is None:
            change = "added"
        elif new is None:
            change = "removed"
        elif old_rank != new_rank or old_score != new_score or old_reason != new_reason:
            change = "changed"
        else:
            change = "unchanged"
        rows.append(
            {
                "ts_code": code,
                "change": change,
                "old_rank": old_rank,
                "new_rank": new_rank,
                "rank_change": old_rank - new_rank
                if old_rank is not None and new_rank is not None
                else None,
                "old_score": float(old_score)
                if old_score is not None and pd.notna(old_score)
                else None,
                "new_score": float(new_score)
                if new_score is not None and pd.notna(new_score)
                else None,
                "score_change": float(new_score - old_score)
                if old_score is not None
                and new_score is not None
                and pd.notna(old_score)
                and pd.notna(new_score)
                else None,
                "old_reason": old_reason,
                "new_reason": new_reason,
            }
        )
    return pd.DataFrame(rows)
