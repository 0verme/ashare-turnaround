"""Low Attention v2: cross-section-aware, PIT-safe attention proxies.

Scope
-----
This module is a *research* calibration of the v1 low-attention proxies
(``features.market.compute_attention_features``, issue #13).  It does **not**
change the production Turnaround Score v1: ``score.py`` continues to read the
v1 ``attention_score`` field, and the v2 outputs are namespaced so they never
collide with v1 fields.  The v1/v2 boundary is explicit through the semantic
version ``low-attention-v2.0.0``.

Core principle (issue #29)
--------------------------
* low attention != low liquidity
* missing data != low attention

Three distinct concepts are never collapsed into a single ``attention`` field:

* ``self-history``: how a generic activity value ranks against the same
  symbol's *prior* sessions (current session excluded from its own baseline).
* ``cross-sectional``: how a value ranks among the declared population *at the
  same session* ``t`` (PIT-safe, tie convention fixed).
* ``liquidity eligibility``: a separate gate (average traded amount, session
  presence, listing age, exclusion flags).  An extremely inactive but
  non-investable symbol can therefore never be turned into a "low-attention
  opportunity" by its own inactivity.

All percentile definitions, windows, minimums and tie handling are explicit,
configurable and versioned.  No new external data source is required: every
proxy is derived from the existing ``daily`` and ``daily_basic`` tables.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..dates import normalize_date_series
from ..replay_cache import current_replay_cache
from ..scanner.contracts import FeatureVector
from .common import add_known, new_vector, numeric

SEMANTIC_VERSION = "low-attention-v2.0.0"
LOW_ATTENTION_V2_VERSION = SEMANTIC_VERSION
LOW_ATTENTION_CONTRACT_VERSION = SEMANTIC_VERSION
LOW_ATTENTION_V2_FIELDS = (
    "self_turnover_percentile",
    "self_amount_percentile",
    "self_volume_percentile",
    "cross_section_turnover_percentile",
    "cross_section_amount_percentile",
    "cross_section_volume_percentile",
    "abnormal_volume",
    "attention_baseline_change",
    "attention_surge",
    "session_status",
    "liquidity_eligible",
    "liquidity_average_amount",
    "low_attention_v2_score",
    "low_attention_v2_opportunity",
)


# --------------------------------------------------------------------------
# Versioned configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelfWindowConfig:
    """Rolling self-history window used for the self percentiles."""

    version: str = "self-window-v1"
    window: int = 252  # prior valid sessions considered (one trading year, explicit)
    min_valid: int = 21  # below this a self percentile is `unknown` (insufficient history)
    min_listing_days: int = 120  # below this listing age a symbol is `new_listing`

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError("self window must be at least 2 sessions")
        if self.min_valid < 2 or self.min_valid > self.window:
            raise ValueError("min_valid must be between 2 and the window")
        if self.min_listing_days < 0:
            raise ValueError("min_listing_days must be non-negative")


@dataclass(frozen=True, slots=True)
class CrossSectionConfig:
    """Cross-sectional population and tie conventions."""

    version: str = "cross-section-v1"
    population_scope: str = "tradable_market"  # 'tradable_market' | 'investable_universe'
    tie_convention: str = "inclusive"  # percentile = P(X <= x), ties share the rank
    min_population: int = 20  # below this the cross-section is `unknown`

    def __post_init__(self) -> None:
        if self.population_scope not in {"tradable_market", "investable_universe"}:
            raise ValueError(
                "population_scope must be 'tradable_market' or 'investable_universe'"
            )
        if self.tie_convention != "inclusive":
            raise ValueError("tie_convention must be 'inclusive' (deterministic)")
        if self.min_population < 2:
            raise ValueError("min_population must be at least 2")


@dataclass(frozen=True, slots=True)
class AbnormalVolumeConfig:
    """Prior-window baseline for abnormal volume and the attention baseline change."""

    version: str = "abnormal-volume-v1"
    baseline_window: int = 60  # prior sessions used for the median baseline
    min_observations: int = 20  # valid observations required to form a baseline
    max_abnormal_cap: float = 10.0  # extreme outliers are capped and flagged
    max_staleness_days: int = 10  # session lag beyond this is `stale` not just `suspended`

    def __post_init__(self) -> None:
        if self.baseline_window < 2:
            raise ValueError("baseline_window must be at least 2")
        if self.min_observations < 2 or self.min_observations > self.baseline_window:
            raise ValueError("min_observations must be between 2 and baseline_window")
        if self.max_abnormal_cap <= 1.0:
            raise ValueError("max_abnormal_cap must be > 1.0")
        if self.max_staleness_days < 0:
            raise ValueError("max_staleness_days must be non-negative")


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    """Versioned liquidity floor kept independent from attention ranking."""

    version: str = "liquidity-gate-v2"
    average_lookback: int = 20
    min_average_amount: float = 1.0
    require_current_session: bool = True

    def __post_init__(self) -> None:
        if self.average_lookback < 1:
            raise ValueError("average_lookback must be positive")
        if self.min_average_amount < 0:
            raise ValueError("min_average_amount must be non-negative")


@dataclass(frozen=True, slots=True)
class LowAttentionConfig:
    """Aggregate configuration for the Low Attention v2 module."""

    version: str = SEMANTIC_VERSION
    self_window: SelfWindowConfig = field(default_factory=SelfWindowConfig)
    cross_section: CrossSectionConfig = field(default_factory=CrossSectionConfig)
    abnormal_volume: AbnormalVolumeConfig = field(default_factory=AbnormalVolumeConfig)
    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)
    low_attention_cross_percentile: float = 0.30  # threshold for the A/B classification
    attention_surge_ratio: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 < self.low_attention_cross_percentile <= 1.0:
            raise ValueError("low_attention_cross_percentile must be in (0, 1]")
        if self.attention_surge_ratio <= 0:
            raise ValueError("attention_surge_ratio must be positive")

    def declared(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "self_window": as_dict(self.self_window),
            "cross_section": as_dict(self.cross_section),
            "abnormal_volume": as_dict(self.abnormal_volume),
            "liquidity": as_dict(self.liquidity),
            "low_attention_cross_percentile": self.low_attention_cross_percentile,
            "attention_surge_ratio": self.attention_surge_ratio,
        }


def as_dict(value: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(value)


# --------------------------------------------------------------------------
# Session helpers (PIT-safe)
# --------------------------------------------------------------------------


def _as_of_timestamp(value: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid as_of_date: {value!r}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _stable_row_key(row: pd.Series) -> str:
    """Build a row-order-independent key for duplicate market observations."""

    return "\x1f".join(
        f"{column}={row[column]!r}"
        for column in sorted(row.index)
        if column not in {"_date", "_available_date", "_row_key", "_row_completeness"}
    )


def _deduplicate_session_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Choose one deterministic observation per symbol and trading session.

    Raw market datasets are expected to be keyed by ``(ts_code, trade_date)``,
    but research fixtures and append-only stores can contain repeated rows.  A
    stable tie break prevents the result from depending on input row order;
    the most recently available revision wins when availability is supplied,
    followed by the most complete row and a canonical value key.
    """

    if frame.empty or not {"ts_code", "_date"}.issubset(frame.columns):
        return frame
    if not frame.duplicated(["ts_code", "_date"], keep=False).any():
        return frame
    result = frame.copy()
    result["_row_completeness"] = result.notna().sum(axis=1)
    result["_row_key"] = result.apply(_stable_row_key, axis=1)
    sort_columns = ["ts_code", "_date"]
    ascending = [True, True]
    if "actual_available_date" in result.columns:
        result["_available_date"] = normalize_date_series(result["actual_available_date"])
        sort_columns.append("_available_date")
        ascending.append(False)
    sort_columns.extend(["_row_completeness", "_row_key"])
    ascending.extend([False, True])
    result = result.sort_values(sort_columns, ascending=ascending, kind="mergesort")
    # ``first`` coalesces complementary daily/daily_basic rows while keeping
    # deterministic precedence for conflicting non-null revisions.
    result = result.groupby(
        ["ts_code", "_date"], sort=False, as_index=False, dropna=False
    ).first()
    return result.drop(
        columns=["_available_date", "_row_key", "_row_completeness"], errors="ignore"
    )


