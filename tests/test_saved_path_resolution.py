"""A data path saved on Windows reads the same file on Linux, in the editor and in deploy.

The editor's file browser reports paths from the project root (the directory
``haute serve`` runs in), and on Windows it used to write them with backslashes.
The API Input resolved such a path through the shared runtime resolver (project
root and pipeline dir both tried, backslashes read as separators), but a direct
Parquet Data Input and deploy's schema inference and scoring joined it onto the
pipeline dir alone. On Linux CI ``data\\x.parquet`` then named one file with a
backslash in it, under the pipeline dir, and the dry-run deploy failed. Every reader now
tries both places and never leaves the project root. The registry and the engine share
one rule, the project root first (``resolve_node_input_path``); deploy bundles a Data
Input's file from the pipeline dir first, as its specification states.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._path_resolution import (
    RuntimePathOutsideProjectError,
    resolve_graph_source_path,
    runtime_project_root_scope,
)
from haute._polars_io_registry import anchor_config_source_path
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.deploy._schema import _resolve_pipeline_path
from haute.deploy._scorer import _bundled_input_artifact, _resolve_runtime_graph_paths


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


def test_a_direct_input_falls_back_to_the_file_beside_the_pipeline(project: Path) -> None:
    beside_pipeline = _file(project / "rating" / "data" / "policies.parquet")

    assert _anchored("data\\policies.parquet", project / "rating") == beside_pipeline


def test_a_direct_input_found_nowhere_keeps_the_project_root_anchoring(project: Path) -> None:
    # A glob that matches nothing names no file, so it keeps the first candidate.
    assert (
        _anchored("data/*.parquet", project / "rating")
        == (project / "data" / "*.parquet").resolve()
    )


def test_the_registry_and_the_walker_read_the_project_root_file_over_a_decoy(
    project: Path,
) -> None:
    """A path present in both places names one file at every seam: the project root's."""
    from haute._input_providers import resolve_data_input
    from haute.execution import canonical_dataframe_execution_graph

    saved = project / "data" / "policies.parquet"
    saved.parent.mkdir()
    pl.DataFrame({"read": ["project root"]}).write_parquet(saved)
    decoy = project / "rating" / "data" / "policies.parquet"
    decoy.parent.mkdir()
    pl.DataFrame({"read": ["beside the pipeline"]}).write_parquet(decoy)
    config = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "data/policies.parquet",
        "arguments": {},
    }
    graph = _graph(project, "data/policies.parquet")
    graph = graph.model_copy(update={"nodes": [graph.nodes[0].with_config(config)]})

    registry = _anchored("data/policies.parquet", project / "rating")
    [walked] = canonical_dataframe_execution_graph(graph).nodes

    assert registry == Path(walked.data.config["path"]).resolve() == saved.resolve()
    rows = resolve_data_input(config, base_dir=project / "rating").collect()
    assert rows["read"].to_list() == ["project root"]


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
    [scored] = _resolve_runtime_graph_paths(graph, remap={}, request_input_ids=()).nodes
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


def test_a_path_never_resolves_outside_the_project(project: Path) -> None:
    # ``../outside.parquet`` from the pipeline dir is inside the project (missing);
    # from the project root it is outside (present). Deploy's validation admits only
    # the inside one, so schema inference, scoring and a direct input must read it.
    _file(project.parent / "outside.parquet")
    inside = (project / "outside.parquet").resolve()
    graph = _graph(project, "../outside.parquet")

    assert Path(_resolve_pipeline_path(graph, "../outside.parquet")) == inside
    [scored] = _resolve_runtime_graph_paths(graph, remap={}, request_input_ids=()).nodes
    assert Path(scored.data.config["path"]) == inside
    assert _anchored("../outside.parquet", project / "rating") == inside


def test_a_path_with_no_candidate_inside_the_project_is_refused(project: Path) -> None:
    graph = _graph(project, "../../elsewhere.parquet")

    with pytest.raises(RuntimePathOutsideProjectError):
        _resolve_pipeline_path(graph, "../../elsewhere.parquet")
    with pytest.raises(RuntimePathOutsideProjectError):
        _anchored("../../elsewhere.parquet", project / "rating")


def test_deploy_resolves_inside_the_root_it_validated(project: Path, tmp_path_factory) -> None:
    # Deploy's project root, not the serving process's, bounds schema inference.
    other = tmp_path_factory.mktemp("deployed")
    saved = _file(other / "data" / "policies.parquet")
    graph = PipelineGraph(
        nodes=_graph(project, "data/policies.parquet").nodes,
        edges=[],
        source_file=str(other / "rating" / "main.py"),
    )

    with runtime_project_root_scope(graph.source_file, project_root=other):
        assert Path(_resolve_pipeline_path(graph, "data/policies.parquet")) == saved.resolve()


def test_serving_resolves_only_the_paths_it_reads(project: Path, tmp_path_factory) -> None:
    # A served bundle keeps the source_file it was deployed from, which is not under the
    # serving process's project, so its inferred root is the pipeline dir and a ``../``
    # path would be refused. The request input's sample is replaced by the request and
    # the Data Input reads its bundled file, so neither path is resolved.
    deployed_from = tmp_path_factory.mktemp("deployed_from") / "rating" / "main.py"
    request = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes", nodeType=NodeType.API_INPUT, config={"path": "../data/quotes.json"}
        ),
    )
    bundled = GraphNode(
        id="policies",
        data=NodeData(
            label="policies",
            nodeType=NodeType.DATA_INPUT,
            config={
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "../data/policies.parquet",
                "arguments": {},
            },
        ),
    )
    graph = PipelineGraph(nodes=[request, bundled], edges=[], source_file=str(deployed_from))

    served = _resolve_runtime_graph_paths(
        graph,
        remap={"policies__policies.parquet": "/bundle/policies.parquet"},
        request_input_ids=("quotes",),
    )

    assert [node.data.config["path"] for node in served.nodes] == [
        "../data/quotes.json",
        "../data/policies.parquet",
    ]


def test_the_bundled_artifact_each_node_kind_reads() -> None:
    def node(node_id: str, node_type: NodeType, config: dict) -> GraphNode:
        return GraphNode(
            id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config)
        )

    direct = {"inputType": "file", "format": "parquet", "mode": "scan", "path": r"a\p.parquet"}
    snapshot = {"inputType": "file", "format": "csv", "mode": "scan", "path": "s.csv"}
    remap = {
        "direct__p.parquet": "/b/p.parquet",
        "load__model.pkl": "/b/model.pkl",
        "snap__snapshot.part-00001.parquet": "/b/1.parquet",
        "snap__snapshot.part-00000.parquet": "/b/0.parquet",
    }

    assert _bundled_input_artifact(node("direct", NodeType.DATA_INPUT, direct), remap) == (
        "/b/p.parquet"
    )
    assert _bundled_input_artifact(node("snap", NodeType.DATA_INPUT, snapshot), remap) == [
        "/b/0.parquet",
        "/b/1.parquet",
    ]
    assert _bundled_input_artifact(
        node("load", NodeType.EXTERNAL_FILE, {"path": "m/model.pkl"}), remap
    ) == ("/b/model.pkl")
    # No artifact: a snapshot input with no parts, a request input, or no bundle at all.
    assert _bundled_input_artifact(node("other", NodeType.DATA_INPUT, snapshot), remap) is None
    assert (
        _bundled_input_artifact(node("api", NodeType.API_INPUT, {"path": "q.json"}), remap) is None
    )
    assert _bundled_input_artifact(node("direct", NodeType.DATA_INPUT, direct), {}) is None
