"""Small thread-safe request pacing for shared API clients."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from threading import Lock


class RateLimiter:
    """Space API requests from all callers using one shared limiter.

    This deliberately uses conservative fixed-interval pacing instead of a
    bursty token bucket.  A single instance can therefore be shared by every
    financial bootstrap worker and by every retry attempt.
    """

    def __init__(
        self,
        requests_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not math.isfinite(requests_per_minute) or requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be a finite positive number")
        self._requests_per_minute = float(requests_per_minute)
        self._interval_seconds = 60.0 / self._requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        self._next_allowed = 0.0
        self._request_count = 0

    @property
    def requests_per_minute(self) -> float:
        return self._requests_per_minute

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count

    def acquire(self) -> None:
        """Wait for and reserve one request slot."""

        with self._lock:
            now = self._clock()
            scheduled = max(now, self._next_allowed)
            delay = scheduled - now
            self._next_allowed = scheduled + self._interval_seconds

        if delay > 0:
            self._sleep(delay)

        with self._lock:
            self._request_count += 1
