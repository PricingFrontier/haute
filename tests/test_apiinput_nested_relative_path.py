"""Regression: a v2 apiInput whose data ``path`` is pipeline-directory-relative
must resolve to the same file — and therefore the same cache — whether the
pipeline lives at the project root or in a subdirectory, and regardless of cwd.

THE BUG. The in-process executor's v2 apiInput source builder
(``_builders._make_api_source_v2``) closed over the RAW relative ``path`` and
handed it to ``_json_shred.load_v2_api_source``, whose cache-directory hash
(``_json_flatten._path_hash``) resolves a relative path against ``cwd``
(``Path(data_path).resolve()``).  The cache-build route
(``routes.json_cache._resolve_data_path``) and codegen
(``_codegen_builders._api_input_template`` → ``Path(__file__).parent / rel``)
instead anchor the relative path to the PIPELINE DIRECTORY.  So with the
standard ``rating/main.py`` layout and the server run from the project root,
the executor hashed ``<root>/data/quotes/...`` while the valid cache lived under
``<root>/rating/data/quotes/...``.  The two only agreed when cwd == the pipeline
dir; otherwise every execute-to-OUTPUT reported the spurious
"API Input data hasn't been cached ... or the cache is stale" error even though
the cache was valid and the apiInput's own input-preview rendered fine.

WHY THE EXISTING SUITE MISSED IT.  ``test_apiinput_multi_port_runtime.py`` masks
the bug three independent ways, each sufficient on its own:

* it passes an ABSOLUTE ``path`` (``"path": str(data_path)``) — an absolute path
  short-circuits resolution, so pipeline-vs-cwd never matters;
* it ``monkeypatch.chdir(tmp_path)`` — cwd becomes the (flat) pipeline dir, so
  cwd-relative and pipeline-relative coincide;
* it uses a FLAT layout — project root == the data dir, so the missing
  subdirectory can't surface.

This test removes all three: a NESTED pipeline (``rating/main.py``), a RELATIVE
pipeline-dir-relative ``path``, cwd kept at the project root (never the pipeline
dir), and no absolute path anywhere.

WHY ``source_file`` IS LEFT EMPTY.  The graph-level resolver
(``execution.canonical_dataframe_execution_graph``) can pre-resolve the path,
but only when ``graph.source_file`` is set.  The OUTPUT-editor dry-run route
(``/api/output-assemble/dry-run``) — the exact user action that failed — does
NOT call ``_ensure_source_file`` and the frontend graph carries no
``source_file``, so the executed graph has none and the builder must resolve the
path itself.  Both tests below therefore drive an empty ``source_file``: the
direct test asserts the executor core, the route test asserts the real endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph

# The canonical multi-port witness: one nested document shredded into four
# emit-true ports, reassembled by the OUTPUT mapping back into the same
# document (see test_output_nested_roundtrip for the identity round-trip).
from tests.test_output_nested_roundtrip import (
    _FIXTURE,
    _api_input_config,
    _expected_document,
    _output_mapping,
)

_PORTS = ["policies", "drivers", "licenses", "vehicles"]
#: The apiInput data ``path`` written into the node config — RELATIVE and
#: pipeline-directory-relative (resolves under ``rating/`` at runtime).
_RELATIVE_DATA_PATH = "data/data_model_example.json"


@pytest.fixture()
def nested_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A project whose pipeline lives in a SUBDIRECTORY, with cwd pinned at the
    project ROOT (never the pipeline dir) — the configuration the bug needs.

    Yields ``(config, data_path)`` where ``config`` carries the RELATIVE
    ``path`` and the per-port cache is already built at the pipeline-dir-anchored
    absolute location (mirroring what "Cache as Parquet" writes).
    """
    from haute.routes._helpers import pipeline_dir as _pipeline_dir

    monkeypatch.chdir(tmp_path)  # cwd == project root, NOT the pipeline dir
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()
    _pipeline_dir.cache_clear()

    # haute.toml declares the nested pipeline — the anchor both the cache route
    # and (post-fix) the executor resolve a relative data path against.
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    pipeline_directory = tmp_path / "rating"
    pipeline_directory.mkdir(parents=True, exist_ok=True)
    pipeline_directory.joinpath("main.py").write_text("import haute\npipeline = haute.Pipeline()\n")

    # Data + cache live under the PIPELINE dir (<root>/rating/data/...), where a
    # pipeline-dir-relative path points — NOT under <root>/data/...
    data_path = pipeline_directory / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())

    config = _api_input_config(data_path)
    config["path"] = _RELATIVE_DATA_PATH  # relative, overriding the abs helper

    # Build the per-port cache at the pipeline-dir-anchored ABSOLUTE path — the
    # cache the executor must find. _json_cache_dir is rooted at cwd, so this
    # lands under <root>/.haute_cache/working/json_<hash of the abs path>.
    build_per_port_cache(str(data_path), config, _json_cache_dir(str(data_path), "working"))

    yield config, data_path

    set_project_root(original)
    _preview_cache.invalidate()
    _pipeline_dir.cache_clear()


