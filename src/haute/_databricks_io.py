"""Databricks table I/O with local parquet caching.

Data is fetched from Databricks once via :func:`fetch_and_cache`, which
writes a local ``.parquet`` file under ``.haute_cache/``.  All subsequent
pipeline runs read from that cached file with :func:`read_cached_table`,
giving full Polars ``scan_parquet`` speed with predicate pushdown.

Connection details live on the data source node (``http_path`` in config).
Secrets are resolved from the environment:
    DATABRICKS_HOST
    DATABRICKS_TOKEN
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import TypedDict

import polars as pl

from haute._types import HauteError

# Fully-qualified Databricks table names: catalog.schema.table (each part is
# alphanumeric + underscores/hyphens, optionally backtick-quoted).
_TABLE_NAME_RE = re.compile(r"^`?[\w-]+`?\.`?[\w-]+`?\.`?[\w-]+`?$")

# Dangerous SQL keywords that must never appear in user-supplied SELECT clauses.
_DANGEROUS_SQL_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|EXEC|EXECUTE|CREATE|GRANT|REVOKE|UNION|LATERAL)\b",
    re.IGNORECASE,
)

CACHE_DIR = ".haute_cache"

# Thread-safe progress tracking for active fetches, keyed by table name.
_fetch_progress: dict[str, dict[str, object]] = {}
_fetch_lock = threading.Lock()


# -- Test helpers for fetch progress state -----------------------------------


def _set_fetch_progress(table: str, data: dict[str, object]) -> None:
    """Set fetch progress for *table* (test helper)."""
    with _fetch_lock:
        _fetch_progress[table] = data


def _clear_fetch_progress() -> None:
    """Clear all fetch progress entries (test helper)."""
    with _fetch_lock:
        _fetch_progress.clear()


def _validate_select_clause(query: str) -> None:
    """Validate that *query* is a safe SELECT clause.

    Since the ``query`` field comes from a GUI config (not arbitrary SQL),
    we enforce that it:
    1. Starts with ``SELECT`` (case-insensitive).
    2. Contains no semicolons (statement terminators).
    3. Contains no dangerous SQL keywords (DROP, DELETE, etc.).
    """
    stripped = query.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError(f"Query must start with SELECT, got: {stripped[:40]!r}")
    if ";" in stripped:
        raise ValueError("Query must not contain semicolons.")
    # Block SQL comments that could neutralize the appended FROM clause.
    if "--" in stripped:
        raise ValueError("Query must not contain SQL line comments (--).")
    if "/*" in stripped:
        raise ValueError("Query must not contain SQL block comments (/*).")
    match = _DANGEROUS_SQL_RE.search(stripped)
    if match:
        raise ValueError(f"Query contains forbidden SQL keyword: {match.group()!r}")


class DatabricksConfigError(HauteError):
    """Raised when required Databricks data credentials are missing."""


class CacheNotFoundError(HauteError):
    """Raised when a pipeline tries to read a table that hasn't been fetched yet."""


def _get_credentials(http_path: str | None = None) -> tuple[str, str, str]:
    """Resolve Databricks data credentials.

    Args:
        http_path: SQL Warehouse HTTP path from the node config.
            Falls back to ``DATABRICKS_HTTP_PATH`` env var.

    Returns (host, token, http_path).
    """
    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    resolved_http_path = http_path or os.getenv("DATABRICKS_HTTP_PATH", "")

    missing: list[str] = []
    if not host:
        missing.append("DATABRICKS_HOST")
    if not token:
        missing.append("DATABRICKS_TOKEN")
    if not resolved_http_path:
        missing.append("http_path on the data source node (or DATABRICKS_HTTP_PATH env var)")

    if missing:
        raise DatabricksConfigError(
            "Missing Databricks data credentials:\n  "
            + "\n  ".join(missing)
            + "\nSet host/token in .env and http_path on the data source node."
        )

    # Strip protocol for the SQL connector (it wants bare hostname)
    host = host.rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    elif host.startswith("http://"):
        host = host[len("http://") :]

    assert resolved_http_path is not None
    return host, token, resolved_http_path


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_path_for(table: str, project_root: Path | None = None) -> Path:
    """Return the parquet cache path for a fully-qualified table name.

    All path separators and dots are replaced with underscores, and the
    result is verified to stay within the cache directory to prevent
    path-traversal attacks.
    """
    root = project_root or Path.cwd()
    safe_name = table.replace(".", "_").replace("/", "_").replace("\\", "_")
    cache_dir = (root / CACHE_DIR).resolve()
    result = (cache_dir / f"{safe_name}.parquet").resolve()
    if not result.is_relative_to(cache_dir):
        raise ValueError(f"Invalid table name for caching: {table!r}")
    return result


def cached_path(
    table: str,
    project_root: Path | None = None,
) -> Path | None:
    """Return the cache file path if it exists, else ``None``."""
    p = _cache_path_for(table, project_root)
    return p if p.exists() else None


def clear_cache(table: str, project_root: Path | None = None) -> bool:
    """Delete the cached parquet file for a table. Returns True if deleted."""
    p = cached_path(table, project_root)
    if p is not None:
        p.unlink()
        return True
    return False


class CacheInfoDict(TypedDict):
    path: str
    table: str
    row_count: int
    column_count: int
    columns: dict[str, str]
    size_bytes: int
    fetched_at: float


