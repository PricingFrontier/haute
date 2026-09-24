"""Process-wide value cache gated on a backing file's freshness token.

A cached value is reused while the backing file's freshness token, observed
through :func:`haute._json_shred._source_proof.observe_freshness`, is
unchanged; any write moves the token and reloads. The token is the file's
native revision where the platform has one, and otherwise its stat, trusted
only once the file has settled; a younger file without a native revision is
loaded on every call and never cached. One slot per key is replaced when the
token changes, and least-recently-used slots are evicted at the configured
entry bound (a :class:`haute._lru_cache.LRUCache`).

Concurrency: the first load for a key runs under a per-key lock — other
callers arriving during that load wait and then reuse the cached value,
so a thundering herd performs exactly one disk load (single flight). A lock
with no waiter left is dropped. A forked child starts with fresh locks and
an empty cache.

Failure semantics (fail loud, never cache garbage):

* observation errors propagate — a missing/unreadable file fails the caller;
* loader exceptions propagate and nothing is cached, so the next call
  retries against the (possibly repaired) file;
* a token that moves during the load is a torn read — retried once against
  the fresh token, then raised as
  :class:`~haute._json_shred._source_proof.SourceChangedError`.

Cached values are shared across threads and must be treated as immutable by
callers.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Generic, TypeVar

from haute._json_shred._source_proof import SourceChangedError, observe_freshness
from haute._lru_cache import LRUCache

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

    :func:`resolve_artifact_path` plus ``os.path.normcase``, which folds
    case where the OS convention is case-insensitive (Windows).  The folded
    string is a KEY ONLY — it must never be used for stat or I/O, where the
    case-preserved :func:`resolve_artifact_path` spelling belongs (a folded
    spelling need not exist on a case-sensitive filesystem).  Residual:
    ``normcase`` is a no-op on POSIX, so on macOS (case-insensitive
    filesystem, case-preserving API) two case spellings of one file can
    still occupy two slots; the cost is memory residue only, and Windows (where ``normcase`` folds)
    is fully covered.
    """
    return os.path.normcase(resolve_artifact_path(path))


class StatGatedCache(Generic[K, V]):
    """Bounded, single-flight, freshness-gated cache of loaded file artifacts."""

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
        self._reset_process_state()

    def _reset_process_state(self) -> None:
        # Replace, never acquire: after a fork another thread may have held them.
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries: LRUCache[K, tuple[Hashable, V]] = LRUCache(max_size=self._max_entries)
        self._load_locks: dict[K, _LoadGate] = {}

    def get_or_load(self, key: K, path: str, loader: Callable[[], V]) -> V:
        """Return the cached value for *key*, loading at most once per token.

        The token of *path* is observed before the load and again after it;
        the value is cached (and returned) only if the token held, and only
        while the token is reusable.
        """
        if os.getpid() != self._process_id:
            self._reset_process_state()
        for _ in range(2):
            before = observe_freshness(Path(path))
            entry = self._entries.get(key) if before.reusable else None
            if entry is not None and entry[0] == before.token:
                return entry[1]
            with self._lock:
                load_gate = self._load_locks.setdefault(key, _LoadGate())
                load_gate.participants += 1
            try:
                with load_gate.lock:
                    # Observe again: the file may have moved while this caller
                    # waited, and the load that held the gate may already have
                    # cached the value for the token the file has now.
                    before = observe_freshness(Path(path))
                    entry = self._entries.get(key) if before.reusable else None
                    if entry is not None and entry[0] == before.token:
                        return entry[1]
                    loaded = loader()
                    if observe_freshness(Path(path)).token != before.token:
                        continue
                    if before.reusable:
                        self._entries.put(key, (before.token, loaded))
                    return loaded
            finally:
                with self._lock:
                    load_gate.participants -= 1
                    if load_gate.participants == 0 and self._load_locks.get(key) is load_gate:
                        del self._load_locks[key]
        raise SourceChangedError(f"{self._artifact_kind} changed on disk while loading: {path}")

    def __len__(self) -> int:
        """Return the number of retained cache entries."""
        return len(self._entries)

    def clear(self) -> None:
        """Drop cached entries; an active load keeps its gate."""
        self._entries.clear()
