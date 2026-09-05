# Scanner 基线收益与基本面延续性评估

**Report contract:** `baseline-evaluation-contract-v1`  
**Evaluation engine:** `evaluation-v3`  
**Scope:** frozen complete Scanner only；本报告不是调参报告，也不是投资建议。

## A. Baseline

| Item | Value |
| --- | --- |
| repo | `ashare-turnaround` |
| branch | `research/baseline-evaluation-campaign` |
| before HEAD | `14ff21398d753242ccd4891c28b9409d78a5957e` |
| origin/main at campaign start | `14ff21398d753242ccd4891c28b9409d78a5957e` |
| Phase 2.5 gate | `PHASE2_5_READY_TO_CLOSE` |
| Issue #26 | closed after acceptance audit |
| Issue #32 / PR #43 | closed / merged in main |
| working tree | clean at final validation (generated data remains local/gitignored) |
| tests | `307 passed, 1 skipped` (integration skip) |
| return semantics gate | PASS；exact selected endpoints had `980/980` positive finite factors |

The 11 available Scanner snapshots used below are the 10 frozen Issue #32
representative members plus the retained 2025-06 validated member. They were
projected from existing full artifacts; no full artifact was copied into the
baseline output.

## B. Phase 2.5 Closure

| Item | Status |
| --- | --- |
| #27 Comparable financial periods | PASS |
| #28 Trend / acceleration | PASS |
| #29 Low Attention v2 | PASS |
| #30 Expectation / Crowding v2 | PASS |
| #31 Evidence / Confidence | PASS |
| #32 Historical PIT replay validation | PASS |
| **Final** | **`PHASE2_5_READY_TO_CLOSE`** |

The detailed closure matrix is in
[`phase2.5-closure-matrix.md`](phase2.5-closure-matrix.md). Issue #26 was closed
with a Chinese closure comment. No correctness gap, Score v2 change, or return
tuning was found in the gate audit.

## C. Evaluation Framework Audit

| Area | Before | Status | Action |
| --- | --- | --- | --- |
| frozen selection vs outcomes | diagnostic/ineligible rows could be selected; branches were combined | GAP | formal `ranking_eligible` selection and separate schemas |
| benchmark | optional/combined `daily` source | GAP | fixed `000300.SH` / CSI 300 from `index_daily` |
| horizons | union of `daily` dates | GAP | open `trade_cal` sessions: 20/60/120/250D |
| suspension | generic missing endpoint | GAP | exact endpoint, explicit suspension reason, no carry-forward |
| delisting | dated assumption existed but was not a separate schema field | GAP | retained dated `-1.0` assumption and reason code |
| transaction cost | 0 default and ambiguous `×2` implementation | GAP | one frozen 30 bps round-trip total deduction |
| price adjustment | raw `close` only | GAP | stock `close × adj_factor`; missing factor is unavailable |
| next report | first row inside price horizon | GAP | next distinct report period after T |
| next two reports | unsupported | UNSUPPORTED | added distinct-period persistence output |
| revisions | no selected-version evidence | GAP | first post-T available version; later revisions recorded, not used |
| missingness/provenance | generic drop-na summaries | GAP | reason-coded outcome rows, digests, revision evidence, branch flags |
| second evaluator | none | ALREADY_CORRECT | extended shared `scanner.evaluation`; no ablation run |

Full audit: [`evaluation-framework-audit.md`](evaluation-framework-audit.md).

## D. Frozen Evaluation Contract

- snapshot schedule: Issue #32 `monthly-anchor-15-v1`, starting `2017-01`,
  first open SSE session on/after the 15th, same-month only;
- baseline decision: **Top-20**, with Top-3/Top-10 descriptive only;
- benchmark: **`000300.SH` / CSI 300**;
- horizons: **20D / 60D / 120D / 250D** open-market sessions;
- entry: Scanner `as_of` session close;
- exit: Nth strictly subsequent `trade_cal` open session, exact endpoint;
- stock return: adjustment-aware `close × adj_factor` endpoint ratio;
- benchmark return: raw `index_daily.close` endpoint ratio on the same dates;
- cost: **30 bps round-trip total**, not a sensitivity sweep;
- dated delist inside a window: `delisted_assumption = -1.0`; no survivor-only
  deletion;
- fundamental next report / next two reports: distinct report periods, first
  post-T available version, with `report_period`, `disclosure_version`, and
  `actual_available_date` recorded;
- fundamental follow-through: strict majority of available positive deltas,
  minimum two metrics; missing is neither success nor failure;
- joint diagnostic: gross `excess_return > 0` crossed with next-report
  `fundamental_improved`, reported separately by each fixed horizon.

