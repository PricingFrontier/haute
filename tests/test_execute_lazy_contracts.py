"""Focused tests for boundary-contract classification in ``haute._execute_lazy``."""

from __future__ import annotations

import json
from unittest.mock import patch

import polars as pl
import pytest

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._contracts import Contract
from haute._execute_lazy import (
    _declared_api_input_frame_schema_items,
    _execute_eager_core,
    _execute_lazy,
    _resolve_effective_contract,
    _runtime_join_demands,
    _strict_contract_resolution,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import (
    ConfigError,
    ContractMismatchError,
    ContractResolutionError,
    SchemaMismatchError,
)
from tests._projection_helpers import pair_value
from tests.conftest import make_edge, make_graph, make_output_config


def _node(node_type: NodeType, config: dict[str, object] | None = None) -> GraphNode:
    return GraphNode(
        id="node_1",
        data=NodeData(label="Node 1", nodeType=node_type, config=config or {}),
    )


def test_declared_api_input_frame_schema_items_ignores_non_api_or_malformed_tables() -> None:
    assert _declared_api_input_frame_schema_items(_node(NodeType.POLARS)) == {}
    assert _declared_api_input_frame_schema_items(_node(NodeType.API_INPUT, {"tables": {}})) == {}


def _undeclared_two_port_api_graph(with_target: bool) -> PipelineGraph:
    nodes = [
        {"id": "source", "data": {"label": "source", "nodeType": "apiInput", "config": {}}},
    ]
    edges: list[dict[str, object]] = []
    if with_target:
        nodes.append(
            {
                "id": "target",
                "data": {"label": "target", "nodeType": "polars", "config": {}},
            }
        )
        edges.append(make_edge("source", "target", source_handle="first").model_dump())
    return make_graph({"nodes": nodes, "edges": edges})


def _undeclared_two_port_builder(node: GraphNode, **_kwargs):
    if node.id == "source":
        return (
            node.id,
            lambda: {"first": pl.LazyFrame({"a": [1]}), "second": pl.LazyFrame({"b": ["x"]})},
            True,
        )
    return node.id, lambda frame: frame, False


def test_eager_multi_port_materialized_target_records_observed_port_schemas() -> None:
    result = _execute_eager_core(
        _undeclared_two_port_api_graph(with_target=False),
        _undeclared_two_port_builder,
        target_node_id="source",
        materialize_node_ids={"source"},
    )

    assert result.frame_columns == {
        ("source", "first"): [("a", "Int64")],
        ("source", "second"): [("b", "String")],
    }


def test_eager_multi_port_lazy_ancestor_records_schema_without_materialising_source() -> None:
    result = _execute_eager_core(
        _undeclared_two_port_api_graph(with_target=True),
        _undeclared_two_port_builder,
        target_node_id="target",
        materialize_node_ids={"target"},
    )

    assert result.frame_columns == {
        ("source", "first"): [("a", "Int64")],
        ("source", "second"): [("b", "String")],
    }
    assert "source" not in result.outputs


@pytest.mark.parametrize(
    ("error", "failure_kind"),
    [
        pytest.param(ConfigError("missing model"), "configuration", id="configuration"),
        pytest.param(OSError("disk offline"), "io", id="io"),
    ],
)
def test_strict_contract_resolution_raises_typed_redacted_error(
    error: BaseException,
    failure_kind: str,
) -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=error,
    ):
        with pytest.raises(ContractResolutionError) as exc_info:
            _resolve_effective_contract(
                _node(NodeType.MODEL_SCORE),
                strict=True,
            )

    assert exc_info.value.__cause__ is error
    assert exc_info.value.to_payload() == {
        "error_code": "contract_resolution_failed",
        "message": "Unable to resolve the node column contract.",
        "node_id": "node_1",
        "node_type": "modelScore",
        "failure_kind": failure_kind,
    }
    assert str(error) not in exc_info.value.message


def test_preview_contract_resolution_reports_opaque_degradation() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=ConfigError("missing model"),
    ):
        resolution = _resolve_effective_contract(
            _node(NodeType.MODEL_SCORE),
            strict=False,
        )

    assert resolution.contract == Contract.opaque()
    assert resolution.state == "degraded"
    assert resolution.failure_kind == "configuration"


@pytest.mark.parametrize(
    "profile",
    [
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.DEPLOY_LIVE,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    ],
)
def test_bounded_profiles_require_strict_contract_resolution(
    profile: ExecutionProfile,
) -> None:
    assert _strict_contract_resolution(profile) is True


def test_unprofiled_execution_requires_strict_contract_resolution() -> None:
    assert _strict_contract_resolution(None) is True


@pytest.mark.parametrize(
    "profile",
    [ExecutionProfile.PREVIEW_EAGER],
)
def test_materialising_profiles_allow_diagnosed_contract_degradation(
    profile: ExecutionProfile,
) -> None:
    assert _strict_contract_resolution(profile) is False


def test_lazy_and_eager_bounded_execution_share_typed_resolution_failure() -> None:
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
                }
            ],
            "edges": [],
        }
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        return node.id, lambda: pl.LazyFrame({"value": [1]}), True

    payloads: list[dict[str, object]] = []
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=ConfigError("missing source contract"),
    ):
        for execute in (
            lambda: _execute_lazy(
                graph,
                build_node_fn,
                target_node_id="source",
                enforce_contracts=True,
                execution_context=ExecutionContext(
                    operation="lazy",
                    profile=ExecutionProfile.LAZY_SINK,
                ),
            ),
            lambda: _execute_eager_core(
                graph,
                build_node_fn,
                target_node_id="source",
                execution_context=ExecutionContext(
                    operation="eager",
                    profile=ExecutionProfile.TRAINING_PREP,
                ),
            ),
        ):
            with pytest.raises(ContractResolutionError) as exc_info:
                execute()
            payloads.append(exc_info.value.to_payload())

    assert payloads[0] == payloads[1]


def test_contract_resolution_reraises_attribute_error() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        side_effect=AttributeError("bug"),
    ):
        with pytest.raises(AttributeError, match="bug"):
            _resolve_effective_contract(_node(NodeType.MODEL_SCORE), strict=True)


