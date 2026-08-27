"""Small JSON synchronization log with atomic updates."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SyncRecord:
    dataset: str
    params: dict[str, Any]
    started_at: str
    finished_at: str
    status: str
    rows: int = 0
    error_type: str | None = None
    error_message: str | None = None


_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "passwd", "api_key", "apikey")


def _sanitize(value: Any, secret: str | None = None, key: str | None = None) -> Any:
    if key and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, secret, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item, secret) for item in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str) and secret:
        return value.replace(secret, "<redacted>")
    return value


class SyncStateStore:
    """Append structured run records to a local JSON array."""

    def __init__(self, path: str | Path, *, secret: str | None = None) -> None:
        self.path = Path(path).expanduser()
        self._secret = secret

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, list):
            raise ValueError(f"sync state must be a JSON list: {self.path}")
        return value

    def append(self, record: SyncRecord) -> None:
        payload = _sanitize(asdict(record), self._secret)
        records = self.records()
        records.append(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(records, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def latest(self, dataset: str | None = None) -> dict[str, Any] | None:
        values = self.records()
        if dataset is not None:
            values = [record for record in values if record.get("dataset") == dataset]
        return values[-1] if values else None