Frozen contract: [`baseline-evaluation-contract-v1.md`](baseline-evaluation-contract-v1.md).

## E. Snapshot Campaign

| Item | Count / status |
| --- | ---: |
| requested schedule targets | 120 |
| schedule `AVAILABLE` targets | 108 |
| schedule unavailable | 12 (`7` data, `1` incomplete current, `4` future) |
| lightweight snapshots completed | 11 |
| reused compatible Issue #32 full artifacts | 11 |
| exact new Scanner replay runs | 0 |
| compatible snapshots unavailable | 97 |
| projection/replay failures | 0 |
| PIT violations in reused artifacts | 0 |
| baseline rows | 220 (11 × Top-20) |

The reusable artifacts were read only to project the ranked rows needed for
outcomes. The 11 local Parquet snapshots are approximately 16 KiB each. No RAW
was downloaded, rewritten, or duplicated. The 97 missing monthly snapshots were
not approximated and are carried as explicit unavailable campaign records;
therefore this report must not be read as a completed 108-month statistical
campaign.

Descriptive Top-3 (not a choice among N):

| Snapshot | Regime | Top-3 |
| --- | --- | --- |
| 2017-01 | range | `000038.SZ`, `600338.SH`, `002750.SZ` |
| 2018-10 | bear | `600215.SH`, `601360.SH`, `603127.SH` |
| 2019-03 | bull | `600817.SH`, `000543.SZ`, `000055.SZ` |
| 2020-01 | range | `601099.SH`, `000061.SZ`, `603508.SH` |
| 2020-09 | bull | `002164.SZ`, `600679.SH`, `300404.SZ` |
| 2022-05 | bear | `300343.SZ`, `002759.SZ`, `000792.SZ` |
| 2023-12 | bear | `301371.SZ`, `688623.SH`, `601816.SH` |
| 2024-05 | range | `688008.SH`, `301566.SZ`, `603955.SH` |
| 2024-11 | bull | `002670.SZ`, `301538.SZ`, `688253.SZ` |
| 2025-06 | range | `688233.SH`, `002355.SZ`, `688615.SZ` |
| 2025-12 | range | `002546.SZ`, `688685.SH`, `002016.SZ` |

## F. Outcome Availability

Cells are `available / Top-20`. Fundamental availability means an aggregate
follow-through outcome, not merely the existence of a raw row.

| Snapshot | Regime | 20D | 60D | 120D | 250D | next report | next 2 reports |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2017-01-16 | range | 19/20 | 20/20 | 19/20 | 20/20 | 20/20 | 20/20 |
| 2018-10-15 | bear | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2019-03-15 | bull | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2020-01-15 | range | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2020-09-15 | bull | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2022-05-16 | bear | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2023-12-15 | bear | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2024-05-15 | range | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2024-11-15 | bull | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 |
| 2025-06-16 | range | 20/20 | 20/20 | 20/20 | 0/20 | 20/20 | 20/20 |
| 2025-12-15 | range | 0/20 | 0/20 | 0/20 | 0/20 | 20/20 | 0/20 |
| **Total** |  | **199/220** | **200/220** | **199/220** | **180/220** | **220/220** | **200/220** |

Market missing reasons: `incomplete_market_window` = 20/20/20/40 by horizon;
`suspended_at_exit` = 1 at 20D and 1 at 120D. Fundamental next-two missing:
`missing_second_report_period` = 20. CFO/cash-conversion has one
`missing_metric`; it is not silently treated as a failure.

## G. Market Outcomes

Returns below are gross unless the column is explicitly `net`; all use the
adjustment-aware stock return contract and fixed CSI 300 benchmark.

| Horizon | Obs / eligible | Mean | Median | Net mean | Benchmark | Excess | Abs hit | Excess hit | Cohort drawdown | Coverage | IQR |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20D | 199/220 | 4.20% | 2.97% | 3.90% | 1.99% | 2.22% | 62.31% | 55.28% | -6.46% | 90.45% | 10.79% |
| 60D | 200/220 | -0.23% | -3.35% | -0.53% | 1.59% | -1.82% | 41.00% | 36.50% | -17.57% | 90.91% | 21.76% |
| 120D | 199/220 | 7.98% | 2.69% | 7.68% | 9.22% | -1.24% | 52.76% | 41.71% | -12.23% | 90.45% | 36.54% |
| 250D | 180/220 | 7.56% | 0.18% | 7.26% | 14.22% | -6.66% | 50.56% | 33.89% | -11.28% | 81.82% | 43.68% |

