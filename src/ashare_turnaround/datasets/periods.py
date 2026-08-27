"""Report-period generation for historical bootstrap plans."""

from __future__ import annotations

from datetime import date

REPORT_PERIOD_SUFFIXES: tuple[str, ...] = ("0331", "0630", "0930", "1231")


def latest_complete_annual_year(today: date | None = None) -> int:
    """Return the latest conservative complete annual report year.

    An annual report is not treated as complete merely because the calendar
    year has ended.  The default therefore uses the previous calendar year;
    operators can pass ``--end-year`` when a newer period is explicitly known
    to be complete.
    """

    reference = today or date.today()
    return reference.year - 1


def report_periods(start_year: int, end_year: int) -> tuple[str, ...]:
    """Generate Q1, H1, Q3 and FY period values in chronological order."""

    if start_year < 1900 or end_year < 1900:
        raise ValueError("report years must be four-digit years")
    if end_year < start_year:
        raise ValueError("end_year must not be earlier than start_year")
    return tuple(
        f"{year}{suffix}"
        for year in range(start_year, end_year + 1)
        for suffix in REPORT_PERIOD_SUFFIXES
    )


def period_year(period: str) -> int:
    """Return the year component of a validated report period."""

    if len(period) != 8 or not period.isdigit():
        raise ValueError("period must be an 8-digit YYYYMMDD string")
    return int(period[:4])
