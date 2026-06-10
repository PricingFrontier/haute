"""Public projection planner API tests."""

from __future__ import annotations

import json

import pytest

from haute._edge_join import narrow_join_parent_demand
from haute._execute_lazy import _compute_projection_plan
from haute._execution_context import ExecutionProfile
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
    source_scan_projection,
    validate_projection_rule_coverage,
)
from tests.conftest import make_edge, make_graph


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


def _edge_join_graph(*, keys: dict[str, object], base_outputs, join_outputs):
    """Build a base/join edge-join graph with concrete-contract parents."""
    join_config: dict[str, object] = {
        "baseInput": "base",
        "joinInput": "join",
        "how": "left",
        "contract": "opaque",
        **keys,
    }
    return make_graph(
        {
            "nodes": [
                {
                    "id": "base",
                    "data": {
                        "label": "base",
                        "nodeType": "polars",
                        "config": {"contract": {"inputs": [], "outputs": base_outputs}},
                    },
                },
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "modelScore",
                        "config": {"contract": {"inputs": [], "outputs": join_outputs}},
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "edgeJoin",
                        "config": join_config,
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {},
                    },
                },
            ],
            "edges": [
                make_edge("base", "joined").model_dump(),
                make_edge("join", "joined").model_dump(),
                make_edge("joined", "out").model_dump(),
            ],
        }
    )


def test_edge_join_projection_routes_demand_by_parent_produced_columns():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                keys={"on": ["quote_id"]},
                base_outputs=["policy_id"],
                join_outputs=["competitor_premium"],
            ),
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"joined": {"quote_id", "competitor_premium", "policy_id"}},
        )
    )

    # The join key is demanded from both roles; produced columns route to the
    # parent that owns them; the base role is the spine for anything else.
    assert projection.edge_demands[("base", "joined")] == frozenset({"quote_id", "policy_id"})
    assert projection.edge_demands[("join", "joined")] == frozenset(
        {"quote_id", "competitor_premium"}
    )
    assert projection.diagnostics.edge_reasons[("join", "joined")].rule == "edge_join_fan_in"


def test_edge_join_projection_keeps_suffixed_collision_column_from_join_parent():
    """Regression: a base/join name collision is emitted by Polars as
    ``<col><suffix>``; the rule must demand ``<col>`` from the JOIN parent so it
    survives pruning. Previously it was routed to base and the join's copy was
    dropped — silently, since the edge-join output contract is opaque."""
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                keys={"on": ["quote_id"]},
                base_outputs=["x"],
                join_outputs=["x"],
            ),
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            # downstream wants base's 'x', the join's suffixed 'x_right', and the key.
            required_columns_by_node={"joined": {"quote_id", "x", "x_right"}},
        )
    )
    # The join parent must still be asked for 'x' (so Polars can emit 'x_right').
    assert "x" in projection.edge_demands[("join", "joined")]
    assert "x" in projection.edge_demands[("base", "joined")]
    assert "quote_id" in projection.edge_demands[("join", "joined")]


def test_narrow_join_parent_demand_routes_keys_and_producers():
    assert narrow_join_parent_demand(
        {"k", "a", "b"},
        left_keys={"k"},
        right_keys={"k"},
        left_schema={"k", "a"},
        right_schema={"k", "b"},
        how="left",
        suffix="_right",
    ) == ({"k", "a"}, {"k", "b"})


def test_narrow_join_parent_demand_suffix_collision_demands_original_from_both():
    assert narrow_join_parent_demand(
        {"x_right"},
        left_keys={"k"},
        right_keys={"k"},
        left_schema={"k", "x"},
        right_schema={"k", "x"},
        how="left",
        suffix="_right",
    ) == ({"k", "x"}, {"k", "x"})


def test_narrow_join_parent_demand_semi_demands_keys_only_from_join():
    # A semi join's output is base-only: 'a' is a base column → base; the join
    # parent contributes only the key.
    assert narrow_join_parent_demand(
        {"k", "a"},
        left_keys={"k"},
        right_keys={"k"},
        left_schema={"k", "a"},
        right_schema={"k", "b"},
        how="semi",
        suffix="_right",
    ) == ({"k", "a"}, {"k"})