All 880 candidate-horizon rows retain status/reason fields. No dated delisting
was encountered in these Top-20 observations (`delisted_count=0`); the dated
policy remains active and was covered by the #32 synthetic boundary fixtures.
Jaccard turnover over the 11 irregularly spaced observed snapshots was
`98.96%`; this is descriptive only and is not a monthly portfolio turnover
claim.

Worst observed absolute-return rows (not selected for tuning):

| Horizon | Five worst observations |
| ---: | --- |
| 20D | `002607.SZ@2024-05 -25.30%`; `603023.SH@2024-05 -20.89%`; `603828.SH@2024-05 -18.89%`; `603955.SH@2024-05 -15.93%`; `000031.SZ@2020-01 -15.40%` |
| 60D | `300842.SZ@2024-05 -30.16%`; `002869.SZ@2020-09 -30.05%`; `002524.SZ@2020-01 -29.03%`; `300684.SZ@2018-10 -27.80%`; `300052.SZ@2020-01 -26.71%` |
| 120D | `600620.SH@2023-12 -53.36%`; `600095.SH@2020-09 -44.28%`; `002869.SZ@2020-09 -42.48%`; `002456.SZ@2020-09 -39.95%`; `002645.SZ@2017-01 -39.59%` |
| 250D | `002869.SZ@2020-09 -67.15%`; `600781.SH@2018-10 -59.93%`; `300300.SZ@2020-01 -58.88%`; `300052.SZ@2020-01 -51.35%`; `603508.SH@2020-01 -48.13%` |

## H. Fundamental Outcomes

The outcomes are independent of market horizon and use the fixed four-metric
follow-through rule.

| Window / metric | Eligible | Available | Missing | Coverage | Positive follow-through | False-turnaround |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| next report / aggregate | 220 | 220 | 0 | 100.00% | 34/220 = 15.45% | 186/220 = 84.55% |
| next report / Revenue YoY | 220 | 220 | 0 | 100.00% | 80/220 = 36.36% | 140 |
| next report / Profit YoY | 220 | 220 | 0 | 100.00% | 53/220 = 24.09% | 167 |
| next report / margin | 220 | 220 | 0 | 100.00% | 83/220 = 37.73% | 137 |
| next report / CFO conversion | 220 | 219 | 1 | 99.55% | 104/219 = 47.49% | 115 |
| next two reports / persistence | 220 | 200 | 20 | 90.91% | 17/200 = 8.50% | — |

As-of regime description for next-report aggregate:

| Regime | Available | Follow-through | False-turnaround |
| --- | ---: | ---: | ---: |
| bear | 60 | 14/60 = 23.33% | 46 |
| bull | 60 | 9/60 = 15.00% | 51 |
| range | 100 | 11/100 = 11.00% | 89 |

The low aggregate follow-through is a result, not a reason to alter Score or
metric thresholds. Missing next reports/metrics remain unavailable states;
none are reclassified as false-turnarounds.

## I. Segments

All segment views below are descriptive, not selection rules. `n` is the number
of observed candidate returns in that segment.

### Year

| Horizon | Year | n | Mean return | Median return | Mean excess | Abs hit |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20D | 2017 | 19 | 5.93% | 4.11% | 1.35% | 84.21% |
| 20D | 2018 | 20 | 7.70% | 7.12% | 5.18% | 85.00% |
| 20D | 2019 | 20 | 10.83% | 8.82% | 4.67% | 90.00% |
| 20D | 2020 | 40 | -1.36% | -2.39% | -2.21% | 37.50% |
| 20D | 2022 | 20 | 13.11% | 13.34% | 6.39% | 75.00% |
| 20D | 2023 | 20 | 0.15% | -0.38% | 1.97% | 45.00% |
| 20D | 2024 | 40 | 0.50% | -1.84% | 2.33% | 45.00% |
| 20D | 2025 | 20 | 6.10% | 6.20% | 2.38% | 80.00% |
| 60D | 2017 | 20 | -2.11% | -3.60% | -5.92% | 30.00% |
| 60D | 2018 | 20 | 0.13% | 2.38% | 1.67% | 55.00% |
| 60D | 2019 | 20 | 2.16% | -9.64% | 4.57% | 25.00% |
| 60D | 2020 | 40 | -9.16% | -12.15% | -8.06% | 20.00% |
| 60D | 2022 | 20 | 20.65% | 21.90% | 15.60% | 90.00% |
| 60D | 2023 | 20 | -3.28% | -5.35% | -10.34% | 40.00% |
| 60D | 2024 | 40 | -6.15% | -7.09% | -1.54% | 27.50% |
| 60D | 2025 | 20 | 10.79% | 10.63% | -4.53% | 75.00% |
| 120D | 2017 | 19 | -12.23% | -17.54% | -22.59% | 21.05% |
| 120D | 2018 | 20 | 44.02% | 31.47% | 16.16% | 100.00% |
| 120D | 2019 | 20 | 6.75% | -6.96% | 1.32% | 35.00% |
| 120D | 2020 | 40 | -1.94% | -10.35% | -11.31% | 32.50% |
| 120D | 2022 | 20 | 11.01% | 3.75% | 17.13% | 65.00% |
| 120D | 2023 | 20 | -6.46% | -8.44% | -12.06% | 25.00% |
| 120D | 2024 | 40 | 11.28% | 8.51% | 5.47% | 70.00% |
| 120D | 2025 | 20 | 16.98% | 9.17% | -1.72% | 75.00% |
| 250D | 2017 | 20 | -8.26% | -16.57% | -40.29% | 20.00% |
| 250D | 2018 | 20 | 16.02% | 12.03% | -7.79% | 70.00% |
| 250D | 2019 | 20 | 0.83% | -10.54% | 1.43% | 40.00% |
| 250D | 2020 | 40 | -5.81% | -19.16% | -23.98% | 30.00% |
| 250D | 2022 | 20 | 7.89% | 9.13% | 10.35% | 60.00% |
| 250D | 2023 | 20 | 9.86% | 4.28% | -9.28% | 65.00% |
| 250D | 2024 | 40 | 26.66% | 10.38% | 16.80% | 70.00% |

