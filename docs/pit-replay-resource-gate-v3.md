# PIT replay resource-gate-v3 calibration

Issue #32 / PR #41, branch `research/32-pit-replay-validation-sample`.
This document records the resource-contract calibration only.  It does not
change PIT visibility, feature formulas, score weights, ranking, Top-N,
workers, `max_in_flight`, RAW files, or the normalized artifact layout.

## A. v2 audit

The preserved real full run must remain labelled **resource-gate-v2 baseline #1
resource-failed run**.  It is not a determinism baseline.  Its correctness
signals were healthy: 5,102/5,102 candidates completed, PIT violations were
zero, runtime was below 7,200 seconds, live PSS/private peaked at about 2.566
GiB, `MemAvailable` stayed above about 10.4 GiB, and candidate-loop process
swap stayed at zero.  The failure was observed during CAS finalization after
system swap occupancy and the sampled process swap crossed v2's hard limits.

The v2 checks conflated three different observations:

- `smaps_rollup:Swap` is the process's pages currently backed by swap.  It can
  contain cold pages swapped out earlier; it is not a swap-I/O rate and does
  not prove current thrashing.
- `SwapFree` is available swap capacity.  Linux does not have to pull a cold
  page back into RAM merely because `MemAvailable` later improves, so a low
  value can persist after pressure has ended.  Low `SwapFree` alone is not
  proof of present memory starvation.
- `SwapTotal - SwapFree` is system-wide swap occupancy.  The existing
  baseline-to-sample delta is a net change over the replay window, not a
  process-attributed I/O counter.  Other NAS processes or unrelated host work
  can change it, including while the CAS finalization phase is running.

`ru_maxrss` remains a lifetime high-water diagnostic and is never used as a
live gate.  Current PSS/private (or current `VmRSS` fallback), current
`MemAvailable`, and swap-I/O deltas are the measurements used by v3.

## B. v3 decision model

`resource-gate-v3` reports one of:

- `PASS`: hard signals are healthy and no soft swap warning was observed;
- `PASS_WITH_WARNING`: hard signals are healthy, but historical/process swap
  occupancy, low `SwapFree`, or net system swap occupancy crossed a soft limit;
- `FAIL`: present memory exhaustion, runaway live working set, active swap
  thrashing, allocator failure, or unavailable production telemetry.

The hard limits are:

- `MemAvailable >= 2 GiB`;
- live PSS and live private memory each `<= 6 GiB`;
- current `VmRSS <= 6 GiB` when `smaps_rollup` is unavailable;
- complete resource telemetry for a large-corpus production run;
- allocator/OOM failures are fail-closed;
- active swap pressure requires both `pswpin` and `pswpout` to advance by at
  least 64 MiB of I/O over a window of at least 30 seconds, at the declared
  rate, while current `MemAvailable < 4 GiB`.

The following remain soft warnings rather than standalone failures:

- process `Swap > 256 MiB`:
  `historical_process_swap_above_soft_limit`;
- `SwapFree < 512 MiB`:
  `system_swap_free_below_soft_floor`;
- net system swap occupancy growth `> 256 MiB` from the replay baseline:
  `system_swap_growth_above_soft_limit`.

A large run still fails closed if `/proc/meminfo`, live process memory, swap
capacity, or (when swap is enabled) the `/proc/vmstat` counters cannot be
sampled.  The `/proc/vmstat` counters are system-wide and can establish
activity/pressure, not process causality.  Counter resets are not treated as
new I/O.

## C. Evidence surface

Each resource sample records the current memory values, process swap,
`pswpin`/`pswpout` counters, baseline/window deltas, I/O rate, pressure-window
length, sampler completeness, status, warnings, and hard failures.  The final
resource summary records minima/peaks and the aggregate status.  Resource
warnings are also copied into:

- the validation result decision metadata;
- the top-level `manifest.json` and `summary.json`;
- the per-snapshot run manifest when observed before snapshot promotion;
- the human-readable `summary.md`.

Resource telemetry timestamps and values are runtime diagnostics and are not
part of the logical candidate/vector/score/ranking/provenance equivalence
contract.  The existing semantic warning digest continues to cover replay
warnings; v3 resource warnings are reported separately from that logical
payload.

## D. Deterministic synthetic contract tests

`tests/test_replay_validation.py` covers:

1. healthy host with no swap: `PASS`;
2. high `MemAvailable` and low live PSS/private with already-swapped cold
   pages: `PASS_WITH_WARNING`;
