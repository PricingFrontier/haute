"""The underwriter app's quotes (specs/workbench): each quote's current state in the form's
own tables, what was done to it in ``haute_activity``, and the pages of quotes the Quotes page
lists.

A quote is identified by its reference and option. A new quote, a duplicate and a renewal take
the reference the underwriter keys and option 1; an alternative its source's reference and the
next option under it. A quote is ``draft``, which takes saves, or ``final``, which takes none
until it is amended back to draft. A save writes the quote's rows whole in one transaction: a
compare-and-swap on its ``quote_details`` row by its version, every other table's rows deleted
and inserted again, and its activity row. Each change names the version it was made on and is
refused, with nothing written, when the quote has moved on; each write carries an operation
key, bound to who sent it, the quote and what it does, so one repeated after its answer was
lost writes nothing more.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    Connection,
    Engine,
    Row,
    and_,
    delete,
    false,
    func,
    insert,
    or_,
    select,
    true,
    union,
    update,
)
from sqlalchemy.exc import IntegrityError

from haute._quote_store._rows import (
    PricedTables,
    Rows,
    Values,
    cells,
    input_rows,
    output_rows,
    rendered_outputs,
)
from haute._quote_store._schema import (
    NAME_LENGTH,
    OPERATION_LENGTH,
    PRINCIPAL_LENGTH,
    REFERENCE_LENGTH,
    StoreSchema,
)
from haute._quote_store._store import (
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteNotReadyError,
    QuoteStoreError,
    ReferenceTakenError,
    StaleQuoteError,
    WriteSupersededError,
    writing,
)
from haute._workbench_form import (
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_ID,
    QUOTE_DETAILS_MANAGED,
    FormSpec,
)
from haute._workbench_tables import QUOTE_ROW

#: A quote's stage: draft, which takes saves, or final.
Stage = Literal["draft", "final"]
#: How a quote was made from another.
Relation = Literal["duplicate", "renewal", "alternative"]
#: The lists of the Quotes page: the signed-in underwriter's, everyone's, the archive.
View = Literal["mine", "team", "archived"]
#: The orders a list is read in.
Sort = Literal["saved", "reference", "insured"]
#: What an activity row records.
Action = Literal[
    "created", "saved", "assigned", "archived", "restored", "finalised", "amended", "noted"
]

#: The quotes one page holds, unless asked for fewer; and the most it holds.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
#: The people an assignee is picked from, at most.
MAX_PEOPLE = 500
#: How often a load reads a quote again when it moved on while it was read.
READS = 3
#: The details' column ids by name.
DETAIL_IDS: dict[str, str] = {name: column_id for column_id, name, _ in QUOTE_DETAILS_COLUMNS}
_DATES = frozenset(name for _, name, kind in QUOTE_DETAILS_COLUMNS if kind == "date")
_WHOLE = frozenset(name for _, name, kind in QUOTE_DETAILS_COLUMNS if kind == "int")
_SEARCHED = ("quote_name", "insured", "broker")
#: A word of a search naming a reference and an option, as a quote is shown: ``ABC-1/2``.
_REFERENCE_OPTION = re.compile(r"(.+)/(\d+)")
_OPERATION = re.compile(rf"[A-Za-z0-9_-]{{8,{OPERATION_LENGTH}}}")
#: What each action says a person did, in a stale save's refusal.
_DONE: dict[str, str] = {
    "created": "made",
    "saved": "saved",
    "assigned": "assigned",
    "noted": "gave a note to",
    "archived": "archived",
    "restored": "restored",
    "finalised": "marked final",
    "amended": "amended",
}


@dataclass(frozen=True, slots=True)
class Source:
    """The quote a new one is made from, and how."""

    reference: str
    option: int
    relation: Relation


@dataclass(frozen=True, slots=True)
class PricedOutputs:
    """A save's pricing: the release that priced it and the response's typed document."""

    release: str
    document: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AppQuote:
    """A quote as its ``quote_details`` row holds it, with who marked it final or archived it
    and when, read from its activity."""

    reference: str
    option: int
    #: The ``quote_details`` row, by column name: dates as ISO text, blanks left out.
    details: dict[str, Any]
    stage: Stage
    assignee: str
    archived: bool
    #: The version a change names: every write that changes the quote moves it on.
    version: int
    saved_at: datetime
    saved_by: str
    #: The release whose pricing the output tables hold; None while unpriced.
    priced_release: str | None
    source: Source | None
    note: str
    note_by: str | None
    note_at: datetime | None
    final_by: str | None = None
    final_at: datetime | None = None
    archived_by: str | None = None
    archived_at: datetime | None = None

    @property
    def label(self) -> str:
        """The quote as the app shows it: its reference and option, ``ABC-1/2``."""
        return identity_label(self.reference, self.option)

    @property
    def title(self) -> str:
        """What a list shows as the quote: its name, else its insured, else its label."""
        for name in ("quote_name", "insured"):
            value = self.details.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self.label

    @property
    def priced(self) -> bool:
        return self.priced_release is not None


@dataclass(frozen=True, slots=True)
class OpenQuote:
    """A quote with what it holds, read together: its values as on the sheets, its output
    tables as the pricing rendered them (None while unpriced), and each row that no longer
    fits the sheets."""

    quote: AppQuote
    values: Values
    outputs: PricedTables | None
    dropped: list[str]


@dataclass(frozen=True, slots=True)
class QuotePage:
    """One page of a list, the cursor of the next when there is one, and the counts the
    Quotes page shows: each list's quotes, and each stage's in the list asked for."""

    quotes: list[AppQuote]
    next_cursor: str | None
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    """One thing done to a quote: what, by whom and when, with what it carried."""

    action: Action
    actor: str
    at: datetime
    version: int | None
    assignee: str | None = None
    note: str | None = None
    reason: str | None = None
    priced_release: str | None = None


@dataclass(frozen=True, slots=True)
class RelatedQuote:
    """A quote this one was made from, or one made from it, and how."""

    reference: str
    option: int
    relation: Relation

    @property
    def label(self) -> str:
        return identity_label(self.reference, self.option)


@dataclass(frozen=True, slots=True)
class QuoteActivity:
    """What was done to a quote, the quote it was made from and those made from it."""

    entries: list[ActivityEntry]
    source: RelatedQuote | None
    derived: list[RelatedQuote]


def identity_label(reference: str, option: int) -> str:
    """A quote's reference and option as the app shows them: ``ABC-1/2``."""
    return f"{reference}/{option}"


