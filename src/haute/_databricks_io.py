"""Bounded Databricks snapshot acquisition for canonical Data Input nodes.

Connection details live on the Data Input node (``http_path`` in config).
Secrets are resolved from the environment:
    DATABRICKS_HOST
    DATABRICKS_TOKEN                                  (PAT; takes precedence)
    DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET   (service-principal OAuth)
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from haute._databricks_credentials import (
    DatabricksConfigError,
    resolve_databricks_credentials,
)
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


class FetchIntegrityError(HauteError):
    """Raised when a Databricks fetch cannot prove the cached data is complete.

    Better no cache at all than a cache that silently misses rows or carries
    a fabricated schema — the fetch is always safe to re-run.
    """


def _service_principal_credentials(
    host: str, client_id: str, client_secret: str
) -> Callable[[], Any]:
    """Zero-arg OAuth M2M credentials provider for the SQL connector.

    The databricks-sdk import is deferred to call time so haute installs
    without the ``databricks`` extra still import this module; only an
    actual service-principal connection needs the SDK.
    """

    def provider() -> Any:
        try:
            from databricks.sdk.core import Config, oauth_service_principal
        except ImportError as exc:
            raise DatabricksConfigError(
                "Databricks service-principal auth requires the databricks-sdk "
                "package. Install the databricks extra: haute[databricks]."
            ) from exc

        return oauth_service_principal(
            Config(host=f"https://{host}", client_id=client_id, client_secret=client_secret)
        )

    return provider


def _connection_settings(http_path: str | None = None) -> tuple[str, dict[str, Any], str]:
    """Resolve Databricks connection settings from the environment.

    Auth precedence: an explicit ``DATABRICKS_TOKEN`` (PAT) wins;
    otherwise a ``DATABRICKS_CLIENT_ID``/``DATABRICKS_CLIENT_SECRET``
    service-principal pair (injected automatically inside a Databricks
    App container) authenticates via OAuth M2M.

    Args:
        http_path: SQL Warehouse HTTP path from the node config.

    Returns ``(host, auth_kwargs, http_path)`` where ``auth_kwargs`` are
    passed to ``databricks.sql.connect``. The failure message names every
    consulted source without echoing any value.
    """
    resolved_http_path = (http_path or "").strip()
    credentials = resolve_databricks_credentials(
        additional_missing=("http_path on the Data Input node",) if not resolved_http_path else ()
    )

    if credentials.auth_mode == "pat":
        assert credentials.token is not None
        auth_kwargs: dict[str, Any] = {"access_token": credentials.token}
    else:
        assert credentials.client_id is not None
        assert credentials.client_secret is not None
        auth_kwargs = {
            "credentials_provider": _service_principal_credentials(
                credentials.server_hostname,
                credentials.client_id,
                credentials.client_secret,
            )
        }
    return credentials.server_hostname, auth_kwargs, resolved_http_path


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

    host, auth_kwargs, resolved_http_path = _connection_settings(http_path)
    select_clause = query.strip() if query else "SELECT *"
    sql_query = f"{select_clause} FROM {table}"  # noqa: S608
    row_count = 0
    context.checkpoint()
    with dbsql.connect(
        server_hostname=host,
        http_path=resolved_http_path,
        **auth_kwargs,
    ) as connection:
        with connection.cursor() as cursor:
            context.checkpoint()
            cursor.execute(sql_query)
            fetch_was_retried = False
            rows_consumed_previously: object = None
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
                    rows_consumed = cursor.rownumber
                    _assert_no_rows_lost_after_retry(
                        table=table,
                        rows_received=row_count + batch.num_rows,
                        rows_consumed=rows_consumed,
                        rows_consumed_previously=rows_consumed_previously,
                    )
                    rows_consumed_previously = rows_consumed
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
    rows_consumed_previously: object,
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

    ``rows_consumed_previously`` is the position read at the previous
    post-retry boundary (``None`` at the first).  The equality check trusts
    that ``rownumber`` still means "rows consumed"; a counter that goes
    backwards between two boundaries has changed meaning, and is reported
    as that rather than letting a coincidental match pass or misreporting it
    as lost rows.  A missing or ``None`` counter still fails closed through
    the equality check.
    """
    if (
        rows_consumed_previously is not None
        and isinstance(rows_consumed, int)
        and isinstance(rows_consumed_previously, int)
        and rows_consumed < rows_consumed_previously
    ):
        raise FetchIntegrityError(
            f'Databricks fetch of "{table}" cannot verify a retry: the cursor row '
            f"counter went backwards from {rows_consumed_previously} to {rows_consumed}, "
            "so cursor.rownumber no longer counts consumed rows and the received rows "
            "cannot be proved complete. The cache was not written; re-run the fetch."
        )
    if rows_consumed != rows_received:
        raise FetchIntegrityError(
            f'Databricks fetch of "{table}" lost rows during a retry: the cursor '
            f"consumed {rows_consumed!r} row(s) but only {rows_received} row(s) were "
            "received locally. The cache was not written; re-run the fetch."
        )
