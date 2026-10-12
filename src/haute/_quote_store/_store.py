"""The quote store (specs/workbench): quotes and their revisions on a SQLAlchemy database.

One schema, Haute's, for every database the store runs on; :mod:`._tables` holds it and the
migrations under ``migrations/versions`` bring a database to it. :func:`migrate_store` runs
them, and :func:`open_store` refuses a database they have not brought to the newest, so
nothing migrates as it opens. A revision is written whole in one transaction, the form it
was keyed into with it, and a revision that names one the quote has moved past is refused.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import (
    Connection,
    Engine,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from haute._quote_store._tables import QUOTE_FORMS, QUOTE_REVISIONS, QUOTES, VERSION_TABLE
from haute._workbench_config import WorkbenchError
from haute._workbench_form import Cell, FormSpec, form_revision, render_form
from haute._workbench_tables import _filled

#: The values typed into the sheets: by table id, rows keyed by column id, as typed.
Values = dict[str, list[dict[str, Cell]]]

_VALUES = TypeAdapter(Values)
_MIGRATIONS = Path(__file__).with_name("migrations")


class QuoteStoreError(WorkbenchError):
    """The quote store cannot do what was asked: the message says why."""

    error_code = "quote_store_invalid"


class QuoteStoreNotMigratedError(QuoteStoreError):
    """The database is not at the store's newest schema, so the store does not open it."""

    error_code = "quote_store_not_migrated"


class QuoteNotFoundError(QuoteStoreError):
    """The store holds no such quote, revision or form."""

    error_code = "quote_not_found"


class StaleQuoteError(QuoteStoreError):
    """A revision named one its quote has moved past; nothing of it was written."""

    error_code = "stale_quote_revision"


