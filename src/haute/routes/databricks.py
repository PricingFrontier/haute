"""Read-only Databricks Unity Catalog browsing endpoints."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from haute._logging import get_logger
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
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
        from databricks.sdk import WorkspaceClient
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="databricks-sdk is not installed. Install with: pip install haute[databricks]",
        )

    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")
    client_id = os.getenv("DATABRICKS_CLIENT_ID", "")
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "")

    if host and token:
        return WorkspaceClient(host=host, token=token)
    if host and client_id and client_secret:
        # Service-principal OAuth M2M — the credential shape injected into a
        # Databricks App container.
        return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)

    raise HTTPException(
        status_code=503,
        detail=(
            "DATABRICKS_HOST plus either DATABRICKS_TOKEN or "
            "DATABRICKS_CLIENT_ID/DATABRICKS_CLIENT_SECRET must be set "
            "(.env locally; injected automatically in a Databricks App)"
        ),
    )


@router.get("/warehouses", response_model=WarehouseListResponse)
def list_databricks_warehouses() -> WarehouseListResponse:
    """List available Databricks SQL Warehouses."""
    try:
        w = _get_databricks_client()
        warehouses = [
            WarehouseItem(
                id=wh.id,
                name=wh.name,
                http_path=f"/sql/1.0/warehouses/{wh.id}",
                state=wh.state.value if wh.state else "UNKNOWN",
                size=wh.cluster_size or "",
            )
            for wh in w.warehouses.list()
            if wh.id and wh.name
        ]
        return WarehouseListResponse(warehouses=warehouses)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_warehouses_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.get("/catalogs", response_model=CatalogListResponse)
def list_databricks_catalogs() -> CatalogListResponse:
    """List Unity Catalog catalogs."""
    try:
        w = _get_databricks_client()
        catalogs = [
            CatalogItem(name=c.name, comment=c.comment or "") for c in w.catalogs.list() if c.name
        ]
        return CatalogListResponse(catalogs=catalogs)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_catalogs_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.get("/schemas", response_model=SchemaListResponse)
def list_databricks_schemas(catalog: str) -> SchemaListResponse:
    """List schemas within a Unity Catalog catalog."""
    try:
        w = _get_databricks_client()
        schemas = [
            SchemaItem(name=s.name, comment=s.comment or "")
            for s in w.schemas.list(catalog_name=catalog)
            if s.name
        ]
        return SchemaListResponse(schemas=schemas)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_schemas_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


@router.get("/tables", response_model=TableListResponse)
def list_databricks_tables(catalog: str, schema: str) -> TableListResponse:
    """List tables within a Unity Catalog schema."""
    try:
        w = _get_databricks_client()
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_tables_failed", error=str(e))
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
