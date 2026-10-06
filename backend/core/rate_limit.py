"""In-process sliding-window rate limiter.

Counters live in this process's memory: they reset on restart and are not shared between instances, so
this is a guard against a single client hammering the (paid, rate-limited) LLM, not a distributed quota.
"""

import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional

WINDOW_SECONDS = 60.0
_PURGE_EVERY = 256  # calls between sweeps of keys that have gone quiet


class RateLimiter:
    def __init__(self, limit: int, window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit  # requests per window per key; <= 0 disables the limiter
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, Deque[float]] = {}
        self._calls = 0

    def check(self, key: str) -> Optional[int]:
        """Record one request. Returns None if allowed, else the whole seconds until the key may retry."""
        if self.limit <= 0:
            return None
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            self._calls += 1
            if self._calls % _PURGE_EVERY == 0:
                self._purge(cutoff)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return None

    def _purge(self, cutoff: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
