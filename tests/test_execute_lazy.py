"""Comprehensive tests for haute._execute_lazy.

Covers:
  - _prune_live_switch_edges  — scenario-based edge pruning
  - prepare_graph             — topo sort, parent building, id_to_name
  - execute_lazy_graph        — lazy execution path (the walker's sink walk)
  - _build_funcs              — function building for eager execution
  - walk_graph (display)      — eager execution with recorded failures, timings, memory
  - _apply_selected_columns   — shared column-filter helper (D4)
"""

from __future__ import annotations

import polars as pl
import pytest

import haute._execute_lazy as execution_core
import haute._graph_walker as graph_walker
import haute.projection as projection_planner
from haute._execute_lazy import (
    NodeBoundaryRunner,
    PreparedExecutionRequest,
    _apply_selected_columns,
    _build_funcs,
    _extract_error_line,
    _prepare_execution,
    _prune_live_switch_edges,
)
from haute._execution_context import ExecutionProfile
from haute._graph_walker import CollectPolicy, WalkResult, walk_graph
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.execution import execute_lazy_graph

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
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_INPUT),
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

    if nt == NodeType.DATA_INPUT:
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
# prepare_graph
# ===========================================================================


class TestPrepareGraph:
    def test_basic_preparation(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b")],
            edges=[_e("a", "b")],
        )
        prepared = projection_planner.prepare_graph(g)
        assert set(prepared.order) == {"a", "b"}
        assert prepared.parents_of["b"] == ["a"]
        assert prepared.parents_of["a"] == []
        assert prepared.id_to_name["a"] == "a"
        assert prepared.id_to_name["b"] == "b"

    def test_target_filters_to_ancestors(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("a", "c")],
        )
        prepared = projection_planner.prepare_graph(g, target_node_id="b")
        assert set(prepared.order) == {"a", "b"}
        assert "c" not in prepared.order

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
        prepared = projection_planner.prepare_graph(g, source="live")
        # "batch" should not be a parent of "sw" in live scenario
        assert "batch" not in prepared.parents_of.get("sw", [])

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
                GraphNode(id="n1", data=NodeData(label="My Node", nodeType=NodeType.DATA_INPUT)),
            ],
            edges=[],
        )
        prepared = projection_planner.prepare_graph(g)
        assert prepared.id_to_name["n1"] == "My_Node"


class TestPreparedExecution:
    def test_owns_shared_identity_routing_and_contract_policy(self):
        edge_ab = _e("a", "b")
        edge_ac = _e("a", "c")
        graph = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[edge_ab, edge_ac],
        )

        prepared = _prepare_execution(
            PreparedExecutionRequest(
                graph=graph,
                target_node_id="b",
                source="live",
                required_columns_by_node={"b": {"x"}},
                profile=ExecutionProfile.PREVIEW_EAGER,
            )
        )

        assert prepared.graph_plan.order == ["a", "b"]
        assert prepared.graph_plan.id_to_name == {"a": "a", "b": "b"}
        assert prepared.normalised_required_columns == {"b": {"x"}}
        assert prepared.strict_contract_resolution is False
        assert prepared.children_count == {"a": 1, "b": 0}
        assert prepared.children_of == {"a": ("b",), "b": ()}
        assert prepared.incoming_edges_by_target == {"b": (edge_ab,)}
        assert prepared.all_incoming_edges_by_target == {
            "b": (edge_ab,),
            "c": (edge_ac,),
        }
        assert prepared.all_parents == {"a": [], "b": ["a"], "c": ["a"]}

    def test_node_boundary_runner_routes_and_invokes_prepared_inputs(self):
        edge = _e("source", "transform")
        graph = PipelineGraph(
            nodes=[_source_node("source"), _transform_node("transform")],
            edges=[edge],
        )
        prepared = _prepare_execution(PreparedExecutionRequest(graph=graph))
        runner = NodeBoundaryRunner(
            prepared=prepared,
            funcs={
                "source": (lambda: pl.DataFrame({"x": [1]}).lazy(), True),
                "transform": (lambda frame: frame.with_columns(y=pl.col("x") + 1), False),
            },
            enforce_contracts=False,
            execution_context=None,
            needed_columns={},
        )
        source_frame = pl.DataFrame({"x": [1]}).lazy()

        boundary = runner.open("transform")
        result = runner.invoke(boundary, [source_frame])

        assert boundary.parent_ids == ("source",)
        assert boundary.incoming_edges == (edge,)
        assert result.collect().to_dict(as_series=False) == {"x": [1], "y": [2]}

    def test_node_boundary_runner_rejects_missing_non_source_input(self):
        graph = PipelineGraph(
            nodes=[_source_node("source"), _transform_node("transform")],
            edges=[_e("source", "transform")],
        )
        prepared = _prepare_execution(PreparedExecutionRequest(graph=graph))
        runner = NodeBoundaryRunner(
            prepared=prepared,
            funcs={
                "source": (lambda: pl.DataFrame({"x": [1]}).lazy(), True),
                "transform": (lambda frame: frame, False),
            },
            enforce_contracts=False,
            execution_context=None,
            needed_columns={},
        )

        with pytest.raises(ValueError, match="transform"):
            runner.invoke(runner.open("transform"))

    def test_node_boundary_runner_skips_contract_assertions_when_disabled(self, monkeypatch):
        graph = PipelineGraph(
            nodes=[_source_node("source"), _transform_node("transform")],
            edges=[_e("source", "transform")],
        )
        prepared = _prepare_execution(PreparedExecutionRequest(graph=graph))
        runner = NodeBoundaryRunner(
            prepared=prepared,
            funcs={
                "source": (lambda: pl.DataFrame({"x": [1]}).lazy(), True),
                "transform": (lambda frame: frame, False),
            },
            enforce_contracts=False,
            execution_context=None,
            needed_columns={},
        )
        boundary = runner.open("transform")
        assert boundary.check_contract is False

        def unexpected(*_args, **_kwargs):
            raise AssertionError("contract assertion should be skipped")

        monkeypatch.setattr(execution_core, "_assert_inputs_satisfy_contract", unexpected)
        monkeypatch.setattr(execution_core, "_assert_outputs_satisfy_contract", unexpected)

        runner.assert_inputs(boundary, frozenset())
        runner.assert_outputs(boundary, frozenset())

    def test_both_engines_delegate_to_the_same_preparation_boundary(self, monkeypatch):
        graph = PipelineGraph(
            nodes=[_source_node("source"), _transform_node("transform")],
            edges=[_e("source", "transform")],
        )
        requests: list[PreparedExecutionRequest] = []
        prepare = execution_core._prepare_execution

        def capture(request: PreparedExecutionRequest):
            requests.append(request)
            return prepare(request)

        monkeypatch.setattr(execution_core, "_prepare_execution", capture)
        monkeypatch.setattr(graph_walker, "_prepare_execution", capture)

        execute_lazy_graph(graph, _simple_build_fn)
        walk_graph(graph, _simple_build_fn, policy=CollectPolicy.display())

        assert requests == [
            PreparedExecutionRequest(graph=graph),
            PreparedExecutionRequest(graph=graph),
        ]


