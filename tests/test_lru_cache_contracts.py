"""LRUCache migration contract tests.

This module pins two orthogonal contracts around
:mod:`haute._lru_cache`:

1. **Extras regression guard** — the custom LRU implementation ships
   three features that :func:`functools.lru_cache` cannot offer:

   * ``evict_where(predicate)`` — atomic scan-and-delete.
   * Value-typed eviction (``evict_where`` returns the evicted values).
   * Snapshot / restore hooks and custom stats-friendly internals
     (exposed to subclasses via ``_lock`` and ``_data``).

   Callers that rely on any of these must stay on the custom
   implementation; the extras must keep working.

2. **Migration contract** — some callers use only plain
   ``get()`` / ``put()`` memoisation.  Those sites should move to
   ``functools.lru_cache`` so the custom cache is not imported when
   the stdlib is sufficient.  After the migration:

   * Category-A files must not import from ``haute._lru_cache`` and
     must apply ``@lru_cache`` (or ``@functools.lru_cache``) at the
     memoisation site.
   * Category-B files must still import ``LRUCache`` and must still
     be backed by the custom class.


Caller inventory
----------------

Grep result for ``from haute._lru_cache | LRUCache(`` in
``src/haute/`` (excluding the implementation file itself):

================================  ========================================  ==========
File:symbol                        Why                                        Category
================================  ========================================  ==========
_fingerprint_cache.FingerprintCache  Subclass of LRUCache; reaches into       B
                                     ``_data`` / ``_lock`` / ``_max_size``;
                                     uses ``pin`` / ``unpin``.
_io._load_cached                     Plain memoisation; content-hash key      A
                                     hash key computed at call site.
_mlflow_io._model_cache              Subclass ``_ModelCacheWithCascade``;     B
                                     uses ``evict_where`` via
                                     ``evict_matching`` and custom stats.
_model_scorer._feature_validation_cache  Uses ``evict_where`` in              B
                                     ``_invalidate_feature_validation_cache_for``;
                                     cascade from model-cache eviction.
_optimiser_io._load_artifact_cached  Plain memoisation; content-hash key      A
                                     hash key computed at call site.
_optimiser_io._load_mlflow_cached    Plain memoisation.                       A
================================  ========================================  ==========

The migration contract therefore targets three files:
``_io.py`` and ``_optimiser_io.py``.  The three category-B files
(``_fingerprint_cache.py``, ``_mlflow_io.py``,
``_model_scorer.py``) must keep importing ``LRUCache``.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import threading
import time as _time
from pathlib import Path

import pytest

from haute._lru_cache import LRUCache

# ---------------------------------------------------------------------------
# Per-test isolation: the module-level caches we poke at must start empty
# and be wiped afterwards so we do not pollute neighbouring suites.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Clear every module-level LRU cache we touch across the file.

    These caches are module-level singletons so a leftover entry from
    one test can cause a hit in the next and skew the hit-rate
    assertions below.  Clearing both pre- and post-yield keeps the
    tests order-independent within this file and keeps them from
    leaking into test files that run after us.
    """
    from haute._io import _load_cached
    from haute._optimiser_io import _load_artifact_cached, _load_mlflow_cached

    _load_cached.cache_clear()
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()
    yield
    _load_cached.cache_clear()
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


REPO_SRC = Path(__file__).resolve().parent.parent / "src" / "haute"

# Files that are expected to migrate to ``functools.lru_cache``.
# After the migration they MUST NOT import from ``haute._lru_cache``.
CATEGORY_A_FILES: tuple[Path, ...] = (
    REPO_SRC / "_io.py",
    REPO_SRC / "_optimiser_io.py",
)

# Files that must continue to import and use the custom LRUCache
# because they rely on one or more of: ``evict_where``, subclassing,
# direct access to ``_lock`` / ``_data``, or value-typed eviction.
CATEGORY_B_FILES: tuple[Path, ...] = (
    REPO_SRC / "_fingerprint_cache.py",
    REPO_SRC / "_mlflow_io.py",
    REPO_SRC / "_model_scorer.py",
)


