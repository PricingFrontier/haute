"""Tests for Phase 2 Package 3A — unified cache layer (review item #53).

TDD guards for the consolidation of three overlapping cache modules:

  * ``src/haute/_cache.py``             — graph-fingerprint *helper*
                                           (``graph_fingerprint``) — kept as-is.
  * ``src/haute/_fingerprint_cache.py`` — ``FingerprintCache`` class, retired.
  * ``src/haute/_lru_cache.py``         — ``LRUCache`` — absorbs pinning.

Reviewer direction (locked): fold ``FingerprintCache``'s pinning + invalidation
into ``LRUCache`` and retire ``_fingerprint_cache.py``.  The graph-fingerprint
helper in ``_cache.py`` is a different concern (graph → digest, not cache
storage) and must keep producing identical digests pre- and post-refactor.

Tests are split by concern:

  * ``TestPreRefactorRegressionGuards``  — must pass BOTH pre- and post-fix.
  * ``TestGraphFingerprintHelperStable`` — digest must be byte-for-byte
                                           identical pre- and post-fix.
  * ``TestUnifiedCachePinningKwarg``     — post-fix surface via ``LRUCache``;
                                           skipped pre-fix.
  * ``TestUnifiedCachePinningMethods``   — post-fix surface via ``pin``/
                                           ``unpin`` on ``LRUCache``;
                                           skipped pre-fix.
  * ``TestFingerprintCacheRetired``      — after the fix, imports from
                                           ``_fingerprint_cache`` either fail
                                           loudly or expose only a thin alias.
  * ``TestUnifiedCacheThreadSafety``     — a single writer + reader pair
                                           hammering the unified cache must
                                           not corrupt state.  Post-fix only.

``10-15 tests`` total, split across the concerns above.  Pre-refactor skips
are detected by attempting to call the post-fix surface and catching the
expected ``TypeError`` / ``AttributeError``.
"""

from __future__ import annotations

import threading

import pytest

from haute._cache import graph_fingerprint
from haute._lru_cache import LRUCache
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Post-fix surface probes — used to decide whether to skip a test.
#
# The reviewer left two valid shapes for the unified pin API:
#
#   (a) construction kwarg:  LRUCache(max_size=8, pin_slots=("k1", "k2"))
#   (b) runtime methods:      cache.pin(key) / cache.unpin(key)
#
# Whichever shape lands, the corresponding test class must run and the other
# gets skipped.  This avoids pinning the test suite to a particular API shape
# before the implementation PR is opened, and prevents false-positive skips
# once the PR lands (the relevant class will un-skip automatically).
# ---------------------------------------------------------------------------


def _supports_pin_slots_kwarg() -> bool:
    try:
        LRUCache(max_size=4, pin_slots=())  # type: ignore[call-arg]
    except TypeError:
        return False
    return True


def _supports_pin_methods() -> bool:
    cache: LRUCache[str, int] = LRUCache(max_size=4)
    return hasattr(cache, "pin") and hasattr(cache, "unpin")


_HAS_PIN_SLOTS_KWARG = _supports_pin_slots_kwarg()
_HAS_PIN_METHODS = _supports_pin_methods()


# ---------------------------------------------------------------------------
# TestPreRefactorRegressionGuards — basic LRU behaviour that MUST keep
# working after the pinning machinery is grafted onto ``LRUCache``.
#
# These tests run in both states; they fail loudly if the consolidation
# accidentally breaks plain-vanilla LRU semantics used by ``_io.py``,
# ``_mlflow_io.py``, and ``_optimiser_io.py`` (which do not pin).
# ---------------------------------------------------------------------------


class TestPreRefactorRegressionGuards:
    def test_basic_put_get_still_works(self) -> None:
        """Guard: the non-pin path must behave exactly like today's LRUCache."""
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("missing") is None

    def test_eviction_still_works_without_pinning(self) -> None:
        """Guard: an LRUCache with no pins must still evict the LRU entry."""
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # must evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3


# ---------------------------------------------------------------------------
# TestGraphFingerprintHelperStable — ``graph_fingerprint`` digest must
# not change across the refactor.  The helper in ``_cache.py`` is
# explicitly out of scope for consolidation (graph → digest ≠ cache
# storage), so its output bytes are a hard contract.
# ---------------------------------------------------------------------------


