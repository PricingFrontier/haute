"""Comprehensive tests for haute._execute_lazy.

Covers:
  - _prune_live_switch_edges  — scenario-based edge pruning
  - _prepare_graph            — topo sort, parent building, id_to_name
  - _execute_lazy             — lazy execution path
  - _build_funcs              — function building for eager execution
  - _execute_eager_core       — eager execution with swallow_errors, timings, memory
  - _apply_selected_columns   — shared column-filter helper (D4)
  - EagerResult               — named tuple structure
"""

from __future__ import annotations

import polars as pl
import pytest

import haute.projection as projection_planner
from haute._execute_lazy import (
    EagerResult,
    _apply_selected_columns,
    _build_funcs,
    _execute_eager_core,
    _execute_lazy,
    _extract_error_line,
    _prepare_graph,
    _prune_live_switch_edges,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _port_edge(src: str, tgt: str, port: str) -> GraphEdge:
    return GraphEdge(
        id=f"e_{src}_{tgt}_{port}",
        source=src,
        target=tgt,
        sourceHandle=port,
    )


def _source_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_SOURCE),
    )


def _api_input_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.API_INPUT),
    )


def _transform_node(nid: str, label: str | None = None, **extra_config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.POLARS, config=extra_config),
    )


def _live_switch_node(nid: str, ism: dict[str, str], inputs: list[str] | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.LIVE_SWITCH,
            config={"input_scenario_map": ism, "inputs": inputs or []},
        ),
    )


def _simple_build_fn(node: GraphNode, source_names=None, **kwargs):
    """Minimal build_node_fn for testing."""
    nid = node.id
    nt = node.data.nodeType

    if nt == NodeType.DATA_SOURCE:
        return nid, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
    else:
        return nid, lambda *dfs: dfs[0].with_columns(y=pl.col("x") * 2), False


# ===========================================================================
# _prune_live_switch_edges
# ===========================================================================


class TestPruneLiveSwitchEdges:
    def test_no_live_switch_no_change(self):
        edges = [_e("a", "b")]
        node_map = {"a": _source_node("a"), "b": _transform_node("b")}
        result = _prune_live_switch_edges(edges, node_map, "live")
        assert result == edges

    def test_prunes_inactive_branch(self):
        """Live switch with two inputs; only the active scenario's edge survives."""
        edges = [_e("live_input", "sw"), _e("batch_input", "sw")]
        node_map = {
            "live_input": _source_node("live_input"),
            "batch_input": _source_node("batch_input"),
            "sw": _live_switch_node("sw", {"live_input": "live", "batch_input": "test_batch"}),
        }
        result = _prune_live_switch_edges(edges, node_map, "live")
        sources = [e.source for e in result]
        assert "live_input" in sources
        assert "batch_input" not in sources

    @pytest.mark.parametrize(
        ("scenario", "expected_edge_id"),
        [
            ("live", "e_api_sw_quotes"),
            ("batch", "e_api_sw_drivers"),
        ],
    )
    def test_routes_api_input_frames_individually_by_edge_name(
        self,
        scenario: str,
        expected_edge_id: str,
    ) -> None:
        """Two ports from one source remain independently selectable."""
        edges = [
            _port_edge("api", "sw", "quotes"),
            _port_edge("api", "sw", "drivers"),
        ]
        node_map = {
            "api": _api_input_node("api", "API Input"),
            "sw": _live_switch_node("sw", {"quotes": "live", "drivers": "batch"}),
        }

        result = _prune_live_switch_edges(edges, node_map, scenario)

        assert [edge.id for edge in result] == [expected_edge_id]

    def test_ordinary_parent_mapping_still_uses_sanitised_source_labels(self) -> None:
        edges = [_e("live_src", "sw"), _e("batch_src", "sw")]
        node_map = {
            "live_src": _source_node("live_src", "Live Source"),
            "batch_src": _source_node("batch_src", "Batch-Source"),
            "sw": _live_switch_node(
                "sw",
                {"Live_Source": "live", "Batch_Source": "batch"},
            ),
        }

        result = _prune_live_switch_edges(edges, node_map, "live")

        assert [edge.id for edge in result] == ["e_live_src_sw"]

    def test_keeps_both_when_scenario_not_in_map(self):
        """If active scenario is not in any ISM value, keep all edges (fallback)."""
        edges = [_e("a", "sw"), _e("b", "sw")]
        node_map = {
            "a": _source_node("a"),
            "b": _source_node("b"),
            "sw": _live_switch_node("sw", {"a": "live", "b": "batch"}),
        }
        result = _prune_live_switch_edges(edges, node_map, "unknown_scenario")
        assert len(result) == 2

    def test_empty_ism_keeps_all(self):
        edges = [_e("a", "sw")]
        node_map = {
            "a": _source_node("a"),
            "sw": _live_switch_node("sw", {}),
        }
        result = _prune_live_switch_edges(edges, node_map, "live")
        assert len(result) == 1

    def test_non_switch_edges_unaffected(self):
        """Edges not targeting a live_switch are never pruned."""
        edges = [_e("a", "b"), _e("live_input", "sw")]
        node_map = {
            "a": _source_node("a"),
            "b": _transform_node("b"),
            "live_input": _source_node("live_input"),
            "sw": _live_switch_node("sw", {"live_input": "live"}),
        }
        result = _prune_live_switch_edges(edges, node_map, "live")
        non_sw_edges = [e for e in result if e.target != "sw"]
        assert len(non_sw_edges) == 1
        assert non_sw_edges[0].source == "a"


