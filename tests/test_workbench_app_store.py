"""The underwriter app's quote store as it stands (specs/workbench): each use open while the
tables it uses fit, refused naming why while they do not, every use refused while the store
cannot be reached and without one, and checked again at most once every bound, one check at a
time."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from haute._project_context import context_thread
from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    QuoteStoreUnreachableError,
    StoreMisfit,
    StoreUse,
)
from haute._quote_store._store import store_mismatch
from haute._workbench_app_store import (
    NO_ENVIRONMENT,
    RECHECK_SECONDS,
    QuoteStoreClosedError,
    StoreGate,
    no_environment,
)


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Book:
    """A stand-in for the book a check opens on a database not fitting as *misfits* say, each
    use refused for its own as the store's book refuses it (``QuoteBook.unfit``)."""

    def __init__(self, *misfits: StoreMisfit) -> None:
        self.closed = False
        self.misfits = misfits

    def unfit(self, use: StoreUse) -> tuple[StoreMisfit, ...]:
        if use == "quote":
            return self.misfits
        return tuple(
            m
            for m in self.misfits
            if m.table == "quote_details" and m.privilege in (None, "SELECT")
        )

    def mismatch(self, use: StoreUse) -> QuoteStoreMismatchError | None:
        unfit = self.unfit(use)
        return store_mismatch(unfit, "test") if unfit else None

    def close(self) -> None:
        self.closed = True


