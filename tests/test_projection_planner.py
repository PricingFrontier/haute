"""Public projection planner API tests."""

from __future__ import annotations

import json

import pytest

from haute._edge_join import (
    EDGE_JOIN_DEFAULT_HOW,
    EDGE_JOIN_DEFAULT_SUFFIX,
    build_edge_join_kwargs,
    resolve_edge_join_role_indices,
)
from haute._execute_lazy import _compute_projection_plan
from haute._execution_context import ExecutionProfile
from haute.errors import ConfigError, ContractMismatchError
from haute.graph_utils import NodeType, _prepare_graph
from haute.projection import (
    UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
    AllExcept,
    ProjectionRequest,
    builder_required_output_columns_by_node,
    explain,
    model_score_required_output_columns,
    plan,
    projection_rule_coverage_by_node_type,
    simple_join_calls_for_parent_inputs,
    source_scan_projection,
    validate_projection_rule_coverage,
)
from tests.conftest import make_edge, make_graph, make_node


def test_projection_coverage_map_mentions_every_node_type() -> None:
    coverage = projection_rule_coverage_by_node_type()
    assert set(coverage) == set(NodeType)
    validate_projection_rule_coverage()


def test_projection_rule_coverage_is_immutable() -> None:
    coverage = projection_rule_coverage_by_node_type()

    with pytest.raises(TypeError):
        coverage[NodeType.POLARS] = coverage[NodeType.DATA_SOURCE]  # type: ignore[index]


def test_projection_rule_coverage_declares_opaque_node_types_explicitly() -> None:
    coverage = projection_rule_coverage_by_node_type()

    opaque_types = {node_type for node_type, entry in coverage.items() if entry.opaque}
    assert opaque_types == {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
    for node_type in opaque_types:
        assert coverage[node_type].rules == frozenset({"opaque_contract"})


def _projection_signature(projection_plan):
    def _normalise(mapping):
        return {
            key: None if value is None else tuple(sorted(value))
            for key, value in sorted(mapping.items(), key=lambda item: str(item[0]))
        }

    return (
        _normalise(projection_plan.needed_by_node),
        _normalise(projection_plan.edge_demands),
        tuple(sorted(projection_plan.materialisation_boundaries)),
        tuple(sorted(projection_plan.opaque_boundaries)),
    )


def test_projection_plan_is_stable_when_graph_order_changes() -> None:
    nodes = [
        {
            "id": "source",
            "data": {"label": "source", "nodeType": "dataSource", "config": {}},
        },
        {
            "id": "band",
            "data": {
                "label": "band",
                "nodeType": "banding",
                "config": {
                    "factors": [
                        {
                            "column": "age",
                            "outputColumn": "age_band",
                            "banding": "continuous",
                            "rules": [],
                            "default": "other",
                        }
                    ]
                },
            },
        },
        {
            "id": "out",
            "data": {
                "label": "out",
                "nodeType": "output",
                "config": {"fields": ["quote_id", "age_band"]},
            },
        },
    ]
    edges = [
        make_edge("source", "band").model_dump(),
        make_edge("band", "out").model_dump(),
    ]
    graph = make_graph({"nodes": nodes, "edges": edges})
    permuted = make_graph({"nodes": list(reversed(nodes)), "edges": list(reversed(edges))})

    request_kwargs = {
        "target_node_id": "out",
        "required_columns_by_node": {"out": {"quote_id", "age_band"}},
        "profile": ExecutionProfile.PREVIEW_EAGER,
    }

    assert _projection_signature(plan(ProjectionRequest(graph=graph, **request_kwargs))) == (
        _projection_signature(plan(ProjectionRequest(graph=permuted, **request_kwargs)))
    )


def _fan_in_graph(*, declared_parent_inputs: bool = True):
    contract: dict[str, object] = {
        "inputs": ["quote_id", "left_value", "right_value"],
        "outputs": [],
    }
    if declared_parent_inputs:
        contract["inputs_by_parent"] = {
            "left": ["quote_id", "left_value"],
            "right": ["quote_id", "right_value"],
        }

    return make_graph(
        {
            "nodes": [
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='quote_id', how='left')",
                            "contract": contract,
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["quote_id", "left_value", "right_value"]},
                    },
                },
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "out").model_dump(),
            ],
        }
    )


def _ratebook_graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "scored",
                    "data": {
                        "label": "scored",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "ratebook_opt",
                    "data": {
                        "label": "ratebook_opt",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "data_input": "scored",
                            "banding_source": "banding",
                            "quote_id": "quote_ref",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "factor_columns": [["territory_band"], ["channel_band"]],
                        },
                    },
                },
            ],
            "edges": [
                make_edge("scored", "ratebook_opt").model_dump(),
                make_edge("banding", "ratebook_opt").model_dump(),
            ],
        }
    )


