"""The underwriter app's quotes (specs/workbench): its routes to list, open, save and change
the quotes it keeps in its quote store, mounted by ``haute._workbench_app.create_app`` when
the app has a store.

A save names the sheets' revision and the release the page was drawn from, and either
saves the quote unpriced or prices it again here, keeping this pricing and saying whether it
moved from what the page showed. Every change names the version of the quote it was made on.
The store's refusals answer as the app's: a quote it does not keep 404, a quote moved on 409
with the quote as it is now, a quote that does not take the change 409, anything else it
refuses 422.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from haute._quote_store import (
    AppQuote,
    QuoteBook,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteRevision,
    QuoteStoreError,
    Source,
    StaleQuoteError,
    carry_values,
    open_store,
)
from haute._quote_store._book import MAX_PAGE_SIZE, PAGE_SIZE
from haute._test_quotes import ACCEPTED_TOLERANCE, quote_request
from haute._workbench_form import FormSpec
from haute._workbench_quote_list import project, projection_key
from haute.deploy._request_limits import (
    RequestBodyHeaderError,
    RequestBodyLimitError,
    RequestBodyParseError,
    deploy_quote_request_body_limit_bytes,
    read_limited_json_body,
)
from haute.deploy._validators import response_changes
from haute.schemas import (
    WorkbenchAppCarryResponse,
    WorkbenchAppPeopleResponse,
    WorkbenchAppQuote,
    WorkbenchAppQuoteAssign,
    WorkbenchAppQuoteChange,
    WorkbenchAppQuoteChangeResponse,
    WorkbenchAppQuoteCreateRequest,
    WorkbenchAppQuoteEvent,
    WorkbenchAppQuoteFinal,
    WorkbenchAppQuoteHistoryResponse,
    WorkbenchAppQuoteNote,
    WorkbenchAppQuoteRename,
    WorkbenchAppQuoteReopen,
    WorkbenchAppQuoteResponse,
    WorkbenchAppQuoteReviseRequest,
    WorkbenchAppQuoteRevision,
    WorkbenchAppQuoteRevisionSummary,
    WorkbenchAppQuoteSaveRequest,
    WorkbenchAppQuotesResponse,
    WorkbenchAppRelatedQuote,
    WorkbenchPricedTables,
    WorkbenchValues,
)

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)

#: Prices a quote's values through the release's pipeline: its output tables by name and
#: the request priced, or an HTTP error saying why not.
Pricer = Callable[[WorkbenchValues], Awaitable[tuple[WorkbenchPricedTables, dict[str, Any]]]]


@dataclass(frozen=True)
class QuoteApp:
    """What the quote routes work with: the release running, its sheets, the book of quotes
    and the release's pricing."""

    release_id: str
    form_revision: str
    form: FormSpec
    book: QuoteBook
    price: Pricer


def open_book(url: str, form: FormSpec) -> QuoteBook:
    """The book of the store at *url*, which must be migrated, with every quote projected by
    *form*'s quote list setting: those projected by another are projected again."""
    store = open_store(url)
    book = QuoteBook(store)
    forms: dict[str, FormSpec] = {}

    def again(revision: QuoteRevision) -> Any:
        if revision.form_revision not in forms:
            forms[revision.form_revision] = store.form(revision.form_revision)
        saved = forms[revision.form_revision]
        return project(form.quote_list, saved, revision.inputs, revision.response)

    try:
        book.reproject(projection_key(form.quote_list), again)
    except Exception:
        book.close()
        raise
    return book


def _iso(moment: Any) -> str | None:
    return None if moment is None else moment.isoformat()


