"""Shared test fixtures and helpers for the haute test suite."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from click.testing import CliRunner
from hypothesis import settings as hypothesis_settings

from haute._config_io import config_path_for_node
from haute._execution_context import ExecutionProfile
from haute._sandbox import _get_project_root, set_project_root
from haute.executor import _preview_cache
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.trace import _cache as _trace_cache
from tests import _write_sandbox as _ws

_TEST_LOCAL_SESSION_TOKEN = "pytest-haute-local-session-token"


# Hypothesis' 200ms per-example deadline measures wall clock, so a property
# that touches the filesystem can miss it on a loaded machine and pass on the
# retry, which Hypothesis reports as FlakyFailure ("Unreliable test timings!").
# Every deliberate budget in this suite already sets deadline=None (see
# tests/_property_budget.pr_budget); making that the default means a property
# need not restate it. A test that wants a deadline still gets one by passing
# it to its own @settings.
hypothesis_settings.register_profile("haute", deadline=None)
hypothesis_settings.load_profile("haute")


@pytest.fixture(autouse=True, scope="session")
def _isolate_repository_source_cache(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Keep the suite's source snapshots out of the working tree.

    A test that never sets its own project root inherits the repository root,
    so ``SourceCacheStore`` publishes its snapshots into
    ``<repo>/.haute_cache/inputs``. That store is shared by every xdist
    worker and is never cleaned between runs. Isolating it prevents tests from
    accumulating cached data in the working tree or reading an earlier run's
    snapshots.

    Stores opened against the repository root are redirected to a per-session
    directory — the same redirect ``_widen_sandbox_root`` applies to the
    widened root. A store opened against a test's own tmp_path is untouched.
    The session also owns its coordination table so process-owner files close
    before an embedded mutation runner removes the temporary directory.
    """
    from haute._source_cache import SourceCacheStore

    repository_root = Path(__file__).resolve().parents[1]
    session_cache_root = tmp_path_factory.mktemp("source-cache")
    original_init = SourceCacheStore.__init__

    def init_off_the_working_tree(
        self: SourceCacheStore,
        root: str | Path,
        **kwargs: Any,
    ) -> None:
        resolved = Path(root).resolve()
        original_init(
            self,
            session_cache_root if resolved == repository_root else resolved,
            **kwargs,
        )

    patch = pytest.MonkeyPatch()
    patch.setattr(SourceCacheStore, "__init__", init_off_the_working_tree)
    coordination_by_root: dict[Any, Any] = {}
    patch.setattr(SourceCacheStore, "_coordination_by_root", coordination_by_root)
    try:
        yield
    finally:
        try:
            for coordination in coordination_by_root.values():
                handle = coordination.token_handle
                if handle is not None:
                    handle.close()
                coordination.token = None
                coordination.token_handle = None
            coordination_by_root.clear()
        finally:
            patch.undo()


@pytest.fixture(autouse=True)
def _restore_mlflow_databricks_binding() -> Iterator[None]:
    """Undo the process-global MLflow Databricks credential binding after each test.

    Any Databricks destination resolution binds MLflow's credential provider and
    artifact repository globals for the rest of the process; restoring them keeps
    every test independent of execution order.
    """
    yield
    from haute._mlflow_utils import _restore_mlflow_databricks_credentials

    _restore_mlflow_databricks_credentials()


@pytest.fixture(autouse=True)
def _restore_streaming_chunk_size() -> Iterator[None]:
    """Put back the process-wide Polars streaming chunk size after each test.

    ``set_streaming_chunk_size`` (directly or through ``PUT
    /api/execution-settings``) sets it for the whole process, so a test's tiny
    chunk size would otherwise reach whichever test runs next on the same
    worker. Polars caches the value and rereads ``POLARS_STREAMING_CHUNK_SIZE``
    only through its ``Config`` API, so editing the environment (as
    ``monkeypatch.delenv`` does) cannot restore it.
    """
    before = os.environ.get("POLARS_STREAMING_CHUNK_SIZE")
    yield
    pl.Config.set_streaming_chunk_size(None if before is None else int(before))


