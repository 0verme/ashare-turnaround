"""Point-in-time scanner contracts and research workflows."""

from .contracts import FeatureEvidence, FeatureVector, flatten_feature_vectors
from .evidence import (
    EVIDENCE_CONFIDENCE_CONTRACT_VERSION,
    FEATURE_GROUP_ORDER,
    FEATURE_GROUP_REGISTRY,
    FEATURE_GROUP_REGISTRY_VERSION,
    FIELD_TO_GROUP,
    UNKNOWN_EVIDENCE_STATUSES,
    EvidenceAssessment,
    EvidenceConfidenceConfig,
    FeatureGroupSpec,
    assess_evidence_coverage,
)
from .universe import (
    UniverseConfig,
    UniverseDecision,
    UniverseResult,
    build_investable_universe,
)

__all__ = [
    "FeatureEvidence",
    "FeatureVector",
    "EvidenceAssessment",
    "EvidenceConfidenceConfig",
    "FeatureGroupSpec",
    "EVIDENCE_CONFIDENCE_CONTRACT_VERSION",
    "FEATURE_GROUP_ORDER",
    "FEATURE_GROUP_REGISTRY",
    "FIELD_TO_GROUP",
    "FEATURE_GROUP_REGISTRY_VERSION",
    "UNKNOWN_EVIDENCE_STATUSES",
    "assess_evidence_coverage",
    "UniverseConfig",
    "UniverseDecision",
    "UniverseResult",
    "build_investable_universe",
    "flatten_feature_vectors",
]
