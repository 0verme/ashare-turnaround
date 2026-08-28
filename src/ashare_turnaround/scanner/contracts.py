"""Small serializable contracts shared by feature, score, and report layers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ..pit.comparable import COMPARABLE_PERIOD_CONTRACT_VERSION

# Trend semantics are versioned independently from the period semantics that
# supply their observations.  Keeping the constant at this low-level contract
# avoids an import cycle between ``features.trend`` and ``FeatureVector``.
TURNAROUND_TREND_CONTRACT_VERSION = "turnaround-trend-v2"
TREND_CONTRACT_VERSION = TURNAROUND_TREND_CONTRACT_VERSION


def _merge_metadata(
    target: dict[str, Any],
    source: dict[str, Any],
    *,
    namespace: str | None = None,
) -> None:
    """Merge metadata additively, retaining conflicting producer values."""

    for key, value in source.items():
        if key not in target:
            target[key] = deepcopy(value)
            continue
        existing = target[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_metadata(existing, value, namespace=namespace)
            continue
        if existing == value:
            continue
        prefix = namespace or "merged"
        alternate = f"{prefix}_{key}"
        suffix = 2
        while alternate in target:
            alternate = f"{prefix}_{key}_{suffix}"
            suffix += 1
        target[alternate] = deepcopy(value)


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
    trend_contract_version: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    # Crowding v2 keeps a structured, endpoint-friendly evidence contract.
    semantic_version: str = "features-v1"
    formula: str | None = None
    components: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    # Low Attention v2 uses the additive metadata vocabulary. Keep both
    # surfaces so the independently versioned feature contracts compose.
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Keep the structured components authoritative while exposing the
        # contract's endpoint keys at the artifact boundary as well.
        for key in (
            "start_session",
            "end_session",
            "stock_start",
            "stock_end",
            "benchmark_start",
            "benchmark_end",
            "benchmark_id",
            "stock_return",
            "benchmark_return",
            "excess_return",
        ):
            if key in self.components:
                payload[key] = self.components[key]
        return payload

    def component(self, name: str, default: Any = None) -> Any:
        """Read a derived contract component without flattening evidence."""

        return self.components.get(name, default)


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
    trend_contract_version: str = TURNAROUND_TREND_CONTRACT_VERSION
    feature_contract_versions: dict[str, str] = field(default_factory=dict)
    benchmark_metadata: dict[str, Any] = field(default_factory=dict)
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
        trend_contract_version: str | None = None,
        provenance: dict[str, Any] | None = None,
        semantic_version: str = "features-v1",
        formula: str | None = None,
        components: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        resolved_contract_version = contract_version or self.comparable_period_contract_version
        if resolved_contract_version != self.comparable_period_contract_version:
            raise ValueError("feature evidence uses a different comparable-period contract version")
        if value is None and status == "known":
            status = "unknown"
        if (
            trend_contract_version is not None
            and trend_contract_version != self.trend_contract_version
        ):
            raise ValueError("feature evidence uses a different trend contract version")
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
            trend_contract_version=trend_contract_version,
            provenance=provenance or {},
            semantic_version=semantic_version,
            formula=formula,
            components=dict(components or {}),
            config=dict(config or {}),
            metadata=metadata or {},
        )
        if (
            status
            in {
                "unknown",
                "insufficient_data",
                "insufficient_history",
                "discontinuous",
                "unsupported",
            }
            and name not in self.unknown_features
        ):
            self.unknown_features.append(name)

    def merge(self, other: FeatureVector) -> FeatureVector:
        """Add one feature group without silently losing another contract.

        Feature groups are allowed to share legacy names, but a collision is
        always made explicit. A producer namespace (for example
        ``low_attention_v2``) is preferred; an unnamespaced producer receives
        a stable ``merged_`` prefix. Evidence and unknown-feature references
        follow the same rename map as values.
        """

        if self.ts_code != other.ts_code or self.as_of_date != other.as_of_date:
            raise ValueError("feature vectors must share ts_code and as_of_date")
        if self.comparable_period_contract_version != other.comparable_period_contract_version:
            raise ValueError("feature vectors must share comparable-period contract version")
        if self.trend_contract_version != other.trend_contract_version:
            raise ValueError("feature vectors must share trend contract version")

        for name, version in other.feature_contract_versions.items():
            existing = self.feature_contract_versions.get(name)
            if existing is not None and existing != version:
                raise ValueError(f"feature vectors use different {name} contract versions")
            self.feature_contract_versions[name] = version
        _merge_metadata(
            self.benchmark_metadata,
            other.benchmark_metadata,
            namespace=str(other.metadata.get("namespace") or "") or None,
        )

        namespace = str(other.metadata.get("namespace") or "").strip() or None
        name_map: dict[str, str] = {}
        for name, value in other.values.items():
            target = name
            if target in self.values:
                prefix = namespace or "merged"
                target = f"{prefix}_{name}"
                suffix = 2
                while target in self.values:
                    target = f"{prefix}_{name}_{suffix}"
                    suffix += 1
            name_map[name] = target
            self.values[target] = value
            evidence = other.evidence.get(name)
            if evidence is not None:
                self.evidence[target] = (
                    evidence if target == name else replace(evidence, feature=target)
                )
        _merge_metadata(self.metadata, other.metadata, namespace=namespace)
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
    def expectation_crowding_contract_version(self) -> str | None:
        declared = self.feature_contract_versions.get("expectation_crowding")
        if declared is not None:
            return declared
        metadata = self.metadata.get("expectation_crowding_v2", {})
        return metadata.get("contract_version") or metadata.get(
            "expectation_crowding_contract_version"
        )

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
            "trend_contract_version": self.trend_contract_version,
            "values": dict(self.values),
            "evidence": {key: value.as_dict() for key, value in self.evidence.items()},
            "risk_flags": list(self.risk_flags),
            "rejected_reasons": list(self.rejected_reasons),
            "unknown_features": list(self.unknown_features),
            "feature_contract_versions": dict(self.feature_contract_versions),
            "expectation_crowding_contract_version": self.expectation_crowding_contract_version,
            "benchmark_metadata": dict(self.benchmark_metadata),
            "benchmark_id": self.benchmark_metadata.get("benchmark_id"),
            "benchmark_name": self.benchmark_metadata.get("benchmark_name"),
            "benchmark_contract_version": self.benchmark_metadata.get(
                "benchmark_contract_version", self.benchmark_metadata.get("version")
            ),
            "benchmark_source_dataset": self.benchmark_metadata.get("source_dataset"),
            "metadata": dict(self.metadata),
            "feature_metadata": dict(self.metadata),
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
            "trend_contract_version": vector.trend_contract_version,
            "expectation_crowding_contract_version": vector.expectation_crowding_contract_version,
            "benchmark_id": vector.benchmark_metadata.get("benchmark_id"),
            "benchmark_name": vector.benchmark_metadata.get("benchmark_name"),
            "benchmark_contract_version": vector.benchmark_metadata.get(
                "benchmark_contract_version", vector.benchmark_metadata.get("version")
            ),
            "benchmark_source_dataset": vector.benchmark_metadata.get("source_dataset"),
            "attention_contract_version": vector.metadata.get("low_attention_v2", {}).get(
                "attention_contract_version"
            ),
            "risk_flags": "|".join(vector.risk_flags),
            "rejected_reasons": "|".join(vector.rejected_reasons),
            "unknown_features": "|".join(vector.unknown_features),
            "feature_metadata": dict(vector.metadata),
        }
        row.update(vector.values)
        rows.append(row)
    return pd.DataFrame(rows)
