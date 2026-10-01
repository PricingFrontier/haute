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
from haute.assistant._ops import (
    AssistantOperationError,
    OpValidationError,
    PlanReceipt,
    _apply_ops_with_refs,
    apply_ops,
    parse_ops,
)

#: The receipt every plan these tests store carries.
_RECEIPT = PlanReceipt("Test plan.")

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
                    "node_type": "edgeJoin",
                    "name": "My Step",
                    "config": {"suffix": "_lookup"},
                },
            ],
        )
        expected_id = _sanitize_func_name("My Step")
        node = _get(out, expected_id)
        assert node.data.label == "My_Step"
        assert node.data.nodeType == "edgeJoin"
        assert node.data.config == {"how": "left", "suffix": "_lookup"}

    def test_a_data_input_starts_from_the_palette_config(self):
        out = _apply(
            _graph([]),
            [
                {
                    "op": "add_node",
                    "node_type": "dataInput",
                    "name": "claims",
                    "config": {"path": "claims.parquet"},
                }
            ],
        )

        assert _get(out, "claims").data.config == {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "claims.parquet",
            "arguments": {},
            "steps": [],
            "code": "",
        }

    @pytest.mark.parametrize(
        ("node_type", "config", "absent"),
        [
            (
                "dataInput",
                {"inputType": "database", "connection": "warehouse", "query": "select 1"},
                {"format", "mode", "path"},
            ),
            (
                "modelScore",
                {"sourceType": "run", "run_id": "abc123", "artifact_path": "model.cbm"},
                {"registered_model", "version", "task", "output_column"},
            ),
        ],
    )
    def test_a_config_on_another_branch_keeps_only_the_palette_steps(
        self, node_type: str, config: dict, absent: set[str]
    ):
        out = _apply(
            _graph([]),
            [{"op": "add_node", "node_type": node_type, "name": "n", "config": config}],
        )

        saved = _get(out, "n").data.config
        assert saved["steps"] == []
        assert not absent & set(saved)
        assert {key: saved[key] for key in config} == config

    def test_an_instance_takes_no_palette_config(self):
        base = _graph([_node("original", steps=[{"id": "start", "kind": "source", "input": "a"}])])
        out = _apply(
            base,
            [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "copy",
                    "config": {"instanceOf": "original"},
                }
            ],
        )

        assert _get(out, "copy").data.config == {"instanceOf": "original"}

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

    def test_ref_without_its_dollar_names_the_ref_and_the_id(self):
        """The live join shape: the model declared ref 'join_node' and wired it bare."""
        base = _graph([_node("quotes", "dataInput"), _node("rates", "dataInput")])
        with pytest.raises(OpValidationError) as excinfo:
            _apply(
                base,
                [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "attach_regional_rates_join",
                        "ref": "join_node",
                    },
                    {"op": "add_edge", "source": "quotes", "target": "join_node"},
                ],
            )
        exc = excinfo.value
        assert exc.where == {"op_index": 1}
        assert str(exc).startswith("Unknown edge target node 'join_node'")
        assert exc.fix == (
            "Write '$join_node', the ref add_node declared at operation 0, or the "
            "node's id 'attach_regional_rates_join'."
        )

    @pytest.mark.parametrize("target", ["enriched_quotes", "$out", "out"])
    def test_a_node_added_later_in_the_batch_names_the_move(self, target: str):
        """The live output shape: the edge to the output preceded its add_node."""
        base = _graph([_node("quote_with_competitor")])
        with pytest.raises(OpValidationError) as excinfo:
            _apply(
                base,
                [
                    {"op": "add_edge", "source": "quote_with_competitor", "target": target},
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "enriched_quotes",
                        "ref": "out",
                    },
                ],
            )
        exc = excinfo.value
        assert exc.where == {"op_index": 0}
        assert "comes after this operation" in str(exc)
        assert exc.fix == "Move add_node 'enriched_quotes' (operation 1) before operation 0."

    def test_an_undeclared_ref_lists_the_refs_declared_so_far(self):
        base = _graph([_node("src")])
        with pytest.raises(OpValidationError) as excinfo:
            _apply(
                base,
                [
                    {"op": "add_node", "node_type": "polars", "name": "derive", "ref": "d"},
                    {"op": "add_edge", "source": "src", "target": "$ghost"},
                ],
            )
        exc = excinfo.value
        assert exc.where == {"op_index": 1}
        assert str(exc) == (
            "Unknown batch reference '$ghost': no add_node before this operation declared "
            "ref 'ghost'. Refs declared so far: '$d' (id 'derive')."
        )
        assert exc.fix == (
            "Declare ref 'ghost' on an add_node before this operation, or use a declared "
            "ref or a node id get_pipeline lists."
        )

    def test_a_node_no_operation_adds_says_a_dry_run_is_a_whole_plan(self):
        """The live retry shape: after a failed dry-run the model resent only the
        edge to the output it had proposed, so no operation added it."""
        base = _graph([_node("nb_batch", "dataInput")])
        with pytest.raises(OpValidationError) as excinfo:
            _apply(
                base,
                [
                    {"op": "add_node", "node_type": "polars", "name": "joined", "ref": "q"},
                    {"op": "add_edge", "source": "nb_batch", "target": "$q"},
                    {"op": "add_edge", "source": "$q", "target": "enriched_quotes"},
                ],
            )
        exc = excinfo.value
        assert exc.where == {"op_index": 2}
        assert str(exc) == (
            "Unknown edge target node 'enriched_quotes': no node has this id and no "
            "operation of this plan adds it. Nodes this plan adds so far: 'joined' ($q)."
        )
        assert exc.fix == (
            "Add 'enriched_quotes' with add_node before operation 2: each dry run is a "
            "whole plan, and a node a failed dry run proposed was never kept. Otherwise "
            "use a node id get_pipeline lists."
        )
        assert exc.did_you_mean == ()

    def test_a_misspelt_node_id_suggests_the_close_id(self):
        base = _graph([_node("quote_with_competitor")])
        with pytest.raises(OpValidationError) as excinfo:
            _apply(base, [{"op": "update_node", "node": "quote_with_competitr", "config": {}}])
        exc = excinfo.value
        assert exc.did_you_mean == ("quote_with_competitor",)
        assert "This plan adds no node before it." in str(exc)
        assert exc.fix is not None and exc.fix.startswith(
            "Use 'quote_with_competitor' if that is the node you meant; otherwise add "
            "'quote_with_competitr' with add_node before operation 0"
        )

    def test_ref_shadowing_existing_id_disambiguated_by_prefix(self):
        """``$x`` targets the batch-created node; bare ``x`` the existing one."""
        base = _graph([_node("x", "edgeJoin", suffix="_old")])
        out = _apply(
            base,
            [
                {
                    "op": "add_node",
                    "node_type": "edgeJoin",
                    "name": "fresh",
                    "ref": "x",
                    "config": {"suffix": "_new"},
                },
                {"op": "update_node", "node": "$x", "config": {"suffix": "_via_ref"}},
                {"op": "update_node", "node": "x", "config": {"suffix": "_via_id"}},
            ],
        )
        assert _get(out, "fresh").data.config["suffix"] == "_via_ref"
        assert _get(out, "x").data.config["suffix"] == "_via_id"


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


_AGE = {
    "banding": "breakpoints",
    "column": "driver_age",
    "outputColumn": "age_band",
    "rules": [{"boundary": "24", "label": "17-24"}, {"boundary": "", "label": "25+"}],
}
_REGION = {
    "banding": "categorical",
    "column": "region_code",
    "outputColumn": "region",
    "rules": [{"value": "NW", "assignment": "North"}],
    "default": "Other",
}
_LICENCE = {
    "banding": "breakpoints",
    "column": "licence_years",
    "outputColumn": "licence_band",
    "rules": [{"boundary": "2", "label": "new"}, {"boundary": "", "label": "settled"}],
}


