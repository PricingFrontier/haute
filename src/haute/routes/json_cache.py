"""JSON cache endpoints — explicit parquet caching for large JSONL files."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, validate_safe_path
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


@router.post("/build", response_model=JsonCacheBuildResponse)
async def build_json_cache(body: JsonCacheBuildRequest) -> JsonCacheBuildResponse:
    """Flatten a JSON/JSONL file and cache it as parquet."""
    validate_safe_path(Path.cwd(), body.path)
    try:
        import asyncio

        from haute._json_flatten import JsonCacheCancelledError
        from haute._json_flatten import build_json_cache as _build

        if body.config_path:
            validate_safe_path(Path.cwd(), body.config_path)

        result = await asyncio.wait_for(
            asyncio.to_thread(
                _build,
                data_path=body.path,
                config_path=body.config_path,
            ),
            timeout=_BUILD_TIMEOUT,
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
    validate_safe_path(Path.cwd(), body.path)
    from haute._json_flatten import cancel_json_cache

    cancelled = cancel_json_cache(body.path)
    return JsonCacheCancelResponse(cancelled=cancelled, data_path=body.path)


@router.get("/progress", response_model=JsonCacheProgressResponse)
async def get_json_cache_progress(path: str) -> JsonCacheProgressResponse:
    """Poll flatten progress for a file currently being cached."""
    validate_safe_path(Path.cwd(), path)
    from haute._json_flatten import flatten_progress

    progress = flatten_progress(path)
    if progress is None:
        return JsonCacheProgressResponse(active=False)
    return JsonCacheProgressResponse.model_validate({"active": True, **progress})


@router.get("/status", response_model=JsonCacheStatusResponse)
async def get_json_cache_status(path: str) -> JsonCacheStatusResponse:
    """Check whether a JSON file has been cached as parquet."""
    validate_safe_path(Path.cwd(), path)
    from haute._json_flatten import json_cache_info

    info = json_cache_info(path)
    if info is None:
        return JsonCacheStatusResponse(cached=False, data_path=path)
    return JsonCacheStatusResponse.model_validate({"cached": True, **info})


@router.delete("", response_model=JsonCacheStatusResponse)
async def delete_json_cache(path: str) -> JsonCacheStatusResponse:
    """Delete the local parquet cache for a JSON file."""
    validate_safe_path(Path.cwd(), path)
    from haute._json_flatten import clear_json_cache

    clear_json_cache(path)
    return JsonCacheStatusResponse(cached=False, data_path=path)
