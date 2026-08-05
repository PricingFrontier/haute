"""Tests for the assistant graph-edit ops engine (``haute.assistant._ops``).

Spec: specs/assistant/low-level.md — Key types (``GraphEditOp``) and
Edge cases.  The engine is a pure graph→graph function: ``parse_ops``
validates wire-shaped op dicts, ``apply_ops`` applies them in order
against a copy of the graph and returns the new graph.  Any validation
failure raises ``OpValidationError`` and the input graph is untouched
(all-or-nothing; the save never happens on a failed batch).

Authored test-first per CLAUDE.md TDD — the module is implemented to
make these pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._graph_utils import _sanitize_func_name
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph, SubmodelDefinition
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

    def test_operation_count_is_bounded(self):
        with pytest.raises(OpValidationError, match="at most 100"):
            parse_ops([{"op": "update_preamble", "preamble": None} for _ in range(101)])


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
        assert node.data.label == "My_Step"
        assert node.data.nodeType == "polars"
        assert node.data.config == {"code": "df"}

    def test_submodel_types_rejected(self):
        for bad in ("submodel", "submodelPort"):
            with pytest.raises(OpValidationError):
                _apply(_graph([]), [{"op": "add_node", "node_type": bad, "name": "sm"}])

    def test_unknown_node_type_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(_graph([]), [{"op": "add_node", "node_type": "notAType", "name": "x"}])

    def test_sanitized_id_collision_is_rejected_without_changing_input(self):
        graph = _graph([_node("My_Step")])

        with pytest.raises(OpValidationError, match="already exists"):
            _apply(graph, [{"op": "add_node", "node_type": "polars", "name": "My Step"}])

        assert _ids(graph) == {"My_Step"}


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
        assert _get(out, new_id).data.label == "first_step"
        assert any(e.source == new_id and e.target == "b" for e in out.edges)

    def test_rename_unknown_node_rejected(self):
        with pytest.raises(OpValidationError):
            _apply(_graph([]), [{"op": "rename_node", "node": "ghost", "new_name": "x"}])

    def test_rename_cannot_overwrite_an_existing_sanitized_id(self):
        graph = _graph([_node("first"), _node("Existing_Name")])

        with pytest.raises(OpValidationError, match="already exists"):
            _apply(
                graph,
                [{"op": "rename_node", "node": "first", "new_name": "Existing Name"}],
            )

        assert _ids(graph) == {"first", "Existing_Name"}


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
        placeholder = _node("submodel__sm1", "submodel", definitionId="sm1", alias="sm1")
        graph = _graph([_node("a"), placeholder], [])
        return graph.model_copy(
            update={
                "submodels": {
                    "sm1": SubmodelDefinition(
                        definition_id="sm1",
                        file="modules/sm1.py",
                        graph=PipelineGraph(nodes=[_node("inner_child")], edges=[]),
                        input_ports=[],
                        output_ports=[],
                    )
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


class TestProjectRevision:
    def test_revision_is_deterministic_and_covers_source_config_graph_and_capabilities(
        self, tmp_path: Path
    ):
        from haute.assistant._ops import build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("first", encoding="utf-8")
        (tmp_path / "haute.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        graph = _graph([_node("source", "dataInput", path="quotes.parquet")])

        first = build_project_snapshot(tmp_path, source, graph)
        assert first == build_project_snapshot(tmp_path, source, graph)
        assert len(first.revision) == 64
        assert first.capability_hash

        source.write_text("second", encoding="utf-8")
        assert build_project_snapshot(tmp_path, source, graph).revision != first.revision
        source.write_text("first", encoding="utf-8")

        (tmp_path / "haute.toml").write_text("[project]\nname='y'\n", encoding="utf-8")
        assert build_project_snapshot(tmp_path, source, graph).revision != first.revision

        changed_graph = _graph([_node("source", "dataInput", path="other.parquet")])
        assert build_project_snapshot(tmp_path, source, changed_graph).revision != first.revision

    def test_project_sources_are_path_safe_and_content_addressed(self, tmp_path: Path):
        from haute.assistant._ops import AssistantOperationError, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        knowledge = tmp_path / "docs" / "terms.md"
        knowledge.parent.mkdir()
        knowledge.write_text("one", encoding="utf-8")

        first = build_project_snapshot(tmp_path, source, _graph([]), project_sources=[knowledge])
        knowledge.write_text("two", encoding="utf-8")
        second = build_project_snapshot(tmp_path, source, _graph([]), project_sources=[knowledge])
        assert first.revision != second.revision

        outside = tmp_path.parent / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        with pytest.raises(AssistantOperationError) as exc:
            build_project_snapshot(tmp_path, source, _graph([]), project_sources=[outside])
        assert exc.value.code == "project_source_forbidden"


class TestSemanticPlans:
    def test_plan_is_canonical_and_reports_bounded_semantic_diff(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph([_node("source", "dataInput")])
        snapshot = build_project_snapshot(tmp_path, source, graph)
        raw_ops = [
            {"op": "add_node", "node_type": "banding", "name": "Age band", "ref": "band"},
            {"op": "add_edge", "source": "source", "target": "$band"},
        ]

        first = build_graph_edit_plan(snapshot, raw_ops)
        second = build_graph_edit_plan(snapshot, json.loads(json.dumps(raw_ops)))

        assert first == second
        assert len(first.plan_hash) == 64
        assert first.base_revision == snapshot.revision
        assert first.verification_tier == "structural"
        assert first.diff.nodes_added == ("Age_band",)
        assert first.diff.nodes_removed == ()
        assert len(first.diff.edges_added) == 1
        assert first.diff.sidecar_changes
        assert first.affected_capabilities == ("banding", "dataInput")
        assert first.postconditions
        assert first.normalized_operations[0]["op"] == "add_node"

    def test_semantic_diff_resolves_batch_refs_to_final_node_identity(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("source")]))

        plan = build_graph_edit_plan(
            snapshot,
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "Fresh Node",
                    "ref": "fresh",
                },
                {
                    "op": "update_node",
                    "node": "$fresh",
                    "config": {"code": "return frame"},
                },
                {"op": "add_edge", "source": "source", "target": "$fresh"},
            ],
        )

        assert plan.diff.nodes_updated == ("Fresh_Node",)
        assert plan.diff.config_changes == ("Fresh_Node:code",)
        assert "$fresh" not in json.dumps(plan.diff.as_dict())

    def test_plan_rejects_a_new_disconnected_node(self, tmp_path: Path):
        from haute.assistant._ops import (
            AssistantOperationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph([_node("source", "dataInput")]),
        )

        with pytest.raises(AssistantOperationError) as exc:
            build_graph_edit_plan(
                snapshot,
                [{"op": "add_node", "node_type": "polars", "name": "orphan"}],
            )

        assert exc.value.code == "invalid_plan"
        assert "disconnected" in str(exc.value).lower()

    def test_renaming_a_new_node_cannot_bypass_connectivity_validation(self, tmp_path: Path):
        from haute.assistant._ops import (
            AssistantOperationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph([_node("source", "dataInput")]),
        )

        with pytest.raises(AssistantOperationError) as exc:
            build_graph_edit_plan(
                snapshot,
                [
                    {"op": "add_node", "node_type": "polars", "name": "fresh", "ref": "fresh"},
                    {"op": "rename_node", "node": "$fresh", "new_name": "renamed"},
                ],
            )

        assert exc.value.code == "invalid_plan"
        assert "renamed" in str(exc.value)

    def test_existing_disconnected_node_does_not_block_an_unrelated_plan(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph([_node("source", "dataInput"), _node("existing_orphan")]),
        )

        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
        )

        assert plan.diff.nodes_renamed == (("source", "renamed"),)

    @pytest.mark.parametrize(
        "code",
        [
            "df.filter(pl.col('age') > 18)",
            "df.with_columns(pl.lit(1).alias('one'))",
            "df = df",
            "return df",
            "return None",
            "def inner():\n    return df.filter(pl.col('age') > 18)",
            "df.filter(",
        ],
    )
    def test_plan_rejects_polars_code_whose_result_is_discarded(
        self,
        tmp_path: Path,
        code: str,
    ):
        from haute.assistant._ops import (
            OpValidationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph([_node("source", "dataInput")]),
        )

        with pytest.raises(OpValidationError):
            build_graph_edit_plan(
                snapshot,
                [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "transform",
                        "config": {"code": code},
                        "ref": "transform",
                    },
                    {"op": "add_edge", "source": "source", "target": "$transform"},
                ],
            )

    @pytest.mark.parametrize(
        "code",
        [
            "df = df.filter(pl.col('age') > 18)",
            "return df.with_columns(pl.lit(1).alias('one'))",
            "df = prepared_frame",
        ],
    )
    def test_plan_accepts_polars_code_that_retains_its_result(self, tmp_path: Path, code: str):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("transform")]))

        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "update_node", "node": "transform", "config": {"code": code}}],
        )

        assert plan.diff.config_changes == ("transform:code",)

    def test_bounded_diff_retains_complete_identity_for_exact_verification(self):
        from haute.assistant._ops import semantic_diff

        targets = [_node(f"target_{index:02d}") for index in range(61)]
        before = _graph(
            [_node("hub"), *targets],
            [_edge("hub", target.id) for target in targets],
        )
        expected_after = _apply(before, [{"op": "delete_node", "node": "hub"}])
        incomplete_after = expected_after.model_copy(deep=True)
        incomplete_after.edges.append(_edge("hub", "target_60"))

        expected = semantic_diff(
            before,
            expected_after,
            [{"op": "delete_node", "node": "hub"}],
        )
        incomplete = semantic_diff(
            before,
            incomplete_after,
            [{"op": "delete_node", "node": "hub"}],
        )

        assert len(expected.edges_removed) == 50
        assert expected.edges_removed == incomplete.edges_removed
        assert expected.truncated is True
        assert expected.complete_counts["edges_removed"] == 61
        assert incomplete.complete_counts["edges_removed"] == 60
        assert expected.complete_hash != incomplete.complete_hash
        assert expected != incomplete

    def test_affected_capabilities_cover_changes_beyond_visible_diff_limit(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        nodes = [_node(f"node_{index:02d}") for index in range(50)]
        nodes.append(_node("zz_rating", "ratingStep"))
        snapshot = build_project_snapshot(tmp_path, source, _graph(nodes))
        operations = [{"op": "update_node", "node": node.id, "config": {}} for node in nodes]

        plan = build_graph_edit_plan(snapshot, operations)

        assert plan.diff.truncated is True
        assert plan.diff.complete_counts["nodes_updated"] == 51
        assert plan.affected_capabilities == ("polars", "ratingStep")

    @pytest.mark.parametrize(
        "ops",
        [
            [{"op": "delete_node", "node": "transform"}],
            [{"op": "update_preamble", "preamble": "import os"}],
            [{"op": "update_node", "node": "transform", "config": {"code": "return frame"}}],
            [
                {
                    "op": "add_node",
                    "node_type": "modelScore",
                    "name": "score",
                    "config": {"version": "2"},
                },
                {"op": "add_edge", "source": "transform", "target": "score"},
            ],
        ],
    )
    def test_graph_authoring_plan_has_no_runtime_consent_classification(
        self, tmp_path: Path, ops: list[dict]
    ):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph([_node("transform", "polars", code="return frame")])
        snapshot = build_project_snapshot(tmp_path, source, graph)

        plan = build_graph_edit_plan(snapshot, ops)
        assert "risk" not in plan.as_dict()
        assert "confirmation_required" not in plan.as_dict()

    def test_deleting_an_edge_has_exact_plan_authority_only(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph([_node("source"), _node("target")], [_edge("source", "target")])
        snapshot = build_project_snapshot(tmp_path, source, graph)

        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "delete_edge", "source": "source", "target": "target"}],
        )

        assert "risk" not in plan.as_dict()
        assert "confirmation_required" not in plan.as_dict()

    @pytest.mark.parametrize(
        "ops",
        [
            [{"op": "update_node", "node": "sink", "config": {"mode": "write"}}],
            [{"op": "rename_node", "node": "sink", "new_name": "renamed_sink"}],
            [{"op": "add_edge", "source": "source", "target": "sink"}],
        ],
    )
    def test_authoring_an_external_output_does_not_authorize_execution(
        self,
        tmp_path: Path,
        ops: list[dict],
    ):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph([_node("source"), _node("sink", "dataOutput")])
        snapshot = build_project_snapshot(tmp_path, source, graph)

        plan = build_graph_edit_plan(snapshot, ops)

        assert "risk" not in plan.as_dict()
        assert "confirmation_required" not in plan.as_dict()

    def test_plan_hash_binds_postconditions_and_base_revision(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("one", encoding="utf-8")
        graph = _graph([_node("source")])
        first_snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = [{"op": "rename_node", "node": "source", "new_name": "renamed"}]
        first = build_graph_edit_plan(first_snapshot, ops)

        source.write_text("two", encoding="utf-8")
        second_snapshot = build_project_snapshot(tmp_path, source, graph)
        second = build_graph_edit_plan(second_snapshot, ops)
        assert first.plan_hash != second.plan_hash

        altered = build_graph_edit_plan(
            first_snapshot,
            ops,
            postconditions=[{"kind": "node_exists", "node": "renamed"}],
        )
        assert altered.plan_hash != first.plan_hash

    @pytest.mark.parametrize(
        "postconditions",
        [
            [{"kind": "unsupported"}],
            [{"kind": "node_exists", "node": "source", "extra": True}],
            [{"kind": "node_exists", "node": "missing"}],
            [{"kind": "graph_shape", "nodes": -1, "edges": 0}],
        ],
    )
    def test_invalid_or_unsatisfied_postconditions_fail_during_planning(
        self,
        tmp_path: Path,
        postconditions: list[dict],
    ):
        from haute.assistant._ops import (
            AssistantOperationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph([_node("source")]),
        )

        with pytest.raises(AssistantOperationError):
            build_graph_edit_plan(
                snapshot,
                [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
                postconditions=postconditions,
            )


class TestPlanStore:
    def test_applying_plan_survives_ttl_until_terminal_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.assistant import _ops
        from haute.assistant._ops import (
            AssistantOperationError,
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        now = 0.0
        monkeypatch.setattr(_ops, "monotonic", lambda: now)
        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        plan = build_graph_edit_plan(
            build_project_snapshot(tmp_path, source, _graph([_node("source")])),
            [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
        )
        store = PlanStore(ttl_seconds=1)
        store.put(plan)
        store.begin_apply(plan.plan_hash)

        now = 2.0
        store.complete_apply(plan.plan_hash, {"result_revision": "a" * 64})

        with pytest.raises(AssistantOperationError) as exc:
            store.get(plan.plan_hash)
        assert exc.value.code == "plan_expired"

    def test_capacity_never_evicts_an_applying_plan(self, tmp_path: Path):
        from haute.assistant._ops import (
            AssistantOperationError,
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("source")]))
        first = build_graph_edit_plan(
            snapshot,
            [{"op": "rename_node", "node": "source", "new_name": "first"}],
        )
        second = build_graph_edit_plan(
            snapshot,
            [{"op": "rename_node", "node": "source", "new_name": "second"}],
        )
        store = PlanStore(max_size=1)
        store.put(first)
        store.begin_apply(first.plan_hash)

        with pytest.raises(AssistantOperationError) as exc:
            store.put(second)
        assert exc.value.code == "plan_store_busy"

        store.complete_apply(first.plan_hash, {"result_revision": "a" * 64})
        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(first.plan_hash)
        assert exc.value.code == "plan_already_applied"

    def test_plan_is_single_use(self, tmp_path: Path):
        from haute.assistant._ops import (
            AssistantOperationError,
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("source")]))
        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
        )
        store = PlanStore()
        store.put(plan)
        assert store.begin_apply(plan.plan_hash) == plan
        store.complete_apply(plan.plan_hash, {"result_revision": "a" * 64})
        store.put(plan)

        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(plan.plan_hash)
        assert exc.value.code == "plan_already_applied"

    def test_aborted_plan_requires_a_fresh_identical_put_before_retry(self, tmp_path: Path):
        from haute.assistant._ops import (
            AssistantOperationError,
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        plan = build_graph_edit_plan(
            build_project_snapshot(tmp_path, source, _graph([_node("source")])),
            [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
        )
        store = PlanStore()
        store.put(plan)
        store.begin_apply(plan.plan_hash)
        store.abort_apply(plan.plan_hash)

        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(plan.plan_hash)
        assert exc.value.code == "plan_aborted"
        assert "dry-run" in str(exc.value)

        store.put(plan)
        assert store.begin_apply(plan.plan_hash) == plan

    def test_destructive_plan_enters_applying_without_session_consent(self, tmp_path: Path):
        from haute.assistant._ops import (
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("source")]))
        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "delete_node", "node": "source"}],
        )
        store = PlanStore()
        store.put(plan)

        assert store.begin_apply(plan.plan_hash) == plan