def check_reference(reference: str | None) -> str:
    """*reference* as a new quote keeps it: trimmed, in Unicode's composed form (NFC), 1 to
    ``REFERENCE_LENGTH`` characters, no ``/``, which a label puts before the option, no control
    or format character, and not ``.`` or ``..``, which an address reads as folders. Two
    references are one only when they are the same, case included. Refused otherwise."""
    value = unicodedata.normalize("NFC", (reference or "").strip())
    if not value:
        raise QuoteStoreError("Key the quote's reference.")
    if len(value) > REFERENCE_LENGTH:
        raise QuoteStoreError(
            f"A reference is at most {REFERENCE_LENGTH} characters; this one has {len(value)}."
        )
    if "/" in value:
        raise QuoteStoreError("A reference holds no /, which the app puts before its option.")
    if value in (".", ".."):
        raise QuoteStoreError(
            "A reference is more than . or .., which an address reads as folders."
        )
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in value):
        raise QuoteStoreError("A reference holds no control characters.")
    return value


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
    """*values* with their ``quote_details`` row set to *details* (by column name), the blanks
    left out and the row with them when none is left; every other table as it was."""
    row = {
        DETAIL_IDS[name]: _detail_cell(value)
        for name, value in details.items()
        if name in DETAIL_IDS and value is not None and value != ""
    }
    merged = {table: rows for table, rows in values.items() if table != QUOTE_DETAILS_ID}
    if row:
        merged[QUOTE_DETAILS_ID] = [row]
    return merged


def without_app_details(values: Values) -> Values:
    """*values* without the ``quote_details`` cells the app sets (``QUOTE_DETAILS_MANAGED``):
    a reference, stage, assignee and the rest belong to the quote that has them, and are
    never read from what a page sends."""
    rows = values.get(QUOTE_DETAILS_ID)
    if rows is None:
        return values
    typed = [
        {column: value for column, value in row.items() if column not in QUOTE_DETAILS_MANAGED}
        for row in rows
    ]
    return {**values, QUOTE_DETAILS_ID: typed}


def as_written(values: Values, managed: Mapping[str, Any]) -> Values:
    """*values* as a write keeps and prices them: the cells the app sets left out, whatever a
    page sent, and its *managed* details (:meth:`QuoteBook.managed`) set over the rest."""
    typed = without_app_details(values)
    return with_details(typed, {**details_of(typed), **managed})


def _detail_cell(value: Any) -> Any:
    """A detail as a cell holds it: a whole number as its text; text and ticks as they are."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _day(moment: datetime) -> str:
    """The date of *moment* in UTC, as the store keeps its times, as its ISO text."""
    return moment.astimezone(UTC).date().isoformat()


def _like(term: str) -> str:
    """A ``LIKE`` pattern for *term* anywhere, its wildcards escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _check_operation(operation: str) -> None:
    if not _OPERATION.fullmatch(operation):
        raise QuoteStoreError(
            "A write's operation key is 8 to 64 letters, digits, dashes or underscores."
        )


def _check_principal(principal: str, what: str) -> str:
    value = principal.strip()
    if not value or len(value) > PRINCIPAL_LENGTH:
        raise QuoteStoreError(f"{what} is a name of 1 to {PRINCIPAL_LENGTH} characters.")
    return value


def _check_name(details: Mapping[str, Any]) -> None:
    name = details.get("quote_name")
    if isinstance(name, str) and len(name) > NAME_LENGTH:
        raise QuoteStoreError(
            f"A quote's name is at most {NAME_LENGTH} characters; this one has {len(name)}."
        )