def _model_score_graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "score",
                    "data": {
                        "label": "score",
                        "nodeType": "modelScore",
                        "config": {
                            "task": "regression",
                            "output_column": "prediction",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["prediction"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "score").model_dump(),
                make_edge("score", "out").model_dump(),
            ],
        }
    )


def _children_of_for_target(graph, target_node_id: str):
    node_map, order, parents_of, _id_to_name = _prepare_graph(graph, target_node_id)
    children_of: dict[str, list[str]] = {node_id: [] for node_id in order}
    for child_id, parent_ids in parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children_of:
                children_of[parent_id].append(child_id)
    return node_map, order, children_of


def test_public_projection_plan_matches_private_projection_engine():
    graph = _fan_in_graph()
    required = {"out": {"quote_id", "left_value", "right_value"}}
    node_map, order, children_of = _children_of_for_target(graph, "out")

    private_plan = _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node=required,
        strict_projection=True,
    )
    public_plan = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node=required,
        )
    )

    assert public_plan.needed_by_node == {
        node_id: None if columns is None else frozenset(columns)
        for node_id, columns in private_plan.needed_by_node.items()
    }
    assert public_plan.edge_demands == {
        edge: None if columns is None else frozenset(columns)
        for edge, columns in private_plan.edge_demands.items()
    }


def test_public_projection_plan_does_not_delegate_to_executor_private_planner(
    monkeypatch,
):
    graph = _fan_in_graph()
    required = {"out": {"quote_id", "left_value", "right_value"}}

    def _private_planner_called(*_args, **_kwargs):
        raise AssertionError("public projection planner called executor private planner")

    import haute._execute_lazy as execute_lazy

    monkeypatch.setattr(
        execute_lazy,
        "_compute_projection_plan",
        _private_planner_called,
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node=required,
        )
    )

    assert projection.needed_by_node["joined"] == frozenset(
        {"quote_id", "left_value", "right_value"}
    )
    assert projection.edge_demands[("left", "joined")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "joined")] == frozenset({"quote_id", "right_value"})


def test_public_projection_plan_routes_fan_in_demands_by_parent():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value", "right_value"}},
        )
    )

    assert projection.needed_by_node["joined"] == frozenset(
        {"quote_id", "left_value", "right_value"}
    )
    assert projection.edge_demands[("left", "joined")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "joined")] == frozenset({"quote_id", "right_value"})
    assert isinstance(projection.opaque_boundaries, frozenset)


def test_projection_explain_reports_node_and_edge_reasons():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value", "right_value"}},
        )
    )

    lines = explain(projection, column="right_value")

    assert any("out" in line and "caller required columns" in line for line in lines)
    assert any("right -> joined" in line and "fan-in" in line for line in lines)
    assert all("left_value" not in line for line in lines)


def test_projection_diagnostics_records_named_rule_reasons():
    fan_in_projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value", "right_value"}},
        )
    )
    ratebook_projection = plan(
        ProjectionRequest(
            graph=_ratebook_graph(),
            target_node_id="ratebook_opt",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={"ratebook_opt": {"quote_ref", "scenario_index"}},
        )
    )

    assert fan_in_projection.diagnostics.node_reasons["out"].rule == "projection_seed"
    assert fan_in_projection.diagnostics.edge_reasons[("right", "joined")].rule == "polars_fan_in"
    assert (
        ratebook_projection.diagnostics.edge_reasons[("scored", "ratebook_opt")].rule
        == "optimiser_parent_demand"
    )


def test_projection_diagnostics_payload_is_json_safe():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(declared_parent_inputs=False),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value"}},
        )
    )

    assert projection.diagnostics_payload()["opaque_reasons"]["left"]["rule"] in {
        "child_demand",
        "opaque_demand",
    }
    assert projection.diagnostics_payload()["node_reasons"]["out"]["rule"] == ("projection_seed")
    json.dumps(projection.diagnostics_payload())


def test_execution_facade_attaches_projection_strategy_to_context():
    from haute._execution_context import ExecutionContext
    from haute.execution import plan_execution_strategy

    context = ExecutionContext(
        operation="test_projection_facade",
        profile=ExecutionProfile.LAZY_SINK,
    )
    request = ProjectionRequest(
        graph=_fan_in_graph(declared_parent_inputs=False),
        target_node_id="out",
        profile=ExecutionProfile.LAZY_SINK,
        required_columns_by_node={"out": {"quote_id", "left_value"}},
    )

    projection = plan_execution_strategy(request, execution_context=context)

    assert context.projection_plan is projection
    diagnostics = context.metrics_payload(status="completed")["projection_plan_diagnostics"]
    assert diagnostics["strategy_summary"]["profile"] == "lazy_sink"


