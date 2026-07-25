"""Shared helpers for route modules — self-write tracking, WebSocket broadcast, pipeline parsing."""

from __future__ import annotations

import asyncio
import json as _json
import math
import shutil
import tempfile
import threading
import time
import tomllib
import weakref
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from fastapi import HTTPException, WebSocket
from pydantic import BaseModel, Field, model_validator

from haute._file_ops import atomic_write_text
from haute._io import read_user_text
from haute._logging import get_logger
from haute.errors import ConfigError
from haute.graph_utils import GraphNode, NodeType, PipelineGraph, _sanitize_func_name

logger = get_logger(component="server")


# ---------------------------------------------------------------------------
# Sidecar schema (.haute.json on-disk format)
# ---------------------------------------------------------------------------


class SidecarModel(BaseModel):
    """On-disk schema for the ``.haute.json`` sidecar file.

    The sidecar carries editor-state that doesn't belong in the pipeline
    ``.py`` source-of-truth:

    * ``positions`` — canvas (x, y) co-ordinates per sanitised node id,
      so the layout survives label renames.
    * ``sources`` — ordered list of available data sources for this
      pipeline (``"live"`` is always first).
    * ``active_source`` — which source is currently selected in the UI.

    Every optional field has a sensible default so sparse sidecars still
    parse.  That current-shape defaulting contract is pinned by
    ``tests/test_routes_hygiene.py::TestSidecarDefaults``.

    Write path: ``save_sidecar`` constructs a ``SidecarModel`` and
    serialises via :meth:`model_dump_json`, excluding defaults so a
    freshly-saved pipeline with ``sources=["live"]`` does not bloat the
    file with redundant state (see
    ``tests/test_route_helpers.py::test_default_source_not_saved``).
    Read path: ``load_sidecar``/``parse_pipeline_to_graph`` still parses
    as plain JSON today, but consumers may upgrade to
    :meth:`model_validate_json` for typed access.
    """

    positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"

    @model_validator(mode="after")
    def _active_source_must_be_in_sources(self) -> SidecarModel:
        if self.active_source not in self.sources:
            raise ValueError(
                f"active_source={self.active_source!r} is not in sources={self.sources!r}"
            )
        return self


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def validate_safe_path(base: Path, user_provided: str | Path) -> Path:
    """Resolve *user_provided* relative to *base* and verify it stays within *base*.

    Returns the resolved ``Path``.  Raises ``HTTPException(400)`` for invalid
    path bytes and ``HTTPException(403)`` if the resolved path escapes the
    project root.
    """
    if "\x00" in str(user_provided):
        raise HTTPException(status_code=400, detail="Invalid path")

    base = base.resolve()
    raw_target = Path(user_provided)
    if raw_target.is_absolute() and not raw_target.is_relative_to(base):
        raise HTTPException(
            status_code=403,
            detail="Cannot access paths outside the project root",
        )

    target = (base / raw_target).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(
            status_code=403,
            detail="Cannot access paths outside the project root",
        )
    return target


# ---------------------------------------------------------------------------
# Pipeline directory resolution
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def pipeline_dir() -> Path:
    """Return the directory containing the active pipeline file as an absolute Path.

    Resolution order:

    1. ``[project].pipeline`` from ``haute.toml`` in cwd — return its parent dir.
    2. Fall back to ``Path.cwd()``.

    The result is cached for the lifetime of the process (the pipeline location
    won't change during a session).

    A missing ``[project].pipeline`` key is a soft configuration omission:
    we warn and fall back to cwd so a fresh project still works.  A
    malformed ``haute.toml`` (decode error) or an I/O error, however, is
    propagated as a ``ConfigError`` — silently returning cwd would
    route every subsequent save / load at the wrong directory and
    surface as confusing "file not found" errors far from the real
    cause.  Programming bugs inside the ``dict.get(...)`` chain
    (``AttributeError``, ``KeyError``) are deliberately NOT caught so
    they surface as normal tracebacks during development.
    """
    toml_path = Path.cwd() / "haute.toml"
    if not toml_path.exists():
        logger.error(
            "haute_toml_missing", cwd=str(Path.cwd()), hint="Run 'haute init' to create a project"
        )
        return Path.cwd().resolve()

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        logger.error("haute_toml_decode_failed", path=str(toml_path), error=str(exc))
        raise ConfigError(
            "haute.toml is malformed and could not be parsed",
            path=str(toml_path),
            error=str(exc),
        ) from exc
    except OSError as exc:
        logger.error("haute_toml_read_failed", path=str(toml_path), error=str(exc))
        raise ConfigError(
            "haute.toml could not be read",
            path=str(toml_path),
            error=str(exc),
        ) from exc

    configured: str | None = data.get("project", {}).get("pipeline")
    if configured:
        project_root = Path.cwd().resolve()
        pipeline_path = (project_root / configured).resolve()
        if not pipeline_path.is_relative_to(project_root):
            raise ConfigError(
                "haute.toml [project].pipeline resolves outside the project root",
                path=str(toml_path),
                pipeline=configured,
            )
        return pipeline_path.parent
    logger.warning(
        "haute_toml_missing_pipeline",
        path=str(toml_path),
        hint="Add [project].pipeline to haute.toml",
    )
    return Path.cwd().resolve()