def _api_output_graph(config: dict[str, Any]) -> PipelineGraph:
    """Four-port apiInput → OUTPUT, with NO ``source_file`` (as the frontend and
    the dry-run route produce it)."""
    return PipelineGraph(
        source_file="",
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(label="quotes", nodeType=NodeType.API_INPUT, config=config),
            ),
            GraphNode(
                id="out",
                data=NodeData(
                    label="out",
                    nodeType=NodeType.OUTPUT,
                    config={"outputMapping": _output_mapping(), "outputFormat": "json"},
                ),
            ),
        ],
        edges=[
            GraphEdge(id=f"e_{p}", source="quotes", target="out", sourceHandle=p) for p in _PORTS
        ],
    )


def test_output_executes_with_pipeline_relative_path_from_project_root(nested_project) -> None:
    """Executor core: OUTPUT resolves through a nested apiInput whose data path is
    relative, with cwd at the project root and no ``source_file`` to anchor to.

    Pre-fix this failed with "Upstream node(s) failed: quotes: API Input data
    hasn't been cached ... or the cache is stale" because the raw relative path
    hashed against cwd (``<root>/data/...``) instead of the pipeline dir
    (``<root>/rating/data/...``) where the valid cache lives.
    """
    config, _data_path = nested_project
    graph = _api_output_graph(config)

    results = execute_graph(graph, target_node_id="out")

    # The apiInput frames load and the OUTPUT assembles — not a stale-cache error.
    assert results["quotes"].status == "ok", results["quotes"].error
    assert results["out"].status == "ok", results["out"].error
    # And it returns the expected reassembled document (the four frames, nested).
    assert results["out"].preview == _expected_document()


def test_output_dry_run_route_with_pipeline_relative_path(nested_project) -> None:
    """The exact failing user action: the OUTPUT editor's dry-run preview via
    ``/api/output-assemble/dry-run``. This route does not call
    ``_ensure_source_file``, so the executed graph has no ``source_file`` and the
    builder alone must anchor the relative path to the pipeline dir.
    """
    from fastapi.testclient import TestClient

    from haute.server import app

    config, _data_path = nested_project
    graph = _api_output_graph(config)
    graph_dict = graph.model_dump()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/output-assemble/dry-run",
        json={
            "graph": graph_dict,
            "node_id": "out",
            "output_mapping": _output_mapping(),
            "output_format": "json",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body.get("error")
    assert body["document"] == _expected_document()


# ─── unit coverage for the resolution helpers ─────────────────────────────


def test_configured_pipeline_dir_reads_haute_toml(tmp_path, monkeypatch) -> None:
    """``_configured_pipeline_dir`` returns the parent of ``[project].pipeline``
    from the selected project's haute.toml — the cache route's anchor."""
    from haute._builders import _configured_pipeline_dir

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    assert _configured_pipeline_dir() == (tmp_path / "rating")


def test_configured_pipeline_dir_none_without_toml(tmp_path, monkeypatch) -> None:
    """No haute.toml (or no ``[project].pipeline``) → ``None``, so resolution
    falls back to the selected project root rather than guessing a pipeline dir."""
    from haute._builders import _configured_pipeline_dir

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    assert _configured_pipeline_dir() is None


def test_resolve_runtime_data_path_passthrough_and_absolute(tmp_path, monkeypatch) -> None:
    """An empty path is returned verbatim; an absolute path passes straight
    through ``resolve_runtime_file_path`` unchanged (no pipeline-dir rewrite)."""
    from haute._builders import _resolve_runtime_data_path

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    assert _resolve_runtime_data_path("") == ""

    absolute = tmp_path / "rating" / "data" / "quotes.json"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("[]")
    assert _resolve_runtime_data_path(str(absolute)) == str(absolute)


def test_resolve_runtime_data_path_anchors_relative_to_pipeline_dir(tmp_path, monkeypatch) -> None:
    """A relative path resolves under the configured pipeline dir (existing-file
    wins), matching the cache route — NOT under cwd/project-root."""
    from haute._builders import _resolve_runtime_data_path

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    target = tmp_path / "rating" / "data" / "quotes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[]")

    resolved = _resolve_runtime_data_path("data/quotes.json")
    assert resolved == str(target)


def test_resolve_runtime_data_path_rejects_out_of_cwd_absolute(tmp_path, monkeypatch) -> None:
    """The builder's final runtime resolver enforces the active project root."""
    from haute._builders import _resolve_runtime_data_path

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    outside = tmp_path.parent / "elsewhere" / "data" / "api_input.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("[]")
    with pytest.raises(ValueError, match="outside the project root"):
        _resolve_runtime_data_path(str(outside))
