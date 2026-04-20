"""Thread-safe bounded LRU cache with pinning.

Unified eviction + pinning layer consolidated from the three overlapping
cache modules reviewed in Phase 2 Package 3A:

* this module — bounded LRU with optional TTL, now absorbing pinning.
* ``_fingerprint_cache.py`` — multi-slot fingerprint cache, reduced to
  a thin subclass that adds slot-dict sugar on top of the pinning /
  eviction machinery defined here.
* ``_cache.py`` — the ``graph_fingerprint`` helper (graph → digest) is
  a different concern and remains untouched.

Call-site shape::

    cache: LRUCache[tuple[str, float], object] = LRUCache(max_size=32)
    cache.put(key, value)
    hit = cache.get(key)          # returns None on miss
    if key in cache: ...          # __contains__

    # Pin an entry so the eviction loop skips it.  Pinning is an
    # optional overlay — entries that are never pinned behave exactly
    # like a plain bounded LRU cache.
    cache.pin(key)
    cache.unpin(key)
"""

from __future__ import annotations

import threading
import time as _time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_MISSING = object()
"""Sentinel distinguishing a cache miss from a stored ``None`` value."""


class LRUCache(Generic[K, V]):
    """Thread-safe bounded LRU cache with optional TTL and pinning.

    Parameters
    ----------
    max_size:
        Maximum number of entries.  When exceeded the least-recently-used
        *unpinned* entry is evicted.  Pinned entries are skipped by the
        eviction loop and therefore may push the live entry count beyond
        *max_size* — this mirrors the pre-refactor ``FingerprintCache``
        contract where pinned previews survived LRU pressure so the
        trace could always reuse the exact same DataFrames.
    ttl:
        Optional time-to-live in seconds.  Entries older than *ttl* are
        treated as misses and evicted lazily on the next ``get``.
        ``None`` (the default) disables expiry.
    """

    __slots__ = ("_max_size", "_ttl", "_data", "_timestamps", "_pinned", "_lock")

    def __init__(self, max_size: int = 128, ttl: float | None = None) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        self._max_size = max_size
        self._ttl = ttl
        self._data: OrderedDict[K, V] = OrderedDict()
        self._timestamps: dict[K, float] = {}  # only populated when ttl is set
        self._pinned: set[K] = set()
        self._lock = threading.RLock()

    # -- public API --------------------------------------------------------

    def get(self, key: K) -> V | None:
        """Return the cached value or ``None`` on miss.

        On a hit the entry is promoted to most-recently-used.
        If *ttl* is configured and the entry has expired, it is evicted
        and ``None`` is returned.  Pinned entries are *not* exempt from
        TTL expiry — a stale pin is still stale.
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
                    self._pinned.discard(key)
                    return None
            self._data.move_to_end(key)
            return value  # type: ignore[return-value]

    def put(self, key: K, value: V) -> None:
        """Insert or update *key*.

        When capacity is exceeded, the least-recently-used *unpinned*
        entry is evicted.  If every live entry is pinned, the cache is
        allowed to exceed ``max_size`` — this is the FingerprintCache
        contract and keeps pinned entries from being silently dropped.
        """
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                self._evict_if_over_capacity()
            if self._ttl is not None:
                self._timestamps[key] = _time.monotonic()

    def pin(self, key: K) -> None:
        """Exempt *key* from LRU eviction.

        Pinning an unknown key is a silent no-op — this mirrors the
        pre-refactor ``FingerprintCache.pin`` contract and keeps call
        sites that race a store with a rollback from needing to
        coordinate.  Pins on a key that has since been evicted or
        TTL-expired are also silently dropped on the next ``unpin``.
        """
        with self._lock:
            if key in self._data:
                self._pinned.add(key)

    def unpin(self, key: K) -> None:
        """Remove eviction exemption for *key*.  Silent on unknown keys."""
        with self._lock:
            self._pinned.discard(key)

    def clear(self) -> None:
        """Remove all entries and pins."""
        with self._lock:
            self._data.clear()
            self._timestamps.clear()
            self._pinned.clear()

    def evict_where(self, predicate: Callable[[K], bool]) -> list[V]:
        """Atomically evict every entry whose key satisfies *predicate*.

        The whole scan-and-delete happens under ``self._lock`` so callers
        see a single atomic step — no entry can be promoted, pinned, or
        TTL-expired in the middle of the iteration.

        Returns the *values* of the evicted entries so callers can run
        cleanup (cascade invalidation etc.) **outside** the lock.  This
        is deliberate: invoking a cross-module callback under our own
        internal lock is a deadlock risk if the callback ever reaches
        back into another LRUCache that shares a lock-ordering.

        Pinning is *not* honoured — a predicate-driven eviction is an
        explicit "get rid of these" instruction from the caller and
        silently keeping a pinned entry alive would defeat the point.
        """
        with self._lock:
            # Materialise matches inside the lock so the iteration is
            # consistent even if another thread is queueing writes.
            evicted_pairs = [(k, v) for k, v in self._data.items() if predicate(k)]
            for k, _ in evicted_pairs:
                del self._data[k]
                self._timestamps.pop(k, None)
                self._pinned.discard(k)
        return [v for _, v in evicted_pairs]

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

    # -- internal helpers --------------------------------------------------

    def _evict_if_over_capacity(self) -> None:
        """Evict LRU unpinned entries until the cache is at or below
        ``max_size``.  Caller must hold ``self._lock``.

        If every live entry is pinned, the loop exits without evicting
        and the cache is allowed to exceed capacity — this is the
        FingerprintCache contract from the pre-refactor world.
        """
        while len(self._data) > self._max_size:
            evicted = False
            # Iterate from the LRU end (OrderedDict head).  ``list(...)``
            # materialises the keys so we can ``del`` during iteration
            # without mutating what we're walking.
            for candidate in list(self._data.keys()):
                if candidate in self._pinned:
                    continue
                del self._data[candidate]
                self._timestamps.pop(candidate, None)
                evicted = True
                break
            if not evicted:
                # All live entries are pinned — allow over-capacity.
                break
