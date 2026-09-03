"""Public projection planner API tests."""

from __future__ import annotations

import json

import pytest

from haute._edge_join import narrow_join_parent_demand
from haute._execution_context import ExecutionProfile
from haute.errors import ContractMismatchError
from haute.graph_utils import NodeType
from haute.projection import (
    UNPROJECTED_STREAMING_BOUNDARY_RULE_NAME,
    AllExcept,
    ProjectionRequest,
    api_input_port_columns_by_node,
    builder_required_output_columns_by_node,
    explain,
    model_score_required_output_columns,
    plan,
    prepare_graph,
    projection_rule_coverage_by_node_type,
    source_scan_projection,
    validate_projection_rule_coverage,
)
from tests._projection_helpers import has_pair, pair_value, pair_value_or_none
from tests.conftest import make_edge, make_graph, make_output_config


def _public_output_definition(*, label: str = "public result") -> dict[str, object]:
    return {
        "definitionId": "definition_public_output",
        "file": "modules/public_output.py",
        "graph": {
            "nodes": [
                {
                    "id": "internal_result",
                    "data": {
                        "label": "private implementation result",
                        "nodeType": "polars",
                        "config": {},
                    },
                }
            ],
            "edges": [],
        },
        "inputPorts": [],
        "outputPorts": [
            {
                "portId": "opaque-output-id",
                "label": label,
                "source": {"nodeId": "internal_result", "handleId": None},
            }
        ],
    }


def test_projection_coverage_map_mentions_every_node_type() -> None:
    coverage = projection_rule_coverage_by_node_type()
    assert set(coverage) == set(NodeType)
    assert "polars_column_lineage" in coverage[NodeType.POLARS].rules
    validate_projection_rule_coverage()


def test_terminal_output_ignores_incomplete_enabled_mapping_rows() -> None:
    """Incomplete editor rows must not demand a blank upstream column."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {"label": "source", "nodeType": "dataInput", "config": {}},
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "enabled": True,
                                    "source_column": "quote_id",
                                    "output_path": "quote.id",
                                },
                                {
                                    "enabled": True,
                                    "source_column": "",
                                    "output_path": "quote.unfinished",
                                },
                            ]
                        },
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
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert projection.needed_by_node["out"] == {"quote_id"}
    assert all("" not in demand for demand in projection.needed_by_node.values() if demand)
    assert all("" not in demand for demand in projection.edge_demands.values() if demand)


def test_projection_rule_coverage_is_immutable() -> None:
    coverage = projection_rule_coverage_by_node_type()

    with pytest.raises(TypeError):
        coverage[NodeType.POLARS] = coverage[NodeType.DATA_INPUT]  # type: ignore[index]


def test_projection_rule_coverage_declares_opaque_node_types_explicitly() -> None:
    coverage = projection_rule_coverage_by_node_type()

    opaque_types = {node_type for node_type, entry in coverage.items() if entry.opaque}
    assert opaque_types == {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
    for node_type in opaque_types:
        assert coverage[node_type].rules == frozenset({"opaque_contract"})


def test_projection_resolves_collapsed_submodel_inputs_by_public_output_label() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "occurrence",
                    "type": "submodel",
                    "data": {
                        "label": "Occurrence presentation",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": "definition_public_output",
                            "alias": "unrelated_alias",
                        },
                    },
                },
                {
                    "id": "consumer",
                    "data": {
                        "label": "consumer",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = public_result.select(pl.col('premium'))",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "public-result-edge",
                    "source": "occurrence",
                    "target": "consumer",
                    "sourceHandle": "out__opaque-output-id",
                }
            ],
            "submodels": {
                "definition_public_output": _public_output_definition(),
            },
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="consumer",
            required_columns_by_node={"consumer": {"premium"}},
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    [edge_reason] = projection.diagnostics.edge_reasons.values()
    assert edge_reason.details["input_name"] == "public_result"


def test_live_switch_pruning_uses_collapsed_submodel_public_output_label() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "occurrence",
                    "type": "submodel",
                    "data": {
                        "label": "Occurrence presentation",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": "definition_public_output",
                            "alias": "unrelated_alias",
                        },
                    },
                },
                {
                    "id": "fallback",
                    "data": {
                        "label": "fallback result",
                        "nodeType": "dataInput",
                        "config": {},
                    },
                },
                {
                    "id": "switch",
                    "data": {
                        "label": "switch",
                        "nodeType": "liveSwitch",
                        "config": {
                            "input_scenario_map": {
                                "public_result": "live",
                                "fallback_result": "batch",
                            }
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "public-result-edge",
                    "source": "occurrence",
                    "target": "switch",
                    "sourceHandle": "out__opaque-output-id",
                },
                {
                    "id": "fallback-edge",
                    "source": "fallback",
                    "target": "switch",
                },
            ],
            "submodels": {
                "definition_public_output": _public_output_definition(),
            },
        }
    )

    prepared = prepare_graph(graph, "switch", source="live")

    assert [edge.id for edge in prepared.relevant_edges] == ["public-result-edge"]


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
            "data": {"label": "source", "nodeType": "dataInput", "config": {}},
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
                "config": make_output_config(["quote_id", "age_band"]),
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
                        "nodeType": "dataInput",
                        "config": {},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["quote_id", "left_value", "right_value"]),
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
                        "nodeType": "dataInput",
                        "config": {},
                    },
                },
                {
                    "id": "banding",
                    "data": {
                        "label": "banding",
                        "nodeType": "dataInput",
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["prediction"]),
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
    prepared = prepare_graph(graph, target_node_id)
    children_of: dict[str, list[str]] = {node_id: [] for node_id in prepared.order}
    for child_id, parent_ids in prepared.parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children_of:
                children_of[parent_id].append(child_id)
    return prepared.node_map, prepared.order, children_of


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
    assert pair_value(projection.edge_demands, "left", "joined") == frozenset(
        {"quote_id", "left_value"}
    )
    assert pair_value(projection.edge_demands, "right", "joined") == frozenset(
        {"quote_id", "right_value"}
    )
    assert isinstance(projection.opaque_boundaries, frozenset)


def _edge_join_graph(*, keys: dict[str, object], base_outputs, join_outputs):
    """Build a base/join edge-join graph with concrete-contract parents."""
    join_config: dict[str, object] = {
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
                        "config": make_output_config([]),
                    },
                },
            ],
            "edges": [
                make_edge("base", "joined", target_handle="base").model_dump(),
                make_edge("join", "joined", target_handle="join").model_dump(),
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
    assert pair_value(projection.edge_demands, "base", "joined") == frozenset(
        {"quote_id", "policy_id"}
    )
    assert pair_value(projection.edge_demands, "join", "joined") == frozenset(
        {"quote_id", "competitor_premium"}
    )
    assert (
        pair_value(projection.diagnostics.edge_reasons, "join", "joined").rule == "edge_join_fan_in"
    )


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
    assert "x" in pair_value(projection.edge_demands, "join", "joined")
    assert "x" in pair_value(projection.edge_demands, "base", "joined")
    assert "quote_id" in pair_value(projection.edge_demands, "join", "joined")


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
    assert narrow_join_parent_demand({"a"}, how="right", suffix="_right", **base) is None
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
    assert pair_value(projection.edge_demands, "base", "joined") == frozenset(
        {"base_key", "policy_id"}
    )
    assert pair_value(projection.edge_demands, "join", "joined") == frozenset(
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
                                "nodeType": "dataInput",
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
                                    "how": "left",
                                    "on": ["quote_id"],
                                    "contract": "opaque",
                                },
                            },
                        },
                        {
                            "id": "out",
                            "data": {
                                "label": "out",
                                "nodeType": "output",
                                "config": make_output_config([]),
                            },
                        },
                    ],
                    "edges": [
                        make_edge("base", "joined", target_handle="base").model_dump(),
                        make_edge("join", "joined", target_handle="join").model_dump(),
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


def test_edge_join_projection_keeps_parallel_frames_from_one_source_distinct():
    """Two API frames on separate role edges must not collapse to one parent demand."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "request",
                    "data": {
                        "label": "Quote_Input_1",
                        "nodeType": "apiInput",
                        "config": {
                            "contract": "opaque",
                            "tables": [
                                {"label": "quote_info", "path": "$[:]", "emit": True},
                                {"label": "competitor", "path": "$.competitor[:]", "emit": True},
                            ],
                        },
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "on": ["quote_id"],
                            "contract": "opaque",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_quote_base",
                    "source": "request",
                    "sourceHandle": "quote_info",
                    "target": "joined",
                    "targetHandle": "base",
                },
                {
                    "id": "e_competitor_join",
                    "source": "request",
                    "sourceHandle": "competitor",
                    "target": "joined",
                    "targetHandle": "join",
                },
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"joined": {"quote_id", "competitor_premium"}},
        )
    )

    # Node-level contracts cannot safely narrow two distinct frames from one
    # parent. Both physical role edges therefore remain full-width, but retain
    # their own identities and edge-join provenance.
    for edge in graph.edges:
        assert projection.demand_for_edge(edge) is None
        reason = projection.reason_for_edge(edge)
        assert reason is not None
        assert reason.rule == "edge_join_fan_in"
    assert projection.needed_by_node["request"] is None