class Opener:
    """An opener answering each check in turn: a book, or an error raised."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.checks = 0

    def __call__(self) -> Any:
        self.checks += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _refusal(gate: StoreGate, use: StoreUse = "quote") -> QuoteStoreClosedError:
    with pytest.raises(QuoteStoreClosedError) as refused:
        gate.book(use)
    return refused.value


#: A column of a table only a quote reads, missing.
REGION = StoreMisfit("policy", "region", "is missing", "policy.region is missing")
#: A column of quote_details, which the list reads too, missing.
BROKER = StoreMisfit("quote_details", "broker", "is missing", "quote_details.broker is missing")


def test_a_store_whose_tables_fit_opens() -> None:
    book = Book()
    gate = StoreGate(Opener(book))

    assert gate.book() is book
    state = gate.state()
    assert (state.open, state.error_code, state.message, state.differences) == (
        True,
        None,
        None,
        [],
    )


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (
            QuoteStoreUnreachableError("could not be reached, or it refused the sign-in"),
            "quote_store_unreachable",
        ),
        (OperationalError(None, None, Exception("no route to host")), "quote_store_unreachable"),
        (
            QuoteStoreEnvironmentError("set HAUTE_STORE_DATABASE to its file's path"),
            "quote_store_environment",
        ),
    ],
    ids=["unreachable", "driver", "location"],
)
def test_a_store_that_cannot_open_refuses_every_use_naming_why(
    raised: Exception, code: str
) -> None:
    gate = StoreGate(Opener(raised))

    refusal = _refusal(gate)

    assert refusal.code == code
    assert refusal.detail == {
        "message": refusal.message,
        "error_code": code,
        "differences": [],
        "misfits": [],
        "environment": None,
    }
    assert "no route to host" not in refusal.message
    assert _refusal(gate, "list").code == code
    state = gate.state()
    assert (state.open, state.error_code) == (False, code)


def test_a_store_that_does_not_fit_refuses_only_the_uses_its_misfits_touch() -> None:
    book = Book(REGION)
    gate = StoreGate(Opener(book))

    # The list reads quote_details alone, which fits.
    assert gate.book("list") is book
    refusal = _refusal(gate)
    assert (refusal.code, refusal.differences) == (
        "quote_store_mismatch",
        ["policy.region is missing"],
    )
    assert refusal.detail["misfits"] == [
        {"table": "policy", "column": "region", "problem": "is missing"}
    ]
    assert refusal.detail["environment"] == "test"
    assert "policy.region is missing" in refusal.message
    # The whole store, as a rollout reads it, does not fit.
    state = gate.state()
    assert (state.open, state.error_code, state.differences) == (
        False,
        "quote_store_mismatch",
        ["policy.region is missing"],
    )

    # A misfit of quote_details refuses the list too, naming it alone.
    both = StoreGate(Opener(Book(REGION, BROKER)))
    assert _refusal(both, "list").differences == ["quote_details.broker is missing"]
    assert _refusal(both).differences == [
        "policy.region is missing",
        "quote_details.broker is missing",
    ]


def test_an_app_with_no_store_is_closed_naming_haute_environment() -> None:
    refusal = _refusal(StoreGate(no_environment))

    assert (refusal.code, refusal.message) == ("quote_store_environment", NO_ENVIRONMENT)
    assert "HAUTE_ENVIRONMENT" in NO_ENVIRONMENT


def test_a_store_that_does_not_fit_is_checked_again_only_past_the_bound() -> None:
    clock = Clock()
    first, second, fitting = Book(REGION), Book(REGION), Book()
    opener = Opener(first, second, fitting)
    gate = StoreGate(opener, clock=clock)

    _refusal(gate)
    clock.now += RECHECK_SECONDS - 1
    # Within the bound each quote is answered with the last refusal, nothing checked, and
    # the list keeps the book it fits.
    assert _refusal(gate).code == "quote_store_mismatch"
    assert gate.book("list") is first
    assert gate.state().open is False
    assert opener.checks == 1
    clock.now += 1
    _refusal(gate)
    assert opener.checks == 2 and first.closed
    # Put right in SQL, the next check past the bound opens the quote's use too.
    clock.now += RECHECK_SECONDS
    assert gate.book() is fitting
    assert opener.checks == 3
    assert gate.book() is fitting and gate.book("list") is fitting and opener.checks == 3


def test_a_recheck_refuses_the_uses_of_tables_that_no_longer_fit() -> None:
    first, second, third = Book(), Book(), Book(REGION)
    gate = StoreGate(Opener(first, second, third))
    assert gate.book() is first

    # The tables still fit: the book is opened again and every use stays open.
    assert gate.recheck() is None
    assert first.closed and gate.book() is second
    # They no longer do: a quote's use is refused, the list's not.
    refusal = gate.recheck()
    assert refusal is not None and refusal.code == "quote_store_mismatch"
    assert second.closed
    assert _refusal(gate).code == "quote_store_mismatch"
    assert gate.recheck(third, "list") is None and gate.book("list") is third


def test_a_recheck_for_a_book_opened_again_since_keeps_the_newer_book() -> None:
    first, second, third = Book(), Book(), Book()
    opener = Opener(first, second, third)
    gate = StoreGate(opener)
    assert gate.book() is first
    # Two requests saw the tables change under the first book; the first to check opens the
    # second, and the other's check keeps it rather than opening a third.
    assert gate.recheck(first) is None
    assert gate.recheck(first) is None
    assert gate.book() is second and not second.closed and opener.checks == 2
    assert gate.recheck(second) is None
    assert gate.book() is third


def test_a_recheck_for_a_book_another_check_replaced_answers_that_checks_finding() -> None:
    first = Book()
    opener = Opener(first, Book(REGION))
    gate = StoreGate(opener)
    assert gate.book() is first
    refusal = gate.recheck(first)
    assert refusal is not None and refusal.code == "quote_store_mismatch"
    # A second request refused on the first book is answered with what that check found,
    # rather than waiting on a check of its own.
    again = gate.recheck(first)
    assert again is not None and again.differences == refusal.differences
    assert gate.recheck(first, "list") is None
    assert opener.checks == 2


def test_a_recheck_for_a_book_another_check_closed_answers_that_checks_refusal() -> None:
    first = Book()
    opener = Opener(first, QuoteStoreUnreachableError("could not be reached"))
    gate = StoreGate(opener)
    assert gate.book() is first
    refusal = gate.recheck(first)
    assert refusal is not None and refusal.code == "quote_store_unreachable"
    assert gate.recheck(first) is refusal
    assert opener.checks == 2


def test_an_unreachable_store_stays_closed_until_the_next_check() -> None:
    clock = Clock()
    first, second = Book(), Book()
    opener = Opener(first, second)
    gate = StoreGate(opener, clock=clock)
    assert gate.book() is first

    gate.unreachable()

    assert first.closed
    assert _refusal(gate).code == "quote_store_unreachable"
    assert opener.checks == 1
    clock.now += RECHECK_SECONDS
    assert gate.book() is second


def test_one_check_runs_at_a_time() -> None:
    entered, release = threading.Event(), threading.Event()
    book = Book()
    checks: list[int] = []

    def slow() -> Book:
        checks.append(1)
        entered.set()
        release.wait(5)
        return book

    gate = StoreGate(slow)
    answers: list[Any] = []
    threads = [context_thread(lambda: answers.append(gate.book())) for _ in range(2)]
    threads[0].start()
    assert entered.wait(5)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(5)

    assert answers == [book, book]
    assert checks == [1]


def test_any_other_error_a_check_meets_is_raised_as_it_was() -> None:
    def broken() -> Book:
        raise RuntimeError("a fault of Haute's")

    opener: Callable[[], Any] = broken
    gate = StoreGate(opener)

    with pytest.raises(RuntimeError, match="a fault of Haute's"):
        gate.book()
    # Nothing was closed by it: the next request checks again.
    with pytest.raises(RuntimeError):
        gate.book()


def test_closing_the_gate_closes_its_book() -> None:
    book = Book()
    gate = StoreGate(Opener(book))
    gate.book()

    gate.close()

    assert book.closed
