"""Tests for the assistant graph-edit ops engine (``haute.assistant._ops``).

Spec: docs/specs/assistant/low-level.md — Key types (``GraphEditOp``) and
Edge cases.  The engine is a pure graph→graph function: ``parse_ops``
validates wire-shaped op dicts, ``apply_ops`` applies them in order
against a copy of the graph and returns the new graph.  Any validation
failure raises ``OpValidationError`` and the input graph is untouched
(all-or-nothing; the save never happens on a failed batch).

Authored test-first per CLAUDE.md TDD — the module is implemented to
make these pass.
"""

from __future__ import annotations

import pytest

from haute._graph_utils import _sanitize_func_name
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.assistant._ops import OpValidationError, apply_ops, parse_ops

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str = "polars", **config: object) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=dict(config)),
        position={"x": 0.0, "y": 0.0},
    )


def _edge(source: str, target: str, sh: str | None = None, th: str | None = None) -> GraphEdge:
    return GraphEdge(
        id=f"{source}->{target}:{sh}:{th}",
        source=source,
        target=target,
        sourceHandle=sh,
        targetHandle=th,
    )


def _graph(nodes: list[GraphNode], edges: list[GraphEdge] | None = None) -> PipelineGraph:
    return PipelineGraph(nodes=nodes, edges=edges or [])


def _apply(graph: PipelineGraph, raw_ops: list[dict]) -> PipelineGraph:
    return apply_ops(graph, parse_ops(raw_ops))


def _ids(graph: PipelineGraph) -> set[str]:
    return {n.id for n in graph.nodes}


def _get(graph: PipelineGraph, node_id: str) -> GraphNode:
    (node,) = [n for n in graph.nodes if n.id == node_id]
    return node


# ---------------------------------------------------------------------------
# parse_ops — wire validation
# ---------------------------------------------------------------------------


class TestParseOps:
    def test_unknown_op_type_rejected(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "explode_node", "node": "a"}])

    def test_missing_required_field_rejected(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "add_node", "name": "no type given"}])

    def test_valid_batch_parses(self):
        ops = parse_ops(
            [
                {"op": "add_node", "node_type": "polars", "name": "step one", "ref": "s1"},
                {"op": "update_preamble", "preamble": "X = 1"},
            ]
        )
        assert len(ops) == 2


# ---------------------------------------------------------------------------
# add_node
# ---------------------------------------------------------------------------


class TestAddNode:
    def test_adds_node_with_sanitized_id_and_label(self):
        out = _apply(
            _graph([]),
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "My Step",
                    "config": {"code": "df"},
                },
            ],
        )
        expected_id = _sanitize_func_name("My Step")
        node = _get(out, expected_id)
        assert node.data.label == "My Step"
        assert node.data.nodeType == "polars"
        assert node.data.config == {"code": "df"}

    def test_submodel_types_rejected(self):
        for bad in ("submodel", "submodelPort"):
            with pytest.raises(OpValidationError):
                _apply(_graph([]), [{"op": "add_node", "node_type": bad, "name": "sm"}])

    def test_unknown_node_type_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(_graph([]), [{"op": "add_node", "node_type": "notAType", "name": "x"}])


# ---------------------------------------------------------------------------
# Batch-local $refs
# ---------------------------------------------------------------------------


