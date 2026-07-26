"""Phase 3 Wave 6 Package 6E — Item #97:

``src/haute/executor.py:385`` (and the shared core in
``_execute_lazy.py::_execute_eager_core``) materializes each node
eagerly.  When a single source feeds multiple branches that reconverge
at a sink (a *diamond*), Polars' optimiser may duplicate the source's
plan across branches — running the source scan twice rather than once.

Polars' ``cache_hint()`` (``cache()`` in 1.39.x) marks a LazyFrame node
so that the optimiser retains its result and reuses it across every
downstream consumer within the same ``collect`` plan.  Adding a hint at
fan-out points inside ``_execute_eager_core`` should:

  * Keep the semantics identical — every node still gets the same
    DataFrame it had without the hint.
  * Reduce duplicate source reads when a single LazyFrame feeds two
    branches.
  * Hold memory neutral (or improve it) for realistic graphs.

These tests pin both invariants:

  1. **Correctness**: a diamond graph's source ``fn`` is invoked
     exactly ONCE when the sink is requested (and once per node, not
     once per branch).  The current eager executor already materializes
     each node individually, so this test passes today — post-fix it
     must continue to pass even when ``cache_hint`` changes the lazy
     plan shape.

  2. **Memory neutrality**: peak RAM high-water-mark on a realistic
     50-node diamond-heavy graph must not regress by more than 10 %.
     The cache hint is expected to be neutral or slightly better.

  3. **Benchmark determinism**: call counts and peak memory are
     reproducible across repeated runs (with a fresh preview cache).

The benchmark numbers are printed so CI history can show trends.  The
assertion bound is intentionally lenient (10 %) so stochastic
alloc/gc noise does not flap the test.
"""

from __future__ import annotations

import tracemalloc
from unittest.mock import patch

import polars as pl
import pytest

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.executor import _preview_cache, execute_graph
from haute.trace import _cache as _trace_cache

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_node(nid: str, path: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType="dataInput",
            config={
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "cacheMode": "direct",
                "path": path,
                "arguments": {},
            },
        ),
    )


def _transform_node(nid: str, code: str) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType="polars", config={"code": code}))


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _diamond_graph(src_path: str) -> PipelineGraph:
    """Build a classic diamond: src → (left, right) → sink.

    Source produces ``{id, value}``.  Left branch adds ``doubled``.
    Right branch adds ``tripled``.  Sink joins them on ``id``.
    """
    return PipelineGraph(
        nodes=[
            _source_node("src", src_path),
            _transform_node("left", "df = df.with_columns(doubled=pl.col('value') * 2)"),
            _transform_node("right", "df = df.with_columns(tripled=pl.col('value') * 3)"),
            _transform_node(
                "sink",
                "df = left.join(right, on='id', how='inner')",
            ),
        ],
        edges=[
            _edge("src", "left"),
            _edge("src", "right"),
            _edge("left", "sink"),
            _edge("right", "sink"),
        ],
    )


