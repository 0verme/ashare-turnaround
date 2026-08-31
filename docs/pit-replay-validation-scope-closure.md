# Issue #32 scope closure: monthly targets versus full evidence

This document closes the scope ambiguity identified after the resource-gate-v3
full validation pair. It is part of `pit-replay-validation-v1`; it does not
change PIT semantics, feature formulas, score weights, ranking eligibility,
Top-N behavior, workers, RAW data, or the artifact layout.

## Interpretation of the original acceptance criteria

Issue #32 requires an approximately monthly **validation target** across
2017--2026. It does not say that every target must materialize a multi-gigabyte
full evidence artifact. The issue itself says that the set is intentionally
small enough to inspect, and its acceptance criteria separately require a
machine-readable artifact, a human-readable summary, and a declared manual
review sample.

The accepted contract therefore has two layers:

| Layer | Coverage | Required output | Full replay required |
| --- | --- | --- | --- |
| Monthly Target Schedule | every requested month, `2017-01` through `2026-12` | small JSON/CSV target and availability records, manifest, summary | No |
| Full Evidence Validation Sample | frozen representative members only | complete production replay artifact per member, except retained existing evidence | Yes |

A monthly target is not silently promoted to a full artifact. Conversely, a
full sample artifact may not omit evidence because it is large. Unavailable,
future, and incomplete months remain explicit schedule records and never fall
back to a neighboring month, the current universe, or a current snapshot.

The exact Layer-2 configuration is tracked in
[`pit-replay-validation-sample-v1.json`](pit-replay-validation-sample-v1.json).

## Acceptance-criteria audit

The audit below maps the original Issue #32 checklist to the implementation and
the current execution evidence. `PARTIAL` means the contract exists but the
representative full-sample execution or human sign-off is not yet complete.

| Criterion | Current status | Evidence | Gap | Required action |
| --- | --- | --- | --- | --- |
| Versioned rule yields one fixed valid trading-date target per available month and reports unavailable months | PASS (Layer 1) | Read-only schedule run: 120 targets, 108 available, 7 `UNAVAILABLE_DATA`, 1 `INCOMPLETE_CURRENT_MONTH`, 4 `UNAVAILABLE_FUTURE`; digest `9869849b0a22a5e64b482677b4cceb1315c027f8222ec842b4af52cd4c310bf8` | The schedule is runtime-bound to the input manifest | Retain `validation-targets.json/csv` from the final campaign |
| Bull, bear, and range periods are documented without future outcomes | PASS (contract) | `market-regime-v1`, benchmark-only trailing inputs through `as_of`, frozen sample config | Real full artifacts do not yet cover every frozen regime member | Execute the frozen Layer-2 members; do not relabel from returns |
| Every executed snapshot retains Top-N, complete universe decisions, feature evidence, score breakdown, confidence/coverage/unknowns, manifest, versions, and warnings | PARTIAL | Existing 2025-06 full pair and production normalized writer; synthetic lossless tests | Only the retained 2025-06 member has full-corpus evidence so far | Execute remaining frozen members one at a time |
| No financial/reference observation published after `as_of` is used | PASS (gate) | Candidate/artifact PIT validator; revision and exact-boundary fixtures; 2025-06 pair has zero violations | No new correctness gap identified | Preserve the hard-stop validator for every full member |
| Historical universe is used; current lists/statuses cannot substitute | PASS (gate) | `historical-universe-v1`, `pit_safe_only`, universe decision audit, status/delist fixture | Historical ST/name/industry/board state remains unsupported and must stay disclosed | Keep the limitation visible in every summary/manual checklist |
| Fixed seed/configuration reproduces candidates, ranks, warnings, and metadata | PASS (coverage contract) | 2025-06 full baseline #1 plus identical determinism #2; bounded semantic tests | The pair is not repeated for every member by default | Use the same deterministic path for the sample; perform only declared bounded duplicate checks |
| Revised-disclosure, delisted/status, missing-data, and boundary fixtures exist | PASS | `run_adversarial_fixtures`, `synthetic-fixtures.json`, adversarial test suite | None | Run fixtures before every campaign |
| Machine-readable validation artifact and human-readable summary exist | PASS (implementation) | `write_replay_validation_artifacts`, `summary.json`, `summary.md`, target JSON/CSV | Final campaign output is not committed with RAW | Generate local ignored campaign output and retain checksums |
| Manual review is possible on a declared sample without UI or network secret | PASS (workflow) | `manual-review-sample-v2`, fixed three-month subset, JSON checklist | Human sign-off has not been recorded in this scope-freeze step | Review Top-3, diagnostic ineligible/unknown, exclusion, evidence and PIT fields |
| Summary reports counts, missing/incomplete rates, warnings, limitations, and no tuning | PARTIAL | summary implementation and this scope contract | Counts for the representative campaign are not complete until remaining members run | Publish the final summary after sample execution |
| Validation gates #17 and #18 | NOT RUN | No Evaluation/Ablation was run; existing 2025 pair is correctness evidence only | Overall Issue #32 scope is not closed by one snapshot | Do not start #17/#18 until the representative sample and manual review close |

