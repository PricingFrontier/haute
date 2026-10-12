"""The underwriter app's quotes (specs/workbench): the record the app keeps of each quote it
saves, beside the store's quote and revisions, and the pages of quotes its Quotes page lists.

A record holds the quote's reference, the title, summary and search text projected from its
newest revision, who it is assigned to, whether that revision is priced, the revision it was
finalised at, its archive, a handover note, the quote it was made from and a version every
change moves. Every change is one transaction: the store's revision when there is one, the
record, an event of who did what and, for a save, its operation key are written together or
not at all; every change names the version it was made on and is refused, with nothing
written, when the quote has moved on. The test quotes of the analysts' Preview share the
store and never have a record.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    ColumnElement,
    Connection,
    RowMapping,
    Select,
    and_,
    func,
    insert,
    or_,
    select,
    union,
    update,
)
from sqlalchemy.orm import aliased

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
    APP_QUOTES,
    NAME_LENGTH,
    OPERATION_LENGTH,
    PRINCIPAL_LENGTH,
    QUOTE_COUNTER,
    QUOTE_EVENTS,
    QUOTE_OPERATIONS,
    QUOTE_REVISIONS,
    QUOTES,
)
from haute._workbench_form import FormSpec

#: A quote's stage: saved without a price on its newest revision, saved priced, or final.
Stage = Literal["draft", "priced", "final"]
#: How a quote was made from another.
Relation = Literal["duplicate", "renewal", "alternative"]
#: The lists of the Quotes page: the signed-in underwriter's, everyone's, the archive.
View = Literal["mine", "team", "archived"]
#: The orders a list is read in: the newest saved first, the newest reference first, or by
#: title.
Sort = Literal["saved", "reference", "title"]
#: What an event records.
EventKind = Literal[
    "created",
    "saved",
    "renamed",
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
#: What a reprojection reads and writes at a time.
_REPROJECT_BATCH = 200
_OPERATION = re.compile(rf"[A-Za-z0-9_-]{{8,{OPERATION_LENGTH}}}")
_COUNTER_SCOPE = "quotes"
_SOURCE = aliased(APP_QUOTES, name="source_quote")


class QuoteLockedError(QuoteStoreError):
    """The quote does not take this change as it stands: final, archived, or not ready to be
    finalised. The message says what would let it."""

    error_code = "quote_locked"


@dataclass(frozen=True, slots=True)
class Projection:
    """What the Quotes page shows of a quote, projected from a revision by the quote list
    setting of the sheets it is listed under."""

    #: The title field's value as text; None when blank, or when the setting names none.
    title: str | None = None
    #: Each summary field's value as saved, in the setting's order; None where blank.
    fields: list[Any] = field(default_factory=list)
    #: The headline output's value; None when unpriced or blank.
    headline: Any = None
    #: What identifies the setting it was projected with; a different one is reprojected.
    key: str = ""

    def summary(self) -> dict[str, Any]:
        return {"title": self.title, "fields": self.fields, "headline": self.headline}


@dataclass(frozen=True, slots=True)
class Source:
    """The quote and revision a new quote is made from, and how."""

    quote_id: str
    revision: int
    relation: Relation


@dataclass(frozen=True, slots=True)
class AppQuote:
    """An app quote as its record and its newest revision hold it."""

    id: str
    reference: str
    name: str
    #: What the list shows as the quote: the title field's value, else the name, else the
    #: reference.
    title: str
    #: The projected title value, summary fields and headline.
    summary: dict[str, Any]
    stage: Stage
    priced: bool
    assignee: str
    #: Who made it, and when.
    owner: str
    created_at: datetime
    #: The newest revision's number, the release that priced it and the sheets it was keyed
    #: into.
    latest: int
    release: str | None
    form_revision: str
    last_saved_by: str
    last_saved_at: datetime
    final_revision: int | None
    final_by: str | None
    final_at: datetime | None
    archived_by: str | None
    archived_at: datetime | None
    note: str
    note_by: str | None
    note_at: datetime | None
    source_quote_id: str | None
    source_reference: str | None
    source_revision: int | None
    relation: Relation | None
    version: int


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


@dataclass(frozen=True, slots=True)
class RelatedQuote:
    """A quote this one was made from, or one made from it."""

    id: str
    reference: str
    title: str
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


def display_title(projected: str | None, name: str, reference: str) -> str:
    """What the list shows as a quote: its title field's value, else its name, else its
    reference; no longer than the store keeps."""
    for candidate in (projected, name):
        if candidate is not None and candidate.strip():
            return candidate.strip()[:NAME_LENGTH]
    return reference


def _search_text(reference: str, name: str, title: str, fields: list[Any]) -> str:
    """What a quote is searched by: its reference, name, title and summary fields' text."""
    texts = [str(value) for value in fields if value is not None and value != ""]
    return " ".join([reference, name, title, *texts]).lower()


