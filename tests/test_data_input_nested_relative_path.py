"""Regression: a ``DATA_INPUT`` whose file ``path`` is pipeline-directory-
relative must resolve to the same file whether the pipeline lives at the project
root or in a subdirectory, and regardless of cwd.

The canonical Data Input resolver must anchor relative file paths to the
pipeline directory. With the standard ``rating/main.py`` layout and the server
run from the project root, it must read ``<root>/rating/data/...`` rather than
``<root>/data/...``.

WHY THE FIX ANCHORS AT CALL TIME.  The graph-level resolver
(``execution.canonical_dataframe_execution_graph``) pre-resolves the path, but
only when ``graph.source_file`` is set. The OUTPUT-editor dry-run route
(``/api/output-assemble/dry-run``) — the same class of empty-``source_file`` flow
that surfaced the apiInput bug — carries no ``source_file``, so the executed
graph has none and the builder itself must anchor the relative path. Both tests
below therefore drive an empty ``source_file``.

This mirrors tests/test_apiinput_nested_relative_path.py: a NESTED pipeline, a
RELATIVE pipeline-dir-relative ``path``, cwd kept at the project root (never the
pipeline dir), and no absolute path anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph

#: The Data Input ``path`` written into the node config — RELATIVE and
#: pipeline-directory-relative (resolves under ``rating/`` at runtime).
_RELATIVE_DATA_PATH = "data/customers.csv"
_CSV_TEXT = "customer_id,premium\n1,100\n2,250\n"
_EXPECTED_RECORDS = [
    {"customer_id": 1, "premium": 100},
    {"customer_id": 2, "premium": 250},
]


@pytest.fixture()
def nested_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A project whose pipeline lives in a SUBDIRECTORY, with cwd pinned at the
    project ROOT (never the pipeline dir) — the configuration the bug needs.

    Yields the on-disk absolute data path; the node config carries the RELATIVE
    ``path`` which must anchor to ``<root>/rating/data/...``.
    """
    monkeypatch.chdir(tmp_path)  # cwd == project root, NOT the pipeline dir
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()

    # haute.toml declares the nested pipeline — the anchor both the cache route
    # and (post-fix) the executor resolve a relative data path against.
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    pipeline_directory = tmp_path / "rating"
    pipeline_directory.mkdir(parents=True, exist_ok=True)
    pipeline_directory.joinpath("main.py").write_text("import haute\npipeline = haute.Pipeline()\n")

    # Data lives under the PIPELINE dir (<root>/rating/data/...), where a
    # pipeline-dir-relative path points — NOT under <root>/data/...
    data_path = pipeline_directory / "data" / "customers.csv"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_CSV_TEXT)

    yield data_path

    set_project_root(original)
    _preview_cache.invalidate()


def _data_input_graph(path: str) -> PipelineGraph:
    """A lone file Data Input node with NO ``source_file`` (as the frontend
    and the dry-run route produce it)."""
    return PipelineGraph(
        source_file="",
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(
                    label="src",
                    nodeType=NodeType.DATA_INPUT,
                    config={
                        "inputType": "file",
                        "format": "csv",
                        "mode": "scan",
                        "cacheMode": "direct",
                        "path": path,
                        "arguments": {},
                    },
                ),
            ),
        ],
        edges=[],
    )


def test_data_input_reads_pipeline_relative_path_from_project_root(nested_project) -> None:
    """Executor core: a dataInput whose ``path`` is relative reads the pipeline-dir
    file, with cwd at the project root and no ``source_file`` to anchor to.

    Pre-fix this failed (or read the wrong file) because the raw relative path
    resolved against cwd (``<root>/data/customers.csv``, absent) instead of the
    pipeline dir (``<root>/rating/data/customers.csv``) where the file lives.
    """
    graph = _data_input_graph(_RELATIVE_DATA_PATH)

    results = execute_graph(graph, target_node_id="src")

    assert results["src"].status == "ok", results["src"].error
    assert results["src"].row_count == 2
    assert results["src"].preview == _EXPECTED_RECORDS


def test_data_input_out_of_cwd_absolute_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ABSOLUTE data path that resolves OUTSIDE cwd is loaded, NOT rejected and
    NOT re-anchored to the pipeline dir.

    ``canonical_dataframe_execution_graph`` may already have resolved the path
    against ``graph.source_file`` to somewhere outside cwd (a codegen round-trip
    re-execute, or ``haute run <pipeline outside cwd>``); the executor must load
    it. This stage does not enforce project-root containment (that gate lives on
    the route boundary), and ``read_source`` imposes none either. Regression
    guard for tests/test_e2e.py::test_full_lifecycle.
    """
    monkeypatch.chdir(tmp_path)  # cwd == project root
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()
    try:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
        (tmp_path / "rating").mkdir(parents=True, exist_ok=True)

        # The data file sits OUTSIDE the project root entirely.
        outside = tmp_path.parent / "elsewhere" / "customers.csv"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text(_CSV_TEXT)

        graph = _data_input_graph(str(outside))
        results = execute_graph(graph, target_node_id="src")

        assert results["src"].status == "ok", results["src"].error
        assert results["src"].preview == _EXPECTED_RECORDS
    finally:
        set_project_root(original)
        _preview_cache.invalidate()
