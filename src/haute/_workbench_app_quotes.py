"""The underwriter app's quotes (specs/workbench): its routes to list, open, save and change
the quotes it keeps in its quote store, mounted by ``haute._workbench_app.create_app`` when
the app has a store.

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
anything else it refuses, 422. A store the app cannot reach, or that refuses its sign-in, 503,
said and logged without what the driver said of where it is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

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
    QuoteStoreError,
    QuoteValuesError,
    ReferenceTakenError,
    RelatedQuote,
    Source,
    StaleQuoteError,
    WriteSupersededError,
    as_written,
    identity_label,
    input_rows,
)
from haute._quote_store._book import MAX_PAGE_SIZE, PAGE_SIZE
from haute._quote_store._store import unreached
from haute._workbench_app import Pricing
from haute._workbench_form import QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, FormSpec
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
from haute.schemas import (
    WorkbenchAppPeopleResponse,
    WorkbenchAppQuote,
    WorkbenchAppQuoteActivityEntry,
    WorkbenchAppQuoteActivityResponse,
    WorkbenchAppQuoteAmend,
    WorkbenchAppQuoteAssign,
    WorkbenchAppQuoteChange,
    WorkbenchAppQuoteChangeResponse,
    WorkbenchAppQuoteCreateRequest,
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

#: The quote_details cells the app sets, by table and column id: no typing.
_APP_SET_CELLS = frozenset((QUOTE_DETAILS_ID, column_id) for column_id in QUOTE_DETAILS_MANAGED)


@dataclass(frozen=True)
class QuoteApp:
    """What the quote routes work with: the release running, its sheets, the book of quotes
    and the release's pricing."""

    release_id: str
    form_revision: str
    form: FormSpec
    book: QuoteBook
    price: Pricer


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


def _refusal(status: int, exc: QuoteStoreError, **extra: Any) -> HTTPException:
    return HTTPException(
        status, detail={"message": exc.message or str(exc), "error_code": exc.error_code, **extra}
    )


async def _call(app: QuoteApp, work: Callable[[], T], identity: tuple[str, int] | None = None) -> T:
    """*work* on a worker thread, the store's refusals answered as the app's; a quote moved on
    with the quote *identity* names as it is now."""
    try:
        return await run_in_threadpool(work)
    except QuoteNotFoundError as exc:
        raise _refusal(404, exc) from None
    except StaleQuoteError as exc:
        current = None
        if identity is not None:
            current = quote_model(await run_in_threadpool(app.book.get, *identity)).model_dump()
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
        if not unreached(exc):
            raise
        # The driver's own text names the server, its port and who signs in: none of it said.
        logger.warning("quote_store_unreachable", error_type=type(exc).__name__)
        raise HTTPException(
            503,
            detail={
                "message": "The app cannot reach its quote store just now: try again shortly.",
                "error_code": "quote_store_unreachable",
            },
        ) from None


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


def _drawn_for(
    app: QuoteApp, body: WorkbenchAppQuoteCreateRequest | WorkbenchAppQuoteSaveRequest
) -> None:
    """Refuse (409) a write from a page drawn for another release or other sheets."""
    if body.form_revision != app.form_revision or body.release != app.release_id:
        raise HTTPException(
            409,
            "This page was drawn for another release of the app: reload it to save on this one.",
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
    book = app.book
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
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteCreateRequest)
        # A creation repeated with its key answers the quote it made, whichever release the
        # page was drawn for.
        replay = await _call(app, lambda: book.replayed(body.operation, actor, "created"))
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
            lambda: book.create(
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
        return answer(await _call(app, lambda: book.read(reference, option)))

    @router.post("/quotes/{reference}/{option}/save", response_model=WorkbenchAppQuoteResponse)
    async def save_quote(reference: str, option: int, http: Request) -> WorkbenchAppQuoteResponse:
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteSaveRequest)
        identity = (reference, option)
        # A save repeated with its key after its answer was lost is answered with what that key
        # wrote, nothing priced or written again, whichever release the page was drawn for.
        replay = await _call(
            app, lambda: book.replayed(body.operation, actor, "saved", reference, option), identity
        )
        if replay is not None:
            return answer(replay, body.shown)
        _drawn_for(app, body)
        current = await _call(app, lambda: book.get(reference, option))
        # One instant stamps the save: the request priced and the rows written.
        at = book.now()
        values = as_written(body.values, book.managed(current, at=at, actor=actor))
        # The details the app sets show on the sheets as any value does, but are no typing.
        if not quote_request(app.form, values, untyped=_APP_SET_CELLS):
            raise HTTPException(422, "Type the quote first")
        priced = None
        if body.shown is not None:
            # A value not of its column's type refuses the save before it is priced, naming each.
            await _call(app, lambda: input_rows(app.form, values, identity))
            pricing = await app.price(values)
            priced = PricedOutputs(app.release_id, pricing.document)
        saved = await _call(
            app,
            lambda: book.save(
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
        apply: Callable[[str, int, Any, str], AppQuote],
    ) -> None:
        async def route(
            reference: str, option: int, http: Request
        ) -> WorkbenchAppQuoteChangeResponse:
            actor = _editor(http)
            body = await _body(http, model)
            quote = await _call(
                app, lambda: apply(reference, option, body, actor), (reference, option)
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
        lambda r, o, b, a: book.assign(r, o, version=b.version, actor=a, assignee=b.assignee),
    )
    change(
        "archive",
        WorkbenchAppQuoteChange,
        lambda r, o, b, a: book.archive(r, o, version=b.version, actor=a),
    )
    change(
        "restore",
        WorkbenchAppQuoteChange,
        lambda r, o, b, a: book.restore(r, o, version=b.version, actor=a),
    )
    change(
        "final",
        WorkbenchAppQuoteChange,
        lambda r, o, b, a: book.finalise(r, o, version=b.version, actor=a, release=app.release_id),
    )
    change(
        "amend",
        WorkbenchAppQuoteAmend,
        lambda r, o, b, a: book.amend(r, o, version=b.version, actor=a, reason=b.reason),
    )

    @router.post(
        "/quotes/{reference}/{option}/note", response_model=WorkbenchAppQuoteChangeResponse
    )
    async def note(reference: str, option: int, http: Request) -> WorkbenchAppQuoteChangeResponse:
        actor = _editor(http)
        body = await _body(http, WorkbenchAppQuoteNote)
        quote = await _call(
            app, lambda: book.set_note(reference, option, actor=actor, note=body.note)
        )
        return WorkbenchAppQuoteChangeResponse(quote=quote_model(quote))

    @router.get(
        "/quotes/{reference}/{option}/activity", response_model=WorkbenchAppQuoteActivityResponse
    )
    async def activity(reference: str, option: int) -> WorkbenchAppQuoteActivityResponse:
        done = await _call(app, lambda: book.activity(reference, option))
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

    @router.get("/people", response_model=WorkbenchAppPeopleResponse)
    async def people() -> WorkbenchAppPeopleResponse:
        return WorkbenchAppPeopleResponse(people=await _call(app, book.people))

    return router
