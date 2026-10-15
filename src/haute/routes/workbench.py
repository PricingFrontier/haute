"""The workbench routes (specs/workbench): its status, its tables as the saved form defines
them now or as an unsaved form the view holds would, the form itself, read and saved, and
the params files a column's allowed values are read from.

Every route reads ``haute.toml``, and those that serve the saved form read it too, from the
working directory on every request, off the event loop, so an edit to either reaches the
next request. A bad table or form answers 409 with what to fix
(``haute.routes._error_handlers``) while the pipeline editor goes on working. A save quotes
the revision the form was read at and is refused, writing nothing, when the file has
changed since; a save that lands is captured on the clone's save ledger as the pipeline's
saves are, and answers what the capture gave. A save runs under the pipeline save's lock,
as every write to the project does.

The test quotes an analyst keeps in Preview are read and written here too, in the project's
test-quote file. A write runs under the save lock and the repository's lock, which a commit,
a branch switch and a merge hold, with the file opened for that write alone and closed before
the lock is let go, and is captured on the ledger; a read holds the repository's lock alone.
A file behind the store's schema is brought up to date by a write and refused by a read.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from haute._git_lock import repository_mutation
from haute._logging import get_logger
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_form import (
    FormSpec,
    form_file_revision,
    read_form_document,
    with_quote_details,
    write_form,
)
from haute._workbench_params import read_param_file
from haute._workbench_tables import WorkbenchTablesError, workbench_tables
from haute.routes._contract_errors import contract_error_http_exception
from haute.routes._helpers import save_lock
from haute.routes._save_pipeline import StaleDocumentRevisionError, capture_save_in_ledger
from haute.schemas import (
    WorkbenchFormResponse,
    WorkbenchFormSaveRequest,
    WorkbenchFormSaveResponse,
    WorkbenchFormTablesRequest,
    WorkbenchParamFileResponse,
    WorkbenchStatusResponse,
    WorkbenchTablesResponse,
    WorkbenchTestQuoteAcceptRequest,
    WorkbenchTestQuoteChange,
    WorkbenchTestQuoteCompareRequest,
    WorkbenchTestQuoteCompareResponse,
    WorkbenchTestQuoteOpenRequest,
    WorkbenchTestQuoteOpenResponse,
    WorkbenchTestQuoteSaveRequest,
    WorkbenchTestQuotesBranchResponse,
    WorkbenchTestQuotesResponse,
    WorkbenchTestQuoteSummary,
    WorkbenchTestQuoteWriteResponse,
)

if TYPE_CHECKING:
    from haute._quote_store import QuoteRevision, QuoteStore, QuoteSummary

_T = TypeVar("_T")

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


def _saved_tables_response(config: WorkbenchConfig) -> WorkbenchTablesResponse:
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
            message=f"{config.form} changed on disk after the workbench read it. "
            "Reload the workbench before saving.",
        )
    form = with_quote_details(body.form)
    revision = write_form(config.form_path, form)
    # The written file is one save on the ledger, as the pipeline's written files are, so a
    # milestone's sweep takes it from then on: a file this save created included. The path
    # resolves inside the project (the config checked it), which is where the ledger is.
    warnings: list[str] = []
    form_path = config.form_path.resolve().relative_to(config.project_root.resolve()).as_posix()
    git_sha, identity_required = capture_save_in_ledger(config.project_root, [form_path], warnings)
    return WorkbenchFormSaveResponse(
        form=form,
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
    """The form as its file holds it now, with the file's revision."""
    config = await _enabled_config()
    document = await run_in_threadpool(read_form_document, config)
    return WorkbenchFormResponse(form=document.spec, revision=document.revision)


@router.put("/form", response_model=WorkbenchFormSaveResponse)
async def put_workbench_form(body: WorkbenchFormSaveRequest) -> WorkbenchFormSaveResponse:
    """Write the form, unless its file changed since the view read it, and capture the write."""
    config = await _enabled_config()
    try:
        # Under the shared save lock, as the pipeline's save is: two saves never pass the
        # revision check together, and a capture never races a pipeline save's on the
        # git index.
        async with save_lock:
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
    except OSError as exc:
        # The file or its folder could not be written (a permission, a file where the
        # folder should be): the pipeline's writes answer the same way.
        logger.warning("workbench_form_write_failed", form=config.form, error=str(exc))
        raise HTTPException(
            status_code=409, detail=f"{config.form} could not be written: {exc}"
        ) from None


