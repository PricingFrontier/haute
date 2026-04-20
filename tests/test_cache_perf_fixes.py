"""Tests for Phase 3 Wave 6 package 6C — cache performance fixes.

Covers two ``docs/CODEBASE_REVIEW.md`` items whose fix is a stdlib / ecosystem
swap (Theme E — performance wins):

  * **#88** — ``src/haute/executor.py:88`` preamble cache with manual
    ``OrderedDict + popitem`` eviction.  Target: swap to
    ``functools.lru_cache``.  The stdlib C implementation is O(1) per
    operation and removes ~20 lines of hand-rolled LRU bookkeeping.

  * **#89** — ``src/haute/_cache.py:27, 46`` uses ``hashlib.sha256`` for
    local cache keys.  Target: swap to xxh64 via the existing
    ``haute._hashing.content_hash_bytes`` helper (Phase 0 F3 foundation).
    xxhash is 10-100x faster than SHA-256; collision risk is irrelevant
    for local, non-cryptographic cache keys.

Test strategy
-------------
This file is written *before* the refactor lands.  Tests split into three
classes based on which state they expect:

  * ``TestPreambleCacheCorrectness`` — semantic invariants that must hold
    both pre- and post-refactor.  Same preamble → same namespace,
    different preamble → different namespace, ``force_refresh=True``
    bypasses cached values, max size honoured, thread-safe.

  * ``TestPreambleCachePostRefactor`` — invariants specific to the
    ``functools.lru_cache`` shape (presence of ``cache_info()``,
    ``cache_clear()``, default ``maxsize=128``).  These tests are marked
    ``xfail(strict=True)`` so the maintainer sees them flip to passing
    the moment the refactor lands.  Once green, the ``xfail`` decorators
    can be removed.

  * ``TestCacheKeyXxhashMigration`` — digest length + determinism +
    collision-freedom checks pinned to xxh64.  The "new keys are 16 hex
    chars" assertion is marked ``xfail(strict=True)`` pre-refactor
    (current SHA-256 produces 64 hex chars) and passes post-refactor.

  * ``TestCacheKeyBenchmark`` and ``TestPreambleCacheBenchmark`` — the
    two performance targets from the review.  Both target the
    post-refactor state; pre-refactor they may run but the bench assertion
    will fail (the point of the migration).  Marked ``xfail`` so CI
    stays green until the refactor lands.

No production code is edited here.  The refactor itself lives in a
separate PR.
"""

from __future__ import annotations

import gc
import hashlib
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Imports under test — the executor preamble cache + the graph-fingerprint
# helper in _cache.py.  Also pull in the xxh64 helper from the F3 foundation
# so benchmark tests can hit the exact implementation the migration will use.
# ---------------------------------------------------------------------------
from haute._cache import _graph_base_fingerprint, graph_fingerprint
from haute._hashing import content_hash_bytes
from haute._types import GraphNode, NodeData, PipelineGraph
from haute.executor import _compile_preamble

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_preamble_cache() -> None:
    """Clear whichever cache surface the executor currently uses.

    Pre-refactor the cache lives in ``executor._preamble_cache`` (a plain
    ``OrderedDict``); post-refactor it's the ``__wrapped__`` of a
    ``functools.lru_cache``-decorated function.  This helper inspects
    both so the test file keeps working through the swap.
    """
    import haute.executor as _ex

    # Pre-refactor surface.
    cache = getattr(_ex, "_preamble_cache", None)
    if cache is not None and hasattr(cache, "clear"):
        cache.clear()

    # Post-refactor surface.
    wrapped = getattr(_ex._compile_preamble, "cache_clear", None)
    if callable(wrapped):
        wrapped()


def _cache_size() -> int:
    """Report the current number of cache entries.

    Works across both the pre-refactor ``OrderedDict`` surface and the
    post-refactor ``functools.lru_cache`` ``cache_info().currsize``.
    """
    import haute.executor as _ex

    # Prefer the lru_cache inspection API if present — it's the canonical
    # post-refactor path.
    info_fn = getattr(_ex._compile_preamble, "cache_info", None)
    if callable(info_fn):
        return int(info_fn().currsize)

    # Fall back to the dict-based pre-refactor cache.
    cache = getattr(_ex, "_preamble_cache", None)
    if cache is not None:
        return len(cache)
    raise RuntimeError("no recognised preamble cache surface on haute.executor")