def test_narrow_join_parent_demand_returns_none_for_unnarrowable_cases():
    base = dict(left_keys={"k"}, right_keys={"k"}, left_schema={"k", "a"}, right_schema={"k", "b"})
    # cross / full / right are not mechanically narrowable → full-width.
    assert (
        narrow_join_parent_demand(
            {"a"},
            left_keys=set(),
            right_keys=set(),
            left_schema={"a"},
            right_schema=set(),
            how="cross",
            suffix="_right",
        )
        is None
    )
    assert narrow_join_parent_demand({"a"}, how="full", suffix="_right", **base) is None
    # A demanded column produced by neither parent can't be mapped → full-width.
    assert (
        narrow_join_parent_demand(
            {"k", "z"},
            left_keys={"k"},
            right_keys={"k"},
            left_schema={"k"},
            right_schema={"k"},
            how="left",
            suffix="_right",
        )
        is None
    )
    # The suffixed name is itself a real column → ambiguous → full-width.
    assert (
        narrow_join_parent_demand(
            {"x_right"},
            left_keys={"k"},
            right_keys={"k"},
            left_schema={"k", "x", "x_right"},
            right_schema={"k", "x"},
            how="left",
            suffix="_right",
        )
        is None
    )


def test_edge_join_projection_splits_left_and_right_keys_by_role():
    projection = plan(
        ProjectionRequest(
            graph=_edge_join_graph(
                keys={"leftOn": ["base_key"], "rightOn": ["join_key"]},
                base_outputs=["base_key", "policy_id"],
                join_outputs=["join_key", "competitor_premium"],
            ),
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"joined": {"policy_id", "competitor_premium"}},
        )
    )

    # leftOn demands the left key from base; rightOn demands the right key from join.
    assert projection.edge_demands[("base", "joined")] == frozenset({"base_key", "policy_id"})
    assert projection.edge_demands[("join", "joined")] == frozenset(
        {"join_key", "competitor_premium"}
    )


def test_edge_join_projection_keeps_full_width_when_a_parent_is_opaque():
    projection = plan(
        ProjectionRequest(
            graph=make_graph(
                {
                    "nodes": [
                        {
                            "id": "base",
                            "data": {
                                "label": "base",
                                "nodeType": "dataSource",
                                "config": {"contract": "opaque"},
                            },
                        },
                        {
                            "id": "join",
                            "data": {
                                "label": "join",
                                "nodeType": "modelScore",
                                "config": {
                                    "contract": {
                                        "inputs": [],
                                        "outputs": ["competitor_premium"],
                                    }
                                },
                            },
                        },
                        {
                            "id": "joined",
                            "data": {
                                "label": "joined",
                                "nodeType": "edgeJoin",
                                "config": {
                                    "baseInput": "base",
                                    "joinInput": "join",
                                    "how": "left",
                                    "on": ["quote_id"],
                                    "contract": "opaque",
                                },
                            },
                        },
                        {
                            "id": "out",
                            "data": {"label": "out", "nodeType": "output", "config": {}},
                        },
                    ],
                    "edges": [
                        make_edge("base", "joined").model_dump(),
                        make_edge("join", "joined").model_dump(),
                        make_edge("joined", "out").model_dump(),
                    ],
                }
            ),
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"joined": {"quote_id", "competitor_premium"}},
        )
    )

    # An opaque parent cannot prove ownership, so the join boundary stays full
    # width: the edge-join and both parents become opaque demand boundaries.
    assert projection.needed_by_node["joined"] == frozenset({"quote_id", "competitor_premium"})
    assert projection.needed_by_node["base"] is None
    assert projection.needed_by_node["join"] is None
    assert "base" in projection.opaque_boundaries
    assert "join" in projection.opaque_boundaries


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


def _single_parent_polars_plan(code: str, fields: list[str]):
    """Plan ``source -> transform(code) -> out(fields)`` under a strict profile."""
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
                    "id": "transform",
                    "data": {
                        "label": "transform",
                        "nodeType": "polars",
                        "config": {"code": code},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {"fields": fields},
                    },
                },
            ],
            "edges": [
                make_edge("source", "transform").model_dump(),
                make_edge("transform", "out").model_dump(),
            ],
        }
    )
    return plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )


