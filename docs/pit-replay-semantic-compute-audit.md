# PIT replay financial semantic compute audit

Issue #32 / PR #41, branch `research/32-pit-replay-validation-sample`.
Artifact layout, gzip/CAS schemas, formulas, PIT/comparable semantics, evidence,
score, and ranking contracts were frozen for this audit.

**Historical status note:** the `READY_FOR_FULL_SMOKE` decision below predates
`resource-gate-v2` and is not the current decision. The cutoff/resource
contract is documented in
[pit-replay-resource-gate-v2.md](pit-replay-resource-gate-v2.md); the later
resource-failed full baseline and bounded finalization repair are in
[pit-replay-finalization-working-set.md](pit-replay-finalization-working-set.md).
The current decision is `READY_FOR_FULL_SMOKE_AGAIN`.

## Structural changes

- `FinancialSemanticContext` now owns candidate-local canonical income,
  balance-sheet, and cash-flow histories shared by fundamental and trend.
- Trend prepares one wide income quarter frame and immutable field projections
  shared by QoQ and TTM.
- Quarterization keeps field-specific source values, periods, versions, and
  availability chains; no evidence is dropped.
- The normal unique-version quarterization path avoids one-row groupby and
  DataFrame/`iloc` predecessor materialization. Revision/tie inputs retain the
  original fail-closed selector.
- `period_identity` recognizes annotated `Series.index` rather than
  re-annotating each Series.
- candidate cleanup now clears `trend_series`; retaining that DataFrame-id
  cache across candidates allowed id reuse and worker-dependent results.

## Profile

Real 2025-06-16, artifact-disabled cProfile:

| function | cap=50 calls | cumulative s | self s | mean calls/candidate |
|---|---:|---:|---:|---:|
| `validated_single_quarter_series` | 200 | 13.803 | 0.255 | 4.00 |
| `annotate_period_identity` | 150 | 16.705 | 0.187 | 3.00 |
| `match_comparable_period` | 16,448 | 20.124 | 0.443 | 328.96 |
| `ttm_from_series` | 8,019 | 15.037 | 0.445 | 160.38 |
| `_qoq_observations` | 150 | 16.886 | 0.080 | 3.00 |
| `compute_fundamental_features` | 50 | 20.047 | 0.021 | 1.00 |
| `compute_trend_features` | 50 | 73.682 | 0.014 | 1.00 |

Validated histories averaged 53.53 input/output rows per call. Period annotation
is now invoked only by the three canonical financial dataset preparations per
candidate. Trend matching calls are the expected per-period evidence
construction: YoY 8,031, QoQ 8,019, and TTM 8,019 for cap=50.

The artifact-disabled unprofiled cap=100 measured 233.581 s wall, 77.623 s
non-candidate work, and 155.958 s candidate work. Financial preparation,
fundamental, trend, and other feature phases were respectively 0.3161, 0.2024,
0.7397, and 0.2338 s/candidate, for about 1.492 s/candidate feature compute.
Peak RSS was 1,470,406,656 B.

## Equivalence

The detached old HEAD and new path were run over the same real bounded ten
candidates. Full legacy vectors, evidence/provenance/observations, scores,
formal and diagnostic rankings, and every `pit-replay-digests-v2` field were
recursively identical. Focused tests compare wide quarterization field by
field and assert that context histories and wide projections are not mutated.

## Bounded process feasibility

A forked candidate-local financial-kernel audit returned normalized candidate
payloads in parent-restored order. After clearing candidate-local trend caches,
workers=1 and workers=2 cap=20 had identical ordered payload, score/ranking,
and byte payload digests. Wall was 42.920 s versus 30.395 s: 1.412x speedup
(70.6% efficiency). IPC was 45,901,983 B. Worker peak RSS sums were 1.330 GiB
and 2.907 GiB; with the approximately 1.37 GiB parent both were bounded.

The cap=100 workers=2 memory probe reached 4,919,181,312 B aggregate worker RSS;
with the parent this exceeds the comfortable 6 GiB total boundary, and it
caused about 152 MiB of swap-in. Therefore workers=4 was not started and no
parallel production path was added. The measured bounded speedup would still
project an integrated full run above 7,200 seconds once parent serialization
and PIT work are included.

## Endpoint batching and bounded replay follow-up

The next round introduced immutable, candidate-local `PreparedFinancialRow`
and `PreparedComparableSeries` objects. YoY/QoQ prepare the semantic index and
row evidence once per selected series, while TTM prepares one rolling endpoint
table and uses scalar fallback for ambiguous, revision, mismatch, and missing
paths. The public scalar functions remain the reference implementations.
Prepared objects are compute-only and do not change the artifact schema.

A real financial cap=20 cProfile (deliberately biased toward the longest
histories) attributed 325.65 growth constructions and 596.55 trend observation
constructions per candidate. Batch dispatch reduced scalar
`match_comparable_period` to 18.5 and scalar `ttm_from_series` to 11.55 calls per
candidate in that sample; the remaining scalar calls are fail-closed fallback
paths. Prepared references recursively equal scalar references. Fresh result
references preserve the pre-existing artifact object-sharing layout.

Detached `8e6731a` versus the new serial path over the same real cap=10 was
byte-identical after gzip decompression (SHA-256
`d76589c22165442ef1f8256a5c09a7a07e0eae8ba4018ba9e332d52f3d60cee0`).
The current workers=1 and workers=2 cap=10 artifacts had the same digest as
well. This covers full normalized vectors/evidence, scores, formal and
diagnostic ranking, provenance store, and `pit-replay-digests-v2`.

The final serial cap=100 measured 301.078 s wall, 104.772 s fixed overhead,
1.9631 s candidate wall, and 1.1712 s feature CPU per candidate. Fundamental
and trend were 0.1996 and 0.6742 s/candidate; PIT and serialization were 0.1048
and 0.4258 s/candidate. This meets the updated 1.18 s useful single-thread
feature threshold.

Replay diagnostics now optionally use exactly two forked workers with two
in-flight candidates. Workers have isolated candidate caches; the parent
restores input order and performs artifact serialization/PIT validation. No
worker shares a mutable content-addressed store, and at most two completed
payloads can be pending.

The final production gzip-1 cap=250 workers=2 run measured 415.552 s wall,
117.182 s fixed overhead, 298.371 s candidate-loop time, and 1.1935 s candidate
wall. The projected full wall is 6,206.3 s for 5,102 candidates. The artifact
was 173,232,467 bytes, projecting to 3.293 GiB. PIT violations and failed
snapshots were zero.

`smaps_rollup` sampling covered the parent and both workers. Peak summed RSS,
PSS, and private memory were 5,786,004 KiB, 3,206,800 KiB, and 1,906,816 KiB
while all three processes were alive. Parent post-worker private high-water was
2,922,076 KiB. MemAvailable stayed at or above 9,690,872 KiB and ended only
52,672 KiB below its pre-run value. Process Swap remained zero. System counters
showed 6,309 pages (24.6 MiB) swap-in and zero swap-out, with no sustained swap
pressure. PSS/private, rather than summed fork RSS, therefore support the
bounded-memory decision.

All financial datasets are consumed by the combined fundamental/trend path, so
lazy `FinancialSemanticContext` canonicalization showed no real integrated
saving and was not introduced. RAW files were neither downloaded nor modified.

## Decision

**READY_FOR_FULL_SMOKE.** Semantic equivalence, PIT, determinism, artifact size,
memory, swap-pressure, and projected-wall gates pass. Candidate caps remain
strictly diagnostic-only. No full 2025-06 replay was run in this round.