def _like_term(term: str) -> str:
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


def _stage(row: RowMapping) -> Stage:
    if row["final_revision"] is not None:
        return "final"
    return "priced" if row["priced"] else "draft"


def _quote(row: RowMapping) -> AppQuote:
    return AppQuote(
        id=row["quote_id"],
        reference=row["reference"],
        name=row["name"],
        title=row["title"],
        summary=json.loads(row["summary"]),
        stage=_stage(row),
        priced=bool(row["priced"]),
        assignee=row["assignee"],
        owner=row["owner"],
        created_at=row["created_at"],
        latest=row["latest"],
        release=row["release"],
        form_revision=row["form_revision"],
        last_saved_by=row["last_saved_by"],
        last_saved_at=row["last_saved_at"],
        final_revision=row["final_revision"],
        final_by=row["final_by"],
        final_at=row["final_at"],
        archived_by=row["archived_by"],
        archived_at=row["archived_at"],
        note=row["note"],
        note_by=row["note_by"],
        note_at=row["note_at"],
        source_quote_id=row["source_quote_id"],
        source_reference=row["source_reference"],
        source_revision=row["source_revision"],
        relation=row["relation"],
        version=row["version"],
    )


def _records() -> Select[Any]:
    """Each record with its quote, its newest revision's release and sheets, and the
    reference of the quote it was made from."""
    return (
        select(
            APP_QUOTES,
            QUOTES.c.name,
            QUOTES.c.owner,
            QUOTES.c.created_at,
            QUOTES.c.latest,
            QUOTE_REVISIONS.c.release,
            QUOTE_REVISIONS.c.form_revision,
            _SOURCE.c.reference.label("source_reference"),
        )
        .join(QUOTES, QUOTES.c.id == APP_QUOTES.c.quote_id)
        .join(
            QUOTE_REVISIONS,
            (QUOTE_REVISIONS.c.quote_id == QUOTES.c.id)
            & (QUOTE_REVISIONS.c.number == QUOTES.c.latest),
        )
        .outerjoin(_SOURCE, _SOURCE.c.quote_id == APP_QUOTES.c.source_quote_id)
    )


def _view_filter(view: View, actor: str) -> ColumnElement[bool]:
    if view == "archived":
        return APP_QUOTES.c.archived_at.is_not(None)
    live = APP_QUOTES.c.archived_at.is_(None)
    return and_(live, APP_QUOTES.c.assignee == actor) if view == "mine" else live


def _stage_filter(stage: Stage) -> ColumnElement[bool]:
    if stage == "final":
        return APP_QUOTES.c.final_revision.is_not(None)
    open_ = APP_QUOTES.c.final_revision.is_(None)
    return and_(open_, APP_QUOTES.c.priced.is_(stage == "priced"))