def test_single_parent_polars_rename_then_filter_on_new_name_projects_pre_rename_columns():
    """A filter on the post-rename name must not re-add that name upstream.

    The parent only has ``raw_premium``; demanding ``premium`` from it would
    hard-fail a perfectly valid rename->filter pipeline at execution.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'raw_premium': 'premium'})\ndf = df.filter(pl.col('premium') > 0)",
        ["premium", "quote_id"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset(
        {"raw_premium", "quote_id"}
    )
    assert projection.needed_by_node["source"] == frozenset({"raw_premium", "quote_id"})
    assert projection.diagnostics.edge_reasons[("source", "transform")].rule == (
        "polars_expression_dependency"
    )


def test_single_parent_polars_chained_renames_track_each_namespace():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'}).filter(pl.col('b') > 0).rename({'b': 'c'})",
        ["c", "keep"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "keep"})


def test_single_parent_polars_multiple_renames_in_one_call_map_all_pairs():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'x', 'b': 'y'})\ndf = df.filter(pl.col('x') > 0)",
        ["x", "y"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_rename_to_same_name_is_a_no_op():
    projection = _single_parent_polars_plan(
        "df = df.rename({'premium': 'premium'})\ndf = df.filter(pl.col('premium') > 0)",
        ["premium"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"premium"})


def test_single_parent_polars_swap_renames_resolve_simultaneously():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b', 'b': 'a'})\ndf = df.filter(pl.col('b') > 0)",
        ["a", "b"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_rename_collision_keeps_pre_rename_reference_demand():
    """A pre-rename reference to the collision target must stay demanded.

    Both ``a`` and ``b`` reach the node, so the genuine Polars DuplicateError
    still surfaces at execution instead of being masked by projection.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('b') > 0)\ndf = df.rename({'a': 'b'})",
        ["b"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_demand_for_renamed_away_column_keeps_full_width():
    """Demanding a name the rename removed cannot be projected coherently.

    Full width lets execution raise the genuine missing-column error instead
    of the planner guessing a projection that changes the failure shape.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'})",
        ["a", "b"],
    )

    assert ("source", "transform") not in projection.edge_demands
    assert projection.needed_by_node["source"] is None
    assert "source" in projection.opaque_boundaries


def test_single_parent_polars_duplicate_rename_targets_keep_full_width():
    """Two sources renamed onto one target is a genuine DuplicateError.

    The planner must not pick one source and project the other away, which
    would replace the real error with a misleading missing-column failure.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'c', 'b': 'c'})\ndf = df.filter(pl.col('c') > 0)",
        ["c"],
    )

    assert ("source", "transform") not in projection.edge_demands
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_dynamic_rename_mapping_keeps_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename(mapping)\ndf = df.filter(pl.col('x') > 0)",
        ["x"],
    )

    assert ("source", "transform") not in projection.edge_demands
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_then_select_on_new_name_projects_pre_rename_columns():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'})\ndf = df.select('b', 'keep')",
        ["b", "keep"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "keep"})


def test_single_parent_polars_rename_then_with_columns_projects_pre_rename_inputs():
    projection = _single_parent_polars_plan(
        "df = df.rename({'raw': 'amount'})\n"
        "df = df.with_columns((pl.col('amount') * 2).alias('double'))",
        ["double", "amount"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"raw"})


def test_single_parent_polars_no_rename_union_path_unchanged():
    """Rename-free code keeps the established unordered-union demand result."""
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.filter(pl.col('flag'))",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "flag"})


