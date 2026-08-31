from __future__ import annotations

import copy
import gc
import gzip
import json
import weakref
from dataclasses import replace

import pytest

import ashare_turnaround.scanner.artifacts as artifacts_module
from ashare_turnaround.scanner.artifacts import (
    ARTIFACT_LAYOUT_VERSION,
    ChunkedContentAddressedStore,
    ContentAddressedStore,
    assert_lossless_expansion,
    attribute_feature_vector_size,
    canonical_json_bytes,
    content_ref,
    deterministic_replay_digests,
    expand_normalized_replay_artifact,
    expand_normalized_snapshot,
    expand_normalized_vector,
    measure_feature_vector_sizes,
    normalize_feature_vector,
    normalize_replay_artifact,
    normalize_snapshot_payload,
    semantic_digest,
    semantic_sequence_digest,
    serialized_json_bytes,
    validate_normalized_integrity,
    write_json_artifact,
    write_normalized_snapshot_with_streamed_vectors,
)
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.replay_validation import (
    ResourceBlocked,
    validate_normalized_vector_pit,
)
from ashare_turnaround.scanner.score import score_feature_vector

CODE = "600000.SH"
AS_OF = "20250616"


def _vector(code: str = CODE) -> FeatureVector:
    vector = FeatureVector(
        ts_code=code,
        as_of_date=AS_OF,
        benchmark_metadata={
            "benchmark_id": "000300.SH",
            "lookbacks": (20, 60),
        },
        metadata={
            "namespace": "test",
            "nested": {"as_of_date": AS_OF, "enabled_groups": ("trend", "quality")},
        },
    )
    shared = {
        "metric": "synthetic_trend",
        "trend_contract_version": "turnaround-trend-v2",
        "current_period": "20250331",
        "comparison_period": "20240331",
        "observations": [
            {
                "period": "20250331",
                "availability_dates": ["20250430"],
                "source_versions": ["income:v1"],
                "source_chain": [{"period": "20250331", "availability_date": "20250430"}],
            }
        ],
        "component_statuses": {"level": {"status": "known"}},
    }
    vector.add(
        "trend_a",
        1.0,
        availability_dates=(AS_OF,),
        current_period="20250331",
        comparison_period="20240331",
        provenance=shared,
        components={"source": "synthetic"},
        config={"lookbacks": (20, 60)},
        metadata={"observation_date": AS_OF},
        trend_contract_version="turnaround-trend-v2",
    )
    vector.add(
        "trend_b",
        2.0,
        availability_dates=(AS_OF,),
        current_period="20250331",
        comparison_period="20240331",
        provenance=shared,
        components={"source": "synthetic"},
        config={"lookbacks": (20, 60)},
        metadata={"observation_date": AS_OF},
        trend_contract_version="turnaround-trend-v2",
    )
    return vector


def test_recursive_attribution_exposes_repeated_provenance() -> None:
    vector = _vector()
    report = attribute_feature_vector_size(vector)

    assert report["total_bytes"] > report["values"]
    assert report["provenance"] > 0
    assert report["trend_provenance"] == report["provenance"]
    assert report["non_trend_provenance"] == 0
    assert report["identical_subtree_group_count"] > 0
    assert report["identical_subtree_total_duplicated_bytes"] > 0
    assert report["top_duplicated_structures"]
    assert {"path", "hash", "occurrences", "one_size", "duplicated_size"} <= set(
        report["top_duplicated_structures"][0]
    )


def test_provenance_refs_are_canonical_and_stable() -> None:
    assert content_ref({"b": 2, "a": 1}) == content_ref({"a": 1, "b": 2})
    first = normalize_feature_vector(_vector())
    second = normalize_feature_vector(_vector())

    assert first["evidence"]["trend_a"]["provenance_ref"].startswith("sha256:")
    assert first["evidence"]["trend_a"]["provenance_ref"] == first["evidence"]["trend_b"][
        "provenance_ref"
    ]
    assert first["evidence"]["trend_a"]["provenance_ref"] == second["evidence"]["trend_a"][
        "provenance_ref"
    ]
    assert serialized_json_bytes(first) == serialized_json_bytes(second)


def test_normalized_vector_roundtrip_is_lossless_and_keeps_all_evidence() -> None:
    vector = _vector()
    normalized = normalize_feature_vector(vector)
    expanded = expand_normalized_vector(normalized)

    assert expanded == vector.as_dict()
    assert_lossless_expansion(vector, normalized)
    assert set(expanded["values"]) == set(expanded["evidence"])
    assert len(normalized["provenance_store"]) >= 1


