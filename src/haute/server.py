"""FastAPI backend for haute.

App factory, middleware, WebSocket sync, file watcher, and static serving.
Route handlers live in ``haute.routes.*`` — see:
  - ``routes/pipeline.py``  — pipeline CRUD, run, preview, trace, sink
  - ``routes/databricks.py``— Unity Catalog browsing, data fetching
  - ``routes/files.py``     — file browsing, schema inspection
  - ``routes/submodel.py``  — submodel create, get, dissolve
"""

import asyncio
import hashlib
import json
import mimetypes
import time
import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from haute._event_bus import default_bus
from haute._local_security import (
    LocalSessionMiddleware,
    local_session_auth_disabled,
    local_session_token,
    websocket_rejection_reason,
)
from haute._logging import configure_logging, get_logger
from haute.routes._helpers import (
    _ensure_pipeline_index,
    broadcast,
    discover_pipelines,
    invalidate_pipeline_index,
    is_self_write,
    parse_pipeline_to_graph,
    pipeline_dir,
    pipelines_importing_module,
    ws_clients,
    ws_clients_add,
    ws_clients_discard,
    ws_clients_lock,
)
from haute.routes.databricks import router as databricks_router
from haute.routes.explore import router as explore_router
from haute.routes.files import router as files_router
from haute.routes.git import router as git_router
from haute.routes.json_cache import router as json_cache_router
from haute.routes.mlflow import router as mlflow_router
from haute.routes.modelling import router as modelling_router
from haute.routes.optimiser import router as optimiser_router
from haute.routes.pipeline import router as pipeline_router
from haute.routes.submodel import router as submodel_router
from haute.routes.utility import router as utility_router

# Windows registry often maps .js to text/plain, causing browsers to reject
# the JS bundle.  Patch before StaticFiles or FileResponse uses mimetypes.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

STATIC_DIR = Path(__file__).parent / "static"
logger = get_logger(component="server")

_watcher_task: asyncio.Task | None = None
_WATCHER_RESTART_DELAY_SECONDS = 0.1
WS_FRAME_GRAPH_UPDATE = "graph_update"
WS_FRAME_PARSE_ERROR = "parse_error"


# ---------------------------------------------------------------------------
# Event-bus subscribers — translate domain events into WebSocket frames.
# Wired at import time so bare ``default_bus.publish(...)`` calls in the
# file-watcher reach every current WebSocket client without the watcher
# needing a reference to the broadcaster.  Unsubscribe handles are kept
# on the module so tests can tear them down if needed.
# ---------------------------------------------------------------------------


def _ws_message_frame(wire_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the WebSocket envelope for an event-bus payload."""
    if "type" in payload:
        raise ValueError("payload uses reserved WebSocket frame key 'type'")
    return {"type": wire_type, **payload}


def _broadcast_event_as_ws_message(wire_type: str, payload: dict[str, Any]) -> None:
    """Schedule ``broadcast`` of a WebSocket frame derived from *payload*.

    ``broadcast`` is async because ``websocket.send_text`` is; but the
    event bus is synchronous so a subscriber cannot ``await`` directly.
    We schedule the coroutine on the running event loop instead — the
    file-watcher that publishes these events runs on the server's
    loop, so :func:`asyncio.get_running_loop` is always available in
    the production path.  A plain-sync call (e.g. from a unit test
    with no loop) logs the missed broadcast and returns; the test can
    assert on the event-bus side directly in that case.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "event_bus_broadcast_skipped",
            wire_type=wire_type,
            reason="no_running_loop",
        )
        return
    task = loop.create_task(broadcast(_ws_message_frame(wire_type, payload)))
    task.add_done_callback(_log_broadcast_task_result)