class TestRefs:
    def test_add_then_connect_via_ref(self):
        base = _graph([_node("src", "dataInput", path="data.parquet")])
        out = _apply(
            base,
            [
                {"op": "add_node", "node_type": "polars", "name": "derive", "ref": "d"},
                {"op": "add_edge", "source": "src", "target": "$d"},
            ],
        )
        assert any(e.source == "src" and e.target == "derive" for e in out.edges)

    def test_unknown_ref_rejected(self):
        base = _graph([_node("src")])
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "add_edge", "source": "src", "target": "$ghost"}])

    def test_duplicate_ref_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(
                _graph([]),
                [
                    {"op": "add_node", "node_type": "polars", "name": "a1", "ref": "r"},
                    {"op": "add_node", "node_type": "polars", "name": "a2", "ref": "r"},
                ],
            )

    def test_ref_use_before_declaration_rejected(self):
        base = _graph([_node("src")])
        with pytest.raises(OpValidationError):
            _apply(
                base,
                [
                    {"op": "add_edge", "source": "src", "target": "$late"},
                    {"op": "add_node", "node_type": "polars", "name": "late node", "ref": "late"},
                ],
            )

    def test_ref_shadowing_existing_id_disambiguated_by_prefix(self):
        """``$x`` targets the batch-created node; bare ``x`` the existing one."""
        base = _graph([_node("x", "polars", code="old")])
        out = _apply(
            base,
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "fresh",
                    "ref": "x",
                    "config": {"code": "new"},
                },
                {"op": "update_node", "node": "$x", "config": {"code": "via_ref"}},
                {"op": "update_node", "node": "x", "config": {"code": "via_id"}},
            ],
        )
        assert _get(out, "fresh").data.config["code"] == "via_ref"
        assert _get(out, "x").data.config["code"] == "via_id"


# ---------------------------------------------------------------------------
# update_node
# ---------------------------------------------------------------------------


class TestUpdateNode:
    def test_shallow_merge_preserves_untouched_keys(self):
        base = _graph([_node("src", "dataInput", path="a.parquet", format="parquet")])
        out = _apply(
            base,
            [
                {"op": "update_node", "node": "src", "config": {"path": "b.parquet"}},
            ],
        )
        cfg = _get(out, "src").data.config
        assert cfg["path"] == "b.parquet"
        assert cfg["format"] == "parquet"

    def test_explicit_null_removes_key(self):
        base = _graph([_node("src", "dataInput", path="a.parquet", format="parquet")])
        out = _apply(
            base,
            [
                {"op": "update_node", "node": "src", "config": {"format": None}},
            ],
        )
        assert "format" not in _get(out, "src").data.config

    def test_unknown_config_key_rejected(self):
        base = _graph([_node("src", "dataInput", path="a.parquet")])
        with pytest.raises(OpValidationError):
            _apply(
                base,
                [
                    {"op": "update_node", "node": "src", "config": {"bogus_key_xyz": 1}},
                ],
            )

    def test_unknown_node_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(_graph([]), [{"op": "update_node", "node": "ghost", "config": {}}])


# ---------------------------------------------------------------------------
# rename_node
# ---------------------------------------------------------------------------


class TestRenameNode:
    def test_rename_changes_label_id_and_rewires_edges(self):
        base = _graph(
            [_node("a"), _node("b")],
            [_edge("a", "b")],
        )
        out = _apply(base, [{"op": "rename_node", "node": "a", "new_name": "first step"}])
        new_id = _sanitize_func_name("first step")
        assert new_id in _ids(out) and "a" not in _ids(out)
        assert _get(out, new_id).data.label == "first step"
        assert any(e.source == new_id and e.target == "b" for e in out.edges)

    def test_rename_unknown_node_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(_graph([]), [{"op": "rename_node", "node": "ghost", "new_name": "x"}])


# ---------------------------------------------------------------------------
# delete_node / delete_edge / add_edge
# ---------------------------------------------------------------------------


class TestEdgesAndDeletion:
    def test_delete_node_drops_touching_edges(self):
        base = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("b", "c")],
        )
        out = _apply(base, [{"op": "delete_node", "node": "b"}])
        assert "b" not in _ids(out)
        assert out.edges == []

    def test_add_edge_unknown_endpoint_rejected(self):
        base = _graph([_node("a")])
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "add_edge", "source": "a", "target": "ghost"}])

    def test_add_edge_with_handles_round_trips(self):
        base = _graph([_node("a"), _node("b")])
        out = _apply(
            base,
            [
                {
                    "op": "add_edge",
                    "source": "a",
                    "target": "b",
                    "source_handle": "out1",
                    "target_handle": "in1",
                },
            ],
        )
        (e,) = out.edges
        assert (e.source, e.target, e.sourceHandle, e.targetHandle) == ("a", "b", "out1", "in1")

    def test_delete_edge_exact_match(self):
        base = _graph([_node("a"), _node("b")], [_edge("a", "b", "p1", None)])
        out = _apply(
            base,
            [
                {"op": "delete_edge", "source": "a", "target": "b", "source_handle": "p1"},
            ],
        )
        assert out.edges == []

    def test_delete_edge_ambiguous_match_rejected(self):
        base = _graph(
            [_node("a"), _node("b")],
            [_edge("a", "b", "p1", None), _edge("a", "b", "p2", None)],
        )
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "delete_edge", "source": "a", "target": "b"}])

    def test_delete_edge_no_match_rejected(self):
        base = _graph([_node("a"), _node("b")])
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "delete_edge", "source": "a", "target": "b"}])


