"""Write a Data Output to a Unity Catalog table through a SQL warehouse.

The result streams to a local Parquet file, which one warehouse connection
then stages and loads:

1. ``CREATE VOLUME IF NOT EXISTS`` the staging volume in the target schema.
2. ``PUT`` the file into it (the SQL connector's staging operation).
3. Load it into the table in one statement: ``CREATE OR REPLACE TABLE`` for
   ``overwrite``, ``INSERT INTO ... BY NAME`` for ``append`` (creating the
   table first when it does not exist).
4. ``REMOVE`` the staged file, whether or not the load succeeded.

One bulk load lets the warehouse map Parquet types to table types, rather
than inserting rows through the connector. Authentication is the Data
Input's (:func:`haute._databricks_io._connection_settings`).
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from haute._databricks_credentials import DatabricksConfigError
from haute._databricks_io import _TABLE_NAME_RE, _connection_settings, _quoted_table_name
from haute._logging import get_logger
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


def load_statements(table: str, staged_path: str, write_mode: str) -> list[str]:
    """The SQL that loads *staged_path* into *table* under *write_mode*."""
    target = _quoted_table_name(table)
    source = f"parquet.`{staged_path}`"
    if write_mode == "overwrite":
        return [f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source}"]
    if write_mode == "append":
        return [
            f"CREATE TABLE IF NOT EXISTS {target} AS SELECT * FROM {source} LIMIT 0",
            f"INSERT INTO {target} BY NAME SELECT * FROM {source}",
        ]
    raise DatabricksConfigError(
        f"Databricks output 'write_mode' must be one of {list(DATABRICKS_WRITE_MODES)}."
    )


def _warehouse_message(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= _MAX_ERROR_CHARS else f"{text[:_MAX_ERROR_CHARS]}..."


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
    catalog, schema, _name = _table_parts(table)
    staged_path = f"/Volumes/{catalog}/{schema}/{STAGING_VOLUME}/{uuid.uuid4().hex}.parquet"
    statements = load_statements(table, staged_path, write_mode)
    host, auth_kwargs, http_path = _connection_settings(
        str(config.get("http_path") or ""), node_label="Data Output"
    )
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise DatabricksConfigError(
            "Writing to Databricks requires the databricks-sql-connector package. "
            "Install the databricks extra: haute[databricks]."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="haute-databricks-") as staging_dir:
        local = Path(staging_dir) / "output.parquet"
        if "'" in local.as_posix():
            raise DatabricksConfigError(
                "The temporary directory path contains a quote, which a Databricks PUT "
                "cannot name. Point TMPDIR at a path without one."
            )
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
            recipe=recipe,
            execution_context=execution_context,
            node_id=node_id,
        )
        rows = int(pl.scan_parquet(local).select(pl.len()).collect(engine="streaming").item())
        if execution_context is not None:
            execution_context.checkpoint(label="before_databricks_load", node_id=node_id)
        try:
            with (
                dbsql.connect(
                    server_hostname=host,
                    http_path=http_path,
                    staging_allowed_local_path=staging_dir,
                    **auth_kwargs,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.`{STAGING_VOLUME}` "
                    "COMMENT 'Staging for Haute Data Output writes'"
                )
                cursor.execute(f"PUT '{local.as_posix()}' INTO '{staged_path}' OVERWRITE")
                try:
                    for statement in statements:
                        cursor.execute(statement)
                finally:
                    try:
                        cursor.execute(f"REMOVE '{staged_path}'")
                    except Exception as exc:  # noqa: BLE001 - cleanup must not mask the load
                        logger.warning(
                            "databricks_staging_cleanup_failed",
                            path=staged_path,
                            error=_warehouse_message(exc),
                        )
        except DatabricksConfigError:
            raise
        except Exception as exc:
            raise DatabricksWriteError(
                f"Databricks could not write {table}: {_warehouse_message(exc)}"
            ) from exc

    logger.info("databricks_output_written", table=table, write_mode=write_mode, rows=rows)
    return rows