def quote_model(quote: AppQuote) -> WorkbenchAppQuote:
    """An app quote as the routes answer it."""
    return WorkbenchAppQuote(
        id=quote.id,
        reference=quote.reference,
        name=quote.name,
        title=quote.title,
        title_value=quote.summary.get("title"),
        fields=list(quote.summary.get("fields", [])),
        headline=quote.summary.get("headline"),
        stage=quote.stage,
        assignee=quote.assignee,
        owner=quote.owner,
        created_at=quote.created_at.isoformat(),
        latest=quote.latest,
        release=quote.release,
        form_revision=quote.form_revision,
        last_saved_by=quote.last_saved_by,
        last_saved_at=quote.last_saved_at.isoformat(),
        final_revision=quote.final_revision,
        final_by=quote.final_by,
        final_at=_iso(quote.final_at),
        archived_by=quote.archived_by,
        archived_at=_iso(quote.archived_at),
        note=quote.note,
        note_by=quote.note_by,
        note_at=_iso(quote.note_at),
        source_quote_id=quote.source_quote_id,
        source_reference=quote.source_reference,
        source_revision=quote.source_revision,
        relation=quote.relation,
        version=quote.version,
    )


def _revision_model(revision: QuoteRevision) -> WorkbenchAppQuoteRevision:
    return WorkbenchAppQuoteRevision(
        number=revision.number,
        values=revision.inputs,
        tables=revision.response,
        request=revision.request,
        release=revision.release,
        form_revision=revision.form_revision,
        saved_by=revision.saved_by,
        saved_at=revision.saved_at.isoformat(),
    )


def _refusal(status: int, exc: QuoteStoreError, **extra: Any) -> HTTPException:
    return HTTPException(
        status, detail={"message": exc.message or str(exc), "error_code": exc.error_code, **extra}
    )


async def _call(app: QuoteApp, work: Callable[[], T], quote_id: str | None = None) -> T:
    """*work* on a worker thread, the store's refusals answered as the app's."""
    try:
        return await run_in_threadpool(work)
    except QuoteNotFoundError as exc:
        raise _refusal(404, exc) from None
    except StaleQuoteError as exc:
        current = None
        if quote_id is not None:
            current = quote_model(await run_in_threadpool(app.book.get, quote_id)).model_dump()
        raise _refusal(409, exc, quote=current) from None
    except QuoteLockedError as exc:
        raise _refusal(409, exc) from None
    except QuoteStoreError as exc:
        raise _refusal(422, exc) from None


async def _body(http: Request, model: type[M]) -> M:
    """The request's body, read within the deployed quote route's limit, as *model*."""
    try:
        raw = await read_limited_json_body(
            http,
            operation="workbench_app_quote",
            limit_bytes=deploy_quote_request_body_limit_bytes(),
        )
    except RequestBodyLimitError as exc:
        raise HTTPException(413, detail=exc.to_payload()) from None
    except RequestBodyHeaderError as exc:
        raise HTTPException(400, detail=exc.to_payload()) from None
    except RequestBodyParseError as exc:
        raise HTTPException(422, detail=exc.to_payload()) from None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


async def _priced_save(
    app: QuoteApp, body: WorkbenchAppQuoteSaveRequest
) -> tuple[dict[str, Any], WorkbenchPricedTables | None, bool]:
    """The request a save keeps, its prices (made again here when the page showed some;
    none for an unpriced save) and whether they moved from what the page showed."""
    if body.form_revision != app.form_revision or body.release != app.release_id:
        raise HTTPException(
            409,
            "This page was drawn for another release of the app: reload it to save on this one.",
        )
    if body.shown is None:
        request = quote_request(app.form, body.values)
        if not request:
            raise HTTPException(422, "Type the quote first")
        return request, None, False
    tables, request = await app.price(body.values)
    moved = bool(response_changes(tables, body.shown, ACCEPTED_TOLERANCE))
    return request, tables, moved


