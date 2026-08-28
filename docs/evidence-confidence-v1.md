# Evidence coverage and confidence gate v1 (issue #31)

This is the independent **evidence-confidence-v1** contract for the research
scanner.  It does not redefine `turnaround_score`, change any ScoreConfig
weight, or claim a probability of return.

> Score magnitude != evidence completeness != confidence != ranking eligibility.

## Versioned registry

The registry is implemented in
`scanner.evidence.FEATURE_GROUP_REGISTRY` and is versioned as
`feature-group-registry-v1`.  Group order is deterministic:

```text
fundamental, trend, quality, attention, expectation_crowding
```

The required score primitives are:

| Group | Required fields | Optional fields | Critical by default |
| --- | --- | --- | --- |
| `fundamental` | `revenue_yoy`, `net_profit_yoy`, `operating_profit_yoy`, `gross_margin`, `operating_margin`, `net_margin` | levels, cash-flow/ratio diagnostics, margin changes, `fundamental_data_status` | yes |
| `trend` | `yoy_acceleration`, `qoq_acceleration`, `consecutive_improvement`, `sign_transition`, `margin_inflection` | metric-qualified YoY/QoQ/TTM trend fields and aliases | yes |
| `quality` | `quality_score`, `quality_gate_status` | quality profit/cash-flow, ratio, inventory, receivables and leverage diagnostics | no |
| `attention` | v1 production primitives: `turnover_percentile`, `amount_percentile`, `abnormal_volume`, `attention_score` | declared Low Attention v2 session, self-history, cross-section, liquidity and opportunity evidence | yes |
| `expectation_crowding` | `repricing_20d`, `repricing_60d`, `high_proximity`, `volume_spike_penalty`, `turnover_spike_penalty`, `expectation_score` | returns, 52-week evidence, valuation, disclosure reaction and `crowding_penalty` | yes |

The attention v2 fields are retained and reported, but the pre-existing
Low Attention v2 contract explicitly remains research-only and is not a
production score input.  The registry therefore requires the v1 primitives
that actually feed `score-v2` and records v2 evidence as optional/additive
metadata.  The expectation/crowding v2 primitives are required because that
aggregate is the production expectation input.

A field is resolved only through this registry (including its explicit
`abnormal_volume` compatibility alias); the implementation never infers a
group from whichever keys happen to be present in `FeatureVector.values`.

## Field and group coverage

Only `FeatureEvidence.status` values `known` and `valid`, with a finite/non-null
value, are valid.  The following never contribute valid coverage:

```text
unknown, missing, stale, unsupported, insufficient_history,
insufficient_data, discontinuous, invalid, future-unsafe, PIT warning
```

PIT warnings are fail-closed for coverage.  An evidence record may still be
preserved and a diagnostic score may still be computed, but it is not counted
as complete evidence.  Zero/negative denominators and other semantic failures
remain the feature producer's explicit unknown/invalid evidence; this layer
never recomputes a growth or ratio.

For every group `g`:

```text
field_coverage_g = valid required fields / total required fields
```

The result records `required_count`, `valid_count`, required/optional field
statuses, `missing_fields`, `invalid_fields`, and `unsupported_fields` per
group.  A group is `COMPLETE`, `PARTIAL`, `UNKNOWN`, or `INSUFFICIENT`.
`unknown_groups` lists groups with zero valid required fields; the separate
`incomplete_groups` list also includes partially evidenced groups. Missing
optional fields are reported but do not reduce required-field coverage or turn
the group into `UNKNOWN`.

Overall coverage is deliberately unweighted:

```text
evidence_coverage = all valid required fields / all required fields
```

It is a ratio in `[0, 1]`, not a return metric and not a score-weighted value.
The `group_coverage` map and detailed `coverage` map are emitted alongside it;
rank rows and score JSON also expose the named fields
`fundamental_coverage`, `trend_coverage`, `quality_coverage`,
`attention_coverage`, and `expectation_crowding_coverage`.

## Confidence policy

The policy is part of `EvidenceConfidenceConfig` and is serialized with every
score/replay artifact.  Default, non-performance-tuned rules are:

```text
HIGH:
  evidence_coverage >= 0.90 and every critical group is COMPLETE
MEDIUM:
  evidence_coverage >= 0.75 and no critical group is completely UNKNOWN
LOW:
  evidence exists but does not meet HIGH/MEDIUM rules
INSUFFICIENT:
  evidence_coverage < 0.25, or a critical group is completely UNKNOWN
```

The default critical groups are `fundamental`, `trend`, `attention`, and
`expectation_crowding`.  `quality` remains a separate hard-rejection surface;
its missing evidence still lowers coverage/confidence.  The policy is
configurable and fail-closed for unknown critical groups by default.

`confidence` means evidence completeness/reliability under this contract.  It
is **not** a probability that a stock will have a positive return and must not
be presented as investment advice.

## Partial score and ranking eligibility

The existing `turnaround_score` calculation is retained as a backward-compatible
diagnostic score.  Current behavior may renormalize the configured weights of
known score components; this is no longer implicit.  Every `ScoreResult`
exposes:

```text
configured_weight_total
observed_weight
missing_weight
score_is_partial
diagnostic_partial_score
```

The weights themselves remain:

```text
fundamental 0.30 | trend 0.20 | quality 0.20 | attention 0.15 | expectation 0.15
```

`observed_weight` is the configured weight of score components with a usable
score value; `missing_weight` is the omitted configured weight.  Incomplete
required evidence also sets `score_is_partial`, even when the old component
formula happened to produce a number.  Missing groups are never filled with a
neutral value.

Formal Top-N membership is independent of score magnitude.  With the default
policy a candidate must satisfy all of:

```text
not rejected
and turnaround_score is finite/known
and evidence_coverage >= 0.50
and no configured critical group has zero required-field coverage
and confidence >= the configured minimum (LOW by default)
```

The output records `ranking_eligible` and a deterministic
`eligibility_reason`.  `rank_scores(..., top_n=None)` is the full diagnostic
ordering and retains eligible, ineligible, rejected, and unknown candidates.
A finite `top_n` is the formal ranking and filters to `ranking_eligible` rows
only.  Replay keeps both `ranked` (formal) and `diagnostic_ranked` (full).

Example:

```text
Fundamental       complete/high
Trend             complete/high
Quality           complete
Attention         UNKNOWN
Expectation/...   UNKNOWN
turnaround_score  82
coverage          0.60 (illustrative)
confidence        INSUFFICIENT
unknown_groups    attention, expectation_crowding
ranking_eligible  false
```

A high diagnostic number does not override the missing critical evidence.
Conversely, a fully evidenced lower score can be formally eligible while a
higher, poorly evidenced diagnostic candidate is retained only for audit.

## Artifact boundary and scope

The contract version and fields are additive in:

- `ScoreResult` and `ScoreResult.input_metadata`;
- `ReplayConfig`, replay metadata, formal/full rank outputs, and JSON artifacts;
- candidate JSON and Markdown reports.

JSON and Markdown use the same values, statuses, unknown groups, and reasons;
unknown is never converted to a neutral financial or market view.  Reports are
research audit artifacts, not recommendations.

This issue intentionally does **not** implement Score v2, weight/threshold
selection from forward returns, production replay, evaluation, ablation, new
data download, or RAW-data rewriting.  Those decisions require the later
validation work.