def test_contract_resolution_raises_for_malformed_declared_contract() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        with pytest.raises(ContractMismatchError, match="malformed"):
            _resolve_effective_contract(
                _node(
                    NodeType.POLARS,
                    {"contract": {"inputs": ["base_rate"]}},
                ),
                strict=True,
            )


def test_contract_resolution_merges_declared_inputs_with_builder_outputs() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        contract = _resolve_effective_contract(
            _node(
                NodeType.POLARS,
                {"contract": {"inputs": ["declared_rate"], "outputs": None}},
            ),
            strict=True,
        ).contract

    assert contract == Contract(
        inputs=frozenset({"declared_rate"}),
        outputs=frozenset({"premium"}),
    )


def test_contract_resolution_declared_opaque_preserves_builder_contract() -> None:
    with patch(
        "haute._execute_lazy.get_column_contract",
        return_value=({"premium"}, {"base_rate"}),
    ):
        contract = _resolve_effective_contract(
            _node(NodeType.POLARS, {"contract": "opaque"}),
            strict=True,
        ).contract

    assert contract == Contract(
        inputs=frozenset({"base_rate"}),
        outputs=frozenset({"premium"}),
    )


def _join_graph(
    *,
    code: str,
    right_parent_inputs: list[str] | None = None,
) -> PipelineGraph:
    right_inputs = right_parent_inputs or ["quote_id", "value"]
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
                            "code": code,
                            "contract": {
                                "inputs": ["quote_id", "value"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "left": ["quote_id"],
                                    "right": right_inputs,
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
                        "config": make_output_config(["quote_id", "value"]),
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


def _contract_free_join_graph(*, code: str, fields: list[str]) -> PipelineGraph:
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
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "out").model_dump(),
            ],
        }
    )


def _execute_contract_free_join(
    *,
    code: str,
    join_fn,
    fields: list[str],
    left_df: pl.DataFrame,
    right_df: pl.DataFrame,
) -> tuple[dict[str, pl.LazyFrame | pl.DataFrame], list[tuple[list[str], list[str]]]]:
    graph = _contract_free_join_graph(code=code, fields=fields)
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: left_df.lazy(), True
        if node.id == "right":
            return node.id, lambda: right_df.lazy(), True
        if node.id == "joined":

            def join(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                seen_join_schemas.append(
                    (left.collect_schema().names(), right.collect_schema().names())
                )
                return join_fn(left, right)

            return node.id, join, False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=ExecutionContext(
            operation="test_runtime_join_projection",
            profile=ExecutionProfile.LAZY_SINK,
        ),
    )

    return outputs, seen_join_schemas


def test_execute_lazy_rejects_simple_join_key_dtype_mismatch_before_running_node() -> None:
    graph = _join_graph(code="df = left.join(right, on='quote_id', how='left')")

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: pl.DataFrame({"quote_id": [1]}).lazy(), True
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["1"], "value": [10]}).lazy(),
                True,
            )
        if node.id == "joined":

            def join_should_not_run(*_dfs):
                raise AssertionError("join function should not run")

            return node.id, join_should_not_run, False
        return node.id, lambda df: df, False

    with pytest.raises(SchemaMismatchError, match="Join key dtype mismatch") as excinfo:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            enforce_contracts=True,
            execution_context=ExecutionContext(
                operation="test_join_dtype",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert excinfo.value.context["node_id"] == "joined"
    assert excinfo.value.context["left_key"] == "quote_id"
    assert excinfo.value.context["right_key"] == "quote_id"


def test_execute_lazy_accepts_matching_simple_join_key_dtypes() -> None:
    graph = _join_graph(code="df = left.join(right, on='quote_id', how='left')")

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: pl.DataFrame({"quote_id": [1]}).lazy(), True
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": [1], "value": [10]}).lazy(),
                True,
            )
        if node.id == "joined":
            return node.id, lambda left, right: left.join(right, on="quote_id"), False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        enforce_contracts=True,
        execution_context=ExecutionContext(
            operation="test_join_dtype",
            profile=ExecutionProfile.LAZY_SINK,
        ),
    )

    assert outputs["out"].collect()["value"].to_list() == [10]


def test_bounded_lazy_execution_context_carries_projection_plan() -> None:
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
    context = ExecutionContext(
        operation="test_projection_plan",
        profile=ExecutionProfile.LAZY_SINK,
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "unused": [1]}).lazy(),
                True,
            )
        return node.id, lambda df: df, False

    _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] == frozenset({"quote_id"})


def test_bounded_lazy_execution_refines_unowned_fan_in_from_parent_schemas() -> None:
    graph = make_graph(
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
                        "config": {"code": "df = left.join(right, on='quote_id')"},
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
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "out").model_dump(),
            ],
        }
    )
    context = ExecutionContext(
        operation="test_projection_contract",
        profile=ExecutionProfile.LAZY_SINK,
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id in {"left", "right"}:
            return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
        if node.id == "joined":
            return node.id, lambda left, right: left.join(right, on="quote_id"), False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert outputs["out"].collect().to_dict(as_series=False) == {"quote_id": ["q1"]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["left"] == frozenset({"quote_id"})
    assert context.projection_plan.needed_by_node["right"] == frozenset({"quote_id"})
    assert context.projection_plan.status.value == "projected"


def test_bounded_lazy_execution_runtime_projects_simple_contract_free_join() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "df = left.join(right, on='quote_id')"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["quote_id", "right_value"]),
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
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "left_value": [1],
                        "left_unused": [100],
                    }
                ).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "right_value": [2],
                        "right_unused": [200],
                    }
                ).lazy(),
                True,
            )
        if node.id == "joined":

            def join(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                seen_join_schemas.append(
                    (left.collect_schema().names(), right.collect_schema().names())
                )
                return left.join(right, on="quote_id")

            return node.id, join, False
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_runtime_join_projection",
        profile=ExecutionProfile.LAZY_SINK,
    )

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert seen_join_schemas == [(["quote_id"], ["quote_id", "right_value"])]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [2],
    }
    assert context.projection_plan is not None
    diagnostics = context.projection_plan.projection_plan.diagnostics_payload(
        profile=context.projection_plan.profile
    )
    assert diagnostics is not None
    assert diagnostics["edge_reasons"]["left->joined"]["rule"] == ("runtime_inferred_streaming")
    assert diagnostics["edge_reasons"]["right->joined"]["rule"] == ("runtime_inferred_streaming")
    assert diagnostics["edge_reasons"]["right->joined"]["details"] == {
        "strategy": "runtime_inferred_streaming",
        "columns": ("quote_id", "right_value"),
    }
    assert diagnostics["strategy_summary"]["profile"] == "lazy_sink"
    assert diagnostics["strategy_summary"]["node_strategy_counts"] == {"projected": 4}
    json.dumps(diagnostics)


