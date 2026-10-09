"""The workbench routes (specs/workbench): its status, its tables as the form defines them
now, and the form itself, read and saved.

Every route reads ``haute.toml``, and all but the status the form, from the working
directory on every request, off the event loop, so an edit to either reaches the next
request. A bad table or form answers 409 with what to fix (``haute.routes._error_handlers``)
while the pipeline editor goes on working. A save quotes the revision the form was read at
and is refused, writing nothing, when the file has changed since.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._logging import get_logger
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_form import form_file_revision, read_form_document, write_form
from haute._workbench_tables import WorkbenchTables, workbench_tables
from haute.routes._save_pipeline import StaleDocumentRevisionError
from haute.routes.json_cache import api_input_schema_error_response
from haute.schemas import (
    WorkbenchFormResponse,
    WorkbenchFormSaveRequest,
    WorkbenchStatusResponse,
    WorkbenchTablesResponse,
)

router = APIRouter(prefix="/api/workbench", tags=["workbench"])
logger = get_logger(component="server.workbench")


def _config() -> WorkbenchConfig:
    # The project is the working directory, as it is for the rest of the server.
    return read_workbench_config(Path.cwd())


async def _enabled_config() -> WorkbenchConfig:
    """The workbench's config, or 404 while the workbench is not enabled."""
    config = await run_in_threadpool(_config)
    if not config.enabled:
        raise HTTPException(
            404, "The workbench is not enabled: set [workbench] enabled = true in haute.toml."
        )
    return config


def _tables(config: WorkbenchConfig) -> WorkbenchTables:
    return workbench_tables(read_form_document(config).spec)


def _save_form(config: WorkbenchConfig, body: WorkbenchFormSaveRequest) -> str:
    """Write the form unless the file changed since the view read it; return its revision."""
    current = form_file_revision(config)
    if current != body.base_revision:
        raise StaleDocumentRevisionError(
            expected_revision=current,
            provided_revision=body.base_revision,
            message="The workbench's form changed on disk after the workbench read it. "
            "Reload the workbench before saving.",
        )
    return write_form(config.form_path, body.form)


@router.get("", response_model=WorkbenchStatusResponse)
async def workbench_status() -> WorkbenchStatusResponse:
    """Whether the project's workbench is enabled, and where its form is."""
    config = await run_in_threadpool(_config)
    return WorkbenchStatusResponse(
        enabled=config.enabled, form=config.form if config.enabled else None
    )


@router.get("/tables", response_model=WorkbenchTablesResponse)
async def get_workbench_tables() -> WorkbenchTablesResponse | JSONResponse:
    """The workbench's tables, sample and response tables as its form defines them now."""
    config = await _enabled_config()
    tables = await run_in_threadpool(_tables, config)
    try:
        validate_v2_schema({"tables": tables.tables})
        validate_v2_schema({"tables": tables.response_tables})
    except ApiInputSchemaError as exc:
        return api_input_schema_error_response(exc)
    # Not checked against the tables here: a sample that doesn't fit fails the previews
    # that read it, and never stops the tables updating.
    return WorkbenchTablesResponse(
        tables=tables.tables, sample=tables.sample, response_tables=tables.response_tables
    )


@router.get("/form", response_model=WorkbenchFormResponse)
async def get_workbench_form() -> WorkbenchFormResponse:
    """The form as its file holds it now, with the file's revision."""
    config = await _enabled_config()
    document = await run_in_threadpool(read_form_document, config)
    return WorkbenchFormResponse(form=document.spec, revision=document.revision)


@router.put("/form", response_model=WorkbenchFormResponse)
async def put_workbench_form(body: WorkbenchFormSaveRequest) -> WorkbenchFormResponse:
    """Write the form, unless its file changed since the view read it."""
    config = await _enabled_config()
    try:
        revision = await run_in_threadpool(_save_form, config, body)
    except StaleDocumentRevisionError as exc:
        # The view's form is behind the disk; nothing was written. The detail leads with
        # the same stable code the pipeline save answers, so the client matches one.
        logger.warning(
            "workbench_form_stale_revision",
            expected_revision=exc.expected_revision,
            provided_revision=exc.provided_revision,
        )
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc}") from None
    return WorkbenchFormResponse(form=body.form, revision=revision)
