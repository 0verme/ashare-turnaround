"""Financial point-in-time normalization and a small quarterization prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..dates import normalize_date_series
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
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm f_ann_date as the actual "
            "announcement date; ann_date is an explicit fallback."
        ),
    ),
    "balancesheet": PITMapping(
        "balancesheet",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "f_ann_date", "ann_date"),
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm f_ann_date as the actual "
            "announcement date; ann_date is an explicit fallback."
        ),
    ),
    "cashflow": PITMapping(
        "cashflow",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "f_ann_date", "ann_date"),
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm f_ann_date as the actual "
            "announcement date; ann_date is an explicit fallback."
        ),
    ),
    "fina_indicator": PITMapping(
        "fina_indicator",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm ann_date as the endpoint's "
            "available announcement date; no f_ann_date is exposed."
        ),
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
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm ann_date for availability; "
            "first_ann_date remains a raw field and is not substituted."
        ),
    ),
    "express": PITMapping(
        "express",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm ann_date as the available "
            "announcement date."
        ),
    ),
    "fina_audit": PITMapping(
        "fina_audit",
        ("report_period", "end_date"),
        ("announcement_date", "ann_date"),
        ("actual_available_date", "ann_date"),
        semantic_status="confirmed",
        notes=(
            "Live schema and source field definitions confirm ann_date as the available "
            "announcement date."
        ),
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
        # A non-empty but malformed preferred field must not block a valid
        # fallback such as ann_date when f_ann_date is unusable.
        usable = _normalize_date_series(candidate_values).notna()
        present = values.isna() & usable
        values.loc[present] = candidate_values.loc[present]
        sources.loc[present] = candidate
    return values, sources


def _normalize_date_series(values: pd.Series) -> pd.Series:
    """Compatibility wrapper around the shared date parser."""

    return normalize_date_series(values)


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
    if mapping.disclosure_fallback:
        disclosure_values, disclosure_sources = _disclosure_available_dates(
            output, disclosure_frame
        )
        fill_from_disclosure = available_values.isna() & disclosure_values.notna()
        available_values.loc[fill_from_disclosure] = disclosure_values.loc[fill_from_disclosure]
        available_sources.loc[fill_from_disclosure] = disclosure_sources.loc[fill_from_disclosure]
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
    for candidate in (
        "report_type",
        "type",
        "comp_type",
        "end_type",
        # ``fina_mainbz`` contains one row per business-line identity.  These
        # are record keys, not versions, and must not be collapsed by PIT.
        "bz_item",
        "curr_type",
    ):
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
    required = {"ts_code", "actual_available_date", "report_period"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            "frame must be canonicalized before PIT selection; "
            f"missing columns: {sorted(missing)}"
        )

    as_of_values = _normalize_date_series(pd.Series([as_of_date]))
    as_of = as_of_values.iloc[0]
    if pd.isna(as_of):
        raise ValueError(f"invalid as_of_date: {as_of_date!r}")
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
    standard_periods = output["_month_day"].isin({"03-31", "06-30", "09-30", "12-31"})
    duplicate_periods = output.loc[standard_periods].duplicated(
        ["ts_code", "_year", "_month_day"], keep=False
    )
    if duplicate_periods.any():
        raise ValueError(
            "duplicate cumulative rows for the same ts_code/year/report period; "
            "canonicalize revisions before quarterization"
        )
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


@dataclass(frozen=True, slots=True)
class RevisionCandidate:
    """A bounded, comparable financial version chain found in local raw data."""

    dataset: str
    ts_code: str
    report_period: pd.Timestamp
    identity: tuple[tuple[str, str], ...]
    available_dates: tuple[pd.Timestamp, ...]
    update_flags: tuple[str, ...]
    changed_fields: tuple[str, ...]
    rows: pd.DataFrame = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RevisionPITCheck:
    """Results for the four as-of boundaries around a real revision chain."""

    status: str
    before_first_empty: bool
    first_version_visible: bool
    before_revision_first: bool
    after_revision_revised: bool
    first_available_date: pd.Timestamp
    revision_available_date: pd.Timestamp
    value_column: str
    first_value: object
    revised_value: object

    @property
    def checks(self) -> dict[str, bool]:
        return {
            "before_first": self.before_first_empty,
            "after_first": self.first_version_visible,
            "before_revision": self.before_revision_first,
            "after_revision": self.after_revision_revised,
        }


def _distinct_non_null_values(values: pd.Series) -> bool:
    return values.dropna().astype("string").nunique() > 1


def find_financial_revision_candidates(
    dataset: str,
    frame: pd.DataFrame,
    *,
    max_candidates: int = 20,
) -> tuple[RevisionCandidate, ...]:
    """Find real temporal revision candidates without scanning a remote universe.

    A candidate must share a comparable report identity, have at least two
    available dates, and change at least one non-metadata financial value.  A
    mere duplicate with only a different ``update_flag`` is intentionally
    excluded.
    """

    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    canonical = (
        frame.copy()
        if {"report_period", "actual_available_date"}.issubset(frame.columns)
        else canonicalize_financial_frame(dataset, frame)
    )
    required = {"ts_code", "report_period", "actual_available_date"}
    if not required.issubset(canonical.columns) or canonical.empty:
        return ()
    canonical = canonical.copy()
    canonical["report_period"] = _normalize_date_series(canonical["report_period"])
    canonical["actual_available_date"] = _normalize_date_series(
        canonical["actual_available_date"]
    )
    if "announcement_date" in canonical.columns:
        canonical["announcement_date"] = _normalize_date_series(canonical["announcement_date"])
    canonical = canonical.loc[
        canonical["ts_code"].notna()
        & canonical["report_period"].notna()
        & canonical["actual_available_date"].notna()
    ].copy()
    if canonical.empty:
        return ()

    identity_columns = _version_columns(canonical)
    metadata = {
        "ts_code",
        "ann_date",
        "f_ann_date",
        "end_date",
        "report_period",
        "announcement_date",
        "actual_available_date",
        "available_date_source",
        "report_type",
        "comp_type",
        "end_type",
        "type",
        "update_flag",
        "retrieved_at",
        "source",
    }
    value_columns = [column for column in canonical.columns if column not in metadata]
    candidates: list[RevisionCandidate] = []
    for _, group in canonical.groupby(identity_columns, dropna=False, sort=False):
        available_dates = sorted(
            {
                pd.Timestamp(value).normalize()
                for value in group["actual_available_date"].dropna()
            }
        )
        if len(group) < 2 or len(available_dates) < 2:
            continue
        changed_fields = tuple(
            column for column in value_columns if _distinct_non_null_values(group[column])
        )
        if not changed_fields:
            continue
        first = group.iloc[0]
        identity = tuple((column, str(first[column])) for column in identity_columns)
        update_series = group.get("update_flag", pd.Series(pd.NA, index=group.index))
        update_flags = tuple(sorted({str(value) for value in update_series.dropna()}))
        ordered = group.copy()
        if "announcement_date" not in ordered.columns:
            ordered["announcement_date"] = pd.NaT
        ordered["_candidate_update_rank"] = pd.to_numeric(
            ordered.get("update_flag", pd.Series(pd.NA, index=ordered.index)), errors="coerce"
        ).fillna(-1)
        ordered = ordered.sort_values(
            ["actual_available_date", "announcement_date", "_candidate_update_rank"],
            kind="stable",
            na_position="last",
        ).drop(columns="_candidate_update_rank")
        candidates.append(
            RevisionCandidate(
                dataset=dataset,
                ts_code=str(first["ts_code"]),
                report_period=pd.Timestamp(first["report_period"]).normalize(),
                identity=identity,
                available_dates=tuple(available_dates),
                update_flags=update_flags,
                changed_fields=changed_fields,
                rows=ordered.reset_index(drop=True),
            )
        )
        if len(candidates) >= max_candidates:
            break
    return tuple(candidates)


def _scalar_equal(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is right or (pd.isna(left) and pd.isna(right))
    try:
        left_missing = bool(pd.isna(left))
        right_missing = bool(pd.isna(right))
    except (TypeError, ValueError):
        left_missing = right_missing = False
    if left_missing or right_missing:
        return left_missing and right_missing
    try:
        return bool(left == right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _one_value(frame: pd.DataFrame, column: str) -> object:
    if len(frame) != 1 or column not in frame.columns:
        return None
    return frame.iloc[0][column]


def validate_revision_candidate(
    candidate: RevisionCandidate,
    *,
    value_column: str | None = None,
) -> RevisionPITCheck:
    """Apply real as-of boundaries to one candidate's canonical rows."""

    selected_column = value_column or candidate.changed_fields[0]
    if selected_column not in candidate.changed_fields:
        raise ValueError(f"value column is not changed in candidate: {selected_column}")
    first_date, revision_date = candidate.available_dates[:2]
    first = select_financial_as_of(
        candidate.rows,
        ts_code=candidate.ts_code,
        as_of_date=first_date,
    )
    before_revision = select_financial_as_of(
        candidate.rows,
        ts_code=candidate.ts_code,
        as_of_date=revision_date - pd.Timedelta(days=1),
    )
    after_revision = select_financial_as_of(
        candidate.rows,
        ts_code=candidate.ts_code,
        as_of_date=revision_date,
    )
    before_first = select_financial_as_of(
        candidate.rows,
        ts_code=candidate.ts_code,
        as_of_date=first_date - pd.Timedelta(days=1),
    )
    first_value = _one_value(first, selected_column)
    before_revision_value = _one_value(before_revision, selected_column)
    revised_value = _one_value(after_revision, selected_column)
    checks = {
        "before_first": before_first.empty,
        "after_first": len(first) == 1,
        "before_revision": len(before_revision) == 1
        and _scalar_equal(before_revision_value, first_value),
        "after_revision": len(after_revision) == 1
        and not _scalar_equal(revised_value, first_value),
    }
    return RevisionPITCheck(
        status="PASS" if all(checks.values()) else "FAIL",
        before_first_empty=checks["before_first"],
        first_version_visible=checks["after_first"],
        before_revision_first=checks["before_revision"],
        after_revision_revised=checks["after_revision"],
        first_available_date=first_date,
        revision_available_date=revision_date,
        value_column=selected_column,
        first_value=first_value,
        revised_value=revised_value,
    )