3. `MemAvailable < 2 GiB`: hard `FAIL`;
4. live PSS above 6 GiB: hard `FAIL`;
5. degrading available memory with sustained `pswpin`/`pswpout`: hard `FAIL`;
6. unavailable large-run telemetry: fail closed.

The existing full test suite also preserves PIT, artifact, digest, and cleanup
regressions.  No swap is disabled or cleared by the tests.

## E. Validation disposition

The v2 resource-failed run remains historical evidence only.  After v3, the
authorized sequence is:

1. run the exact bounded `candidate_limit=100` semantic regression;
2. verify PIT, vector/score/universe/ranking/diagnostic/provenance and RAW
   invariants;
3. run exactly one frozen 5,102-candidate baseline;
4. only after a valid artifact-producing baseline, run the identical second
   determinism baseline.

A healthy full replay may therefore finish as
`FULL_SMOKE_PASS_WITH_RESOURCE_WARNING`.  That warning does not ignore resource
risk: it means live memory and `MemAvailable` were healthy and no sustained
swap-I/O pressure was observed, so Linux's retained cold-page swap occupancy
did not independently become a correctness hard failure.

The authorized bounded regression has now completed under the ignored local
output `data/reports/issue32-resource-v3-cap100/`:

- frozen target `2025-06`, selected session `20250616`, cutoff `20260830`;
- `workers=2`, `max_in_flight=2`, `top_n=3`, candidate cap 100;
- 100 / 5,102 candidates, 231.101 seconds wall, failed snapshots 0, PIT
  violations 0;
- candidate-vector, score, universe, formal ranking, diagnostic ranking,
  semantic warning, and provenance-store digests were unchanged from the
  previous cap=100 semantic output; normalized artifact logical components and
  integrity checks were `PASS`;
- v3 resource status `PASS`, no soft warnings, minimum MemAvailable
  10,727,854,080 B, minimum SwapFree 1,001,267,200 B, peak live PSS
  1,436,367,872 B, peak live private 1,435,246,592 B, process swap 0, and no
  active pressure window.

The bounded run was followed by exactly one frozen 5,102-candidate full
baseline and, only after it completed with a valid artifact, one identical
full determinism baseline. Both used `2025-06`, selected session `20250616`,
`today=20260830`, `top_n=3`, `seed=0`, `workers=2`, `max_in_flight=2`,
`candidate_limit=None`, `determinism_sample=0`, and `content_hash=False`.

Full baseline #1 (`data/reports/issue32-resource-v3-full-baseline1/`) and
repeat #2 (`data/reports/issue32-resource-v3-full-determinism2/`) each report
`status=READY`, `gate_status=READY`, 5,102/5,102 candidates, failed snapshots
0, PIT violations 0, warnings 0, and 76 diagnostic ranking-ineligible rows.
Wall time was 6,059.104 s and 6,089.986 s. Resource status was `PASS` with no
warnings in both runs. Baseline/repeat minimum MemAvailable was
10,414,280,704 B / 9,890,746,368 B; minimum SwapFree was
680,230,912 B / 849,289,216 B; peak live PSS was 2,133,456,896 B /
2,189,577,216 B; peak live private was 2,130,722,816 B / 2,186,797,056 B;
and process swap was 0 B in both. The vmstat windows observed 43,241 in /
22,244 out pages and 12,583 in / 0 out pages respectively, with active
pressure `False` in both. The swap counters therefore remained diagnostics,
not a false Swap Thrashing failure.

Both gzip-1 artifacts are 2,781,058,369 B, pass `gzip -t`, and have the same
SHA-256 `142082b0649180e09e0dea946feb868f6e831d314c39324c2e69a37a154adce8`.
The repeat comparison is `PASS`: all 5,102 candidate-vector digests and the
score, universe, formal-ranking, diagnostic-ranking, warning, and
provenance-store digests are byte-for-byte equal. The formal Top-3 is
`688233.SH`, `002355.SZ`, `688615.SH` with scores 91.7192465382, 91.3143860739,
and 90.4367116778. RAW postflight is `PASS` (911 files, 1,821,251,649 B,
metadata digest `df03e77557b1bddd14de9d50177794c4b945af213efbe9dc6223d0670ddc825e`,
unchanged from the previous postflight).

The final disposition is **`FULL_SMOKE_PASS`**. The machine status remains
`READY`; no merge was performed.
