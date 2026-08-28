"""Versioned comparable-period semantics for financial observations.

This module is deliberately conservative.  It gives a financial observation a
period identity before any derived calculation is attempted.  A row that
cannot be identified, matched, or made point-in-time visible is not guessed;
it produces an explicit ``UNKNOWN`` result and a reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series

COMPARABLE_PERIOD_CONTRACT_VERSION = "comparable-period-v1"
COMPARABLE_FINANCIAL_PERIOD_CONTRACT_VERSION = COMPARABLE_PERIOD_CONTRACT_VERSION

SINGLE_QUARTER = "SINGLE_QUARTER"
CUMULATIVE_YTD = "CUMULATIVE_YTD"
POINT_IN_TIME = "POINT_IN_TIME"
UNKNOWN = "UNKNOWN"

KNOWN_DURATION_SEMANTICS = frozenset({SINGLE_QUARTER, CUMULATIVE_YTD, POINT_IN_TIME})

INVALID_DENOMINATOR = "invalid_denominator"
NEGATIVE_DENOMINATOR = "negative_denominator"

_SEMANTIC_COLUMNS = (
    "fiscal_year",
    "fiscal_period",
    "quarter",
    "report_family",
    "statement_type",
    "duration_semantics",
    "scope",
    "unit",
    "accounting_semantics",
    "period_key",
    "period_semantics_status",
    "period_semantics_reason",
    "source_dataset",
    "source_version_identity",
    "source_version",
    "comparable_period_contract_version",
)

_STATEMENT_TYPES = {
    "income": "INCOME_STATEMENT",
    "cashflow": "CASH_FLOW_STATEMENT",
    "balancesheet": "BALANCE_SHEET",
    "fina_indicator": "FINANCIAL_INDICATOR",
    "fina_mainbz": "MAIN_BUSINESS",
    "forecast": "FORECAST",
    "express": "EXPRESS",
    "fina_audit": "AUDIT",
}

# Tushare's standard financial report_type values distinguish consolidated /
# parent statements and cumulative / single-quarter report families.  The
# mapping is kept here rather than inferred from adjacent report dates.
_REPORT_TYPE_SCOPE = {
    "1": "consolidated",
    "2": "consolidated",
    "3": "consolidated",
    "4": "consolidated",
    "5": "parent_only",
    "6": "parent_only",
    "7": "parent_only",
    "8": "parent_only",
}
_REPORT_TYPE_DURATION = {
    "1": CUMULATIVE_YTD,
    "2": SINGLE_QUARTER,
    "3": SINGLE_QUARTER,
    "4": CUMULATIVE_YTD,
    "5": CUMULATIVE_YTD,
    "6": CUMULATIVE_YTD,
    "7": SINGLE_QUARTER,
    "8": SINGLE_QUARTER,
}

_PERIOD_FIELDS = {"03-31": ("Q1", 1), "06-30": ("H1", 2), "09-30": ("Q3", 3), "12-31": ("FY", 4)}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _token(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip().lower()
    if not text or text in {"nan", "nat", "none", "<na>"}:
        return None
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _clean_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).normalize().strftime("%Y%m%d")
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _numeric(value: Any) -> float | None:
    if _is_missing(value):
        return None
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(parsed) else float(parsed)


def _date(value: Any) -> pd.Timestamp | None:
    if _is_missing(value):
        return None
    parsed = normalize_date_series(pd.Series([value])).iloc[0]
    return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()


def _date_text(value: Any) -> str | None:
    parsed = _date(value)
    return None if parsed is None else parsed.strftime("%Y%m%d")


def _first_value(row: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and not _is_missing(row[name]):
            return row[name]
    return None


def _normalise_duration(value: Any) -> str | None:
    token = _token(value)
    if token is None:
        return None
    compact = token.replace(" ", "")
    if compact in {"singlequarter", "quarterly", "quarter", "single"}:
        return SINGLE_QUARTER
    if compact in {"cumulative", "cumulativeytd", "ytd", "yeartodate", "annual", "fy"}:
        return CUMULATIVE_YTD
    if compact in {"pointintime", "point", "stock", "snapshot", "instant"}:
        return POINT_IN_TIME
    if compact in {"unknown", "unsupported", "na", "n/a"}:
        return UNKNOWN
    if "single" in compact and "quarter" in compact:
        return SINGLE_QUARTER
    if "cumulative" in compact or "yeartodate" in compact or compact.endswith("ytd"):
        return CUMULATIVE_YTD
    if "point" in compact or "snapshot" in compact:
        return POINT_IN_TIME
    return UNKNOWN


def _duration_from_row(
    row: Mapping[str, Any], dataset: str, year: int | None, quarter: int | None
) -> str:
    explicit: list[str] = []
    for name in (
        "duration_semantics",
        "period_semantics",
        "statement_duration",
        "duration_type",
        "duration",
    ):
        if name in row and not _is_missing(row[name]):
            parsed = _normalise_duration(row[name])
            explicit.append(parsed or UNKNOWN)
    for name in ("is_single_quarter", "single_quarter"):
        if name in row and not _is_missing(row[name]):
            if name == "single_quarter" and _numeric(row[name]) not in {0.0, 1.0}:
                continue
            token = _token(row[name])
            if token in {"1", "true", "yes", "y", "single", "single quarter"}:
                explicit.append(SINGLE_QUARTER)
            elif token in {"0", "false", "no", "n"}:
                explicit.append(CUMULATIVE_YTD)
            else:
                explicit.append(UNKNOWN)
    for name in ("is_cumulative", "cumulative", "is_ytd"):
        if name in row and not _is_missing(row[name]):
            token = _token(row[name])
            if token in {"1", "true", "yes", "y", "cumulative", "ytd"}:
                explicit.append(CUMULATIVE_YTD)
            elif token in {"0", "false", "no", "n"}:
                explicit.append(SINGLE_QUARTER)
            else:
                explicit.append(UNKNOWN)

    distinct = set(explicit)
    if len(distinct) > 1:
        return UNKNOWN
    if distinct:
        value = distinct.pop()
        if dataset.removesuffix("_vip") == "balancesheet" and value != POINT_IN_TIME:
            return UNKNOWN
        report_type = _token(row.get("report_type"))
        if report_type in _REPORT_TYPE_DURATION and _REPORT_TYPE_DURATION[report_type] != value:
            return UNKNOWN
        return value

    base = dataset.removesuffix("_vip")
    if base == "balancesheet":
        return POINT_IN_TIME

    report_type = _token(row.get("report_type"))
    if report_type in _REPORT_TYPE_DURATION:
        return _REPORT_TYPE_DURATION[report_type]

    start = _date(_first_value(row, ("start_date", "period_start", "start")))
    if start is not None and year is not None and quarter is not None:
        fiscal_year_start = pd.Timestamp(year=year, month=1, day=1)
        quarter_start = {
            1: fiscal_year_start,
            2: pd.Timestamp(year=year, month=4, day=1),
            3: pd.Timestamp(year=year, month=7, day=1),
            4: pd.Timestamp(year=year, month=10, day=1),
        }[quarter]
        if start == fiscal_year_start:
            return CUMULATIVE_YTD
        if start == quarter_start and quarter > 1:
            return SINGLE_QUARTER

    if base == "balancesheet":
        return POINT_IN_TIME
    if base in {"income", "cashflow"}:
        # The validated standard Tushare income/cashflow endpoints expose
        # cumulative report values for report_type=1 and related cumulative
        # families.  This is a dataset contract, not an adjacent-row guess.
        return CUMULATIVE_YTD
    return UNKNOWN


def _normalise_scope(value: Any) -> str:
    token = _token(value)
    if token is None:
        return "source_default"
    if token.startswith("raw:"):
        return token
    if token == "source default":
        return "source_default"
    compact = token.replace(" ", "")
    if compact in {"consolidated", "consolidation", "合并", "合并报表"}:
        return "consolidated"
    if compact in {"parent", "parentonly", "parentcompany", "母公司", "母公司报表"}:
        return "parent_only"
    if compact in {"unknown", "unsupported", "na", "n/a"}:
        return UNKNOWN
    return f"raw:{token}"


def _scope_from_row(row: Mapping[str, Any]) -> str:
    values: list[str] = []
    for name in ("scope", "consolidation", "consolidated_scope", "entity_scope", "report_scope"):
        if name in row and not _is_missing(row[name]):
            values.append(_normalise_scope(row[name]))
    if "consolidated" in row and not _is_missing(row["consolidated"]):
        token = _token(row["consolidated"])
        if token in {"1", "true", "yes", "y"}:
            values.append("consolidated")
        elif token in {"0", "false", "no", "n"}:
            values.append("parent_only")
        else:
            values.append(UNKNOWN)
    if values and len(set(values)) != 1:
        return UNKNOWN
    if values:
        return values[0]
    report_type = _token(row.get("report_type"))
    return _REPORT_TYPE_SCOPE.get(report_type, "source_default")


def _normalise_unit(value: Any) -> str:
    token = _token(value)
    if token is None:
        return "source_default"
    if token.startswith("raw:") or token.startswith("cny:"):
        return token
    if token == "source default":
        return "source_default"
    compact = token.replace(" ", "")
    if compact in {"cny", "rmb", "yuan", "元", "人民币"}:
        return "cny:1"
    if compact in {"thousandcny", "thousandrmb", "千元"} or "thousandcny" in compact:
        return "cny:1000"
    if compact in {"millioncny", "millionrmb", "百万元"} or "millioncny" in compact:
        return "cny:1000000"
    if compact in {"unknown", "unsupported", "na", "n/a"}:
        return UNKNOWN
    return f"raw:{token}"


def _unit_from_row(row: Mapping[str, Any]) -> str:
    direct = _first_value(row, ("unit", "currency_unit", "data_unit", "unit_name"))
    if direct is not None:
        return _normalise_unit(direct)
    currency = _first_value(row, ("currency", "currency_code"))
    scale = _first_value(row, ("unit_scale", "scale", "scale_factor"))
    if currency is None and scale is None:
        return "source_default"
    currency_text = _normalise_unit(currency or "cny")
    if scale is None:
        return currency_text
    numeric_scale = _numeric(scale)
    if numeric_scale is None:
        return UNKNOWN
    return f"{currency_text.split(':', 1)[0]}:{numeric_scale:g}"


def _normalise_accounting(value: Any) -> str:
    token = _token(value)
    if token is None:
        return "source_default"
    if token.startswith("raw:"):
        return token
    if token == "source default":
        return "source_default"
    if token in {"unknown", "unsupported", "na", "n/a"}:
        return UNKNOWN
    return f"raw:{token}"


def _accounting_from_row(row: Mapping[str, Any]) -> str:
    values = [
        _normalise_accounting(row[name])
        for name in ("accounting_semantics", "accounting_basis", "accounting_standard", "gaap")
        if name in row and not _is_missing(row[name])
    ]
    if values and len(set(values)) != 1:
        return UNKNOWN
    return values[0] if values else "source_default"


def _statement_type_from_row(row: Mapping[str, Any], dataset: str) -> str:
    explicit = _first_value(row, ("statement_type", "statement"))
    if explicit is not None:
        token = _token(explicit)
        return UNKNOWN if token in {"unknown", "unsupported"} else str(explicit).strip()
    return _STATEMENT_TYPES.get(dataset.removesuffix("_vip"), "UNKNOWN")


def _report_family_from_row(row: Mapping[str, Any]) -> str:
    explicit = _first_value(row, ("report_family", "report_fam"))
    if explicit is not None:
        token = _token(explicit)
        if token in {"unknown", "unsupported"}:
            return UNKNOWN
        if token.startswith("raw:"):
            return token
        if token.startswith("report type:"):
            return token.replace("report type:", "report_type:", 1)
        if token.startswith("report_type:"):
            return token
        return f"raw:{token}"
    report_type = _token(row.get("report_type"))
    if report_type is not None:
        return f"report_type:{report_type}"
    family = _first_value(row, ("type", "comp_type", "end_type"))
    token = _token(family)
    return f"raw:{token}" if token is not None else "source_default"


def _period_info(value: Any) -> tuple[pd.Timestamp | None, int | None, str | None, int | None]:
    token = _token(value)
    compact = token.replace(" ", "").upper() if token is not None else ""
    period_match = re.fullmatch(r"(\d{4})(?:Q([1-4])|H1|FY)", compact)
    if period_match:
        fiscal_year = int(period_match.group(1))
        quarter = int(period_match.group(2) or (2 if compact.endswith("H1") else 4))
        month_day = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}[quarter]
        parsed = pd.Timestamp(f"{fiscal_year}{month_day}")
    else:
        parsed = _date(value)
        if parsed is None:
            return None, None, None, None
    fiscal_year = int(parsed.year)
    fiscal_period, quarter = _PERIOD_FIELDS.get(parsed.strftime("%m-%d"), (None, None))
    return parsed, fiscal_year, fiscal_period, quarter


def _identity_hash(row: Mapping[str, Any], dataset: str) -> str:
    excluded = set(_SEMANTIC_COLUMNS) | {
        "retrieved_at",
        "report_period",
        "announcement_date",
        "actual_available_date",
        "available_date_source",
    }
    payload = {
        str(key): _clean_value(value)
        for key, value in sorted(row.items(), key=lambda item: str(item[0]))
        if key not in excluded
    }
    if not any(name in row and _date(row[name]) is not None for name in ("f_ann_date", "ann_date")):
        available = _date_text(row.get("actual_available_date"))
        if available is not None:
            payload["actual_available_date"] = available
    if "end_date" not in row:
        report_period = _date_text(row.get("report_period"))
        if report_period is not None:
            payload["report_period"] = report_period
    encoded = json.dumps(
        {"dataset": dataset, "row": payload}, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return f"{dataset}:{hashlib.sha256(encoded).hexdigest()[:20]}"


def annotate_period_identity(dataset: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Attach canonical period identity columns without dropping raw fields.

    Standard income/cash-flow rows are explicitly classified as cumulative
    YTD unless their source exposes a single-quarter semantic.  Balance-sheet
    rows are point-in-time.  Non-standard report ends and unsupported datasets
    remain ``UNKNOWN``.
    """

    output = frame.reset_index(drop=True).copy()
    period_source = (
        output["end_date"]
        if "end_date" in output.columns
        else output["report_period"]
        if "report_period" in output.columns
        else None
    )
    if period_source is None:
        period_values = [None] * len(output)
    else:
        period_values = [_period_info(value)[0] for value in period_source]
    output["report_period"] = pd.Series(period_values, index=output.index, dtype="datetime64[ns]")

    years: list[int | None] = []
    fiscal_periods: list[str | None] = []
    quarters: list[int | None] = []
    families: list[str] = []
    statements: list[str] = []
    durations: list[str] = []
    scopes: list[str] = []
    units: list[str] = []
    accounting: list[str] = []
    statuses: list[str] = []
    reasons: list[str | None] = []
    period_keys: list[str | None] = []
    versions: list[str] = []
    for row_index, row in output.iterrows():
        parsed, year, fiscal_period, quarter = _period_info(row.get("report_period"))
        if parsed is not None:
            output.at[row_index, "report_period"] = parsed
        duration = _duration_from_row(row, dataset, year, quarter)
        family = _report_family_from_row(row)
        statement = _statement_type_from_row(row, dataset)
        scope = _scope_from_row(row)
        unit = _unit_from_row(row)
        accounting_semantics = _accounting_from_row(row)
        if parsed is None or fiscal_period is None or quarter is None:
            reason = "unsupported_report_period"
        elif duration == UNKNOWN:
            reason = "unsupported_duration_semantics"
        elif family == UNKNOWN:
            reason = "unsupported_report_family"
        elif statement == UNKNOWN:
            reason = "unsupported_statement_type"
        elif scope == UNKNOWN:
            reason = "unknown_scope"
        elif unit == UNKNOWN:
            reason = "unknown_unit"
        elif accounting_semantics == UNKNOWN:
            reason = "unknown_accounting_semantics"
        else:
            reason = None
        years.append(year)
        fiscal_periods.append(fiscal_period)
        quarters.append(quarter)
        families.append(family)
        statements.append(statement)
        durations.append(duration)
        scopes.append(scope)
        units.append(unit)
        accounting.append(accounting_semantics)
        status = "known" if reason is None else "unknown"
        statuses.append(status)
        reasons.append(reason)
        period_keys.append(
            f"{year}Q{quarter}:{duration}" if year is not None and quarter is not None else None
        )
        versions.append(_identity_hash(row, dataset))

    output["fiscal_year"] = pd.Series(years, index=output.index, dtype="Int64")
    output["fiscal_period"] = pd.Series(fiscal_periods, index=output.index, dtype="string")
    output["quarter"] = pd.Series(quarters, index=output.index, dtype="Int64")
    output["report_family"] = pd.Series(families, index=output.index, dtype="string")
    output["statement_type"] = pd.Series(statements, index=output.index, dtype="string")
    output["duration_semantics"] = pd.Series(durations, index=output.index, dtype="string")
    output["scope"] = pd.Series(scopes, index=output.index, dtype="string")
    output["unit"] = pd.Series(units, index=output.index, dtype="string")
    output["accounting_semantics"] = pd.Series(accounting, index=output.index, dtype="string")
    output["period_semantics_status"] = pd.Series(statuses, index=output.index, dtype="string")
    output["period_semantics_reason"] = pd.Series(reasons, index=output.index, dtype="string")
    output["period_key"] = pd.Series(period_keys, index=output.index, dtype="string")
    output["source_dataset"] = dataset
    output["source_version_identity"] = pd.Series(versions, index=output.index, dtype="string")
    output["source_version"] = output["source_version_identity"]
    output["comparable_period_contract_version"] = COMPARABLE_PERIOD_CONTRACT_VERSION
    return output


