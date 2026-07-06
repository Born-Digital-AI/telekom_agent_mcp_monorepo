"""Process-local TTL store for transient per-conversation state.

In-memory state in MCP services (e.g. authentication progress, troubleshooting
session) leaks memory if entries are never evicted, and breaks across replicas
if the deployment scales beyond one pod. This module provides a small TTL-aware
``dict``-like store that performs lazy eviction on access plus a periodic
sweep when the store grows.

For multi-replica deployments use Redis or a similar shared store instead —
this class is process-local on purpose. See AGENTS.md > "State management".
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


class TTLStore[V]:
    """Thread-safe ``key -> value`` store with TTL-based eviction.

    - Entries expire ``ttl_seconds`` after their last write.
    - Eviction is lazy: stale entries are dropped when accessed or when the
      store grows past ``sweep_threshold`` keys.
    - Uses ``time.monotonic()`` so wall-clock changes don't affect expiry.
    """

    def __init__(self, ttl_seconds: float, sweep_threshold: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._sweep_threshold = sweep_threshold
        self._data: dict[str, tuple[float, V]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def get(self, key: str, default: V | None = None) -> V | None:
        """Return value for ``key`` if present and not expired, else ``default``."""
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            written_at, value = entry
            if now - written_at > self._ttl:
                self._data.pop(key, None)
                return default
            return value

    def set(self, key: str, value: V) -> None:
        """Store ``value`` under ``key`` with the current timestamp."""
        now = time.monotonic()
        with self._lock:
            self._data[key] = (now, value)
            if len(self._data) > self._sweep_threshold:
                self._sweep_expired(now)

    def clear(self) -> None:
        """Drop all entries. Keeps the store object identity (useful for test resets)."""
        with self._lock:
            self._data.clear()

    def pop(self, key: str, default: V | None = None) -> V | None:
        """Remove ``key`` and return its value (or ``default`` if absent/expired)."""
        with self._lock:
            entry = self._data.pop(key, None)
            if entry is None:
                return default
            return entry[1]

    def keys(self) -> Iterator[str]:
        """Iterate over non-expired keys (snapshot at call time)."""
        now = time.monotonic()
        with self._lock:
            return iter([k for k, (ts, _) in self._data.items() if now - ts <= self._ttl])

    def _sweep_expired(self, now: float) -> None:
        """Drop all entries older than the TTL. Caller must hold ``self._lock``."""
        expired = [k for k, (ts, _) in self._data.items() if now - ts > self._ttl]
        for key in expired:
            del self._data[key]