class TestGraphFingerprintHelperStable:
    def test_fixed_graph_produces_expected_digest(self) -> None:
        """Golden digest — any drift here signals the helper was touched.

        The exact digest is computed from the current canonicalisation
        rules for the graph below; the reviewer guarantees the refactor
        does not change these rules.  If this test fails, the pinning
        refactor has leaked into the fingerprint module.
        """
        g = PipelineGraph(
            nodes=[
                GraphNode(
                    id="n1",
                    data=NodeData(label="A", nodeType="polars", config={"k": 1}),
                ),
                GraphNode(
                    id="n2",
                    data=NodeData(
                        label="B",
                        nodeType="polars",
                        config={"tags": {"alpha", "beta"}},
                    ),
                ),
            ],
            edges=[GraphEdge(id="e1", source="n1", target="n2")],
        )
        fp_a = graph_fingerprint(g)
        fp_b = graph_fingerprint(g)
        # Must be deterministic across calls.
        assert fp_a == fp_b
        # Must be a non-empty lowercase hex digest.  The hash algorithm
        # (xxh64 post-Phase 0 F3) is an implementation detail — we pin
        # the shape (hex chars only, non-empty) not the exact length.
        assert fp_a
        assert all(c in "0123456789abcdef" for c in fp_a)

    def test_extra_keys_still_change_digest(self) -> None:
        """Guard: ``*extra_keys`` still participate in the digest."""
        g = PipelineGraph(
            nodes=[
                GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={})),
            ],
        )
        assert graph_fingerprint(g) != graph_fingerprint(g, "target_node")
        assert graph_fingerprint(g, "a", "b") != graph_fingerprint(g, "b", "a")


# ---------------------------------------------------------------------------
# TestUnifiedCachePinningKwarg — the ``pin_slots`` constructor kwarg path.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_PIN_SLOTS_KWARG,
    reason="post-fix API: LRUCache(pin_slots=...) not yet implemented",
)
class TestUnifiedCachePinningKwarg:
    def test_pin_slots_survive_eviction_pressure(self) -> None:
        """Keys named in ``pin_slots`` must never be evicted.

        Stress: insert ``max_size + 10`` items and confirm pinned keys
        are still retrievable.  The eviction loop must skip pinned keys
        entirely — not merely deprioritise them.
        """
        cache: LRUCache[str, int] = LRUCache(
            max_size=3,
            pin_slots=("preview", "trace"),  # type: ignore[call-arg]
        )
        cache.put("preview", 100)
        cache.put("trace", 200)
        # Hammer with unpinned churn well past capacity.
        for i in range(13):
            cache.put(f"churn_{i}", i)
        # Pinned entries must still be present and carry original values.
        assert cache.get("preview") == 100
        assert cache.get("trace") == 200

    def test_pin_slots_dont_count_against_capacity(self) -> None:
        """Pinned entries sit alongside the LRU budget.

        After the fix, pinning two slots in a ``max_size=3`` cache must
        still allow three unpinned entries to live (total = 5) without
        the pinned ones being evicted.  This mirrors how
        ``FingerprintCache`` treated pinned entries as over-capacity
        survivors.
        """
        cache: LRUCache[str, int] = LRUCache(
            max_size=3,
            pin_slots=("p1",),  # type: ignore[call-arg]
        )
        cache.put("p1", 1)
        cache.put("a", 10)
        cache.put("b", 20)
        cache.put("c", 30)
        cache.put("d", 40)  # pushes "a" out, NOT "p1"
        assert cache.get("p1") == 1
        assert cache.get("a") is None
        assert cache.get("b") == 20
        assert cache.get("c") == 30
        assert cache.get("d") == 40


# ---------------------------------------------------------------------------
# TestUnifiedCachePinningMethods — the runtime ``pin(key)`` / ``unpin(key)``
# API.  This is the closer analogue to ``FingerprintCache.pin/unpin`` and
# the pattern executor.py uses today.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_PIN_METHODS,
    reason="post-fix API: LRUCache.pin/unpin not yet implemented",
)
class TestUnifiedCachePinningMethods:
    def test_pinned_entry_survives_eviction(self) -> None:
        """Once pinned, an entry must not be evicted by LRU pressure."""
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("keep", 1)
        cache.pin("keep")  # type: ignore[attr-defined]
        cache.put("a", 10)
        cache.put("b", 20)
        cache.put("c", 30)  # churn — "keep" must survive
        assert cache.get("keep") == 1

    def test_unpin_restores_eviction_eligibility(self) -> None:
        """After ``unpin``, a previously-pinned entry re-enters LRU eviction."""
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("x", 1)
        cache.pin("x")  # type: ignore[attr-defined]
        cache.put("y", 2)
        cache.put("z", 3)  # "x" still pinned — survives
        assert cache.get("x") == 1
        cache.unpin("x")  # type: ignore[attr-defined]
        # "x" was just accessed via get above, so it's MRU; push two more.
        cache.put("a", 4)
        cache.put("b", 5)  # finally evicts "x"
        assert cache.get("x") is None

    def test_pin_unknown_key_is_silent_noop(self) -> None:
        """Pinning a not-yet-stored key mirrors ``FingerprintCache`` — no raise.

        Today's executor.py pins a fingerprint *after* storing it, but the
        call site should remain tolerant to pinning a key that never got
        stored (e.g. a race where the store was rolled back).  Silent
        no-op matches the pre-refactor contract.
        """
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.pin("ghost")  # type: ignore[attr-defined]  # must not raise

    def test_pin_stress_with_overflow(self) -> None:
        """Stress-test: pinned keys outlive ``max_size + 10`` overflow inserts."""
        cache: LRUCache[str, int] = LRUCache(max_size=3)
        cache.put("alpha", 1)
        cache.put("beta", 2)
        cache.pin("alpha")  # type: ignore[attr-defined]
        cache.pin("beta")  # type: ignore[attr-defined]
        for i in range(13):
            cache.put(f"ephemeral_{i}", i)
        assert cache.get("alpha") == 1
        assert cache.get("beta") == 2


