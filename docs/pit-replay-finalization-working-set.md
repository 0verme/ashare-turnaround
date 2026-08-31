# PIT replay finalization working-set repair

Issue #32 / PR #41, branch
`research/32-pit-replay-validation-sample`. This is an execution-only repair:
feature formulas, PIT/score/ranking semantics, universe, Top-N, workers, logical
CAS refs, artifact schema, and RAW are unchanged.

This file preserves the execution-only v2 finalization evidence. The current
resource decision contract is `resource-gate-v3`, which supersedes the v2 swap
hard gates; see [pit-replay-resource-gate-v3.md](pit-replay-resource-gate-v3.md).

## A. Preserved baseline

The latest real full run remains labelled **resource-gate-v2 baseline #1
resource-failed run** at
`f58e86688a42ddc2e5a5a2cc68b4124dc666bc4c`:

- 5,102 / 5,102 candidates; snapshot `READY`;
- 6,228.981 s runtime;
- 2,801,660,875 B (2.609 GiB) compressed artifact;
- zero PIT violations and semantic regression `PASS`;
- bounded candidate-loop memory.

The independent 30-second monitor is authoritative for its hard failure:
process swap peaked at 511,152,128 B (limit 268,435,456 B), `SwapFree`
fell to 377,372,672 B (floor 536,870,912 B), and system swap growth reached
421,625,856 B (limit 268,435,456 B). It is not retroactively promoted to
`FULL_SMOKE_PASS` or used as the first member of a final determinism pair.

## B. Finalization root cause

The pre-repair paths were confirmed directly:

1. `deterministic_replay_digests()` built the complete
   `[score.as_dict() for score in scores]` before the provenance-store digest;
2. `ScoreResult.as_dict()` begins with `dataclasses.asdict(self)`, recursively
   copying nested metadata;
3. `_run_manifest()` calls `deterministic_replay_digests()`, which called the
   chunked store's `digest()`;
4. that `digest()` called `iter_entries_sorted()` and built a multipass CAS
   merge;
5. `record_snapshot()` later supplied a second `iter_entries_sorted()` to the
   streamed writer, rebuilding the same 164-chunk merge tree;
6. `ReplayValidationSnapshot.as_dict()` called
   `ReplayResult.artifact_dict()`, materializing every score dictionary;
7. the writer then deep-copied the snapshot, JSON-safed the whole tree, and
   encoded the whole envelope before replacing vector/store markers;
8. projected/raw `replay_frames` remained referenced until after artifact
   promotion and cleanup.

A prior real cap=100 score array was measured after JSON parsing at
25,515,688 B of recursive Python object graph, or 255,156.88 B/score. The
5,102-score-equivalent linear size is 1,301,810,402 B (1.212 GiB), confirming
that the former all-score temporary was a large graph even before considering
co-resident frames, CAS state, the source `ScoreResult` objects, and writer
copies.

## C. Streaming score digest

`semantic_sequence_digest()` now hashes `[` + each canonical semantic item in
order + `]`, retaining only one expanded item. It is byte-for-byte equivalent
to `semantic_digest(materialized_sequence)` and is used for scores and
universe decisions. Runtime-key cleaning and item order are unchanged;
`pit-replay-digests-v2` therefore remains the digest contract version.

Tests cover empty, one, many, runtime-key, nested-metadata, and real
`ScoreResult` fixtures against the old materialized expression.

## D. Early frame release

After worker shutdown, PIT validation, initial snapshot status, and any
actually requested same-process repeat, orchestration now samples resources,
sets the projected `replay_frames` reference to `None`, runs `gc.collect()`,
and samples again before CAS finalization. `determinism_sample > 0` remains
correct because its repeat is completed before this boundary.

In the required real cap=100 run:

| sample | PSS (B) | private (B) | process swap (B) |
|---|---:|---:|---:|
| before frame release | 1,289,475,072 | 1,288,720,384 | 0 |
| after frame release | 1,157,841,920 | 1,157,087,232 | 0 |

Both PSS and private memory fell by 131,633,152 B before the first CAS merge.

## E. Streamed score artifact

The production snapshot path builds a lightweight replay envelope with empty
vector/score compatibility fields. It never calls
`ReplayResult.artifact_dict()`. The normalized writer replaces a score marker
and invokes `ScoreResult.as_dict()` one item at a time, while vectors continue
to come from the canonical JSONL spool and the CAS comes from its finalized
file. `ReplayResult.scores` itself remains unchanged for ranking, PIT,
semantic digesting, metrics, and manual review until those consumers finish.

