"""The underwriter app's quotes (specs/workbench): the quotes it keeps in the store, each
revision's ``quote_details`` row held in the store's typed table, and the pages of quotes its
Quotes page lists.

A revision of an app quote keeps its values as the store keeps any, without the
``quote_details`` table, whose row it keeps in ``haute_quote_details`` alone: its reference
and option, name, insured, broker, dates and currency as typed, and its stage, who it is with
and whether it is archived, which the app sets. A quote is made with its reference and
option: a new quote, a duplicate and a renewal take the next reference and option 1, an
alternative its source's reference and the next option under it, and the quote is shown as
both, ``Q-000123/2``. Every change to the quote is a revision written
whole, in one transaction with its event of who did what: a save writes the values and
prices; assigning, archiving, restoring, marking final and reopening write a revision of the
newest's values, request and prices with the details the change sets. Each change names the
revision it was made on and is refused, with nothing written, when the quote has moved past
it.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    Connection,
    Integer,
    RowMapping,
    Select,
    and_,
    func,
    insert,
    literal,
    or_,
    select,
    union,
    update,
)
from sqlalchemy.exc import IntegrityError

from haute._quote_store._store import (
    QuoteNotFoundError,
    QuoteRevision,
    QuoteStore,
    QuoteStoreError,
    StaleQuoteError,
    Values,
    _read_revision,
)
from haute._quote_store._tables import (
    NAME_LENGTH,
    OPERATION_LENGTH,
    PRINCIPAL_LENGTH,
    QUOTE_COUNTER,
    QUOTE_DETAILS,
    QUOTE_EVENTS,
    QUOTE_OPERATIONS,
    QUOTE_OPTIONS,
    QUOTE_REVISIONS,
    QUOTES,
)
from haute._workbench_form import QUOTE_DETAILS_COLUMNS, QUOTE_DETAILS_ID, FormSpec

#: A quote's stage: saved without a price on its newest revision, saved priced, or final.
Stage = Literal["draft", "priced", "final"]
#: How a quote was made from another.
Relation = Literal["duplicate", "renewal", "alternative"]
#: The lists of the Quotes page: the signed-in underwriter's, everyone's, the archive.
View = Literal["mine", "team", "archived"]
#: The orders a list is read in: the newest saved first, the newest reference first, or by
#: insured.
Sort = Literal["saved", "reference", "insured"]
#: What an event records.
EventKind = Literal[
    "created",
    "saved",
    "assigned",
    "noted",
    "archived",
    "restored",
    "finalised",
    "reopened",
    "derived",
]

#: A reference is this and the counter's number in six digits, or more past a million.
REFERENCE_PREFIX = "Q-"
#: The quotes one page holds, unless asked for fewer; and the most it holds.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
#: The people an assignee is picked from, at most.
MAX_PEOPLE = 500
#: The details' column ids by name.
DETAIL_IDS: dict[str, str] = {name: column_id for column_id, name, _ in QUOTE_DETAILS_COLUMNS}
_DATES = frozenset(name for _, name, kind in QUOTE_DETAILS_COLUMNS if kind == "date")
_WHOLE = frozenset(name for _, name, kind in QUOTE_DETAILS_COLUMNS if kind == "int")
_SEARCHED = ("reference", "quote_name", "insured", "broker")
#: A word of a search naming a reference and an option, as a quote is shown: ``Q-000123/2``.
_REFERENCE_OPTION = re.compile(r"(.+)/(\d+)")
_OPERATION = re.compile(rf"[A-Za-z0-9_-]{{8,{OPERATION_LENGTH}}}")
_COUNTER_SCOPE = "quotes"


class QuoteLockedError(QuoteStoreError):
    """The quote does not take this change as it stands: final, archived, or not ready to be
    finalised. The message says what would let it."""

    error_code = "quote_locked"


@dataclass(frozen=True, slots=True)
class Source:
    """The quote and revision a new quote is made from, and how."""

    quote_id: str
    revision: int
    relation: Relation


@dataclass(frozen=True, slots=True)
class AppQuote:
    """An app quote as its newest revision holds it. The fields from ``final_revision`` on are
    read from its events by :meth:`QuoteBook.get` alone; a list leaves them empty."""

    id: str
    reference: str
    #: Which of the quotes offered under its reference it is, from 1.
    option: int
    #: The ``quote_details`` row, by column name: dates as ISO text, blanks left out.
    details: dict[str, Any]
    stage: Stage
    assignee: str
    archived: bool
    #: Who made it, and when.
    owner: str
    created_at: datetime
    #: The newest revision: its number, which a change names, the release that priced it and
    #: the sheets it was keyed into.
    latest: int
    release: str | None
    form_revision: str
    priced: bool
    last_saved_by: str
    last_saved_at: datetime
    final_revision: int | None = None
    final_by: str | None = None
    final_at: datetime | None = None
    archived_by: str | None = None
    archived_at: datetime | None = None
    note: str = ""
    note_by: str | None = None
    note_at: datetime | None = None
    source_quote_id: str | None = None
    source_reference: str | None = None
    source_option: int | None = None
    source_revision: int | None = None
    relation: Relation | None = None

    @property
    def label(self) -> str:
        """The quote as the app shows it: its reference and option, ``Q-000123/2``."""
        return identity_label(self.reference, self.option)

    @property
    def title(self) -> str:
        """What a list shows as the quote: its name, else its insured, else its label."""
        for name in ("quote_name", "insured"):
            value = self.details.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self.label


def identity_label(reference: str, option: int) -> str:
    """A quote's reference and option as the app shows them: ``Q-000123/2``."""
    return f"{reference}/{option}"


