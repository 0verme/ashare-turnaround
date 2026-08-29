# Scanner contracts and issue mapping

This document freezes the public seams used by the parallel issue work. The
pipeline is local-first and point-in-time (PIT) by construction:

```text
RAW Parquet + checkpoints
        ↓
coverage / incremental sync
        ↓
canonical PIT financial rows + comparable-period contract
        ↓
PIT investable universe
        ↓
fundamental | trend | quality | attention | crowding
        ↓
FeatureVector + FeatureEvidence
        ↓
Turnaround Score (score-v2; frozen weights)
        + evidence-confidence-v1 gate
        ↓
replay / daily snapshot / evaluation / report
```

## Issue-to-module map

| Issue | Implemented seam | Main artifact |
| --- | --- | --- |
| #6 | Coverage and integrity audit | `storage.inventory.build_coverage_report` |
| #7 | PIT adversarial contract | existing PR #24; review/merge separately |
| #8 | Date-scoped incremental synchronization | `datasets.sync.sync_daily` |
| #9 | PIT investable universe | `scanner.universe.build_investable_universe` |
| #10 | Fundamental feature group | `features.fundamental.compute_fundamental_features` |
| #11 | Trend, persistence, acceleration | `features.trend.compute_trend_features` |
| #12 | Quality gate and false-turnaround flags | `features.quality.compute_quality_features` |
| #13 | Low-attention proxies | `features.market.compute_attention_features` |
| #29 | Low-attention v2 calibration (cross-sectional context) | `features.low_attention.compute_low_attention_v2` — research-only; see [docs/low-attention-v2.md](low-attention-v2.md); v1/v2 boundary preserved (production score still reads v1 `attention_score`) |
| #14 | Low-expectation/crowding proxies | `features.market.compute_crowding_features` (`expectation-crowding-v2`, see `docs/expectation-crowding-v2.md`) |
| #30 | Expectation/crowding v2 benchmark-relative semantics | `features.market.compute_crowding_features` / `docs/expectation-crowding-v2.md` |
| #15 | Weighted transparent score | `scanner.score.score_feature_vector` |
| #16 | Historical PIT replay | `scanner.replay.run_replay` |
| #17 | Forward evaluation | `scanner.evaluation.evaluate_scans` |
| #18 | Feature-group ablation | `scanner.stability.analyze_feature_stability` |
| #19 | Daily snapshot and comparison | `scanner.daily.scan_data` / `compare_scan_snapshots` |
| #20 | Explainable candidate report | `scanner.report.write_candidate_reports` |
| #34 | Market / Reference historical corpus | `datasets.market_bootstrap` / `datasets.market_validation` |
| #27 | Comparable financial period semantics | `pit.comparable` / `features.fundamental` |
| #28 | Turnaround trend and acceleration semantics | `features.trend` / `docs/trend-semantics.md` |
| #31 | Evidence coverage and confidence gate | `scanner.evidence` / `scanner.score` / `docs/evidence-confidence-v1.md` |
| #32 | Historical PIT replay validation sample | `scanner.replay_validation` / `docs/pit-replay-validation.md` |
| #41 | Lossless normalized replay artifact layout / bounded audit | `scanner.artifacts` / `docs/pit-replay-artifact-normalization.md` |