def _has_identity_columns(frame: pd.DataFrame) -> bool:
    required = {
        "report_period",
        "fiscal_year",
        "quarter",
        "duration_semantics",
        "source_version_identity",
    }
    return required.issubset(frame.columns)


def _prepared_frame(dataset: str, frame: pd.DataFrame) -> pd.DataFrame:
    output = (
        annotate_period_identity(dataset, frame)
        if not _has_identity_columns(frame)
        else frame.reset_index(drop=True).copy()
    )
    output["report_period"] = normalize_date_series(output["report_period"])
    if "announcement_date" in output.columns:
        output["announcement_date"] = normalize_date_series(output["announcement_date"])
    if "actual_available_date" not in output.columns:
        available = pd.Series(pd.NaT, index=output.index, dtype="datetime64[ns]")
        for field_name in ("f_ann_date", "ann_date"):
            if field_name in output.columns:
                available = available.fillna(normalize_date_series(output[field_name]))
        output["actual_available_date"] = available
    else:
        output["actual_available_date"] = normalize_date_series(output["actual_available_date"])
    return output


def _split_provenance(value: Any) -> tuple[str, ...]:
    if _is_missing(value):
        return ()
    return tuple(part for part in str(value).split("|") if part and part.lower() != "nan")


@dataclass(frozen=True, slots=True)
class PeriodIdentity:
    """Canonical economic identity of one financial observation."""

    report_period: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    quarter: int | None
    report_family: str | None
    statement_type: str | None
    duration_semantics: str
    scope: str | None
    unit: str | None
    accounting_semantics: str | None
    source_dataset: str | None
    source_version: str | None
    availability_date: str | None
    contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION

    @property
    def period_key(self) -> str | None:
        if self.fiscal_year is None or self.quarter is None:
            return None
        return f"{self.fiscal_year}Q{self.quarter}:{self.duration_semantics}"

    @property
    def economic_period(self) -> str | None:
        if self.fiscal_year is None or self.quarter is None:
            return None
        return f"{self.fiscal_year}Q{self.quarter}"

    @property
    def is_known(self) -> bool:
        return (
            self.report_period is not None
            and self.fiscal_year is not None
            and self.quarter is not None
            and self.fiscal_period is not None
            and self.duration_semantics in KNOWN_DURATION_SEMANTICS
            and self.report_family not in {None, UNKNOWN}
            and self.statement_type not in {None, UNKNOWN}
            and self.scope not in {None, UNKNOWN}
            and self.unit not in {None, UNKNOWN}
            and self.accounting_semantics not in {None, UNKNOWN}
            and self.source_version not in {None, UNKNOWN}
        )

    def as_single_quarter(self) -> PeriodIdentity:
        if self.quarter is None:
            return self
        return PeriodIdentity(
            report_period=self.report_period,
            fiscal_year=self.fiscal_year,
            fiscal_period=f"Q{self.quarter}",
            quarter=self.quarter,
            report_family=self.report_family,
            statement_type=self.statement_type,
            duration_semantics=SINGLE_QUARTER,
            scope=self.scope,
            unit=self.unit,
            accounting_semantics=self.accounting_semantics,
            source_dataset=self.source_dataset,
            source_version=self.source_version,
            availability_date=self.availability_date,
            contract_version=self.contract_version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_period": self.report_period,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "quarter": self.quarter,
            "report_family": self.report_family,
            "statement_type": self.statement_type,
            "duration_semantics": self.duration_semantics,
            "scope": self.scope,
            "unit": self.unit,
            "accounting_semantics": self.accounting_semantics,
            "period_key": self.period_key,
            "source_dataset": self.source_dataset,
            "source_version": self.source_version,
            "availability_date": self.availability_date,
            "contract_version": self.contract_version,
        }


