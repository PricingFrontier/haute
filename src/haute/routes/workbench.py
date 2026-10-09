"""The workbench routes (specs/workbench): its status, its tables as the saved form defines
them now or as an unsaved form the view holds would, and the form itself, read and saved.

Every route reads ``haute.toml``, and those that serve the saved form read it too, from the
working directory on every request, off the event loop, so an edit to either reaches the
next request. A bad table or form answers 409 with what to fix
(``haute.routes._error_handlers``) while the pipeline editor goes on working. A save quotes
the revision the form was read at and is refused, writing nothing, when the file has
changed since; a save that lands is captured on the clone's save ledger as the pipeline's
saves are, and answers what the capture gave.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._logging import get_logger
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_form import FormSpec, form_file_revision, read_form_document, write_form
from haute._workbench_tables import workbench_tables
from haute.routes._save_pipeline import StaleDocumentRevisionError, capture_save_in_ledger
from haute.routes.json_cache import api_input_schema_error_response
from haute.schemas import (
    WorkbenchFormResponse,
    WorkbenchFormSaveRequest,
    WorkbenchFormSaveResponse,
    WorkbenchFormTablesRequest,
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


def _tables_response(spec: FormSpec) -> WorkbenchTablesResponse | JSONResponse:
    """The tables, sample and response tables of *spec*, the tables checked as a Quote Input's."""
    tables = workbench_tables(spec)
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


def _saved_tables_response(config: WorkbenchConfig) -> WorkbenchTablesResponse | JSONResponse:
    return _tables_response(read_form_document(config).spec)


def _save_form(
    config: WorkbenchConfig, body: WorkbenchFormSaveRequest
) -> WorkbenchFormSaveResponse:
    """Write the form unless the file changed since the view read it, and capture the write."""
    current = form_file_revision(config)
    if current != body.base_revision:
        raise StaleDocumentRevisionError(
            expected_revision=current,
            provided_revision=body.base_revision,
            message="The workbench's form changed on disk after the workbench read it. "
            "Reload the workbench before saving.",
        )
    revision = write_form(config.form_path, body.form)
    # The written file is one save on the ledger, as the pipeline's written files are, so a
    # milestone's sweep takes it from then on: a file this save created included. The path
    # resolves inside the project (the config checked it), which is where the ledger is.
    warnings: list[str] = []
    form_path = config.form_path.resolve().relative_to(config.project_root.resolve()).as_posix()
    git_sha, identity_required = capture_save_in_ledger(config.project_root, [form_path], warnings)
    return WorkbenchFormSaveResponse(
        form=body.form,
        revision=revision,
        warnings=warnings,
        git_sha=git_sha,
        identity_required=identity_required,
    )


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
    return await run_in_threadpool(_saved_tables_response, config)


@router.post("/tables", response_model=WorkbenchTablesResponse)
async def post_workbench_tables(
    body: WorkbenchFormTablesRequest,
) -> WorkbenchTablesResponse | JSONResponse:
    """The tables, sample and response tables of a form as the view holds it, saved or not."""
    await _enabled_config()
    return await run_in_threadpool(_tables_response, body.form)


@router.get("/form", response_model=WorkbenchFormResponse)
async def get_workbench_form() -> WorkbenchFormResponse:
    """The form as its file holds it now, with the file's revision."""
    config = await _enabled_config()
    document = await run_in_threadpool(read_form_document, config)
    return WorkbenchFormResponse(form=document.spec, revision=document.revision)


@router.put("/form", response_model=WorkbenchFormSaveResponse)
async def put_workbench_form(body: WorkbenchFormSaveRequest) -> WorkbenchFormSaveResponse:
    """Write the form, unless its file changed since the view read it, and capture the write."""
    config = await _enabled_config()
    try:
        return await run_in_threadpool(_save_form, config, body)
    except StaleDocumentRevisionError as exc:
        # The view's form is behind the disk; nothing was written. The detail leads with
        # the same stable code the pipeline save answers, so the client matches one.
        logger.warning(
            "workbench_form_stale_revision",
            expected_revision=exc.expected_revision,
            provided_revision=exc.provided_revision,
        )
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc}") from None
