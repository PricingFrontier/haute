"""Thread-safe bounded LRU cache with pinning.

Unified eviction + pinning layer used by bounded in-memory caches:

* this module — bounded LRU with optional TTL, now absorbing pinning.
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

import logging
import threading
import time as _time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_MISSING = object()
"""Sentinel distinguishing a cache miss from a stored ``None`` value."""

_LOG = logging.getLogger(__name__)


class LRUCache(Generic[K, V]):
    """Thread-safe bounded LRU cache with optional TTL and pinning.

    Parameters
    ----------
    max_size:
        Maximum number of entries.  When exceeded the least-recently-used
        *unpinned* entry is evicted.  Pinned entries are skipped by the
        eviction loop and therefore may push the live entry count beyond
        *max_size*. Pinned previews survive LRU pressure so the trace
        can always reuse the exact same DataFrames.
    ttl:
        Optional time-to-live in seconds.  Entries older than *ttl* are
        treated as misses and evicted lazily on the next ``get``.
        ``None`` (the default) disables expiry.
    pin_slots:
        Optional keys that are protected from LRU eviction as soon as
        they are populated.  Unlike ``pin(key)``, these may be named
        before the corresponding entry exists.
    max_bytes:
        Optional byte budget.  When configured, *size_of* must return a
        deterministic byte weight for each value.  Eviction uses the same
        LRU/pinning rules as the entry-count bound.
    """

    __slots__ = (
        "_max_size",
        "_ttl",
        "_data",
        "_timestamps",
        "_pinned",
        "_pin_slots",
        "_lock",
        "_max_bytes",
        "_size_of",
        "_sizes",
        "_current_bytes",
    )

    def __init__(
        self,
        max_size: int = 128,
        ttl: float | None = None,
        *,
        pin_slots: Iterable[K] = (),
        max_bytes: int | None = None,
        size_of: Callable[[V], int] | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        if max_bytes is not None and max_bytes < 1:
            raise ValueError(f"max_bytes must be >= 1, got {max_bytes}")
        if max_bytes is not None and size_of is None:
            raise ValueError("size_of is required when max_bytes is configured")
        if max_bytes is None and size_of is not None:
            raise ValueError("max_bytes is required when size_of is configured")
        self._max_size = max_size
        self._ttl = ttl
        self._data: OrderedDict[K, V] = OrderedDict()
        self._timestamps: dict[K, float] = {}  # only populated when ttl is set
        self._pinned: set[K] = set()
        self._pin_slots = set(pin_slots)
        self._lock = threading.RLock()
        self._max_bytes = max_bytes
        self._size_of = size_of
        self._sizes: dict[K, int] = {}
        self._current_bytes = 0

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
                    self._remove_key(key)
                    return None
            self._data.move_to_end(key)
            return value  # type: ignore[return-value]

    def put(self, key: K, value: V) -> bool:
        """Insert or update *key* and report whether the value was retained.

        When capacity is exceeded, the least-recently-used *unpinned*
        entry is evicted.  If every live entry is pinned, the cache is
        allowed to exceed ``max_size`` so pinned entries are not silently
        dropped.
        """
        size = self._measure(value)
        with self._lock:
            if self._max_bytes is not None and size > self._max_bytes:
                replaced_existing = key in self._data
                _LOG.warning(
                    "lru_cache_oversized_entry_not_cached",
                    extra={
                        "key": key,
                        "measured_size": size,
                        "max_bytes": self._max_bytes,
                        "replaced_existing": replaced_existing,
                    },
                )
                return False
            if key in self._data:
                self._data.move_to_end(key)
                self._current_bytes -= self._sizes.pop(key, 0)
                self._data[key] = value
            else:
                self._data[key] = value
            if self._max_bytes is not None:
                self._sizes[key] = size
                self._current_bytes += size
            if self._ttl is not None:
                self._timestamps[key] = _time.monotonic()
            self._evict_if_over_capacity()
            return key in self._data

    def pin(self, key: K) -> None:
        """Exempt *key* from LRU eviction.

        Pinning an unknown key is a silent no-op. This keeps call sites
        that race a store with a rollback from needing to coordinate.
        Pins on a key that has since been evicted or TTL-expired are
        also silently dropped on the next ``unpin``.
        """
        with self._lock:
            if key in self._data:
                self._pinned.add(key)

    def unpin(self, key: K) -> None:
        """Remove eviction exemption for *key*.  Silent on unknown keys."""
        with self._lock:
            self._pinned.discard(key)
            self._pin_slots.discard(key)
            self._evict_if_over_capacity()

    def clear(self) -> None:
        """Remove all entries and dynamic pins.

        Constructor-level ``pin_slots`` are cache policy and remain in
        force after ``clear()`` so a repopulated slot stays protected.
        """
        with self._lock:
            self._data.clear()
            self._timestamps.clear()
            self._pinned.clear()
            self._sizes.clear()
            self._current_bytes = 0

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
                self._remove_key(k)
        return [v for _, v in evicted_pairs]

    def stats(self) -> dict[str, int | None]:
        """Return deterministic cache diagnostics for tests and operators."""
        with self._lock:
            return {
                "entries": len(self._data),
                "max_entries": self._max_size,
                "bytes": self._current_bytes,
                "max_bytes": self._max_bytes,
                "pinned_entries": sum(1 for key in self._data if self._is_pinned(key)),
            }

    @property
    def most_recent_key(self) -> K | None:
        """Return the most-recently-used key, or ``None`` when empty."""
        with self._lock:
            return next(reversed(self._data)) if self._data else None

    def __contains__(self, key: K) -> bool:
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
        with self._lock:
            entries = len(self._data)
            current_bytes = self._current_bytes
        return (
            f"LRUCache(max_size={self._max_size}, ttl={self._ttl}, "
            f"entries={entries}, bytes={current_bytes}/{self._max_bytes})"
        )

    # -- internal helpers --------------------------------------------------

    def _measure(self, value: V) -> int:
        """Return the byte weight for *value* when byte caps are enabled."""
        if self._size_of is None:
            return 0
        size = self._size_of(value)
        if type(size) is not int or size < 0:
            raise ValueError(f"size_of must return a non-negative int, got {size!r}")
        return size

    def _is_pinned(self, key: K) -> bool:
        return key in self._pinned or key in self._pin_slots

    def _capacity_entry_count(self) -> int:
        """Return the number of entries that count against ``max_size``."""
        return sum(1 for key in self._data if not self._is_pinned(key))

    def _remove_key(self, key: K) -> V:
        """Delete *key* and keep all auxiliary bookkeeping in sync."""
        value = self._data.pop(key)
        self._timestamps.pop(key, None)
        self._pinned.discard(key)
        self._current_bytes -= self._sizes.pop(key, 0)
        return value

    def _evict_if_over_capacity(self) -> None:
        """Evict LRU unpinned entries until the cache is at or below
        ``max_size`` and ``max_bytes``.  Caller must hold ``self._lock``.

        If every live entry is pinned, the loop exits without evicting
        and the cache is allowed to exceed capacity.
        """
        while self._capacity_entry_count() > self._max_size or (
            self._max_bytes is not None and self._current_bytes > self._max_bytes
        ):
            evicted = False
            # Iterate from the LRU end (OrderedDict head).  ``list(...)``
            # materialises the keys so we can ``del`` during iteration
            # without mutating what we're walking.
            for candidate in list(self._data.keys()):
                if self._is_pinned(candidate):
                    continue
                self._remove_key(candidate)
                evicted = True
                break
            if not evicted:
                # All live entries are pinned — allow over-capacity.
                break
