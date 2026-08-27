"""Small, non-blocking data-quality checks for Phase 1 datasets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from .datasets.specs import DatasetSpec


@dataclass(frozen=True, slots=True)
class FrameQuality:
    """Checks that can be attached to a bounded API response or sample frame."""

    dataset: str
    rows: int
    missing_required: tuple[str, ...] = ()
    duplicate_identity_rows: int = 0
    null_partition_rows: int = 0
    bad_date_values: tuple[tuple[str, int], ...] = ()
    schema_relation: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether no structural or parseability warning was found."""

        return not self.warnings


def compare_field_sets(left: Iterable[str], right: Iterable[str]) -> str:
    """Describe ``left`` relative to ``right`` for ordinary/VIP schema checks."""

    left_set = set(left)
    right_set = set(right)
    if left_set == right_set:
        return "same"
    if left_set.issuperset(right_set):
        return "superset"
    if left_set.issubset(right_set):
        return "subset"
    return "different"


def _bad_date_count(values: pd.Series) -> int:
    text = values.astype("string").str.strip()
    present = values.notna() & text.notna() & text.ne("")
    if not present.any():
        return 0

    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    eight_digit = text.str.fullmatch(r"\d{8}", na=False)
    if (present & eight_digit).any():
        parsed.loc[present & eight_digit] = pd.to_datetime(
            text.loc[present & eight_digit], format="%Y%m%d", errors="coerce"
        )
    remaining = present & ~eight_digit
    if remaining.any():
        parsed.loc[remaining] = pd.to_datetime(text.loc[remaining], errors="coerce")
    return int((present & parsed.isna()).sum())


def check_frame_quality(
    dataset: str,
    frame: pd.DataFrame,
    spec: DatasetSpec,
    *,
    expected_fields: Iterable[str] | None = None,
) -> FrameQuality:
    """Run a deliberately small set of warnings on one response frame.

    These checks do not silently repair data.  In particular, duplicate rows,
    null partition keys, and schema drift remain visible to the caller.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")

    rows = len(frame)
    warnings: list[str] = []
    missing_required = tuple(field for field in spec.required_fields if field not in frame.columns)
    if missing_required:
        warnings.append("missing_required=" + ",".join(missing_required))
    if rows == 0:
        warnings.append("row_count=0")

    duplicate_identity_rows = 0
    identity_fields = tuple(field for field in spec.primary_keys if field in frame.columns)
    if spec.primary_keys and len(identity_fields) == len(spec.primary_keys):
        duplicate_identity_rows = int(frame.duplicated(list(spec.primary_keys), keep=False).sum())
        if duplicate_identity_rows:
            warnings.append(f"duplicate_identity_rows={duplicate_identity_rows}")
    elif rows:
        warnings.append("duplicate_identity_check_skipped=missing_identity_field")

    null_partition_rows = 0
    if spec.partition_field:
        if spec.partition_field not in frame.columns:
            warnings.append(f"partition_field_missing={spec.partition_field}")
        else:
            null_partition_rows = int(frame[spec.partition_field].isna().sum())
            if null_partition_rows:
                warnings.append(f"null_partition_rows={null_partition_rows}")

    bad_dates: list[tuple[str, int]] = []
    for field in spec.date_fields:
        if field not in frame.columns:
            continue
        count = _bad_date_count(frame[field])
        if count:
            bad_dates.append((field, count))
            warnings.append(f"bad_dates={field}:{count}")

    schema_relation: str | None = None
    if expected_fields is not None:
        schema_relation = compare_field_sets(frame.columns, expected_fields)
        if schema_relation != "same":
            warnings.append(f"schema_drift={schema_relation}")

    return FrameQuality(
        dataset=dataset,
        rows=rows,
        missing_required=missing_required,
        duplicate_identity_rows=duplicate_identity_rows,
        null_partition_rows=null_partition_rows,
        bad_date_values=tuple(bad_dates),
        schema_relation=schema_relation,
        warnings=tuple(warnings),
    )
