"""Direct tests for shared runtime-path validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes.pipeline import _validate_runtime_input_paths


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "node_type",
    [NodeType.DATA_SOURCE, NodeType.API_INPUT, NodeType.EXTERNAL_FILE],
)
def test_validate_runtime_input_paths_rejects_project_escape_for_file_backed_nodes(
    node_type: NodeType,
) -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(
                    label="n1",
                    nodeType=node_type,
                    config={"path": "../escape.parquet"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


def test_validate_runtime_input_paths_maps_embedded_null_byte_to_400() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(
                    label="n1",
                    nodeType=NodeType.API_INPUT,
                    config={"path": "bad\x00name.parquet"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 400
    assert "null byte" in exc_info.value.detail


@pytest.mark.parametrize(
    "path_field",
    ["artifact_path", "feature_contract_path"],
)
def test_validate_runtime_input_paths_rejects_model_score_escape(
    path_field: str,
) -> None:
    """modelScore artifact/contract paths must be confined like every input.

    The executor deliberately does not enforce the project root for these
    fields, relying on this route guard to gate route-driven flows. A path that
    escapes the project root must be rejected here, exactly like a dataSource
    ``path``.
    """
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="score",
                data=NodeData(
                    label="score",
                    nodeType=NodeType.MODEL_SCORE,
                    config={path_field: "../escape.json"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


def test_validate_runtime_input_paths_checks_optimiser_apply_file_mode_only() -> None:
    file_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="apply",
                data=NodeData(
                    label="apply",
                    nodeType=NodeType.OPTIMISER_APPLY,
                    config={"sourceType": "file", "artifact_path": "../escape.json"},
                ),
            )
        ],
        edges=[],
    )
    job_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="apply",
                data=NodeData(
                    label="apply",
                    nodeType=NodeType.OPTIMISER_APPLY,
                    config={"sourceType": "job", "artifact_path": "../escape.json"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(file_graph)
    assert exc_info.value.status_code == 403

    _validate_runtime_input_paths(job_graph)
