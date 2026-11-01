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

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem
from haute._git_lock import repository_mutation
from haute._logging import get_logger
from haute._workbench_app import ResolvedRelease, resolve_release
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
from haute._workbench_test_quotes import (
    RUN_TIMEOUT_SECONDS,
    Checked,
    TestQuoteError,
    TestQuoteFile,
    TestQuoteTakenError,
    basis,
    check_name,
    check_test_quote,
    effective_request,
    opened_values,
    price_request,
    read_test_quote,
    read_test_quotes,
    revision_of,
    test_quote_bytes,
    test_quote_file,
    too_large,
)
from haute.errors import HauteError
from haute.routes._contract_errors import contract_error_http_exception
from haute.routes._helpers import project_write
from haute.routes._save_pipeline import (
    LedgerCapture,
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
    WorkbenchTestQuoteMove,
    WorkbenchTestQuoteOpenRequest,
    WorkbenchTestQuoteOpenResponse,
    WorkbenchTestQuoteRefusal,
    WorkbenchTestQuoteRunResponse,
    WorkbenchTestQuotesAcceptRequest,
    WorkbenchTestQuotesAcceptResponse,
    WorkbenchTestQuoteSaveRequest,
    WorkbenchTestQuotesResponse,
    WorkbenchTestQuoteSummary,
    WorkbenchTestQuoteTarget,
    WorkbenchTestQuoteWriteResponse,
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
                        quotes=column.quotes,
                    )
                    for column in table.columns
                ],
                primary_key=list(table.primary_key),
                refers=table.refers,
                quotes=table.quotes,
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
                quotes=each.quotes,
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


# ── Test quotes (specs/workbench, Test quotes; Control flow 37) ─────────────

_T = TypeVar("_T")
_W = TypeVar("_W")


class _RefusedError(Exception):
    """A test-quote write refused: its status and why, nothing written."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _test_quote_folder(config: WorkbenchConfig) -> Path:
    """The test-quote folder: ``[test_quotes] dir``, or ``tests/quotes`` when it names none,
    whether or not it exists yet (the first save makes it)."""
    from haute.deploy._config import DEFAULT_TEST_QUOTES_DIR, DeployConfig

    named = DeployConfig.from_toml(config.project_root / "haute.toml").test_quotes_dir
    return named if named is not None else config.project_root / DEFAULT_TEST_QUOTES_DIR


def _branch(config: WorkbenchConfig) -> str:
    from haute._git_core import _get_current_branch

    return _get_current_branch(config.project_root)


def _summary(quote: TestQuoteFile) -> WorkbenchTestQuoteSummary:
    return WorkbenchTestQuoteSummary(
        name=quote.name, file=quote.file, revision=quote.revision, problem=quote.problem
    )


def _named_file(config: WorkbenchConfig, folder: Path, file: str) -> Path:
    try:
        return test_quote_file(config.project_root, folder, file)
    except TestQuoteError as exc:
        raise _RefusedError(422, exc.message or str(exc)) from None


def _held(path: Path) -> tuple[bytes, TestQuoteFile]:
    """The file at *path* as read, refused when it is gone or is not a Preview test quote."""
    if not path.is_file():
        raise _RefusedError(404, f"{path.name} is no longer kept: list the test quotes again.")
    data = path.read_bytes()
    quote = read_test_quote(path)
    if quote.input is None:
        raise _RefusedError(422, f"{path.name} cannot be opened here: {quote.problem}.")
    return data, quote


def _on_branch(config: WorkbenchConfig, branch: str) -> None:
    now = _branch(config)
    if now != branch:
        raise _RefusedError(
            409,
            f"The branch is {now} now, not {branch}, which the panel read: list the test "
            "quotes again.",
        )


def _resolved(config: WorkbenchConfig) -> ResolvedRelease:
    """The project as saved, resolved as ``haute app build`` resolves it; a project that
    does not resolve is refused, saying why."""
    try:
        return resolve_release(config.project_root)
    except HauteError as exc:
        raise _RefusedError(409, exc.message or str(exc)) from None


async def _within_deadline(
    release: ResolvedRelease, work: Callable[[], _T], timed_out: Callable[[], _T]
) -> _T:
    """*work* on *release* in the thread pool within ``RUN_TIMEOUT_SECONDS``, *release* closed
    once it is done; past the deadline *timed_out*'s answer, the work left running closing
    *release* itself when it ends."""
    loop = asyncio.get_running_loop()
    running = loop.run_in_executor(None, work)
    try:
        answer = await asyncio.wait_for(asyncio.shield(running), RUN_TIMEOUT_SECONDS)
    except TimeoutError:
        running.add_done_callback(lambda _: release.close())
        return timed_out()
    except BaseException:
        release.close()
        raise
    release.close()
    return answer


def _http(refused: _RefusedError) -> HTTPException:
    return HTTPException(status_code=refused.status, detail=refused.detail)


def _written(
    config: WorkbenchConfig, writes: dict[Path, bytes | None]
) -> tuple[list[str], LedgerCapture]:
    """Stage *writes* (bytes to write, None to delete) as the form's save stages its files,
    every one rolled back when one fails, and capture them on the save ledger as one save;
    answers the capture's warnings and the capture."""
    touched: list[_TouchedFile] = []
    try:
        for path, data in writes.items():
            if data is None:
                _stage_artifact_delete(path, touched)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                _stage_artifact_write_bytes(path, data, touched)
    except OSError as exc:
        _rollback_artifacts(touched)
        logger.warning("workbench_test_quote_write_failed", error=str(exc))
        raise _RefusedError(409, f"The test quotes could not be written: {exc}") from None
    changed = [path.relative_to(config.project_root).as_posix() for path in writes]
    warnings: list[str] = []
    captured = capture_save_in_ledger(config.project_root, changed, warnings)
    return warnings, captured