def _session_rows(
    frame: pd.DataFrame, as_of: pd.Timestamp, *, code: str | None = None
) -> pd.DataFrame:
    """Rows observable by ``as_of``, normalized to one row per symbol/session."""

    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return pd.DataFrame()
    as_of_timestamp = _as_of_timestamp(as_of)
    if code is not None:
        if "ts_code" not in frame.columns:
            return pd.DataFrame()
        # Filter before copying: production replay invokes this helper once
        # per candidate, while the cross-sectional population is prepared once
        # per snapshot below.  A replay-local code index avoids scanning the
        # complete market frame for every candidate.
        cache = current_replay_cache()
        indexed = cache.market_for_code(frame, code) if cache is not None else None
        result = (
            indexed
            if indexed is not None
            else frame.loc[frame["ts_code"].astype("string").eq(str(code))].copy()
        )
    else:
        result = frame.copy()
    dates = normalize_date_series(result["trade_date"])
    result = result.loc[dates.notna() & dates.le(as_of_timestamp)].copy()
    if result.empty:
        return result.assign(_date=pd.Series(dtype="datetime64[ns]"))
    if "actual_available_date" in result.columns:
        raw_available = result["actual_available_date"]
        available = normalize_date_series(raw_available)
        missing_available = raw_available.isna() | (
            raw_available.astype("string")
            .str.strip()
            .str.lower()
            .isin({"", "nan", "nat", "none", "<na>"})
        )
        # Missing availability is retained for legacy market rows; a dated
        # observation is visible only after its declared availability date.
        # A non-empty invalid date fails closed instead of entering the PIT set.
        result = result.loc[
            missing_available | (available.notna() & available.le(as_of_timestamp))
        ].copy()
    result["_date"] = normalize_date_series(result["trade_date"])
    result = _deduplicate_session_rows(result)
    if result.empty:
        return result
    sort_columns = ["_date"]
    if "ts_code" in result.columns:
        sort_columns.append("ts_code")
    return result.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)


def _effective_session_dates(frame: pd.DataFrame, as_of: pd.Timestamp) -> set[str]:
    """Visible market sessions across the supplied frame, at or before as_of.

    Availability filtering is deliberately shared with ``_session_rows`` so a
    row whose publication date is in the future cannot define today's market
    population or make a stale symbol appear suspended.
    """

    visible = _session_rows(frame, pd.Timestamp(as_of).normalize())
    if visible.empty or "_date" not in visible.columns:
        return set()
    return set(visible["_date"].dt.strftime("%Y%m%d").dropna())


def _field_values(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[field], errors="coerce")


# --------------------------------------------------------------------------
# Self-history percentiles
# --------------------------------------------------------------------------


def _prior_rows(history: pd.DataFrame, current_row: pd.Series | None) -> pd.DataFrame:
    """Return rows strictly before the current observation's session."""

    if history.empty or current_row is None:
        return history.iloc[0:0]
    current_date = current_row.get("_date")
    if pd.isna(current_date):
        return history.iloc[0:0]
    return history.loc[history["_date"] < current_date].sort_values(
        "_date", kind="mergesort"
    )


def _self_percentile(
    history: pd.DataFrame,
    current_row: pd.Series | None,
    field: str,
    *,
    cfg: SelfWindowConfig,
) -> tuple[float | None, str | None, int]:
    """Percentile against prior valid sessions, never the current session.

    The strict date comparison is intentional.  Slicing off the last row is
    insufficient when a raw frame contains duplicate observations for one
    session; duplicates are normalized by ``_session_rows`` but this helper
    also remains safe when called with a hand-built history.
    """

    if history.empty or current_row is None:
        return None, "insufficient_self_history", 0
    baseline = _prior_rows(history, current_row).tail(cfg.window)
    values = _field_values(baseline, field).dropna()
    current = numeric(current_row.get(field))
    if current is None:
        return None, "missing_current_field", int(len(values))
    if len(values) < cfg.min_valid:
        return None, "insufficient_self_history", int(len(values))
    percentile = float((values <= current).mean())
    return percentile, None, int(len(values))


# --------------------------------------------------------------------------
# Cross-sectional percentiles
# --------------------------------------------------------------------------


