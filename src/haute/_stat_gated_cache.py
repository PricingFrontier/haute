"""Process-wide value cache gated on a backing file's ``(mtime_ns, size)``.

Shares the invalidation discipline of
:func:`haute.execution._stat_gated_runtime_path_fingerprint`: a cached
value is reused while the backing file's ``(st_mtime_ns, st_size)`` is
unchanged; any metadata change reloads. One slot per key is replaced when
the stat gate changes, and least-recently-used slots are evicted at the
configured entry bound.

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

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


DEFAULT_STAT_GATED_CACHE_MAX_ENTRIES = 256


class _LoadGate:
    """A per-key load lock, with its current number of participants."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.participants = 0


def resolve_artifact_path(path: str | Path) -> str:
    """Canonical ON-DISK spelling of an artifact path — for stat and I/O.

    ``expanduser`` + ``resolve`` collapse ``./``, ``..`` and symlinks but
    preserve case: this is the string to hand to ``stat``/``open``.  Use
    :func:`artifact_cache_key` — NOT this — as the cache slot key.
    """
    return str(Path(path).expanduser().resolve())


def artifact_cache_key(path: str | Path) -> str:
    """Canonical cache-KEY string for a filesystem artifact path.

    Mirrors :func:`haute._json_flatten._path_hash`'s canonicalisation:
    :func:`resolve_artifact_path` plus ``os.path.normcase``, which folds
    case where the OS convention is case-insensitive (Windows).  The folded
    string is a KEY ONLY — it must never be used for stat or I/O, where the
    case-preserved :func:`resolve_artifact_path` spelling belongs (a folded
    spelling need not exist on a case-sensitive filesystem).  Residual:
    ``normcase`` is a no-op on POSIX, so on macOS (case-insensitive
    filesystem, case-preserving API) two case spellings of one file can
    still occupy two slots — the same accepted posture as the JSON cache;
    the cost is memory residue only, and Windows (where ``normcase`` folds)
    is fully covered.
    """
    return os.path.normcase(resolve_artifact_path(path))


class StatGatedCache(Generic[K, V]):
    """Bounded, single-flight, stat-gated cache of loaded file artifacts."""

    def __init__(
        self,
        *,
        artifact_kind: str,
        max_entries: int = DEFAULT_STAT_GATED_CACHE_MAX_ENTRIES,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        self._artifact_kind = artifact_kind
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[K, tuple[int, int, V]] = OrderedDict()
        self._load_locks: dict[K, _LoadGate] = {}

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
                    self._entries.move_to_end(key)
                    return entry[2]
                load_gate = self._load_locks.setdefault(key, _LoadGate())
                load_gate.participants += 1
            try:
                with load_gate.lock:
                    # Re-check: the load that held this lock while we waited
                    # has already populated the cache for this gate.
                    with self._lock:
                        entry = self._entries.get(key)
                        if entry is not None and (entry[0], entry[1]) == gate:
                            self._entries.move_to_end(key)
                            return entry[2]
                    value = loader()
                    after = Path(path).stat()
                    if (after.st_mtime_ns, after.st_size) != gate:
                        continue
                    with self._lock:
                        self._entries[key] = (gate[0], gate[1], value)
                        self._entries.move_to_end(key)
                        while len(self._entries) > self._max_entries:
                            evicted_key, _ = self._entries.popitem(last=False)
                            evicted_gate = self._load_locks.get(evicted_key)
                            if evicted_gate is not None and evicted_gate.participants == 0:
                                del self._load_locks[evicted_key]
                    return value
            finally:
                with self._lock:
                    load_gate.participants -= 1
                    if (
                        load_gate.participants == 0
                        and self._load_locks.get(key) is load_gate
                        and key not in self._entries
                    ):
                        del self._load_locks[key]
        raise RuntimeError(f"{self._artifact_kind} changed on disk while loading: {path}")

    def __len__(self) -> int:
        """Return the number of retained cache entries."""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Drop cached entries and idle gates without splitting an active flight."""
        with self._lock:
            self._entries.clear()
            self._load_locks = {
                key: load_gate
                for key, load_gate in self._load_locks.items()
                if load_gate.participants
            }