def _withheld(graph: PipelineGraph, raw_ops: list[dict]) -> PipelineGraph:
    """Apply *raw_ops* as a dry-run does when the policy withholds saved node configuration."""

    return _apply_ops_with_refs(graph, parse_ops(raw_ops), config_withheld=True).graph


def test_an_output_row_ref_to_a_node_the_plan_deleted_is_an_unknown_reference():
    row = {
        "source_port": "$gone",
        "source_column": "x",
        "output_path": "$[:].x",
        "enabled": True,
    }
    with pytest.raises(OpValidationError, match="'\\$gone'"):
        _apply(
            _graph([]),
            [
                {"op": "add_node", "node_type": "polars", "name": "gone", "ref": "gone"},
                {"op": "delete_node", "node": "$gone"},
                {
                    "op": "add_node",
                    "node_type": "output",
                    "name": "response",
                    "config": {"outputMapping": [row]},
                },
            ],
        )


class TestWithheldConfigGuard:
    """An update that would retype a saved list or map the model cannot read is refused."""

    def _bands(self) -> PipelineGraph:
        return _graph(
            [
                _node("bands", "banding", factors=[_AGE, _REGION]),
                _node(
                    "policies",
                    "liveSwitch",
                    input_scenario_map={"quotes": "live", "batch_quotes": "nb_batch"},
                    inputs=["quotes", "batch_quotes"],
                ),
                _node("shaped", "polars", steps=_LOGIC, contract={"region_code": "01"}),
            ]
        )

    def test_changing_a_saved_entry_names_the_node_key_and_entry_never_its_rules(self):
        changed = {**_REGION, "rules": [{"value": "NW", "assignment": "North West"}]}
        with pytest.raises(AssistantOperationError) as caught:
            _withheld(
                self._bands(),
                [{"op": "update_node", "node": "bands", "config": {"factors": [_AGE, changed]}}],
            )

        error = caught.value
        assert error.code == "config_withheld"
        assert error.where == {"op_index": 0, "node": "bands", "field": "factors"}
        assert "'region'" in str(error) and "'age_band'" not in str(error)
        assert "egress policy" in str(error)
        assert "North" not in str(error) and "NW" not in str(error)
        assert error.fix is not None and "NEEDS_INPUT:" in error.fix

    def test_a_retyped_list_that_drops_entries_names_each_one(self):
        with pytest.raises(AssistantOperationError, match="'age_band', 'region'"):
            _withheld(
                self._bands(),
                [{"op": "update_node", "node": "bands", "config": {"factors": [_LICENCE]}}],
            )

    def test_removing_a_saved_list_is_refused(self):
        with pytest.raises(AssistantOperationError, match="'age_band', 'region'"):
            _withheld(
                self._bands(),
                [{"op": "update_node", "node": "bands", "config": {"factors": None}}],
            )

    def test_keeping_every_saved_entry_unchanged_passes(self):
        out = _withheld(
            self._bands(),
            [
                {
                    "op": "update_node",
                    "node": "bands",
                    "config": {"factors": [_AGE, _REGION, _LICENCE]},
                }
            ],
        )
        assert _get(out, "bands").data.config["factors"] == [_AGE, _REGION, _LICENCE]

    def test_a_map_keyed_by_input_names_names_the_changed_input(self):
        with pytest.raises(AssistantOperationError, match="'batch_quotes'") as caught:
            _withheld(
                self._bands(),
                [
                    {
                        "op": "update_node",
                        "node": "policies",
                        "config": {
                            "input_scenario_map": {"quotes": "live", "batch_quotes": "batch_quotes"}
                        },
                    }
                ],
            )
        assert "nb_batch" not in str(caught.value)

    def test_a_map_keyed_by_values_is_counted_never_named(self):
        with pytest.raises(AssistantOperationError, match="1 of its 1 keys") as caught:
            _withheld(
                self._bands(),
                [{"op": "update_node", "node": "shaped", "config": {"contract": {}}}],
            )
        assert "region_code" not in str(caught.value)

    def test_a_retyped_step_list_points_at_edit_steps(self):
        with pytest.raises(AssistantOperationError, match="'logic'") as caught:
            _withheld(
                self._bands(),
                [
                    {
                        "op": "update_node",
                        "node": "shaped",
                        "config": {
                            "steps": [
                                {"id": "logic", "kind": "free_code", "code": "df = df.head(3)"}
                            ]
                        },
                    }
                ],
            )
        assert caught.value.fix is not None and "edit_steps" in caught.value.fix

    def test_a_node_the_plan_adds_and_a_readable_policy_are_not_guarded(self):
        added = _withheld(
            self._bands(),
            [
                {
                    "op": "add_node",
                    "node_type": "banding",
                    "name": "more",
                    "config": {"factors": [_AGE]},
                },
                {"op": "update_node", "node": "more", "config": {"factors": [_LICENCE]}},
            ],
        )
        assert _get(added, "more").data.config["factors"] == [_LICENCE]
        readable = _apply(
            self._bands(),
            [{"op": "update_node", "node": "bands", "config": {"factors": [_LICENCE]}}],
        )
        assert _get(readable, "bands").data.config["factors"] == [_LICENCE]


# ---------------------------------------------------------------------------
# Stepped-node write contract
# ---------------------------------------------------------------------------

_TRANSFORM_FORM = (
    '[{"id": "start", "kind": "source", "input": "<edge name>"}, '
    '{"id": "logic", "kind": "free_code", "code": "..."}]'
)
_FRAME_FORM = '[{"id": "logic", "kind": "free_code", "code": "..."}]'
_LOGIC = [{"id": "logic", "kind": "free_code", "code": "df = df.head(2)"}]


def _stepped_graph() -> PipelineGraph:
    """``stepped`` and ``rated`` hold steps; ``coded`` is a code-mode Transform with code;
    ``bare`` is a Rating Step with neither steps nor code."""

    return _graph(
        [
            _node("src"),
            _node(
                "stepped",
                steps=[{"id": "start", "kind": "source", "input": "src"}, *_LOGIC],
            ),
            _node("rated", "ratingStep", steps=_LOGIC),
            _node("coded", code="df = src"),
            _node("bare", "ratingStep"),
        ],
        [_edge("src", "stepped"), _edge("src", "rated"), _edge("src", "coded")],
    )


