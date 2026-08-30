# Issue #32 / PR #41 — artifact normalization and bounded audit

This document records the post-`6f5946c` work for **artifact normalization /
provenance deduplication** and the bounded performance audit.  It is not a
full 2025-06 replay, yearly replay, monthly replay, feature recalibration, or
RAW operation.  The real measurements below use only the fixed 2025-06
`as_of=20250616` candidate probes capped at 10 and 100.

**Historical status note:** decisions recorded in this artifact-normalization
round use the pre-resource-gate-v2 contract.  The preserved later full run,
the lifetime-`ru_maxrss` root cause, and the current
`READY_FOR_FULL_SMOKE_AGAIN` static/bounded decision are recorded in
[pit-replay-resource-gate-v2.md](pit-replay-resource-gate-v2.md).

## A. Baseline

The baseline facts supplied for this round are:

| Item | Baseline |
| --- | ---: |
| Historical candidates | 5,102 |
| Pre-normalization cap=100 wall | about 367 s |
| Pre-normalization candidate loop | about 256 s |
| Pre-normalization peak RSS | about 1.95 GiB |
| 10-vector expanded serialization probe | 45–69 MiB/vector; mean about 48 MiB/vector |
| Expanded snapshot projection | about 228 GiB |
| Free disk at the decision point | about 20–21 GiB |

The baseline semantic and PIT equivalence checks were passing.  They remain
separate from the physical layout change.

## B. Artifact size attribution

The attribution uses canonical UTF-8 JSON bytes (sorted keys, compact
separators) and `sys.getsizeof` is not used.  Nested attribution categories
overlap by design: for example, `observations` is inside `provenance` and
`source_chain` can be inside an observation.  They must not be added as
independent totals.

A fixed real 10-candidate probe was run after the implementation.  Per-vector
canonical totals were:

| Metric | Aggregate for 10 | Mean/vector | Range/vector |
| --- | ---: | ---: | ---: |
| vector total | 480,548,381 B | 48,054,838 B | 45,529,794–69,040,589 B |
| values | 83,370 B | 8,337 B | — |
| evidence total | 479,897,539 B | 47,989,754 B | — |
| provenance | 473,765,897 B | 47,376,590 B | — |
| components | 125,472 B | 12,547 B | — |
| metadata | 389,976 B | 38,998 B | — |
| source_chain | 118,569,701 B | 11,856,970 B | — |
| observations | 437,026,377 B | 43,702,638 B | — |
| trend provenance | 473,550,064 B | 47,355,006 B | — |
| non-trend provenance | 215,833 B | 21,583 B | — |

Thus, on the mean vector, evidence is **99.8646%** of the payload,
provenance is **98.5886%**, trend provenance is **98.5437%**, and observations
alone account for **90.9433%**.  Values, components, and ordinary metadata are
not the cause of the 45–69 MiB result.

The largest individual vector was `000001.SZ` at 69,040,589 B; the smallest
was `000004.SZ` at 45,529,794 B.  The remaining eight vectors were between
45,614,672 B and 46,095,505 B.

### Recursive subtree accounting

For each vector, every mapping/list subtree is hashed and counted.  The
10-vector aggregate was:

```text
subtree bytes before deduplication: 4,082,518,387 B
unique subtree bytes:               1,975,082,799 B
identical subtree duplicated bytes: 2,107,435,588 B
identical subtree theoretical ratio: 51.62097%
```

The per-vector duplicate-group count ranged from 3,412 to 5,085.  This metric
counts overlapping ancestor and descendant subtrees; it is a root-cause
measure, not a prediction of the final file size.

## C. Confirmed duplication mechanism

`features.trend._add_summary()` obtains one complete `TrendSummary.as_dict()`
and embeds it into the provenance of each component.  `_add_alias()` and the
generic `yoy_*`, `qoq_*`, and `ttm_*` aliases then retain the same complete
summary chain again.  The component value is different, but the large
`observations` and nested `provenance` structures are identical.

The measured `TOP duplicated structures` for `000001.SZ` were:

