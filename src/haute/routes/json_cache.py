"""Schema inference for structured (JSON, JSONL, XML) API Input files.

A structured API Input's tables are shared input snapshots, built by automatic
preparation or the input-cache routes (see ``haute._json_shred._snapshots``);
this router only sniffs a v2 schema mapping from the data.

Errors raised by :func:`haute._api_input_schema.validate_v2_schema`,
:func:`parse_table_path`, and :func:`parse_column_path` arrive as
:class:`haute._api_input_schema.ApiInputSchemaError` and turn into a
structured HTTP 422 with body
``{"detail": "...", "type": "ApiInputSchemaError"}`` — the frontend
discriminates on ``type`` rather than string-matching ``detail``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._api_input_schema import ApiInputSchemaError
from haute._logging import get_logger
from haute._path_resolution import RuntimePathError, resolve_runtime_file_path
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, pipeline_dir
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.schemas import JsonCacheInferRequest, JsonCacheInferResponse

logger = get_logger(component="server.json_cache")

router = APIRouter(prefix="/api/json-cache", tags=["json-cache"])


def _api_input_schema_error_response(err: ApiInputSchemaError) -> JSONResponse:
    """422 response with the structured discriminator.

    Frontend reads ``body.type === "ApiInputSchemaError"`` to branch
    rather than string-matching ``body.detail``.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(err),
            "type": "ApiInputSchemaError",
        },
    )


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
    except RuntimePathError as exc:
        raise runtime_path_http_exception(exc) from None


@router.post("/infer", response_model=JsonCacheInferResponse)
async def infer_json_cache_schema(body: JsonCacheInferRequest) -> Any:
    """Sniff a v2 schema mapping from JSON, JSONL, or XML records.

    Drives the ApiInputEditor's *Infer Tables* button. Returns a
    v2-shaped ``tables: [...]`` array the editor stitches into the
    apiInput's config. Only the root table is ``emit=True`` by default;
    nested tables are off so the user opts in. A JSON scalar array becomes
    its own child table (one ``value`` column).
    """
    data_path = _resolve_data_path(body.path)
    try:
        from haute._json_shred._inference import infer_v2_schema_from_data

        result = await run_in_threadpool(
            infer_v2_schema_from_data,
            data_path,
            sample_size=body.sample_size,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Data file not found") from None
    except orjson.JSONDecodeError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid JSON in data file: {e}",
        ) from None
    except ApiInputSchemaError as e:
        # e.g. a nested array (array of arrays) that can't be a flat table —
        # surface the structured 422 naming the field rather than an opaque 500.
        return _api_input_schema_error_response(e)
    except Exception as e:
        logger.error("json_cache_infer_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    return JsonCacheInferResponse(tables=result.get("tables", []))
