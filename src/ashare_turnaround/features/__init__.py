"""Auditable feature groups used by the turnaround scanner."""

from .fundamental import compute_fundamental_features
from .low_attention import (
    LOW_ATTENTION_CONTRACT_VERSION,
    LOW_ATTENTION_V2_FIELDS,
    LOW_ATTENTION_V2_VERSION,
    AbnormalVolumeConfig,
    CrossSectionConfig,
    LiquidityConfig,
    LowAttentionConfig,
    SelfWindowConfig,
    assess_liquidity_eligibility,
    build_cross_section_population,
    classify_low_attention_case,
    compute_low_attention_v2,
    low_attention_sample_report,
    low_attention_sample_report_markdown,
)
from .market import compute_attention_features, compute_crowding_features
from .quality import compute_quality_features
from .trend import (
    TREND_CONTRACT_VERSION,
    calculate_trend,
    compute_trend_features,
)

__all__ = [
    "compute_attention_features",
    "compute_crowding_features",
    "compute_fundamental_features",
    "compute_quality_features",
    "compute_trend_features",
    "TREND_CONTRACT_VERSION",
    "calculate_trend",
    "compute_low_attention_v2",
    "assess_liquidity_eligibility",
    "build_cross_section_population",
    "classify_low_attention_case",
    "low_attention_sample_report",
    "low_attention_sample_report_markdown",
    "LOW_ATTENTION_CONTRACT_VERSION",
    "LOW_ATTENTION_V2_FIELDS",
    "LOW_ATTENTION_V2_VERSION",
    "AbnormalVolumeConfig",
    "CrossSectionConfig",
    "LiquidityConfig",
    "LowAttentionConfig",
    "SelfWindowConfig",
]
