"""Small serializable contracts shared by feature, score, and report layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION


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
    current_period: str | None = None
    comparison_period: str | None = None
    current_availability_date: str | None = None
    comparison_availability_date: str | None = None
    current_raw_value: Any = None
    comparison_raw_value: Any = None
    period_semantics: str | None = None
    source_versions: tuple[str, ...] = ()
    contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

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
    comparable_period_contract_version: str = COMPARABLE_PERIOD_CONTRACT_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

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
        current_period: str | None = None,
        comparison_period: str | None = None,
        current_availability_date: str | None = None,
        comparison_availability_date: str | None = None,
        current_raw_value: Any = None,
        comparison_raw_value: Any = None,
        period_semantics: str | None = None,
        source_versions: tuple[str, ...] = (),
        contract_version: str | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        resolved_contract_version = contract_version or self.comparable_period_contract_version
        if resolved_contract_version != self.comparable_period_contract_version:
            raise ValueError("feature evidence uses a different comparable-period contract version")
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
            current_period=current_period,
            comparison_period=comparison_period,
            current_availability_date=current_availability_date,
            comparison_availability_date=comparison_availability_date,
            current_raw_value=current_raw_value,
            comparison_raw_value=comparison_raw_value,
            period_semantics=period_semantics,
            source_versions=source_versions,
            contract_version=resolved_contract_version,
            provenance=provenance or {},
            metadata=metadata or {},
        )
        if (
            status in {"unknown", "insufficient_data", "unsupported"}
            and name not in self.unknown_features
        ):
            self.unknown_features.append(name)

    def merge(self, other: FeatureVector) -> FeatureVector:
        if self.ts_code != other.ts_code or self.as_of_date != other.as_of_date:
            raise ValueError("feature vectors must share ts_code and as_of_date")
        if self.comparable_period_contract_version != other.comparable_period_contract_version:
            raise ValueError("feature vectors must share comparable-period contract version")
        namespace = other.metadata.get("namespace")
        name_map: dict[str, str] = {}
        for name, value in other.values.items():
            target = name
            if namespace and target in self.values:
                target = f"{namespace}_{name}"
                suffix = 2
                while target in self.values:
                    target = f"{namespace}_{name}_{suffix}"
                    suffix += 1
            name_map[name] = target
            self.values[target] = value
            evidence = other.evidence.get(name)
            if evidence is not None:
                self.evidence[target] = (
                    evidence if target == name else replace(evidence, feature=target)
                )
        self.metadata.update(other.metadata)
        for value in (*other.risk_flags,):
            if value not in self.risk_flags:
                self.risk_flags.append(value)
        for value in (*other.rejected_reasons,):
            if value not in self.rejected_reasons:
                self.rejected_reasons.append(value)
        for value in (*other.unknown_features,):
            target = name_map.get(value, value)
            if target not in self.unknown_features:
                self.unknown_features.append(target)
        return self

    @property
    def rejected(self) -> bool:
        return bool(self.rejected_reasons)

    @property
    def feature_metadata(self) -> dict[str, Any]:
        """Compatibility alias for callers that name this field explicitly."""

        return self.metadata

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "as_of_date": self.as_of_date,
            "version": self.version,
            "comparable_period_contract_version": self.comparable_period_contract_version,
            "values": dict(self.values),
            "evidence": {key: value.as_dict() for key, value in self.evidence.items()},
            "risk_flags": list(self.risk_flags),
            "rejected_reasons": list(self.rejected_reasons),
            "unknown_features": list(self.unknown_features),
            "metadata": dict(self.metadata),
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
            "comparable_period_contract_version": vector.comparable_period_contract_version,
            "risk_flags": "|".join(vector.risk_flags),
            "rejected_reasons": "|".join(vector.rejected_reasons),
            "unknown_features": "|".join(vector.unknown_features),
            "feature_metadata": dict(vector.metadata),
        }
        row.update(vector.values)
        rows.append(row)
    return pd.DataFrame(rows)