@pytest.mark.parametrize("error", [KeyError("source"), ValueError("port")])
def test_runtime_join_inference_fails_closed_when_an_edge_name_is_invalid(
    error: Exception,
) -> None:
    node = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.POLARS,
            config={"code": "df = left.select(['x'])"},
        ),
    )
    left = GraphNode(id="left", data=NodeData(label="left", nodeType=NodeType.DATA_INPUT))
    right = GraphNode(
        id="right",
        data=NodeData(label="right", nodeType=NodeType.DATA_INPUT),
    )
    edges = [make_edge("left", "joined"), make_edge("right", "joined")]

    with patch("haute._execute_lazy.edge_input_name", side_effect=error):
        demands = _runtime_join_demands(
            node,
            edges,
            [pl.LazyFrame({"x": [1]}), pl.LazyFrame({"y": [2]})],
            {"x"},
            {},
            {"left": left, "right": right, "joined": node},
        )

    assert demands == {}


def test_runtime_join_inference_fails_closed_on_duplicate_input_names() -> None:
    node = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.POLARS,
            config={"code": "df = duplicate.select(['x'])"},
        ),
    )
    left = GraphNode(
        id="left",
        data=NodeData(label="duplicate", nodeType=NodeType.DATA_INPUT),
    )
    right = GraphNode(
        id="right",
        data=NodeData(label="duplicate", nodeType=NodeType.DATA_INPUT),
    )
    edges = [make_edge("left", "joined"), make_edge("right", "joined")]

    assert (
        _runtime_join_demands(
            node,
            edges,
            [pl.LazyFrame({"x": [1]}), pl.LazyFrame({"y": [2]})],
            {"x"},
            {},
            {"left": left, "right": right, "joined": node},
        )
        == {}
    )


@pytest.mark.parametrize(
    "input_mapping",
    [
        ["alias"],
        {"": "left"},
        {"alias": "missing"},
        {"right": "left"},
    ],
)
def test_runtime_join_inference_rejects_invalid_input_mapping(
    input_mapping: object,
) -> None:
    node = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.POLARS,
            config={
                "code": "df = left.select(['x'])",
                "inputMapping": input_mapping,
            },
        ),
    )
    left = GraphNode(id="left", data=NodeData(label="left", nodeType=NodeType.DATA_INPUT))
    right = GraphNode(
        id="right",
        data=NodeData(label="right", nodeType=NodeType.DATA_INPUT),
    )
    edges = [make_edge("left", "joined"), make_edge("right", "joined")]

    assert (
        _runtime_join_demands(
            node,
            edges,
            [pl.LazyFrame({"x": [1]}), pl.LazyFrame({"y": [2]})],
            {"x"},
            {},
            {"left": left, "right": right, "joined": node},
        )
        == {}
    )


def test_runtime_join_inference_maps_alias_demand_to_its_physical_edge() -> None:
    node = GraphNode(
        id="joined",
        data=NodeData(
            label="joined",
            nodeType=NodeType.POLARS,
            config={
                "code": "df = alias.select(['x'])",
                "inputMapping": {"alias": "left"},
            },
        ),
    )
    left = GraphNode(id="left", data=NodeData(label="left", nodeType=NodeType.DATA_INPUT))
    right = GraphNode(
        id="right",
        data=NodeData(label="right", nodeType=NodeType.DATA_INPUT),
    )
    edges = [make_edge("left", "joined"), make_edge("right", "joined")]

    demands = _runtime_join_demands(
        node,
        edges,
        [pl.LazyFrame({"x": [1], "unused": [2]}), pl.LazyFrame({"y": [3]})],
        {"x"},
        {},
        {"left": left, "right": right, "joined": node},
    )

    assert demands[projection_planner.ProjectionEdgeKey.from_edge(edges[0])] == {"x"}
    assert demands[projection_planner.ProjectionEdgeKey.from_edge(edges[1])] == set()