def test_projection_diagnostics_payload_exposes_strategy_reasons_for_broad_and_all_except():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {
                            "path": "data.parquet",
                            "code": "df = df.filter(pl.col('segment') == 'A')",
                        },
                    },
                },
                {
                    "id": "train",
                    "data": {
                        "label": "train",
                        "nodeType": "modelling",
                        "config": {},
                    },
                },
            ],
            "edges": [make_edge("source", "train").model_dump()],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="train",
            profile=ExecutionProfile.TRAINING_PREP,
            required_columns_by_node={
                "train": AllExcept(
                    required_columns=frozenset({"target", "weight"}),
                    excluded_columns=frozenset({"quote_id"}),
                )
            },
        )
    )

    payload = projection.diagnostics_payload()

    assert payload["node_reasons"]["train"] == {
        "rule": "schema_all_except",
        "message": "schema-derived all-except demand",
        "details": {
            "exclude": ("quote_id",),
            "keep": ("target", "weight"),
        },
    }
    assert payload["opaque_reasons"]["source"]["rule"] == (UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME)
    assert payload["strategy_summary"]["node_strategy_counts"] == {
        "unprojected_streaming_boundary": 1,
        "schema_all_except": 1,
    }
    assert payload["strategy_summary"]["node_strategies"] == [
        {
            "node_id": "source",
            "strategy": "unprojected_streaming_boundary",
            "reason_rule": UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
        },
        {
            "node_id": "train",
            "strategy": "schema_all_except",
            "reason_rule": "schema_all_except",
        },
    ]
    json.dumps(payload)


def test_single_parent_polars_with_columns_projects_expression_dependencies():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "features",
                    "data": {
                        "label": "features",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.with_columns("
                                "(pl.col('premium') - pl.col('burn_cost')).alias('margin'))"
                            )
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["margin"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "features").model_dump(),
                make_edge("features", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("source", "features")] == frozenset({"premium", "burn_cost"})
    assert projection.diagnostics.edge_reasons[("source", "features")].rule == (
        "polars_expression_dependency"
    )


def test_single_parent_polars_filter_keeps_predicate_dependencies():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "filtered",
                    "data": {
                        "label": "filtered",
                        "nodeType": "polars",
                        "config": {"code": "df = df.filter(pl.col('segment') == 'A')"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["premium"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "filtered").model_dump(),
                make_edge("filtered", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("source", "filtered")] == frozenset({"premium", "segment"})


def test_single_parent_polars_rename_maps_logical_demand_to_parent_column():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "renamed",
                    "data": {
                        "label": "renamed",
                        "nodeType": "polars",
                        "config": {"code": "df = df.rename({'raw_premium': 'premium'})"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["premium", "quote_id"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "renamed").model_dump(),
                make_edge("renamed", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("source", "renamed")] == frozenset({"raw_premium", "quote_id"})


def test_single_parent_polars_group_by_uses_explicit_boundary_not_wrong_projection():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.group_by('segment').agg("
                                "pl.col('premium').sum().alias('premium'))"
                            )
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["premium"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands.get(("source", "agg")) is None
    assert projection.diagnostics.opaque_reasons["source"].rule in {
        "opaque_demand",
        "child_demand",
    }


def test_public_projection_plan_strict_profile_uses_boundary_for_unowned_fan_in():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(declared_parent_inputs=False),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value", "right_value"}},
        )
    )

    assert projection.needed_by_node["joined"] == frozenset(
        {"quote_id", "left_value", "right_value"}
    )
    assert ("left", "joined") not in projection.edge_demands
    assert ("right", "joined") not in projection.edge_demands
    assert "left" in projection.opaque_boundaries
    assert "right" in projection.opaque_boundaries


def test_public_projection_plan_strict_profile_uses_boundary_for_unowned_fan_in_without_seed():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(declared_parent_inputs=False),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node=None,
        )
    )

    assert projection.needed_by_node["joined"] == frozenset(
        {"quote_id", "left_value", "right_value"}
    )
    assert projection.needed_by_node["left"] is None
    assert projection.needed_by_node["right"] is None


def test_public_projection_plan_strict_profile_projects_simple_user_code():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "custom",
                    "data": {
                        "label": "custom",
                        "nodeType": "polars",
                        "config": {"code": "df = df.with_columns(pl.col('a') + 1)"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["a"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "custom").model_dump(),
                make_edge("custom", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["custom"] == frozenset({"a"})
    assert projection.needed_by_node["source"] == frozenset({"a"})
    assert projection.diagnostics.edge_reasons[("source", "custom")].rule == (
        "polars_expression_dependency"
    )


def test_public_projection_plan_strict_profile_boundaries_terminal_user_code():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "custom",
                    "data": {
                        "label": "custom",
                        "nodeType": "polars",
                        "config": {"code": "df = df.with_columns(pl.col('a') + 1)"},
                    },
                },
                {
                    "id": "sink",
                    "data": {
                        "label": "sink",
                        "nodeType": "dataSink",
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("source", "custom").model_dump(),
                make_edge("custom", "sink").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="sink",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["custom"] is None
    assert projection.needed_by_node["source"] is None


def test_public_projection_plan_strict_profile_runs_source_user_code_unprojected():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {
                            "path": "data.parquet",
                            "code": "df = df.with_columns(pl.col('a') + 1)",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["a"]},
                    },
                },
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["source"] is None
    assert (
        projection.diagnostics.opaque_reasons["source"].rule
        == UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME
    )


def test_public_projection_plan_strict_profile_allows_projection_safe_source_limit():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {
                            "path": "data.parquet",
                            "contract": "opaque",
                            "code": "df = df.limit(10000000)",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["quote_id", "premium"]},
                    },
                },
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["source"] == frozenset({"quote_id", "premium"})


def test_public_projection_plan_strict_profile_runs_source_filter_unprojected():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {
                            "path": "data.parquet",
                            "contract": "opaque",
                            "code": "df = df.filter(pl.col('segment') == 'A')",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["quote_id"]},
                    },
                },
            ],
            "edges": [make_edge("source", "out").model_dump()],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["source"] is None
    assert (
        projection.diagnostics.opaque_reasons["source"].rule
        == UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME
    )


