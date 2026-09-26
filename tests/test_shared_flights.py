"""OPT-V09B: the single-flight and latest-wins schedulers behind choice queries and point apply."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from haute._execution_context import ExecutionCancellationToken, ExecutionCancelledError
from haute.routes._shared_flights import FlightReplacedError, LatestWinsQueue, SharedFlights

_WAIT = 10.0


class _Gate:
    """A run that records its calls and blocks until released."""

    def __init__(self, value: Any = "done") -> None:
        self.value = value
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.tokens: list[ExecutionCancellationToken] = []

    def __call__(self, token: ExecutionCancellationToken) -> Any:
        self.calls += 1
        self.tokens.append(token)
        self.started.set()
        assert self.release.wait(_WAIT)
        return self.value


def _in_thread(fn: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # recorded for the assertion
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


class TestSharedFlights:
    def test_identical_keys_share_one_run_and_its_result(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        gate = _Gate("shared")
        first = flights.subscribe("k", gate)
        assert gate.started.wait(_WAIT)
        second = flights.subscribe("k", gate)
        gate.release.set()

        assert first.wait(operation="q") == "shared"
        assert second.wait(operation="q") == "shared"
        assert gate.calls == 1

    def test_different_keys_run_independently(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        a, b = _Gate("a"), _Gate("b")
        sub_a = flights.subscribe("a", a)
        sub_b = flights.subscribe("b", b)
        assert a.started.wait(_WAIT) and b.started.wait(_WAIT)
        a.release.set()
        b.release.set()
        assert (sub_a.wait(operation="q"), sub_b.wait(operation="q")) == ("a", "b")

    def test_a_finished_key_is_not_cached(self) -> None:
        flights: SharedFlights[str, int] = SharedFlights()
        calls: list[int] = []

        def run(_token: ExecutionCancellationToken) -> int:
            calls.append(1)
            return len(calls)

        assert flights.subscribe("k", run).wait(operation="q") == 1
        assert flights.subscribe("k", run).wait(operation="q") == 2

    def test_an_error_reaches_every_subscriber(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        started = threading.Event()
        release = threading.Event()

        def run(_token: ExecutionCancellationToken) -> str:
            started.set()
            assert release.wait(_WAIT)
            raise ValueError("boom")

        first = flights.subscribe("k", run)
        assert started.wait(_WAIT)
        second = flights.subscribe("k", run)
        release.set()
        for subscription in (first, second):
            with pytest.raises(ValueError, match="boom"):
                subscription.wait(operation="q")

    def test_a_detaching_subscriber_leaves_the_others_their_result(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        gate = _Gate("kept")
        leaving = flights.subscribe("k", gate)
        staying = flights.subscribe("k", gate)
        assert gate.started.wait(_WAIT)

        leaving.detach()
        assert not gate.tokens[0].cancelled
        gate.release.set()

        assert staying.wait(operation="q") == "kept"
        assert gate.calls == 1

    def test_the_last_detach_cancels_the_run_and_releases_the_key(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        stopped = threading.Event()
        started = threading.Event()

        def cancellable(token: ExecutionCancellationToken) -> str:
            started.set()
            deadline = time.monotonic() + _WAIT
            while not token.cancelled:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            stopped.set()
            return "abandoned"

        only = flights.subscribe("k", cancellable)
        assert started.wait(_WAIT)
        only.detach()
        assert stopped.wait(_WAIT)

        fresh = _Gate("fresh")
        fresh.release.set()
        assert flights.subscribe("k", fresh).wait(operation="q") == "fresh"
        assert fresh.calls == 1

    def test_a_cancelled_waiter_detaches_itself_and_raises_cancelled(self) -> None:
        flights: SharedFlights[str, str] = SharedFlights()
        gate = _Gate("kept")
        token = ExecutionCancellationToken()
        waiting = flights.subscribe("k", gate)
        staying = flights.subscribe("k", gate)
        assert gate.started.wait(_WAIT)

        thread, outcome = _in_thread(lambda: waiting.wait(token, operation="q"))
        token.cancel()
        thread.join(_WAIT)

        assert isinstance(outcome.get("error"), ExecutionCancelledError)
        assert not gate.tokens[0].cancelled
        gate.release.set()
        assert staying.wait(operation="q") == "kept"


class TestLatestWinsQueue:
    def test_one_run_per_group_and_the_replaced_waiter_is_refused(self) -> None:
        """A runs; B waits; C replaces B (409-shaped); C runs after A; B never runs."""
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        a, b, c = _Gate("A"), _Gate("B"), _Gate("C")
        running: list[str] = []
        overlap: list[int] = []
        lock = threading.Lock()

        def tracked(gate: _Gate, name: str) -> Callable[[ExecutionCancellationToken], str]:
            def run(token: ExecutionCancellationToken) -> str:
                with lock:
                    running.append(name)
                    overlap.append(len(running))
                try:
                    return gate(token)
                finally:
                    with lock:
                        running.remove(name)

            return run

        sub_a = queue.subscribe("job", "A", tracked(a, "A"))
        assert a.started.wait(_WAIT)
        sub_b = queue.subscribe("job", "B", tracked(b, "B"))
        sub_c = queue.subscribe("job", "C", tracked(c, "C"))

        with pytest.raises(FlightReplacedError):
            sub_b.wait(operation="q")
        assert not c.started.is_set()

        a.release.set()
        assert sub_a.wait(operation="q") == "A"
        assert c.started.wait(_WAIT)
        c.release.set()
        assert sub_c.wait(operation="q") == "C"
        assert (a.calls, b.calls, c.calls) == (1, 0, 1)
        assert max(overlap) == 1

    def test_requests_for_the_running_or_waiting_key_share_it(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        a, b = _Gate("A"), _Gate("B")
        first_a = queue.subscribe("job", "A", a)
        assert a.started.wait(_WAIT)
        first_b = queue.subscribe("job", "B", b)
        second_a = queue.subscribe("job", "A", a)
        second_b = queue.subscribe("job", "B", b)
        a.release.set()
        b.release.set()

        assert [s.wait(operation="q") for s in (first_a, second_a)] == ["A", "A"]
        assert [s.wait(operation="q") for s in (first_b, second_b)] == ["B", "B"]
        assert (a.calls, b.calls) == (1, 1)

    def test_groups_do_not_wait_for_each_other(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        one, two = _Gate("1"), _Gate("2")
        sub_one = queue.subscribe("job-1", "A", one)
        sub_two = queue.subscribe("job-2", "A", two)
        assert one.started.wait(_WAIT) and two.started.wait(_WAIT)
        one.release.set()
        two.release.set()
        assert (sub_one.wait(operation="q"), sub_two.wait(operation="q")) == ("1", "2")

    def test_a_detaching_subscriber_leaves_a_running_apply_to_finish(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        a = _Gate("A")
        leaving = queue.subscribe("job", "A", a)
        staying = queue.subscribe("job", "A", a)
        assert a.started.wait(_WAIT)

        leaving.detach()
        a.release.set()
        assert staying.wait(operation="q") == "A"
        assert a.calls == 1

    def test_a_running_apply_whose_every_subscriber_left_still_finishes(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        a = _Gate("A")
        finished = threading.Event()

        def run(token: ExecutionCancellationToken) -> str:
            try:
                return a(token)
            finally:
                finished.set()

        queue.subscribe("job", "A", run).detach()
        assert a.started.wait(_WAIT)
        assert not a.tokens[0].cancelled
        a.release.set()
        assert finished.wait(_WAIT)

    def test_a_waiter_every_subscriber_left_is_dropped_before_it_starts(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        a, b = _Gate("A"), _Gate("B")
        sub_a = queue.subscribe("job", "A", a)
        assert a.started.wait(_WAIT)
        queue.subscribe("job", "B", b).detach()
        a.release.set()
        assert sub_a.wait(operation="q") == "A"

        c = _Gate("C")
        c.release.set()
        assert queue.subscribe("job", "C", c).wait(operation="q") == "C"
        assert b.calls == 0

    def test_a_failed_run_still_starts_the_waiter(self) -> None:
        queue: LatestWinsQueue[str, str, str] = LatestWinsQueue()
        started = threading.Event()
        release = threading.Event()

        def failing(_token: ExecutionCancellationToken) -> str:
            started.set()
            assert release.wait(_WAIT)
            raise RuntimeError("apply failed")

        sub_a = queue.subscribe("job", "A", failing)
        assert started.wait(_WAIT)
        b = _Gate("B")
        b.release.set()
        sub_b = queue.subscribe("job", "B", b)
        release.set()

        with pytest.raises(RuntimeError, match="apply failed"):
            sub_a.wait(operation="q")
        assert sub_b.wait(operation="q") == "B"


def test_a_sole_waiter_whose_token_fires_cancels_the_run() -> None:
    flights: SharedFlights[str, str] = SharedFlights()
    started = threading.Event()
    stopped = threading.Event()

    def cancellable(token: ExecutionCancellationToken) -> str:
        started.set()
        deadline = time.monotonic() + _WAIT
        while not token.cancelled:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        stopped.set()
        return "abandoned"

    token = ExecutionCancellationToken()
    only = flights.subscribe("k", cancellable)
    assert started.wait(_WAIT)
    thread, outcome = _in_thread(lambda: only.wait(token, operation="q"))
    token.cancel()
    thread.join(_WAIT)

    assert isinstance(outcome.get("error"), ExecutionCancelledError)
    assert stopped.wait(_WAIT)
