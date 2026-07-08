"""Shared test fixtures and helpers for the haute test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from haute._config_io import config_path_for_node
from haute._sandbox import _get_project_root, set_project_root
from haute.executor import _preview_cache
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.trace import _cache as _trace_cache

_TEST_LOCAL_SESSION_TOKEN = "pytest-haute-local-session-token"


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
def _local_session_auth_for_route_clients(monkeypatch: pytest.MonkeyPatch):
    """Make test HTTP clients use Haute's real local-session token path."""
    import httpx
    from starlette.testclient import TestClient as StarletteTestClient

    from haute._local_security import (
        SESSION_TOKEN_ENV,
        SESSION_TOKEN_HEADER,
        local_session_token,
    )

    monkeypatch.setenv(SESSION_TOKEN_ENV, _TEST_LOCAL_SESSION_TOKEN)

    def headers_with_session_token(headers) -> httpx.Headers:
        merged = httpx.Headers(headers or {})
        if "host" not in merged:
            merged["host"] = "localhost"
        if SESSION_TOKEN_HEADER not in merged:
            merged[SESSION_TOKEN_HEADER] = local_session_token()
        return merged

    original_test_client_init = StarletteTestClient.__init__

    def test_client_init_with_session_token(self, *args, **kwargs):
        kwargs.setdefault("base_url", "http://localhost")
        kwargs["headers"] = headers_with_session_token(kwargs.get("headers"))
        return original_test_client_init(self, *args, **kwargs)

    monkeypatch.setattr(StarletteTestClient, "__init__", test_client_init_with_session_token)

    original_async_client_init = httpx.AsyncClient.__init__

    def async_client_init_with_session_token(self, *args, **kwargs):
        if isinstance(kwargs.get("transport"), httpx.ASGITransport):
            if str(kwargs.get("base_url", "")).rstrip("/") == "http://testserver":
                kwargs["base_url"] = "http://localhost"
            kwargs["headers"] = headers_with_session_token(kwargs.get("headers"))
        return original_async_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", async_client_init_with_session_token)


@pytest.fixture(autouse=True)
def _clear_execution_admission_reservations():
    """Keep process-wide in-flight budget reservations isolated per test."""
    from haute._execution_admission import _clear_in_flight_reservations_for_tests

    _clear_in_flight_reservations_for_tests()
    yield
    _clear_in_flight_reservations_for_tests()


@pytest.fixture(scope="session")
def _default_bus_baseline() -> dict:
    """Capture default_bus' import-time subscribers once per session.

    The file-watcher's WS translators in :mod:`haute.server` register
    themselves at module import.  We must import ``haute.server`` here
    so those subscribers exist before the snapshot — otherwise the
    per-test restore in :func:`_default_bus_test_isolation` resets
    ``default_bus`` to an empty registry, breaking every subsequent
    test that expects a broadcast.
    """
    import haute.server  # noqa: F401 — side effect: register subscribers
    from haute._event_bus import default_bus

    return default_bus._snapshot_handlers_for_testing()


@pytest.fixture(autouse=True)
def _default_bus_test_isolation(_default_bus_baseline: dict):
    """Restore ``default_bus`` to its session baseline after every test.

    Without this, a test that calls ``default_bus.subscribe(...)`` and
    forgets to unsubscribe leaks its handler into every subsequent
    test in the session — the bus is a module-level singleton.  The
    baseline is the set of handlers registered at module-import time
    (server.py's WS subscribers), so production wiring persists while
    any test-added handlers are evicted.

    Prefer instantiating a fresh ``EventBus()`` inside a test for full
    isolation; this fixture is a safety net for tests that reach for
    ``default_bus`` by accident.
    """
    from haute._event_bus import default_bus

    yield
    default_bus._restore_handlers_for_testing(_default_bus_baseline)


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


@pytest.fixture(autouse=True)
def _clear_git_content_caches():
    """Reset the SHA-keyed git content caches between tests.

    The caches in ``haute._git`` (_is_ancestor/_merge_base/_commit_parents/
    _first_parent_spine/_graph_log) are keyed by (full SHA, str(cwd)) and are
    process-global. tmp_path directories recycle across tests, so without a
    clear a cached entry from one test's repo could be consulted by another
    test's repo at the same path. Content-addressing makes a wrong answer
    nearly impossible (same SHA ⇒ same history), but the isolation is
    belt-and-braces and keeps per-test subprocess-count assertions honest.
    """
    from haute._git import _clear_content_caches

    _clear_content_caches()
    yield
    _clear_content_caches()


@pytest.fixture(autouse=True)
def _clear_dual_cache_session():
    """Reset the dual-cache consulted-hashes set between tests.

    The set is module-level in ``haute._json_flatten`` — once a test calls
    ``build_json_cache`` or ``read_json_flat`` for a data file, the hash
    persists across subsequent tests in the same process. That would let
    one test's working-layer state spill into another's emitter precedence
    check, masking regressions or producing flaky failures. Clearing
    before AND after each test gives the same per-process isolation the
    other module-level singletons in this conftest get.
    """
    from haute._json_flatten import _clear_session

    _clear_session()
    yield
    _clear_session()


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


