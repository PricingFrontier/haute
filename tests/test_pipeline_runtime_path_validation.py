"""Direct tests for shared runtime-path validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes._save_pipeline import SavePipelineService
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


# ---------------------------------------------------------------------------
# Case-only config-sidecar collisions.
#
# Node labels differing only in case ("Foo" / "foo") sanitize to distinct
# Python identifiers, but their config sidecars (config/<type>/Foo.json vs
# config/<type>/foo.json) are the SAME file on the case-insensitive
# filesystems macOS and Windows default to — the second write would silently
# overwrite the first. The save-time guard therefore rejects the collision on
# every platform. These tests live in this file because it runs on the
# macOS/Windows platform-smoke CI leg, i.e. on real case-insensitive
# filesystems.
# ---------------------------------------------------------------------------


def _config_node(node_id: str, label: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label,
            nodeType=NodeType.DATA_SOURCE,
            config={"path": f"{node_id}.csv"},
        ),
    )


def test_case_only_label_collision_rejected_before_any_config_write(
    tmp_path: Path,
) -> None:
    """Two same-type nodes whose labels differ only in case must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(
        nodes=[_config_node("a", "Foo"), _config_node("b", "foo")],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "Duplicate config sidecar path" in exc_info.value.detail
    assert "config/data_source/Foo.json" in exc_info.value.detail
    assert "config/data_source/foo.json" in exc_info.value.detail
    # Rejected before any write: neither casing reached the disk.
    assert not (tmp_path / "config").exists()


def test_case_only_collision_between_parent_and_submodel_rejected(
    tmp_path: Path,
) -> None:
    """A submodel child colliding case-only with a parent node must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("parent", "Shared")], edges=[])
    child = _config_node("child", "shared")
    graph.submodels = {
        "pricing": {
            "file": "modules/pricing.py",
            "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "Duplicate config sidecar path" in exc_info.value.detail
    assert not (tmp_path / "config").exists()


def test_distinct_labels_still_write_one_sidecar_each(tmp_path: Path) -> None:
    """The casefold guard must not over-trigger on genuinely distinct names."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(
        nodes=[_config_node("a", "Foo"), _config_node("b", "Bar")],
        edges=[],
    )

    svc._write_config_files(graph)

    assert (tmp_path / "config" / "data_source" / "Foo.json").is_file()
    assert (tmp_path / "config" / "data_source" / "Bar.json").is_file()


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
