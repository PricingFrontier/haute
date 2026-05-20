"""JSON cache endpoints — explicit parquet caching for large JSONL files.

Two cache shapes coexist behind these endpoints:

- **v1 (legacy flat).** ``flattenSchema`` on disk; one ``data.parquet``
  per cache directory; columns are index-expanded array fields. See
  ``haute._json_flatten`` for the implementation.
- **v2 (multi-frame).** ``tables[]`` on disk; one parquet per emit-true
  table; columns belong to that table's iteration depth. See
  ``haute._json_shred`` for the implementation.

The route dispatch reads the on-disk config file, checks
:func:`haute._api_input_schema.is_v2_shape`, and routes to the v2 path
when applicable. v1 is preserved verbatim until commit 10's cleanup.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import orjson
from fastapi import APIRouter, HTTPException

from haute._api_input_schema import is_v2_shape
from haute._logging import get_logger
from haute._path_resolution import resolve_runtime_file_path
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, pipeline_dir
from haute.routes._timeouts import run_blocking_with_response_timeout
from haute.schemas import (
    JsonCacheBuildRequest,
    JsonCacheBuildResponse,
    JsonCacheCancelResponse,
    JsonCacheProgressResponse,
    JsonCacheStatusResponse,
)

logger = get_logger(component="server.json_cache")

router = APIRouter(prefix="/api/json-cache", tags=["json-cache"])

# ── Timeout constant (seconds) ───────────────────────────────────
_BUILD_TIMEOUT = float(os.environ.get("HAUTE_BUILD_TIMEOUT", "1800"))


def _resolve_data_path(path: str) -> str:
    try:
        return str(
            resolve_runtime_file_path(
                path,
                pipeline_dir=pipeline_dir(),
                project_root=Path.cwd(),
                prefer="project",
                enforce_project_root=True,
            )
        )
    except ValueError as exc:
        status_code = 400 if "embedded null byte" in str(exc) else 403
        raise HTTPException(status_code=status_code, detail=str(exc)) from None


def _resolve_config_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(
            resolve_runtime_file_path(
                path,
                pipeline_dir=pipeline_dir(),
                project_root=Path.cwd(),
                prefer="pipeline",
                enforce_project_root=True,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


def _read_v2_config(config_path: str | None) -> dict[str, Any] | None:
    """Read *config_path* and return its content iff it's a v2 schema mapping.

    Returns ``None`` when the file is absent, unreadable, malformed, or v1.
    Used by the dispatch in the build / status routes; callers fall back
    to v1 behaviour when this returns ``None``.
    """
    if not config_path:
        return None
    p = Path(config_path)
    if not p.exists():
        return None
    try:
        raw = orjson.loads(p.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if not is_v2_shape(raw):
        return None
    return raw


def _aggregate_v2_build_response(
    summary: dict[str, Any],
    cache_dir: Path,
    data_path: str,
    elapsed_seconds: float,
) -> JsonCacheBuildResponse:
    """Collapse a v2 per-port summary into the flat build-response shape.

    The wire schema (``JsonCacheBuildResponse``) is flat — single
    ``row_count``, single ``column_count``, etc. v2 has N tables, each
    with its own counts. We aggregate:

    - ``row_count`` = sum of per-table row counts.
    - ``column_count`` = sum of per-table column counts (each table's
      columns are disjoint).
    - ``columns`` = ``{f"{label}.{col_name}": "v2"}`` so consumers can
      still discriminate per-table column identity.
    - ``size_bytes`` = sum of per-table parquet sizes on disk.
    - ``cached_at`` = max(parquet mtime) on disk.
    - ``path`` = the cache directory (not a single file).
    """
    tables = summary.get("tables", []) or []
    row_count = sum(int(t.get("row_count", 0)) for t in tables)
    column_count = sum(int(t.get("column_count", 0)) for t in tables)
    columns: dict[str, str] = {}
    size_bytes = 0
    cached_at = 0.0
    for table in tables:
        label = table.get("label", "")
        parquet_name = table.get("parquet")
        if isinstance(parquet_name, str):
            parquet_path = cache_dir / parquet_name
            if parquet_path.exists():
                stat = parquet_path.stat()
                size_bytes += int(stat.st_size)
                cached_at = max(cached_at, float(stat.st_mtime))
        for ci in range(int(table.get("column_count", 0))):
            # Without re-reading the parquet schema we don't have column
            # names here cheaply; for the build-response we just label by
            # table+index. The detailed per-column dict is the status
            # endpoint's job.
            columns[f"{label}.col{ci}"] = "v2"
    return JsonCacheBuildResponse(
        path=str(cache_dir),
        data_path=data_path,
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        size_bytes=size_bytes,
        cached_at=cached_at,
        cache_seconds=round(elapsed_seconds, 3),
    )


def _aggregate_v2_status_response(
    cache_dir: Path,
    data_path: str,
    meta: dict[str, Any],
) -> JsonCacheStatusResponse:
    """Same aggregation as the build response, for status queries."""
    tables = meta.get("tables", []) or []
    row_count = sum(int(t.get("row_count", 0)) for t in tables)
    column_count = sum(int(t.get("column_count", 0)) for t in tables)
    columns: dict[str, str] = {}
    size_bytes = 0
    cached_at = 0.0
    for table in tables:
        label = table.get("label", "")
        parquet_name = table.get("parquet")
        if isinstance(parquet_name, str):
            parquet_path = cache_dir / parquet_name
            if parquet_path.exists():
                stat = parquet_path.stat()
                size_bytes += int(stat.st_size)
                cached_at = max(cached_at, float(stat.st_mtime))
        for ci in range(int(table.get("column_count", 0))):
            columns[f"{label}.col{ci}"] = "v2"
    return JsonCacheStatusResponse(
        cached=True,
        path=str(cache_dir),
        data_path=data_path,
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        size_bytes=size_bytes,
        cached_at=cached_at,
    )


@router.post("/build", response_model=JsonCacheBuildResponse)
async def build_json_cache(body: JsonCacheBuildRequest) -> JsonCacheBuildResponse:
    """Flatten or shred a JSON/JSONL file and cache it as parquet.

    Dispatch:
    - v2 schema mapping on disk → per-port shred (one parquet per
      emit-true table) via :func:`haute._json_shred.build_per_port_cache`.
    - Otherwise → v1 flat shred via
      :func:`haute._json_flatten.build_json_cache`.
    """
    data_path = _resolve_data_path(body.path)
    config_path = _resolve_config_path(body.config_path)

    # v2 dispatch — if the config file is on disk and is v2-shaped,
    # bypass the v1 path entirely.
    v2_config = _read_v2_config(config_path)
    if v2_config is not None:
        from haute._json_flatten import _json_cache_dir
        from haute._json_shred import build_per_port_cache

        cache_dir = _json_cache_dir(data_path, "working")
        t0 = time.monotonic()
        try:
            summary = await run_blocking_with_response_timeout(
                build_per_port_cache,
                data_path=data_path,
                v2_config=v2_config,
                cache_dir=cache_dir,
                timeout=_BUILD_TIMEOUT,
                operation="json_cache_build_v2",
            )
        except TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"JSON cache build timed out ({_BUILD_TIMEOUT / 60:.0f} min limit)",
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Data file not found") from None
        except orjson.JSONDecodeError as e:
            # The data file is unparseable JSON. The schema is fine —
            # don't tell the user their schema is broken.
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON in data file: {e}",
            ) from None
        except ValueError as e:
            # A schema-validation error from validate_v2_schema OR
            # parse_table_path raises ValueError. The catch is placed
            # AFTER orjson.JSONDecodeError so data-file parse errors
            # don't get mis-labelled as schema errors.
            raise HTTPException(status_code=422, detail=f"Invalid v2 schema: {e}") from None
        except Exception as e:
            logger.error("json_cache_build_v2_failed", error=str(e))
            raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
        elapsed = time.monotonic() - t0
        return _aggregate_v2_build_response(summary, cache_dir, data_path, elapsed)

    # v1 path — unchanged from before.
    try:
        from haute._json_flatten import (
            JsonCacheCancelledError,
            JsonFlattenDataError,
            JsonFlattenSchemaError,
        )
        from haute._json_flatten import (
            build_json_cache as _build,
        )

        result = await run_blocking_with_response_timeout(
            _build,
            data_path=data_path,
            schema=body.flatten_schema,
            config_path=config_path,
            timeout=_BUILD_TIMEOUT,
            operation="json_cache_build",
        )
        return JsonCacheBuildResponse.model_validate(result)
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"JSON cache build timed out ({_BUILD_TIMEOUT / 60:.0f} min limit)",
        )
    except JsonCacheCancelledError:
        raise HTTPException(status_code=499, detail="Cache build cancelled")
    except JsonFlattenDataError as e:
        line = e.context.get("line")
        detail = e.message
        if isinstance(line, int):
            detail = f"{detail} at line {line}"
        raise HTTPException(status_code=400, detail=detail) from None
    except JsonFlattenSchemaError as e:
        raise HTTPException(status_code=422, detail=f"Invalid flatten schema: {e}") from None
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Data file not found") from None
    except Exception as e:
        logger.error("json_cache_build_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.post("/cancel", response_model=JsonCacheCancelResponse)
async def cancel_json_cache_build(body: JsonCacheBuildRequest) -> JsonCacheCancelResponse:
    """Cancel an in-progress JSON cache build."""
    data_path = _resolve_data_path(body.path)
    from haute._json_flatten import cancel_json_cache

    cancelled = cancel_json_cache(data_path)
    return JsonCacheCancelResponse(cancelled=cancelled, data_path=body.path)


@router.get("/progress", response_model=JsonCacheProgressResponse)
async def get_json_cache_progress(path: str) -> JsonCacheProgressResponse:
    """Poll flatten progress for a file currently being cached."""
    data_path = _resolve_data_path(path)
    from haute._json_flatten import flatten_progress

    progress = flatten_progress(data_path)
    if progress is None:
        return JsonCacheProgressResponse(active=False)
    return JsonCacheProgressResponse.model_validate({"active": True, **progress})


def _v2_status_response(
    data_path: str,
    v2_config: dict[str, Any],
    input_path: str,
) -> JsonCacheStatusResponse:
    """Compute the status response for a v2 schema mapping.

    Returns cached=False when the v2 cache isn't valid (missing, stale
    relative to the schema, or missing required parquets). Returns the
    aggregated cached=True payload otherwise.
    """
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import (
        is_per_port_cache_valid,
        read_per_port_cache_meta,
    )

    cache_dir = _json_cache_dir(data_path, "working")
    if not is_per_port_cache_valid(cache_dir, v2_config):
        return JsonCacheStatusResponse(cached=False, data_path=input_path)
    meta = read_per_port_cache_meta(cache_dir)
    if meta is None:
        return JsonCacheStatusResponse(cached=False, data_path=input_path)
    return _aggregate_v2_status_response(cache_dir, data_path, meta)


@router.post("/status", response_model=JsonCacheStatusResponse)
async def post_json_cache_status(body: JsonCacheBuildRequest) -> JsonCacheStatusResponse:
    """Check whether a JSON file has a valid cache the emitter would consume.

    Dispatch mirrors :func:`build_json_cache`: a v2 config on disk routes
    through :func:`_v2_status_response`; otherwise the v1 flat-cache path
    is consulted.
    """
    data_path = _resolve_data_path(body.path)
    config_path = _resolve_config_path(body.config_path)

    v2_config = _read_v2_config(config_path)
    if v2_config is not None:
        return _v2_status_response(data_path, v2_config, body.path)

    from haute._json_flatten import (
        JsonFlattenSchemaError,
        json_cache_path_if_valid,
    )
    from haute._polars_utils import read_parquet_metadata

    try:
        cache_path = json_cache_path_if_valid(
            data_path,
            schema=body.flatten_schema,
            config_path=config_path,
        )
    except JsonFlattenSchemaError as e:
        raise HTTPException(status_code=422, detail=f"Invalid flatten schema: {e}") from None
    if cache_path is None:
        return JsonCacheStatusResponse(cached=False, data_path=body.path)

    meta = read_parquet_metadata(cache_path)
    return JsonCacheStatusResponse.model_validate(
        {
            "cached": True,
            "path": str(cache_path),
            "data_path": data_path,
            "row_count": meta["row_count"],
            "column_count": meta["column_count"],
            "columns": meta["columns"],
            "size_bytes": meta["size_bytes"],
            "cached_at": meta["mtime"],
        }
    )


@router.get("/status", response_model=JsonCacheStatusResponse)
async def get_json_cache_status(
    path: str,
    config_path: str | None = None,
) -> JsonCacheStatusResponse:
    """Check whether a JSON file has a valid cache.

    Now accepts an optional ``config_path`` query parameter, mirroring
    the POST variant — when supplied and v2-shaped, dispatch to the
    v2-aware status. Without ``config_path`` (or when v1), falls back to
    the schema-agnostic v1 check, preserving the previous behaviour for
    callers that don't pass config.
    """
    data_path = _resolve_data_path(path)
    resolved_config_path = _resolve_config_path(config_path)

    v2_config = _read_v2_config(resolved_config_path)
    if v2_config is not None:
        return _v2_status_response(data_path, v2_config, path)

    from haute._json_flatten import json_cache_info

    info = json_cache_info(data_path)
    if info is None:
        return JsonCacheStatusResponse(cached=False, data_path=path)
    return JsonCacheStatusResponse.model_validate({"cached": True, **info})


@router.delete("", response_model=JsonCacheStatusResponse)
async def delete_json_cache(path: str) -> JsonCacheStatusResponse:
    """Delete the volatile (working/) cache layer for a JSON file.

    Dual-cache semantics: delete operates on the working/ layer only. The
    durable committed/ layer is untouched and remains the source of truth
    until a subsequent save mirrors a (possibly absent) working/ into it.
    """
    data_path = _resolve_data_path(path)
    from haute._json_flatten import clear_json_cache

    clear_json_cache(data_path)
    return JsonCacheStatusResponse(cached=False, data_path=path)