def test_semantic_digest_is_shared_by_legacy_and_normalized_vector() -> None:
    vector = _vector()
    normalized = normalize_feature_vector(vector)

    assert semantic_digest(vector) == semantic_digest(normalized)


def test_normalized_json_writer_is_byte_stable(tmp_path) -> None:
    normalized = normalize_feature_vector(_vector())
    first = write_json_artifact(tmp_path / "first.json", normalized)
    second = write_json_artifact(tmp_path / "second.json", normalized)

    assert first.read_bytes() == second.read_bytes()


def test_normalized_replay_snapshot_has_lossless_expansion_and_byte_stability() -> None:
    legacy = {
        "metadata": {"as_of_date": AS_OF},
        "ranked": [],
        "diagnostic_ranked": [],
        "vectors": [_vector("A.SH").as_dict(), _vector("B.SH").as_dict()],
        "scores": [],
        "universe": {"decisions": []},
    }
    normalized = normalize_replay_artifact(legacy)
    repeated = normalize_replay_artifact(copy.deepcopy(legacy))

    assert normalized["artifact_layout_version"] == ARTIFACT_LAYOUT_VERSION
    assert serialized_json_bytes(normalized) == serialized_json_bytes(repeated)
    assert canonical_json_bytes(expand_normalized_replay_artifact(normalized)) == (
        canonical_json_bytes(legacy)
    )


def test_size_report_has_shared_store_and_projection() -> None:
    vectors = [_vector("A.SH"), _vector("B.SH")]
    for vector in vectors:
        for evidence in vector.evidence.values():
            evidence.provenance["large_repeated_audit_payload"] = "x" * 10_000
    report = measure_feature_vector_sizes(vectors, projected_candidate_count=10)

    assert report["shared_provenance_store_bytes"] > 0
    assert report["normalized_actual_bytes"] < report["legacy_expanded_bytes"]
    assert report["compression_ratio"] > 1.0
    assert report["projected_full_snapshot_bytes"] > report["normalized_actual_bytes"]


def test_normalized_pit_validation_expands_every_evidence_record() -> None:
    normalized = normalize_feature_vector(_vector())
    assert validate_normalized_vector_pit(normalized, as_of_date=AS_OF) == ()

    future = _vector()
    future.evidence["trend_a"].provenance["observation_date"] = "20250701"
    future_normalized = normalize_feature_vector(future)
    violations = validate_normalized_vector_pit(future_normalized, as_of_date=AS_OF)
    assert any("observation_after_as_of" in value for value in violations)


def test_normalized_integrity_rejects_silent_evidence_omission() -> None:
    normalized = normalize_feature_vector(_vector())
    normalized["evidence"]["trend_a"].pop("provenance_ref")

    violations = validate_normalized_integrity(
        {"vectors": [normalized], "provenance_store": normalized["provenance_store"]}
    )
    assert any("trend_a:missing_provenance_ref" in value for value in violations)


def test_invalid_reference_fails_closed() -> None:
    normalized = normalize_feature_vector(_vector())
    normalized["evidence"]["trend_a"]["provenance_ref"] = "sha256:missing"

    with pytest.raises(KeyError):
        expand_normalized_vector(normalized)


def test_direct_object_normalization_does_not_mutate_or_call_vector_as_dict(
    monkeypatch,
) -> None:
    vector = _vector()
    reference = vector.as_dict()
    monkeypatch.setattr(
        FeatureVector,
        "as_dict",
        lambda _self: (_ for _ in ()).throw(AssertionError("legacy materialization")),
    )

    normalized = normalize_feature_vector(vector)

    assert canonical_json_bytes(expand_normalized_vector(normalized)) == canonical_json_bytes(
        reference
    )
    assert vector.evidence["trend_a"].provenance["metric"] == "synthetic_trend"


def test_chunked_store_merges_deduplicates_and_matches_mapping_digest(tmp_path) -> None:
    memory = ContentAddressedStore()
    chunked = ChunkedContentAddressedStore(tmp_path / "chunks", chunk_entries=1)
    for value in ({"kind": "same"}, {"kind": "other"}, {"kind": "same"}):
        memory.intern(value)
        chunked.intern(value)
        chunked.flush_chunk_if_needed()

    assert chunked.chunk_count == 3
    assert list(chunked.iter_entries_sorted()) == list(memory.iter_entries_sorted())
    assert chunked.digest() == memory.digest()
    assert chunked.entry_count == len(memory)
    chunked.close()
    assert not (tmp_path / "chunks").exists()