def test_public_projection_plan_strict_profile_allows_contracted_user_code():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "custom",
                    "data": {
                        "label": "custom",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = df.with_columns((pl.col('a') + 1).alias('b'))",
                            "contract": {"inputs": ["a"], "outputs": ["b"]},
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["b"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "custom").model_dump(),
                make_edge("custom", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["source"] == frozenset({"a"})


def test_public_projection_plan_routes_ratebook_shared_input_factors_in_planner():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "ratebook_opt",
                    "data": {
                        "label": "ratebook_opt",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "data_input": "source",
                            "banding_source": "source",
                            "quote_id": "quote_ref",
                            "scenario_index": "scenario_index",
                            "scenario_value": "scenario_value",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "factor_columns": [["territory_band"], ["channel_band"]],
                        },
                    },
                },
            ],
            "edges": [make_edge("source", "ratebook_opt").model_dump()],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="ratebook_opt",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={
                "source": {
                    "quote_ref",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            },
        )
    )

    assert projection.edge_demands[("source", "ratebook_opt")] == frozenset(
        {
            "quote_ref",
            "scenario_index",
            "scenario_value",
            "expected_income",
            "volume",
            "territory_band",
            "channel_band",
        }
    )
    assert projection.diagnostics.edge_reasons[("source", "ratebook_opt")].rule == (
        "optimiser_parent_demand"
    )


def test_public_projection_plan_treats_empty_polars_node_as_passthrough():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "passthrough",
                    "data": {
                        "label": "passthrough",
                        "nodeType": "polars",
                        "config": {},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": ["quote_id"]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "passthrough").model_dump(),
                make_edge("passthrough", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.needed_by_node["source"] == frozenset({"quote_id"})


def test_public_projection_plan_preview_profile_preserves_compatibility():
    projection = plan(
        ProjectionRequest(
            graph=_fan_in_graph(declared_parent_inputs=False),
            target_node_id="out",
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"out": {"quote_id", "left_value"}},
        )
    )

    assert projection.needed_by_node["left"] is None
    assert projection.needed_by_node["right"] is None


def test_public_projection_plan_routes_ratebook_data_and_banding_inputs():
    required = {
        "quote_ref",
        "scenario_index",
        "scenario_value",
        "expected_income",
        "volume",
    }

    projection = plan(
        ProjectionRequest(
            graph=_ratebook_graph(),
            target_node_id="ratebook_opt",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={"ratebook_opt": required},
        )
    )

    assert projection.edge_demands[("scored", "ratebook_opt")] == frozenset(required)
    assert projection.edge_demands[("banding", "ratebook_opt")] == frozenset(
        {"quote_ref", "territory_band", "channel_band"}
    )
    assert projection.needed_by_node["banding"] == frozenset(
        {"quote_ref", "territory_band", "channel_band"}
    )


def test_public_ratebook_factor_required_columns_validates_factor_config():
    from haute.projection import ratebook_factor_required_columns

    assert ratebook_factor_required_columns(
        {
            "quote_id": "quote_ref",
            "factor_columns": [["territory_band"], ["channel_band", "age_band"]],
        }
    ) == frozenset({"quote_ref", "territory_band", "channel_band", "age_band"})

    with pytest.raises(ValueError, match="must be lists"):
        ratebook_factor_required_columns({"factor_columns": ["territory_band"]})


def test_builder_demands_keep_eager_model_score_schema_expanded():
    graph = _model_score_graph()
    node_map, order, _children_of = _children_of_for_target(graph, "out")
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"out": {"prediction"}},
        )
    )

    eager_demands = builder_required_output_columns_by_node(
        node_map,
        projection.needed_by_node,
        preserve_eager_model_score_inputs=True,
    )
    lazy_demands = builder_required_output_columns_by_node(
        node_map,
        projection.needed_by_node,
        preserve_eager_model_score_inputs=False,
    )

    assert "score" in order
    assert eager_demands["score"] is None
    assert lazy_demands["score"] == frozenset({"prediction"})