# ---------------------------------------------------------------------------
# Safe error detail — prevents leaking internal exception strings (E3)
# ---------------------------------------------------------------------------

_INTERNAL_ERROR_DETAIL = "Operation failed. Check the server logs for details."

# ---------------------------------------------------------------------------
# HTTP error helpers (DRY structured-logging + HTTPException raising)
# ---------------------------------------------------------------------------


def raise_node_not_found(node_id: str) -> NoReturn:
    """Raise 404 for a missing node, with structured logging."""
    logger.warning("node_not_found", node_id=node_id)
    raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")


def raise_node_type_error(node_id: str, expected: str, got: str) -> NoReturn:
    """Raise 400 for a node type mismatch, with structured logging."""
    logger.warning("node_type_mismatch", node_id=node_id, expected=expected, got=got)
    raise HTTPException(
        status_code=400,
        detail=f"Node '{node_id}' is not a {expected} node (got {got})",
    )


def raise_pipeline_not_found(name: str) -> NoReturn:
    """Raise 404 for a missing pipeline, with structured logging."""
    logger.warning("pipeline_not_found", name=name)
    raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found")


def raise_validation_error(detail: str) -> NoReturn:
    """Raise 400 for a validation failure, with structured logging."""
    logger.warning("validation_error", detail=detail)
    raise HTTPException(status_code=400, detail=detail)


def find_typed_node(
    graph: PipelineGraph,
    node_id: str,
    expected_type: NodeType,
    type_label: str,
) -> GraphNode:
    """Find a node by ID and verify its ``nodeType``.

    Raises ``HTTPException(404)`` if the node is missing, or
    ``HTTPException(400)`` if it has the wrong type.

    Parameters
    ----------
    graph:
        The pipeline graph to search.
    node_id:
        Node identifier.
    expected_type:
        The :class:`NodeType` value the node must match (e.g.
        ``NodeType.MODELLING``).
    type_label:
        Human-readable label used in the error message (e.g. ``"modelling"``).
    """
    node: GraphNode | None = graph.node_map.get(node_id)
    if node is None:
        raise_node_not_found(node_id)
    if node.data.nodeType != expected_type:
        raise_node_type_error(node_id, type_label, str(node.data.nodeType))
    return node


# ---------------------------------------------------------------------------
# Self-write tracking (avoid file-watcher feedback loops)
# ---------------------------------------------------------------------------
_last_self_write: float = 0.0
_SELF_WRITE_COOLDOWN = 2.0  # seconds (must exceed save duration + watcher debounce)
_SELF_WRITE_RETENTION = 60.0
_self_write_paths: dict[str, float] = {}
_self_write_lock = threading.Lock()


# Bundle 5.M1 — single-writer guarantee across all save-shaped endpoints.
# OPUS race-conditions scenarios S1 (concurrent /pipeline/save stale-cleanup
# clobber) and S4 (codegen-vs-sidecar split mid-save) both stem from two
# saves interleaving. The lock is acquired by:
#   - routes/pipeline.py::save_pipeline    (/api/pipeline/save)
#   - routes/submodel.py::create_submodel  (/api/submodel/create)
#   - routes/submodel.py::dissolve_submodel (/api/submodel/dissolve)
# all of which touch the project's .py / .haute.json / config sidecars.
#
# Scope: global (per-process). Per-pipeline keying would be sharper but
# the single-user threat model has effectively no contention; the global
# lock is the cheaper, well-trodden pattern (matches `_pipeline_index_lock`,
# `_self_write_lock`, `ws_clients_lock` above).
#
# Save bodies run in a threadpool while this async lock is held, keeping the
# event loop responsive without allowing two write-shaped operations to
# interleave. It does NOT protect against multiple uvicorn worker processes
# — out of scope under the single-user trust model.
save_lock: asyncio.Lock = asyncio.Lock()