No criterion above authorizes a 120-artifact monthly batch. The schedule is
complete at Layer 1; the remaining gap is bounded Layer-2 evidence coverage.

## Layer 1: monthly target schedule

The schedule covers the inclusive range `2017-01` through `2026-12`.
For each month it records:

- `target_month`, fixed `anchor_date`, and `selected_trading_date`;
- `availability_status` (`AVAILABLE`, `UNAVAILABLE_DATA`,
  `UNAVAILABLE_FUTURE`, or `INCOMPLETE_CURRENT_MONTH`);
- `incomplete_month` and `unavailable_reason`;
- `regime_label`, `regime_label_version`, and regime status/reason when an
  as-of label can be computed;
- `selection_rule_version`, calendar source/version/exchange, and the
  calendar dataset manifest ID;
- whether the month belongs to the frozen representative sample and why.

The selected date is the first open SSE `trade_cal` session on or after the
fixed 15th, never crossing the month. The preserved local-corpus schedule
run produced 120 records (108 available, 7 data-unavailable, 1 incomplete
current month, and 4 future). Its schedule digest was
`9869849b0a22a5e64b482677b4cceb1315c027f8222ec842b4af52cd4c310bf8`.
The explicit campaign cutoff is
`20260830`. A missing historical calendar/data unit is `UNAVAILABLE_DATA`; a
month after the cutoff is `UNAVAILABLE_FUTURE`; the cutoff month is
`INCOMPLETE_CURRENT_MONTH` when it cannot be proven complete. There is no
fallback to another month or to today's stock list.

The schedule command is intentionally cheap and does not call the feature
pipeline:

```bash
ashare-turnaround replay-validate \
  --stage schedule --start 2017-01 --end 2026-12 --today 20260830 \
  --data-dir data --output data/reports/issue32-target-schedule \
  --no-content-hash
```

It writes `validation-targets.json` and `validation-targets.csv` in addition
to the normal small manifest/summary/checkpoint files.

## Layer 2: frozen representative sample

Selection is frozen by `representative-regime-strata-v1` before any remaining
full replay is inspected:

- fixed calendar bands at the frozen corpus boundary: early
  `2017-01..2019-12`, middle `2020-01..2022-12`, late `2023-01..2025-12`;
  2026 remains Layer-1 schedule coverage and is not allowed to move the
  frozen sample if new data is later added;
- one midpoint of each available `(band, as-of regime)` stratum;
- the first member is used for the early-range and middle-range named boundary
  strata;
- append the fixed existing 2025-06 validation member and the latest complete
  target at the current-data boundary;
- selection inputs are calendar position, as-of-only regime, data
  availability, and declared boundary coverage;