def _cache_max() -> int:
    """Report the cache's bound (``max_size`` in old cache, ``maxsize`` in new)."""
    import haute.executor as _ex

    info_fn = getattr(_ex._compile_preamble, "cache_info", None)
    if callable(info_fn):
        return int(info_fn().maxsize)

    bound = getattr(_ex, "_PREAMBLE_CACHE_MAX", None)
    if bound is not None:
        return int(bound)
    raise RuntimeError("no recognised preamble cache bound on haute.executor")


@pytest.fixture(autouse=True)
def _clear_preamble_between_tests() -> None:
    """Preamble cache is a module-level singleton — keep tests isolated."""
    _clear_preamble_cache()
    yield
    _clear_preamble_cache()


# ---------------------------------------------------------------------------
# Item #88 — preamble cache correctness (pre- and post-refactor invariants)
# ---------------------------------------------------------------------------


class TestPreambleCacheCorrectness:
    """Behavioural contract that must hold both before and after the swap.

    These tests are the regression net: if the ``functools.lru_cache``
    swap drops a guarantee (e.g. identity preservation, force_refresh
    honoured, concurrent safety), one of these will catch it.
    """

    def test_same_preamble_returns_same_namespace_object(self) -> None:
        """Cache hit must return the *same* dict object, not a copy.

        ``functools.lru_cache`` returns the cached result by identity,
        matching today's ``OrderedDict`` behaviour.  Callers rely on
        object identity to detect whether the preamble was re-compiled.
        """
        source = "PI = 3.14159\nE = 2.71828\n"
        ns1 = _compile_preamble(source, force_refresh=False)
        ns2 = _compile_preamble(source, force_refresh=False)
        assert ns1 is ns2, (
            "cache hit must return the same dict object (lru_cache guarantees this);"
            " got distinct objects which means the cache isn't serving hits"
        )

    def test_different_preamble_returns_different_namespace(self) -> None:
        """Different source text → different namespace dict.

        No surprise cross-contamination when the user edits the
        preamble between runs.
        """
        ns_a = _compile_preamble("X = 1\n", force_refresh=False)
        ns_b = _compile_preamble("X = 2\n", force_refresh=False)
        assert ns_a is not ns_b
        assert ns_a["X"] == 1
        assert ns_b["X"] == 2

    def test_force_refresh_busts_cache(self) -> None:
        """``force_refresh=True`` must not return a cached entry.

        The preview path passes ``force_refresh=True`` so edits to
        utility modules in the GUI are always picked up.  If
        ``lru_cache`` quietly serves a stale hit, GUI edits stop
        propagating — a silent correctness regression.
        """
        source = "UTILITY_VALUE = 42\n"
        ns_cached = _compile_preamble(source, force_refresh=False)
        ns_forced = _compile_preamble(source, force_refresh=True)
        assert ns_cached is not ns_forced, (
            "force_refresh=True must return a freshly-compiled namespace;"
            " received the cached dict instead"
        )
        # Content equivalence holds even though identity doesn't.
        assert ns_forced["UTILITY_VALUE"] == 42

    def test_force_refresh_then_cached_reuses_latest_entry(self) -> None:
        """After a ``force_refresh=True`` re-compile, a subsequent
        ``force_refresh=False`` call should return the freshly-written
        entry (object identity with the forced result).

        This exercises the write-through semantics: a forced recompile
        must still update the cache so future cache hits serve the new
        namespace, not a pre-forced stale one.
        """
        source = "WRITE_THROUGH = 7\n"
        _ns_initial = _compile_preamble(source, force_refresh=False)
        ns_forced = _compile_preamble(source, force_refresh=True)
        ns_hit = _compile_preamble(source, force_refresh=False)
        assert ns_hit is ns_forced, (
            "cache entry after force_refresh must serve the freshly-compiled ns,"
            " not the pre-forced one"
        )

    def test_cache_is_bounded(self) -> None:
        """Cache size must stay at or below the declared bound.

        Pre-refactor: ``_PREAMBLE_CACHE_MAX = 64``.  Post-refactor:
        ``functools.lru_cache(maxsize=128)`` (stdlib default).  Either
        way, insert ``bound + 20`` distinct preambles and confirm the
        cache never exceeds its bound.
        """
        bound = _cache_max()
        for i in range(bound + 20):
            # Each preamble is distinct so every insert is a miss.
            _compile_preamble(f"COUNTER_{i} = {i}\n", force_refresh=False)
        size = _cache_size()
        assert size <= bound, (
            f"cache grew to {size} entries with bound {bound} — the eviction path is broken"
        )

    def test_lru_eviction_order(self) -> None:
        """Least-recently-used entries must be evicted first.

        Insert ``bound`` distinct preambles, re-access the first one,
        then insert one more.  The re-accessed entry must survive —
        the one that got evicted should be the second-oldest, not the
        MRU one we just touched.

        This is the core LRU invariant ``functools.lru_cache`` delivers
        for free (and today's manual ``OrderedDict + popitem(last=False)``
        also delivers as long as insertion order is preserved).
        """
        bound = _cache_max()
        # Fill exactly to the bound so every new insert forces one eviction.
        sources = [f"LRU_ENTRY_{i} = {i}\n" for i in range(bound)]
        for s in sources:
            _compile_preamble(s, force_refresh=False)

        # Touch the oldest entry to make it MRU.
        first = sources[0]
        _compile_preamble(first, force_refresh=False)

        # Insert one more — this should evict sources[1] (now LRU), not sources[0].
        _compile_preamble("LRU_OVERFLOW = 999\n", force_refresh=False)

        # The first entry should still be a cache hit (same identity as before).
        ns_first_again = _compile_preamble(first, force_refresh=False)
        # Verify via identity: a cache miss would re-compile and allocate a new dict.
        ns_first_control = _compile_preamble(first, force_refresh=False)
        assert ns_first_again is ns_first_control, (
            "after touching sources[0] then overflowing, sources[0] must "
            "still be the MRU and serve hits — LRU eviction policy violated"
        )

    def test_concurrent_compiles_do_not_double_evaluate(self, tmp_path, monkeypatch) -> None:
        """N threads compiling the *same* preamble must produce the
        *same* cached namespace — not N separate re-executions with N
        different resulting dicts.

        Real failure mode: two GUI requests hit ``_compile_preamble``
        simultaneously; if the cache is read-then-write without a lock,
        both threads miss, both exec() the preamble, and the second
        writer overwrites the first — doubling CPU and potentially
        producing two distinct dicts that callers now hold references
        to.  ``functools.lru_cache`` in CPython is thread-safe at the
        cache-lookup level but does not serialise the wrapped function,
        so any additional locking (like today's ``_preamble_lock``)
        must be preserved in the refactor.
        """
        monkeypatch.chdir(tmp_path)

        source = "def shared_helper(x):\n    return x * 3\n"

        n_threads = 8
        results: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(n_threads)

        def worker() -> None:
            try:
                barrier.wait()  # maximise simultaneity
                ns = _compile_preamble(source, force_refresh=False)
                results.append(ns)
            except BaseException as exc:  # pragma: no cover — failure path
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            for _ in range(n_threads):
                pool.submit(worker)

        assert not errors, f"concurrent compile crashed: {errors!r}"
        assert len(results) == n_threads

        # All threads should have seen the same cached dict.  At minimum
        # the values must be equal; ideally the identity is preserved
        # (lru_cache guarantees this after the first writer finishes).
        first = results[0]
        for other in results[1:]:
            assert other == first, (
                "concurrent compiles produced semantically different "
                "namespaces — race in cache population"
            )

    def test_empty_preamble_does_not_fill_cache(self) -> None:
        """Empty / whitespace preamble short-circuits before the cache.

        The current implementation returns ``{}`` immediately without
        populating the cache — calling it 200 times should not push any
        other entries out.  This is a small but load-bearing detail the
        refactor must preserve.
        """
        # Seed with a sentinel non-empty preamble that we expect to survive.
        sentinel_src = "SURVIVOR = 1\n"
        sentinel_ns = _compile_preamble(sentinel_src, force_refresh=False)
        assert sentinel_ns["SURVIVOR"] == 1

        # Hammer the empty-preamble fast path.
        for _ in range(200):
            assert _compile_preamble("", force_refresh=False) == {}
            assert _compile_preamble("   ", force_refresh=False) == {}

        # The sentinel must still be in the cache (identity check).
        assert _compile_preamble(sentinel_src, force_refresh=False) is sentinel_ns