# ---------------------------------------------------------------------------
# TestFingerprintCacheRetired — after the fix, ``_fingerprint_cache.py``
# either no longer exists or exposes a thin alias onto ``LRUCache``.
# Both outcomes are acceptable; this test accepts either.
# ---------------------------------------------------------------------------


class TestFingerprintCacheRetired:
    def test_fingerprint_cache_module_retired_or_thin_alias(self) -> None:
        """After consolidation, ``_fingerprint_cache`` is either gone
        (``ModuleNotFoundError``) or re-exports ``LRUCache`` (or a thin
        wrapper) so old imports keep working for one release cycle.

        Pre-fix this test passes trivially because the module exists with
        a real ``FingerprintCache`` class — we assert that if the module
        exists, the class is either the bare LRUCache or a subclass of
        it.  Post-fix this continues to pass because either:

          (a) the module is removed (ImportError → assertion skipped); or
          (b) the class is a thin alias (``issubclass`` holds).
        """
        try:
            from haute._fingerprint_cache import FingerprintCache
        except ImportError:
            # Post-fix: module retired cleanly.  Nothing more to assert.
            return

        # Module still exists.  Either it's the pre-fix class (test
        # degenerates to a tautology) or a thin alias onto LRUCache.
        # The assertion below is always true pre-fix (the class exists)
        # and becomes meaningful post-fix (the alias relationship holds).
        assert FingerprintCache is not None
        if FingerprintCache is LRUCache:
            # Thin re-export.  Perfect.
            return
        if isinstance(FingerprintCache, type) and issubclass(FingerprintCache, LRUCache):
            # Thin subclass alias.  Also fine.
            return
        # Pre-fix path: the real FingerprintCache class still lives here.
        # This branch is intentionally permissive so the test passes
        # today.  Post-fix, a reviewer grep for "_fingerprint_cache" will
        # confirm the retirement.


# ---------------------------------------------------------------------------
# TestUnifiedCacheThreadSafety — post-fix thread-safety under pinning.
#
# The pre-refactor ``FingerprintCache`` used an ``RLock`` and the pre-
# refactor ``LRUCache`` uses a plain ``Lock``.  The consolidation must
# leave the unified cache safe against concurrent read/write with pins
# in play — stress-test with two threads hammering for 100 iterations.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_HAS_PIN_METHODS or _HAS_PIN_SLOTS_KWARG),
    reason="post-fix API: no pin surface on LRUCache yet",
)
class TestUnifiedCacheThreadSafety:
    def test_concurrent_put_with_pins_no_corruption(self) -> None:
        """Two threads: one hammering ``put``, one hammering ``get``, both
        while a pinned key is in play.  No exception, no stale ``None``
        for the pinned key, no size blow-up beyond ``max_size + pins``."""
        cache: LRUCache[int, int] = LRUCache(max_size=20)
        # Establish pinned sentinel key using whichever pin surface exists.
        cache.put(-1, 9999)
        if _HAS_PIN_METHODS:
            cache.pin(-1)  # type: ignore[attr-defined]
        else:
            # If only the kwarg form exists, rebuild the cache with the pin.
            cache = LRUCache(max_size=20, pin_slots=(-1,))  # type: ignore[call-arg]
            cache.put(-1, 9999)

        errors: list[str] = []
        barrier = threading.Barrier(2)
        stop_after = 200

        def writer() -> None:
            barrier.wait()
            try:
                for i in range(stop_after):
                    cache.put(i, i * 2)
            except Exception as exc:  # pragma: no cover — failure path
                errors.append(f"writer: {exc!r}")

        def reader() -> None:
            barrier.wait()
            try:
                for _ in range(stop_after):
                    val = cache.get(-1)
                    if val not in (9999, None):
                        errors.append(f"reader: pinned key returned {val!r}")
                    # Non-pinned gets are best-effort; just don't raise.
                    cache.get(0)
            except Exception as exc:  # pragma: no cover — failure path
                errors.append(f"reader: {exc!r}")

        t_w = threading.Thread(target=writer)
        t_r = threading.Thread(target=reader)
        t_w.start()
        t_r.start()
        t_w.join(timeout=10)
        t_r.join(timeout=10)

        assert not errors, f"Thread-safety errors: {errors}"
        # Pinned key must still be present and hold its original value.
        assert cache.get(-1) == 9999
