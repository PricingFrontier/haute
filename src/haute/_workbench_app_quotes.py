"""The underwriter app's quotes (specs/workbench): its routes to list, open, save and change
the quotes it keeps in its quote store, mounted by ``haute._workbench_app.create_app``, each
taking the open book from the store's gate (``haute._workbench_app_store``) as it starts and
answering 503 with the refusal while the quotes are closed.

A quote is named by its reference and option, ``/api/app/quotes/{reference}/{option}``, the
reference URL-encoded. Its details are the row of the sheets' ``quote_details`` table: the
app sets the reference, the stage, who the quote is with and the rest of
``QUOTE_DETAILS_MANAGED`` over whatever a page sends, before the quote is priced. A creation
and a save are first looked up by their operation key, so one repeated after its answer was
lost is answered with what it wrote; then they name the sheets' revision and the release the
page was drawn for. A creation makes the quote unpriced; a save either saves it unpriced or
prices it again here, keeping this pricing and saying whether it moved from what the page
showed. Every other change names the version of the quote it was made on. The store's
refusals answer as the app's: a quote it does not keep 404; a quote moved on 409 with the
quote as it is now; a quote that does not take the change, one not ready to be marked final,
a repeated save superseded and a reference held 409; values not of their columns' types, and
anything else it refuses, 422. What the database itself does is said by
``haute._failure_answers``: a store the app cannot reach, or that refuses its sign-in, 503,
without what the driver said of where it is, closing the quotes; a connection lost part-way
through a write 503, to be sent again; a SQLite file another program holds or that is
read-only 409, the quotes left open; and a statement the database refuses for its own
reason 422 with an incident under which its message is logged, after the gate checks the
tables again at once, 503 when they no longer fit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from haute._failure_answers import answer_failure
from haute._json_safe import to_json_safe
from haute._logging import get_logger
from haute._quote_store import (
    AppQuote,
    OpenQuote,
    PricedOutputs,
    QuoteBook,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteNotReadyError,
    QuoteStoreChangedError,
    QuoteStoreError,
    QuoteValuesError,
    ReferenceTakenError,
    RelatedQuote,
    Source,
    StaleQuoteError,
    StoreUse,
    WriteSupersededError,
    as_written,
    identity_label,
    input_rows,
)
from haute._quote_store._book import MAX_PAGE_SIZE, PAGE_SIZE
from haute._workbench_app import Pricing
from haute._workbench_app_editing import EDIT_LEASE_SECONDS, EditLeases
from haute._workbench_app_store import QuoteStoreClosedError, StoreGate
from haute._workbench_export import (
    XLSX_MEDIA_TYPE,
    ExportRefusedError,
    attachment,
    quote_export,
)
from haute._workbench_form import QUOTE_DETAILS_MANAGED, QUOTE_DETAILS_NAME, FormSpec
from haute._workbench_tables import (
    IDENTITY_SHAPE,
    carries_identity,
    output_tables,
    quote_request,
)
from haute.deploy._request_limits import (
    RequestBodyHeaderError,
    RequestBodyLimitError,
    RequestBodyParseError,
    deploy_quote_request_body_limit_bytes,
    read_limited_json_body,
)
from haute.deploy._validators import response_changes
from haute.errors import (
    ConflictError,
    ForbiddenError,
    InvalidRequestError,
    RequestRefusedError,
    UnprocessableError,
)
from haute.schemas import (
    WorkbenchAppEditingResponse,
    WorkbenchAppPeopleResponse,
    WorkbenchAppQuote,
    WorkbenchAppQuoteActivityEntry,
    WorkbenchAppQuoteActivityResponse,
    WorkbenchAppQuoteAmend,
    WorkbenchAppQuoteAssign,
    WorkbenchAppQuoteChange,
    WorkbenchAppQuoteChangeResponse,
    WorkbenchAppQuoteCreateRequest,
    WorkbenchAppQuoteExport,
    WorkbenchAppQuoteNote,
    WorkbenchAppQuoteResponse,
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

#: Prices a quote's values through the release's pipeline, or raises an HTTP error saying
#: why not.
Pricer = Callable[[WorkbenchValues], Awaitable[Pricing]]

#: The quote_details cells the app sets, by table and column name, and the quote's name, which
#: a save keeps as the quote was made (:meth:`QuoteBook.managed`): no typing.
_APP_SET_CELLS = frozenset(
    (QUOTE_DETAILS_NAME, column_name) for column_name in (*QUOTE_DETAILS_MANAGED, "quote_name")
)


@dataclass(frozen=True)
class QuoteApp:
    """What the quote routes work with: the release running, its sheets, the gate of its
    quote store, the release's pricing and who is editing which quote."""

    release_id: str
    form_revision: str
    form: FormSpec
    gate: StoreGate
    price: Pricer
    editing: EditLeases = field(default_factory=EditLeases)


