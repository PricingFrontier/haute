"""Read-only Databricks Unity Catalog browsing endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException

from haute._credential_security import redact_sensitive_text
from haute._databricks_credentials import (
    DatabricksConfigError,
    credentials_rejected,
    databricks_workspace_client,
    resolve_databricks_credentials,
)
from haute._logging import get_logger
from haute.schemas import (
    CatalogItem,
    CatalogListResponse,
    SchemaItem,
    SchemaListResponse,
    TableItem,
    TableListResponse,
    WarehouseItem,
    WarehouseListResponse,
)

logger = get_logger(component="server.databricks")

router = APIRouter(prefix="/api/databricks", tags=["databricks"])


def _get_databricks_client() -> Any:
    """Return a Databricks WorkspaceClient using credentials from .env."""
    try:
        from databricks.sdk import WorkspaceClient  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="databricks-sdk is not installed. Install with: pip install haute[databricks]",
        )

    try:
        credentials = resolve_databricks_credentials()
    except DatabricksConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return databricks_workspace_client(credentials)


@contextmanager
def _browsing(subject: str, *, within: str | None = None) -> Iterator[Any]:
    """The workspace client for one listing, whose SDK failure answers with its cause.

    *subject* names what is listed ("the catalogs"); *within* the object it is
    listed in ("catalog 'main'"), which a not-found failure means is missing. The
    SDK's listings are lazy, so the listing is consumed inside the block.
    """
    client = _get_databricks_client()
    from databricks.sdk.errors import DatabricksError

    try:
        yield client
    except Exception as exc:
        # A refused service principal surfaces from the SDK's OAuth token
        # exchange as a plain ValueError, never as a DatabricksError.
        if isinstance(exc, DatabricksError) or credentials_rejected(exc):
            raise _browsing_failure(exc, subject, within) from exc
        raise


def _browsing_failure(exc: Exception, subject: str, within: str | None) -> HTTPException:
    """The answer to *exc*, raised listing *subject*, with the SDK's text only in the log.

    The SDK's text is the workspace's own and can name hosts; it is logged with the
    credentials scrubbed, and the answer is written here.
    """
    from databricks.sdk.errors import NotFound, PermissionDenied

    credentials = resolve_databricks_credentials()
    if credentials_rejected(exc):
        detail = (
            f"Databricks refused DATABRICKS_TOKEN, so {subject} could not be listed: "
            "set a current personal access token in .env."
            if credentials.auth_mode == "pat"
            else f"Databricks refused DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET, so "
            f"{subject} could not be listed: check the service principal's credentials in .env."
        )
        failure = HTTPException(status_code=401, detail=detail)
    elif isinstance(exc, PermissionDenied):
        failure = HTTPException(
            status_code=403,
            detail=(
                f"The Databricks credentials may not list {subject}: ask a workspace admin "
                "for access."
            ),
        )
    elif isinstance(exc, NotFound) and within is not None:
        failure = HTTPException(
            status_code=404,
            detail=f"{within[:1].upper()}{within[1:]} was not found in the Databricks workspace.",
        )
    else:
        failure = HTTPException(
            status_code=502,
            detail=f"Databricks could not list {subject}. Check the server logs for details.",
        )
    secrets = (credentials.token or "", credentials.client_secret or "")
    logger.warning(
        "databricks_browsing_failed",
        subject=subject,
        status_code=failure.status_code,
        error_type=type(exc).__name__,
        error=redact_sensitive_text(str(exc), known_secrets=secrets),
    )
    return failure


@router.get("/warehouses", response_model=WarehouseListResponse)
def list_databricks_warehouses() -> WarehouseListResponse:
    """List available Databricks SQL Warehouses."""
    with _browsing("the SQL warehouses") as w:
        warehouses = [
            WarehouseItem(
                id=wh.id,
                name=wh.name,
                http_path=f"/sql/1.0/warehouses/{wh.id}",
                state=wh.state.value if wh.state else "UNKNOWN",
                size=wh.cluster_size or "",
                serverless=bool(wh.enable_serverless_compute),
            )
            for wh in w.warehouses.list()
            if wh.id and wh.name
        ]
    return WarehouseListResponse(warehouses=warehouses)


@router.get("/catalogs", response_model=CatalogListResponse)
def list_databricks_catalogs() -> CatalogListResponse:
    """List Unity Catalog catalogs."""
    with _browsing("the catalogs") as w:
        catalogs = [
            CatalogItem(name=c.name, comment=c.comment or "") for c in w.catalogs.list() if c.name
        ]
    return CatalogListResponse(catalogs=catalogs)


@router.get("/schemas", response_model=SchemaListResponse)
def list_databricks_schemas(catalog: str) -> SchemaListResponse:
    """List schemas within a Unity Catalog catalog."""
    within = f"catalog '{catalog}'"
    with _browsing(f"the schemas of {within}", within=within) as w:
        schemas = [
            SchemaItem(name=s.name, comment=s.comment or "")
            for s in w.schemas.list(catalog_name=catalog)
            if s.name
        ]
    return SchemaListResponse(schemas=schemas)


@router.get("/tables", response_model=TableListResponse)
def list_databricks_tables(catalog: str, schema: str) -> TableListResponse:
    """List tables within a Unity Catalog schema."""
    within = f"schema '{catalog}.{schema}'"
    with _browsing(f"the tables of {within}", within=within) as w:
        tables = [
            TableItem(
                name=t.name,
                full_name=t.full_name or f"{catalog}.{schema}.{t.name}",
                table_type=t.table_type.value if t.table_type else "",
                comment=t.comment or "",
            )
            for t in w.tables.list(catalog_name=catalog, schema_name=schema)
            if t.name
        ]
    return TableListResponse(tables=tables)
