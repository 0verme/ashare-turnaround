"""Candidate-local prepared financial semantics shared by feature groups.

The context owns canonical PIT histories and derived quarterized frames for one
``(ts_code, as_of)``.  Frames returned by this module are read-only by
convention: consumers must copy before adding or replacing columns.  The
context never outlives a candidate in replay execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType

import pandas as pd

from .common import canonical_history, single_quarter_history


@dataclass(slots=True)
class FinancialSemanticContext:
    """Prepared, candidate-local ownership boundary for financial semantics."""

    code: str
    as_of_date: str | date | datetime | pd.Timestamp
    _histories: Mapping[str, pd.DataFrame]
    _quarterized: dict[
        tuple[str, tuple[str, ...]], tuple[pd.DataFrame, Mapping[str, str]]
    ] = field(default_factory=dict, repr=False)
    _quarter_projections: dict[tuple[int, str], pd.DataFrame] = field(
        default_factory=dict, repr=False
    )

    @classmethod
    def prepare(
        cls,
        financial_frames: Mapping[str, pd.DataFrame],
        code: str,
        as_of_date: str | date | datetime | pd.Timestamp,
    ) -> FinancialSemanticContext:
        """Canonicalize/select each financial dataset exactly once."""

        histories = {
            dataset: canonical_history(
                dataset,
                financial_frames.get(dataset),
                str(code),
                as_of_date,
            )
            for dataset in ("income", "balancesheet", "cashflow")
        }
        return cls(
            code=str(code),
            as_of_date=as_of_date,
            _histories=MappingProxyType(histories),
        )

    def history(self, dataset: str) -> pd.DataFrame:
        """Return the context-owned immutable canonical history."""

        return self._histories.get(dataset, pd.DataFrame())

    def single_quarter_history(
        self,
        dataset: str,
        fields: tuple[str, ...],
    ) -> tuple[pd.DataFrame, Mapping[str, str]]:
        """Return one aligned wide quarterized frame for all requested fields."""

        history = self.history(dataset)
        available = tuple(
            dict.fromkeys(field_name for field_name in fields if field_name in history)
        )
        key = (dataset, available)
        cached = self._quarterized.get(key)
        if cached is None:
            frame, columns = single_quarter_history(
                self.history(dataset),
                dataset,
                available,
                as_of_date=self.as_of_date,
            )
            cached = (frame, MappingProxyType(dict(columns)))
            self._quarterized[key] = cached
        return cached

    def single_quarter_projection(
        self,
        prepared: tuple[pd.DataFrame, Mapping[str, str]],
        field_name: str,
    ) -> pd.DataFrame:
        """Return a cached immutable field view over one wide quarter frame."""

        frame, columns = prepared
        value_column = columns[field_name]
        key = (id(frame), field_name)
        projected = self._quarter_projections.get(key)
        if projected is None:
            projected = frame.copy()
            projected["comparable_value"] = projected[value_column]
            projected["comparable_raw_value"] = projected[f"{value_column}_raw"]
            projected["comparable_status"] = projected[f"{value_column}_status"]
            projected["comparable_reason"] = projected[f"{value_column}_reason"]
            projected["comparable_source_field"] = field_name
            for provenance_column in (
                "single_quarter_source_periods",
                "single_quarter_source_versions",
                "single_quarter_source_values",
                "single_quarter_availability_dates",
            ):
                projected[provenance_column] = projected[
                    f"{value_column}_{provenance_column}"
                ]
            self._quarter_projections[key] = projected
        return projected


__all__ = ["FinancialSemanticContext"]
