# Turnaround Trend Semantics

This document freezes the trend contract for Issue #28.  It is deliberately a
small semantic contract, not a feature zoo or a parameter-tuning exercise.

```text
comparable_period_contract_version = "comparable-period-v1"
trend_contract_version             = "turnaround-trend-v2"
```

The dependency boundary is:

```text
PIT financial records
    ↓
comparable-period-v1
    ├─ validated comparable YoY observations
    ├─ validated single-quarter observations
    ├─ validated period margins
    └─ validated TTM observations
    ↓
turnaround-trend-v2
```

The trend layer never chooses `latest` and `previous` rows as a substitute for
an economic comparison, never quarterizes a cumulative statement itself, and
never rolls four arbitrary disclosures into a TTM.  If the comparable-period
contract cannot prove an input, the dependent trend result is `UNKNOWN` (or the
more specific `INSUFFICIENT_HISTORY`, `DISCONTINUOUS`, or `UNSUPPORTED` status).

## Level

A level says where the latest validated observation is:

```text
revenue_yoy = +10%
profit_yoy  = -5%
margin      = 12%
```

It does **not** say that the value is improving.  For example, `+40% → +35%
→ +30%` remains a high positive level while its direction is deterioration.

Rate levels are stored as ratios (`0.10 == 10%`).  Margin and growth changes
are stored in `percentage_points`, so the unit is never confused with a
percent growth rate.

## Growth and First Change

For a validated comparable growth series:

```text
growth_t
change_t = growth_t - growth_t-1
```

Thus:

```text
-20% → -5%
change = +15pp
```

is improvement, while:

```text
+40% → +35%
change = -5pp
```

is deterioration.  This is a first change of the growth rate, not an absolute
profit difference.

## Acceleration

Acceleration requires three consecutive, comparable observations:

```text
change_t-1   = growth_t-1 - growth_t-2
change_t     = growth_t   - growth_t-1
acceleration = change_t - change_t-1
```

The value is in percentage points.  It is allowed to be zero:

```text
-20% → -5% → +10%
change:       +15pp, +15pp
acceleration: 0pp
```

This is strong turnaround evidence even though the mathematical acceleration
is not positive.  Conversely:

```text
+40% → +35% → +30%
change:       -5pp, -5pp
acceleration: 0pp
state:        DETERIORATING
```

It is not a high-growth turnaround.  The following logic is explicitly
forbidden:

```text
absolute profit → first difference → second difference = YoY acceleration
```

## Sign Transition

`sign_transition` is separate from the numeric acceleration.  The contract
recognizes at least:

```text
NEGATIVE_TO_POSITIVE
POSITIVE_TO_NEGATIVE
NONE
UNKNOWN
```

The implementation also records explicit zero transitions where the primitive
can prove them (`ZERO_TO_POSITIVE`, `ZERO_TO_NEGATIVE`, `TO_ZERO`).  A negative
or zero denominator remains `UNKNOWN` for ordinary growth according to
`comparable-period-v1`; it is never converted into an infinite or invented
percentage.  When that contract supplies a reliable underlying sign state,
the sign evidence may be retained separately, without manufacturing a growth
level or change.

## Persistence / Consecutive Improvement

`improvement_count` counts positive first changes in the contiguous run ending
at the current observation.  For example:

```text
-30 → -20 → -5 → +8
improvement_count = 3
persistence       = improving
```

The contract requires at least two valid observations (one change) for
persistence.  Periods must be consecutive fiscal quarters within one semantic
series.  An `UNKNOWN` observation, an invalid observation, a missing fiscal
period, or a report-family/unit/scope discontinuity interrupts the run:

```text
-20 → UNKNOWN → +10
```

is not two consecutive improvements.  Missing is never filled with zero and
never treated as improvement.  Revisions are resolved by the #27 PIT selection
before this count is made.

## Turnaround / Inflection State

State is an explicit deterministic combination, not an alias for
acceleration:

```text
STRONG_TURNAROUND  latest change > 0 and latest sign transition is
                   NEGATIVE_TO_POSITIVE
IMPROVING          latest change > 0 without that sign transition
STABLE             latest change == 0
DETERIORATING      latest change < 0
INSUFFICIENT       no valid pair, insufficient history, or a failed dependency
```

The corresponding `turnaround_evidence` is `positive`, `negative`, `neutral`,
or `unknown`.  Direction, first change, sign transition, persistence, and
coverage are exposed independently in evidence.  No forward return is used to
choose a state or a threshold.

