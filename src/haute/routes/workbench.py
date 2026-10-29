"""The workbench routes (specs/workbench): its status, its tables as the saved form defines
them now or as an unsaved form the view holds would, the form itself, read and saved, and
the params files a column's allowed values are read from.

Every route reads ``haute.toml``, and those that serve the saved form read its folder too,
from the working directory on every request, off the event loop, so an edit to either
reaches the next request. A bad table or form answers 409 with what to fix
(``haute.routes._error_handlers``) while the pipeline editor goes on working. A save quotes
the revision the form was read at and is refused, writing nothing, when the folder has
changed since; it writes the files that changed and deletes those the form no longer has,
all put back when one fails, and a save that lands is captured on the clone's save ledger as
the pipeline's saves are, answering what the capture gave. A save runs under the pipeline
save's lock, as every write to the project does, and holds the form folder's lock while it
checks and writes, as every read of the folder does.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem
from haute._git_lock import repository_mutation
from haute._logging import get_logger
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_folder import (
    FormFolderNameError,
    folder_files,
    folder_revision,
    form_folder_lock,
    read_folder_unlocked,
    read_form_document,
)
from haute._workbench_form import FormDocument, FormSpec, with_quote_details
from haute._workbench_params import read_param_file
from haute._workbench_tables import WorkbenchTablesError, workbench_tables
from haute.routes._contract_errors import contract_error_http_exception
from haute.routes._helpers import project_write
from haute.routes._save_pipeline import (
    StaleDocumentRevisionError,
    _rollback_artifacts,
    _stage_artifact_delete,
    _stage_artifact_write_bytes,
    _TouchedFile,
    capture_save_in_ledger,
)
from haute.schemas import (
    WorkbenchFormResponse,
    WorkbenchFormSaveRequest,
    WorkbenchFormSaveResponse,
    WorkbenchFormTablesRequest,
    WorkbenchParamFileResponse,
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


def _tables_response(spec: FormSpec) -> WorkbenchTablesResponse:
    """The tables, sample and response tables of *spec*, holding Haute's ``quote_details``
    as every form read or saved does, the tables checked as the pipeline's."""
    spec = with_quote_details(spec)
    try:
        tables = workbench_tables(spec)
    except WorkbenchTablesError as exc:
        raise contract_error_http_exception(exc) from exc
    # Not checked against the tables here: a sample that doesn't fit fails the previews
    # that read it, and never stops the tables updating.
    return WorkbenchTablesResponse(
        tables=list(tables.tables),
        sample=tables.sample,
        response_tables=list(tables.response_tables),
    )


def _read_document(config: WorkbenchConfig) -> FormDocument:
    """The form the folder holds now, read under the repository's lock, which a branch
    switch holds, as well as the form folder's, which every save holds."""
    with repository_mutation(config.project_root):
        return read_form_document(config)


def _saved_tables_response(config: WorkbenchConfig) -> WorkbenchTablesResponse:
    return _tables_response(_read_document(config).spec)


def _stale(
    config: WorkbenchConfig, body: WorkbenchFormSaveRequest, current: str | None
) -> NoReturn:
    raise StaleDocumentRevisionError(
        expected_revision=current,
        provided_revision=body.base_revision,
        message=f"{config.form} changed on disk after the workbench read it. "
        "Reload the workbench before saving.",
    )