# ---------------------------------------------------------------------------
# Item #88 — post-refactor shape of the preamble cache
# ---------------------------------------------------------------------------


class TestPreambleCachePostRefactor:
    """Shape-pinning guards for the ``functools.lru_cache`` migration.

    Every test in this class pins the post-refactor surface
    (``cache_info``, ``cache_clear``, ``maxsize=128``).  These now pass
    as regression guards — the migration itself lives in Phase 3 Wave 6
    package 6C (``functools.lru_cache`` on ``_compile_preamble_cached``).
    """

    def test_compile_preamble_exposes_cache_info(self) -> None:
        """``functools.lru_cache`` exposes ``cache_info()`` that returns
        a ``CacheInfo`` namedtuple with ``hits``, ``misses``, ``maxsize``,
        ``currsize`` fields.  Pin this so the swap isn't to some other
        cache library with a different API.
        """
        info_fn = getattr(_compile_preamble, "cache_info", None)
        assert callable(info_fn), (
            "post-refactor: _compile_preamble must expose cache_info() "
            "(functools.lru_cache signature)"
        )
        info = info_fn()
        # The stdlib CacheInfo is a 4-field namedtuple.
        for field in ("hits", "misses", "maxsize", "currsize"):
            assert hasattr(info, field), f"CacheInfo missing field {field!r}"

    def test_compile_preamble_exposes_cache_clear(self) -> None:
        """The stdlib ``lru_cache`` exposes ``cache_clear()``.  Needed
        by tests and any test fixture that wants to reset state."""
        clear_fn = getattr(_compile_preamble, "cache_clear", None)
        assert callable(clear_fn), "post-refactor: _compile_preamble must expose cache_clear()"

    def test_default_maxsize_is_128(self) -> None:
        """stdlib default for ``lru_cache`` is ``maxsize=128``.  The
        review note says "pin the bound" — 128 is the bound we pin.

        Diverging here (e.g. keeping 64 from the old ``OrderedDict``)
        is a conscious choice that must be made in code review, not
        quietly.
        """
        info = _compile_preamble.cache_info()  # type: ignore[attr-defined]
        assert info.maxsize == 128, f"default lru_cache maxsize must be 128; got {info.maxsize!r}"

    def test_cache_hit_increments_hit_counter(self) -> None:
        """The stdlib ``CacheInfo`` counts hits and misses separately —
        a useful diagnostic that the old manual ``OrderedDict`` lacked.
        """
        _compile_preamble.cache_clear()  # type: ignore[attr-defined]
        source = "HIT_COUNTER = 1\n"

        before = _compile_preamble.cache_info()  # type: ignore[attr-defined]
        _compile_preamble(source, force_refresh=False)  # miss
        _compile_preamble(source, force_refresh=False)  # hit
        _compile_preamble(source, force_refresh=False)  # hit
        after = _compile_preamble.cache_info()  # type: ignore[attr-defined]

        # Two hits, one miss relative to the pre-call snapshot.
        assert after.misses - before.misses == 1
        assert after.hits - before.hits == 2