def test_model_score_required_output_columns_uses_explicit_downstream_demand_only():
    assert (
        model_score_required_output_columns(
            {"selected_columns": ["quote_id", "prediction", 123]},
            None,
        )
        is None
    )
    assert (
        model_score_required_output_columns(
            {"code": "df = df.with_columns(extra=pl.lit(1))"},
            {"prediction"},
        )
        is None
    )
    assert model_score_required_output_columns(
        {"code": "df = df", "selected_columns": ["quote_id"]},
        {"prediction"},
    ) == frozenset({"prediction"})
    assert model_score_required_output_columns(
        {"code": "df = df", "selected_columns": ["quote_id"]},
        {"prediction"},
        post_processing_code="",
    ) == frozenset({"prediction"})


def test_source_scan_projection_maps_logical_renames_to_physical_columns():
    projection = source_scan_projection(
        {
            "selected_columns": ["quote_id", "raw_premium", "unused"],
            "column_renames": {"raw_premium": "premium"},
        },
        {"quote_id", "premium"},
    )

    assert projection.columns == frozenset({"quote_id", "raw_premium"})
    assert projection.validate_columns == frozenset({"quote_id", "raw_premium", "unused"})


def test_source_scan_projection_broadens_unsafe_rename_without_selected_columns():
    projection = source_scan_projection(
        {"column_renames": {"raw_premium": "premium"}},
        {"premium"},
    )

    assert projection.columns is None


def test_source_scan_projection_rejects_demand_excluded_by_selected_columns():
    with pytest.raises(ValueError, match="excluded by selected_columns"):
        source_scan_projection(
            {
                "selected_columns": ["quote_id"],
                "column_renames": {"raw_premium": "premium"},
            },
            {"premium"},
        )


def test_source_scan_projection_rejects_malformed_projection_config():
    with pytest.raises(ValueError, match="selected_columns"):
        source_scan_projection({"selected_columns": ["quote_id", 123]}, {"quote_id"})

    with pytest.raises(ValueError, match="column_renames"):
        source_scan_projection(
            {"column_renames": {"raw_premium": 123}},
            {"premium"},
        )


def _edge_join_contract(
    inputs: list[str],
    left_inputs: list[str],
    right_inputs: list[str],
) -> dict[str, object]:
    return {
        "inputs": inputs,
        "outputs": [],
        "inputs_by_parent": {"left": left_inputs, "right": right_inputs},
    }


def _edge_join_graph(
    *,
    join_config: dict[str, object],
    contract: dict[str, object] | None,
    out_fields: list[str],
):
    config: dict[str, object] = dict(join_config)
    if contract is not None:
        config["contract"] = contract

    return make_graph(
        {
            "nodes": [
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataSource",
                        "config": {},
                    },
                },
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "edgeJoin",
                        "config": config,
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": out_fields},
                    },
                },
            ],
            "edges": [
                make_edge("left", "join").model_dump(),
                make_edge("right", "join").model_dump(),
                make_edge("join", "out").model_dump(),
            ],
        }
    )


def test_edge_join_routes_fan_in_demands_by_parent_through_declared_contract():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={"baseInput": "left", "joinInput": "right", "on": "quote_id"},
                contract=_edge_join_contract(
                    ["quote_id", "left_value", "right_value"],
                    ["quote_id", "left_value"],
                    ["quote_id", "right_value"],
                ),
                out_fields=["quote_id", "left_value", "right_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"out": {"quote_id", "left_value", "right_value"}},
        )
    )

    assert projection.needed_by_node["join"] == frozenset(
        {"quote_id", "left_value", "right_value"}
    )
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"quote_id", "right_value"})
    assert "left_value" not in projection.edge_demands[("right", "join")]
    assert "right_value" not in projection.edge_demands[("left", "join")]
    assert projection.diagnostics.edge_reasons[("left", "join")].rule == "polars_fan_in"
    assert projection.diagnostics.edge_reasons[("right", "join")].rule == "polars_fan_in"


def test_edge_join_demands_on_keys_from_both_parents_even_when_not_demanded():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={"baseInput": "left", "joinInput": "right", "on": "quote_id"},
                contract=_edge_join_contract(
                    ["left_value", "right_value"],
                    ["left_value"],
                    ["right_value"],
                ),
                out_fields=["left_value", "right_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"quote_id", "right_value"})


def test_edge_join_routes_left_on_right_on_keys_to_their_own_sides():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "leftOn": ["left_key_a", "left_key_b"],
                    "rightOn": ["right_key_a", "right_key_b"],
                },
                contract=_edge_join_contract(
                    ["left_value", "right_value"],
                    ["left_value"],
                    ["right_value"],
                ),
                out_fields=["left_value", "right_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("left", "join")] == frozenset(
        {"left_value", "left_key_a", "left_key_b"}
    )
    assert projection.edge_demands[("right", "join")] == frozenset(
        {"right_value", "right_key_a", "right_key_b"}
    )


