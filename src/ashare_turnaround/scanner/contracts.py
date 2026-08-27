"""Small serializable contracts shared by feature, score, and report layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    """Traceability for one derived value or an explicit unknown result."""

    feature: str
    value: float | int | str | bool | None
    status: str = "known"
    source_datasets: tuple[str, ...] = ()
    source_fields: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()
    availability_dates: tuple[str, ...] = ()
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeatureVector:
    """One security's feature values plus provenance and risk state."""

    ts_code: str
    as_of_date: str
    version: str = "features-v1"
    values: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    evidence: dict[str, FeatureEvidence] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    rejected_reasons: list[str] = field(default_factory=list)
    unknown_features: list[str] = field(default_factory=list)

    def add(
        self,
        name: str,
        value: float | int | str | bool | None,
        *,
        status: str = "known",
        source_datasets: tuple[str, ...] = (),
        source_fields: tuple[str, ...] = (),
        periods: tuple[str, ...] = (),
        availability_dates: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> None:
        self.values[name] = value
        self.evidence[name] = FeatureEvidence(
            feature=name,
            value=value,
            status=status,
            source_datasets=source_datasets,
            source_fields=source_fields,
            periods=periods,
            availability_dates=availability_dates,
            reason=reason,
        )
        if (
            status in {"unknown", "insufficient_data", "unsupported"}
            and name not in self.unknown_features
        ):
            self.unknown_features.append(name)

    def merge(self, other: FeatureVector) -> FeatureVector:
        if self.ts_code != other.ts_code or self.as_of_date != other.as_of_date:
            raise ValueError("feature vectors must share ts_code and as_of_date")
        self.values.update(other.values)
        self.evidence.update(other.evidence)
        for value in (*other.risk_flags,):
            if value not in self.risk_flags:
                self.risk_flags.append(value)
        for value in (*other.rejected_reasons,):
            if value not in self.rejected_reasons:
                self.rejected_reasons.append(value)
        for value in (*other.unknown_features,):
            if value not in self.unknown_features:
                self.unknown_features.append(value)
        return self

    @property
    def rejected(self) -> bool:
        return bool(self.rejected_reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "as_of_date": self.as_of_date,
            "version": self.version,
            "values": dict(self.values),
            "evidence": {key: value.as_dict() for key, value in self.evidence.items()},
            "risk_flags": list(self.risk_flags),
            "rejected_reasons": list(self.rejected_reasons),
            "unknown_features": list(self.unknown_features),
        }


def flatten_feature_vectors(vectors: list[FeatureVector] | tuple[FeatureVector, ...]) -> Any:
    """Return a stable tabular representation suitable for Parquet/CSV output."""

    import pandas as pd

    rows: list[dict[str, Any]] = []
    for vector in vectors:
        row: dict[str, Any] = {
            "ts_code": vector.ts_code,
            "as_of_date": vector.as_of_date,
            "feature_version": vector.version,
            "risk_flags": "|".join(vector.risk_flags),
            "rejected_reasons": "|".join(vector.rejected_reasons),
            "unknown_features": "|".join(vector.unknown_features),
        }
        row.update(vector.values)
        rows.append(row)
    return pd.DataFrame(rows)