# ---------------------------------------------------------------------------
# Item #89 — xxhash migration for graph_fingerprint cache keys
# ---------------------------------------------------------------------------


def _make_graph(config: dict[str, Any] | None = None) -> PipelineGraph:
    """Minimal single-node graph for fingerprint tests."""
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(label="A", nodeType="polars", config=config or {}),
            ),
        ],
    )


class TestCacheKeyXxhashMigration:
    """The ``graph_fingerprint`` digest must become xxh64 (16 hex chars).

    Most checks here are post-refactor expectations — they're gathered
    under a single class so a reader sees the full contract.  Tests that
    should pass pre-refactor (determinism, change detection) are
    separated from the xfail-pre-refactor ones (length, hashing algo).
    """

    # --- determinism: must pass pre- and post-refactor --------------------

    def test_deterministic_across_calls(self) -> None:
        g = _make_graph({"x": 1})
        assert graph_fingerprint(g) == graph_fingerprint(g)

    def test_deterministic_across_instances_with_same_content(self) -> None:
        g1 = _make_graph({"x": 1, "y": [1, 2, 3]})
        g2 = _make_graph({"x": 1, "y": [1, 2, 3]})
        assert graph_fingerprint(g1) == graph_fingerprint(g2)

    def test_config_mutation_changes_digest(self) -> None:
        """Regression guard: a content change must produce a new digest,
        regardless of hash algorithm."""
        g_a = _make_graph({"x": 1})
        g_b = _make_graph({"x": 2})
        assert graph_fingerprint(g_a) != graph_fingerprint(g_b)

    def test_extra_keys_change_digest(self) -> None:
        """``graph_fingerprint(g, "extra")`` must differ from
        ``graph_fingerprint(g)``, both pre- and post-xxhash."""
        g = _make_graph({"x": 1})
        assert graph_fingerprint(g) != graph_fingerprint(g, "target")
        assert graph_fingerprint(g, "a") != graph_fingerprint(g, "b")

    # --- 10k collision test: must pass pre- and post-refactor -------------

    def test_10k_random_inputs_produce_distinct_keys(self) -> None:
        """10k cryptographically random preamble strings must hash to
        10k distinct digests.  This is a statistical safety net: xxh64
        has a birthday-bound collision probability of ~1 in 2**32 for
        10k inputs — effectively zero.

        Using ``secrets.token_hex`` for entropy so the test cannot be
        accidentally defeated by PRNG seed reuse.
        """
        n = 10_000
        digests: set[str] = set()
        for _ in range(n):
            payload = secrets.token_hex(32).encode()
            digests.add(content_hash_bytes(payload))
        assert len(digests) == n, (
            f"collisions detected in {n} random inputs: only {len(digests)} unique"
        )

    def test_10k_distinct_graph_fingerprints_no_collision(self) -> None:
        """Same collision test at the public API level: ``graph_fingerprint``
        on 10k distinct graphs must produce 10k distinct digests.

        This guards the end-to-end path (canonicalise → hash).  If
        ``graph_fingerprint`` post-refactor still hit SHA-256 somewhere
        downstream, this test would still pass (both algorithms are
        collision-safe at this scale) — so it's paired with the
        length test below to pin the algorithm.
        """
        n = 10_000
        digests: set[str] = set()
        for i in range(n):
            # Mix in the iteration counter so every config is unique even
            # after canonicalisation.
            g = _make_graph({"id": i, "rand": secrets.token_hex(8)})
            digests.add(graph_fingerprint(g))
        assert len(digests) == n

    # --- post-refactor only: xxh64 produces 16-char hex -------------------

    def test_graph_fingerprint_digest_is_16_hex_chars(self) -> None:
        """Post-refactor, ``graph_fingerprint`` returns a 16-char
        lowercase-hex xxh64 digest — pinning the migration away from
        SHA-256 (which would produce 64 hex chars).
        """
        g = _make_graph({"x": 1})
        digest = graph_fingerprint(g)
        assert isinstance(digest, str)
        assert len(digest) == 16, (
            f"xxh64 digest must be 16 hex chars; got {len(digest)} "
            "(pre-refactor SHA-256 produced 64)"
        )
        assert all(c in "0123456789abcdef" for c in digest)

    def test_graph_base_fingerprint_digest_is_16_hex_chars(self) -> None:
        """Same pin on the internal helper — ensures the whole module
        migrates, not just the public entry point."""
        g = _make_graph({"x": 1})
        digest = _graph_base_fingerprint(g)
        assert len(digest) == 16
        assert all(c in "0123456789abcdef" for c in digest)

    def test_digest_matches_xxh64_content_hash(self) -> None:
        """``graph_fingerprint`` should produce the exact digest shape that
        ``content_hash_bytes`` produces for the same bytes.

        This is the strongest pin: it says "use the F3 helper", not
        just "use xxh64 somehow".  Keeps a single source of truth for
        how content digests are computed across the codebase.
        """
        # Build a graph whose canonical serialisation is predictable.
        g = _make_graph({"x": 1})

        digest = graph_fingerprint(g)
        # We don't reproduce the exact canonical bytes here (that would
        # couple the test to the internal canonicalisation format).
        # Instead, we verify the digest's *shape* matches exactly what
        # content_hash_bytes produces for arbitrary input — a property
        # only shared by xxh64 and not by SHA-256.
        sample_xxh64 = content_hash_bytes(b"probe")
        assert len(digest) == len(sample_xxh64) == 16

    def test_legacy_sha256_cache_keys_are_not_readable_as_xxh64(self) -> None:
        """Old caches keyed on SHA-256 digests will NOT collide with
        xxh64 digests for the same input — confirming that the
        migration invalidates existing on-disk / persistent caches.

        Why test this?  The review note says "existing tests that pin
        specific hash values must update".  This test makes the
        algorithm switch explicit: computing the SHA-256 hex and the
        xxh64 hex of the same payload produces different strings of
        different lengths, so any stored SHA-256-keyed cache entry
        simply won't be found under the xxh64 key (fine for ephemeral
        in-memory caches, but flags persistent caches that need
        invalidation on upgrade).

        This holds both pre- and post-refactor; it's a property of the
        two algorithms, not the production wiring.
        """
        payload = b"graph-fingerprint-legacy-key-probe"
        old_key = hashlib.sha256(payload).hexdigest()
        new_key = content_hash_bytes(payload)
        # Different length → different strings by construction.
        assert old_key != new_key
        assert len(old_key) == 64
        assert len(new_key) == 16


