"""Bounded Databricks snapshot acquisition for canonical Data Input nodes.

Connection details live on the Data Input node (``http_path`` in config).
Secrets are resolved from the environment:
    DATABRICKS_HOST
    DATABRICKS_TOKEN
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute.errors import HauteError

if TYPE_CHECKING:
    import pyarrow as pa

    from haute._source_cache import SourceCacheBuildContext

logger = get_logger(component="databricks_io")

# Fully-qualified Databricks table names: catalog.schema.table (each part is
# alphanumeric + underscores/hyphens, optionally backtick-quoted).
_TABLE_NAME_RE = re.compile(r"^`?[\w-]+`?\.`?[\w-]+`?\.`?[\w-]+`?$")

# Dangerous SQL keywords that must never appear in user-supplied SELECT clauses.
_DANGEROUS_SQL_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|EXEC|EXECUTE|CREATE|GRANT|"
    r"REVOKE|UNION|LATERAL)\b",
    re.IGNORECASE,
)
_FROM_SQL_RE = re.compile(r"\bFROM\b", re.IGNORECASE)


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
    if _FROM_SQL_RE.search(stripped):
        raise ValueError("Query contains forbidden SQL keyword: 'FROM'")


class DatabricksConfigError(HauteError):
    """Raised when required Databricks data credentials are missing."""


class FetchIntegrityError(HauteError):
    """Raised when a Databricks fetch cannot prove the cached data is complete.

    Better no cache at all than a cache that silently misses rows or carries
    a fabricated schema — the fetch is always safe to re-run.
    """


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
        missing.append("http_path on the Data Input node (or DATABRICKS_HTTP_PATH env var)")

    if missing:
        raise DatabricksConfigError(
            "Missing Databricks data credentials:\n  "
            + "\n  ".join(missing)
            + "\nSet host/token in .env and http_path on the Data Input node."
        )

    # Strip protocol for the SQL connector (it wants bare hostname)
    host = host.rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    elif host.startswith("http://"):
        host = host[len("http://") :]

    if resolved_http_path is None:
        raise RuntimeError("resolved_http_path must not be None after credential resolution")
    return host, token, resolved_http_path


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _canonical_table(table: str) -> str:
    """Return the canonical spelling of a fully-qualified table reference.

    Databricks resolves unquoted identifiers case-insensitively, and backtick
    quoting is spelling rather than identity: ``MyCat.Sch.Tbl``,
    ``mycat.sch.tbl`` and the backtick-quoted form of either all name ONE
    table.  Strip backticks and casefold so every spelling derives the same
    cache identity — otherwise one table double-caches on a case-sensitive
    deploy filesystem and a clear-cache issued under another spelling misses
    the live file.
    """
    return table.replace("`", "").casefold()


_FETCH_BATCH_SIZE = 100_000
_FETCH_MAX_RETRIES = 3
_FETCH_INITIAL_BACKOFF = 1.0  # seconds


class DatabricksSnapshotBuilder:
    """Stream Databricks Arrow batches into the shared source-cache publisher."""

    build_class = "bounded"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._table = str(config.get("table") or "")
        self._http_path = str(config.get("http_path") or "")
        query = config.get("query")
        self._query = str(query) if query else None
        arguments = config.get("arguments") or {}
        self._batch_size = int(arguments.get("batch_size", _FETCH_BATCH_SIZE))
        if not _TABLE_NAME_RE.fullmatch(self._table):
            raise ValueError(
                f"Invalid table name: {self._table!r}. "
                "Expected fully-qualified name like 'catalog.schema.table'."
            )
        if not self._http_path:
            raise DatabricksConfigError("Databricks input requires a SQL warehouse http_path.")
        if self._query:
            _validate_select_clause(self._query)
        if self._batch_size <= 0:
            raise ValueError("Databricks batch_size must be a positive integer.")

    def build(self, context: SourceCacheBuildContext) -> Iterator[pa.Table]:
        return _iter_databricks_batches(
            table=self._table,
            http_path=self._http_path,
            query=self._query,
            batch_size=self._batch_size,
            context=context,
        )


def _iter_databricks_batches(
    *,
    table: str,
    http_path: str,
    query: str | None,
    batch_size: int,
    context: SourceCacheBuildContext,
) -> Iterator[pa.Table]:
    """Yield a complete Databricks result through bounded Arrow batches."""
    from databricks import sql as dbsql

    host, token, resolved_http_path = _get_credentials(http_path)
    select_clause = query.strip() if query else "SELECT *"
    sql_query = f"{select_clause} FROM {table}"  # noqa: S608
    row_count = 0
    with dbsql.connect(
        server_hostname=host,
        http_path=resolved_http_path,
        access_token=token,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql_query)
            fetch_was_retried = False
            while True:
                context.checkpoint()
                batch = None
                for attempt in range(_FETCH_MAX_RETRIES):
                    try:
                        batch = cursor.fetchmany_arrow(batch_size)
                        break
                    except (KeyboardInterrupt, SystemExit, TypeError, KeyError, AttributeError):
                        raise
                    except Exception:
                        if attempt == _FETCH_MAX_RETRIES - 1:
                            raise
                        fetch_was_retried = True
                        backoff = _FETCH_INITIAL_BACKOFF * (2**attempt)
                        logger.warning("fetch_retry", attempt=attempt, backoff=backoff)
                        time.sleep(backoff)
                if batch is None:
                    raise RuntimeError("fetchmany_arrow returned None after all retries")
                if fetch_was_retried:
                    _assert_no_rows_lost_after_retry(
                        table=table,
                        rows_received=row_count + batch.num_rows,
                        rows_consumed=cursor.rownumber,
                    )
                if batch.num_rows == 0:
                    if row_count == 0:
                        if batch.num_columns == 0:
                            raise FetchIntegrityError(
                                f'Databricks fetch of "{table}" returned zero rows and no '
                                "result schema; refusing to publish a schemaless snapshot."
                            )
                        yield batch
                    break
                row_count += batch.num_rows
                yield batch


def _assert_no_rows_lost_after_retry(
    *,
    table: str,
    rows_received: int,
    rows_consumed: object,
) -> None:
    """Fail loudly when a retried batch fetch lost rows.

    ``cursor.rownumber`` is the connector's DBAPI count of rows consumed
    from the result set (``ResultSet._next_row_index``).  A
    ``fetchmany_arrow`` call that fails mid-way can have consumed result
    chunks and advanced that position *before* raising; a retry then
    continues from the advanced position and the consumed rows are silently
    dropped.  Once any retry has happened, every batch boundary must
    therefore prove that the rows received locally still match the rows the
    cursor consumed.
    """
    if rows_consumed != rows_received:
        raise FetchIntegrityError(
            f'Databricks fetch of "{table}" lost rows during a retry: the cursor '
            f"consumed {rows_consumed!r} row(s) but only {rows_received} row(s) were "
            "received locally. The cache was not written; re-run the fetch."
        )