A 2,000-record nested-metadata test uses a one-pass iterator that fails if
items are eagerly retained; it also rejects `copy.deepcopy()` and proves every
score is converted exactly once.

## F. Writer working set

The writer now shallow-copies only the two small envelope dictionaries. It no
longer constructs a full snapshot score list, deep copy, whole-tree JSON-safe
copy, and whole large encoded envelope simultaneously. Caller objects are not
mutated. Vectors, scores, and store entries are serialized independently.

Cap=100 internal samples were:

| sample | PSS (B) | private (B) | process swap (B) |
|---|---:|---:|---:|
| before writer | 1,157,844,992 | 1,157,087,232 | 0 |
| writer sampled peak | 1,157,849,088 | 1,157,091,328 | 0 |
| after writer/CAS cleanup | 1,157,847,040 | 1,157,066,752 | 0 |

The independent five-second monitor's overall PSS peak was 1,725,517,824 B
during candidate computation, not finalization; process swap remained zero.

## G. Single CAS finalization

`ChunkedContentAddressedStore.finalize()` now force-flushes active entries,
performs one bounded external consolidation, checks duplicate refs, computes
the mapping digest during the final merge, and caches the digest, unique entry
count, physical value bytes, and final sorted runtime path. Original and
intermediate chunks are removed after a durable successor exists. A failed
finalization cannot be retried as a partial store. Later `digest()` and
`iter_entries_sorted()` reuse the final file directly; `close()` removes it.

The configured fan-in remains 32. The architectural external-merge count is
old = 2 (manifest digest plus writer), new = 1 (finalize then reuse). Cap=100
had four chunks, one merge pass/group, 232,920 unique entries, 235,593,825
physical value bytes, a 331,429,892 B finalized runtime file, and peak four
open streams. The >=225-chunk tests exercise true multipass reuse and keep peak
open streams within configured fan-in.

## H. Resource sampling (historical v2 implementation)

At this repair boundary the thresholds were still v2 hard thresholds:

- live PSS/private/RSS fallback <= 6 GiB;
- `SwapFree` >= 512 MiB;
- system swap growth <= 256 MiB;
- process swap <= 256 MiB.

The declared resource surface now includes
`sampling_contract_version=resource-finalization-sampling-v1` and a 256 MiB
interval. The orchestration callback enforced the v2 limits at frame release,
merge-group boundaries, finalized-store completion, final store iteration, and
vector/score/store artifact streams. Resource-gate-v3 keeps those sampling
points but classifies swap occupancy/history as warnings and uses a vmstat
pressure window for hard swap-thrashing detection. The artifact
module has no dependency on `ResourceBlocked`; any callback exception is
cleaned up and propagated.

Injected callback tests prove healthy completion and fail-closed cleanup during
intermediate/final CAS merge, finalized-store iteration, vector writing, score
writing, and the final pre-promotion writer probe.

## I. Artifact and digest equivalence

Controlled old/new writers are recursively equal after JSON parsing and
normalized expansion. Candidate-vector, score, universe, formal-ranking,
diagnostic-ranking, warning, and provenance-store digests are identical. The
layout remains `pit-replay-artifact-normalized-v1`.

The real cap=100 comparison against the previous equivalent artifact produced
exactly equal 100-entry candidate-vector digest maps and these equal digests:

| component | SHA-256 |
|---|---|
| config | `5306e60d765e87b50902aa577be5d6ead32fb35498e77bfe8e49cc8db027574f` |
| scores | `a6f2e853deab10e7a2f9d46b422019fa97fc99d2028b78f1634482a102672f5a` |
| universe | `51b18d8d2ec9efaa1bcccc7013216d29e749f601c3df0e7588a2d57c20f7e55d` |
| formal ranking | `d304a2271b91da09a5c7918cebfc58b41fc3b3bf1f6ca6bb278a71360f2679ae` |
| diagnostic ranking | `010fa869bbd82b8a6850a9c0654d8b21bc73910f5d0afc4d4cdf1ba952ac9bd5` |
| warnings | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| provenance store | `ab6fbfef9498b8acad6fe745ca38c705db0182245dd1cc1aa9ccd69677ed5d2a` |