def _iso(moment: Any) -> str | None:
    return None if moment is None else moment.isoformat()


def quote_model(quote: AppQuote) -> WorkbenchAppQuote:
    """An app quote as the routes answer it."""
    source = quote.source
    return WorkbenchAppQuote(
        reference=quote.reference,
        option=quote.option,
        label=quote.label,
        title=quote.title,
        details=quote.details,
        stage=quote.stage,
        assignee=quote.assignee,
        archived=quote.archived,
        version=quote.version,
        saved_at=quote.saved_at.isoformat(),
        saved_by=quote.saved_by,
        priced=quote.priced,
        priced_release=quote.priced_release,
        final_by=quote.final_by,
        final_at=_iso(quote.final_at),
        archived_by=quote.archived_by,
        archived_at=_iso(quote.archived_at),
        note=quote.note,
        note_by=quote.note_by,
        note_at=_iso(quote.note_at),
        source_reference=None if source is None else source.reference,
        source_option=None if source is None else source.option,
        source_label=None if source is None else identity_label(source.reference, source.option),
        relation=None if source is None else source.relation,
    )


logger = get_logger(component="workbench.app.quotes")


def _refusal(status: int, exc: QuoteStoreError, **extra: Any) -> RequestRefusedError:
    return RequestRefusedError(
        {"message": exc.message or str(exc), "error_code": exc.error_code, **extra},
        status_code=status,
    )


async def _book(app: QuoteApp, use: StoreUse = "quote") -> QuoteBook:
    """The open book for *use*, from the gate on a worker thread, as it may check the store;
    a store that cannot be used for it answered 503 with the gate's refusal."""
    try:
        return await run_in_threadpool(app.gate.book, use)
    except QuoteStoreClosedError as refusal:
        raise RequestRefusedError(refusal.detail, status_code=503) from None


async def _call(
    app: QuoteApp,
    work: Callable[[QuoteBook], T],
    identity: tuple[str, int] | None = None,
    use: StoreUse = "quote",
) -> T:
    """*work* on the book open for *use* (:func:`_answered`): the Quotes page's list, or one
    quote. A store whose tables changed under the running app is checked again at once and,
    while the tables *use* needs fit, *work* done once more on the store as it opens now, as
    nothing was written; answered 503 when they no longer fit, or changed again meanwhile."""
    book = await _book(app, use)
    try:
        return await _answered(app, work, book, identity, use)
    except QuoteStoreChangedError:
        refusal = await run_in_threadpool(app.gate.recheck, book, use)
        if refusal is not None:
            raise RequestRefusedError(refusal.detail, status_code=503) from None
    try:
        return await _answered(app, work, await _book(app, use), identity, use)
    except QuoteStoreChangedError as exc:
        raise _refusal(503, exc) from None