def test_ratebook_optimiser_projection_keeps_parallel_source_frames_distinct():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "request",
                    "data": {
                        "label": "Quote_Input_1",
                        "nodeType": "apiInput",
                        "config": {
                            "contract": "opaque",
                            "tables": [
                                {"label": "quote_info", "path": "$[:].quotes", "emit": True},
                                {"label": "rating_factors", "path": "$[:].factors", "emit": True},
                            ],
                        },
                    },
                },
                {
                    "id": "optimiser",
                    "data": {
                        "label": "Optimiser",
                        "nodeType": "optimiser",
                        "config": {
                            "mode": "ratebook",
                            "objective": "expected_income",
                            "constraints": {"volume": {"min": 0.9}},
                            "factor_columns": [["territory"]],
                            "data_input": "quote_info",
                            "banding_source": "rating_factors",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_quotes",
                    "source": "request",
                    "sourceHandle": "quote_info",
                    "target": "optimiser",
                },
                {
                    "id": "e_factors",
                    "source": "request",
                    "sourceHandle": "rating_factors",
                    "target": "optimiser",
                },
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="optimiser",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={
                "optimiser": {
                    "quote_id",
                    "scenario_index",
                    "scenario_value",
                    "expected_income",
                    "volume",
                }
            },
        )
    )

    for edge in graph.edges:
        assert projection.demand_for_edge(edge) is None
        reason = projection.reason_for_edge(edge)
        assert reason is not None
        assert reason.rule == "optimiser_parent_demand"
    assert projection.needed_by_node["request"] is None


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
    assert (
        pair_value(fan_in_projection.diagnostics.edge_reasons, "right", "joined").rule
        == "polars_fan_in"
    )
    assert (
        pair_value(ratebook_projection.diagnostics.edge_reasons, "scored", "ratebook_opt").rule
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
    from haute._execution_admission import create_admitted_execution_context
    from haute._native_memory_limit import native_memory_backend_scope
    from haute.execution import plan_execution_strategy

    # The fan-in node joins, which EXEC-P07 admits as a materialisation
    # boundary, so the plan needs an admitted context; the sources are not
    # readable here, so a hard worker cap supplies the bounded envelope.
    context = create_admitted_execution_context(
        operation="test_projection_facade",
        profile=ExecutionProfile.LAZY_SINK,
    )
    request = ProjectionRequest(
        graph=_fan_in_graph(declared_parent_inputs=False),
        target_node_id="out",
        profile=ExecutionProfile.LAZY_SINK,
        required_columns_by_node={"out": {"quote_id", "left_value"}},
    )

    with native_memory_backend_scope("rlimit"):
        projection = plan_execution_strategy(request, execution_context=context)

    assert context.projection_plan is projection
    diagnostics = context.projection_plan.projection_plan.diagnostics_payload(
        profile=context.projection_plan.profile
    )
    assert diagnostics["strategy_summary"]["profile"] == "lazy_sink"


def test_projection_diagnostics_payload_exposes_strategy_reasons_for_broad_and_all_except():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["margin"]),
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

    assert pair_value(projection.edge_demands, "source", "features") == frozenset(
        {"premium", "burn_cost"}
    )
    assert pair_value(projection.diagnostics.edge_reasons, "source", "features").rule == (
        "polars_column_lineage"
    )


