# Historical PIT replay validation contract v1 (issue #32)

`pit-replay-validation-v1` is a correctness-validation sample, not a
performance backtest.  It answers what the Scanner could observe at a selected
historical session and preserves the reason a security was included, excluded,
ranked, or gated.  It does not consume any observation after the selected
session and it does not use forward outcomes to choose a date, threshold,
configuration, regime, or ranking rule.

The final scope is explicitly two-layered: the monthly target schedule covers
all requested months, while complete evidence artifacts are limited to the
frozen representative sample. See
[`pit-replay-validation-scope-closure.md`](pit-replay-validation-scope-closure.md)
and the machine-readable
[`pit-replay-validation-sample-v1.json`](pit-replay-validation-sample-v1.json)
for the acceptance audit and exact sample list.

## Existing replay audit

Before implementation, the existing replay path was classified as follows:

- **ALREADY CORRECT:** financial selection uses `actual_available_date`, market
  feature helpers cap observations at `as_of`, benchmark-relative crowding is
  fail-closed, and #31 already retains diagnostic ranking plus evidence-gate
  fields.
- **CONFIRMED BUGS FIXED HERE:** the old universe path could consult current
  `stock_basic` status/name fields and its persisted replay artifact did not
  carry the complete universe decision log. Historical validation now calls the
  same replay engine with `pit_safe_only=True`, strips unsafe reference fields,
  records every decision, and passes dated suspension evidence.
- **SEMANTICALLY AMBIGUOUS:** `stock_basic` historical ST/name/industry/board
  state, `namechange` identity/history, and the publication time of
  `suspend_d` remain explicitly qualified rather than guessed.
- **UNSUPPORTED:** no historical status/industry/board backfill is introduced;
  no new source or alternate replay engine is used.

## Frozen monthly target rule

The default `monthly-anchor-15-v1` rule is:

1. enumerate calendar months in the requested inclusive range;
2. set `anchor_date` to the 15th day of that month;
3. use `trade_cal` (`is_open=1`, SSE by default) and select the first open
   session on or after the anchor, without crossing into another month;
4. emit `target_month`, `anchor_date`, `selected_trading_date`, and
   `selection_reason`;
5. mark a month `UNAVAILABLE_DATA` (exposed as the legacy execution status
   `UNAVAILABLE`) when no session can be proven, `UNAVAILABLE_FUTURE` when it
   is after the explicit selection cutoff, and
   `INCOMPLETE_CURRENT_MONTH` when the cutoff month cannot be proven complete.

A selected date is never a natural-calendar date.  The target-selection
cutoff is explicit orchestration metadata, not a feature observation cutoff.
The current month is marked `incomplete_month` and exposed as
`INCOMPLETE_CURRENT_MONTH` when the target month equals that cutoff month and
its fixed anchor has already been reached (including when no current-month
session is yet provable). No neighboring month is substituted. The validation
campaign freezes this cutoff at `20260830`; it is recorded in each
configuration/manifest. The schedule also records the selection-rule version,
calendar manifest ID, and as-of regime label/version for every month.

## Historical universe boundary

Validation invokes the existing `scanner.replay.run_replay_frames` path with
`historical-universe-v1` / `UniverseConfig(pit_safe_only=True)`.  The universe
artifact contains every decision and reason, not only Top-N rows.  This mode
uses only:

- stable `ts_code`;
- `list_date <= as_of` and listing-age policy;
- a supplied, proven-safe `delist_date` event;
- the stable `.BJ` symbol namespace for the configured BSE policy;
- dated `suspend_d.trade_date` when supplied;
- PIT-filtered market and financial history.

Current `stock_basic` `name`, `list_status/status`, `industry`, `market/board`,
and `exchange` values are not consulted or copied into historical rows.
Historical ST/name/industry/board state remains `UNSUPPORTED_PIT`; this is a
reported limitation, not a current-snapshot fallback.

## PIT gates

- Financial rows are selected through `actual_available_date <= as_of`; a
  later revision cannot rewrite an earlier snapshot.