class TestSteppedWrites:
    @pytest.mark.parametrize(
        ("node", "config", "form"),
        [
            ("stepped", {"code": "df = src"}, _TRANSFORM_FORM),
            ("stepped", {"steps": None, "code": "df = src"}, _TRANSFORM_FORM),
            ("stepped", {"steps": None}, _TRANSFORM_FORM),
            ("stepped", {"code": None}, _TRANSFORM_FORM),
            ("rated", {"code": "df = df.head(1)"}, _FRAME_FORM),
            ("rated", {"steps": None, "code": "df = df.head(1)"}, _FRAME_FORM),
        ],
    )
    def test_a_write_that_would_leave_the_step_builder_is_refused(
        self, node: str, config: dict, form: str
    ):
        graph = _stepped_graph()

        with pytest.raises(OpValidationError) as excinfo:
            _apply(graph, [{"op": "update_node", "node": node, "config": config}])

        assert form in str(excinfo.value)
        assert _get(graph, node).data.config["steps"]

    def test_steps_are_refused_on_a_code_mode_node_with_code(self):
        with pytest.raises(OpValidationError, match="discard its code"):
            _apply(
                _stepped_graph(),
                [{"op": "update_node", "node": "coded", "config": {"steps": _LOGIC}}],
            )

    def test_code_mode_nodes_keep_code_editing_and_code_less_nodes_accept_steps(self):
        out = _apply(
            _stepped_graph(),
            [
                {"op": "update_node", "node": "coded", "config": {"code": "df = src.head(1)"}},
                {"op": "update_node", "node": "bare", "config": {"steps": _LOGIC}},
            ],
        )

        assert _get(out, "coded").data.config["code"] == "df = src.head(1)"
        assert _get(out, "bare").data.config["steps"] == _LOGIC

    @pytest.mark.parametrize(
        ("ops", "form"),
        [
            (
                [{"op": "add_node", "node_type": "polars", "name": "t", "config": {"code": "x"}}],
                _TRANSFORM_FORM,
            ),
            (
                [
                    {
                        "op": "add_node",
                        "node_type": "ratingStep",
                        "name": "r",
                        "config": {"steps": None},
                    }
                ],
                _FRAME_FORM,
            ),
            # A node added earlier in the batch is already stepped.
            (
                [
                    {"op": "add_node", "node_type": "explore", "name": "e", "ref": "e"},
                    {"op": "update_node", "node": "$e", "config": {"code": "df = df"}},
                ],
                _FRAME_FORM,
            ),
        ],
    )
    def test_add_node_of_a_stepped_type_is_authored_as_steps(self, ops: list[dict], form: str):
        with pytest.raises(OpValidationError) as excinfo:
            _apply(_graph([]), ops)

        assert form in str(excinfo.value)

    @pytest.mark.parametrize(
        "op",
        [
            {
                "op": "add_node",
                "node_type": "ratingStep",
                "name": "r",
                "config": {"steps": [{"id": "keep", "kind": "filter"}]},
            },
            {
                "op": "update_node",
                "node": "rated",
                "config": {"steps": [{"id": "keep", "kind": "filter"}]},
            },
        ],
    )
    def test_a_steps_write_that_cannot_render_is_not_applied(self, op: dict):
        from haute.assistant._ops import AssistantOperationError

        with pytest.raises(AssistantOperationError) as excinfo:
            _apply(_stepped_graph(), [op])

        assert excinfo.value.code == "op_not_applied"
        assert "Step 1" in str(excinfo.value)
        assert _FRAME_FORM in str(excinfo.value)
        assert excinfo.value.where["step"] == "keep"


def _edit_steps(node: str, *edits: dict) -> list[dict]:
    return [{"op": "edit_steps", "node": node, "edits": list(edits)}]


class TestEditSteps:
    def test_edits_apply_in_order_and_new_steps_get_deterministic_ids(self):
        out = _apply(
            _stepped_graph(),
            _edit_steps(
                "stepped",
                {"insert_after": "start", "step": {"kind": "limit", "n": 5}},
                {"insert_after": "limit_1", "step": {"kind": "limit", "n": 3}},
                {"replace": "logic", "step": {"kind": "free_code", "code": "df = df.head(1)"}},
                {"remove": "limit_1"},
                {"insert_after": "limit_2", "step": {"kind": "limit", "n": 9}},
            ),
        )

        # limit_1 was free again when the last insertion was made.
        assert _get(out, "stepped").data.config["steps"] == [
            {"id": "start", "kind": "source", "input": "src"},
            {"id": "limit_2", "kind": "limit", "n": 3},
            {"id": "limit_1", "kind": "limit", "n": 9},
            {"id": "logic", "kind": "free_code", "code": "df = df.head(1)"},
        ]

    def test_an_insert_at_the_start_of_a_frame_surface(self):
        out = _apply(
            _stepped_graph(),
            _edit_steps(
                "rated", {"insert_after": None, "step": {"id": "few", "kind": "limit", "n": 2}}
            ),
        )

        assert _get(out, "rated").data.config["steps"] == [
            {"id": "few", "kind": "limit", "n": 2},
            *_LOGIC,
        ]

    @pytest.mark.parametrize(
        ("node", "edit", "step"),
        [
            ("stepped", {"replace": "ghost", "step": {"kind": "limit", "n": 1}}, "ghost"),
            ("stepped", {"remove": "ghost"}, "ghost"),
            ("stepped", {"insert_after": "ghost", "step": {"kind": "limit", "n": 1}}, "ghost"),
            (
                "stepped",
                {"insert_after": "start", "step": {"id": "logic", "kind": "limit", "n": 1}},
                "logic",
            ),
            (
                "stepped",
                {"replace": "start", "step": {"id": "logic", "kind": "limit", "n": 1}},
                "logic",
            ),
        ],
    )
    def test_an_unknown_or_duplicate_step_id_is_refused_naming_it(
        self, node: str, edit: dict, step: str
    ):
        with pytest.raises(OpValidationError) as excinfo:
            _apply(_stepped_graph(), _edit_steps(node, edit))

        assert excinfo.value.where == {
            "op_index": 0,
            "node": node,
            "field": "steps",
            "step": step,
        }
        assert repr(step) in str(excinfo.value)

    def test_a_code_mode_node_is_refused_pointing_at_its_code(self):
        with pytest.raises(OpValidationError) as excinfo:
            _apply(_stepped_graph(), _edit_steps("coded", {"remove": "logic"}))

        assert "code mode" in str(excinfo.value)
        assert excinfo.value.where == {"op_index": 0, "node": "coded", "field": "code"}
        assert excinfo.value.fix is not None and "update_node" in excinfo.value.fix

    def test_a_node_without_steps_or_code_is_refused_with_its_free_code_form(self):
        with pytest.raises(OpValidationError) as excinfo:
            _apply(_stepped_graph(), _edit_steps("bare", {"remove": "logic"}))

        assert excinfo.value.fix is not None and _FRAME_FORM in excinfo.value.fix

    @pytest.mark.parametrize(
        ("ops", "field"),
        [
            (_edit_steps("copy", {"remove": "logic"}), "steps"),
            (
                [
                    {
                        "op": "update_node",
                        "node": "copy",
                        "config": {"steps": [{"id": "start", "kind": "source", "input": "src"}]},
                    }
                ],
                "steps",
            ),
            ([{"op": "update_node", "node": "copy", "config": {"code": "df = src"}}], "code"),
            (
                [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "twin",
                        "config": {"instanceOf": "stepped", "code": "df = src"},
                    }
                ],
                "code",
            ),
        ],
    )
    def test_an_instance_is_refused_pointing_at_its_original(self, ops: list[dict], field: str):
        """An instance runs its original's configuration; its own would never be read."""

        base = _stepped_graph()
        graph = _graph([*base.nodes, _node("copy", instanceOf="stepped")], list(base.edges))

        with pytest.raises(OpValidationError) as excinfo:
            _apply(graph, ops)

        assert "instance of 'stepped'" in str(excinfo.value)
        assert excinfo.value.where["field"] == field
        assert excinfo.value.fix is not None
        assert "'stepped'" in excinfo.value.fix and "update_node {steps" not in excinfo.value.fix

    def test_detaching_an_instance_writes_its_own_steps(self):
        base = _stepped_graph()
        graph = _graph([*base.nodes, _node("copy", instanceOf="stepped")], list(base.edges))
        steps = [{"id": "start", "kind": "source", "input": "src"}]

        out = _apply(
            graph,
            [
                {
                    "op": "update_node",
                    "node": "copy",
                    "config": {"instanceOf": None, "steps": steps},
                }
            ],
        )

        config = _get(out, "copy").data.config
        assert "instanceOf" not in config and config["steps"] == steps

    def test_a_node_type_without_steps_is_refused(self):
        graph = _graph([_node("out", "output", fields=["x"])])

        with pytest.raises(OpValidationError, match="does not author steps"):
            _apply(graph, _edit_steps("out", {"remove": "logic"}))

    def test_an_edit_that_cannot_render_is_not_applied_naming_the_step(self):
        from haute.assistant._ops import AssistantOperationError

        with pytest.raises(AssistantOperationError) as excinfo:
            _apply(
                _stepped_graph(),
                _edit_steps("rated", {"replace": "logic", "step": {"kind": "filter"}}),
            )

        assert excinfo.value.code == "op_not_applied"
        assert excinfo.value.where == {
            "op_index": 0,
            "node": "rated",
            "field": "steps",
            "step": "logic",
        }

    @pytest.mark.parametrize(
        "edit",
        [
            {"replace": "logic", "remove": "logic"},
            {"insert_after": "logic"},
            {"replace": "logic", "step": {"kind": "limit", "n": 1}, "extra": 1},
        ],
    )
    def test_an_edit_in_no_single_shape_does_not_parse(self, edit: dict):
        with pytest.raises(OpValidationError):
            parse_ops(_edit_steps("stepped", edit))

    def test_an_empty_edit_list_does_not_parse(self):
        with pytest.raises(OpValidationError):
            parse_ops([{"op": "edit_steps", "node": "stepped", "edits": []}])


