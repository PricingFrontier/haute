"""Deterministic assistant recipe contracts (ASSIST-A06)."""

from __future__ import annotations

from copy import deepcopy

import pytest

from haute._graph_utils import _sanitize_func_name
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.assistant._ops import (
    ProjectSnapshot,
    apply_ops,
    build_graph_edit_plan,
    parse_ops,
    verify_postconditions,
)

EXPECTED_RECIPES = {
    "categorical_banding",
    "reference_join",
    "response_output",
    "rating_step",
}
DOWNSTREAM_OUTPUT_RECIPES = EXPECTED_RECIPES - {"response_output"}


def _arguments(recipe_id: str) -> dict[str, object]:
    if recipe_id == "categorical_banding":
        return {
            "source": "quotes",
            "name": "Region band",
            "column": "region",
            "output_column": "region_group",
            "rules": [
                {"value": "north", "assignment": "core"},
                {"value": "south", "assignment": "core"},
            ],
            "default": "unknown",
        }
    if recipe_id == "reference_join":
        return {
            "base_source": "quotes",
            "reference_source": "regions",
            "name": "Attach region",
            "how": "left",
            "left_on": ["region"],
            "right_on": ["region"],
        }
    if recipe_id == "rating_step":
        return {
            "source": "banded",
            "name": "Apply rates",
            "tables": [
                {
                    "factors": ["driver_age_band"],
                    "output_column": "age_factor",
                    "entries": [
                        {"factor_values": ["young"], "value": 1.2},
                        {"factor_values": ["experienced"], "value": 1.0},
                    ],
                    "default_value": 1.0,
                }
            ],
            "combined_outputs": [
                {"output_column": "technical_premium", "operation": "multiply", "base_value": 100}
            ],
        }
    if recipe_id == "response_output":
        return {
            "source": "quotes",
            "output_name": "quote_response",
            "output_columns": ["quote_id", "region"],
        }
    raise AssertionError(recipe_id)


class TestRecipeRegistry:
    def test_descriptors_are_complete_closed_and_versioned(self):
        from haute.assistant._recipes import recipe_manifest

        manifest = recipe_manifest()
        assert {descriptor["id"] for descriptor in manifest} == EXPECTED_RECIPES
        expected_keys = {
            "id",
            "version",
            "summary",
            "use_cases",
            "argument_schema",
            "unresolved_decisions",
            "preconditions",
            "allowed_operations",
            "postconditions",
            "examples",
            "errors",
        }
        for descriptor in manifest:
            assert set(descriptor) == expected_keys
            assert descriptor["version"]
            assert descriptor["summary"]
            assert descriptor["argument_schema"]["additionalProperties"] is False
            assert descriptor["allowed_operations"]
            assert descriptor["postconditions"]
            assert descriptor["examples"]
            assert descriptor["errors"]

    def test_categorical_rule_schema_is_closed_and_self_describing(self):
        from haute.assistant._recipes import recipe_descriptor

        schema = recipe_descriptor("categorical_banding")["argument_schema"]
        properties = schema["properties"]
        rule = properties["rules"]["items"]

        assert "graph node name" in properties["name"]["description"]
        assert "output column" in properties["output_column"]["description"]
        assert properties["rules"]["description"]
        assert rule["additionalProperties"] is False
        assert set(rule["required"]) == {"value", "assignment"}
        assert set(rule["properties"]) == {"value", "assignment"}
        value = rule["properties"]["value"]
        assert value["type"] == "string"
        assert '"true"' in value["description"] and '"false"' in value["description"]
        assert "digits" in value["description"]

    def test_reference_join_never_advertises_a_cross_join(self):
        from haute.assistant._recipes import recipe_descriptor

        how = recipe_descriptor("reference_join")["argument_schema"]["properties"]["how"]
        assert tuple(how["enum"]) == ("inner", "left", "right", "full", "semi", "anti")

    def test_there_is_no_numeric_banding_recipe(self):
        from haute.assistant._recipes import RecipeError, recipe_descriptor

        with pytest.raises(RecipeError) as exc:
            recipe_descriptor("continuous_banding")
        assert exc.value.code == "unknown_recipe"

    def test_manifest_is_deeply_immutable(self):
        from haute.assistant._recipes import recipe_manifest

        manifest = recipe_manifest()
        with pytest.raises(TypeError):
            manifest[0]["summary"] = "changed"
        with pytest.raises(TypeError):
            manifest[0]["argument_schema"]["properties"]["source"] = {}