def test_empty_declared_polars_contract_does_not_mask_expression_dependency():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "features",
                    "data": {
                        "label": "features",
                        "nodeType": "polars",
                        "config": {
                            "code": ("df = source.with_columns(burn_cost=pl.col('premium') * 0.7)"),
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["quote_id", "burn_cost"]),
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
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={"out": {"quote_id", "burn_cost"}},
        )
    )

    assert projection.needed_by_node["source"] == frozenset({"quote_id", "premium"})
    assert pair_value(projection.edge_demands, "source", "features") == frozenset(
        {"quote_id", "premium"}
    )
    assert pair_value(projection.diagnostics.edge_reasons, "source", "features").rule == (
        "polars_column_lineage"
    )


def test_empty_declared_scenario_contract_keeps_structural_outputs():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "expand",
                    "data": {
                        "label": "expand",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "column_name": "premium_multiplier",
                            "step_column": "scenario_index",
                            "selected_columns": [
                                "quote_id",
                                "premium",
                                "premium_multiplier",
                                "scenario_index",
                            ],
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(
                            [
                                "quote_id",
                                "premium",
                                "premium_multiplier",
                                "scenario_index",
                            ]
                        ),
                    },
                },
            ],
            "edges": [
                make_edge("source", "expand").model_dump(),
                make_edge("expand", "out").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={
                "out": {"quote_id", "premium", "premium_multiplier", "scenario_index"}
            },
        )
    )

    assert projection.needed_by_node["source"] == frozenset({"quote_id", "premium"})


def test_stale_empty_contracts_do_not_project_optimiser_outputs_into_edge_join():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "join_premiums",
                    "data": {
                        "label": "join_premiums",
                        "nodeType": "edgeJoin",
                        "config": {},
                    },
                },
                {
                    "id": "sale_flag",
                    "data": {
                        "label": "sale_flag",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = join_premiums.with_columns(burn_cost=pl.col('premium') * 0.7)"
                            ),
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
                {
                    "id": "premium",
                    "data": {
                        "label": "premium",
                        "nodeType": "scenarioExpander",
                        "config": {
                            "column_name": "premium_multiplier",
                            "step_column": "scenario_index",
                            "code": (
                                "df = sale_flag.with_columns("
                                "premium=pl.col('premium') * pl.col('premium_multiplier'))"
                            ),
                            "selected_columns": [
                                "quote_id",
                                "premium",
                                "burn_cost",
                                "premium_multiplier",
                                "scenario_index",
                            ],
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
                {
                    "id": "conversion_scoring",
                    "data": {
                        "label": "conversion_scoring",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = premium.with_columns(conversion_prediction=pl.lit(0.5))"
                            ),
                            "contract": {
                                "inputs": [],
                                "outputs": ["conversion_prediction"],
                            },
                        },
                    },
                },
                {
                    "id": "optimiser_input",
                    "data": {
                        "label": "optimiser_input",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = conversion_scoring.with_columns("
                                "expected_margin=pl.col('premium') - pl.col('burn_cost'))"
                            ),
                            "contract": {
                                "inputs": ["premium", "burn_cost", "conversion_prediction"],
                                "outputs": ["expected_margin"],
                            },
                        },
                    },
                },
            ],
            "edges": [
                make_edge("join_premiums", "sale_flag").model_dump(),
                make_edge("sale_flag", "premium").model_dump(),
                make_edge("premium", "conversion_scoring").model_dump(),
                make_edge("conversion_scoring", "optimiser_input").model_dump(),
            ],
        }
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="optimiser_input",
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node={
                "optimiser_input": {
                    "quote_id",
                    "scenario_index",
                    "premium_multiplier",
                    "conversion_prediction",
                    "expected_margin",
                }
            },
        )
    )

    assert projection.needed_by_node["join_premiums"] is None
    assert "join_premiums" in projection.opaque_boundaries


def test_single_parent_polars_filter_keeps_predicate_dependencies():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["premium"]),
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

    assert pair_value(projection.edge_demands, "source", "filtered") == frozenset(
        {"premium", "segment"}
    )


