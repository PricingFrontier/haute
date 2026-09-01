"""Deterministic assistant recipe contracts (ASSIST-A06)."""

from __future__ import annotations

import re
from copy import deepcopy

import pytest

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
    "continuous_banding",
    "parquet_showcase",
    "reference_join",
    "response_output",
    "rating_step",
}
DOWNSTREAM_OUTPUT_RECIPES = EXPECTED_RECIPES - {
    "parquet_showcase",
    "response_output",
}


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Band driver_age into an age band.", None),
        ("Add continuous banding for driver_age.", "continuous_banding"),
        ("Band driver_age as young when <= 25 and older when > 25.", "continuous_banding"),
        ("Bucket vehicle values into ranges.", "continuous_banding"),
        ("Add a LEFT JOIN to the lookup.", "reference_join"),
        ("Build a rating-step node.", "rating_step"),
        ("Join the lookup and then band the result.", None),
        ("Add rating factors without values.", None),
        ("Transform two columns with Polars.", None),
        ("Add categorical banding for region.", "categorical_banding"),
        ("Add a response output for quote_id.", "response_output"),
        (
            "Build a pipeline with the parquets and use many node types.",
            "parquet_showcase",
        ),
        (
            (
                "can you make a pipeline with the parquets in the data folder. "
                "use as many nodee types as you can"
            ),
            "parquet_showcase",
        ),
        (
            "Add a rating step and then a response output.",
            "rating_step",
        ),
        ("Add a bandwidth field.", None),
    ],
)
def test_explicit_request_recipe_routing_is_conservative(prompt: str, expected: str | None) -> None:
    from haute.assistant._recipes import route_recipe_request

    assert route_recipe_request(prompt) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "Explain how joins work in this pipeline",
        "What does joining two tables do?",
        "How is banding of continuous ranges handled?",
        "Describe the rating step for this pipeline",
    ],
)
def test_explanation_only_requests_never_route_to_a_recipe(prompt: str) -> None:
    from haute.assistant._recipes import route_recipe_request

    assert route_recipe_request(prompt) is None


def test_explanation_followed_by_explicit_authoring_still_routes_to_a_recipe() -> None:
    from haute.assistant._recipes import route_recipe_request

    assert (
        route_recipe_request("Explain how joins work, then add a left join to the lookup.")
        == "reference_join"
    )


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Add continuous banding for driver_age.", "continuous_banding"),
        ("Add a LEFT JOIN to the lookup.", "reference_join"),
        ("Build a rating-step node.", "rating_step"),
        ("Add categorical banding for region.", "categorical_banding"),
        ("Add a response output for quote_id.", "response_output"),
        (
            "Build a pipeline with the parquets and use many node types.",
            "parquet_showcase",
        ),
    ],
)
def test_imperative_requests_still_route_past_the_explanation_guard(
    prompt: str, expected: str
) -> None:
    from haute.assistant._recipes import route_recipe_request

    assert route_recipe_request(prompt) == expected


def test_explicitly_withheld_rating_material_requires_clarification() -> None:
    from haute.assistant._recipes import request_requires_material_clarification

    assert request_requires_material_clarification(
        "Add rating factors, but do not supply missing-factor policy or factor values."
    )
    assert request_requires_material_clarification("Add rating factors without factor values.")
    assert not request_requires_material_clarification(
        "Add a rating step with region north = 1.1 and default 1.0."
    )


def test_explanation_phrased_material_question_is_not_forced_to_clarify() -> None:
    from haute.assistant._recipes import request_requires_material_clarification

    assert not request_requires_material_clarification(
        "Explain what happens to rating factors when I do not supply factor values"
    )
    assert request_requires_material_clarification(
        "Add rating factors, but do not supply missing-factor policy or factor values."
    )
    assert request_requires_material_clarification("Add rating factors without factor values.")