def identity_unknown_reason(identity: PeriodIdentity) -> str:
    """Return the first explicit reason why an identity cannot be used."""

    if identity.report_period is None or identity.fiscal_period is None or identity.quarter is None:
        return "unsupported_report_period"
    for value, reason in (
        (identity.duration_semantics, "unsupported_duration_semantics"),
        (identity.report_family, "unsupported_report_family"),
        (identity.statement_type, "unsupported_statement_type"),
        (identity.scope, "unknown_scope"),
        (identity.unit, "unknown_unit"),
        (identity.accounting_semantics, "unknown_accounting_semantics"),
        (identity.source_version, "missing_source_version"),
    ):
        if value in {None, UNKNOWN}:
            return reason
    return "insufficient_evidence"


def period_identity(row: Mapping[str, Any], dataset: str = "income") -> PeriodIdentity:
    """Return a :class:`PeriodIdentity` for a raw or annotated row."""

    if not _has_identity_columns(pd.DataFrame([dict(row)])):
        row = annotate_period_identity(dataset, pd.DataFrame([dict(row)])).iloc[0].to_dict()
    year_value = row.get("fiscal_year")
    quarter_value = row.get("quarter")
    year_number = int(year_value) if not _is_missing(year_value) else None
    quarter_number = int(quarter_value) if not _is_missing(quarter_value) else None
    duration_value = row.get("duration_semantics")
    duration = UNKNOWN if _is_missing(duration_value) else str(duration_value)
    return PeriodIdentity(
        report_period=_date_text(row.get("report_period")),
        fiscal_year=year_number,
        fiscal_period=None if _is_missing(row.get("fiscal_period")) else str(row["fiscal_period"]),
        quarter=quarter_number,
        report_family=None if _is_missing(row.get("report_family")) else str(row["report_family"]),
        statement_type=None
        if _is_missing(row.get("statement_type"))
        else str(row["statement_type"]),
        duration_semantics=duration,
        scope=None if _is_missing(row.get("scope")) else str(row["scope"]),
        unit=None if _is_missing(row.get("unit")) else str(row["unit"]),
        accounting_semantics=(
            None
            if _is_missing(row.get("accounting_semantics"))
            else str(row["accounting_semantics"])
        ),
        source_dataset=(
            None if _is_missing(row.get("source_dataset")) else str(row["source_dataset"])
        ),
        source_version=(
            None
            if _is_missing(row.get("source_version_identity"))
            else str(row["source_version_identity"])
        ),
        availability_date=_date_text(row.get("actual_available_date")),
        contract_version=(
            str(row.get("comparable_period_contract_version"))
            if not _is_missing(row.get("comparable_period_contract_version"))
            else COMPARABLE_PERIOD_CONTRACT_VERSION
        ),
    )


@dataclass(frozen=True, slots=True)
class SourceRecord:
    period: str | None
    availability_date: str | None
    source_version: str | None
    value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "availability_date": self.availability_date,
            "source_version": self.source_version,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class SourceReference:
    dataset: str | None
    fields: tuple[str, ...]
    identity: PeriodIdentity
    raw_value: float | None
    source_chain: tuple[SourceRecord, ...] = ()

    @property
    def period(self) -> str | None:
        return self.identity.report_period

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "fields": list(self.fields),
            "period": self.identity.as_dict(),
            "raw_value": self.raw_value,
            "source_chain": [record.as_dict() for record in self.source_chain],
        }


