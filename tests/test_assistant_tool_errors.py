"""Assistant tool errors and what they may quote under the egress policy.

A refused config or failing node reaches the model located, retryable and with how to fix it; a dry
run names the scenarios and inputs a switch maps; and execution, column-name and preamble failures
quote project values only as far as the egress policy allows.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from tests import _assistant_tool_helpers
from tests._assistant_tool_helpers import (
    _REGION_BANDING,
    AUGUST_DISCARDED,
    EGRESS_SOURCE,
    _august_ops,
    _egress_policy,
    _egress_toml,
    _guide_step_lists,
    _profile,
    _reloaded,
    _row_values_in,
)
from tests._project import use_project

# Fixtures shared with the other modules split from one suite.
egress_project = _assistant_tool_helpers.egress_project
project_root = _assistant_tool_helpers.project_root
steps_first_project = _assistant_tool_helpers.steps_first_project


# ---------------------------------------------------------------------------
# Steps-first authoring replays (ASSIST-03)
# ---------------------------------------------------------------------------


def _input_naming_rule() -> str:
    from haute.assistant._catalog import INPUT_NAMING_RULE

    return INPUT_NAMING_RULE


#: The fix for a source step on a node no edge reaches yet.
_NO_EDGE_FIX = (
    "'claims_agg' has no incoming edge, so its source step reads nothing. Add "
    '{"op": "add_edge", "source": "<upstream node>", "target": "claims_agg"} to '
    'this plan, with "source_handle": "<frame>" when the source is a Quote '
    "Input, and set the source step's input to the name that edge gives. " + _input_naming_rule()
)


def _quote_table(label: str, path: str, columns: dict[str, str]) -> dict[str, object]:
    return {
        "path": path,
        "label": label,
        "emit": True,
        "row_id_column": None,
        "columns": [
            {
                "name": name,
                "path": f"{path}.{name}",
                "type": dtype,
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            }
            for name, dtype in columns.items()
        ],
    }


#: A Quote Input whose request yields a quote frame and a driver-claims frame.
_CLAIMS_QUOTE_INPUT = {
    "op": "add_node",
    "node_type": "apiInput",
    "name": "quote_input",
    "config": {
        "path": "quote.json",
        "tables": [
            _quote_table("quote", "$[:]", {"quote_id": "str"}),
            _quote_table(
                "additional_driver_claims",
                "$[:].claims[:]",
                {"quote_id": "str", "amount": "float"},
            ),
        ],
    },
}


#: The August failure: a Transform fed by two claim sources reads one of them
#: by indexing df, as if df held every input.
AUGUST_INDEXING = "df = df['proposer_claims'].filter(pl.col('claim_month') == '2026-08')"


AUGUST_TYPO = "df = df.filter(pl.col('claim_mnth') == '2026-08')"


CLAIM_COLUMNS = ["policy_id", "claim_month", "amount"]


RATING_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="rating fixture")


@pipeline.polars
def policies() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "driver_age": [19, 40],
            "region_code": ["NW", "SE"],
            "inception_date": ["2024-03-01", "2025-06-01"],
        }
    ).with_columns(pl.col("inception_date").str.to_date())
"""


_AGE_FACTOR = {
    "banding": "breakpoints",
    "column": "driver_age",
    "output_column": "age_band",
    "rules": [{"boundary": "24", "label": "young"}, {"boundary": "", "label": "older"}],
    "default": "unknown",
}


