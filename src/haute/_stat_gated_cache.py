"""Process-wide value cache gated on a backing file's ``(mtime_ns, size)``.

Shares the invalidation discipline of
:func:`haute.execution._stat_gated_runtime_path_fingerprint`: a cached
value is reused while the backing file's ``(st_mtime_ns, st_size)`` is
unchanged; any metadata change reloads.  One slot per key, replaced when
the stat gate changes, so a cache stays bounded by the number of
distinct artifacts the process touches.

Concurrency: the first load for a key runs under a per-key lock — other
callers arriving during that load wait and then reuse the cached value,
so a thundering herd performs exactly one disk load (single flight).

Failure semantics (fail loud, never cache garbage):

* stat errors propagate — a missing/unreadable file fails the caller;
* loader exceptions propagate and nothing is cached, so the next call
  retries against the (possibly repaired) file;
* a stat gate that moves during the load is a torn read — retried once
  against the fresh gate, then raised as :class:`RuntimeError`.

A rewrite that changes bytes while preserving both ``mtime_ns`` and
``size`` is below the gate's resolution (the documented
``GraphFingerprintMemo`` trade).  Cached values are shared across
threads and must be treated as immutable by callers.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class StatGatedCache(Generic[K, V]):
    """Single-flight, stat-gated cache of loaded file artifacts."""

    def __init__(self, *, artifact_kind: str) -> None:
        self._artifact_kind = artifact_kind
        self._lock = threading.Lock()
        self._entries: dict[K, tuple[int, int, V]] = {}
        self._load_locks: dict[K, threading.Lock] = {}

    def get_or_load(self, key: K, path: str, loader: Callable[[], V]) -> V:
        """Return the cached value for *key*, loading at most once per gate.

        The gate is ``(st_mtime_ns, st_size)`` of *path* stat'd before the
        load; after a load the file is stat'd again and the value is only
        cached (and returned) if the gate held.
        """
        for _ in range(2):
            stat_result = Path(path).stat()
            gate = (stat_result.st_mtime_ns, stat_result.st_size)
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and (entry[0], entry[1]) == gate:
                    return entry[2]
                load_lock = self._load_locks.setdefault(key, threading.Lock())
            with load_lock:
                # Re-check: the load that held this lock while we waited
                # has already populated the cache for this gate.
                with self._lock:
                    entry = self._entries.get(key)
                    if entry is not None and (entry[0], entry[1]) == gate:
                        return entry[2]
                value = loader()
                after = Path(path).stat()
                if (after.st_mtime_ns, after.st_size) != gate:
                    continue
                with self._lock:
                    self._entries[key] = (gate[0], gate[1], value)
                return value
        raise RuntimeError(f"{self._artifact_kind} changed on disk while loading: {path}")

    def clear(self) -> None:
        """Drop every cached entry and per-key load lock."""
        with self._lock:
            self._entries.clear()
            self._load_locks.clear()
