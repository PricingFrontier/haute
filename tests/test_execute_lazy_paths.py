"""Direct tests for canonical lazy execution graph path rewriting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from haute._execute_lazy import _resolve_graph_paths
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph


def _node(node_id: str, node_type: NodeType, config: dict[str, object]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config),
    )


def test_resolve_graph_paths_rewrites_file_backed_nodes_only() -> None:
    graph = PipelineGraph(
        source_file="pipelines/pricing.py",
        nodes=[
            _node("api", NodeType.API_INPUT, {"path": "data/api.json"}),
            _node("source", NodeType.DATA_SOURCE, {"path": "data/source.parquet"}),
            _node("sink", NodeType.DATA_SINK, {"path": "outputs/result.parquet"}),
            _node("transform", NodeType.POLARS, {"code": "df"}),
        ],
    )

    calls: list[str] = []

    def fake_resolve(raw_path: str, **_: object) -> Path:
        calls.append(raw_path)
        return Path("resolved") / raw_path.replace("/", "_")

    with patch("haute.execution.resolve_runtime_file_path", side_effect=fake_resolve):
        resolved = _resolve_graph_paths(graph)

    assert calls == [
        "data/api.json",
        "data/source.parquet",
        "outputs/result.parquet",
    ]
    assert resolved.node_map["api"].data.config["path"] == str(Path("resolved") / "data_api.json")
    assert resolved.node_map["source"].data.config["path"] == str(
        Path("resolved") / "data_source.parquet"
    )
    assert resolved.node_map["sink"].data.config["path"] == str(
        Path("resolved") / "outputs_result.parquet"
    )
    assert resolved.node_map["transform"].data.config["code"] == "df"


def test_resolve_graph_paths_rewrites_optimiser_apply_artifact_path_for_file_source() -> None:
    graph = PipelineGraph(
        source_file="pipelines/pricing.py",
        nodes=[
            _node(
                "apply",
                NodeType.OPTIMISER_APPLY,
                {"sourceType": "file", "artifact_path": "artifacts/solve.json"},
            )
        ],
    )

    with patch(
        "haute.execution.resolve_runtime_file_path",
        return_value=Path("resolved") / "artifacts_solve.json",
    ) as mock_resolve:
        resolved = _resolve_graph_paths(graph)

    mock_resolve.assert_called_once()
    assert resolved.node_map["apply"].data.config["artifact_path"] == str(
        Path("resolved") / "artifacts_solve.json"
    )


def test_resolve_graph_paths_does_not_rewrite_non_file_optimiser_apply() -> None:
    graph = PipelineGraph(
        source_file="pipelines/pricing.py",
        nodes=[
            _node(
                "apply",
                NodeType.OPTIMISER_APPLY,
                {"sourceType": "job", "artifact_path": "artifacts/solve.json"},
            )
        ],
    )

    with patch("haute.execution.resolve_runtime_file_path") as mock_resolve:
        resolved = _resolve_graph_paths(graph)

    mock_resolve.assert_not_called()
    assert resolved == graph


def test_resolve_graph_paths_without_source_file_is_identity() -> None:
    graph = PipelineGraph(
        nodes=[_node("api", NodeType.API_INPUT, {"path": "data/api.json"})],
    )

    with patch("haute.execution.resolve_runtime_file_path") as mock_resolve:
        resolved = _resolve_graph_paths(graph)

    mock_resolve.assert_not_called()
    assert resolved == graph
