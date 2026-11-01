"""Databricks endpoints: the sign-in's status and test, and read-only Unity Catalog browsing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException

from haute._credential_security import redact_sensitive_text
from haute._databricks_credentials import (
    DatabricksConfigError,
    DatabricksCredentials,
    DatabricksSignInError,
    credentials_rejected,
    databricks_workspace_client,
    resolve_databricks_credentials,
)
from haute._logging import get_logger
from haute.schemas import (
    CatalogItem,
    CatalogListResponse,
    DatabricksConnectionResponse,
    DatabricksTestConnectionResponse,
    SchemaItem,
    SchemaListResponse,
    TableItem,
    TableListResponse,
    WarehouseItem,
    WarehouseListResponse,
)

logger = get_logger(component="server.databricks")

router = APIRouter(prefix="/api/databricks", tags=["databricks"])

_SDK_MISSING = "databricks-sdk is not installed. Install with: pip install haute[databricks]"


def _get_databricks_client() -> Any:
    """Return a Databricks WorkspaceClient using credentials from .env.

    Building the client is the sign-in for a user form (the SDK runs the CLI or
    opens the browser), so a :class:`DatabricksSignInError` leaves here as it is,
    for the caller to answer as a refused credential.
    """
    try:
        from databricks.sdk import WorkspaceClient  # noqa: F401
    except ImportError:
        raise HTTPException(status_code=503, detail=_SDK_MISSING)

    try:
        credentials = resolve_databricks_credentials()
    except DatabricksConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return databricks_workspace_client(credentials)


def _is_sdk_error(exc: BaseException) -> bool:
    try:
        from databricks.sdk.errors import DatabricksError
    except ImportError:
        return False
    return isinstance(exc, DatabricksError)


@contextmanager
def _browsing(subject: str, *, within: str | None = None) -> Iterator[Any]:
    """The workspace client for one listing, whose SDK failure answers with its cause.

    *subject* names what is listed ("the catalogs"); *within* the object it is
    listed in ("catalog 'main'"), which a not-found failure means is missing. The
    SDK's listings are lazy, so the listing is consumed inside the block, and the
    client is built inside it too: a user sign-in that fails as the client is
    built must answer like any other refused credential, not as a server error.
    """
    try:
        yield _get_databricks_client()
    except HTTPException:
        raise
    except Exception as exc:
        # A refused service principal surfaces from the SDK's OAuth token
        # exchange as a plain ValueError, never as a DatabricksError.
        if _is_sdk_error(exc) or credentials_rejected(exc):
            raise _browsing_failure(exc, subject, within) from exc
        raise


def _refused_credentials(credentials: DatabricksCredentials, exc: BaseException) -> tuple[str, str]:
    """The problem and the remedy when the credentials themselves were refused.

    A user sign-in the SDK could not complete carries its own words. A token or
    service principal the workspace refused names its ``.env`` variables. A user
    sign-in whose token the workspace refused after the SDK produced it (revoked
    meanwhile) gets the same remedy as a failed sign-in.
    """
    if isinstance(exc, DatabricksSignInError):
        return exc.problem, exc.remedy
    if credentials.auth_mode == "pat":
        return "Databricks refused DATABRICKS_TOKEN", "set a current personal access token in .env"
    if credentials.auth_mode == "service_principal":
        return (
            "Databricks refused DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET",
            "check the service principal's credentials in .env",
        )
    return credentials.sign_in_failure()


def _log_failure(
    event: str, exc: BaseException, credentials: DatabricksCredentials, **fields: Any
) -> None:
    """Log *exc* with the credentials scrubbed; a sign-in failure's SDK text is its cause."""
    secrets = (credentials.token or "", credentials.client_secret or "")
    text = str(exc) if exc.__cause__ is None else f"{exc} ({exc.__cause__})"
    logger.warning(
        event,
        error_type=type(exc).__name__,
        error=redact_sensitive_text(text, known_secrets=secrets),
        **fields,
    )


def _browsing_failure(exc: Exception, subject: str, within: str | None) -> HTTPException:
    """The answer to *exc*, raised listing *subject*, with the SDK's text only in the log.

    The SDK's text is the workspace's own and can name hosts; it is logged with the
    credentials scrubbed, and the answer is written here.
    """
    from databricks.sdk.errors import NotFound, PermissionDenied

    credentials = resolve_databricks_credentials()
    if credentials_rejected(exc):
        problem, remedy = _refused_credentials(credentials, exc)
        failure = HTTPException(
            status_code=401, detail=f"{problem}, so {subject} could not be listed: {remedy}."
        )
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
    _log_failure(
        "databricks_browsing_failed",
        exc,
        credentials,
        subject=subject,
        status_code=failure.status_code,
    )
    return failure


@router.get("/connection", response_model=DatabricksConnectionResponse)
def databricks_connection() -> DatabricksConnectionResponse:
    """How this server signs in to Databricks, read from its environment alone.

    No network call and no SDK: a browser sign-in must never open on a page load,
    only on Test, Browse, or a fetch. The host is not answered; the component's
    responses carry no host value.
    """
    try:
        credentials = resolve_databricks_credentials()
    except DatabricksConfigError as exc:
        return DatabricksConnectionResponse(configured=False, detail=str(exc))
    return DatabricksConnectionResponse(
        configured=True,
        auth_mode=credentials.auth_mode,
        profile=credentials.profile or "",
        sdk_auth_type=credentials.sdk_auth_type or "",
    )


def _test_failure(credentials: DatabricksCredentials, exc: BaseException) -> tuple[str, str]:
    from databricks.sdk.errors import PermissionDenied

    if credentials_rejected(exc):
        return _refused_credentials(credentials, exc)
    if isinstance(exc, PermissionDenied):
        return (
            "The Databricks credentials may not read who they sign in as",
            "ask a workspace admin for access",
        )
    return "Databricks did not answer the connection test", "check the server logs for details"


@router.post("/test-connection", response_model=DatabricksTestConnectionResponse)
def databricks_test_connection() -> DatabricksTestConnectionResponse:
    """Sign in and ask the workspace who haute is; expected failures never 5xx.

    For a browser sign-in this is the call that opens the browser.
    """
    try:
        import databricks.sdk  # noqa: F401
    except ImportError:
        return DatabricksTestConnectionResponse(ok=False, detail=_SDK_MISSING)
    try:
        credentials = resolve_databricks_credentials()
    except DatabricksConfigError as exc:
        return DatabricksTestConnectionResponse(ok=False, detail=str(exc))
    try:
        client = databricks_workspace_client(credentials)
        me = client.current_user.me()
    except Exception as exc:
        if not (_is_sdk_error(exc) or credentials_rejected(exc)):
            raise
        problem, remedy = _test_failure(credentials, exc)
        _log_failure("databricks_connection_test_failed", exc, credentials)
        return DatabricksTestConnectionResponse(ok=False, detail=f"{problem}: {remedy}.")
    auth_type = getattr(getattr(client, "config", None), "auth_type", None)
    return DatabricksTestConnectionResponse(
        ok=True,
        identity=me.user_name or me.display_name or "",
        sdk_auth_type=auth_type if isinstance(auth_type, str) else "",
    )


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