def build_cross_section_population(
    frame: pd.DataFrame,
    *,
    as_of_date: str | date | datetime | pd.Timestamp,
    config: CrossSectionConfig | None = None,
    investable_codes: Container[str] | None = None,
) -> pd.DataFrame:
    """The declared population at session ``t`` (most recent session <= as_of).

    Default scope is the as-of *tradable market*: every symbol with an observed
    session at ``t``.  ``investable_universe`` restricts to an explicit
    pass-list (the existing ``build_investable_universe`` membership is the
    production source of that list).  All rows share one session, so this is
    PIT-safe by construction and deterministic given a snapshot.
    """

    settings = config or CrossSectionConfig()
    as_of = _as_of_timestamp(as_of_date)
    visible = _session_rows(frame, as_of)
    if visible.empty or "_date" not in visible.columns:
        return pd.DataFrame()
    session = visible["_date"].max().strftime("%Y%m%d")
    populated = visible.loc[visible["_date"].dt.strftime("%Y%m%d").eq(session)].copy()
    if settings.population_scope == "investable_universe":
        if investable_codes is None:
            return pd.DataFrame()
        allowed = {str(code) for code in investable_codes} if investable_codes else set()
        if "ts_code" not in populated.columns:
            return pd.DataFrame()
        populated = populated.loc[populated["ts_code"].astype(str).isin(allowed)].copy()
    if "ts_code" not in populated.columns:
        return pd.DataFrame()
    populated = populated.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
    populated.attrs.update(
        {
            "population_scope": settings.population_scope,
            "tie_convention": settings.tie_convention,
            "population_session": session,
            "as_of_date": pd.Timestamp(as_of).strftime("%Y%m%d"),
            "population_policy": "visible_rows_at_effective_session",
            "st_or_stock_basic_policy": "not_consulted",
            "missing_daily_basic_policy": "row_retained_but_field_excluded_per_metric",
            "suspension_policy": "no_row_at_effective_session",
        }
    )
    return populated


def _cross_sectional_percentile(
    population: pd.DataFrame,
    code: str,
    field: str,
    *,
    cfg: CrossSectionConfig,
) -> tuple[float | None, str | None, int]:
    """Inclusive percentile of ``code`` within the population at session ``t``.

    Ties share a rank because ``P(X <= x)`` gives identical values the same
    percentile; this is fixed, deterministic, and documented.
    """

    if population.empty:
        return None, "no_observation_at_session", 0
    target = population.loc[population["ts_code"].astype(str).eq(code)]
    if target.empty:
        return None, "no_observation_at_session", 0
    current = numeric(target.iloc[-1].get(field))
    values = _field_values(population, field)
    clean = values[values.notna()].astype(float)
    population_size = int(len(clean))
    if current is None:
        return None, "missing_current_field", population_size
    if population_size < cfg.min_population:
        return None, "insufficient_population", population_size
    if field not in population.columns:
        return None, "missing_current_field", population_size
    percentile = float((clean <= current).mean())
    return percentile, None, population_size


# --------------------------------------------------------------------------
# Abnormal volume / attention baseline change
# --------------------------------------------------------------------------


def _prior_baseline(
    history: pd.DataFrame,
    field: str,
    *,
    cfg: AbnormalVolumeConfig,
    current_row: pd.Series | None = None,
) -> tuple[float | None, str | None, int]:
    """Median of prior-window values, with the current session excluded."""

    if history.empty:
        return None, "insufficient_baseline", 0
    if current_row is None:
        prior = history.iloc[:-1]
    else:
        prior = _prior_rows(history, current_row)
    prior = prior.tail(cfg.baseline_window)
    values = _field_values(prior, field).dropna()
    if len(values) < cfg.min_observations:
        return None, "insufficient_baseline", int(len(values))
    median = float(values.median())
    return median, None, int(len(values))


def _ratio_versus_baseline(
    current: float | None, baseline: float | None, baseline_reason: str | None
) -> tuple[float | None, str | None]:
    if current is None:
        return None, "missing_current_field"
    if baseline_reason is not None:
        return None, baseline_reason
    if baseline in {None, 0.0}:
        return None, "zero_baseline"
    return current / baseline, None


# --------------------------------------------------------------------------
# Research-side liquidity eligibility (issue #29 anti-bypass guard)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiquidityEligibility:
    """Independent eligibility verdict, separate from any attention aggregation.

    This is a *research* re-statement of the production universe gate for the
    specific purpose of proving that extreme inactivity can never be promoted
    into a low-attention opportunity.  The production gate remains
    ``scanner.universe.build_investable_universe`` with an explicit
    ``min_average_amount`` floor.
    """

    eligible: bool
    average_amount: float | None
    min_average_amount: float
    traded_current_session: bool
    listing_age_days: int | None
    min_listing_days: int
    reasons: tuple[str, ...] = ()
    version: str = "liquidity-gate-v2"


def assess_liquidity_eligibility(
    history: pd.DataFrame,
    current_row: pd.Series | None,
    *,
    average_lookback: int = 20,
    min_average_amount: float,
    listing_age_days: int | None,
    min_listing_days: int,
    require_current_session: bool = True,
    amended_reasons: tuple[str, ...] = (),
) -> LiquidityEligibility:
    """Evaluate the independent liquidity floor for one symbol.

    ``average_lookback`` uses the trailing amount average (investable-universe
    convention).  A symbol suspended at the current session, with a sub-floor
    average traded amount, or with a listing age below the floor is not
    ``eligible`` regardless of how low its attention proxies are.
    """

    reasons = list(amended_reasons)
    amount_average: float | None = None
    if history.empty or "amount" not in history.columns:
        reasons.append("unknown_liquidity")
        amount_average = None
    else:
        lookback = history.tail(average_lookback)
        values = _field_values(lookback, "amount").dropna()
        if len(values) == 0:
            reasons.append("unknown_liquidity")
        else:
            amount_average = float(values.mean())
            if amount_average < min_average_amount:
                reasons.append("low_liquidity")
    if current_row is not None:
        traded = bool(
            pd.notna(current_row.get("_session_matches")) and current_row["_session_matches"]
        )
        current_amount = numeric(current_row.get("amount"))
        if traded and current_amount is None:
            reasons.append("unknown_current_liquidity")
        elif traded and current_amount <= 0:
            reasons.append("no_trading_activity")
    else:
        traded = False
    if listing_age_days is not None and listing_age_days < min_listing_days:
        reasons.append("new_listing")
    if require_current_session and not traded:
        reasons.append("no_session_at_decision")
    return LiquidityEligibility(
        eligible=not reasons,
        average_amount=amount_average,
        min_average_amount=min_average_amount,
        traded_current_session=traded,
        listing_age_days=listing_age_days,
        min_listing_days=min_listing_days,
        reasons=tuple(dict.fromkeys(reasons)),
        version="liquidity-gate-v2",
    )


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


