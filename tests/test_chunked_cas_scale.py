from __future__ import annotations

from ashare_turnaround.scanner.artifacts import (
    ChunkedContentAddressedStore,
    ContentAddressedStore,
)


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