def test_eager_runtime_partial_inference_keeps_unknown_edge_full_and_empty_edge_cardinality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput"}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput"}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "if enabled:\n    df = left"},
                    },
                },
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
            ],
        }
    )
    left_edge = graph.edges[0]
    seen: list[tuple[list[str], list[str], int]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: pl.LazyFrame({"left_id": [1, 2], "unused": [3, 4]}), True
        if node.id == "right":
            return node.id, lambda: pl.LazyFrame({"right_id": [1, 2], "payload": [5, 6]}), True

        def retain_right(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
            seen.append(
                (
                    left.collect_schema().names(),
                    right.collect_schema().names(),
                    left.select(pl.len()).collect().item(),
                )
            )
            return right

        return node.id, retain_right, False

    monkeypatch.setattr(
        "haute._execute_lazy._runtime_join_demands",
        lambda *_args, **_kwargs: {
            projection_planner.ProjectionEdgeKey.from_edge(left_edge): set()
        },
    )

    result = _execute_eager_core(
        graph,
        build_node_fn,
        target_node_id="joined",
        materialize_node_ids={"joined"},
    )

    assert seen == [(["left_id"], ["right_id", "payload"], 2)]
    output = result.outputs["joined"]
    assert isinstance(output, pl.DataFrame)
    assert output.columns == ["right_id", "payload"]


def test_bounded_lazy_execution_runtime_projects_builtin_edge_join_and_final_diagnostic() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "base", "data": {"label": "base", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "competitor",
                    "data": {"label": "competitor", "nodeType": "dataInput", "config": {}},
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "on": ["quote_id"],
                            "suffix": "_right",
                            "contract": "opaque",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("base", "joined", target_handle="base").model_dump(),
                make_edge("competitor", "joined", target_handle="join").model_dump(),
            ],
        }
    )
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "base":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {"quote_id": ["q1"], "base_value": [1], "base_unused": [100]}
                ).lazy(),
                True,
            )
        if node.id == "competitor":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "competitor_premium": [2.0],
                        "competitor_unused": [200],
                    }
                ).lazy(),
                True,
            )

        def join(base: pl.LazyFrame, competitor: pl.LazyFrame) -> pl.LazyFrame:
            seen_join_schemas.append(
                (base.collect_schema().names(), competitor.collect_schema().names())
            )
            return base.join(competitor, on="quote_id", how="left", suffix="_right")

        return node.id, join, False

    context = ExecutionContext(
        operation="test_runtime_builtin_edge_join_projection",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="joined",
        required_columns_by_node={"joined": {"quote_id", "competitor_premium"}},
        execution_context=context,
    )

    assert seen_join_schemas == [(["quote_id"], ["quote_id", "competitor_premium"])]
    assert outputs["joined"].collect().to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "competitor_premium": [2.0],
    }
    metrics = context.metrics_payload(status="completed")
    strategy = metrics["execution_strategy"]
    assert strategy is not None
    assert strategy["status"] == "projected"
    assert strategy["strategy"] == "projected"
    assert strategy.get("blocking_node_id") is None


def test_eager_preview_runtime_projects_builtin_edge_join_and_final_diagnostic() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "base", "data": {"label": "base", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "competitor",
                    "data": {"label": "competitor", "nodeType": "dataInput", "config": {}},
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "on": ["quote_id"],
                            "suffix": "_right",
                            "contract": "opaque",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("base", "joined", target_handle="base").model_dump(),
                make_edge("competitor", "joined", target_handle="join").model_dump(),
            ],
        }
    )
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "base":
            return node.id, lambda: pl.LazyFrame({"quote_id": ["q1"], "base_unused": [1]}), True
        if node.id == "competitor":
            return (
                node.id,
                lambda: pl.LazyFrame(
                    {"quote_id": ["q1"], "competitor_premium": [2.0], "unused": [3]}
                ),
                True,
            )

        def join(base: pl.LazyFrame, competitor: pl.LazyFrame) -> pl.LazyFrame:
            seen_join_schemas.append(
                (base.collect_schema().names(), competitor.collect_schema().names())
            )
            return base.join(competitor, on="quote_id", how="left")

        return node.id, join, False

    required = {"joined": {"quote_id", "competitor_premium"}}
    context = ExecutionContext(
        operation="test_eager_runtime_builtin_edge_join_projection",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    execution_facade.plan_execution_strategy(
        execution_facade.ProjectionRequest(
            graph=graph,
            target_node_id="joined",
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node=required,
        ),
        execution_context=context,
    )

    result = _execute_eager_core(
        graph,
        build_node_fn,
        target_node_id="joined",
        required_columns_by_node=required,
        materialize_node_ids={"joined"},
        execution_context=context,
    )

    assert seen_join_schemas == [(["quote_id"], ["quote_id", "competitor_premium"])]
    joined_output = result.outputs["joined"]
    assert isinstance(joined_output, pl.DataFrame)
    assert joined_output.to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "competitor_premium": [2.0],
    }
    strategy = context.metrics_payload(status="completed")["execution_strategy"]
    assert strategy is not None
    assert strategy["status"] == "projected"
    assert strategy["strategy"] == "projected"


@pytest.mark.parametrize(
    ("code", "join_fn", "expected_left", "expected_right"),
    [
        (
            "df = left.join(right, on='quote_id')",
            lambda left, right: left.join(right, on="quote_id"),
            ["quote_id"],
            ["quote_id", "right_value"],
        ),
        (
            "df = left.join(right, on=['quote_id', 'version'])",
            lambda left, right: left.join(right, on=["quote_id", "version"]),
            ["quote_id", "version"],
            ["quote_id", "version", "right_value"],
        ),
    ],
)
def test_bounded_lazy_execution_runtime_projects_join_on_string_and_list_keys(
    code,
    join_fn,
    expected_left: list[str],
    expected_right: list[str],
) -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code=code,
        join_fn=join_fn,
        fields=["quote_id", "right_value"],
        left_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "version": [1],
                "left_value": [10],
                "left_unused": [100],
            }
        ),
        right_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "version": [1],
                "right_value": [20],
                "right_unused": [200],
            }
        ),
    )

    assert seen_join_schemas == [(expected_left, expected_right)]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [20],
    }


def test_bounded_lazy_execution_runtime_projects_left_on_right_on_join_keys() -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code="df = left.join(right, left_on='quote_id', right_on='policy_id')",
        join_fn=lambda left, right: left.join(
            right,
            left_on="quote_id",
            right_on="policy_id",
        ),
        fields=["quote_id", "right_value"],
        left_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "left_value": [10],
                "left_unused": [100],
            }
        ),
        right_df=pl.DataFrame(
            {
                "policy_id": ["q1"],
                "right_value": [20],
                "right_unused": [200],
            }
        ),
    )

    assert seen_join_schemas == [(["quote_id"], ["policy_id", "right_value"])]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [20],
    }