def _log_broadcast_task_result(task: asyncio.Task[None]) -> None:
    """Log unexpected failures from fire-and-forget broadcast tasks."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is None:
        return
    logger.error(
        "event_bus_broadcast_failed",
        error=str(exc),
        traceback="".join(traceback.format_exception(exc)),
    )


def _discovered_pipeline_paths() -> dict[str, Path]:
    """Return discovered Python pipelines keyed by resolved absolute path."""
    paths: dict[str, Path] = {}
    for path in discover_pipelines():
        if path.suffix != ".py" or path.name.startswith("__"):
            continue
        paths[str(path.resolve())] = path
    return paths


def _known_pipeline_paths() -> dict[str, Path]:
    """Return pipelines already known to the server before the latest edit."""
    paths = {str(path.resolve()): path for path in _ensure_pipeline_index().values()}
    paths.update({key: Path(key) for key in _last_broadcast_fp})
    return paths


def _resolve_client_source_file(source_file: Any) -> Path | None:
    """Resolve a client-provided source_file relative to the current project."""
    if not isinstance(source_file, str):
        return None
    raw = source_file.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


async def _send_ws_json(websocket: WebSocket, frame: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(frame))


async def _send_ws_parse_error(
    websocket: WebSocket,
    *,
    error: str,
    source_file: str,
) -> None:
    await _send_ws_json(
        websocket,
        _ws_message_frame(
            WS_FRAME_PARSE_ERROR,
            {
                "error": error,
                "source_file": source_file,
            },
        ),
    )


async def _handle_ws_sync_message(websocket: WebSocket, message_text: str) -> None:
    """Handle optional client commands sent over ``/ws/sync``.

    Plain keep-alive strings remain valid and are ignored.  A JSON resync
    request reparses exactly one discovered pipeline and sends the graph only
    to the requesting websocket.
    """
    try:
        message = json.loads(message_text)
    except json.JSONDecodeError:
        return
    if not isinstance(message, dict) or message.get("type") != "resync":
        return

    source_path = _resolve_client_source_file(message.get("source_file"))
    if source_path is None:
        await _send_ws_parse_error(
            websocket,
            error="Resync request requires a source_file",
            source_file="",
        )
        return

    discovered = _discovered_pipeline_paths()
    pipeline_path = discovered.get(str(source_path))
    if pipeline_path is None:
        await _send_ws_parse_error(
            websocket,
            error="Resync source is not a discovered pipeline",
            source_file=str(source_path),
        )
        return

    try:
        graph = parse_pipeline_to_graph(pipeline_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("parse_error", file=pipeline_path.name, error=str(exc))
        await _send_ws_parse_error(
            websocket,
            error=str(exc),
            source_file=str(pipeline_path),
        )
        return

    await _send_ws_json(
        websocket,
        _ws_message_frame(
            WS_FRAME_GRAPH_UPDATE,
            {
                "graph": graph.model_dump(),
                "source_file": str(pipeline_path),
            },
        ),
    )


# Keep strong refs to the handlers so the bus cannot collect them, and
# retain the unsubscribe callables on the module for lifespan teardown
# / test isolation.
def _ws_graph_update_subscriber(payload: dict[str, Any]) -> None:
    """Forward ``graph.update`` bus events to every connected WebSocket."""
    _broadcast_event_as_ws_message(WS_FRAME_GRAPH_UPDATE, payload)


def _ws_parse_error_subscriber(payload: dict[str, Any]) -> None:
    """Forward ``parse.error`` bus events to every connected WebSocket."""
    _broadcast_event_as_ws_message(WS_FRAME_PARSE_ERROR, payload)


_unsubscribe_graph_update = default_bus.subscribe("graph.update", _ws_graph_update_subscriber)
_unsubscribe_parse_error = default_bus.subscribe("parse.error", _ws_parse_error_subscriber)


def _clear_bytecache() -> None:
    """Remove all .pyc files so stale bytecode never masks code changes."""
    import shutil

    src_dir = Path(__file__).resolve().parent
    for pycache in src_dir.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    from haute.deploy._config import _load_env

    _clear_bytecache()
    configure_logging()
    _load_env(Path.cwd())

    # Prime the pipeline-name → path index so the first HTTP request doesn't
    # synchronously pay for discovery + parse of every pipeline in the
    # project.  Startup is the *only* place besides the file-watcher
    # callback that is allowed to (re)build this index — see
    # ``haute.routes._helpers`` for the full contract.
    _ensure_pipeline_index()

    global _watcher_task
    _watcher_task = asyncio.create_task(_watcher_forever())
    yield
    if _watcher_task:
        _watcher_task.cancel()
        with suppress(asyncio.CancelledError):
            await _watcher_task
        _watcher_task = None


app = FastAPI(title="Haute", version="0.1.0", lifespan=_lifespan)
_TRUSTED_LOCAL_HOSTS = ["localhost", "127.0.0.1", "testserver"]


class _RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind request_id, log every request with timing, capture 500 tracebacks."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)

        method = request.method
        path = request.url.path
        t0 = time.monotonic()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.error(
                "unhandled_exception",
                method=method,
                path=path,
                duration_ms=duration_ms,
                traceback=traceback.format_exc(),
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )

        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        status = response.status_code
        response.headers["x-request-id"] = rid

        kw = dict(method=method, path=path, status=status, duration_ms=duration_ms)
        if status >= 500:
            logger.error("request_error", **kw)
        elif status >= 400:
            logger.warning("request_client_error", **kw)
        elif path.startswith("/api/"):
            logger.info("request_ok", **kw)

        return response


app.add_middleware(_RequestIdMiddleware)
app.add_middleware(LocalSessionMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_TRUSTED_LOCAL_HOSTS,
    www_redirect=False,
)

# CORS for dev mode — Vite dev server (port 5173) talks to FastAPI (port 8000)
if not STATIC_DIR.exists():
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---------------------------------------------------------------------------
# Include route modules
# ---------------------------------------------------------------------------
app.include_router(pipeline_router)
app.include_router(databricks_router)
app.include_router(files_router)
app.include_router(json_cache_router)
app.include_router(submodel_router)
app.include_router(explore_router)
app.include_router(modelling_router)
app.include_router(optimiser_router)
app.include_router(mlflow_router)
app.include_router(utility_router)
app.include_router(git_router)


# ---------------------------------------------------------------------------
# WebSocket endpoint for live code ↔ GUI sync
# ---------------------------------------------------------------------------


@app.websocket("/ws/sync")
async def ws_sync(websocket: WebSocket) -> None:
    """WebSocket endpoint for live code ↔ GUI sync."""
    headers = getattr(websocket, "headers", None)
    query_params = getattr(websocket, "query_params", None)
    if headers is not None and query_params is not None:
        rejection_reason = websocket_rejection_reason(headers, query_params)
        if rejection_reason is not None:
            await websocket.close(code=1008, reason=rejection_reason)
            return

    await websocket.accept()
    ws_clients_add(websocket)
    with ws_clients_lock:
        total = len(ws_clients)
    logger.info("ws_connected", total_clients=total)
    try:
        while True:
            message_text = await websocket.receive_text()
            await _handle_ws_sync_message(websocket, message_text)
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients_discard(websocket)
        with ws_clients_lock:
            remaining = len(ws_clients)
        logger.info("ws_disconnected", remaining_clients=remaining)


# ---------------------------------------------------------------------------
# File watcher - live sync from .py edits to GUI
# ---------------------------------------------------------------------------


_DEBOUNCE_SECONDS = 0.3
# Track last-broadcast graph fingerprint per pipeline file to skip redundant broadcasts
_last_broadcast_fp: dict[str, str] = {}


async def _watcher_forever() -> None:
    """Keep the live-sync watcher alive across unexpected watcher crashes."""
    while True:
        try:
            await _file_watcher()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "file_watcher_crashed",
                error=str(exc),
                traceback=traceback.format_exc(),
                restart_delay_s=_WATCHER_RESTART_DELAY_SECONDS,
            )
            await asyncio.sleep(_WATCHER_RESTART_DELAY_SECONDS)


async def _file_watcher() -> None:
    """Watch pipeline directories for .py changes and broadcast to GUI.

    Uses a 300ms debounce window to batch rapid edits (e.g. IDE auto-save)
    into a single parse + broadcast cycle.
    """
    try:
        from watchfiles import Change, awatch
    except ImportError:
        logger.warning("watchfiles_missing", msg="live sync disabled")
        return

    cwd = Path.cwd()
    pipe_dir = pipeline_dir()
    watch_dirs = [cwd]
    if pipe_dir != cwd:
        watch_dirs.append(pipe_dir)
    modules_dir = cwd / "modules"
    if modules_dir.is_dir():
        watch_dirs.append(modules_dir)
    config_dir = pipe_dir / "config"
    if config_dir.is_dir():
        watch_dirs.append(config_dir)

    logger.info("file_watcher_started", watch_dirs=[str(d) for d in watch_dirs])

    pending_changes: set[tuple[Change, str]] = set()
    debounce_task: asyncio.Task[None] | None = None
    completed_normally = False

    async def _flush() -> None:
        """Parse and broadcast after debounce window expires."""
        nonlocal debounce_task
        await asyncio.sleep(_DEBOUNCE_SECONDS)

        to_process: set[tuple[Change, str]] = set()
        try:
            # Snapshot and clear before processing so new events can queue
            # independently. If a higher-level flush failure happens below,
            # we requeue this batch and schedule a retry.
            to_process = set(pending_changes)
            pending_changes.clear()

            # Collect changed files from pending set
            changed_files: list[Path] = []
            direct_py_changes: list[Path] = []
            module_stems: list[str] = []
            config_changed = False
            self_write_keys: set[str] = set()
            for change_type, changed_path in to_process:
                p = Path(changed_path)
                key = str(p.resolve())
                if key in self_write_keys or is_self_write(p, consume=True):
                    self_write_keys.add(key)
                    logger.debug("file_watcher_skipped_self_write", file=str(p))
                    continue
                if change_type not in (Change.modified, Change.added):
                    continue
                # JSON config files in config/ directory
                if p.suffix == ".json" and config_dir.is_dir() and p.is_relative_to(config_dir):
                    config_changed = True
                    continue
                if p.suffix != ".py" or p.name.startswith("__"):
                    continue
                # Skip utility/ directory — utility scripts don't affect graph structure
                utility_dir = pipe_dir / "utility"
                if utility_dir.is_dir() and p.is_relative_to(utility_dir):
                    continue
                if modules_dir.is_dir() and p.is_relative_to(modules_dir):
                    module_stems.append(p.stem)
                else:
                    direct_py_changes.append(p)

            known_pipelines = _known_pipeline_paths() if direct_py_changes else {}

            invalidate_pipeline_index()

            discovered_pipelines: dict[str, Path] | None = None

            def _discovered() -> dict[str, Path]:
                nonlocal discovered_pipelines
                if discovered_pipelines is None:
                    discovered_pipelines = _discovered_pipeline_paths()
                return discovered_pipelines

            for p in direct_py_changes:
                resolved_key = str(p.resolve())
                pipeline_path = _discovered().get(resolved_key) or known_pipelines.get(resolved_key)
                if pipeline_path is None:
                    logger.debug("file_watcher_skipped_non_pipeline_python", file=str(p))
                    continue
                changed_files.append(pipeline_path)

            # For changed modules, only re-parse pipelines that import them
            for stem in module_stems:
                changed_files.extend(pipelines_importing_module(stem))

            # If config JSON changed, re-parse all discovered pipelines
            if config_changed:
                changed_files.extend(_discovered().values())

            # Deduplicate and parse
            seen: set[str] = set()
            for p in changed_files:
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)

                logger.info("file_changed", file=p.name)
                try:
                    # Hash raw bytes so ANY edit triggers a broadcast.  The parser
                    # normalises code (strips whitespace, docstrings, return
                    # statements) which hides real edits from a graph-only
                    # fingerprint.  Checking before parse skips the expensive
                    # AST walk when the file is byte-identical.
                    fp = hashlib.sha256(p.read_bytes()).hexdigest()
                    fp_key = str(p.resolve())
                    if _last_broadcast_fp.get(fp_key) == fp:
                        logger.info("graph_unchanged", file=p.name)
                        continue
                    graph = parse_pipeline_to_graph(p)
                    _last_broadcast_fp[fp_key] = fp
                    # Publish through the event bus instead of hand-building a
                    # ``{"type": "graph_update", ...}`` dict for ``broadcast``.
                    # ``_ws_graph_update_subscriber`` below turns the event
                    # back into a WebSocket frame; tests and other subscribers
                    # can hang off the same event without touching this
                    # watcher body.
                    default_bus.publish(
                        "graph.update",
                        {
                            "graph": graph.model_dump(),
                            "source_file": str(p),
                        },
                    )
                    n_nodes = len(graph.nodes)
                    with ws_clients_lock:
                        client_count = len(ws_clients)
                    logger.info(
                        "graph_broadcast",
                        clients=client_count,
                        nodes=n_nodes,
                    )
                except Exception as e:
                    _last_broadcast_fp.pop(str(p.resolve()), None)
                    logger.error("parse_error", file=p.name, error=str(e))
                    default_bus.publish(
                        "parse.error",
                        {
                            "error": str(e),
                            "source_file": str(p),
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            pending_changes.update(to_process)
            logger.error(
                "file_watcher_flush_failed",
                error=str(exc),
                traceback=traceback.format_exc(),
                requeued_changes=len(to_process),
            )
            debounce_task = asyncio.create_task(_flush())

    try:
        async for changes in awatch(*watch_dirs, recursive=True):
            # Accumulate changes and (re)start the debounce timer
            pending_changes.update(changes)
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()
            debounce_task = asyncio.create_task(_flush())
        completed_normally = True
    finally:
        if debounce_task is None:
            return
        if completed_normally:
            with suppress(asyncio.CancelledError):
                await debounce_task
            return

        debounce_task.cancel()
        with suppress(asyncio.CancelledError):
            await debounce_task


# ---------------------------------------------------------------------------
# Static file serving (built React frontend)
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    def _serve_index_html() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        token = "" if local_session_auth_disabled() else local_session_token()
        token_script = f"<script>window.__HAUTE_SESSION_TOKEN__ = {json.dumps(token)};</script>"
        if "</head>" in html:
            html = html.replace("</head>", f"    {token_script}\n  </head>", 1)
        else:
            html = f"{token_script}\n{html}"
        return HTMLResponse(
            html,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/{full_path:path}", response_model=None)
    async def serve_spa(full_path: str) -> Response:
        """Serve the React SPA - all non-API routes return index.html."""
        file_path = (STATIC_DIR / full_path).resolve()
        if file_path.is_relative_to(STATIC_DIR) and file_path.is_file():
            if file_path.name == "index.html":
                return _serve_index_html()
            media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=media_type)
        return _serve_index_html()
