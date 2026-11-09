"""Authoring through the assistant's tools: steps, recipes, build plans and views.

The authoring guide's worked examples fill the named nodes as the model sends them, a recipe
expands inside one dry-run plan, the executor records applies against the build plan, a node is
rewritten only after the turn has read it, and the graph views show how each stepped node is
authored under the policy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests import _assistant_tool_helpers
from tests._assistant_tool_helpers import (
    _REGION_BANDING,
    AUGUST_DISCARDED,
    _august_ops,
    _guide_json_blocks,
    _guide_step_lists,
    _policy,
    _reloaded,
    _stored_plan,
    _turn_policy,
)

# Fixtures shared with the other modules split from one suite.
steps_first_project = _assistant_tool_helpers.steps_first_project


# ---------------------------------------------------------------------------
# Steps-first authoring replays (ASSIST-03)
# ---------------------------------------------------------------------------


def _guide_edit_steps() -> dict[str, object]:
    """The guide's one `edit_steps` operation."""

    (operation,) = [block for block in _guide_json_blocks() if isinstance(block, dict)]
    assert operation["op"] == "edit_steps"
    return operation


class TestStepsFirstAuthoring:
    """The authoring guide's worked examples, sent as the model would send
    them, fill the named palette-default node through the real dry-run and
    apply tools, stay in the step builder and run."""

    @pytest.mark.parametrize(
        ("example", "node", "config", "key", "expected"),
        [
            pytest.param(
                0,
                "august_totals",
                {},
                "policy_id",
                {"policy_id": ["p1", "p2"], "august_claims": [170.0, 10.0]},
                id="august-aggregation-on-a-transform",
            ),
            pytest.param(
                1,
                "rated",
                {},
                "quote_id",
                {"quote_id": ["q1", "q2"], "premium": [100.0, 150.0]},
                id="rating-step-hook",
            ),
            pytest.param(
                2,
                "loaded",
                {"path": "loadings.json", "file_type": "json"},
                "quote_id",
                {"quote_id": ["q1", "q2"], "zone": ["A", "B"], "loading": [1.1, 0.9]},
                id="load-file-with-obj-and-a-second-input",
            ),
        ],
    )
    async def test_a_guide_example_fills_the_named_node_and_runs(
        self,
        steps_first_project: Path,
        example: int,
        node: str,
        config: dict[str, str],
        key: str,
        expected: dict[str, list[object]],
    ):
        from haute._native_memory_limit import native_memory_backend_scope
        from haute.assistant._tools import apply_graph_plan, dry_run_graph_edits
        from haute.executor import execute_graph
        from haute.routes._helpers import parse_pipeline_to_graph

        steps = _guide_step_lists()[example]
        before = parse_pipeline_to_graph(steps_first_project / "main.py")
        ops = [{"op": "update_node", "node": node, "config": {**config, "steps": steps}}]

        plan = await dry_run_graph_edits(
            "main.py", ops, summary="Test plan.", config_visibility=None
        )
        assert "error" not in plan, plan
        applied = await apply_graph_plan("main.py", plan["plan_hash"], session_id="test")
        assert "error" not in applied, applied

        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        assert [item.id for item in graph.nodes] == [item.id for item in before.nodes]
        saved = next(item for item in graph.nodes if item.id == node).data
        # A stepped node's steps come back from its code (READ-22, READ-44); an Explore
        # node's decorator keeps its own.
        assert saved.config["steps"] == (steps if saved.nodeType == "explore" else _reloaded(steps))
        assert saved.editor.steps_error is None and saved.editor.steps_discarded is None
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(graph, target_node_id=node)[node]
        assert result.status == "ok", result.error
        rows = sorted(result.preview, key=lambda row: row[key])
        assert {column: [row[column] for row in rows] for column in expected} == expected

    async def test_a_rename_rewrites_a_stepped_consumer_in_one_dry_run(
        self, steps_first_project: Path
    ):
        """A stepped consumer follows a rename; its free code never does."""
        from haute._native_memory_limit import native_memory_backend_scope
        from haute.assistant._tools import apply_graph_plan, dry_run_graph_edits
        from haute.executor import execute_graph
        from haute.routes._helpers import parse_pipeline_to_graph

        filled = await dry_run_graph_edits(
            "main.py",
            _august_ops("df = pl.concat([df, additional_drivers_claims])"),
            summary="Test plan.",
            config_visibility=None,
        )
        assert "error" not in filled, filled
        assert "error" not in await apply_graph_plan(
            "main.py", filled["plan_hash"], session_id="test"
        )

        refused = await dry_run_graph_edits(
            "main.py",
            [{"op": "rename_node", "node": "additional_drivers_claims", "new_name": "drivers"}],
            summary="Test plan.",
            config_visibility=None,
        )
        assert refused["error"]["code"] == "rename_has_consumers"
        assert refused["error"]["consumers"] == [
            {"node": "august_totals", "field": "steps[2].code"}
        ]

        plan = await dry_run_graph_edits(
            "main.py",
            [{"op": "rename_node", "node": "proposer_claims", "new_name": "proposer"}],
            summary="Test plan.",
            config_visibility=None,
        )
        assert "error" not in plan, plan
        diff = _stored_plan(plan).diff
        assert list(diff.nodes_updated) == ["august_totals"]
        # The fill's apply reloaded the steps with ids from kind and place (READ-22).
        assert list(diff.config_changes) == ["august_totals:steps[source-1].input"]
        consumer = next(node for node in plan["changes"]["nodes"] if node["id"] == "august_totals")
        assert (consumer["change"], consumer["steps_changed"]) == ("changed", 1)
        assert "error" not in await apply_graph_plan(
            "main.py", plan["plan_hash"], session_id="test"
        )

        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        saved = next(item for item in graph.nodes if item.id == "august_totals").data.config
        assert saved["steps"][0] == {"id": "source-1", "kind": "source", "input": "proposer"}
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(graph, target_node_id="august_totals")["august_totals"]
        assert result.status == "ok", result.error
        assert len(result.preview) == 5