@pytest.fixture(autouse=True)
def _patient_preparation_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wait longer for a preparation thread than a production shutdown does.

    ``TrainService._join_preparation``'s 10s default bounds a graceful
    shutdown. Under ``-n auto`` the suite runs one worker per core against a
    single CPU pool, and a preparation thread that finishes well inside a
    second can still miss that bound, failing the test with "Training
    preparation for job ... is still running" for a reason the code under test
    is not answerable for. Only the default moves: a test that pins the
    timeout itself (the expiry case passes 0.01s) is handed through unchanged.
    """
    from haute.routes._training_lifecycle import TrainService

    original = TrainService._join_preparation

    def join_patiently(self: TrainService, job_id: str, *, timeout: float = 120.0) -> None:
        original(self, job_id, timeout=timeout)

    monkeypatch.setattr(TrainService, "_join_preparation", join_patiently)


@pytest.fixture(autouse=True)
def _isolate_mlflow_fluent_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test fresh MLflow fluent globals.

    ``set_tracking_uri``, ``set_registry_uri`` and ``set_experiment`` write
    process-global state, so a test that resolves a destination or starts a run
    would otherwise hand its URI or experiment ID to whichever test runs next on
    the worker (seen as "Could not find experiment with ID ..." against a fresh
    local store). monkeypatch restores the originals after the test.

    ``set_tracking_uri`` also exports ``MLFLOW_TRACKING_URI``, so a test that
    restores MLflow's default URI in a ``finally`` leaves that default in the
    environment, where a fresh checkout's ``sqlite:///mlflow.db`` fails the next
    destination resolution on the worker ("Unsupported MLflow tracking URI
    scheme 'sqlite'"). The URI variables are restored exactly after the test.
    """
    monkeypatch.setattr("mlflow.tracking._tracking_service.utils._tracking_uri", None)
    monkeypatch.setattr("mlflow.tracking._model_registry.utils._registry_uri", None)
    monkeypatch.setattr("mlflow.tracking.fluent._active_experiment_id", None)
    # set_experiment also exports the ID; monkeypatch unsets it again afterwards.
    monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
    exported = {
        name: os.environ.get(name) for name in ("MLFLOW_TRACKING_URI", "MLFLOW_REGISTRY_URI")
    }
    yield
    for name, value in exported.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _no_mlflow_telemetry(monkeypatch: pytest.MonkeyPatch):
    """Keep the suite hermetic: MLflow must not phone home from a test.

    MLflow 3 posts usage telemetry to its own endpoint the first time a client
    is created in a process. That is an outbound request the suite never asked
    for, and it lands in whichever test happens to be recording requests at the
    time — which is how `test_tracking_requests_reach_only_the_mlflow_host_with
    _the_mlflow_token` saw a telemetry POST ahead of its own.
    """
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")


@pytest.fixture(autouse=True)
def _interactive_execution_test_mode(monkeypatch: pytest.MonkeyPatch):
    """Keep legacy in-process seams explicit; isolation tests opt into processes."""
    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "thread")


@pytest.fixture(autouse=True)
def _one_training_thread_per_test(monkeypatch: pytest.MonkeyPatch):
    """Give each training job one engine thread, so parallel test workers don't oversubscribe.

    A training job's allotment defaults to every logical CPU. CI runs four xdist
    workers on a four-vCPU runner, so concurrent training tests each started
    XGBoost/CatBoost/LightGBM with all cores, and OpenMP's spinning threads
    slowed a five-trial XGBoost study past the 60-second timeout. Tests that
    exercise the allotment set ``HAUTE_TRAINING_THREADS`` themselves.
    """
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "1")


@pytest.fixture(autouse=True)
def _clear_trace_caches():
    """Invalidate global trace, preview and inference caches between tests.

    The preview and trace caches are module-level singletons. Without clearing them,
    a prior test's cached DataFrames can bleed into the next test if they
    happen to share the same fingerprint (e.g., same node ids, same code).
    """
    from haute._json_shred._inference_cache import _INFERENCE_CACHE

    _INFERENCE_CACHE.clear()
    _trace_cache.clear()
    _preview_cache.clear()
    yield
    _INFERENCE_CACHE.clear()
    _trace_cache.clear()
    _preview_cache.clear()


