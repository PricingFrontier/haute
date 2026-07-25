"""JSON cache endpoints — explicit parquet caching for JSON files.

The route's only shape is **v2 per-port shred** (see ``haute._json_shred``):
one parquet per emit-true ``tables[]`` entry, columns at that table's
JSON iteration depth.

Dispatch precedence (build, status):

1. ``request.volatile_schema is not None`` — use the editor's in-memory
   v2 (handover working principle 4: volatile vs persistent at the
   schema plane mirrors PR13's data plane).
2. Else read ``config_path`` from disk and use that v2 config.
3. Else return 422 — no schema source.

Errors raised by :func:`haute._api_input_schema.validate_v2_schema`,
:func:`parse_table_path`, and :func:`parse_column_path` arrive as
:class:`haute._api_input_schema.ApiInputSchemaError` and turn into a
structured HTTP 422 with body
``{"detail": "...", "type": "ApiInputSchemaError"}`` — the frontend
discriminates on ``type`` rather than string-matching ``detail``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import orjson
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._api_input_schema import ApiInputSchemaError, is_v2_shape
from haute._env import float_env
from haute._logging import get_logger
from haute._path_resolution import RuntimePathError, resolve_runtime_file_path
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, pipeline_dir
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.routes._timeouts import run_blocking_with_response_timeout
from haute.schemas import (
    JsonCacheBuildRequest,
    JsonCacheBuildResponse,
    JsonCacheCancelResponse,
    JsonCacheInferRequest,
    JsonCacheInferResponse,
    JsonCacheProgressResponse,
    JsonCacheStatusResponse,
)

logger = get_logger(component="server.json_cache")

router = APIRouter(prefix="/api/json-cache", tags=["json-cache"])


# ── Timeout (seconds) — resolved per request so env overrides set
# after import take effect ───────────────────────────────────────
def _build_timeout() -> float:
    return float_env("HAUTE_BUILD_TIMEOUT", 1800.0)


_build_progress: dict[str, dict[str, Any]] = {}
_build_progress_lock = threading.Lock()


def _progress_key(data_path: str | Path) -> str:  # pragma: no mutate
    return str(Path(data_path).resolve())


def _start_build_progress(data_path: str) -> None:
    key = _progress_key(data_path)
    now = time.monotonic()
    with _build_progress_lock:
        current = _build_progress.get(key)
        if current is None:
            _build_progress[key] = {
                "started_at": now,
                "active_count": 1,
                "phase": "building",
            }
            return
        current["active_count"] = int(current["active_count"]) + 1


def _finish_build_progress(data_path: str) -> None:
    key = _progress_key(data_path)
    with _build_progress_lock:
        current = _build_progress.get(key)
        if current is None:
            return
        remaining = int(current["active_count"]) - 1
        if remaining <= 0:
            _build_progress.pop(key, None)
            return
        current["active_count"] = remaining


def _get_build_progress(data_path: str) -> JsonCacheProgressResponse:
    key = _progress_key(data_path)
    with _build_progress_lock:
        current = dict(_build_progress.get(key) or {})
    if not current:
        return JsonCacheProgressResponse(active=False)
    elapsed = max(0.0, time.monotonic() - float(current["started_at"]))
    # `rows` is intentionally omitted: no producer ever writes it, so the
    # response default (0) is the honest value. See JsonCacheProgressResponse.
    return JsonCacheProgressResponse(
        active=True,
        elapsed=round(elapsed, 1),
        phase=str(current.get("phase", "building")),
    )


def _api_input_schema_error_response(err: ApiInputSchemaError) -> JSONResponse:
    """422 response with the structured discriminator.

    Frontend reads ``body.type === "ApiInputSchemaError"`` to branch
    rather than string-matching ``body.detail``. The shape is verified
    by T8 in ``tests/test_v1_removal_contract.py``.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(err),
            "type": "ApiInputSchemaError",
        },
    )