class TestRecipeOperations:
    """A recipe is one operation of a dry-run batch, expanded inside the same plan."""

    @pytest.fixture(autouse=True)
    def _internal_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import haute.assistant._tools as tools_module

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: _policy(max_sensitivity="internal", executable=False),
        )

    async def test_a_recipe_and_a_primitive_operation_apply_in_one_plan(
        self, steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute._native_memory_limit import native_memory_backend_scope
        from haute.executor import execute_graph
        from haute.routes._helpers import parse_pipeline_to_graph

        execute = tools_module.build_tool_executor("main.py")
        steps = [
            {"id": "start", "kind": "source", "input": "region_band"},
            {
                "id": "logic",
                "kind": "free_code",
                "code": "# Double the premium\ndf = df.with_columns(double=pl.col('premium') * 2)",
            },
        ]

        plan = await execute(
            "dry_run_graph_edits",
            {
                "summary": "Band regions and double the premium after the banding.",
                "ops": [
                    {**_REGION_BANDING, "ref": "band"},
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "doubled",
                        "ref": "doubled",
                        "config": {"steps": steps},
                    },
                    {"op": "add_edge", "source": "$band", "target": "$doubled"},
                ],
            },
        )
        assert "error" not in plan, plan
        # The recipe expands into its node and its input edge inside the plan.
        assert plan["operations"] == 4
        applied = await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})
        assert "error" not in applied, applied

        # One plan, so one change card naming both the recipe's node and the primitive one.
        change = applied["change"]
        assert {chip["id"] for chip in change["changes"]["nodes"]} == {"region_band", "doubled"}
        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        assert {(edge.source, edge.target) for edge in graph.edges} >= {
            ("quotes", "region_band"),
            ("region_band", "doubled"),
        }
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(graph, target_node_id="doubled")["doubled"]
        assert result.status == "ok", result.error
        assert [(row["region_group"], row["double"]) for row in result.preview] == [
            ("core", 200.0),
            ("other", 400.0),
        ]

    async def test_two_recipes_in_one_plan_keep_their_own_refs(self, steps_first_project: Path):
        from haute.assistant._tools import build_tool_executor

        plan = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Band regions and map the response.",
                "ops": [
                    _REGION_BANDING,
                    {
                        "op": "recipe",
                        "recipe": "response_output",
                        "arguments": {
                            "source": "quotes",
                            "output_name": "quote_response",
                            "output_columns": ["quote_id", "premium"],
                        },
                    },
                ],
            },
        )

        assert "error" not in plan, plan
        assert {node["id"] for node in plan["changes"]["nodes"]} == {
            "region_band",
            "quote_response",
        }

    async def test_a_recipe_argument_failure_is_located_at_the_recipe_operation(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        banding = {
            **_REGION_BANDING,
            "arguments": {
                **_REGION_BANDING["arguments"],
                "rules": [
                    {"value": "north", "assignment": "core"},
                    {"value": "north", "assignment": "other"},
                ],
            },
        }
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Band regions.",
                "ops": [{"op": "delete_node", "node": "loaded"}, banding],
            },
        )

        error = result["error"]
        assert error["code"] == "recipe_argument_invalid"
        assert error["where"] == {
            "op_index": 1,
            "recipe": "categorical_banding",
            "field": "arguments.rules[1].value",
        }
        assert "recipe:categorical_banding" in error["fix"]
        assert error["retryable"] is True

    async def test_a_failure_after_a_recipe_names_the_operation_the_model_sent(
        self, steps_first_project: Path
    ):
        """The recipe expands into two operations; the edge after it is still
        operation 1 in the batch the model sent, in `where` and in the text."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Band regions.",
                "ops": [
                    _REGION_BANDING,
                    {"op": "add_edge", "source": "region_band", "target": "$later"},
                    {"op": "add_node", "node_type": "explore", "name": "later", "ref": "later"},
                ],
            },
        )

        error = result["error"]
        assert error["where"]["op_index"] == 1
        assert "recipe" not in error["where"]
        assert "(operation 2) before operation 1" in error["fix"]


class TestBuildPlan:
    """The executor updates the session's build plan and records an apply's
    committed change against the item it names; only the model claims completion."""

    @pytest.fixture(autouse=True)
    def _internal_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import haute.assistant._tools as tools_module

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: _policy(max_sensitivity="internal", executable=False),
        )

    async def test_a_claim_needs_a_saved_change_that_an_apply_naming_the_item_records(
        self, steps_first_project: Path
    ):
        from haute.assistant._build_plan import BuildPlan
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor
        from haute.routes._helpers import parse_pipeline_to_graph

        plan = BuildPlan()
        execute = build_tool_executor("main.py", plan=plan)
        stages = [
            {"id": "banding", "title": "Region banding"},
            {"id": "response", "title": "Quote response"},
        ]

        assert await execute("update_build_plan", {"items": stages}) == {
            "items": [
                {"id": "banding", "title": "Region banding", "complete": False, "changes": 0},
                {"id": "response", "title": "Quote response", "complete": False, "changes": 0},
            ],
            "capability_hash": capability_manifest().capability_hash,
            "operation_version": "1.0",
        }
        before = plan.current
        refused = await execute("update_build_plan", {"complete": "banding"})
        assert refused["error"]["code"] == "plan_item_unsaved"
        assert refused["error"]["retryable"] is True
        assert refused["error"]["where"] == {"field": "complete", "item": "banding"}
        assert "apply_graph_plan" in refused["error"]["fix"]
        assert plan.current is before

        dry_run = await execute(
            "dry_run_graph_edits", {"summary": "Band regions.", "ops": [_REGION_BANDING]}
        )
        assert "error" not in dry_run, dry_run
        unknown = await execute(
            "apply_graph_plan", {"plan_hash": dry_run["plan_hash"], "item": "bandng"}
        )
        assert unknown["error"]["code"] == "unknown_plan_item"
        assert unknown["error"]["retryable"] is True
        assert unknown["error"]["where"] == {"field": "item"}
        assert unknown["error"]["did_you_mean"] == ["banding"]
        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        assert "region_band" not in {node.id for node in graph.nodes}

        applied = await execute(
            "apply_graph_plan", {"plan_hash": dry_run["plan_hash"], "item": "banding"}
        )
        assert "error" not in applied, applied
        assert applied["item"] == "banding"
        assert plan.current is not None
        banding = plan.current.items[0]
        assert (banding.complete, [change.id for change in banding.changes]) == (
            False,
            [applied["change"]["id"]],
        )

        claimed = await execute("update_build_plan", {"complete": "banding"})
        assert claimed["items"][0] == {
            "id": "banding",
            "title": "Region banding",
            "complete": True,
            "changes": 1,
        }
        assert claimed["items"][1]["complete"] is False

    async def test_a_committed_save_that_fails_verification_still_counts_toward_its_item(
        self, steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The save committed, so the analyst can undo it: its change is recorded
        against the item like a verified one's, and the item stays open."""

        import haute.assistant._tools as tools_module
        from haute.assistant._build_plan import BuildPlan

        committed = {
            "id": "c" * 64,
            "summary": "Band regions.",
            "changes": {"nodes": [{"id": "region_band", "type": "Banding", "change": "added"}]},
            "git_sha": None,
            "parent_sha": None,
            "revision": "r" * 64,
        }

        async def unverified(_source_file, _plan_hash, *, session_id):
            return {
                "error": {"code": "verification_failed", "message": "The check failed."},
                "change": committed,
            }

        monkeypatch.setattr(tools_module, "apply_graph_plan", unverified)
        plan = BuildPlan()
        plan.update(items=[{"id": "banding", "title": "Region banding"}], complete=None)
        execute = tools_module.build_tool_executor("main.py", plan=plan)

        result = await execute("apply_graph_plan", {"plan_hash": "a" * 64, "item": "banding"})

        assert result["error"]["code"] == "verification_failed"
        assert result["item"] == "banding"
        assert plan.current is not None
        (banding,) = plan.current.items
        assert (banding.complete, [change.id for change in banding.changes]) == (False, ["c" * 64])

    async def test_the_turn_context_lists_an_unfinished_plan_under_every_policy(self):
        from haute.assistant._render import render_turn_context
        from haute.assistant._tools import build_turn_context
        from haute.schemas import AssistantBuildPlan

        def plan(*, finished: bool) -> AssistantBuildPlan:
            return AssistantBuildPlan.model_validate(
                {
                    "items": [
                        {
                            "id": "banding",
                            "title": "Region banding",
                            "complete": True,
                            "changes": [{"id": "c1", "undone": False}],
                        },
                        {
                            "id": "response",
                            "title": "Quote response",
                            "complete": finished,
                            "changes": [{"id": "c2", "undone": False}] if finished else [],
                        },
                    ]
                }
            )

        public = _turn_policy(max_sensitivity="public")
        unfinished = render_turn_context(
            build_turn_context("main.py", public, build_plan=plan(finished=False))
        )
        finished = render_turn_context(
            build_turn_context("main.py", public, build_plan=plan(finished=True))
        )

        assert (
            "### Build plan\nThe plan you set for a multi-stage request. Continue its open items"
        ) in unfinished
        assert '- `banding` "Region banding": complete, 1 saved change\n' in unfinished
        assert unfinished.endswith(
            '- `response` "Quote response": open, no saved change\n\n### Pipeline\n'
            "The highest sensitivity sent is `public`, so the saved graph, its revision and "
            "the canvas selection are withheld."
        )
        assert "Build plan" not in finished