## YoY Trend

YoY trend consumes only the validated comparable-period matcher from #27.  An
observation carries:

```text
period
comparison_period
growth_rate
status
availability_date / availability_dates
source_version / source_versions
comparable_period_contract_version
```

The matcher requires the same economic quarter and compatible duration,
report family, statement type, scope, unit, and accounting semantics.  The
following is forbidden:

```text
latest disclosure vs previous disclosure = YoY trend
```

One valid observation is enough for a level; two are required for first
change; three are required for acceleration.  Missing comparators and invalid
period identities fail closed.

## QoQ Trend

QoQ consumes only #27 validated `SINGLE_QUARTER` observations.  For cumulative
income/cash-flow reports the only accepted path is:

```text
Q3 cumulative
    ↓ comparable-period-v1 quarterization
Q3 single
    ↓ validated qoq matcher
Q3 single vs Q2 single
    ↓
QoQ trend
```

`Q3 cumulative - H1 cumulative` is not allowed to reappear as a local trend
calculation, and a raw cumulative statement sequence is not a QoQ trend.  The
source-quarter provenance remains attached to each observation.

## Margin Trend

A period margin is produced by the #27 margin primitive before trend
calculation:

```text
margin_t = numerator_t / revenue_t
margin_change_t = margin_t - margin_t-1
```

The trend outputs remain separate:

```text
gross_margin_change
operating_margin_change
net_margin_change
```

and, where three valid observations exist, their separate acceleration fields.
Profit growth acceleration is never presented as margin inflection.  Invalid
or negative/zero revenue bases remain unknown under the comparable-period
contract.

## TTM Trend

TTM endpoints consume only #27 validated TTM values.  Each endpoint records
its four validated consecutive source quarters:

```text
ttm_value_t
ttm_value_t-1
ttm_change = ttm_value_t - ttm_value_t-1
```

TTM acceleration, when requested, is the change of these TTM changes and is in
source units.  Issue #28 never computes a rolling sum over arbitrary
 disclosures.  A missing quarter, mixed semantic identity, or future
quarter makes the endpoint unknown and cannot be skipped.

## Minimum history and statuses

| Component | Minimum valid history |
| --- | ---: |
| Level | 1 valid observation |
| First change | 2 consecutive comparable observations |
| Acceleration | 3 consecutive comparable observations |
| Persistence | 2 valid observations / 1 first change; count only the contiguous tail |
| TTM level | one valid #27 four-quarter endpoint |
| TTM change | two consecutive valid TTM endpoints |

The feature evidence status vocabulary is:

```text
VALID
UNKNOWN
INSUFFICIENT_HISTORY
DISCONTINUOUS
UNSUPPORTED
```

The serialized implementation uses lower-case strings to match the existing
`FeatureEvidence` convention.  Reasons include `insufficient_history`,
`discontinuous_periods`, `missing_period`, `missing_quarter`, and the original
#27 primitive reason.  No status is converted to zero or to an assumed stable
value.

## Revision / PIT rule

The #27 rule applies before every trend operation:

```text
actual_available_date <= as_of_date
```

If an original value is visible before a later revision, the earlier snapshot
uses the original value.  The revised value becomes visible on its own
availability boundary and cannot rewrite the earlier trend artifact.  Source
availability dates and source versions are retained in every trend evidence
chain.

## Evidence and versioning

Each metric-qualified trend output has evidence containing, where applicable:

```text
metric, value, unit, status, reason
current_period, previous_period, older_period
current_growth, previous_growth, older_growth
current_change, previous_change, acceleration
sign_transition, persistence, improvement_count
availability_dates, source_versions, source_dataset, source_fields, source_chain
comparable_period_contract_version, trend_contract_version
```

Metric-qualified fields are authoritative.  Existing names such as
`yoy_acceleration`, `qoq_acceleration`, `consecutive_improvement`,
`sign_transition`, `margin_inflection`, and `ttm_trend` remain as deprecated
schema-compatible aliases and now carry the new evidence chain.  Because the
meaning of those score inputs changed, the score input configuration is
versioned as `score-v2`; its weights were not changed.

The overall evidence/confidence gate, ranking eligibility, and critical-group
policy are implemented separately by `evidence-confidence-v1`; this contract
continues to make each individual trend evidence honest. See
[evidence-confidence-v1.md](evidence-confidence-v1.md).