def _source_reference(
    row: Mapping[str, Any],
    *,
    dataset: str,
    fields: tuple[str, ...],
    value_column: str | None,
) -> SourceReference:
    identity = period_identity(row, dataset=dataset)
    raw_value = (
        _numeric(row.get("comparable_raw_value", row.get(value_column))) if value_column else None
    )
    periods = _split_provenance(row.get("single_quarter_source_periods"))
    versions = _split_provenance(row.get("single_quarter_source_versions"))
    availability = _split_provenance(row.get("single_quarter_availability_dates"))
    values = tuple(
        _numeric(value) for value in _split_provenance(row.get("single_quarter_source_values"))
    )
    if not periods:
        periods = (identity.report_period,) if identity.report_period else ()
    if not versions:
        versions = (identity.source_version,) if identity.source_version else ()
    if not availability:
        availability = (identity.availability_date,) if identity.availability_date else ()
    size = max(len(periods), len(versions), len(availability), len(values), 1)
    chain: list[SourceRecord] = []
    for index in range(size):
        chain.append(
            SourceRecord(
                period=periods[index] if index < len(periods) else None,
                availability_date=availability[index] if index < len(availability) else None,
                source_version=versions[index] if index < len(versions) else None,
                value=values[index] if index < len(values) else (raw_value if index == 0 else None),
            )
        )
    return SourceReference(
        dataset=dataset,
        fields=fields,
        identity=identity,
        raw_value=raw_value,
        source_chain=tuple(chain),
    )


def _as_of_timestamp(value: str | date | datetime | pd.Timestamp | None) -> pd.Timestamp | None:
    if value is None:
        return None
    parsed = _date(value)
    if parsed is None:
        raise ValueError(f"invalid as_of_date: {value!r}")
    return parsed


def _row_available(row: Mapping[str, Any]) -> pd.Timestamp | None:
    return _date(row.get("actual_available_date"))


def _version_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    available = _row_available(row)
    announcement = _date(row.get("announcement_date"))
    update = _numeric(row.get("update_flag"))
    return (
        available.value if available is not None else -1,
        announcement.value if announcement is not None else -1,
        update if update is not None else -1,
    )


def _select_visible_candidate(
    candidates: pd.DataFrame,
    *,
    visible_at: pd.Timestamp | None,
    value_column: str | None,
) -> tuple[pd.Series | None, str | None]:
    if candidates.empty:
        return None, "missing_comparable_period"
    rows: list[pd.Series] = []
    for _, row in candidates.iterrows():
        available = _row_available(row)
        if visible_at is not None:
            if available is None:
                return None, "insufficient_evidence"
            if available > visible_at:
                continue
        rows.append(row)
    if not rows:
        return None, "future_disclosure_not_visible"
    rows.sort(
        key=lambda row: (*_version_rank(row), str(row.get("source_version_identity") or "")),
        reverse=True,
    )
    best = rows[0]
    best_rank = _version_rank(best)
    ties = [row for row in rows if _version_rank(row) == best_rank]
    if len(ties) > 1:
        values = [row.get(value_column) for row in ties] if value_column else [None] * len(ties)
        if len({str(_clean_value(value)) for value in values}) > 1:
            return None, "ambiguous_period_chain"
        ties.sort(key=lambda row: str(row.get("source_version_identity") or ""), reverse=True)
        best = ties[0]
    return best, None


def _unknown_match(
    *,
    comparison: str,
    reason: str,
    current_identity: PeriodIdentity | None = None,
    current_row: Mapping[str, Any] | None = None,
    dataset: str = "income",
    value_column: str | None = None,
) -> PeriodMatch:
    row = dict(current_row) if current_row is not None else None
    reference = (
        _source_reference(
            row,
            dataset=dataset,
            fields=(value_column,),
            value_column=value_column,
        )
        if row is not None and value_column
        else None
    )
    current_value = _numeric(row.get(value_column)) if row is not None and value_column else None
    current_raw_value = (
        _numeric(row.get("comparable_raw_value", row.get(value_column)))
        if row is not None and value_column
        else None
    )
    return PeriodMatch(
        status="unknown",
        reason=reason,
        comparison_kind=comparison,
        current_identity=current_identity,
        current_value=current_value,
        current_raw_value=current_raw_value,
        current_reference=reference,
        current_row=row,
    )


@dataclass(frozen=True, slots=True)
class PeriodMatch:
    status: str
    reason: str | None
    comparison_kind: str
    current_identity: PeriodIdentity | None = None
    comparison_identity: PeriodIdentity | None = None
    current_value: float | None = None
    comparison_value: float | None = None
    current_raw_value: float | None = None
    comparison_raw_value: float | None = None
    current_reference: SourceReference | None = None
    comparison_reference: SourceReference | None = None
    current_row: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    comparison_row: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION

    @property
    def current_period(self) -> str | None:
        return self.current_identity.report_period if self.current_identity else None

    @property
    def comparison_period(self) -> str | None:
        return self.comparison_identity.report_period if self.comparison_identity else None

    @property
    def period_semantics(self) -> str | None:
        return self.current_identity.duration_semantics if self.current_identity else None

    @property
    def source_versions(self) -> tuple[str, ...]:
        values: list[str] = []
        for reference in (self.current_reference, self.comparison_reference):
            if reference is None:
                continue
            values.extend(
                record.source_version
                for record in reference.source_chain
                if record.source_version is not None
            )
        return tuple(dict.fromkeys(values))

    @property
    def availability_dates(self) -> tuple[str, ...]:
        values: list[str] = []
        for reference in (self.current_reference, self.comparison_reference):
            if reference is None:
                continue
            values.extend(
                record.availability_date
                for record in reference.source_chain
                if record.availability_date is not None
            )
        return tuple(dict.fromkeys(values))

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "comparison_kind": self.comparison_kind,
            "current_period": self.current_identity.as_dict() if self.current_identity else None,
            "comparison_period": (
                self.comparison_identity.as_dict() if self.comparison_identity else None
            ),
            "current_value": self.current_value,
            "comparison_value": self.comparison_value,
            "current_raw_value": self.current_raw_value,
            "comparison_raw_value": self.comparison_raw_value,
            "current_source": self.current_reference.as_dict() if self.current_reference else None,
            "comparison_source": (
                self.comparison_reference.as_dict() if self.comparison_reference else None
            ),
            "source_versions": list(self.source_versions),
            "availability_dates": list(self.availability_dates),
            "contract_version": self.contract_version,
        }