@pytest.mark.parametrize(
    ("how", "fields", "join_fn", "expected_left", "expected_right", "expected_output"),
    [
        (
            "left",
            ["quote_id", "right_value"],
            lambda left, right: left.join(right, on="quote_id", how="left"),
            ["quote_id"],
            ["quote_id", "right_value"],
            {"quote_id": ["q1", "q2"], "right_value": [20, None]},
        ),
        (
            "right",
            ["quote_id", "right_value"],
            lambda left, right: left.join(right, on="quote_id", how="right"),
            ["quote_id", "left_value", "left_unused"],
            ["quote_id", "right_value", "right_unused"],
            {"quote_id": ["q1"], "right_value": [20]},
        ),
        (
            "full",
            ["quote_id_right", "right_value"],
            lambda left, right: left.join(right, on="quote_id", how="full"),
            ["quote_id", "left_value", "left_unused"],
            ["quote_id", "right_value", "right_unused"],
            {"quote_id_right": ["q1", None], "right_value": [20, None]},
        ),
        (
            "semi",
            ["quote_id", "left_value"],
            lambda left, right: left.join(right, on="quote_id", how="semi"),
            ["quote_id", "left_value"],
            ["quote_id"],
            {"quote_id": ["q1"], "left_value": [10]},
        ),
        (
            "anti",
            ["quote_id", "left_value"],
            lambda left, right: left.join(right, on="quote_id", how="anti"),
            ["quote_id", "left_value"],
            ["quote_id"],
            {"quote_id": ["q2"], "left_value": [11]},
        ),
    ],
)
def test_bounded_lazy_execution_runtime_projects_common_join_hows(
    how: str,
    fields: list[str],
    join_fn,
    expected_left: list[str],
    expected_right: list[str],
    expected_output: dict[str, list[object]],
) -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code=f"df = left.join(right, on='quote_id', how='{how}')",
        join_fn=join_fn,
        fields=fields,
        left_df=pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "left_value": [10, 11],
                "left_unused": [100, 101],
            }
        ),
        right_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "right_value": [20],
                "right_unused": [200],
            }
        ),
    )

    assert seen_join_schemas == [(expected_left, expected_right)]
    assert outputs["out"].collect().select(fields).to_dict(as_series=False) == expected_output


def test_bounded_lazy_execution_runtime_projection_preserves_join_suffixes() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "df = left.join(right, on='quote_id')"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["value_right"]),
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
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {"quote_id": ["q1"], "value": [1], "left_unused": [100]}
                ).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {"quote_id": ["q1"], "value": [2], "right_unused": [200]}
                ).lazy(),
                True,
            )
        if node.id == "joined":

            def join(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                seen_join_schemas.append(
                    (left.collect_schema().names(), right.collect_schema().names())
                )
                return left.join(right, on="quote_id")

            return node.id, join, False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=ExecutionContext(
            operation="test_runtime_join_suffix_projection",
            profile=ExecutionProfile.LAZY_SINK,
        ),
    )

    assert seen_join_schemas == [(["quote_id", "value"], ["quote_id", "value"])]
    assert outputs["out"].collect().select("value_right").to_dict(as_series=False) == {
        "value_right": [2]
    }


def test_bounded_lazy_execution_runtime_projection_preserves_custom_join_suffix() -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code="df = left.join(right, on='quote_id', suffix='_lookup')",
        join_fn=lambda left, right: left.join(right, on="quote_id", suffix="_lookup"),
        fields=["value_lookup"],
        left_df=pl.DataFrame({"quote_id": ["q1"], "value": [1], "left_unused": [100]}),
        right_df=pl.DataFrame({"quote_id": ["q1"], "value": [2], "right_unused": [200]}),
    )

    assert seen_join_schemas == [(["quote_id", "value"], ["quote_id", "value"])]
    assert outputs["out"].collect().select("value_lookup").to_dict(as_series=False) == {
        "value_lookup": [2]
    }


def test_bounded_lazy_execution_contract_free_join_missing_key_fails_loudly() -> None:
    graph = _contract_free_join_graph(
        code="df = left.join(right, on='quote_id')",
        fields=["quote_id", "right_value"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "left_unused": [100]}).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame({"policy_id": ["q1"], "right_value": [20]}).lazy(),
                True,
            )
        if node.id == "joined":

            def join_should_not_run(*_dfs):
                raise AssertionError("join function should not run")

            return node.id, join_should_not_run, False
        return node.id, lambda df: df, False

    with pytest.raises(ContractMismatchError, match="missing from the parent frame") as excinfo:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test_runtime_join_missing_key",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert excinfo.value.context["node_id"] == "joined"
    assert excinfo.value.context["parent_id"] == "right"
    assert excinfo.value.context["missing"] == ["quote_id"]


def test_bounded_lazy_execution_contract_free_join_suffix_collision_fails_loudly() -> None:
    with pytest.raises(pl.exceptions.DuplicateError, match="value_right"):
        _execute_contract_free_join(
            code="df = left.join(right, on='quote_id')",
            join_fn=lambda left, right: left.join(right, on="quote_id"),
            fields=["value_right"],
            left_df=pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "value": [1],
                    "left_unused": [100],
                }
            ),
            right_df=pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "value": [2],
                    "value_right": [3],
                    "right_unused": [200],
                }
            ),
        )


def test_bounded_lazy_execution_dynamic_join_how_stays_unprojected_boundary() -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code="df = left.join(right, on='quote_id', how=join_type)",
        join_fn=lambda left, right: left.join(right, on="quote_id", how="inner"),
        fields=["quote_id", "right_value"],
        left_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "left_value": [1],
                "left_unused": [100],
            }
        ),
        right_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "right_value": [2],
                "right_unused": [200],
            }
        ),
    )

    assert seen_join_schemas == [
        (
            ["quote_id", "left_value", "left_unused"],
            ["quote_id", "right_value", "right_unused"],
        )
    ]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [2],
    }


def test_bounded_lazy_execution_empty_join_suffix_stays_unprojected_boundary() -> None:
    outputs, seen_join_schemas = _execute_contract_free_join(
        code="df = left.join(right, on='quote_id', suffix='')",
        join_fn=lambda left, right: left.join(right, on="quote_id", suffix=""),
        fields=["quote_id", "right_value"],
        left_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "left_value": [1],
                "left_unused": [100],
            }
        ),
        right_df=pl.DataFrame(
            {
                "quote_id": ["q1"],
                "right_value": [2],
                "right_unused": [200],
            }
        ),
    )

    assert seen_join_schemas == [
        (
            ["quote_id", "left_value", "left_unused"],
            ["quote_id", "right_value", "right_unused"],
        )
    ]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [2],
    }


