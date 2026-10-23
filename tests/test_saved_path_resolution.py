"""A data path saved on Windows reads the same file on Linux, in the editor and in deploy.

The editor's file browser reports paths from the project root (the directory
``haute serve`` runs in), and on Windows it used to write them with backslashes.
The API Input resolved such a path through the shared runtime resolver (project
root and pipeline dir both tried, backslashes read as separators), but a direct
Parquet Data Input and deploy's schema inference and scoring joined it onto the
pipeline dir alone. On Linux CI ``data\\x.parquet`` then named one file with a
backslash in it, under ``rating/``, and the dry-run deploy failed. Every reader now
resolves as deploy bundles: pipeline dir first, then the project root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute._path_resolution import resolve_graph_source_path
from haute._polars_io_registry import anchor_config_source_path
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.deploy._schema import _resolve_pipeline_path
from haute.deploy._scorer import _resolve_runtime_graph_paths


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A project with its pipeline in ``rating/``, served from the project root."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "rating").mkdir()
    try:
        yield tmp_path
    finally:
        set_project_root(original)


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _anchored(path: str, base_dir: Path) -> Path:
    return Path(anchor_config_source_path({"path": path}, base_dir)["path"]).resolve()


def test_a_direct_input_finds_a_project_root_path_written_on_windows(project: Path) -> None:
    saved = _file(project / "data" / "policies.parquet")

    assert _anchored("data\\policies.parquet", project / "rating") == saved
    assert _anchored("data/policies.parquet", project / "rating") == saved


def test_a_direct_inputs_pipeline_relative_file_wins(project: Path) -> None:
    _file(project / "data" / "policies.parquet")
    beside_pipeline = _file(project / "rating" / "data" / "policies.parquet")

    assert _anchored("data\\policies.parquet", project / "rating") == beside_pipeline


def test_a_direct_input_found_nowhere_keeps_the_pipeline_anchoring(project: Path) -> None:
    # A glob names no one file, so it resolves where it always has.
    assert (
        _anchored("data/*.parquet", project / "rating")
        == (project / "rating" / "data" / "*.parquet").resolve()
    )


def test_an_absolute_direct_input_path_is_left_alone(project: Path) -> None:
    absolute = str(project / "data" / "policies.parquet")

    assert anchor_config_source_path({"path": absolute}, project / "rating")["path"] == absolute


def _graph(project: Path, path: str) -> PipelineGraph:
    node = GraphNode(
        id="policies",
        data=NodeData(label="policies", nodeType=NodeType.DATA_INPUT, config={"path": path}),
    )
    return PipelineGraph(nodes=[node], edges=[], source_file=str(project / "rating" / "main.py"))


def test_deploy_resolves_a_windows_path_as_bundling_does(project: Path) -> None:
    saved = _file(project / "data" / "policies.parquet")
    graph = _graph(project, "data\\policies.parquet")

    assert Path(_resolve_pipeline_path(graph, "data\\policies.parquet")) == saved.resolve()
    [scored] = _resolve_runtime_graph_paths(graph).nodes
    assert Path(scored.data.config["path"]) == saved.resolve()
    assert resolve_graph_source_path("data\\policies.parquet", graph.source_file or "") == str(
        saved.resolve()
    )


def test_deploy_prefers_the_file_beside_the_pipeline(project: Path) -> None:
    _file(project / "data" / "policies.parquet")
    beside_pipeline = _file(project / "rating" / "data" / "policies.parquet")
    graph = _graph(project, "data/policies.parquet")

    assert Path(_resolve_pipeline_path(graph, "data/policies.parquet")) == beside_pipeline.resolve()


def test_the_file_browser_writes_forward_slashes(project: Path) -> None:
    from haute.server import app

    _file(project / "data" / "quotes" / "policies.parquet")

    listing = TestClient(app).get("/api/files", params={"dir": "data/quotes"}).json()

    assert listing["dir"] == "data/quotes"
    assert [item["path"] for item in listing["items"]] == ["data/quotes/policies.parquet"]