def match_comparable_period(
    history: pd.DataFrame,
    current: pd.Series | Mapping[str, Any] | int,
    *,
    comparison: str,
    dataset: str = "income",
    value_column: str | None = None,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
) -> PeriodMatch:
    """Match a current observation to a true YoY or QoQ economic period.

    ``previous row`` is never used as a denominator.  YoY requires the same
    quarter and duration in the prior fiscal year.  QoQ requires the previous
    quarter with the same duration; flow statements must already be a validated
    single-quarter series.
    """

    kind = comparison.lower()
    if kind not in {"yoy", "qoq"}:
        return _unknown_match(comparison=kind, reason="unsupported_comparison")
    prepared = _prepared_frame(dataset, history)
    if isinstance(current, int):
        if current < 0 or current >= len(prepared):
            return _unknown_match(comparison=kind, reason="missing_current_period")
        current_row = prepared.iloc[current]
    else:
        current_frame = pd.DataFrame([dict(current)])
        current_row = (
            current
            if _has_identity_columns(current_frame)
            else _prepared_frame(dataset, current_frame).iloc[0]
        )
    current_map = current_row.to_dict() if isinstance(current_row, pd.Series) else dict(current_row)
    identity = period_identity(current_map, dataset=dataset)
    if not identity.is_known:
        reason = identity_unknown_reason(identity)
        return _unknown_match(
            comparison=kind, reason=reason, current_identity=identity, current_row=current_map
        )

    as_of = _as_of_timestamp(as_of_date)
    current_available = _row_available(current_map)
    if as_of is not None:
        if current_available is None:
            return _unknown_match(
                comparison=kind,
                reason="insufficient_evidence",
                current_identity=identity,
                current_row=current_map,
            )
        if current_available > as_of:
            return _unknown_match(
                comparison=kind,
                reason="future_disclosure_not_visible",
                current_identity=identity,
                current_row=current_map,
            )

    base_dataset = dataset.removesuffix("_vip")
    if (
        kind == "qoq"
        and identity.duration_semantics == CUMULATIVE_YTD
        and base_dataset
        in {
            "income",
            "cashflow",
        }
    ):
        return _unknown_match(
            comparison=kind,
            reason="qoq_requires_validated_single_quarter",
            current_identity=identity,
            current_row=current_map,
        )
    if identity.fiscal_year is None or identity.quarter is None:
        return _unknown_match(
            comparison=kind,
            reason="unsupported_report_period",
            current_identity=identity,
            current_row=current_map,
        )
    if kind == "yoy":
        target_year, target_quarter = identity.fiscal_year - 1, identity.quarter
    elif identity.quarter > 1:
        target_year, target_quarter = identity.fiscal_year, identity.quarter - 1
    else:
        target_year, target_quarter = identity.fiscal_year - 1, 4

    if prepared.empty or "ts_code" not in prepared.columns:
        return _unknown_match(
            comparison=kind,
            reason="missing_comparable_period",
            current_identity=identity,
            current_row=current_map,
        )
    code = str(current_map.get("ts_code", ""))
    pool = prepared.loc[
        prepared["ts_code"].astype("string").eq(code)
        & prepared["fiscal_year"].eq(target_year)
        & prepared["quarter"].eq(target_quarter)
    ].copy()
    if pool.empty:
        return _unknown_match(
            comparison=kind,
            reason="missing_comparable_period",
            current_identity=identity,
            current_row=current_map,
        )

    # Return a reason that identifies which semantic boundary rejected an
    # otherwise time-aligned candidate.
    for column, reason in (
        ("duration_semantics", "period_semantics_mismatch"),
        ("report_family", "report_family_mismatch"),
        ("statement_type", "statement_type_mismatch"),
        ("scope", "scope_mismatch"),
        ("unit", "unit_mismatch"),
        ("accounting_semantics", "accounting_semantics_mismatch"),
    ):
        matching = pool.loc[pool[column].astype("string").eq(str(getattr(identity, column)))]
        if matching.empty:
            return _unknown_match(
                comparison=kind,
                reason=reason,
                current_identity=identity,
                current_row=current_map,
            )
        pool = matching

    if "comparable_status" in pool.columns:
        unknown_rows = pool.loc[pool["comparable_status"].astype("string").ne("known")]
        if not unknown_rows.empty and len(unknown_rows) == len(pool):
            reason_value = unknown_rows.iloc[0].get("comparable_reason")
            return _unknown_match(
                comparison=kind,
                reason=str(reason_value or "unsupported_quarterization"),
                current_identity=identity,
                current_row=current_map,
            )

    comparison_row, reason = _select_visible_candidate(
        pool,
        visible_at=as_of or current_available,
        value_column=value_column,
    )
    if comparison_row is None:
        return _unknown_match(
            comparison=kind,
            reason=reason or "missing_comparable_period",
            current_identity=identity,
            current_row=current_map,
        )
    comparison_map = comparison_row.to_dict()
    if (
        "comparable_status" in comparison_map
        and str(comparison_map.get("comparable_status")) != "known"
    ):
        return _unknown_match(
            comparison=kind,
            reason=str(comparison_map.get("comparable_reason") or "unsupported_quarterization"),
            current_identity=identity,
            current_row=current_map,
        )
    current_value = _numeric(current_map.get(value_column)) if value_column else None
    comparison_value = _numeric(comparison_map.get(value_column)) if value_column else None
    current_raw_value = (
        _numeric(current_map.get("comparable_raw_value", current_map.get(value_column)))
        if value_column
        else None
    )
    comparison_raw_value = (
        _numeric(comparison_map.get("comparable_raw_value", comparison_map.get(value_column)))
        if value_column
        else None
    )
    if value_column and current_value is None:
        return _unknown_match(
            comparison=kind,
            reason=str(current_map.get("comparable_reason") or "missing_value"),
            current_identity=identity,
            current_row=current_map,
        )
    if value_column and comparison_value is None:
        return _unknown_match(
            comparison=kind,
            reason=str(comparison_map.get("comparable_reason") or "missing_value"),
            current_identity=identity,
            current_row=current_map,
        )
    current_reference = _source_reference(
        current_map,
        dataset=dataset,
        fields=(value_column,) if value_column else (),
        value_column=value_column,
    )
    comparison_reference = _source_reference(
        comparison_map,
        dataset=dataset,
        fields=(value_column,) if value_column else (),
        value_column=value_column,
    )
    return PeriodMatch(
        status="known",
        reason=None,
        comparison_kind=kind,
        current_identity=identity,
        comparison_identity=period_identity(comparison_map, dataset=dataset),
        current_value=current_value,
        comparison_value=comparison_value,
        current_raw_value=current_raw_value,
        comparison_raw_value=comparison_raw_value,
        current_reference=current_reference,
        comparison_reference=comparison_reference,
        current_row=current_map,
        comparison_row=comparison_map,
    )


@dataclass(frozen=True, slots=True)
class DerivedMetric:
    """A derived value with its semantic and source provenance."""

    metric: str
    value: float | None
    status: str
    reason: str | None = None
    current_identity: PeriodIdentity | None = None
    comparison_identity: PeriodIdentity | None = None
    current_raw_value: float | None = None
    comparison_raw_value: float | None = None
    period_semantics: str | None = None
    source_datasets: tuple[str, ...] = ()
    source_fields: tuple[str, ...] = ()
    source_versions: tuple[str, ...] = ()
    availability_dates: tuple[str, ...] = ()
    source_chain: tuple[SourceRecord, ...] = ()
    contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def current_period(self) -> str | None:
        return self.current_identity.report_period if self.current_identity else None

    @property
    def comparison_period(self) -> str | None:
        return self.comparison_identity.report_period if self.comparison_identity else None

    @property
    def current_availability_date(self) -> str | None:
        return self.current_identity.availability_date if self.current_identity else None

    @property
    def comparison_availability_date(self) -> str | None:
        return self.comparison_identity.availability_date if self.comparison_identity else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "current_period": self.current_identity.as_dict() if self.current_identity else None,
            "comparison_period": (
                self.comparison_identity.as_dict() if self.comparison_identity else None
            ),
            "current_availability_date": self.current_availability_date,
            "comparison_availability_date": self.comparison_availability_date,
            "current_raw_value": self.current_raw_value,
            "comparison_raw_value": self.comparison_raw_value,
            "period_semantics": self.period_semantics,
            "source_datasets": list(self.source_datasets),
            "source_fields": list(self.source_fields),
            "source_versions": list(self.source_versions),
            "availability_dates": list(self.availability_dates),
            "source_chain": [record.as_dict() for record in self.source_chain],
            "contract_version": self.contract_version,
            "provenance": dict(self.provenance),
        }


def _metric_from_match(
    match: PeriodMatch, metric: str, *, source_fields: tuple[str, ...] = ()
) -> DerivedMetric:
    references = tuple(
        reference
        for reference in (match.current_reference, match.comparison_reference)
        if reference
    )
    datasets = tuple(
        dict.fromkeys(reference.dataset for reference in references if reference.dataset)
    )
    versions = match.source_versions
    availability = match.availability_dates
    chain = tuple(record for reference in references for record in reference.source_chain)
    return DerivedMetric(
        metric=metric,
        value=None,
        status=match.status,
        reason=match.reason,
        current_identity=match.current_identity,
        comparison_identity=match.comparison_identity,
        current_raw_value=match.current_raw_value,
        comparison_raw_value=match.comparison_raw_value,
        period_semantics=match.period_semantics,
        source_datasets=datasets,
        source_fields=source_fields
        or tuple(field for reference in references for field in reference.fields),
        source_versions=versions,
        availability_dates=availability,
        source_chain=chain,
        provenance={"period_match": match.as_dict()},
    )