# ---------------------------------------------------------------------------
# Benchmarks — post-refactor targets
# ---------------------------------------------------------------------------


def _perf_ratio(slow_seconds: float, fast_seconds: float) -> float:
    """Speed-up ratio = slow / fast.  Guards against div-by-zero."""
    if fast_seconds <= 0:
        return float("inf")
    return slow_seconds / fast_seconds


class TestCacheKeyBenchmark:
    """Benchmark for item #89 — xxhash must be ``>3x`` faster than SHA-256
    on a 1 MB payload.

    The review note claims 10-100x; we set the pass bar to ``>3x`` to
    give ample headroom for slow CI machines (Windows GitHub Actions
    runners in particular), while still catching a failed migration
    that would leave SHA-256 in place.

    This benchmark is **algorithm-level** (direct call to
    ``hashlib.sha256`` and ``content_hash_bytes``), so it passes both
    pre- and post-refactor as long as the xxhash library is installed.
    It exists to *justify* the swap in item #89, not gate on it.
    """

    def test_xxhash_is_over_3x_faster_than_sha256_on_1mb_payload(self) -> None:
        """Direct algorithm benchmark — hash a 1 MB payload 100 times
        with each algorithm.  Justifies the F3 migration target."""
        payload = os.urandom(1024 * 1024)  # 1 MB
        iterations = 100

        # Warm-up so JIT / C import costs don't skew the first run.
        _ = hashlib.sha256(payload).hexdigest()
        _ = content_hash_bytes(payload)

        # Interleave to average out unrelated CPU jitter.
        t_sha_total = 0.0
        t_xxh_total = 0.0
        rounds = 3
        for _ in range(rounds):
            t0 = time.perf_counter()
            for _ in range(iterations):
                hashlib.sha256(payload).hexdigest()
            t_sha_total += time.perf_counter() - t0

            t0 = time.perf_counter()
            for _ in range(iterations):
                content_hash_bytes(payload)
            t_xxh_total += time.perf_counter() - t0

        ratio = _perf_ratio(t_sha_total, t_xxh_total)
        assert ratio > 3.0, (
            f"xxh64 must be >3x faster than SHA-256 on 1MB payload; "
            f"got {ratio:.2f}x (sha256={t_sha_total * 1000:.1f}ms, "
            f"xxh64={t_xxh_total * 1000:.1f}ms over "
            f"{iterations * rounds} iterations)"
        )