def test_single_parent_polars_keyword_filter_keeps_constraint_column():
    """``df.filter(segment='A')`` names ``segment`` via the kwarg, not ``pl.col``.

    The unordered demand walk (no rename/select) must still carry the keyword
    constraint column upstream; otherwise the projection prunes ``segment`` from
    the parent even though the filter reads it. Regression for the unordered
    filter branch omitting keyword-constraint columns.
    """
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "filtered",
                    "data": {
                        "label": "filtered",
                        "nodeType": "polars",
                        "config": {"code": "df = df.filter(segment='A')"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["premium"]),
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

    assert pair_value(projection.edge_demands, "source", "filtered") == frozenset(
        {"premium", "segment"}
    )


def test_single_parent_polars_keyword_filter_double_star_stays_full_width():
    """``df.filter(**constraints)`` cannot be proven, so the parent stays full width.

    A ``**kwargs`` filter has a ``None`` kwarg name; the walk must bail rather
    than silently narrow the parent demand.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(**constraints)",
        ["premium"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_to_new_target_keeps_full_width():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["premium", "quote_id"]),
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

    assert not has_pair(projection.edge_demands, "source", "renamed")
    assert projection.needed_by_node["source"] is None


def _single_parent_polars_plan(code: str, fields: list[str]):
    """Plan ``source -> transform(code) -> out(fields)`` under a strict profile."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(fields),
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


def test_single_parent_polars_rename_then_filter_on_new_name_keeps_full_width():
    """A new rename target could collide with an unchanged upstream column.

    Without schema proof that ``premium`` is absent upstream, full width keeps
    Polars' real DuplicateError behavior intact for frames that already have
    both ``raw_premium`` and ``premium``.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'raw_premium': 'premium'})\ndf = df.filter(pl.col('premium') > 0)",
        ["premium", "quote_id"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_chained_new_target_renames_keep_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'}).filter(pl.col('b') > 0).rename({'b': 'c'})",
        ["c", "keep"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_multiple_new_target_renames_keep_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'x', 'b': 'y'})\ndf = df.filter(pl.col('x') > 0)",
        ["x", "y"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_to_same_name_is_a_no_op():
    projection = _single_parent_polars_plan(
        "df = df.rename({'premium': 'premium'})\ndf = df.filter(pl.col('premium') > 0)",
        ["premium"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"premium"})


def test_single_parent_polars_swap_renames_resolve_simultaneously():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b', 'b': 'a'})\ndf = df.filter(pl.col('b') > 0)",
        ["a", "b"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_rename_collision_keeps_pre_rename_reference_demand():
    """A pre-rename reference to the collision target must stay demanded.

    Both ``a`` and ``b`` reach the node, so the genuine Polars DuplicateError
    still surfaces at execution instead of being masked by projection.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('b') > 0)\ndf = df.rename({'a': 'b'})",
        ["b"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_target_collision_keeps_full_width():
    """An existing target column collision must not be hidden by pruning.

    With input columns ``a``, ``b``, and ``c``, Polars raises DuplicateError
    for ``rename({'a': 'b'})`` before the following select executes. Projecting
    the parent to only ``a`` and ``c`` would remove the original ``b`` and
    change the program outcome.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'})\ndf = df.select('c')",
        ["c"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_demand_for_renamed_away_column_keeps_full_width():
    """Demanding a name the rename removed cannot be projected coherently.

    Full width lets execution raise the genuine missing-column error instead
    of the planner guessing a projection that changes the failure shape.
    """
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'})",
        ["a", "b"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
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

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_dynamic_rename_mapping_keeps_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename(mapping)\ndf = df.filter(pl.col('x') > 0)",
        ["x"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_then_select_on_new_name_keeps_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename({'a': 'b'})\ndf = df.select('b', 'keep')",
        ["b", "keep"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_then_with_columns_keeps_full_width():
    projection = _single_parent_polars_plan(
        "df = df.rename({'raw': 'amount'})\n"
        "df = df.with_columns((pl.col('amount') * 2).alias('double'))",
        ["double", "amount"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_rename_keeps_unused_rename_source_for_execution():
    projection = _single_parent_polars_plan(
        "df = df.rename({'customer_id': 'cid'})\n"
        "df = df.with_columns((pl.col('premium') * 2).alias('p2'))",
        ["p2"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_no_rename_union_path_unchanged():
    """Rename-free code keeps the established unordered-union demand result."""
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.filter(pl.col('flag'))",
        ["m"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset(
        {"a", "b", "flag"}
    )


def test_rename_node_then_downstream_filter_node_keeps_rename_parent_full_width():
    """Across nodes, the downstream filter can narrow but the rename parent stays full-width."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["premium", "quote_id"]),
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

    assert pair_value(projection.edge_demands, "renamed", "filtered") == frozenset(
        {"premium", "quote_id"}
    )
    assert not has_pair(projection.edge_demands, "source", "renamed")
    assert projection.needed_by_node["source"] is None


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

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})
    assert projection.needed_by_node["source"] == frozenset({"a", "b"})
    assert pair_value(projection.diagnostics.edge_reasons, "source", "transform").rule == (
        "polars_column_lineage"
    )