@pytest.fixture()
def rating_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A code-mode source of driver ages, region codes and inception dates."""

    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore

    use_project(tmp_path)
    use_project(tmp_path)  # restored by the autouse _restore_project_root
    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())
    monkeypatch.setattr(_tools, "mutations_readiness", lambda _root: (True, None))
    (tmp_path / "main.py").write_text(RATING_SOURCE, encoding="utf-8")
    _egress_toml(tmp_path, max_sensitivity="restricted")
    return tmp_path


def _bands(*factors: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "op": "add_node",
            "node_type": "banding",
            "name": "bands",
            "config": {"factors": list(factors)},
        },
        {"op": "add_edge", "source": "policies", "target": "bands"},
    ]


class TestConfigErrors:
    """A config a node's parser refuses reaches the model located and retryable,
    at the node that raised it and the operation that wrote it."""

    async def test_number_breakpoints_on_a_date_column_name_the_date_form(
        self, rating_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        dated = {
            "banding": "breakpoints",
            "column": "inception_date",
            "output_column": "inception_year",
            "rules": [{"boundary": "2024", "label": "2024"}, {"boundary": "", "label": "later"}],
            "default": "unknown",
        }
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits", {"summary": "Band years.", "ops": _bands(dated)}
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("schema_unresolvable", True)
        assert error["where"] == {"op_index": 0, "node": "bands", "field": "factors"}
        assert "has number breakpoints, but its column is Date" in error["message"]
        assert "2024-12-31" in error["fix"]

    async def test_a_rating_row_without_its_factor_is_a_located_config_error(
        self, rating_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        rates = {
            "op": "add_node",
            "node_type": "ratingStep",
            "name": "rates",
            "config": {
                "tables": [
                    {
                        "factors": ["age_band"],
                        "output_column": "age_factor",
                        "entries": [{"factor_values": ["young"], "value": 1.2}],
                        "default_value": 1.0,
                    }
                ]
            },
        }
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Rate ages.",
                "ops": [
                    *_bands(_AGE_FACTOR),
                    rates,
                    {"op": "add_edge", "source": "bands", "target": "rates"},
                ],
            },
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("invalid_config", True)
        assert error["where"] == {"op_index": 2, "node": "rates", "field": "tables"}
        assert "requires factor 'age_band'" in error["message"]
        assert '{"age_band": <value>, "value": <number>}' in error["fix"]

    async def test_a_schema_failure_names_the_node_that_raised_not_the_terminal(
        self, rating_project: Path
    ):
        """Emptying the bands leaves the rating step without its factor: the
        failure is located at the rating step the plan did not touch, while the
        message names the terminal whose validation reached it."""

        from haute.assistant._tools import build_tool_executor

        rates = {
            "op": "add_node",
            "node_type": "ratingStep",
            "name": "rates",
            "config": {
                "tables": [
                    {
                        "factors": ["age_band"],
                        "output_column": "age_factor",
                        "entries": [{"age_band": "young", "value": 1.2}],
                        "default_value": 1.0,
                    }
                ]
            },
        }
        priced = {
            "op": "add_node",
            "node_type": "polars",
            "name": "priced",
            "config": {
                "steps": [
                    {"id": "start", "kind": "source", "input": "rates"},
                    {"id": "logic", "kind": "free_code", "code": "# Keep\ndf = df"},
                ]
            },
        }
        execute = build_tool_executor("main.py")
        plan = await execute(
            "dry_run_graph_edits",
            {
                "summary": "Rate ages.",
                "ops": [
                    *_bands(_AGE_FACTOR),
                    rates,
                    {"op": "add_edge", "source": "bands", "target": "rates"},
                    priced,
                    {"op": "add_edge", "source": "rates", "target": "priced"},
                ],
            },
        )
        assert "error" not in plan, plan
        assert "error" not in await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})

        result = await execute(
            "dry_run_graph_edits",
            {
                "summary": "Empty the bands.",
                "ops": [{"op": "update_node", "node": "bands", "config": {"factors": []}}],
            },
        )

        error = result["error"]
        assert error["code"] == "schema_unresolvable"
        assert error["where"] == {"node": "rates"}
        assert "Schema validation of node 'priced' failed at node 'rates'" in error["message"]
        assert "needs the column 'age_band'" in error["message"]


async def test_categorical_rules_on_a_date_column_point_at_breakpoints(rating_project: Path):
    from haute.assistant._tools import build_tool_executor

    inception = {
        "banding": "categorical",
        "column": "inception_date",
        "output_column": "inception_band",
        "rules": [{"value": "2024-03-01", "assignment": "early"}],
        "default": "later",
    }
    result = await build_tool_executor("main.py")(
        "dry_run_graph_edits", {"summary": "Band inception.", "ops": _bands(inception)}
    )

    error = result["error"]
    assert (error["code"], error["retryable"]) == ("invalid_config", True)
    assert error["where"] == {"op_index": 0, "node": "bands", "field": "factors"}
    assert "'inception_date', a Date column, with categorical rules" in error["message"]
    assert 'banding: "breakpoints"' in error["fix"]


async def test_categorical_rules_on_an_integer_code_column_are_accepted(rating_project: Path):
    """Integer-coded factors (vehicle groups, NCD years) are mapped value by value."""

    from haute.assistant._tools import build_tool_executor

    ages = {
        "banding": "categorical",
        "column": "driver_age",
        "output_column": "age_band",
        "rules": [{"value": "19", "assignment": "young"}],
        "default": "older",
    }
    result = await build_tool_executor("main.py")(
        "dry_run_graph_edits", {"summary": "Band ages.", "ops": _bands(ages)}
    )

    assert "error" not in result, result


async def test_a_dry_run_validates_every_scenario_a_switch_maps(steps_first_project: Path):
    """Dropping the input the batch scenario routes to resolves under the live
    scenario, so only validating every mapped scenario catches it; the brief
    names the switch's scenarios, which are pipeline metadata."""

    from haute.assistant._render import render_turn_context
    from haute.assistant._tools import (
        build_tool_executor,
        build_turn_context,
        resolve_egress_policy,
    )

    _egress_toml(steps_first_project, max_sensitivity="internal")
    execute = build_tool_executor("main.py")
    switch = {
        "op": "add_node",
        "node_type": "liveSwitch",
        "name": "policies",
        "config": {
            "input_scenario_map": {"quotes": "live", "regions": "nb_batch"},
            "inputs": ["quotes", "regions"],
        },
    }
    kept = {
        "op": "add_node",
        "node_type": "polars",
        "name": "kept",
        "config": {
            "steps": [
                {"id": "start", "kind": "source", "input": "policies"},
                {"id": "logic", "kind": "free_code", "code": "# Keep\ndf = df"},
            ]
        },
    }
    plan = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Route quotes or regions.",
            "ops": [
                switch,
                {"op": "add_edge", "source": "quotes", "target": "policies"},
                {"op": "add_edge", "source": "regions", "target": "policies"},
                kept,
                {"op": "add_edge", "source": "policies", "target": "kept"},
            ],
        },
    )
    assert "error" not in plan, plan
    assert "error" not in await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})
    brief = render_turn_context(
        build_turn_context("main.py", resolve_egress_policy(steps_first_project))
    )
    assert '  - scenarios: ["live", "nb_batch"]' in brief

    dropped = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Drop the regions source.",
            "ops": [{"op": "delete_edge", "source": "regions", "target": "policies"}],
        },
    )

    error = dropped["error"]
    assert (error["code"], error["retryable"]) == ("scenario_unrouted", True)
    assert error["where"] == {"op_index": 0, "node": "policies", "field": "input_scenario_map"}
    assert "routes scenario 'nb_batch' to 'regions', which no incoming edge" in error["message"]

    premium = {"kind": "free_code", "code": "# Premium only\ndf = df.select('premium')"}
    narrowed = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Keep the premium.",
            "ops": [
                {
                    "op": "edit_steps",
                    "node": "kept",
                    # The apply's reload named the code step by kind and place (READ-22).
                    "edits": [{"replace": "free_code-2", "step": premium}],
                }
            ],
        },
    )

    error = narrowed["error"]
    assert error["code"] == "schema_unresolvable"
    # The column the edit reads is required of the switch, whose batch input lacks it.
    assert error["where"] == {"node": "policies"}
    assert (
        "Schema validation of node 'kept' failed at node 'policies' under scenario 'nb_batch'"
        in error["message"]
    )


