"""Non-canonical input follows one rule (specs/README.md, canonical-only format policy).

A removed field may be rejected with a targeted "X was removed; use Y" message;
nothing migrates it, nothing drops it silently, no boundary passes an unknown
field through, and a check degrades only on a named infrastructure failure.
One test per site that recognises non-canonical input.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from mlflow.exceptions import MlflowException

from haute._config_builder import _build_node_config
from haute._contracts import Contract
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ConfigError

_EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}


def test_recover_reports_a_retired_config_field_and_keeps_it_out_of_the_candidate() -> None:
    from haute._node_config_recovery import reconcile_config

    result = reconcile_config(
        NodeType.EDGE_JOIN, {"how": "left", "on": ["id"], "baseInput": "a", "stale": 1}
    )

    # ``suffix`` is absent from the saved config, so it takes the palette default.
    assert result.config == {"how": "left", "on": ["id"], "suffix": "_right"}
    outcomes = {change.path: change.outcome for change in result.changes}
    assert outcomes["/baseInput"] == "needs_review"
    assert outcomes["/stale"] == "removed"
    assert outcomes["/suffix"] == "defaulted"
    assert {(issue.path, issue.code, issue.severity) for issue in result.issues} == {
        ("/baseInput", "unknown_field", "warning"),
        ("/stale", "unknown_field", "warning"),
    }


def test_a_removed_edge_join_decorator_argument_is_rejected_with_its_replacement() -> None:
    from haute._edge_join import normalise_edge_join_decorator_kwargs

    with pytest.raises(ConfigError, match="use incoming target ports 'base' and 'join'") as raised:
        normalise_edge_join_decorator_kwargs({"base_input": "quotes", "how": "left"})

    assert raised.value.context["legacy_arguments"] == ["base_input"]


@pytest.mark.parametrize(
    ("node_type", "config", "replacement"),
    [
        (NodeType.EDGE_JOIN, {"baseInput": "quotes"}, "use incoming target ports"),
        (NodeType.OPTIMISER, {"scored_input": "quotes"}, "use data_input and banding_source"),
        (NodeType.MODELLING, {"model_name": "m"}, "register or promote runs outside haute"),
    ],
)
def test_a_removed_config_key_is_rejected_with_its_replacement(
    node_type: NodeType, config: dict, replacement: str
) -> None:
    from haute._config_validation import reject_removed_config_keys

    with pytest.raises(ConfigError, match=replacement) as raised:
        reject_removed_config_keys(node_type, config)

    assert raised.value.context["removed_config_keys"] == sorted(config)


@pytest.mark.parametrize(
    "metrics",
    [
        pytest.param({}, id="route-derives-metrics-through-the-builder"),
        pytest.param({"metrics": ["rmse"]}, id="route-validates-without-the-builder"),
    ],
)
def test_the_removed_split_fields_are_rejected_by_one_check_for_training_and_the_route(
    metrics: dict,
) -> None:
    from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
    from haute.routes._training_lifecycle import TrainService

    config = {
        **metrics,
        "target": "y",
        "algorithm": "catboost",
        "loss_function": "RMSE",
        "params": {"iterations": 10},
        "evaluation": _EVALUATION,
        "split": {"method": "random", "test_size": 0.2},
        "cross_validation": {"fold_count": 3},
    }
    message = (
        "The modelling config fields 'split' and 'cross_validation' were removed; "
        "use the versioned 'evaluation' object."
    )

    with pytest.raises(TrainingConfigError) as built:
        build_training_job_kwargs(config, data="data.parquet")
    with pytest.raises(HTTPException) as routed:
        TrainService._validate_config(config)

    assert str(built.value) == message
    assert routed.value.status_code == 400
    assert routed.value.detail == message


def test_the_planners_take_the_prepared_graph_edges() -> None:
    """No planner synthesises edge identity from adjacency for a caller without edges."""
    from haute.execution import plan_prepared_execution_strategy
    from haute.projection import compute_prepared_plan

    for planner in (compute_prepared_plan, plan_prepared_execution_strategy):
        parameter = inspect.signature(planner).parameters["relevant_edges"]
        assert parameter.default is inspect.Parameter.empty, planner.__name__
    with pytest.raises(TypeError, match="relevant_edges"):
        compute_prepared_plan([], {}, {})  # type: ignore[call-arg]


def test_an_unknown_explore_overview_card_is_rejected_by_name() -> None:
    from haute.codegen import graph_to_code

    overview = {"dataset_snapshot": True, "custom_card": {"label": "Loss ratio"}}
    with pytest.raises(ConfigError, match="has no card 'custom_card'; the cards are"):
        _build_node_config(NodeType.EXPLORE, {"overview": overview}, "", ["df"])

    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(
                    label="quotes",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "quote_id", "value": "1"}]},
                ),
            ),
            GraphNode(
                id="inspect",
                data=NodeData(
                    label="inspect", nodeType=NodeType.EXPLORE, config={"overview": overview}
                ),
            ),
        ],
        edges=[GraphEdge(id="e_quotes_inspect", source="quotes", target="inspect")],
    )
    with pytest.raises(ConfigError, match="has no card 'custom_card'"):
        graph_to_code(graph, pipeline_name="overview")


@pytest.mark.parametrize(
    ("error", "degrades"),
    [
        (OSError("artifact missing"), True),
        (ImportError("optional dependency missing"), True),
        (MlflowException("tracking server unreachable"), True),
        (ConfigError("sourceType is required"), False),
        (RuntimeError("builder bug"), False),
    ],
)
def test_the_parse_time_contract_degrades_only_on_named_infrastructure_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception, degrades: bool
) -> None:
    from haute import _config_builder

    def derive(node_type: NodeType, config: dict) -> Contract:
        raise error

    monkeypatch.setattr(_config_builder, "_derive_parse_time_contract", derive)
    if degrades:
        assert _config_builder.resolve_parse_time_contract(NodeType.POLARS, {}) == (
            Contract.opaque()
        )
    else:
        with pytest.raises(type(error)):
            _config_builder.resolve_parse_time_contract(NodeType.POLARS, {})


def test_a_declared_contract_on_an_unbuildable_config_fails_to_load() -> None:
    """A configuration error is not an infrastructure failure: it surfaces at parse."""
    from haute._config_builder import _validate_user_contract

    with pytest.raises(ConfigError, match="requires sourceType='file'"):
        _validate_user_contract(
            NodeType.OPTIMISER_APPLY,
            {"artifact_path": "optimiser.json"},
            {"inputs": [], "outputs": []},
            "apply_optimiser",
        )