# ===========================================================================
# _prepare_graph
# ===========================================================================


class TestPrepareGraph:
    def test_basic_preparation(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b")],
            edges=[_e("a", "b")],
        )
        node_map, order, parents, id_to_name = _prepare_graph(g)
        assert set(order) == {"a", "b"}
        assert parents["b"] == ["a"]
        assert parents["a"] == []
        assert id_to_name["a"] == "a"
        assert id_to_name["b"] == "b"

    def test_target_filters_to_ancestors(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("a", "c")],
        )
        _, order, _, _ = _prepare_graph(g, target_node_id="b")
        assert set(order) == {"a", "b"}
        assert "c" not in order

    def test_scenario_passed_to_prune(self):
        """Verify scenario is used for live_switch pruning."""
        g = PipelineGraph(
            nodes=[
                _source_node("live"),
                _source_node("batch"),
                _live_switch_node("sw", {"live": "live", "batch": "test_batch"}),
            ],
            edges=[_e("live", "sw"), _e("batch", "sw")],
        )
        _, order, parents, _ = _prepare_graph(g, source="live")
        # "batch" should not be a parent of "sw" in live scenario
        assert "batch" not in parents.get("sw", [])

    @pytest.mark.parametrize(
        ("scenario", "expected_edge_id"),
        [
            ("live", "e_api_sw_quotes"),
            ("batch", "e_api_sw_drivers"),
        ],
    )
    def test_projection_preparation_retains_only_the_selected_api_frame_edge(
        self,
        scenario: str,
        expected_edge_id: str,
    ) -> None:
        """Projection preparation must preserve the per-edge liveSwitch choice."""
        graph = PipelineGraph(
            nodes=[
                _api_input_node("api", "API Input"),
                _live_switch_node("sw", {"quotes": "live", "drivers": "batch"}),
            ],
            edges=[
                _port_edge("api", "sw", "quotes"),
                _port_edge("api", "sw", "drivers"),
            ],
        )

        prepared = projection_planner.prepare_graph(graph, target_node_id="sw", source=scenario)

        assert [edge.id for edge in prepared.relevant_edges] == [expected_edge_id]

    def test_id_to_name_sanitizes_labels(self):
        g = PipelineGraph(
            nodes=[
                GraphNode(id="n1", data=NodeData(label="My Node", nodeType=NodeType.DATA_SOURCE)),
            ],
            edges=[],
        )
        _, _, _, id_to_name = _prepare_graph(g)
        assert id_to_name["n1"] == "My_Node"


# ===========================================================================
# _execute_lazy
# ===========================================================================


class TestExecuteLazy:
    def test_basic_lazy_chain(self):
        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        outputs, order, parents, id_to_name = _execute_lazy(g, _simple_build_fn)
        assert isinstance(outputs["t"], pl.LazyFrame)
        df = outputs["t"].collect()
        assert "y" in df.columns
        assert df["y"].to_list() == [2, 4, 6]

    def test_dataframe_auto_converted_to_lazy(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        outputs, _, _, _ = _execute_lazy(g, build_fn)
        assert isinstance(outputs["src"], pl.LazyFrame)

    def test_non_source_with_no_input_raises(self):
        def build_fn(node, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_transform_node("lonely")],
            edges=[],
        )
        with pytest.raises(ValueError, match="No input data available"):
            _execute_lazy(g, build_fn)

    def test_target_node_filters_execution(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("b", "c")],
        )
        outputs, _, _, _ = _execute_lazy(g, _simple_build_fn, target_node_id="b")
        assert "b" in outputs
        assert "c" not in outputs

    def test_preamble_ns_forwarded(self):
        """preamble_ns should be passed through to build_node_fn."""
        captured = {}

        def build_fn(node, **kwargs):
            captured.update(kwargs)
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("s")],
            edges=[],
        )
        _execute_lazy(g, build_fn, preamble_ns={"helper": lambda x: x})
        assert "preamble_ns" in captured


