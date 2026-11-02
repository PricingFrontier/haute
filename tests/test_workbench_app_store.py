"""The underwriter app's quote store as it stands (specs/workbench): open while its tables fit,
closed naming why while they do not, while the store cannot be reached and without one, and
checked again at most once every bound, one check at a time."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from haute._quote_store import (
    QuoteStoreEnvironmentError,
    QuoteStoreMismatchError,
    QuoteStoreUnreachableError,
)
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
    """A stand-in for the book a check opens."""

    def __init__(self) -> None:
        self.closed = False

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


def _refusal(gate: StoreGate) -> QuoteStoreClosedError:
    with pytest.raises(QuoteStoreClosedError) as refused:
        gate.book()
    return refused.value


MISMATCH = QuoteStoreMismatchError(
    "This database's tables do not fit the form: policy.region is missing.",
    differences=["policy.region is missing"],
)


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
    ("raised", "code", "differences"),
    [
        (MISMATCH, "quote_store_mismatch", ["policy.region is missing"]),
        (
            QuoteStoreUnreachableError("could not be reached, or it refused the sign-in"),
            "quote_store_unreachable",
            [],
        ),
        (
            OperationalError(None, None, Exception("no route to host")),
            "quote_store_unreachable",
            [],
        ),
        (
            QuoteStoreEnvironmentError("set HAUTE_STORE_DATABASE to its file's path"),
            "quote_store_environment",
            [],
        ),
    ],
    ids=["tables", "unreachable", "driver", "location"],
)
def test_a_store_that_cannot_open_closes_naming_why(
    raised: Exception, code: str, differences: list[str]
) -> None:
    gate = StoreGate(Opener(raised))

    refusal = _refusal(gate)

    assert (refusal.code, refusal.differences) == (code, differences)
    assert refusal.detail == {
        "message": refusal.message,
        "error_code": code,
        "differences": differences,
    }
    assert "no route to host" not in refusal.message
    state = gate.state()
    assert (state.open, state.error_code) == (False, code)


def test_an_app_with_no_store_is_closed_naming_haute_environment() -> None:
    refusal = _refusal(StoreGate(no_environment))

    assert (refusal.code, refusal.message) == ("quote_store_environment", NO_ENVIRONMENT)
    assert "HAUTE_ENVIRONMENT" in NO_ENVIRONMENT


def test_a_closed_store_is_checked_again_only_past_the_bound() -> None:
    clock = Clock()
    book = Book()
    opener = Opener(MISMATCH, MISMATCH, book)
    gate = StoreGate(opener, clock=clock)

    _refusal(gate)
    clock.now += RECHECK_SECONDS - 1
    # Within the bound each request is answered with the last refusal, nothing checked.
    assert _refusal(gate).code == "quote_store_mismatch"
    assert gate.state().open is False
    assert opener.checks == 1
    clock.now += 1
    _refusal(gate)
    assert opener.checks == 2
    # Put right, the next check past the bound opens the quotes.
    clock.now += RECHECK_SECONDS
    assert gate.book() is book
    assert opener.checks == 3
    assert gate.book() is book and opener.checks == 3


def test_a_recheck_closes_a_store_whose_tables_no_longer_fit() -> None:
    first, second = Book(), Book()
    gate = StoreGate(Opener(first, second, MISMATCH))
    assert gate.book() is first

    # The tables still fit: the book is opened again and the quotes stay open.
    assert gate.recheck() is None
    assert first.closed and gate.book() is second
    # They no longer do: the refusal closes them.
    refusal = gate.recheck()
    assert refusal is not None and refusal.code == "quote_store_mismatch"
    assert second.closed
    assert _refusal(gate).code == "quote_store_mismatch"


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
    threads = [threading.Thread(target=lambda: answers.append(gate.book())) for _ in range(2)]
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