@router.get("/params", response_model=WorkbenchParamFileResponse)
async def get_param_file(file: str, column: str | None = None) -> WorkbenchParamFileResponse:
    """A params file's header, with the distinct values of *column* when one is named: what
    a column's allowed values are read from. A file or column that cannot give them answers
    409 with why (``ParamFileError``)."""
    config = await _enabled_config()
    read = await run_in_threadpool(read_param_file, config.project_root, file, column)
    return WorkbenchParamFileResponse(columns=read.columns, values=read.values)


# ── Test quotes ───────────────────────────────────────────────────────────


def _test_quotes_file(config: WorkbenchConfig) -> tuple[Path, str]:
    """The test-quote file and its path as the ledger names it, relative to the project."""
    from haute._test_quotes import project_test_quotes_path

    path = project_test_quotes_path(config.project_root)
    return path, path.relative_to(config.project_root.resolve()).as_posix()


def _summary(summary: QuoteSummary) -> WorkbenchTestQuoteSummary:
    return WorkbenchTestQuoteSummary(
        id=summary.id,
        name=summary.name,
        number=summary.latest,
        saved_by=summary.saved_by,
        saved_at=summary.saved_at.isoformat(),
        priced=summary.priced,
    )


def _read_test_quotes(config: WorkbenchConfig, read: Callable[[QuoteStore], _T]) -> _T | None:
    """*read* on the test-quote file under the repository's lock; None while there is none."""
    from haute._quote_store import QuoteStoreNotMigratedError, open_store, store_url
    from haute._test_quotes import WorkbenchTestQuoteError, ensure_test_quote_git

    with repository_mutation(config.project_root):
        path, relative = _test_quotes_file(config)
        if not path.is_file():
            return None
        # The clone's drivers; .gitattributes, if it changes, rides the next write's capture.
        # A file that cannot be written leaves the read as it is: the next write says so.
        with suppress(OSError):
            ensure_test_quote_git(config.project_root, relative)
        try:
            store = open_store(store_url(path))
        except QuoteStoreNotMigratedError as exc:
            raise WorkbenchTestQuoteError(
                f"{relative} was written by another version of Haute. Save a test quote to "
                "bring it up to date."
            ) from exc
        with store:
            return read(store)


#: A write that names no branch: an accept names its revision by digest instead.
_ANY_BRANCH = object()


def _write_test_quotes(
    config: WorkbenchConfig,
    write: Callable[[QuoteStore, str], _T],
    *,
    create: bool = False,
    branch: object = _ANY_BRANCH,
) -> tuple[_T, WorkbenchTestQuoteWriteResponse]:
    """*write* on the test-quote file, brought to the newest schema if it must be (and made,
    when *create*), under the repository's lock, then the write captured on the ledger.

    Everything that can refuse it does so before anything is written, so a refusal never
    reports as unsaved a write that landed: *branch*, when given, must be the branch checked
    out now, and the file's git drivers are registered in ``.gitattributes`` first.
    """
    from haute._git_setup import get_identity
    from haute._quote_store import QuoteNotFoundError, migrate_store, open_store, store_url
    from haute._test_quotes import (
        WorkbenchTestQuoteError,
        ensure_test_quote_git,
        project_gitattributes_path,
        test_quotes_branch,
    )

    with repository_mutation(config.project_root):
        path, relative = _test_quotes_file(config)
        project_gitattributes_path(config.project_root)
        if branch is not _ANY_BRANCH and test_quotes_branch(config.project_root) != branch:
            raise WorkbenchTestQuoteError(
                "The branch changed while the quote was priced: save it again on this one."
            )
        if not create and not path.is_file():
            raise QuoteNotFoundError(f"{relative} holds no test quotes yet.")
        try:
            ensure_test_quote_git(config.project_root, relative)
        except OSError as exc:
            raise WorkbenchTestQuoteError(
                f".gitattributes could not be written, so nothing was saved: {exc}"
            ) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        migrate_store(store_url(path))
        name, email = get_identity(config.project_root)
        with open_store(store_url(path)) as store:
            result = write(store, email or name or "")
        captured = _capture_test_quotes(config, relative)
    return result, captured


