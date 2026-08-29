from __future__ import annotations

import copy

import pytest

from ashare_turnaround.scanner.artifacts import (
    ARTIFACT_LAYOUT_VERSION,
    assert_lossless_expansion,
    attribute_feature_vector_size,
    canonical_json_bytes,
    content_ref,
    expand_normalized_replay_artifact,
    expand_normalized_vector,
    measure_feature_vector_sizes,
    normalize_feature_vector,
    normalize_replay_artifact,
    semantic_digest,
    serialized_json_bytes,
    validate_normalized_integrity,
    write_json_artifact,
)
from ashare_turnaround.scanner.contracts import FeatureVector
from ashare_turnaround.scanner.replay_validation import (
    validate_normalized_vector_pit,
)

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