# ===========================================================================
# EagerResult
# ===========================================================================


# ===========================================================================
# _build_funcs
# ===========================================================================


class TestBuildFuncs:
    def test_builds_funcs_for_all_nodes(self):
        node_map = {"a": _source_node("a"), "b": _transform_node("b")}
        order = ["a", "b"]
        parents_of = {"a": [], "b": ["a"]}
        id_to_name = {"a": "a", "b": "b"}
        all_parents = {"b": ["a"]}

        funcs = _build_funcs(
            order,
            node_map,
            parents_of,
            id_to_name,
            all_parents,
            _simple_build_fn,
        )
        assert "a" in funcs
        assert "b" in funcs
        fn_a, is_source_a = funcs["a"]
        fn_b, is_source_b = funcs["b"]
        assert is_source_a is True
        assert is_source_b is False

    def test_row_limit_forwarded(self):
        captured_kwargs = {}

        def build_fn(node, **kwargs):
            captured_kwargs[node.id] = kwargs
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        node_map = {"a": _source_node("a")}
        _build_funcs(
            ["a"],
            node_map,
            {"a": []},
            {"a": "a"},
            {},
            build_fn,
            row_limit=100,
        )
        assert captured_kwargs["a"]["row_limit"] == 100

    def test_scenario_forwarded(self):
        captured_kwargs = {}

        def build_fn(node, **kwargs):
            captured_kwargs[node.id] = kwargs
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        node_map = {"a": _source_node("a")}
        _build_funcs(
            ["a"],
            node_map,
            {"a": []},
            {"a": "a"},
            {},
            build_fn,
            source="test_batch",
        )
        assert captured_kwargs["a"]["source"] == "test_batch"


# ===========================================================================
# _execute_eager_core
# ===========================================================================