@pytest.fixture(autouse=True)
def _restore_project_root():
    """Restore the sandbox project root after every test.

    Several tests call ``set_project_root(tmp_path)`` directly (for
    quick path-validation overrides) without restoring the original
    value, leaking the temp directory into subsequent tests that use
    real fixtures under ``tests/fixtures/``.  Snapshotting and restoring
    the root here makes the global state behave per-test without
    requiring every call site to add its own try/finally.
    """
    original = _get_project_root()
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


def write_node_config(
    base_dir: Path,
    node_type: NodeType,
    func_name: str,
    config: dict,
) -> str:
    """Write a canonical node JSON sidecar and return its relative path."""
    rel_path = config_path_for_node(node_type, func_name)
    abs_path = base_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    abs_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return rel_path.as_posix()


def write_data_source_config(
    base_dir: Path,
    func_name: str,
    path: str,
    *,
    source_type: str = "flat_file",
) -> str:
    """Write the canonical sidecar for a ``dataSource`` node."""
    return write_node_config(
        base_dir,
        NodeType.DATA_SOURCE,
        func_name,
        {"path": path, "sourceType": source_type},
    )


def make_transform_node(nid: str, code: str = "") -> GraphNode:
    """Build a minimal transform node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="polars", config={"code": code}),
    )


def make_output_config(fields: list[str], *, source_port: str = "in") -> dict:
    """Build a v2 OUTPUT node config (``outputMapping``) from a flat field list.

    Each field maps to a top-level array-element path (``$[:].<field>``), so the
    assembled document is a flat array-of-rows — the behavioural equivalent of
    the retired v1 ``fields`` passthrough. ``source_port`` defaults to a
    placeholder that the single-parent fallback in ``_build_output`` resolves to
    the sole incoming frame; projection-only tests never read it.
    """
    return {
        "outputMapping": [
            {
                "source_port": source_port,
                "source_column": f,
                "output_path": f"$[:].{f}",
                "enabled": True,
            }
            for f in fields
        ],
        "outputFormat": "json",
    }


def make_output_node(nid: str, fields: list[str] | None = None) -> GraphNode:
    """Build a minimal output node (v2 ``outputMapping``)."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="output", config=make_output_config(fields or [])),
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


# ---------------------------------------------------------------------------
# Route job-store isolation — shared across route test files
# ---------------------------------------------------------------------------


def _clear_job_store_jobs(store) -> None:
    """Empty a route JobStore through its public cleanup path."""
    for job_id in list(store.jobs):
        store.delete_job(job_id)
    store._running_activity_at.clear()
    for timer in list(store._heavy_object_timers.values()):
        timer.cancel()
    store._heavy_object_timers.clear()


def _clear_loaded_route_job_store(module_name: str) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        return
    store = getattr(module, "_store", None)
    if store is None:
        return
    _clear_job_store_jobs(store)


def _clear_cached_route_job_store(prefix: str) -> None:
    module = sys.modules.get("haute.routes._job_store")
    if module is None:
        return
    get_job_store = getattr(module, "get_job_store", None)
    if get_job_store is None:
        return
    _clear_job_store_jobs(get_job_store(prefix))


def _clear_training_route_job_store_for_tests() -> None:
    _clear_loaded_route_job_store("haute.routes.modelling")
    _clear_cached_route_job_store("training")


@pytest.fixture(autouse=True)
def _clear_loaded_training_route_jobs():
    """Prevent training route jobs leaking between tests in the same worker."""
    _clear_training_route_job_store_for_tests()
    yield
    _clear_training_route_job_store_for_tests()


@pytest.fixture()
def clean_training_job_store():
    """Provide a fresh training route job store for tests that mutate it."""
    from haute.routes.modelling import _store

    _clear_job_store_jobs(_store)
    yield _store
    _clear_job_store_jobs(_store)


# ---------------------------------------------------------------------------
# Optimiser job-store isolation — shared across all optimiser test files
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_job_store():
    """Snapshot and restore the optimiser job store around each test.

    Tests that inject fake jobs into ``_store.jobs`` no longer need a
    manual try/finally; the snapshot/restore here keeps test isolation
    on the module-level job store without requiring per-file fixture
    duplication.

    Single source of truth for the optimiser job-store fixture; previously
    each optimiser test file (``test_optimiser_routes.py``,
    ``test_optimiser_routes_critical_edges.py``,
    ``test_optimiser_frontier_materialisation.py``) defined its own copy.
    """
    from haute.routes.optimiser import _store

    snapshot = dict(_store.jobs)
    yield _store
    _store.jobs.clear()
    _store.jobs.update(snapshot)
