"""Shared test fixtures and helpers for the haute test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from haute._sandbox import _get_project_root, set_project_root
from haute.executor import _preview_cache
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.trace import _cache as _trace_cache


@pytest.fixture(autouse=True)
def _clear_trace_caches():
    """Invalidate the global trace and preview caches between tests.

    The FingerprintCache is a module-level singleton.  Without clearing it,
    a prior test's cached DataFrames can bleed into the next test if they
    happen to share the same fingerprint (e.g., same node ids, same code).
    """
    _trace_cache.invalidate()
    _preview_cache.invalidate()
    yield
    _trace_cache.invalidate()
    _preview_cache.invalidate()


@pytest.fixture(autouse=True)
def _clear_pipeline_dir_cache():
    """Prevent the lru_cache on pipeline_dir from leaking real paths into tests.

    Without this, save tests that trigger ``_remove_stale_config_files`` will
    scan and delete real config files from ``rating/config/`` because the
    cached ``pipeline_dir()`` points at the real project, not the test's
    ``tmp_path``.
    """
    from haute.routes._helpers import pipeline_dir

    pipeline_dir.cache_clear()
    yield
    pipeline_dir.cache_clear()


@pytest.fixture()
def _widen_sandbox_root():
    """Allow tests to load files from temp directories.

    Sets the sandbox project root to ``/`` for the duration of each test
    so that ``validate_project_path`` accepts paths in ``/tmp``.
    Restores the original root afterwards.
    """
    original = _get_project_root()
    set_project_root(Path("/"))
    yield
    set_project_root(original)


# ---------------------------------------------------------------------------
# Graph builder helpers — used across test_executor, test_trace, etc.
# ---------------------------------------------------------------------------


def make_source_node(nid: str, path: str = "data.parquet") -> GraphNode:
    """Build a minimal dataSource node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="dataSource", config={"path": path}),
    )


def make_transform_node(nid: str, code: str = "") -> GraphNode:
    """Build a minimal transform node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="polars", config={"code": code}),
    )


def make_output_node(nid: str, fields: list[str] | None = None) -> GraphNode:
    """Build a minimal output node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="output", config={"fields": fields or []}),
    )


def make_edge(src: str, tgt: str) -> GraphEdge:
    """Build a minimal edge."""
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def make_node(d: dict) -> GraphNode:
    """Build a GraphNode from a raw dict (model_validate shorthand)."""
    return GraphNode.model_validate(d)


def make_graph(d: dict) -> PipelineGraph:
    """Build a PipelineGraph from a raw dict (model_validate shorthand)."""
    return PipelineGraph.model_validate(d)


def compile_node_code(code: str) -> None:
    """Verify generated node code compiles inside a pipeline context.

    Shared by test_codegen.py and test_codegen_builders.py.
    """
    wrapper = f"import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n{code}\n"
    compile(wrapper, "<test>", "exec")


# ---------------------------------------------------------------------------
# CLI runner fixture — shared across all test_cli_*.py files
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """Provide a Click CLI test runner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# FastAPI TestClient fixtures — shared across route test files
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with ``raise_server_exceptions=False``.

    Most route tests assert on HTTP status codes, so server exceptions are
    translated to responses rather than raised.
    """
    from fastapi.testclient import TestClient

    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)