class TestExecuteEagerCore:
    def test_basic_eager_execution(self):
        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        assert isinstance(result, EagerResult)
        assert result.outputs["src"] is not None
        assert result.outputs["t"] is not None
        assert isinstance(result.outputs["t"], pl.DataFrame)
        assert "y" in result.outputs["t"].columns

    def test_timings_populated(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        assert "src" in result.timings
        assert result.timings["src"] >= 0

    def test_memory_bytes_populated(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        assert "src" in result.memory_bytes
        assert result.memory_bytes["src"] > 0

    def test_swallow_errors_true_captures_error(self):
        """With swallow_errors=True, errors are captured, not raised."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def failing_fn(*dfs):
                raise RuntimeError("intentional test error")

            return node.id, failing_fn, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = _execute_eager_core(g, build_fn, swallow_errors=True)
        assert "t" in result.errors
        assert "intentional test error" in result.errors["t"]
        assert result.outputs["t"] is None

    def test_swallow_errors_false_raises(self):
        """With swallow_errors=False (default), errors are raised."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def failing_fn(*dfs):
                raise RuntimeError("boom")

            return node.id, failing_fn, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        with pytest.raises(RuntimeError, match="boom"):
            _execute_eager_core(g, build_fn, swallow_errors=False)

    def test_row_limit_applied_to_lazy_source(self):
        """row_limit should head-truncate source LazyFrames."""

        def build_fn(node, **kwargs):
            return node.id, lambda: pl.DataFrame({"x": list(range(100))}).lazy(), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = _execute_eager_core(g, build_fn, row_limit=5)
        assert len(result.outputs["src"]) == 5

    def test_target_node_filters(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("b", "c")],
        )
        result = _execute_eager_core(g, _simple_build_fn, target_node_id="b")
        assert "b" in result.outputs
        assert "c" not in result.outputs

    def test_non_source_no_input_raises_eagerly(self):
        def build_fn(node, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_transform_node("lonely")],
            edges=[],
        )
        with pytest.raises(ValueError, match="No input data available"):
            _execute_eager_core(g, build_fn)

    def test_eager_handles_dataframe_source(self):
        """A source that returns a DataFrame (not LazyFrame) should work."""

        def build_fn(node, **kwargs):
            return node.id, lambda: pl.DataFrame({"x": [1, 2]}), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = _execute_eager_core(g, build_fn)
        assert isinstance(result.outputs["src"], pl.DataFrame)
        assert len(result.outputs["src"]) == 2

    def test_scenario_forwarded_to_build_fn(self):
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs.get("source")
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        g = PipelineGraph(nodes=[_source_node("s")], edges=[])
        _execute_eager_core(g, build_fn, source="test_batch")
        assert captured["s"] == "test_batch"

    def test_multiple_errors_captured_with_swallow(self):
        """Multiple node failures are all captured."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def fail(*dfs):
                raise ValueError(f"fail_{node.id}")

            return node.id, fail, False

        g = PipelineGraph(
            nodes=[
                _source_node("s"),
                _transform_node("t1"),
                _transform_node("t2"),
            ],
            edges=[_e("s", "t1"), _e("s", "t2")],
        )
        result = _execute_eager_core(g, build_fn, swallow_errors=True)
        assert "t1" in result.errors
        assert "t2" in result.errors


# ═══════════════════════════════════════════════════════════════════════════
# _extract_error_line
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractErrorLine:
    """Tests for _extract_error_line helper."""

    def test_syntax_error_with_lineno(self):
        exc = SyntaxError("invalid syntax")
        exc.lineno = 5
        assert _extract_error_line(exc) == 5

    def test_syntax_error_without_lineno(self):
        exc = SyntaxError("unexpected EOF")
        exc.lineno = None
        assert _extract_error_line(exc) is None

    def test_runtime_error_with_line_in_message(self):
        exc = ValueError("name 'foo' is not defined (line 3)")
        assert _extract_error_line(exc) == 3

    def test_runtime_error_without_line_info(self):
        exc = TypeError("unsupported operand type")
        assert _extract_error_line(exc) is None

    def test_no_match_on_partial_word(self):
        """'inline' or 'pipeline' should not match."""
        exc = ValueError("inline processing failed")
        assert _extract_error_line(exc) is None

    def test_traceback_style_message(self):
        exc = RuntimeError('File "<string>", line 7, in <module>')
        assert _extract_error_line(exc) == 7


# ═══════════════════════════════════════════════════════════════════════════
# error_lines in _execute_eager_core
# ═══════════════════════════════════════════════════════════════════════════


class TestEagerCoreErrorLines:
    """Test that error_lines is populated in EagerResult."""

    def test_syntax_error_populates_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def bad_syntax(*dfs):
                exc = SyntaxError("bad syntax")
                exc.lineno = 3
                raise exc

            return node.id, bad_syntax, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = _execute_eager_core(g, build_fn, swallow_errors=True)
        assert result.error_lines["t"] == 3

    def test_runtime_error_with_line_populates_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def bad_runtime(*dfs):
                raise ValueError("error on line 5")

            return node.id, bad_runtime, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = _execute_eager_core(g, build_fn, swallow_errors=True)
        assert result.error_lines["t"] == 5

    def test_error_without_line_not_in_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def no_line(*dfs):
                raise TypeError("unsupported operand")

            return node.id, no_line, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = _execute_eager_core(g, build_fn, swallow_errors=True)
        assert "t" not in result.error_lines

    def test_successful_node_not_in_error_lines(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        assert result.error_lines == {}


# ═══════════════════════════════════════════════════════════════════════════
# Checkpoint multi-input nodes (joins) — Polars pola-rs/polars#24206
# ═══════════════════════════════════════════════════════════════════════════


def _join_build_fn(node: GraphNode, source_names=None, **kwargs):
    """Build function that supports multi-input (join) nodes."""
    nid = node.id
    if node.data.nodeType == NodeType.DATA_SOURCE:
        data = {
            "s1": {"key": [1, 2], "a": [10, 20]},
            "s2": {"key": [1, 2], "b": [30, 40]},
            "s3": {"key": [1, 2], "c": [50, 60]},
        }
        frame_data = data.get(nid, {"key": [1]})
        return nid, lambda d=frame_data: pl.DataFrame(d).lazy(), True

    def join_fn(*dfs):
        result = dfs[0]
        for df in dfs[1:]:
            result = result.join(df, on="key", how="left")
        return result

    return nid, join_fn, False


class TestCheckpointing:
    """Tests for checkpoint_dir parameter on _execute_lazy."""

    def test_checkpoint_creates_file_for_multi_input(self, tmp_path):
        """Multi-input (join) nodes produce checkpoint parquet files."""
        g = PipelineGraph(
            nodes=[_source_node("s1"), _source_node("s2"), _transform_node("j")],
            edges=[_e("s1", "j"), _e("s2", "j")],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        assert (tmp_path / "j.parquet").exists()
        df = outputs["j"].collect()
        assert set(df.columns) >= {"key", "a", "b"}
        assert len(df) == 2

    def test_required_columns_seed_projects_data_input_checkpoint(self, tmp_path):
        """A caller-owned data_input demand prevents terminal optimiser poisoning."""
        required = ["quote_id", "scenario_index", "scenario_value", "objective", "constraint"]

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.id == "quotes":
                return (
                    node.id,
                    lambda: pl.DataFrame(
                        {
                            "quote_id": ["q1", "q2"],
                            "scenario_index": [0, 1],
                            "scenario_value": [0.9, 1.1],
                            "objective": [10.0, 12.0],
                            "constraint": [2.0, 3.0],
                            "unused_quote_payload": ["drop", "drop"],
                        }
                    ).lazy(),
                    True,
                )
            if node.id == "lookup":
                return (
                    node.id,
                    lambda: pl.DataFrame(
                        {
                            "quote_id": ["q1", "q2"],
                            "unused_lookup_payload": [100, 200],
                        }
                    ).lazy(),
                    True,
                )
            if node.id == "join":
                return node.id, lambda *dfs: dfs[0].join(dfs[1], on="quote_id", how="left"), False
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("quotes"),
                _source_node("lookup"),
                _live_switch_node("join", {}),
                _live_switch_node("data_input", {}),
                GraphNode(
                    id="opt",
                    data=NodeData(
                        label="optimiser",
                        nodeType=NodeType.OPTIMISER,
                        config={"data_input": "data_input"},
                    ),
                ),
            ],
            edges=[
                _e("quotes", "join"),
                _e("lookup", "join"),
                _e("join", "data_input"),
                _e("data_input", "opt"),
            ],
        )

        outputs, *_ = _execute_lazy(
            g,
            build_fn,
            target_node_id="opt",
            checkpoint_dir=tmp_path,
            required_columns_by_node={"data_input": required},
        )

        checkpoint_cols = pl.read_parquet(tmp_path / "join.parquet").columns
        assert checkpoint_cols == required
        assert outputs["data_input"].collect().columns == required

    def test_no_checkpoint_for_single_input(self, tmp_path):
        """Single-input transform nodes are NOT checkpointed."""
        g = PipelineGraph(
            nodes=[_source_node("s1"), _transform_node("t")],
            edges=[_e("s1", "t")],
        )
        _execute_lazy(g, _simple_build_fn, checkpoint_dir=tmp_path)

        assert not list(tmp_path.glob("*.parquet"))

    def test_no_checkpoint_without_dir(self):
        """Without checkpoint_dir, multi-input nodes stay lazy (no files)."""
        g = PipelineGraph(
            nodes=[_source_node("s1"), _source_node("s2"), _transform_node("j")],
            edges=[_e("s1", "j"), _e("s2", "j")],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn)  # no checkpoint_dir

        df = outputs["j"].collect()
        assert set(df.columns) >= {"key", "a", "b"}

    def test_multi_parent_contract_inputs_use_union_of_parent_columns(self):
        """Lazy input contract checks must match eager multi-parent semantics.

        A transform can legitimately read a column supplied by its second
        parent, for example after joining lookup data. The contract check
        should validate against the union of direct parent schemas, not only
        the first parent.
        """

        def build_fn(node: GraphNode, source_names=None, **kwargs):
            if node.id == "s1":
                return node.id, lambda: pl.DataFrame({"key": [1], "a": [10]}).lazy(), True
            if node.id == "s2":
                return node.id, lambda: pl.DataFrame({"key": [1], "b": [30]}).lazy(), True

            def use_second_parent(_left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                return right.with_columns(b2=pl.col("b") * 2)

            return node.id, use_second_parent, False

        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node(
                    "t",
                    contract={"inputs": ["b"], "outputs": ["b2"]},
                ),
            ],
            edges=[_e("s1", "t"), _e("s2", "t")],
        )

        eager = _execute_eager_core(g, build_fn, enforce_contracts=True).outputs["t"]
        lazy_outputs, *_ = _execute_lazy(g, build_fn, enforce_contracts=True)
        lazy = lazy_outputs["t"].collect()

        assert eager["b2"].to_list() == [60]
        assert lazy["b2"].to_list() == [60]

    def test_source_nodes_not_checkpointed(self, tmp_path):
        """Source nodes are never checkpointed."""
        g = PipelineGraph(
            nodes=[_source_node("s1")],
            edges=[],
        )
        _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        assert not list(tmp_path.glob("*.parquet"))

    def test_chained_joins_all_checkpointed(self, tmp_path):
        """Both join nodes in s1+s2→j1, j1+s3→j2 should be checkpointed."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _source_node("s3"),
                _transform_node("j1"),
                _transform_node("j2"),
            ],
            edges=[
                _e("s1", "j1"),
                _e("s2", "j1"),
                _e("j1", "j2"),
                _e("s3", "j2"),
            ],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        checkpoint_names = sorted(f.name for f in tmp_path.glob("*.parquet"))
        assert checkpoint_names == ["j1.parquet", "j2.parquet"]

        df = outputs["j2"].collect()
        assert set(df.columns) >= {"key", "a", "b", "c"}
        assert len(df) == 2

    def test_checkpoint_with_selected_columns(self, tmp_path):
        """selected_columns filtering should apply before checkpointing."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("j", selected_columns=["key", "a"]),
            ],
            edges=[_e("s1", "j"), _e("s2", "j")],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        df = outputs["j"].collect()
        assert df.columns == ["key", "a"]

    def test_live_switch_multi_parent_checkpointed(self, tmp_path):
        """live_switch with 2 parents IS checkpointed (multi-input trigger).

        Uses a scenario not in the ISM so edge pruning keeps both parents.
        """

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("live_in"),
                _source_node("batch_in"),
                _live_switch_node("sw", {"live_in": "live", "batch_in": "batch"}),
            ],
            edges=[_e("live_in", "sw"), _e("batch_in", "sw")],
        )
        # scenario="unknown" keeps both edges (ISM fallback)
        _execute_lazy(g, build_fn, checkpoint_dir=tmp_path, source="unknown")

        assert (tmp_path / "sw.parquet").exists()

    def test_live_switch_single_parent_single_child_not_checkpointed(self, tmp_path):
        """live_switch with 1 parent and 1 child — NOT checkpointed."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1, 2]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                _source_node("live_in"),
                _live_switch_node("sw", {"live_in": "live"}, inputs=["live_in"]),
                _transform_node("t"),
            ],
            edges=[_e("live_in", "sw"), _e("sw", "t")],
        )
        _execute_lazy(g, build_fn, checkpoint_dir=tmp_path, source="live")

        assert not list(tmp_path.glob("*.parquet"))

    def test_fanout_node_checkpointed(self, tmp_path):
        """A node with 1 parent but 2+ children is checkpointed (fan-out)."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _transform_node("mid"),
                _transform_node("c1"),
                _transform_node("c2"),
            ],
            edges=[_e("s1", "mid"), _e("mid", "c1"), _e("mid", "c2")],
        )
        _execute_lazy(g, _simple_build_fn, checkpoint_dir=tmp_path)

        # mid has 2 children → checkpointed
        assert (tmp_path / "mid.parquet").exists()
        # c1 and c2 have 1 parent, 0 children → NOT checkpointed
        assert not (tmp_path / "c1.parquet").exists()
        assert not (tmp_path / "c2.parquet").exists()

    def test_fanout_data_preserved(self, tmp_path):
        """Fan-out checkpoint preserves correct data for all children."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _transform_node("mid"),
                _transform_node("c1"),
                _transform_node("c2"),
            ],
            edges=[_e("s1", "mid"), _e("mid", "c1"), _e("mid", "c2")],
        )
        outputs, *_ = _execute_lazy(g, _simple_build_fn, checkpoint_dir=tmp_path)

        df_c1 = outputs["c1"].collect()
        df_c2 = outputs["c2"].collect()
        # Both children should see the same data from mid's checkpoint
        assert df_c1["y"].to_list() == df_c2["y"].to_list()

    def test_feeds_join_node_checkpointed(self, tmp_path):
        """A node that feeds into a multi-input (join) node is checkpointed.

        Graph: s1 → t → join ← s2
        t has 1 parent, 1 child, but that child is a join → checkpoint t.
        """
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("t"),
                _transform_node("join"),
            ],
            edges=[_e("s1", "t"), _e("t", "join"), _e("s2", "join")],
        )
        _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        # t feeds a join → checkpointed
        assert (tmp_path / "t.parquet").exists()
        # join is multi-input → checkpointed
        assert (tmp_path / "join.parquet").exists()

    def test_feeds_join_data_correct(self, tmp_path):
        """Checkpoint of join-feeder preserves correct results."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("t"),
                _transform_node("join"),
            ],
            edges=[_e("s1", "t"), _e("t", "join"), _e("s2", "join")],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        df = outputs["join"].collect()
        assert "key" in df.columns
        assert len(df) == 2

    def test_no_checkpoint_for_leaf_single_parent(self, tmp_path):
        """A leaf node with 1 parent, no children, not feeding a join → NOT checkpointed."""
        g = PipelineGraph(
            nodes=[_source_node("s1"), _transform_node("t"), _transform_node("leaf")],
            edges=[_e("s1", "t"), _e("t", "leaf")],
        )
        _execute_lazy(g, _simple_build_fn, checkpoint_dir=tmp_path)

        assert not list(tmp_path.glob("*.parquet"))

    def test_parent_lazyframe_cleaned_up_after_all_consumers_checkpointed(self, tmp_path):
        """After both children of S are checkpointed, S should be removed from lazy_outputs.

        Graph:  S → J1 ← s2
                S → J2 ← s3

        S has 2 children (fan-out) so it is checkpointed.  Then J1 and J2
        are both multi-input nodes (joins) so they are also checkpointed.
        Once both J1 and J2 have been checkpointed, S's remaining consumer
        count hits 0 — its LazyFrame reference should be dropped from
        lazy_outputs to free Polars/Rust Arrow buffers.
        """
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _source_node("s3"),
                _transform_node("mid"),  # fan-out: feeds both j1 and j2
                _transform_node("j1"),  # join: mid + s2
                _transform_node("j2"),  # join: mid + s3
            ],
            edges=[
                _e("s1", "mid"),
                _e("mid", "j1"),
                _e("s2", "j1"),
                _e("mid", "j2"),
                _e("s3", "j2"),
            ],
        )
        outputs, *_ = _execute_lazy(g, _join_build_fn, checkpoint_dir=tmp_path)

        # mid is a fan-out node (2 children, both joins) → checkpointed
        assert (tmp_path / "mid.parquet").exists()
        # j1 and j2 are multi-input → checkpointed
        assert (tmp_path / "j1.parquet").exists()
        assert (tmp_path / "j2.parquet").exists()

        # After both consumers of mid have been checkpointed, mid's
        # LazyFrame should have been evicted from lazy_outputs.
        assert "mid" not in outputs, (
            "Parent LazyFrame 'mid' should be cleaned up after all consumers have been checkpointed"
        )

        # The final outputs (j1, j2) should still be present and correct
        df_j1 = outputs["j1"].collect()
        df_j2 = outputs["j2"].collect()
        assert set(df_j1.columns) >= {"key", "a", "b"}
        assert set(df_j2.columns) >= {"key", "a", "c"}
        assert len(df_j1) == 2
        assert len(df_j2) == 2


# ═══════════════════════════════════════════════════════════════════════════
# D4: _apply_selected_columns helper
# ═══════════════════════════════════════════════════════════════════════════


class TestApplySelectedColumns:
    """Tests for the shared _apply_selected_columns helper."""

    def test_lazyframe_selects_valid_columns(self):
        """LazyFrame: only valid selected_columns are kept."""
        lf = pl.DataFrame({"a": [1], "b": [2], "c": [3]}).lazy()
        result = _apply_selected_columns(lf, {"selected_columns": ["a", "c"]})
        assert isinstance(result, pl.LazyFrame)
        df = result.collect()
        assert df.columns == ["a", "c"]

    def test_dataframe_selects_valid_columns(self):
        """DataFrame: only valid selected_columns are kept."""
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "c"]})
        assert isinstance(result, pl.DataFrame)
        assert result.columns == ["a", "c"]

    def test_no_selected_columns_returns_unchanged(self):
        """Missing or None selected_columns returns the frame unchanged."""
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = _apply_selected_columns(lf, {})
        schema = result.collect_schema().names()
        assert schema == ["a", "b"]

    def test_empty_selected_columns_returns_unchanged(self):
        """Empty list selected_columns returns the frame unchanged."""
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = _apply_selected_columns(lf, {"selected_columns": []})
        schema = result.collect_schema().names()
        assert schema == ["a", "b"]

    def test_all_columns_selected_returns_unchanged(self):
        """When all columns are in selected_columns, no projection is applied."""
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "b"]})
        # Should be the exact same object (no unnecessary projection)
        assert result.columns == ["a", "b"]

    def test_nonexistent_columns_ignored(self):
        """Columns in selected_columns that don't exist are silently skipped."""
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "missing"]})
        assert result.columns == ["a"]

    def test_all_nonexistent_returns_unchanged(self):
        """If no selected_columns exist in the frame, frame is unchanged."""
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["x", "y"]})
        assert result.columns == ["a", "b"]

    def test_preserves_data_values(self):
        """Verify data integrity after column filtering."""
        df = pl.DataFrame({"a": [10, 20], "b": [30, 40], "c": [50, 60]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "c"]})
        assert result["a"].to_list() == [10, 20]
        assert result["c"].to_list() == [50, 60]