def test_bounded_lazy_execution_runtime_projects_left_on_right_on_join() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = left.join(right, left_on='quote_id', right_on='policy_id')"
                            )
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["quote_id", "right_value"]),
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
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "left_unused": [100]}).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "policy_id": ["q1"],
                        "right_value": [2],
                        "right_unused": [200],
                    }
                ).lazy(),
                True,
            )
        if node.id == "joined":

            def join(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                seen_join_schemas.append(
                    (left.collect_schema().names(), right.collect_schema().names())
                )
                return left.join(right, left_on="quote_id", right_on="policy_id")

            return node.id, join, False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=ExecutionContext(
            operation="test_runtime_left_right_join_projection",
            profile=ExecutionProfile.LAZY_SINK,
        ),
    )

    assert seen_join_schemas == [(["quote_id"], ["policy_id", "right_value"])]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [2],
    }


def test_bounded_lazy_execution_runtime_projection_fails_loudly_on_missing_join_key() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "df = left.join(right, on='quote_id')"},
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
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "out").model_dump(),
            ],
        }
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: pl.DataFrame({"quote_id": ["q1"]}).lazy(), True
        if node.id == "right":
            return node.id, lambda: pl.DataFrame({"other_id": ["q1"]}).lazy(), True
        if node.id == "joined":
            return node.id, lambda left, right: left.join(right, on="quote_id"), False
        return node.id, lambda df: df, False

    with pytest.raises(ContractMismatchError, match="missing from the parent frame") as excinfo:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test_runtime_missing_join_key",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert excinfo.value.context["parent_id"] == "right"
    assert excinfo.value.context["missing"] == ["quote_id"]


def test_bounded_lazy_execution_keeps_full_width_for_unsupported_join_type() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "df = left.join(right, on='quote_id', how='full')"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["quote_id", "right_value"]),
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
    seen_join_schemas: list[tuple[list[str], list[str]]] = []

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "left_unused": [100]}).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "right_value": [2],
                        "right_unused": [200],
                    }
                ).lazy(),
                True,
            )
        if node.id == "joined":

            def join(left: pl.LazyFrame, right: pl.LazyFrame) -> pl.LazyFrame:
                seen_join_schemas.append(
                    (left.collect_schema().names(), right.collect_schema().names())
                )
                return left.join(right, on="quote_id", how="full", coalesce=True)

            return node.id, join, False
        return node.id, lambda df: df, False

    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=ExecutionContext(
            operation="test_runtime_join_boundary",
            profile=ExecutionProfile.LAZY_SINK,
        ),
    )

    assert seen_join_schemas == [
        (
            ["quote_id", "left_unused"],
            ["quote_id", "right_value", "right_unused"],
        )
    ]
    assert outputs["out"].collect().select("quote_id", "right_value").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "right_value": [2],
    }


def test_bounded_lazy_execution_projects_simple_uncontracted_user_code() -> None:
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

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1]}).lazy(), True
        if node.id == "custom":
            return node.id, lambda df: df.with_columns((pl.col("a") + 1).alias("a")), False
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_user_code_contract",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert outputs["out"].collect().to_dict(as_series=False) == {"a": [2]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] == frozenset({"a"})
    assert (
        pair_value(context.projection_plan.diagnostics.edge_reasons, "source", "custom").rule
        == "polars_column_lineage"
    )


def test_lazy_checkpoint_does_not_project_stale_contract_outputs_into_edge_join(
    tmp_path,
) -> None:
    graph = make_graph(
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
                                "competitor_premium",
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
                make_edge("left", "join_premiums").model_dump(),
                make_edge("right", "join_premiums").model_dump(),
                make_edge("join_premiums", "sale_flag").model_dump(),
                make_edge("sale_flag", "premium").model_dump(),
                make_edge("premium", "conversion_scoring").model_dump(),
                make_edge("conversion_scoring", "optimiser_input").model_dump(),
            ],
        }
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "premium": [100.0],
                        "unused_policy": ["kept until checkpoint"],
                    }
                ).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "quote_id": ["q1"],
                        "competitor_premium": [120.0],
                        "unused_market": ["kept until checkpoint"],
                    }
                ).lazy(),
                True,
            )
        if node.id == "join_premiums":
            return (
                node.id,
                lambda left, right: left.join(right, on="quote_id", how="left"),
                False,
            )
        if node.id == "sale_flag":
            return (
                node.id,
                lambda df: df.with_columns(burn_cost=pl.col("premium") * 0.7),
                False,
            )
        if node.id == "premium":
            return node.id, _expand_premium_scenarios, False
        if node.id == "conversion_scoring":
            return (
                node.id,
                lambda df: df.with_columns(conversion_prediction=pl.lit(0.5)),
                False,
            )
        if node.id == "optimiser_input":
            return (
                node.id,
                lambda df: df.with_columns(expected_margin=pl.col("premium") - pl.col("burn_cost")),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_stale_contract_checkpoint_projection",
        profile=ExecutionProfile.OPTIMISER_SETUP,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="optimiser_input",
        checkpoint_dir=tmp_path,
        enforce_contracts=True,
        required_columns_by_node={
            "optimiser_input": {
                "quote_id",
                "scenario_index",
                "premium_multiplier",
                "conversion_prediction",
                "expected_margin",
            }
        },
        execution_context=context,
    )

    result = outputs["optimiser_input"].collect()

    assert (tmp_path / "join_premiums.parquet").exists()
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["join_premiums"] is None
    assert result.select("quote_id", "scenario_index").to_dict(as_series=False) == {
        "quote_id": ["q1", "q1"],
        "scenario_index": [0, 1],
    }
    assert result["expected_margin"].to_list() == pytest.approx([20.0, 40.0])


def _expand_premium_scenarios(df: pl.LazyFrame) -> pl.LazyFrame:
    return (
        df.with_columns(
            [
                pl.lit([0, 1]).alias("scenario_index"),
                pl.lit([0.9, 1.1]).alias("premium_multiplier"),
            ]
        )
        .explode(["scenario_index", "premium_multiplier"])
        .with_columns(
            premium=pl.col("premium") * pl.col("premium_multiplier"),
            scenario_index=pl.col("scenario_index").cast(pl.Int32),
            premium_multiplier=pl.col("premium_multiplier").cast(pl.Float32),
        )
    )


