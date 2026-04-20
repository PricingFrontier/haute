"""Shared helpers for route modules — self-write tracking, WebSocket broadcast, pipeline parsing."""

from __future__ import annotations

import json as _json
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from fastapi import HTTPException, WebSocket

from haute._io import read_user_text
from haute._logging import get_logger
from haute.graph_utils import GraphNode, NodeType, PipelineGraph, _sanitize_func_name

logger = get_logger(component="server")

# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def validate_safe_path(base: Path, user_provided: str | Path) -> Path:
    """Resolve *user_provided* relative to *base* and verify it stays within *base*.

    Returns the resolved ``Path``.  Raises ``HTTPException(403)`` if the
    resolved path escapes the project root.
    """
    base = base.resolve()
    target = (base / user_provided).resolve()
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
    import tomllib

    from haute.errors import ConfigError

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
        return (Path.cwd() / configured).resolve().parent
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


def mark_self_write() -> None:
    """Record that we just wrote a pipeline file ourselves."""
    global _last_self_write
    _last_self_write = time.monotonic()


def is_self_write() -> bool:
    """Return True if a self-write happened within the cooldown window."""
    return (time.monotonic() - _last_self_write) < _SELF_WRITE_COOLDOWN


# ---------------------------------------------------------------------------
# WebSocket connections for live sync
# ---------------------------------------------------------------------------
# ``ws_clients`` is mutated from FastAPI's async handlers (ws_sync) AND from
# the synchronous file-watcher callback path (broadcast is awaited but its
# cleanup loop mutates the set).  On multi-worker deployments and on
# free-threaded CPython (3.13 --disable-gil) ``set.add`` / ``set.discard``
# are no longer GIL-atomic, so every add/discard must go through the
# explicit lock below.
ws_clients: set[WebSocket] = set()
ws_clients_lock = threading.Lock()


def ws_clients_add(ws: WebSocket) -> None:
    """Thread-safe ``ws_clients.add(ws)``."""
    with ws_clients_lock:
        ws_clients.add(ws)


def ws_clients_discard(ws: WebSocket) -> None:
    """Thread-safe ``ws_clients.discard(ws)``."""
    with ws_clients_lock:
        ws_clients.discard(ws)


def ws_clients_snapshot() -> list[WebSocket]:
    """Return a consistent snapshot of the current clients under the lock."""
    with ws_clients_lock:
        return list(ws_clients)


async def broadcast(data: dict[str, Any]) -> None:
    """Push a message to all connected WebSocket clients.

    Iterates a lock-protected snapshot of ``ws_clients`` so concurrent
    connects/disconnects cannot corrupt the set.  Dead clients discovered
    during the iteration are removed under the same lock.
    """
    try:
        payload = _json.dumps(data)
    except (TypeError, ValueError) as exc:
        logger.error("broadcast_serialization_failed", error=str(exc))
        return

    snapshot = ws_clients_snapshot()

    dead: list[WebSocket] = []
    for ws in snapshot:
        try:
            await ws.send_text(payload)
        except Exception:  # noqa: BLE001
            # Any ``send_text`` failure (connection closed, transport
            # error, ASGI shutdown, or a custom test double raising a
            # plain ``Exception``) marks the client dead so the next
            # broadcast doesn't waste a round-trip on it.  Narrowing
            # this except causes flaky behaviour with ASGI clients
            # that raise generic Exception subclasses.
            dead.append(ws)
    if dead:
        logger.debug("broadcast_cleaned_dead_clients", count=len(dead))
        with ws_clients_lock:
            for ws in dead:
                ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Pipeline helpers shared across routes
# ---------------------------------------------------------------------------

# Lightweight index: pipeline_name → file_path.
#
# Lifecycle (Phase 2 Wave 5 / item #75 of the review):
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
            except Exception:
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
            logger.debug("module_deps_parse_failed", file=f.name, error=str(exc))
            continue

        from haute._parser_submodels import extract_submodel_calls

        for rel_path in extract_submodel_calls(tree):
            module_stem = Path(rel_path).stem
            deps.setdefault(module_stem, set()).add(f)

    _module_deps = deps
    return _module_deps


def pipelines_importing_module(module_stem: str) -> list[Path]:
    """Return the pipeline files that import a given module (by stem name)."""
    deps = _ensure_module_deps()
    return list(deps.get(module_stem, []))


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
    """Return only the positions dict — backward-compatible alias for submodel.py."""
    result = load_sidecar(py_path).get("positions", {})
    return dict(result) if isinstance(result, dict) else {}


def save_sidecar(py_path: Path, graph: PipelineGraph) -> list[str]:
    """Write node positions + source state to the sidecar .haute.json file.

    Keys are the sanitised function names (which the parser uses as node IDs
    on re-parse), so positions survive label renames.

    When two distinct labels sanitize to the same function name only one
    position can survive — which one is arbitrary.  We detect this here
    rather than silently overwrite, emit a structured ``warning`` log
    event, and return a human-readable warnings list so callers can
    surface the collision to the UI.  The save itself still proceeds so
    users can recover once they rename the offender; the dropped
    position is simply flagged.
    """
    # Detect sanitized-name collisions BEFORE collapsing them into the
    # positions dict.  A collision would let the second node's position
    # silently overwrite the first.
    sanitized_to_labels: dict[str, list[str]] = {}
    for node in graph.nodes:
        key = _sanitize_func_name(node.data.label)
        sanitized_to_labels.setdefault(key, []).append(node.data.label)

    warnings: list[str] = []
    for sanitized, labels in sanitized_to_labels.items():
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

    positions = {_sanitize_func_name(node.data.label): node.position for node in graph.nodes}
    sidecar_data: dict[str, Any] = {"positions": positions}
    # Persist source state
    if graph.sources and graph.sources != ["live"]:
        sidecar_data["sources"] = graph.sources
    if graph.active_source and graph.active_source != "live":
        sidecar_data["active_source"] = graph.active_source
    sidecar = py_path.with_suffix(".haute.json")
    sidecar.write_text(_json.dumps(sidecar_data, indent=2) + "\n")
    return warnings


def parse_pipeline_to_graph(py_path: Path) -> PipelineGraph:
    """Parse a .py file and merge with sidecar positions + source state."""
    from haute.parser import parse_pipeline_file

    graph = parse_pipeline_file(py_path)
    sidecar = load_sidecar(py_path)
    positions: dict[str, dict[str, float]] = sidecar.get("positions", {})

    for node in graph.nodes:
        if node.id in positions:
            node.position = positions[node.id]

    # Populate source state from sidecar
    raw_sources = sidecar.get("sources")
    if isinstance(raw_sources, list) and raw_sources:
        # Ensure "live" is always first
        if "live" not in raw_sources:
            raw_sources = ["live", *raw_sources]
        elif raw_sources[0] != "live":
            raw_sources = ["live", *(s for s in raw_sources if s != "live")]
        graph.sources = raw_sources
    active = sidecar.get("active_source", "live")
    if isinstance(active, str):
        # Ensure the active source is in the sources list
        if active not in graph.sources and active != "live":
            graph.sources = [*graph.sources, active]
        if active in graph.sources:
            graph.active_source = active

    return graph