class TestRecipePlanning:
    @pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
    def test_planners_are_deterministic_and_emit_only_canonical_declared_ops(self, recipe_id):
        from haute.assistant._recipes import expand_recipe, recipe_descriptor

        arguments = _arguments(recipe_id)
        original = deepcopy(arguments)
        first = expand_recipe(recipe_id, arguments, ref="made")
        second = expand_recipe(recipe_id, deepcopy(arguments), ref="made")

        assert first == second
        assert arguments == original
        parsed = parse_ops(first)
        assert parsed
        allowed = set(recipe_descriptor(recipe_id)["allowed_operations"])
        assert {operation.op for operation in parsed} <= allowed
        # The node the recipe creates declares the ref it was given.
        assert [operation.ref for operation in parsed if operation.op == "add_node"][0] == "made"

    @pytest.mark.parametrize(
        "rules",
        [
            [{"label": "core", "value": "north"}],
            [{"value": "north", "assignment": "core", "extra": 1}],
            [{"value": None, "assignment": "core"}],
            [{"value": float("inf"), "assignment": "core"}],
            [{"value": 3, "assignment": "core"}],
            [{"value": 2.5, "assignment": "core"}],
            [{"value": "", "assignment": "core"}],
            [{"value": "north", "assignment": " "}],
            [{"value": "north", "assignment": "core"}, {"value": "north", "assignment": "x"}],
        ],
    )
    def test_invalid_categorical_rules_fail_inside_the_deterministic_planner(self, rules):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("categorical_banding")
        arguments["rules"] = rules
        with pytest.raises(RecipeError) as exc:
            expand_recipe("categorical_banding", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"
        assert exc.value.context["argument"].startswith("rules[")

    @pytest.mark.parametrize(
        ("value", "text_form"),
        [(True, '"true"'), (False, '"false"'), (3, '"3"')],
    )
    def test_non_string_rule_value_is_refused_naming_its_text_form(self, value, text_form):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("categorical_banding")
        arguments["rules"] = [{"value": value, "assignment": "core"}]
        with pytest.raises(RecipeError) as exc:
            expand_recipe("categorical_banding", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"
        assert exc.value.context["argument"] == "rules[0].value"
        assert text_form in str(exc.value)

    def test_rules_colliding_as_text_are_refused_at_planning(self):
        """A rule value is the text the runtime compares, so the digits "1" and
        an earlier "1" are one key; applying them would fail in the sidecar."""

        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("categorical_banding")
        arguments["rules"] = [
            {"value": "1", "assignment": "one"},
            {"value": "1", "assignment": "uno"},
        ]
        with pytest.raises(RecipeError) as exc:
            expand_recipe("categorical_banding", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"
        assert exc.value.context["argument"] == "rules[1].value"

    def test_cross_join_mode_is_refused(self):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("reference_join")
        arguments["how"] = "cross"
        with pytest.raises(RecipeError) as exc:
            expand_recipe("reference_join", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"

    def test_missing_material_decision_fails_by_stable_code(self):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("categorical_banding")
        del arguments["rules"]
        with pytest.raises(RecipeError) as exc:
            expand_recipe("categorical_banding", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"
        assert "rules" in str(exc.value)

    def test_unknown_recipe_fails_by_stable_code_and_lists_valid_ids(self):
        from haute.assistant._recipes import RecipeError, expand_recipe

        with pytest.raises(RecipeError) as exc:
            expand_recipe("unknown", {}, ref="made")
        assert exc.value.code == "unknown_recipe"
        assert EXPECTED_RECIPES <= set(exc.value.context["valid_ids"])

    def test_unknown_argument_is_rejected_instead_of_ignored(self):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("reference_join")
        arguments["silent_fallback"] = True
        with pytest.raises(RecipeError) as exc:
            expand_recipe("reference_join", arguments, ref="made")
        assert exc.value.code == "recipe_argument_invalid"

    def test_rating_recipe_converts_closed_positional_rows_to_canonical_config(self):
        from haute.assistant._recipes import expand_recipe, recipe_descriptor

        descriptor = recipe_descriptor("rating_step")
        schema = descriptor["argument_schema"]["properties"]
        table_schema = schema["tables"]["items"]
        entry_schema = table_schema["properties"]["entries"]["items"]
        combined_schema = schema["combined_outputs"]["items"]
        assert set(table_schema["properties"]) == {
            "factors",
            "output_column",
            "entries",
            "default_value",
        }
        assert set(entry_schema["properties"]) == {"factor_values", "value"}
        assert set(combined_schema["properties"]) == {"output_column", "operation", "base_value"}

        recipe = expand_recipe("rating_step", _arguments("rating_step"), ref="made")
        config = recipe[0]["config"]
        assert config == {
            "tables": [
                {
                    "factors": ["driver_age_band"],
                    "outputColumn": "age_factor",
                    "entries": [
                        {"driver_age_band": "young", "value": 1.2},
                        {"driver_age_band": "experienced", "value": 1.0},
                    ],
                    "defaultValue": 1.0,
                }
            ],
            "combinedOutputs": [
                {
                    "outputColumn": "technical_premium",
                    "operation": "multiply",
                    "baseValue": 100.0,
                }
            ],
        }

    def test_rating_recipe_rejects_misaligned_factor_values(self):
        from haute.assistant._recipes import RecipeError, expand_recipe

        arguments = _arguments("rating_step")
        arguments["tables"][0]["entries"][0]["factor_values"] = []
        with pytest.raises(RecipeError) as exc_info:
            expand_recipe("rating_step", arguments, ref="made")
        assert exc_info.value.code == "recipe_argument_invalid"
        assert exc_info.value.context["argument"] == "tables[0].entries[0].factor_values"

    @pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
    def test_an_expansion_satisfies_its_plan_s_automatic_postconditions(self, recipe_id: str):
        """A recipe declares no postconditions: its nodes and edges are proved by the
        plan's own automatic ones, like any other operation's."""

        from haute.assistant._recipes import expand_recipe

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id=node_id,
                    data=NodeData(label=node_id, nodeType=NodeType.POLARS, config={}),
                )
                for node_id in ("quotes", "regions", "banded")
            ],
            edges=[],
        )
        recipe = expand_recipe(recipe_id, _arguments(recipe_id), ref="made")
        snapshot = ProjectSnapshot(
            revision="base-revision",
            capability_hash="capability-hash",
            graph=graph,
            source_manifest=(),
        )
        plan = build_graph_edit_plan(snapshot, recipe)

        assert "$made" not in repr(plan.postconditions)
        assert {"node_exists", "edge_exists"} <= {
            condition["kind"] for condition in plan.postconditions
        }
        result = apply_ops(graph, parse_ops(recipe))
        assert all(item["passed"] for item in verify_postconditions(result, plan.postconditions))


def test_categorical_banding_recipe_emits_canonical_rules() -> None:
    from haute.assistant._recipes import expand_recipe

    recipe = expand_recipe("categorical_banding", _arguments("categorical_banding"), ref="made")

    assert recipe[0]["config"] == {
        "factors": [
            {
                "banding": "categorical",
                "column": "region",
                "outputColumn": "region_group",
                "rules": [
                    {"value": "north", "assignment": "core"},
                    {"value": "south", "assignment": "core"},
                ],
                "default": "unknown",
            }
        ]
    }


def test_true_false_rules_from_the_recipe_match_every_boolean_row() -> None:
    import polars as pl

    from haute._rating import apply_banding_from_config
    from haute.assistant._recipes import expand_recipe

    arguments = _arguments("categorical_banding")
    arguments["column"] = "has_claims"
    arguments["rules"] = [
        {"value": "true", "assignment": "claimed"},
        {"value": "false", "assignment": "clean"},
    ]
    config = expand_recipe("categorical_banding", arguments, ref="made")[0]["config"]

    banded = apply_banding_from_config(
        pl.LazyFrame({"has_claims": [True, False, True]}), config
    ).collect()

    assert banded["region_group"].to_list() == ["claimed", "clean", "claimed"]


def test_response_output_recipe_emits_canonical_mapping_and_edge() -> None:
    from haute.assistant._recipes import expand_recipe

    recipe = expand_recipe("response_output", _arguments("response_output"), ref="made")

    assert recipe == [
        {
            "op": "add_node",
            "node_type": "output",
            "name": "quote_response",
            "ref": "made",
            "config": {
                "outputMapping": [
                    {
                        "source_port": "quotes",
                        "source_column": column,
                        "output_path": f"$[:].{column}",
                        "enabled": True,
                    }
                    for column in ("quote_id", "region")
                ],
                "outputFormat": "json",
            },
        },
        {
            "op": "add_edge",
            "source": "quotes",
            "target": "$made",
        },
    ]


def test_reference_join_recipe_emits_explicit_base_and_join_handles() -> None:
    from haute.assistant._recipes import expand_recipe

    recipe = expand_recipe("reference_join", _arguments("reference_join"), ref="made")
    incoming = [operation for operation in recipe if operation["op"] == "add_edge"]

    assert [(edge["source"], edge["target_handle"]) for edge in incoming] == [
        ("quotes", "base"),
        ("regions", "join"),
    ]


@pytest.mark.parametrize("recipe_id", sorted(DOWNSTREAM_OUTPUT_RECIPES))
def test_recipe_can_own_one_connected_response_output(recipe_id: str) -> None:
    from haute.assistant._recipes import expand_recipe

    arguments = _arguments(recipe_id)
    arguments["output_name"] = "response"
    output_column = {
        "categorical_banding": "region_group",
        "reference_join": "region",
        "rating_step": "technical_premium",
    }[recipe_id]
    arguments["output_columns"] = [output_column]
    recipe = expand_recipe(recipe_id, arguments, ref="made")

    output_nodes = [
        operation
        for operation in recipe
        if operation["op"] == "add_node" and operation["node_type"] == "output"
    ]
    assert output_nodes == [
        {
            "op": "add_node",
            "node_type": "output",
            "name": "response",
            "ref": "made_output",
            "config": {
                "outputMapping": [
                    {
                        "source_port": _sanitize_func_name(str(arguments["name"])),
                        "source_column": output_column,
                        "output_path": f"$[:].{output_column}",
                        "enabled": True,
                    }
                ],
                "outputFormat": "json",
            },
        }
    ]
    assert recipe[-1] == {"op": "add_edge", "source": "$made", "target": "$made_output"}


def test_a_recipe_output_reads_the_created_node_by_its_id() -> None:
    """The created node's id is its sanitised name, and so is the frame name its
    response output's rows read."""

    from haute.assistant._recipes import expand_recipe

    arguments = _arguments("categorical_banding")
    arguments.update(name="Region Band", output_name="response", output_columns=["region_group"])

    recipe = expand_recipe("categorical_banding", arguments, ref="made")

    output = next(op for op in recipe if op["op"] == "add_node" and op["node_type"] == "output")
    assert output["config"]["outputMapping"][0]["source_port"] == "Region_Band"


@pytest.mark.parametrize(
    "partial_output",
    [{"output_name": "response"}, {"output_columns": ["premium"]}],
)
def test_recipe_rejects_partial_output_mapping(partial_output: dict[str, object]) -> None:
    from haute.assistant._recipes import RecipeError, expand_recipe

    with pytest.raises(RecipeError):
        expand_recipe(
            "reference_join", {**_arguments("reference_join"), **partial_output}, ref="made"
        )


def test_a_batch_expands_each_recipe_in_place_with_its_own_refs() -> None:
    """Two recipes and a primitive operation keep their order; each recipe's node
    declares its operation's ref, or recipe_<index> without one, and every expanded
    operation records the index of the operation it came from."""

    from haute.assistant._recipes import expand_recipe_operations

    primitive = {"op": "add_edge", "source": "$band", "target": "premium"}
    batch = expand_recipe_operations(
        [
            {
                "op": "recipe",
                "recipe": "categorical_banding",
                "arguments": _arguments("categorical_banding"),
                "ref": "band",
            },
            primitive,
            {
                "op": "recipe",
                "recipe": "response_output",
                "arguments": _arguments("response_output"),
            },
        ]
    )

    assert [operation["op"] for operation in batch.operations] == [
        "add_node",
        "add_edge",
        "add_edge",
        "add_node",
        "add_edge",
    ]
    assert batch.operations[0]["ref"] == "band"
    assert batch.operations[2] is primitive
    assert batch.operations[3]["ref"] == "recipe_2"
    assert batch.positions == (0, 0, 1, 2, 2)
    assert dict(batch.recipes) == {0: "categorical_banding", 2: "response_output"}
    parse_ops(batch.operations)


def test_a_recipe_failure_names_the_recipe_operation_that_raised_it() -> None:
    from haute.assistant._recipes import RecipeOperationError, expand_recipe_operations

    arguments = _arguments("categorical_banding")
    arguments["rules"] = [{"value": True, "assignment": "core"}]
    with pytest.raises(RecipeOperationError) as exc_info:
        expand_recipe_operations(
            [
                {"op": "delete_node", "node": "old"},
                {"op": "recipe", "recipe": "categorical_banding", "arguments": arguments},
            ]
        )

    assert (exc_info.value.op_index, exc_info.value.recipe_id) == (1, "categorical_banding")
    assert exc_info.value.code == "recipe_argument_invalid"
    assert exc_info.value.context["argument"] == "rules[0].value"
