"""Tests pinning the @cached_property fingerprint memoisation on PipelineGraph.

Covers two performance items from ``docs/CODEBASE_REVIEW.md``:

* **#86** — ``executor.py:385`` recomputes the graph fingerprint every preview
  call.  Cache the structural ("base") fingerprint on ``PipelineGraph`` via
  ``@cached_property`` so repeated calls reuse the computation.
* **#94** — ``trace.py`` recomputes the preview fingerprint even though the
  preview call already had it.  Either pass it through or let the
  ``@cached_property`` absorb the cost.

These tests pin the **correctness invariants** (same digest as the old free
function form, per-call extra keys never baked into the cache, fresh copies
get fresh caches) and the **observable speedup** (comparative — absolute wall
time is CI-flaky).

Production code is NOT edited by this suite — some tests are expected to fail
until the ``@cached_property`` lands.  The passing ones act as regression
guards for behaviour that must hold both before and after the optimisation.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from haute._cache import graph_fingerprint
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(nid: str, config: dict[str, Any] | None = None) -> GraphNode:
    """Build a minimal polars node with the given id and config payload."""
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.POLARS,
            config=config or {"code": f"# node {nid}"},
        ),
    )


def _make_chain_graph(n_nodes: int) -> PipelineGraph:
    """Build a linear chain of *n_nodes* polars nodes.

    Used for the benchmark scaffold — 100 nodes is enough to make the
    fingerprint computation show up in profiles without inflating other
    costs into the noise.
    """
    nodes = [
        _make_node(f"n{i}", {"code": f"df = df.with_columns(y{i}=pl.col('x') * {i})"})
        for i in range(n_nodes)
    ]
    edges = [GraphEdge(id=f"e{i}", source=f"n{i}", target=f"n{i + 1}") for i in range(n_nodes - 1)]
    return PipelineGraph(nodes=nodes, edges=edges)


def _small_graph() -> PipelineGraph:
    """A tiny two-node graph — fast to construct, easy to reason about."""
    return PipelineGraph(
        nodes=[_make_node("a", {"code": "df = df"}), _make_node("b", {"code": "df = df"})],
        edges=[GraphEdge(id="e_ab", source="a", target="b")],
    )


# ---------------------------------------------------------------------------
# Cached-property shape — contract pinning
# ---------------------------------------------------------------------------


class TestCachedPropertyContract:
    """The cached form must agree with the free-function form, byte-for-byte.

    We don't assume a specific attribute name (``base_fingerprint``,
    ``_haute_base_fingerprint``, ``cached_fingerprint`` are all plausible) —
    we only assert the observable effect: ``graph_fingerprint(g)`` produces
    a deterministic string of the right shape on repeat calls.
    """

    def test_repeat_calls_produce_identical_digest(self) -> None:
        """Calling ``graph_fingerprint`` twice on the same instance must
        produce byte-identical results even after the cache lands."""
        g = _small_graph()
        fp1 = graph_fingerprint(g)
        fp2 = graph_fingerprint(g)
        assert fp1 == fp2
        # Non-empty lowercase hex digest after the Wave 9C ``v<N>:``
        # prefix; the exact algorithm (xxh64 after the Phase 3 Wave 6
        # migration) is pinned in ``test_cache_perf_fixes.py`` — here
        # we only pin stability + shape.
        assert fp1
        _, _, digest = fp1.partition(":")
        assert all(c in "0123456789abcdef" for c in digest)

    def test_repeat_calls_with_same_extra_keys_are_identical(self) -> None:
        g = _small_graph()
        fp1 = graph_fingerprint(g, "target", "row_limit=10")
        fp2 = graph_fingerprint(g, "target", "row_limit=10")
        assert fp1 == fp2

    def test_two_independent_instances_with_same_structure_agree(self) -> None:
        """Fresh construction of an identical graph must yield the same
        fingerprint — the cache is per-instance, not somehow tied to
        ``id(graph)``."""
        g1 = _small_graph()
        g2 = _small_graph()
        assert graph_fingerprint(g1) == graph_fingerprint(g2)

    def test_model_copy_with_update_changes_fingerprint(self) -> None:
        """``model_copy(update={...})`` produces a distinct instance whose
        cached fingerprint reflects the new structure, not the old one.

        This is the "cache invalidation on copy" invariant.  Because
        ``PipelineGraph`` is mutable (no ``frozen=True``), the property
        cache lives on the instance; ``model_copy`` creates a new
        instance, so the cache starts fresh automatically.
        """
        g = _small_graph()
        fp_original = graph_fingerprint(g)
        g_copy = g.model_copy(update={"nodes": [*g.nodes, _make_node("c")]})
        fp_after_copy = graph_fingerprint(g_copy)
        assert fp_original != fp_after_copy
        # The original's fingerprint must be unchanged — copy is pure.
        assert graph_fingerprint(g) == fp_original

    def test_model_copy_without_update_preserves_fingerprint(self) -> None:
        """A vanilla ``model_copy()`` produces a new instance with the
        same structure, so the fingerprint must be equal.  This pins
        that the cache isn't keyed on anything instance-specific (like
        a random id or creation timestamp)."""
        g = _small_graph()
        fp_original = graph_fingerprint(g)
        g_copy = g.model_copy()
        assert graph_fingerprint(g_copy) == fp_original


# ---------------------------------------------------------------------------
# Extra keys must not leak into the cached base
# ---------------------------------------------------------------------------


class TestExtraKeysAreNotCached:
    """``graph_fingerprint(graph, *extra)`` mixes *extra* into the digest
    *at call time*.  The cache must only memoise the structural (base)
    digest — extra keys are per-call and must NOT be baked in.
    """

    def test_different_extras_produce_different_fingerprints(self) -> None:
        g = _small_graph()
        fp_no_extra = graph_fingerprint(g)
        fp_a = graph_fingerprint(g, "a")
        fp_b = graph_fingerprint(g, "b")
        fp_ab = graph_fingerprint(g, "a", "b")
        fp_ba = graph_fingerprint(g, "b", "a")
        assert len({fp_no_extra, fp_a, fp_b, fp_ab, fp_ba}) == 5

    def test_extras_do_not_poison_subsequent_no_extra_call(self) -> None:
        """After calling with extras, a subsequent plain call must return
        the *base* fingerprint — not some residue of the prior extras.

        This is the failure mode that would happen if someone naively
        cached ``graph_fingerprint(graph, *extra_keys)`` on the graph.
        """
        g = _small_graph()
        fp_base = graph_fingerprint(g)
        _ = graph_fingerprint(g, "some_extra")
        fp_base_again = graph_fingerprint(g)
        assert fp_base == fp_base_again

    def test_extras_ordering_matters(self) -> None:
        """``("a", "b")`` and ``("b", "a")`` are different inputs.  If
        the cache accidentally treated extras as an unordered set this
        would false-positive."""
        g = _small_graph()
        fp_ab = graph_fingerprint(g, "a", "b")
        fp_ba = graph_fingerprint(g, "b", "a")
        assert fp_ab != fp_ba

    def test_many_extra_key_variations_on_same_graph(self) -> None:
        """Exercises the path where the graph's base is cached once and
        many per-call extras run through on top without corrupting it."""
        g = _small_graph()
        fp_base = graph_fingerprint(g)
        distinct = set()
        for extra in ["x1", "x2", "x3", "x4", "x5", "target_A", "target_B"]:
            distinct.add(graph_fingerprint(g, extra))
        # All extras produce unique digests and none of them is the base.
        assert fp_base not in distinct
        assert len(distinct) == 7
        # Base is still recoverable.
        assert graph_fingerprint(g) == fp_base


# ---------------------------------------------------------------------------
# Mutation invariants — frozen vs. mutable behaviour
# ---------------------------------------------------------------------------


class TestMutationBehaviour:
    """``PipelineGraph`` is currently mutable (no ``frozen=True``) which
    means ``@cached_property`` lives on the instance and survives direct
    list mutation — callers are expected to use ``model_copy(update=...)``
    instead of mutating in place.

    These tests pin that:

    1. Direct in-place mutation of ``nodes``/``edges`` is NOT a supported
       pattern (behaviour under mutation is an implementation detail we
       don't assert — we simply verify that the recommended immutable
       path works correctly).
    2. ``model_copy`` is the supported invalidation mechanism.
    """

    def test_model_copy_update_nodes_yields_fresh_fingerprint(self) -> None:
        g = _small_graph()
        fp_before = graph_fingerprint(g)
        new_nodes = [*g.nodes, _make_node("c", {"code": "# new"})]
        g2 = g.model_copy(update={"nodes": new_nodes})
        assert graph_fingerprint(g2) != fp_before

    def test_model_copy_update_edges_yields_fresh_fingerprint(self) -> None:
        g = _small_graph()
        fp_before = graph_fingerprint(g)
        new_edges = [*g.edges, GraphEdge(id="e_extra", source="a", target="a")]
        g2 = g.model_copy(update={"edges": new_edges})
        assert graph_fingerprint(g2) != fp_before

    def test_different_node_config_means_different_fingerprint(self) -> None:
        """The base digest is over node config as well as topology — so a
        config-only change must break the cache boundary even though
        node ids and edge set are identical."""
        n1 = _make_node("a", {"code": "df = df.filter(pl.col('x') > 0)"})
        n2 = _make_node("a", {"code": "df = df.filter(pl.col('x') > 1)"})
        g1 = PipelineGraph(nodes=[n1], edges=[])
        g2 = PipelineGraph(nodes=[n2], edges=[])
        assert graph_fingerprint(g1) != graph_fingerprint(g2)


# ---------------------------------------------------------------------------
# Call-counting spy — trace passthrough (item #94)
# ---------------------------------------------------------------------------


class _CallCountingFingerprint:
    """Context-manager-style spy that counts ``_graph_base_fingerprint``
    invocations.

    We patch the **base** computation rather than the public
    ``graph_fingerprint`` wrapper: the wrapper is called many times per
    preview+trace (once for the preview cache key, once for the trace
    cache key, maybe more for child modules).  The optimisation is that
    the *base* computation runs at most once per distinct ``PipelineGraph``
    instance — that's what the cache eliminates.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.calls = 0
        self._original = None

    def __enter__(self) -> _CallCountingFingerprint:
        from haute import _cache as cache_mod

        self._original = cache_mod._graph_base_fingerprint

        def _counting(graph: PipelineGraph) -> str:
            assert self._original is not None
            self.calls += 1
            return self._original(graph)

        # Patch the binding the wrapper actually looks up.
        self.monkeypatch.setattr(cache_mod, "_graph_base_fingerprint", _counting)
        return self

    def __exit__(self, *exc: object) -> None:
        # monkeypatch undoes itself at fixture teardown.
        return None