# ---------------------------------------------------------------------------
# rename_node
# ---------------------------------------------------------------------------


_JOIN = {"how": "left", "leftOn": ["k"], "rightOn": ["k"], "suffix": "_right"}


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

    @pytest.mark.parametrize(
        ("consumer", "key", "expected"),
        [
            (
                _node("sink", code="df = frame", inputMapping={"frame": "src"}),
                "inputMapping",
                {"frame": "renamed"},
            ),
            (
                _node(
                    "sink",
                    steps=[
                        {"id": "s", "kind": "source", "input": "src"},
                        {"id": "j", "kind": "join", "input": "src", **_JOIN},
                    ],
                ),
                "steps",
                [
                    {"id": "s", "kind": "source", "input": "renamed"},
                    {"id": "j", "kind": "join", "input": "renamed", **_JOIN},
                ],
            ),
            (
                _node(
                    "sink",
                    steps=[
                        {"id": "s", "kind": "source", "input": "other"},
                        {"id": "c", "kind": "concat", "inputs": ["src"], "how": "vertical"},
                    ],
                ),
                "steps",
                [
                    {"id": "s", "kind": "source", "input": "other"},
                    {"id": "c", "kind": "concat", "inputs": ["renamed"], "how": "vertical"},
                ],
            ),
            (
                _node("sink", "liveSwitch", input_scenario_map={"other": "batch", "src": "live"}),
                "input_scenario_map",
                {"other": "batch", "renamed": "live"},
            ),
            (_node("sink", "optimiser", data_input="src"), "data_input", "renamed"),
            (_node("sink", "optimiser", banding_source="src"), "banding_source", "renamed"),
            (_node("sink", "optimiser", analysis_input="src"), "analysis_input", "renamed"),
            (_node("sink", "optimiserApply", ratebook_input="src"), "ratebook_input", "renamed"),
            (
                _node(
                    "sink",
                    "output",
                    outputMapping=[
                        {
                            "source_port": "src",
                            "source_column": "x",
                            "output_path": "$.x",
                            "enabled": True,
                        }
                    ],
                ),
                "outputMapping",
                [
                    {
                        "source_port": "renamed",
                        "source_column": "x",
                        "output_path": "$.x",
                        "enabled": True,
                    }
                ],
            ),
        ],
    )
    def test_rename_rewrites_a_structured_consumer_field(
        self, consumer: GraphNode, key: str, expected: object
    ):
        graph = _graph(
            [_node("src", "dataInput"), _node("other", "dataInput"), consumer],
            [_edge("src", "sink"), _edge("other", "sink")],
        )

        out = _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert _get(out, "sink").data.config[key] == expected
        assert consumer.data.config[key] != expected

    def test_rename_rewrites_the_input_keys_of_the_targets_instances(self):
        graph = _graph(
            [
                _node("src", "dataInput"),
                _node("sink", steps=[{"id": "s", "kind": "source", "input": "src"}]),
                _node("copy", instanceOf="sink", inputMapping={"src": "other"}),
                _node("other", "dataInput"),
            ],
            [_edge("src", "sink"), _edge("other", "copy")],
        )

        out = _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert _get(out, "copy").data.config["inputMapping"] == {"renamed": "other"}

    def test_rename_with_stepped_mapped_and_free_code_consumers_lists_only_the_code(self):
        from haute.assistant._ops import RenameConsumersError

        graph = _graph(
            [
                _node("src", "dataInput"),
                _node("stepped", steps=[{"id": "s", "kind": "source", "input": "src"}]),
                _node("mapped", code="df = frame", inputMapping={"frame": "src"}),
                _node(
                    "free",
                    steps=[
                        {"id": "s", "kind": "source", "input": "src"},
                        {"id": "c", "kind": "free_code", "code": "df = df.join(src, on='k')"},
                    ],
                ),
            ],
            [_edge("src", "stepped"), _edge("src", "mapped"), _edge("src", "free")],
        )

        with pytest.raises(RenameConsumersError) as excinfo:
            _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert excinfo.value.consumers == (("free", "steps[2].code"),)

    @pytest.mark.parametrize(
        "consumer",
        [
            _node("sink", "liveSwitch", input_scenario_map={"src": "live", "renamed": "batch"}),
            _node("sink", code="df = frame", inputMapping={"frame": "src", "alt": "renamed"}),
        ],
    )
    def test_rename_refuses_a_rewrite_that_collides_in_a_mapping(self, consumer: GraphNode):
        graph = _graph(
            [_node("src", "dataInput"), consumer],
            [_edge("src", "sink")],
        )

        with pytest.raises(OpValidationError, match="'sink' already has an input named 'renamed'"):
            _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

    def test_rename_refuses_a_target_left_with_two_inputs_of_one_name(self):
        graph = _graph(
            [
                _node("src", "dataInput"),
                _node("api", "apiInput"),
                _node("sink", steps=[{"id": "s", "kind": "source", "input": "src"}]),
            ],
            [_edge("src", "sink"), _edge("api", "sink", sh="renamed")],
        )

        with pytest.raises(OpValidationError) as excinfo:
            _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert "'sink'" in str(excinfo.value) and "'renamed'" in str(excinfo.value)
        assert excinfo.value.where["node"] == "sink"

    def test_rename_reconciliation_covers_every_field_the_editor_reconciles(self):
        """The editor's rename plan and this operation rewrite the same fields.

        ``outputMapping`` is reconciled here only: the editor's Quote Response
        panel owns its mapping rows.
        """
        import re

        from haute.assistant._ops import RENAME_RECONCILED_FIELDS

        source = (
            Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "nodeUpdatePlan.ts"
        ).read_text(encoding="utf-8")
        union = re.search(r"field:\s*((?:\"\w+\"\s*\|\s*)*\"\w+\")", source)
        loop = re.search(r"for \(const field of \[([^\]]*)\] as const\)", source)
        assert union and loop, "nodeUpdatePlan.ts no longer declares its reconciled fields"
        editor_fields = set(re.findall(r"\"(\w+)\"", union.group(1) + loop.group(1)))
        assert "renameStepInputs(config.steps" in source
        editor_fields.add("steps")

        assert editor_fields <= set(RENAME_RECONCILED_FIELDS)
        assert set(RENAME_RECONCILED_FIELDS) - editor_fields == {"outputMapping"}

    @pytest.mark.parametrize(
        ("consumer", "field"),
        [
            (_node("sink", code="df = src.filter(pl.col('x') > 0)"), "code"),
            (_node("sink", code="df = ("), "code"),
            (
                _node(
                    "sink",
                    steps=[
                        {"id": "s", "kind": "source", "input": "other"},
                        {"id": "c", "kind": "free_code", "code": "df = df.join(src, on='k')"},
                    ],
                ),
                "steps[2].code",
            ),
        ],
    )
    def test_rename_refuses_a_consumer_whose_code_reads_the_input(
        self, consumer: GraphNode, field: str
    ):
        from haute.assistant._ops import RenameConsumersError

        graph = _graph(
            [_node("src", "dataInput"), _node("other", "dataInput"), consumer],
            [_edge("src", "sink"), _edge("other", "sink")],
        )

        with pytest.raises(RenameConsumersError) as excinfo:
            _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert excinfo.value.code == "rename_has_consumers"
        assert excinfo.value.consumers == (("sink", field),)
        assert "'sink'" in str(excinfo.value) and field in str(excinfo.value)

    def test_rename_refuses_code_and_instances_that_name_the_node(self):
        from haute.assistant._ops import RenameConsumersError

        graph = _graph(
            [
                _node("src", "dataInput"),
                _node("sink", code="df = src"),
                _node("copy", instanceOf="sink", inputMapping={"src": "src"}),
                _node("twin", instanceOf="src"),
            ],
            [_edge("src", "sink"), _edge("src", "copy")],
        )

        with pytest.raises(RenameConsumersError) as excinfo:
            _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert set(excinfo.value.consumers) == {("sink", "code"), ("twin", "instanceOf")}

    def test_rename_with_only_edge_consumers_applies(self):
        graph = _graph(
            [
                _node("src", "dataInput"),
                # The column 'src' and a keyword named src are not the input.
                _node("sink", code="df = pl.DataFrame({'src': [1]}).with_columns(src=pl.lit(1))"),
                _node("join", "edgeJoin"),
            ],
            [_edge("src", "sink"), _edge("src", "join", th="base")],
        )

        out = _apply(graph, [{"op": "rename_node", "node": "src", "new_name": "renamed"}])

        assert "renamed" in _ids(out)

    def test_rename_of_an_api_input_keeps_its_frame_names(self):
        graph = _graph(
            [_node("api", "apiInput"), _node("sink", code="df = quotes")],
            [_edge("api", "sink", sh="quotes")],
        )

        out = _apply(graph, [{"op": "rename_node", "node": "api", "new_name": "requests"}])

        assert "requests" in _ids(out)

    def test_rename_applies_after_an_earlier_op_rewrites_the_consumer(self):
        graph = _graph(
            [_node("src", "dataInput"), _node("sink", code="df = src")],
            [_edge("src", "sink")],
        )

        out = _apply(
            graph,
            [
                {"op": "update_node", "node": "sink", "config": {"code": "df = renamed"}},
                {"op": "rename_node", "node": "src", "new_name": "renamed"},
            ],
        )

        assert _get(out, "sink").data.config["code"] == "df = renamed"


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
        with pytest.raises(AssistantOperationError) as caught:
            _apply(
                base,
                [
                    {"op": "update_node", "node": "inner_child", "config": {"code": "df"}},
                ],
            )
        assert caught.value.code == "submodel_boundary"
        assert "cannot be read or edited by the assistant" in str(caught.value)
        assert caught.value.fix is not None and "BLOCKED:" in caught.value.fix

    def test_delete_submodel_placeholder_rejected(self):
        base = self._graph_with_submodel()
        with pytest.raises(AssistantOperationError, match="submodel") as caught:
            _apply(base, [{"op": "delete_node", "node": "submodel__sm1"}])
        assert caught.value.code == "submodel_boundary"


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
                    "config": {
                        "steps": [
                            {"id": "start", "kind": "source", "input": "source"},
                            {"id": "logic", "kind": "free_code", "code": "df = df.head(1)"},
                        ]
                    },
                },
                {"op": "add_edge", "source": "source", "target": "$fresh"},
            ],
        )

        assert plan.diff.nodes_updated == ("Fresh_Node",)
        assert plan.diff.config_changes == ("Fresh_Node:steps",)
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
                [{"op": "add_node", "node_type": "explore", "name": "orphan"}],
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
                    {"op": "add_node", "node_type": "explore", "name": "fresh", "ref": "fresh"},
                    {"op": "rename_node", "node": "$fresh", "new_name": "renamed"},
                ],
            )

        assert exc.value.code == "invalid_plan"
        assert "renamed" in str(exc.value)

    def test_a_rename_lists_each_reconciled_consumer_field_in_the_diff(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        # An analyst's free-code step the assistant's authoring rules would refuse
        # (its result is discarded) is not re-judged by a rename that only
        # rewrites the step list's input references.
        stepped = _node(
            "stepped",
            steps=[
                {"id": "s", "kind": "source", "input": "source"},
                {"id": "c", "kind": "free_code", "code": "df.head(1)"},
            ],
        )
        snapshot = build_project_snapshot(
            tmp_path,
            source,
            _graph(
                [
                    _node("source", "dataInput"),
                    stepped,
                    _node("switch", "liveSwitch", input_scenario_map={"source": "live"}),
                ],
                [_edge("source", "stepped"), _edge("source", "switch")],
            ),
        )

        plan = build_graph_edit_plan(
            snapshot,
            [{"op": "rename_node", "node": "source", "new_name": "renamed"}],
        )

        assert plan.diff.nodes_renamed == (("source", "renamed"),)
        assert plan.diff.nodes_updated == ("stepped", "switch")
        assert plan.diff.config_changes == (
            "stepped:steps[s].input",
            "switch:input_scenario_map.renamed",
        )
        assert {"node_config", "stepped"} <= {
            value for condition in plan.postconditions for value in condition.values()
        }

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
            "df = pl.LazyFrame({'age': [21]}).filter(pl.col('age') > 18)",
            "return pl.LazyFrame({'age': [21]}).with_columns(pl.lit(1).alias('one'))",
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

    @pytest.mark.parametrize(
        ("code", "rejected"),
        [
            # `df` is the output variable, never bound to an input; reading it
            # before assigning it is a guaranteed NameError at execution.
            ("df = df.group_by('quote_id').agg(pl.len())", True),
            ("df = df.filter(pl.col('age') > 18)", True),
            ("return df.with_columns(pl.lit(1).alias('one'))", True),
            # Naming the input, or binding it before reusing `df`, is explicit.
            ("df = left.group_by('quote_id').agg(pl.len())", False),
            ("df = left\ndf = df.group_by('quote_id').agg(pl.len())", False),
            # A store only establishes `df` when it definitely runs.  A
            # conditional or zero-iteration loop body cannot bind it for the
            # following statement, whereas complete conditional branches can.
            ("if False:\n    df = left\ndf = df.head()", True),
            ("for _ in ():\n    df = left\ndf = df.head()", True),
            ("if condition:\n    df = left", True),
            ("for _ in ():\n    df = left", True),
            (
                "try:\n    df = left\nexcept Exception:\n    pass\ndf = df.head()",
                True,
            ),
            ("df: pl.LazyFrame\ndf = df.head()", True),
            ("df += left", True),
            (
                "if condition:\n    df = left\nelse:\n    df = right\ndf = df.head()",
                False,
            ),
            # `df` bound by a nested scope is that scope's own name and never
            # resolves to the module-level output variable, so it is not a
            # bare read.
            ("def widen(df):\n    return df\ndf = widen(left)", False),
            ("widen = lambda df: df\ndf = widen(left)", False),
            ("df = [df.head() for df in (left, right)][0]", False),
            # A binding in a deeper scope cannot shadow an enclosing read, and
            # a comprehension's first iterable is evaluated before its target.
            (
                "def widen():\n    def inner():\n        df = left\n    return df\ndf = widen()",
                True,
            ),
            ("df = [left.head() for df in df][0]", True),
            ("def widen(df=df):\n    return df\ndf = widen()", True),
            ("def widen():\n    import polars as df\n    return df\ndf = widen()", False),
        ],
    )
    def test_multi_input_polars_code_must_name_the_input_it_starts_from(
        self, tmp_path: Path, code: str, rejected: bool
    ):
        from haute.assistant._ops import (
            OpValidationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph(
            [_node("left"), _node("right"), _node("combined")],
            [_edge("left", "combined"), _edge("right", "combined")],
        )
        snapshot = build_project_snapshot(tmp_path, source, graph)
        operations = [{"op": "update_node", "node": "combined", "config": {"code": code}}]

        if not rejected:
            assert build_graph_edit_plan(snapshot, operations).diff.config_changes == (
                "combined:code",
            )
            return
        with pytest.raises(OpValidationError) as excinfo:
            build_graph_edit_plan(snapshot, operations)
        assert "reads 'df' before assigning it" in str(excinfo.value)
        assert "left, right" in str(excinfo.value)

    def test_single_input_polars_code_must_also_name_its_input(self, tmp_path: Path):
        """`df` is unbound on any input count; a single-input bare read is
        rejected with the node's actual input name."""

        from haute.assistant._ops import (
            OpValidationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph(
            [_node("left"), _node("combined")],
            [_edge("left", "combined")],
        )
        snapshot = build_project_snapshot(tmp_path, source, graph)

        with pytest.raises(OpValidationError) as excinfo:
            build_graph_edit_plan(
                snapshot,
                [
                    {
                        "op": "update_node",
                        "node": "combined",
                        "config": {"code": "df = df.filter(pl.col('age') > 18)"},
                    }
                ],
            )
        assert "reads 'df' before assigning it" in str(excinfo.value)
        assert "left" in str(excinfo.value)

    def test_polars_input_cannot_use_the_reserved_df_output_name(self, tmp_path: Path):
        from haute.assistant._ops import (
            OpValidationError,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph(
            [_node("df"), _node("combined")],
            [_edge("df", "combined")],
        )
        snapshot = build_project_snapshot(tmp_path, source, graph)

        with pytest.raises(OpValidationError, match="reserved output name"):
            build_graph_edit_plan(
                snapshot,
                [
                    {
                        "op": "update_node",
                        "node": "combined",
                        "config": {"code": "df = pl.LazyFrame({'x': [1]})"},
                    }
                ],
            )

    @staticmethod
    def _stepped_plan(
        tmp_path: Path,
        node: str,
        steps: list[dict],
        preamble: str | None = None,
        *,
        edits: list[dict] | None = None,
    ):
        """Plan setting *node*'s steps in quotes -> prepared -> {t, rated}.

        With *edits*, *node* already holds *steps* and the plan edits them.
        """
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        rating_tables = [
            {
                "factors": ["region"],
                "outputColumn": "rate_factor",
                "defaultValue": "1.0",
                "entries": [{"region": "north", "value": "1.25"}],
            }
        ]
        graph = _graph(
            [
                _node("quotes", "dataInput", path="quotes.parquet"),
                _node("prepared", code="df = quotes"),
                _node("t"),
                _node("rated", "ratingStep", tables=rating_tables, combinedOutputs=[]),
            ],
            [_edge("quotes", "prepared"), _edge("prepared", "t"), _edge("prepared", "rated")],
        )
        graph.preamble = preamble
        if edits is not None:
            index = next(i for i, item in enumerate(graph.nodes) if item.id == node)
            graph.nodes[index] = graph.nodes[index].with_config(
                {**graph.nodes[index].data.config, "steps": steps}
            )
        snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = (
            [{"op": "update_node", "node": node, "config": {"steps": steps}}]
            if edits is None
            else [{"op": "edit_steps", "node": node, "edits": edits}]
        )
        return build_graph_edit_plan(snapshot, ops)

    def test_edit_steps_records_each_step_it_changes(self, tmp_path: Path):
        steps = [
            {"id": "keep", "kind": "limit", "n": 2},
            {"id": "logic", "kind": "free_code", "code": "df = df.head(1)"},
        ]
        plan = self._stepped_plan(
            tmp_path,
            "rated",
            steps,
            edits=[
                {"remove": "keep"},
                {"insert_after": "logic", "step": {"kind": "limit", "n": 1}},
            ],
        )

        assert plan.diff.nodes_updated == ("rated",)
        assert plan.diff.config_changes == ("rated:steps[keep]", "rated:steps[limit_1]")
        assert plan.diff.sidecar_changes == ("config/rating_step/rated",)

    def test_an_edited_free_code_step_is_checked_naming_its_step(self, tmp_path: Path):
        steps = [
            {"id": "keep", "kind": "limit", "n": 2},
            {"id": "logic", "kind": "free_code", "code": "df = df.head(1)"},
        ]
        with pytest.raises(OpValidationError) as excinfo:
            self._stepped_plan(
                tmp_path,
                "rated",
                steps,
                edits=[
                    {
                        "replace": "logic",
                        "step": {"kind": "free_code", "code": "df.drop('x')"},
                    }
                ],
            )

        assert "step 2 ('logic')" in str(excinfo.value)
        assert excinfo.value.where["step"] == "logic"

    @pytest.mark.parametrize(
        ("node", "steps"),
        [
            # The rendered `df = prepared` line retains a frame, but not this step's.
            (
                "t",
                [
                    {"id": "start", "kind": "source", "input": "prepared"},
                    {"id": "logic", "kind": "free_code", "code": "df.filter(pl.col('x') > 1)"},
                ],
            ),
            (
                "rated",
                [
                    {"id": "keep", "kind": "limit", "n": 2},
                    {"id": "logic", "kind": "free_code", "code": "# tidy\ndf.drop('x')"},
                ],
            ),
        ],
    )
    def test_a_free_code_step_whose_frame_result_is_discarded_is_refused(
        self, tmp_path: Path, node: str, steps: list[dict]
    ):
        with pytest.raises(OpValidationError) as excinfo:
            self._stepped_plan(tmp_path, node, steps)
        message = str(excinfo.value)
        assert "step 2 ('logic')" in message
        assert "discarded" in message

    @pytest.mark.parametrize(
        ("code", "name"),
        [
            ("df = prepared.head(2)", "prepared"),
            ("df = df.join(quotes, on='region')", "quotes"),
        ],
    )
    def test_a_rating_step_step_reading_an_input_by_name_is_refused(
        self, tmp_path: Path, code: str, name: str
    ):
        steps = [{"id": "logic", "kind": "free_code", "code": code}]
        with pytest.raises(OpValidationError) as excinfo:
            self._stepped_plan(tmp_path, "rated", steps)
        message = str(excinfo.value)
        assert f"Rating Step code sees only df; {name} is not in scope" in message
        assert "step 1 ('logic')" in message

    @pytest.mark.parametrize(
        "code",
        [
            "# Keep two rows\ndf = keep_two(df)",
            "def keep(frame):\n    return frame.head(2)\n\ndf = keep(df)",
            "prepared = df.head(2)\ndf = prepared.with_columns(pl.lit(len('ab')).alias('n'))",
        ],
    )
    def test_a_rating_step_step_using_helpers_and_its_own_names_is_accepted(
        self, tmp_path: Path, code: str
    ):
        steps = [{"id": "logic", "kind": "free_code", "code": code}]
        preamble = "def keep_two(frame):\n    return frame.head(2)\n"
        plan = self._stepped_plan(tmp_path, "rated", steps, preamble=preamble)
        assert plan.diff.config_changes == ("rated:steps",)

    @pytest.mark.parametrize(
        ("node", "steps", "fix"),
        [
            (
                "t",
                [
                    {"id": "start", "kind": "source", "input": "prepared"},
                    {"id": "logic", "kind": "free_code", "code": "df = df['prepared'].head(2)"},
                ],
                "df is already the 'prepared' frame the source step chose; transform df "
                "directly (df = df.filter(...)).",
            ),
            (
                "rated",
                [{"id": "logic", "kind": "free_code", "code": "df = df['prepared']"}],
                "df is this node's own frame; transform df directly (df = df.filter(...)).",
            ),
        ],
    )
    def test_a_step_indexing_df_by_an_input_name_names_the_operation_that_wrote_it(
        self, tmp_path: Path, node: str, steps: list[dict], fix: str
    ):
        """The check runs on the planned graph after the whole batch, and its
        failure still names the operation that wrote the node."""

        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _graph(
            [
                _node("quotes", "dataInput", path="quotes.parquet"),
                _node("prepared", code="df = quotes"),
                _node("t"),
                _node(
                    "rated",
                    "ratingStep",
                    tables=[
                        {
                            "factors": ["region"],
                            "outputColumn": "rate_factor",
                            "defaultValue": "1.0",
                            "entries": [{"region": "north", "value": "1.25"}],
                        }
                    ],
                    combinedOutputs=[],
                ),
            ],
            [_edge("quotes", "prepared"), _edge("prepared", "t"), _edge("prepared", "rated")],
        )
        snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = [
            {"op": "update_node", "node": "prepared", "config": {"code": "df = quotes.head(5)"}},
            {"op": "update_node", "node": node, "config": {"steps": steps}},
        ]

        with pytest.raises(OpValidationError) as excinfo:
            build_graph_edit_plan(snapshot, ops)

        error = excinfo.value
        assert "indexes df by the input name 'prepared'" in str(error)
        assert error.where == {"op_index": 1, "node": node, "field": "steps", "step": "logic"}
        assert error.fix == fix
        assert error.graph is not None

    def test_a_column_named_like_an_input_is_not_mistaken_for_indexing(self, tmp_path: Path):
        steps = [
            {"id": "start", "kind": "source", "input": "prepared"},
            {"id": "logic", "kind": "free_code", "code": "df = df.filter(pl.col('prepared') > 0)"},
        ]
        plan = self._stepped_plan(tmp_path, "t", steps)
        assert plan.diff.config_changes == ("t:steps",)

    def test_a_failing_operation_is_located_by_its_index(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("a")], []))

        with pytest.raises(OpValidationError) as excinfo:
            build_graph_edit_plan(
                snapshot,
                [
                    {"op": "update_node", "node": "a", "config": {"code": "df = pl.LazyFrame()"}},
                    {"op": "update_node", "node": "b", "config": {}},
                ],
            )

        assert excinfo.value.where == {"op_index": 1}
        assert excinfo.value.fix == (
            "Add 'b' with add_node before operation 1: each dry run is a whole plan, and "
            "a node a failed dry run proposed was never kept. Otherwise use a node id "
            "get_pipeline lists."
        )

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

    def test_config_postconditions_cover_every_written_node_beyond_the_diff_limit(
        self, tmp_path: Path
    ):
        from haute.assistant._ops import (
            AssistantOperationError,
            build_graph_edit_plan,
            build_project_snapshot,
            verify_postconditions,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        coded = [_node(f"coded_{index:02d}", code="df = src") for index in range(50)]
        coded.append(_node("zz_coded", code="df = src"))
        graph = _graph([_node("src"), *coded], [_edge("src", node.id) for node in coded])
        snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = [
            {"op": "update_node", "node": node.id, "config": {"code": "df = src.head(3)\n"}}
            for node in coded
        ]

        plan = build_graph_edit_plan(snapshot, ops)

        assert plan.diff.truncated is True
        config_nodes = [
            condition["node"]
            for condition in plan.postconditions
            if condition["kind"] == "node_config"
        ]
        assert config_nodes == [node.id for node in coded]
        saved = _apply(graph, ops)
        last = _get(saved, "zz_coded")
        saved.nodes[saved.nodes.index(last)] = last.with_config({"code": "df = src"})
        with pytest.raises(AssistantOperationError) as excinfo:
            verify_postconditions(saved, plan.postconditions)
        assert excinfo.value.code == "postcondition_failed"

    def test_a_plan_at_the_operation_cap_seals_and_replays_every_config_postcondition(
        self, tmp_path: Path
    ):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot
        from haute.assistant._wire_ops import MAX_PLAN_OPERATIONS

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        coded = [
            _node(f"coded_{index:03d}", code="df = src") for index in range(MAX_PLAN_OPERATIONS)
        ]
        graph = _graph([_node("src"), *coded], [_edge("src", node.id) for node in coded])
        snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = [
            {"op": "update_node", "node": node.id, "config": {"code": "df = src.head(3)\n"}}
            for node in coded
        ]
        declared = [{"kind": "node_exists", "node": node.id} for node in coded]

        plan = build_graph_edit_plan(snapshot, ops, postconditions=declared)

        assert len(plan.postconditions) == 2 * MAX_PLAN_OPERATIONS
        replayed = build_graph_edit_plan(
            snapshot, ops, postconditions=[dict(item) for item in plan.as_dict()["postconditions"]]
        )
        assert replayed.plan_hash == plan.plan_hash

    def test_a_free_code_step_beyond_the_diff_limit_is_still_refused(self, tmp_path: Path):
        from haute.assistant._ops import build_graph_edit_plan, build_project_snapshot

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        filler = [_node(f"coded_{index:02d}", code="df = src") for index in range(50)]
        graph = _graph(
            [_node("src"), *filler, _node("zz_rated", "ratingStep")],
            [_edge("src", node.id) for node in [*filler, _node("zz_rated")]],
        )
        snapshot = build_project_snapshot(tmp_path, source, graph)
        ops = [
            *(
                {"op": "update_node", "node": node.id, "config": {"code": "df = src.head(3)\n"}}
                for node in filler
            ),
            {
                "op": "update_node",
                "node": "zz_rated",
                "config": {"steps": [{"id": "logic", "kind": "free_code", "code": "df = src"}]},
            },
        ]

        with pytest.raises(OpValidationError) as excinfo:
            build_graph_edit_plan(snapshot, ops)
        assert "Rating Step code sees only df; src is not in scope" in str(excinfo.value)

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

    @pytest.mark.parametrize(
        "supplied",
        [[], [{"kind": "node_exists", "node": "rated"}]],
    )
    def test_updated_code_carrying_nodes_carry_a_config_postcondition(
        self, tmp_path: Path, supplied: list[dict]
    ):
        from haute.assistant._ops import (
            AssistantOperationError,
            build_graph_edit_plan,
            build_project_snapshot,
            verify_postconditions,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        graph = _stepped_graph()
        snapshot = build_project_snapshot(tmp_path, source, graph)
        steps = [{"id": "logic", "kind": "free_code", "code": "df = df.head(3)"}]
        ops = [
            {"op": "update_node", "node": "rated", "config": {"steps": steps}},
            {"op": "update_node", "node": "coded", "config": {"code": "df = src.head(3)\n"}},
        ]
        plan = build_graph_edit_plan(snapshot, ops, postconditions=supplied)

        config_nodes = [
            condition["node"]
            for condition in plan.postconditions
            if condition["kind"] == "node_config"
        ]
        assert config_nodes == ["coded", "rated"]
        # Replaying the sealed plan's own postconditions reproduces it exactly.
        replayed = build_graph_edit_plan(
            snapshot, ops, postconditions=[dict(item) for item in plan.as_dict()["postconditions"]]
        )
        assert replayed.postconditions == plan.postconditions

        saved = _apply(graph, ops)
        # Reparse returns code-mode code normalised; the digest compares it so.
        coded = _get(saved, "coded")
        saved.nodes[saved.nodes.index(coded)] = coded.with_config({"code": "df = src.head(3)"})
        verify_postconditions(saved, plan.postconditions)

        rated = _get(saved, "rated")
        discarded = rated.with_config({"code": "df = df.head(3)", "_steps_discarded": "x"})
        saved.nodes[saved.nodes.index(rated)] = discarded
        with pytest.raises(AssistantOperationError) as excinfo:
            verify_postconditions(saved, plan.postconditions)
        assert excinfo.value.code == "postcondition_failed"


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
        store.put(plan, _RECEIPT)
        store.begin_apply(plan.plan_hash)

        now = 2.0
        store.complete_apply(plan.plan_hash, {"result_revision": "a" * 64})

        with pytest.raises(AssistantOperationError) as exc:
            store.get(plan.plan_hash)
        assert exc.value.code == "plan_expired"

    @pytest.mark.parametrize("ending", ["complete", "abort"])
    def test_plans_awaiting_use_are_bounded_and_a_lease_does_not_count(
        self, tmp_path: Path, ending: str
    ):
        """An applying lease is pinned outside the bound on plans awaiting use;
        when it ends, completed or aborted, the least recently used plan beyond
        the bound is dropped."""
        from haute.assistant._ops import (
            AssistantOperationError,
            PlanStore,
            build_graph_edit_plan,
            build_project_snapshot,
        )

        source = tmp_path / "main.py"
        source.write_text("pipeline", encoding="utf-8")
        snapshot = build_project_snapshot(tmp_path, source, _graph([_node("source")]))
        leased, older, newer, newest = (
            build_graph_edit_plan(
                snapshot,
                [{"op": "rename_node", "node": "source", "new_name": name}],
            )
            for name in ("leased", "older", "newer", "newest")
        )
        store = PlanStore(max_size=2)
        store.put(leased, _RECEIPT)
        store.begin_apply(leased.plan_hash)
        store.put(older, _RECEIPT)
        store.put(newer, _RECEIPT)
        assert len(store) == 3, "the lease sits outside the two plans awaiting use"

        store.put(newest, _RECEIPT)  # the least recently used plan awaiting use goes
        with pytest.raises(AssistantOperationError) as exc:
            store.get(older.plan_hash)
        assert exc.value.code == "plan_not_found"
        assert store.get(newer.plan_hash) == newer  # a read is a use: newest is now LRU

        if ending == "complete":
            store.complete_apply(leased.plan_hash, {"result_revision": "a" * 64})
        else:
            store.abort_apply(leased.plan_hash)
        assert len(store) == 2
        with pytest.raises(AssistantOperationError) as exc:
            store.get(newest.plan_hash)
        assert exc.value.code == "plan_not_found"
        assert store.get(newer.plan_hash) == newer
        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(leased.plan_hash)
        assert exc.value.code == (
            "plan_already_applied" if ending == "complete" else "plan_aborted"
        )

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
        store.put(first, _RECEIPT)
        store.begin_apply(first.plan_hash)

        with pytest.raises(AssistantOperationError) as exc:
            store.put(second, _RECEIPT)
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
        store.put(plan, _RECEIPT)
        assert store.begin_apply(plan.plan_hash) == plan
        store.complete_apply(plan.plan_hash, {"result_revision": "a" * 64})

        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(plan.plan_hash)
        assert exc.value.code == "plan_already_applied"

    def test_a_fresh_dry_run_of_an_applied_plan_issues_it_again(self, tmp_path: Path):
        """The hash covers the base revision, so a fresh dry-run producing an applied
        plan's hash means an undo restored that revision: the plan applies once more."""

        from haute.assistant._ops import (
            PlanReceipt,
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
        store.put(plan, _RECEIPT)
        store.begin_apply(plan.plan_hash)
        store.complete_apply(plan.plan_hash, {"result_revision": "a" * 64})

        store.put(plan, PlanReceipt("Rename it again."))

        assert store.begin_apply(plan.plan_hash) == plan
        assert store.receipt(plan.plan_hash).summary == "Rename it again."

    @pytest.mark.parametrize("text", ["Rename\x1ethe node.", "Rename\x00it."])
    def test_a_receipt_refuses_control_characters(self, text: str):
        from haute.assistant._ops import AssistantOperationError, PlanReceipt

        with pytest.raises(AssistantOperationError) as exc:
            PlanReceipt(text)
        assert exc.value.code == "invalid_request"
        with pytest.raises(AssistantOperationError):
            PlanReceipt("Rename the node.", (text,))
        # Whitespace, including a line break, is ordinary text.
        assert PlanReceipt("Rename\nthe node.\t").summary == "Rename\nthe node.\t"

    def test_a_receipt_holds_a_two_sentence_summary_and_refuses_one_past_the_bound(self):
        """A live run's summaries ran past 160 characters; the bound fits two sentences."""
        from haute.assistant._ops import AssistantOperationError, PlanReceipt
        from haute.schemas import ASSISTANT_RECEIPT_TEXT_LIMIT

        assert ASSISTANT_RECEIPT_TEXT_LIMIT == 400
        summary = (
            "Join the competitor insight onto the new-business batch by quote id, keeping "
            "every quote in the batch. Write the enriched quotes to a new output node so "
            "the analyst can review the competitor premium beside our own."
        )
        assert 160 < len(summary) <= ASSISTANT_RECEIPT_TEXT_LIMIT
        assert PlanReceipt(summary, (summary,)).summary == summary
        with pytest.raises(AssistantOperationError) as exc:
            PlanReceipt("x" * (ASSISTANT_RECEIPT_TEXT_LIMIT + 1))
        assert exc.value.code == "invalid_request"
        assert "400 characters" in str(exc.value)

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
        store.put(plan, _RECEIPT)
        store.begin_apply(plan.plan_hash)
        store.abort_apply(plan.plan_hash)

        with pytest.raises(AssistantOperationError) as exc:
            store.begin_apply(plan.plan_hash)
        assert exc.value.code == "plan_aborted"
        assert "dry-run" in str(exc.value)

        store.put(plan, _RECEIPT)
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
        store.put(plan, _RECEIPT)

        assert store.begin_apply(plan.plan_hash) == plan


class TestChangeHeadline:
    """The Git commit subject a summary yields: one line, cut at a word boundary."""

    def test_a_short_summary_is_its_headline_on_one_line(self):
        from haute.assistant._change_record import change_headline

        assert change_headline("  Add an age band\nafter\tquotes.  ") == (
            "Add an age band after quotes."
        )

    def test_a_long_summary_is_cut_at_a_word_boundary_with_an_ellipsis(self):
        from haute.assistant._change_record import CHANGE_HEADLINE_LIMIT, change_headline

        assert CHANGE_HEADLINE_LIMIT == 100
        summary = (
            "Join the competitor insight onto the new-business batch by quote id, keeping "
            "every quote in the batch.\nWrite the enriched quotes to a new output node."
        )
        headline = change_headline(summary)
        assert headline == (
            "Join the competitor insight onto the new-business batch by quote id, keeping "
            "every quote in the…"
        )
        assert len(headline) <= CHANGE_HEADLINE_LIMIT
        # Exactly at the limit, nothing is cut.
        exact = "word " * 19 + "abcde"
        assert len(exact) == CHANGE_HEADLINE_LIMIT
        assert change_headline(exact) == exact

    def test_a_single_word_past_the_limit_is_cut_inside_it(self):
        from haute.assistant._change_record import CHANGE_HEADLINE_LIMIT, change_headline

        headline = change_headline("x" * 150)
        assert headline == "x" * (CHANGE_HEADLINE_LIMIT - 1) + "…"