| path | hash | occurrences | one_size | duplicated_size |
| --- | --- | ---: | ---: | ---: |
| `$.evidence.consecutive_improvement.provenance.observations` | `d9c07e4add1b8aa8097faec41b820f23c2af3b0fe2c13ecb741cb0576e3f746f` | 44 | 291,749 B | 12,545,207 B |
| `$.evidence.net_profit_qoq_acceleration.provenance.observations` | `98029be188c6e70401ec958a160ef494320c74999c53d9929d35e621fec13602` | 36 | 328,065 B | 11,482,275 B |
| `$.evidence.net_profit_ttm_acceleration.provenance.observations` | `46f7c0fb665ea5a08fe23c5558d66e0ff9d37f3279bf9288b7c3db38c1b0fdbc` | 34 | 189,805 B | 6,263,565 B |
| `$.evidence.consecutive_improvement.provenance.provenance` | `b6a0e475f794e70330ba06c44e0c278a705fa6cd295ce562729c753f3981273f` | 22 | 292,282 B | 6,137,922 B |
| `$.evidence.net_profit_qoq_acceleration.provenance.provenance` | `0f11153c20b2bc8c0b93136c91d9ad10fa916bca4a6a29c78ad01ef76908aaf4` | 18 | 328,598 B | 5,586,166 B |
| `$.evidence.operating_profit_qoq_acceleration.provenance.observations` | `30f354abc686565364d0068be420a3b7381a9c3de9cdfd0d9f5facd700a6457d` | 18 | 328,584 B | 5,585,928 B |

The repeated TrendSummary observation/provenance nodes, rather than feature
values or score rows, therefore explain the 45–69 MiB/vector measurement.

## D. Normalized artifact design

The new physical version is:

```text
pit-replay-artifact-normalized-v1
```

It is deliberately independent of these semantic contracts:

- `pit-replay-validation-v1`;
- logical `FeatureVector` / `FeatureEvidence` values;
- `turnaround-trend-v2`;
- `comparable-period-v1`;
- `evidence-confidence-v1`;
- `score-v2` and `ranking_eligible` / eligibility semantics.

The logical `FeatureVector.as_dict()` and `ReplayResult.artifact_dict()`
contracts are unchanged.  New JSON writers use the physical layout below
(conceptually simplified):

```json
{
  "artifact_layout_version": "pit-replay-artifact-normalized-v1",
  "vectors": [
    {
      "ts_code": "...",
      "values": {"...": "..."},
      "evidence": {
        "feature": {
          "...ordinary fields...": "...",
          "provenance_ref": "sha256:...",
          "components_ref": "sha256:...",
          "config_ref": "sha256:...",
          "metadata_ref": "sha256:...",
          "periods_ref": "sha256:...",
          "availability_dates_ref": "sha256:...",
          "source_versions_ref": "sha256:..."
        }
      }
    }
  ],
  "provenance_store": {"sha256:...": "..."}
}
```

The refs are full SHA-256 values over canonical JSON for the **logical**
immutable payload.  Store nodes form a deterministic snapshot-local
content-addressed graph, so repeated nested nodes are stored once.  Large
nodes use the deterministic JSON-safe `zlib+base64-json-v1` wrapper; this is a
physical encoding only and is expanded by the decoder.

Ordering is key-sorted for JSON and candidate/vector order is retained from
the deterministic replay result.  No Python object ID or runtime timestamp is
used for a ref.  `current_period`, `comparison_period`,
`availability_dates`, `source_versions`, `status`, `reason`, `source_chain`,
and all observations remain addressable.  Components, config, metadata, and
repeated evidence arrays also use refs where useful.

**The normalized representation is lossless.  Physical deduplication does not
remove evidence.**  It does not select Top-N evidence: every executed
candidate still has its complete logical evidence map and its score/diagnostic
row.

## E. Lossless equivalence

The explicit helpers are:

```text
expand_normalized_vector(...)
expand_normalized_replay_artifact(...)
expand_normalized_snapshot(...)
```