# ===========================================================================
# _execute_lazy
# ===========================================================================


class TestExecuteLazy:
    def test_basic_lazy_chain(self):
        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        outputs, order, parents, id_to_name = execute_lazy_graph(g, _simple_build_fn)
        assert isinstance(outputs["t"], pl.LazyFrame)
        df = outputs["t"].collect()
        assert "y" in df.columns
        assert df["y"].to_list() == [2, 4, 6]

    def test_dataframe_auto_converted_to_lazy(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        outputs, _, _, _ = execute_lazy_graph(g, build_fn)
        assert isinstance(outputs["src"], pl.LazyFrame)

    def test_non_source_with_no_input_raises(self):
        def build_fn(node, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_transform_node("lonely")],
            edges=[],
        )
        with pytest.raises(ValueError, match="No input data available"):
            execute_lazy_graph(g, build_fn)

    def test_target_node_filters_execution(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("b", "c")],
        )
        outputs, _, _, _ = execute_lazy_graph(g, _simple_build_fn, target_node_id="b")
        assert "b" in outputs
        assert "c" not in outputs

    def test_preamble_ns_forwarded(self):
        """preamble_ns should be passed through to build_node_fn."""
        captured = {}

        def build_fn(node, **kwargs):
            captured.update(kwargs)
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("s")],
            edges=[],
        )
        execute_lazy_graph(g, build_fn, preamble_ns={"helper": lambda x: x})
        assert "preamble_ns" in captured


# ===========================================================================
# _build_funcs
# ===========================================================================


class TestBuildFuncs:
    def test_builds_funcs_for_all_nodes(self):
        node_map = {"a": _source_node("a"), "b": _transform_node("b")}
        order = ["a", "b"]
        id_to_name = {"a": "a", "b": "b"}
        all_parents = {"b": ["a"]}
        edge = _e("a", "b")
        incoming_edges = {"b": [edge]}

        funcs = _build_funcs(
            order,
            node_map,
            id_to_name,
            all_parents,
            _simple_build_fn,
            incoming_edges_by_target=incoming_edges,
            all_incoming_edges_by_target=incoming_edges,
            all_node_map=node_map,
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
            {"a": "a"},
            {},
            build_fn,
            incoming_edges_by_target={},
            all_incoming_edges_by_target={},
            all_node_map=node_map,
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
            {"a": "a"},
            {},
            build_fn,
            incoming_edges_by_target={},
            all_incoming_edges_by_target={},
            all_node_map=node_map,
            source="test_batch",
        )
        assert captured_kwargs["a"]["source"] == "test_batch"


