# PIT replay financial semantic compute audit

Issue #32 / PR #41, branch `research/32-pit-replay-validation-sample`.
Artifact layout, gzip/CAS schemas, formulas, PIT/comparable semantics, evidence,
score, and ranking contracts were frozen for this audit.

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

## Decision

**BLOCKED.** Semantic equivalence, PIT/determinism, artifact-size, and
single-process RSS gates remain passing, but feature compute is about 1.49
s/candidate versus the 0.8 s target. A conservative integrated projection is
about 10.3--10.6 ks single-threaded; bounded two-worker feasibility remains
above the runtime gate and is not comfortably within the memory gate. No
cap=250 or full 2025-06 run was performed.