@router.get("/test-quotes", response_model=WorkbenchTestQuotesResponse)
async def list_test_quotes() -> WorkbenchTestQuotesResponse:
    """The test-quote folder's files by name, with the branch they were read on."""
    config = await _enabled_config()

    def read() -> WorkbenchTestQuotesResponse:
        with repository_mutation(config.project_root):
            folder = _test_quote_folder(config)
            return WorkbenchTestQuotesResponse(
                branch=_branch(config), quotes=[_summary(q) for q in read_test_quotes(folder)]
            )

    return await run_in_threadpool(read)


@router.post("/test-quotes", response_model=WorkbenchTestQuoteWriteResponse)
async def save_test_quote(body: WorkbenchTestQuoteSaveRequest) -> WorkbenchTestQuoteWriteResponse:
    """Save the sample as saved as a test quote: priced on the project as saved, then
    written under a new name or over the test quote it replaces."""
    config = await _enabled_config()
    if (body.name is None) == (body.replace is None):
        raise HTTPException(422, "Name a new test quote, or the one it replaces.")
    try:
        release = await run_in_threadpool(_resolved, config)
    except _RefusedError as refused:
        raise _http(refused) from None

    def priced() -> tuple[dict[str, Any], dict[str, Any]]:
        if release.document.revision != body.form_revision:
            raise _RefusedError(
                409,
                f"{config.form} was saved again since this panel saved it: save the project "
                "again to save this quote.",
            )
        request = effective_request(release.form)
        if not request:
            raise _RefusedError(422, "Type the quote first.")
        large = too_large(request)
        if large is not None:
            raise _RefusedError(422, f"The quote cannot be kept: {large}.")
        try:
            return request, price_request(release, request)
        # A pipeline's own code can raise anything on a request: the save says why.
        except Exception as exc:  # noqa: BLE001
            message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            raise _RefusedError(422, f"The quote did not price: {message}") from None

    def took_too_long() -> tuple[dict[str, Any], dict[str, Any]]:
        raise _RefusedError(
            422, f"The quote took longer than {RUN_TIMEOUT_SECONDS} seconds to price."
        )

    try:
        request, answer = await _within_deadline(release, priced, took_too_long)
    except _RefusedError as refused:
        raise _http(refused) from None

    def write() -> WorkbenchTestQuoteWriteResponse:
        _on_branch(config, body.branch)
        with form_folder_lock(config.project_root):
            if read_folder_unlocked(config).revision != body.form_revision:
                raise _RefusedError(
                    409,
                    f"{config.form} was saved again while the quote priced: save the project "
                    "again to save this quote.",
                )
        folder = _test_quote_folder(config)
        tolerance: float | None = None
        metadata: dict[str, Any] = {}
        if body.replace is not None:
            path = _named_file(config, folder, body.replace.file)
            data, held = _held(path)
            if revision_of(data) != body.replace.revision:
                raise _RefusedError(
                    409, f"{path.name} changed since the panel read it: list the test quotes again."
                )
            tolerance, metadata = held.tolerance, held.metadata
        else:
            assert body.name is not None
            taken = [each.name for each in folder.glob("*.json")] if folder.is_dir() else []
            try:
                name = check_name(body.name, taken)
            except TestQuoteTakenError as exc:
                raise _RefusedError(409, exc.message or str(exc)) from None
            except TestQuoteError as exc:
                raise _RefusedError(422, exc.message or str(exc)) from None
            path = _named_file(config, folder, f"{name}.json")
        data = test_quote_bytes(request, answer, tolerance, metadata)
        warnings, captured = _written(config, {path: data})
        return WorkbenchTestQuoteWriteResponse(
            quote=_summary(read_test_quote(path)), warnings=warnings, **captured._asdict()
        )

    return await _write(config, write)