- forward returns, scanner scores, Top-N results, and post-as-of observations
  are forbidden selection inputs.

The frozen members are:

| Month | Date | Band | As-of regime | Reason | Execution |
| --- | --- | --- | --- | --- | --- |
| 2017-01 | 20170116 | early | range | early range data boundary | required |
| 2018-10 | 20181015 | early | bear | early bear stratum midpoint | required |
| 2019-03 | 20190315 | early | bull | early bull stratum midpoint | required |
| 2020-01 | 20200115 | middle | range | middle calendar-year boundary | required |
| 2020-09 | 20200915 | middle | bull | middle bull stratum midpoint | required |
| 2022-05 | 20220516 | middle | bear | middle bear stratum midpoint | required |
| 2023-12 | 20231215 | late | bear | late bear stratum midpoint | required |
| 2024-05 | 20240515 | late | range | late range stratum midpoint | required |
| 2024-11 | 20241115 | late | bull | late bull stratum midpoint | required |
| 2025-06 | 20250616 | late | range | existing validated resource-gate-v3 evidence | `EXISTING_VALIDATED`, do not rerun |
| 2025-12 | 20251215 | late | range | latest complete current-data boundary | required |

The retained 2025-06 baseline #1 and determinism #2 artifacts are members of
this sample by reuse, not a new run. The remaining ten members are executed
sequentially, with a correctness/resource check and cleanup after each member.
A hard PIT failure stops the campaign; an unavailable frozen member is recorded
as such and is never replaced.

## Regime contract

`market-regime-v1` is an audit-coverage label, not an evaluation label. It uses
benchmark `000300.SH` only:

```text
trailing_change = close(as_of) / close(as_of - 60 sessions) - 1
trailing_high_drawdown = close(as_of) / max(close over trailing 252 sessions) - 1

bull  = trailing_change >= +10% and drawdown > -20%
bear  = trailing_change <= -10% or drawdown <= -20%
range = otherwise
unknown = missing/insufficient as-of benchmark history
```

All benchmark dates and the calendar session index are `<= as_of`. Future
returns, future peaks/troughs, and later scanner/evaluation outcomes are not
inputs.

## Determinism and manual review coverage

The 2025-06 full pair is the production full determinism proof: fixed seed and
configuration produced identical 5,102 candidate-vector digests, semantic
digests, formal/diagnostic rankings, Top-3, warnings, metadata, and gzip
artifact bytes. Other representative members use the identical deterministic
path. Synthetic adversarial tests and bounded duplicate checks cover the
remaining code paths; a second full run per member is not required unless the
Issue/contract is changed to require it.

The manual subset is frozen independently at three snapshots:

- `2019-03` (bull);
- `2022-05` (bear);
- `2025-06` (range, retained existing artifact).

For each, the checklist asks a human to inspect Top-3 formal candidates, one
high-score ineligible diagnostic candidate, one unknown-heavy candidate, and
one excluded-universe boundary case when present. It includes inclusion/
exclusion reasons, visible disclosures and availability dates, feature evidence,
unknown groups, coverage/confidence, score breakdown, PIT boundaries, and
ranking eligibility/reason. No UI or secret is required.

## Artifact policy and gate

Layer 1 artifacts are small. Layer 2 artifacts retain the complete normalized
logical evidence: vectors/evidence/provenance, all universe decisions and
exclusions, scores, confidence/coverage/unknown groups, formal and diagnostic
rankings, manifests, versions, warnings, and PIT checks. Compression or content
addressing is a physical representation only; evidence deletion is forbidden.

The final decision remains `ISSUE32_SAMPLE_EXECUTION_REQUIRED` until the ten
remaining frozen members have either produced complete passing artifacts or
been explicitly recorded as unavailable under this contract, and the declared
manual-review checklist has been completed. Evaluation #17 and Ablation #18
remain out of scope and may start only after Issue #32 is accepted and PR #41
is merged.