def test_edge_join_planner_prefers_on_over_left_on_right_on_while_executor_rejects():
    join_config: dict[str, object] = {
        "baseInput": "left",
        "joinInput": "right",
        "on": "quote_id",
        "leftOn": "left_key",
        "rightOn": "right_key",
    }

    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config=join_config,
                contract=_edge_join_contract(
                    ["left_value", "right_value"],
                    ["left_value"],
                    ["right_value"],
                ),
                out_fields=["left_value", "right_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # Planner mirrors _edge_join_calls: `on` wins, leftOn/rightOn are ignored.
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"quote_id", "right_value"})

    # The executor is stricter and refuses the combined config outright.
    with pytest.raises(ConfigError, match="combine on with leftOn/rightOn"):
        build_edge_join_kwargs(join_config)


def test_edge_join_config_without_how_and_suffix_matches_explicit_defaults():
    suffixed = f"x{EDGE_JOIN_DEFAULT_SUFFIX}"
    contract = _edge_join_contract(
        ["quote_id", "left_value", "right_value"],
        ["quote_id", "left_value"],
        ["quote_id", "right_value"],
    )
    out_fields = ["left_value", suffixed, "extra"]
    defaulted = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={"baseInput": "left", "joinInput": "right", "on": "quote_id"},
                contract=contract,
                out_fields=out_fields,
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )
    explicit = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "on": "quote_id",
                    "how": EDGE_JOIN_DEFAULT_HOW,
                    "suffix": EDGE_JOIN_DEFAULT_SUFFIX,
                },
                contract=contract,
                out_fields=out_fields,
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert _projection_signature(defaulted) == _projection_signature(explicit)
    # Default suffix routes the suffixed demand to base column "x" on both
    # sides; default how ("left") routes the unmatched "extra" to the base.
    assert defaulted.edge_demands[("left", "join")] == frozenset(
        {"quote_id", "left_value", "x", "extra"}
    )
    assert defaulted.edge_demands[("right", "join")] == frozenset(
        {"quote_id", "right_value", "x"}
    )


def test_edge_join_suffixed_demand_routes_base_column_to_both_parents():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "on": "quote_id",
                    "suffix": "_lookup",
                },
                contract=_edge_join_contract(
                    ["quote_id", "left_value", "right_value"],
                    ["quote_id", "left_value"],
                    ["quote_id", "right_value"],
                ),
                out_fields=["left_value", "premium_lookup"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # A suffix collision means both sides carried "premium", so both parents
    # must be asked for the base column; the suffixed name itself is derived.
    assert projection.edge_demands[("left", "join")] == frozenset(
        {"quote_id", "left_value", "premium"}
    )
    assert projection.edge_demands[("right", "join")] == frozenset(
        {"quote_id", "right_value", "premium"}
    )


def test_edge_join_unmatched_demand_routes_to_configured_base_parent():
    # Roles are reversed relative to edge order: the "right" node is the
    # join's base (Polars left side), so the default left join preserves it.
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={"baseInput": "right", "joinInput": "left", "on": "quote_id"},
                contract=_edge_join_contract(
                    ["quote_id", "left_value", "right_value"],
                    ["quote_id", "left_value"],
                    ["quote_id", "right_value"],
                ),
                out_fields=["right_value", "extra"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("right", "join")] == frozenset(
        {"quote_id", "right_value", "extra"}
    )
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})


