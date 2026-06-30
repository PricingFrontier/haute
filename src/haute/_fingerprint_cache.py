"""Thin slot-dict sugar layer over ``LRUCache``.

The heavy lifting (bounded LRU eviction, pinning, thread safety) lives
in ``_lru_cache.LRUCache``.
``FingerprintCache`` is a thin subclass that adds multi-slot dict-valued
semantics on top: each cache entry is a dict keyed by a fixed set of
declared slot names, so callers can do::

    cache = FingerprintCache(slots=("eager_outputs", "order", "errors"))
    cache.store(fp, eager_outputs={...}, order=[...], errors={})
    data = cache.try_get(fp)           # returns the slot dict or None
    cache.update_slot("order", [...], fingerprint=fp)
    cache.invalidate()

The subclass relationship is intentional: the ``TestFingerprintCacheRetired``
test accepts either removal of this module or a thin ``LRUCache`` alias.
Keeping it as a subclass preserves every existing call site in
``executor.py`` and ``trace.py`` verbatim while the pinning / eviction
machinery is now shared with the other three LRU-backed caches in
``_io.py``, ``_mlflow_io.py``, and ``_optimiser_io.py``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from haute._logging import get_logger
from haute._lru_cache import LRUCache

logger = get_logger(component="fingerprint_cache")

_MISSING = object()


class FingerprintCache(LRUCache[str, dict[str, Any]]):
    """Multi-slot fingerprint cache layered on top of :class:`LRUCache`.

    Parameters
    ----------
    slots:
        Names of the data fields this cache stores.  ``store()``
        accepts keyword arguments matching these names.
    max_entries:
        Maximum number of fingerprint entries to keep.  When exceeded,
        the least-recently-used *unpinned* entry is evicted.  Default
        ``8`` allows caching ~4 sources × 2 row-limits without thrashing.
    max_bytes:
        Optional retained-byte budget for entries.  When set, *size_of*
        must deterministically estimate each slot dict's byte weight.
        Eviction follows the same LRU and pinning rules as *max_entries*.
    size_of:
        Byte-estimation callback used only when *max_bytes* is configured.
    """

    __slots__ = ("_slots", "_size_sensitive_slots")

    def __init__(
        self,
        slots: tuple[str, ...],
        max_entries: int = 8,
        *,
        max_bytes: int | None = None,
        size_of: Callable[[dict[str, Any]], int] | None = None,
        size_sensitive_slots: tuple[str, ...] | None = None,
    ) -> None:
        if not slots:
            raise ValueError("At least one slot name is required")
        if size_sensitive_slots is None:
            size_sensitive_slots = slots
        unknown_size_slots = set(size_sensitive_slots) - set(slots)
        if unknown_size_slots:
            raise ValueError(
                "Unknown size-sensitive slot(s): "
                f"{sorted(unknown_size_slots)}. Declared slots: {sorted(slots)}"
            )
        super().__init__(max_size=max(max_entries, 1), max_bytes=max_bytes, size_of=size_of)
        self._slots = slots
        self._size_sensitive_slots = frozenset(size_sensitive_slots)

    # -- public API --------------------------------------------------------

    @property
    def fingerprint(self) -> str | None:
        """Most-recently accessed (MRU) fingerprint, or ``None`` if empty."""
        with self._lock:
            if self._data:
                return next(reversed(self._data))
            return None

    def try_get(self, fingerprint: str) -> dict[str, Any] | None:
        """Return a *shallow copy* of the slot dict if *fingerprint* matches.

        Returns ``None`` on miss.  On hit the entry is promoted to
        most-recently-used.  The top-level dict is a fresh copy so
        callers can mutate it without affecting the cache, but the
        inner values (typically large DataFrames) are shared by design.
        """
        with self._lock:
            entry = self._data.get(fingerprint)
            if entry is None:
                return None
            first_slot = self._slots[0]
            if entry.get(first_slot, _MISSING) is _MISSING:
                return None
            self._data.move_to_end(fingerprint)
            return {name: entry[name] for name in self._slots}

    def store(self, fingerprint: str, **slot_data: Any) -> None:
        """Store (or replace) an entry for *fingerprint*.

        Every key in *slot_data* must be a declared slot name.  Any
        declared slot not provided is reset to an empty dict.  LRU
        eviction (skipping pinned entries) occurs when ``max_entries``
        is exceeded.
        """
        unknown = set(slot_data) - set(self._slots)
        if unknown:
            raise ValueError(
                f"Unknown slot(s): {sorted(unknown)}. Declared slots: {sorted(self._slots)}"
            )
        entry = {name: slot_data.get(name, {}) for name in self._slots}
        # Use ``put`` for the base-class eviction + pinning logic.
        self.put(fingerprint, entry)

    def update_slot(self, slot: str, value: Any, *, fingerprint: str) -> None:
        """Replace a single slot's value on the entry matching *fingerprint*.

        Useful for the preview cache's "extend" path where only some slots
        are merged. Byte-capped caches preserve the stored byte estimate when
        a slot outside ``size_sensitive_slots`` changes, avoiding allocator-
        dependent remeasurement drift for unchanged heavy objects. Updates to
        size-sensitive slots still route through ``put()`` so growth, shrink,
        and oversize replacements are accounted for. If *fingerprint* is not
        found, a warning is logged and the call is a no-op.
        """
        if slot not in self._slots:
            raise ValueError(f"Unknown slot: {slot!r}. Declared slots: {sorted(self._slots)}")
        with self._lock:
            entry = self._data.get(fingerprint)
            if entry is None:
                logger.warning("update_slot_unknown_fingerprint", fingerprint=fingerprint[:8])
                return
            updated = dict(entry)
            updated[slot] = value
            if self._max_bytes is not None and slot not in self._size_sensitive_slots:
                self._data.move_to_end(fingerprint)
                self._data[fingerprint] = updated
                return
            self.put(fingerprint, updated)

    def invalidate(self) -> None:
        """Clear all entries and pins (alias for :meth:`LRUCache.clear`)."""
        self.clear()

    def _capacity_entry_count(self) -> int:
        """Fingerprint ``max_entries`` is a total-entry budget.

        Pinned entries are still protected from LRU eviction, but they do
        not make room for unlimited new fingerprints.  If all resident
        entries are pinned and a new unpinned fingerprint arrives, the
        newcomer is evicted instead of growing the preview cache forever.
        """
        return len(self._data)

    @property
    def lock(self) -> threading.RLock:
        """Expose the lock for callers that need atomic read-modify-write."""
        return self._lock

    def __repr__(self) -> str:
        with self._lock:
            n = len(self._data)
            fps = list(self._data.keys())
        fp_summary = ", ".join(f[:8] for f in fps[-3:])
        return f"FingerprintCache(entries={n}/{self._max_size}, recent=[{fp_summary}])"