@dataclass(frozen=True, slots=True)
class QuotePage:
    """One page of a list, the cursor of the next when there is one, and the counts the
    Quotes page shows: each list's quotes, and each stage's in the list asked for."""

    quotes: list[AppQuote]
    next_cursor: str | None
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    """Who did what to a quote, and when; *detail* says what changed."""

    id: int
    at: datetime
    actor: str
    kind: EventKind
    revision: int | None
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RevisionSummary:
    """A revision as a quote's history lists it."""

    number: int
    saved_by: str
    saved_at: datetime
    priced: bool
    release: str | None
    form_revision: str
    stage: Stage


@dataclass(frozen=True, slots=True)
class RelatedQuote:
    """A quote this one was made from, or one made from it."""

    id: str
    reference: str
    option: int
    relation: Relation
    #: The revision of the quote made from that it was made from.
    revision: int


@dataclass(frozen=True, slots=True)
class QuoteTimeline:
    """A quote's history: its revisions, the events, the quote it was made from and those
    made from it."""

    revisions: list[RevisionSummary]
    events: list[QuoteEvent]
    source: RelatedQuote | None
    derived: list[RelatedQuote]


def details_of(values: Values) -> dict[str, Any]:
    """The ``quote_details`` row of *values*, by column name, blanks left out: the option,
    held as text as a cell is, read back as the whole number it is."""
    rows = values.get(QUOTE_DETAILS_ID) or []
    row = rows[0] if rows else {}
    names = {column_id: name for name, column_id in DETAIL_IDS.items()}
    return {
        names[key]: int(value) if names[key] in _WHOLE and isinstance(value, str) else value
        for key, value in row.items()
        if key in names and value is not None and value != ""
    }


def with_details(values: Values, details: Mapping[str, Any]) -> Values:
    """*values* with their ``quote_details`` row set to *details* (by column name), the
    blanks left out and the row with them when none is left; every other table as it was."""
    row = {
        DETAIL_IDS[name]: _cell(value)
        for name, value in details.items()
        if name in DETAIL_IDS and value is not None and value != ""
    }
    merged = {table: rows for table, rows in values.items() if table != QUOTE_DETAILS_ID}
    if row:
        merged[QUOTE_DETAILS_ID] = [row]
    return merged


def _cell(value: Any) -> Any:
    """A detail as the values hold a cell: a whole number, the option, as its text, as it
    would be typed; text and ticks as they are."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _stage_of(priced: bool) -> Stage:
    return "priced" if priced else "draft"


def _search_like(term: str) -> str:
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _check_operation(operation: str) -> None:
    if not _OPERATION.fullmatch(operation):
        raise QuoteStoreError(
            "A save's operation key is 8 to 64 letters, digits, dashes or underscores."
        )


def _check_principal(principal: str, what: str) -> str:
    value = principal.strip()
    if not value or len(value) > PRINCIPAL_LENGTH:
        raise QuoteStoreError(f"{what} is a name of 1 to {PRINCIPAL_LENGTH} characters.")
    return value


def _check_name(details: Mapping[str, Any]) -> None:
    """Refuse a quote name longer than the store keeps one, at any save, not only the first,
    which copies it into the quote's own row."""
    name = details.get("quote_name")
    if isinstance(name, str) and len(name) > NAME_LENGTH:
        raise QuoteStoreError(
            f"A quote's name is at most {NAME_LENGTH} characters; this one has {len(name)}."
        )


def _row_details(row: RowMapping) -> dict[str, Any]:
    """The ``quote_details`` row of a table row, by column name, dates as ISO text."""
    details: dict[str, Any] = {}
    for _, name, _ in QUOTE_DETAILS_COLUMNS:
        value = row[name]
        if value is None:
            continue
        details[name] = value.isoformat() if isinstance(value, date) else value
    return details


def _columns(details: Mapping[str, Any]) -> dict[str, Any]:
    """*details* as the typed table's columns: a date read from its ISO text, refused when
    it is not one; a column the row does not fill, null."""
    columns: dict[str, Any] = {}
    for _, name, _ in QUOTE_DETAILS_COLUMNS:
        value = details.get(name)
        if name in _WHOLE and isinstance(value, str):
            value = int(value)
        if name in _DATES and isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError as exc:
                raise QuoteStoreError(
                    f"{QUOTE_DETAILS_ID}.{name} is not a date (YYYY-MM-DD): {value!r}."
                ) from exc
        columns[name] = value
    return columns