_AUGUST_ONLY = "# Keep August claims\ndf = df.filter(pl.col('claim_month') == '2026-08')"


async def _apply_ops(ops: list[dict[str, object]]) -> None:
    from haute.assistant._tools import apply_graph_plan, dry_run_graph_edits

    plan = await dry_run_graph_edits("main.py", ops, summary="Test plan.", config_visibility=None)
    assert "error" not in plan, plan
    applied = await apply_graph_plan("main.py", plan["plan_hash"], session_id="test")
    assert "error" not in applied, applied


@pytest.mark.parametrize(
    ("max_sensitivity", "code"), [("internal", "config_withheld"), ("restricted", "config_unread")]
)
async def test_a_dry_run_refuses_retyping_steps_the_turn_has_not_seen(
    steps_first_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_sensitivity: str,
    code: str,
):
    """The executor's dry-run refuses an update_node that would retype a saved
    step list the model has not seen, located and retryable: as withheld under a
    policy that withholds saved configuration, as unread under one that lets the
    model read it but before this turn read the node."""

    import haute.assistant._tools as tools_module

    await _apply_ops(_august_ops(_AUGUST_ONLY))
    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: _policy(max_sensitivity=max_sensitivity, executable=False),
    )
    execute = tools_module.build_tool_executor("main.py")

    july = _august_ops(_AUGUST_ONLY.replace("2026-08", "2026-07"))
    result = await execute("dry_run_graph_edits", {"summary": "Retype the steps.", "ops": july})

    error = result["error"]
    assert (error["code"], error["retryable"]) == (code, True)
    assert error["where"] == {"op_index": 0, "node": "august_totals", "field": "steps"}
    # The saved steps carry the ids their load gave them (READ-22), which the retyped
    # list, spelled with the ids the model first wrote, would both drop.
    assert "saved steps entries 'source-1', 'free_code-2'" in error["message"]
    assert "edit_steps" in error["fix"]