# ---------------------------------------------------------------------------
# update_preamble
# ---------------------------------------------------------------------------


class TestUpdatePreamble:
    def test_full_replacement(self):
        base = _graph([_node("a")])
        base = base.model_copy(update={"preamble": "OLD = 1"})
        out = _apply(base, [{"op": "update_preamble", "preamble": "NEW = 2"}])
        assert out.preamble == "NEW = 2"


# ---------------------------------------------------------------------------
# Submodel boundary
# ---------------------------------------------------------------------------


class TestSubmodelBoundary:
    def _graph_with_submodel(self) -> PipelineGraph:
        placeholder = _node("submodel__sm1", "submodel")
        graph = _graph([_node("a"), placeholder], [])
        return graph.model_copy(
            update={
                "submodels": {
                    "sm1": {
                        "graph": {
                            "nodes": [_node("inner_child").model_dump()],
                            "edges": [],
                        }
                    }
                }
            }
        )

    def test_op_targeting_submodel_internal_node_rejected(self):
        base = self._graph_with_submodel()
        with pytest.raises(OpValidationError):
            _apply(
                base,
                [
                    {"op": "update_node", "node": "inner_child", "config": {"code": "df"}},
                ],
            )

    def test_delete_submodel_placeholder_rejected(self):
        base = self._graph_with_submodel()
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "delete_node", "node": "submodel__sm1"}])


# ---------------------------------------------------------------------------
# Atomicity and purity
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_failed_batch_leaves_input_untouched(self):
        base = _graph([_node("src", "dataInput", path="a.parquet")])
        snapshot = base.model_dump()
        with pytest.raises(OpValidationError):
            _apply(
                base,
                [
                    {"op": "add_node", "node_type": "polars", "name": "ok one"},
                    {"op": "update_node", "node": "ghost", "config": {}},
                ],
            )
        assert base.model_dump() == snapshot

    def test_successful_batch_does_not_mutate_input(self):
        base = _graph([_node("src", "dataInput", path="a.parquet")])
        snapshot = base.model_dump()
        out = _apply(base, [{"op": "add_node", "node_type": "polars", "name": "new step"}])
        assert base.model_dump() == snapshot
        assert len(out.nodes) == 2


# ---------------------------------------------------------------------------
# Deterministic positions (assigned after the whole batch)
# ---------------------------------------------------------------------------