- `daily`, `daily_basic`, `index_daily`, and `trade_cal` calculations are
  bounded by `trade_date/cal_date <= selected_trading_date`.
- `suspend_d` is treated as a dated trade-day observation.
- Crowding uses the explicit `000300.SH` benchmark.  A missing benchmark makes
  benchmark-relative evidence `UNKNOWN`; it never falls back to an absolute
  stock return.
- Attention self-history and cross-sectional population are sourced from
  visible sessions at or before the selected date.
- Formal Top-N consumes `ranking_eligible == true` and is sorted by score
  descending, then `ts_code` ascending.  `diagnostic_ranked` retains every
  scored candidate, including high-score ineligible and unknown-heavy rows.
- Every hard violation is a `FAILED` snapshot and stops subsequent runnable
  snapshots.  It is never downgraded to a warning.

## Memory and input boundary

The real-corpus path does not call `RawParquetStore.read` for all manifest
partitions.  It loads only the small calendar/reference base first, then reads
per snapshot with Parquet partition pruning and a feature-path column
projection.  Market rows are bounded to the required trailing session window;
financial rows are bounded by report-period partition and
`actual_available_date <= as_of` before entering the replay path.  The existing
replay engine still performs its own PIT selection and the evidence assertions
remain enabled.  When `artifact_output` is supplied, each complete snapshot is
written before its `ReplayResult` is released; `checkpoint.json` records
progress without duplicating RAW data.

This is a memory/disk safety boundary only. `resource-gate-v3` enforces
current `/proc/self/smaps_rollup` working-set metrics and fail-closes on
present low `MemAvailable`, live PSS/private overflow, unavailable large-run
telemetry, allocator failure, or sustained `/proc/vmstat` swap I/O while memory
is pressured. `ru_maxrss`, process `Swap`, low `SwapFree`, and net system
swap-used growth are recorded as diagnostic/soft-warning signals rather than
standalone failures. `summary.json` and `summary.md` record live telemetry,
timestamped gate samples, warning names, vmstat deltas, and the diagnostic
peak. This does not alter Score v1, feature formulas, evidence-confidence
thresholds, or ranking semantics. See
[pit-replay-resource-gate-v3.md](pit-replay-resource-gate-v3.md) for the audit.

## Versioned run manifest

Each snapshot records a machine-readable run manifest with:

```text
run_id, snapshot_id, as_of_date
input manifest IDs and dataset manifest IDs
financial and market corpus identities
universe / feature / score versions
comparable-period, trend, attention, crowding, evidence-confidence versions
pit-replay-validation-v1 and market-regime-v1
benchmark identity and contract
config hash, code commit, seed, warnings
```

`manifest.json` also records each raw partition's relative path, row count,
size, schema columns/hash, and content hash by default.  The manifest is
read-only and does not copy or rewrite RAW data.  Runtime timestamps are not
part of deterministic identities.

## Regime labels

`market-regime-v1` is an audit-coverage label only.  It uses the benchmark's
trailing 60-session change and trailing high drawdown, with fixed declared
thresholds (`+10%`, `-10%`, `-20%`) and only observations through `as_of`:

```text
bull  = trailing change >= +10% and drawdown > -20%
bear  = trailing change <= -10% or drawdown <= -20%
range = otherwise
unknown = endpoint/history unavailable
```

The label is not defined from Scanner outcomes.

## Synthetic adversarial fixtures

The validation module and test suite run a deterministic, no-network fixture
matrix before real snapshots: future financial revision, exact disclosure
boundary, future market row exclusion, missing benchmark, insufficient
attention history, delisted security, pre-listing security, missing critical
group/ranking gate, and deterministic tie handling. A fixture failure is a
hard stop; it cannot be hidden by marking a historical snapshot incomplete.

## Artifacts and manual review

`replay-validate` writes the following under the requested output directory:

```text
validation-targets.json       # Layer 1: every requested month/status
validation-targets.csv
manifest.json
summary.json
snapshots/<selected-date>-<status>.json.gz  # Layer 2 only
manual-review.json
synthetic-fixtures.json
checkpoint.json
summary.md
```