def compute_low_attention_v2(
    market_frame: pd.DataFrame | None,
    code: str,
    as_of_date: str | date | datetime | pd.Timestamp,
    *,
    config: LowAttentionConfig | None = None,
    list_date: str | date | datetime | pd.Timestamp | None = None,
    investable_codes: Container[str] | None = None,
    population_frame: pd.DataFrame | None = None,
) -> FeatureVector:
    """Compute the Low Attention v2 self/cross-sectional/abnormal evidence.

    Every value is stored with full evidence (raw value, observation date,
    as-of date, self window or cross population, percentile, valid count,
    population count, missing/stale reasons, source and semantic version).
    """

    settings = config or LowAttentionConfig()
    self_cfg = settings.self_window
    cross_cfg = settings.cross_section
    abnormal_cfg = settings.abnormal_volume
    liquidity_cfg = settings.liquidity
    attention_version = settings.version

    vector = new_vector(code, as_of_date)
    vector.version = attention_version
    as_of = _as_of_timestamp(as_of_date)
    as_of_text = as_of.strftime("%Y%m%d")
    vector.metadata["namespace"] = "low_attention_v2"
    vector.metadata["low_attention_v2"] = {
        "namespace": "low_attention_v2",
        "contract_version": attention_version,
        "attention_contract_version": attention_version,
        "low_attention_version": attention_version,
        "config": settings.declared(),
        "fields": list(LOW_ATTENTION_V2_FIELDS),
        "source_datasets": ["daily", "daily_basic"],
        "as_of_date": as_of_text,
    }

    frame = market_frame if market_frame is not None else pd.DataFrame()
    parsed_list_date: pd.Timestamp | None = None
    if list_date is not None and pd.notna(list_date):
        parsed = pd.to_datetime(list_date, errors="coerce")
        if pd.notna(parsed):
            parsed_list_date = pd.Timestamp(parsed)
            if parsed_list_date.tzinfo is not None:
                parsed_list_date = parsed_list_date.tz_localize(None)
            parsed_list_date = parsed_list_date.normalize()
    history = _session_rows(frame, as_of, code=code)
    if parsed_list_date is not None and not history.empty:
        history = history.loc[history["_date"] >= parsed_list_date].reset_index(drop=True)
    if population_frame is not None and not population_frame.empty:
        declared_session = population_frame.attrs.get("population_session")
        effective_session = str(declared_session) if declared_session is not None else None
        sessions = {effective_session} if effective_session is not None else set()
    else:
        sessions = _effective_session_dates(frame, as_of)
        effective_session = max(sessions) if sessions else None
    listing_age_days: int | None = (
        int((as_of - parsed_list_date).days) if parsed_list_date is not None else None
    )
    vector.metadata["low_attention_v2"].update(
        {
            "effective_session": effective_session,
            "population_scope": cross_cfg.population_scope,
            "tie_convention": cross_cfg.tie_convention,
            "list_date": (
                parsed_list_date.strftime("%Y%m%d") if parsed_list_date is not None else None
            ),
            "listing_age_days": listing_age_days,
            "population_policy": {
                "membership": "visible_market_row_at_effective_session",
                "st_or_stock_basic": "not_consulted; retained_if_row_exists",
                "suspension": "no_row_at_effective_session_is_excluded",
                "missing_daily_basic": "retained_for_other_fields; excluded_per_missing_metric",
                "zero_activity_row": "retained_in_population; liquidity_gate_handles_eligibility",
                "listing_day": "not_inferred_from_population",
                "bse": "not_consulted; universe_policy_is_external_to_attention",
            },
        }
    )

    # session status -----------------------------------------------------
    session_status = "no_data"
    staleness_days: int | None = None
    current_row: pd.Series | None = None
    if not history.empty:
        latest_date = history["_date"].iloc[-1]
        latest_text = latest_date.strftime("%Y%m%d")
        current_row = history.iloc[-1].copy()
        if effective_session is not None:
            effective_dt = pd.to_datetime(effective_session, format="%Y%m%d")
            staleness_days = int((effective_dt - latest_date).days)
            session_status = "traded" if latest_text == effective_session else (
                "stale" if staleness_days and staleness_days > abnormal_cfg.max_staleness_days
                else "suspended_session"
            )
        else:
            session_status = "stale" if staleness_days else "no_data"
    current_row_df = current_row
    if current_row_df is not None:
        current_row_df["_session_matches"] = session_status == "traded"

    # session status is informational but *known*: it records whether the
    # symbol actually traded on the effective decision session and how stale
    # its latest observation is.  It is never treated as a missing numeric.
    latest_observation = (
        history["_date"].iloc[-1].strftime("%Y%m%d") if not history.empty else None
    )
    vector.add(
        "session_status",
        session_status,
        status="known",
        source_datasets=("daily", "daily_basic"),
        source_fields=("trade_date",),
        periods=(),
        availability_dates=(),
        reason=None,
        metadata={
            "attention_contract_version": attention_version,
            "as_of_date": as_of_text,
            "observation_date": latest_observation,
            "effective_session": effective_session,
            "staleness_days": staleness_days,
            "session_presence_policy": "latest_visible_row_at_effective_session",
        },
    )

    # liquidity eligibility (independent of attention aggregation) --------
    liquidity = assess_liquidity_eligibility(
        history,
        current_row_df,
        average_lookback=liquidity_cfg.average_lookback,
        min_average_amount=liquidity_cfg.min_average_amount,
        listing_age_days=listing_age_days,
        min_listing_days=self_cfg.min_listing_days,
        require_current_session=liquidity_cfg.require_current_session,
    )
    liquidity_metadata = {
        "name": "liquidity_eligible",
        "reference_type": "LIQUIDITY_GATE",
        "reference_window": liquidity_cfg.average_lookback,
        "reference_population": "same_symbol_trailing_sessions",
        "history_count": int(len(history)),
        "population_count": None,
        "raw_value": liquidity.average_amount,
        "value": liquidity.eligible,
        "status": "known",
        "reason": "|".join(liquidity.reasons) or None,
        "valid_observation_count": int(
            _field_values(history, "amount").notna().sum()
        ) if "amount" in history.columns else 0,
        "required_history": liquidity_cfg.average_lookback,
        "observation_date": latest_observation,
        "as_of_date": as_of_text,
        "source_dataset": "daily_basic",
        "source_fields": ["amount"],
        "attention_contract_version": attention_version,
        "semantic_version": attention_version,
        "warnings": "|".join(liquidity.reasons) or None,
        "average_amount": liquidity.average_amount,
        "min_average_amount": liquidity.min_average_amount,
        "reasons": list(liquidity.reasons),
        "listing_age_days": liquidity.listing_age_days,
        "traded_current_session": liquidity.traded_current_session,
        "version": liquidity.version,
    }
    vector.add(
        "liquidity_eligible",
        liquidity.eligible,
        status="known",
        source_datasets=("daily_basic",),
        source_fields=("amount",),
        reason="|".join(liquidity.reasons) or None,
        metadata=liquidity_metadata,
    )
    vector.add(
        "liquidity_average_amount",
        liquidity.average_amount,
        status="known" if liquidity.average_amount is not None else "unknown",
        source_datasets=("daily_basic",),
        source_fields=("amount",),
        reason="unknown_liquidity" if liquidity.average_amount is None else None,
        metadata=liquidity_metadata,
    )

    # self-history proxies -------------------------------------------------
    is_new_listing = listing_age_days is not None and listing_age_days < self_cfg.min_listing_days
    self_turnover, self_turnover_reason, self_turnover_valid = _self_percentile(
        history, current_row_df, "turnover_rate", cfg=self_cfg
    )
    self_amount, self_amount_reason, self_amount_valid = _self_percentile(
        history, current_row_df, "amount", cfg=self_cfg
    )
    self_volume, self_volume_reason, self_volume_valid = _self_percentile(
        history, current_row_df, "vol", cfg=self_cfg
    )
    # Session policy is stronger than a stale self rank: a suspended/stale
    # observation is never an attention observation at today's decision point.
    if session_status != "traded":
        session_reason = {
            "stale": "stale_data",
            "suspended_session": "suspended_session",
        }.get(session_status, "missing_history")
        self_turnover = self_amount = self_volume = None
        self_turnover_reason = self_amount_reason = self_volume_reason = session_reason
        self_turnover_valid = self_amount_valid = self_volume_valid = 0
    # A fresh listing is a first-class `unknown` state, not a percentile of
    # its own (possibly long but irrelevant) warm-up history.
    elif is_new_listing:
        self_turnover = self_amount = self_volume = None
        self_turnover_reason = self_amount_reason = self_volume_reason = "new_listing"
        self_turnover_valid = self_amount_valid = self_volume_valid = 0

    prior_history_count = int(len(_prior_rows(history, current_row_df)))
    self_fields = {
        "self_turnover_percentile": ("turnover_rate", ("daily_basic",)),
        "self_amount_percentile": ("amount", ("daily_basic",)),
        "self_volume_percentile": ("vol", ("daily",)),
    }
    for name, value, reason, valid in (
        ("self_turnover_percentile", self_turnover, self_turnover_reason, self_turnover_valid),
        ("self_amount_percentile", self_amount, self_amount_reason, self_amount_valid),
        ("self_volume_percentile", self_volume, self_volume_reason, self_volume_valid),
    ):
        raw_field, datasets = self_fields[name]
        raw_current = numeric(current_row_df.get(raw_field)) if current_row_df is not None else None
        add_known(
            vector,
            name,
            value,
            datasets=datasets,
            fields=(raw_field,),
            history=history,
            reason=reason,
            metadata=_proxy_metadata(
                name=name,
                kind="self",
                reference_type="SELF_HISTORY",
                reference_population="same_symbol_prior_sessions",
                as_of=as_of_text,
                observation=latest_observation,
                value=value,
                percentile=value,
                valid_count=valid,
                history_count=prior_history_count,
                observed_count=int(len(history)),
                required_history=self_cfg.min_valid,
                population_count=None,
                window=self_cfg.window,
                source="|".join(datasets),
                source_fields=(raw_field,),
                semantic=attention_version,
                reason=reason,
                extra={"raw_current_value": raw_current},
            ),
        )

    # abnormal volume + attention baseline change (prior-window baselines) --
    current_volume = numeric(current_row_df.get("vol")) if current_row_df is not None else None
    current_turnover = (
        numeric(current_row_df.get("turnover_rate")) if current_row_df is not None else None
    )
    vol_baseline, vol_baseline_reason, vol_baseline_valid = _prior_baseline(
        history, "vol", cfg=abnormal_cfg, current_row=current_row_df
    )
    turnover_baseline, turnover_reason, turnover_valid = _prior_baseline(
        history, "turnover_rate", cfg=abnormal_cfg, current_row=current_row_df
    )
    abnormal, abnormal_reason = _ratio_versus_baseline(
        current_volume, vol_baseline, vol_baseline_reason
    )
    baseline_change, baseline_change_reason = _ratio_versus_baseline(
        current_turnover, turnover_baseline, turnover_reason
    )
    raw_abnormal_ratio = abnormal
    abnormal_capped = False
    if session_status != "traded":
        session_reason = {
            "stale": "stale_data",
            "suspended_session": "suspended_session",
        }.get(session_status, "missing_history")
        abnormal = None
        abnormal_reason = session_reason
        baseline_change = None
        baseline_change_reason = session_reason
        raw_abnormal_ratio = None
    if abnormal is not None and abnormal > abnormal_cfg.max_abnormal_cap:
        abnormal = float(abnormal_cfg.max_abnormal_cap)
        abnormal_capped = True
        vector.risk_flags.append("abnormal_volume_capped")
    vector.metadata["low_attention_v2"]["research_only_risk_flags"] = [
        flag for flag in vector.risk_flags if flag == "abnormal_volume_capped"
    ]
    add_known(
        vector,
        "abnormal_volume",
        abnormal,
        datasets=("daily",),
        fields=("vol",),
        history=history,
        reason=abnormal_reason,
        metadata=_proxy_metadata(
            name="abnormal_volume",
            kind="prior_baseline",
            reference_type="PRIOR_BASELINE",
            reference_population="same_symbol_prior_sessions",
            as_of=as_of_text,
            observation=latest_observation,
            value=abnormal,
            percentile=None,
            valid_count=vol_baseline_valid,
            history_count=prior_history_count,
            observed_count=int(len(history)),
            required_history=abnormal_cfg.min_observations,
            population_count=None,
            window=abnormal_cfg.baseline_window,
            source="daily",
            source_fields=("vol",),
            semantic=attention_version,
            reason=abnormal_reason,
            extra={
                "raw_current_value": current_volume,
                "baseline_median_volume": vol_baseline,
                "current_volume": current_volume,
                "raw_ratio": raw_abnormal_ratio,
                "capped": abnormal_capped,
                "max_abnormal_cap": abnormal_cfg.max_abnormal_cap,
            },
        ),
    )
    add_known(
        vector,
        "attention_baseline_change",
        baseline_change,
        datasets=("daily_basic",),
        fields=("turnover_rate",),
        history=history,
        reason=baseline_change_reason,
        metadata=_proxy_metadata(
            name="attention_baseline_change",
            kind="prior_baseline",
            reference_type="PRIOR_BASELINE",
            reference_population="same_symbol_prior_sessions",
            as_of=as_of_text,
            observation=latest_observation,
            value=baseline_change,
            percentile=None,
            valid_count=turnover_valid,
            history_count=prior_history_count,
            observed_count=int(len(history)),
            required_history=abnormal_cfg.min_observations,
            population_count=None,
            window=abnormal_cfg.baseline_window,
            source="daily_basic",
            source_fields=("turnover_rate",),
            semantic=attention_version,
            reason=baseline_change_reason,
            extra={
                "raw_current_value": current_turnover,
                "baseline_median_turnover": turnover_baseline,
                "current_turnover": current_turnover,
            },
        ),
    )

    # cross-sectional proxies -----------------------------------------------
    population = (
        population_frame
        if population_frame is not None
        else build_cross_section_population(
            frame,
            as_of_date=as_of,
            config=cross_cfg,
            investable_codes=investable_codes,
        )
    )
    cross_turnover, cross_turnover_reason, cross_turnover_pop = _cross_sectional_percentile(
        population, code, "turnover_rate", cfg=cross_cfg
    )
    cross_amount, cross_amount_reason, cross_amount_pop = _cross_sectional_percentile(
        population, code, "amount", cfg=cross_cfg
    )
    cross_volume, cross_volume_reason, cross_volume_pop = _cross_sectional_percentile(
        population, code, "vol", cfg=cross_cfg
    )

    cross_fields = {
        "cross_section_turnover_percentile": ("turnover_rate", ("daily_basic",)),
        "cross_section_amount_percentile": ("amount", ("daily_basic",)),
        "cross_section_volume_percentile": ("vol", ("daily",)),
    }
    for name, value, reason, pop_count in (
        (
            "cross_section_turnover_percentile",
            cross_turnover,
            cross_turnover_reason,
            cross_turnover_pop,
        ),
        (
            "cross_section_amount_percentile",
            cross_amount,
            cross_amount_reason,
            cross_amount_pop,
        ),
        (
            "cross_section_volume_percentile",
            cross_volume,
            cross_volume_reason,
            cross_volume_pop,
        ),
    ):
        raw_field, datasets = cross_fields[name]
        raw_current = numeric(current_row_df.get(raw_field)) if current_row_df is not None else None
        population_name = (
            "tradable_market_at_effective_session"
            if cross_cfg.population_scope == "tradable_market"
            else "investable_universe_at_effective_session"
        )
        add_known(
            vector,
            name,
            value,
            datasets=datasets,
            fields=(raw_field,),
            history=population,
            reason=reason,
            metadata=_proxy_metadata(
                name=name,
                kind="cross_sectional",
                reference_type="CROSS_SECTION",
                reference_population=population_name,
                as_of=as_of_text,
                observation=effective_session,
                value=value,
                percentile=value,
                valid_count=None,
                history_count=0,
                observed_count=int(len(population)),
                required_history=cross_cfg.min_population,
                population_count=pop_count,
                window=None,
                source="|".join(datasets),
                source_fields=(raw_field,),
                semantic=attention_version,
                reason=reason,
                extra={
                    "population_scope": cross_cfg.population_scope,
                    "tie_convention": cross_cfg.tie_convention,
                    "population_session": effective_session,
                    "population_row_count": int(len(population)),
                    "valid_population_count": pop_count,
                    "population_policy": "visible_market_row_at_effective_session",
                    "st_or_stock_basic_policy": "not_consulted",
                    "bse_policy": "not_consulted",
                    "raw_current_value": raw_current,
                    "missing_field_policy": "excluded_from_metric_population",
                    "suspension_policy": "no_row_at_effective_session",
                },
            ),
        )

    # A volume/turnover surge is positive attention (or crowding) evidence,
    # not a low-attention observation.  It is kept outside the four-percentile
    # research aggregate so the aggregate remains transparent and comparable.
    surge_ratios = tuple(
        value for value in (raw_abnormal_ratio, baseline_change) if value is not None
    )
    attention_surge = (
        any(value >= settings.attention_surge_ratio for value in surge_ratios)
        if surge_ratios
        else None
    )
    attention_surge_reason = (
        {
            "stale": "stale_data",
            "suspended_session": "suspended_session",
        }.get(session_status, "missing_history")
        if session_status != "traded"
        else "missing_attention_baseline"
        if attention_surge is None
        else None
    )
    vector.add(
        "attention_surge",
        attention_surge,
        status="known" if attention_surge is not None else "unknown",
        source_datasets=("daily", "daily_basic"),
        source_fields=("vol", "turnover_rate"),
        periods=(),
        availability_dates=(),
        reason=attention_surge_reason,
        metadata={
            "name": "attention_surge",
            "reference_type": "PRIOR_BASELINE",
            "reference_window": abnormal_cfg.baseline_window,
            "reference_population": "same_symbol_prior_sessions",
            "raw_value": raw_abnormal_ratio,
            "value": attention_surge,
            "status": "known" if attention_surge is not None else "unknown",
            "reason": attention_surge_reason,
            "valid_observation_count": max(vol_baseline_valid, turnover_valid),
            "required_history": abnormal_cfg.min_observations,
            "history_count": prior_history_count,
            "population_count": None,
            "observation_date": latest_observation,
            "as_of_date": as_of_text,
            "source_dataset": "daily|daily_basic",
            "source_fields": ["vol", "turnover_rate"],
            "attention_contract_version": attention_version,
            "semantic_version": attention_version,
            "warnings": attention_surge_reason,
            "attention_surge_ratio": settings.attention_surge_ratio,
            "abnormal_volume_ratio": raw_abnormal_ratio,
            "attention_baseline_change": baseline_change,
        },
    )

    # research aggregate (ordered output only, never substituted into score) --
    v2_core = {
        "cross_section_turnover_percentile": cross_turnover,
        "cross_section_amount_percentile": cross_amount,
        "self_turnover_percentile": self_turnover,
        "self_amount_percentile": self_amount,
    }
    if all(value is not None for value in v2_core.values()):
        v2_score = 100.0 * sum(
            (1.0 - value) / len(v2_core) for value in v2_core.values()
        )
    else:
        v2_score = None
    gated_opportunity = (
        v2_score is not None
        and liquidity.eligible
        and session_status == "traded"
        and attention_surge is not True
    )
    vector.metadata["low_attention_v2"].update(
        {
            "core_components": list(v2_core),
            "aggregate_is_raw_and_ungated": True,
            "opportunity_requires": [
                "liquidity_eligible",
                "session_status=traded",
                "attention_surge is not True",
            ],
            "gated_opportunity": bool(gated_opportunity) if v2_score is not None else None,
        }
    )
    add_known(
        vector,
        "low_attention_v2_score",
        v2_score,
        datasets=("daily", "daily_basic"),
        fields=("turnover_rate", "amount"),
        history=history,
        reason="insufficient_attention_evidence" if v2_score is None else None,
        metadata={
            "name": "low_attention_v2_score",
            "version": attention_version,
            "attention_contract_version": attention_version,
            "as_of": as_of_text,
            "as_of_date": as_of_text,
            "observation_session": effective_session,
            "observation_date": latest_observation,
            "components": list(v2_core),
            "status": "known" if v2_score is not None else "unknown",
            "reason": "insufficient_attention_evidence" if v2_score is None else None,
            "reference_type": "AGGREGATE",
            "reference_window": self_cfg.window,
            "reference_population": "self_and_cross_section_components",
            "history_count": prior_history_count,
            "population_count": cross_turnover_pop,
            "raw_value": v2_score,
            "source_dataset": "daily|daily_basic",
            "source_fields": ["turnover_rate", "amount"],
            "semantic_version": attention_version,
            "research_only": True,
            "must_be_gated_by_liquidity_eligibility": True,
            "gated_opportunity": bool(gated_opportunity) if v2_score is not None else None,
            "warnings": "insufficient_attention_evidence" if v2_score is None else None,
        },
    )
    vector.add(
        "low_attention_v2_opportunity",
        gated_opportunity if v2_score is not None else None,
        status="known" if v2_score is not None else "unknown",
        source_datasets=("daily", "daily_basic"),
        source_fields=("turnover_rate", "amount", "vol"),
        reason="insufficient_attention_evidence" if v2_score is None else None,
        metadata={
            "attention_contract_version": attention_version,
            "raw_score": v2_score,
            "liquidity_eligible": liquidity.eligible,
            "session_status": session_status,
            "attention_surge": attention_surge,
            "research_only": True,
        },
    )
    return vector