The gates compare the complete recursive JSON value, not a list of selected
fields.  They cover:

- synthetic vectors with repeated nested provenance;
- a controlled two-candidate replay;
- the bounded real 10-candidate artifact;
- PIT validation over expanded normalized evidence;
- missing-ref/integrity failures (fail closed).

Synthetic and controlled replay checks passed.  The real normalized probe
expanded and PIT-validated without violations.  The controlled fixture's
expanded physical snapshot is recursively identical to its legacy snapshot;
only the physical byte layout differs.

## F. Size before / after

The on-disk normalized files were written with deterministic streaming JSON.
The legacy values below are expanded JSON estimates from the same bounded
result; no full artifact was constructed.

| Probe | Legacy expanded estimate | Normalized actual | Actual normalized bytes/candidate | Shared store | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10 candidates | 1,022,549,409 B | 25,923,829 B | 2,592,383 B | 22,471,537 B | 39.44x |
| 100 candidates | about 10,225,494,090 B (10x probe estimate) | 220,476,606 B | 2,204,766 B | 186,647,417 B | bounded estimate only |

For the 100-candidate file, the normalized vector records themselves occupied
about 30,733,355 B; the remaining bytes are the shared store, scores,
diagnostic ranking, universe, metadata, and envelope.  The store contained
340,437 entries.  The 10-candidate file contained 36,201 entries.

A 100-candidate linear upper-bound projection (fixed normalized envelope,
measured vector/score/diagnostic record rate, and measured shared-store growth)
is:

```text
projected 5,102-candidate normalized snapshot: 11,551,497,534 B
                                             ≈ 10.76 GiB
```

The 10-candidate basis gives 11,969,130,142 B (11.15 GiB), so both bounded
projections remain above the 5 GiB smoke gate.  The projection is explicitly
reported rather than hidden behind a claim that the file is merely “smaller.”
It is an estimate, not a full snapshot write.

The result is a substantial reduction from the expanded estimate, but it is
not yet the desired hundred-MB-scale full snapshot.  The remaining growth is
candidate-specific complete trend observations in the shared store; removing
those would remove evidence and is prohibited.  A more compact columnar or
source-record encoding would be a separate correctness-reviewed design.

## G. Determinism

`pit-replay-digests-v1` records:

- input manifest digest;
- config digest;
- universe decision digest;
- per-candidate vector semantic digest;
- score digest;
- formal ranking digest;
- diagnostic ranking digest;
- warnings digest;
- provenance-store digest.

Candidate semantic digests exclude diagnostic timings, RSS, memory addresses,
process/runtime timestamps, and other runtime-only fields.  The normalized
writer sorts keys and store refs; repeated writes of the same fixture are byte
identical.  Legacy and normalized controlled snapshots have the same semantic
content digest after explicit expansion/canonicalization.

## H. Performance re-profile

The final bounded cap=100 run was single-threaded and processed 100 of 5,102
candidates.  It did not become a validation PASS; it remained a bounded
`INCOMPLETE` diagnostic.  PIT violations were zero.

| Phase | Total seconds | Seconds/candidate |
| --- | ---: | ---: |
| fundamental | 80.573 | 0.806 |
| trend | 144.369 | 1.444 |
| quality | 0.893 | 0.009 |
| attention | 6.151 | 0.062 |
| crowding | 14.251 | 0.143 |
| low_attention | 1.980 | 0.020 |
| scoring | 6.440 | 0.064 |
| PIT validation | 8.951 | 0.090 |
| artifact serialization | 676.621 | 6.766 |

Other cap=100 measurements:

```text
total wall:              1,054.401 s
candidate loop:            934.379 s
candidate seconds/candidate: 9.3438 s
peak RSS:                    2,010,001,408 B ≈ 1.872 GiB
```

The cap=10 run was consistent: 203.387 s wall, 10.0121 s/candidate, 73.626 s
artifact serialization, and 1.511 GiB peak RSS.  The byte-backed store and
streamed store writer are what keep the final cap=100 RSS near the original
replay RSS; an earlier pre-fix bounded writer attempt hit the resource guard
while materializing the entire store.

