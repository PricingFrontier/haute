"""Central cache-fingerprint completeness contracts."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest

from haute._cache import (
    CACHE_CONFIG_FIELD_CLASSIFICATIONS,
    CACHE_CONSUMER_CONTRACTS,
    CacheConfigFieldClassification,
    CacheConsumer,
    CacheInputClass,
    CacheInputDisposition,
    _consumer_contract,
    checked_cache_inputs,
    graph_fingerprint,
    validate_cache_config_field_classifications,
)
from haute._config_validation import VALID_KEYS
from haute._dataframe_execution_cache import dataframe_execution_cache_key
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _node(
    node_id: str,
    *,
    label: str | None = None,
    node_type: NodeType = NodeType.POLARS,
    config: dict[str, object] | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label or node_id,
            nodeType=node_type,
            config=dict(config or {}),
        ),
    )


def _graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node(
                "source",
                node_type=NodeType.DATA_INPUT,
                config={
                    "inputType": "file",
                    "format": "parquet",
                    "cacheMode": "direct",
                    "mode": "scan",
                    "path": "input.parquet",
                },
            ),
            _node(
                "target",
                label="Premium target",
                config={"code": "df = df.select('premium')"},
            ),
        ],
        edges=[
            GraphEdge(
                id="canvas-edge-1",
                source="source",
                sourceHandle="quotes",
                target="target",
                targetHandle="base",
            )
        ],
        pipeline_name="Pricing",
        pipeline_description="UI copy",
        preamble="import polars as pl",
        preserved_blocks=["# retained source comment"],
        source_file="pipelines/main.py",
        warning="display warning",
        sources=["live", "batch"],
        active_source="live",
    )


def _replace_node(
    graph: PipelineGraph,
    node_id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    position: dict[str, float] | None = None,
    react_flow_type: str | None = None,
) -> PipelineGraph:
    nodes: list[GraphNode] = []
    for node in graph.nodes:
        if node.id != node_id:
            nodes.append(node)
            continue
        data_updates: dict[str, object] = {}
        if label is not None:
            data_updates["label"] = label
        if description is not None:
            data_updates["description"] = description
        updated = node.model_copy(
            update={
                **({"data": node.data.model_copy(update=data_updates)} if data_updates else {}),
                **({"position": position} if position is not None else {}),
                **({"type": react_flow_type} if react_flow_type is not None else {}),
            }
        )
        nodes.append(updated)
    return graph.model_copy(update={"nodes": nodes})


def test_every_consumer_totally_classifies_the_closed_logical_input_set() -> None:
    assert set(CACHE_CONSUMER_CONTRACTS) == set(CacheConsumer)
    for consumer, contract in CACHE_CONSUMER_CONTRACTS.items():
        assert set(contract.input_classes) == set(CacheInputClass), consumer
        for disposition in contract.input_classes.values():
            assert bool(disposition.fields) != bool(disposition.exclusion_reason)
            if disposition.fields:
                assert set(disposition.fields) <= set(contract.fields)
        classified_fields = {
            field_name
            for disposition in contract.input_classes.values()
            for field_name in disposition.fields
        }
        assert classified_fields == set(contract.fields), consumer


def test_checked_payload_rejects_missing_and_unknown_dimensions() -> None:
    contract = CACHE_CONSUMER_CONTRACTS[CacheConsumer.INPUT_SNAPSHOT]
    valid = {
        "schema_version": 1,
        "provider": "file",
        "descriptor": {"path": "input.csv"},
    }

    checked = checked_cache_inputs(CacheConsumer.INPUT_SNAPSHOT, valid)

    assert checked.values == MappingProxyType(valid)
    assert checked.ordered_values == (1, "file", {"path": "input.csv"})
    with pytest.raises(ValueError, match="missing.*descriptor"):
        checked_cache_inputs(
            CacheConsumer.INPUT_SNAPSHOT,
            {"schema_version": 1, "provider": "file"},
        )
    with pytest.raises(ValueError, match="unknown.*extra"):
        checked_cache_inputs(
            CacheConsumer.INPUT_SNAPSHOT,
            {**valid, "extra": True},
        )
    assert contract.version >= 1


def test_contract_builders_reject_ambiguous_or_empty_classifications() -> None:
    with pytest.raises(ValueError, match="both consumed and excluded"):
        _consumer_contract(
            CacheConsumer.INPUT_SNAPSHOT,
            version=1,
            fields=("identity",),
            consumed={CacheInputClass.NODE_CONFIG: ("identity",)},
            excluded={CacheInputClass.NODE_CONFIG: "duplicate"},
        )
    with pytest.raises(ValueError, match="rationale"):
        CacheConfigFieldClassification(exclusion_reason="   ")
    with pytest.raises(ValueError, match="rationale"):
        CacheInputDisposition(exclusion_reason="   ")


def test_every_recognised_config_field_has_an_explicit_cache_classification() -> None:
    validate_cache_config_field_classifications()
    assert set(CACHE_CONFIG_FIELD_CLASSIFICATIONS) == set(NodeType)
    for node_type, valid_fields in VALID_KEYS.items():
        assert set(CACHE_CONFIG_FIELD_CLASSIFICATIONS[node_type]) == set(valid_fields)


def test_reflective_coverage_rejects_a_new_unclassified_config_field() -> None:
    expanded = dict(VALID_KEYS)
    expanded[NodeType.POLARS] = VALID_KEYS[NodeType.POLARS] | {"new_output_field"}

    with pytest.raises(RuntimeError, match="polars.*new_output_field"):
        validate_cache_config_field_classifications(expanded)


def test_every_presentation_config_exclusion_has_a_rationale() -> None:
    exclusions = [
        classification
        for fields in CACHE_CONFIG_FIELD_CLASSIFICATIONS.values()
        for classification in fields.values()
        if classification.exclusion_reason
    ]
    assert exclusions
    assert all(item.input_class is None and item.exclusion_reason.strip() for item in exclusions)


def test_graph_identity_changes_for_execution_label_and_source_location() -> None:
    graph = _graph()
    baseline = graph_fingerprint(graph)

    assert graph_fingerprint(_replace_node(graph, "target", label="Other target")) != baseline
    assert graph_fingerprint(graph.model_copy(update={"source_file": "other/main.py"})) != baseline


@pytest.mark.parametrize(
    "changed",
    [
        lambda graph: graph.model_copy(update={"pipeline_name": "Other"}),
        lambda graph: graph.model_copy(update={"pipeline_description": "Other copy"}),
        lambda graph: graph.model_copy(update={"preserved_blocks": ["# other comment"]}),
        lambda graph: graph.model_copy(update={"warning": "other warning"}),
        lambda graph: graph.model_copy(update={"sources": ["live", "stress"]}),
        lambda graph: graph.model_copy(update={"active_source": "batch"}),
        lambda graph: _replace_node(graph, "target", description="Other description"),
        lambda graph: _replace_node(graph, "target", position={"x": 999.0, "y": 1.0}),
        lambda graph: _replace_node(graph, "target", react_flow_type="otherCanvasNode"),
        lambda graph: graph.model_copy(
            update={"edges": [graph.edges[0].model_copy(update={"id": "other-edge-id"})]}
        ),
    ],
)
def test_graph_identity_is_stable_for_presentation_only_changes(changed) -> None:
    graph = _graph()
    assert graph_fingerprint(changed(graph)) == graph_fingerprint(graph)


def test_dataframe_identity_consumes_label_and_source_location() -> None:
    graph = _graph()

    def key(candidate: PipelineGraph):
        return dataframe_execution_cache_key(
            candidate,
            node_id="target",
            namespace="test",
            source="live",
            profile=ExecutionProfile.LAZY_SINK,
            input_fingerprint="runtime:v1",
            execution_policy={"target_node_id": "target"},
        )

    baseline = key(graph)
    assert key(_replace_node(graph, "target", label="Other target")) != baseline
    assert key(graph.model_copy(update={"source_file": "other/main.py"})) != baseline


def test_model_contract_key_routes_through_the_checked_contract(monkeypatch) -> None:
    import haute._model_scorer as scorer

    seen: list[CacheConsumer] = []
    real = scorer.checked_cache_inputs

    def recording(consumer, values):
        seen.append(consumer)
        return real(consumer, values)

    monkeypatch.setattr(scorer, "checked_cache_inputs", recording)
    model = SimpleNamespace(
        feature_names=["age", "region"],
        cat_feature_names=["region"],
        offset_column=None,
    )

    assert scorer._model_feature_contract_key(model) == (
        ("age", "region"),
        frozenset({"region"}),
        None,
    )
    assert seen == [CacheConsumer.MODEL_CONTRACT]


def test_input_snapshot_identity_routes_through_the_checked_contract(monkeypatch) -> None:
    import haute._source_cache as source_cache

    seen: list[CacheConsumer] = []
    real = source_cache.checked_cache_inputs

    def recording(consumer, values):
        seen.append(consumer)
        return real(consumer, values)

    monkeypatch.setattr(source_cache, "checked_cache_inputs", recording)
    identity = source_cache.SourceCacheIdentity(
        provider="database",
        descriptor={"query": "SELECT * FROM quotes"},
    )

    assert identity.payload == {
        "schema_version": 1,
        "provider": "database",
        "descriptor": {"query": "SELECT * FROM quotes"},
    }
    assert seen == [CacheConsumer.INPUT_SNAPSHOT, CacheConsumer.INPUT_SNAPSHOT]