def quote_router(app: QuoteApp) -> APIRouter:
    """The routes of the app's quotes, under ``/api/app``."""
    book = app.book
    router = APIRouter(prefix="/api/app")

    def answer(
        quote: AppQuote, revision: QuoteRevision, *, moved: bool = False
    ) -> WorkbenchAppQuoteResponse:
        form = None
        if revision.form_revision != app.form_revision:
            form = book.store.form(revision.form_revision)
        return WorkbenchAppQuoteResponse(
            quote=quote_model(quote), revision=_revision_model(revision), form=form, moved=moved
        )

    @router.get("/quotes", response_model=WorkbenchAppQuotesResponse)
    async def list_quotes(
        http: Request,
        view: Literal["mine", "team", "archived"] = "mine",
        stage: Literal["draft", "priced", "final"] | None = None,
        search: str = Query(default="", max_length=200),
        assignee: str | None = Query(default=None, max_length=320),
        sort: Literal["saved", "reference", "title"] = "saved",
        cursor: str | None = Query(default=None, max_length=2000),
        limit: int = Query(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> WorkbenchAppQuotesResponse:
        actor: str = http.state.principal
        page = await _call(
            app,
            lambda: book.page(
                actor=actor,
                view=view,
                stage=stage,
                search=search,
                assignee=assignee,
                sort=sort,
                cursor=cursor,
                limit=limit,
            ),
        )
        return WorkbenchAppQuotesResponse(
            quotes=[quote_model(quote) for quote in page.quotes],
            next_cursor=page.next_cursor,
            counts=page.counts,
        )

    @router.post("/quotes", response_model=WorkbenchAppQuoteResponse)
    async def create_quote(http: Request) -> WorkbenchAppQuoteResponse:
        body = await _body(http, WorkbenchAppQuoteCreateRequest)
        request, tables, moved = await _priced_save(app, body)
        actor: str = http.state.principal
        source = (
            None
            if body.source is None
            else Source(body.source.quote_id, body.source.revision, body.source.relation)
        )

        def create() -> WorkbenchAppQuoteResponse:
            quote = book.create(
                operation=body.operation,
                actor=actor,
                name=body.name,
                form=app.form,
                inputs=body.values,
                request=request,
                response=tables,
                release=None if tables is None else app.release_id,
                projection=project(app.form.quote_list, app.form, body.values, tables),
                source=source,
            )
            return answer(quote, book.store.revision(quote.id, quote.latest), moved=moved)

        return await _call(app, create)

    @router.get("/quotes/{quote_id}", response_model=WorkbenchAppQuoteResponse)
    async def open_quote(quote_id: str) -> WorkbenchAppQuoteResponse:
        def read() -> WorkbenchAppQuoteResponse:
            quote = book.get(quote_id)
            return answer(quote, book.store.revision(quote_id, quote.latest))

        return await _call(app, read)

    @router.get("/quotes/{quote_id}/revisions/{number}", response_model=WorkbenchAppQuoteResponse)
    async def open_revision(quote_id: str, number: int) -> WorkbenchAppQuoteResponse:
        def read() -> WorkbenchAppQuoteResponse:
            return answer(book.get(quote_id), book.store.revision(quote_id, number))

        return await _call(app, read)

    @router.get(
        "/quotes/{quote_id}/revisions/{number}/carry", response_model=WorkbenchAppCarryResponse
    )
    async def carry(quote_id: str, number: int) -> WorkbenchAppCarryResponse:
        def read() -> WorkbenchAppCarryResponse:
            book.get(quote_id)
            revision = book.store.revision(quote_id, number)
            carried = carry_values(
                revision.inputs, book.store.form(revision.form_revision), app.form
            )
            return WorkbenchAppCarryResponse(values=carried.values, dropped=carried.dropped)

        return await _call(app, read)

    @router.post("/quotes/{quote_id}/revisions", response_model=WorkbenchAppQuoteResponse)
    async def save_quote(quote_id: str, http: Request) -> WorkbenchAppQuoteResponse:
        body = await _body(http, WorkbenchAppQuoteReviseRequest)
        request, tables, moved = await _priced_save(app, body)
        actor: str = http.state.principal

        def save() -> WorkbenchAppQuoteResponse:
            quote = book.save(
                quote_id,
                operation=body.operation,
                version=body.version,
                actor=actor,
                form=app.form,
                inputs=body.values,
                request=request,
                response=tables,
                release=None if tables is None else app.release_id,
                projection=project(app.form.quote_list, app.form, body.values, tables),
            )
            return answer(quote, book.store.revision(quote_id, quote.latest), moved=moved)

        return await _call(app, save, quote_id)

    def change(
        path: str,
        model: type[WorkbenchAppQuoteChange],
        apply: Callable[[str, Any, str], AppQuote],
    ) -> None:
        async def route(quote_id: str, http: Request) -> WorkbenchAppQuoteChangeResponse:
            body = await _body(http, model)
            actor: str = http.state.principal
            quote = await _call(app, lambda: apply(quote_id, body, actor), quote_id)
            return WorkbenchAppQuoteChangeResponse(quote=quote_model(quote))

        router.add_api_route(
            f"/quotes/{{quote_id}}/{path}",
            route,
            methods=["POST"],
            response_model=WorkbenchAppQuoteChangeResponse,
            name=f"quote_{path}",
        )

    change(
        "name",
        WorkbenchAppQuoteRename,
        lambda q, b, a: book.rename(q, version=b.version, actor=a, name=b.name),
    )
    change(
        "assignee",
        WorkbenchAppQuoteAssign,
        lambda q, b, a: book.assign(q, version=b.version, actor=a, assignee=b.assignee),
    )
    change(
        "note",
        WorkbenchAppQuoteNote,
        lambda q, b, a: book.set_note(q, version=b.version, actor=a, note=b.note),
    )
    change(
        "archive",
        WorkbenchAppQuoteChange,
        lambda q, b, a: book.archive(q, version=b.version, actor=a),
    )
    change(
        "restore",
        WorkbenchAppQuoteChange,
        lambda q, b, a: book.restore(q, version=b.version, actor=a),
    )
    change(
        "final",
        WorkbenchAppQuoteFinal,
        lambda q, b, a: book.finalise(
            q, version=b.version, actor=a, revision=b.revision, release=app.release_id
        ),
    )
    change(
        "reopen",
        WorkbenchAppQuoteReopen,
        lambda q, b, a: book.reopen(q, version=b.version, actor=a, reason=b.reason),
    )

    @router.get("/quotes/{quote_id}/history", response_model=WorkbenchAppQuoteHistoryResponse)
    async def history(quote_id: str) -> WorkbenchAppQuoteHistoryResponse:
        timeline = await _call(app, lambda: book.timeline(quote_id))
        related = lambda r: WorkbenchAppRelatedQuote(  # noqa: E731 - a one-line mapping
            id=r.id, reference=r.reference, title=r.title, relation=r.relation, revision=r.revision
        )
        return WorkbenchAppQuoteHistoryResponse(
            revisions=[
                WorkbenchAppQuoteRevisionSummary(
                    number=r.number,
                    saved_by=r.saved_by,
                    saved_at=r.saved_at.isoformat(),
                    priced=r.priced,
                    release=r.release,
                    form_revision=r.form_revision,
                )
                for r in timeline.revisions
            ],
            events=[
                WorkbenchAppQuoteEvent(
                    id=e.id,
                    at=e.at.isoformat(),
                    actor=e.actor,
                    kind=e.kind,
                    revision=e.revision,
                    detail=e.detail,
                )
                for e in timeline.events
            ],
            source=None if timeline.source is None else related(timeline.source),
            derived=[related(r) for r in timeline.derived],
        )

    @router.get("/people", response_model=WorkbenchAppPeopleResponse)
    async def people() -> WorkbenchAppPeopleResponse:
        return WorkbenchAppPeopleResponse(people=await _call(app, book.people))

    return router