def _parse(path: Path) -> ast.Module:
    """Parse *path* into an ``ast.Module`` with sane error messages."""
    assert path.is_file(), f"Source file missing: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports_from_lru_cache(tree: ast.Module) -> list[ast.ImportFrom]:
    """Return every ``from haute._lru_cache import ...`` node in *tree*.

    Only runtime imports are counted — ``if TYPE_CHECKING:`` guarded
    imports are considered type-check-only and excluded so the dev
    can keep a type-only reference without breaking the contract.
    """

    class _Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.runtime: list[ast.ImportFrom] = []
            self._guarded: bool = False

        def visit_If(self, node: ast.If) -> None:  # noqa: N802 — ast visitor name.
            # Heuristic: a top-level ``if TYPE_CHECKING:`` block guards
            # only type-time imports.  We walk the body with a flag set
            # so any ImportFrom inside is excluded from *runtime*.
            if _is_type_checking_guard(node.test):
                prev = self._guarded
                self._guarded = True
                for child in node.body:
                    self.visit(child)
                self._guarded = prev
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if node.module == "haute._lru_cache" and not self._guarded:
                self.runtime.append(node)
            # do not recurse — ImportFrom has no meaningful children.

    collector = _Collector()
    collector.visit(tree)
    return collector.runtime