def test_rename_node_then_downstream_filter_node_projects_pre_rename_upstream():
    """Across nodes: pre-rename name upstream, post-rename name downstream."""
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
                    "id": "filtered",
                    "data": {
                        "label": "filtered",
                        "nodeType": "polars",
                        "config": {"code": "df = df.filter(pl.col('premium') > 0)"},
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
                make_edge("renamed", "filtered").model_dump(),
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

    assert projection.edge_demands[("renamed", "filtered")] == frozenset({"premium", "quote_id"})
    assert projection.edge_demands[("source", "renamed")] == frozenset({"raw_premium", "quote_id"})


def test_single_parent_polars_derived_column_filter_projects_expression_inputs():
    """A filter on a with_columns-derived name must not re-add it upstream.

    The parent only has ``a`` and ``b``; demanding the derived ``m`` from it
    would hard-fail a perfectly valid derive->filter pipeline at execution.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})
    assert projection.needed_by_node["source"] == frozenset({"a", "b"})
    assert projection.diagnostics.edge_reasons[("source", "transform")].rule == (
        "polars_expression_dependency"
    )


def test_single_parent_polars_derived_keyword_column_filter_projects_expression_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns(m=pl.col('a') + pl.col('b'))\ndf = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_derived_of_derived_projects_root_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.with_columns((pl.col('m') * 2).alias('n'))\n"
        "df = df.filter(pl.col('n') > 0)",
        ["n"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_select_of_derived_projects_expression_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\ndf = df.select('m', 'a')",
        ["m", "a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_overwrite_same_name_keeps_single_demand():
    """Overwriting ``a`` from ``a`` then filtering keeps exactly ``{a}``.

    The demand must be neither dropped (the overwrite reads the parent's
    ``a``) nor widened by re-adding the produced name a second time.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.col('a').alias('a'))\ndf = df.filter(pl.col('a') > 0)",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a"})


def test_single_parent_polars_reference_before_production_still_demands_parent_column():
    """A filter that runs before the derive reads the parent's column.

    Ordered analysis must keep demanding ``m`` from the parent here; only
    references made after the production may be satisfied by the derive.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('m') > 0)\ndf = df.with_columns((pl.col('a') * 2).alias('m'))",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "m"})


def test_single_parent_polars_helper_assignment_keeps_union_narrowing():
    """Helper assignments stay on the union walk's established narrowing.

    The ordered extractor cannot prove this shape (the helper hides a call),
    so routing it anywhere but the union walk would widen ``{x, keep}`` to
    full width - a forbidden narrowing regression.
    """
    projection = _single_parent_polars_plan(
        "t = threshold()\ndf = df.filter(pl.col('x') > t)",
        ["x", "keep"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"x", "keep"})
    assert projection.needed_by_node["source"] == frozenset({"x", "keep"})


def test_single_parent_polars_select_subset_demands_inputs_of_every_select_output():
    """A select executes all of its output expressions, so all inputs are demanded.

    PIN REVISION (2.12c): the 2.12b pin asserted the union walk's ``{a}``
    here, which preserved a pre-existing loud failure - the node still
    executes ``select('a', 'b', 'c')`` verbatim, so projecting ``b`` and
    ``c`` off the parent edge broke a perfectly valid pipeline with
    ``ColumnNotFoundError`` at execution.  Provable select-bearing chains
    now route through the ordered extractor, which demands the inputs of
    every select output, not just the downstream-demanded subset.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "c"})
    assert projection.needed_by_node["source"] == frozenset({"a", "b", "c"})
    assert projection.diagnostics.edge_reasons[("source", "transform")].rule == (
        "polars_expression_dependency"
    )


def test_single_parent_polars_select_with_mixed_expression_args_demands_all_inputs():
    """Expression and plain-string select args both contribute their inputs."""
    projection = _single_parent_polars_plan(
        "df = df.select(pl.col('a'), 'b')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_select_with_aliased_expression_demands_all_inputs():
    """An un-demanded plain output next to an aliased one stays demanded.

    The node executes both select expressions, so the parent must provide
    ``b`` even though downstream only wants the derived ``m``.
    """
    projection = _single_parent_polars_plan(
        "df = df.select(pl.col('a').alias('m'), 'b')",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_select_seq_subset_demands_inputs_of_every_select_output():
    """``select_seq`` is ``select`` with sequential evaluation; same demand rule."""
    projection = _single_parent_polars_plan(
        "df = df.select_seq('a', 'b', 'c')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "c"})


def test_single_parent_polars_select_then_filter_demands_all_select_inputs():
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b', 'c')\ndf = df.filter(pl.col('a') > 0)",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "c"})


def test_single_parent_polars_filter_then_select_demands_filter_and_select_inputs():
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('x') > 0)\ndf = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "c", "x"})


