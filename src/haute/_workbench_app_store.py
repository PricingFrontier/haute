"""The underwriter app's quote store as it stands (specs/workbench): each use of it open while
the tables it uses fit, refused naming why otherwise, checked again on a bound.

The app starts whatever its store holds. :class:`StoreGate` opens the book through its opener,
``open_book`` for the store's location, as a request first needs it. A book opens on whatever
the database holds, naming each table, column or privilege that does not fit the release's
form, and each use is refused only for those it needs (:meth:`QuoteBook.unfit`): the Quotes
page's list reads ``quote_details`` alone, and a quote every table. A database the gate cannot
reach, a location that cannot be resolved, or no environment named refuse every use. Each
request in the next :data:`RECHECK_SECONDS` is refused with the last check's answer, and the
first after them checks again, so tables put right in SQL are used with no restart and no new
image. Nothing here answers a request: a route turns a :class:`QuoteStoreClosedError` into
its 503, from :attr:`QuoteStoreClosedError.detail`. The store's modules are imported only as
the gate checks, so an app with no store loads none.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute._workbench_config import ENVIRONMENT_ENV, WorkbenchError

if TYPE_CHECKING:
    from haute._quote_store import QuoteBook, StoreMisfit, StoreUse
    from haute.schemas import WorkbenchAppQuoteStore

#: How long a closed store's refusal stands before a request checks it again, in seconds.
RECHECK_SECONDS = 10.0
#: Why an app started with no environment named keeps no quotes.
NO_ENVIRONMENT = (
    "This app keeps no quotes: it was started with no quote store environment named "
    f"({ENVIRONMENT_ENV})."
)
#: Why the quotes closed when a connection failed under a running app.
UNREACHABLE = "The app cannot reach its quote store just now."

logger = get_logger(component="workbench.app.store")


class QuoteStoreClosedError(WorkbenchError):
    """The store cannot be used as a request asks: the refusal's *code*, the message,
    *differences* and, for tables that do not fit, *misfits*, each by its table, its column and
    what is wrong, of *environment*'s database, say why."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        differences: Sequence[str] = (),
        misfits: Sequence[StoreMisfit] = (),
        environment: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.differences = list(differences)
        self.misfits = tuple(misfits)
        self.environment = environment

    @property
    def detail(self) -> dict[str, Any]:
        """What a route answers the refusal with, as its 503's detail."""
        return {
            "message": self.message,
            "error_code": self.code,
            "differences": self.differences,
            "misfits": [
                {"table": misfit.table, "column": misfit.column, "problem": misfit.problem}
                for misfit in self.misfits
            ],
            "environment": self.environment,
        }


#: The failure answers' codes for a SQLite store's file the app cannot use as it is.
_SQLITE_FILE_REFUSALS = frozenset(
    {"quote_store_damaged", "quote_store_busy", "quote_store_read_only"}
)


def _closed_by(exc: BaseException) -> QuoteStoreClosedError | None:
    """The refusal *exc* closes the quotes with, None for an error that is no store's: the
    store's own refusals, a location that cannot be resolved among them, keep their error code
    and differences, and a driver's error before any statement is a store not reached."""
    if isinstance(exc, QuoteStoreClosedError):
        return exc
    from haute._failure_answers import classify_failure
    from haute._quote_store import QuoteStoreError
    from haute._quote_store._store import unreached

    # A SQLite file that is damaged, held by another program or read-only is called what it
    # is, in the words every edge gives it, never "cannot reach".
    answer = classify_failure(exc)
    if answer is not None and answer.code in _SQLITE_FILE_REFUSALS:
        return QuoteStoreClosedError(answer.message, code=answer.code)
    if unreached(exc):
        return QuoteStoreClosedError(UNREACHABLE, code="quote_store_unreachable")
    if not isinstance(exc, QuoteStoreError):
        return None
    return QuoteStoreClosedError(
        exc.message or str(exc),
        code=exc.error_code or "quote_store_invalid",
        differences=getattr(exc, "differences", ()),
        misfits=getattr(exc, "misfits", ()),
        environment=getattr(exc, "environment", None),
    )


def _refusal_for(book: QuoteBook, use: StoreUse) -> QuoteStoreClosedError | None:
    """The refusal of *use* on *book*, naming what of its database does not fit the use; None
    when it fits."""
    mismatch = book.mismatch(use)
    return None if mismatch is None else _closed_by(mismatch)


def no_environment() -> QuoteBook:
    """The opener of an app started with no environment named: its quotes always closed."""
    raise QuoteStoreClosedError(NO_ENVIRONMENT, code="quote_store_environment")