def _self_write_key(path: str | Path) -> str:
    return str(Path(path).resolve())


def _prune_self_write_paths(now: float) -> None:
    stale = [
        key
        for key, marked_at in _self_write_paths.items()
        if now - marked_at > _SELF_WRITE_RETENTION
    ]
    for key in stale:
        _self_write_paths.pop(key, None)


def mark_self_write(path: str | Path | None = None) -> None:
    """Record that the server is about to write a pipeline-related file."""
    global _last_self_write
    now = time.monotonic()
    with _self_write_lock:
        _last_self_write = now
        if path is not None:
            _prune_self_write_paths(now)
            _self_write_paths[_self_write_key(path)] = now


def is_self_write(path: str | Path | None = None, *, consume: bool = False) -> bool:
    """Return True when a watcher event belongs to a server-originated write."""
    now = time.monotonic()
    with _self_write_lock:
        if path is None:
            return (now - _last_self_write) < _SELF_WRITE_COOLDOWN

        _prune_self_write_paths(now)
        key = _self_write_key(path)
        matched = key in _self_write_paths
        if matched and consume:
            _self_write_paths.pop(key, None)
        return matched


# ---------------------------------------------------------------------------
# Watcher pause (S30) — suspend the file-watcher during haute-initiated git ops
# ---------------------------------------------------------------------------
# A move/checkout/merge replaces the working tree wholesale. The per-path
# self-write registry above can't cover that — the changed paths aren't known
# ahead of time — so for haute-initiated git operations the watcher is paused
# entirely (S30 ruling: "if haute hangs the watcher isn't protecting anything
# anyway"). Resume is guaranteed by ``pause_watcher``'s try/finally; a deadline
# watchdog force-resumes if a git op overruns or never unwinds the context; and
# a short post-release settle window absorbs the debounced filesystem events the
# checkout leaves behind so they aren't broadcast as user edits.
_watcher_pause_lock = threading.Lock()
_watcher_pause_depth = 0
_watcher_pause_deadline = 0.0  # monotonic; hard cap so a hung op can't freeze the watcher
_watcher_pause_released_at = 0.0  # monotonic; start of the post-release settle window
_watcher_pause_watchdog_fired = False  # de-dupe the watchdog warning within one overrun
_WATCHER_PAUSE_MAX_SECONDS = 60.0  # watchdog: longest a single git op may hold the pause
_WATCHER_PAUSE_SETTLE_SECONDS = 1.0  # must exceed the watcher debounce so post-op events drop


@contextmanager
def pause_watcher(max_seconds: float = _WATCHER_PAUSE_MAX_SECONDS) -> Iterator[None]:
    """Pause the file-watcher for the duration of a haute-initiated git op.

    Reentrant (depth-counted) so nested git ops share a single pause. Resume is
    guaranteed on exit even if the body raises. ``max_seconds`` bounds how long
    the pause may hold before the watchdog (see :func:`watcher_is_paused`)
    force-resumes the watcher, so a hung git op can never freeze live-sync.
    """
    global _watcher_pause_depth, _watcher_pause_deadline
    global _watcher_pause_released_at, _watcher_pause_watchdog_fired
    with _watcher_pause_lock:
        _watcher_pause_depth += 1
        # Outermost pause sets the deadline; a nested pause may only extend it.
        _watcher_pause_deadline = max(_watcher_pause_deadline, time.monotonic() + max_seconds)
        _watcher_pause_watchdog_fired = False
    try:
        yield
    finally:
        with _watcher_pause_lock:
            _watcher_pause_depth -= 1
            if _watcher_pause_depth <= 0:
                _watcher_pause_depth = 0
                _watcher_pause_deadline = 0.0
                _watcher_pause_released_at = time.monotonic()