def _quote(row: RowMapping) -> AppQuote:
    return AppQuote(
        id=row["quote_id"],
        reference=row["reference"],
        option=row["option"],
        details=_row_details(row),
        stage=row["stage"],
        assignee=row["assignee"],
        archived=bool(row["archived"]),
        owner=row["owner"],
        created_at=row["created_at"],
        latest=row["latest"],
        release=row["release"],
        form_revision=row["form_revision"],
        priced=bool(row["priced"]),
        last_saved_by=row["saved_by"],
        last_saved_at=row["saved_at"],
    )


def _newest(at: int | None = None) -> Select[Any]:
    """Each app quote's newest revision with its details: the quotes that have details, a
    quote the store saved alone having none. Given *at*, revision *at* in its place, as the
    quote stood when that revision was its newest: what a save repeated with its key
    answers."""
    revision = QUOTE_DETAILS.c.revision
    newest = QUOTES.c.latest == revision if at is None else revision == at
    latest = QUOTES.c.latest if at is None else literal(at, Integer).label("latest")
    return (
        select(
            QUOTE_DETAILS,
            QUOTES.c.owner,
            QUOTES.c.created_at,
            latest,
            QUOTE_REVISIONS.c.release,
            QUOTE_REVISIONS.c.form_revision,
            QUOTE_REVISIONS.c.saved_by,
            QUOTE_REVISIONS.c.saved_at,
            QUOTE_REVISIONS.c.response.is_not(None).label("priced"),
        )
        .join(QUOTES, (QUOTES.c.id == QUOTE_DETAILS.c.quote_id) & newest)
        .join(
            QUOTE_REVISIONS,
            (QUOTE_REVISIONS.c.quote_id == QUOTE_DETAILS.c.quote_id)
            & (QUOTE_REVISIONS.c.number == QUOTE_DETAILS.c.revision),
        )
    )


def _view_filter(view: View, actor: str) -> ColumnElement[bool]:
    if view == "archived":
        return QUOTE_DETAILS.c.archived.is_(True)
    live = QUOTE_DETAILS.c.archived.is_(False)
    return and_(live, QUOTE_DETAILS.c.assignee == actor) if view == "mine" else live


def _search_filter(term: str) -> ColumnElement[bool]:
    named = _REFERENCE_OPTION.fullmatch(term)
    if named is not None:
        reference = func.lower(QUOTE_DETAILS.c.reference)
        return and_(
            reference.like(_search_like(named.group(1)), escape="\\"),
            QUOTE_DETAILS.c.option == int(named.group(2)),
        )
    like = _search_like(term)
    return or_(
        *(
            func.lower(func.coalesce(QUOTE_DETAILS.c[name], "")).like(like, escape="\\")
            for name in _SEARCHED
        )
    )


def _sort_key(sort: Sort) -> Any:
    if sort == "saved":
        return QUOTE_REVISIONS.c.saved_at
    if sort == "reference":
        return QUOTE_DETAILS.c.reference
    return func.lower(func.coalesce(QUOTE_DETAILS.c.insured, ""))


