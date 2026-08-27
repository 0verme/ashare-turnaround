from __future__ import annotations

import json

from ashare_turnaround.storage.state import SyncRecord, SyncStateStore


def test_sync_state_records_required_fields_without_secret(tmp_path) -> None:
    path = tmp_path / "data" / "state" / "sync-log.json"
    state = SyncStateStore(path, secret="secret-token")
    state.append(
        SyncRecord(
            dataset="income",
            params={"ts_code": "600000.SH", "token": "secret-token"},
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:00:01Z",
            status="failed",
            error_type="permission",
            error_message="bad secret-token",
        )
    )

    raw = path.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    value = json.loads(raw)[0]
    assert value["dataset"] == "income"
    assert value["params"]["ts_code"] == "600000.SH"
    assert value["params"]["token"] == "<redacted>"
    assert value["error_type"] == "permission"
