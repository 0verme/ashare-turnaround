from __future__ import annotations

import base64

import pytest

from ashare_turnaround.scanner.artifacts import (
    ChunkedContentAddressedStore,
    ContentAddressedStore,
    write_normalized_snapshot_with_streamed_vectors,
)
from ashare_turnaround.scanner.replay_validation import ResourceBlocked


def test_candidate_batches_do_not_use_cas_entry_units(tmp_path) -> None:
    store = ChunkedContentAddressedStore(tmp_path / "cas", chunk_entries=10_000)
    for candidate in range(250):
        store.intern({"candidate": candidate, "nested": {"value": candidate}})
        if (candidate + 1) % 100 == 0:
            store.flush_chunk(force=True)
    store.flush_chunk(force=True)
    assert store.chunk_count == 3
    store.close()


def test_active_byte_safety_flushes_incrementally(tmp_path) -> None:
    store = ChunkedContentAddressedStore(
        tmp_path / "cas", chunk_entries=10_000, max_active_physical_bytes=1
    )
    store.intern({"large": "x"})
    store.flush_chunk_if_needed()
    assert store.active_entry_count == 0
    assert store.active_physical_byte_count == 0
    assert store.chunk_count == 1
    store.close()


def test_200_chunks_use_bounded_multipass_merge_and_match_memory(tmp_path) -> None:
    chunked = ChunkedContentAddressedStore(
        tmp_path / "cas", chunk_entries=1, merge_fan_in=8
    )
    memory = ContentAddressedStore()
    for index in range(200):
        value = {"value": index % 17, "index": index}
        chunked.intern(value)
        memory.intern(value)
        chunked.flush_chunk_if_needed()

    assert list(chunked.iter_entries_sorted()) == list(memory.iter_entries_sorted())
    assert chunked.digest() == memory.digest()
    assert chunked.peak_open_chunk_streams <= 8
    chunked.close()
    assert not list((tmp_path / "cas").glob("merge-*.cas"))


def test_finalize_is_reused_by_digest_iteration_and_writer(tmp_path) -> None:
    chunked = ChunkedContentAddressedStore(
        tmp_path / "cas-reuse", chunk_entries=1, merge_fan_in=8
    )
    memory = ContentAddressedStore()
    for index in range(225):
        value = {"index": index, "duplicate_group": index % 13}
        memory.intern(value)
        chunked.intern(value)
        chunked.flush_chunk_if_needed()
    # Duplicate refs in separate chunks must still deduplicate to equal bytes.
    duplicate = {"index": 7, "duplicate_group": 7}
    chunked.intern(duplicate)
    chunked.flush_chunk_if_needed()

    events: list[str] = []
    finalized = chunked.finalize(
        resource_probe=events.append,
        probe_interval_bytes=1,
    )
    digest = chunked.digest()
    entries = list(chunked.iter_entries_sorted())
    merge_groups = chunked.merge_group_count
    merge_passes = chunked.merge_pass_count
    spool = tmp_path / "empty-vectors.jsonl"
    spool.write_text("", encoding="utf-8")
    written = write_normalized_snapshot_with_streamed_vectors(
        tmp_path / "snapshot.json",
        {"replay": {"vectors": []}},
        spool,
        chunked.iter_entries_sorted(),
        canonical_spool=True,
    )

    assert written.exists()
    assert finalized == chunked.finalized_path
    assert finalized.exists()
    assert chunked.finalization_count == 1
    assert chunked.merge_group_count == merge_groups
    assert chunked.merge_pass_count == merge_passes
    assert digest == chunked.digest() == memory.digest()
    assert entries == list(memory.iter_entries_sorted())
    assert entries == list(chunked.iter_entries_sorted())
    assert chunked.peak_open_chunk_streams <= 8
    assert "cas_finalized_store_completion" in events
    assert not list((tmp_path / "cas-reuse").glob("merge-*.cas"))

    chunked.close()
    assert not (tmp_path / "cas-reuse").exists()