class TestPositions:
    def test_first_node_in_empty_graph_at_origin(self):
        out = _apply(_graph([]), [{"op": "add_node", "node_type": "polars", "name": "only"}])
        pos = _get(out, "only").position
        assert (pos["x"], pos["y"]) == (0.0, 0.0)

    def test_new_node_lands_right_of_parent(self):
        base = _graph([_node("src")])
        out = _apply(
            base,
            [
                {"op": "add_node", "node_type": "polars", "name": "child", "ref": "c"},
                {"op": "add_edge", "source": "src", "target": "$c"},
            ],
        )
        assert _get(out, "child").position["x"] > _get(out, "src").position["x"]

    def test_siblings_share_x_and_stagger_y(self):
        base = _graph([_node("src")])
        out = _apply(
            base,
            [
                {"op": "add_node", "node_type": "polars", "name": "kid one", "ref": "k1"},
                {"op": "add_node", "node_type": "polars", "name": "kid two", "ref": "k2"},
                {"op": "add_edge", "source": "src", "target": "$k1"},
                {"op": "add_edge", "source": "src", "target": "$k2"},
            ],
        )
        p1, p2 = _get(out, "kid_one").position, _get(out, "kid_two").position
        assert p1["x"] == p2["x"]
        assert p1["y"] != p2["y"]

    def test_positions_use_final_wiring_not_op_order(self):
        """Edges added *after* the add_node still drive placement —
        positions are assigned once the whole batch has applied."""
        base = _graph([_node("src")])
        far = base.model_copy()
        far.nodes[0].position = {"x": 500.0, "y": 0.0}
        out = _apply(
            far,
            [
                {"op": "add_node", "node_type": "polars", "name": "late wired", "ref": "lw"},
                {"op": "add_edge", "source": "src", "target": "$lw"},
            ],
        )
        assert _get(out, "late_wired").position["x"] > 500.0

    def test_existing_nodes_never_move(self):
        base = _graph([_node("a"), _node("b")], [_edge("a", "b")])
        before = {n.id: dict(n.position) for n in base.nodes}
        out = _apply(
            base,
            [
                {"op": "add_node", "node_type": "polars", "name": "new one", "ref": "n"},
                {"op": "add_edge", "source": "b", "target": "$n"},
            ],
        )
        for node_id, pos in before.items():
            assert dict(_get(out, node_id).position) == pos

    def test_same_batch_same_graph_same_positions(self):
        base = _graph([_node("src")])
        batch = [
            {"op": "add_node", "node_type": "polars", "name": "kid one", "ref": "k1"},
            {"op": "add_node", "node_type": "polars", "name": "kid two", "ref": "k2"},
            {"op": "add_edge", "source": "src", "target": "$k1"},
            {"op": "add_edge", "source": "src", "target": "$k2"},
        ]
        out1, out2 = _apply(base, batch), _apply(base, batch)
        pos1 = {n.id: dict(n.position) for n in out1.nodes}
        pos2 = {n.id: dict(n.position) for n in out2.nodes}
        assert pos1 == pos2


class TestParseRejections:
    def test_blank_name_rejected(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "add_node", "node_type": "polars", "name": "   "}])

    def test_ref_may_not_start_with_dollar(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "add_node", "node_type": "polars", "name": "x", "ref": "$r"}])

    def test_blank_handles_rejected(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "add_edge", "source": "a", "target": "b", "source_handle": " "}])

    def test_payload_must_be_a_list(self):
        with pytest.raises(OpValidationError):
            parse_ops("not a list")  # type: ignore[arg-type]

    def test_item_must_be_an_object(self):
        with pytest.raises(OpValidationError):
            parse_ops([42])  # type: ignore[list-item]

    def test_empty_dollar_reference_rejected(self):
        base = _graph([_node("src")])
        with pytest.raises(OpValidationError):
            _apply(base, [{"op": "add_edge", "source": "src", "target": "$"}])

    def test_extra_keys_rejected(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "delete_node", "node": "a", "bogus": 1}])


class TestDuplicateEdges:
    def test_exact_duplicate_add_edge_rejected(self):
        base = _graph([_node("a"), _node("b")], [_edge("a", "b", "p1", None)])
        with pytest.raises(OpValidationError, match="already exists"):
            _apply(base, [{"op": "add_edge", "source": "a", "target": "b", "source_handle": "p1"}])

    def test_same_endpoints_with_different_handles_allowed(self):
        base = _graph([_node("a"), _node("b")], [_edge("a", "b", "p1", None)])
        out = _apply(
            base, [{"op": "add_edge", "source": "a", "target": "b", "source_handle": "p2"}]
        )
        assert len(out.edges) == 2

    def test_delete_then_re_add_same_edge_within_one_batch(self):
        base = _graph([_node("a"), _node("b")], [_edge("a", "b")])
        out = _apply(
            base,
            [
                {"op": "delete_edge", "source": "a", "target": "b"},
                {"op": "add_edge", "source": "a", "target": "b"},
            ],
        )
        assert len(out.edges) == 1