def _save_form(
    config: WorkbenchConfig, body: WorkbenchFormSaveRequest
) -> WorkbenchFormSaveResponse:
    """Write the form's files that changed and delete those it no longer has, unless the
    folder changed since the view read it or a path on the way is no ordinary one of the
    project's (a link, a junction, a reserved name), and capture the writes: one
    `project_write`."""
    form = with_quote_details(body.form)
    try:
        files = folder_files(form, config.form_folder)
    except FormFolderNameError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    with form_folder_lock(config.project_root):
        if not config.form_folder.is_dir():
            _stale(config, body, None)
        snapshot = read_folder_unlocked(config)
        if snapshot.revision != body.base_revision:
            _stale(config, body, snapshot.revision)
        writes = {path: data for path, data in files.items() if snapshot.files.get(path) != data}
        deletes = sorted(path for path in snapshot.files if path not in files)
        for path in [*writes, *deletes]:
            _, problem = ordinary_path_problem(config.project_root, f"{config.form}/{path}")
            if problem is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"{config.form}/{path} {PATH_PROBLEMS[problem]}: the form folder "
                    "holds files of its own.",
                )
        touched: list[_TouchedFile] = []
        try:
            for path, data in writes.items():
                _stage_artifact_write_bytes(config.form_folder / path, data, touched)
            for path in deletes:
                _stage_artifact_delete(config.form_folder / path, touched)
        except OSError as exc:
            failed = _rollback_artifacts(touched)
            detail = f"{config.form} could not be written: {exc}"
            if failed:
                # By its path in the folder: schema/ and sample/ hold files of one name.
                kept = ", ".join(
                    f"{config.form}/{path.relative_to(config.form_folder).as_posix()}"
                    for path in failed
                )
                detail += f"; {kept} could not be put back: restore them from version history"
            logger.warning("workbench_form_write_failed", form=config.form, error=str(exc))
            raise HTTPException(status_code=409, detail=detail) from None
    # The written and deleted files are one save on the ledger, as the pipeline's written
    # files are, so a milestone's sweep takes them from then on. Each path is an ordinary
    # one inside the project (checked above), where the ledger is.
    warnings: list[str] = []
    changed = [f"{config.form}/{path}" for path in [*writes, *deletes]]
    captured = capture_save_in_ledger(config.project_root, changed, warnings)
    return WorkbenchFormSaveResponse(
        form=form, revision=folder_revision(files), warnings=warnings, **captured._asdict()
    )


@router.get("", response_model=WorkbenchStatusResponse)
async def workbench_status() -> WorkbenchStatusResponse:
    """Whether the project's workbench is enabled, and where its form folder is."""
    config = await run_in_threadpool(_config)
    return WorkbenchStatusResponse(
        enabled=config.enabled, form=config.form if config.enabled else None
    )


@router.get("/tables", response_model=WorkbenchTablesResponse)
async def get_workbench_tables() -> WorkbenchTablesResponse:
    """The workbench's tables, sample and response tables as its form defines them now."""
    config = await _enabled_config()
    return await run_in_threadpool(_saved_tables_response, config)


@router.post("/tables", response_model=WorkbenchTablesResponse)
async def post_workbench_tables(body: WorkbenchFormTablesRequest) -> WorkbenchTablesResponse:
    """The tables, sample and response tables of a form as the view holds it, saved or not."""
    await _enabled_config()
    return await run_in_threadpool(_tables_response, body.form)


@router.get("/form", response_model=WorkbenchFormResponse)
async def get_workbench_form() -> WorkbenchFormResponse:
    """The form as its folder holds it now, with the folder's revision."""
    config = await _enabled_config()
    document = await run_in_threadpool(_read_document, config)
    return WorkbenchFormResponse(
        form=document.spec, revision=document.revision, repairs=list(document.repairs)
    )


@router.put("/form", response_model=WorkbenchFormSaveResponse)
async def put_workbench_form(body: WorkbenchFormSaveRequest) -> WorkbenchFormSaveResponse:
    """Write the form, unless its folder changed since the view read it, and capture it."""
    config = await _enabled_config()
    try:
        # In turn with every other save, the pipeline's too: two saves never pass the
        # revision check together, and a capture never races another's on the git index.
        return await project_write(config.project_root, lambda: _save_form(config, body))
    except StaleDocumentRevisionError as exc:
        # The view's form is behind the disk; nothing was written. The detail leads with
        # the same stable code the pipeline save answers, so the client matches one.
        logger.warning(
            "workbench_form_stale_revision",
            expected_revision=exc.expected_revision,
            provided_revision=exc.provided_revision,
        )
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc}") from None


@router.get("/params", response_model=WorkbenchParamFileResponse)
async def get_param_file(file: str, column: str | None = None) -> WorkbenchParamFileResponse:
    """A params file's header, with the distinct values of *column* when one is named: what
    a column's allowed values are read from. A file or column that cannot give them answers
    409 with why (``ParamFileError``). Served whether or not the workbench is enabled, since
    Banding's Import from params reads its categorical values here too."""
    # The project is the working directory, as it is for the rest of the server.
    read = await run_in_threadpool(read_param_file, Path.cwd(), file, column)
    return WorkbenchParamFileResponse(columns=read.columns, values=read.values)