class StoreGate:
    """The app's quote store: the book *opener* opens, each use given it while the tables that
    use needs fit, else the refusal naming why; a store that cannot be used, or does not fit a
    use, checked again at most once every :data:`RECHECK_SECONDS` on *clock*, one check at a
    time."""

    def __init__(
        self,
        opener: Callable[[], QuoteBook],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._opener = opener
        self._clock = clock
        self._lock = threading.Lock()
        self._book: QuoteBook | None = None
        self._refusal: QuoteStoreClosedError | None = None
        self._due = 0.0
        # Whether the last check found anything wrong: the store not used, or a misfit.
        self._wrong = False

    def book(self, use: StoreUse = "quote") -> QuoteBook:
        """The open book for *use*; :class:`QuoteStoreClosedError` while it cannot be used for
        it: the store not used, or what of its database does not fit *use*, as the last check
        found until the bound has passed, as a fresh check finds after."""
        opened = self._book
        if opened is not None and not opened.unfit(use):
            return opened
        with self._lock:
            held = self._book
            if held is not None:
                refusal = _refusal_for(held, use)
                if refusal is None:
                    return held
                if self._clock() < self._due:
                    raise refusal
                # Past the bound the tables may have been put right in SQL: checked again.
                self._shut()
            elif self._refusal is not None and self._clock() < self._due:
                raise self._refusal
            return self._fitting(self._check(), use)

    def recheck(
        self, seen: QuoteBook | None = None, use: StoreUse = "quote"
    ) -> QuoteStoreClosedError | None:
        """Check at once, after a statement the database refused or tables seen to have changed
        under the open book *seen*: the book closed and opened again, None while the tables
        *use* needs still fit, else the refusal that now refuses it. Another request's check
        since *seen* stands, so one slow check answers them all: a book it opened is judged for
        *use*, and a refusal it found is answered."""
        with self._lock:
            if seen is not None and self._book is not seen:
                if self._book is not None:
                    return _refusal_for(self._book, use)
                if self._refusal is not None:
                    return self._refusal
            self._shut()
            try:
                book = self._check()
            except QuoteStoreClosedError as refusal:
                return refusal
            return _refusal_for(book, use)

    def unreachable(self) -> None:
        """Close the quotes as a connection that failed under the running app, checked again
        once the bound has passed."""
        with self._lock:
            self._shut()
            self._close(QuoteStoreClosedError(UNREACHABLE, code="quote_store_unreachable"))

    def state(self) -> WorkbenchAppQuoteStore:
        """The whole store as the release route answers it, and a rollout checks it: open only
        while every table and privilege fits, checked as :meth:`book` checks one quote's use."""
        from haute.schemas import WorkbenchAppQuoteStore

        try:
            self.book("quote")
        except QuoteStoreClosedError as refusal:
            return WorkbenchAppQuoteStore(
                open=False,
                error_code=refusal.code,
                message=refusal.message,
                differences=refusal.differences,
            )
        return WorkbenchAppQuoteStore(open=True, error_code=None, message=None, differences=[])

    def close(self) -> None:
        """Close the book, as the app ends."""
        with self._lock:
            self._shut()

    def _check(self) -> QuoteBook:
        # Under the lock.
        try:
            book = self._opener()
        except Exception as exc:
            # A store that cannot be used refuses every use with its refusal; any other
            # failure is raised on, for the edge to answer.
            refusal = _closed_by(exc)
            if refusal is not None:
                self._close(refusal)
                raise refusal from None
            raise
        was_wrong = self._wrong
        self._book = book
        self._refusal = None
        self._wrong = bool(book.misfits)
        if book.misfits:
            # The uses its misfits refuse are checked again once the bound has passed.
            self._due = self._clock() + RECHECK_SECONDS
            logger.warning(
                "quote_store_misfits", differences=[misfit.text for misfit in book.misfits]
            )
        elif was_wrong:
            logger.info("quote_store_opened")
        return book

    @staticmethod
    def _fitting(book: QuoteBook, use: StoreUse) -> QuoteBook:
        refusal = _refusal_for(book, use)
        if refusal is not None:
            raise refusal
        return book

    def _close(self, refusal: QuoteStoreClosedError) -> None:
        # Under the lock.
        self._refusal = refusal
        self._wrong = True
        self._due = self._clock() + RECHECK_SECONDS
        logger.warning(
            "quote_store_closed", error_code=refusal.code, differences=refusal.differences
        )

    def _shut(self) -> None:
        # Under the lock.
        if self._book is not None:
            self._book.close()
            self._book = None
