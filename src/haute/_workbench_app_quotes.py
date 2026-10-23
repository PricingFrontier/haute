"""The underwriter app's quotes (specs/workbench): its routes to list, open, save and change
the quotes it keeps in its quote store, mounted by ``haute._workbench_app.create_app`` when
the app has a store.

A quote's details are the row of the sheets' ``quote_details`` table, which the app keeps in
the store's typed table: the app sets the reference, the stage, who the quote is with and
whether it is archived, over whatever a page sends, before the quote is priced. A save names
the sheets' revision and the release the page was drawn for, and either saves the quote
unpriced or prices it again here, keeping this pricing and saying whether it moved from what
the page showed. Every change names the revision of the quote it was made on. The store's
refusals answer as the app's: a quote it does not keep 404, a quote moved on 409 with the
quote as it is now, a quote that does not take the change 409, anything else it refuses 422.
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
    details_of,
    open_store,
    with_details,
)
from haute._quote_store._book import MAX_PAGE_SIZE, PAGE_SIZE
from haute._workbench_form import QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, FormSpec
from haute._workbench_tables import quote_request
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

#: A kept price and the one the page showed are matched within one part in a billion, so
#: a pricing that differs only in a float's last bits has not moved.
PRICE_TOLERANCE = 1e-9

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


def open_book(url: str) -> QuoteBook:
    """The book of the store at *url*, which must be migrated."""
    return QuoteBook(open_store(url))


def _iso(moment: Any) -> str | None:
    return None if moment is None else moment.isoformat()


def quote_model(quote: AppQuote) -> WorkbenchAppQuote:
    """An app quote as the routes answer it."""
    return WorkbenchAppQuote(
        id=quote.id,
        reference=quote.reference,
        title=quote.title,
        details=quote.details,
        stage=quote.stage,
        assignee=quote.assignee,
        archived=quote.archived,
        owner=quote.owner,
        created_at=quote.created_at.isoformat(),
        latest=quote.latest,
        release=quote.release,
        form_revision=quote.form_revision,
        priced=quote.priced,
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


def _editor(http: Request) -> str:
    """The principal changing a quote; refused (403) when they may only read quotes."""
    if not http.state.can_edit:
        raise HTTPException(
            403,
            detail={
                "message": "You can read quotes in this app; changing them needs its edit role.",
                "error_code": "quote_read_only",
            },
        )
    principal: str = http.state.principal
    return principal


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


@dataclass(frozen=True)
class _Prepared:
    """A save as it is kept: the values with the app's details over them, the request priced
    and its prices (none unpriced)."""

    values: WorkbenchValues
    request: dict[str, Any]
    tables: WorkbenchPricedTables | None


async def _prepared(
    app: QuoteApp, body: WorkbenchAppQuoteSaveRequest, managed: dict[str, Any]
) -> _Prepared:
    """*body*'s save made ready: refused (409) for another sheets' revision or release; its
    details' app columns set to *managed*; refused (422) with nothing typed; unpriced, or
    priced again here."""
    if body.form_revision != app.form_revision or body.release != app.release_id:
        raise HTTPException(
            409,
            "This page was drawn for another release of the app: reload it to save on this one.",
        )
    # The details the app sets are its own, whatever the page sent; and nothing the
    # underwriter typed is nothing to save.
    details = {
        name: value for name, value in details_of(body.values).items() if name not in managed
    }
    typed = with_details(body.values, details)
    if not quote_request(app.form, typed):
        raise HTTPException(422, "Type the quote first")
    values = with_details(typed, {**details, **managed})
    if body.shown is None:
        return _Prepared(values, quote_request(app.form, values), None)
    tables, request = await app.price(values)
    return _Prepared(values, request, tables)


def _moved(revision: QuoteRevision, shown: WorkbenchPricedTables | None) -> bool:
    """Whether the prices *revision* keeps moved from those the page *shown*: a save's, or,
    for a save repeated with its key, those its first write kept."""
    if shown is None or revision.response is None:
        return False
    return bool(response_changes(revision.response, shown, PRICE_TOLERANCE))


def _without_app_details(values: dict[str, Any]) -> dict[str, Any]:
    """*values* without the quote_details columns the app sets (`QUOTE_DETAILS_MANAGED`): a
    reference, stage, assignee and archived flag belong to the quote that has them."""
    rows = values.get(QUOTE_DETAILS_ID)
    if rows is None:
        return values
    typed = [
        {column: value for column, value in row.items() if column not in QUOTE_DETAILS_MANAGED}
        for row in rows
    ]
    return {**values, QUOTE_DETAILS_ID: typed}


def quote_router(app: QuoteApp) -> APIRouter:
    """The routes of the app's quotes, under ``/api/app``."""
    book = app.book
    router = APIRouter(prefix="/api/app")

    def answer(
        quote: AppQuote, revision: QuoteRevision, *, moved: bool = False
    ) -> WorkbenchAppQuoteResponse:
        """*quote* with *revision*, the sheets it was keyed into when not the release's."""
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
        sort: Literal["saved", "reference", "insured"] = "saved",
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

    def saved(quote: AppQuote, shown: WorkbenchPricedTables | None) -> WorkbenchAppQuoteResponse:
        """A save's answer: *quote* as the save left it, with the revision it wrote, read by
        its number, and whether its prices moved from those the page *shown*."""
        revision = book.revision(quote.id, quote.latest)
        return answer(quote, revision, moved=_moved(revision, shown))

    async def replayed(
        body: WorkbenchAppQuoteSaveRequest, actor: str, quote_id: str | None
    ) -> WorkbenchAppQuoteResponse | None:
        """A save repeated with its key after its answer was lost: what that key wrote, with
        nothing priced or written again; None for a key not written."""

        def read() -> WorkbenchAppQuoteResponse | None:
            quote = book.replayed(body.operation, actor, quote_id)
            return None if quote is None else saved(quote, body.shown)

        return await _call(app, read, quote_id)

    @router.post("/quotes", response_model=WorkbenchAppQuoteResponse)
    async def create_quote(http: Request) -> WorkbenchAppQuoteResponse:
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteCreateRequest)
        replay = await replayed(body, actor, None)
        if replay is not None:
            return replay
        managed = {**QuoteBook.managed(None, priced=body.shown is not None), "assignee": actor}
        ready = await _prepared(app, body, managed)
        source = (
            None
            if body.source is None
            else Source(body.source.quote_id, body.source.revision, body.source.relation)
        )

        def create() -> WorkbenchAppQuoteResponse:
            quote = book.create(
                operation=body.operation,
                actor=actor,
                form=app.form,
                inputs=ready.values,
                request=ready.request,
                response=ready.tables,
                release=None if ready.tables is None else app.release_id,
                source=source,
            )
            return saved(quote, body.shown)

        return await _call(app, create)

    @router.get("/quotes/{quote_id}", response_model=WorkbenchAppQuoteResponse)
    async def open_quote(quote_id: str) -> WorkbenchAppQuoteResponse:
        def read() -> WorkbenchAppQuoteResponse:
            quote = book.get(quote_id)
            return answer(quote, book.revision(quote_id, quote.latest))

        return await _call(app, read)

    @router.get("/quotes/{quote_id}/revisions/{number}", response_model=WorkbenchAppQuoteResponse)
    async def open_revision(quote_id: str, number: int) -> WorkbenchAppQuoteResponse:
        def read() -> WorkbenchAppQuoteResponse:
            return answer(book.get(quote_id), book.revision(quote_id, number))

        return await _call(app, read)

    @router.get(
        "/quotes/{quote_id}/revisions/{number}/carry", response_model=WorkbenchAppCarryResponse
    )
    async def carry(quote_id: str, number: int, details: bool = False) -> WorkbenchAppCarryResponse:
        """Revision *number*'s values on this release's sheets: for a quote made from it,
        without the details the app sets, which are the source's and not the new quote's;
        with them only when *details* asks, the same quote carried onto newer sheets."""

        def read() -> WorkbenchAppCarryResponse:
            revision = book.revision(quote_id, number)
            saved = book.store.form(revision.form_revision)
            carried = carry_values(revision.inputs, saved, app.form)
            values = carried.values if details else _without_app_details(carried.values)
            return WorkbenchAppCarryResponse(values=values, dropped=carried.dropped)

        return await _call(app, read)

    @router.post("/quotes/{quote_id}/revisions", response_model=WorkbenchAppQuoteResponse)
    async def save_quote(quote_id: str, http: Request) -> WorkbenchAppQuoteResponse:
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteReviseRequest)
        replay = await replayed(body, actor, quote_id)
        if replay is not None:
            return replay
        current = await _call(app, lambda: book.get(quote_id))
        ready = await _prepared(
            app, body, QuoteBook.managed(current, priced=body.shown is not None)
        )

        def save() -> WorkbenchAppQuoteResponse:
            quote = book.save(
                quote_id,
                operation=body.operation,
                version=body.version,
                actor=actor,
                form=app.form,
                inputs=ready.values,
                request=ready.request,
                response=ready.tables,
                release=None if ready.tables is None else app.release_id,
            )
            return saved(quote, body.shown)

        return await _call(app, save, quote_id)

    def change(
        path: str,
        model: type[WorkbenchAppQuoteChange],
        apply: Callable[[str, Any, str], AppQuote],
    ) -> None:
        async def route(quote_id: str, http: Request) -> WorkbenchAppQuoteChangeResponse:
            actor = _editor(http)
            body = await _body(http, model)

            def made() -> WorkbenchAppQuoteChangeResponse:
                quote = apply(quote_id, body, actor)
                revision = book.revision(quote_id, quote.latest)
                return WorkbenchAppQuoteChangeResponse(
                    quote=quote_model(quote), revision=_revision_model(revision)
                )

            return await _call(app, made, quote_id)

        router.add_api_route(
            f"/quotes/{{quote_id}}/{path}",
            route,
            methods=["POST"],
            response_model=WorkbenchAppQuoteChangeResponse,
            name=f"quote_{path}",
        )

    change(
        "assignee",
        WorkbenchAppQuoteAssign,
        lambda q, b, a: book.assign(q, version=b.version, actor=a, assignee=b.assignee),
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

    @router.post("/quotes/{quote_id}/note", response_model=WorkbenchAppQuoteChangeResponse)
    async def note(quote_id: str, http: Request) -> WorkbenchAppQuoteChangeResponse:
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteNote)
        quote = await _call(app, lambda: book.set_note(quote_id, actor=actor, note=body.note))
        return WorkbenchAppQuoteChangeResponse(quote=quote_model(quote))

    @router.get("/quotes/{quote_id}/history", response_model=WorkbenchAppQuoteHistoryResponse)
    async def history(quote_id: str) -> WorkbenchAppQuoteHistoryResponse:
        timeline = await _call(app, lambda: book.timeline(quote_id))

        def related(each: Any) -> WorkbenchAppRelatedQuote:
            return WorkbenchAppRelatedQuote(
                id=each.id, reference=each.reference, relation=each.relation, revision=each.revision
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
                    stage=r.stage,
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