@pytest.fixture(autouse=True)
def _local_session_auth_for_route_clients(monkeypatch: pytest.MonkeyPatch):
    """Make test HTTP clients use Haute's real local-session token path."""
    import httpx
    from starlette.testclient import TestClient as StarletteTestClient

    from haute._local_security import (
        SESSION_TOKEN_COOKIE,
        SESSION_TOKEN_ENV,
        local_session_token,
    )

    monkeypatch.setenv(SESSION_TOKEN_ENV, _TEST_LOCAL_SESSION_TOKEN)

    def headers_with_session_cookie(headers) -> httpx.Headers:
        merged = httpx.Headers(headers or {})
        if "host" not in merged:
            merged["host"] = "localhost"
        cookie = merged.get("cookie", "")
        if f"{SESSION_TOKEN_COOKIE}=" not in cookie:
            session_cookie = f"{SESSION_TOKEN_COOKIE}={local_session_token()}"
            merged["cookie"] = f"{cookie}; {session_cookie}" if cookie else session_cookie
        return merged

    original_test_client_init = StarletteTestClient.__init__

    def test_client_init_with_session_token(self, *args, **kwargs):
        kwargs.setdefault("base_url", "http://localhost")
        kwargs["headers"] = headers_with_session_cookie(kwargs.get("headers"))
        return original_test_client_init(self, *args, **kwargs)

    monkeypatch.setattr(StarletteTestClient, "__init__", test_client_init_with_session_token)

    original_async_client_init = httpx.AsyncClient.__init__

    def async_client_init_with_session_token(self, *args, **kwargs):
        if isinstance(kwargs.get("transport"), httpx.ASGITransport):
            if str(kwargs.get("base_url", "")).rstrip("/") == "http://testserver":
                kwargs["base_url"] = "http://localhost"
            kwargs["headers"] = headers_with_session_cookie(kwargs.get("headers"))
        return original_async_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", async_client_init_with_session_token)


@pytest.fixture(autouse=True)
def _clear_execution_admission_reservations():
    """Keep process-wide in-flight budget reservations isolated per test."""
    from haute._execution_admission import _clear_in_flight_reservations_for_tests

    _clear_in_flight_reservations_for_tests()
    yield
    _clear_in_flight_reservations_for_tests()


@pytest.fixture(autouse=True)
def _clear_materialisation_calibration():
    """Keep process-local estimate learning deterministic between tests."""
    from haute._estimate_calibration import _reset_materialisation_calibration_for_tests

    _reset_materialisation_calibration_for_tests()
    yield
    _reset_materialisation_calibration_for_tests()


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
def _clear_source_signatures():
    """Forget memoised source content proofs between tests.

    The memo is process-wide and keyed by a file's freshness token, which a
    reused temporary path can repeat across tests.
    """
    from haute._json_shred._source_proof import clear_file_signatures

    clear_file_signatures()
    yield
    clear_file_signatures()


@pytest.fixture()
def _widen_sandbox_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Allow tests to load files from temp directories.

    Sets the sandbox project root to ``/`` for the duration of each test
    so that ``validate_project_path`` accepts paths in ``/tmp``.
    Source snapshots still need writable project-local storage, so stores
    opened against that synthetic filesystem root are redirected to this
    test's temp directory.
    Restores the original root afterwards.
    """
    from haute._source_cache import SourceCacheStore

    original = _get_project_root()
    widened_root = Path("/").resolve()
    # Keep this root short enough for the generation + atomic-write suffixes
    # to remain below Windows' traditional path limit.
    cache_root = Path(tempfile.mkdtemp(prefix="sc-", dir=tmp_path.parent))
    original_store_init = SourceCacheStore.__init__

    def init_with_writable_cache(
        self: SourceCacheStore,
        root: str | Path,
        **kwargs: object,
    ) -> None:
        # Every keyword the store takes is forwarded, so a subclass that passes
        # its own options (a node-output store's retirement grace, for example)
        # still constructs under the widened root.
        resolved_root = Path(root).resolve()
        original_store_init(
            self,
            cache_root if resolved_root == widened_root else resolved_root,
            **kwargs,
        )

    monkeypatch.setattr(SourceCacheStore, "__init__", init_with_writable_cache)
    set_project_root(widened_root)
    yield
    set_project_root(original)


@pytest.fixture()
def training_artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep job-owned training artifact directories in this test's scratch space."""
    from haute.routes import _training_artifacts

    root = (tmp_path / "training-artifacts").resolve()
    monkeypatch.setattr(_training_artifacts, "training_artifact_root", lambda: root)
    return root


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


