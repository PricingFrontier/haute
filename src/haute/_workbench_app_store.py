"""The underwriter app's quote store as it stands (specs/workbench): open, or closed naming why,
checked again on a bound until its tables fit.

The app starts whatever its store holds. :class:`StoreGate` opens the book through its opener,
``open_book`` for the store's location, as a request first needs it, and, while it cannot,
keeps the refusal: a database whose catalogue does not hold the release's form's tables, a
sign-in lacking a privilege the store needs, a database it cannot reach, a location that cannot
be resolved, or no environment named. Each request in the next :data:`RECHECK_SECONDS` is
refused with it, and the first after them checks again, so tables put right in SQL open the
quotes with no restart and no new image. Nothing here answers a request: a route turns a
:class:`QuoteStoreClosedError` into its 503, from :attr:`QuoteStoreClosedError.detail`. The
store's modules are imported only as the gate checks, so an app with no store loads none.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute._workbench_config import ENVIRONMENT_ENV, WorkbenchError

if TYPE_CHECKING:
    from haute._quote_store import QuoteBook
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
    """The app's quotes are closed: the refusal's *code*, the message and *differences* say
    why."""

    def __init__(self, message: str, *, code: str, differences: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.differences = list(differences)

    @property
    def detail(self) -> dict[str, Any]:
        """What a route answers the refusal with, as its 503's detail."""
        return {
            "message": self.message,
            "error_code": self.code,
            "differences": self.differences,
        }


def _closed_by(exc: BaseException) -> QuoteStoreClosedError | None:
    """The refusal *exc* closes the quotes with, None for an error that is no store's: the
    store's own refusals, a location that cannot be resolved among them, keep their error code
    and differences, and a driver's error before any statement is a store not reached."""
    if isinstance(exc, QuoteStoreClosedError):
        return exc
    from haute._quote_store import QuoteStoreError
    from haute._quote_store._store import unreached

    if unreached(exc):
        return QuoteStoreClosedError(UNREACHABLE, code="quote_store_unreachable")
    if not isinstance(exc, QuoteStoreError):
        return None
    return QuoteStoreClosedError(
        exc.message or str(exc),
        code=exc.error_code or "quote_store_invalid",
        differences=getattr(exc, "differences", ()),
    )


def no_environment() -> QuoteBook:
    """The opener of an app started with no environment named: its quotes always closed."""
    raise QuoteStoreClosedError(NO_ENVIRONMENT, code="quote_store_environment")


class StoreGate:
    """The app's quote store: the book *opener* opens while its database fits, else the
    refusal that closed it, checked again at most once every :data:`RECHECK_SECONDS` on
    *clock*, one check at a time."""

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

    def book(self) -> QuoteBook:
        """The open book; :class:`QuoteStoreClosedError` while the quotes are closed, the last
        check's refusal until the bound has passed, a fresh check's after."""
        opened = self._book
        if opened is not None:
            return opened
        with self._lock:
            if self._book is not None:
                return self._book
            if self._refusal is not None and self._clock() < self._due:
                raise self._refusal
            return self._check()

    def recheck(self) -> QuoteStoreClosedError | None:
        """Check at once, after a statement the database refused: the book closed and opened
        again, None while the tables still fit, else the refusal that now closes them."""
        with self._lock:
            self._shut()
            try:
                self._check()
            except QuoteStoreClosedError as refusal:
                return refusal
            return None

    def unreachable(self) -> None:
        """Close the quotes as a connection that failed under the running app, checked again
        once the bound has passed."""
        with self._lock:
            self._shut()
            self._close(QuoteStoreClosedError(UNREACHABLE, code="quote_store_unreachable"))

    def state(self) -> WorkbenchAppQuoteStore:
        """The quotes as the release route answers them, checked as :meth:`book` checks."""
        from haute.schemas import WorkbenchAppQuoteStore

        try:
            self.book()
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
            self._book = self._opener()
        except Exception as exc:
            refusal = _closed_by(exc)
            if refusal is None:
                raise
            self._close(refusal)
            raise refusal from None
        if self._refusal is not None:
            logger.info("quote_store_opened")
        self._refusal = None
        return self._book

    def _close(self, refusal: QuoteStoreClosedError) -> None:
        # Under the lock.
        self._refusal = refusal
        self._due = self._clock() + RECHECK_SECONDS
        logger.warning(
            "quote_store_closed", error_code=refusal.code, differences=refusal.differences
        )

    def _shut(self) -> None:
        # Under the lock.
        if self._book is not None:
            self._book.close()
            self._book = None