The two largest remaining hotspots are:

1. normalized artifact serialization (676.621 s/100 candidates); and
2. the trend feature path (144.369 s/100 candidates), followed by fundamental
   (80.573 s/100 candidates).

Feature-only candidate work is about 2.5466 s/candidate, so the <=1.0 s target
is not met without additional correctness-preserving vectorization/cache work.
No parallelization or semantic shortcut was introduced.

The 100-candidate full-replay ETA, including fixed snapshot overhead and the
measured normalized serialization, is:

```text
47,792.034 s ≈ 13.28 h
```

This is above the 7,200-second ceiling and is not a permission to run the
full snapshot.

## I. Projected full snapshot and decision inputs

- Projected normalized snapshot: **about 10.76 GiB** on the 100-candidate
  basis, above the explicit 5 GiB gate and not comfortably below the recorded
  20–21 GiB free-disk margin once temporary files and recovery headroom are
  considered.
- Projected full replay ETA: **about 13.28 h**, above 7,200 s.
- Normalized cap=100: **not PASS**; it is a diagnostic cap and the validation
  result remains `INCOMPLETE`.
- No full 2025-06 smoke was run.  No yearly/monthly sweep was run.  RAW was
  read-only and not downloaded or rewritten.

## J. Tests and equivalence gates

Added coverage includes:

- recursive provenance and subtree attribution;
- stable canonical SHA-256 refs;
- lossless vector and replay/snapshot round trips;
- byte-stable normalized serialization;
- controlled two-candidate equivalence;
- normalized PIT/evidence validation;
- integrity failure when a ref/evidence record is silently omitted;
- bounded diagnostic cap remains non-PASS.

The final repository gates are run before commit:

```text
pytest -q
ruff check .
python3 -m compileall -q src tests
git diff --check
```

## K. Git / PR

Changes belong on:

```text
research/32-pit-replay-validation-sample
```

The normalized layout, measured attribution, bounded cap results, and the
continued BLOCKED disposition are the diagnostic update for PR #41.  PR #41
is not merged.

## L. Decision

**BLOCKED.**

The lossless semantic/PIT/evidence gates pass, and normalized artifacts are
materially smaller, but the projected full artifact remains above 5 GiB and
the bounded ETA remains above two hours.  The <=1.0-second candidate target is
also not met.  Therefore this round must not recommend the next full
2025-06 smoke and must not emit `READY_FOR_FULL_SMOKE`.


---

# Issue #32 serialization/compute repair round (2026-08-29)

## A. Baseline

The comparison baseline remains the real `as_of=20250616`, cap=100 run at
HEAD `14906aa`: 5,102 historical candidates; 1,054.401 s wall; 934.379 s
candidate loop; 676.621 s artifact serialization; 2.5466 s/candidate feature
work; 220,476,606 B normalized artifact; 186,647,417 B store; 340,437 entries;
10.76 GiB full projection. No full, yearly, or monthly replay was run.

## B. Serialization micro-profile

Measurement preceded the repair. A real cap=20 cProfile of the old path took
687.346 profile-seconds (profiling amplification is material):

| operation | calls | cumulative seconds | bytes/calls note |
|---|---:|---:|---|
| candidate stream callback | 20 | 408.994 | 20 vectors |
| logical semantic digest | 26 | 266.023 | expanded logical trees |
| `_json_safe` | 64,807,453 | 265.454 | recursively revisited nested values |
| logical canonical hash | 26 | 172.248 | expanded logical bytes |
| `normalize_feature_vector` | 20 | 143.548 | 7.177 s/profiled vector |
| `ContentAddressedStore.intern` | 39,840 | 135.118 | public field interns |
| recursive CAS intern | 311,019 | 134.848 | old all-container strategy |
| `copy.deepcopy` | 6,117,864/359,295 | 9.795 | avoidable logical copy path |
| JSON dumps | 573,740 | 34.237 | includes CAS canonicalization |