# ═══════════════════════════════════════════════════════════════════════════
# D3: _execute_lazy delegates to _build_funcs
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteLazyDelegatesToBuildFuncs:
    """Verify that _execute_lazy uses _build_funcs (not inline loop)."""

    def test_row_limit_none_forwarded(self):
        """_execute_lazy should pass row_limit=None to _build_funcs."""
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        _execute_lazy(g, build_fn)
        # _build_funcs always passes row_limit — lazy path sends None
        assert captured["src"]["row_limit"] is None

    def test_node_map_always_forwarded(self):
        """_execute_lazy should always pass node_map to build_node_fn (via _build_funcs)."""
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs
            if node.data.nodeType == NodeType.DATA_SOURCE:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        _execute_lazy(g, build_fn)
        # node_map should always be passed (not conditionally)
        assert "node_map" in captured["src"]
        assert "node_map" in captured["t"]

    def test_preamble_ns_forwarded_even_when_none(self):
        """_execute_lazy should pass preamble_ns through to _build_funcs."""
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        _execute_lazy(g, build_fn, preamble_ns=None)
        # preamble_ns is always forwarded (even if None)
        assert "preamble_ns" in captured["src"]

    def test_scenario_forwarded(self):
        """_execute_lazy should forward the scenario to _build_funcs."""
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        _execute_lazy(g, build_fn, source="test_batch")
        assert captured["src"]["source"] == "test_batch"

    def test_lazy_execution_still_works_after_refactor(self):
        """End-to-end: lazy chain still works after switching to _build_funcs."""
        g = PipelineGraph(
            nodes=[_source_node("s"), _transform_node("t")],
            edges=[_e("s", "t")],
        )
        outputs, order, parents, id_to_name = _execute_lazy(g, _simple_build_fn)
        df = outputs["t"].collect()
        assert "y" in df.columns
        assert df["y"].to_list() == [2, 4, 6]