### Regime

| Horizon | Regime | Available / total | Mean return | Median return | Mean excess | Abs hit | Excess hit |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20D | bear | 60/60 | 6.99% | 4.52% | 4.51% | 68.33% | 71.67% |
| 20D | bull | 60/60 | 6.17% | 2.65% | 3.67% | 66.67% | 51.67% |
| 20D | range | 79/100 | 0.59% | 1.03% | -0.62% | 54.43% | 45.57% |
| 60D | bear | 60/60 | 5.84% | 4.31% | 2.31% | 61.67% | 51.67% |
| 60D | bull | 60/60 | -1.50% | -7.48% | -2.11% | 30.00% | 30.00% |
| 60D | range | 80/100 | -3.82% | -6.61% | -4.68% | 33.75% | 30.00% |
| 120D | bear | 60/60 | 16.19% | 7.16% | 7.08% | 63.33% | 53.33% |
| 120D | bull | 60/60 | -0.19% | -6.96% | -4.45% | 38.33% | 38.33% |
| 120D | range | 79/100 | 7.94% | 4.54% | -5.11% | 55.70% | 35.44% |
| 250D | bear | 60/60 | 11.25% | 7.09% | -2.24% | 65.00% | 36.67% |
| 250D | bull | 60/60 | 3.02% | -6.28% | -2.73% | 41.67% | 36.67% |
| 250D | range | 60/100 | 8.40% | -1.76% | -15.00% | 45.00% | 28.33% |

### Market-cap bucket

Buckets are deterministic as-of cross-sectional terciles. No candidate lacks an
as-of market-cap exposure (`large=348`, `mid=272`, `small=260` across all
horizons).

| Horizon | Bucket | n | Mean return | Median return | Mean excess | Abs hit |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 20D | large | 83 | 5.12% | 3.02% | 2.90% | 66.27% |
| 20D | mid | 58 | 2.62% | 1.53% | 0.79% | 55.17% |
| 20D | small | 58 | 4.47% | 3.42% | 2.68% | 63.79% |
| 60D | large | 83 | 0.38% | -2.07% | -0.78% | 46.99% |
| 60D | mid | 58 | -2.60% | -6.34% | -5.65% | 37.93% |
| 60D | small | 59 | 1.26% | -2.52% | 0.49% | 35.59% |
| 120D | large | 82 | 7.51% | 3.43% | -1.10% | 54.88% |
| 120D | mid | 58 | 3.13% | -3.29% | -7.25% | 44.83% |
| 120D | small | 59 | 13.40% | 7.77% | 4.48% | 57.63% |
| 250D | large | 76 | 1.01% | -1.84% | -12.12% | 46.05% |
| 250D | mid | 48 | -1.28% | -1.18% | -17.57% | 45.83% |
| 250D | small | 56 | 24.02% | 7.88% | 10.10% | 60.71% |

### Industry

Industry segmentation is **unavailable** for this baseline: `880/880`
market-horizon rows have no frozen or dated PIT industry field. Current
`stock_basic.industry` was deliberately not used, consistent with Issue #32's
`UNSUPPORTED_PIT` limitation. No industry conclusion is made.