@dataclass(frozen=True, slots=True)
class QuoteRevision:
    """One revision of a quote, as it was saved."""

    quote_id: str
    number: int
    form_revision: str
    inputs: Values
    request: dict[str, Any]
    #: The response's tables in full, or None when the quote was saved unpriced.
    response: dict[str, Any] | None
    #: The deployed release that priced it; None when the editor priced it, or nothing did.
    release: str | None
    saved_by: str
    saved_at: datetime

    @property
    def digest(self) -> str:
        """What this revision is, beyond its number: two branches can each hold a revision 2
        of one quote, saved apart. A content hash of who saved what, when."""
        from haute._cache import canonical_json
        from haute._hashing import content_hash_bytes

        material = {
            "quote_id": self.quote_id,
            "number": self.number,
            "saved_at": self.saved_at.isoformat(),
            "saved_by": self.saved_by,
            "request": self.request,
            "response": self.response,
        }
        return content_hash_bytes(canonical_json(material).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class QuoteSummary:
    """A quote as a list shows it: its newest revision's number, saver, time and whether
    it was priced."""

    id: str
    name: str
    owner: str
    created_at: datetime
    latest: int
    saved_by: str
    saved_at: datetime
    priced: bool


@dataclass(frozen=True)
class QuoteHistory:
    """A quote whole, as its rows hold it: the quote, every revision in order and the forms
    they name, so it can be compared with another file's and put into it."""

    quote: dict[str, Any]
    revisions: list[dict[str, Any]]
    #: Each form a revision names, by its revision.
    forms: dict[str, str]

    @property
    def name(self) -> str:
        return str(self.quote["name"])


@dataclass(frozen=True, slots=True)
class CarriedValues:
    """A saved quote's values on the current form."""

    #: The values the current form has a place for, by the stable ids.
    values: Values
    #: Each filled value's ``table.column`` the current form has no place for, by the names
    #: it was saved under, in the saved form's order.
    dropped: list[str]


def store_url(path: Path) -> str:
    """The SQLAlchemy URL of a SQLite store at *path*."""
    return f"sqlite:///{path.resolve().as_posix()}"


def _engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite leaves foreign keys off unless each connection turns them on. The rollback
        # journal is required, not assumed: a file left in write-ahead logging would keep
        # saved quotes in a -wal file beside it, which the ledger never commits.
        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                try:
                    mode = cursor.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                except sqlite3.OperationalError as exc:
                    mode = str(exc)
            finally:
                cursor.close()
            if str(mode).lower() != "delete":
                raise QuoteStoreError(
                    f"The quote store's database is in {mode!r} mode and could not be put "
                    "back to a rollback journal: close whatever else has it open and try again."
                )

    return engine


def _alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.attributes["connection"] = connection
    return config


def migrate_store(url: str) -> None:
    """Bring the database at *url* to the store's newest schema; a no-op when it is there."""
    engine = _engine(url)
    try:
        with engine.begin() as connection:
            command.upgrade(_alembic_config(connection), "head")
    finally:
        engine.dispose()


def open_store(url: str, *, clock: Callable[[], datetime] | None = None) -> QuoteStore:
    """The store at *url*, once its database is at the newest schema.

    Raises :class:`QuoteStoreNotMigratedError`, naming the database's version and the newest,
    when it is not: the store never migrates as it opens. *clock* gives the time a quote and
    a revision are saved at; UTC now by default.
    """
    engine = _engine(url)
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
            current = context.get_current_revision()
    except Exception:
        engine.dispose()
        raise
    if current != head:
        engine.dispose()
        raise QuoteStoreNotMigratedError(
            f"The quote store's database is at version {current or 'none'}, not the newest, "
            f"{head}: bring it there with migrate_store before opening it."
        )
    return QuoteStore(engine, clock=clock or (lambda: datetime.now(UTC)))


def _read_revision(connection: Connection, quote_id: str, number: int | None) -> QuoteRevision:
    """Revision *number* of quote *quote_id* on *connection*, its newest when *number* is None."""
    query = select(QUOTE_REVISIONS).where(QUOTE_REVISIONS.c.quote_id == quote_id)
    if number is None:
        query = query.join(
            QUOTES,
            (QUOTES.c.id == QUOTE_REVISIONS.c.quote_id)
            & (QUOTES.c.latest == QUOTE_REVISIONS.c.number),
        )
    else:
        query = query.where(QUOTE_REVISIONS.c.number == number)
    row = connection.execute(query).mappings().one_or_none()
    if row is None:
        what = f"quote {quote_id!r}" if number is None else f"revision {number} of {quote_id!r}"
        raise QuoteNotFoundError(f"The quote store holds no {what}.")
    return QuoteRevision(
        quote_id=row["quote_id"],
        number=row["number"],
        form_revision=row["form_revision"],
        inputs=json.loads(row["inputs"]),
        request=json.loads(row["request"]),
        response=None if row["response"] is None else json.loads(row["response"]),
        release=row["release"],
        saved_by=row["saved_by"],
        saved_at=row["saved_at"],
    )


class QuoteStore:
    """Quotes and their revisions on one database; :func:`open_store` opens one."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime]) -> None:
        self._engine = engine
        self._clock = clock

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> QuoteStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def create(
        self,
        *,
        name: str,
        owner: str,
        saved_by: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
    ) -> QuoteRevision:
        """A new quote, owned by *owner*, with its first revision."""
        quote_id = str(uuid.uuid4())
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(
                insert(QUOTES).values(id=quote_id, name=name, owner=owner, created_at=now, latest=1)
            )
            return self._write_revision(
                connection,
                quote_id,
                1,
                saved_by=saved_by,
                saved_at=now,
                form=form,
                inputs=inputs,
                request=request,
                response=response,
                release=release,
            )

    def save_revision(
        self,
        quote_id: str,
        *,
        after: int,
        after_digest: str | None = None,
        saved_by: str,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
    ) -> QuoteRevision:
        """The revision following revision *after* of quote *quote_id*.

        Raises :class:`StaleQuoteError` when the quote's newest revision is not *after*, or,
        given *after_digest*, is a revision *after* other than the one that digest names (one
        saved apart on another branch); :class:`QuoteNotFoundError` when there is no such
        quote. Nothing is written either way.
        """
        with self._engine.begin() as connection:
            moved = connection.execute(
                update(QUOTES)
                .where(QUOTES.c.id == quote_id, QUOTES.c.latest == after)
                .values(latest=after + 1)
            )
            if moved.rowcount != 1:
                latest = connection.execute(
                    select(QUOTES.c.latest).where(QUOTES.c.id == quote_id)
                ).scalar_one_or_none()
                if latest is None:
                    raise QuoteNotFoundError(f"The quote store holds no quote {quote_id!r}.")
                raise StaleQuoteError(
                    f"Quote {quote_id!r} has moved on since revision {after}: its newest is "
                    f"{latest}. Open it again to save over the newest."
                )
            if after_digest is not None and _read_revision(connection, quote_id, after).digest != (
                after_digest
            ):
                # Raised inside the transaction, so the move above is rolled back.
                raise StaleQuoteError(
                    f"Quote {quote_id!r} revision {after} is not the one that was read: it was "
                    "saved apart, on another branch. Open it again to save over it."
                )
            return self._write_revision(
                connection,
                quote_id,
                after + 1,
                saved_by=saved_by,
                saved_at=self._clock(),
                form=form,
                inputs=inputs,
                request=request,
                response=response,
                release=release,
            )

    def revision(self, quote_id: str, number: int | None = None) -> QuoteRevision:
        """Revision *number* of quote *quote_id*, its newest when *number* is None."""
        with self._engine.connect() as connection:
            return _read_revision(connection, quote_id, number)

    def form(self, revision: str) -> FormSpec:
        """The form a quote was keyed into, by its *revision*."""
        with self._engine.connect() as connection:
            document = connection.execute(
                select(QUOTE_FORMS.c.document).where(QUOTE_FORMS.c.revision == revision)
            ).scalar_one_or_none()
        if document is None:
            raise QuoteNotFoundError(f"The quote store holds no form {revision!r}.")
        return FormSpec.model_validate(json.loads(document))

    def quotes(self, *, owner: str | None = None) -> list[QuoteSummary]:
        """The quotes, *owner*'s alone when given, the most recently saved first."""
        query = (
            select(
                QUOTES,
                QUOTE_REVISIONS.c.saved_by,
                QUOTE_REVISIONS.c.saved_at,
                QUOTE_REVISIONS.c.response.is_not(None).label("priced"),
            )
            .join(
                QUOTE_REVISIONS,
                (QUOTE_REVISIONS.c.quote_id == QUOTES.c.id)
                & (QUOTE_REVISIONS.c.number == QUOTES.c.latest),
            )
            .order_by(QUOTE_REVISIONS.c.saved_at.desc(), QUOTES.c.id)
        )
        if owner is not None:
            query = query.where(QUOTES.c.owner == owner)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            QuoteSummary(
                id=row["id"],
                name=row["name"],
                owner=row["owner"],
                created_at=row["created_at"],
                latest=row["latest"],
                saved_by=row["saved_by"],
                saved_at=row["saved_at"],
                priced=bool(row["priced"]),
            )
            for row in rows
        ]

    def histories(self) -> dict[str, QuoteHistory]:
        """Every quote whole, by id."""
        with self._engine.connect() as connection:
            quotes = connection.execute(select(QUOTES)).mappings().all()
            revisions = (
                connection.execute(
                    select(QUOTE_REVISIONS).order_by(
                        QUOTE_REVISIONS.c.quote_id, QUOTE_REVISIONS.c.number
                    )
                )
                .mappings()
                .all()
            )
            # Iterated, not passed to dict(): a result's keys() would make it read as a mapping.
            forms = {
                revision: document
                for revision, document in connection.execute(
                    select(QUOTE_FORMS.c.revision, QUOTE_FORMS.c.document)
                ).tuples()
            }
        by_quote: dict[str, list[dict[str, Any]]] = {}
        for revision in revisions:
            by_quote.setdefault(revision["quote_id"], []).append(dict(revision))
        histories: dict[str, QuoteHistory] = {}
        for quote in quotes:
            held = by_quote.get(quote["id"], [])
            named = {revision["form_revision"] for revision in held}
            histories[quote["id"]] = QuoteHistory(
                quote=dict(quote),
                revisions=held,
                forms={key: forms[key] for key in sorted(named)},
            )
        return histories

    def put_history(self, history: QuoteHistory) -> None:
        """Put *history* in place of whatever this store holds of its quote, in one write."""
        quote_id = history.quote["id"]
        with self._engine.begin() as connection:
            for revision, document in history.forms.items():
                _keep_form(connection, revision, document)
            connection.execute(
                delete(QUOTE_REVISIONS).where(QUOTE_REVISIONS.c.quote_id == quote_id)
            )
            connection.execute(delete(QUOTES).where(QUOTES.c.id == quote_id))
            connection.execute(insert(QUOTES).values(**history.quote))
            if history.revisions:
                connection.execute(insert(QUOTE_REVISIONS), history.revisions)

    def delete(self, quote_id: str) -> None:
        """Remove quote *quote_id* and its revisions; the forms they name stay kept.

        Raises :class:`QuoteNotFoundError` when there is no such quote.
        """
        with self._engine.begin() as connection:
            connection.execute(
                delete(QUOTE_REVISIONS).where(QUOTE_REVISIONS.c.quote_id == quote_id)
            )
            removed = connection.execute(delete(QUOTES).where(QUOTES.c.id == quote_id))
            if removed.rowcount != 1:
                raise QuoteNotFoundError(f"The quote store holds no quote {quote_id!r}.")

    def _write_revision(
        self,
        connection: Connection,
        quote_id: str,
        number: int,
        *,
        saved_by: str,
        saved_at: datetime,
        form: FormSpec,
        inputs: Values,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        release: str | None,
    ) -> QuoteRevision:
        try:
            values = _VALUES.validate_python(inputs, strict=True)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise QuoteStoreError(
                f"The quote's values are not as the sheets keep them ({where}: {first['msg']})."
            ) from exc
        document = render_form(form)
        revision = form_revision(document.encode("utf-8"))
        _keep_form(connection, revision, document)
        connection.execute(
            insert(QUOTE_REVISIONS).values(
                quote_id=quote_id,
                number=number,
                form_revision=revision,
                inputs=json.dumps(values, ensure_ascii=False),
                request=json.dumps(request, ensure_ascii=False),
                response=None if response is None else json.dumps(response, ensure_ascii=False),
                release=release,
                saved_by=saved_by,
                saved_at=saved_at,
            )
        )
        return QuoteRevision(
            quote_id=quote_id,
            number=number,
            form_revision=revision,
            inputs=values,
            request=request,
            response=response,
            release=release,
            saved_by=saved_by,
            saved_at=saved_at,
        )