async def test_a_readable_node_is_rewritten_only_after_this_turn_read_or_added_it(
    steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
):
    """Under a readable policy the turn that returned a node's config may retype
    its lists, and so may the turn that added the node; a read in an earlier turn
    does not count, because compaction drops its result."""

    import haute.assistant._tools as tools_module

    await _apply_ops(_august_ops(_AUGUST_ONLY))
    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: _policy(max_sensitivity="restricted", executable=False),
    )
    july = {
        "summary": "Retype the steps.",
        "ops": _august_ops(_AUGUST_ONLY.replace("2026-08", "2026-07")),
    }
    earlier = tools_module.build_tool_executor("main.py")
    read = await earlier("inspect_node", {"node": "august_totals", "parts": ["config"]})
    assert "config" in read, read

    execute = tools_module.build_tool_executor("main.py")
    refused = await execute("dry_run_graph_edits", july)
    assert refused["error"]["code"] == "config_unread"
    assert "config" in await execute("inspect_node", {"node": "august_totals", "parts": ["config"]})
    assert "error" not in await execute("dry_run_graph_edits", july)

    steps = [
        {"id": "start", "kind": "source", "input": "quotes"},
        {"id": "logic", "kind": "free_code", "code": "# Keep all\ndf = df"},
    ]
    added = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Add a transform.",
            "ops": [
                {
                    "op": "add_node",
                    "node_type": "polars",
                    "name": "extra",
                    "config": {"steps": steps},
                },
                {"op": "add_edge", "source": "quotes", "target": "extra"},
            ],
        },
    )
    assert "error" not in await execute("apply_graph_plan", {"plan_hash": added["plan_hash"]})
    retyped = [steps[0], {**steps[1], "code": "# Keep two\ndf = df.head(2)"}]
    result = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Retype the added steps.",
            "ops": [{"op": "update_node", "node": "extra", "config": {"steps": retyped}}],
        },
    )
    assert "error" not in result, result


