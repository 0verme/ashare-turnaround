"""Small, shared date parsing helpers for Tushare-shaped data."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd

_EIGHT_DIGIT = re.compile(r"^\d{8}$")
_INTEGER_FLOAT = re.compile(r"^(\d+)\.0+$")


def _parsed_series(values: pd.Series, *, format: str | None = None) -> pd.Series:
    parsed = pd.to_datetime(values, format=format, errors="coerce", utc=True)
    result = pd.Series(parsed, index=values.index)
    if isinstance(result.dtype, pd.DatetimeTZDtype):
        result = result.dt.tz_localize(None)
    return result.dt.normalize()


def normalize_date_series(values: pd.Series) -> pd.Series:
    """Parse ``YYYYMMDD`` and ordinary date-like values into naive dates.

    Empty, null, and invalid values become ``NaT``.  Keeping one parser for
    quality checks, partitioning, and PIT selection prevents the modules from
    disagreeing about values such as integer-shaped dates.
    """

    if not isinstance(values, pd.Series):
        raise TypeError("values must be a pandas Series")

    text = values.astype("string").str.strip()
    text = text.str.replace(_INTEGER_FLOAT, r"\1", regex=True)
    present = text.notna() & text.ne("")
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    eight_digit = present & text.str.fullmatch(_EIGHT_DIGIT.pattern, na=False)
    if eight_digit.any():
        result.loc[eight_digit] = _parsed_series(
            text.loc[eight_digit], format="%Y%m%d"
        )

    remaining = present & ~eight_digit
    if remaining.any():
        result.loc[remaining] = _parsed_series(text.loc[remaining], format="mixed")
    return result


def bad_date_count(values: pd.Series) -> int:
    """Count non-empty values that cannot be parsed as dates."""

    text = values.astype("string").str.strip()
    present = values.notna() & text.notna() & text.ne("")
    if not present.any():
        return 0
    return int((present & normalize_date_series(values).isna()).sum())


def date_text(value: Any) -> str | None:
    """Return a validated ``YYYYMMDD`` string for one scalar value.

    Numeric integer-shaped values are accepted because API clients and Parquet
    readers may infer date columns as integers or floats.  Invalid values
    return ``None`` so callers can fail closed instead of creating an
    ``unknown`` partition silently.
    """

    if value is None:
        return None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None

    text = str(value).strip()
    if not text or text.lower() in {"nat", "none", "nan", "<na>"}:
        return None
    integer_float = _INTEGER_FLOAT.fullmatch(text)
    if integer_float:
        text = integer_float.group(1)
    try:
        if _EIGHT_DIGIT.fullmatch(text):
            parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
        elif isinstance(value, (date, datetime, pd.Timestamp)):
            parsed = pd.Timestamp(value)
        else:
            parsed = pd.to_datetime(text, format="mixed", errors="coerce", utc=True)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(parsed):
        return None
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize().strftime("%Y%m%d")