def test_canonical_spool_is_copied_verbatim_and_gzip_is_deterministic(tmp_path) -> None:
    vector = _vector()
    store = ContentAddressedStore()
    normalized = normalize_feature_vector(vector, store=store)
    record = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    spool = tmp_path / "vectors.jsonl"
    spool.write_text(record + "\n", encoding="utf-8")
    snapshot = {"replay": {"vectors": []}}

    paths = []
    for name in ("first.json.gz", "second.json.gz"):
        paths.append(
            write_normalized_snapshot_with_streamed_vectors(
                tmp_path / name,
                snapshot,
                spool,
                store.entries_view(),
                canonical_spool=True,
                gzip_level=6,
            )
        )

    assert paths[0].read_bytes() == paths[1].read_bytes()
    decoded = gzip.decompress(paths[0].read_bytes()).decode("utf-8")
    assert record in decoded


def test_determinism_digest_does_not_call_expanded_artifact(monkeypatch) -> None:
    class Result:
        vectors = (_vector(),)
        scores = ()
        ranked = []
        diagnostic_ranked = []
        configuration = {}
        universe_decisions = ()
        warnings = ()

        def artifact_dict(self):
            raise AssertionError("expanded artifact boundary crossed")

    digests = deterministic_replay_digests(Result())

    assert digests["per_candidate_normalized_vector_digest"]


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"one": 1}],
        [{"index": index, "values": [index, index + 1]} for index in range(25)],
        [
            {
                "started_at": "removed",
                "kept": {"runtime_timestamp": "removed", "as_of_date": AS_OF},
            },
            {"diagnostics": {"elapsed_seconds": 99}, "value": 2},
        ],
        [
            {
                "input_metadata": {
                    "nested": {
                        "periods": ("20250331", "20241231"),
                        "source": {"dataset": "income", "rows": [1, 2, 3]},
                    }
                }
            }
        ],
    ],
    ids=["empty", "one", "many", "runtime-keys", "nested-metadata"],
)
def test_streaming_semantic_sequence_digest_matches_materialized(items) -> None:
    assert semantic_sequence_digest(iter(items)) == semantic_digest(items)


def test_streaming_semantic_sequence_digest_matches_score_result_fixtures() -> None:
    first = score_feature_vector(_vector("A.SH"))
    second = replace(first, ts_code="B.SH", input_metadata={"nested": {"value": [1, 2]}})
    scores = (first, second)

    assert semantic_sequence_digest(iter(scores)) == semantic_digest(
        [score.as_dict() for score in scores]
    )


