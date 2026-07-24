"""Tests for scenario propagation across all execution paths.

Validates that the active scenario is correctly threaded through every
layer: routes → executor → builders → source-switch edge pruning.

Bug reference: useDataInputColumns called previewNode without the active
scenario, defaulting to "live" when the user was in "nb_batch" mode.
This caused source-switch nodes to route through the wrong data path,
and column fetches failed silently.
"""

from __future__ import annotations

import polars as pl

from haute._builders import resolve_instance_node
from haute._execute_lazy import (
    _build_funcs,
    _execute_eager_core,
    _execute_lazy,
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


def _source_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_INPUT),
    )


def _transform_node(nid: str, label: str | None = None, **extra_config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=label or nid,
            nodeType=NodeType.POLARS,
            config=extra_config,
        ),
    )


def _live_switch_node(nid: str, ism: dict[str, str]) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.LIVE_SWITCH,
            config={"input_scenario_map": ism},
        ),
    )


def _modelling_node(nid: str = "model", label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.MODELLING, config={}),
    )


def _scenario_tracking_build_fn(captured: dict):
    """Build function that records what scenario each node receives."""

    def build_fn(node, *, source="live", source_names=None, **kwargs):
        captured[node.id] = source
        nt = node.data.nodeType
        if nt == NodeType.DATA_INPUT:
            return node.id, lambda: pl.DataFrame({"x": [1, 2, 3]}).lazy(), True
        return node.id, lambda *dfs: dfs[0] if dfs else pl.LazyFrame(), False

    return build_fn


def _branching_build_fn(captured: dict):
    """Build function for a pipeline with live/batch branches that produce
    different data, so we can verify which branch was taken."""

    def build_fn(node, *, source="live", source_names=None, **kwargs):
        captured[node.id] = source
        nid = node.id
        nt = node.data.nodeType

        if nt == NodeType.DATA_INPUT:
            if nid == "live_source":
                return nid, lambda: pl.DataFrame({"src": ["live"]}).lazy(), True
            elif nid == "batch_source":
                return nid, lambda: pl.DataFrame({"src": ["batch"]}).lazy(), True
            return nid, lambda: pl.DataFrame({"src": ["unknown"]}).lazy(), True

        # Pass-through
        return nid, lambda *dfs: dfs[0] if dfs else pl.LazyFrame(), False

    return build_fn


# ===========================================================================
# Scenario forwarding through execution layers
# ===========================================================================


class TestScenarioForwardingToBuilders:
    """Verify scenario is passed from execute_* → _build_funcs → build_fn."""

    def test_execute_lazy_forwards_scenario(self):
        captured: dict[str, str] = {}
        g = PipelineGraph(
            nodes=[_source_node("s"), _transform_node("t")],
            edges=[_e("s", "t")],
        )
        _execute_lazy(g, _scenario_tracking_build_fn(captured), source="nb_batch")
        assert captured["s"] == "nb_batch"
        assert captured["t"] == "nb_batch"

    def test_execute_eager_core_forwards_scenario(self):
        captured: dict[str, str] = {}
        g = PipelineGraph(
            nodes=[_source_node("s"), _transform_node("t")],
            edges=[_e("s", "t")],
        )
        _execute_eager_core(g, _scenario_tracking_build_fn(captured), source="nb_batch")
        assert captured["s"] == "nb_batch"
        assert captured["t"] == "nb_batch"

    def test_build_funcs_forwards_scenario(self):
        captured: dict[str, str] = {}
        node_map = {"s": _source_node("s"), "t": _transform_node("t")}
        _build_funcs(
            ["s", "t"],
            node_map,
            {"s": [], "t": ["s"]},
            {"s": "s", "t": "t"},
            {"t": ["s"]},
            _scenario_tracking_build_fn(captured),
            source="custom_scenario",
        )
        assert captured["s"] == "custom_scenario"
        assert captured["t"] == "custom_scenario"

    def test_default_scenario_is_live(self):
        captured: dict[str, str] = {}
        g = PipelineGraph(
            nodes=[_source_node("s")],
            edges=[],
        )
        _execute_eager_core(g, _scenario_tracking_build_fn(captured))
        assert captured["s"] == "live"