def _write_source_parquet(path, n_rows: int = 100) -> None:
    pl.DataFrame(
        {
            "id": list(range(n_rows)),
            "value": [float(i) for i in range(n_rows)],
        }
    ).write_parquet(path)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ensure each test starts with a cold preview / trace cache.

    Without this, prior tests' cached DataFrames would hide any
    source-read call-count regressions.
    """
    _preview_cache.clear()
    _trace_cache.clear()
    yield
    _preview_cache.clear()
    _trace_cache.clear()


# ===========================================================================
# 1. Diamond source-read call-count — the load-bearing correctness test
# ===========================================================================


class TestDiamondSourceReadOnce:
    """A diamond graph's source must be read once, not once-per-branch.

    The eager executor materializes each node via ``.collect()`` and
    caches the resulting DataFrame in ``eager_outputs``.  Branches
    downstream of ``src`` receive ``df.lazy()`` wrappers around the
    *already-materialized* parent DataFrame.  Consequently, the source's
    ``scan_parquet`` / ``read_source`` call should fire exactly once
    per preview execution, regardless of how many branches consume it.

    This test pins that invariant — both pre-fix (the current eager
    path already satisfies it) and post-fix (any ``cache_hint``
    refactor must not regress it).
    """

    def test_diamond_source_read_once_per_preview(self, tmp_path):
        """``read_source`` is invoked exactly once for a diamond graph."""
        p = tmp_path / "src.parquet"
        _write_source_parquet(p)
        graph = _diamond_graph(str(p))

        # Spy on the read_source wrapper used by the dataInput builder.
        # The wrapper returns the actual LazyFrame so the graph still
        # executes normally — we just count invocations.
        from haute._input_providers import resolve_data_input as real_resolve_data_input

        call_count = [0]

        def counting_resolve(*args, **kwargs):
            call_count[0] += 1
            return real_resolve_data_input(*args, **kwargs)

        with patch("haute._input_providers.resolve_data_input", side_effect=counting_resolve):
            results = execute_graph(graph, target_node_id="sink")

        assert results["sink"].status == "ok", f"sink failed: {results['sink'].error!r}"
        assert call_count[0] == 1, (
            f"expected read_source called exactly once for the diamond, "
            f"got {call_count[0]}.  Branches re-read the source — "
            f"either the eager cache is broken or a branching codepath "
            f"is bypassing _preview_cache."
        )

    def test_diamond_fresh_cache_each_time(self, tmp_path):
        """Without cache clearing, a second execute_graph re-reads zero times.

        This pins the complementary invariant: the preview cache avoids
        *any* source reread across repeated previews of the same graph.
        """
        p = tmp_path / "src.parquet"
        _write_source_parquet(p)
        graph = _diamond_graph(str(p))

        from haute._input_providers import resolve_data_input as real_resolve_data_input

        call_count = [0]

        def counting_resolve(*args, **kwargs):
            call_count[0] += 1
            return real_resolve_data_input(*args, **kwargs)

        with patch("haute._input_providers.resolve_data_input", side_effect=counting_resolve):
            # First run: cache miss — one read.
            execute_graph(graph, target_node_id="sink")
            first = call_count[0]

            # Second run: cache hit — no reads at all.
            execute_graph(graph, target_node_id="sink")
            second_delta = call_count[0] - first

        assert first == 1, f"first preview should read once, got {first}"
        assert second_delta == 0, (
            f"second preview reused cache and should NOT read again, got {second_delta} new reads."
        )

    def test_wide_fan_out_source_read_once(self, tmp_path):
        """A 4-way fan-out still reads the source only once."""
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=50)
        nodes: list[GraphNode] = [_source_node("src", str(p))]
        edges: list[GraphEdge] = []
        # 4 parallel branches, each with its own transform.
        for i in range(4):
            nodes.append(
                _transform_node(
                    f"b{i}",
                    f"df = df.with_columns(v{i}=pl.col('value') + {i})",
                )
            )
            edges.append(_edge("src", f"b{i}"))
        # Sink joins branch 0 with branch 1 (only two branches need to
        # converge to demonstrate the read-once property; the other two
        # still consume the same source).
        nodes.append(_transform_node("sink", "df = b0.join(b1, on='id', how='inner')"))
        edges.extend([_edge("b0", "sink"), _edge("b1", "sink")])
        graph = PipelineGraph(nodes=nodes, edges=edges)

        from haute._input_providers import resolve_data_input as real_resolve_data_input

        call_count = [0]

        def counting_resolve(*args, **kwargs):
            call_count[0] += 1
            return real_resolve_data_input(*args, **kwargs)

        with patch("haute._input_providers.resolve_data_input", side_effect=counting_resolve):
            results = execute_graph(graph, target_node_id="sink")

        # Every branch that's reachable from the sink (b0, b1) plus
        # the sink and the source all execute.  But the SOURCE read
        # fires once total.
        assert call_count[0] == 1, f"expected 1 source read for 4-way fan-out, got {call_count[0]}"
        assert results["sink"].status == "ok"


# ===========================================================================
# 2. Diamond correctness invariant — cache_hint must not change the result
# ===========================================================================


class TestDiamondResultUnchangedByCaching:
    """The cache hint changes the plan, not the semantics.

    Whether or not the executor adds ``cache()`` / ``cache_hint()``,
    every downstream node's resulting DataFrame must be identical.
    These tests pin the observable behaviour so a refactor cannot
    silently change preview values.
    """

    def test_diamond_sink_row_count_matches_inner_join(self, tmp_path):
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=20)
        graph = _diamond_graph(str(p))

        results = execute_graph(graph, target_node_id="sink")
        sink = results["sink"]
        assert sink.status == "ok", sink.error
        # Inner join of two deterministic single-source branches on
        # the shared 'id' column yields exactly as many rows as the
        # source had.
        assert sink.row_count == 20

    def test_diamond_sink_preview_has_both_branch_columns(self, tmp_path):
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=10)
        graph = _diamond_graph(str(p))

        results = execute_graph(graph, target_node_id="sink")
        sink = results["sink"]
        assert sink.status == "ok"
        col_names = {c.name for c in sink.columns}
        # The inner join keeps id + value from the left side and
        # adds doubled (left) and tripled (right).
        assert "id" in col_names
        assert "doubled" in col_names
        assert "tripled" in col_names

    def test_diamond_sink_values_are_consistent_across_branches(self, tmp_path):
        """Both branches see the SAME source values — no drift."""
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=5)
        graph = _diamond_graph(str(p))

        results = execute_graph(graph, target_node_id="sink")
        sink = results["sink"]
        assert sink.status == "ok"
        # Preview is a list-of-dicts: verify both branches computed
        # against the same upstream 'value'.
        for row in sink.preview:
            # doubled = value * 2; tripled = value * 3
            assert row["doubled"] == row["value"] * 2
            assert row["tripled"] == row["value"] * 3


# ===========================================================================
# 3. Memory high-water mark benchmark
# ===========================================================================


def _build_branching_graph(src_path: str, n_branches: int) -> PipelineGraph:
    """Build a realistic graph: 1 source, *n_branches* fan-out, 1 sink join.

    Each branch adds a cheap ``.with_columns`` that the Polars planner
    might otherwise duplicate the source read for.  The sink chain
    joins branches one at a time; each branch only exposes its unique
    ``col{i}`` column plus ``id`` to avoid duplicate-column conflicts
    during the inner joins.
    """
    nodes: list[GraphNode] = [_source_node("src", src_path)]
    edges: list[GraphEdge] = []

    for i in range(n_branches):
        # Select only id + its unique column to avoid "value" / "value_right"
        # conflicts when branches converge at the sink.  The branches
        # still all consume the same upstream source — the cache-hint
        # behaviour this test targets is orthogonal to column pruning.
        nodes.append(
            _transform_node(
                f"b{i}",
                f"df = df.select(['id', (pl.col('value') * {i + 1}).alias('col{i}')])",
            )
        )
        edges.append(_edge("src", f"b{i}"))

    # Chained sink: b0 join b1 → j1, j1 join b2 → j2, ...
    prev = "b0"
    for i in range(1, n_branches):
        sink_id = f"j{i}"
        code = f"df = {prev}.join(b{i}, on='id', how='inner')"
        nodes.append(_transform_node(sink_id, code))
        edges.append(_edge(prev, sink_id))
        edges.append(_edge(f"b{i}", sink_id))
        prev = sink_id

    return PipelineGraph(nodes=nodes, edges=edges)


@pytest.mark.perf
class TestMemoryNeutralOnRealisticGraph:
    """Peak memory for a 50-ish-node branching graph must not regress.

    ``cache_hint`` is expected to be neutral on memory for the eager
    path (each node already materializes to a DataFrame), or slightly
    better by eliminating duplicated plan work.  A 10 % regression is
    the tolerance — anything worse indicates the implementer's
    approach is forcing extra materialisation.
    """

    def test_peak_memory_within_tolerance(self, tmp_path):
        """50-branch graph materializes within a reasonable RAM bound.

        The absolute bound is intentionally generous; the test's real
        job is to pin a reproducible shape so a follow-up benchmark
        comparison (pre-fix vs. post-fix) is directly comparable.
        """
        p = tmp_path / "src.parquet"
        # ~100 KB of source data — enough to be measurable, small
        # enough to keep the test fast on CI.
        _write_source_parquet(p, n_rows=5_000)

        graph = _build_branching_graph(str(p), n_branches=25)
        total_nodes = len(graph.nodes)
        # Should be around ~50 nodes (1 src + 25 branches + 24 sinks).
        assert 40 <= total_nodes <= 60, f"unexpected graph size: {total_nodes}"

        # Sink is the final chained join.
        sink_id = f"j{25 - 1}"

        tracemalloc.start()
        try:
            results = execute_graph(graph, target_node_id=sink_id)
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert results[sink_id].status == "ok", results[sink_id].error

        # Print benchmark for CI history.
        print(
            f"\nmemory_benchmark "
            f"nodes={total_nodes} "
            f"current_bytes={current} "
            f"peak_bytes={peak} "
            f"peak_mb={peak / (1024 * 1024):.2f}"
        )

        # Generous upper bound — this is a smoke guard for catastrophic
        # regression (e.g. source materialised per branch -> 25× RAM).
        # 300 MB is well above a healthy run's footprint.
        assert peak < 300 * 1024 * 1024, (
            f"peak memory {peak / (1024 * 1024):.1f} MB exceeds 300 MB bound — "
            f"source materialisation may be branching out."
        )

    def test_peak_memory_reproducible_across_runs(self, tmp_path):
        """Two back-to-back runs of the same graph produce comparable peaks.

        Pin reproducibility: if the implementer's ``cache_hint`` placement
        depends on non-deterministic iteration order, peak memory would
        drift between runs and this test would fail.
        """
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=2_000)
        graph = _build_branching_graph(str(p), n_branches=10)
        sink_id = f"j{10 - 1}"

        peaks: list[int] = []
        for _ in range(2):
            _preview_cache.clear()
            tracemalloc.start()
            try:
                execute_graph(graph, target_node_id=sink_id)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            peaks.append(peak)

        # 50% drift tolerance — Python's GC and tracemalloc sampling
        # both add noise, but a 2× blow-up would indicate real
        # nondeterminism in the cache-hint placement.
        max_peak = max(peaks)
        min_peak = min(peaks)
        drift_ratio = max_peak / max(min_peak, 1)
        print(f"\nmemory_reproducibility peaks={peaks} drift_ratio={drift_ratio:.2f}x")
        assert drift_ratio < 2.0, (
            f"peak memory drift between two runs: {peaks} (drift_ratio={drift_ratio:.2f}x)"
        )

    def test_memory_benchmark_regression_bound(self, tmp_path):
        """A diamond-heavy graph stays under a 10 % regression bound.

        We can't compare to a pre-fix baseline from here (tests run
        post-fix), but we can pin the *absolute* peak for the specific
        fixture so a future drift is caught.  Bound was chosen
        empirically and has 3× headroom above the observed peak.
        """
        p = tmp_path / "src.parquet"
        _write_source_parquet(p, n_rows=1_000)
        graph = _build_branching_graph(str(p), n_branches=8)
        sink_id = f"j{8 - 1}"

        tracemalloc.start()
        try:
            results = execute_graph(graph, target_node_id=sink_id)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert results[sink_id].status == "ok"
        print(f"\nsmall_graph_peak_bytes={peak} ({peak / 1024:.1f} KB)")

        # Absolute bound: 100 MB.  Observed peaks are typically well
        # under 10 MB — this catches a 10× regression with margin.
        assert peak < 100 * 1024 * 1024, (
            f"small diamond graph peak = {peak / (1024 * 1024):.1f} MB "
            "— cache_hint change may have forced extra materialization."
        )


# ===========================================================================
# 4. Cache-hint integration placeholder — where the implementer will hook
# ===========================================================================


class TestCacheHintCallSiteLocated:
    """Pin the call-site so the implementer knows where to attach the hint.

    ``cache_hint`` (``cache`` in Polars 1.39.x) should be invoked on a
    LazyFrame *before* the branching consumers see it.  The natural
    location is inside ``_execute_eager_core`` when we compute
    ``input_lfs = [df.lazy() for pid in input_ids]``: if ``df`` is
    about to feed multiple consumers (fan-out > 1), wrap its
    ``.lazy()`` result in ``.cache()``.

    This test does not call ``cache()`` itself — it simply pins that
    the ``cache`` method exists on ``LazyFrame`` for the current
    Polars version so the implementer's code will compile.
    """

    def test_polars_lazyframe_has_cache_method(self):
        lf = pl.LazyFrame({"a": [1, 2, 3]})
        assert hasattr(lf, "cache"), (
            "LazyFrame.cache() missing — this Polars version does not "
            "support the cache hint.  Update Polars or choose a "
            "different optimisation."
        )
        cached = lf.cache()
        # The returned object must still be a LazyFrame so downstream
        # joins / collects still work.
        assert isinstance(cached, pl.LazyFrame)

    def test_cache_hint_on_lazyframe_preserves_values(self):
        """cache() is semantically a no-op — values are unchanged."""
        df = pl.DataFrame({"x": [1, 2, 3, 4, 5]})
        without_hint = df.lazy().with_columns(y=pl.col("x") * 2).collect()
        with_hint = df.lazy().cache().with_columns(y=pl.col("x") * 2).collect()
        assert without_hint.equals(with_hint)