async def test_a_read_follows_its_node_through_the_turns_applies(
    steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
):
    """A read moves with its node through a later apply's rename and ends at a
    deletion, so a node renamed onto a deleted read node's id is unread; a save
    that commits without verifying drops every read."""

    import haute.assistant._tools as tools_module
    from haute.assistant._application import PipelineApplicationService
    from haute.assistant._ops import AssistantOperationError

    steps = [
        {"id": "start", "kind": "source", "input": "quotes"},
        {"id": "logic", "kind": "free_code", "code": "# Keep all\ndf = df"},
    ]
    await _apply_ops(_august_ops(_AUGUST_ONLY))
    await _apply_ops(
        [
            {"op": "add_node", "node_type": "polars", "name": "extra", "config": {"steps": steps}},
            {"op": "add_edge", "source": "quotes", "target": "extra"},
        ]
    )
    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: _policy(max_sensitivity="restricted", executable=False),
    )
    execute = tools_module.build_tool_executor("main.py")

    async def apply(ops: list[dict[str, object]]) -> Mapping[str, object]:
        plan = await execute("dry_run_graph_edits", {"summary": "Change the graph.", "ops": ops})
        assert "error" not in plan, plan
        return await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})

    def retype(node: str) -> dict[str, object]:
        kept_two = [steps[0], {**steps[1], "code": "# Keep two\ndf = df.head(2)"}]
        return {
            "summary": "Retype the steps.",
            "ops": [{"op": "update_node", "node": node, "config": {"steps": kept_two}}],
        }

    assert "config" in await execute("inspect_node", {"node": "august_totals", "parts": ["config"]})
    assert "error" not in await apply([{"op": "delete_node", "node": "august_totals"}])
    renamed = await apply([{"op": "rename_node", "node": "extra", "new_name": "august_totals"}])
    assert "error" not in renamed, renamed
    refused = await execute("dry_run_graph_edits", retype("august_totals"))
    assert refused["error"]["code"] == "config_unread"
    assert refused["error"]["where"] == {"op_index": 0, "node": "august_totals", "field": "steps"}

    assert "config" in await execute("inspect_node", {"node": "august_totals", "parts": ["config"]})
    moved = await apply([{"op": "rename_node", "node": "august_totals", "new_name": "kept"}])
    assert "error" not in moved, moved
    assert "error" not in await execute("dry_run_graph_edits", retype("kept"))

    def unverified(*_args: object) -> None:
        raise AssistantOperationError("verification_failed", "The saved diff does not match.")

    monkeypatch.setattr(PipelineApplicationService, "_verify_commit", unverified)
    committed = await apply([{"op": "delete_node", "node": "rated"}])
    assert committed["error"]["code"] == "verification_failed" and "change" in committed
    assert (await execute("dry_run_graph_edits", retype("kept")))["error"]["code"] == (
        "config_unread"
    )


