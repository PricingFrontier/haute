"""The workbench routes (specs/workbench): its status, and its tables as the form defines
them now.

Both read ``haute.toml``, and the second the form, from the working directory on every
request, off the event loop, so an edit to either reaches the next request. A bad table or
form answers 409 with what to fix (``haute.routes._error_handlers``) while the pipeline
editor goes on working.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_form import read_form
from haute._workbench_tables import WorkbenchTables, workbench_tables
from haute.routes.json_cache import api_input_schema_error_response
from haute.schemas import WorkbenchStatusResponse, WorkbenchTablesResponse

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


def _config() -> WorkbenchConfig:
    # The project is the working directory, as it is for the rest of the server.
    return read_workbench_config(Path.cwd())


def _tables(config: WorkbenchConfig) -> WorkbenchTables:
    return workbench_tables(read_form(config))


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
    config = await run_in_threadpool(_config)
    if not config.enabled:
        raise HTTPException(
            404, "The workbench is not enabled: set [workbench] enabled = true in haute.toml."
        )
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