## J. Joint Diagnostic

The matrix uses gross benchmark excess and next-report aggregate fundamental
follow-through. Rows missing either outcome are outside that horizon's matrix
denominator.

| Horizon | Joint n | A: fundamental+ / market+ | B: fundamental- / market+ | C: fundamental+ / market- | D: fundamental- / market- |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 20D | 199 | 27 | 83 | 7 | 82 |
| 60D | 200 | 20 | 53 | 14 | 113 |
| 120D | 199 | 17 | 66 | 17 | 99 |
| 250D | 180 | 11 | 50 | 21 | 98 |

Interpretation is diagnostic only: A fits the intended joint hypothesis; B is
price-positive without fundamental follow-through; C is fundamental-positive
without market excess; D is a false-turnaround quadrant. No quadrant was used
to modify the Scanner.

## K. Limitations

1. Only `11/108` available monthly snapshots were available as compatible local
   lightweight inputs in this campaign. The missing 97 months are explicit and
   make the economic result **insufficient for a full-period claim**.
2. The 11 snapshots are intentionally irregular representative members, not a
   contiguous monthly sample. The 2025-12 market horizon is unavailable because
   the local daily corpus ends at 2025-12-31.
3. `index_daily` has no adjustment-factor series; benchmark is raw index level by
   contract. Stock adjustment factors were complete at the selected endpoint
   checks, but this does not prove every possible corporate-action data issue.
4. Current historical `stock_basic` name/status/industry/board state remains
   `UNSUPPORTED_PIT`; industry was therefore reported as missing rather than
   filled from today's reference.
5. Overlapping equal-weight cohort drawdown is a research summary, not a
   capital-constrained or executable portfolio path. No slippage, liquidity
   execution, or exit rule beyond the frozen endpoint was studied.
6. Future fundamentals use the local `fina_indicator` mapping and first
   post-snapshot disclosure version. Later restatements are recorded but not
   used; source coverage and vendor semantics limit generalization.
7. The strict-majority fundamental rule is a frozen measurement rule, not a
   calibrated predictor probability. Confidence/coverage is not return
   probability.
8. No multiple-testing correction or confidence interval is presented for this
   incomplete descriptive sample; the correct conclusion is guarded by
   availability rather than a headline return.

## L. Scope Guard

Confirmed for this campaign:

```text
no score tuning                 PASS
no threshold tuning             PASS
no Top-N search                 PASS (Top-20 frozen)
no holding-period optimization  PASS (20/60/120/250D all reported)
no transaction-cost search      PASS (30 bps frozen)
no benchmark search              PASS (000300.SH frozen)
no Feature Ablation             PASS (not run)
no Score v2                     PASS (no new Score v2 decision/change)
no ML / grid / Bayesian search  PASS
no RAW rewrite/download         PASS
```

The existing repository score identifier `score-v2` is the frozen upstream
scanner input label; this campaign did not create or select a new Score v2.
Future exit/holding research (`3 months`, next-quarter report, `+15%` stop)
remains out of scope.

## M. Final Decision

```text
INSUFFICIENT_EVIDENCE
```

Reason: the observed 11-snapshot subset does **not** support
`STRONG_SIGNAL` or `WEAK_BUT_CONSISTENT`. The only positive mean excess is at
20D (+2.22% gross; +3.90% absolute net mean), while 60D, 120D, and 250D mean
excess are negative (-1.82%, -1.24%, -6.66%) and medians are weak or negative.
The segment tables show clear year/regime dependence. The fundamental branch is
also weak: 15.45% next-report aggregate follow-through, 8.50% next-two-report
persistence among available rows, and 84.55% observed false-turnaround rate.

Because 97 of 108 available monthly targets lack a compatible lightweight
snapshot, this result is first a coverage gate, not a claim that the Scanner
has no signal. On the observed subset, however, there is **no clear repeatable
joint economic signal** strong enough to justify any Scanner change.

This is distinct from Scanner correctness: #32 PASS shows that the frozen
Scanner did not show the tested PIT/selection violations; it does not show
that the Scanner produces economic alpha.

## N. Git

| Item | Status |
| --- | --- |
| branch | `research/baseline-evaluation-campaign` |
| contract/audit commit | `9379ad8` |
| outcome/provenance commits | `4cdc9e1`, `c089f18`, `329af39` |
| report commit | generated with this research report; see `git log` |
| push / PR | not performed yet in this local report state |
| CI | not run remotely |

No automatic merge was performed. Baseline evaluation stops here; Feature
Ablation / Stability #18 is not started.