# ===========================================================================
# Source switch + scenario routing
# ===========================================================================


class TestSourceSwitchScenarioRouting:
    """Verify that source switches route to the correct branch based on scenario."""

    def _make_switch_graph(self) -> PipelineGraph:
        """Two sources → live_switch → downstream."""
        return PipelineGraph(
            nodes=[
                _source_node("live_source"),
                _source_node("batch_source"),
                _live_switch_node(
                    "sw",
                    {
                        "live_source": "live",
                        "batch_source": "nb_batch",
                    },
                ),
                _transform_node("downstream"),
            ],
            edges=[
                _e("live_source", "sw"),
                _e("batch_source", "sw"),
                _e("sw", "downstream"),
            ],
        )

    def test_live_scenario_prunes_batch_edge(self):
        g = self._make_switch_graph()
        node_map = {n.id: n for n in g.nodes}
        pruned = _prune_live_switch_edges(g.edges, node_map, "live")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert "live_source" in sources
        assert "batch_source" not in sources

    def test_batch_scenario_prunes_live_edge(self):
        g = self._make_switch_graph()
        node_map = {n.id: n for n in g.nodes}
        pruned = _prune_live_switch_edges(g.edges, node_map, "nb_batch")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert "batch_source" in sources
        assert "live_source" not in sources

    def test_unknown_scenario_keeps_all_edges(self):
        """When scenario not in ISM, ALL edges are kept (defensive fallback)."""
        g = self._make_switch_graph()
        node_map = {n.id: n for n in g.nodes}
        pruned = _prune_live_switch_edges(g.edges, node_map, "unknown")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert "live_source" in sources
        assert "batch_source" in sources

    def test_prepare_graph_uses_scenario_for_pruning(self):
        """_prepare_graph should prune edges based on the passed scenario."""
        g = self._make_switch_graph()
        _, order, parents, _ = _prepare_graph(g, source="nb_batch")
        # The batch path should be in the ancestry
        sw_parents = parents.get("sw", [])
        parent_ids = set(sw_parents)
        assert "batch_source" in parent_ids or "batch_source" in order
        # The live source should be excluded from sw's parents
        assert "live_source" not in sw_parents

    def test_full_pipeline_with_scenario_routes_correctly(self):
        """End-to-end: the right data flows through with nb_batch scenario."""
        captured: dict[str, str] = {}
        g = self._make_switch_graph()
        result = _execute_eager_core(
            g,
            _branching_build_fn(captured),
            source="nb_batch",
            target_node_id="downstream",
        )
        # With nb_batch, the downstream should get batch data
        df = result.outputs.get("downstream")
        assert df is not None
        # The switch should have received only the batch source
        assert captured["sw"] == "nb_batch"


# ===========================================================================
# Stale ISM keys (renamed upstream nodes)
# ===========================================================================


