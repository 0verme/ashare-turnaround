"""Small filesystem durability helpers shared by local stores."""

from __future__ import annotations

import os
from pathlib import Path


def fsync_directory(directory: str | Path) -> None:
    """Fsync a directory after a rename when the platform supports it."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(Path(directory), flags)
    except OSError:
        # Directory fsync is not available on every supported filesystem.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