async def test_an_unrouted_scenario_names_the_inputs_the_edges_provide(
    steps_first_project: Path,
):
    """The live run's renewal plan: an edge's target_handle does not name a Source
    Switch input, its source node does, so mapping the handle leaves the scenario
    unrouted. The error lists the inputs the edges provide, says how they are
    named, and gives the mapping that routes the scenario; that mapping passes."""

    from haute.assistant._tools import build_tool_executor

    _egress_toml(steps_first_project, max_sensitivity="internal")
    execute = build_tool_executor("main.py")
    saved = await execute(
        "dry_run_graph_edits",
        {
            "summary": "Route quotes or regions.",
            "ops": [
                {
                    "op": "add_node",
                    "node_type": "liveSwitch",
                    "name": "policies",
                    "config": {
                        "input_scenario_map": {"quotes": "live", "regions": "nb_batch"},
                        "inputs": ["quotes", "regions"],
                    },
                },
                {"op": "add_edge", "source": "quotes", "target": "policies"},
                {"op": "add_edge", "source": "regions", "target": "policies"},
            ],
        },
    )
    assert "error" not in await execute("apply_graph_plan", {"plan_hash": saved["plan_hash"]})

    def renewal_ops(name: str) -> list[dict[str, object]]:
        return [
            {
                "op": "add_node",
                "node_type": "dataInput",
                "name": "renewal_quotes",
                "ref": "renewal_quotes_node",
                "config": {"path": "quotes.parquet"},
            },
            {
                "op": "update_node",
                "node": "policies",
                "config": {
                    "input_scenario_map": {
                        "quotes": "live",
                        "regions": "nb_batch",
                        name: "renewal_batch",
                    },
                    "inputs": ["quotes", "regions", name],
                },
            },
            {
                "op": "add_edge",
                "source": "$renewal_quotes_node",
                "target": "policies",
                "target_handle": "renewal_batch_queue",
            },
        ]

    unrouted = await execute(
        "dry_run_graph_edits",
        {"summary": "Add the renewal batch.", "ops": renewal_ops("renewal_batch_queue")},
    )

    error = unrouted["error"]
    assert (error["code"], error["retryable"]) == ("scenario_unrouted", True)
    assert error["where"] == {"op_index": 1, "node": "policies", "field": "input_scenario_map"}
    assert error["message"] == (
        "Source Switch 'policies' routes scenario 'renewal_batch' to 'renewal_batch_queue', "
        "which no incoming edge provides, so under that scenario it would have no input. Its "
        "incoming edges provide 'quotes', 'regions', 'renewal_quotes'; add_edge's "
        f"target_handle never names an input. {_input_naming_rule()} The edge from "
        "'renewal_quotes' has target_handle 'renewal_batch_queue', which does not name an "
        "input; it provides 'renewal_quotes'."
    )
    assert error["fix"] == (
        "Map 'renewal_quotes' to 'renewal_batch' in input_scenario_map, and list "
        "'renewal_quotes' in inputs in place of 'renewal_batch_queue'."
    )

    routed = await execute(
        "dry_run_graph_edits",
        {"summary": "Add the renewal batch.", "ops": renewal_ops("renewal_quotes")},
    )

    assert "error" not in routed, routed