def _keep_form(connection: Connection, revision: str, document: str) -> None:
    """Keep the form under its revision, once however many quotes name it."""
    if connection.dialect.name == "sqlite":
        connection.execute(
            sqlite_insert(QUOTE_FORMS)
            .values(revision=revision, document=document)
            .on_conflict_do_nothing(index_elements=["revision"])
        )
        return
    kept = connection.execute(
        select(func.count()).select_from(QUOTE_FORMS).where(QUOTE_FORMS.c.revision == revision)
    ).scalar_one()
    if not kept:
        connection.execute(insert(QUOTE_FORMS).values(revision=revision, document=document))


def carry_values(values: Values, saved: FormSpec, current: FormSpec) -> CarriedValues:
    """*values*, typed into *saved*, as the *current* form holds them.

    Values follow the stable ids, so a renamed table or column keeps them. A filled value
    whose input table or column the current form no longer has is named as ``table.column``
    by the names it was saved under; a blank one is no loss.
    """
    places = {
        table.id: {column.id for column in table.columns}
        for table in current.data_schema.tables
        if table.role == "input"
    }
    carried: Values = {}
    dropped: list[str] = []
    for table in saved.data_schema.tables:
        rows = values.get(table.id)
        if not rows:
            continue
        columns = places.get(table.id, set())
        for column in table.columns:
            if column.id not in columns and any(_filled(row.get(column.id)) for row in rows):
                dropped.append(f"{table.name}.{column.name}")
        if table.id in places:
            kept = [{key: value for key, value in row.items() if key in columns} for row in rows]
            if any(kept):
                carried[table.id] = kept
    return CarriedValues(carried, dropped)