async def _answered(
    app: QuoteApp,
    work: Callable[[QuoteBook], T],
    book: QuoteBook,
    identity: tuple[str, int] | None,
    use: StoreUse,
) -> T:
    """*work* on *book*, on a worker thread, the store's refusals answered as the app's; a
    quote moved on with the quote *identity* names as it is now; a
    :class:`QuoteStoreChangedError` raised on, for :func:`_call` to check the store again."""
    try:
        return await run_in_threadpool(work, book)
    except QuoteStoreChangedError:
        raise
    except QuoteNotFoundError as exc:
        raise _refusal(404, exc) from None
    except StaleQuoteError as exc:
        current = None
        if identity is not None:
            now = await _book(app)
            current = quote_model(await run_in_threadpool(now.get, *identity)).model_dump()
        raise _refusal(409, exc, quote=current) from None
    except QuoteNotReadyError as exc:
        raise _refusal(409, exc, missing=exc.missing) from None
    except WriteSupersededError as exc:
        raise _refusal(
            409,
            exc,
            landed_at=exc.landed_at.isoformat(),
            changed_by=exc.changed_by,
            changed_at=exc.changed_at.isoformat(),
        ) from None
    except QuoteLockedError as exc:
        raise _refusal(409, exc) from None
    except ReferenceTakenError as exc:
        raise _refusal(
            409, exc, reference=exc.reference, option=exc.option, label=exc.label
        ) from None
    except QuoteValuesError as exc:
        cells = [{"table": c.table, "row": c.row, "column": c.column} for c in exc.cells]
        raise _refusal(422, exc, cells=cells) from None
    except QuoteStoreError as exc:
        raise _refusal(422, exc) from None
    except Exception as exc:
        # The store's database said in words (haute._failure_answers): a store it cannot
        # reach, a connection lost part-way, a file another program holds or that is
        # read-only, a statement it refused for its own reason; anything else an incident.
        answer = answer_failure(exc, operation="workbench_app_quote")
        if answer.code == "quote_store_unreachable":
            # The quotes close until a check reaches the store again. On a worker thread:
            # the gate's lock may be held by a check still waiting.
            await run_in_threadpool(app.gate.unreachable)
        elif answer.code == "quote_store_refused":
            # A statement refused: the tables may have changed under the running app. The
            # book it was refused on is named, so a check another request made since stands.
            refusal = await run_in_threadpool(app.gate.recheck, book, use)
            if refusal is not None:
                raise RequestRefusedError(refusal.detail, status_code=503) from None
        raise answer.refusal() from None


def _being_edited(reference: str, option: int, editor: str) -> RequestRefusedError:
    return RequestRefusedError(
        {
            "message": f"{editor} is editing {identity_label(reference, option)}.",
            "error_code": "quote_being_edited",
            "editor": editor,
        },
        status_code=409,
    )


def _not_edited_by_another(app: QuoteApp, reference: str, option: int, actor: str) -> None:
    """Refused (409) while anyone but *actor* is editing the quote: only its editor changes
    it, and anyone may while no one is editing it."""
    editor = app.editing.other((reference, option), actor)
    if editor is not None:
        raise _being_edited(reference, option, editor)


def _editor(http: Request) -> str:
    """The principal changing a quote; refused (403) when they may only read quotes."""
    if not http.state.can_edit:
        raise ForbiddenError(
            {
                "message": "You can read quotes in this app; changing them needs its edit role.",
                "error_code": "quote_read_only",
            }
        )
    principal: str = http.state.principal
    return principal


async def app_body(
    http: Request,
    model: type[M],
    *,
    operation: str = "workbench_app_quote",
    limit_bytes: int | None = None,
) -> M:
    """The request's body as *model*, read within *limit_bytes*, else the deployed quote
    route's limit, before it is parsed: 413 past it, 400 for a malformed length, 422 for a
    body that is not JSON or not *model*."""
    try:
        raw = await read_limited_json_body(
            http,
            operation=operation,
            limit_bytes=deploy_quote_request_body_limit_bytes()
            if limit_bytes is None
            else limit_bytes,
        )
    except RequestBodyLimitError as exc:
        raise RequestRefusedError(exc.to_payload(), status_code=413) from None
    except RequestBodyHeaderError as exc:
        raise InvalidRequestError(exc.to_payload()) from None
    except RequestBodyParseError as exc:
        raise UnprocessableError(exc.to_payload()) from None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


