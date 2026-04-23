"""In-process pub/sub event bus for Haute server events.

The file watcher publishes typed events (``"graph.update"``,
``"parse.error"``) to the bus; subscribers — most notably the WebSocket
broadcaster — translate each event into
whatever wire format their transport needs.  Decoupling the producer
from the consumer makes the watcher trivial to unit-test and lets new
subscribers (metrics, audit logs, test harnesses) hang off the same
event stream without touching the watcher.

Design choices:

* **Thread-safe.**  ``subscribe`` / ``publish`` / the unsubscribe
  callable all take an :class:`threading.RLock`.  The file-watcher
  runs in an asyncio task while HTTP handlers may spin up threads via
  :func:`asyncio.to_thread`, so concurrent mutation of the handler
  registry is a real concern.
* **Handler-exception isolation.**  A raising handler must not prevent
  subsequent handlers from receiving the same event.  We catch every
  exception, log it via structlog with the handler + event-type
  context, and move on.
* **Event-type routing.**  Handlers are stored in a dict keyed by
  event type, so a subscriber for ``"file.changed"`` never sees
  ``"graph.update"`` traffic.
* **Typed payloads.**  :meth:`EventBus.publish` annotates its payload
  parameter as ``dict[str, Any]`` rather than bare ``Any``.  The point
  of the refactor is to stop smuggling arbitrary shapes across
  process boundaries; constraining the public type at the bus layer
  makes the contract explicit even for callers who don't use
  ``TypedDict``.
* **Unsubscribe via returned callable.**  :meth:`subscribe` returns a
  zero-arg callable that removes the exact handler registration it
  just created.  This is safer than asking callers to remember their
  (event_type, handler) pair for a separate ``unsubscribe`` method —
  two subscribes of the same handler remain independently
  addressable.

Naming convention (keep this consistent across the codebase):

* **Bus event types** use dotted names: ``"graph.update"``,
  ``"parse.error"``, ``"file.changed"``.  The dotted form reads as a
  topic hierarchy and pairs cleanly with any future wildcard routing.
* **structlog event names** (see :mod:`haute._logging`) use
  snake_case: ``"server_bind_non_loopback"``,
  ``"sanitize_name_collision"``, ``"model_cache_hit"``.  These feed
  log aggregators that typically don't treat dots as field
  separators.

The two naming systems are deliberately distinct — a bus event type
is a transport key, a structlog event name is a log identifier."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Literal, TypedDict, overload

from haute._logging import get_logger

logger = get_logger(component="event_bus")


# ---------------------------------------------------------------------------
# Event-type registry
# ---------------------------------------------------------------------------
#
# Every event the bus supports is declared here.  Producers (the
# file-watcher in :mod:`haute.server`) and consumers (the WebSocket
# translator) pay for this verbosity once in exchange for static
# type-checking at every call site: a subscriber that reads a typo'd
# key now fails mypy/pyright, not silently at runtime.
#
# Adding a new event:
#   1. Declare its ``TypedDict`` below.
#   2. Add the string literal to :data:`EventType`.
#   3. Add ``@overload`` rows for :meth:`EventBus.publish` and
#      :meth:`EventBus.subscribe`.
#
# The wide ``dict[str, Any]`` fallback remains for tests and
# experimental events that do not yet warrant a TypedDict.


class GraphUpdatePayload(TypedDict):
    """Emitted by the file-watcher when a pipeline file re-parses cleanly."""

    graph: dict[str, Any]
    source_file: str


class ParseErrorPayload(TypedDict):
    """Emitted by the file-watcher when a pipeline file fails to parse."""

    error: str
    source_file: str


EventType = Literal["graph.update", "parse.error"]

# Wide backstop used at the bus' *implementation* signature and for
# ad-hoc test events.  Producers that publish a typed event should use
# the overloads above so the narrow TypedDict is type-checked.
PayloadType = dict[str, Any]

# A handler is any callable that consumes a payload dict.  We do not
# constrain the return type because most subscribers are fire-and-forget
# side-effect callbacks.
HandlerType = Callable[[PayloadType], None]


class EventBus:
    """Minimal synchronous, thread-safe pub/sub hub.

    Public API:

    * :meth:`subscribe` — register a handler for an event type.  Returns
      a zero-arg unsubscribe callable.
    * :meth:`publish` — fan the payload out to every currently-registered
      handler for that event type.  No subscribers is a silent no-op.

    The bus is intentionally synchronous: handlers execute on the
    thread that called ``publish``.  Async handlers are welcome to
    schedule themselves onto an event loop internally, but the bus
    itself does not juggle coroutines — that keeps the abstraction
    thin enough to reason about without framework glue.
    """

    __slots__ = ("_handlers", "_lock")

    def __init__(self) -> None:
        # Dict keyed by event type; each value is the list of handlers
        # currently registered for that type.  We use a list (not a
        # set) so handler order is preserved — useful when diagnostics
        # subscribers must see the event before lossy aggregators do.
        self._handlers: dict[str, list[HandlerType]] = {}
        # RLock so a handler that re-publishes (recursive dispatch) does
        # not deadlock the bus.  Handler-driven publishes are rare but
        # legitimate — e.g. a translate-then-republish bridge.
        self._lock = threading.RLock()

    @overload
    def subscribe(
        self,
        event_type: Literal["graph.update"],
        handler: Callable[[GraphUpdatePayload], None],
    ) -> Callable[[], None]: ...

    @overload
    def subscribe(
        self,
        event_type: Literal["parse.error"],
        handler: Callable[[ParseErrorPayload], None],
    ) -> Callable[[], None]: ...

    @overload
    def subscribe(self, event_type: str, handler: HandlerType) -> Callable[[], None]: ...

    def subscribe(  # type: ignore[misc]  # noqa: E501 — impl widens payload type for overload dispatch; public API constrained by @overloads above
        self, event_type: str, handler: HandlerType
    ) -> Callable[[], None]:
        """Register *handler* to receive events of type *event_type*.

        Returns a zero-arg callable; invoking it removes *this*
        registration from the bus.  Calling the callable twice is a
        no-op — the second call finds nothing to remove.

        Multiple subscribes of the same handler are independent: each
        returns its own unsubscribe callable and each receives events
        until its callable is invoked.  This matches how most pub/sub
        buses behave and avoids surprising "my first unsubscribe
        silently killed my second subscribe" footguns.
        """
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type)
                if not handlers:
                    return
                try:
                    handlers.remove(handler)
                except ValueError:
                    # Already unsubscribed — treat as idempotent.  The
                    # caller is allowed to call the unsubscribe
                    # callable more than once without caring.
                    return
                if not handlers:
                    # Keep the dict tidy so ``publish`` on a
                    # once-subscribed-now-empty event type is a true
                    # no-op (no iteration at all).
                    self._handlers.pop(event_type, None)

        return _unsubscribe

    @overload
    def publish(self, event_type: Literal["graph.update"], payload: GraphUpdatePayload) -> None: ...

    @overload
    def publish(self, event_type: Literal["parse.error"], payload: ParseErrorPayload) -> None: ...

    @overload
    def publish(self, event_type: str, payload: PayloadType) -> None: ...

    def publish(self, event_type: str, payload: PayloadType) -> None:  # type: ignore[misc]
        """Fan *payload* out to every handler registered for *event_type*.

        Publishing an event type with no subscribers is a no-op — the
        bus deliberately does not treat that as an error.  A fresh
        process has zero subscribers on every channel, so raising
        would just force every producer to guard.

        Handler isolation: if a handler raises, the exception is
        logged (at ``warning`` level with ``event_type`` and the
        handler qualname) and the remaining handlers still run.  The
        exception does *not* propagate to the publisher because a
        single misbehaving subscriber must not silence an event for
        every other subscriber.
        """
        # Snapshot the handler list under the lock so a concurrent
        # subscribe / unsubscribe cannot corrupt our iteration.  The
        # snapshot is a shallow copy of the list; handlers are
        # functions (not mutated) so aliasing is fine.
        with self._lock:
            handlers = list(self._handlers.get(event_type, ()))

        for handler in handlers:
            try:
                handler(payload)
            except Exception:  # noqa: BLE001
                # Breadth deliberate: any handler misbehaviour must be
                # contained at the bus boundary.  We log with
                # ``exc_info=True`` so ops can diagnose without
                # waiting for a reproducible crash, but we do not
                # re-raise — downstream handlers must still fire.
                logger.warning(
                    "event_bus_handler_failed",
                    event_type=event_type,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    exc_info=True,
                )

    def __repr__(self) -> str:
        with self._lock:
            summary = {et: len(hs) for et, hs in self._handlers.items()}
        return f"EventBus(subscribers={summary})"

    # -- Test-isolation hooks -------------------------------------------------
    #
    # Tests that inadvertently subscribe to :data:`default_bus` leak their
    # handler into the rest of the session.  The conftest.py autouse
    # fixture snapshots the handler registry before each test and
    # restores it afterwards, preserving production wiring (the
    # server.py WS subscribers) while evicting anything the test added.
    #
    # Prefer instantiating a fresh ``EventBus()`` inside a test for full
    # isolation; these hooks are a safety net, not the primary pattern.

    def _snapshot_handlers_for_testing(self) -> dict[str, list[HandlerType]]:
        """Deep copy of the current handler registry.  Test-only."""
        with self._lock:
            return {et: list(hs) for et, hs in self._handlers.items()}

    def _restore_handlers_for_testing(self, snapshot: dict[str, list[HandlerType]]) -> None:
        """Replace the handler registry with *snapshot*.  Test-only."""
        with self._lock:
            self._handlers.clear()
            for event_type, hs in snapshot.items():
                if hs:
                    self._handlers[event_type] = list(hs)


# Module-level default bus so producers (file-watcher) and consumers
# (WebSocket broadcaster) can find each other without plumbing a bus
# instance through the lifespan.  Tests that need isolation always
# instantiate a fresh ``EventBus()``; the module-level instance is
# only used by the server's own wiring in ``server.py`` / ``routes/``.
default_bus = EventBus()


__all__ = [
    "EventBus",
    "EventType",
    "GraphUpdatePayload",
    "HandlerType",
    "ParseErrorPayload",
    "PayloadType",
    "default_bus",
]
