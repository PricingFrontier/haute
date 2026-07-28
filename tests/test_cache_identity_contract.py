"""Central cache-fingerprint completeness contracts."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

import haute._cache as cache_module
from haute._cache import (
    ALGO_VERSION,
    CACHE_CONFIG_FIELD_CLASSIFICATIONS,
    CACHE_CONSUMER_CONTRACTS,
    CacheConfigFieldClassification,
    CacheConsumer,
    CacheInputClass,
    CacheInputDisposition,
    _consumer_contract,
    checked_cache_input_values,
    checked_cache_inputs,
    graph_fingerprint,
    validate_cache_config_field_classifications,
)
from haute._config_validation import VALID_KEYS
from haute._dataframe_execution_cache import dataframe_execution_cache_key
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.execution import dataframe_graph_input_fingerprint


def test_json_digest_encoders_use_canonical_json_except_persisted_feature_contract() -> None:
    """Raw ``json.dumps`` may not feed a digest for transient cache identity."""
    source_root = Path(__file__).parents[1] / "src" / "haute"
    violations: list[str] = []

    def is_json_dumps(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"json", "_json"}
        )

    def is_digest_call(call: ast.Call) -> bool:
        if isinstance(call.func, ast.Name):
            return call.func.id == "content_hash_bytes"
        return isinstance(call.func, ast.Attribute) and call.func.attr in {
            "sha256",
            "sha224",
            "sha384",
            "sha512",
            "blake2b",
        }

    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            dumped_names = {
                target.id
                for assignment in ast.walk(function)
                if isinstance(assignment, ast.Assign)
                and any(isinstance(target, ast.Name) for target in assignment.targets)
                and isinstance(assignment.value, ast.Call)
                and is_json_dumps(assignment.value)
                for target in assignment.targets
                if isinstance(target, ast.Name)
            }
            has_raw_digest = any(
                is_digest_call(call)
                and (
                    any(is_json_dumps(child) for child in ast.walk(call))
                    or any(
                        isinstance(child, ast.Name) and child.id in dumped_names
                        for child in ast.walk(call)
                    )
                )
                for call in ast.walk(function)
                if isinstance(call, ast.Call)
            )
            relative = path.relative_to(source_root).as_posix()
            if has_raw_digest and (relative, function.name) != (
                "modelling/_feature_contract.py",
                "_hash_payload",
            ):
                violations.append(f"{relative}:{function.name}")

    assert violations == []


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


def test_checked_contract_versions_advance_with_changed_byte_layouts() -> None:
    assert ALGO_VERSION == 8
    assert CACHE_CONSUMER_CONTRACTS[CacheConsumer.GRAPH_STRUCTURE].version == 2
    assert CACHE_CONSUMER_CONTRACTS[CacheConsumer.PREVIEW_TRACE].version == 3
    assert CACHE_CONSUMER_CONTRACTS[CacheConsumer.RUNTIME_GRAPH_INPUT].version == 2


def test_preview_contract_checks_lineage_graph_dimensions_individually() -> None:
    contract = CACHE_CONSUMER_CONTRACTS[CacheConsumer.PREVIEW_TRACE]

    assert "graph" not in contract.fields
    assert {"preamble", "source_file", "nodes", "edges"} <= set(contract.fields)
    assert contract.input_classes[CacheInputClass.NODE_CONFIG].fields == ("nodes",)
    assert set(contract.input_classes[CacheInputClass.EDGE_WIRING].fields) == {
        "edges",
        "selected_live_switch_path",
    }


def test_runtime_graph_input_declares_structural_identity_as_a_required_companion() -> None:
    contract = CACHE_CONSUMER_CONTRACTS[CacheConsumer.RUNTIME_GRAPH_INPUT]

    for input_class in (
        CacheInputClass.NODE_CONFIG,
        CacheInputClass.UPSTREAM_LINEAGE,
        CacheInputClass.EDGE_WIRING,
    ):
        disposition = contract.input_classes[input_class]
        assert disposition.fields == ()
        assert "structural" in disposition.exclusion_reason.lower()

    for consumer, structural_field in (
        (CacheConsumer.PREVIEW_TRACE, "nodes"),
        (CacheConsumer.DATAFRAME_EXECUTION, "lineage_fingerprint"),
        (CacheConsumer.DEPLOY_SCHEMA, "graph_fingerprint"),
    ):
        disposition = CACHE_CONSUMER_CONTRACTS[consumer].input_classes[CacheInputClass.NODE_CONFIG]
        assert structural_field in disposition.fields


@pytest.mark.parametrize(
    "record_name",
    ["GRAPH_NODE", "GRAPH_EDGE", "RUNTIME_INPUT_ENTRY", "LIVE_SWITCH_SELECTION"],
)
def test_nested_identity_records_reject_missing_and_unknown_fields(record_name: str) -> None:
    record = getattr(cache_module.CacheIdentityRecord, record_name)
    contract = cache_module.CACHE_IDENTITY_RECORD_CONTRACTS[record]
    valid = {field_name: None for field_name in contract.fields}

    checked = cache_module.checked_cache_identity_record(record, valid)

    assert checked["cache_record_schema"] == {
        "record": record.value,
        "version": contract.version,
    }
    assert set(checked) == {"cache_record_schema", *contract.fields}
    with pytest.raises(ValueError, match="missing"):
        cache_module.checked_cache_identity_record(record, dict(list(valid.items())[1:]))
    with pytest.raises(ValueError, match="unknown"):
        cache_module.checked_cache_identity_record(record, {**valid, "extra": True})


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
    assert checked_cache_input_values(CacheConsumer.INPUT_SNAPSHOT, valid) == (
        1,
        "file",
        {"path": "input.csv"},
    )
    assert checked_cache_input_values(
        CacheConsumer.INPUT_SNAPSHOT,
        {
            "descriptor": {"path": "input.csv"},
            "provider": "file",
            "schema_version": 1,
        },
    ) == (1, "file", {"path": "input.csv"})
    with pytest.raises(ValueError, match="missing.*descriptor"):
        checked_cache_inputs(
            CacheConsumer.INPUT_SNAPSHOT,
            {"schema_version": 1, "provider": "file"},
        )
    with pytest.raises(ValueError, match="missing.*descriptor"):
        checked_cache_input_values(
            CacheConsumer.INPUT_SNAPSHOT,
            {"schema_version": 1, "provider": "file"},
        )
    with pytest.raises(ValueError, match="unknown.*extra"):
        checked_cache_inputs(
            CacheConsumer.INPUT_SNAPSHOT,
            {**valid, "extra": True},
        )
    with pytest.raises(ValueError, match="unknown.*extra"):
        checked_cache_input_values(
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


@pytest.mark.parametrize(
    ("node_type", "base_config", "field", "changed_value"),
    [
        (
            NodeType.EXTERNAL_FILE,
            {"path": "artifact.bin", "fileType": "pickle", "modelClass": "Estimator"},
            "fileType",
            "joblib",
        ),
        (
            NodeType.EXTERNAL_FILE,
            {"path": "artifact.bin", "fileType": "pickle", "modelClass": "Estimator"},
            "modelClass",
            "OtherEstimator",
        ),
        (
            NodeType.MODEL_SCORE,
            {"output_column": "prediction", "task": "regression"},
            "output_column",
            "score",
        ),
        (
            NodeType.MODEL_SCORE,
            {"output_column": "prediction", "task": "regression"},
            "task",
            "classification",
        ),
    ],
)
def test_runtime_input_identity_is_composed_with_structural_node_config(
    tmp_path,
    monkeypatch,
    node_type: NodeType,
    base_config: dict[str, object],
    field: str,
    changed_value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifact.bin").write_bytes(b"artifact")
    node = _node("runtime", node_type=node_type, config=base_config)
    graph = PipelineGraph(nodes=[node], source_file=str(tmp_path / "pipeline.py"))
    changed_node = node.model_copy(
        update={
            "data": node.data.model_copy(update={"config": {**base_config, field: changed_value}})
        }
    )
    changed = graph.model_copy(update={"nodes": [changed_node]})

    runtime = dataframe_graph_input_fingerprint(
        graph,
        target_node_id="runtime",
        source="live",
    )
    changed_runtime = dataframe_graph_input_fingerprint(
        changed,
        target_node_id="runtime",
        source="live",
    )

    assert changed_runtime == runtime
    assert graph_fingerprint(changed) != graph_fingerprint(graph)
    assert (graph_fingerprint(changed), changed_runtime) != (graph_fingerprint(graph), runtime)


def test_database_snapshot_pointer_uses_configured_pipeline_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime identity signs the same relative SQLite snapshot that execution opens."""
    from haute._input_providers import source_cache_identity
    from haute._sandbox import _get_project_root, set_project_root
    from haute._source_cache import SourceCacheStore

    monkeypatch.chdir(tmp_path)
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        pipeline_dir = tmp_path / "rating"
        pipeline_dir.mkdir()
        (pipeline_dir / "main.py").write_text("# pipeline\n", encoding="utf-8")
        (tmp_path / "haute.toml").write_text(
            '[project]\npipeline = "rating/main.py"\n',
            encoding="utf-8",
        )
        config = {
            "inputType": "database",
            "format": "database",
            "cacheMode": "snapshot",
            "uri": "sqlite:///pricing.sqlite",
            "query": "SELECT 1",
            "arguments": {},
        }
        graph = PipelineGraph(
            nodes=[_node("database", node_type=NodeType.DATA_INPUT, config=config)],
        )
        identity = source_cache_identity(config, base_dir=pipeline_dir)
        pointer = SourceCacheStore(tmp_path).identity_path(identity) / "current.json"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text('{"generation":"first"}', encoding="utf-8")

        first = dataframe_graph_input_fingerprint(
            graph,
            target_node_id="database",
            source="live",
        )
        pointer.write_text('{"generation":"second-and-longer"}', encoding="utf-8")
        second = dataframe_graph_input_fingerprint(
            graph,
            target_node_id="database",
            source="live",
        )

        assert second != first
    finally:
        set_project_root(original_root)


def test_model_contract_key_routes_through_the_checked_value_projection(monkeypatch) -> None:
    import haute._model_scorer as scorer

    seen: list[CacheConsumer] = []
    real = scorer.checked_cache_input_values

    def recording(consumer, values):
        seen.append(consumer)
        return real(consumer, values)

    monkeypatch.setattr(scorer, "checked_cache_input_values", recording)
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
