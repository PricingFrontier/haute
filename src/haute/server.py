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
import mimetypes
import time
import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

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
    _watcher_task = asyncio.create_task(_file_watcher())
    yield
    if _watcher_task:
        _watcher_task.cancel()


app = FastAPI(title="Haute", version="0.1.0", lifespan=_lifespan)


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
    await websocket.accept()
    ws_clients_add(websocket)
    with ws_clients_lock:
        total = len(ws_clients)
    logger.info("ws_connected", total_clients=total)
    try:
        while True:
            await websocket.receive_text()  # keep-alive / client messages
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

    async def _flush() -> None:
        """Parse and broadcast after debounce window expires."""
        await asyncio.sleep(_DEBOUNCE_SECONDS)

        # Snapshot and clear BEFORE processing to avoid losing changes
        # that arrive during the async broadcast awaits below.
        to_process = set(pending_changes)
        pending_changes.clear()

        if is_self_write():
            return

        # Collect changed files from pending set
        changed_files: list[Path] = []
        module_stems: list[str] = []
        config_changed = False
        for change_type, changed_path in to_process:
            p = Path(changed_path)
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
            if p.parent == modules_dir:
                module_stems.append(p.stem)
            else:
                changed_files.append(p)

        invalidate_pipeline_index()

        # For changed modules, only re-parse pipelines that import them
        for stem in module_stems:
            changed_files.extend(pipelines_importing_module(stem))

        # If config JSON changed, re-parse all discovered pipelines
        if config_changed and not changed_files:
            changed_files.extend(
                p for p in discover_pipelines() if p.suffix == ".py" and not p.name.startswith("__")
            )

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
                await broadcast(
                    {
                        "type": "graph_update",
                        "graph": graph.model_dump(),
                        "source_file": str(p),
                    }
                )
                n_nodes = len(graph.nodes)
                logger.info(
                    "graph_broadcast",
                    clients=len(ws_clients),
                    nodes=n_nodes,
                )
            except Exception as e:
                logger.error("parse_error", file=p.name, error=str(e))
                await broadcast(
                    {
                        "type": "parse_error",
                        "error": str(e),
                        "source_file": str(p),
                    }
                )

    async for changes in awatch(*watch_dirs, recursive=True):
        # Accumulate changes and (re)start the debounce timer
        pending_changes.update(changes)
        if debounce_task and not debounce_task.done():
            debounce_task.cancel()
        debounce_task = asyncio.create_task(_flush())


# ---------------------------------------------------------------------------
# Static file serving (built React frontend)
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve the React SPA - all non-API routes return index.html."""
        file_path = (STATIC_DIR / full_path).resolve()
        if file_path.is_relative_to(STATIC_DIR) and file_path.is_file():
            media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            return FileResponse(file_path, media_type=media_type)
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")