class TestFingerprintRecomputeSpy:
    """After caching, calling ``graph_fingerprint`` N times on the same
    instance must compute the base exactly once.  This is the core
    performance claim of item #86."""

    def test_repeated_calls_on_same_instance_compute_base_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        g = _small_graph()
        with _CallCountingFingerprint(monkeypatch) as spy:
            for _ in range(10):
                graph_fingerprint(g)
            # After the cache lands, 10 public calls should trigger
            # exactly 1 base computation.  Before the cache lands, this
            # test fails with spy.calls == 10 — documenting the current
            # pathology.
            assert spy.calls == 1, (
                f"Expected base to be computed once per instance, got {spy.calls}. "
                "This test guards item #86 — once @cached_property is added "
                "to PipelineGraph, this must hold."
            )

    def test_repeated_calls_with_extras_still_compute_base_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even with varying extras per call, the base is the same and
        must be computed only once."""
        g = _small_graph()
        with _CallCountingFingerprint(monkeypatch) as spy:
            graph_fingerprint(g)
            graph_fingerprint(g, "target_a")
            graph_fingerprint(g, "target_b", "row_limit=5")
            graph_fingerprint(g, "target_c")
            assert spy.calls == 1, (
                f"Extras differ per call but graph is the same — base "
                f"should cache.  Got {spy.calls} base computations."
            )

    def test_distinct_instances_compute_base_independently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache is per-instance, so two instances → two base computations."""
        g1 = _small_graph()
        g2 = _small_graph()
        with _CallCountingFingerprint(monkeypatch) as spy:
            graph_fingerprint(g1)
            graph_fingerprint(g2)
            assert spy.calls == 2

    def test_model_copy_creates_fresh_cache_slot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``model_copy`` produces a distinct instance, so the copy
        re-computes the base once (even if its structure is identical)."""
        g = _small_graph()
        g_copy = g.model_copy()
        with _CallCountingFingerprint(monkeypatch) as spy:
            graph_fingerprint(g)
            graph_fingerprint(g_copy)
            assert spy.calls == 2

    def test_trace_does_not_recompute_preview_fingerprint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        """Item #94 — trace.py should not recompute a preview fingerprint
        that ``execute_graph`` already computed on the same ``PipelineGraph``
        instance.

        With ``@cached_property`` on PipelineGraph, even if trace and
        executor each call ``graph_fingerprint`` on the same instance,
        the **base** computation happens once.  That's what this spy
        asserts.  We don't need to assert "trace doesn't call it at all"
        because the idiomatic fix (memoise on the graph) satisfies #94
        regardless of whether the call sites are refactored to pass
        fingerprints around.
        """
        import polars as pl

        # A tiny, self-contained graph that both execute_graph and
        # execute_trace can run end-to-end.
        parquet = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(parquet)

        g = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src",
                        nodeType=NodeType.DATA_SOURCE,
                        config={"path": str(parquet)},
                    ),
                ),
                GraphNode(
                    id="t",
                    data=NodeData(
                        label="t",
                        nodeType=NodeType.POLARS,
                        config={"code": "df = df.with_columns(z=pl.col('x') + 1)"},
                    ),
                ),
            ],
            edges=[GraphEdge(id="e1", source="src", target="t")],
        )

        from haute.executor import execute_graph
        from haute.trace import execute_trace

        # Run preview once, then run trace.  Both operate on *the same*
        # PipelineGraph instance ``g``.  The base fingerprint must be
        # computed exactly once across both calls.
        with _CallCountingFingerprint(monkeypatch) as spy:
            execute_graph(g, target_node_id="t", row_limit=3)
            execute_trace(g, row_index=0, target_node_id="t", row_limit=3)
            assert spy.calls == 1, (
                f"Preview + trace on the same PipelineGraph computed the "
                f"base fingerprint {spy.calls} times — item #94 expects "
                "passthrough / @cached_property to collapse this to 1."
            )


# ---------------------------------------------------------------------------
# Benchmark scaffold — comparative speedup
# ---------------------------------------------------------------------------


class TestFingerprintBenchmark:
    """Comparative micro-benchmark.  Absolute thresholds are CI-flaky, so
    we pin a **relative** speedup: the cached path must spend at least
    30% less time than a forced-recompute baseline.

    Baseline: call the underlying ``_graph_base_fingerprint`` directly
    every iteration (the pathology item #86 describes).
    Cached:   call the public ``graph_fingerprint`` every iteration and
              rely on ``@cached_property`` to short-circuit after the
              first call.
    """

    # Graph size tuned so that the fingerprint computation dominates the
    # loop body — at 100 nodes the canonicalise + json + hash round trip
    # is roughly a ms each call, but still fast enough to not slow CI.
    N_NODES = 100
    N_ITERATIONS = 100

    # Comparative speedup threshold.  Kept loose (30%) so the assertion
    # doesn't flake on slow CI runners.  In practice the speedup is near
    # 99% because the cached path short-circuits to an attribute lookup.
    MIN_SPEEDUP = 0.30

    def _time_baseline(self, graph: PipelineGraph) -> float:
        """Force-recompute every iteration — simulates pre-cache behaviour."""
        from haute._cache import _graph_base_fingerprint

        # Warm up caches (JIT, page faults, etc.) — discard the first run.
        for _ in range(5):
            _graph_base_fingerprint(graph)
        t0 = time.perf_counter()
        for _ in range(self.N_ITERATIONS):
            _graph_base_fingerprint(graph)
        return time.perf_counter() - t0

    def _time_cached(self, graph: PipelineGraph) -> float:
        """Public ``graph_fingerprint`` — cached after the first call."""
        # Warm up — also seeds the @cached_property for the measured loop.
        for _ in range(5):
            graph_fingerprint(graph)
        t0 = time.perf_counter()
        for _ in range(self.N_ITERATIONS):
            graph_fingerprint(graph)
        return time.perf_counter() - t0

    def test_cached_path_is_at_least_30_percent_faster(self) -> None:
        """Pin the speedup from item #86's optimisation.

        Fails before the optimisation lands (cached path ~= baseline
        because every public call recomputes the base).  Passes after
        the ``@cached_property`` eliminates the per-call hash.
        """
        graph = _make_chain_graph(self.N_NODES)

        # Three-sample median so a GC pause in one sample doesn't sink
        # the comparison.
        baseline_samples = sorted(self._time_baseline(graph) for _ in range(3))
        cached_samples = sorted(self._time_cached(graph) for _ in range(3))
        baseline = baseline_samples[1]
        cached = cached_samples[1]

        # Prevent division-by-zero on extremely fast runners (baseline
        # could round to 0 on a blazing machine); the test is only
        # meaningful when baseline is measurably above noise.
        assert baseline > 1e-5, (
            f"Baseline timing too small to compare ({baseline:.2e}s). "
            "Increase N_NODES or N_ITERATIONS."
        )

        speedup = (baseline - cached) / baseline
        assert speedup >= self.MIN_SPEEDUP, (
            f"Cached fingerprint path must be >={self.MIN_SPEEDUP:.0%} faster "
            f"than baseline; got {speedup:.1%} "
            f"(baseline={baseline:.4f}s, cached={cached:.4f}s, "
            f"N={self.N_ITERATIONS}, nodes={self.N_NODES})."
        )

    def test_cached_path_does_not_regress_below_baseline(self) -> None:
        """A weaker floor — the cached path must not be *slower* than
        the baseline.  Acts as a regression guard even if the 30%
        threshold is relaxed in future."""
        graph = _make_chain_graph(self.N_NODES)
        baseline = min(self._time_baseline(graph) for _ in range(3))
        cached = min(self._time_cached(graph) for _ in range(3))
        # Allow 20% slack for scheduler noise — the cached path should
        # never be meaningfully slower than the recompute path.
        assert cached <= baseline * 1.20, (
            f"Cached path ({cached:.4f}s) slower than baseline "
            f"({baseline:.4f}s) — a regression that would undo item #86."
        )