Every full sample artifact retains the complete `FeatureVector` evidence and
`ScoreResult` coverage/confidence/ranking gate, in addition to formal and
diagnostic rankings and universe inclusion/exclusion decisions. The monthly
schedule is not a full artifact and never substitutes for one. The manual
review subset is frozen at `2019-03` (bull), `2022-05` (bear), and `2025-06`
(range; existing validated artifact). Each review records Top-3, a high-score
ineligible candidate, an unknown-heavy candidate, and an exclusion boundary
case when present. Its checklist covers session validity, universe provenance,
financial revision visibility, benchmark/attention/crowding cutoffs,
current-field leakage, and warnings. Human sign-off is represented separately
from the machine precheck.

The staged commands are:

```bash
# Layer 1: enumerate every monthly target/status without a full replay
ashare-turnaround replay-validate --stage schedule --start 2017-01 --end 2026-12 \
  --today 20260830 --data-dir data \
  --output data/reports/issue32-target-schedule --no-content-hash

# Layer 2: execute only the frozen representative full-evidence sample.
# The retained 2025-06 v3 pair is reused and is not rerun.
ashare-turnaround replay-validate --stage sample --start 2017-01 --end 2026-12 \
  --today 20260830 --data-dir data \
  --output data/reports/issue32-representative-sample

# One explicit bounded target; diagnostic only, never a validation PASS.
ashare-turnaround replay-profile --as-of 20250616 --candidate-cap 100 \
  --data-dir data --output data/reports/replay-profile

# Legacy diagnostic slices (not the final Issue #32 scope command).
ashare-turnaround replay-validate --stage smoke --start 2017-01 --end 2026-12 \
  --today 20260830
ashare-turnaround replay-validate --stage yearly --start 2017-01 --end 2026-12 \
  --today 20260830
```

The output also writes a small `checkpoint.json` progress record; it is an
artifact pointer, not a second data store. The output reports only correctness
metrics: snapshot completion/status,
missing inputs, warnings, evidence coverage/confidence, unknown groups,
ranking eligibility, PIT violations, and deterministic repeat checks.  It
makes no claim about strategy performance and must be kept separate from the
later Evaluation and Ablation work.

## Physical artifact layout (post-#41 diagnostic)

The logical contracts above are unchanged. New JSON snapshots use the separate
physical layout `pit-replay-artifact-normalized-v1`: immutable provenance,
components, config, metadata, and repeated evidence arrays are referenced by
canonical SHA-256 content refs in a snapshot-local store. The decoder
(`expand_normalized_vector`, `expand_normalized_snapshot`) reconstructs the
legacy payload recursively. The representation is lossless; physical
deduplication never removes evidence and never changes Top-N, score,
eligibility, or PIT semantics.

The bounded attribution and performance measurements are recorded in
[pit-replay-artifact-normalization.md](pit-replay-artifact-normalization.md).
The final v3 full validation pair is preserved in the ignored local outputs
`data/reports/issue32-resource-v3-full-baseline1/` and
`data/reports/issue32-resource-v3-full-determinism2/`. Both report machine
status `READY`, resource status `PASS`, 5,102/5,102 candidates, zero failed
snapshots, zero PIT violations, and equal semantic/artifact digests. The
2,781,058,369 B gzip-1 artifact passes `gzip -t` and is byte-identical across
the pair. It is formally reused as the `2025-06` member of the Layer-2 sample,
not evidence that the other frozen members have run. The complete scope and
acceptance state are tracked in
[pit-replay-validation-scope-closure.md](pit-replay-validation-scope-closure.md).
The earlier **resource-gate-v2 baseline #1 resource-failed run** at `f58e866`
remains historical evidence only and is not a member of this pair. The v3
calibration and bounded synthetic contract are documented in
[pit-replay-resource-gate-v3.md](pit-replay-resource-gate-v3.md); the
finalization measurements remain in
[pit-replay-finalization-working-set.md](pit-replay-finalization-working-set.md).