def test_single_parent_polars_chained_selects_demand_first_select_inputs():
    """Backward propagation re-derives demand through each select namespace.

    The second select reads only ``a``, but the first still executes both
    of its outputs, so the parent must provide ``a`` and ``b``.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b')\ndf = df.select('a')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_unaliased_with_columns_then_select_keeps_union_demand():
    """Un-aliased ``with_columns`` outputs must never be demanded from the parent.

    ``name.suffix`` creates ``a_2`` under a name the output walker cannot
    see, so the ordered extractor must bail rather than let the in-node
    created name survive backward propagation into the parent demand.  The
    union walk's ``{a, b}`` keeps this today-working pipeline working: the
    parent supplies ``a`` and ``b``, the node derives ``a_2`` and selects
    it.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.col('a').name.suffix('_2'))\ndf = df.select('a_2', 'b')",
        ["b"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_unaliased_lit_then_select_excludes_literal_demand():
    """``pl.lit(1)`` creates the ``literal`` column in-node; never demand it.

    Today-working pipeline: the parent supplies ``b``, the node creates
    ``literal`` and selects it.  The ordered extractor cannot attribute the
    un-aliased output name, so it bails to the union walk's ``{b}``.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.lit(1))\ndf = df.select('literal', 'b')",
        ["b"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"b"})


def test_single_parent_polars_unaliased_sum_horizontal_then_select_excludes_sum_demand():
    """``pl.sum_horizontal`` creates the ``sum`` column in-node; never demand it.

    Today-working pipeline under demand ``{a, b, c}``: the union walk keeps
    ``{a, b, c}``, the node computes ``sum`` from the supplied ``a``/``b``
    and selects all four.  The ordered extractor cannot attribute the
    un-aliased output name (and the column walker is blind to
    ``sum_horizontal``'s string references), so it must bail rather than
    demand ``sum`` from a parent that never had it.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.sum_horizontal('a', 'b'))\ndf = df.select('sum', 'a', 'b', 'c')",
        ["a", "b", "c"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "c"})


def test_single_parent_polars_select_of_derived_subset_demand_projects_expression_inputs():
    """Select of a derived column keeps the ordered route under subset demand.

    Lock-in: this shape already reached the ordered extractor via the
    derived-reference predicate before 2.12c; the select-call trigger must
    not change its result.  The select executes both outputs, so the
    passthrough ``a`` stays demanded alongside the derive inputs.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\ndf = df.select('m', 'a')",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b"})


def test_single_parent_polars_select_demand_outside_outputs_keeps_full_width():
    """Demanding a column the select does not produce cannot be projected.

    Lock-in: the ordered extractor bails (the demand is not satisfiable by
    the select) and the union walk bails the same way, so full width lets
    execution raise the genuine missing-column error.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b')",
        ["z"],
    )

    assert ("source", "transform") not in projection.edge_demands
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_unprovable_select_keeps_loud_under_demand():
    """An unprovable select shape keeps today's union-walk under-demand.

    Deliberate: the branch makes the operation order unprovable, so the
    ordered extractor bails and the union walk's demand-intersected select
    handling is kept.  If the branch executes, the node fails loudly with
    the pre-existing ``ColumnNotFoundError`` rather than the planner
    widening shapes it cannot prove.
    """
    projection = _single_parent_polars_plan(
        "if True:\n    df = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a"})


def test_single_parent_polars_unprovable_select_seq_keeps_union_result():
    """An unprovable ``select_seq`` keeps the union walk's established result.

    Deliberate: the ordered extractor bails on the branch and the union walk
    does not model ``select_seq``, so the demand stays the filter input plus
    the downstream demand - exactly today's behaviour.  If the branch
    executes, the node fails loudly at execution; revising this requires a
    sanctioned pin revision, never a silent union-walk change.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('x') > 0)\nif True:\n    df = df.select_seq('a', 'b', 'c')",
        ["a"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "x"})


def test_single_parent_polars_unprovable_derived_reference_keeps_loud_over_demand():
    """An unprovable derived-reference shape keeps today's behaviour.

    Deliberate: the branch makes the operation order unprovable, so the
    union walk's over-demand of the derived ``m`` is kept.  Execution then
    fails loudly at the edge projection instead of the planner guessing a
    narrowing it cannot prove, or widening shapes the union walk narrows
    correctly today.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "if True:\n"
        "    df = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    assert projection.edge_demands[("source", "transform")] == frozenset({"a", "b", "m"})


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