@pytest.mark.parametrize("chunk_entries,fan_in", [(1, 2), (3, 7), (11, 32)])
def test_finalized_digest_is_independent_of_chunk_and_fan_in(
    tmp_path, chunk_entries, fan_in
) -> None:
    memory = ContentAddressedStore()
    chunked = ChunkedContentAddressedStore(
        tmp_path / f"cas-{chunk_entries}-{fan_in}",
        chunk_entries=chunk_entries,
        merge_fan_in=fan_in,
    )
    values = [
        {"kind": "value", "index": index % 31, "nested": [index % 5, {"x": index % 3}]}
        for index in range(120)
    ]
    for value in values:
        memory.intern(value)
        chunked.intern(value)
        chunked.flush_chunk_if_needed()

    chunked.finalize()

    assert list(chunked.iter_entries_sorted()) == list(memory.iter_entries_sorted())
    assert chunked.digest() == memory.digest()
    assert chunked.entry_count == memory.entry_count
    assert chunked.physical_byte_count == memory.physical_byte_count
    assert chunked.peak_open_chunk_streams <= fan_in
    chunked.close()


def test_finalize_fails_closed_on_conflicting_duplicate_bytes(tmp_path) -> None:
    store = ChunkedContentAddressedStore(tmp_path / "cas-conflict", chunk_entries=1)
    ref = store.intern({"value": "original"})
    store.flush_chunk_if_needed()
    conflicting = tmp_path / "cas-conflict" / "chunk-conflicting.cas"
    conflicting.write_bytes(
        ref.encode("ascii")
        + b"\t"
        + base64.b64encode(b'{"value":"conflicting"}')
        + b"\n"
    )
    store._chunks.append(conflicting)

    with pytest.raises(ValueError, match="conflicting physical bytes"):
        store.finalize()

    assert store.finalized_path is None
    assert not (tmp_path / "cas-conflict" / "finalized.cas").exists()
    assert not (tmp_path / "cas-conflict" / "finalized.cas.partial").exists()
    store.close()


def test_resource_failure_during_intermediate_merge_cleans_partial_outputs(tmp_path) -> None:
    store = ChunkedContentAddressedStore(
        tmp_path / "cas-blocked", chunk_entries=1, merge_fan_in=8
    )
    for index in range(205):
        store.intern({"value": index})
        store.flush_chunk_if_needed()

    def probe(stage: str) -> None:
        if stage == "cas_intermediate_merge_group_complete":
            raise ResourceBlocked(stage)

    with pytest.raises(ResourceBlocked, match="intermediate_merge"):
        store.finalize(resource_probe=probe, probe_interval_bytes=1)

    assert store.finalized_path is None
    assert not list((tmp_path / "cas-blocked").glob("merge-*.cas"))
    assert not list((tmp_path / "cas-blocked").glob("*.partial"))
    store.close()
    assert not (tmp_path / "cas-blocked").exists()


def test_resource_failure_before_finalized_store_promotion_removes_partial(tmp_path) -> None:
    store = ChunkedContentAddressedStore(
        tmp_path / "cas-final-blocked", chunk_entries=1, merge_fan_in=4
    )
    for index in range(20):
        store.intern({"value": index})
        store.flush_chunk_if_needed()

    def probe(stage: str) -> None:
        if stage == "cas_finalized_store_completion":
            raise ResourceBlocked(stage)

    with pytest.raises(ResourceBlocked, match="finalized_store_completion"):
        store.finalize(resource_probe=probe, probe_interval_bytes=1)

    assert store.finalized_path is None
    assert not (tmp_path / "cas-final-blocked" / "finalized.cas").exists()
    assert not (tmp_path / "cas-final-blocked" / "finalized.cas.partial").exists()
    assert not list((tmp_path / "cas-final-blocked").glob("merge-*.cas"))
    with pytest.raises(RuntimeError, match="previously failed"):
        store.finalize()
    store.close()


def test_healthy_resource_callback_allows_finalization(tmp_path) -> None:
    store = ChunkedContentAddressedStore(
        tmp_path / "cas-healthy", chunk_entries=1, merge_fan_in=4
    )
    for index in range(20):
        store.intern({"value": index})
        store.flush_chunk_if_needed()
    events: list[str] = []

    store.finalize(resource_probe=events.append, probe_interval_bytes=1)

    assert store.finalization_count == 1
    assert events[0] == "cas_finalize_start"
    assert events[-1] == "cas_finalized_store_completion"
    store.close()