def make_file_input_config(path: object, **extra: object) -> dict[str, object]:
    """Build a persisted canonical file ``dataInput`` config for tests."""
    path_text = str(path)
    format_name, mode = {
        ".csv": ("csv", "scan"),
        ".json": ("json", "read"),
        ".jsonl": ("ndjson", "scan"),
        ".ndjson": ("ndjson", "scan"),
        ".parquet": ("parquet", "scan"),
        ".arrow": ("ipc", "scan"),
        ".feather": ("ipc", "scan"),
        ".ipc": ("ipc", "scan"),
        ".avro": ("avro", "read"),
        ".xlsx": ("excel", "read"),
        ".ods": ("ods", "read"),
        ".txt": ("lines", "scan"),
        ".log": ("lines", "scan"),
    }.get(Path(path_text).suffix.lower(), ("parquet", "scan"))
    return {
        "inputType": "file",
        "format": format_name,
        "mode": mode,
        "path": path_text,
        "arguments": {},
        **extra,
    }


def build_test_input_snapshot(
    config: dict[str, object],
    *,
    base_dir: str | Path | None = None,
    profile: ExecutionProfile = ExecutionProfile.PREVIEW_EAGER,
) -> None:
    """Prepare a runtime test input, building only snapshot-backed sources."""
    from haute._builders import _configured_pipeline_dir
    from haute._input_providers import build_input_snapshot
    from haute._polars_io_registry import data_input_is_direct
    from haute._source_cache import SourceCacheStore

    if data_input_is_direct(config):
        return
    build_input_snapshot(
        config,
        store=SourceCacheStore(_get_project_root()),
        base_dir=base_dir if base_dir is not None else _configured_pipeline_dir(),
        profile=profile,
    )


def build_test_api_input_snapshots(
    data_path: str | Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Build every emitting table of a structured API Input into the project store.

    *data_path* is the source file exactly as execution anchors it. Returns
    the published generations keyed by table identity digest.
    """
    from haute._json_shred._snapshots import api_input_snapshot_source, build_api_input_tables
    from haute._source_cache import SourceCacheStore

    source = api_input_snapshot_source(config, data_path)
    return build_api_input_tables(
        source,
        [table.label for table in source.tables],
        store=SourceCacheStore(_get_project_root()),
        profile=ExecutionProfile.LAZY_SINK,
    )


def make_ready_file_input_config(path: object, **extra: object) -> dict[str, object]:
    """Build and return a canonical file input config ready for runtime use."""
    from haute._polars_io_registry import data_input_is_direct

    config = make_file_input_config(path, **extra)
    if not data_input_is_direct(config):
        build_test_input_snapshot(config)
    return config


def make_file_output_config(
    path: object,
    *,
    format_name: str | None = None,
    mode: str | None = None,
    **extra: object,
) -> dict[str, object]:
    """Build a persisted canonical file ``dataOutput`` config for tests."""
    path_text = str(path)
    resolved_format = format_name or {
        ".csv": "csv",
        ".json": "json",
        ".jsonl": "ndjson",
        ".ndjson": "ndjson",
        ".parquet": "parquet",
        ".arrow": "ipc",
        ".feather": "ipc",
        ".ipc": "ipc",
        ".avro": "avro",
        ".xlsx": "excel",
        ".ods": "ods",
    }.get(Path(path_text).suffix.lower(), "parquet")
    resolved_mode = mode or ("sink" if resolved_format in {"csv", "ndjson", "parquet"} else "write")
    return {
        "outputType": "file",
        "format": resolved_format,
        "mode": resolved_mode,
        "path": path_text,
        "arguments": {},
        **extra,
    }


def make_source_node(nid: str, path: str = "data.parquet") -> GraphNode:
    """Build a minimal canonical file ``dataInput`` node."""
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType="dataInput",
            config=make_file_input_config(path),
        ),
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


def write_data_input_config(
    base_dir: Path,
    func_name: str,
    path: str,
) -> str:
    """Write the canonical sidecar for a ``dataInput`` node."""
    suffix = Path(path).suffix.lower()
    format_name, mode = {
        ".csv": ("csv", "scan"),
        ".json": ("json", "read"),
        ".jsonl": ("ndjson", "scan"),
        ".ndjson": ("ndjson", "scan"),
        ".parquet": ("parquet", "scan"),
        ".arrow": ("ipc", "scan"),
        ".feather": ("ipc", "scan"),
        ".ipc": ("ipc", "scan"),
        ".avro": ("avro", "read"),
        ".xlsx": ("excel", "read"),
        ".ods": ("ods", "read"),
        ".txt": ("lines", "scan"),
        ".log": ("lines", "scan"),
    }.get(suffix, ("parquet", "scan"))
    return write_node_config(
        base_dir,
        NodeType.DATA_INPUT,
        func_name,
        {
            "inputType": "file",
            "format": format_name,
            "mode": mode,
            "path": path,
            "arguments": {},
        },
    )


def make_transform_node(nid: str, code: str = "") -> GraphNode:
    """Build a minimal transform node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="polars", config={"code": code}),
    )