def watcher_is_paused() -> bool:
    """Return True while the file-watcher should suspend processing.

    True when a git op holds the pause, or within the post-release settle window
    that swallows the checkout's debounced trailing events. The watchdog: if an
    active pause has outlived its deadline, report unpaused (force-resume) so a
    hung or non-unwinding git op cannot freeze the watcher permanently.
    """
    global _watcher_pause_watchdog_fired
    now = time.monotonic()
    with _watcher_pause_lock:
        if _watcher_pause_depth > 0:
            if now <= _watcher_pause_deadline:
                return True
            # Watchdog tripped: the op overran its deadline — give up and resume.
            if not _watcher_pause_watchdog_fired:
                _watcher_pause_watchdog_fired = True
                logger.warning(
                    "watcher_pause_watchdog_resumed",
                    depth=_watcher_pause_depth,
                    max_seconds=_WATCHER_PAUSE_MAX_SECONDS,
                )
            return False
        # Post-release settle window absorbs the just-finished checkout's
        # debounced filesystem events so they aren't mistaken for user edits.
        return (now - _watcher_pause_released_at) < _WATCHER_PAUSE_SETTLE_SECONDS


# ---------------------------------------------------------------------------
# WebSocket connections for live sync
# ---------------------------------------------------------------------------
# ``ws_clients`` is mutated from FastAPI's async handlers (ws_sync) AND from
# the event-bus broadcast tasks scheduled by the file-watcher path. On
# multi-worker deployments and on
# free-threaded CPython (3.13 --disable-gil) ``set.add`` / ``set.discard``
# are no longer GIL-atomic, so every add/discard must go through the
# explicit lock below.
ws_clients: set[WebSocket] = set()
ws_clients_lock = threading.Lock()
_WS_SEND_TIMEOUT_SECONDS = 1.0
_ws_send_state_lock = threading.Lock()
_ws_send_inflight: weakref.WeakSet[WebSocket] = weakref.WeakSet()
_ws_send_pending: weakref.WeakKeyDictionary[WebSocket, deque[str]] = weakref.WeakKeyDictionary()