def test_single_parent_polars_derived_keyword_column_filter_projects_expression_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns(m=pl.col('a') + pl.col('b'))\ndf = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_derived_of_derived_projects_root_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.with_columns((pl.col('m') * 2).alias('n'))\n"
        "df = df.filter(pl.col('n') > 0)",
        ["n"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_select_of_derived_projects_expression_inputs():
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\ndf = df.select('m', 'a')",
        ["m", "a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_overwrite_same_name_keeps_single_demand():
    """Overwriting ``a`` from ``a`` then filtering keeps exactly ``{a}``.

    The demand must be neither dropped (the overwrite reads the parent's
    ``a``) nor widened by re-adding the produced name a second time.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.col('a').alias('a'))\ndf = df.filter(pl.col('a') > 0)",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a"})


def test_single_parent_polars_reference_before_production_still_demands_parent_column():
    """A filter that runs before the derive reads the parent's column.

    Ordered analysis must keep demanding ``m`` from the parent here; only
    references made after the production may be satisfied by the derive.
    """
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('m') > 0)\ndf = df.with_columns((pl.col('a') * 2).alias('m'))",
        ["m"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "m"})


def test_single_parent_polars_helper_call_keeps_visible_full_width_boundary():
    """A dynamic helper is not guessed through by the closed lineage model."""
    projection = _single_parent_polars_plan(
        "t = threshold()\ndf = df.filter(pl.col('x') > t)",
        ["x", "keep"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None
    reason = pair_value(projection.diagnostics.edge_reasons, "source", "transform")
    assert reason.rule == "polars_lineage_unsupported"
    assert reason.details == {"reason": "dynamic_helper", "operation": None}


def test_single_parent_polars_named_root_derive_select_narrows_parent_demand():
    """A first chain rooted at the named input is as projectable as ``df``."""
    projection = _single_parent_polars_plan(
        "df = source.with_columns("
        "(pl.col('raw_premium') * 2).alias('premium')"
        ").select('premium', 'quote_id')",
        ["premium", "quote_id"],
    )

    assert projection.needed_by_node["source"] == frozenset({"raw_premium", "quote_id"})
    assert pair_value(projection.edge_demands, "source", "transform") == frozenset(
        {"raw_premium", "quote_id"}
    )


def test_single_parent_polars_select_subset_demands_inputs_of_every_select_output():
    """A select executes all of its output expressions, so all inputs are demanded.

    The lineage transfer therefore retains ``b`` and ``c`` even though the
    downstream output only asks for ``a``.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b", "c"})
    assert projection.needed_by_node["source"] == frozenset({"a", "b", "c"})
    assert pair_value(projection.diagnostics.edge_reasons, "source", "transform").rule == (
        "polars_column_lineage"
    )


def test_single_parent_polars_select_with_mixed_expression_args_demands_all_inputs():
    """Expression and plain-string select args both contribute their inputs."""
    projection = _single_parent_polars_plan(
        "df = df.select(pl.col('a'), 'b')",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_select_with_aliased_expression_demands_all_inputs():
    """An un-demanded plain output next to an aliased one stays demanded.

    The node executes both select expressions, so the parent must provide
    ``b`` even though downstream only wants the derived ``m``.
    """
    projection = _single_parent_polars_plan(
        "df = df.select(pl.col('a').alias('m'), 'b')",
        ["m"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_select_seq_subset_demands_inputs_of_every_select_output():
    """``select_seq`` is ``select`` with sequential evaluation; same demand rule."""
    projection = _single_parent_polars_plan(
        "df = df.select_seq('a', 'b', 'c')",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b", "c"})


def test_single_parent_polars_select_then_filter_demands_all_select_inputs():
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b', 'c')\ndf = df.filter(pl.col('a') > 0)",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b", "c"})


def test_single_parent_polars_filter_then_select_demands_filter_and_select_inputs():
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('x') > 0)\ndf = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset(
        {"a", "b", "c", "x"}
    )


def test_single_parent_polars_chained_selects_demand_first_select_inputs():
    """Backward propagation re-derives demand through each select namespace.

    The second select reads only ``a``, but the first still executes both
    of its outputs, so the parent must provide ``a`` and ``b``.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b')\ndf = df.select('a')",
        ["a"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_unaliased_with_columns_tracks_structural_output_name():
    """Un-aliased ``with_columns`` outputs must never be demanded from the parent.

    The structural naming transfer knows that ``name.suffix`` creates
    ``a_2`` from ``a``, so only the real parent inputs ``a`` and ``b`` remain.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.col('a').name.suffix('_2'))\ndf = df.select('a_2', 'b')",
        ["b"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_unaliased_lit_then_select_excludes_literal_demand():
    """``pl.lit(1)`` creates the ``literal`` column in-node; never demand it.

    The lineage model records Polars' unaliased literal output name, leaving
    only the passthrough ``b`` as a parent dependency.
    """
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.lit(1))\ndf = df.select('literal', 'b')",
        ["b"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"b"})


def test_single_parent_polars_unaliased_sum_horizontal_uses_first_input_name():
    """Polars names an unaliased horizontal expression after its first input."""
    projection = _single_parent_polars_plan(
        "df = df.with_columns(pl.sum_horizontal('a', 'b'))\ndf = df.select('a', 'b', 'c')",
        ["a", "b", "c"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b", "c"})


def test_single_parent_polars_select_of_derived_subset_demand_projects_expression_inputs():
    """A derived select routes back to its inputs and executed passthrough."""
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\ndf = df.select('m', 'a')",
        ["m"],
    )

    assert pair_value(projection.edge_demands, "source", "transform") == frozenset({"a", "b"})


def test_single_parent_polars_select_demand_outside_outputs_keeps_full_width():
    """Demanding a column the select does not produce cannot be projected.

    The closed lineage proof rejects the unsatisfied output demand, so full
    width preserves Polars' authoritative missing-column error.
    """
    projection = _single_parent_polars_plan(
        "df = df.select('a', 'b')",
        ["z"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_unprovable_select_fails_closed():
    """Control-flow-dependent selection retains a visible full-width edge."""
    projection = _single_parent_polars_plan(
        "if True:\n    df = df.select('a', 'b', 'c')",
        ["a"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None
    reason = pair_value(projection.diagnostics.edge_reasons, "source", "transform")
    assert reason.details == {"reason": "non_linear_control_flow", "operation": "If"}


def test_single_parent_polars_unprovable_select_seq_fails_closed():
    """A supported operation inside control flow is still not guessed through."""
    projection = _single_parent_polars_plan(
        "df = df.filter(pl.col('x') > 0)\nif True:\n    df = df.select_seq('a', 'b', 'c')",
        ["a"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_unprovable_derived_reference_fails_closed():
    """Derived-column use behind control flow keeps the whole parent schema."""
    projection = _single_parent_polars_plan(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "if True:\n"
        "    df = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    assert not has_pair(projection.edge_demands, "source", "transform")
    assert projection.needed_by_node["source"] is None


def test_single_parent_polars_group_by_projects_literal_keys_and_aggregate_inputs():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["premium"]),
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

    assert pair_value(projection.edge_demands, "source", "agg") == frozenset({"segment", "premium"})
    assert projection.needed_by_node["source"] == frozenset({"segment", "premium"})
    assert pair_value(projection.diagnostics.edge_reasons, "source", "agg").rule == (
        "polars_column_lineage"
    )


def test_single_parent_polars_group_by_with_dynamic_key_stays_full_width():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                                "key = 'segment'\n"
                                "df = df.group_by(key).agg("
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
                        "config": make_output_config(["premium"]),
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

    assert pair_value_or_none(projection.edge_demands, "source", "agg") is None
    assert "source" in projection.opaque_boundaries


def _api_join_select_fan_in_graph(*, join_key: str = '"quote_id"'):
    return make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "Quote_Input_1",
                        "nodeType": "apiInput",
                        "config": {
                            "contract": "opaque",
                            "tables": [
                                {
                                    "label": "quote_info",
                                    "path": "$[:]",
                                    "emit": True,
                                    "columns": [
                                        {"name": name, "selected": True}
                                        for name in (
                                            "quote_id",
                                            "cover_type",
                                            "channel",
                                            "date_of_birth",
                                            "ncd_years",
                                            "unused_quote_column",
                                        )
                                    ],
                                },
                                {
                                    "label": "proposer_claims",
                                    "path": "$[:].proposer.claims[:]",
                                    "emit": True,
                                    "columns": [
                                        {"name": name, "selected": True}
                                        for name in (
                                            "quote_id",
                                            "fault",
                                            "amount_paid",
                                            "unused_claim_column",
                                        )
                                    ],
                                },
                            ],
                        },
                    },
                },
                {
                    "id": "claims_agg",
                    "data": {
                        "label": "claims_agg",
                        "nodeType": "polars",
                        "config": {
                            "contract": "opaque",
                            "code": (
                                "df = proposer_claims\n"
                                "df = df.group_by('quote_id').agg(\n"
                                "    pl.len().alias('total_claims'),\n"
                                "    (pl.col('fault') == 'at_fault').sum().alias("
                                "'total_fault_claims'),\n"
                                "    pl.col('amount_paid').sum().alias('total_incurred'),\n"
                                ")"
                            ),
                        },
                    },
                },
                {
                    "id": "quote_features",
                    "data": {
                        "label": "quote_features",
                        "nodeType": "polars",
                        "config": {
                            "contract": "opaque",
                            "code": (
                                "df = quote_info\n"
                                f"df = claims_agg.join(quote_info, on={join_key})"
                                ".with_columns(\n"
                                "    (pl.col('date_of_birth').str.to_date('%Y-%m-%d')"
                                ".dt.year() - 1970).alias('proposer_age')\n"
                                ").select([\n"
                                "    'quote_id', 'cover_type', 'channel', 'proposer_age',\n"
                                "    'ncd_years', 'total_claims',\n"
                                "])"
                            ),
                        },
                    },
                },
            ],
            "edges": [
                make_edge("api", "claims_agg", source_handle="proposer_claims").model_dump(),
                make_edge("api", "quote_features", source_handle="quote_info").model_dump(),
                make_edge("claims_agg", "quote_features").model_dump(),
            ],
        }
    )


def test_api_input_port_projection_crosses_provable_polars_join_select_fan_in():
    """Regression: quote_features must not make its wide quote_info port opaque."""
    graph = _api_join_select_fan_in_graph()
    prepared = prepare_graph(graph, "quote_features")
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="quote_features",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert projection.needed_by_node["quote_features"] == frozenset(
        {
            "quote_id",
            "cover_type",
            "channel",
            "proposer_age",
            "ncd_years",
            "total_claims",
        }
    )
    assert projection.diagnostics.node_reasons["quote_features"].rule == ("polars_column_lineage")
    assert pair_value(projection.edge_demands, "claims_agg", "quote_features") == frozenset(
        {"quote_id", "total_claims"}
    )
    assert pair_value(projection.edge_demands, "api", "quote_features") == frozenset(
        {"quote_id", "cover_type", "channel", "date_of_birth", "ncd_years"}
    )
    assert pair_value(projection.diagnostics.edge_reasons, "api", "quote_features").rule == (
        "polars_column_lineage"
    )
    assert api_input_port_columns_by_node(
        prepared.node_map,
        prepared.relevant_edges,
        projection,
    ) == {
        "api": {
            "proposer_claims": frozenset({"quote_id", "fault", "amount_paid"}),
            "quote_info": frozenset(
                {"quote_id", "cover_type", "channel", "date_of_birth", "ncd_years"}
            ),
        }
    }
    assert projection.opaque_boundaries == frozenset()


def test_api_input_port_projection_resolves_join_rooted_at_seeded_df():
    """Generated ``df = input`` boilerplate may be the join's live left operand."""
    graph = _api_join_select_fan_in_graph()
    graph.node_map["quote_features"].data.config["code"] = (
        "df = quote_info\n"
        "df = df.join(claims_agg, on='quote_id')"
        ".with_columns(\n"
        "    (pl.col('date_of_birth').str.to_date('%Y-%m-%d')"
        ".dt.year() - 1970).alias('proposer_age')\n"
        ").select([\n"
        "    'quote_id', 'cover_type', 'channel', 'proposer_age',\n"
        "    'ncd_years', 'total_claims',\n"
        "])"
    )

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="quote_features",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert pair_value(projection.edge_demands, "claims_agg", "quote_features") == frozenset(
        {"quote_id", "total_claims"}
    )
    assert pair_value(projection.edge_demands, "api", "quote_features") == frozenset(
        {"quote_id", "cover_type", "channel", "date_of_birth", "ncd_years"}
    )
    assert "quote_features" not in projection.opaque_boundaries


def test_api_input_port_projection_keeps_dynamic_polars_join_full_width():
    graph = _api_join_select_fan_in_graph(join_key="join_key")
    prepared = prepare_graph(graph, "quote_features")
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="quote_features",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert not has_pair(projection.edge_demands, "api", "quote_features")
    assert (
        api_input_port_columns_by_node(
            prepared.node_map,
            prepared.relevant_edges,
            projection,
        )["api"]["quote_info"]
        is None
    )
    assert "api" in projection.opaque_boundaries


def test_polars_join_projection_rejects_a_coalesced_right_key_as_output():
    """Default joins consume a distinct right key but do not emit it."""
    graph = _api_join_select_fan_in_graph()
    graph.node_map["quote_features"].data.config["code"] = (
        "df = claims_agg.join("
        "quote_info, left_on='quote_id', right_on='channel'"
        ").select(['channel'])"
    )
    prepared = prepare_graph(graph, "quote_features")

    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="quote_features",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert not has_pair(projection.edge_demands, "api", "quote_features")
    assert (
        api_input_port_columns_by_node(
            prepared.node_map,
            prepared.relevant_edges,
            projection,
        )["api"]["quote_info"]
        is None
    )
    assert "quote_features" in projection.opaque_boundaries


def test_api_input_port_demands_union_consumers_and_project_exact_terminal_port():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "api",
                        "nodeType": "apiInput",
                        "config": {
                            "path": "data.json",
                            "tables": [
                                {
                                    "label": "claims",
                                    "path": "$[:].claims[:]",
                                    "emit": True,
                                    "columns": [
                                        {"name": "quote_id", "selected": True},
                                        {"name": "fault", "selected": True},
                                    ],
                                },
                                {
                                    "label": "quotes",
                                    "path": "$[:]",
                                    "emit": True,
                                    "columns": [{"name": "postcode", "selected": True}],
                                },
                            ],
                        },
                    },
                },
                {
                    "id": "by_quote",
                    "data": {
                        "label": "by_quote",
                        "nodeType": "polars",
                        "config": {"code": "df = claims.group_by('quote_id').agg()"},
                    },
                },
                {
                    "id": "by_fault",
                    "data": {
                        "label": "by_fault",
                        "nodeType": "polars",
                        "config": {"code": "df = claims.group_by('fault').agg()"},
                    },
                },
                {
                    "id": "opaque_quotes",
                    "data": {
                        "label": "opaque_quotes",
                        "nodeType": "polars",
                        "config": {"code": "df = quotes.sort('postcode')"},
                    },
                },
            ],
            "edges": [
                make_edge("api", "by_quote", source_handle="claims").model_dump(),
                make_edge("api", "by_fault", source_handle="claims").model_dump(),
                make_edge("api", "opaque_quotes", source_handle="quotes").model_dump(),
            ],
        }
    )
    prepared = prepare_graph(graph)
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id=None,
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert api_input_port_columns_by_node(
        prepared.node_map,
        prepared.relevant_edges,
        projection,
    ) == {
        "api": {
            "claims": frozenset({"quote_id", "fault"}),
            "quotes": frozenset({"postcode"}),
        }
    }


