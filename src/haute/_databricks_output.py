"""Write a Data Output to a Unity Catalog table through a SQL warehouse.

The result streams to a local Parquet file, which is staged and loaded:

1. ``CREATE VOLUME IF NOT EXISTS`` the staging volume in the target schema.
2. Upload the file into it through the Files API in parts of a fixed size, so
   the upload holds a bounded number of parts in memory however large the
   output is. (The SQL connector's ``PUT`` reads the whole file into memory.)
3. Load it into the table in one statement, so a load that fails leaves the
   table as it was: ``CREATE OR REPLACE TABLE`` for ``overwrite``; for
   ``append``, ``CREATE TABLE ... AS SELECT`` when the table does not exist
   yet and ``INSERT INTO ... BY NAME`` when it does.
4. Delete the staged file, whether or not the upload or the load succeeded.

One bulk load lets the warehouse map Parquet types to table types, rather
than inserting rows through the connector. Authentication is the Data
Input's (:func:`haute._databricks_io._resolve_connection`), for both the SQL
connection and the Files API client. Warehouse and storage errors reach the
user and the log only after credential redaction: a failed upload's error can
carry a signed storage URL.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from haute._credential_security import redact_sensitive_text
from haute._databricks_credentials import (
    DatabricksConfigError,
    DatabricksCredentials,
    databricks_workspace_client,
)
from haute._databricks_io import (
    _TABLE_NAME_RE,
    _quoted_table_name,
    _resolve_connection,
    _sql_auth_kwargs,
)
from haute._logging import get_logger
from haute._polars_utils import streaming_collect
from haute.errors import HauteError

if TYPE_CHECKING:
    from haute._chunked_writes import WriteRecipe
    from haute._execution_context import ExecutionContext

logger = get_logger(component="databricks_output")

DATABRICKS_WRITE_MODES: tuple[str, ...] = ("overwrite", "append")
DEFAULT_WRITE_MODE = "overwrite"
#: The volume, in the target table's schema, a write stages its file in.
STAGING_VOLUME = "haute_staging"
_MAX_ERROR_CHARS = 600
# The upload's memory bound is their product: the Files API buffers one part per
# thread. (A file under its 50 MiB multipart threshold goes up in one request.)
UPLOAD_PART_BYTES = 16 * 1024 * 1024
UPLOAD_PARALLELISM = 4


class DatabricksWriteError(HauteError):
    """A Databricks Data Output write the warehouse refused or could not finish."""


def _table_parts(table: str) -> tuple[str, str, str]:
    catalog, schema, name = (part.strip("`") for part in table.split("."))
    return catalog, schema, name


def _spark_compatible(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Cast the dtypes Databricks cannot read from Parquet into ones it can.

    Spark timestamps hold microseconds, so nanosecond datetimes are truncated
    to them; Databricks has no TIME type, and an all-null column has no type
    at all, so both are written as strings.
    """
    casts = []
    for name, dtype in lf.collect_schema().items():
        if dtype == pl.Null or dtype == pl.Time:
            casts.append(pl.col(name).cast(pl.String))
        elif isinstance(dtype, pl.Datetime) and dtype.time_unit == "ns":
            casts.append(pl.col(name).cast(pl.Datetime("us", dtype.time_zone)))
    return lf.with_columns(casts) if casts else lf


def _require_write_mode(write_mode: str) -> None:
    if write_mode not in DATABRICKS_WRITE_MODES:
        raise DatabricksConfigError(
            f"Databricks output 'write_mode' must be one of {list(DATABRICKS_WRITE_MODES)}."
        )


def load_statement(table: str, staged_path: str, write_mode: str, *, table_exists: bool) -> str:
    """The one statement that loads *staged_path* into *table* under *write_mode*.

    Each is a single Delta transaction, so a load that fails leaves the table as
    it was: an ``overwrite`` keeps the previous table, a first ``append`` creates
    none, and a later one adds no rows. *table_exists* decides an ``append``: it
    creates a table that does not exist from the file, and inserts into one that
    does. A table created between the check and the load fails the create, which
    never replaces it.
    """
    _require_write_mode(write_mode)
    target = _quoted_table_name(table)
    source = f"parquet.`{staged_path}`"
    if write_mode == "overwrite":
        return f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source}"
    if table_exists:
        return f"INSERT INTO {target} BY NAME SELECT * FROM {source}"
    return f"CREATE TABLE {target} AS SELECT * FROM {source}"