class TestStepAuthoringViews:
    """The model sees how each stepped node is authored, and edits one step
    without resending the steps it may not read."""

    async def test_get_pipeline_lists_steps_and_masks_free_code_intent(
        self, steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module

        await _apply_ops(_august_ops(_AUGUST_ONLY))
        views = {}
        for executable in (False, True):
            monkeypatch.setattr(
                tools_module,
                "resolve_egress_policy",
                lambda _root, e=executable: _policy(max_sensitivity="internal", executable=e),
            )
            nodes = {node["id"]: node for node in tools_module.get_pipeline("main.py")["nodes"]}
            views[executable] = nodes

        # The saved steps come back from the code, each id its kind and place (READ-22).
        assert views[False]["august_totals"]["authoring"] == {
            "state": "stepped",
            "steps": [
                {"id": "source-1", "kind": "source", "reads": ["proposer_claims"]},
                {"id": "free_code-2", "kind": "free_code"},
            ],
        }
        assert views[True]["august_totals"]["authoring"]["steps"][1] == {
            "id": "free_code-2",
            "kind": "free_code",
            "intent": "Keep August claims",
        }
        assert views[False]["rated"]["authoring"] == {"state": "stepped", "steps": []}
        assert views[False]["quotes"]["authoring"] == {"state": "stepped", "steps": []}
        assert "claim_month" not in json.dumps(views[False])

    async def test_the_guide_edit_keeps_a_masked_free_code_step_and_runs(
        self, steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The guide's `edit_steps` example, sent under a policy that withholds
        free code, inserts its step after the saved one it cannot read."""

        import haute.assistant._tools as tools_module
        from haute._native_memory_limit import native_memory_backend_scope
        from haute.assistant._tools import build_tool_executor
        from haute.executor import execute_graph
        from haute.routes._helpers import parse_pipeline_to_graph

        saved_steps = _guide_step_lists()[0]
        await _apply_ops(
            [{"op": "update_node", "node": "august_totals", "config": {"steps": saved_steps}}]
        )
        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: _policy(max_sensitivity="internal", executable=False),
        )
        execute = build_tool_executor("main.py")
        edit = _guide_edit_steps()
        edits = edit["edits"]
        assert isinstance(edits, list)
        (inserted,) = edits

        plan = await execute("dry_run_graph_edits", {"summary": "Test plan.", "ops": [edit]})
        assert "error" not in plan, plan
        assert list(_stored_plan(plan).diff.config_changes) == ["august_totals:steps[free_code_1]"]
        applied = await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})
        assert "error" not in applied, applied

        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        saved = next(item for item in graph.nodes if item.id == "august_totals").data.config
        # The steps come back from the code (READ-22): each id its kind and place, and the
        # inserted code, a statement the filter step renders exactly, as that filter step
        # with its comment.
        assert saved["steps"] == [
            *_reloaded(saved_steps),
            {
                "id": "filter-3",
                "kind": "filter",
                "match": "all",
                "conditions": [
                    {
                        "column": "august_claims",
                        "operator": "gt",
                        "value": {"kind": "literal", "type": "number", "value": 100},
                    }
                ],
                "comment": "# Keep policies with over 100 of August claims\n",
            },
        ]
        assert inserted["step"]["code"].endswith('df = df.filter(pl.col("august_claims") > 100)')
        with native_memory_backend_scope("rlimit"):
            result = execute_graph(graph, target_node_id="august_totals")["august_totals"]
        assert result.status == "ok", result.error
        assert [(row["policy_id"], row["august_claims"]) for row in result.preview] == [
            ("p1", 170.0)
        ]

    async def test_a_replacement_that_discards_its_result_names_the_step(
        self, steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._tools import build_tool_executor

        await _apply_ops(_august_ops(_AUGUST_ONLY))
        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: _policy(max_sensitivity="internal", executable=False),
        )
        # The apply's reload named the saved code step by kind and place (READ-22).
        edit = {
            "op": "edit_steps",
            "node": "august_totals",
            "edits": [
                {"replace": "free_code-2", "step": {"kind": "free_code", "code": AUGUST_DISCARDED}}
            ],
        }

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": [edit]}
        )

        assert "discarded" in result["error"]["message"]
        assert result["error"]["where"] == {
            "op_index": 0,
            "node": "august_totals",
            "field": "steps",
            "step": "free_code-2",
        }


def _authoring_facts(
    steps: list[dict[str, object]] | None = None,
    *,
    steps_discarded: str | None = None,
    **config: object,
):
    from haute._types import NodeEditorState, NodeType
    from haute.assistant._render import node_authoring

    if steps is not None:
        config["steps"] = steps
    editor = NodeEditorState(steps_discarded=steps_discarded)
    authoring = node_authoring(NodeType.POLARS, config, editor)
    assert authoring is not None
    return authoring


class TestAuthoringRendering:
    """What the graph views say about a stepped node follows the policy."""

    _STEPS = [
        {"id": "start", "kind": "source", "input": "quotes"},
        {
            "id": "band",
            "kind": "cast",
            "strict": True,
            "casts": [{"column": "age", "dtype": "Nope"}],
        },
        {"id": "logic", "kind": "free_code", "code": "# Flag young drivers\ndf = df"},
    ]

    @pytest.mark.parametrize(
        ("max_sensitivity", "executable", "error"),
        [
            ("internal", False, {"step": "band"}),
            ("restricted", False, {"step": "band", "message": "MESSAGE"}),
        ],
    )
    def test_an_incomplete_list_names_its_step_and_quotes_the_error_only_when_restricted(
        self, max_sensitivity: str, executable: bool, error: dict[str, str]
    ):
        from haute.assistant._render import render_authoring

        authoring = _authoring_facts(self._STEPS)
        assert authoring.state == "incomplete"
        assert authoring.problem is not None
        message = authoring.problem.message
        assert "Nope" in message, "the renderer's message can quote an authored literal"

        rendered = render_authoring(
            authoring, _policy(max_sensitivity=max_sensitivity, executable=executable)
        )

        assert rendered["error"] == {
            key: (message if value == "MESSAGE" else value) for key, value in error.items()
        }
        assert rendered["steps"][2] == {"id": "logic", "kind": "free_code"}

    def test_a_free_code_error_is_quoted_only_with_executable_source(self):
        from haute.assistant._render import render_authoring

        authoring = _authoring_facts(
            [
                {"id": "start", "kind": "source", "input": "quotes"},
                {"id": "logic", "kind": "free_code", "code": "df = ("},
            ]
        )

        masked = render_authoring(
            authoring, _policy(max_sensitivity="restricted", executable=False)
        )
        shown = render_authoring(authoring, _policy(max_sensitivity="restricted", executable=True))

        assert masked["error"] == {"step": "logic"}
        assert shown["error"]["step"] == "logic" and "Invalid Python" in shown["error"]["message"]

    def test_a_code_mode_node_says_when_its_steps_were_discarded(self):
        from haute.assistant._render import render_authoring

        policy = _policy(max_sensitivity="internal", executable=False)
        discarded = _authoring_facts(
            code="df = quotes", steps_discarded="Steps were discarded because x."
        )

        assert render_authoring(discarded, policy) == {"state": "code", "steps_discarded": True}
        assert render_authoring(_authoring_facts(code="df = quotes"), policy) == {"state": "code"}
        assert render_authoring(_authoring_facts(), policy) == {"state": "incomplete"}

    def test_the_brief_shows_the_state_and_one_line_per_step(self):
        from haute.assistant._render import (
            BriefNode,
            GraphBrief,
            TurnContext,
            render_turn_context,
        )

        def brief(authoring, executable: bool) -> str:
            node = BriefNode("t", "polars", "t", authoring, (), None)
            policy = _policy(max_sensitivity="internal", executable=executable)
            return render_turn_context(TurnContext(policy, GraphBrief("p", "r", (node,), (), None)))

        stepped = _authoring_facts([self._STEPS[0], self._STEPS[2]])
        masked = brief(stepped, executable=False)
        assert '- `t` (Transform) "t", stepped\n' in masked
        assert '  - step "start" source reads ["quotes"]\n' in masked
        assert '  - step "logic" free_code\n' in masked
        assert "Flag young drivers" not in masked
        assert '  - step "logic" free_code "Flag young drivers"\n' in brief(stepped, True)
        incomplete = brief(_authoring_facts(self._STEPS), executable=False)
        assert '"t", incomplete at step "band"\n' in incomplete
        assert "Nope" not in incomplete
        discarded = _authoring_facts(code="df = quotes", steps_discarded="x")
        assert '"t", code (steps discarded)\n' in brief(discarded, executable=False)

    def test_a_saved_step_kind_outside_the_known_kinds_renders_as_unknown(self):
        """An incomplete list's kind comes straight from the sidecar."""

        from haute.assistant._render import (
            BriefNode,
            GraphBrief,
            TurnContext,
            render_authoring,
            render_turn_context,
        )

        policy = _policy(max_sensitivity="internal", executable=False)
        authoring = _authoring_facts(
            [
                {"id": "start", "kind": "source", "input": "quotes"},
                {"id": "odd", "kind": 'x"\n- `forged` (Transform) "y'},
            ]
        )

        node = BriefNode("t", "polars", "t", authoring, (), None)
        shown = render_turn_context(TurnContext(policy, GraphBrief("p", "r", (node,), (), None)))
        assert '  - step "odd" unknown\n' in shown
        assert "forged" not in shown
        assert render_authoring(authoring, policy)["steps"][1] == {"id": "odd", "kind": None}