Compact per-score stream whitespace reduces cap=100 decompressed bytes from
305,279,456 to 298,784,784 and compressed bytes from 69,526,063 to 69,122,897.
This is not a logical/physical schema change: parsed JSON, semantic digests,
physical store digest, refs, and values remain equivalent.

## J. Synthetic scale tests

The suite includes:

- 2,000 nested score-like records with one-pass/eager-retention guards;
- >=225 one-entry CAS chunks followed by repeated finalize/digest/iteration/
  writer reads;
- chunk-size/fan-in variants with identical memory/chunked/finalized ordering
  and digest;
- equal-byte duplicate deduplication and conflicting-byte failure;
- cleanup of all partial/intermediate/finalized runtime files.

## K. Real cap=100 regression

Only the authorized diagnostic was run: `2025-06`, `as_of=20250616`,
`today=20260830`, `top_n=3`, `workers=2`, `max_in_flight=2`,
`candidate_limit=100`, and `determinism_sample=0`.

- 100 / 5,102 candidates; diagnostic status `INCOMPLETE` as designed;
- 230.021 s wall; 1.189949 s/candidate loop; no target-status regression;
- PIT violations 0; failed snapshots 0;
- vectors, scores, universe, rankings, warnings, and provenance digest exact
  against the previous cap=100 semantic output;
- artifact 69,122,897 B;
- process swap 0; minimum externally sampled `SwapFree` 1,087,086,592 B;
- minimum externally sampled `MemAvailable` 11,172,519,936 B.

This is diagnostic evidence only, not `FULL_SMOKE_PASS`.

## L. v3 full validation pair

After the resource-gate-v3 calibration, the authorized sequence completed with
exactly one full baseline followed by one identical determinism baseline. Both
used `2025-06`, selected session `20250616`, `today=20260830`, `top_n=3`,
`seed=0`, `workers=2`, `max_in_flight=2`, `candidate_limit=None`,
`determinism_sample=0`, and `content_hash=False`.

- Baseline #1: 5,102/5,102 candidates, `READY`, 0 failed snapshots, 0 PIT
  violations, 0 warnings, 6,059.104 s wall, and resource status `PASS`.
- Determinism #2: 5,102/5,102 candidates, `READY`, 0 failed snapshots, 0 PIT
  violations, 0 warnings, 6,089.986 s wall, and resource status `PASS`.
- Both artifacts are 2,781,058,369 B gzip-1 streams, pass `gzip -t`, and share
  SHA-256 `142082b0649180e09e0dea946feb868f6e831d314c39324c2e69a37a154adce8`.
- All 5,102 candidate-vector digests and score, universe, formal-ranking,
  diagnostic-ranking, warning, and provenance-store digests match exactly
  between the pair and the established logical reference. The formal Top-3 is
  `688233.SH`, `002355.SZ`, `688615.SH`.
- v3 observed no active sustained bidirectional swap pressure. Baseline/repeat
  minimum MemAvailable was 10,414,280,704 B / 9,890,746,368 B, peak live PSS
  was 2,133,456,896 B / 2,189,577,216 B, and process swap was 0 B in both.
- RAW postflight passed unchanged: 911 files, 1,821,251,649 B, metadata digest
  `df03e77557b1bddd14de9d50177794c4b945af213efbe9dc6223d0670ddc825e`.

The preserved v2 resource-failed run is historical evidence only and is not a
member of this final pair.

## M. Tests

Final static gates:

- `pytest -q`: 294 passed, 1 skipped;
- `ruff check .`: PASS;
- `python3 -m compileall -q src tests`: PASS;
- `git diff --check`: PASS.

## N. RAW integrity

No download, rewrite, or compaction occurred. Before/after metadata files are
byte-identical: 911 files, 1,821,251,649 B, digest
`df03e77557b1bddd14de9d50177794c4b945af213efbe9dc6223d0670ddc825e`.

## O. Git / PR

The calibration is committed as
`30068f9` (`fix(research): calibrate replay resource pressure gate`) on
`research/32-pit-replay-validation-sample` and pushed to `origin`. PR #41
remains unmerged; no merge was performed.

## P. Decision

**FULL_SMOKE_PASS.** The v3 baseline #1 and identical determinism #2 both
returned machine status `READY`, passed the PIT/Feature/Score/Ranking/Top-N and
artifact gates, and had no resource warnings or active Swap Thrashing. The
preserved `f58e866` v2 resource-failed run remains excluded from the pair.