# ═══════════════════════════════════════════════════════════════════════════
# D4: selected_columns applied consistently in lazy and eager paths
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectedColumnsInPaths:
    """Verify selected_columns filtering works in both lazy and eager execution."""

    def test_lazy_path_applies_selected_columns(self):
        """_execute_lazy applies selected_columns using _apply_selected_columns."""
        g = PipelineGraph(
            nodes=[
                _source_node("s"),
                _transform_node("t", selected_columns=["x"]),
            ],
            edges=[_e("s", "t")],
        )
        outputs, *_ = _execute_lazy(g, _simple_build_fn)
        df = outputs["t"].collect()
        # Only "x" should survive (not "y" which is added by transform)
        assert df.columns == ["x"]

    def test_eager_path_applies_selected_columns(self):
        """_execute_eager_core applies selected_columns using _apply_selected_columns."""
        g = PipelineGraph(
            nodes=[
                _source_node("s"),
                _transform_node("t", selected_columns=["x"]),
            ],
            edges=[_e("s", "t")],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        df = result.outputs["t"]
        assert df.columns == ["x"]

    def test_eager_available_columns_captured_before_filter(self):
        """Eager path captures available_columns BEFORE applying selected_columns filter."""
        g = PipelineGraph(
            nodes=[
                _source_node("s"),
                _transform_node("t", selected_columns=["x"]),
            ],
            edges=[_e("s", "t")],
        )
        result = _execute_eager_core(g, _simple_build_fn)
        # available_columns should have all columns (before filtering)
        col_names = [name for name, _ in result.available_columns["t"]]
        assert "x" in col_names
        assert "y" in col_names
        # But the actual output should be filtered
        assert result.outputs["t"].columns == ["x"]
