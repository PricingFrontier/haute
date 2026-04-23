"""File browsing and schema inspection endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._json_safe import rows_to_json_safe
from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, validate_safe_path
from haute.schemas import (
    BrowseFilesResponse,
    FileItem,
    SchemaResponse,
)

logger = get_logger(component="server.files")

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files", response_model=BrowseFilesResponse)
async def browse_files(
    dir: str = ".",
    extensions: str = ".parquet,.csv,.json,.xml",
) -> BrowseFilesResponse:
    """Browse files on disk for the file picker UI."""
    base = Path.cwd()
    target = validate_safe_path(base, dir)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {dir}")

    ext_list = [e.strip() for e in extensions.split(",")]
    items: list[FileItem] = []

    for entry in sorted(target.iterdir()):
        rel = str(entry.relative_to(base))
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            items.append(FileItem(name=entry.name, path=rel, type="directory"))
        elif any(entry.name.endswith(ext) for ext in ext_list):
            items.append(
                FileItem(
                    name=entry.name,
                    path=rel,
                    type="file",
                    size=entry.stat().st_size,
                )
            )

    return BrowseFilesResponse(
        dir=str(target.relative_to(base)),
        items=items,
    )


def _read_schema_blocking(path: str, target: Path) -> SchemaResponse:
    """Synchronous schema + preview reader.

    Run from a thread pool (``run_in_threadpool``) so the event loop
    stays responsive while Polars materialises the preview and row count.
    """
    import polars as pl

    from haute.graph_utils import read_source
    from haute.schemas import ColumnInfo

    lf = read_source(str(target))
    schema = lf.collect_schema()
    columns = [ColumnInfo(name=c, dtype=str(d)) for c, d in schema.items()]
    preview_df = lf.head(5).collect()

    # For JSONL files, estimating row count avoids reading the entire file
    # into memory (pl.len() on scan_ndjson materialises every row).
    row_count: int | None
    row_count_estimated = False
    if path.endswith(".jsonl"):
        file_size = target.stat().st_size
        n_preview = len(preview_df)
        if n_preview > 0:
            avg_line_bytes = file_size / max(n_preview, 1)
            # Use serialized preview size as a better per-row estimate
            sample_bytes = sum(
                len(line) + 1  # +1 for newline
                for line in preview_df.write_ndjson().splitlines()
            )
            if sample_bytes > 0:
                avg_line_bytes = sample_bytes / n_preview
            row_count = max(1, int(file_size / avg_line_bytes))
        else:
            row_count = 0
        row_count_estimated = row_count is not None and row_count > 0
    else:
        row_count = lf.select(pl.len()).collect().item()

    return SchemaResponse(
        path=path,
        columns=columns,
        row_count=row_count,
        row_count_estimated=row_count_estimated,
        column_count=len(columns),
        preview=rows_to_json_safe(preview_df.to_dicts()),
    )


@router.get("/schema", response_model=SchemaResponse)
async def get_schema(path: str) -> SchemaResponse:
    """Read a data file and return its schema + preview.

    Blocking parquet/CSV/JSON reads are offloaded to ``run_in_threadpool``
    so concurrent requests on the single async event loop are not
    serialised behind disk I/O.
    """
    base = Path.cwd()
    target = validate_safe_path(base, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        return await run_in_threadpool(_read_schema_blocking, path, target)
    except HTTPException:
        raise
    except ValueError as exc:
        # Raw ValueError text may embed absolute paths, tracebacks, or
        # git output — never safe to surface.  Log full detail
        # server-side (``exc_info=True`` preserves the stack trace;
        # ``error_class`` / ``error_message`` are explicit keys so
        # downstream log searches can filter on them), respond with
        # the sanitized constant.
        logger.warning(
            "schema_value_error",
            path=path,
            error_class=type(exc).__name__,
            error_message=str(exc),
            exc_info=True,
        )
        raise HTTPException(status_code=400, detail=_INTERNAL_ERROR_DETAIL) from None
    except Exception as exc:  # noqa: BLE001
        # Fail loudly server-side: structured log with the full stack
        # trace via ``exc_info=True`` so ops can diagnose the real
        # error.  Respond with a sanitized 500 — OS errors, polars
        # decoder crashes, and platform paths must never leak through
        # ``str(exc)``.  The broad except is deliberate:
        # every exception class needs the same treatment here, and we
        # do NOT swallow silently — the structured log always fires
        # with explicit ``error_class`` / ``error_message`` keys.
        logger.error(
            "schema_read_failed",
            path=path,
            error_class=type(exc).__name__,
            error_message=str(exc),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None


def _validate_table_param(table: str) -> None:
    """Reject table names that don't match catalog.schema.table format."""
    from haute._databricks_io import _TABLE_NAME_RE

    if not _TABLE_NAME_RE.match(table):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid table name: {table!r}. "
            "Expected fully-qualified name like 'catalog.schema.table'.",
        )


def _read_databricks_schema_blocking(table: str, p: Path) -> SchemaResponse:
    """Synchronous Databricks cached-parquet schema + preview reader."""
    import polars as pl

    from haute._polars_utils import read_parquet_metadata
    from haute.schemas import ColumnInfo

    df = pl.scan_parquet(p).head(1000).collect()
    columns = [ColumnInfo(name=c, dtype=str(df[c].dtype)) for c in df.columns]
    preview_df = df.head(5)
    meta = read_parquet_metadata(p)
    row_count = meta["row_count"]

    return SchemaResponse(
        path=table,
        columns=columns,
        row_count=row_count,
        column_count=len(columns),
        preview=rows_to_json_safe(preview_df.to_dicts()),
    )


@router.get("/schema/databricks", response_model=SchemaResponse)
async def get_databricks_schema(table: str) -> SchemaResponse:
    """Return schema + preview from the local parquet cache of a Databricks table.

    Parquet metadata + preview materialisation are offloaded to
    ``run_in_threadpool`` so the async event loop is not blocked on disk I/O.
    """
    _validate_table_param(table)

    from haute._databricks_io import cached_path

    p = cached_path(table)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f'Table "{table}" has not been fetched yet. '
            f"Click Fetch Data on the data source node to download it.",
        )

    try:
        return await run_in_threadpool(_read_databricks_schema_blocking, table, p)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        # Fail loudly server-side with the full stack trace (filesystem
        # error, corrupt parquet, etc.) and return a sanitized 500 so
        # cache paths and parquet internals never surface in the HTTP
        # body.
        logger.error("databricks_schema_read_failed", table=table, exc_info=True)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from None
