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
Turnaround Score v1
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
| #14 | Low-expectation/crowding proxies | `features.market.compute_crowding_features` |
| #15 | Weighted transparent score | `scanner.score.score_feature_vector` |
| #16 | Historical PIT replay | `scanner.replay.run_replay` |
| #17 | Forward evaluation | `scanner.evaluation.evaluate_scans` |
| #18 | Feature-group ablation | `scanner.stability.analyze_feature_stability` |
| #19 | Daily snapshot and comparison | `scanner.daily.scan_data` / `compare_scan_snapshots` |
| #20 | Explainable candidate report | `scanner.report.write_candidate_reports` |
| #34 | Market / Reference historical corpus | `datasets.market_bootstrap` / `datasets.market_validation` |
| #27 | Comparable financial period semantics | `pit.comparable` / `features.fundamental` |

Issue #7 is deliberately not duplicated in this branch: the repository already
has the proposed adversarial PIT test/documentation in [PR #24](https://github.com/0verme/ashare-turnaround/pull/24).

## Frozen contracts

### `FeatureVector` and evidence

Each candidate is represented by `scanner.contracts.FeatureVector`:

- `ts_code` and normalized `as_of_date` identify the decision;
- `version` is currently `features-v1`;
- `comparable_period_contract_version` is `comparable-period-v1`;
- `values` contains numeric features or explicit `None`;
- `evidence` maps every value to datasets, source fields, report periods, raw
  values, period semantics, source versions, and actual availability dates;
- `risk_flags` are soft penalties while `rejected_reasons` are hard gates;
- `unknown_features` is populated for `unknown`, `insufficient_data`, and
  `unsupported` values.

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

### Score

`ScoreConfig` is `score-v1` with these weights:

| Component | Weight |
| --- | ---: |
| Fundamental | 0.30 |
| Trend | 0.20 |
| Quality | 0.20 |
| Attention | 0.15 |
| Expectation/crowding | 0.15 |

Known components are renormalized when a component is unavailable. Risk flags
subtract bounded penalties, while hard quality gates remain visible in the
ranked output and cannot be mistaken for a clean candidate.

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
  under `data/derived/replays/`.
- `replay-variants --as-of YYYYMMDD` writes the four versioned score variants
  from one verified PIT snapshot.
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
