"""Regression: a FLAT (CSV/parquet) ``API_INPUT`` whose data ``path`` is pipeline-
directory-relative must resolve to the same file whether the pipeline lives at the
project root or in a subdirectory, and regardless of cwd.

THE BUG (third sibling of the v2 apiInput / DATA_INPUT / EXTERNAL_FILE cwd
path-resolution bug). ``_builders._build_api_input`` has two codecs: the JSON one
(``_make_api_source_v2``), anchored to the pipeline dir by 09a5500f, and the FLAT
one (``_api_source_flat``, taken for a non-JSON ``.csv`` / ``.parquet`` path). The
flat closure handed the RAW ``config`` — carrying a relative ``path`` — to
``_io.read_data_source`` → ``build_data_source_adapter`` → ``read_source``, which
resolves a relative path against ``cwd``. The cache-build route
(``routes.json_cache._resolve_data_path``) and codegen anchor a relative data
path to the PIPELINE directory instead. So with the standard ``rating/main.py``
layout and the server run from the project root, the executor read
``<root>/data/...`` while the file actually lived under ``<root>/rating/data/...``.
The two only agreed when cwd == the pipeline dir; otherwise the source read the
wrong file or failed with "No such file or directory". 09a5500f fixed only the
JSON codec; this closes the flat codec with the identical fix
(the canonical file-input resolver) already applied to the two ``DATA_INPUT``
call sites in tests/test_data_input_nested_relative_path.py.

WHY THE FIX ANCHORS AT CALL TIME / WHY ``source_file`` IS EMPTY. Same as the
DATA_INPUT sibling: the graph-level resolver
(``execution.canonical_dataframe_execution_graph``) pre-resolves the path only
when ``graph.source_file`` is set. The empty-``source_file`` flows — the frontend
canvas graph and the OUTPUT-editor dry-run route
(``/api/output-assemble/dry-run``), which does NOT call ``_ensure_source_file`` —
carry no ``source_file``, so the executed graph has none and the builder itself
must anchor the relative path. This test drives an empty ``source_file`` to
reproduce that condition.

Note ``is_json_api_input_path(path)`` must be False for the flat branch to be
taken — the ``path`` is a ``.csv`` (not ``.json`` / ``.jsonl``), so the executor
dispatches to ``_api_source_flat`` rather than the v2 JSON shred codec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph

#: The apiInput ``path`` written into the node config — RELATIVE, pipeline-
#: directory-relative (resolves under ``rating/`` at runtime), and FLAT (``.csv``
#: so ``is_json_api_input_path`` is False and the flat codec is taken).
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


def _api_input_config(path: str) -> dict[str, Any]:
    """A flat-file apiInput config carrying a (relative) ``path``.

    ``sourceType`` defaults to ``flat_file`` inside ``build_data_source_adapter``;
    spelling it out matches how the frontend serialises a flat apiInput and keeps
    this parallel with the DATA_INPUT sibling test.
    """
    return {"sourceType": "flat_file", "path": path}


def _api_input_graph(path: str) -> PipelineGraph:
    """A lone flat-file apiInput node with NO ``source_file`` (as the frontend
    and the dry-run route produce it)."""
    return PipelineGraph(
        source_file="",
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(
                    label="src",
                    nodeType=NodeType.API_INPUT,
                    config=_api_input_config(path),
                ),
            ),
        ],
        edges=[],
    )


def test_apiinput_flat_reads_pipeline_relative_path_from_project_root(nested_project) -> None:
    """Executor core: a flat apiInput whose ``path`` is relative reads the
    pipeline-dir file, with cwd at the project root and no ``source_file`` to
    anchor to.

    Pre-fix this failed (or read the wrong file) because the raw relative path
    resolved against cwd (``<root>/data/customers.csv``, absent) instead of the
    pipeline dir (``<root>/rating/data/customers.csv``) where the file lives.
    """
    graph = _api_input_graph(_RELATIVE_DATA_PATH)

    results = execute_graph(graph, target_node_id="src")

    assert results["src"].status == "ok", results["src"].error
    assert results["src"].row_count == 2
    assert results["src"].preview == _EXPECTED_RECORDS


def test_apiinput_flat_out_of_cwd_absolute_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ABSOLUTE data path that resolves OUTSIDE cwd is loaded, NOT rejected and
    NOT re-anchored to the pipeline dir.

    ``canonical_dataframe_execution_graph`` may already have resolved the path
    against ``graph.source_file`` to somewhere outside cwd (a codegen round-trip
    re-execute, or ``haute run <pipeline outside cwd>``); the executor must load
    it. This stage does not enforce project-root containment (that gate lives on
    the route boundary), and ``read_source`` imposes none either. Regression
    guard mirroring the DATA_INPUT sibling.
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

        graph = _api_input_graph(str(outside))
        results = execute_graph(graph, target_node_id="src")

        assert results["src"].status == "ok", results["src"].error
        assert results["src"].preview == _EXPECTED_RECORDS
    finally:
        set_project_root(original)
        _preview_cache.invalidate()
