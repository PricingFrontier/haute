"""The workbench routes (specs/workbench): its status, its tables as the saved form defines
them now or as an unsaved form the view holds would, the form itself, read and saved, the
params files a column's allowed values are read from, the SQL that brings a quote store's
tables to a form, which the people who own its database start from, and a form compared with
the tables an environment's database holds, read through its connection in
``connections.toml`` under the analyst's own sign-in, nothing written there.

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
    folder_index,
    folder_revision,
    form_at_ref,
    form_folder_lock,
    form_from_files,
    read_folder_unlocked,
    read_form_document,
)
from haute._workbench_form import FormDocument, FormSpec, WorkbenchFormError, with_quote_details
from haute._workbench_params import read_param_file
from haute._workbench_tables import (
    WorkbenchTablesError,
    input_tables,
    output_tables,
    workbench_tables,
)
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
    WorkbenchCatalogueColumn,
    WorkbenchCatalogueTable,
    WorkbenchFormResponse,
    WorkbenchFormSaveRequest,
    WorkbenchFormSaveResponse,
    WorkbenchFormTablesRequest,
    WorkbenchParamFileResponse,
    WorkbenchStatusResponse,
    WorkbenchStoreCatalogueRequest,
    WorkbenchStoreCatalogueResponse,
    WorkbenchStoreColumn,
    WorkbenchStoreCompareRequest,
    WorkbenchStoreCompareResponse,
    WorkbenchStoreDifference,
    WorkbenchStoreExtra,
    WorkbenchStoreRename,
    WorkbenchStoreSqlEnvironment,
    WorkbenchStoreSqlRequest,
    WorkbenchStoreSqlResponse,
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
    as every form read or saved does, the tables checked as the pipeline's, and the columns
    the quote store keeps in each table that the form does not."""
    # The store's columns are declared beside its tables, which only the app's store loads.
    from haute._quote_store._schema import store_columns

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
        store_columns={
            table_id: [WorkbenchStoreColumn(name=name, kind=kind) for name, kind in kept]
            for table_id, kept in store_columns(spec).items()
        },
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
    folder changed since the view read it, holds a file that is not the form's, or a path on
    the way is no ordinary one of the project's (a link, a junction, a reserved name), and
    capture the writes: one `project_write`."""
    form = with_quote_details(body.form)
    try:
        files = folder_files(form, config.form_folder)
    except FormFolderNameError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    try:
        # What a save writes reads back: a reference kept under a name the schema did not
        # hold, which another column of the component's has taken since, would otherwise
        # leave a folder no read takes (a second look for one column, say).
        form_from_files(config, files)
    except WorkbenchFormError as exc:
        raise HTTPException(
            status_code=422, detail=f"its files would not read back as a form: {exc.message}"
        ) from None
    with form_folder_lock(config.project_root):
        if not config.form_folder.is_dir():
            _stale(config, body, None)
        snapshot = read_folder_unlocked(config)
        if snapshot.revision != body.base_revision:
            _stale(config, body, snapshot.revision)
        try:
            # The save deletes only the form's own files: the folder must hold no other.
            folder_index(config, snapshot.files)
        except WorkbenchFormError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from None
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
        enabled=config.enabled,
        form=config.form if config.enabled else None,
        environments=[environment.name for environment in config.store] if config.enabled else [],
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


def _default_branch_form(config: WorkbenchConfig) -> tuple[str, FormSpec] | None:
    """The project's default branch and the form it holds, as the remote holds it where the
    clone has one; None outside a repository and while that branch holds no form."""
    from haute._git_core import (
        _canonical_remote,
        _get_default_branch,
        _is_git_repo,
        _run_git_ok,
        git_binary_available,
    )

    root = config.project_root
    if not git_binary_available() or not _is_git_repo(root):
        return None
    branch = _get_default_branch(root)
    remote = _canonical_remote(root)
    refs = [f"refs/remotes/{remote}/{branch}"] if remote is not None else []
    for ref in [*refs, f"refs/heads/{branch}"]:
        found, _ = _run_git_ok("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=root)
        if found:
            spec = form_at_ref(config, ref)
            return None if spec is None else (branch, spec)
    return None


def _checked_form(form: FormSpec) -> FormSpec:
    """*form* as the SQL is written for it: holding quote_details as every form holds it once
    read, whatever the view sent of it, and each name checked as the tables' are, so the SQL
    holds none a database cannot."""
    spec = with_quote_details(form)
    try:
        input_tables(spec)
        output_tables(spec)
    except WorkbenchTablesError as exc:
        raise contract_error_http_exception(exc) from exc
    return spec


def _store_sql(
    config: WorkbenchConfig, body: WorkbenchStoreSqlRequest
) -> WorkbenchStoreSqlResponse:
    from haute._quote_store import offline_plan, render_sql
    from haute._quote_store._migrate import PROVIDER_DIALECTS
    from haute._quote_store._providers import PROVIDERS

    spec = _checked_form(body.form)
    found = _default_branch_form(config) if body.start == "branch" else None
    branch, base = found if found is not None else (None, None)
    environments = [
        WorkbenchStoreSqlEnvironment(
            name=environment.name,
            provider=environment.provider,
            sql=render_sql(
                offline_plan(base, spec, provider=PROVIDERS[environment.provider]),
                spec,
                PROVIDER_DIALECTS[environment.provider],
            ),
        )
        for environment in config.store
    ]
    return WorkbenchStoreSqlResponse(branch=branch, environments=environments)


@router.post("/store-sql", response_model=WorkbenchStoreSqlResponse)
async def store_sql(body: WorkbenchStoreSqlRequest) -> WorkbenchStoreSqlResponse:
    """The SQL that brings each environment the project declares to a form the view holds,
    saved or not, from the form the default branch holds, or from none."""
    config = await _enabled_config()
    return await run_in_threadpool(_store_sql, config, body)


def _store_catalogue(
    config: WorkbenchConfig, body: WorkbenchStoreCatalogueRequest
) -> WorkbenchStoreCatalogueResponse:
    from haute._connections import project_environ
    from haute._quote_store import connected_store
    from haute._quote_store._compare import read_store_catalogue

    location = connected_store(config, body.environment, project_environ(config.project_root))
    catalogue = read_store_catalogue(location)
    return WorkbenchStoreCatalogueResponse(
        environment=body.environment,
        tables=[
            WorkbenchCatalogueTable(
                name=table.name,
                columns=[
                    WorkbenchCatalogueColumn(
                        name=column.name,
                        kind=column.kind,
                        type=column.type,
                        nullable=column.nullable,
                        collation=column.collation,
                        quotes=column.quotes or 0,
                    )
                    for column in table.columns
                ],
                primary_key=list(table.primary_key),
                refers=table.refers,
                quotes=table.quotes or 0,
            )
            for table in catalogue.tables
        ],
    )


@router.post("/store-catalogue", response_model=WorkbenchStoreCatalogueResponse)
async def store_catalogue(body: WorkbenchStoreCatalogueRequest) -> WorkbenchStoreCatalogueResponse:
    """The tables an environment's database holds, read through its connection in
    ``connections.toml`` under the analyst's sign-in, with the quotes holding values in each
    table and column; nothing written. An environment with no connection, or a database that
    cannot be reached or read, answers 409 naming what to set."""
    config = await _enabled_config()
    return await run_in_threadpool(_store_catalogue, config, body)


def _store_compare(
    config: WorkbenchConfig, body: WorkbenchStoreCompareRequest
) -> WorkbenchStoreCompareResponse:
    from haute._quote_store import render_sql
    from haute._quote_store._catalogue import Catalogue, CatalogueColumn, CatalogueTable
    from haute._quote_store._compare import Rename, compare
    from haute._quote_store._environment import connection_of
    from haute._quote_store._migrate import PROVIDER_DIALECTS
    from haute._quote_store._providers import PROVIDERS

    spec = _checked_form(body.form)
    connection = connection_of(config, body.environment)
    found = _default_branch_form(config)
    branch, base = found if found is not None else (None, None)
    catalogue = Catalogue(
        tables=tuple(
            CatalogueTable(
                name=table.name,
                columns=tuple(
                    CatalogueColumn(
                        name=column.name,
                        kind=column.kind,
                        type=column.type,
                        nullable=column.nullable,
                        collation=column.collation,
                        quotes=column.quotes,
                    )
                    for column in table.columns
                ),
                primary_key=tuple(table.primary_key),
                refers=table.refers,
                quotes=table.quotes,
            )
            for table in body.tables
        )
    )
    provider = PROVIDERS[connection.provider]
    comparison = compare(
        spec,
        catalogue,
        provider=provider,
        base=base,
        renames=[Rename(each.table, each.column, each.was) for each in body.renames],
    )
    return WorkbenchStoreCompareResponse(
        differences=[
            WorkbenchStoreDifference(
                table=each.table, column=each.column, kind=each.kind, message=each.message
            )
            for each in comparison.differences
        ],
        extras=[
            WorkbenchStoreExtra(
                table=each.table,
                column=each.column,
                table_id=each.table_id,
                quotes=each.quotes or 0,
            )
            for each in comparison.extras
        ],
        renames=[
            WorkbenchStoreRename(table=each.table, column=each.column, was=each.was)
            for each in comparison.renames
        ],
        matches=comparison.matches,
        sql=render_sql(
            comparison.plan,
            spec,
            PROVIDER_DIALECTS[provider.name],
            sql_schema=connection.schema,
        ),
        branch=branch,
    )


@router.post("/store-compare", response_model=WorkbenchStoreCompareResponse)
async def store_compare(body: WorkbenchStoreCompareRequest) -> WorkbenchStoreCompareResponse:
    """A form the view holds, saved or not, compared with the tables an environment's
    database holds, as the store's catalogue read them: each difference by the form's ids,
    each table or column the database holds beyond it, and the SQL that brings the tables to
    the form from what they hold, renaming what the analyst paired or the default branch's
    form names by one id. Reads no database."""
    config = await _enabled_config()
    return await run_in_threadpool(_store_compare, config, body)


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