def _drawn_for(
    app: QuoteApp, body: WorkbenchAppQuoteCreateRequest | WorkbenchAppQuoteSaveRequest
) -> None:
    """Refuse (409) a write from a page drawn for another release or other sheets."""
    if body.form_revision != app.form_revision or body.release != app.release_id:
        raise ConflictError(
            "This page was drawn for another release of the app: reload it to save on this one."
        )


def _moved(
    form: FormSpec, kept: WorkbenchPricedTables | None, shown: WorkbenchPricedTables | None
) -> bool:
    """Whether the prices *kept*, a save's, moved from those the page *shown*. The quote's
    reference and option are no price: a page priced under another quote's has not moved."""
    if shown is None or kept is None:
        return False
    carrying = {table.name for table in output_tables(form) if carries_identity(table)}
    return bool(
        response_changes(
            _without_identity(kept, carrying), _without_identity(shown, carrying), PRICE_TOLERANCE
        )
    )


def _without_identity(tables: Any, carrying: set[str]) -> Any:
    """*tables* by name, the rows of those in *carrying* without the quote's identity."""
    if not isinstance(tables, dict):
        return tables
    names = {name for name, _ in IDENTITY_SHAPE}

    def bare(row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        return {column: value for column, value in row.items() if column not in names}

    return {
        name: ([bare(row) for row in rows] if isinstance(rows, list) else bare(rows))
        if name in carrying
        else rows
        for name, rows in tables.items()
    }


def _related(quote: RelatedQuote) -> WorkbenchAppRelatedQuote:
    return WorkbenchAppRelatedQuote(
        reference=quote.reference, option=quote.option, label=quote.label, relation=quote.relation
    )


def quote_router(app: QuoteApp) -> APIRouter:
    """The routes of the app's quotes, under ``/api/app``."""
    router = APIRouter(prefix="/api/app")

    def answer(
        opened: OpenQuote, shown: WorkbenchPricedTables | None = None
    ) -> WorkbenchAppQuoteResponse:
        """*opened* as the routes answer it, with the request its values price as and, for a
        save, whether its prices moved from those the page *shown*."""
        return WorkbenchAppQuoteResponse(
            quote=quote_model(opened.quote),
            values=opened.values,
            tables=opened.outputs,
            request=to_json_safe(quote_request(app.form, opened.values)),
            dropped=opened.dropped,
            moved=_moved(app.form, opened.outputs, shown),
        )

    @router.get("/quotes", response_model=WorkbenchAppQuotesResponse)
    async def list_quotes(
        http: Request,
        view: Literal["mine", "team", "archived"] = "mine",
        stage: Literal["draft", "final"] | None = None,
        search: str = Query(default="", max_length=200),
        assignee: str | None = Query(default=None, max_length=320),
        sort: Literal["saved", "reference", "insured"] = "saved",
        cursor: str | None = Query(default=None, max_length=2000),
        limit: int = Query(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> WorkbenchAppQuotesResponse:
        actor: str = http.state.principal
        page = await _call(
            app,
            lambda book: book.page(
                actor=actor,
                view=view,
                stage=stage,
                search=search,
                assignee=assignee,
                sort=sort,
                cursor=cursor,
                limit=limit,
            ),
            use="list",
        )
        return WorkbenchAppQuotesResponse(
            quotes=[quote_model(quote) for quote in page.quotes],
            next_cursor=page.next_cursor,
            counts=page.counts,
        )

    @router.post("/quotes", response_model=WorkbenchAppQuoteResponse)
    async def create_quote(http: Request) -> WorkbenchAppQuoteResponse:
        actor = _editor(http)
        body = await app_body(http, WorkbenchAppQuoteCreateRequest)
        # A creation repeated with its key answers the quote it made, whichever release the
        # page was drawn for.
        replay = await _call(app, lambda book: book.replayed(body.operation, actor, "created"))
        if replay is not None:
            return answer(replay)
        _drawn_for(app, body)
        source = (
            None
            if body.source is None
            else Source(body.source.reference, body.source.option, body.source.relation)
        )
        made = await _call(
            app,
            lambda book: book.create(
                operation=body.operation,
                actor=actor,
                values=body.values,
                at=book.now(),
                reference=body.reference,
                source=source,
            ),
        )
        return answer(made)

    @router.get("/quotes/{reference}/{option}", response_model=WorkbenchAppQuoteResponse)
    async def open_quote(reference: str, option: int) -> WorkbenchAppQuoteResponse:
        return answer(await _call(app, lambda book: book.read(reference, option)))

    @router.post("/quotes/{reference}/{option}/save", response_model=WorkbenchAppQuoteResponse)
    async def save_quote(reference: str, option: int, http: Request) -> WorkbenchAppQuoteResponse:
        actor = _editor(http)
        body = await app_body(http, WorkbenchAppQuoteSaveRequest)
        book = await _book(app)
        identity = (reference, option)
        # A save repeated with its key after its answer was lost is answered with what that key
        # wrote, nothing priced or written again, whichever release the page was drawn for.
        replay = await _call(
            app,
            lambda book: book.replayed(body.operation, actor, "saved", reference, option),
            identity,
        )
        if replay is not None:
            return answer(replay, body.shown)
        _drawn_for(app, body)
        _not_edited_by_another(app, reference, option, actor)
        current = await _call(app, lambda book: book.get(reference, option))
        # One instant stamps the save: the request priced and the rows written.
        at = book.now()
        values = as_written(body.values, book.managed(current, at=at, actor=actor))
        # The details the app sets show on the sheets as any value does, but are no typing.
        if not quote_request(app.form, values, untyped=_APP_SET_CELLS):
            raise UnprocessableError("Type the quote first")
        priced = None
        if body.shown is not None:
            # A value not of its column's type refuses the save before it is priced, naming each.
            await _call(app, lambda _book: input_rows(app.form, values, identity))
            pricing = await app.price(values)
            priced = PricedOutputs(app.release_id, pricing.document)
        saved = await _call(
            app,
            lambda book: book.save(
                reference,
                option,
                operation=body.operation,
                version=body.version,
                actor=actor,
                values=body.values,
                priced=priced,
                at=at,
            ),
            identity,
        )
        return answer(saved, body.shown)

    def change(
        path: str,
        model: type[WorkbenchAppQuoteChange],
        apply: Callable[[QuoteBook, str, int, Any, str], AppQuote],
    ) -> None:
        async def route(
            reference: str, option: int, http: Request
        ) -> WorkbenchAppQuoteChangeResponse:
            actor = _editor(http)
            body = await app_body(http, model)
            _not_edited_by_another(app, reference, option, actor)
            quote = await _call(
                app, lambda book: apply(book, reference, option, body, actor), (reference, option)
            )
            return WorkbenchAppQuoteChangeResponse(quote=quote_model(quote))

        router.add_api_route(
            f"/quotes/{{reference}}/{{option}}/{path}",
            route,
            methods=["POST"],
            response_model=WorkbenchAppQuoteChangeResponse,
            name=f"quote_{path}",
        )

    change(
        "assignee",
        WorkbenchAppQuoteAssign,
        lambda book, r, o, b, a: book.assign(r, o, version=b.version, actor=a, assignee=b.assignee),
    )
    change(
        "archive",
        WorkbenchAppQuoteChange,
        lambda book, r, o, b, a: book.archive(r, o, version=b.version, actor=a),
    )
    change(
        "restore",
        WorkbenchAppQuoteChange,
        lambda book, r, o, b, a: book.restore(r, o, version=b.version, actor=a),
    )
    change(
        "final",
        WorkbenchAppQuoteChange,
        lambda book, r, o, b, a: book.finalise(
            r, o, version=b.version, actor=a, release=app.release_id
        ),
    )
    change(
        "amend",
        WorkbenchAppQuoteAmend,
        lambda book, r, o, b, a: book.amend(r, o, version=b.version, actor=a, reason=b.reason),
    )

    @router.post(
        "/quotes/{reference}/{option}/export",
        response_class=Response,
        responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "The workbook."}},
    )
    async def export(reference: str, option: int, http: Request) -> Response:
        """A final quote's Export to an Excel workbook of cells, run on its values and prices
        as the store keeps them and recorded in its activity: the workbook, named by the
        Export's file name. Anyone signed in may export, as anyone may read the quote."""
        actor: str = http.state.principal
        body = await app_body(http, WorkbenchAppQuoteExport)
        identity = (reference, option)
        opened = await _call(app, lambda book: book.read(reference, option), identity)
        quote = opened.quote
        if quote.stage != "final" or quote.priced_release is None:
            raise RequestRefusedError(
                {
                    "message": f"Mark {quote.label} final before exporting it.",
                    "error_code": "quote_not_ready",
                },
                status_code=409,
            )
        release = quote.priced_release
        try:
            file_name, data = await run_in_threadpool(
                quote_export, app.form, body.component, opened.values, opened.outputs or {}, release
            )
        except ExportRefusedError as exc:
            raise UnprocessableError(exc.message) from None
        await _call(
            app,
            lambda book: book.record_export(reference, option, version=body.version, actor=actor),
            identity,
        )
        return Response(
            content=data,
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": attachment(file_name)},
        )

    @router.post(
        "/quotes/{reference}/{option}/note", response_model=WorkbenchAppQuoteChangeResponse
    )
    async def note(reference: str, option: int, http: Request) -> WorkbenchAppQuoteChangeResponse:
        actor = _editor(http)
        body = await app_body(http, WorkbenchAppQuoteNote)
        _not_edited_by_another(app, reference, option, actor)
        quote = await _call(
            app, lambda book: book.set_note(reference, option, actor=actor, note=body.note)
        )
        return WorkbenchAppQuoteChangeResponse(quote=quote_model(quote))

    @router.get(
        "/quotes/{reference}/{option}/activity", response_model=WorkbenchAppQuoteActivityResponse
    )
    async def activity(reference: str, option: int) -> WorkbenchAppQuoteActivityResponse:
        done = await _call(app, lambda book: book.activity(reference, option))
        return WorkbenchAppQuoteActivityResponse(
            entries=[
                WorkbenchAppQuoteActivityEntry(
                    action=entry.action,
                    actor=entry.actor,
                    at=entry.at.isoformat(),
                    version=entry.version,
                    assignee=entry.assignee,
                    note=entry.note,
                    reason=entry.reason,
                    priced_release=entry.priced_release,
                )
                for entry in done.entries
            ],
            source=None if done.source is None else _related(done.source),
            derived=[_related(quote) for quote in done.derived],
        )

    @router.post("/quotes/{reference}/{option}/edit", response_model=WorkbenchAppEditingResponse)
    async def take_editing(
        reference: str, option: int, http: Request
    ) -> WorkbenchAppEditingResponse:
        """The quote held to edit by whoever asks, taken or renewed; refused (409) naming who
        else is editing it, and 404 when the app keeps no such quote."""
        actor = _editor(http)
        await _call(app, lambda book: book.get(reference, option), (reference, option))
        editor = app.editing.take((reference, option), actor)
        if editor is not None:
            raise _being_edited(reference, option, editor)
        return WorkbenchAppEditingResponse(editor=actor, lease_seconds=EDIT_LEASE_SECONDS)

    @router.post(
        "/quotes/{reference}/{option}/edit/release", response_model=WorkbenchAppEditingResponse
    )
    async def release_editing(
        reference: str, option: int, http: Request
    ) -> WorkbenchAppEditingResponse:
        """The quote let go by whoever asks, when they hold it, as their page leaves it."""
        principal: str = http.state.principal
        app.editing.release((reference, option), principal)
        return WorkbenchAppEditingResponse(editor=None, lease_seconds=EDIT_LEASE_SECONDS)

    @router.get("/people", response_model=WorkbenchAppPeopleResponse)
    async def people() -> WorkbenchAppPeopleResponse:
        return WorkbenchAppPeopleResponse(
            people=await _call(app, lambda book: book.people(), use="list")
        )

    return router