def _capture_test_quotes(config: WorkbenchConfig, relative: str) -> WorkbenchTestQuoteWriteResponse:
    """The test-quote file at *relative* captured on the save ledger as it is, with the
    drivers in ``.gitattributes``, under the repository's lock the caller holds. Whatever
    cannot be done is a warning: the write, if any, has landed."""
    from haute._test_quotes import (
        WorkbenchTestQuoteError,
        ensure_test_quote_git,
        project_gitattributes_path,
    )

    warnings: list[str] = []
    captured = [relative]
    try:
        ensure_test_quote_git(config.project_root, relative)
        if project_gitattributes_path(config.project_root).is_file():
            captured.append(".gitattributes")
    except (OSError, WorkbenchTestQuoteError) as exc:
        warnings.append(f"The test quotes' git drivers could not be registered: {exc}")
    git_sha, identity_required = capture_save_in_ledger(config.project_root, captured, warnings)
    return WorkbenchTestQuoteWriteResponse(
        warnings=warnings, git_sha=git_sha, identity_required=identity_required
    )


def _store_error(exc: Exception) -> HTTPException:
    """A quote-store refusal as 409, its code first, as the form's stale save answers."""
    from haute._quote_store import QuoteStoreError

    assert isinstance(exc, QuoteStoreError)
    return HTTPException(status_code=409, detail=f"{exc.error_code}: {exc.message}")


def _written(store: QuoteStore, revision: QuoteRevision) -> WorkbenchTestQuoteSummary:
    [summary] = [quote for quote in store.quotes() if quote.id == revision.quote_id]
    return _summary(summary)


@router.get("/test-quotes", response_model=WorkbenchTestQuotesResponse)
async def list_test_quotes() -> WorkbenchTestQuotesResponse:
    """The project's test quotes, the most recently saved first."""
    config = await _enabled_config()
    quotes = await run_in_threadpool(
        _read_test_quotes, config, lambda store: [_summary(q) for q in store.quotes()]
    )
    return WorkbenchTestQuotesResponse(quotes=quotes or [])


@router.get("/test-quotes/branch", response_model=WorkbenchTestQuotesBranchResponse)
async def test_quotes_branch_now() -> WorkbenchTestQuotesBranchResponse:
    """The branch the test quotes are on now, which a save names; the file is not opened."""
    from haute._test_quotes import test_quotes_branch

    config = await _enabled_config()
    branch = await run_in_threadpool(test_quotes_branch, config.project_root)
    return WorkbenchTestQuotesBranchResponse(branch=branch)


@router.post("/test-quotes", response_model=WorkbenchTestQuoteWriteResponse)
async def save_test_quote(body: WorkbenchTestQuoteSaveRequest) -> WorkbenchTestQuoteWriteResponse:
    """The quote on the sheets, saved as a new test quote with its pricing's response."""
    from haute._quote_store import QuoteStoreError
    from haute._test_quotes import quote_request, response_document

    config = await _enabled_config()
    request = quote_request(body.form, body.values)
    response = response_document(body.form, body.tables)

    def write(store: QuoteStore, saved_by: str) -> WorkbenchTestQuoteSummary:
        revision = store.create(
            name=body.name,
            owner=saved_by,
            saved_by=saved_by,
            form=body.form,
            inputs=body.values,
            request=request,
            response=response,
            release=None,
        )
        return _written(store, revision)

    try:
        async with save_lock:
            quote, written = await run_in_threadpool(
                lambda: _write_test_quotes(config, write, create=True, branch=body.branch)
            )
    except QuoteStoreError as exc:
        raise _store_error(exc) from None
    return written.model_copy(update={"quote": quote})


@router.post("/test-quotes/capture", response_model=WorkbenchTestQuoteWriteResponse)
async def capture_test_quotes() -> WorkbenchTestQuoteWriteResponse:
    """The test-quote file captured on the save ledger as it is, nothing written to it: the
    retry of a write whose capture was skipped or failed. Nothing to capture without a file."""
    config = await _enabled_config()

    def capture() -> WorkbenchTestQuoteWriteResponse:
        with repository_mutation(config.project_root):
            path, relative = _test_quotes_file(config)
            if not path.is_file():
                return WorkbenchTestQuoteWriteResponse()
            return _capture_test_quotes(config, relative)

    async with save_lock:
        return await run_in_threadpool(capture)