def _rename_pipeline_graph(code: str, fields: list[str]) -> PipelineGraph:
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


def test_bounded_lazy_execution_executes_rename_then_filter_pipeline() -> None:
    """Rename then filter on the new name must run under projection planning.

    The planner previously re-added the post-rename name to the parent demand,
    so this valid pipeline hard-failed with a missing-column contract error.
    """
    graph = _rename_pipeline_graph(
        "df = df.rename({'raw_premium': 'premium'})\ndf = df.filter(pl.col('premium') > 0)",
        ["premium", "quote_id"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "raw_premium": [120, -5],
                        "quote_id": ["q1", "q2"],
                        "unused": [1, 2],
                    }
                ).lazy(),
                True,
            )
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.rename({"raw_premium": "premium"}).filter(pl.col("premium") > 0),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_rename_then_filter",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    collected = outputs["out"].collect()
    assert collected.select("premium", "quote_id").to_dict(as_series=False) == {
        "premium": [120],
        "quote_id": ["q1"],
    }
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] is None


def test_bounded_lazy_execution_rename_collision_still_fails_loudly() -> None:
    """Projection must not mask a rename collision with a demanded column."""
    graph = _rename_pipeline_graph(
        "df = df.filter(pl.col('b') > 0)\ndf = df.rename({'a': 'b'})",
        ["b"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1], "b": [2]}).lazy(), True
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.filter(pl.col("b") > 0).rename({"a": "b"}),
                False,
            )
        return node.id, lambda df: df, False

    with pytest.raises(pl.exceptions.DuplicateError):
        outputs, *_ = _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test_rename_collision",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )
        outputs["out"].collect()


def test_bounded_lazy_execution_rename_pipeline_unknown_column_still_fails_loudly() -> None:
    """A genuinely unknown downstream column keeps a clear missing-column error."""
    graph = _rename_pipeline_graph(
        "df = df.rename({'a': 'b'})\ndf = df.filter(pl.col('zzz') > 0)",
        ["b"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1]}).lazy(), True
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.rename({"a": "b"}).filter(pl.col("zzz") > 0),
                False,
            )
        return node.id, lambda df: df, False

    with pytest.raises(pl.exceptions.ColumnNotFoundError) as excinfo:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test_rename_unknown_column",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert "zzz" in str(excinfo.value)


def test_bounded_lazy_execution_unknown_column_without_rename_still_fails_contract() -> None:
    """Rename-free unknown columns still fail at the projection boundary."""
    graph = _rename_pipeline_graph(
        "df = df.filter(pl.col('zzz') > 0)",
        ["a"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1]}).lazy(), True
        if node.id == "transform":
            return node.id, lambda df: df.filter(pl.col("zzz") > 0), False
        return node.id, lambda df: df, False

    with pytest.raises(ContractMismatchError) as contract_exc:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=ExecutionContext(
                operation="test_unknown_column_without_rename",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert "zzz" in str(contract_exc.value)


def test_bounded_lazy_execution_executes_derived_column_filter_pipeline() -> None:
    """Deriving a column then filtering on it must run under projection planning.

    The planner previously re-added the derived name to the parent demand, so
    this valid rename-free pipeline hard-failed with a missing-column contract
    error naming a column the parent never had.
    """
    graph = _rename_pipeline_graph(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('m'))\n"
        "df = df.filter(pl.col('m') > 0)",
        ["m"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "a": [1, -5],
                        "b": [2, 1],
                        "unused": [9, 9],
                    }
                ).lazy(),
                True,
            )
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.with_columns((pl.col("a") + pl.col("b")).alias("m")).filter(
                    pl.col("m") > 0
                ),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_derived_column_filter",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    collected = outputs["out"].collect()
    assert collected.select("m").to_dict(as_series=False) == {"m": [3]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] == frozenset({"a", "b"})


def test_bounded_lazy_execution_unprovable_derived_reference_runs_full_width() -> None:
    """Unsupported control flow remains correct by retaining the full input."""
    graph = _rename_pipeline_graph(
        "df = df.with_columns((pl.col('a') + pl.col('b')).alias('margin'))\n"
        "if True:\n"
        "    df = df.filter(pl.col('margin') > 0)",
        ["margin"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1], "b": [2]}).lazy(), True
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.with_columns((pl.col("a") + pl.col("b")).alias("margin")).filter(
                    pl.col("margin") > 0
                ),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_unprovable_derived_reference",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert outputs["out"].collect().to_dict(as_series=False) == {"margin": [3]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] is None


def test_bounded_lazy_execution_executes_select_subset_pipeline() -> None:
    """A select wider than the downstream demand must run under projection.

    PIN REVISION (2.12c): the planner previously demanded only the
    downstream subset ``{a}`` from the parent, but the node executes
    ``select('a', 'b', 'c')`` verbatim, so this valid pipeline hard-failed
    with ``ColumnNotFoundError`` at collect.  The parent demand must be
    exactly the select's inputs - and must still exclude ``unused``.
    """
    graph = _rename_pipeline_graph(
        "df = df.select('a', 'b', 'c')",
        ["a"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "a": [1, 2],
                        "b": [3, 4],
                        "c": [5, 6],
                        "unused": [9, 9],
                    }
                ).lazy(),
                True,
            )
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.select("a", "b", "c"),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_select_subset",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    collected = outputs["out"].collect()
    assert collected.select("a").to_dict(as_series=False) == {"a": [1, 2]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] == frozenset({"a", "b", "c"})