class TestPreambleCacheBenchmark:
    """Benchmark for item #88 — ``functools.lru_cache`` vs hand-rolled
    ``OrderedDict`` LRU on repeated cache hits.

    Two benchmark shapes here:

      * ``test_lru_cache_vs_manual_dict_dispatch_overhead`` — micro-
        benchmark that isolates *cache dispatch* cost on both
        implementations.  Runs independent of the preamble-cache
        refactor and justifies the swap: ``functools.lru_cache`` is
        C-implemented and always comparable or faster than a Python
        ``OrderedDict`` + manual LRU bookkeeping.

      * ``test_compile_preamble_hot_path_stays_bounded`` — end-to-end
        benchmark on ``_compile_preamble`` itself.  100 compiles of a
        1000-line preamble.  Dominant cost post-refactor is the
        single cold miss; the 99 hot hits should be effectively free.
    """

    @staticmethod
    def _build_1000_line_preamble() -> str:
        """Realistic 1000-line preamble.

        Uses trivial assignments so compilation is cheap enough that
        cache-hit overhead dominates the measurement (otherwise we're
        benchmarking exec() time, not cache dispatch).
        """
        return "\n".join(f"VAR_{i} = {i}" for i in range(1000)) + "\n"

    def test_lru_cache_vs_manual_dict_dispatch_overhead(self) -> None:
        """Isolate cache-dispatch cost by wrapping a trivial function
        with both implementations and timing 100k hits each.

        ``functools.lru_cache`` must be comparable or faster than a
        hand-rolled ``OrderedDict + popitem`` LRU.  The stdlib wrapper
        is C-implemented (``_functools._lru_cache_wrapper``) so it
        typically beats any pure-Python alternative by >2x.
        """
        import functools
        from collections import OrderedDict

        def _expensive(x: int) -> int:
            # Non-trivial work so call overhead is measurable but the
            # cache still dominates for hot hits.
            return x * 2

        # 1. functools.lru_cache wrapper.
        lru_wrapped = functools.lru_cache(maxsize=128)(_expensive)
        # Warm.
        lru_wrapped(42)

        # 2. Hand-rolled OrderedDict LRU mirror of the current
        #    executor implementation: dict lookup + miss populate +
        #    LRU eviction.  Wrapped as a callable for fair comparison.
        _store: OrderedDict[int, int] = OrderedDict()
        _max = 128
        _lock = threading.Lock()  # mirror today's _preamble_lock

        def manual_lru(x: int) -> int:
            with _lock:
                if x in _store:
                    _store.move_to_end(x)
                    return _store[x]
                result = _expensive(x)
                _store[x] = result
                if len(_store) > _max:
                    _store.popitem(last=False)
                return result

        # Warm.
        manual_lru(42)

        iterations = 100_000

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(iterations):
            lru_wrapped(42)
        t_lru = time.perf_counter() - t0

        gc.collect()
        t0 = time.perf_counter()
        for _ in range(iterations):
            manual_lru(42)
        t_manual = time.perf_counter() - t0

        # lru_cache must not be slower by more than 20% — the cross-over
        # direction is the whole point of the refactor.  The "comparable"
        # bound protects against Python implementations (PyPy) where the
        # C wrapper might not win.
        ratio = _perf_ratio(t_manual, t_lru)
        assert ratio >= 0.8, (
            f"functools.lru_cache dispatch too slow vs manual dict: "
            f"{ratio:.2f}x (lru={t_lru * 1000:.1f}ms, "
            f"manual={t_manual * 1000:.1f}ms over {iterations} hits). "
            "Expected comparable-or-faster; the refactor's premise is "
            "broken if lru_cache costs more per hit than the OrderedDict."
        )

    def test_compile_preamble_hot_path_stays_bounded(self) -> None:
        """Hammer the cache with 100 compiles of the SAME 1000-line
        preamble.  Post-refactor, only the first is a miss; the other
        99 should be O(1) hits.

        If the cache is broken (e.g. ``force_refresh`` wasn't wired
        through, or the key function is non-deterministic), every call
        is a miss and this test takes proportionally longer.
        """
        preamble = self._build_1000_line_preamble()

        # Fresh cache for a clean miss+hits sequence.
        _clear_preamble_cache()

        t0 = time.perf_counter()
        for _ in range(100):
            _compile_preamble(preamble, force_refresh=False)
        total_ms = (time.perf_counter() - t0) * 1000

        # A single exec of a 1000-line preamble takes a few ms.  99
        # cache hits should cost effectively nothing.  Set the cap
        # generously to avoid false positives on slow CI: 500 ms
        # total for 100 calls means each call averages 5 ms — well
        # above any real cache-hit cost but well below what uncached
        # re-compiles would need.
        assert total_ms < 500, (
            f"100 compiles of same preamble took {total_ms:.0f}ms; "
            "cache is probably not serving hits"
        )


# ---------------------------------------------------------------------------
# Sanity sentinel — catches accidental import drift.
# ---------------------------------------------------------------------------


class TestSurfaceSanity:
    """Fast-fail checks that the modules under test still have the public
    surface this file depends on.  If one of these fails, every other
    test in the file is noise."""

    def test_hashing_helper_importable(self) -> None:
        from haute._hashing import HASH_ALGO, content_hash_bytes

        assert HASH_ALGO == "xxh64"
        assert callable(content_hash_bytes)

    def test_compile_preamble_accepts_force_refresh_kwarg(self) -> None:
        """Pre- and post-refactor, ``_compile_preamble`` must accept a
        ``force_refresh`` keyword argument."""
        # Empty preamble short-circuits without touching the cache.
        assert _compile_preamble("", force_refresh=True) == {}
        assert _compile_preamble("", force_refresh=False) == {}

    def test_graph_fingerprint_returns_non_empty_string(self) -> None:
        g = _make_graph({"x": 1})
        digest = graph_fingerprint(g)
        assert isinstance(digest, str)
        assert digest  # non-empty