@router.post("/test-quotes/{quote_id}/open", response_model=WorkbenchTestQuoteOpenResponse)
async def open_test_quote(
    quote_id: str, body: WorkbenchTestQuoteOpenRequest
) -> WorkbenchTestQuoteOpenResponse:
    """A test quote's newest revision, its values carried onto the view's form."""
    from haute._quote_store import QuoteNotFoundError, QuoteStoreError, carry_values

    config = await _enabled_config()

    def read(store: QuoteStore) -> WorkbenchTestQuoteOpenResponse:
        revision = store.revision(quote_id)
        [summary] = [quote for quote in store.quotes() if quote.id == quote_id]
        carried = carry_values(revision.inputs, store.form(revision.form_revision), body.form)
        return WorkbenchTestQuoteOpenResponse(
            id=quote_id,
            name=summary.name,
            number=revision.number,
            revision_digest=revision.digest,
            values=carried.values,
            dropped=carried.dropped,
            response=revision.response,
        )

    try:
        opened = await run_in_threadpool(_read_test_quotes, config, read)
    except QuoteStoreError as exc:
        raise _store_error(exc) from None
    if opened is None:
        raise _store_error(QuoteNotFoundError(f"The quote store holds no quote {quote_id!r}."))
    return opened


@router.post("/test-quotes/{quote_id}/compare", response_model=WorkbenchTestQuoteCompareResponse)
async def compare_test_quote(
    quote_id: str, body: WorkbenchTestQuoteCompareRequest
) -> WorkbenchTestQuoteCompareResponse:
    """Every place a run's response differs from the one revision ``number`` accepted, and
    every place the request the run priced differs from the one that revision keeps, which a
    deploy prices: a column renamed since asks for an accept, so the two price alike."""
    from haute._quote_store import QuoteNotFoundError, QuoteStoreError, StaleQuoteError
    from haute._test_quotes import ACCEPTED_TOLERANCE, quote_request, response_document
    from haute.deploy._validators import response_changes

    config = await _enabled_config()
    priced = response_document(body.form, body.tables)
    request = quote_request(body.form, body.values)
    try:
        revision = await run_in_threadpool(
            _read_test_quotes, config, lambda store: store.revision(quote_id, body.number)
        )
    except QuoteStoreError as exc:
        raise _store_error(exc) from None
    if revision is None:
        raise _store_error(QuoteNotFoundError(f"The quote store holds no quote {quote_id!r}."))
    if revision.digest != body.revision_digest:
        raise _store_error(
            StaleQuoteError(
                f"Quote {quote_id!r} revision {body.number} is not the one the run opened: it "
                "was saved apart, on another branch. Run it again."
            )
        )
    changes = [
        *response_changes(priced, revision.response or {}, ACCEPTED_TOLERANCE),
        *(
            change._replace(path=f"request.{change.path}")
            for change in response_changes(request, revision.request, ACCEPTED_TOLERANCE)
        ),
    ]
    return WorkbenchTestQuoteCompareResponse(
        number=revision.number,
        revision_digest=revision.digest,
        changes=[WorkbenchTestQuoteChange(**change._asdict()) for change in changes],
    )


@router.post("/test-quotes/{quote_id}/accept", response_model=WorkbenchTestQuoteWriteResponse)
async def accept_test_quote(
    quote_id: str, body: WorkbenchTestQuoteAcceptRequest
) -> WorkbenchTestQuoteWriteResponse:
    """A run's response accepted as the test quote's revision after ``after``."""
    from haute._quote_store import QuoteStoreError
    from haute._test_quotes import quote_request, response_document

    config = await _enabled_config()
    request = quote_request(body.form, body.values)
    response = response_document(body.form, body.tables)

    def write(store: QuoteStore, saved_by: str) -> WorkbenchTestQuoteSummary:
        revision = store.save_revision(
            quote_id,
            after=body.after,
            after_digest=body.revision_digest,
            saved_by=saved_by,
            form=body.form,
            inputs=body.values,
            request=request,
            response=response,
            release=None,
        )
        return _written(store, revision)

    try:
        async with save_lock:
            quote, written = await run_in_threadpool(_write_test_quotes, config, write)
    except QuoteStoreError as exc:
        raise _store_error(exc) from None
    return written.model_copy(update={"quote": quote})


@router.delete("/test-quotes/{quote_id}", response_model=WorkbenchTestQuoteWriteResponse)
async def delete_test_quote(quote_id: str) -> WorkbenchTestQuoteWriteResponse:
    """A test quote and its revisions, removed."""
    from haute._quote_store import QuoteStoreError

    config = await _enabled_config()
    try:
        async with save_lock:
            _, written = await run_in_threadpool(
                _write_test_quotes, config, lambda store, _saved_by: store.delete(quote_id)
            )
    except QuoteStoreError as exc:
        raise _store_error(exc) from None
    return written