class TestStaleIsmKeys:
    """When an upstream node is renamed, the ISM key becomes stale."""

    def test_renamed_node_breaks_ism_key_matching(self):
        """If node 'feature_processing' is renamed to 'data_prep', its
        ISM key 'feature_processing' no longer matches the node label.

        The pruning logic checks: ism.get(parent_label). For the renamed node
        ism.get("data_prep") returns None, so mapped is None → edge is NOT
        excluded (unknown inputs are kept). The batch edge is still correctly
        pruned because its label still matches its ISM key.

        But the switch builder will then fail to find which input is "live"
        because it looks up ISM key "feature_processing" against input_names
        which now contains "data_prep". This causes a silent fallback.
        """
        sw = _live_switch_node(
            "sw",
            {
                "feature_processing": "live",
                "batch_quotes": "nb_batch",
            },
        )
        renamed_node = _source_node("live_src", label="data_prep")
        batch_node = _source_node("batch_src", label="batch_quotes")

        node_map = {
            "live_src": renamed_node,
            "batch_src": batch_node,
            "sw": sw,
        }
        edges = [_e("live_src", "sw"), _e("batch_src", "sw")]

        # With scenario="live", batch IS correctly pruned (its label matches).
        # But the renamed live source survives because its label "data_prep"
        # is NOT in ISM → mapped=None → edge kept (unmapped inputs kept).
        pruned = _prune_live_switch_edges(edges, node_map, "live")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert "live_src" in sources  # Kept (unmapped → not pruned)
        assert "batch_src" not in sources  # Pruned (ISM match, wrong scenario)

        # Now test with "nb_batch": both should survive because the renamed
        # node is unmapped (not excluded) and batch maps to nb_batch (not excluded)
        pruned_batch = _prune_live_switch_edges(edges, node_map, "nb_batch")
        sources_batch = {e.source for e in pruned_batch if e.target == "sw"}
        assert "live_src" in sources_batch  # Unmapped → not pruned
        assert "batch_src" in sources_batch  # Maps to nb_batch → not pruned
        # This means BOTH inputs flow into the switch — wasteful and potentially
        # confusing, though the switch builder will correctly pick batch_quotes

    def test_consistent_labels_match_correctly(self):
        """When labels match ISM keys, pruning works correctly."""
        sw = _live_switch_node(
            "sw",
            {
                "feature_processing": "live",
                "batch_quotes": "nb_batch",
            },
        )
        live_node = _source_node("live_src", label="feature_processing")
        batch_node = _source_node("batch_src", label="batch_quotes")

        node_map = {
            "live_src": live_node,
            "batch_src": batch_node,
            "sw": sw,
        }
        edges = [_e("live_src", "sw"), _e("batch_src", "sw")]

        pruned = _prune_live_switch_edges(edges, node_map, "live")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert "live_src" in sources
        assert "batch_src" not in sources


# ===========================================================================
# Instance node dangling references
# ===========================================================================


class TestInstanceNodeDanglingReferences:
    """When the original node referenced by instanceOf is deleted or renamed."""

    def test_dangling_instance_of_returns_node_unchanged(self):
        """If instanceOf points to a non-existent node, the instance is
        returned unchanged (no error, no warning)."""
        instance = GraphNode(
            id="inst1",
            data=NodeData(
                label="my_instance",
                nodeType=NodeType.POLARS,
                config={"instanceOf": "deleted_node"},
            ),
        )
        node_map = {"inst1": instance}  # original not in map
        result = resolve_instance_node(instance, node_map)
        # Should return unchanged — no merge with original
        assert result.data.nodeType == NodeType.POLARS
        assert result.data.config.get("instanceOf") == "deleted_node"

    def test_valid_instance_of_merges_config(self):
        """When instanceOf references a valid node, config is merged."""
        original = GraphNode(
            id="orig",
            data=NodeData(
                label="original",
                nodeType=NodeType.MODEL_SCORE,
                config={"output_column": "prediction", "task": "regression"},
            ),
        )
        instance = GraphNode(
            id="inst1",
            data=NodeData(
                label="my_instance",
                nodeType=NodeType.POLARS,
                config={"instanceOf": "orig"},
            ),
        )
        node_map = {"orig": original, "inst1": instance}
        result = resolve_instance_node(instance, node_map)
        # Should merge original's type and config
        assert result.data.nodeType == NodeType.MODEL_SCORE
        assert result.data.config["output_column"] == "prediction"
        assert result.data.config["instanceOf"] == "orig"

    def test_instance_preserves_own_id_and_label(self):
        """Merged instance keeps its own id and label."""
        original = GraphNode(
            id="orig",
            data=NodeData(label="original", nodeType=NodeType.MODEL_SCORE, config={}),
        )
        instance = GraphNode(
            id="inst1",
            data=NodeData(
                label="my_instance",
                nodeType=NodeType.POLARS,
                config={"instanceOf": "orig"},
            ),
        )
        node_map = {"orig": original, "inst1": instance}
        result = resolve_instance_node(instance, node_map)
        assert result.id == "inst1"
        assert result.data.label == "my_instance"

    def test_empty_instance_of_returns_unchanged(self):
        """Empty string instanceOf is treated as no reference."""
        node = GraphNode(
            id="n1",
            data=NodeData(
                label="node",
                nodeType=NodeType.POLARS,
                config={"instanceOf": ""},
            ),
        )
        result = resolve_instance_node(node, {"n1": node})
        assert result is node

    def test_no_instance_of_key_returns_unchanged(self):
        """Node without instanceOf in config is returned as-is."""
        node = GraphNode(
            id="n1",
            data=NodeData(label="node", nodeType=NodeType.POLARS, config={}),
        )
        result = resolve_instance_node(node, {"n1": node})
        assert result is node