def _drain_abandoned_ws_task(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.debug("broadcast_abandoned_ws_task_failed", error=str(exc))


def _cancel_and_drain_abandoned_ws_task(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    task.add_done_callback(_drain_abandoned_ws_task)


def _clear_ws_send_state(ws: WebSocket) -> None:
    with _ws_send_state_lock:
        _ws_send_inflight.discard(ws)
        _ws_send_pending.pop(ws, None)


def ws_clients_add(ws: WebSocket) -> None:
    """Thread-safe ``ws_clients.add(ws)``."""
    with ws_clients_lock:
        ws_clients.add(ws)


def ws_clients_discard(ws: WebSocket) -> None:
    """Thread-safe ``ws_clients.discard(ws)``."""
    with ws_clients_lock:
        ws_clients.discard(ws)
    _clear_ws_send_state(ws)


def ws_clients_snapshot() -> list[WebSocket]:
    """Return a consistent snapshot of the current clients under the lock."""
    with ws_clients_lock:
        return list(ws_clients)


async def broadcast(data: dict[str, Any]) -> None:
    """Push a message to all connected WebSocket clients.

    Iterates a lock-protected snapshot of ``ws_clients`` so concurrent
    connects/disconnects cannot corrupt the set.  Dead clients discovered
    during the send fan-out are removed under the same lock.
    """
    try:
        payload = _json.dumps(data)
    except (TypeError, ValueError) as exc:
        logger.error("broadcast_serialization_failed", error=str(exc))
        return

    snapshot = ws_clients_snapshot()
    if not snapshot:
        return

    async def _close_stalled_client(ws: WebSocket) -> None:
        close_task = asyncio.create_task(ws.close())
        try:
            await asyncio.wait_for(
                asyncio.shield(close_task),
                timeout=_WS_SEND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _cancel_and_drain_abandoned_ws_task(close_task)
            logger.debug("broadcast_stalled_ws_close_timed_out")
        except asyncio.CancelledError:
            _cancel_and_drain_abandoned_ws_task(close_task)
            raise
        except Exception as exc:
            logger.debug("broadcast_stalled_ws_close_failed", error=str(exc))

    async def _send_text_with_hard_timeout(ws: WebSocket, current_payload: str) -> None:
        send_task = asyncio.create_task(ws.send_text(current_payload))
        try:
            await asyncio.wait_for(
                asyncio.shield(send_task),
                timeout=_WS_SEND_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _cancel_and_drain_abandoned_ws_task(send_task)
            ws_clients_discard(ws)
            await _close_stalled_client(ws)
            raise
        except asyncio.CancelledError:
            _cancel_and_drain_abandoned_ws_task(send_task)
            raise
        except BaseException:
            if not send_task.done():
                _cancel_and_drain_abandoned_ws_task(send_task)
            raise

    async def _send_serialized(ws: WebSocket, initial_payload: str) -> None:
        current_payload = initial_payload
        while True:
            try:
                await _send_text_with_hard_timeout(ws, current_payload)
            except BaseException:
                _clear_ws_send_state(ws)
                raise

            with _ws_send_state_lock:
                pending = _ws_send_pending.get(ws)
                if not pending:
                    _ws_send_pending.pop(ws, None)
                    _ws_send_inflight.discard(ws)
                    return
                current_payload = pending.popleft()
                if not pending:
                    _ws_send_pending.pop(ws, None)

    async def _send_with_timeout(ws: WebSocket) -> str:
        with _ws_send_state_lock:
            if ws in _ws_send_inflight:
                pending = _ws_send_pending.get(ws)
                if pending is None:
                    pending = deque()
                    _ws_send_pending[ws] = pending
                pending.append(payload)
                return "queued"
            _ws_send_inflight.add(ws)
        await _send_serialized(ws, payload)
        return "sent"

    tasks = [asyncio.create_task(_send_with_timeout(ws)) for ws in snapshot]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise

    dead = [
        ws for ws, result in zip(snapshot, results, strict=False) if isinstance(result, Exception)
    ]
    if dead:
        logger.debug("broadcast_cleaned_dead_clients", count=len(dead))
        for ws in dead:
            ws_clients_discard(ws)


# ---------------------------------------------------------------------------
# Pipeline helpers shared across routes
# ---------------------------------------------------------------------------

# Lightweight index: pipeline_name → file_path.
#
# Lifecycle:
#   1. Populated at server startup by ``haute.server._lifespan``.
#   2. Rebuilt on file-watcher events by ``haute.server._file_watcher``, which
#      is the *only* production caller of ``invalidate_pipeline_index``.
# No other production code path is allowed to clear or rebuild the index —
# doing so reintroduces the "two sources of truth" race the review flagged.
#
# The lock below serialises the rebuild path so two concurrent readers that
# both observe a cold cache cannot scan the filesystem twice.  The swap at
# the end of ``_ensure_pipeline_index`` assigns a fully-built local dict to
# the module global in a single bytecode op; readers therefore either see
# the previous dict or the new dict, never a half-populated one.
_pipeline_index: dict[str, Path] | None = None
_pipeline_index_lock = threading.Lock()


def invalidate_pipeline_index() -> None:
    """Clear the cached pipeline name→path index.

    Intended to be called **only** from the file-watcher in
    ``haute.server._file_watcher``.  All other production code paths must
    treat the cache as read-only — startup + watcher are the two — and only
    two — legitimate writers.  Test suites are free to poke this directly
    to set up fresh state between tests.
    """
    global _pipeline_index, _module_deps
    with _pipeline_index_lock:
        _pipeline_index = None
        _module_deps = None


def _ensure_pipeline_index() -> dict[str, Path]:
    """Build or return the cached pipeline name→path index.

    If the cache is already populated, returns it without taking the lock
    (reads of a single reference are atomic on CPython).  If the cache is
    ``None``, acquires the lock, re-checks (double-checked locking), and
    builds the index into a *local* dict.  Only the final assignment to
    ``_pipeline_index`` publishes the new dict — concurrent readers never
    observe a partially-constructed mapping.
    """
    global _pipeline_index

    cached = _pipeline_index
    if cached is not None:
        return cached

    from haute.discovery import discover_pipelines as _discover
    from haute.parser import parse_pipeline_file

    with _pipeline_index_lock:
        # Re-check under the lock: another thread may have built the index
        # while we were waiting.  Returning the existing dict here avoids a
        # redundant filesystem scan and guarantees all concurrent callers
        # agree on the same object.
        cached = _pipeline_index
        if cached is not None:
            return cached

        new_index: dict[str, Path] = {}
        for f in _discover():
            try:
                graph = parse_pipeline_file(f)
                name = graph.pipeline_name or f.stem
                new_index[name] = f
            except Exception as exc:
                # Parse failure — index by stem as a fallback so the file is
                # still listable, but log at ``warning`` so the user can
                # surface the actual problem in their pipeline.  Using
                # ``debug`` or silent-skip would mask a broken pipeline as
                # "the file just uses its stem as its name".
                logger.warning(
                    "pipeline_index_parse_failed",
                    path=str(f),
                    stem=f.stem,
                    error=repr(exc),
                )
                new_index[f.stem] = f

        # Atomic publish: a single assignment is one bytecode op in CPython,
        # so readers never see ``None`` as a transient state during rebuild.
        _pipeline_index = new_index
        return new_index


def discover_pipelines() -> list[Path]:
    """Find pipeline .py files in the project root that contain ``haute.Pipeline``."""
    from haute.discovery import discover_pipelines as _discover

    return _discover()


# ---------------------------------------------------------------------------
# Module dependency map: module_stem → set of pipeline Paths that import it
# ---------------------------------------------------------------------------
_module_deps: dict[str, set[Path]] | None = None


def _module_dep_key(module_stem: str) -> str:
    """Canonical module-dep map key for a module file stem.

    The build side derives stems from ``pipeline.submodel("modules/<name>.py")``
    source literals; the file-watcher derives them from on-disk filenames.  On
    a case-insensitive filesystem (macOS/Windows) a literal ``modules/foo.py``
    resolves against an on-disk ``modules/Foo.py`` — the two spellings name
    ONE module, so both sides must casefold through this single helper or the
    map key and the watcher's query silently drift and live-sync goes stale.
    """
    return module_stem.casefold()


def _ensure_module_deps() -> dict[str, set[Path]]:
    """Build or return the cached module → pipeline dependency map.

    Scans each pipeline source for ``pipeline.submodel("...")`` calls and
    maps the module file stem to the set of pipeline files that reference it.
    """
    global _module_deps
    if _module_deps is not None:
        return _module_deps

    import ast

    deps: dict[str, set[Path]] = {}
    for f in discover_pipelines():
        try:
            source = read_user_text(f)
            tree = ast.parse(source)
        except Exception as exc:
            logger.warning("module_deps_parse_failed", file=f.name, error=str(exc))
            continue

        from haute._parser_submodels import extract_submodel_calls

        for rel_path in extract_submodel_calls(tree):
            module_stem = Path(rel_path).stem
            deps.setdefault(_module_dep_key(module_stem), set()).add(f)

    _module_deps = deps
    return _module_deps


def pipelines_importing_module(module_stem: str) -> list[Path]:
    """Return the pipeline files that import a given module (by stem name).

    The stem is matched case-insensitively (see :func:`_module_dep_key`): the
    watcher passes the on-disk filename's stem, which may differ in case from
    the ``pipeline.submodel("...")`` source literal the map was built from.
    """
    deps = _ensure_module_deps()
    return list(deps.get(_module_dep_key(module_stem), []))


def lookup_pipeline_by_name(name: str) -> Path | None:
    """O(1) lookup of a pipeline file by name, using the cached index."""
    index = _ensure_pipeline_index()
    return index.get(name)


def load_sidecar(py_path: Path) -> dict[str, Any]:
    """Load the full sidecar .haute.json file as a dict.

    Returns a dict with ``positions``, ``sources``, and ``active_source``
    keys (all optional — callers should use ``.get()``).
    """
    sidecar = py_path.with_suffix(".haute.json")
    if sidecar.exists():
        try:
            return dict(_json.loads(read_user_text(sidecar)))
        except (_json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            logger.warning("corrupt_sidecar", file=sidecar.name, error=str(e))
    return {}


def load_sidecar_positions(py_path: Path) -> dict[str, Any]:
    """Return only the positions dict for submodel sidecar loading."""
    result = load_sidecar(py_path).get("positions", {})
    return dict(result) if isinstance(result, dict) else {}


def _sidecar_position_key(node: GraphNode) -> str:
    """Return the sidecar positions key that will match this node on re-parse.

    The load path (:func:`parse_pipeline_to_graph`) looks positions up by
    ``node.id``, where ``node.id`` is whatever the parser reconstructs from
    the regenerated ``.py``.  For ordinary nodes that id IS the sanitised
    function name, so keying by ``sanitize(label)`` round-trips.

    Submodel placeholder nodes are the exception: the parser rebuilds them
    with ``id = "submodel__" + sanitize(name)`` (see
    ``_submodel_graph.build_submodel_node``) while their ``data.label`` is the
    bare submodel name.  Keying purely by ``sanitize(label)`` therefore wrote
    ``"model_stuff"`` but the load read ``"submodel__model_stuff"`` — a guaranteed
    miss, so every submodel node snapped back to (0, 0) on reload.

    Mirroring the parser's id reconstruction here keeps the write key and the
    read key identical for every node type.
    """
    sanitized = _sanitize_func_name(node.data.label)
    if node.data.nodeType == NodeType.SUBMODEL:
        return f"submodel__{sanitized}"
    return sanitized


def save_sidecar(py_path: Path, graph: PipelineGraph) -> list[str]:
    """Write node positions + source state to the sidecar .haute.json file.

    Keys are the node ids the parser assigns on re-parse (the sanitised
    function name for ordinary nodes, ``submodel__<name>`` for submodel
    placeholders — see :func:`_sidecar_position_key`), so positions survive
    label renames and round-trip for every node type.

    When two distinct labels collapse to the same key only one
    position can survive — which one is arbitrary.  We detect this here
    rather than silently overwrite, emit a structured ``warning`` log
    event, and return a human-readable warnings list so callers can
    surface the collision to the UI.  The save itself still proceeds so
    users can recover once they rename the offender; the dropped
    position is simply flagged.
    """
    # Detect key collisions BEFORE collapsing them into the positions dict.
    # A collision would let the second node's position silently overwrite the
    # first.
    key_to_labels: dict[str, list[str]] = {}
    for node in graph.nodes:
        key = _sidecar_position_key(node)
        key_to_labels.setdefault(key, []).append(node.data.label)

    warnings: list[str] = []
    for sanitized, labels in key_to_labels.items():
        if len(labels) <= 1:
            continue
        logger.warning(
            "sidecar_position_collision",
            sanitized=sanitized,
            labels=labels,
            file=py_path.name,
        )
        warnings.append(
            f"Position for node {labels[-1]!r} replaces node {labels[0]!r} "
            f"because both sanitize to {sanitized!r}"
        )

    positions = {_sidecar_position_key(node): node.position for node in graph.nodes}

    # Build the on-disk payload via ``SidecarModel`` so the schema is
    # typed and validated.  We still omit default source state so a
    # freshly-saved pipeline with ``sources=["live"]`` does not bloat the
    # file — callers that never touched the source selector should not
    # see spurious sidecar keys appear.  This is pinned by
    # ``test_route_helpers.py::test_default_source_not_saved``.
    model_kwargs: dict[str, Any] = {"positions": positions}
    if graph.sources and graph.sources != ["live"]:
        model_kwargs["sources"] = graph.sources
    if graph.active_source and graph.active_source != "live":
        model_kwargs["active_source"] = graph.active_source

    sidecar_model = SidecarModel(**model_kwargs)
    # ``exclude_defaults`` drops any field whose value equals the
    # declared default, so unset ``sources``/``active_source`` do not
    # serialise.  ``indent`` matches the prior manual ``json.dumps``
    # output so diffs on existing sidecars stay minimal.
    serialised = sidecar_model.model_dump_json(
        indent=2,
        exclude_defaults=True,
    )
    sidecar = py_path.with_suffix(".haute.json")
    # Bundle 5.M2 — atomic write closes the partial-bytes window OPUS
    # race-scenario S2 surfaced: the file-watcher's reparse path and
    # any concurrent /pipeline GET hit `load_sidecar`, which would see
    # a half-written file if `Path.write_text` truncates then writes
    # non-atomically. `atomic_write_text` stages to a sibling temp and
    # renames into place, so a reader NEVER observes torn/partial bytes
    # on any OS. On POSIX the rename also fully succeeds under concurrent
    # readers (rename(2) is atomic). On Windows the corruption window is
    # likewise closed, but the rename is NOT guaranteed to succeed under
    # reader contention: a concurrent open reader (default `open()` does
    # not pass FILE_SHARE_DELETE) can make the replace raise
    # PermissionError (ERROR_ACCESS_DENIED), surfacing this save as a 500.
    # That is a fail-loud miss, not silent corruption, and is acceptable
    # under the single-user trust model. See `atomic_write_bytes` for the
    # primitive-level note. Pinning tests:
    # TestSaveSidecar.test_writes_atomically_via_atomic_write_text and
    # tests/test_file_ops.py::TestAtomicWriteWindowsReaderContention.
    atomic_write_text(sidecar, serialised + "\n")
    return warnings


def parse_pipeline_to_graph(py_path: Path) -> PipelineGraph:
    """Parse a .py file and merge with sidecar positions + source state."""
    from haute.parser import parse_pipeline_file

    graph = parse_pipeline_file(py_path)
    sidecar = load_sidecar(py_path)
    positions = _normalise_sidecar_positions(sidecar.get("positions"))

    for node in graph.nodes:
        position = positions.get(node.id)
        # Backward-compat: sidecars written before the submodel-key fix keyed
        # submodel positions by the bare ``sanitize(label)`` (e.g.
        # ``"model_stuff"``) instead of the parser's ``submodel__<name>`` id.
        # Fall back to the legacy key so existing pipelines don't snap their
        # submodel nodes back to (0, 0) on the first reload after the fix;
        # the next save rewrites the sidecar with the correct key.
        if position is None and node.data.nodeType == NodeType.SUBMODEL:
            position = positions.get(_sanitize_func_name(node.data.label))
        if position is not None:
            node.position = position

    # Populate source state from sidecar
    raw_sources = _normalise_sidecar_sources(sidecar.get("sources"))
    if raw_sources is not None:
        graph.sources = raw_sources
    active = sidecar.get("active_source", "live")
    if isinstance(active, str) and active.strip():
        active = active.strip()
        # Ensure the active source is in the sources list
        if active not in graph.sources and active != "live":
            graph.sources = [*graph.sources, active]
        if active in graph.sources:
            graph.active_source = active

    return graph


def commit_pipeline_graph(sha: str) -> PipelineGraph:
    """Parse the active pipeline as it was at commit *sha* into a read-only graph
    (S11). Only pipeline artifacts are materialised (no checkout, no HEAD
    change). Parse failures are explicit rather than a successful empty graph."""
    from haute._git import GitHistoryReadError, archive_commit
    from haute.discovery import discover_pipelines as _discover_in

    root = Path(tempfile.mkdtemp(prefix="haute-show-"))
    try:
        archive_commit(sha, root)
        best: PipelineGraph | None = None
        candidates = sorted(_discover_in(root=root))
        parse_failures = 0
        for f in candidates:
            try:
                graph = parse_pipeline_to_graph(f)
                graph.source_file = str(f.relative_to(root))
                if graph.nodes:
                    return graph
                best = best if best is not None else graph
            except Exception as e:
                parse_failures += 1
                logger.warning("commit_parse_failed", file=f.name, error=str(e))
                continue
        if best is not None:
            return best
        if parse_failures:
            raise GitHistoryReadError("The selected version's pipeline could not be parsed.")
        raise GitHistoryReadError(
            "The selected version does not contain a readable Haute pipeline."
        )
    finally:
        for attempt in range(3):
            try:
                shutil.rmtree(root)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 2:
                    logger.warning("commit_temp_cleanup_failed", path=str(root), error=str(exc))
                else:
                    time.sleep(0.02 * (attempt + 1))


def _normalise_sidecar_sources(raw_sources: Any) -> list[str] | None:
    if not isinstance(raw_sources, list):
        return None

    seen: set[str] = set()
    cleaned: list[str] = []
    saw_live = False
    for value in raw_sources:
        if not isinstance(value, str):
            continue
        source = value.strip()
        if not source:
            continue
        if source == "live":
            saw_live = True
            continue
        if source in seen:
            continue
        seen.add(source)
        cleaned.append(source)

    if not cleaned and not saw_live:
        return None
    return ["live", *cleaned]


def _normalise_sidecar_positions(raw_positions: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw_positions, dict):
        return {}

    positions: dict[str, dict[str, float]] = {}
    for node_id, position in raw_positions.items():
        if not isinstance(node_id, str) or not isinstance(position, dict):
            continue
        x = position.get("x")
        y = position.get("y")
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            continue
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            continue
        xf = float(x)
        yf = float(y)
        if not math.isfinite(xf) or not math.isfinite(yf):
            continue
        positions[node_id] = {"x": xf, "y": yf}
    return positions