def test_api_input_port_demand_outside_declared_schema_stays_visible_and_full_width():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "api",
                        "nodeType": "apiInput",
                        "config": {
                            "path": "data.json",
                            "tables": [
                                {
                                    "label": "claims",
                                    "path": "$[:].claims[:]",
                                    "emit": True,
                                    "columns": [{"name": "quote_id", "selected": True}],
                                }
                            ],
                        },
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": "df = claims.group_by('missing').agg()"},
                    },
                },
            ],
            "edges": [make_edge("api", "agg", source_handle="claims").model_dump()],
        }
    )
    prepared = prepare_graph(graph)
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id=None,
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert projection.needed_by_node["api"] is None
    assert "api" in projection.opaque_boundaries
    assert api_input_port_columns_by_node(
        prepared.node_map,
        prepared.relevant_edges,
        projection,
    ) == {"api": {"claims": None}}


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
    assert not has_pair(projection.edge_demands, "left", "joined")
    assert not has_pair(projection.edge_demands, "right", "joined")
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


def test_right_join_missing_parent_contract_does_not_route_left_column_to_right_parent():
    """Row preservation is not column provenance for right joins."""
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "dataInput",
                        "config": {"contract": {"inputs": [], "outputs": ["k", "left_value"]}},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "dataInput",
                        "config": {"contract": {"inputs": [], "outputs": ["k", "right_value"]}},
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='k', how='right')",
                            "contract": {
                                "inputs": ["k", "right_value"],
                                "outputs": ["k", "right_value"],
                                "inputs_by_parent": {
                                    "left": ["k"],
                                    "right": ["k", "right_value"],
                                },
                            },
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["left_value"]),
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

    with pytest.raises(ContractMismatchError, match="does not cover columns"):
        plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="out",
                profile=ExecutionProfile.LAZY_SINK,
            )
        )