The old cap=20 physical snapshot was 47,855,506 B with 70,344 entries. The
post-repair cProfile shows direct payload construction at 0.068 s/20 vectors,
no node zlib/base64 work, `normalize_feature_vector` 21.130 profile-seconds,
and the final streamed writer 3.448 profile-seconds. cProfile exaggerates the
normalizer; the unprofiled cap=100 serializer measurement is the gate value
below. Spool bytes are canonical UTF-8 JSON produced once and copied verbatim;
there is no final `json.loads -> _json_safe -> json.dumps` in the trusted path.

## C. CAS entry economics

The repaired cap=100 artifact has 232,316 entries (2,323/candidate rather than
3,404/candidate). Reachability/reference accounting:

- refs: 903,489; CAS hits beyond first reference: 671,173;
- reused at least twice: 44,117; referenced exactly once: 188,199;
- entry size p50/p90/p99/max: 799 / 2,825 / 3,150 / 44,406 B;
- bytes in single-use entries: 203,877,239 B;
- bytes in reused entries: 30,937,020 B;
- types: 81,547 lists; 75,891 observation/source-record mappings; 13,540
  TrendSummary mappings; 61,338 other mappings.

At roughly 75 B for a store key/wrapper plus 75 B for a ref marker, the
single-use ref/key overhead is about 28.2 MB before stream compression. Thus
recursive all-container CAS was confirmed as over-normalization. The repaired
artifact-aware strategy recurses only through measured high-value
`observations`, `source_chain`, nested `provenance`, `periods`, and
`source_versions`; tiny unique mapping wrappers stay inline. Single-use source
records still dominate raw store bytes, so further source-record dictionary
work would be justified only in another round.

## D. Serialization fix

- Physical normalization now builds the exact legacy shape directly from
  `FeatureVector`/`FeatureEvidence` fields; it does not call
  `FeatureVector.as_dict()` or deep-copy nested provenance.
- Candidate-scoped identity and JSON-safe caches preserve immutable alias reuse
  and are cleared after every candidate. Tests prove source vectors are not
  mutated.
- CAS hashes lossless Merkle nodes after child refs are formed, avoiding
  repeated hashing of fully expanded descendants.
- New nodes are plain canonical JSON. Legacy node-zlib wrappers remain
  decodable.
- Canonical spool records are validated at creation and copied verbatim by the
  final writer.

## E. Compression layout comparison

All repaired cap=100 modes below represent the same plain normalized CAS
payload (304,968,607 B):

| mode | physical bytes | compression wall | full projection |
|---|---:|---:|---:|
| A per-node zlib-9 + base64 | 178,558,339 | 10.956 s (+3.634 s dump) | 8.48 GiB |
| B plain CAS | 304,968,607 | 0 | 14.49 GiB |
| C whole-stream gzip-1 | 69,380,858 | 2.127 s | 3.30 GiB |
| D whole-stream gzip-6 | 55,224,055 | 4.796 s | 2.62 GiB |

Plain-stream decode was 0.876 s with about 17 MiB process peak in the streaming
copy probe. Compression-only process peak was about 80 MiB. gzip output uses
`mtime=0` and an empty filename; repeated unit writes are byte-identical.
Lossless expansion, byte stability, missing-ref fail-closed behavior, and PIT
checks pass. The production bounded writer selects gzip-1 because gzip-6 adds
about 0.027 s/candidate and the measured serializer is already near the hard
0.5 s/candidate investigation line. gzip-6 is retained as the preferred
recovery-headroom option once serializer CPU has more margin. No zstd
dependency was present or added.

## F. Source-record deduplication

Observation/source-record mappings are repeatedly referenced (75,891 entries),
and the artifact-aware CAS preserves complete observations/source chains while
sharing exact source records. A separate new source-record schema was not
introduced in this round: 188,199 entries are still single-use and a new
schema would need a measured two-pass economics design rather than an arbitrary
threshold. No source chain, observation, provenance, period, source version, or
derived value was deleted.

## G. Artifact size projection