Issue #7 is deliberately not duplicated in this branch: the repository already
has the proposed adversarial PIT test/documentation in [PR #24](https://github.com/0verme/ashare-turnaround/pull/24).

## Frozen contracts

### `FeatureVector` and evidence

Each candidate is represented by `scanner.contracts.FeatureVector`:

- `ts_code` and normalized `as_of_date` identify the decision;
- `version` is the feature schema version (`features-v1` for a merged scanner
  vector); the additive Low Attention v2 group declares
  `low-attention-v2.0.0` in `FeatureVector.metadata["low_attention_v2"]`;
- `feature_contract_versions` records group contracts, including
  `expectation_crowding: expectation-crowding-v2`;
- `benchmark_metadata` records the primary `000300.SH` declaration and its
  `index_basic + index_daily` source;
- `FeatureVector.metadata` merges the `low_attention_v2` and
  `expectation_crowding_v2` provenance namespaces without overwriting either.
- `comparable_period_contract_version` is `comparable-period-v1`;
- `trend_contract_version` is `turnaround-trend-v2`; this is independent of
  the comparable-period version;
- `values` contains numeric features or explicit `None`;
- `evidence` maps every value to datasets, source fields, report periods, raw
  values, period semantics, source versions, actual availability dates, and—
  for crowding—formula, components, config, and semantic version;
- `risk_flags` are soft penalties while `rejected_reasons` are hard gates;
- `unknown_features` is populated for `unknown`, `missing`, `insufficient_data`,
  `insufficient_history`, `discontinuous`, `stale`, `invalid`, PIT-unsafe, and
  `unsupported` values;
- the additive `evidence-confidence-v1` score result records field/group
  coverage, confidence, unknown groups, ranking eligibility, and all missing /
  invalid / unsupported required fields; PIT warnings do not count as valid
  evidence;
- score inputs, replay metadata, and candidate reports repeat the low-attention
  contract version and fields. The score still consumes only v1
  `attention_score`; v2 metadata/evidence is research-only.

Feature groups only add namespaced values to this object. They do not change the
universe, score weights, CLI parser, or another feature group's data. This keeps
#10, #13, and #14 independently testable in separate branches.

### Missingness and PIT

Missing data is never silently imputed. A feature is either known from an
available observation or carries an evidence status and reason. Financial frames
are canonicalized with `report_period` and `actual_available_date`, then selected
with `select_financial_as_of`. Market rows are restricted to `trade_date <=
as_of_date` and, when present, `actual_available_date <= as_of_date`. Phase 1.6 stores
stock daily and daily_basic in monthly historical units, a separate configured
`index_daily` benchmark, exchange-range calendars, and dated suspension evidence.
The current stock_basic name/status/industry/board fields remain explicitly
snapshot-only; they are never treated as historical PIT state.

The universe records every exclusion reason, including ST status, delisting,
future listing, BSE policy, new listing, suspension, low liquidity, insufficient
financial history, and unavailable reference data. The default policy uses at
least four available financial periods and excludes BSE unless configured.

### Comparable financial periods

`pit.comparable` assigns each canonical row a fiscal year/period, quarter,
report family, statement type, duration semantics, scope, unit, accounting
semantics, source version identity, and availability date.  YoY and QoQ use
explicit period matching; cumulative income/cash-flow values are quarterized
only through a validated Q1/H1/Q3/FY chain.  TTM requires four validated single
quarters.  Ambiguous or unsupported semantics are `UNKNOWN`.  See
[docs/comparable-period-semantics.md](comparable-period-semantics.md) for the
full contract and denominator policy.

### Turnaround trend

`features.trend` consumes only the validated primitives above under
`turnaround-trend-v2`.  It keeps level, first change, acceleration, sign
transition, persistence, and turnaround state as separate fields.  YoY uses
validated comparable periods, QoQ uses validated single quarters, margins use
period margins, and TTM uses validated four-quarter endpoints.  A gap or
unknown observation interrupts persistence; no status is filled with zero.
See [docs/trend-semantics.md](trend-semantics.md) for the full contract and
adversarial examples.

### Low Attention v2

`features.low_attention.compute_low_attention_v2` is the research-only
`low-attention-v2.0.0` calibration. Self-history is prior-only, cross-sectional
percentiles use the visible population at the effective session, and liquidity
eligibility is a separate gate. Missing, stale, suspended, and insufficient
history observations remain `UNKNOWN`; the production score continues to read
only the v1 `attention_score`.

See [docs/low-attention-v2.md](low-attention-v2.md) for the full contract and
A/B/C boundary tests.

### Expectation / Crowding v2 and benchmark

`features.market.compute_crowding_features` uses
`expectation-crowding-v2` with benchmark `000300.SH` under `benchmark-v1`.
For 20D and 60D trading-session windows:

```text
excess_return = (stock_end / stock_start - 1)
              - (benchmark_end / benchmark_start - 1)
```

A missing or misaligned benchmark is `UNKNOWN`; it never falls back to the
absolute stock return. Crowding/expectation penalties and evidence remain
separate from fundamental and trend evidence. See
[docs/expectation-crowding-v2.md](expectation-crowding-v2.md).

### Score and evidence-confidence gate

`ScoreConfig` remains `score-v2` with these unchanged weights.  The separate
`EvidenceConfidenceConfig` is `evidence-confidence-v1` with a versioned
`feature-group-registry-v1`; it does not tune or redefine the score:

| Component | Weight |
| --- | ---: |
| Fundamental | 0.30 |
| Trend | 0.20 |
| Quality | 0.20 |
| Attention | 0.15 |
| Expectation/crowding | 0.15 |

The backward-compatible `turnaround_score` may remain a diagnostic partial
score when a component is unavailable.  If known components are renormalized,
`ScoreResult` explicitly reports `configured_weight_total`, `observed_weight`,
`missing_weight`, and `score_is_partial`; no missing group is filled with a
neutral value.  Risk flags subtract bounded penalties, while hard quality gates
remain visible in the ranked output.

The independent gate reports `evidence_coverage` as the unweighted ratio of
valid required fields to all required fields, per-group coverage/status, the
configured critical groups, `confidence` (`HIGH`/`MEDIUM`/`LOW`/
`INSUFFICIENT`), `unknown_groups`, `ranking_eligible`, and
`eligibility_reason`.  `rank_scores(top_n=None)` retains the full diagnostic
ordering; a finite Top-N contains only `ranking_eligible` candidates.  Score
weights remain unchanged and confidence is evidence completeness, not a
probability of positive return.  See [docs/evidence-confidence-v1.md](evidence-confidence-v1.md).

## Runtime commands and artifacts

- `market-capacity-plan` writes a no-network capacity estimate for the declared
  Market / Reference window.
- `bootstrap-market` writes month/range/snapshot Market / Reference units and
  `data/state/market-bootstrap-checkpoints.json`; it does not touch Financial P0.
- `verify-market` writes the Market / Reference coverage, cross-section,
  benchmark, forward-window, PIT-limitation, and RAW-integrity report only.
- `inventory` writes `data/state/raw-manifest.json` and
  `data/state/data-coverage.json`.
- `sync-daily` writes raw partitions and an append-only sanitized state log.
  `write_incremental` merges by the dataset's declared primary keys and keeps
  the latest row for an exact identity.
- `replay --as-of YYYYMMDD` writes a ranked Parquet artifact and JSON metadata
  under `data/derived/replays/`; it loads `index_daily` separately from stock
  `daily`, records the `expectation-crowding-v2`/benchmark declarations, and
  persists evidence-confidence fields for every candidate (including the full
  diagnostic ordering in JSON).
- `replay-variants --as-of YYYYMMDD` writes the four versioned score variants
  from one verified PIT snapshot.
- `replay-validate --start YYYY-MM --end YYYY-MM` selects fixed monthly sessions
  from `trade_cal`, runs the existing replay path with the historical-universe
  gate, and writes per-snapshot PIT/evidence/manifest artifacts. It performs no
  forward-return evaluation; see [docs/pit-replay-validation.md](pit-replay-validation.md).
- `scan` writes a daily snapshot under `data/derived/scans/`; `scan-compare`
  reports additions, removals, rank/score changes, and risk-flag changes.
- `evaluate` persists declared holding/benchmark/cost/delisting assumptions,
  aligned forward returns, coverage, hit rate, excess return, price-path and
  cohort drawdown, industry and market-cap exposure, turnover, reason-coded
  missingness, and separate PIT fundamental improvement.
- `ablate` consumes the four named saved evaluation artifacts, verifies common
  PIT snapshots and evaluation rules, then reports rank overlap, performance
  dispersion, segmented stability, and the precommitted promotion decision.
- `report` writes JSON and Markdown candidate reports containing score breakdown,
  flags, and source-period evidence.

All generated runtime data is ignored by Git. Tests use synthetic DataFrames and
temporary local Parquet directories; no test performs a full-market Tushare
request.

The #32 validation artifact keeps `pit-replay-validation-v1` separate from the
physical `pit-replay-artifact-normalized-v1` layout and from the
frozen `comparable-period-v1`, `turnaround-trend-v2`, `low-attention-v2`,
`expectation-crowding-v2`, and `evidence-confidence-v1` contracts. It is a
correctness boundary, not an Evaluation, Ablation, or Score v2 decision.