def test_public_projection_plan_strict_profile_projects_simple_user_code():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["a"]),
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
    assert pair_value(projection.diagnostics.edge_reasons, "source", "custom").rule == (
        "polars_column_lineage"
    )


def test_public_projection_plan_strict_profile_boundaries_terminal_user_code():
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
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
                        "nodeType": "dataOutput",
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["a"]),
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["quote_id", "premium"]),
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["quote_id"]),
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["b"]),
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
                        "nodeType": "dataInput",
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

    assert pair_value(projection.edge_demands, "source", "ratebook_opt") == frozenset(
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
    assert pair_value(projection.diagnostics.edge_reasons, "source", "ratebook_opt").rule == (
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
                        "nodeType": "dataInput",
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
                        "config": make_output_config(["quote_id"]),
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


def test_public_projection_plan_preview_profile_preserves_opaque_propagation():
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

    assert pair_value(projection.edge_demands, "scored", "ratebook_opt") == frozenset(required)
    assert pair_value(projection.edge_demands, "banding", "ratebook_opt") == frozenset(
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
    assert (
        model_score_required_output_columns(
            {"code": "df = df", "selected_columns": ["quote_id"]},
            {"prediction"},
        )
        is None
    )
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


# ----------------------------------------------- EXEC-P07 chained boundaries


def _chained_boundary_sequences(code: str):
    from haute._types import GraphNode, NodeData, NodeType
    from haute.projection import (
        materialising_operator_sequences_by_input_names,
    )

    node = GraphNode(
        id="op",
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(label="op", nodeType=NodeType.POLARS, config={"code": code}),
    )
    return materialising_operator_sequences_by_input_names(["op"], {"op": node}, {"op": ["src"]})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("df = src.unique(subset=['k']).reverse()", ("unique", "reverse")),
        ("df = src.reverse().unique(subset=['k'])", ("reverse", "unique")),
        ("df = src.sort('a').unique(subset=['k']).reverse()", ("sort", "unique", "reverse")),
    ],
)
def test_chained_boundaries_are_recorded_in_evaluation_order(
    code: str,
    expected: tuple[str, ...],
) -> None:
    """Chained calls share a source position, so order must come from evaluation.

    Sorting by ``(lineno, col_offset)`` tied every call in one chain and left the
    operator to a lexical tie-break, which named ``reverse`` for
    ``unique(...).reverse()``.
    """
    assert dict(_chained_boundary_sequences(code)) == {"op": expected}


def test_chained_boundary_diagnostic_names_the_first_operator_evaluated() -> None:
    from haute.projection import first_materialising_operators

    for code, first in (
        ("df = src.unique(subset=['k']).reverse()", "unique"),
        ("df = src.reverse().unique(subset=['k'])", "reverse"),
    ):
        sequences = _chained_boundary_sequences(code)
        assert dict(first_materialising_operators(sequences)) == {"op": first}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("df = left.join(right.sort('a'), on='k')", ("sort", "join")),
        ("df = left.join(right.unique(subset=['k']), on='k')", ("unique", "join")),
        (
            "df = left.sort('a').join(right.unique(subset=['k']), on='k')",
            ("sort", "unique", "join"),
        ),
    ],
)
def test_a_boundary_inside_an_argument_is_recorded_before_its_outer_call(
    code: str,
    expected: tuple[str, ...],
) -> None:
    """Python evaluates the receiver, then the arguments, then the call.

    Recording the outer call first reported ``left.join(right.sort(...))`` as
    join-then-sort, which is the reverse of the order the frames are transformed.
    """
    from haute._types import GraphNode, NodeData, NodeType
    from haute.projection import materialising_operator_sequences_by_input_names

    node = GraphNode(
        id="op",
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(label="op", nodeType=NodeType.POLARS, config={"code": code}),
    )

    sequences = materialising_operator_sequences_by_input_names(
        ["op"], {"op": node}, {"op": ["left", "right"]}
    )

    assert dict(sequences) == {"op": expected}


def test_malformed_api_input_edge_classifies_without_raising() -> None:
    """A malformed apiInput edge is skipped by the classifier, not fatal.

    ``edge_input_name`` raises for an apiInput edge with no frame label, but
    strategy planning must still classify the graph: the node builder is the
    fail-loud point that reports the malformed edge.
    """
    from haute._types import GraphNode, NodeData, NodeType
    from haute.projection import materialising_operator_sequences_by_node

    source = GraphNode(
        id="src",
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(label="src", nodeType=NodeType.API_INPUT, config={}),
    )
    target = GraphNode(
        id="op",
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(
            label="op",
            nodeType=NodeType.POLARS,
            config={"code": "df = src.unique(subset=['k'])"},
        ),
    )
    edge = make_edge("src", "op", source_handle=None)

    sequences = materialising_operator_sequences_by_node(
        ["op"], {"src": source, "op": target}, relevant_edges=[edge]
    )

    assert dict(sequences) == {"op": ("unique",)}