def make_output_config(fields: list[str], *, source_port: str = "in") -> dict:
    """Build an OUTPUT node config from a flat field list.

    Each field maps to a top-level array-element path (``$[:].<field>``), so the
    assembled document is a flat array of rows. ``source_port`` defaults to the
    placeholder used by single-parent test graphs.
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
    """Build a minimal output node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="output", config=make_output_config(fields or [])),
    )


def current_source_revision(path: str | Path, project_root: str | Path) -> str | None:
    """Return the on-disk document revision a save must name, or None when the file is absent."""
    from haute._pipeline_recovery import load_pipeline_editor_document

    target = Path(path)
    if not target.is_file():
        return None
    return load_pipeline_editor_document(target, project_root=Path(project_root)).source_revision


def make_edge(
    src: str,
    tgt: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> GraphEdge:
    """Build an edge with optional explicit port identity."""
    return GraphEdge(
        id=f"e_{src}_{tgt}",
        source=src,
        target=tgt,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


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
    store.clear_all()


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
    """Provide an empty optimiser job store for one test.

    The store is cleared through its public cleanup operation on both sides
    of the test, so timers and owned artifacts cannot leak between cases.

    Single source of truth for the optimiser job-store fixture; previously
    each optimiser test file (``test_optimiser_routes.py``,
    ``test_optimiser_routes_critical_edges.py``,
    ``test_optimiser_frontier_materialisation.py``) defined its own copy.
    """
    from haute.routes.optimiser import _store

    _store.clear_all()
    yield _store
    _store.clear_all()


# ---------------------------------------------------------------------------
# Test write-sandbox (layers 1 + 3) — logic in tests/_write_sandbox.py
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent
_WS_REPO_ROOT = _TESTS_DIR.parent


@pytest.fixture(autouse=True, scope="session")
def _project_root_baseline():
    """Pin the sandbox project root before any test moves the working directory.

    ``_get_project_root()`` lazily captures ``Path.cwd()`` on its first call.
    Without an eager baseline, that first call happens inside
    ``_restore_project_root`` — which sets up *after* ``_haute_write_sandbox``
    has already chdir'd into the first strict test's tmp dir — so every later
    test in the process inherits that tmp dir as the project root. That stays
    invisible until the first test on a worker also writes a ``haute.toml``
    into its tmp dir (e.g. the malformed-toml regression), which then poisons
    unrelated executor and route tests.
    """
    set_project_root(_WS_REPO_ROOT)
    yield


@pytest.fixture(autouse=True)
def _haute_write_sandbox(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Layer-3 runtime guard: containment env + open() interception.

    Strict for the converted pilot slice (``_write_sandbox.STRICT_FILES`` or
    the ``sandbox_strict`` marker), observe-and-record for everything else,
    off for perf-marked tests and under ``HAUTE_TEST_WRITE_SANDBOX=off``.
    """
    mode = _ws.resolve_mode(
        os.environ.get(_ws.ENV_MODE),
        is_perf=request.node.get_closest_marker("perf") is not None,
        filename=request.path.name,
        marked_strict=request.node.get_closest_marker("sandbox_strict") is not None,
    )
    if mode == "off":
        yield
        return
    monkeypatch.setenv(_ws.ENV_ROOT, str(tmp_path))
    if mode == "strict":
        sandbox_tmp = tmp_path / "_tmp"
        sandbox_home = tmp_path / "_home"
        sandbox_tmp.mkdir(exist_ok=True)
        sandbox_home.mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_path)
        for var in ("TMPDIR", "TEMP", "TMP"):
            monkeypatch.setenv(var, str(sandbox_tmp))
        monkeypatch.setattr(tempfile, "tempdir", str(sandbox_tmp))
        monkeypatch.setenv("HOME", str(sandbox_home))
        monkeypatch.setenv("USERPROFILE", str(sandbox_home))
        allowed = (os.path.realpath(str(tmp_path)),)
    else:
        allowed = (
            os.path.realpath(str(tmp_path.parent)),  # this run's basetemp
            os.path.realpath(tempfile.gettempdir()),
            os.path.realpath(str(_WS_REPO_ROOT / ".hypothesis")),  # example DB, harness infra
        )
    guard = _ws.Guard(mode=mode, nodeid=request.node.nodeid, allowed_roots=allowed)
    guard.install()
    try:
        yield
    finally:
        guard.uninstall()


@pytest.fixture()
def haute_scratch(tmp_path: Path) -> Path:
    """Layer-1 convention: the per-test scratch directory all writes derive from.

    Same substrate as ``tmp_path`` (unique per test, platform-appropriate,
    auto-pruned) plus the sandbox semantics: it is exactly the root the
    layer-3 guard confines strict tests to and exports as
    ``HAUTE_TEST_SANDBOX_ROOT``. It is also the declared Haute project root
    for execution tests that build graphs against files in this directory.
    """
    set_project_root(tmp_path)
    return tmp_path


def pytest_configure(config: pytest.Config) -> None:
    # Aggregate the write-sandbox census across xdist workers: the controller
    # allocates a shared spool dir before workers spawn; workers inherit it via
    # the environment and dump their in-process records at session finish. The
    # controller merges, reports, and removes the spool in the summary hook.
    if os.environ.get(_ws.ENV_CENSUS_DIR) or hasattr(config, "workerinput"):
        return
    spool = tempfile.mkdtemp(prefix="haute-write-sandbox-census-")  # write-sandbox: deliberate
    os.environ[_ws.ENV_CENSUS_DIR] = spool
    config._ws_census_spool = spool


def pytest_sessionfinish(session: pytest.Session) -> None:
    census_dir = os.environ.get(_ws.ENV_CENSUS_DIR)
    if census_dir and _ws.VIOLATIONS:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        _ws.dump_census(census_dir, worker)


def pytest_terminal_summary(terminalreporter) -> None:
    violations = list(_ws.VIOLATIONS)
    census_dir = os.environ.get(_ws.ENV_CENSUS_DIR)
    if census_dir:
        merged = _ws.load_census(census_dir)
        if merged:
            violations = merged
    spool = getattr(terminalreporter.config, "_ws_census_spool", None)
    if spool:
        shutil.rmtree(spool, ignore_errors=True)  # write-sandbox: deliberate
        os.environ.pop(_ws.ENV_CENSUS_DIR, None)
    if violations:
        terminalreporter.section("write-sandbox census (observe mode)")
        for line in _ws.summarize(violations):
            terminalreporter.line(line)
        terminalreporter.line(
            f"{len(violations)} out-of-sandbox write(s) recorded; "
            "see tests/_write_sandbox.py (conversion ratchet)."
        )