The final real cap=100 gzip-1 artifact is 69,380,858 B, or 693,809 B/candidate.
A deliberately conservative linear 5,102-candidate projection is
3,539,811,375 B (3.30 GiB), below the 5 GiB hard gate but above the preferred
3 GiB headroom target. gzip-6 projects to 2.62 GiB. Peak replay RSS was
2,030,407,680 B (1.89 GiB), comfortably below 6 GiB.

## H. Determinism memory boundary

`pit-replay-digests-v2` is assembled from manifest, config, universe,
per-candidate normalized vector, score, formal ranking, diagnostic ranking,
warning, and store digests. Neither `deterministic_replay_digests` nor
`_result_payload_fingerprint` calls `ReplayResult.artifact_dict()`. A focused
test installs a raising `artifact_dict` method and passes. Expanded semantic
digests remain available only to bounded equivalence tests.

## I. Compute-only profile

The artifact profile is now separately identifiable; feature phase timers do
not include serializer CPU. Current cap=20 cProfile function evidence:

- trend: `compute_trend_features` 52.175 s; `_qoq_observations` 34.997 s;
  `_ttm_observations` 6.668 s; `calculate_trend` 5.723 s;
  `_yoy_observations` 4.043 s; `_choose_series` 1.910 s;
- fundamental: `compute_fundamental_features` 32.110 s; `_history` 12.596 s;
  `_growth_result` 11.848 s; `_margin_result` 3.273 s;
- shared: `validated_single_quarter_series` 36.877 s/80 calls;
  `annotate_period_identity` 9.396 s/344; `match_comparable_period` 7.853 s;
  `ttm_from_series` 5.784 s.

These are cProfile values, not wall projections. The unprofiled cap=100 feature
measurement is 2.4061 s/candidate.

## J. Trend/fundamental repair

Only demonstrated hotspots were touched: canonical histories and validated
quarter histories now remain candidate-local immutable cached frames instead
of returning copies that destroy semantic-cache identity; `_choose_series`
uses canonical identity columns with vectorized masks instead of row-wise
`apply(row.to_dict())`, and selected trend series are candidate-locally cached.
Lookbacks, formulas, period matching, unknown behavior, evidence, and ranking
gates are unchanged. The gain is modest; quarterization/comparable matching
remain the blocker and were not rewritten without stronger equivalence proof.

## K. Bounded final re-profile

Final real cap=100, single-threaded, diagnostic only:

- wall 401.130 s; fixed snapshot overhead 109.235 s;
- candidate loop 291.895 s; total 2.9189 s/candidate;
- feature 2.4061 s/candidate;
- serialization 0.4824 s/candidate;
- PIT 0.0798 s/candidate;
- fundamental 80.630 s; trend 130.099 s;
- RSS 2,030,407,680 B; no intentional swap use;
- artifact 693,809 B/candidate; 232,316-entry shared store;
- projected full wall 15,001.713 s (4.17 h).

A cap=250 run was not justified because the cap=100 runtime gate already fails.

## L. Semantic/PIT equivalence

Direct-object normalized expansion equals the existing legacy
`FeatureVector.as_dict()` recursively. Controlled replay/snapshot expansion,
normalized PIT validation, complete evidence integrity, byte stability, and
missing-ref failure all pass. No feature, PIT, score, evidence-confidence, or
ranking semantics changed.

## M. Tests

`pytest -q`: **249 passed, 1 skipped**. `ruff check .`,
`python3 -m compileall -q src tests`, and `git diff --check`: **PASS**.

## N. Git

Branch: `research/32-pit-replay-validation-sample`; PR: #41. No RAW files were
modified, no data was downloaded, no parallelism was added, and the PR is not
merged.

## O. Decision

**BLOCKED.** Artifact size and RSS hard gates now pass, and serializer time is
at the hard investigation boundary, but projected wall is 15,002 s versus the
7,200 s gate and feature work is 2.406 s/candidate versus the preferred
0.8 s/candidate. `READY_FOR_FULL_SMOKE` is therefore not reported, and no full
2025-06 replay is authorized.