@pytest.mark.parametrize("how", ["semi", "anti"])
def test_edge_join_without_contract_keeps_semi_anti_parents_unprojected(how):
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "on": "quote_id",
                    "how": how,
                },
                contract=None,
                out_fields=["quote_id", "left_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # Without a declared contract the planner must not prune either parent:
    # both stay full-width opaque boundaries, so no column can be lost.
    assert projection.needed_by_node["join"] == frozenset({"quote_id", "left_value"})
    assert projection.needed_by_node["left"] is None
    assert projection.needed_by_node["right"] is None
    assert ("left", "join") not in projection.edge_demands
    assert ("right", "join") not in projection.edge_demands
    assert "left" in projection.opaque_boundaries
    assert "right" in projection.opaque_boundaries


def test_edge_join_contract_without_inputs_by_parent_uses_unprojected_boundary():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={"baseInput": "left", "joinInput": "right", "on": "quote_id"},
                contract={
                    "inputs": ["quote_id", "left_value", "right_value"],
                    "outputs": [],
                },
                out_fields=["quote_id", "left_value", "right_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert ("left", "join") not in projection.edge_demands
    assert ("right", "join") not in projection.edge_demands
    assert "left" in projection.opaque_boundaries
    assert "right" in projection.opaque_boundaries


def test_edge_join_missing_join_input_routes_contract_only_and_executor_rejects():
    join_config: dict[str, object] = {"baseInput": "left", "on": "quote_id"}
    graph = _edge_join_graph(
        join_config=join_config,
        contract=_edge_join_contract(
            ["left_value", "right_value"],
            ["left_value"],
            ["right_value"],
        ),
        out_fields=["left_value", "right_value"],
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # The planner is lenient: no join call is derived, so the configured `on`
    # key is not demanded and routing falls back to the declared contract.
    assert simple_join_calls_for_parent_inputs(graph.node_map["join"], ["left", "right"]) == ()
    assert projection.edge_demands[("left", "join")] == frozenset({"left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"right_value"})

    # The executor path rejects the same config loudly.
    with pytest.raises(ConfigError, match="joinInput"):
        resolve_edge_join_role_indices(join_config, ["left", "right"])


def test_edge_join_empty_join_keys_route_contract_only_and_executor_rejects():
    join_config: dict[str, object] = {"baseInput": "left", "joinInput": "right", "on": []}
    graph = _edge_join_graph(
        join_config=join_config,
        contract=_edge_join_contract(
            ["left_value", "right_value"],
            ["left_value"],
            ["right_value"],
        ),
        out_fields=["left_value", "right_value"],
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # The planner derives a keyless join call, which adds no key demands.
    calls = simple_join_calls_for_parent_inputs(graph.node_map["join"], ["left", "right"])
    assert len(calls) == 1
    assert calls[0].key_pairs == ()
    assert projection.edge_demands[("left", "join")] == frozenset({"left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"right_value"})

    # The executor path rejects keyless non-cross joins loudly.
    with pytest.raises(ConfigError, match="join keys"):
        build_edge_join_kwargs(join_config)


def test_edge_join_planner_join_call_mirrors_config_roles_and_defaults():
    node = make_node(
        {
            "id": "join",
            "data": {
                "label": "join",
                "nodeType": "edgeJoin",
                "config": {"baseInput": "right", "joinInput": "left", "on": ["k1", "k2"]},
            },
        }
    )

    calls = simple_join_calls_for_parent_inputs(node, ["left", "right"])

    assert len(calls) == 1
    assert calls[0].left_parent == "right"
    assert calls[0].right_parent == "left"
    assert calls[0].key_pairs == (("k1", "k1"), ("k2", "k2"))
    assert calls[0].how == EDGE_JOIN_DEFAULT_HOW
    assert calls[0].suffix == EDGE_JOIN_DEFAULT_SUFFIX


@pytest.mark.parametrize(
    ("config", "executor_error_match"),
    [
        ({"joinInput": "right", "on": "quote_id"}, "baseInput"),
        ({"baseInput": "left", "joinInput": "left", "on": "quote_id"}, "distinct"),
        ({"baseInput": "left", "joinInput": "ghost", "on": "quote_id"}, "not connected"),
    ],
)
def test_edge_join_degenerate_role_configs_yield_no_planner_join_calls(
    config,
    executor_error_match,
):
    node = make_node(
        {
            "id": "join",
            "data": {"label": "join", "nodeType": "edgeJoin", "config": config},
        }
    )

    assert simple_join_calls_for_parent_inputs(node, ["left", "right"]) == ()
    with pytest.raises(ConfigError, match=executor_error_match):
        resolve_edge_join_role_indices(config, ["left", "right"])


@pytest.mark.parametrize("blank", ["", None])
def test_edge_join_blank_how_and_suffix_fall_back_to_executor_defaults(blank):
    config: dict[str, object] = {
        "baseInput": "left",
        "joinInput": "right",
        "on": "quote_id",
        "how": blank,
        "suffix": blank,
    }
    node = make_node(
        {
            "id": "join",
            "data": {"label": "join", "nodeType": "edgeJoin", "config": config},
        }
    )

    calls = simple_join_calls_for_parent_inputs(node, ["left", "right"])
    kwargs = build_edge_join_kwargs(config)

    assert len(calls) == 1
    assert calls[0].how == kwargs["how"] == EDGE_JOIN_DEFAULT_HOW
    assert calls[0].suffix == kwargs["suffix"] == EDGE_JOIN_DEFAULT_SUFFIX


@pytest.mark.parametrize(
    ("join_keys", "executor_error_match"),
    [
        ({}, "join keys"),
        ({"leftOn": ["key_a", "key_b"], "rightOn": ["key_c"]}, "same number"),
        ({"on": ["quote_id", ""]}, "non-empty string"),
        ({"on": ["quote_id", 7]}, "non-empty string"),
    ],
)
def test_edge_join_malformed_key_lists_yield_no_planner_keys_and_executor_rejects(
    join_keys,
    executor_error_match,
):
    config: dict[str, object] = {"baseInput": "left", "joinInput": "right", **join_keys}
    node = make_node(
        {
            "id": "join",
            "data": {"label": "join", "nodeType": "edgeJoin", "config": config},
        }
    )

    # The planner stays lenient: roles are valid so a join call is derived,
    # but no key pairs are — the executor rejects the same config loudly, so
    # the lenient plan can never under-project a frame that actually runs.
    calls = simple_join_calls_for_parent_inputs(node, ["left", "right"])
    assert len(calls) == 1
    assert calls[0].key_pairs == ()
    with pytest.raises(ConfigError, match=executor_error_match):
        build_edge_join_kwargs(config)


def test_edge_join_right_join_routes_unmatched_demand_to_join_parent():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "on": "quote_id",
                    "how": "right",
                },
                contract=_edge_join_contract(
                    ["quote_id", "left_value", "right_value"],
                    ["quote_id", "left_value"],
                    ["quote_id", "right_value"],
                ),
                out_fields=["right_value", "extra"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # A right join preserves the join side, so the undeclared "extra" demand
    # routes there — the mirror of the default-left-join base routing.
    assert projection.edge_demands[("right", "join")] == frozenset(
        {"quote_id", "right_value", "extra"}
    )
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})


def test_edge_join_semi_join_with_declared_contract_routes_declared_demand():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config={
                    "baseInput": "left",
                    "joinInput": "right",
                    "on": "quote_id",
                    "how": "semi",
                },
                contract=_edge_join_contract(
                    ["quote_id", "left_value"],
                    ["quote_id", "left_value"],
                    ["quote_id"],
                ),
                out_fields=["left_value"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # Semi joins emit only base columns, but the join parent must still be
    # asked for its key so the filter can run.
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"quote_id"})


def test_edge_join_anti_join_uncovered_demand_raises_contract_mismatch():
    # Semi/anti joins preserve neither side for passthrough purposes, so an
    # undeclared plain-named demand has nowhere to route: with both parents'
    # declarations inside the node inputs the passthrough parent is ambiguous
    # and strict planning must fail loudly instead of guessing.
    graph = _edge_join_graph(
        join_config={
            "baseInput": "left",
            "joinInput": "right",
            "on": "quote_id",
            "how": "anti",
        },
        contract=_edge_join_contract(
            ["quote_id", "left_value"],
            ["quote_id", "left_value"],
            ["quote_id"],
        ),
        out_fields=["left_value", "mystery"],
    )

    with pytest.raises(ContractMismatchError, match="does not cover"):
        plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="out",
                profile=ExecutionProfile.LAZY_SINK,
            )
        )


def test_edge_join_cross_join_with_full_contract_coverage_routes_cleanly():
    join_config: dict[str, object] = {"baseInput": "left", "joinInput": "right", "how": "cross"}
    graph = _edge_join_graph(
        join_config=join_config,
        contract=_edge_join_contract(
            ["left_value", "right_value"],
            ["left_value"],
            ["right_value"],
        ),
        out_fields=["left_value", "right_value"],
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert projection.edge_demands[("left", "join")] == frozenset({"left_value"})
    assert projection.edge_demands[("right", "join")] == frozenset({"right_value"})

    # Keyless is the valid shape for cross joins on both sides of the parity.
    calls = simple_join_calls_for_parent_inputs(graph.node_map["join"], ["left", "right"])
    assert len(calls) == 1
    assert calls[0].how == "cross"
    assert calls[0].key_pairs == ()
    assert build_edge_join_kwargs(join_config) == {
        "how": "cross",
        "suffix": EDGE_JOIN_DEFAULT_SUFFIX,
    }


def test_edge_join_coalesce_false_key_suffix_demand_keeps_key_on_join_parent():
    join_config: dict[str, object] = {
        "baseInput": "left",
        "joinInput": "right",
        "on": "quote_id",
        "coalesce": False,
    }
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                join_config=join_config,
                contract=_edge_join_contract(
                    ["quote_id", "left_value", "right_value"],
                    ["quote_id", "left_value"],
                    ["quote_id", "right_value"],
                ),
                out_fields=["left_value", "right_value", f"quote_id{EDGE_JOIN_DEFAULT_SUFFIX}"],
            ),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    # With coalesce=False the runtime keeps the right key as quote_id_right;
    # demanding it must keep quote_id demanded from the join parent rather
    # than erroring or pruning the key column.
    assert projection.edge_demands[("right", "join")] == frozenset({"quote_id", "right_value"})
    assert projection.edge_demands[("left", "join")] == frozenset({"quote_id", "left_value"})
    assert build_edge_join_kwargs(join_config)["coalesce"] is False
