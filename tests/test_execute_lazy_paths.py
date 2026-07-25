"""Direct tests for canonical lazy execution graph path rewriting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from haute._execute_lazy import _resolve_graph_paths
from haute._sandbox import set_project_root
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph


def _node(node_id: str, node_type: NodeType, config: dict[str, object]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config),
    )


def test_resolve_graph_paths_rewrites_inputs_but_not_outputs() -> None:
    graph = PipelineGraph(
        source_file="pipelines/pricing.py",
        nodes=[
            _node("api", NodeType.API_INPUT, {"path": "data/api.json"}),
            _node(
                "source",
                NodeType.DATA_INPUT,
                {
                    "inputType": "file",
                    "format": "parquet",
                    "mode": "scan",
                    "cacheMode": "direct",
                    "path": "data/source.parquet",
                    "arguments": {},
                },
            ),
            _node(
                "sink",
                NodeType.DATA_OUTPUT,
                {
                    "outputType": "file",
                    "format": "parquet",
                    "mode": "sink",
                    "path": "outputs/result.parquet",
                    "arguments": {},
                },
            ),
            _node("external", NodeType.EXTERNAL_FILE, {"path": "artifacts/helper.joblib"}),
            _node(
                "score",
                NodeType.MODEL_SCORE,
                {
                    "sourceType": "run",
                    "artifact_path": "artifacts/model.joblib",
                    "feature_contract_path": "artifacts/model.features.json",
                },
            ),
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
        "artifacts/helper.joblib",
        "artifacts/model.features.json",
    ]
    assert resolved.node_map["api"].data.config["path"] == str(Path("resolved") / "data_api.json")
    assert resolved.node_map["source"].data.config["path"] == str(
        Path("resolved") / "data_source.parquet"
    )
    assert resolved.node_map["sink"].data.config["path"] == "outputs/result.parquet"
    assert resolved.node_map["external"].data.config["path"] == str(
        Path("resolved") / "artifacts_helper.joblib"
    )
    # MLflow artifact paths are remote identifiers, not local project files.
    assert resolved.node_map["score"].data.config["artifact_path"] == "artifacts/model.joblib"
    assert resolved.node_map["score"].data.config["feature_contract_path"] == str(
        Path("resolved") / "artifacts_model.features.json"
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


def test_resolve_graph_paths_without_source_file_uses_current_project(
    tmp_path: Path,
) -> None:
    set_project_root(tmp_path)
    graph = PipelineGraph(
        nodes=[_node("api", NodeType.API_INPUT, {"path": "data/api.json"})],
    )

    resolved = _resolve_graph_paths(graph)

    assert resolved.node_map["api"].data.config["path"] == str(
        (tmp_path / "data" / "api.json").resolve()
    )


def test_lazy_graph_path_resolution_rejects_outside_project(
    tmp_path: Path,
) -> None:
    set_project_root(tmp_path)
    outside = tmp_path.parent / "outside.parquet"
    graph = PipelineGraph(
        nodes=[
            _node(
                "source",
                NodeType.DATA_INPUT,
                {
                    "inputType": "file",
                    "path": str(outside),
                },
            )
        ],
    )

    with pytest.raises(ValueError, match="outside the project root"):
        _resolve_graph_paths(graph)
