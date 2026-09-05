# Frozen calibrated baseline evaluation contract

**Contract:** `baseline-evaluation-contract-v1`

This contract evaluates the existing complete Scanner only. It is frozen before
formal outcome observation. It does not authorize Score changes, parameter
selection, ablation, or an exit-policy comparison.

## 1. Snapshot schedule and selection

The monthly schedule reuses Issue #32 semantics:

- range starts at `2017-01`;
- one target per requested month, first open SSE `trade_cal` session on or after
  the fixed 15th, without crossing the month;
- schedule statuses remain explicit (`AVAILABLE`, `UNAVAILABLE_DATA`,
  `UNAVAILABLE_FUTURE`, `INCOMPLETE_CURRENT_MONTH`);
- no month is removed because of candidate count or outcome; no bull/bear slice
  is selected from returns;
- the local corpus determines the final complete market/fundamental boundary.

Only formal rows with `ranking_eligible=true` are selected. A missing compatible
snapshot is `UNAVAILABLE_COMPATIBLE_SNAPSHOT`, never an approximate ranking.
Issue #32 full artifacts may be reused by projecting only the formal Top-20
rows; full evidence JSON is not copied into the evaluation artifact.

## 2. Frozen market evaluation

| Item | Frozen value |
| --- | --- |
| Top-N decision | **Top-20** |
| descriptive appendix | Top-3 and Top-10 only; never used for a decision |
| benchmark | `000300.SH` / CSI 300 |
| benchmark dataset | `index_daily` |
| benchmark price | raw `close` (the index corpus has no adjustment factor) |
| horizons | 20D, 60D, 120D, 250D |
| session axis | open `trade_cal` SSE sessions |
| entry | scanner `as_of` session close |
| exit | close on the Nth subsequent valid market session |
| portfolio view | independent, overlapping, equal-weight cohorts per snapshot |
| turnover | Jaccard turnover of consecutive observed Top-20 cohorts |
| transaction cost | **30 bps round-trip total deduction**, selected before outcomes |
| delisting | dated `delist_date` inside the window receives `-1.0` assumption |

A candidate and benchmark must have exact endpoints on the same session axis.
A missing endpoint is not carried forward. A missing or incomplete window is
retained with a reason code such as `incomplete_market_window`,
`missing_entry_price`, `missing_horizon_price`, `suspended_at_exit`, or
`missing_benchmark_endpoint`. A dated delisting is retained as
`delisted_assumption`; the row is not silently deleted.

### Price-adjustment semantics

For a stock endpoint, the evaluation-only price is:

```text
adjusted_close = raw close × adj_factor
adjusted_return = adjusted_close_exit / adjusted_close_entry - 1
```

`adj_factor` is the local Tushare historical adjustment-factor field. The
contract requires a finite positive factor at both exact endpoints and records
raw and adjusted endpoint prices. The factor cannot enter Scanner feature
calculation, score, rank, or snapshot identity. If an exact factor is absent or
ambiguous, the outcome is unavailable with
`missing_adjustment_factor_entry` or `missing_adjustment_factor_exit`; baseline
code never substitutes raw-close return.

The formula is validated by the synthetic split/dividend boundary fixture:
a raw 10-to-5 split with factor 1-to-2 has zero adjustment-aware return rather
than a -50% mechanical return. Local data-quality checks also record factor
coverage and date ranges before outcomes are interpreted. The benchmark remains
raw index level by contract because no benchmark `adj_factor` series is present.

## 3. Frozen fundamental follow-through

The fundamental branch is independent of the market branch:

```text
selection at T ──> market outcome
             └───> fundamental outcome
                    ├── next distinct report period
                    └── next two distinct report periods
```

The evaluation-only `fina_indicator` projection uses these ratio fields:

| Outcome metric | Source field | Conversion |
| --- | --- | --- |
| `revenue_yoy` | `tr_yoy` (fallback `or_yoy`) | percentage points / 100 |
| `profit_yoy` | `netprofit_yoy` (fallback `dt_netprofit_yoy`) | percentage points / 100 |
| `operating_profit_yoy` | `op_yoy` | percentage points / 100 |
| `margin` | `netprofit_margin` | percentage points / 100 |
| `cfo_cash_conversion` | `q_ocf_to_sales` (fallback `ocf_to_sales`) | percentage points / 100 |

`ann_date` is the declared availability field for this endpoint and `end_date`
is the report period. The mapping and source fields are preserved in the
campaign provenance.

For snapshot `T`, the baseline is the frozen snapshot metric when present;
otherwise it is the latest PIT-visible report at or before `T`. Future reports
are selected by **distinct report period**, not by the next arbitrary row. A
period is eligible only when its initial available version is strictly after
`T`. The earliest version for that period is used under
`first_available_version_after_snapshot`; later revisions/restatements are
counted and recorded in `all_disclosure_versions` but cannot change the
observation. Future data is joined only after frozen selection and cannot mutate
scan rows, ranks, score, or configuration.

A metric improves when its future value minus its frozen baseline is strictly
positive. Each metric records value, baseline, delta, status, reason, and sign
transition. Aggregate follow-through is `observed` only when at least two
metrics have valid deltas and a strict majority improve. Missing metrics do not
vote as failures or successes. The contract records:

- `next_report` follow-through;
- `next_two_reports` persistence, true only when both distinct reports have
  observed follow-through;
- `false_turnaround`, true only for an observed next report that fails the
  aggregate rule.

Zero/negative denominators in any supplied derived metric are explicit
`invalid_denominator` / `negative_denominator` states; no infinity, clipping, or
ordinary growth value is fabricated. Sign transitions are evidence, not an
ordinary growth rate. Missing report, second report, or metric remains a
reason-coded unavailable outcome and is not converted into failure.

## 4. Exposure and segment contract

- Market-cap exposure uses exact as-of `daily_basic.total_mv` and deterministic
  as-of cross-sectional terciles (`small`, `mid`, `large`).
- Industry is taken only from a frozen scan row or dated exposure row. Current
  `stock_basic.industry` is not used as a historical fallback because #32 marks
  that state `UNSUPPORTED_PIT`.
- Segments are descriptive only: year, the Issue #32 as-of regime label,
  market-cap bucket, industry where available, and horizon. No segment may be
  used to alter the baseline.

## 5. Availability and schema

The output has separate `market_outcomes` and `fundamental_outcomes` tables.
The compatibility `observations` view contains the same rows but does not merge
the two branches into a score. Every horizon and fundamental window reports
eligible, available, missing, coverage, and reason-code counts. Missing is not
failure.

Each result carries snapshot/run IDs, score/config fingerprints, input digests,
contract versions, calendar source, benchmark identity, adjustment convention,
fundamental revision policy, and the explicit separation/future-evaluation
flags.

## 6. Scope guard

This campaign has no:

- score, weight, or threshold tuning;
- Top-N search;
- holding-period or exit-policy optimization;
- transaction-cost or benchmark sensitivity sweep;
- Feature Ablation / Stability run;
- new feature formula or Score v2 decision;
- forward label flowing into Scanner selection;
- RAW rewrite or new data download.