def growth_from_match(match: PeriodMatch, *, metric: str) -> DerivedMetric:
    """Calculate a guarded growth rate from a validated comparable match."""

    result = _metric_from_match(match, metric)
    if match.status != "known":
        return result
    current = match.current_value
    previous = match.comparison_value
    if current is None or previous is None:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason="missing_value",
            current_identity=match.current_identity,
            comparison_identity=match.comparison_identity,
            current_raw_value=match.current_raw_value,
            comparison_raw_value=match.comparison_raw_value,
            period_semantics=match.period_semantics,
            source_datasets=result.source_datasets,
            source_fields=result.source_fields,
            source_versions=result.source_versions,
            availability_dates=result.availability_dates,
            source_chain=result.source_chain,
            provenance=dict(result.provenance),
        )
    transition: str | None = None
    if previous < 0 < current:
        transition = "negative_to_positive"
    elif previous > 0 > current:
        transition = "positive_to_negative"
    elif previous == 0 and current != 0:
        transition = "zero_to_nonzero"
    elif previous != 0 and current == 0:
        transition = "nonzero_to_zero"
    base_provenance = dict(result.provenance)
    base_provenance["sign_transition"] = transition
    if transition == "positive_to_negative":
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason="sign_transition",
            current_identity=match.current_identity,
            comparison_identity=match.comparison_identity,
            current_raw_value=match.current_raw_value,
            comparison_raw_value=match.comparison_raw_value,
            period_semantics=match.period_semantics,
            source_datasets=result.source_datasets,
            source_fields=result.source_fields,
            source_versions=result.source_versions,
            availability_dates=result.availability_dates,
            source_chain=result.source_chain,
            provenance=base_provenance,
        )
    if previous == 0:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason=INVALID_DENOMINATOR,
            current_identity=match.current_identity,
            comparison_identity=match.comparison_identity,
            current_raw_value=match.current_raw_value,
            comparison_raw_value=match.comparison_raw_value,
            period_semantics=match.period_semantics,
            source_datasets=result.source_datasets,
            source_fields=result.source_fields,
            source_versions=result.source_versions,
            availability_dates=result.availability_dates,
            source_chain=result.source_chain,
            provenance=base_provenance,
        )
    if previous < 0:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason=NEGATIVE_DENOMINATOR,
            current_identity=match.current_identity,
            comparison_identity=match.comparison_identity,
            current_raw_value=match.current_raw_value,
            comparison_raw_value=match.comparison_raw_value,
            period_semantics=match.period_semantics,
            source_datasets=result.source_datasets,
            source_fields=result.source_fields,
            source_versions=result.source_versions,
            availability_dates=result.availability_dates,
            source_chain=result.source_chain,
            provenance=base_provenance,
        )
    return DerivedMetric(
        metric=metric,
        value=(current - previous) / previous,
        status="known",
        current_identity=match.current_identity,
        comparison_identity=match.comparison_identity,
        current_raw_value=match.current_raw_value,
        comparison_raw_value=match.comparison_raw_value,
        period_semantics=match.period_semantics,
        source_datasets=result.source_datasets,
        source_fields=result.source_fields,
        source_versions=result.source_versions,
        availability_dates=result.availability_dates,
        source_chain=result.source_chain,
        provenance=base_provenance,
    )


def level_from_row(
    row: Mapping[str, Any],
    *,
    dataset: str,
    value_column: str,
    metric: str,
    fields: tuple[str, ...] | None = None,
) -> DerivedMetric:
    """Expose one validated period value with the same provenance contract."""

    identity = period_identity(row, dataset=dataset)
    reference = _source_reference(
        row,
        dataset=dataset,
        fields=fields or (value_column,),
        value_column=value_column,
    )
    value = _numeric(row.get(value_column))
    reason = (
        None
        if value is not None and identity.is_known
        else ("missing_value" if value is None else identity_unknown_reason(identity))
    )
    return DerivedMetric(
        metric=metric,
        value=value,
        status="known" if reason is None else "unknown",
        reason=reason,
        current_identity=identity,
        current_raw_value=value,
        period_semantics=identity.duration_semantics,
        source_datasets=(dataset,),
        source_fields=fields or (value_column,),
        source_versions=tuple(
            record.source_version for record in reference.source_chain if record.source_version
        ),
        availability_dates=tuple(
            record.availability_date
            for record in reference.source_chain
            if record.availability_date
        ),
        source_chain=reference.source_chain,
    )


def _margin_value(numerator: Any, denominator: Any) -> tuple[float | None, str | None]:
    numerator_value, denominator_value = _numeric(numerator), _numeric(denominator)
    if numerator_value is None:
        return None, "missing_value"
    if denominator_value is None:
        return None, "missing_value"
    if denominator_value == 0:
        return None, INVALID_DENOMINATOR
    if denominator_value < 0:
        return None, NEGATIVE_DENOMINATOR
    return numerator_value / denominator_value, None


def margin_from_row(
    row: Mapping[str, Any],
    *,
    dataset: str,
    numerator_column: str,
    denominator_column: str,
    metric: str,
) -> DerivedMetric:
    identity = period_identity(row, dataset=dataset)
    value, reason = _margin_value(row.get(numerator_column), row.get(denominator_column))
    reference = _source_reference(
        row,
        dataset=dataset,
        fields=(numerator_column, denominator_column),
        value_column=numerator_column,
    )
    return DerivedMetric(
        metric=metric,
        value=value,
        status="known" if reason is None and identity.is_known else "unknown",
        reason=reason or (None if identity.is_known else identity_unknown_reason(identity)),
        current_identity=identity,
        current_raw_value=_numeric(row.get(numerator_column)),
        period_semantics=identity.duration_semantics,
        source_datasets=(dataset,),
        source_fields=(numerator_column, denominator_column),
        source_versions=tuple(
            record.source_version for record in reference.source_chain if record.source_version
        ),
        availability_dates=tuple(
            record.availability_date
            for record in reference.source_chain
            if record.availability_date
        ),
        source_chain=reference.source_chain,
        provenance={
            "margin_definition": "numerator / denominator",
            "numerator": _numeric(row.get(numerator_column)),
            "denominator": _numeric(row.get(denominator_column)),
        },
    )


def margin_yoy_from_match(
    match: PeriodMatch,
    *,
    dataset: str,
    numerator_column: str,
    denominator_column: str,
    metric: str,
) -> DerivedMetric:
    result = _metric_from_match(match, metric, source_fields=(numerator_column, denominator_column))
    if match.status != "known" or match.current_row is None or match.comparison_row is None:
        return result
    current_margin, current_reason = _margin_value(
        match.current_row.get(numerator_column), match.current_row.get(denominator_column)
    )
    comparison_margin, comparison_reason = _margin_value(
        match.comparison_row.get(numerator_column), match.comparison_row.get(denominator_column)
    )
    provenance = dict(result.provenance)
    current_numerator = _numeric(match.current_row.get(numerator_column))
    comparison_numerator = _numeric(match.comparison_row.get(numerator_column))
    current_denominator = _numeric(match.current_row.get(denominator_column))
    comparison_denominator = _numeric(match.comparison_row.get(denominator_column))
    provenance.update(
        {
            "current_margin": current_margin,
            "comparison_margin": comparison_margin,
            "margin_change_definition": "current_margin - comparison_margin",
            "current_raw_values": {
                "numerator": current_numerator,
                "denominator": current_denominator,
            },
            "comparison_raw_values": {
                "numerator": comparison_numerator,
                "denominator": comparison_denominator,
            },
        }
    )
    if current_reason or comparison_reason:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason=current_reason or comparison_reason,
            current_identity=match.current_identity,
            comparison_identity=match.comparison_identity,
            current_raw_value=_numeric(match.current_row.get(numerator_column)),
            comparison_raw_value=_numeric(match.comparison_row.get(numerator_column)),
            period_semantics=match.period_semantics,
            source_datasets=(dataset,),
            source_fields=(numerator_column, denominator_column),
            source_versions=result.source_versions,
            availability_dates=result.availability_dates,
            source_chain=result.source_chain,
            provenance=provenance,
        )
    return DerivedMetric(
        metric=metric,
        value=current_margin - comparison_margin,
        status="known",
        current_identity=match.current_identity,
        comparison_identity=match.comparison_identity,
        current_raw_value=current_margin,
        comparison_raw_value=comparison_margin,
        period_semantics=match.period_semantics,
        source_datasets=(dataset,),
        source_fields=(numerator_column, denominator_column),
        source_versions=result.source_versions,
        availability_dates=result.availability_dates,
        source_chain=result.source_chain,
        provenance=provenance,
    )