def test_bounded_lazy_execution_executes_unaliased_with_columns_then_select_pipeline() -> None:
    """Un-aliased ``with_columns`` outputs must never be demanded from the parent.

    The structural naming transfer maps ``a_2`` back to ``a`` and keeps ``b``;
    it never asks the parent for the in-node output name.
    """
    graph = _rename_pipeline_graph(
        "df = df.with_columns(pl.col('a').name.suffix('_2'))\ndf = df.select('a_2', 'b')",
        ["b"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame(
                    {
                        "a": [1, 2],
                        "b": [3, 4],
                        "unused": [9, 9],
                    }
                ).lazy(),
                True,
            )
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.with_columns(pl.col("a").name.suffix("_2")).select("a_2", "b"),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_unaliased_with_columns_select",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    collected = outputs["out"].collect()
    assert collected.select("b").to_dict(as_series=False) == {"b": [3, 4]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] == frozenset({"a", "b"})


def test_bounded_lazy_execution_unprovable_select_runs_full_width() -> None:
    """Unsupported control flow keeps every input needed by the real code."""
    graph = _rename_pipeline_graph(
        "if True:\n    df = df.select('a', 'b', 'c')",
        ["a"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return (
                node.id,
                lambda: pl.DataFrame({"a": [1], "b": [2], "c": [3]}).lazy(),
                True,
            )
        if node.id == "transform":
            return (
                node.id,
                lambda df: df.select("a", "b", "c"),
                False,
            )
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_unprovable_select",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="out",
        execution_context=context,
    )

    assert outputs["out"].collect().to_dict(as_series=False) == {"a": [1]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] is None


def test_bounded_lazy_execution_runs_terminal_uncontracted_user_code_as_boundary() -> None:
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

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "source":
            return node.id, lambda: pl.DataFrame({"a": [1]}).lazy(), True
        if node.id == "custom":
            return node.id, lambda df: df.with_columns((pl.col("a") + 1).alias("a")), False
        return node.id, lambda df: df, False

    context = ExecutionContext(
        operation="test_terminal_user_code_contract",
        profile=ExecutionProfile.LAZY_SINK,
    )
    outputs, *_ = _execute_lazy(
        graph,
        build_node_fn,
        target_node_id="sink",
        execution_context=context,
    )

    assert outputs["sink"].collect().to_dict(as_series=False) == {"a": [2]}
    assert context.projection_plan is not None
    assert context.projection_plan.needed_by_node["source"] is None


def test_execute_lazy_rejects_left_on_right_on_join_key_dtype_mismatch() -> None:
    graph = _join_graph(
        code="df = left.join(right, left_on=['quote_id'], right_on=['policy_id'])",
        right_parent_inputs=["policy_id", "value"],
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return node.id, lambda: pl.DataFrame({"quote_id": [1]}).lazy(), True
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame({"policy_id": ["1"], "value": [10]}).lazy(),
                True,
            )
        if node.id == "joined":

            def join_should_not_run(*_dfs):
                raise AssertionError("join function should not run")

            return node.id, join_should_not_run, False
        return node.id, lambda df: df, False

    with pytest.raises(SchemaMismatchError, match="Join key dtype mismatch") as excinfo:
        _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            enforce_contracts=True,
            execution_context=ExecutionContext(
                operation="test_join_dtype",
                profile=ExecutionProfile.LAZY_SINK,
            ),
        )

    assert excinfo.value.context["left_key"] == "quote_id"
    assert excinfo.value.context["right_key"] == "policy_id"


def test_conservative_strategy_survives_runtime_join_refinement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warned plan keeps its strategy and group-by operator through a rebuild."""
    from haute._execution_context import ExecutionAdmission
    from haute._native_memory_limit import native_memory_backend_scope
    from haute._ram_estimate import MaterialisationEstimate

    graph = make_graph(
        {
            "nodes": [
                {"id": "left", "data": {"label": "left", "nodeType": "dataInput", "config": {}}},
                {"id": "right", "data": {"label": "right", "nodeType": "dataInput", "config": {}}},
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "polars",
                        "config": {"code": "df = left.join(right, on='quote_id')"},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = joined.group_by('quote_id').agg("
                                "pl.col('right_value').sum().alias('total'))"
                            )
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["quote_id", "total"]),
                    },
                },
            ],
            "edges": [
                make_edge("left", "joined").model_dump(),
                make_edge("right", "joined").model_dump(),
                make_edge("joined", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )

    def build_node_fn(node: GraphNode, **_kwargs):
        if node.id == "left":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "left_unused": [100]}).lazy(),
                True,
            )
        if node.id == "right":
            return (
                node.id,
                lambda: pl.DataFrame({"quote_id": ["q1"], "right_value": [2]}).lazy(),
                True,
            )
        if node.id == "joined":
            return node.id, lambda left, right: left.join(right, on="quote_id"), False
        if node.id == "agg":
            return (
                node.id,
                lambda df: df.group_by("quote_id").agg(pl.col("right_value").sum().alias("total")),
                False,
            )
        return node.id, lambda df: df, False

    def unavailable(_graph, node_ids, **_kwargs):
        return [
            (node_id, MaterialisationEstimate.unavailable("metadata_unavailable"))
            for node_id in node_ids
        ]

    monkeypatch.setattr(execution_facade, "estimate_materialisation_boundaries", unavailable)
    limit = 1 << 30
    context = ExecutionContext(
        operation="test_conservative_rebuild",
        profile=ExecutionProfile.LAZY_SINK,
        admission=ExecutionAdmission(
            operation="test_conservative_rebuild",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=limit,
            rss_at_admission_bytes=10,
            rss_limit_bytes=10 + limit,
            headroom_bytes=limit,
            config_key="test",
        ),
    )

    with native_memory_backend_scope("rlimit"):
        outputs, *_ = _execute_lazy(
            graph,
            build_node_fn,
            target_node_id="out",
            execution_context=context,
        )

    assert outputs["out"].collect().select("quote_id", "total").to_dict(as_series=False) == {
        "quote_id": ["q1"],
        "total": [2],
    }
    result = context.projection_plan
    assert result is not None
    assert result.strategy is projection_planner.ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert result.status is projection_planner.ExecutionStrategyStatus.WARNED
    assert result.diagnostic.reason_code == "materialisation_estimate_unavailable_conservative"
    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    assert "proof_gap=agg:metadata_unavailable" in result.diagnostic.assumptions
    diagnostics = result.projection_plan.diagnostics_payload(profile=result.profile)
    assert diagnostics is not None
    assert diagnostics["edge_reasons"]["left->joined"]["rule"] == "runtime_inferred_streaming"
