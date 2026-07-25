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


def test_data_input_out_of_cwd_absolute_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct execution rejects absolute file inputs outside its project root."""
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
        with pytest.raises(ValueError, match="outside the project root"):
            execute_graph(graph, target_node_id="src")
    finally:
        set_project_root(original)
        _preview_cache.invalidate()


@pytest.mark.parametrize("escape", ["../outside.csv", r"nested\..\..\outside.csv"])
def test_direct_executor_rejects_relative_and_mixed_separator_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    escape: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    outside = tmp_path.parent / "outside.csv"
    outside.write_text(_CSV_TEXT, encoding="utf-8")

    with pytest.raises(ValueError, match="outside the project root"):
        execute_graph(_data_input_graph(escape), target_node_id="src")


def test_direct_executor_rejects_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    outside = tmp_path.parent / "outside-data"
    outside.mkdir()
    (outside / "customers.csv").write_text(_CSV_TEXT, encoding="utf-8")
    link = tmp_path / "linked-data"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="outside the project root"):
        execute_graph(_data_input_graph("linked-data/customers.csv"), target_node_id="src")

    (outside / "pipeline.py").write_text("# symlink target", encoding="utf-8")
    symlinked_source_graph = _data_input_graph("customers.csv").model_copy(
        update={"source_file": str(link / "pipeline.py")}
    )
    with pytest.raises(ValueError, match="outside the project root"):
        execute_graph(symlinked_source_graph, target_node_id="src")


def test_selected_external_pipeline_uses_its_parent_as_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    external_root = tmp_path.parent / f"{tmp_path.name}-external-project"
    external_root.mkdir()
    source_file = external_root / "main.py"
    source_file.write_text("# selected pipeline", encoding="utf-8")
    data_path = external_root / "data" / "customers.csv"
    data_path.parent.mkdir()
    data_path.write_text(_CSV_TEXT, encoding="utf-8")
    graph = _data_input_graph("data/customers.csv").model_copy(
        update={"source_file": str(source_file)}
    )

    results = execute_graph(graph, target_node_id="src")

    assert results["src"].status == "ok", results["src"].error
    assert results["src"].preview == _EXPECTED_RECORDS


def test_http_graph_cannot_select_an_external_pipeline_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from haute.routes.pipeline import _validate_runtime_input_paths

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    external_root = tmp_path.parent / f"{tmp_path.name}-untrusted-http-root"
    external_root.mkdir()
    source_file = external_root / "main.py"
    source_file.write_text("# untrusted request source", encoding="utf-8")
    data_path = external_root / "customers.csv"
    data_path.write_text(_CSV_TEXT, encoding="utf-8")
    graph = _data_input_graph("customers.csv").model_copy(update={"source_file": str(source_file)})

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 403
