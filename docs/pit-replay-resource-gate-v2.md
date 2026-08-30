# PIT replay resource-gate-v2 and cutoff audit

Issue #32 / PR #41, branch `research/32-pit-replay-validation-sample`.
This document records the small resource/cutoff repair only.  It does not
change feature formulas, scores, PIT selection, ranking, or RAW data.

## A. Old resource-gate semantics

Before this repair, `MAX_PEAK_RSS_BYTES` was `6 * 1024**3`.  `_host_memory()`
read `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` and the runtime gate
treated a value above six GiB as a hard `ResourceBlocked` failure.

The runtime assertion was reached after `run_replay_frames()` returned (and
therefore after candidate completion and worker-executor shutdown), PIT
validation, deterministic digest construction, and `record_snapshot()`.  The
snapshot writer had already performed CAS merge, gzip writing, artifact
promotion, and cleanup.  `ru_maxrss` is a lifetime historical high-water value;
a failure at that final assertion does not identify the phase that caused the
high-water value.

The preserved real result is therefore labelled **pre-resource-gate-v2 full
correctness run**.  Its `5102/5102`, PIT, digest, artifact, runtime, and CAS
observations remain evidence, but it is not retroactively a
`FULL_SMOKE_PASS`.

## B. Live telemetry and hard gate

On Linux the gate now reads `/proc/self/smaps_rollup` and records:

- `current_rss_bytes` from `Rss`;
- `current_pss_bytes` from `Pss`;
- `current_private_bytes` as `Private_Clean + Private_Dirty`;
- `current_swap_bytes` from `Swap`.

`ru_maxrss` is retained as `peak_rss_diagnostic_bytes` only.  Its old
`peak_rss_bytes` spelling is a compatibility/reporting alias and is never an
enforcement input.

If `smaps_rollup` is unavailable, the code uses current `VmRSS` and `VmSwap`
from `/proc/self/status`; PSS/private remain unavailable and the report names
the fallback.  It never substitutes `ru_maxrss` for a live metric.  A
large-corpus run fails closed if no live process metric is available.

`resource-gate-v2` declares these hard limits without raising the existing
six-GiB budget:

- `MemAvailable >= 4 GiB`;
- swap free at least the existing `512 MiB` floor when swap is enabled;
- system swap-used growth no more than the existing `256 MiB` contract;
- current PSS and current private memory no more than `6 GiB`;
- current process swap no more than `256 MiB`.

The runtime report samples the baseline, post-worker, post-artifact-cleanup,
and validation-complete stages with timestamps.  These samples distinguish
live pressure from the diagnostic lifetime peak; they do not claim causality
for an `ru_maxrss` value.

## C. Current-month audit and cutoff

The old selection path was traced as follows:

1. the CLI `--today` value is passed to `run_replay_validation`;
2. `_effective_validation_today()` uses that explicit value, or (for legacy
   in-memory callers that pass `None`) the maximum supplied `trade_cal` date;
3. `select_monthly_snapshot_dates()` marks `incomplete_month` only when the
   target month equals that effective cutoff month;
4. production selection uses the unprojected base `trade_cal`;
5. the per-target `frame_loader` separately projects market and financial
   inputs to the selected trading date.

The preserved full-run metadata has `target_month=2025-06`,
`selected_trading_date=20250616`, and `incomplete_month=true`.  Under the old
invocation this means the effective selection cutoff was in June 2025 (the
run used the historical as-of date as the `today` cutoff), rather than the
calendar being clipped to `20250616` for feature computation.  The latter
would have been a different bug; production orchestration keeps selection
calendar and feature/PIT frames separate.

The repair freezes the next validation campaign at the explicit cutoff
`20260830` (override remains available through `--today`).  It is written to
`configuration.today`, `configuration.target_selection`, the run manifest,
and the target metadata.  Consequently:

- historical `2025-06` with as-of `20250616` is not incomplete;
- `2026-08` follows the existing current-month incomplete rule;
- `2026-09` and later are `UNAVAILABLE_FUTURE`;
- features for `2025-06` still receive only `as_of=20250616`.

The CLI and documented validation commands now use this one explicit cutoff;
both future deterministic full runs must use exactly the same value.

## D. Determinism consequence and decision

The nested resource declaration is versioned as `resource-gate-v2`, and the
frozen cutoff is part of the declared configuration.  The old config digest,
artifact SHA, and run-manifest digest are preserved for regression comparison
only; they are not the final same-config baseline for the repaired contract.
A new pair is required: full run baseline #1, then (only after its
`FULL_SMOKE_PASS`) the identical full run #2 with semantic digest comparison.

The current decision after static/bounded repair is
**READY_FOR_FULL_SMOKE_AGAIN**, not `FULL_SMOKE_PASS`.  No full 5102 rerun,
yearly/monthly/Evaluation run, or RAW download is part of this repair.
