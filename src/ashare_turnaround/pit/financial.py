"""Financial point-in-time normalization and a small quarterization prototype."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..storage.parquet import RawParquetStore


@dataclass(frozen=True, slots=True)
class PITMapping:
    dataset: str
    report_period_candidates: tuple[str, ...]
    announcement_candidates: tuple[str, ...]
    available_candidates: tuple[str, ...]
    disclosure_fallback: bool = False
    semantic_status: str = "unknown"
    notes: str = ""


PIT_MAPPINGS: dict[str, PITMapping] = {
    "income": PITMapping(
        "income",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "f_ann_date", "ann_date"),
        notes="Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent.",
    ),
    "balancesheet": PITMapping(
        "balancesheet",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "f_ann_date", "ann_date"),
        notes="Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent.",
    ),
    "cashflow": PITMapping(
        "cashflow",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "f_ann_date", "ann_date"),
        notes="Prefer f_ann_date; ann_date is only an explicit fallback when f_ann_date is absent.",
    ),
    "fina_indicator": PITMapping(
        "fina_indicator",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
        notes="No f_ann_date candidate is assumed for this endpoint; confirm against live schema.",
    ),
    "fina_mainbz": PITMapping(
        "fina_mainbz",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date",),
        disclosure_fallback=True,
        notes="Needs an explicit disclosure_date join; actual_date semantics are not assumed.",
    ),
    "forecast": PITMapping(
        "forecast",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
        notes="first_ann_date is retained as a raw field, not silently substituted.",
    ),
    "express": PITMapping(
        "express",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
    ),
    "fina_audit": PITMapping(
        "fina_audit",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
    ),
    "disclosure_date": PITMapping(
        "disclosure_date",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "actual_date"),
        notes="actual_date is an event field; whether it is data availability is unknown.",
    ),
}


_CANONICAL_COLUMNS = (
    "report_period",
    "announcement_date",
    "actual_available_date",
    "available_date_source",
    "report_type",
    "update_flag",
    "retrieved_at",
    "source",
)


def _mapping_for(dataset: str) -> PITMapping:
    base_name = dataset.removesuffix("_vip")
    if base_name not in PIT_MAPPINGS:
        raise KeyError(f"no PIT mapping for dataset: {dataset}")
    return PIT_MAPPINGS[base_name]


def _first_available(
    frame: pd.DataFrame, candidates: tuple[str, ...]
) -> tuple[pd.Series, pd.Series]:
    values = pd.Series(pd.NA, index=frame.index, dtype="object")
    sources = pd.Series(pd.NA, index=frame.index, dtype="string")
    for candidate in candidates:
        if candidate not in frame.columns:
            continue
        candidate_values = frame[candidate]
        present = (
            values.isna() & candidate_values.notna() & candidate_values.astype("string").ne("")
        )
        values.loc[present] = candidate_values.loc[present]
        sources.loc[present] = candidate
    return values, sources


def _normalize_date_series(values: pd.Series) -> pd.Series:
    """Parse Tushare YYYYMMDD strings and ordinary date-like values."""

    normalized = values.astype("string").str.strip()
    eight_digit = normalized.str.fullmatch(r"\d{8}", na=False)
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if eight_digit.any():
        result.loc[eight_digit] = pd.to_datetime(
            normalized.loc[eight_digit], format="%Y%m%d", errors="coerce"
        )
    remaining = ~eight_digit & normalized.notna()
    if remaining.any():
        result.loc[remaining] = pd.to_datetime(normalized.loc[remaining], errors="coerce")
    return result.dt.normalize()


def _disclosure_available_dates(
    frame: pd.DataFrame, disclosure_frame: pd.DataFrame | None
) -> tuple[pd.Series, pd.Series]:
    if disclosure_frame is None or disclosure_frame.empty:
        return (
            pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]"),
            pd.Series(pd.NA, index=frame.index, dtype="string"),
        )
    required = {"ts_code", "end_date", "actual_date"}
    if not required.issubset(disclosure_frame.columns):
        return (
            pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]"),
            pd.Series(pd.NA, index=frame.index, dtype="string"),
        )
    disclosure = disclosure_frame[["ts_code", "end_date", "actual_date"]].copy()
    disclosure["report_period"] = _normalize_date_series(disclosure.pop("end_date"))
    disclosure["actual_date"] = _normalize_date_series(disclosure["actual_date"])
    disclosure = disclosure.dropna(subset=["ts_code", "report_period", "actual_date"])
    disclosure = disclosure.sort_values("actual_date").drop_duplicates(
        ["ts_code", "report_period"], keep="first"
    )
    keys = pd.MultiIndex.from_frame(
        pd.DataFrame(
            {
                "ts_code": frame["ts_code"].astype("string"),
                "report_period": _normalize_date_series(frame["report_period"]),
            },
            index=frame.index,
        )
    )
    lookup = disclosure.set_index(["ts_code", "report_period"])["actual_date"]
    values = pd.Series(lookup.reindex(keys).to_numpy(), index=frame.index, dtype="datetime64[ns]")
    sources = pd.Series(pd.NA, index=frame.index, dtype="string")
    sources.loc[values.notna()] = "disclosure_date.actual_date"
    return values, sources


def canonicalize_financial_frame(
    dataset: str,
    frame: pd.DataFrame,
    *,
    retrieved_at: str | None = None,
    source: str = "tushare-compatible",
    disclosure_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add canonical PIT columns without dropping any raw API columns."""

    mapping = _mapping_for(dataset)
    output = frame.reset_index(drop=True).copy()
    if output.empty:
        for column in _CANONICAL_COLUMNS:
            if column not in output.columns:
                output[column] = pd.Series(dtype="object")
        return output

    report_values, _ = _first_available(output, mapping.report_period_candidates)
    output["report_period"] = _normalize_date_series(report_values)

    announcement_values, _ = _first_available(output, mapping.announcement_candidates)
    output["announcement_date"] = _normalize_date_series(announcement_values)

    available_values, available_sources = _first_available(output, mapping.available_candidates)
    if mapping.disclosure_fallback and available_values.isna().all():
        disclosure_values, disclosure_sources = _disclosure_available_dates(
            output, disclosure_frame
        )
        available_values = disclosure_values
        available_sources = disclosure_sources
    output["actual_available_date"] = _normalize_date_series(available_values)
    output["available_date_source"] = available_sources.astype("string")

    for column in ("report_type", "update_flag"):
        if column not in output.columns:
            output[column] = pd.NA
    if "retrieved_at" not in output.columns:
        output["retrieved_at"] = retrieved_at or pd.Timestamp.now(tz="UTC").isoformat()
    elif retrieved_at is not None:
        output["retrieved_at"] = retrieved_at
    if "source" not in output.columns:
        output["source"] = source
    return output