# ===========================================================================
# Display walks (the eager execution preview and trace run)
# ===========================================================================


class TestExecuteEagerCore:
    def test_basic_eager_execution(self):
        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        assert isinstance(result, WalkResult)
        assert result.collected["src"] is not None
        assert result.collected["t"] is not None
        assert isinstance(result.collected["t"], pl.DataFrame)
        assert "y" in result.collected["t"].columns

    def test_timings_populated(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        assert "src" in result.timings
        assert result.timings["src"] >= 0

    def test_memory_bytes_populated(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        assert "src" in result.memory_bytes
        assert result.memory_bytes["src"] > 0

    def test_swallow_errors_true_captures_error(self):
        """With swallow_errors=True, errors are captured, not raised."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def failing_fn(*dfs):
                raise RuntimeError("intentional test error")

            return node.id, failing_fn, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=True))
        assert "t" in result.errors
        assert "intentional test error" in result.errors["t"]
        assert result.collected["t"] is None

    def test_swallow_errors_false_raises(self):
        """With swallow_errors=False (default), errors are raised."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def failing_fn(*dfs):
                raise RuntimeError("boom")

            return node.id, failing_fn, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        with pytest.raises(RuntimeError, match="boom"):
            walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=False))

    def test_row_limit_applied_to_lazy_source(self):
        """row_limit should head-truncate source LazyFrames."""

        def build_fn(node, **kwargs):
            return node.id, lambda: pl.DataFrame({"x": list(range(100))}).lazy(), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(row_limit=5))
        assert len(result.collected["src"]) == 5

    def test_target_node_filters(self):
        g = PipelineGraph(
            nodes=[_source_node("a"), _transform_node("b"), _transform_node("c")],
            edges=[_e("a", "b"), _e("b", "c")],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display(), target_node_id="b")
        assert "b" in result.collected
        assert "c" not in result.collected

    def test_non_source_no_input_raises_eagerly(self):
        def build_fn(node, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_transform_node("lonely")],
            edges=[],
        )
        with pytest.raises(ValueError, match="No input data available"):
            walk_graph(g, build_fn, policy=CollectPolicy.display())

    def test_eager_handles_dataframe_source(self):
        """A source that returns a DataFrame (not LazyFrame) should work."""

        def build_fn(node, **kwargs):
            return node.id, lambda: pl.DataFrame({"x": [1, 2]}), True

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = walk_graph(g, build_fn, policy=CollectPolicy.display())
        assert isinstance(result.collected["src"], pl.DataFrame)
        assert len(result.collected["src"]) == 2

    def test_scenario_forwarded_to_build_fn(self):
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs.get("source")
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        g = PipelineGraph(nodes=[_source_node("s")], edges=[])
        walk_graph(g, build_fn, policy=CollectPolicy.display(), source="test_batch")
        assert captured["s"] == "test_batch"

    def test_multiple_errors_captured_with_swallow(self):
        """Multiple node failures are all captured."""

        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
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
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=True))
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
# error_lines in a display walk
# ═══════════════════════════════════════════════════════════════════════════


class TestEagerCoreErrorLines:
    """Test that error_lines is populated in a display walk's result."""

    def test_syntax_error_populates_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
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
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=True))
        assert result.error_lines["t"] == 3

    def test_runtime_error_with_line_populates_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def bad_runtime(*dfs):
                raise ValueError("error on line 5")

            return node.id, bad_runtime, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=True))
        assert result.error_lines["t"] == 5

    def test_error_without_line_not_in_error_lines(self):
        def build_fn(node, **kwargs):
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

            def no_line(*dfs):
                raise TypeError("unsupported operand")

            return node.id, no_line, False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        result = walk_graph(g, build_fn, policy=CollectPolicy.display(record_failures=True))
        assert "t" not in result.error_lines

    def test_successful_node_not_in_error_lines(self):
        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        assert result.error_lines == {}


# ═══════════════════════════════════════════════════════════════════════════
# Checkpoint multi-input nodes (joins) — Polars pola-rs/polars#24206
# ═══════════════════════════════════════════════════════════════════════════


def _join_build_fn(node: GraphNode, source_names=None, **kwargs):
    """Build function that supports multi-input (join) nodes."""
    nid = node.id
    if node.data.nodeType == NodeType.DATA_INPUT:
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