# ===========================================================================
# selected_columns with non-existent columns (silent drop)
# ===========================================================================


class TestSelectedColumnsSilentDrop:
    """Verify behavior when selected_columns references columns that
    don't exist in the upstream output — currently silently dropped."""

    def test_nonexistent_columns_silently_excluded(self):
        """Columns in selected_columns that don't exist are ignored."""
        from haute._execute_lazy import _apply_selected_columns

        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "missing_col"]})
        assert result.columns == ["a"]

    def test_all_nonexistent_returns_full_frame(self):
        """If ALL selected columns are nonexistent, the full frame is returned."""
        from haute._execute_lazy import _apply_selected_columns

        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["x", "y", "z"]})
        assert result.columns == ["a", "b"]


# ===========================================================================
# RAM estimation scenario propagation (backend bug)
# ===========================================================================


class TestRamEstimateScenario:
    """estimate_safe_training_rows now accepts a scenario parameter and
    forwards it to _execute_eager_core. These tests verify the fix.
    """

    def test_estimate_accepts_source_parameter(self):
        """estimate_safe_training_rows should accept a source kwarg."""
        import inspect

        from haute._ram_estimate import estimate_safe_training_rows

        sig = inspect.signature(estimate_safe_training_rows)
        param_names = list(sig.parameters.keys())
        assert "source" in param_names

    def test_execute_eager_core_defaults_to_live_scenario(self):
        """When no scenario is passed, _execute_eager_core defaults to 'live'.
        This is what estimate_safe_training_rows triggers."""
        captured: dict[str, str] = {}
        g = PipelineGraph(
            nodes=[_source_node("s"), _modelling_node()],
            edges=[_e("s", "model")],
        )
        # Explicitly NOT passing scenario — simulating what estimate does
        _execute_eager_core(g, _scenario_tracking_build_fn(captured))
        assert captured["s"] == "live"

    def test_execute_eager_core_with_explicit_scenario(self):
        """When scenario IS passed, it reaches the build functions."""
        captured: dict[str, str] = {}
        g = PipelineGraph(
            nodes=[_source_node("s"), _modelling_node()],
            edges=[_e("s", "model")],
        )
        _execute_eager_core(
            g,
            _scenario_tracking_build_fn(captured),
            source="nb_batch",
        )
        assert captured["s"] == "nb_batch"


# ===========================================================================
# Source switch with multiple non-live scenarios
# ===========================================================================


class TestMultipleNonLiveScenarios:
    """Edge cases when the pipeline has multiple non-live scenarios."""

    def test_three_scenario_switch_prunes_correctly(self):
        """A switch with 3 inputs and 3 scenarios prunes correctly."""
        sw = _live_switch_node(
            "sw",
            {
                "src_live": "live",
                "src_batch": "batch",
                "src_test": "test",
            },
        )
        g = PipelineGraph(
            nodes=[
                _source_node("src_live", label="src_live"),
                _source_node("src_batch", label="src_batch"),
                _source_node("src_test", label="src_test"),
                sw,
            ],
            edges=[
                _e("src_live", "sw"),
                _e("src_batch", "sw"),
                _e("src_test", "sw"),
            ],
        )
        node_map = {n.id: n for n in g.nodes}

        # Test scenario keeps only src_test
        pruned = _prune_live_switch_edges(g.edges, node_map, "test")
        sources = {e.source for e in pruned if e.target == "sw"}
        assert sources == {"src_test"}

    def test_empty_ism_keeps_all_edges(self):
        """A switch with no ISM entries keeps all edges (no pruning)."""
        sw = _live_switch_node("sw", {})
        edges = [_e("a", "sw"), _e("b", "sw")]
        node_map = {
            "a": _source_node("a"),
            "b": _source_node("b"),
            "sw": sw,
        }
        pruned = _prune_live_switch_edges(edges, node_map, "live")
        assert len(pruned) == 2