async def _write(config: WorkbenchConfig, work: Callable[[], _W]) -> _W:
    try:
        return await project_write(config.project_root, work)
    except _RefusedError as refused:
        raise _http(refused) from None


@router.post("/test-quotes/{file}/open", response_model=WorkbenchTestQuoteOpenResponse)
async def open_test_quote(
    file: str, body: WorkbenchTestQuoteOpenRequest
) -> WorkbenchTestQuoteOpenResponse:
    """A test quote's values put back onto the form the view holds, and what it could not
    place."""
    config = await _enabled_config()

    def read() -> WorkbenchTestQuoteOpenResponse:
        with repository_mutation(config.project_root):
            path = _named_file(config, _test_quote_folder(config), file)
            _, quote = _held(path)
        assert quote.input is not None
        values, not_opened = opened_values(with_quote_details(body.form), quote.input)
        return WorkbenchTestQuoteOpenResponse(values=values, not_opened=not_opened)

    try:
        return await run_in_threadpool(read)
    except _RefusedError as refused:
        raise _http(refused) from None


@router.post("/test-quotes/{file}/run", response_model=WorkbenchTestQuoteRunResponse)
async def run_test_quote(file: str) -> WorkbenchTestQuoteRunResponse:
    """A test quote priced from its stored request on the project as saved, and compared
    with its expected answer."""
    config = await _enabled_config()

    def prepare() -> tuple[str, TestQuoteFile, ResolvedRelease, str]:
        with repository_mutation(config.project_root):
            branch = _branch(config)
            path = _named_file(config, _test_quote_folder(config), file)
            _, quote = _held(path)
        release = _resolved(config)
        try:
            return branch, quote, release, basis(release)
        except BaseException:
            release.close()
            raise

    try:
        branch, quote, release, run_basis = await run_in_threadpool(prepare)
    except _RefusedError as refused:
        raise _http(refused) from None
    checked = await _within_deadline(
        release,
        lambda: check_test_quote(release, quote),
        lambda: Checked(
            quote.name,
            quote.file,
            "failed",
            why=f"it took longer than {RUN_TIMEOUT_SECONDS} seconds to price",
        ),
    )
    return WorkbenchTestQuoteRunResponse(
        name=checked.name,
        file=checked.file,
        outcome=checked.outcome,
        moves=[
            WorkbenchTestQuoteMove(
                table=move.table,
                row=move.row,
                column=move.column,
                before=move.before,
                after=move.after,
            )
            for move in checked.moves
        ],
        removed=list(checked.removed),
        why=checked.why,
        revision=quote.revision,
        basis=run_basis,
        branch=branch,
    )