def test_streamed_scores_and_vectors_are_exactly_equivalent_after_parsing(tmp_path) -> None:
    vector = _vector()
    score = score_feature_vector(vector)
    legacy_snapshot = {
        "status": "READY",
        "warnings": ["controlled"],
        "replay": {
            "metadata": {"as_of_date": AS_OF},
            "ranked": [{"ts_code": CODE, "rank": 1}],
            "diagnostic_ranked": [{"ts_code": CODE, "rank": 1}],
            "vectors": [vector.as_dict()],
            "scores": [score.as_dict()],
            "universe": {"decisions": [{"ts_code": CODE, "included": True}]},
        },
    }
    expected = json.loads(serialized_json_bytes(normalize_snapshot_payload(legacy_snapshot)))
    store = ContentAddressedStore()
    normalized_vector = normalize_feature_vector(vector, store=store)
    record = json.dumps(
        normalized_vector,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    spool = tmp_path / "vectors.jsonl"
    spool.write_text(record + "\n", encoding="utf-8")

    old_path = write_normalized_snapshot_with_streamed_vectors(
        tmp_path / "old.json",
        legacy_snapshot,
        spool,
        store.iter_entries_sorted(),
        canonical_spool=True,
    )
    lightweight = copy.deepcopy(legacy_snapshot)
    lightweight["replay"]["vectors"] = []
    lightweight["replay"]["scores"] = []
    new_path = write_normalized_snapshot_with_streamed_vectors(
        tmp_path / "new.json",
        lightweight,
        spool,
        store.iter_entries_sorted(),
        scores=iter((score,)),
        canonical_spool=True,
    )
    old_payload = json.loads(old_path.read_text(encoding="utf-8"))
    new_payload = json.loads(new_path.read_text(encoding="utf-8"))

    assert old_payload == new_payload == expected
    assert canonical_json_bytes(expand_normalized_snapshot(new_payload)) == (
        canonical_json_bytes(legacy_snapshot)
    )
    old_expanded = expand_normalized_snapshot(old_payload)
    new_expanded = expand_normalized_snapshot(new_payload)
    for field in ("vectors", "scores", "universe", "ranked", "diagnostic_ranked"):
        assert semantic_digest(old_expanded["replay"][field]) == semantic_digest(
            new_expanded["replay"][field]
        )
    assert semantic_digest(old_expanded["warnings"]) == semantic_digest(
        new_expanded["warnings"]
    )
    assert ContentAddressedStore(
        old_payload["replay"]["provenance_store"]
    ).digest() == ContentAddressedStore(
        new_payload["replay"]["provenance_store"]
    ).digest()


def test_large_score_stream_is_one_pass_and_not_eagerly_materialized(
    tmp_path, monkeypatch
) -> None:
    live_scores: weakref.WeakSet[object] = weakref.WeakSet()

    class ScoreLike:
        def __init__(self, index: int, owner: OnePassScores) -> None:
            self.index = index
            self.owner = owner
            live_scores.add(self)

        def as_dict(self):
            self.owner.converted += 1
            return {
                "ts_code": f"{self.index:06d}.SH",
                "input_metadata": {
                    "nested": {
                        "payload": "x" * 2048,
                        "observations": [
                            {"period": "20250331", "value": self.index},
                            {"period": "20241231", "value": self.index - 1},
                        ],
                    }
                },
            }

    class OnePassScores:
        def __init__(self, count: int) -> None:
            self.count = count
            self.iterations = 0
            self.converted = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations != 1:
                raise AssertionError("score stream was consumed more than once")
            for index in range(self.count):
                # Direct iteration retains at most the previous loop item while
                # requesting the next. list(scores) retains two before item 3.
                if len(live_scores) >= 2:
                    raise AssertionError("score stream was eagerly materialized")
                yield ScoreLike(index, self)

    def reject_deepcopy(_value, _memo=None):
        raise AssertionError("production streamed writer used copy.deepcopy")

    monkeypatch.setattr(artifacts_module.copy, "deepcopy", reject_deepcopy)
    spool = tmp_path / "empty-vectors.jsonl"
    spool.write_text("", encoding="utf-8")
    scores = OnePassScores(2_000)
    destination = write_normalized_snapshot_with_streamed_vectors(
        tmp_path / "large-scores.json.gz",
        {"replay": {"vectors": [], "scores": []}},
        spool,
        (),
        scores=scores,
        canonical_spool=True,
        gzip_level=1,
    )
    payload = json.loads(gzip.decompress(destination.read_bytes()))
    gc.collect()

    assert scores.iterations == 1
    assert scores.converted == scores.count
    assert len(payload["replay"]["scores"]) == scores.count
    assert not live_scores


@pytest.mark.parametrize(
    "blocked_stage",
    [
        "artifact_vector_stream_start",
        "artifact_score_stream_start",
        "artifact_writer_complete",
    ],
)
def test_stream_writer_resource_failure_removes_partial_artifact(
    tmp_path, blocked_stage
) -> None:
    spool = tmp_path / "vectors.jsonl"
    spool.write_text('{"ts_code":"A.SH"}\n', encoding="utf-8")
    destination = tmp_path / f"blocked-{blocked_stage}.json"

    def probe(stage: str) -> None:
        if stage == blocked_stage:
            raise ResourceBlocked(stage)

    with pytest.raises(ResourceBlocked, match=blocked_stage):
        write_normalized_snapshot_with_streamed_vectors(
            destination,
            {"replay": {"vectors": [], "scores": []}},
            spool,
            (),
            scores=iter(({"ts_code": "A.SH"},)),
            canonical_spool=True,
            resource_probe=probe,
            probe_interval_bytes=1,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_finalized_store_iteration_failure_removes_partial_artifact(tmp_path) -> None:
    store = ChunkedContentAddressedStore(tmp_path / "cas", chunk_entries=1)
    store.intern({"value": 1})
    store.flush_chunk_if_needed()
    store.finalize()
    spool = tmp_path / "vectors.jsonl"
    spool.write_text("", encoding="utf-8")
    destination = tmp_path / "blocked-store.json"

    def store_probe(stage: str) -> None:
        if stage == "cas_finalized_store_iteration_start":
            raise ResourceBlocked(stage)

    with pytest.raises(ResourceBlocked, match="finalized_store_iteration"):
        write_normalized_snapshot_with_streamed_vectors(
            destination,
            {"replay": {"vectors": []}},
            spool,
            store.iter_entries_sorted(resource_probe=store_probe),
            canonical_spool=True,
            resource_probe=lambda _stage: None,
        )

    assert not destination.exists()
    assert store.finalized_path is not None
    store.close()