class TestActionableErrors:
    """A tool error says where it happened, what the node's inputs hold and how
    to fix it, through the executor the model calls."""

    async def test_the_august_two_input_failure_names_both_inputs_and_the_by_name_form(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        execute = build_tool_executor("main.py")

        result = await execute(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": _august_ops(AUGUST_INDEXING)}
        )

        error = result["error"]
        assert error["code"] == "invalid_ops"
        assert error["retryable"] is True
        # The plan holds a transform's steps as its reload will: the step the model called
        # 'logic' is the code step in second place (READ-22).
        assert error["where"] == {
            "op_index": 0,
            "node": "august_totals",
            "field": "steps",
            "step": "free_code-2",
        }
        assert error["context"] == {
            "inputs": {
                "proposer_claims": CLAIM_COLUMNS,
                "additional_drivers_claims": CLAIM_COLUMNS,
            }
        }
        assert "indexes df by the input name 'proposer_claims'" in error["message"]
        assert '{"id": "start", "kind": "source", "input": "<edge name>"}' in error["message"]
        assert error["fix"] == (
            "df is already the 'proposer_claims' frame the source step chose; read "
            "'additional_drivers_claims' by edge name (for example "
            "pl.concat([df, additional_drivers_claims]) or "
            "df.join(additional_drivers_claims, ...))."
        )

    async def test_a_public_policy_refuses_the_dry_run_before_naming_any_column(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="public")

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": _august_ops(AUGUST_INDEXING)}
        )

        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["retryable"] is False
        assert "claim_month" not in json.dumps(result)

    async def test_a_misspelt_column_suggests_the_input_column(self, steps_first_project: Path):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        execute = build_tool_executor("main.py")

        result = await execute(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": _august_ops(AUGUST_TYPO)}
        )

        error = result["error"]
        assert error["code"] == "schema_unresolvable"
        # A lazy plan fails when its schema resolves, outside any step's line,
        # so the location stops at the node and the operation that wrote it.
        assert error["where"] == {"op_index": 0, "node": "august_totals"}
        assert error["did_you_mean"] == ["claim_month"]
        assert error["fix"] == "Replace 'claim_mnth' with 'claim_month' in node 'august_totals'."
        assert error["context"]["inputs"]["proposer_claims"] == CLAIM_COLUMNS

    async def test_an_unknown_tool_names_its_close_match(self, steps_first_project: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")("dry_run_graph_edit", {})

        error = result["error"]
        assert error["code"] == "unknown_tool"
        assert error["did_you_mean"][0] == "dry_run_graph_edits"
        assert error["fix"] == "Call dry_run_graph_edits instead."
        assert error["retryable"] is True

    @pytest.mark.parametrize("name", ["ask", "ask_user", "clarify", "question"])
    async def test_a_tool_for_asking_the_analyst_says_how_to_ask(
        self, steps_first_project: Path, name: str
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(name, {})

        error = result["error"]
        assert error["code"] == "unknown_tool"
        assert error["fix"] == (
            "To ask the analyst, end your reply with a line starting NEEDS_INPUT:."
        )

    async def test_update_node_with_step_edits_points_at_edit_steps(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Add a step.",
                "ops": [
                    {
                        "op": "update_node",
                        "node": "august_totals",
                        "edits": [{"remove": "logic"}],
                    }
                ],
            },
        )

        error = result["error"]
        assert (error["code"], error["validation_reason"]) == ("invalid_request", "unknown_field")
        assert error["unknown_fields"] == ["edits"]
        assert '{"op": "edit_steps"' in error["fix"]

    async def test_a_response_output_after_a_node_the_plan_adds_reads_it_by_id(
        self, steps_first_project: Path
    ):
        """A response_output recipe whose source is a $ref saves the referenced
        node's frame name in its rows, never the ref."""

        from haute.assistant._tools import build_tool_executor
        from haute.routes._helpers import parse_pipeline_to_graph

        _egress_toml(steps_first_project, max_sensitivity="internal")
        execute = build_tool_executor("main.py")
        plan = await execute(
            "dry_run_graph_edits",
            {
                "summary": "Band regions and respond with them.",
                "ops": [
                    {**_REGION_BANDING, "ref": "bands"},
                    {
                        "op": "recipe",
                        "recipe": "response_output",
                        "arguments": {
                            "source": "$bands",
                            "output_name": "quote_response",
                            "output_columns": ["region_group"],
                        },
                    },
                ],
            },
        )
        assert "error" not in plan, plan
        applied = await execute("apply_graph_plan", {"plan_hash": plan["plan_hash"]})
        assert "error" not in applied, applied

        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        response = next(node for node in graph.nodes if node.id == "quote_response")
        assert [row["source_port"] for row in response.data.config["output_mapping"]] == [
            "region_band"
        ]

    async def test_an_output_row_reading_no_incoming_frame_is_located(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        row = {
            "source_port": "rated_quotes",
            "source_column": "premium",
            "output_path": "$[:].premium",
            "enabled": True,
        }
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Respond with the premium.",
                "ops": [
                    {
                        "op": "add_node",
                        "node_type": "output",
                        "name": "quote_response",
                        "config": {"output_mapping": [row]},
                    },
                    {"op": "add_edge", "source": "quotes", "target": "quote_response"},
                ],
            },
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("invalid_ops", True)
        assert error["where"] == {
            "op_index": 0,
            "node": "quote_response",
            "field": "output_mapping",
        }
        assert "'rated_quotes'" in error["message"] and "'quotes'" in error["message"]
        assert error["fix"] == (
            "The edges into 'quote_response' provide the input 'quotes'; set row 1's source_port "
            "to 'quotes'. " + _input_naming_rule()
        )
        assert error["did_you_mean"] == ["quotes"]

    @pytest.mark.parametrize(
        ("port", "handles", "fix", "did_you_mean"),
        [
            pytest.param(
                # An edge from a Quote Input is named by its frame, so naming the
                # node is answered with the frame, never a plain add_edge from it.
                "quote_input",
                ("additional_driver_claims",),
                "The edges into 'quote_response' provide the input 'additional_driver_claims'; "
                "set row 1's source_port to 'additional_driver_claims'. " + _input_naming_rule(),
                ["additional_driver_claims"],
                id="the-quote-input-node",
            ),
            pytest.param(
                "quote_input",
                (),
                "'quote_response' has no incoming edge, so row 1 reads nothing. Add "
                '{"op": "add_edge", "source": "<upstream node>", "target": "quote_response"} to '
                'this plan, with "source_handle": "<frame>" when the source is a Quote '
                "Input, and set row 1's source_port to the name that edge gives. "
                + _input_naming_rule(),
                None,
                id="the-quote-input-node-with-no-edge",
            ),
            pytest.param(
                "ad_driver_claims",
                ("quote", "additional_driver_claims"),
                "The edges into 'quote_response' provide the inputs 'quote', "
                "'additional_driver_claims'; set row 1's source_port to "
                "'additional_driver_claims' if that is the frame the column comes from. "
                + _input_naming_rule(),
                ["additional_driver_claims"],
                id="an-invented-frame-name",
            ),
        ],
    )
    async def test_an_output_row_reading_a_quote_input_frame_is_told_the_frame_name(
        self,
        steps_first_project: Path,
        port: str,
        handles: tuple[str, ...],
        fix: str,
        did_you_mean: list[str] | None,
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        row = {
            "source_port": port,
            "source_column": "quote_id",
            "output_path": "$[:].quote_id",
            "enabled": True,
        }
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Respond with the quote id.",
                "ops": [
                    _CLAIMS_QUOTE_INPUT,
                    {
                        "op": "add_node",
                        "node_type": "output",
                        "name": "quote_response",
                        "config": {"output_mapping": [row]},
                    },
                    *(
                        {
                            "op": "add_edge",
                            "source": "quote_input",
                            "target": "quote_response",
                            "source_handle": handle,
                        }
                        for handle in handles
                    ),
                ],
            },
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("invalid_ops", True)
        assert error["where"] == {
            "op_index": 1,
            "node": "quote_response",
            "field": "output_mapping",
        }
        assert error["fix"] == fix
        assert error.get("did_you_mean") == did_you_mean

    async def test_a_step_reading_an_unconnected_node_names_the_edge_to_add(
        self, steps_first_project: Path
    ):
        """The step reads a node that exists but is not wired in: the fix is the
        add_edge that connects it, not the step form again."""

        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        steps = [
            {"id": "start", "kind": "source", "input": "quotes"},
            {"id": "logic", "kind": "free_code", "code": "# Keep it\ndf = df"},
        ]
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Read the quotes.",
                "ops": [{"op": "update_node", "node": "august_totals", "config": {"steps": steps}}],
            },
        )

        error = result["error"]
        # The plan names a transform's steps as its reload will: kind and place (READ-22).
        assert error["where"] == {
            "op_index": 0,
            "node": "august_totals",
            "field": "steps",
            "step": "source-1",
        }
        assert error["fix"] == (
            "Connect 'quotes' to 'august_totals' in the same plan: {\"op\": \"add_edge\", "
            '"source": "quotes", "target": "august_totals"}.'
        )

    @pytest.mark.parametrize(
        ("read", "handles", "fix", "did_you_mean"),
        [
            pytest.param(
                "ad_driver_claims",
                (),
                _NO_EDGE_FIX,
                None,
                id="no-edge",
            ),
            pytest.param(
                # An edge from a Quote Input is named by its frame, never the
                # node, so the fix is not a plain add_edge from the node.
                "quote_input",
                (),
                _NO_EDGE_FIX,
                None,
                id="no-edge-reading-the-quote-input-node",
            ),
            pytest.param(
                "ad_driver_claims",
                ("additional_driver_claims",),
                "The edges into 'claims_agg' provide the input 'additional_driver_claims'; "
                "set the source step's input to 'additional_driver_claims'. "
                + _input_naming_rule(),
                ["additional_driver_claims"],
                id="an-edge-under-another-name",
            ),
            pytest.param(
                "ad_driver_claims",
                ("quote", "additional_driver_claims"),
                "The edges into 'claims_agg' provide the inputs 'quote', "
                "'additional_driver_claims'; set the source step's input to "
                "'additional_driver_claims' if that is the frame df starts from. "
                + _input_naming_rule(),
                ["additional_driver_claims"],
                id="two-edges-under-other-names",
            ),
        ],
    )
    async def test_a_source_step_reading_an_input_no_edge_gives_says_how_inputs_are_named(
        self,
        steps_first_project: Path,
        read: str,
        handles: tuple[str, ...],
        fix: str,
        did_you_mean: list[str] | None,
    ):
        """The live transcript: the model named the source step's input after
        the frame in its own words, first with no edge from the Quote Input and
        then with one, and needed a third dry run to learn the edge's name."""

        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        steps = [
            {"id": "start", "kind": "source", "input": read},
            {"id": "logic", "kind": "free_code", "code": "# Keep it\ndf = df"},
        ]
        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Aggregate the driver claims per quote.",
                "ops": [
                    _CLAIMS_QUOTE_INPUT,
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "claims_agg",
                        "config": {"steps": steps},
                    },
                    *(
                        {
                            "op": "add_edge",
                            "source": "quote_input",
                            "target": "claims_agg",
                            "source_handle": handle,
                        }
                        for handle in handles
                    ),
                ],
            },
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("invalid_ops", True)
        # The plan names a transform's steps as its reload will: kind and place (READ-22).
        assert error["where"] == {
            "op_index": 1,
            "node": "claims_agg",
            "field": "steps",
            "step": "source-1",
        }
        assert f"Unknown input {read!r}" in error["message"]
        assert error["fix"] == fix
        assert error.get("did_you_mean") == did_you_mean

    async def test_an_edge_to_a_node_added_later_in_the_plan_names_the_move(
        self, steps_first_project: Path
    ):
        """The live join failure: the model wired the join before adding it, and
        wrote a two-sentence summary the 160-character bound had refused."""

        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")
        summary = (
            "Attach the regional rates to each quote by joining the rates table onto "
            "the quotes on region, keeping every quote. Quotes whose region has no rate "
            "keep a null rate so the analyst can see which regions are missing."
        )
        assert 160 < len(summary) <= 400

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": summary,
                "ops": [
                    {"op": "add_edge", "source": "quotes", "target": "join_node"},
                    {"op": "add_node", "node_type": "polars", "name": "join_node", "ref": "j"},
                ],
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_ops"
        assert error["where"] == {"op_index": 0}
        assert error["message"].startswith("Unknown edge target node 'join_node'")
        assert error["fix"] == "Move add_node 'join_node' (operation 1) before operation 0."

    async def test_an_edge_to_a_node_no_operation_adds_suggests_close_ids(
        self, steps_first_project: Path
    ):
        from haute.assistant._tools import build_tool_executor

        _egress_toml(steps_first_project, max_sensitivity="internal")

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {"op": "add_node", "node_type": "polars", "name": "enriched", "ref": "e"},
                    {"op": "add_edge", "source": "quotes", "target": "$e"},
                    {"op": "add_edge", "source": "$e", "target": "august_total"},
                ],
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_ops"
        assert error["where"] == {"op_index": 2}
        assert "Nodes this plan adds so far: 'enriched' ($e)." in error["message"]
        assert error["did_you_mean"] == ["august_totals"]
        assert error["fix"].startswith("Use 'august_totals' if that is the node you meant")

    async def test_three_independent_errors_converge_in_one_turn(self, steps_first_project: Path):
        """A scripted replay through the real tools: three different faults,
        each corrected from its error, then the guide's form applies."""

        from haute.assistant._loop import run_turn
        from haute.assistant._providers import ProviderUsage, ToolCallRequest, TurnStop
        from haute.assistant._session import SessionStore
        from haute.assistant._tools import build_tool_executor
        from haute.routes._helpers import parse_pipeline_to_graph

        _egress_toml(steps_first_project, max_sensitivity="internal")
        good = _guide_step_lists()[0]
        attempts = [_august_ops(code) for code in (AUGUST_INDEXING, AUGUST_TYPO, AUGUST_DISCARDED)]
        attempts.append([{"op": "update_node", "node": "august_totals", "config": {"steps": good}}])
        results: list[dict] = []

        class Replay:
            def __init__(self) -> None:
                self.rounds = 0

            async def stream_turn(self, *, system, messages, tools):
                last = messages[-1]
                if last.get("role") == "tool":
                    results.append(last["content"])
                self.rounds += 1
                usage = ProviderUsage(input_tokens=1, output_tokens=1)
                if self.rounds <= len(attempts):
                    yield ToolCallRequest(
                        f"dry-{self.rounds}",
                        "dry_run_graph_edits",
                        {"summary": "Total August premium.", "ops": attempts[self.rounds - 1]},
                    )
                elif self.rounds == len(attempts) + 1:
                    yield ToolCallRequest(
                        "apply", "apply_graph_plan", {"plan_hash": last["content"]["plan_hash"]}
                    )
                else:
                    # The apply does not end the turn; the model closes it.
                    yield TurnStop("end", usage)
                    return
                yield TurnStop("tool_use", usage)

        store = SessionStore()
        session_id = store.create("main.py").id
        events = [
            event
            async for event in run_turn(
                store,
                session_id,
                "total both claim sources' August claims per policy in august_totals",
                provider=Replay(),
                tools=[],
                execute_tool=build_tool_executor("main.py"),
                system_prompt="s",
                turn_timeout=60.0,
                max_tool_calls=10,
            )
        ]

        assert [result["error"]["code"] for result in results[:3]] == [
            "invalid_ops",
            "schema_unresolvable",
            "invalid_ops",
        ]
        assert "error" not in results[3]
        assert events[-1].type == "completed"
        assert events[-1].outcome.kind == "applied"
        graph = parse_pipeline_to_graph(steps_first_project / "main.py")
        saved = next(node for node in graph.nodes if node.id == "august_totals")
        assert saved.data.config["steps"] == _reloaded(good)