def _version_columns(frame: pd.DataFrame) -> list[str]:
    columns = ["ts_code", "report_period"]
    # These distinguish simultaneous report families. update_flag is a version
    # attribute and is deliberately not a grouping key: as-of selects its
    # latest available version instead of hiding earlier versions.
    for candidate in ("report_type", "type", "comp_type", "end_type"):
        if candidate in frame.columns and frame[candidate].notna().any():
            columns.append(candidate)
    return columns


def select_financial_as_of(
    frame: pd.DataFrame,
    *,
    ts_code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Select the latest version available on ``as_of_date`` for each report."""

    if frame.empty:
        return frame.copy()
    if "actual_available_date" not in frame.columns or "report_period" not in frame.columns:
        raise ValueError("frame must be canonicalized before PIT selection")

    as_of = pd.Timestamp(as_of_date).normalize()
    available = _normalize_date_series(frame["actual_available_date"])
    selected = frame.loc[
        frame["ts_code"].astype("string").eq(ts_code)
        & available.notna()
        & available.le(as_of)
        & _normalize_date_series(frame["report_period"]).notna()
    ].copy()
    if selected.empty:
        return selected.reset_index(drop=True)

    selected["actual_available_date"] = available.loc[selected.index]
    selected["_pit_row_order"] = range(len(selected))
    selected["_pit_update_rank"] = pd.to_numeric(
        selected.get("update_flag", pd.Series(pd.NA, index=selected.index)), errors="coerce"
    ).fillna(-1)
    selected = selected.sort_values(
        ["actual_available_date", "announcement_date", "_pit_update_rank", "_pit_row_order"],
        na_position="first",
    )
    keys = _version_columns(selected)
    selected = selected.groupby(keys, dropna=False, sort=False, as_index=False).tail(1)
    return (
        selected.drop(columns=["_pit_row_order", "_pit_update_rank"], errors="ignore")
        .sort_values(keys, kind="stable")
        .reset_index(drop=True)
    )


def query_financial_as_of(
    dataset: str,
    ts_code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    frame: pd.DataFrame | None = None,
    data_dir: str | Path = "data",
    disclosure_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Load, canonicalize and query one financial dataset as of a date."""

    raw = frame if frame is not None else RawParquetStore(data_dir).read(dataset)
    canonical = canonicalize_financial_frame(
        dataset,
        raw,
        disclosure_frame=disclosure_frame,
    )
    return select_financial_as_of(canonical, ts_code=ts_code, as_of_date=as_of_date)


def derive_single_quarter(
    frame: pd.DataFrame,
    value_column: str,
    *,
    dataset_kind: str = "income",
) -> pd.DataFrame:
    """Prototype quarterly bridge for cumulative income/cash-flow values.

    It intentionally handles only the standard Q1/H1/Q3/FY period ends.  The
    source frame is not mutated and missing prior cumulative observations stay
    missing rather than being guessed.
    """

    if dataset_kind not in {"income", "cashflow"}:
        raise ValueError("single-quarter prototype is limited to income and cashflow")
    required = {"ts_code", "end_date", value_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    output = frame.copy()
    output["report_period"] = _normalize_date_series(output["end_date"])
    output["_year"] = output["report_period"].dt.year
    output["_month_day"] = output["report_period"].dt.strftime("%m-%d")
    output["single_quarter"] = pd.to_numeric(output[value_column], errors="coerce")

    sort_columns = ["ts_code", "_year", "report_period"]
    output = output.sort_values(sort_columns, kind="stable")
    for code, year in output[["ts_code", "_year"]].drop_duplicates().itertuples(index=False):
        mask = output["ts_code"].eq(code) & output["_year"].eq(year)
        year_rows = output.loc[mask]
        values = year_rows.set_index("_month_day")[value_column]
        q1 = pd.to_numeric(values.get("03-31"), errors="coerce")
        h1 = pd.to_numeric(values.get("06-30"), errors="coerce")
        q3 = pd.to_numeric(values.get("09-30"), errors="coerce")
        fy = pd.to_numeric(values.get("12-31"), errors="coerce")
        derived = {
            "03-31": q1,
            "06-30": h1 - q1 if pd.notna(h1) and pd.notna(q1) else pd.NA,
            "09-30": q3 - h1 if pd.notna(q3) and pd.notna(h1) else pd.NA,
            "12-31": fy - q3 if pd.notna(fy) and pd.notna(q3) else pd.NA,
        }
        for month_day, value in derived.items():
            row_mask = mask & output["_month_day"].eq(month_day)
            output.loc[row_mask, "single_quarter"] = value
    return output.drop(columns=["_year", "_month_day"])
