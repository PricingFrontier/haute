"""Thread-safe multi-entry fingerprint cache.

Replaces the duplicate ``_TraceCache`` and ``_PreviewCache`` classes in
``trace.py`` and ``executor.py`` with a single generic implementation.

Both caches follow the same pattern:

1. A *fingerprint* (hash of graph structure) determines cache validity.
2. On hit, the cached data dicts are returned without re-execution.
3. On miss, the caller executes and stores new data.
4. ``invalidate()`` clears everything.

Supports multiple entries (default 8) so that switching between
sources or row-limits does not invalidate unrelated cached results.
LRU eviction keeps the most recently accessed entries.

Usage::

    cache = FingerprintCache(
        slots=("eager_outputs", "order", "errors"),
    )

    # Write
    cache.store(fp, eager_outputs={...}, order=[...], errors={})

    # Read
    data = cache.try_get(fp)       # returns dict of slots or None
    data["eager_outputs"]          # typed by caller

    # Invalidate
    cache.invalidate()
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from haute._logging import get_logger

logger = get_logger(component="fingerprint_cache")

_MISSING = object()


class FingerprintCache:
    """Thread-safe multi-entry LRU cache keyed by fingerprint strings.

    Parameters
    ----------
    slots:
        Names of the data fields this cache stores.  ``store()``
        accepts keyword arguments matching these names.
    max_entries:
        Maximum number of fingerprint entries to keep.  When exceeded,
        the least-recently-used entry is evicted.  Default ``8``
        allows caching ~4 sources × 2 row-limits without thrashing.
    """

    __slots__ = ("_slots", "_entries", "_max_entries", "_lock", "_pinned")

    def __init__(
        self,
        slots: tuple[str, ...],
        max_entries: int = 8,
    ) -> None:
        if not slots:
            raise ValueError("At least one slot name is required")
        self._slots = slots
        self._max_entries = max(max_entries, 1)
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._pinned: set[str] = set()

    # -- public API --------------------------------------------------------

    @property
    def fingerprint(self) -> str | None:
        """Most-recently stored fingerprint (backward compat)."""
        with self._lock:
            if self._entries:
                return next(reversed(self._entries))
            return None

    def try_get(self, fingerprint: str) -> dict[str, Any] | None:
        """Return a *shallow copy* of all slot data if *fingerprint* matches.

        Returns ``None`` on miss.  On hit the entry is promoted to
        most-recently-used.
        """
        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                return None
            first_slot = self._slots[0]
            if entry.get(first_slot, _MISSING) is _MISSING:
                return None
            # Promote to MRU
            self._entries.move_to_end(fingerprint)
            return {name: entry[name] for name in self._slots}

    def store(self, fingerprint: str, **slot_data: Any) -> None:
        """Store (or replace) an entry for *fingerprint*.

        Every key in *slot_data* must be a declared slot name.
        Any declared slot not provided is reset to an empty dict.
        LRU eviction occurs when *max_entries* is exceeded.
        """
        unknown = set(slot_data) - set(self._slots)
        if unknown:
            raise ValueError(
                f"Unknown slot(s): {sorted(unknown)}. Declared slots: {sorted(self._slots)}"
            )
        with self._lock:
            entry = {name: slot_data.get(name, {}) for name in self._slots}
            self._entries[fingerprint] = entry
            self._entries.move_to_end(fingerprint)
            # Evict LRU entries if over capacity, skipping pinned entries
            while len(self._entries) > self._max_entries:
                evicted = False
                for key in list(self._entries.keys()):
                    if key not in self._pinned:
                        del self._entries[key]
                        evicted = True
                        break
                if not evicted:
                    break  # all entries pinned — allow over-capacity

    def update_slot(self, slot: str, value: Any, *, fingerprint: str) -> None:
        """Replace a single slot's value on the entry matching *fingerprint*.

        Useful for the preview cache's "extend" path where only some
        slots are merged.  If *fingerprint* is not found, a warning is
        logged and the call is a no-op.
        """
        if slot not in self._slots:
            raise ValueError(f"Unknown slot: {slot!r}. Declared slots: {sorted(self._slots)}")
        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                logger.warning("update_slot_unknown_fingerprint", fingerprint=fingerprint[:8])
                return
            entry[slot] = value

    def pin(self, fingerprint: str) -> None:
        """Exempt an entry from LRU eviction.

        Pinned entries survive eviction pressure so the trace can always
        reuse the exact DataFrames from the most recent preview execution.
        """
        with self._lock:
            if fingerprint in self._entries:
                self._pinned.add(fingerprint)

    def unpin(self, fingerprint: str) -> None:
        """Remove eviction exemption."""
        with self._lock:
            self._pinned.discard(fingerprint)

    def invalidate(self) -> None:
        """Clear all entries and pins."""
        with self._lock:
            self._entries.clear()
            self._pinned.clear()

    @property
    def lock(self) -> threading.RLock:
        """Expose the lock for callers that need atomic read-modify-write."""
        return self._lock

    def __repr__(self) -> str:
        with self._lock:
            n = len(self._entries)
            fps = list(self._entries.keys())
        fp_summary = ", ".join(f[:8] for f in fps[-3:])
        return f"FingerprintCache(entries={n}/{self._max_entries}, recent=[{fp_summary}])"