def _encode_cursor(sort: Sort, row: RowMapping) -> str:
    """The cursor after *row*: its sort key as the database made it (``sort_key``), so the
    next page compares like with like whatever the engine's case rules, its option, which
    orders a reference's quotes, and its id."""
    key = row["sort_key"]
    if isinstance(key, datetime):
        key = key.isoformat()
    raw = json.dumps([sort, key, row["option"], row["quote_id"]]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _cursor_filter(sort: Sort, cursor: str) -> ColumnElement[bool]:
    try:
        named, key, option, quote_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if (
            named != sort
            or not isinstance(key, str)
            or not isinstance(option, int)
            or not isinstance(quote_id, str)
        ):
            raise ValueError(cursor)
        value: Any = datetime.fromisoformat(key) if sort == "saved" else key
    except (ValueError, TypeError) as exc:
        raise QuoteStoreError("The page asked for is not one this list gave.") from exc
    column = _sort_key(sort)
    quote = QUOTE_DETAILS.c.quote_id
    if sort == "insured":
        return or_(column > value, and_(column == value, quote > quote_id))
    if sort == "reference":
        # The newest reference first, and under it its options in order.
        later = or_(
            QUOTE_DETAILS.c.option > option,
            and_(QUOTE_DETAILS.c.option == option, quote < quote_id),
        )
        return or_(column < value, and_(column == value, later))
    return or_(column < value, and_(column == value, quote < quote_id))


def _order(sort: Sort) -> list[Any]:
    column = _sort_key(sort)
    if sort == "insured":
        return [column.asc(), QUOTE_DETAILS.c.quote_id.asc()]
    if sort == "reference":
        return [column.desc(), QUOTE_DETAILS.c.option.asc(), QUOTE_DETAILS.c.quote_id.desc()]
    return [column.desc(), QUOTE_DETAILS.c.quote_id.desc()]


def _option_of(detail: Mapping[str, Any]) -> int:
    """The option an event names a quote by; an event written before the store's fourth
    schema names none, every quote then being option 1."""
    option = detail.get("option", 1)
    if not isinstance(option, int):
        raise QuoteStoreError(f"An event names a quote's option as {option!r}.")
    return option


_DONE = {
    "created": "made",
    "saved": "saved",
    "assigned": "assigned",
    "noted": "gave a note to",
    "archived": "archived",
    "restored": "restored",
    "finalised": "marked final",
    "reopened": "reopened",
    "derived": "copied",
}


class QuoteBook:
    """The underwriter app's quotes on a store :func:`haute._quote_store.open_store` opened;
    closing the book closes the store."""

    def __init__(self, store: QuoteStore) -> None:
        self.store = store
        self._engine = store._engine
        self._clock = store._clock

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> QuoteBook:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── Reading ───────────────────────────────────────────────────────────

    def get(self, quote_id: str) -> AppQuote:
        """The app quote *quote_id*, with what its events say of it: who finalised and
        archived it and when, its note and the quote it was made from.
        :class:`QuoteNotFoundError` when the app keeps none."""
        with self._engine.connect() as connection:
            return self._full(connection, quote_id)

    def replayed(self, operation: str, actor: str, quote_id: str | None = None) -> AppQuote | None:
        """The quote as *operation*'s key left it, at the revision that key wrote, when it has
        been written: a save repeated after its answer was lost answers that, whatever was
        saved since. Refused when the key was another person's or another quote's."""
        _check_operation(operation)
        with self._engine.connect() as connection:
            return self._operation(connection, operation, actor, quote_id)

    def _full(
        self,
        connection: Connection,
        quote_id: str,
        at: int | None = None,
        until: int | None = None,
    ) -> AppQuote:
        """The quote with what its events say of it, read on *connection*: a write answers
        with the quote as its own transaction left it. Given *at* and *until*, as a write left
        it: at revision *at*, its events after event *until* (a note among them) left out."""
        quote = self._get(connection, quote_id, at)
        events = self._events(connection, quote_id)
        if until is not None:
            events = [event for event in events if event.id <= until]
        extras: dict[str, Any] = {}
        for event in events:
            if event.kind == "finalised" and quote.stage == "final":
                extras.update(
                    final_revision=event.revision, final_by=event.actor, final_at=event.at
                )
            elif event.kind == "archived" and quote.archived:
                extras.update(archived_by=event.actor, archived_at=event.at)
            elif event.kind == "noted":
                note = str(event.detail.get("note", ""))
                extras.update(
                    note=note,
                    note_by=event.actor if note else None,
                    note_at=event.at if note else None,
                )
            elif event.kind == "created" and isinstance(event.detail.get("source"), dict):
                source = event.detail["source"]
                extras.update(
                    source_quote_id=source["quote_id"],
                    source_reference=source["reference"],
                    source_option=_option_of(source),
                    source_revision=source["revision"],
                    relation=source["relation"],
                )
        return replace(quote, **extras)

    def _get(self, connection: Connection, quote_id: str, at: int | None = None) -> AppQuote:
        row = (
            connection.execute(_newest(at).where(QUOTE_DETAILS.c.quote_id == quote_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise QuoteNotFoundError(f"The app keeps no quote {quote_id!r}.")
        return _quote(row)

    def revision(self, quote_id: str, number: int | None = None) -> QuoteRevision:
        """Revision *number* of app quote *quote_id*, its newest when *number* is None, its
        values holding its ``quote_details`` row."""
        with self._engine.connect() as connection:
            revision = _read_revision(connection, quote_id, number)
            row = (
                connection.execute(
                    select(QUOTE_DETAILS).where(
                        QUOTE_DETAILS.c.quote_id == quote_id,
                        QUOTE_DETAILS.c.revision == revision.number,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise QuoteNotFoundError(f"The app keeps no quote {quote_id!r}.")
        return replace(revision, inputs=with_details(revision.inputs, _row_details(row)))

    def page(
        self,
        *,
        actor: str,
        view: View = "mine",
        stage: Stage | None = None,
        search: str = "",
        assignee: str | None = None,
        sort: Sort = "saved",
        cursor: str | None = None,
        limit: int = PAGE_SIZE,
    ) -> QuotePage:
        """A page of *view*'s quotes for *actor*, of *stage* alone when given, matching every
        word of *search* in the reference, name, insured or broker when given, with *assignee*
        when given, in *sort*'s order, from *cursor* (the page before's ``next_cursor``)."""
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise QuoteStoreError(f"A page holds 1 to {MAX_PAGE_SIZE} quotes.")
        where = [_view_filter(view, actor)]
        if assignee is not None:
            where.append(QUOTE_DETAILS.c.assignee == assignee)
        where.extend(_search_filter(term) for term in search.split())
        listed = list(where)
        if stage is not None:
            listed.append(QUOTE_DETAILS.c.stage == stage)
        if cursor is not None:
            listed.append(_cursor_filter(sort, cursor))
        query = (
            _newest()
            .add_columns(_sort_key(sort).label("sort_key"))
            .where(*listed)
            .order_by(*_order(sort))
            .limit(limit + 1)
        )
        views: tuple[View, ...] = ("mine", "team", "archived")
        stages: tuple[Stage, ...] = ("draft", "priced", "final")
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            counts: dict[str, int] = {
                name: self._count(connection, _view_filter(name, actor)) for name in views
            }
            for name in stages:
                counts[name] = self._count(connection, *where, QUOTE_DETAILS.c.stage == name)
        quotes = [_quote(row) for row in rows[:limit]]
        return QuotePage(
            quotes=quotes,
            next_cursor=_encode_cursor(sort, rows[limit - 1]) if len(rows) > limit else None,
            counts=counts,
        )

    @staticmethod
    def _count(connection: Connection, *where: ColumnElement[bool]) -> int:
        query = select(func.count()).select_from(_newest().where(*where).subquery())
        return int(connection.execute(query).scalar_one())

    def timeline(self, quote_id: str) -> QuoteTimeline:
        """Quote *quote_id*'s revisions with their stages, its events, the quote it was made
        from and those made from it."""
        with self._engine.connect() as connection:
            self._get(connection, quote_id)
            revisions = [
                RevisionSummary(
                    number=row["number"],
                    saved_by=row["saved_by"],
                    saved_at=row["saved_at"],
                    priced=bool(row["priced"]),
                    release=row["release"],
                    form_revision=row["form_revision"],
                    stage=row["stage"],
                )
                for row in connection.execute(
                    select(
                        QUOTE_REVISIONS.c.number,
                        QUOTE_REVISIONS.c.saved_by,
                        QUOTE_REVISIONS.c.saved_at,
                        QUOTE_REVISIONS.c.response.is_not(None).label("priced"),
                        QUOTE_REVISIONS.c.release,
                        QUOTE_REVISIONS.c.form_revision,
                        QUOTE_DETAILS.c.stage,
                    )
                    .join(
                        QUOTE_DETAILS,
                        (QUOTE_DETAILS.c.quote_id == QUOTE_REVISIONS.c.quote_id)
                        & (QUOTE_DETAILS.c.revision == QUOTE_REVISIONS.c.number),
                    )
                    .where(QUOTE_REVISIONS.c.quote_id == quote_id)
                    .order_by(QUOTE_REVISIONS.c.number)
                ).mappings()
            ]
            events = self._events(connection, quote_id)
        source = None
        derived: list[RelatedQuote] = []
        for event in events:
            made = event.detail.get("source") if event.kind == "created" else None
            if isinstance(made, dict):
                source = RelatedQuote(
                    id=made["quote_id"],
                    reference=made["reference"],
                    option=_option_of(made),
                    relation=made["relation"],
                    revision=made["revision"],
                )
            elif event.kind == "derived" and event.revision is not None:
                derived.append(
                    RelatedQuote(
                        id=event.detail["quote_id"],
                        reference=event.detail["reference"],
                        option=_option_of(event.detail),
                        relation=event.detail["relation"],
                        revision=event.revision,
                    )
                )
        return QuoteTimeline(revisions=revisions, events=events, source=source, derived=derived)

    def _events(self, connection: Connection, quote_id: str) -> list[QuoteEvent]:
        return [
            QuoteEvent(
                id=row["id"],
                at=row["at"],
                actor=row["actor"],
                kind=row["kind"],
                revision=row["revision"],
                detail=json.loads(row["detail"]),
            )
            for row in connection.execute(
                select(QUOTE_EVENTS)
                .where(QUOTE_EVENTS.c.quote_id == quote_id)
                .order_by(QUOTE_EVENTS.c.id)
            ).mappings()
        ]

    def people(self) -> list[str]:
        """Everyone a quote is with, or was saved by, in order; at most :data:`MAX_PEOPLE`."""
        named = union(
            select(QUOTE_DETAILS.c.assignee.label("who")),
            select(QUOTE_REVISIONS.c.saved_by.label("who")).join(
                QUOTE_DETAILS,
                (QUOTE_DETAILS.c.quote_id == QUOTE_REVISIONS.c.quote_id)
                & (QUOTE_DETAILS.c.revision == QUOTE_REVISIONS.c.number),
            ),
        ).subquery()
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    select(named.c.who).order_by(named.c.who).limit(MAX_PEOPLE)
                ).scalars()
            )

    # ── Saving ────────────────────────────────────────────────────────────

    @staticmethod
    def managed(quote: AppQuote | None, *, priced: bool) -> dict[str, Any]:
        """The details the app sets on a save of *quote* (None for a new quote) priced or not:
        its reference and option, who it is with and whether archived as it has them, and the
        stage the price gives. A new quote's reference and option are taken as it is
        written."""
        return {
            "reference": None if quote is None else quote.reference,
            "option": None if quote is None else quote.option,
            "assignee": None if quote is None else quote.assignee,
            "archived": False if quote is None else quote.archived,
            "stage": _stage_of(priced),
        }

    def create(
        self,
        *,
        operation: str,
        actor: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
        source: Source | None = None,
    ) -> AppQuote:
        """A new quote with its first revision, its reference and option, with *actor*, its
        details from *inputs* with the app's set over them; made from *source* when given: an
        alternative takes its source's reference and the next option under it, any other quote
        the next reference and option 1.
        Repeated with *operation*'s key, it answers the quote as that key made it and writes
        nothing."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who saves a quote")

        def write() -> AppQuote:
            return self._create(operation, actor, form, inputs, request, response, release, source)

        return self._or_replayed(write, operation, actor, None)

    def _create(
        self,
        operation: str,
        actor: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
        source: Source | None,
    ) -> AppQuote:
        with self._engine.begin() as connection:
            done = self._operation(connection, operation, actor, None)
            if done is not None:
                return done
            origin: AppQuote | None = None
            if source is not None:
                origin = self._get(connection, source.quote_id)
                _read_revision(connection, source.quote_id, source.revision)
            _check_name(details_of(inputs))
            if source is not None and origin is not None and source.relation == "alternative":
                reference = origin.reference
                option = self._next_option(connection, reference)
            else:
                reference = self._next_reference(connection)
                option = 1
                connection.execute(
                    insert(QUOTE_OPTIONS).values(reference=reference, last_option=option)
                )
            now = self._clock()
            details = {
                **details_of(inputs),
                **self.managed(None, priced=response is not None),
                "reference": reference,
                "option": option,
                "assignee": actor,
            }
            revision = self.store._create_in(
                connection,
                name=str(details.get("quote_name") or identity_label(reference, option)),
                owner=actor,
                saved_by=actor,
                saved_at=now,
                form=form,
                inputs=with_details(inputs, {}),
                request=request,
                response=response,
                release=release,
            )
            connection.execute(
                update(QUOTES)
                .where(QUOTES.c.id == revision.quote_id)
                .values(reference=reference, option=option)
            )
            self._write_details(connection, revision.quote_id, 1, details)
            created: dict[str, Any] = {"reference": reference, "option": option}
            if source is not None and origin is not None:
                created["source"] = {
                    "quote_id": origin.id,
                    "reference": origin.reference,
                    "option": origin.option,
                    "revision": source.revision,
                    "relation": source.relation,
                }
                made = {
                    "quote_id": revision.quote_id,
                    "reference": reference,
                    "option": option,
                    "relation": source.relation,
                }
                self._event(connection, origin.id, actor, "derived", source.revision, made, at=now)
            event = self._event(connection, revision.quote_id, actor, "created", 1, created, at=now)
            self._remember(connection, operation, revision.quote_id, 1, event, actor)
            return self._full(connection, revision.quote_id)

    def save(
        self,
        quote_id: str,
        *,
        operation: str,
        version: int,
        actor: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
    ) -> AppQuote:
        """The quote's next revision, saved over revision *version*: its values and details
        from *inputs*, with the app's set over them (:meth:`managed`). Refused with nothing
        written when the quote has moved past *version*, is final or is archived; repeated
        with *operation*'s key, it answers the quote as that key left it and writes nothing
        more."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who saves a quote")

        def write() -> AppQuote:
            return self._save(
                quote_id, operation, version, actor, form, inputs, request, response, release
            )

        return self._or_replayed(write, operation, actor, quote_id)

    def _save(
        self,
        quote_id: str,
        operation: str,
        version: int,
        actor: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
    ) -> AppQuote:
        with self._engine.begin() as connection:
            done = self._operation(connection, operation, actor, quote_id)
            if done is not None:
                return done
            quote = self._current(connection, quote_id, version)
            _check_name(details_of(inputs))
            if quote.archived:
                raise QuoteLockedError(f"{quote.label} is archived: restore it to change it.")
            if quote.stage == "final":
                raise QuoteLockedError(
                    f"{quote.label} is final: reopen it, giving a reason, to change it."
                )
            now = self._clock()
            revision = self.store._save_in(
                connection,
                quote_id,
                after=quote.latest,
                saved_by=actor,
                saved_at=now,
                form=form,
                inputs=with_details(inputs, {}),
                request=request,
                response=response,
                release=release,
            )
            details = {**details_of(inputs), **self.managed(quote, priced=response is not None)}
            self._write_details(connection, quote_id, revision.number, details)
            event = self._event(
                connection,
                quote_id,
                actor,
                "saved",
                revision.number,
                {"priced": response is not None, "release": release},
                at=now,
            )
            self._remember(connection, operation, quote_id, revision.number, event, actor)
            return self._full(connection, quote_id)

    # ── Changing ──────────────────────────────────────────────────────────

    def assign(self, quote_id: str, *, version: int, actor: str, assignee: str) -> AppQuote:
        """Put the quote with *assignee*: a revision of the newest with its assignee set."""
        assignee = _check_principal(assignee, "Who a quote is with")

        def change(quote: AppQuote) -> tuple[dict[str, Any], dict[str, Any]]:
            return {"assignee": assignee}, {"from": quote.assignee, "to": assignee}

        return self._revise(quote_id, version, actor, "assigned", change)

    def archive(self, quote_id: str, *, version: int, actor: str) -> AppQuote:
        """Take the quote out of the lists in use: an archived revision of the newest; it opens
        read only until restored."""
        return self._revise(
            quote_id, version, actor, "archived", lambda _quote: ({"archived": True}, {})
        )

    def restore(self, quote_id: str, *, version: int, actor: str) -> AppQuote:
        """Bring an archived quote back; a final one stays final."""

        def change(quote: AppQuote) -> tuple[dict[str, Any], dict[str, Any]]:
            if not quote.archived:
                raise QuoteLockedError(f"{quote.label} is not archived.")
            return {"archived": False}, {}

        return self._revise(quote_id, version, actor, "restored", change, archived_ok=True)

    def finalise(
        self, quote_id: str, *, version: int, actor: str, revision: int, release: str
    ) -> AppQuote:
        """Mark the quote final with a revision of *revision*, its newest: it must be priced,
        by *release*, the release running."""

        def change(quote: AppQuote) -> tuple[dict[str, Any], dict[str, Any]]:
            if quote.stage == "final":
                raise QuoteLockedError(f"{quote.label} is final already.")
            if revision != quote.latest:
                raise StaleQuoteError(
                    f"{quote.label} has moved on to revision {quote.latest} since revision "
                    f"{revision} was shown."
                )
            if not quote.priced:
                raise QuoteLockedError(
                    f"{quote.label}'s revision {revision} is not priced: price it and save "
                    "it before marking it final."
                )
            if quote.release != release:
                raise QuoteLockedError(
                    f"{quote.label}'s revision {revision} was priced on release "
                    f"{quote.release}, not this one, {release}: price it again and save it "
                    "before marking it final."
                )
            return {"stage": "final"}, {"from_revision": revision}

        return self._revise(quote_id, version, actor, "finalised", change)

    def reopen(self, quote_id: str, *, version: int, actor: str, reason: str) -> AppQuote:
        """Reopen a final quote for an amendment, giving *reason*: a revision of the newest at
        the stage its price gives; the final revision stays in its history."""
        reason = reason.strip()
        if not reason:
            raise QuoteStoreError("Give a reason for reopening a final quote.")

        def change(quote: AppQuote) -> tuple[dict[str, Any], dict[str, Any]]:
            if quote.stage != "final":
                raise QuoteLockedError(f"{quote.label} is not final.")
            stage = _stage_of(quote.priced)
            return {"stage": stage}, {"reason": reason, "final_revision": quote.final_revision}

        return self._revise(quote_id, version, actor, "reopened", change)

    def set_note(self, quote_id: str, *, actor: str, note: str) -> AppQuote:
        """Put *note*, the handover note, on the quote as an event; a blank one clears it. A note
        is no revision: nothing the quote is priced on changes."""
        actor = _check_principal(actor, "Who writes a note")
        note = note.strip()
        with self._engine.begin() as connection:
            self._hold(connection, quote_id)
            quote = self._get(connection, quote_id)
            if quote.archived:
                raise QuoteLockedError(f"{quote.label} is archived: restore it to change it.")
            self._event(
                connection, quote_id, actor, "noted", None, {"note": note}, at=self._clock()
            )
            return self._full(connection, quote_id)

    # ── Within a transaction ──────────────────────────────────────────────

    def _or_replayed(
        self,
        write: Callable[[], AppQuote],
        operation: str,
        actor: str,
        quote_id: str | None,
    ) -> AppQuote:
        """*write*, or, when it lost a race to the first write of the same key (refused as
        stale, or the key already taken, before that write was seen), what that write left."""
        try:
            return write()
        except (StaleQuoteError, IntegrityError):
            replay = self.replayed(operation, actor, quote_id)
            if replay is None:
                raise
            return replay

    def _revise(
        self,
        quote_id: str,
        version: int,
        actor: str,
        kind: EventKind,
        change: Callable[[AppQuote], tuple[dict[str, Any], dict[str, Any]]],
        *,
        archived_ok: bool = False,
    ) -> AppQuote:
        """A revision of the quote's newest, its values, request and prices kept, its details
        changed by *change*, with its event of *kind*, in one transaction. An archived quote
        is refused unless *archived_ok*, which leaves its state to *change* to judge."""
        actor = _check_principal(actor, "Who changes a quote")
        with self._engine.begin() as connection:
            quote = self._current(connection, quote_id, version)
            if not archived_ok and quote.archived:
                raise QuoteLockedError(f"{quote.label} is archived: restore it to change it.")
            changed, detail = change(quote)
            now = self._clock()
            kept = _read_revision(connection, quote_id, quote.latest)
            revision = self.store._copy_in(
                connection,
                quote_id,
                after=quote.latest,
                saved_by=actor,
                saved_at=now,
                inputs=kept.inputs,
            )
            details = {**quote.details, **changed}
            self._write_details(connection, quote_id, revision.number, details)
            self._event(connection, quote_id, actor, kind, revision.number, detail, at=now)
            return self._full(connection, quote_id)

    def _hold(self, connection: Connection, quote_id: str) -> None:
        """Hold the quote's row until the transaction ends, as `_advance`'s update of it holds
        it for every save and revision, so a write that is no revision, a note, serializes
        with them: what it reads cannot change before it writes. A no-op update takes the row's
        lock on every engine, and on SQLite the database's, which pysqlite takes only at a
        statement that writes, the reads before it being outside the transaction."""
        connection.execute(
            update(QUOTES).where(QUOTES.c.id == quote_id).values(latest=QUOTES.c.latest)
        )

    def _current(self, connection: Connection, quote_id: str, version: int) -> AppQuote:
        """The quote with what its events say of it, refused as stale unless its newest
        revision is *version*, naming who moved it on."""
        quote = self._full(connection, quote_id)
        if quote.latest != version:
            last = connection.execute(
                select(QUOTE_EVENTS.c.actor, QUOTE_EVENTS.c.kind, QUOTE_EVENTS.c.at)
                .where(
                    QUOTE_EVENTS.c.quote_id == quote_id,
                    QUOTE_EVENTS.c.revision == quote.latest,
                )
                .order_by(QUOTE_EVENTS.c.id.desc())
                .limit(1)
            ).one()
            raise StaleQuoteError(
                f"{quote.label} has changed since you opened it: {last.actor} "
                f"{_DONE[last.kind]} it at {last.at:%H:%M} UTC on {last.at:%d %b %Y}."
            )
        return quote

    def _write_details(
        self, connection: Connection, quote_id: str, revision: int, details: Mapping[str, Any]
    ) -> None:
        connection.execute(
            insert(QUOTE_DETAILS).values(quote_id=quote_id, revision=revision, **_columns(details))
        )

    def _event(
        self,
        connection: Connection,
        quote_id: str,
        actor: str,
        kind: EventKind,
        revision: int | None,
        detail: dict[str, Any],
        *,
        at: datetime,
    ) -> int:
        """Write an event; answer its id."""
        written = connection.execute(
            insert(QUOTE_EVENTS).values(
                quote_id=quote_id,
                at=at,
                actor=actor,
                kind=kind,
                revision=revision,
                detail=json.dumps(detail, ensure_ascii=False),
            )
        )
        key = written.inserted_primary_key
        if key is None:
            raise QuoteStoreError("The quote store did not number an event it wrote.")
        return int(key[0])

    def _next_reference(self, connection: Connection) -> str:
        """The next reference, taken from the counter in this transaction: two transactions
        take two, and one rolled back takes none."""
        moved = connection.execute(
            update(QUOTE_COUNTER)
            .where(QUOTE_COUNTER.c.scope == _COUNTER_SCOPE)
            .values(last_number=QUOTE_COUNTER.c.last_number + 1)
        )
        if moved.rowcount != 1:
            raise QuoteStoreError(
                "The quote store has no reference counter: it was not migrated by Haute."
            )
        number = connection.execute(
            select(QUOTE_COUNTER.c.last_number).where(QUOTE_COUNTER.c.scope == _COUNTER_SCOPE)
        ).scalar_one()
        return f"{REFERENCE_PREFIX}{number:06d}"

    def _next_option(self, connection: Connection, reference: str) -> int:
        """The next option under *reference*, taken from its counter in this transaction, as
        :meth:`_next_reference` takes a reference: two alternatives take two."""
        moved = connection.execute(
            update(QUOTE_OPTIONS)
            .where(QUOTE_OPTIONS.c.reference == reference)
            .values(last_option=QUOTE_OPTIONS.c.last_option + 1)
        )
        if moved.rowcount != 1:
            raise QuoteStoreError(
                f"The quote store counts no options under {reference}: it was not migrated by "
                "Haute."
            )
        return int(
            connection.execute(
                select(QUOTE_OPTIONS.c.last_option).where(QUOTE_OPTIONS.c.reference == reference)
            ).scalar_one()
        )

    def _operation(
        self, connection: Connection, operation: str, actor: str, quote_id: str | None
    ) -> AppQuote | None:
        """The quote as *operation*'s key left it, at the revision it wrote, when it was
        written: one repeated after its answer was lost. Refused when the key was another
        person's or another quote's."""
        row = (
            connection.execute(select(QUOTE_OPERATIONS).where(QUOTE_OPERATIONS.c.id == operation))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["actor"] != actor or (quote_id is not None and row["quote_id"] != quote_id):
            raise QuoteStoreError(
                "This save's operation key was used for another save: save again."
            )
        return self._full(connection, row["quote_id"], at=row["number"], until=row["event"])

    def _remember(
        self,
        connection: Connection,
        operation: str,
        quote_id: str,
        number: int,
        event: int,
        actor: str,
    ) -> None:
        connection.execute(
            insert(QUOTE_OPERATIONS).values(
                id=operation, quote_id=quote_id, number=number, event=event, actor=actor
            )
        )