def ttm_from_series(
    series: pd.DataFrame,
    *,
    dataset: str,
    value_column: str = "comparable_value",
    end: pd.Series | Mapping[str, Any] | int | None = None,
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    metric: str = "ttm",
) -> DerivedMetric:
    """Sum four consecutive validated single-quarter observations."""

    prepared = _prepared_frame(dataset, series)
    if prepared.empty:
        return DerivedMetric(metric=metric, value=None, status="unknown", reason="missing_quarter")
    as_of = _as_of_timestamp(as_of_date)
    if end is None:
        end_candidates = prepared
        if as_of is not None:
            available = prepared["actual_available_date"].map(_date)
            end_candidates = prepared.loc[available.notna() & available.le(as_of)]
        if end_candidates.empty:
            return DerivedMetric(
                metric=metric,
                value=None,
                status="unknown",
                reason="insufficient_evidence" if as_of is not None else "missing_quarter",
            )
        end_row = end_candidates.sort_values(["fiscal_year", "quarter"], kind="stable").iloc[-1]
    elif isinstance(end, int):
        end_row = prepared.iloc[end]
    else:
        end_frame = pd.DataFrame([dict(end)])
        end_row = (
            end if _has_identity_columns(end_frame) else _prepared_frame(dataset, end_frame).iloc[0]
        )
    end_map = end_row.to_dict() if isinstance(end_row, pd.Series) else dict(end_row)
    end_identity = period_identity(end_map, dataset=dataset)
    if not end_identity.is_known:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason=identity_unknown_reason(end_identity),
            current_identity=end_identity,
        )
    if end_identity.duration_semantics != SINGLE_QUARTER:
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason="ttm_requires_validated_single_quarter",
            current_identity=end_identity,
        )
    if end_identity.fiscal_year is None or end_identity.quarter is None:
        return DerivedMetric(
            metric=metric, value=None, status="unknown", reason="unsupported_report_period"
        )
    expected: list[tuple[int, int]] = []
    year, quarter = end_identity.fiscal_year, end_identity.quarter
    for offset in range(3, -1, -1):
        index = (year * 4 + quarter - 1) - offset
        expected.append((index // 4, index % 4 + 1))
    current_available = _row_available(end_map)
    if as_of is not None and (current_available is None or current_available > as_of):
        return DerivedMetric(
            metric=metric,
            value=None,
            status="unknown",
            reason="insufficient_evidence"
            if current_available is None
            else "future_disclosure_not_visible",
            current_identity=end_identity,
        )
    selected: list[pd.Series] = []
    source_field_values = tuple(
        dict.fromkeys(
            str(value)
            for value in prepared.get("comparable_source_field", pd.Series(dtype="string"))
            if not _is_missing(value)
        )
    )
    source_field = source_field_values[0] if len(source_field_values) == 1 else value_column
    pool = prepared
    for target_year, target_quarter in expected:
        target_pool = pool.loc[
            pool.get("ts_code", pd.Series(dtype="string"))
            .astype("string")
            .eq(str(end_map.get("ts_code", "")))
            & pool["fiscal_year"].eq(target_year)
            & pool["quarter"].eq(target_quarter)
        ]
        candidates = target_pool
        mismatch_reason = "missing_quarter"
        for column, reason_name in (
            ("duration_semantics", "period_semantics_mismatch"),
            ("report_family", "report_family_mismatch"),
            ("statement_type", "statement_type_mismatch"),
            ("scope", "scope_mismatch"),
            ("unit", "unit_mismatch"),
            ("accounting_semantics", "accounting_semantics_mismatch"),
        ):
            matching = candidates.loc[
                candidates[column].astype("string").eq(str(getattr(end_identity, column)))
            ]
            if matching.empty:
                if not target_pool.empty:
                    mismatch_reason = reason_name
                candidates = matching
                break
            candidates = matching
        if "comparable_status" in candidates.columns:
            candidates = candidates.loc[
                candidates["comparable_status"].astype("string").eq("known")
            ]
        row, reason = _select_visible_candidate(
            candidates, visible_at=as_of or current_available, value_column=value_column
        )
        if row is None:
            selected_reason = mismatch_reason if candidates.empty else reason
            return DerivedMetric(
                metric=metric,
                value=None,
                status="unknown",
                reason=(
                    "missing_quarter"
                    if selected_reason == "missing_comparable_period"
                    else selected_reason
                ),
                current_identity=end_identity,
            )
        if _numeric(row.get(value_column)) is None:
            return DerivedMetric(
                metric=metric,
                value=None,
                status="unknown",
                reason=str(row.get("comparable_reason") or "missing_value"),
                current_identity=end_identity,
            )
        selected.append(row)
    references = [
        _source_reference(
            row.to_dict(), dataset=dataset, fields=(source_field,), value_column=value_column
        )
        for row in selected
    ]
    values = [float(_numeric(row.get(value_column))) for row in selected]
    records = tuple(record for reference in references for record in reference.source_chain)
    versions = tuple(
        dict.fromkeys(
            record.source_version for record in records if record.source_version is not None
        )
    )
    availability = tuple(
        dict.fromkeys(
            record.availability_date for record in records if record.availability_date is not None
        )
    )
    source_periods = tuple(
        identity.report_period
        for identity in (period_identity(row.to_dict(), dataset=dataset) for row in selected)
        if identity.report_period is not None
    )
    return DerivedMetric(
        metric=metric,
        value=sum(values),
        status="known",
        current_identity=end_identity,
        current_raw_value=_numeric(end_map.get("comparable_raw_value", end_map.get(value_column))),
        period_semantics=SINGLE_QUARTER,
        source_datasets=(dataset,),
        source_fields=(source_field,),
        source_versions=versions,
        availability_dates=availability,
        source_chain=records,
        provenance={
            "ttm_end_period": end_identity.as_dict(),
            "source_quarters": list(source_periods),
            "source_versions": list(versions),
            "availability_dates": list(availability),
        },
    )


def validated_single_quarter_series(
    frame: pd.DataFrame,
    value_column: str,
    *,
    dataset_kind: str = "income",
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Return only validated single-quarter semantics plus source provenance.

    Cumulative rows use the explicit Q1/H1/Q3/FY chain.  The returned
    ``comparable_value`` is never populated when a chain condition fails.
    ``strict`` is available for callers that prefer an exception at an API
    boundary; feature derivation uses the default fail-closed DataFrame.
    """

    base = dataset_kind.removesuffix("_vip")
    if value_column not in frame.columns:
        raise ValueError(f"missing columns: ['{value_column}']")
    output = _prepared_frame(dataset_kind, frame).copy()
    if base not in {"income", "cashflow"}:
        output["source_duration_semantics"] = output["duration_semantics"]
        output["source_fiscal_period"] = output["fiscal_period"]
        output["source_quarter"] = output["quarter"]
        output["source_period_key"] = output["period_key"]
        output["comparable_value"] = pd.NA
        output["comparable_source_field"] = value_column
        output["comparable_raw_value"] = output[value_column]
        output["comparable_status"] = "unknown"
        output["comparable_reason"] = "unsupported_statement_type"
        output["single_quarter"] = pd.NA
        output["single_quarter_status"] = "unknown"
        output["single_quarter_reason"] = "unsupported_statement_type"
        output["single_quarter_period"] = pd.NA
        output["single_quarter_duration_semantics"] = UNKNOWN
        output["single_quarter_source_periods"] = pd.NA
        output["single_quarter_source_versions"] = pd.NA
        output["single_quarter_source_values"] = pd.NA
        output["single_quarter_availability_dates"] = pd.NA
        output["single_quarter_contract_version"] = COMPARABLE_PERIOD_CONTRACT_VERSION
        return output
    as_of = _as_of_timestamp(as_of_date)
    if as_of is not None and not output.empty:
        identity_columns = [
            column
            for column in (
                "ts_code",
                "fiscal_year",
                "fiscal_period",
                "duration_semantics",
                "report_family",
                "statement_type",
                "scope",
                "unit",
                "accounting_semantics",
            )
            if column in output.columns
        ]
        selected_indices: list[Any] = []
        for _, group in output.groupby(identity_columns, dropna=False, sort=False):
            selected, _ = _select_visible_candidate(
                group,
                visible_at=as_of,
                value_column=value_column,
            )
            if selected is not None:
                selected_indices.append(selected.name)
        output = output.loc[selected_indices].reset_index(drop=True)
    output["source_duration_semantics"] = output["duration_semantics"]
    output["source_fiscal_period"] = output["fiscal_period"]
    output["source_quarter"] = output["quarter"]
    output["source_period_key"] = output["period_key"]
    output["comparable_value"] = pd.NA
    output["comparable_source_field"] = value_column
    output["comparable_status"] = "unknown"
    output["comparable_reason"] = "unsupported_duration_semantics"
    output["single_quarter"] = pd.NA
    output["single_quarter_status"] = output["comparable_status"]
    output["single_quarter_reason"] = output["comparable_reason"]
    output["single_quarter_period"] = pd.NA
    output["single_quarter_duration_semantics"] = UNKNOWN
    output["single_quarter_source_periods"] = pd.NA
    output["single_quarter_source_versions"] = pd.NA
    output["single_quarter_source_values"] = pd.NA
    output["single_quarter_availability_dates"] = pd.NA
    output["single_quarter_contract_version"] = COMPARABLE_PERIOD_CONTRACT_VERSION

    def set_result(
        index: Any, value: Any, status: str, reason: str | None, rows: list[pd.Series]
    ) -> None:
        source_periods = [
            _date_text(row.get("report_period"))
            for row in rows
            if _date_text(row.get("report_period"))
        ]
        source_versions = [
            str(row.get("source_version_identity"))
            for row in rows
            if not _is_missing(row.get("source_version_identity"))
        ]
        source_values = [
            str(_numeric(row.get(value_column)))
            for row in rows
            if _numeric(row.get(value_column)) is not None
        ]
        available = [
            _date_text(row.get("actual_available_date"))
            for row in rows
            if _date_text(row.get("actual_available_date"))
        ]
        output.loc[index, "comparable_value"] = value if value is not None else pd.NA
        output.loc[index, "comparable_status"] = status
        output.loc[index, "comparable_reason"] = reason
        output.loc[index, "single_quarter"] = value if value is not None else pd.NA
        output.loc[index, "single_quarter_status"] = status
        output.loc[index, "single_quarter_reason"] = reason
        output.loc[index, "single_quarter_period"] = (
            f"Q{int(output.loc[index, 'quarter'])}"
            if not _is_missing(output.loc[index, "quarter"])
            else pd.NA
        )
        output.loc[index, "single_quarter_duration_semantics"] = (
            SINGLE_QUARTER if status == "known" else UNKNOWN
        )
        output.loc[index, "single_quarter_source_periods"] = "|".join(source_periods) or pd.NA
        output.loc[index, "single_quarter_source_versions"] = "|".join(source_versions) or pd.NA
        output.loc[index, "single_quarter_source_values"] = "|".join(source_values) or pd.NA
        output.loc[index, "single_quarter_availability_dates"] = "|".join(available) or pd.NA

    for index, row in output.iterrows():
        identity = period_identity(row.to_dict(), dataset=dataset_kind)
        row_available = _row_available(row)
        if as_of is not None and (row_available is None or row_available > as_of):
            set_result(index, None, "unknown", "insufficient_evidence", [])
            continue
        duplicate_mask = (
            output["ts_code"].astype("string").eq(str(row.get("ts_code")))
            & output["fiscal_year"].eq(identity.fiscal_year)
            & output["fiscal_period"].eq(identity.fiscal_period)
            & output["duration_semantics"].eq(identity.duration_semantics)
            & output["report_family"].eq(identity.report_family)
            & output["statement_type"].eq(identity.statement_type)
            & output["scope"].eq(identity.scope)
            & output["unit"].eq(identity.unit)
            & output["accounting_semantics"].eq(identity.accounting_semantics)
        )
        if duplicate_mask.sum() > 1 and row_available is None and as_of is None:
            set_result(index, None, "unknown", "ambiguous_period_chain", [])
            continue
        if not identity.is_known:
            set_result(index, None, "unknown", identity_unknown_reason(identity), [])
            continue
        if identity.duration_semantics == SINGLE_QUARTER:
            value = _numeric(row.get(value_column))
            set_result(
                index,
                value,
                "known" if value is not None else "unknown",
                None if value is not None else "missing_value",
                [row],
            )
            continue
        if identity.duration_semantics != CUMULATIVE_YTD:
            set_result(index, None, "unknown", "unsupported_duration_semantics", [])
            continue
        if identity.fiscal_year is None or identity.quarter is None:
            set_result(index, None, "unknown", "unsupported_report_period", [])
            continue
        predecessor_period = {1: None, 2: "Q1", 3: "H1", 4: "Q3"}[identity.quarter]
        if identity.quarter == 1:
            predecessor = None
        else:
            predecessor = output.loc[
                output["ts_code"].astype("string").eq(str(row.get("ts_code")))
                & output["fiscal_year"].eq(identity.fiscal_year)
                & output["fiscal_period"].eq(predecessor_period)
                & output["duration_semantics"].eq(CUMULATIVE_YTD)
                & output["report_family"].eq(identity.report_family)
                & output["statement_type"].eq(identity.statement_type)
                & output["scope"].eq(identity.scope)
                & output["unit"].eq(identity.unit)
                & output["accounting_semantics"].eq(identity.accounting_semantics)
            ]
            if predecessor.empty:
                same_period = output.loc[
                    output["ts_code"].astype("string").eq(str(row.get("ts_code")))
                    & output["fiscal_year"].eq(identity.fiscal_year)
                    & output["fiscal_period"].eq(predecessor_period)
                ]
                reason = "missing_preceding_cumulative_period"
                if not same_period.empty:
                    for column, mismatch_reason in (
                        ("duration_semantics", "period_semantics_mismatch"),
                        ("statement_type", "statement_type_mismatch"),
                        ("scope", "scope_mismatch"),
                        ("unit", "unit_mismatch"),
                        ("report_family", "report_family_mismatch"),
                        ("accounting_semantics", "accounting_semantics_mismatch"),
                    ):
                        if not same_period[column].eq(identity.__getattribute__(column)).any():
                            reason = mismatch_reason
                            break
                set_result(index, None, "unknown", reason, [])
                continue
            if len(predecessor) > 1:
                visible_at = as_of or _row_available(row)
                selected, selection_reason = _select_visible_candidate(
                    predecessor, visible_at=visible_at, value_column=value_column
                )
                if selected is None:
                    set_result(
                        index, None, "unknown", selection_reason or "ambiguous_period_chain", []
                    )
                    continue
                predecessor = pd.DataFrame([selected])
        current_available = _row_available(row)
        visibility_date = as_of or current_available
        if as_of is not None and (current_available is None or current_available > as_of):
            set_result(index, None, "unknown", "insufficient_evidence", [])
            continue
        if visibility_date is not None and predecessor is not None:
            predecessor_available = _row_available(predecessor.iloc[0])
            if predecessor_available is None:
                set_result(index, None, "unknown", "insufficient_evidence", [])
                continue
            if predecessor_available > visibility_date:
                set_result(index, None, "unknown", "future_disclosure_not_visible", [])
                continue
        current_value = _numeric(row.get(value_column))
        predecessor_value = (
            None if predecessor is None else _numeric(predecessor.iloc[0].get(value_column))
        )
        if current_value is None or (identity.quarter > 1 and predecessor_value is None):
            set_result(
                index,
                None,
                "unknown",
                "missing_value",
                [row] + ([] if predecessor is None else [predecessor.iloc[0]]),
            )
            continue
        value = current_value if predecessor is None else current_value - predecessor_value
        set_result(
            index,
            value,
            "known",
            None,
            [row] + ([] if predecessor is None else [predecessor.iloc[0]]),
        )

    if strict and output["comparable_status"].astype("string").ne("known").any():
        reasons = sorted(
            set(
                output.loc[output["comparable_status"].ne("known"), "comparable_reason"]
                .dropna()
                .astype(str)
            )
        )
        raise ValueError(f"quarterization failed: {','.join(reasons) or 'unknown'}")
    output["comparable_raw_value"] = output[value_column]
    output["duration_semantics"] = output["single_quarter_duration_semantics"]
    output["fiscal_period"] = output["single_quarter_period"]
    output["period_key"] = output.apply(
        lambda row: (
            f"{row['fiscal_year']}Q{int(row['quarter'])}:{SINGLE_QUARTER}"
            if not _is_missing(row["fiscal_year"]) and not _is_missing(row["quarter"])
            else pd.NA
        ),
        axis=1,
    )
    return output


def quarterize_financial_frame(
    frame: pd.DataFrame,
    value_column: str,
    *,
    dataset_kind: str = "income",
    as_of_date: str | date | datetime | pd.Timestamp | None = None,
    strict: bool = False,
) -> pd.DataFrame:
    """Named public entry point for the validated quarterization contract."""

    return validated_single_quarter_series(
        frame,
        value_column,
        dataset_kind=dataset_kind,
        as_of_date=as_of_date,
        strict=strict,
    )


__all__ = [
    "COMPARABLE_FINANCIAL_PERIOD_CONTRACT_VERSION",
    "COMPARABLE_PERIOD_CONTRACT_VERSION",
    "CUMULATIVE_YTD",
    "DerivedMetric",
    "INVALID_DENOMINATOR",
    "NEGATIVE_DENOMINATOR",
    "POINT_IN_TIME",
    "PeriodIdentity",
    "PeriodMatch",
    "SINGLE_QUARTER",
    "SourceRecord",
    "SourceReference",
    "UNKNOWN",
    "annotate_period_identity",
    "growth_from_match",
    "identity_unknown_reason",
    "level_from_row",
    "margin_from_row",
    "margin_yoy_from_match",
    "match_comparable_period",
    "period_identity",
    "quarterize_financial_frame",
    "ttm_from_series",
    "validated_single_quarter_series",
]