def _free_code_node(source: str, code: str) -> list[dict[str, object]]:
    """Add one Transform that starts from *source* and runs *code* as free code."""

    return [
        {
            "op": "add_node",
            "node_type": "polars",
            "name": "probe",
            "ref": "probe",
            "config": {
                "steps": [
                    {"id": "start", "kind": "source", "input": source},
                    {"id": "logic", "kind": "free_code", "code": code},
                ]
            },
        },
        {"op": "add_edge", "source": source, "target": "$probe"},
    ]


_RAISE_WITH_ROWS = (
    "# Reject unexpected quotes\n"
    "rows = df.collect()\n"
    "raise ValueError(f\"bad {rows['quote_id'].to_list()} {rows['dob'].to_list()}\")\n"
)


class TestExecutionErrorEgress:
    """Resolving a schema runs node code over the project's rows, and that code
    or Polars itself can quote those rows in an exception."""

    async def test_node_code_exception_is_reduced_to_type_and_step(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", _RAISE_WITH_ROWS),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        message = result["error"]["message"]
        # The plan names the probe's code step as its reload will (READ-22).
        assert "ValueError in step 2 ('free_code-2') of node 'probe'" in message
        assert "allow_row_samples" in message

    async def test_csv_cast_failure_names_the_column_not_the_value(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("typed", "# Materialise\ndf = df.collect().lazy()"),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "ComputeError" in result["error"]["message"]
        assert "column(s) 'age'." in result["error"]["message"]

    def test_node_schema_withholds_a_strict_cast_value(self, egress_project: Path):
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "strict")

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "InvalidOperationError at line 1 of the node code" in result["error"]["message"]
        assert "column(s) 'age'." in result["error"]["message"]

    async def test_an_authored_polars_error_cannot_pass_a_value_off_as_a_column(
        self, egress_project: Path
    ):
        """Node code can raise a Polars error whose text embeds collected values
        in a `column '...'` phrase; only a name the code itself writes is named."""

        from haute.assistant._tools import dry_run_graph_edits

        code = (
            "# Reject the first quote\n"
            "rows = df.collect()\n"
            "raise pl.exceptions.ComputeError(f\"column '{rows['quote_id'][0]}' is bad\")\n"
        )
        result = await dry_run_graph_edits(
            "main.py", _free_code_node("quotes", code), summary="Test plan.", config_visibility=None
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        message = result["error"]["message"]
        # The plan names the probe's code step as its reload will (READ-22).
        assert "ComputeError in step 2 ('free_code-2') of node 'probe'" in message
        assert "; it names a column." in message

    async def test_a_missing_column_the_code_names_is_still_named(self, egress_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node(
                "quotes", '# Rename one column\ndf = df.rename({"missing_col": "renamed"})'
            ),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert "ColumnNotFoundError" in result["error"]["message"]
        assert "column(s) 'missing_col'." in result["error"]["message"]


# Saved code the model may not read without executable source, holding a
# literal equal to the malformed cell's `column '...'` phrase.
_MASKED_LITERAL_SOURCES = {
    "preamble": EGRESS_SOURCE.replace(
        'pipeline = haute.Pipeline("main", description="egress fixture")',
        '_BLOCKED_CUSTOMER = "secret-123"\n\n'
        'pipeline = haute.Pipeline("main", description="egress fixture")',
    ),
    "node": EGRESS_SOURCE
    + """

@pipeline.polars
def screened(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.filter(pl.col("quote_id") != "secret-123")
""",
}


_GHOST_SOURCE = (
    EGRESS_SOURCE
    + """

@pipeline.polars
def ghost(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.rename({"ghost_col": "renamed"})
"""
)


class TestFailureColumnNamesFollowTheEgressPolicy:
    """A column name in an authored-code failure is reported only when the
    egress policy already discloses it: permitted schema metadata, text the
    model itself submitted in the plan, or saved code when executable source
    is permitted. Appearing in masked code does not make a value a column."""

    @pytest.mark.parametrize("where", sorted(_MASKED_LITERAL_SOURCES))
    async def test_a_masked_literal_equal_to_a_cell_is_not_named(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch, where: str
    ):
        from haute.assistant._tools import dry_run_graph_edits

        source = _MASKED_LITERAL_SOURCES[where]
        assert source != EGRESS_SOURCE
        (egress_project / "main.py").write_text(source, encoding="utf-8")
        _egress_policy(monkeypatch, executable_source=False, row_samples=False)

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("typed", "# Materialise\ndf = df.collect().lazy()"),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        # The plan never names `age`; it is the failing node's input schema.
        assert "column(s) 'age'." in result["error"]["message"]

    def test_a_profile_names_the_column_of_the_frame_it_collected(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A profile's frame resolved schema-only and failed only when collected,
        so that frame's own schema is permitted metadata; the masked literal
        equal to the cell is still not a column."""

        (egress_project / "main.py").write_text(
            _MASKED_LITERAL_SOURCES["preamble"], encoding="utf-8"
        )
        # Profiles need row samples; the summary still follows the source policy.
        _egress_policy(monkeypatch, executable_source=False, row_samples=True)

        result = _profile("main.py", "typed")

        assert result["error"]["code"] == "profile_unavailable", result
        summary = result["error"]["message"].split(": ", 1)[0]
        assert summary.endswith("; it names column(s) 'age'"), summary

    def test_a_named_input_profile_failure_never_resolves_the_consumer(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Profiling one input of a node collects only that input's frame, so
        naming the failure's columns resolves that frame alone, never the
        consumer the profile did not ask for."""

        import haute.assistant._tools as tools_module

        (egress_project / "main.py").write_text(
            EGRESS_SOURCE
            + """

@pipeline.polars
def consumer(typed: pl.LazyFrame) -> pl.LazyFrame:
    return typed.with_columns(flag=pl.lit(1))
""",
            encoding="utf-8",
        )
        _egress_policy(monkeypatch, executable_source=False, row_samples=True)
        resolved_targets: list[str] = []
        original = tools_module._resolve_schema_outputs

        def recording(*args: object, target: str, **kwargs: object) -> object:
            resolved_targets.append(target)
            return original(*args, target=target, **kwargs)

        monkeypatch.setattr(tools_module, "_resolve_schema_outputs", recording)

        result = _profile("main.py", "consumer", "typed")

        assert result["error"]["code"] == "profile_unavailable", result
        summary = result["error"]["message"].split(": ", 1)[0]
        assert summary.endswith("; it names column(s) 'age'"), summary
        assert "typed" in resolved_targets
        assert "consumer" not in resolved_targets, resolved_targets

    def test_saved_code_names_a_column_only_with_executable_source(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import node_schema

        (egress_project / "main.py").write_text(_GHOST_SOURCE, encoding="utf-8")

        _egress_policy(monkeypatch, executable_source=False, row_samples=False)
        masked = node_schema("main.py", "ghost")
        _egress_policy(monkeypatch, executable_source=True, row_samples=False)
        readable = node_schema("main.py", "ghost")

        for result in (masked, readable):
            assert result["error"]["code"] == "schema_unresolvable", result
            assert "ColumnNotFoundError" in result["error"]["message"]
        assert "ghost_col" not in json.dumps(masked)
        assert "; it names a column." in masked["error"]["message"]
        assert "column(s) 'ghost_col'." in readable["error"]["message"]

    async def test_the_models_own_plan_text_names_a_column_with_source_masked(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import dry_run_graph_edits

        _egress_policy(monkeypatch, executable_source=False, row_samples=False)

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", '# Rename one column\ndf = df.rename({"absent_col": "x"})'),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "schema_unresolvable", result
        assert "column(s) 'absent_col'." in result["error"]["message"]


_PREAMBLE_EGRESS_SOURCE = EGRESS_SOURCE.replace(
    'pipeline = haute.Pipeline("main", description="egress fixture")',
    '_FIRST_QUOTE = int(pl.read_csv("data/quotes.csv")["quote_id"][0])\n\n'
    'pipeline = haute.Pipeline("main", description="egress fixture")',
)


def _allow_row_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=True,
            allow_aggregate_statistics=False,
        ),
    )


@pytest.fixture()
def broken_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The evaluation's broken pricing project: its feature step reads a column
    its input does not hold."""

    import shutil

    root = tmp_path / "b"
    shutil.copytree(Path(__file__).parent / "assistant_eval" / "projects" / "broken_pricing", root)
    # A Parquet scan reads its file directly, so no input snapshot is needed.
    pl.read_csv(root / "data" / "quotes.csv").write_parquet(root / "data" / "quotes.parquet")
    config = root / "config" / "data_input" / "quotes.json"
    config.write_text(
        json.dumps(
            {
                **json.loads(config.read_text(encoding="utf-8")),
                "format": "parquet",
                "path": "data/quotes.parquet",
            }
        ),
        encoding="utf-8",
    )
    use_project(root)
    use_project(root)  # restored by the autouse _restore_project_root
    _egress_toml(root, max_sensitivity="internal")
    return root


def test_a_node_whose_step_fails_names_the_step_and_its_inputs(broken_project: Path):
    """The schema part of a failing stepped node says which step raised and what
    each input holds, as an empty node does, while the missing column itself
    stays withheld."""

    from haute.assistant._tools import node_schema

    error = node_schema("pipeline.py", "rating_features")["error"]

    assert error["code"] == "schema_unresolvable"
    # The transform's steps come from its code, each id its kind and place (READ-22).
    assert error["step"] == "free_code-2"
    assert "in step 2 ('free_code-2') of node 'rating_features'" in error["message"]
    columns = {column["name"] for column in error["inputs"]["quotes"]}
    assert {"year_of_manufacture", "driver_age"} <= columns
    assert "vehicle_year" not in json.dumps(error)


class TestPreambleFailureEgress:
    """The preamble is authored code that can read project data at import, so its
    failure is reduced exactly as a node-code failure is."""

    @pytest.fixture()
    def preamble_project(self, egress_project: Path) -> Path:
        assert _PREAMBLE_EGRESS_SOURCE != EGRESS_SOURCE
        (egress_project / "main.py").write_text(_PREAMBLE_EGRESS_SOURCE, encoding="utf-8")
        return egress_project

    async def test_dry_run_withholds_the_preamble_failure_text(self, preamble_project: Path):
        from haute.assistant._tools import dry_run_graph_edits

        result = await dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", "df = df"),
            summary="Test plan.",
            config_visibility=None,
        )

        assert result["error"]["code"] == "preamble_failed", result
        assert _row_values_in(result) == []
        assert "PreambleError at line" in result["error"]["message"]
        assert "allow_row_samples" in result["error"]["message"]

    def test_node_schema_withholds_the_preamble_failure_text(self, preamble_project: Path):
        from haute.assistant._tools import node_schema

        result = node_schema("main.py", "quotes")

        assert result["error"]["code"] == "schema_unresolvable", result
        assert _row_values_in(result) == []
        assert "PreambleError at line" in result["error"]["message"]

    async def test_apply_withholds_a_preamble_failure(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Apply rebuilds the plan, so the preamble runs again there; a failure
        reaching the tool's generic handler is still reduced."""

        import haute.assistant._tools as tools_module
        from haute.errors import PreambleError

        class _FailingService:
            async def apply(self, _source_file: str, _plan_hash: str) -> object:
                raise PreambleError(
                    "Preamble line 5: invalid literal for int() with base 10: 'Q-7781'",
                    source_line=5,
                )

        monkeypatch.setattr(
            tools_module, "application_service", lambda **_kwargs: _FailingService()
        )

        result = await tools_module.apply_graph_plan("main.py", "0" * 64, session_id="test")

        assert _row_values_in(result) == []
        assert "PreambleError at line 5 of the preamble" in result["error"]["message"]

    async def test_permitted_row_samples_keep_the_preamble_text(
        self, preamble_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from haute.assistant._tools import dry_run_graph_edits, node_schema

        _allow_row_samples(monkeypatch)

        planned = await dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", "df = df"),
            summary="Test plan.",
            config_visibility=None,
        )
        schema = node_schema("main.py", "quotes")

        for result in (planned, schema):
            assert "PreambleError at line" in result["error"]["message"], result
            assert "Q-7781" in result["error"]["message"]

    async def test_permitted_row_samples_keep_the_error_text(
        self, egress_project: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="restricted",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=True,
                allow_aggregate_statistics=False,
            ),
        )

        result = await tools_module.dry_run_graph_edits(
            "main.py",
            _free_code_node("quotes", _RAISE_WITH_ROWS),
            summary="Test plan.",
            config_visibility=None,
        )

        # The plan names the probe's code step as its reload will (READ-22).
        assert "ValueError in step 2 ('free_code-2') of node 'probe'" in result["error"]["message"]
        assert "Q-7781" in result["error"]["message"]


async def test_saved_free_code_step_text_is_masked_without_executable_source(
    steps_first_project: Path, monkeypatch: pytest.MonkeyPatch
):
    """Structured steps are configuration and stay readable; the free-code
    card's text is executable source and follows the policy."""

    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    steps = _guide_step_lists()[0]
    assert [step["kind"] for step in steps] == ["source", "free_code"]
    ops = [{"op": "update_node", "node": "august_totals", "config": {"steps": steps}}]
    plan = await tools_module.dry_run_graph_edits(
        "main.py", ops, summary="Test plan.", config_visibility=None
    )
    applied = await tools_module.apply_graph_plan("main.py", plan["plan_hash"], session_id="test")
    assert "error" not in applied, applied
    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=False,
            allow_aggregate_statistics=False,
        ),
    )

    config = tools_module.node_config("main.py", "august_totals")["config"]

    # Saved, the steps come back from the code with ids from kind and place (READ-22).
    source, logic = _reloaded(steps)
    assert config["steps"] == [source, {**logic, "code": "<redacted: executable_source>"}]
    assert config["code"] == "<redacted: executable_source>"