@router.post("/test-quotes/accept", response_model=WorkbenchTestQuotesAcceptResponse)
async def accept_test_quotes(
    body: WorkbenchTestQuotesAcceptRequest,
) -> WorkbenchTestQuotesAcceptResponse:
    """Accept test quotes' new answers: each priced again on the server, on the basis the
    run priced on, and written over its expected answer while it is still the revision the
    run read."""
    config = await _enabled_config()

    def accept() -> WorkbenchTestQuotesAcceptResponse:
        _on_branch(config, body.branch)
        writes: dict[Path, bytes | None] = {}
        refused: list[WorkbenchTestQuoteRefusal] = []
        release = _resolved(config)
        try:
            if basis(release) != body.basis:
                raise _RefusedError(
                    409,
                    "What the run priced on changed since (the pipeline, its helper code, the "
                    "form, the outputs it answers or a model): run the test quotes again.",
                )
            folder = _test_quote_folder(config)
            for target in body.quotes:
                why = _accept_one(config, release, folder, target, writes)
                if why is not None:
                    refused.append(WorkbenchTestQuoteRefusal(file=target.file, why=why))
        finally:
            release.close()
        if not writes:
            return WorkbenchTestQuotesAcceptResponse(accepted=[], refused=refused)
        warnings, captured = _written(config, writes)
        return WorkbenchTestQuotesAcceptResponse(
            accepted=[_summary(read_test_quote(path)) for path in writes],
            refused=refused,
            warnings=warnings,
            **captured._asdict(),
        )

    return await _write(config, accept)


def _accept_one(
    config: WorkbenchConfig,
    release: ResolvedRelease,
    folder: Path,
    target: WorkbenchTestQuoteTarget,
    writes: dict[Path, bytes | None],
) -> str | None:
    """Price one test quote again and add its new file to *writes*; answers why it is not
    accepted, None when it is."""
    try:
        path = test_quote_file(config.project_root, folder, target.file)
    except TestQuoteError as exc:
        return exc.message or str(exc)
    if not path.is_file():
        return "it is no longer kept"
    if revision_of(path.read_bytes()) != target.revision:
        return "it changed since the run"
    quote = read_test_quote(path)
    if quote.input is None:
        return f"it cannot be opened here: {quote.problem}"
    large = too_large(quote.input)
    if large is not None:
        return large
    try:
        answer = price_request(release, quote.input)
    # A pipeline's own code can raise anything on a request: the quote is named, not accepted.
    except Exception as exc:  # noqa: BLE001
        return f"it did not price: {getattr(exc, 'message', None) or exc}"
    # An editor outside Haute takes no lock: the file is checked again just before it is
    # staged, so an edit made while it priced is never overwritten.
    try:
        test_quote_file(config.project_root, folder, target.file)
    except TestQuoteError as exc:
        return exc.message or str(exc)
    if not path.is_file() or revision_of(path.read_bytes()) != target.revision:
        return "it changed while it priced"
    writes[path] = test_quote_bytes(quote.input, answer, quote.tolerance, quote.metadata)
    return None


@router.delete("/test-quotes/{file}", response_model=WorkbenchTestQuoteWriteResponse)
async def delete_test_quote(
    file: str, revision: str, branch: str
) -> WorkbenchTestQuoteWriteResponse:
    """Delete a test quote, while it is still the revision the panel read."""
    config = await _enabled_config()

    def delete() -> WorkbenchTestQuoteWriteResponse:
        _on_branch(config, branch)
        path = _named_file(config, _test_quote_folder(config), file)
        if not path.is_file():
            raise _RefusedError(404, f"{file} is no longer kept: list the test quotes again.")
        if revision_of(path.read_bytes()) != revision:
            raise _RefusedError(
                409, f"{file} changed since the panel read it: list the test quotes again."
            )
        warnings, captured = _written(config, {path: None})
        return WorkbenchTestQuoteWriteResponse(quote=None, warnings=warnings, **captured._asdict())

    return await _write(config, delete)
