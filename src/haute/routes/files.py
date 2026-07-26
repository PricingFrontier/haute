"""File browsing and schema inspection endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from polars.exceptions import PolarsError

from haute._io import UnsupportedSourceFormatError
from haute._json_safe import rows_to_json_safe
from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL, validate_safe_path
from haute.schemas import BrowseFilesResponse, FileItem, SchemaResponse

if TYPE_CHECKING:
    import polars as pl

logger = get_logger(component="server.files")

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files", response_model=BrowseFilesResponse)
async def browse_files(
    dir: str = ".",
    extensions: str | None = None,
) -> BrowseFilesResponse:
    """Browse files on disk for the file picker UI."""
    return await run_in_threadpool(_browse_files_request, dir, extensions)


def _browse_files_request(
    requested_dir: str,
    raw_extensions: str | None,
) -> BrowseFilesResponse:
    """Resolve and enumerate one browse request off the async event loop."""
    # Resolve the base: ``validate_safe_path`` returns a *resolved* target and
    # ``iterdir()`` yields resolved children, so an unresolved base would break
    # the ``relative_to`` calls below wherever cwd differs from its canonical
    # form — e.g. a Windows 8.3 short path (``C:\Users\RUNNER~1\...``) whose
    # entries come back long-form. (POSIX ``getcwd`` already resolves symlinks,
    # which is why this only bit Windows.)
    base = Path.cwd().resolve()
    target = validate_safe_path(base, requested_dir)
    ext_list = (
        _installed_input_extensions()
        if raw_extensions is None
        else tuple(
            extension.strip().casefold()
            for extension in raw_extensions.split(",")
            if extension.strip()
        )
    )
    return _browse_files_blocking(base, target, requested_dir, ext_list)


def _installed_input_extensions() -> tuple[str, ...]:
    from haute._polars_io_registry import FORMATS, missing_engines

    return tuple(
        dict.fromkeys(
            extension.casefold()
            for fmt in FORMATS
            if fmt.source_kind == "path"
            and (fmt.reader is not None or fmt.scanner is not None)
            and not missing_engines(fmt.read_engines)
            for extension in fmt.extensions
        )
    )


def _browse_files_blocking(
    base: Path,
    target: Path,
    requested_dir: str,
    extensions: tuple[str, ...],
) -> BrowseFilesResponse:
    """Enumerate one directory without blocking the async event loop."""
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {requested_dir}")

    items: list[FileItem] = []
    for entry in sorted(target.iterdir(), key=lambda candidate: candidate.name.casefold()):
        try:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            rel = str(entry.relative_to(base))
            if entry.is_dir():
                items.append(FileItem(name=entry.name, path=rel, type="directory"))
            elif entry.is_file() and any(
                entry.name.casefold().endswith(extension) for extension in extensions
            ):
                items.append(
                    FileItem(
                        name=entry.name,
                        path=rel,
                        type="file",
                        size=entry.stat().st_size,
                    )
                )
        except OSError as exc:
            logger.warning(
                "file_browser_entry_skipped",
                entry=entry.name,
                error_class=type(exc).__name__,
                error_message=str(exc),
            )
            continue

    return BrowseFilesResponse(
        dir=str(target.relative_to(base)),
        items=items,
    )


def _collect_file_preview(lf: pl.LazyFrame) -> pl.DataFrame:
    """Collect a small schema-preview frame through the profiled helper."""
    from haute._polars_utils import streaming_collect

    return streaming_collect(lf)


def _read_schema_blocking(path: str, target: Path) -> SchemaResponse:
    """Synchronous schema + preview reader.

    Run from a thread pool (``run_in_threadpool``) so the event loop
    stays responsive while Polars materialises the preview and row count.
    """
    import polars as pl

    from haute import graph_utils
    from haute.schemas import ColumnInfo

    lf = graph_utils.read_source(str(target))

    if target.suffix.lower() == ".parquet":
        from haute._polars_utils import read_parquet_metadata

        schema = lf.collect_schema()
        columns = [ColumnInfo(name=c, dtype=str(d)) for c, d in schema.items()]
        preview_df = _collect_file_preview(lf.head(5))
        meta = read_parquet_metadata(target)

        return SchemaResponse(
            path=path,
            columns=columns,
            row_count=meta["row_count"],
            row_count_estimated=False,
            column_count=len(columns),
            preview=rows_to_json_safe(preview_df.to_dicts()),
        )

    schema = lf.collect_schema()
    columns = [ColumnInfo(name=c, dtype=str(d)) for c, d in schema.items()]
    preview_df = _collect_file_preview(lf.head(5))

    # For JSONL files, estimating row count avoids reading the entire file
    # into memory (pl.len() on scan_ndjson materialises every row).
    row_count: int | None
    row_count_estimated = False
    if path.lower().endswith((".jsonl", ".ndjson")):
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
        row_count = _collect_file_preview(lf.select(pl.len())).item()

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
    # Resolve the base for the same reason as ``browse_files`` — keep cwd in its
    # canonical form so path handling is consistent on Windows short paths.
    base = Path.cwd().resolve()
    target = validate_safe_path(base, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        return await run_in_threadpool(_read_schema_blocking, path, target)
    except HTTPException:
        raise
    except UnsupportedSourceFormatError as exc:
        logger.info(
            "schema_unsupported_source_format",
            path=path,
            suffix=exc.suffix,
        )
        observed = exc.suffix or "(no extension)"
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported source format {observed}. Supported formats: "
                + ", ".join(exc.supported_suffixes)
                + "."
            ),
        ) from None
    except PolarsError as exc:
        logger.warning(
            "schema_decoder_error",
            path=path,
            error_class=type(exc).__name__,
            error_message=str(exc),
            exc_info=True,
        )
        lower_name = target.name.casefold()
        suffix = next(
            (
                candidate
                for candidate in UnsupportedSourceFormatError.supported_suffixes
                if lower_name.endswith(candidate)
            ),
            target.suffix.casefold() or "selected",
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not decode the {suffix} file. Check that it is valid and "
                "matches its file extension."
            ),
        ) from None
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