def _table_exists(tables: Any, table: str) -> bool:
    """Whether *table* exists, by the Tables API's exact-name check.

    An answer that is not a plain yes is read as no: the ``CREATE TABLE`` that
    follows refuses a table that does exist, so it fails loudly and replaces nothing.
    """
    return tables.exists(".".join(_table_parts(table))).table_exists is True


def _warehouse_message(exc: BaseException, credentials: DatabricksCredentials) -> str:
    """*exc*'s text on one line, credentials redacted before it is shortened."""
    secrets = (credentials.token or "", credentials.client_secret or "")
    text = " ".join(redact_sensitive_text(str(exc), known_secrets=secrets).split())
    return text if len(text) <= _MAX_ERROR_CHARS else f"{text[:_MAX_ERROR_CHARS]}..."


def _remove_staged(files: Any, staged_path: str, credentials: DatabricksCredentials) -> None:
    """Delete the staged file; a failure is logged, never raised over the write's outcome."""
    from databricks.sdk.errors import NotFound

    try:
        files.delete(staged_path)
    except NotFound:
        pass  # The upload failed before the file existed.
    except Exception as exc:  # noqa: BLE001 - cleanup must not mask the write
        logger.warning(
            "databricks_staging_cleanup_failed",
            path=staged_path,
            error=_warehouse_message(exc, credentials),
        )


def write_databricks_output(
    lf: pl.LazyFrame,
    config: Mapping[str, Any],
    *,
    recipe: WriteRecipe | None = None,
    execution_context: ExecutionContext | None = None,
    node_id: str | None = None,
) -> int:
    """Write *lf* to the configured table and return the rows written."""
    from haute._polars_io_registry import write_polars_output

    table = str(config.get("table") or "")
    if not _TABLE_NAME_RE.fullmatch(table):
        raise DatabricksConfigError(
            f"Invalid table name: {table!r}. "
            "Expected fully-qualified name like 'catalog.schema.table'."
        )
    write_mode = str(config.get("write_mode") or DEFAULT_WRITE_MODE)
    _require_write_mode(write_mode)
    catalog, schema, _name = _table_parts(table)
    staged_path = f"/Volumes/{catalog}/{schema}/{STAGING_VOLUME}/{uuid.uuid4().hex}.parquet"
    # A volume is named as a table is: catalog.schema.name.
    staging_volume = _quoted_table_name(f"{catalog}.{schema}.{STAGING_VOLUME}")
    credentials, http_path = _resolve_connection(
        str(config.get("http_path") or ""), node_label="Data Output"
    )
    try:
        from databricks import sql as dbsql

        workspace = databricks_workspace_client(credentials)
        files = workspace.files
    except ImportError as exc:
        raise DatabricksConfigError(
            "Writing to Databricks requires the databricks-sql-connector and databricks-sdk "
            "packages. Install the databricks extra: haute[databricks]."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="haute-databricks-") as staging_dir:
        local = Path(staging_dir) / "output.parquet"
        write_polars_output(
            _spark_compatible(lf),
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": str(local),
                "arguments": {},
            },
            resolved_path=local,
            # The recipe must describe the frame written, casts included.
            recipe=recipe.then(_spark_compatible) if recipe is not None else None,
            execution_context=execution_context,
            node_id=node_id,
        )
        rows = int(
            streaming_collect(
                pl.scan_parquet(local).select(pl.len()), execution_context=execution_context
            ).item()
        )
        if execution_context is not None:
            execution_context.checkpoint(label="before_databricks_load", node_id=node_id)
        try:
            with (
                dbsql.connect(
                    server_hostname=credentials.server_hostname,
                    http_path=http_path,
                    **_sql_auth_kwargs(credentials),
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    f"CREATE VOLUME IF NOT EXISTS {staging_volume} "
                    "COMMENT 'Staging for Haute Data Output writes'"
                )
                try:
                    files.upload_from(
                        staged_path,
                        str(local),
                        overwrite=True,
                        part_size=UPLOAD_PART_BYTES,
                        parallelism=UPLOAD_PARALLELISM,
                    )
                    table_exists = write_mode == "append" and _table_exists(workspace.tables, table)
                    cursor.execute(
                        load_statement(table, staged_path, write_mode, table_exists=table_exists)
                    )
                finally:
                    _remove_staged(files, staged_path, credentials)
        except DatabricksConfigError:
            raise
        except Exception as exc:
            raise DatabricksWriteError(
                f"Databricks could not write {table}: {_warehouse_message(exc, credentials)}"
            ) from exc

    logger.info("databricks_output_written", table=table, write_mode=write_mode, rows=rows)
    return rows