def _server_key() -> str:
    """An operation key for a write the page names none for."""
    return f"server-{uuid.uuid4().hex}"


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class QuoteBook:
    """The underwriter app's quotes on the store's database: *engine* holding *schema*, the
    tables of *form*, written by an app running Haute *haute_version*; *clock* gives the moment
    each write is stamped with. Closing the book disposes of the engine."""

    def __init__(
        self,
        engine: Engine,
        schema: StoreSchema,
        form: FormSpec,
        *,
        haute_version: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._schema = schema
        self._form = form
        self.haute_version = haute_version or None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._details = schema.details.table
        self._activity = schema.activity

    def now(self) -> datetime:
        """The moment a save is stamped with, read once before it is priced, so its request
        and its row hold the same one."""
        return self._clock()

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> QuoteBook:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── The details the app sets ───────────────────────────────────────────

    def managed(self, quote: AppQuote | None, *, at: datetime, actor: str) -> dict[str, Any]:
        """The details the app sets on a write of *quote* (None for a new quote) by *actor* at
        *at*, by name: its identity, who it is with and whether archived as it has them, its
        stage, when it was made and by whom, when it was changed and by whom, and the Haute
        version the app runs. A new quote's identity is taken as it is written."""
        made: dict[str, Any] = (
            {"created_date": _day(at), "created_by": actor}
            if quote is None
            else {
                "created_date": quote.details.get("created_date"),
                "created_by": quote.details.get("created_by"),
            }
        )
        return {
            "quote_reference": None if quote is None else quote.reference,
            "quote_option": None if quote is None else quote.option,
            "assignee": actor if quote is None else quote.assignee,
            "archived": False if quote is None else quote.archived,
            "stage": "draft" if quote is None else quote.stage,
            **made,
            "modified_date": _day(at),
            "modified_by": actor,
            "haute_version": self.haute_version,
        }

    # ── Reading ───────────────────────────────────────────────────────────

    def get(self, reference: str, option: int) -> AppQuote:
        """The quote *reference*/*option*; :class:`QuoteNotFoundError` when the app keeps none."""
        with self._engine.connect() as connection:
            return self._get(connection, reference, option)

    def read(self, reference: str, option: int) -> OpenQuote:
        """The quote with what it holds, read together: read again when it moved on while it
        was read, :data:`READS` times at most."""
        for _ in range(READS):
            with self._engine.begin() as connection:
                opened = self._open(connection, reference, option)
                version = connection.execute(
                    select(self._details.c.quote_version).where(*self._is(reference, option))
                ).scalar_one_or_none()
            if version == opened.quote.version:
                return opened
        raise QuoteStoreError(
            f"{identity_label(reference, option)} kept changing while it was read: open it again."
        )

    def _open(self, connection: Connection, reference: str, option: int) -> OpenQuote:
        """The quote with what it holds, as *connection* reads it."""
        quote = self._get(connection, reference, option)
        rows = self._rows(connection, reference, option)
        values, dropped = cells(self._form, rows)
        outputs = rendered_outputs(self._form, rows) if quote.priced else None
        return OpenQuote(quote=quote, values=values, outputs=outputs, dropped=dropped)

    def _is(self, reference: str, option: int) -> list[ColumnElement[bool]]:
        return [
            self._details.c.quote_reference == reference,
            self._details.c.quote_option == option,
        ]

    def _get(self, connection: Connection, reference: str, option: int) -> AppQuote:
        row = connection.execute(
            select(self._details).where(*self._is(reference, option))
        ).one_or_none()
        if row is None:
            raise QuoteNotFoundError(f"The app keeps no quote {identity_label(reference, option)}.")
        quote = self._quote(row)
        extras: dict[str, Any] = {}
        if quote.stage == "final":
            last = self._last(connection, reference, option, "finalised")
            if last is not None:
                extras.update(final_by=last.actor, final_at=last.occurred_at)
        if quote.archived:
            last = self._last(connection, reference, option, "archived")
            if last is not None:
                extras.update(archived_by=last.actor, archived_at=last.occurred_at)
        return _replaced(quote, extras)

    def _last(
        self, connection: Connection, reference: str, option: int, action: str
    ) -> Row[Any] | None:
        activity = self._activity
        return connection.execute(
            select(activity)
            .where(
                activity.c.quote_reference == reference,
                activity.c.quote_option == option,
                activity.c.action == action,
            )
            .order_by(activity.c.activity_id.desc())
            .limit(1)
        ).one_or_none()

    def _quote(self, row: Row[Any]) -> AppQuote:
        mapping = row._mapping
        details: dict[str, Any] = {}
        for _, name, _ in QUOTE_DETAILS_COLUMNS:
            value = mapping[name]
            if value is None:
                continue
            details[name] = value.isoformat() if isinstance(value, date) else value
        source = None
        if mapping["source_reference"] is not None and mapping["source_option"] is not None:
            source = Source(
                mapping["source_reference"],
                int(mapping["source_option"]),
                mapping["source_relation"],
            )
        return AppQuote(
            reference=mapping["quote_reference"],
            option=int(mapping["quote_option"]),
            details=details,
            stage=mapping["stage"],
            assignee=mapping["assignee"],
            archived=bool(mapping["archived"]),
            version=int(mapping["quote_version"]),
            saved_at=mapping["saved_at"],
            saved_by=mapping["saved_by"],
            priced_release=mapping["priced_release"],
            source=source,
            note=mapping["handover_note"] or "",
            note_by=mapping["note_by"],
            note_at=mapping["note_at"],
        )

    def _rows(self, connection: Connection, reference: str, option: int) -> dict[str, Rows]:
        """Every form table's rows of the quote, by table id."""
        rows: dict[str, Rows] = {}
        for stored in self._schema.tables:
            table = stored.table
            query = select(table).where(
                table.c.quote_reference == reference, table.c.quote_option == option
            )
            if stored.many:
                query = query.order_by(table.c[QUOTE_ROW])
            rows[stored.form.id] = [dict(row._mapping) for row in connection.execute(query)]
        return rows

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
        word of *search* in the reference (``ABC-1/2`` that option alone), name, insured or
        broker whatever their case, with *assignee* when given, in *sort*'s order, from
        *cursor* (the page before's ``next_cursor``)."""
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise QuoteStoreError(f"A page holds 1 to {MAX_PAGE_SIZE} quotes.")
        where = [self._view_filter(view, actor)]
        if assignee is not None:
            where.append(self._details.c.assignee == assignee)
        where.extend(self._search_filter(term) for term in search.split())
        listed = list(where)
        if stage is not None:
            listed.append(self._details.c.stage == stage)
        if cursor is not None:
            listed.append(self._cursor_filter(sort, cursor))
        query = (
            select(self._details, self._sort_key(sort).label("sort_key"))
            .where(*listed)
            .order_by(*self._order(sort))
            .limit(limit + 1)
        )
        views: tuple[View, ...] = ("mine", "team", "archived")
        stages: tuple[Stage, ...] = ("draft", "final")
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
            counts: dict[str, int] = {
                name: self._count(connection, self._view_filter(name, actor)) for name in views
            }
            for name in stages:
                counts[name] = self._count(connection, *where, self._details.c.stage == name)
        quotes = [self._quote(row) for row in rows[:limit]]
        return QuotePage(
            quotes=quotes,
            next_cursor=self._encode_cursor(sort, rows[limit - 1]) if len(rows) > limit else None,
            counts=counts,
        )

    def _count(self, connection: Connection, *where: ColumnElement[bool]) -> int:
        query = select(func.count()).select_from(self._details).where(*where)
        return int(connection.execute(query).scalar_one())

    def _view_filter(self, view: View, actor: str) -> ColumnElement[bool]:
        details = self._details
        # archived takes no null, and SQL Server takes no IS beside a bit: = on every engine.
        if view == "archived":
            return details.c.archived == true()
        live = details.c.archived == false()
        return and_(live, details.c.assignee == actor) if view == "mine" else live

    def _search_filter(self, term: str) -> ColumnElement[bool]:
        """A search word's match, whatever the case: a reference; ``ABC-1/2`` that option
        alone; a name, an insured and a broker."""
        details = self._details
        named = _REFERENCE_OPTION.fullmatch(term)
        if named is not None:
            return and_(
                func.lower(details.c.quote_reference).like(
                    _like(named.group(1).lower()), escape="\\"
                ),
                details.c.quote_option == int(named.group(2)),
            )
        like = _like(term.lower())
        return or_(
            func.lower(details.c.quote_reference).like(like, escape="\\"),
            *(
                func.lower(func.coalesce(details.c[name], "")).like(like, escape="\\")
                for name in _SEARCHED
            ),
        )

    def _sort_key(self, sort: Sort) -> Any:
        details = self._details
        if sort == "saved":
            return details.c.saved_at
        if sort == "reference":
            return details.c.quote_reference
        return func.lower(func.coalesce(details.c.insured, ""))

    def _encode_cursor(self, sort: Sort, row: Row[Any]) -> str:
        """The cursor after *row*: its sort key as the database made it, and its identity,
        which breaks ties."""
        mapping = row._mapping
        key = mapping["sort_key"]
        if isinstance(key, datetime):
            key = key.isoformat()
        raw = json.dumps(
            [sort, key, mapping["quote_reference"], int(mapping["quote_option"])]
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def _cursor_filter(self, sort: Sort, cursor: str) -> ColumnElement[bool]:
        try:
            named, key, reference, option = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii"))
            )
            if (
                named != sort
                or not isinstance(key, str)
                or not isinstance(reference, str)
                or not isinstance(option, int)
            ):
                raise ValueError(cursor)
            value: Any = datetime.fromisoformat(key) if sort == "saved" else key
        except (ValueError, TypeError) as exc:
            raise QuoteStoreError("The page asked for is not one this list gave.") from exc
        details = self._details
        column = self._sort_key(sort)
        after = or_(
            details.c.quote_reference > reference,
            and_(details.c.quote_reference == reference, details.c.quote_option > option),
        )
        before = or_(
            details.c.quote_reference < reference,
            and_(details.c.quote_reference == reference, details.c.quote_option < option),
        )
        if sort == "saved":
            return or_(column < value, and_(column == value, before))
        return or_(column > value, and_(column == value, after))

    def _order(self, sort: Sort) -> list[Any]:
        details = self._details
        if sort == "reference":
            # The key is the reference itself, which SQL Server takes once in an ORDER BY.
            return [details.c.quote_reference.asc(), details.c.quote_option.asc()]
        column = self._sort_key(sort)
        if sort == "saved":
            return [column.desc(), details.c.quote_reference.desc(), details.c.quote_option.desc()]
        return [column.asc(), details.c.quote_reference.asc(), details.c.quote_option.asc()]

    def activity(self, reference: str, option: int) -> QuoteActivity:
        """What was done to the quote in order, the quote it was made from and those made
        from it."""
        activity = self._activity
        details = self._details
        with self._engine.connect() as connection:
            quote = self._get(connection, reference, option)
            entries = [
                ActivityEntry(
                    action=row.action,
                    actor=row.actor,
                    at=row.occurred_at,
                    version=row.quote_version,
                    assignee=row.assignee,
                    note=row.note,
                    reason=row.reason,
                    priced_release=row.priced_release,
                )
                for row in connection.execute(
                    select(activity)
                    .where(
                        activity.c.quote_reference == reference,
                        activity.c.quote_option == option,
                    )
                    .order_by(activity.c.activity_id)
                )
            ]
            derived = [
                RelatedQuote(row.quote_reference, int(row.quote_option), row.source_relation)
                for row in connection.execute(
                    select(
                        details.c.quote_reference,
                        details.c.quote_option,
                        details.c.source_relation,
                    )
                    .where(
                        details.c.source_reference == reference,
                        details.c.source_option == option,
                    )
                    .order_by(details.c.quote_reference, details.c.quote_option)
                )
            ]
        source = (
            None
            if quote.source is None
            else RelatedQuote(quote.source.reference, quote.source.option, quote.source.relation)
        )
        return QuoteActivity(entries=entries, source=source, derived=derived)

    def people(self) -> list[str]:
        """Everyone a quote is with, or was made, saved or changed by, in order; at most
        :data:`MAX_PEOPLE`."""
        details = self._details
        named = union(
            *(
                select(details.c[name].label("who"))
                for name in ("assignee", "created_by", "modified_by", "saved_by")
            )
        ).subquery()
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    select(named.c.who)
                    .where(named.c.who.is_not(None))
                    .order_by(named.c.who)
                    .limit(MAX_PEOPLE)
                ).scalars()
            )

    # ── Repeated writes ───────────────────────────────────────────────────

    def replayed(
        self,
        operation: str,
        actor: str,
        action: Action,
        reference: str | None = None,
        option: int | None = None,
    ) -> OpenQuote | None:
        """The quote the write under *operation*'s key wrote, with what it holds, when that
        write landed; None when it did not. A save whose quote has moved on since is
        :class:`WriteSupersededError`; a creation answers the quote it made as it stands. The
        key of another person, another quote or another kind of write is refused."""
        _check_operation(operation)
        for _ in range(READS):
            with self._engine.connect() as connection:
                quote = self._replayed(connection, operation, actor, action, reference, option)
            if quote is None:
                return None
            opened = self.read(quote.reference, quote.option)
            # A save moved on past while it was read is looked up again, and so superseded.
            if action != "saved" or opened.quote.version == quote.version:
                return opened
        raise QuoteStoreError("The quote kept changing while it was read: open it again.")

    def _replayed(
        self,
        connection: Connection,
        operation: str,
        actor: str,
        action: Action,
        reference: str | None,
        option: int | None,
    ) -> AppQuote | None:
        activity = self._activity
        row = connection.execute(
            select(activity).where(activity.c.operation_key == operation)
        ).one_or_none()
        if row is None:
            return None
        if (
            row.actor != actor
            or row.action != action
            or (
                reference is not None
                and (row.quote_reference, row.quote_option) != (reference, option)
            )
        ):
            raise QuoteStoreError(
                "This write's operation key was used for another write: make it again."
            )
        quote = self._get(connection, row.quote_reference, int(row.quote_option))
        if action == "saved" and row.quote_version != quote.version:
            newest = connection.execute(
                select(activity.c.actor, activity.c.occurred_at)
                .where(
                    activity.c.quote_reference == quote.reference,
                    activity.c.quote_option == quote.option,
                    activity.c.quote_version.is_not(None),
                )
                .order_by(activity.c.activity_id.desc())
                .limit(1)
            ).one()
            raise WriteSupersededError(
                f"Your save of {quote.label} landed at {row.occurred_at:%H:%M} UTC; "
                f"{newest.actor} changed it at {newest.occurred_at:%H:%M} UTC since. Open the "
                "newest to go on.",
                landed_at=row.occurred_at,
                changed_by=newest.actor,
                changed_at=newest.occurred_at,
            )
        return quote

    # ── Writing ───────────────────────────────────────────────────────────

    def create(
        self,
        *,
        operation: str,
        actor: str,
        values: Values,
        at: datetime,
        reference: str | None,
        source: Source | None = None,
    ) -> OpenQuote:
        """A new quote, unpriced, with *actor*, its details from *values* with the app's set
        over them, written at *at*, with what it holds as written; made from *source* when
        given. An alternative takes its source's reference and the next option under it and
        names no *reference*; any other quote takes *reference* (:func:`check_reference`) and
        option 1, refused (:class:`ReferenceTakenError`) when another quote holds it. Repeated
        with *operation*'s key, it answers the quote that key made as it stands and writes
        nothing."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who makes a quote")
        alternative = source is not None and source.relation == "alternative"
        if alternative and reference is not None:
            raise QuoteStoreError("An alternative takes its source's reference: name none.")
        keyed = None if alternative else check_reference(reference)
        _check_name(details_of(without_app_details(values)))
        try:
            with writing(self._engine) as connection:
                done = self._replayed(connection, operation, actor, "created", None, None)
                if done is None:
                    return self._insert(connection, operation, actor, values, at, keyed, source)
        except IntegrityError:
            # Another creation of the same reference or option, or of the same key, came first.
            again = self.replayed(operation, actor, "created")
            if again is not None:
                return again
            if keyed is not None:
                with self._engine.connect() as connection:
                    self._refuse_taken(connection, keyed)
            raise
        # The key made its quote already: it is answered as it stands, and nothing is written.
        return self.read(done.reference, done.option)

    def _insert(
        self,
        connection: Connection,
        operation: str,
        actor: str,
        values: Values,
        at: datetime,
        keyed: str | None,
        source: Source | None,
    ) -> OpenQuote:
        """The new quote written on *connection*: under *keyed* and option 1, refused when
        another quote holds it, or, keyed none, under its source's reference and the next
        option."""
        if source is not None:
            self._get(connection, source.reference, source.option)
        if keyed is None:
            assert source is not None
            reference = source.reference
            option = self._next_option(connection, reference)
        else:
            reference = keyed
            self._refuse_taken(connection, reference)
            option = 1
        managed = {
            **self.managed(None, at=at, actor=actor),
            "quote_reference": reference,
            "quote_option": option,
        }
        written = as_written(values, managed)
        rows = input_rows(self._form, written, (reference, option))
        details = rows.pop(QUOTE_DETAILS_ID)[0]
        connection.execute(
            insert(self._details).values(
                **details,
                **self._store_only(managed),
                quote_version=1,
                saved_at=at,
                saved_by=actor,
                priced_release=None,
                source_reference=None if source is None else source.reference,
                source_option=None if source is None else source.option,
                source_relation=None if source is None else source.relation,
            )
        )
        self._write_rows(connection, rows)
        self._record(connection, operation, reference, option, 1, at, actor, "created")
        return self._open(connection, reference, option)

    def save(
        self,
        reference: str,
        option: int,
        *,
        operation: str,
        version: int,
        actor: str,
        values: Values,
        priced: PricedOutputs | None,
        at: datetime,
    ) -> OpenQuote:
        """The quote saved over *version* at *at*, with what it holds as written: its rows
        written whole from *values*, with the app's details set over them, and from *priced*'s
        document, or none of the output tables when unpriced. Refused with nothing written
        when the quote has moved past *version*, is final or is archived; repeated with
        *operation*'s key, it answers what that key wrote and writes nothing more."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who saves a quote")
        _check_name(details_of(without_app_details(values)))
        current = self.get(reference, option)
        managed = self.managed(current, at=at, actor=actor)
        written = as_written(values, managed)
        rows = {
            **input_rows(self._form, written, (reference, option)),
            **output_rows(
                self._form, None if priced is None else priced.document, (reference, option)
            ),
        }
        details = rows.pop(QUOTE_DETAILS_ID)[0]
        try:
            with writing(self._engine) as connection:
                moved = connection.execute(
                    update(self._details)
                    .where(
                        *self._is(reference, option),
                        self._details.c.quote_version == version,
                        self._details.c.stage == "draft",
                        self._details.c.archived == false(),
                    )
                    .values(
                        **{
                            name: value
                            for name, value in details.items()
                            if name not in ("quote_reference", "quote_option")
                        },
                        **self._store_only(managed),
                        saved_at=at,
                        saved_by=actor,
                        priced_release=None if priced is None else priced.release,
                        quote_version=self._details.c.quote_version + 1,
                    )
                )
                if moved.rowcount != 1:
                    raise _NotMovedError
                self._replace_rows(connection, reference, option, rows)
                self._record(
                    connection,
                    operation,
                    reference,
                    option,
                    version + 1,
                    at,
                    actor,
                    "saved",
                    priced_release=None if priced is None else priced.release,
                )
                return self._open(connection, reference, option)
        except IntegrityError:
            # The key's first write came first, as one save sent twice at once does.
            done = self.replayed(operation, actor, "saved", reference, option)
            if done is not None:
                return done
            raise
        except _NotMovedError:
            pass
        # Nothing was written: the key may have landed meanwhile; otherwise say why the quote
        # took no save.
        done = self.replayed(operation, actor, "saved", reference, option)
        if done is not None:
            return done
        self._refuse_change(reference, option, version, "save")
        raise QuoteStoreError(
            f"{identity_label(reference, option)} took no save over version {version}: open it "
            "again."
        )

    def _refuse_change(self, reference: str, option: int, version: int, what: str) -> None:
        """Raise why the quote took no *what* over *version*: it is gone, moved on, final or
        archived."""
        with self._engine.connect() as connection:
            quote = self._get(connection, reference, option)
            if quote.version != version:
                last = connection.execute(
                    select(
                        self._activity.c.actor,
                        self._activity.c.action,
                        self._activity.c.occurred_at,
                    )
                    .where(
                        self._activity.c.quote_reference == reference,
                        self._activity.c.quote_option == option,
                        self._activity.c.quote_version == quote.version,
                    )
                    .order_by(self._activity.c.activity_id.desc())
                    .limit(1)
                ).one_or_none()
                said = (
                    ""
                    if last is None
                    else f": {last.actor} {_DONE.get(last.action, last.action)} it at "
                    f"{last.occurred_at:%H:%M} UTC on {last.occurred_at:%d %b %Y}"
                )
                raise StaleQuoteError(f"{quote.label} has changed since you opened it{said}.")
        if quote.archived:
            raise QuoteLockedError(f"{quote.label} is archived: restore it to change it.")
        if quote.stage == "final" and what == "save":
            raise QuoteLockedError(
                f"{quote.label} is final: amend it, giving a reason, to change it."
            )

    def _store_only(self, managed: Mapping[str, Any]) -> dict[str, Any]:
        """The details the app sets that no request holds, as the row holds them."""
        return {
            "stage": managed["stage"],
            "assignee": managed["assignee"],
            "archived": managed["archived"],
            "modified_date": date.fromisoformat(managed["modified_date"]),
            "modified_by": managed["modified_by"],
            "haute_version": managed["haute_version"],
        }

    def _write_rows(self, connection: Connection, rows: Mapping[str, Rows]) -> None:
        for stored in self._schema.tables:
            held = rows.get(stored.form.id)
            if held:
                connection.execute(insert(stored.table), list(held))

    def _replace_rows(
        self, connection: Connection, reference: str, option: int, rows: Mapping[str, Rows]
    ) -> None:
        for stored in self._schema.tables:
            if stored.form.id == QUOTE_DETAILS_ID:
                continue
            table = stored.table
            connection.execute(
                delete(table).where(
                    table.c.quote_reference == reference, table.c.quote_option == option
                )
            )
        self._write_rows(connection, rows)

    def _record(
        self,
        connection: Connection,
        operation: str,
        reference: str,
        option: int,
        version: int | None,
        at: datetime,
        actor: str,
        action: Action,
        **carried: Any,
    ) -> None:
        connection.execute(
            insert(self._activity).values(
                operation_key=operation,
                quote_reference=reference,
                quote_option=option,
                quote_version=version,
                occurred_at=at,
                actor=actor,
                action=action,
                **carried,
            )
        )

    def _refuse_taken(self, connection: Connection, reference: str) -> None:
        held = connection.execute(
            select(self._details.c.quote_reference, self._details.c.quote_option)
            .where(self._details.c.quote_reference == reference)
            .order_by(self._details.c.quote_option.asc())
            .limit(1)
        ).one_or_none()
        if held is not None:
            label = identity_label(held.quote_reference, int(held.quote_option))
            raise ReferenceTakenError(
                f"{label} already holds the reference {reference}: open it, or create an "
                "alternative of it.",
                reference=held.quote_reference,
                option=int(held.quote_option),
                label=label,
            )

    def _next_option(self, connection: Connection, reference: str) -> int:
        """The next option under *reference*: its option 1 held first, so two alternatives
        made at once take two."""
        held = connection.execute(
            update(self._details)
            .where(*self._is(reference, 1))
            .values(quote_version=self._details.c.quote_version)
        )
        if held.rowcount != 1:
            raise QuoteNotFoundError(f"The app keeps no quote {identity_label(reference, 1)}.")
        highest = connection.execute(
            select(func.max(self._details.c.quote_option)).where(
                self._details.c.quote_reference == reference
            )
        ).scalar_one()
        return int(highest) + 1

    # ── Changing ──────────────────────────────────────────────────────────

    def assign(
        self, reference: str, option: int, *, version: int, actor: str, assignee: str
    ) -> AppQuote:
        """Put the quote with *assignee*."""
        assignee = _check_principal(assignee, "Who a quote is with")
        return self._change(
            reference,
            option,
            version,
            actor,
            "assigned",
            {"assignee": assignee},
            archived=False,
            carried={"assignee": assignee},
        )

    def archive(self, reference: str, option: int, *, version: int, actor: str) -> AppQuote:
        """Take the quote out of the lists in use; it opens read only until restored."""
        return self._change(
            reference, option, version, actor, "archived", {"archived": True}, archived=False
        )

    def restore(self, reference: str, option: int, *, version: int, actor: str) -> AppQuote:
        """Bring an archived quote back; a final one stays final."""
        return self._change(
            reference, option, version, actor, "restored", {"archived": False}, archived=True
        )

    def amend(
        self, reference: str, option: int, *, version: int, actor: str, reason: str
    ) -> AppQuote:
        """Take a final quote back to draft to amend it, giving *reason*; its next save
        overwrites what was offered."""
        reason = reason.strip()
        if not reason:
            raise QuoteStoreError("Give a reason for amending a final quote.")
        return self._change(
            reference,
            option,
            version,
            actor,
            "amended",
            {"stage": "draft"},
            archived=False,
            stage="final",
            carried={"reason": reason},
        )

    def finalise(
        self, reference: str, option: int, *, version: int, actor: str, release: str
    ) -> AppQuote:
        """Mark the quote final: it must be a draft priced on *release*, the release running,
        with a value in every required input and output column."""
        with self._engine.begin() as connection:
            quote = self._get(connection, reference, option)
            # A quote moved on, archived or final is refused by the swap below, saying which.
            if quote.version == version and not quote.archived and quote.stage == "draft":
                if quote.priced_release != release:
                    priced_on = (
                        "is not priced"
                        if quote.priced_release is None
                        else f"was priced on release {quote.priced_release}, not this one, "
                        f"{release}"
                    )
                    raise QuoteNotReadyError(
                        f"{quote.label} {priced_on}: price it and save it before marking it final."
                    )
                missing = self._missing(self._rows(connection, reference, option))
                if missing:
                    raise QuoteNotReadyError(
                        f"{quote.label} needs a value in "
                        + ", ".join(missing)
                        + " before it is marked final.",
                        missing=missing,
                    )
        return self._change(
            reference,
            option,
            version,
            actor,
            "finalised",
            {"stage": "final"},
            archived=False,
            stage="draft",
        )

    def _missing(self, rows: Mapping[str, Rows]) -> list[str]:
        """Each required column without a value, as ``table.column`` (``table row N.column`` in
        a many-row table): every row's, a table without rows passing; text of spaces alone is
        empty, and zero and false are values."""
        missing: list[str] = []
        for table in self._form.data_schema.tables:
            required = [column for column in table.columns if column.required]
            if not required:
                continue
            for row in rows.get(table.id, []):
                for column in required:
                    if _blank(row.get(column.name)):
                        where = (
                            f"{table.name} row {row[QUOTE_ROW]}"
                            if table.rows == "many"
                            else table.name
                        )
                        missing.append(f"{where}.{column.label or column.name}")
        return missing

    def set_note(self, reference: str, option: int, *, actor: str, note: str) -> AppQuote:
        """Put *note*, the handover note, on the quote with who wrote it and when; a blank one
        clears it. A note moves no version: it never conflicts with another write."""
        actor = _check_principal(actor, "Who writes a note")
        note = note.strip()
        at = self._clock()
        with writing(self._engine) as connection:
            held = connection.execute(
                update(self._details)
                .where(*self._is(reference, option), self._details.c.archived == false())
                .values(
                    handover_note=note or None,
                    note_by=actor if note else None,
                    note_at=at if note else None,
                )
            )
            if held.rowcount != 1:
                quote = self._get(connection, reference, option)
                raise QuoteLockedError(f"{quote.label} is archived: restore it to change it.")
            self._record(
                connection, _server_key(), reference, option, None, at, actor, "noted", note=note
            )
            return self._get(connection, reference, option)

    def _change(
        self,
        reference: str,
        option: int,
        version: int,
        actor: str,
        action: Action,
        changed: Mapping[str, Any],
        *,
        archived: bool,
        stage: Stage | None = None,
        carried: Mapping[str, Any] | None = None,
    ) -> AppQuote:
        """One compare-and-swap on the quote's ``quote_details`` row over *version*, where it is
        *archived* (and at *stage* when given): its *changed* details set, its administration
        saying who changed it and when, its version moved on, with its activity row."""
        actor = _check_principal(actor, "Who changes a quote")
        at = self._clock()
        details = self._details
        with writing(self._engine) as connection:
            conditions = [
                *self._is(reference, option),
                details.c.quote_version == version,
                details.c.archived == archived,
            ]
            if stage is not None:
                conditions.append(details.c.stage == stage)
            moved = connection.execute(
                update(details)
                .where(*conditions)
                .values(
                    **changed,
                    modified_date=at.astimezone(UTC).date(),
                    modified_by=actor,
                    haute_version=self.haute_version,
                    quote_version=details.c.quote_version + 1,
                )
            )
            if moved.rowcount == 1:
                self._record(
                    connection,
                    _server_key(),
                    reference,
                    option,
                    version + 1,
                    at,
                    actor,
                    action,
                    **dict(carried or {}),
                )
                return self._get(connection, reference, option)
        self._refuse_change(reference, option, version, action)
        quote = self.get(reference, option)
        if action == "restored":
            raise QuoteLockedError(f"{quote.label} is not archived.")
        if action == "amended":
            raise QuoteLockedError(f"{quote.label} is not final.")
        if action == "finalised":
            raise QuoteLockedError(f"{quote.label} is final already.")
        raise QuoteLockedError(f"{quote.label} does not take this change as it stands.")


class _NotMovedError(Exception):
    """A compare-and-swap updated no row: nothing was written."""


def _replaced(quote: AppQuote, extras: Mapping[str, Any]) -> AppQuote:
    return replace(quote, **extras) if extras else quote