def _encode_cursor(sort: Sort, quote: AppQuote) -> str:
    key: Any = {
        "saved": quote.last_saved_at.isoformat(),
        "reference": quote.reference,
        "title": quote.title.lower(),
    }[sort]
    raw = json.dumps([sort, key, quote.id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _cursor_filter(sort: Sort, cursor: str) -> ColumnElement[bool]:
    try:
        named, key, quote_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if named != sort or not isinstance(key, str) or not isinstance(quote_id, str):
            raise ValueError(cursor)
        saved = datetime.fromisoformat(key) if sort == "saved" else None
    except (ValueError, TypeError) as exc:
        raise QuoteStoreError("The page asked for is not one this list gave.") from exc
    if sort == "saved":
        column = APP_QUOTES.c.last_saved_at
        return or_(column < saved, and_(column == saved, APP_QUOTES.c.quote_id < quote_id))
    if sort == "reference":
        return APP_QUOTES.c.reference < key
    lowered = func.lower(APP_QUOTES.c.title)
    return or_(lowered > key, and_(lowered == key, APP_QUOTES.c.quote_id > quote_id))


def _order(sort: Sort) -> list[Any]:
    if sort == "saved":
        return [APP_QUOTES.c.last_saved_at.desc(), APP_QUOTES.c.quote_id.desc()]
    if sort == "reference":
        return [APP_QUOTES.c.reference.desc()]
    return [func.lower(APP_QUOTES.c.title).asc(), APP_QUOTES.c.quote_id.asc()]


_DONE = {
    "created": "made",
    "saved": "saved",
    "renamed": "renamed",
    "assigned": "assigned",
    "noted": "given a note",
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
        """The app quote *quote_id*; :class:`QuoteNotFoundError` when the app keeps none."""
        with self._engine.connect() as connection:
            return self._get(connection, quote_id)

    def _get(self, connection: Connection, quote_id: str) -> AppQuote:
        row = (
            connection.execute(_records().where(APP_QUOTES.c.quote_id == quote_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise QuoteNotFoundError(f"The app keeps no quote {quote_id!r}.")
        return _quote(row)

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
        word of *search* when given, assigned to *assignee* when given, in *sort*'s order,
        from *cursor* (the page before's ``next_cursor``) when given."""
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise QuoteStoreError(f"A page holds 1 to {MAX_PAGE_SIZE} quotes.")
        where = [_view_filter(view, actor)]
        if assignee is not None:
            where.append(APP_QUOTES.c.assignee == assignee)
        for term in search.split():
            where.append(APP_QUOTES.c.search_text.like(_like_term(term), escape="\\"))
        listed = list(where)
        if stage is not None:
            listed.append(_stage_filter(stage))
        if cursor is not None:
            listed.append(_cursor_filter(sort, cursor))
        query = _records().where(*listed).order_by(*_order(sort)).limit(limit + 1)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            views: tuple[View, ...] = ("mine", "team", "archived")
            stages: tuple[Stage, ...] = ("draft", "priced", "final")
            counts: dict[str, int] = {
                name: self._count(connection, _view_filter(name, actor)) for name in views
            }
            for name in stages:
                counts[name] = self._count(connection, *where, _stage_filter(name))
        quotes = [_quote(row) for row in rows[:limit]]
        more = len(rows) > limit
        return QuotePage(
            quotes=quotes,
            next_cursor=_encode_cursor(sort, quotes[-1]) if more else None,
            counts=counts,
        )

    @staticmethod
    def _count(connection: Connection, *where: ColumnElement[bool]) -> int:
        return int(
            connection.execute(
                select(func.count()).select_from(APP_QUOTES).where(*where)
            ).scalar_one()
        )

    def timeline(self, quote_id: str) -> QuoteTimeline:
        """Quote *quote_id*'s revisions, events, the quote it was made from and those made
        from it."""
        with self._engine.connect() as connection:
            quote = self._get(connection, quote_id)
            revisions = [
                RevisionSummary(
                    number=row["number"],
                    saved_by=row["saved_by"],
                    saved_at=row["saved_at"],
                    priced=bool(row["priced"]),
                    release=row["release"],
                    form_revision=row["form_revision"],
                )
                for row in connection.execute(
                    select(
                        QUOTE_REVISIONS.c.number,
                        QUOTE_REVISIONS.c.saved_by,
                        QUOTE_REVISIONS.c.saved_at,
                        QUOTE_REVISIONS.c.response.is_not(None).label("priced"),
                        QUOTE_REVISIONS.c.release,
                        QUOTE_REVISIONS.c.form_revision,
                    )
                    .where(QUOTE_REVISIONS.c.quote_id == quote_id)
                    .order_by(QUOTE_REVISIONS.c.number)
                ).mappings()
            ]
            events = [
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
            derived = [
                RelatedQuote(
                    id=row["quote_id"],
                    reference=row["reference"],
                    title=row["title"],
                    relation=row["relation"],
                    revision=row["source_revision"],
                )
                for row in connection.execute(
                    select(APP_QUOTES)
                    .where(APP_QUOTES.c.source_quote_id == quote_id)
                    .order_by(APP_QUOTES.c.reference)
                ).mappings()
            ]
            source = None
            if (
                quote.source_quote_id is not None
                and quote.relation is not None
                and quote.source_revision is not None
            ):
                origin = self._get(connection, quote.source_quote_id)
                source = RelatedQuote(
                    id=origin.id,
                    reference=origin.reference,
                    title=origin.title,
                    relation=quote.relation,
                    revision=quote.source_revision,
                )
        return QuoteTimeline(revisions=revisions, events=events, source=source, derived=derived)

    def people(self) -> list[str]:
        """Everyone a quote is assigned to, was made or saved by, in order; at most
        :data:`MAX_PEOPLE`."""
        named = union(
            select(APP_QUOTES.c.assignee.label("who")),
            select(APP_QUOTES.c.last_saved_by.label("who")),
            select(QUOTES.c.owner.label("who")).join(
                APP_QUOTES, APP_QUOTES.c.quote_id == QUOTES.c.id
            ),
        ).subquery()
        with self._engine.connect() as connection:
            return list(
                connection.execute(
                    select(named.c.who).order_by(named.c.who).limit(MAX_PEOPLE)
                ).scalars()
            )

    # ── Saving ────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        operation: str,
        actor: str,
        name: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
        projection: Projection,
        source: Source | None = None,
    ) -> AppQuote:
        """A new quote with its first revision and a reference of its own, assigned to
        *actor*; made from *source* when given. Repeated with *operation*'s key, it answers
        the quote that key made and writes nothing."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who saves a quote")
        name = name.strip()[:NAME_LENGTH]
        with self._engine.begin() as connection:
            done = self._operation(connection, operation, actor, None)
            if done is not None:
                return done
            origin: AppQuote | None = None
            if source is not None:
                origin = self._get(connection, source.quote_id)
                _read_revision(connection, source.quote_id, source.revision)
            reference = self._next_reference(connection)
            now = self._clock()
            revision = self.store._create_in(
                connection,
                name=name,
                owner=actor,
                saved_by=actor,
                saved_at=now,
                form=form,
                inputs=inputs,
                request=request,
                response=response,
                release=release,
            )
            title = display_title(projection.title, name, reference)
            connection.execute(
                insert(APP_QUOTES).values(
                    quote_id=revision.quote_id,
                    reference=reference,
                    title=title,
                    summary=json.dumps(projection.summary(), ensure_ascii=False),
                    search_text=_search_text(reference, name, title, projection.fields),
                    projection=projection.key,
                    assignee=actor,
                    priced=response is not None,
                    note="",
                    source_quote_id=None if source is None else source.quote_id,
                    source_revision=None if source is None else source.revision,
                    relation=None if source is None else source.relation,
                    last_saved_by=actor,
                    last_saved_at=now,
                    version=1,
                )
            )
            created: dict[str, Any] = {"reference": reference}
            if source is not None and origin is not None:
                created["source"] = {
                    "quote_id": origin.id,
                    "reference": origin.reference,
                    "revision": source.revision,
                    "relation": source.relation,
                }
                self._event(
                    connection,
                    origin.id,
                    actor,
                    "derived",
                    source.revision,
                    {
                        "quote_id": revision.quote_id,
                        "reference": reference,
                        "relation": source.relation,
                    },
                    at=now,
                )
            self._event(connection, revision.quote_id, actor, "created", 1, created, at=now)
            self._remember(connection, operation, revision.quote_id, 1, actor)
            return self._get(connection, revision.quote_id)

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
        projection: Projection,
    ) -> AppQuote:
        """The quote's next revision, saved over *version* of it. Refused with nothing
        written when the quote has moved past *version*, is final or is archived; repeated
        with *operation*'s key, it answers the quote and writes nothing more."""
        _check_operation(operation)
        actor = _check_principal(actor, "Who saves a quote")
        with self._engine.begin() as connection:
            done = self._operation(connection, operation, actor, quote_id)
            if done is not None:
                return done
            quote = self._current(connection, quote_id, version)
            if quote.archived_at is not None:
                raise QuoteLockedError(f"{quote.reference} is archived: restore it to change it.")
            if quote.final_revision is not None:
                raise QuoteLockedError(
                    f"{quote.reference} is final: reopen it, giving a reason, to change it."
                )
            now = self._clock()
            revision = self.store._save_in(
                connection,
                quote_id,
                after=quote.latest,
                after_digest=None,
                saved_by=actor,
                saved_at=now,
                form=form,
                inputs=inputs,
                request=request,
                response=response,
                release=release,
            )
            title = display_title(projection.title, quote.name, quote.reference)
            self._update(
                connection,
                quote,
                title=title,
                summary=json.dumps(projection.summary(), ensure_ascii=False),
                search_text=_search_text(quote.reference, quote.name, title, projection.fields),
                projection=projection.key,
                priced=response is not None,
                last_saved_by=actor,
                last_saved_at=now,
            )
            self._event(
                connection,
                quote_id,
                actor,
                "saved",
                revision.number,
                {"priced": response is not None, "release": release},
                at=now,
            )
            self._remember(connection, operation, quote_id, revision.number, actor)
            return self._get(connection, quote_id)

    # ── Changing ──────────────────────────────────────────────────────────

    def rename(self, quote_id: str, *, version: int, actor: str, name: str) -> AppQuote:
        """Give the quote *name*; its title follows when no title field names it."""
        name = name.strip()[:NAME_LENGTH]

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            title = display_title(quote.summary.get("title"), name, quote.reference)
            connection.execute(update(QUOTES).where(QUOTES.c.id == quote.id).values(name=name))
            fields = list(quote.summary.get("fields", []))
            self._update(
                connection,
                quote,
                title=title,
                search_text=_search_text(quote.reference, name, title, fields),
            )
            return {"from": quote.name, "to": name}

        return self._change(quote_id, version, actor, "renamed", change)

    def assign(self, quote_id: str, *, version: int, actor: str, assignee: str) -> AppQuote:
        """Make *assignee* the one the quote is with."""
        assignee = _check_principal(assignee, "Who a quote is assigned to")

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            self._update(connection, quote, assignee=assignee)
            return {"from": quote.assignee, "to": assignee}

        return self._change(quote_id, version, actor, "assigned", change)

    def set_note(self, quote_id: str, *, version: int, actor: str, note: str) -> AppQuote:
        """Put *note*, the handover note, on the quote; a blank one clears it."""
        note = note.strip()

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            now = self._clock()
            self._update(
                connection,
                quote,
                note=note,
                note_by=actor if note else None,
                note_at=now if note else None,
            )
            return {"note": note}

        return self._change(quote_id, version, actor, "noted", change)

    def archive(self, quote_id: str, *, version: int, actor: str) -> AppQuote:
        """Take the quote out of the lists in use; it opens read only until restored."""

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            self._update(connection, quote, archived_by=actor, archived_at=self._clock())
            return {}

        return self._change(quote_id, version, actor, "archived", change)

    def restore(self, quote_id: str, *, version: int, actor: str) -> AppQuote:
        """Bring an archived quote back; a final one stays final."""

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            if quote.archived_at is None:
                raise QuoteLockedError(f"{quote.reference} is not archived.")
            self._update(connection, quote, archived_by=None, archived_at=None)
            return {}

        return self._change(quote_id, version, actor, "restored", change, archived_ok=True)

    def finalise(
        self, quote_id: str, *, version: int, actor: str, revision: int, release: str
    ) -> AppQuote:
        """Pin *revision*, the quote's newest, as final: it must be priced, by *release*,
        the release running."""

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            if quote.final_revision is not None:
                raise QuoteLockedError(
                    f"{quote.reference} is final at revision {quote.final_revision} already."
                )
            if revision != quote.latest:
                raise StaleQuoteError(
                    f"{quote.reference} has moved on to revision {quote.latest} since revision "
                    f"{revision} was shown."
                )
            if not quote.priced:
                raise QuoteLockedError(
                    f"{quote.reference}'s revision {revision} is not priced: price it and save "
                    "it before marking it final."
                )
            if quote.release != release:
                raise QuoteLockedError(
                    f"{quote.reference}'s revision {revision} was priced on release "
                    f"{quote.release}, not this one, {release}: price it again and save it "
                    "before marking it final."
                )
            self._update(
                connection,
                quote,
                final_revision=revision,
                final_by=actor,
                final_at=self._clock(),
            )
            return {}

        return self._change(
            quote_id, version, actor, "finalised", change, revision_of=lambda _q: revision
        )

    def reopen(self, quote_id: str, *, version: int, actor: str, reason: str) -> AppQuote:
        """Reopen a final quote for an amendment, giving *reason*; its final stays in its
        history and its next save is a new revision."""
        reason = reason.strip()
        if not reason:
            raise QuoteStoreError("Give a reason for reopening a final quote.")

        def change(connection: Connection, quote: AppQuote) -> dict[str, Any]:
            if quote.final_revision is None:
                raise QuoteLockedError(f"{quote.reference} is not final.")
            self._update(connection, quote, final_revision=None, final_by=None, final_at=None)
            return {"reason": reason, "final_revision": quote.final_revision}

        return self._change(
            quote_id,
            version,
            actor,
            "reopened",
            change,
            revision_of=lambda quote: quote.final_revision,
        )

    # ── Projection ────────────────────────────────────────────────────────

    def reproject(self, key: str, project: Callable[[QuoteRevision], Projection]) -> int:
        """Project again every quote projected by a setting other than *key*'s, from its
        newest revision, through *project*; the version, the time saved and the events are
        left as they are. Answers how many were written: none when another process did
        them first."""
        written = 0
        while True:
            with self._engine.connect() as connection:
                batch = list(
                    connection.execute(
                        select(APP_QUOTES.c.quote_id, APP_QUOTES.c.projection)
                        .where(APP_QUOTES.c.projection != key)
                        .order_by(APP_QUOTES.c.quote_id)
                        .limit(_REPROJECT_BATCH)
                    ).tuples()
                )
            if not batch:
                return written
            for quote_id, was in batch:
                quote = self.get(quote_id)
                projection = project(self.store.revision(quote_id, quote.latest))
                if projection.key != key:
                    raise QuoteStoreError(
                        "A reprojection gave a projection of another setting than the one "
                        "asked for."
                    )
                title = display_title(projection.title, quote.name, quote.reference)
                with self._engine.begin() as connection:
                    moved = connection.execute(
                        update(APP_QUOTES)
                        .where(APP_QUOTES.c.quote_id == quote_id, APP_QUOTES.c.projection == was)
                        .values(
                            title=title,
                            summary=json.dumps(projection.summary(), ensure_ascii=False),
                            search_text=_search_text(
                                quote.reference, quote.name, title, projection.fields
                            ),
                            projection=key,
                        )
                    )
                written += moved.rowcount

    # ── Within a transaction ──────────────────────────────────────────────

    def _change(
        self,
        quote_id: str,
        version: int,
        actor: str,
        kind: EventKind,
        change: Callable[[Connection, AppQuote], dict[str, Any]],
        *,
        archived_ok: bool = False,
        revision_of: Callable[[AppQuote], int | None] | None = None,
    ) -> AppQuote:
        """*change* made to the quote at *version*, with its event of *kind*, naming the
        revision *revision_of* gives, in one transaction. An archived quote is refused unless
        *archived_ok*, which leaves the quote's state to *change* to judge."""
        actor = _check_principal(actor, "Who changes a quote")
        with self._engine.begin() as connection:
            quote = self._current(connection, quote_id, version)
            if archived_ok is False and quote.archived_at is not None:
                raise QuoteLockedError(f"{quote.reference} is archived: restore it to change it.")
            detail = change(connection, quote)
            revision = None if revision_of is None else revision_of(quote)
            self._event(connection, quote_id, actor, kind, revision, detail, at=self._clock())
            return self._get(connection, quote_id)

    def _current(self, connection: Connection, quote_id: str, version: int) -> AppQuote:
        """The quote, refused as stale unless at *version*, naming what moved it on."""
        quote = self._get(connection, quote_id)
        if quote.version != version:
            last = connection.execute(
                select(QUOTE_EVENTS.c.actor, QUOTE_EVENTS.c.kind, QUOTE_EVENTS.c.at)
                .where(QUOTE_EVENTS.c.quote_id == quote_id)
                .order_by(QUOTE_EVENTS.c.id.desc())
                .limit(1)
            ).one()
            raise StaleQuoteError(
                f"{quote.reference} has changed since you opened it: {last.actor} "
                f"{_DONE[last.kind]} it at {last.at.isoformat(timespec='minutes')}."
            )
        return quote

    def _update(self, connection: Connection, quote: AppQuote, **values: Any) -> None:
        """Write *values* to the record and move its version on, over the version read."""
        moved = connection.execute(
            update(APP_QUOTES)
            .where(APP_QUOTES.c.quote_id == quote.id, APP_QUOTES.c.version == quote.version)
            .values(version=quote.version + 1, **values)
        )
        if moved.rowcount != 1:
            raise StaleQuoteError(
                f"{quote.reference} changed while this change was being made: open it again."
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
    ) -> None:
        connection.execute(
            insert(QUOTE_EVENTS).values(
                quote_id=quote_id,
                at=at,
                actor=actor,
                kind=kind,
                revision=revision,
                detail=json.dumps(detail, ensure_ascii=False),
            )
        )

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

    def _operation(
        self, connection: Connection, operation: str, actor: str, quote_id: str | None
    ) -> AppQuote | None:
        """The quote *operation*'s key wrote, when it was written: one repeated after its
        answer was lost. Refused when the key was another person's or another quote's."""
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
        return self._get(connection, row["quote_id"])

    def _remember(
        self, connection: Connection, operation: str, quote_id: str, number: int, actor: str
    ) -> None:
        connection.execute(
            insert(QUOTE_OPERATIONS).values(
                id=operation, quote_id=quote_id, number=number, actor=actor
            )
        )