class TestJoinsAndFanOuts:
    """Joins, fan-outs, and join feeders compute correctly on the lazy path.

    What a planned run captures at them — and with which columns — is tested in
    ``test_seed_plans.py`` (capture points) and ``test_capture_projection.py``.
    """

    def test_required_columns_seed_projects_data_input(self):
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

        outputs, *_ = execute_lazy_graph(
            g,
            build_fn,
            target_node_id="opt",
            required_columns_by_node={"data_input": required},
        )

        assert outputs["data_input"].collect().columns == required

    def test_join_output_without_a_plan(self):
        """Without a seed plan nothing is materialised; the join is computed lazily."""
        g = PipelineGraph(
            nodes=[_source_node("s1"), _source_node("s2"), _transform_node("j")],
            edges=[_e("s1", "j"), _e("s2", "j")],
        )
        outputs, *_ = execute_lazy_graph(g, _join_build_fn)

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

        eager = walk_graph(
            g, build_fn, policy=CollectPolicy.display(), enforce_contracts=True
        ).collected["t"]
        lazy_outputs, *_ = execute_lazy_graph(g, build_fn, enforce_contracts=True)
        lazy = lazy_outputs["t"].collect()

        assert eager["b2"].to_list() == [60]
        assert lazy["b2"].to_list() == [60]

    def test_chained_joins_compute_correctly(self):
        """s1+s2→j1, j1+s3→j2 carries every side's columns through both joins."""
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
        outputs, *_ = execute_lazy_graph(g, _join_build_fn)

        df = outputs["j2"].collect()
        assert set(df.columns) >= {"key", "a", "b", "c"}
        assert len(df) == 2

    def test_join_with_selected_columns(self):
        """selected_columns filtering applies to a join's output."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("j", selected_columns=["key", "a"]),
            ],
            edges=[_e("s1", "j"), _e("s2", "j")],
        )
        outputs, *_ = execute_lazy_graph(g, _join_build_fn)

        df = outputs["j"].collect()
        assert df.columns == ["key", "a"]

    def test_fanout_data_preserved(self):
        """A fan-out point gives every child the same data."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _transform_node("mid"),
                _transform_node("c1"),
                _transform_node("c2"),
            ],
            edges=[_e("s1", "mid"), _e("mid", "c1"), _e("mid", "c2")],
        )
        outputs, *_ = execute_lazy_graph(g, _simple_build_fn)

        df_c1 = outputs["c1"].collect()
        df_c2 = outputs["c2"].collect()
        # Both children see the same data from mid
        assert df_c1["y"].to_list() == df_c2["y"].to_list()

    def test_feeds_join_data_correct(self):
        """A join feeder's data reaches the join correctly."""
        g = PipelineGraph(
            nodes=[
                _source_node("s1"),
                _source_node("s2"),
                _transform_node("t"),
                _transform_node("join"),
            ],
            edges=[_e("s1", "t"), _e("t", "join"), _e("s2", "join")],
        )
        outputs, *_ = execute_lazy_graph(g, _join_build_fn)

        df = outputs["join"].collect()
        assert "key" in df.columns
        assert len(df) == 2


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
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src")],
            edges=[],
        )
        execute_lazy_graph(g, build_fn)
        # _build_funcs always passes row_limit — lazy path sends None
        assert captured["src"]["row_limit"] is None

    def test_node_map_always_forwarded(self):
        """_execute_lazy should always pass node_map to build_node_fn (via _build_funcs)."""
        captured = {}

        def build_fn(node, **kwargs):
            captured[node.id] = kwargs
            if node.data.nodeType == NodeType.DATA_INPUT:
                return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return node.id, lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[_source_node("src"), _transform_node("t")],
            edges=[_e("src", "t")],
        )
        execute_lazy_graph(g, build_fn)
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
        execute_lazy_graph(g, build_fn, preamble_ns=None)
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
        execute_lazy_graph(g, build_fn, source="test_batch")
        assert captured["src"]["source"] == "test_batch"

    def test_lazy_execution_still_works_after_refactor(self):
        """End-to-end: lazy chain still works after switching to _build_funcs."""
        g = PipelineGraph(
            nodes=[_source_node("s"), _transform_node("t")],
            edges=[_e("s", "t")],
        )
        outputs, order, parents, id_to_name = execute_lazy_graph(g, _simple_build_fn)
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
        outputs, *_ = execute_lazy_graph(g, _simple_build_fn)
        df = outputs["t"].collect()
        # Only "x" should survive (not "y" which is added by transform)
        assert df.columns == ["x"]

    def test_eager_path_applies_selected_columns(self):
        """A display walk applies selected_columns using _apply_selected_columns."""
        g = PipelineGraph(
            nodes=[
                _source_node("s"),
                _transform_node("t", selected_columns=["x"]),
            ],
            edges=[_e("s", "t")],
        )
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        df = result.collected["t"]
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
        result = walk_graph(g, _simple_build_fn, policy=CollectPolicy.display())
        # available_columns should have all columns (before filtering)
        col_names = [name for name, _ in result.available_columns["t"]]
        assert "x" in col_names
        assert "y" in col_names
        # But the actual output should be filtered
        assert result.collected["t"].columns == ["x"]