def cache_info(
    table: str,
    project_root: Path | None = None,
) -> CacheInfoDict | None:
    """Return metadata about a cached table, or ``None`` if not cached."""
    from haute._polars_utils import read_parquet_metadata

    p = cached_path(table, project_root)
    if p is None:
        return None
    meta = read_parquet_metadata(p)
    return {
        "path": str(p),
        "table": table,
        "row_count": meta["row_count"],
        "column_count": meta["column_count"],
        "columns": meta["columns"],
        "size_bytes": meta["size_bytes"],
        "fetched_at": meta["mtime"],
    }


# ---------------------------------------------------------------------------
# Fetch from Databricks → local parquet
# ---------------------------------------------------------------------------


_FETCH_BATCH_SIZE = 100_000
_FETCH_MAX_RETRIES = 3
_FETCH_INITIAL_BACKOFF = 1.0  # seconds


class FetchResultDict(CacheInfoDict):
    fetch_seconds: float


def fetch_progress(table: str) -> dict[str, object] | None:
    """Return current fetch progress for *table*, or ``None`` if not active."""
    with _fetch_lock:
        return _fetch_progress.get(table)


def fetch_and_cache(
    table: str,
    http_path: str | None = None,
    query: str | None = None,
    project_root: Path | None = None,
    batch_size: int = _FETCH_BATCH_SIZE,
) -> FetchResultDict:
    """Fetch a table from Databricks and cache it as a local parquet file.

    Data is streamed in Arrow batches of *batch_size* rows and written
    incrementally to parquet via :class:`pyarrow.parquet.ParquetWriter`
    with zstd compression, so memory usage stays bounded regardless of
    table size.

    Writes to a temporary file first and atomically renames on success,
    so a failed fetch never leaves a corrupt cache file behind.

    Returns metadata dict with row_count, column_count, path, etc.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    from databricks import sql as dbsql

    host, token, resolved_http_path = _get_credentials(http_path)

    # Validate table name to prevent SQL injection via GUI-editable config
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"Invalid table name: {table!r}. "
            "Expected fully-qualified name like 'catalog.schema.table'."
        )

    if query:
        _validate_select_clause(query)
        select_clause = query.strip().rstrip(";")
    else:
        select_clause = "SELECT *"
    sql_query = f"{select_clause} FROM {table}"  # noqa: S608

    out_path = _cache_path_for(table, project_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".parquet.tmp")

    row_count = 0
    writer: pq.ParquetWriter | None = None

    t0 = time.monotonic()
    with _fetch_lock:
        _fetch_progress[table] = {"rows": 0, "batches": 0, "elapsed": 0.0}
    try:
        with dbsql.connect(
            server_hostname=host,
            http_path=resolved_http_path,
            access_token=token,
        ) as conn:
            cursor_description = None
            with conn.cursor() as cursor:
                cursor.execute(sql_query)
                batch_count = 0
                while True:
                    # Retry with exponential backoff on transient errors
                    batch = None
                    for attempt in range(_FETCH_MAX_RETRIES):
                        try:
                            batch = cursor.fetchmany_arrow(batch_size)
                            break
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except (TypeError, KeyError, AttributeError):
                            raise
                        except Exception:
                            if attempt == _FETCH_MAX_RETRIES - 1:
                                raise
                            backoff = _FETCH_INITIAL_BACKOFF * (2**attempt)
                            time.sleep(backoff)
                    assert batch is not None
                    if batch.num_rows == 0:
                        break
                    if writer is None:
                        writer = pq.ParquetWriter(
                            str(tmp_path),
                            batch.schema,
                            compression="zstd",
                        )
                    writer.write_table(batch)
                    row_count += batch.num_rows
                    batch_count += 1
                    with _fetch_lock:
                        _fetch_progress[table] = {
                            "rows": row_count,
                            "batches": batch_count,
                            "elapsed": round(time.monotonic() - t0, 1),
                        }
                cursor_description = cursor.description
        if writer is not None:
            writer.close()
            writer = None
        else:
            # Zero rows returned -- write an empty parquet preserving schema
            if cursor_description:
                schema = pa.schema([(desc[0], pa.string()) for desc in cursor_description])
                empty_table = pa.table(
                    {f.name: pa.array([], type=f.type) for f in schema},
                    schema=schema,
                )
            else:
                empty_table = pa.table({})
            pq.write_table(empty_table, str(tmp_path))
        tmp_path.replace(out_path)
    except BaseException:
        if writer is not None:
            writer.close()
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        with _fetch_lock:
            _fetch_progress.pop(table, None)
    elapsed = time.monotonic() - t0

    # Read back lightweight schema info from the written file
    from haute._polars_utils import read_parquet_metadata

    meta = read_parquet_metadata(out_path)
    return {
        "path": str(out_path),
        "table": table,
        "row_count": row_count,
        "column_count": meta["column_count"],
        "columns": meta["columns"],
        "size_bytes": meta["size_bytes"],
        "fetched_at": meta["mtime"],
        "fetch_seconds": round(elapsed, 2),
    }


# ---------------------------------------------------------------------------
# Read from cache (used by executor at pipeline runtime)
# ---------------------------------------------------------------------------


def read_cached_table(table: str, project_root: Path | None = None) -> pl.LazyFrame:
    """Read a Databricks table from the local parquet cache.

    Raises :class:`CacheNotFoundError` if the table hasn't been fetched yet.
    """
    p = cached_path(table, project_root)
    if p is None:
        raise CacheNotFoundError(
            f'Table "{table}" has not been fetched yet. '
            f"Click Fetch Data on the data source node to download it."
        )
    return pl.scan_parquet(p)