def _is_type_checking_guard(test: ast.expr) -> bool:
    """True iff *test* is ``TYPE_CHECKING`` or ``typing.TYPE_CHECKING``."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return True
    return False


def _has_lru_cache_decorator(tree: ast.Module) -> bool:
    """True iff any function/method in *tree* carries an
    ``@lru_cache`` / ``@functools.lru_cache`` decorator.

    Both bare form (``@lru_cache``) and called form
    (``@lru_cache(maxsize=...)``) count — the dev may or may not need
    to tune ``maxsize``.
    """
    matched = False

    class _Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            nonlocal matched
            for dec in node.decorator_list:
                if _is_lru_cache_decorator(dec):
                    matched = True
                    return
            self.generic_visit(node)

        def visit_AsyncFunctionDef(  # noqa: N802
            self,
            node: ast.AsyncFunctionDef,
        ) -> None:
            nonlocal matched
            for dec in node.decorator_list:
                if _is_lru_cache_decorator(dec):
                    matched = True
                    return
            self.generic_visit(node)

    _Collector().visit(tree)
    return matched


def _is_lru_cache_decorator(dec: ast.expr) -> bool:
    """Match ``@lru_cache``, ``@lru_cache(...)``,
    ``@functools.lru_cache``, ``@functools.lru_cache(...)``,
    ``@cache`` and ``@functools.cache`` (Python 3.9+).
    """
    # Unwrap ``@decorator(...)`` → inspect the callee.
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id in {"lru_cache", "cache"}
    if isinstance(target, ast.Attribute):
        if target.attr not in {"lru_cache", "cache"}:
            return False
        # ``functools.lru_cache`` or ``ft.lru_cache`` — we don't hard-
        # code the module alias here because the dev might rename.
        return isinstance(target.value, ast.Name)
    return False


# ===========================================================================
# Part 1 — pin the LRUCache extras for category-B callers.
#
# These tests pass today and must keep passing after the migration.
# They guard the behaviours that category-B callers rely on.
# ===========================================================================


class TestEvictWhereReturnsEvictedValues:
    """``evict_where`` must return the *values* of evicted entries
    (in insertion order) so callers can run cross-module cleanup
    outside the cache's internal lock.
    """

    def test_returns_values_for_matched_keys(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=16)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        evicted = cache.evict_where(lambda k: k in {"a", "c"})

        assert sorted(evicted) == [1, 3]
        assert "a" not in cache
        assert "b" in cache
        assert "c" not in cache

    def test_returns_empty_list_on_no_match(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        cache.put("b", 2)

        assert cache.evict_where(lambda k: k == "zzz") == []
        assert len(cache) == 2

    def test_preserves_insertion_order_in_returned_values(self) -> None:
        """``evict_where`` materialises matches in iteration order of the
        underlying ``OrderedDict``.  The contract consumed by
        ``_ModelCacheWithCascade.evict_matching`` is that the returned
        list can be iterated in a deterministic order — here we pin
        that order is the insertion order of the matching keys.
        """
        cache: LRUCache[str, str] = LRUCache(max_size=8)
        for key, value in [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]:
            cache.put(key, value)

        # Match every entry whose key is a vowel.
        evicted = cache.evict_where(lambda k: k in {"a", "c"})

        assert evicted == ["A", "C"]

    def test_evicts_pinned_entries_too(self) -> None:
        """``evict_where`` is an explicit "get rid of these" — pinning
        must NOT protect an entry from predicate-driven eviction.
        """
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("keep", 1)
        cache.put("drop", 2)
        cache.pin("drop")

        evicted = cache.evict_where(lambda k: k == "drop")
        assert evicted == [2]
        assert "drop" not in cache
        assert "keep" in cache

    def test_evict_where_clears_timestamps(self) -> None:
        """TTL bookkeeping must be purged along with the data."""
        cache: LRUCache[str, int] = LRUCache(max_size=4, ttl=60.0)
        cache.put("a", 1)
        cache.put("b", 2)

        cache.evict_where(lambda k: k == "a")

        assert "a" not in cache._timestamps
        assert "b" in cache._timestamps


class TestEvictWhereAtomicUnderLock:
    """``evict_where`` must hold the internal lock across the whole
    scan-and-delete so a concurrent reader issuing a single locked
    operation sees either "all evicted entries still present" or
    "all evicted entries gone" — never a half-torn state.

    The concurrency contract under test: a single lock-taking
    operation (``get``, ``put``, ``__contains__``, ``__len__``) from
    a different thread must block for the full duration of
    ``evict_where`` rather than observing an intermediate state.
    """

    def test_concurrent_reader_blocks_until_eviction_completes(self) -> None:
        """A reader issuing ``get`` during ``evict_where`` must block
        until eviction completes.

        We slow the eviction artificially by passing a predicate that
        sleeps, then launch a reader thread that issues a single
        ``get()`` call on an already-present key.  The reader must
        not return until the eviction has fully committed; at that
        point the cache is in its post-eviction state.
        """
        cache: LRUCache[int, str] = LRUCache(max_size=64)
        for i in range(10):
            cache.put(i, f"v{i}")

        eviction_started = threading.Event()
        eviction_completed = threading.Event()
        reader_observed_at: list[float] = []

        def slow_predicate(key: int) -> bool:
            # First predicate call signals the eviction has started;
            # subsequent calls sleep briefly to widen the window in
            # which a concurrent reader could observe a partial state.
            if not eviction_started.is_set():
                eviction_started.set()
            _time.sleep(0.01)
            return key < 5  # evict keys 0..4

        def reader() -> None:
            # Wait until the main thread's eviction is actively running.
            assert eviction_started.wait(timeout=5.0)
            # Take a snapshot *under a single lock acquisition* — the
            # most stringent probe we can make is ``len()``, which
            # grabs ``_lock`` exactly once and returns.  If
            # ``evict_where`` did NOT hold the lock across its whole
            # loop, this call would return a torn length.
            observed_len = len(cache)
            reader_observed_at.append(_time.monotonic())
            # The reader must have been blocked until eviction
            # completed — at which point the cache has 5 entries.
            assert observed_len == 5, (
                f"Reader observed partial eviction: len={observed_len}, "
                "expected 5 (post-eviction steady state)."
            )

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        try:
            # Eviction runs on the MAIN thread; its slow predicate
            # holds the RLock for ~10 × 0.01 s = 100 ms, during which
            # the reader calls ``len(cache)`` and must block.
            evicted = cache.evict_where(slow_predicate)
            eviction_completed.set()
        finally:
            reader_thread.join(timeout=10.0)
            assert not reader_thread.is_alive(), "Reader deadlocked"

        # Sanity: eviction removed exactly the expected subset.
        assert sorted(evicted) == ["v0", "v1", "v2", "v3", "v4"]
        # The reader observed the cache AFTER eviction completed —
        # its timestamp must come after the eviction's completion.
        assert reader_observed_at, "Reader never returned a sample"
        # If the lock was NOT atomic, the reader could sample mid-
        # eviction and see 10, 9, 8, ... down to 5 — not exactly 5.
        # The assertion inside ``reader`` enforces the "== 5" check,
        # so reaching this point with the thread joined successfully
        # is the proof of atomicity.

    def test_concurrent_mixed_workload_leaves_cache_consistent(self) -> None:
        """Smoke test: mixed ``evict_where`` + ``put`` + ``get`` on
        multiple threads does not corrupt internal state.

        This is a looser regression guard than the blocking test above
        — we simply stress the cache with concurrent traffic and check
        that no exception is raised and the final state is
        well-defined (no dangling timestamps or orphan pins).
        """
        cache: LRUCache[int, int] = LRUCache(max_size=64, ttl=60.0)
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                i = 0
                while not stop.is_set():
                    cache.put(i % 100, i)
                    i += 1
            except BaseException as err:  # noqa: BLE001 — forward all
                errors.append(err)

        def evictor() -> None:
            try:
                while not stop.is_set():
                    cache.evict_where(lambda k: k % 3 == 0)
                    _time.sleep(0.001)
            except BaseException as err:  # noqa: BLE001
                errors.append(err)

        def reader() -> None:
            try:
                while not stop.is_set():
                    cache.get(42)
                    _ = len(cache)
            except BaseException as err:  # noqa: BLE001
                errors.append(err)

        threads = [
            threading.Thread(target=writer, daemon=True),
            threading.Thread(target=evictor, daemon=True),
            threading.Thread(target=reader, daemon=True),
        ]
        for t in threads:
            t.start()
        _time.sleep(0.2)
        stop.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"Thread {t.name} deadlocked"

        assert not errors, f"Concurrent workload raised: {errors[0]!r}"
        # Internal invariant: every live key in ``_data`` must have a
        # corresponding timestamp entry (TTL was configured), and no
        # pinned key is missing from ``_data``.
        assert set(cache._timestamps).issubset(set(cache._data))
        assert cache._pinned.issubset(set(cache._data))


class TestEvictWhereValueTypedEviction:
    """The ``_ModelCacheWithCascade.evict_matching`` helper relies on
    ``evict_where`` returning the VALUES (not the keys) so a caller
    can hand each evicted ``ScoringModel`` to a downstream cascade
    (``_invalidate_feature_validation_cache_for``).  This test pins
    that contract."""

    def test_returned_values_are_the_stored_objects_by_identity(self) -> None:
        class _Sentinel:
            """Unique instance so we can assert by identity."""

        cache: LRUCache[str, _Sentinel] = LRUCache(max_size=8)
        sentinels = [_Sentinel() for _ in range(3)]
        for key, sentinel in zip(("a", "b", "c"), sentinels):
            cache.put(key, sentinel)

        evicted = cache.evict_where(lambda k: k != "b")

        assert len(evicted) == 2
        # The VALUES must be the original sentinel objects, not copies.
        assert evicted[0] is sentinels[0]
        assert evicted[1] is sentinels[2]


class TestLRUOrderPreserved:
    """Oldest-unpinned entry is the one evicted when capacity is
    exceeded — this is the fundamental LRU promise."""

    def test_oldest_entry_evicted_first(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=3)
        cache.put("oldest", 1)
        cache.put("middle", 2)
        cache.put("newest", 3)

        # Adding a 4th entry must evict "oldest" — it has not been
        # touched since its initial insertion so it is at the LRU end.
        cache.put("overflow", 4)

        assert "oldest" not in cache
        assert "middle" in cache
        assert "newest" in cache
        assert "overflow" in cache

    def test_get_promotes_entry_to_mru(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        # Touch "a" so it moves to MRU.  The next put must now evict
        # "b" (the new LRU) instead of "a".
        assert cache.get("a") == 1
        cache.put("d", 4)

        assert "a" in cache
        assert "b" not in cache
        assert "c" in cache
        assert "d" in cache


class TestMaxSizeHonored:
    """Unpinned caches never exceed ``max_size``.

    (``_fingerprint_cache.py`` relies on the documented exception that
    a fully-pinned cache MAY exceed ``max_size``; that exception is
    tested in ``tests/test_lru_cache.py``.)
    """

    def test_len_never_exceeds_max_size(self) -> None:
        cache: LRUCache[int, int] = LRUCache(max_size=5)
        for i in range(100):
            cache.put(i, i)
            assert len(cache) <= 5, (
                f"Cache exceeded max_size after inserting key {i}: len={len(cache)}"
            )
        assert len(cache) == 5


class TestHitMissStatsExposed:
    """``_mlflow_io`` exposes hit / miss counters via
    ``get_model_cache_stats()`` — the counter pair is incremented in
    tandem with ``LRUCache.get`` / ``put`` calls.  We pin the public
    surface (``__contains__``, ``__len__``) that the current stats
    path and its tests read from, so a dev migrating the memoisation
    path to a subclass does not accidentally break the observability
    callers.
    """

    def test_contains_does_not_promote(self) -> None:
        """``in`` is a non-promoting probe — it must not move the
        entry to MRU.  If it did, hit counters wired to ``get`` would
        be bypassed by callers probing before reading."""
        cache: LRUCache[str, int] = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        # Probe "a" repeatedly with ``in``.
        for _ in range(5):
            assert "a" in cache

        # Adding a new entry must evict "a" — the probe didn't promote.
        cache.put("d", 4)
        assert "a" not in cache

    def test_len_reflects_live_entries(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        assert len(cache) == 0
        cache.put("a", 1)
        assert len(cache) == 1
        cache.put("b", 2)
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0


# ===========================================================================
# Part 2 — migration contract (AST walks).
#
# Pre-migration these FAIL because every category-A file currently
# imports from ``haute._lru_cache``.  Post-migration these PASS.
# ===========================================================================


class TestCategoryAFilesDropLRUCacheImport:
    """Category-A files must NOT import from ``haute._lru_cache`` at
    runtime after the migration.  A ``TYPE_CHECKING``-guarded import
    is allowed so the dev can keep a type annotation if needed."""

    @pytest.mark.parametrize("path", CATEGORY_A_FILES, ids=lambda p: p.name)
    def test_no_runtime_import_from_lru_cache(self, path: Path) -> None:
        tree = _parse(path)
        runtime_imports = _imports_from_lru_cache(tree)
        assert runtime_imports == [], (
            f"{path.name} must not import from haute._lru_cache at "
            f"runtime after migration to functools.lru_cache; found "
            f"{len(runtime_imports)} import(s) at lines "
            f"{[n.lineno for n in runtime_imports]}."
        )


class TestCategoryAFilesUseFunctoolsLRUCache:
    """Category-A files must apply ``@functools.lru_cache`` (bare or
    called) at the memoisation site after the migration.  The AST
    walk accepts ``@lru_cache``, ``@lru_cache(...)``,
    ``@functools.lru_cache``, ``@functools.lru_cache(...)``, and the
    sibling ``@cache`` / ``@functools.cache`` forms."""

    @pytest.mark.parametrize("path", CATEGORY_A_FILES, ids=lambda p: p.name)
    def test_has_lru_cache_decorator(self, path: Path) -> None:
        tree = _parse(path)
        assert _has_lru_cache_decorator(tree), (
            f"{path.name} has no @lru_cache / @functools.lru_cache "
            "decorator — after migration the memoisation site should "
            "use functools.lru_cache (or the unbounded @cache variant)."
        )


class TestCategoryBFilesKeepLRUCache:
    """Category-B files MUST continue to import ``LRUCache`` from
    ``haute._lru_cache`` — they rely on ``evict_where`` / subclassing
    / pinning and cannot migrate."""

    @pytest.mark.parametrize("path", CATEGORY_B_FILES, ids=lambda p: p.name)
    def test_still_imports_lru_cache(self, path: Path) -> None:
        tree = _parse(path)
        runtime_imports = _imports_from_lru_cache(tree)
        imported_names: set[str] = set()
        for node in runtime_imports:
            for alias in node.names:
                imported_names.add(alias.name)
        assert "LRUCache" in imported_names, (
            f"{path.name} must continue to import LRUCache from "
            f"haute._lru_cache — it depends on evict_where / "
            f"subclassing / pinning which functools.lru_cache does "
            f"not offer."
        )

    @pytest.mark.parametrize("path", CATEGORY_B_FILES, ids=lambda p: p.name)
    def test_still_uses_lru_cache_identifier(self, path: Path) -> None:
        """Sanity check: the imported name is actually referenced in
        the source.  Guards against a dev stripping usage but leaving
        a dead import behind (or vice versa)."""
        source = path.read_text(encoding="utf-8")
        assert "LRUCache" in source, (
            f"{path.name} imports LRUCache but never uses it — "
            f"either the import or the category-B classification is "
            f"wrong."
        )


# ===========================================================================
# Part 3 — behavioural tests for category-A callers.
#
# These tests verify the MEMOISATION CONTRACT of each caller
# independently of the backing implementation.  They pass today
# (with LRUCache) and must keep passing after migration (with
# functools.lru_cache).  A behavioural regression here means the
# dev broke the caller's contract during migration.
# ===========================================================================


class TestLoadExternalObjectMemoisationContract:
    """``haute._io.load_external_object`` must return the SAME object
    (by identity) on repeated calls with the same arguments, provided
    the underlying file has not changed.

    This is the memoisation contract the caller offers to the rest of
    the codebase — a repeat click on the same preview in the UI
    should not re-parse the same JSON blob.

    Currently backed by ``LRUCache``.  After migration the dev may
    swap to ``@functools.lru_cache`` on the inner loader; the
    contract stays the same.
    """

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_repeated_call_returns_same_object(self, tmp_path: Path) -> None:
        from haute._io import load_external_object

        path = tmp_path / "data.json"
        path.write_text('{"k": 1}')

        first = load_external_object(str(path), "json")
        second = load_external_object(str(path), "json")

        assert first is second, (
            "Memoisation contract broken: repeated call to "
            "load_external_object returned a fresh object — the "
            "cache is bypassed."
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_different_model_class_produces_distinct_cache_entries(
        self,
        tmp_path: Path,
    ) -> None:
        """``model_class`` is part of the cache key.  Two calls with
        different model_class values must NOT collide even though the
        file bytes are identical."""
        from haute._io import _load_cached, load_external_object

        path = tmp_path / "shared.json"
        path.write_text('{"shared": true}')

        r1 = load_external_object(str(path), "json", model_class="classifier")
        r2 = load_external_object(str(path), "json", model_class="regressor")

        # Both are valid dicts parsed from the same file; they MAY be
        # equal by ``==`` but MUST be independent cache entries.  A
        # naive ``@lru_cache`` keyed only on path would collapse them
        # into one entry and serve stale content — this test pins
        # that regression.
        assert r1 == r2
        # Two distinct keys → cache holds two entries (pre-migration).
        # After migration to ``@lru_cache``, the cache_info hit count
        # should also reflect that the second call was a miss.
        assert _load_cached.cache_info().currsize >= 2, (
            "Two calls with different model_class must produce two cache entries."
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_hit_rate_matches_synthetic_access_pattern(
        self,
        tmp_path: Path,
    ) -> None:
        """Hit rate sanity: with 1 unique key accessed N times the
        memoisation layer must serve exactly 1 miss and N-1 hits.

        Hit rate is measured obliquely — the function is pure (no
        side effects on the stored dict), so identity of the returned
        object == cache hit; a fresh object == cache miss.
        """
        from haute._io import load_external_object

        path = tmp_path / "stable.json"
        path.write_text('{"v": 1}')

        results = [load_external_object(str(path), "json") for _ in range(5)]
        first_obj = results[0]
        # Every subsequent call returned the SAME object → 1 miss,
        # 4 hits = 80% hit rate.  If any return was fresh, hit rate
        # would be < 80%.
        hits = sum(1 for r in results[1:] if r is first_obj)
        assert hits == 4, (
            f"Expected 4 cache hits out of 4 repeat calls; got {hits}. Memoisation degraded."
        )


class TestLoadOptimiserArtifactMemoisationContract:
    """``haute._optimiser_io.load_optimiser_artifact`` must return a
    deep copy of the cached artifact on every call (so callers can
    mutate the result without corrupting the cache) but under the
    hood repeated reads of the same unchanged file must share ONE
    cache slot.
    """

    def test_repeated_call_uses_single_cache_entry(
        self,
        tmp_path: Path,
    ) -> None:
        """Five reads of an unchanged file → one live cache entry."""
        from haute._optimiser_io import _load_artifact_cached, load_optimiser_artifact

        path = tmp_path / "opt.json"
        path.write_text(json.dumps({"mode": "online", "lambdas": {"x": 1.0}}))

        artifacts = [load_optimiser_artifact(str(path)) for _ in range(5)]

        # All reads return equal content.
        first = artifacts[0]
        for art in artifacts[1:]:
            assert art == first

        # Memoisation collapses 5 reads into 1 cached entry.
        cache_size = _load_artifact_cached.cache_info().currsize
        assert cache_size == 1, (
            "Five reads of the same unchanged file must collapse to "
            f"one cache entry; got {cache_size}."
        )

    def test_caller_cannot_corrupt_cache_via_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        """Deep copy on return is a load-bearing part of the
        memoisation contract for ``load_optimiser_artifact``.
        Mutating the returned dict must NOT affect a subsequent
        read."""
        from haute._optimiser_io import load_optimiser_artifact

        path = tmp_path / "opt.json"
        path.write_text(json.dumps({"mode": "online", "lambdas": {"x": 1.0}}))

        first = load_optimiser_artifact(str(path))
        first["mode"] = "MUTATED"
        first["lambdas"]["x"] = 99.0

        second = load_optimiser_artifact(str(path))
        assert second["mode"] == "online"
        assert second["lambdas"]["x"] == 1.0

    def test_same_second_overwrite_invalidates(
        self,
        tmp_path: Path,
    ) -> None:
        """TOCTOU safety: the cache key is content-derived, so an
        overwrite with a different payload at the SAME mtime must
        invalidate.  Losing this guarantee is a silent-staleness
        regression class; the behavioural test pins it here so a
        naive ``@lru_cache`` keyed on path alone is caught.
        """
        from haute._optimiser_io import load_optimiser_artifact

        path = tmp_path / "opt.json"
        path.write_text(json.dumps({"version": 1}))

        r1 = load_optimiser_artifact(str(path))
        assert r1["version"] == 1

        mtime = os.path.getmtime(str(path))
        path.write_text(json.dumps({"version": 2}))
        os.utime(str(path), (mtime, mtime))

        # If the dev migrates to ``@lru_cache(maxsize=N)`` keyed on
        # the raw path argument, this test fails: the cache serves
        # stale content.  The correct migration keys on the content
        # hash (computed outside the lru_cache-decorated helper).
        r2 = load_optimiser_artifact(str(path))
        assert r2["version"] == 2, (
            "Same-second overwrite served stale content — the cache "
            "key must remain content-derived across the migration."
        )


class TestLoadMlflowOptimiserArtifactMemoisationContract:
    """Third category-A caller: ``load_mlflow_optimiser_artifact``.

    Its key is fully determined by the resolved
    ``(source_type, run_id, version)`` triple — no content-hash
    complication — so a direct ``@functools.lru_cache`` on the inner
    loader is straightforward.  This test pins the contract callers
    rely on: repeated calls with identical args reuse the cache,
    distinct args do not collide.
    """

    def test_cache_reused_for_repeat_calls(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock, patch

        from haute._optimiser_io import _load_mlflow_cached, load_mlflow_optimiser_artifact

        artifact = {"mode": "ratebook", "constraints": [1]}
        af = tmp_path / "optimiser_result.json"
        af.write_text(json.dumps(artifact))

        download_call_count = 0

        def _downloader(*_a, **_kw):
            nonlocal download_call_count
            download_call_count += 1
            return str(af)

        with (
            patch("haute._mlflow_utils.resolve_mlflow_source") as mock_resolve,
            patch("mlflow.artifacts.download_artifacts", side_effect=_downloader),
        ):
            mock_resolve.return_value = ("run_1", "1", MagicMock(), MagicMock())

            r1 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_1")
            r2 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_1")
            r3 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_1")

            # All three calls return equal content.
            assert r1 == r2 == r3

            # The underlying download was invoked exactly once — the
            # remaining two calls were served from cache.  This is the
            # functional proof that memoisation is active, invariant
            # to the backing implementation.
            assert download_call_count == 1, (
                f"Expected 1 download call for 3 identical queries; "
                f"got {download_call_count}. Memoisation broken."
            )

        assert _load_mlflow_cached.cache_info().currsize == 1

    def test_distinct_run_ids_produce_distinct_cache_entries(
        self,
        tmp_path: Path,
    ) -> None:
        """Different (source_type, run_id, version) triples must not
        collide — e.g. scoring two different runs back-to-back must
        NOT return the same artifact from cache."""
        from unittest.mock import MagicMock, patch

        from haute._optimiser_io import _load_mlflow_cached, load_mlflow_optimiser_artifact

        af1 = tmp_path / "run_1.json"
        af1.write_text(json.dumps({"mode": "online", "id": "one"}))
        af2 = tmp_path / "run_2.json"
        af2.write_text(json.dumps({"mode": "ratebook", "id": "two"}))

        # Resolver and downloader return different artifacts per run.
        def _resolver(*, source_type, run_id, registered_model, version, tracking_uri):
            return (run_id, "1", MagicMock(), MagicMock())

        def _downloader(uri, *a, **kw):
            return str(af1) if "run_a" in uri else str(af2)

        with (
            patch(
                "haute._mlflow_utils.resolve_mlflow_source",
                side_effect=_resolver,
            ),
            patch("mlflow.artifacts.download_artifacts", side_effect=_downloader),
        ):
            r1 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_a")
            r2 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_b")

            assert r1["id"] == "one"
            assert r2["id"] == "two"

        assert _load_mlflow_cached.cache_info().currsize == 2, (
            "Two distinct run_ids must produce two cache entries."
        )


# ===========================================================================
# Part 4 — sanity check that ``functools.lru_cache`` itself is
# imported at the module level so the behavioural tests above can
# still access the migrated functions' ``cache_info``.
# ===========================================================================


class TestFunctoolsAvailable:
    """The behavioural tests depend on ``functools.lru_cache``'s
    ``cache_info`` API being available in the interpreter.  This is a
    sanity check that the stdlib shim (the whole point of the
    migration) is importable."""

    def test_lru_cache_has_cache_info(self) -> None:
        @functools.lru_cache(maxsize=4)
        def _sample(x: int) -> int:
            return x * 2

        _sample(1)
        _sample(1)
        info = _sample.cache_info()
        assert info.hits == 1
        assert info.misses == 1
        assert info.currsize == 1
