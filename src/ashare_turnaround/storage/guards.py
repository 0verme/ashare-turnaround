"""Disk-space gates for long-running historical synchronization."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

RECOMMENDED_FREE_BYTES = 100 * 1024**3
STOP_FREE_BYTES = 50 * 1024**3
PAUSE_NONCORE_FREE_BYTES = 30 * 1024**3
EMERGENCY_STOP_FREE_BYTES = 15 * 1024**3


@dataclass(frozen=True, slots=True)
class DiskSpaceCheck:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    recommendation: str

    @property
    def hard_stop(self) -> bool:
        return self.free_bytes < STOP_FREE_BYTES

    @property
    def emergency_stop(self) -> bool:
        return self.free_bytes < EMERGENCY_STOP_FREE_BYTES

    @property
    def below_recommended(self) -> bool:
        return self.free_bytes < RECOMMENDED_FREE_BYTES

    @property
    def pause_noncore(self) -> bool:
        return self.free_bytes < PAUSE_NONCORE_FREE_BYTES


def check_disk_space(path: str | Path = ".") -> DiskSpaceCheck:
    target = Path(path).expanduser()
    usage = shutil.disk_usage(target if target.exists() else target.parent)
    if usage.free < EMERGENCY_STOP_FREE_BYTES:
        recommendation = "STOP: less than 15 GiB free"
    elif usage.free < PAUSE_NONCORE_FREE_BYTES:
        recommendation = "STOP non-core datasets: less than 30 GiB free"
    elif usage.free < STOP_FREE_BYTES:
        recommendation = "STOP: less than 50 GiB free"
    elif usage.free < RECOMMENDED_FREE_BYTES:
        recommendation = "PROCEED WITH CAUTION: below 100 GiB recommendation"
    else:
        recommendation = "PASS"
    return DiskSpaceCheck(
        path=str(target),
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        recommendation=recommendation,
    )
