"""Shared runs for OPT-V09B: single-flight queries and the latest-wins point queue.

A *flight* is one run of a callable on its own daemon thread, whose outcome
every subscriber shares. Each caller holds its own :class:`FlightSubscription`
and waits on it; a caller that leaves (its cancellation token fires, or it
detaches) drops only its own subscription, never the run another caller is
waiting for.

- :class:`SharedFlights` runs one flight per key. When its last subscriber
  leaves, the flight's cancellation token is cancelled and the key released,
  so a later identical request starts afresh; a finished key is released at
  once (outcomes are not cached).
- :class:`LatestWinsQueue` runs at most one flight per group, with one waiting
  slot. A request for another key while one runs takes the slot and fails the
  waiter it replaces with :class:`FlightReplacedError`. A running flight is
  never cancelled (the work it wraps cannot be interrupted); a waiter every
  subscriber left is dropped before it starts.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from concurrent.futures import CancelledError, Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Generic, TypeVar

from haute._execution_context import ExecutionCancellationToken

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)
G = TypeVar("G", bound=Hashable)

_WAIT_SLICE_SECONDS = 0.05

Run = Callable[[ExecutionCancellationToken], T]


class FlightReplacedError(RuntimeError):
    """A newer request for the same group took this waiting request's place."""


class _Flight(Generic[T]):
    """One run and the subscribers sharing its outcome; guarded by its owner's lock."""

    def __init__(self, key: Hashable, run: Run[T], *, group: Hashable = None) -> None:
        self.key = key
        self.group = group
        self.run = run
        self.token = ExecutionCancellationToken()
        self.outcome: Future[T] = Future()
        self.subscribers: set[FlightSubscription[T]] = set()

    def start(self, on_done: Callable[[_Flight[T]], None]) -> None:
        def target() -> None:
            try:
                value = self.run(self.token)
            except BaseException as exc:  # handed to every subscriber
                self.outcome.set_exception(exc)
            else:
                self.outcome.set_result(value)
            finally:
                on_done(self)

        threading.Thread(target=target, name=f"flight-{self.key!r}", daemon=True).start()


class FlightSubscription(Generic[T]):
    """One caller's share of a flight's outcome."""

    def __init__(self, flight: _Flight[T], on_detach: Callable[[FlightSubscription[T]], None]):
        self._flight = flight
        self._on_detach = on_detach
        self._detached = False

    def wait(
        self,
        cancellation_token: ExecutionCancellationToken | None = None,
        *,
        operation: str,
    ) -> T:
        """Return the shared outcome, raising the run's error.

        A fired *cancellation_token* detaches this subscription and raises
        ``ExecutionCancelledError`` for *operation*; the run carries on for
        any other subscriber.
        """
        while True:
            if cancellation_token is not None and cancellation_token.cancelled:
                self.detach()
                cancellation_token.throw_if_cancelled(operation)
            try:
                return self._flight.outcome.result(timeout=_WAIT_SLICE_SECONDS)
            except FutureTimeoutError:
                continue
            except CancelledError:
                # Only a dropped waiter's outcome is cancelled, and only once its
                # last subscriber has left; a subscriber still waiting never sees it.
                raise RuntimeError("Waited on a flight that was dropped.") from None

    def detach(self) -> None:
        """Leave the flight; the other subscribers keep waiting on it."""
        if self._detached:
            return
        self._detached = True
        self._on_detach(self)


class SharedFlights(Generic[K, T]):
    """One run per key, shared by every concurrent request for that key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[Hashable, _Flight[T]] = {}

    def subscribe(self, key: K, run: Run[T]) -> FlightSubscription[T]:
        """Join the run for *key*, starting it with *run* when none is in flight."""
        with self._lock:
            flight = self._flights.get(key)
            start = flight is None
            if flight is None:
                flight = self._flights[key] = _Flight(key, run)
            subscription = FlightSubscription(flight, self._detach)
            flight.subscribers.add(subscription)
        if start:
            flight.start(self._finished)
        return subscription

    def _release_locked(self, flight: _Flight[T]) -> None:
        if self._flights.get(flight.key) is flight:
            del self._flights[flight.key]

    def _finished(self, flight: _Flight[T]) -> None:
        with self._lock:
            self._release_locked(flight)

    def _detach(self, subscription: FlightSubscription[T]) -> None:
        flight = subscription._flight
        with self._lock:
            flight.subscribers.discard(subscription)
            if flight.subscribers or flight.outcome.done():
                return
            self._release_locked(flight)
        flight.token.cancel()


class _Lane(Generic[T]):
    """One group's running flight and its single waiting slot."""

    def __init__(self) -> None:
        self.running: _Flight[T] | None = None
        self.waiting: _Flight[T] | None = None


class LatestWinsQueue(Generic[G, K, T]):
    """At most one run per group; one waiting slot, taken by the latest other request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lanes: dict[Hashable, _Lane[T]] = {}

    def subscribe(self, group: G, key: K, run: Run[T]) -> FlightSubscription[T]:
        """Join *group*'s run or waiter for *key*, or queue *run* as the latest request."""
        replaced: _Flight[T] | None = None
        start: _Flight[T] | None = None
        with self._lock:
            lane = self._lanes.setdefault(group, _Lane())
            if lane.running is not None and lane.running.key == key:
                flight = lane.running
            elif lane.waiting is not None and lane.waiting.key == key:
                flight = lane.waiting
            else:
                flight = _Flight(key, run, group=group)
                if lane.running is None:
                    lane.running = start = flight
                else:
                    replaced, lane.waiting = lane.waiting, flight
            subscription = FlightSubscription(flight, self._detach)
            flight.subscribers.add(subscription)
        if replaced is not None:
            replaced.outcome.set_exception(
                FlightReplacedError(
                    f"A newer request replaced the waiting request for {replaced.key!r}."
                )
            )
        if start is not None:
            start.start(self._finished)
        return subscription

    def _finished(self, flight: _Flight[T]) -> None:
        with self._lock:
            lane = self._lanes[flight.group]
            next_flight, lane.running, lane.waiting = lane.waiting, lane.waiting, None
            if next_flight is None:
                del self._lanes[flight.group]
        if next_flight is not None:
            next_flight.start(self._finished)

    def _detach(self, subscription: FlightSubscription[T]) -> None:
        flight = subscription._flight
        with self._lock:
            flight.subscribers.discard(subscription)
            lane = self._lanes.get(flight.group)
            if flight.subscribers or lane is None or lane.waiting is not flight:
                return
            lane.waiting = None
        flight.outcome.cancel()