def _arguments(recipe_id: str) -> dict[str, object]:
    if recipe_id == "continuous_banding":
        return {
            "source": "quotes",
            "name": "Age band",
            "column": "driver_age",
            "output_column": "driver_age_band",
            "rules": [
                {"op1": "<=", "val1": 25, "assignment": "young"},
                {"op1": ">", "val1": 25, "assignment": "experienced"},
            ],
            "default": "unknown",
        }
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
    if recipe_id == "parquet_showcase":
        return {
            "base": {"path": "data/nb_batch.parquet", "name": "nb_batch"},
            "reference": {
                "path": "data/competitor_insight.parquet",
                "name": "competitor_insight",
            },
            "join_name": "quote_with_competitor",
            "join_key": "quote_id",
            "transform_name": "quote_features",
            "output_name": "showcase_response",
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

    def test_continuous_rule_schema_is_closed_and_self_describing(self):
        from haute.assistant._recipes import recipe_descriptor

        schema = recipe_descriptor("continuous_banding")["argument_schema"]
        properties = schema["properties"]
        rule = properties["rules"]["items"]

        assert "graph node name" in properties["name"]["description"]
        assert "output column" in properties["output_column"]["description"]
        assert properties["rules"]["description"]
        assert rule["additionalProperties"] is False
        assert set(rule["required"]) == {"op1", "val1", "assignment"}
        assert list(rule["properties"]["op1"]["enum"]) == ["<", "<=", ">", ">=", "=", "=="]
        assert rule["properties"]["val1"]["type"] == "number"

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
        from haute.assistant._recipes import plan_recipe, recipe_descriptor

        arguments = _arguments(recipe_id)
        original = deepcopy(arguments)
        first = plan_recipe(recipe_id, arguments)
        second = plan_recipe(recipe_id, deepcopy(arguments))

        assert first == second
        assert arguments == original
        assert re.fullmatch(r"[0-9a-f]{64}", first["recipe_plan_hash"])
        parsed = parse_ops(first["operations"])
        assert parsed
        allowed = set(recipe_descriptor(recipe_id)["allowed_operations"])
        assert {operation.op for operation in parsed} <= allowed
        assert first["postconditions"]
        edge_operations = [
            operation for operation in first["operations"] if operation["op"] == "add_edge"
        ]
        edge_postconditions = [
            condition for condition in first["postconditions"] if condition["kind"] == "edge_exists"
        ]
        assert len(edge_postconditions) == len(edge_operations)

    @pytest.mark.parametrize(
        "rules",
        [
            [{"label": "young", "value": 25}],
            [{"op1": "<=", "val1": 25, "assignment": "young", "op2": ">"}],
            [{"op1": "approximately", "val1": 25, "assignment": "young"}],
            [{"op1": "<=", "val1": float("inf"), "assignment": "young"}],
        ],
    )
    def test_invalid_continuous_rules_fail_inside_the_deterministic_planner(self, rules):
        from haute.assistant._recipes import RecipeError, plan_recipe

        arguments = _arguments("continuous_banding")
        arguments["rules"] = rules
        with pytest.raises(RecipeError) as exc:
            plan_recipe("continuous_banding", arguments)
        assert exc.value.code == "recipe_argument_invalid"
        assert exc.value.context["argument"].startswith("rules[")

    def test_missing_material_decision_fails_by_stable_code(self):
        from haute.assistant._recipes import RecipeError, plan_recipe

        arguments = _arguments("continuous_banding")
        del arguments["rules"]
        with pytest.raises(RecipeError) as exc:
            plan_recipe("continuous_banding", arguments)
        assert exc.value.code == "recipe_argument_invalid"
        assert "rules" in str(exc.value)

    def test_unknown_recipe_fails_by_stable_code_and_lists_valid_ids(self):
        from haute.assistant._recipes import RecipeError, plan_recipe

        with pytest.raises(RecipeError) as exc:
            plan_recipe("unknown", {})
        assert exc.value.code == "unknown_recipe"
        assert EXPECTED_RECIPES <= set(exc.value.context["valid_ids"])

    def test_unknown_argument_is_rejected_instead_of_ignored(self):
        from haute.assistant._recipes import RecipeError, plan_recipe

        arguments = _arguments("reference_join")
        arguments["silent_fallback"] = True
        with pytest.raises(RecipeError) as exc:
            plan_recipe("reference_join", arguments)
        assert exc.value.code == "recipe_argument_invalid"

    def test_rating_recipe_converts_closed_positional_rows_to_canonical_config(self):
        from haute.assistant._recipes import plan_recipe, recipe_descriptor

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

        recipe = plan_recipe("rating_step", _arguments("rating_step"))
        config = recipe["operations"][0]["config"]
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
        from haute.assistant._recipes import RecipeError, plan_recipe

        arguments = _arguments("rating_step")
        arguments["tables"][0]["entries"][0]["factor_values"] = []
        with pytest.raises(RecipeError) as exc_info:
            plan_recipe("rating_step", arguments)
        assert exc_info.value.code == "recipe_argument_invalid"
        assert exc_info.value.context["argument"] == "tables[0].entries[0].factor_values"

    @pytest.mark.parametrize("recipe_id", sorted(EXPECTED_RECIPES))
    def test_recipe_postcondition_refs_resolve_through_the_canonical_plan(self, recipe_id: str):
        from haute.assistant._recipes import plan_recipe

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
        recipe = plan_recipe(recipe_id, _arguments(recipe_id))
        snapshot = ProjectSnapshot(
            revision="base-revision",
            capability_hash="capability-hash",
            graph=graph,
            source_manifest=(),
        )
        plan = build_graph_edit_plan(
            snapshot,
            recipe["operations"],
            recipe["postconditions"],
        )

        assert "$recipe_" not in repr(plan.postconditions)
        result = apply_ops(graph, parse_ops(recipe["operations"]))
        assert all(item["passed"] for item in verify_postconditions(result, plan.postconditions))


def test_categorical_banding_recipe_emits_canonical_rules() -> None:
    from haute.assistant._recipes import plan_recipe

    recipe = plan_recipe("categorical_banding", _arguments("categorical_banding"))

    assert recipe["operations"][0]["config"] == {
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


def test_response_output_recipe_emits_canonical_mapping_and_edge() -> None:
    from haute.assistant._recipes import plan_recipe

    recipe = plan_recipe("response_output", _arguments("response_output"))

    assert recipe["operations"] == [
        {
            "op": "add_node",
            "node_type": "output",
            "name": "quote_response",
            "ref": "recipe_output",
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
            "target": "$recipe_output",
        },
    ]


def test_parquet_showcase_recipe_emits_one_connected_coherent_graph() -> None:
    from haute.assistant._recipes import plan_recipe

    recipe = plan_recipe("parquet_showcase", _arguments("parquet_showcase"))
    nodes = [
        operation["node_type"]
        for operation in recipe["operations"]
        if operation["op"] == "add_node"
    ]
    edges = [
        (
            operation["source"],
            operation["target"],
            operation.get("target_handle"),
        )
        for operation in recipe["operations"]
        if operation["op"] == "add_edge"
    ]

    assert nodes == ["dataInput", "dataInput", "edgeJoin", "polars", "output"]
    assert edges == [
        ("$recipe_showcase_base", "$recipe_showcase_join", "base"),
        ("$recipe_showcase_reference", "$recipe_showcase_join", "join"),
        ("$recipe_showcase_join", "$recipe_showcase_transform", None),
        ("$recipe_showcase_transform", "$recipe_output", None),
    ]
    transform = next(
        operation for operation in recipe["operations"] if operation.get("node_type") == "polars"
    )
    assert transform["config"]["code"] == (
        "df = quote_with_competitor.with_columns(\n"
        '    pl.col("quote_id").cast(pl.String).alias("quote_id_text"),\n'
        '    pl.lit("haute_showcase").alias("showcase_stage"),\n'
        ")"
    )
    output = next(
        operation for operation in recipe["operations"] if operation.get("node_type") == "output"
    )
    assert [
        (mapping["source_column"], mapping["output_path"])
        for mapping in output["config"]["outputMapping"]
    ] == [
        ("quote_id", "$[:].quote_id"),
        ("quote_id_text", "$[:].quote_id_text"),
        ("showcase_stage", "$[:].showcase_stage"),
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.parquet",
        "C:/outside.parquet",
        "data\\outside.parquet",
        "data/not_parquet.csv",
    ],
)
def test_parquet_showcase_rejects_unsafe_or_non_parquet_paths(path: str) -> None:
    from haute.assistant._recipes import RecipeError, plan_recipe

    arguments = _arguments("parquet_showcase")
    arguments["base"]["path"] = path

    with pytest.raises(RecipeError) as exc_info:
        plan_recipe("parquet_showcase", arguments)

    assert exc_info.value.code == "recipe_argument_invalid"
    assert exc_info.value.context["argument"] == "base.path"


def test_reference_join_recipe_emits_explicit_base_and_join_handles() -> None:
    from haute.assistant._recipes import plan_recipe

    recipe = plan_recipe("reference_join", _arguments("reference_join"))
    incoming = [operation for operation in recipe["operations"] if operation["op"] == "add_edge"]

    assert [(edge["source"], edge["target_handle"]) for edge in incoming] == [
        ("quotes", "base"),
        ("regions", "join"),
    ]
    assert all(
        condition.get("target_handle") in {"base", "join"}
        for condition in recipe["postconditions"]
        if condition["kind"] == "edge_exists"
    )


@pytest.mark.parametrize("recipe_id", sorted(DOWNSTREAM_OUTPUT_RECIPES))
def test_recipe_can_own_one_connected_response_output(recipe_id: str) -> None:
    from haute.assistant._recipes import plan_recipe

    arguments = _arguments(recipe_id)
    arguments["output_name"] = "response"
    output_column = {
        "categorical_banding": "region_group",
        "continuous_banding": "driver_age_band",
        "reference_join": "region",
        "rating_step": "technical_premium",
    }[recipe_id]
    arguments["output_columns"] = [output_column]
    recipe = plan_recipe(recipe_id, arguments)

    output_nodes = [
        operation
        for operation in recipe["operations"]
        if operation["op"] == "add_node" and operation["node_type"] == "output"
    ]
    assert output_nodes == [
        {
            "op": "add_node",
            "node_type": "output",
            "name": "response",
            "ref": "recipe_output",
            "config": {
                "outputMapping": [
                    {
                        "source_port": arguments["name"],
                        "source_column": output_column,
                        "output_path": f"$[:].{output_column}",
                        "enabled": True,
                    }
                ],
                "outputFormat": "json",
            },
        }
    ]
    recipe_ref = {
        "categorical_banding": "categorical_banding",
        "continuous_banding": "banding",
        "reference_join": "reference_join",
        "rating_step": "rating_step",
    }[recipe_id]
    assert recipe["operations"][-1] == {
        "op": "add_edge",
        "source": "$recipe_" + recipe_ref,
        "target": "$recipe_output",
    }


@pytest.mark.parametrize(
    "partial_output",
    [{"output_name": "response"}, {"output_columns": ["premium"]}],
)
def test_recipe_rejects_partial_output_mapping(partial_output: dict[str, object]) -> None:
    from haute.assistant._recipes import RecipeError, plan_recipe

    with pytest.raises(RecipeError):
        plan_recipe("reference_join", {**_arguments("reference_join"), **partial_output})
