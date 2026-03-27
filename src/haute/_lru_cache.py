"""Thread-safe bounded LRU cache.

Replaces the ad-hoc ``OrderedDict`` / ``dict`` + lock + size-cap patterns
that were duplicated across ``_io.py``, ``_mlflow_io.py``, and
``_optimiser_io.py``.

Usage::

    cache: LRUCache[tuple[str, float], object] = LRUCache(max_size=32)
    cache.put(key, value)
    hit = cache.get(key)          # returns None on miss
    if key in cache: ...          # __contains__
    cache.clear()
"""

from __future__ import annotations

import threading
import time as _time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_MISSING = object()
"""Sentinel distinguishing a cache miss from a stored ``None`` value."""


class LRUCache(Generic[K, V]):
    """Thread-safe bounded LRU cache with optional TTL.

    Parameters
    ----------
    max_size:
        Maximum number of entries.  When exceeded the least-recently-used
        entry is evicted.
    ttl:
        Optional time-to-live in seconds.  Entries older than *ttl* are
        treated as misses and evicted lazily on the next ``get``.
        ``None`` (the default) disables expiry.
    """

    __slots__ = ("_max_size", "_ttl", "_data", "_timestamps", "_lock")

    def __init__(self, max_size: int = 128, ttl: float | None = None) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        self._max_size = max_size
        self._ttl = ttl
        self._data: OrderedDict[K, V] = OrderedDict()
        self._timestamps: dict[K, float] = {}  # only populated when ttl is set
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def get(self, key: K) -> V | None:
        """Return the cached value or ``None`` on miss.

        On a hit the entry is promoted to most-recently-used.
        If *ttl* is configured and the entry has expired, it is evicted
        and ``None`` is returned.
        """
        with self._lock:
            value = self._data.get(key, _MISSING)
            if value is _MISSING:
                return None
            if self._ttl is not None:
                stored_at = self._timestamps.get(key, 0.0)
                if (_time.monotonic() - stored_at) > self._ttl:
                    del self._data[key]
                    self._timestamps.pop(key, None)
                    return None
            self._data.move_to_end(key)
            return value  # type: ignore[return-value]

    def put(self, key: K, value: V) -> None:
        """Insert or update *key*.  Evicts the LRU entry if full."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                if len(self._data) > self._max_size:
                    evicted_key, _ = self._data.popitem(last=False)
                    self._timestamps.pop(evicted_key, None)
            if self._ttl is not None:
                self._timestamps[key] = _time.monotonic()

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._data.clear()
            self._timestamps.clear()

    def __contains__(self, key: K) -> bool:  # type: ignore[override]
        """Check presence *without* promoting the entry or checking TTL.

        This is intentionally a lightweight probe used for ``if key in cache``
        guards before a full ``get``.
        """
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __repr__(self) -> str:
        return f"LRUCache(max_size={self._max_size}, ttl={self._ttl}, entries={len(self)})"