def _proxy_metadata(
    *,
    name: str,
    kind: str,
    reference_type: str,
    reference_population: str,
    as_of: str,
    observation: str | None,
    value: float | None,
    percentile: float | None,
    valid_count: int | None,
    history_count: int | None,
    observed_count: int | None,
    required_history: int | None,
    population_count: int | None,
    window: int | None,
    source: str,
    source_fields: tuple[str, ...],
    semantic: str,
    reason: str | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return both the stable evidence vocabulary and compatibility aliases."""

    extras = dict(extra or {})
    raw_value = extras.get("raw_value", extras.get("raw_current_value"))
    reason_alias = {
        "insufficient_self_history": "insufficient_history",
        "insufficient_baseline": "insufficient_history",
        "no_market_history": "missing_history",
        "missing_history": "missing_history",
    }.get(reason, reason)
    if observed_count == 0 and reason in {"insufficient_self_history", "insufficient_baseline"}:
        reason_alias = "missing_history"
    meta: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "reference_type": reference_type,
        "reference_population": reference_population,
        "as_of_date": as_of,
        "observation_date": observation,
        "raw_value": raw_value,
        "raw_current_value": extras.get("raw_current_value", raw_value),
        "value": value,
        "percentile": percentile,
        "status": "known" if value is not None else "unknown",
        "reason": reason,
        "reason_code": reason,
        "reason_codes": [code for code in (reason, reason_alias) if code is not None],
        "valid_observation_count": valid_count,
        "history_count": history_count,
        "observed_session_count": observed_count if observed_count is not None else history_count,
        "observed_count": observed_count if observed_count is not None else history_count,
        "required_history": required_history,
        "required_reference_window": window,
        "minimum_valid_history": required_history,
        "reason_alias": reason_alias,
        "population_count": population_count,
        "reference_window": window,
        "window": window,
        "source_dataset": source,
        "source_datasets": source.split("|") if source else [],
        "source_fields": list(source_fields),
        "source": source,
        "attention_contract_version": semantic,
        "semantic_version": semantic,
        "warnings": reason,
    }
    meta.update(extras)
    return meta


# --------------------------------------------------------------------------
# Sample-case classification and report (research artifact, no trade signals)
# --------------------------------------------------------------------------

SAMPLE_CLASS_A = "A_eligible_low_attention"
SAMPLE_CLASS_B = "B_not_liquidity_eligible"
SAMPLE_CLASS_C = "C_attention_unknown"
SAMPLE_CLASS_NA = "not_low_attention"


def classify_low_attention_case(
    vector: FeatureVector,
    *,
    config: LowAttentionConfig | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Classify one symbol into the A/B/C research sample buckets.

    Decision order (documented, prevents label games):
    1. attention evidence incomplete       -> C (missing data is never low attention)
    2. not liquidity eligible              -> B (inactivity never buys opportunity)
    3. low attention observed              -> A
    4. otherwise                           -> not_low_attention

    This returns no trading recommendation.
    """

    settings = config or LowAttentionConfig()
    values = vector.values
    cross_turnover = values.get("cross_section_turnover_percentile")
    cross_amount = values.get("cross_section_amount_percentile")
    core_names = (
        "cross_section_turnover_percentile",
        "cross_section_amount_percentile",
        "self_turnover_percentile",
        "self_amount_percentile",
    )
    core_known = all(values.get(name) is not None for name in core_names)
    if not core_known:
        # Distinguish policy/session-level exclusions (the symbol exists but is
        # newly listed, suspended or stale: an eligibility failure, B) from
        # data-level missingness (no usable observations at all: attention is
        # genuinely `unknown`, C).
        exclusion_reasons = {
            "new_listing",
            "no_observation_at_session",
            "suspended_session",
            "stale_data",
            "no_session_at_decision",
        }
        warnings = {
            str(vector.evidence[name].metadata.get("warnings"))
            for name in core_names
            if name in vector.evidence
        }
        session_status = str(values.get("session_status"))
        excluded = bool(warnings & exclusion_reasons) and session_status != "no_data"
        if excluded:
            return SAMPLE_CLASS_B, ("suspended_or_inactive_at_session",)
        return SAMPLE_CLASS_C, ("insufficient_attention_evidence",)
    eligible = bool(values.get("liquidity_eligible"))
    reasons = tuple(
        str(value)
        for value in vector.evidence["liquidity_eligible"].metadata.get("reasons", [])
        if value
    )
    if not eligible:
        return SAMPLE_CLASS_B, reasons
    if values.get("attention_surge") is True:
        return SAMPLE_CLASS_NA, ("higher_attention_observed",)
    low_attention = (
        cross_turnover <= settings.low_attention_cross_percentile
        and cross_amount <= settings.low_attention_cross_percentile
    )
    if low_attention:
        return SAMPLE_CLASS_A, ("low_attention_observed",)
    return SAMPLE_CLASS_NA, ()


def low_attention_sample_report(
    vectors: list[FeatureVector] | tuple[FeatureVector, ...],
    *,
    config: LowAttentionConfig | None = None,
) -> pd.DataFrame:
    """A small research sample report (buckets A/B/C) with evidence included.

    The report is an audit artifact and never renders a trade recommendation.
    """

    rows: list[dict[str, Any]] = []
    for vector in vectors:
        classification, reasons = classify_low_attention_case(vector, config=config)
        evidence = vector.evidence
        cross_ev = evidence.get("cross_section_turnover_percentile")
        session_ev = evidence.get("session_status")
        rows.append(
            {
                "ts_code": vector.ts_code,
                "as_of_date": vector.as_of_date,
                "class": classification,
                "reasons": "|".join(reasons),
                "session_status": vector.values.get("session_status"),
                "staleness_days": session_ev.metadata.get("staleness_days") if session_ev else None,
                "liquidity_eligible": vector.values.get("liquidity_eligible"),
                "liquidity_average_amount": vector.values.get("liquidity_average_amount"),
                "self_turnover_percentile": vector.values.get("self_turnover_percentile"),
                "self_amount_percentile": vector.values.get("self_amount_percentile"),
                "self_volume_percentile": vector.values.get("self_volume_percentile"),
                "cross_section_turnover_percentile": vector.values.get(
                    "cross_section_turnover_percentile"
                ),
                "cross_section_amount_percentile": vector.values.get(
                    "cross_section_amount_percentile"
                ),
                "cross_section_volume_percentile": vector.values.get(
                    "cross_section_volume_percentile"
                ),
                "cross_population_count": (
                    cross_ev.metadata.get("population_count") if cross_ev else None
                ),
                "abnormal_volume": vector.values.get("abnormal_volume"),
                "attention_baseline_change": vector.values.get("attention_baseline_change"),
                "attention_surge": vector.values.get("attention_surge"),
                "low_attention_v2_score": vector.values.get("low_attention_v2_score"),
                "low_attention_v2_opportunity": vector.values.get(
                    "low_attention_v2_opportunity"
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    order = [SAMPLE_CLASS_A, SAMPLE_CLASS_NA, SAMPLE_CLASS_C, SAMPLE_CLASS_B]
    frame["_order"] = frame["class"].map(lambda value: order.index(value) if value in order else 99)
    frame = frame.sort_values(["_order", "ts_code"], kind="mergesort").drop(columns="_order")
    frame.attrs["attention_contract_version"] = (
        vectors[0].metadata.get("low_attention_v2", {}).get(
            "attention_contract_version", SEMANTIC_VERSION
        )
        if vectors
        else SEMANTIC_VERSION
    )
    return frame.reset_index(drop=True)


def low_attention_sample_report_markdown(frame: pd.DataFrame) -> str:
    """Render the sample report as plain Markdown without external tables."""

    if frame.empty:
        return (
            "# Low Attention v2 : sample report (issue #29)\n\n"
            "No cases available.\n"
        )
    lines: list[str] = []
    for index, row in frame.iterrows():
        lines.append(f"## Case {index + 1}: {row['ts_code']}")
        lines.append("")
        for column in frame.columns:
            if column == "ts_code":
                continue
            value = row[column]
            if value is None or (isinstance(value, float) and pd.isna(value)):
                text = "unknown"
            elif isinstance(value, float):
                text = f"{value:.4f}"
            else:
                text = str(value)
            lines.append(f"- {column}: `{text}`")
        lines.append("")
    return (
        "# Low Attention v2 : sample report (issue #29)\n\n"
        "Research audit artifact only - reports candidate buckets, not trading "
        "recommendations.\n\n"
        "## Buckets\n"
        "- **A** : genuinely low attention but investable.\n"
        "- **B** : extreme inactivity / not liquidity eligible, therefore NOT an "
        "opportunity (inactivity does not create a low-attention opportunity).\n"
        "- **C** : attention evidence missing, therefore attention is `unknown` "
        "(missing data is never low attention).\n\n"
        "## Cases\n\n"
        + "\n".join(lines)
        + "\n"
    )