def _no_schema_source_response() -> JSONResponse:
    """422 when neither volatile_schema nor a v2 disk config was supplied.

    Same body shape as :func:`_api_input_schema_error_response` so the
    frontend doesn't need a second discriminator for the "schema
    missing entirely" case.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": (
                "No v2 schema source. Either pass `volatile_schema` in the "
                "request body, or provide `config_path` pointing at an on-disk "
                "v2 schema-mapping file with `tables[]`."
            ),
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


def _resolve_config_path(path: str | None) -> str | None:  # pragma: no mutate
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
    except RuntimePathError as exc:
        raise runtime_path_http_exception(exc) from None


def _read_v2_config(config_path: str | None) -> dict[str, Any] | None:  # pragma: no mutate
    """Read *config_path* and return it iff it carries a v2 ``tables[]`` array.

    Per D9 — corrupt-mix tolerance — a config that ALSO has stray
    pre-v2 keys (``flattenSchema``, ``column_renames``, ``selected_columns``)
    is still treated as v2 if ``tables`` is present.

    Bundle 2.a — those stray legacy keys are now **stripped** from the
    returned dict (promoted from "silently ignored" to "silently
    stripped"). The strip mirrors the one applied by
    ``_normalise_loaded_config`` for the parser load path; both funnels
    that materialise a disk-resident apiInput config into an in-memory
    dict must agree, otherwise the cache-build path and the executor
    would see different shapes. Contract pinning test:
    tests/test_strict_v2_contract.py::TestReadV2ConfigStripsLegacyKeys.

    Returns ``None`` when the file is **absent**, or when it is **valid
    JSON that simply isn't a v2 schema** (a legacy ``flattenSchema`` config
    or an empty ``{}`` — the migration path: the user opens the editor and
    clicks *Infer Tables*).

    Raises :class:`ApiInputSchemaError` when the file is **present but
    unreadable or not valid JSON** (corruption from external tooling or an
    interrupted write). This is deliberately distinct from the absent case:
    collapsing corruption into ``None`` would surface the misleading "no
    schema source" message and hide a real write-bug behind a migration
    prompt — the precise "incorrect and hard to notice" fallback the project
    forbids. The corrupt-data-file path already fails loud the same way.
    """
    if not config_path:
        return None
    p = Path(config_path)
    if not p.exists():
        return None
    try:
        raw_bytes = p.read_bytes()
    except OSError as exc:
        raise ApiInputSchemaError(
            f"config file at {config_path!r} could not be read: {exc}",
        ) from exc
    # Parse via the shared duplicate-rejecting hook so this cache-build
    # read funnel agrees with the parser load funnel
    # (`_config_io._load_json_object`): a duplicate key is corruption, not
    # a silently-kept last value. `json.loads` accepts bytes directly.
    from haute._config_io import reject_duplicate_keys_hook

    try:
        raw = json.loads(raw_bytes, object_pairs_hook=reject_duplicate_keys_hook)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApiInputSchemaError(
            f"config file at {config_path!r} is not valid JSON — it may have "
            "been corrupted by external tooling or an interrupted write",
        ) from exc
    if not isinstance(raw, dict) or not is_v2_shape(raw):
        # Valid JSON, but not a v2 schema (legacy/empty) → migration path.
        return None
    # Bundle 2.a — strip legacy apiInput-only keys. Imported lazily to
    # keep this route module's import surface minimal.
    from haute._config_io import _API_INPUT_LEGACY_KEYS_TO_STRIP

    return {k: v for k, v in raw.items() if k not in _API_INPUT_LEGACY_KEYS_TO_STRIP}


def _select_v2_config(body: JsonCacheBuildRequest) -> dict[str, Any] | None:  # pragma: no mutate
    """Apply the volatile-then-disk dispatch.

    Returns the v2 config dict to act on, or ``None`` if no schema
    source was supplied. ``volatile_schema is not None`` is the explicit
    check — an empty dict (``{}``) counts as "user provided this" and
    falls through to validate_v2_schema for an explicit error rather
    than silently falling back to disk.
    """
    if body.volatile_schema is not None:
        # `body.volatile_schema` is typed `Any` at the Pydantic boundary
        # (see schemas.py — intentional, so malformed shapes flow through
        # to `validate_v2_schema`'s structured 422 rather than Pydantic's
        # default 422). Cast here narrows for mypy without runtime change.
        return cast(dict[str, Any], body.volatile_schema)
    config_path = _resolve_config_path(body.config_path)
    return _read_v2_config(config_path)


def _aggregate_v2_tables(
    cache_dir: Path,
    tables: list[dict[str, Any]],
) -> tuple[int, int, dict[str, str], int, float]:
    """Collapse a v2 per-port ``tables[]`` list into shared aggregates.

    Returns ``(row_count, column_count, columns, size_bytes, cached_at)``.
    Both the build and status responses derive their per-port fields from
    this single core so the two can't drift; they differ only in how they
    source ``tables`` and read the ``skipped`` payload (strict vs tolerant),
    which stays with each caller.
    """
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
    return row_count, column_count, columns, size_bytes, cached_at


def _aggregate_v2_build_response(
    summary: dict[str, Any],
    cache_dir: Path,
    data_path: str,
    elapsed_seconds: float,
) -> JsonCacheBuildResponse:
    """Collapse a v2 per-port summary into the flat build-response shape.

    ``summary["skipped"]`` is part of the build contract (W2 item 2.7 —
    every shape-mismatched input the shred dropped is counted), so it is
    read strictly: a build summary without it is a programming error, not
    a tolerable absence.
    """
    tables = summary.get("tables", []) or []
    row_count, column_count, columns, size_bytes, cached_at = _aggregate_v2_tables(
        cache_dir, tables
    )
    skipped = summary["skipped"]
    return JsonCacheBuildResponse(
        path=str(cache_dir),
        data_path=data_path,
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        size_bytes=size_bytes,
        cached_at=cached_at,
        cache_seconds=round(elapsed_seconds, 3),
        skipped_records=int(skipped.get("records", 0)),
        skipped_rows={k: int(v) for k, v in (skipped.get("rows_by_table") or {}).items()},
    )


def _aggregate_v2_status_response(
    cache_dir: Path,
    data_path: str,
    meta: dict[str, Any],
) -> JsonCacheStatusResponse:
    """Same aggregation as the build response, for status queries.

    ``meta`` is read back from disk, so the ``skipped`` payload uses
    tolerant ``.get`` access (consistent with the rest of this function's
    handling of on-disk metadata) — pre-W2 metas can't reach here anyway
    because they fail the data-file-signature validity check.
    """
    tables = meta.get("tables", []) or []
    row_count, column_count, columns, size_bytes, cached_at = _aggregate_v2_tables(
        cache_dir, tables
    )
    skipped = meta.get("skipped") or {}
    return JsonCacheStatusResponse(
        cached=True,
        path=str(cache_dir),
        data_path=data_path,
        row_count=row_count,
        column_count=column_count,
        columns=columns,
        size_bytes=size_bytes,
        cached_at=cached_at,
        skipped_records=int(skipped.get("records", 0)),
        skipped_rows={k: int(v) for k, v in (skipped.get("rows_by_table") or {}).items()},
    )


@router.post("/build", response_model=JsonCacheBuildResponse)
async def build_json_cache(body: JsonCacheBuildRequest) -> Any:
    """Per-port shred of a JSON/JSONL file into one parquet per emit-true table.

    Schema source: volatile_schema, else config_path's on-disk v2.

    Error precedence:
      1. Path validation — 400/403 (`_resolve_data_path`).
      2. No schema source — 422 ApiInputSchemaError.
      3. Schema validation failure — 422 ApiInputSchemaError.
      4. File not found — 404 (data file doesn't exist on disk).
      5. Internal — 500 with `_INTERNAL_ERROR_DETAIL`.

    Note: schema-source check fires BEFORE file-existence so the
    structured 422 surfaces for the common "user forgot to populate
    tables" case. Path-traversal probes are still rejected — they hit
    the schema-source check too (no schema → 422), which is a 4xx
    rejection just as 404 would be.
    """
    data_path = _resolve_data_path(body.path)

    try:
        v2_config = _select_v2_config(body)
    except ApiInputSchemaError as e:
        return _api_input_schema_error_response(e)
    if v2_config is None:
        return _no_schema_source_response()

    if not Path(data_path).exists():
        raise HTTPException(status_code=404, detail="Data file not found")

    from haute._json_flatten import _json_cache_dir, _mark_working_consulted
    from haute._json_shred import build_per_port_cache

    cache_dir = _json_cache_dir(data_path, "working")
    t0 = time.monotonic()
    _start_build_progress(data_path)

    def _build_with_progress() -> dict[str, Any]:
        try:
            return build_per_port_cache(
                data_path=data_path,
                v2_config=v2_config,
                cache_dir=cache_dir,
            )
        finally:
            _finish_build_progress(data_path)

    try:
        summary = await run_blocking_with_response_timeout(
            _build_with_progress,
            timeout=_build_timeout(),
            operation="json_cache_build_v2",
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"JSON cache build timed out ({_build_timeout() / 60:.0f} min limit)",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Data file not found") from None
    except orjson.JSONDecodeError as e:
        # Data file unparseable — distinguish from schema problems.
        raise HTTPException(
            status_code=422,
            detail=f"Invalid JSON in data file: {e}",
        ) from None
    except ApiInputSchemaError as e:
        return _api_input_schema_error_response(e)
    except Exception as e:
        logger.error("json_cache_build_v2_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    # C2 fix (W2 item 2.1): a SUCCESSFUL production build makes this
    # process authoritative for the working/ layer, which is what arms the
    # save-time `mirror_cache_to_committed` promotion. Without this call
    # the mirror short-circuits forever, `committed/` never exists, and the
    # documented deploy / fresh-server fallback can never fire. A failed
    # build deliberately does NOT mark — save must not promote a stale
    # previous-session working/ on the strength of a failed click.
    _mark_working_consulted(data_path)
    elapsed = time.monotonic() - t0
    return _aggregate_v2_build_response(summary, cache_dir, data_path, elapsed)


@router.post("/cancel", response_model=JsonCacheCancelResponse)
async def cancel_json_cache_build(body: JsonCacheBuildRequest) -> JsonCacheCancelResponse:
    """Cancel an in-progress JSON cache build.

    No-op until the v2 per-port build path grows a cancellation
    mechanism. Returns ``cancelled=False`` so a UI that polls for
    cancellation acknowledges the request without claiming success.

    Path validation still fires (`_resolve_data_path`) so a malicious
    caller can't probe filesystem with a path-traversal payload via
    this stub endpoint.
    """
    _resolve_data_path(body.path)
    return JsonCacheCancelResponse(cancelled=False, data_path=body.path)


@router.get("/progress", response_model=JsonCacheProgressResponse)
async def get_json_cache_progress(path: str) -> JsonCacheProgressResponse:
    """Poll progress for an in-flight cache build.

    Reports whether this server process currently has a v2 per-port build
    running for the resolved data path. Path validation still fires
    (`_resolve_data_path`) so this endpoint doesn't open a path-traversal
    probe surface.
    """
    data_path = _resolve_data_path(path)
    return _get_build_progress(data_path)


def _v2_status_response(
    data_path: str,
    v2_config: dict[str, Any],
    input_path: str,
) -> JsonCacheStatusResponse:
    """Compute the status response for a v2 schema mapping.

    Runs :func:`validate_v2_schema` first — exactly as
    ``build_per_port_cache`` does — so the status/validity path enforces
    the SAME B1/B2/non-dict invariants as build. Without it the status
    path would silently accept a schema (duplicate labels, an illegal
    column type) that the build loudly rejects, and report ``cached=False``
    where it should surface a structured error. The raised
    :class:`ApiInputSchemaError` is turned into a 422 on POST /status and
    into a truthful ``cached=False`` on the read-only GET poll.
    """
    from haute._api_input_schema import validate_v2_schema
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import (
        is_per_port_cache_valid,
        read_per_port_cache_meta,
    )

    validate_v2_schema(v2_config)
    cache_dir = _json_cache_dir(data_path, "working")
    if not is_per_port_cache_valid(cache_dir, v2_config, data_path=data_path):
        return JsonCacheStatusResponse(cached=False, data_path=input_path)
    meta = read_per_port_cache_meta(cache_dir)
    if meta is None:
        return JsonCacheStatusResponse(cached=False, data_path=input_path)
    return _aggregate_v2_status_response(cache_dir, data_path, meta)


@router.post("/status", response_model=JsonCacheStatusResponse)
async def post_json_cache_status(body: JsonCacheBuildRequest) -> Any:
    """Status query for the v2 per-port cache.

    Dispatch mirrors :func:`build_json_cache` — volatile first, then
    disk. No v2 schema source → 422.
    """
    data_path = _resolve_data_path(body.path)
    try:
        v2_config = _select_v2_config(body)
    except ApiInputSchemaError as e:
        return _api_input_schema_error_response(e)
    if v2_config is None:
        return _no_schema_source_response()
    try:
        return await run_in_threadpool(_v2_status_response, data_path, v2_config, body.path)
    except ApiInputSchemaError as e:
        return _api_input_schema_error_response(e)


@router.get("/status", response_model=JsonCacheStatusResponse)
async def get_json_cache_status(
    path: str,
    config_path: str | None = None,
) -> JsonCacheStatusResponse:
    """GET variant — disk-only (no volatile body on a GET).

    Returns ``cached=False`` when there's no v2 config on disk.
    """
    data_path = _resolve_data_path(path)
    resolved_config_path = _resolve_config_path(config_path)

    try:
        v2_config = _read_v2_config(resolved_config_path)
    except ApiInputSchemaError:
        # GET status is a read-only poll — a corrupt config means there's no
        # valid schema, so "not cached" is the truthful answer here. The
        # precise corruption error surfaces on the build/POST-status paths.
        return JsonCacheStatusResponse(cached=False, data_path=path)
    if v2_config is None:
        return JsonCacheStatusResponse(cached=False, data_path=path)
    try:
        return await run_in_threadpool(_v2_status_response, data_path, v2_config, path)
    except ApiInputSchemaError:
        # GET status is a read-only poll — an invalid v2 schema means no
        # valid cache can exist, so "not cached" is the truthful answer
        # here. The precise validation error surfaces on the build/POST-
        # status paths (mirrors the corrupt-config handling above).
        return JsonCacheStatusResponse(cached=False, data_path=path)


@router.post("/infer", response_model=JsonCacheInferResponse)
async def infer_json_cache_schema(body: JsonCacheInferRequest) -> Any:
    """Sniff a v2 schema mapping from the records of a JSON/JSONL file.

    Drives the ApiInputEditor's *Infer Tables* button. Returns a
    v2-shaped ``tables: [...]`` array the editor stitches into the
    apiInput's config. Only the root table is ``emit=True`` by default;
    nested tables are off so the user opts in. A JSON scalar array becomes
    its own child table (one ``value`` column).
    """
    data_path = _resolve_data_path(body.path)
    try:
        from haute._json_shred import infer_v2_schema_from_data

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


@router.delete("", response_model=JsonCacheStatusResponse)
async def delete_json_cache(path: str) -> JsonCacheStatusResponse:
    """Delete the volatile (working/) cache layer for a JSON file.

    Dual-cache semantics: delete operates on the working/ layer only.
    The durable committed/ layer is untouched and remains the source of
    truth until a subsequent save mirrors a (possibly absent)
    working/ into it.
    """
    data_path = _resolve_data_path(path)
    from haute._json_flatten import clear_json_cache

    clear_json_cache(data_path)
    return JsonCacheStatusResponse(cached=False, data_path=path)
