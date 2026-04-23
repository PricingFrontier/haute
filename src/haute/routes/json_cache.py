"""JSON cache endpoints — explicit parquet caching for large JSONL files."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

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
        raise HTTPException(status_code=403, detail=str(exc)) from None


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


@router.post("/build", response_model=JsonCacheBuildResponse)
async def build_json_cache(body: JsonCacheBuildRequest) -> JsonCacheBuildResponse:
    """Flatten a JSON/JSONL file and cache it as parquet."""
    data_path = _resolve_data_path(body.path)
    config_path = _resolve_config_path(body.config_path)
    try:
        from haute._json_flatten import JsonCacheCancelledError
        from haute._json_flatten import build_json_cache as _build

        result = await run_blocking_with_response_timeout(
            _build,
            data_path=data_path,
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
    except HTTPException:
        raise
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


@router.get("/status", response_model=JsonCacheStatusResponse)
async def get_json_cache_status(path: str) -> JsonCacheStatusResponse:
    """Check whether a JSON file has been cached as parquet."""
    data_path = _resolve_data_path(path)
    from haute._json_flatten import json_cache_info

    info = json_cache_info(data_path)
    if info is None:
        return JsonCacheStatusResponse(cached=False, data_path=path)
    return JsonCacheStatusResponse.model_validate({"cached": True, **info})


@router.delete("", response_model=JsonCacheStatusResponse)
async def delete_json_cache(path: str) -> JsonCacheStatusResponse:
    """Delete the local parquet cache for a JSON file."""
    data_path = _resolve_data_path(path)
    from haute._json_flatten import clear_json_cache

    clear_json_cache(data_path)
    return JsonCacheStatusResponse(cached=False, data_path=path)
