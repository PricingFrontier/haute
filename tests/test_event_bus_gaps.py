"""Targeted coverage for :mod:`haute._event_bus` edge branches.

The decoupling-contract suite (``test_decoupling_contracts.py``) pins the
*public* pub/sub contract.  This file fills the remaining
implementation-detail gaps that those behaviour tests don't exercise:

* the unsubscribe callable's idempotency / empty-list early returns,
* ``__repr__`` formatting,
* the test-isolation snapshot/restore hooks (including the ``if hs``
  skip-empty branch in :meth:`EventBus._restore_handlers_for_testing`).
"""

from __future__ import annotations

from haute._event_bus import EventBus, PipelineDocumentUpdatePayload


def test_pipeline_document_update_payload_declares_complete_wire_contract() -> None:
    """Static publisher checks must cover every field sent to WebSocket clients."""
    assert PipelineDocumentUpdatePayload.__required_keys__ == {
        "document",
        "document_fingerprint",
        "source_file",
    }


def test_unsubscribe_is_idempotent() -> None:
    """Calling the unsubscribe callable twice is a no-op (handlers.remove
    raises ValueError on the second call and is swallowed)."""
    bus = EventBus()
    calls: list[object] = []
    unsubscribe = bus.subscribe("x", calls.append)

    bus.publish("x", {"n": 1})
    assert calls == [{"n": 1}]

    unsubscribe()
    # Second invocation finds the handler already gone and returns quietly.
    unsubscribe()

    bus.publish("x", {"n": 2})
    assert calls == [{"n": 1}]


def test_unsubscribe_on_empty_event_type_returns_early() -> None:
    """If the whole event type was already popped, the unsubscribe callable
    hits the ``if not handlers`` guard and returns without raising."""
    bus = EventBus()
    handler: list[object] = []
    unsub_a = bus.subscribe("y", handler.append)
    unsub_b = bus.subscribe("y", handler.append)

    # First unsub leaves one handler; the type is still present.
    unsub_a()
    # Second unsub removes the last handler and pops the now-empty type.
    unsub_b()
    assert "y" not in bus._handlers

    # A third unsubscribe against the popped type takes the early return.
    unsub_a()
    assert "y" not in bus._handlers


def test_unsubscribe_keeps_other_registrations_of_same_handler() -> None:
    """Two subscribes of the same handler are independently addressable:
    unsubscribing one leaves the other delivering events."""
    bus = EventBus()
    calls: list[object] = []
    unsub_first = bus.subscribe("z", calls.append)
    bus.subscribe("z", calls.append)

    unsub_first()
    bus.publish("z", {"v": 1})
    # One registration survives, so the payload is delivered exactly once.
    assert calls == [{"v": 1}]


def test_repr_reports_subscriber_counts_per_event_type() -> None:
    """``__repr__`` summarises the per-event-type handler counts."""
    bus = EventBus()
    assert repr(bus) == "EventBus(subscribers={})"

    bus.subscribe("pipeline.document.update", lambda _p: None)
    bus.subscribe("pipeline.document.update", lambda _p: None)
    bus.subscribe("parse.error", lambda _p: None)

    assert repr(bus) == (
        "EventBus(subscribers={'pipeline.document.update': 2, 'parse.error': 1})"
    )


def test_snapshot_is_a_deep_copy_of_the_registry() -> None:
    """The snapshot copies each handler list, so later mutation of the live
    registry does not bleed into the captured snapshot."""
    bus = EventBus()
    bus.subscribe("a", lambda _p: None)

    snapshot = bus._snapshot_handlers_for_testing()
    assert list(snapshot) == ["a"]
    assert len(snapshot["a"]) == 1

    # Add another handler after snapshotting; snapshot stays at one.
    bus.subscribe("a", lambda _p: None)
    assert len(snapshot["a"]) == 1


def test_restore_replaces_registry_and_skips_empty_lists() -> None:
    """Restore clears the live registry then re-installs only the non-empty
    handler lists from the snapshot (the ``if hs`` branch drops empties)."""
    bus = EventBus()
    delivered: list[object] = []
    bus.subscribe("kept", delivered.append)
    snapshot = bus._snapshot_handlers_for_testing()

    # Mutate the live bus away from the snapshot.
    bus.subscribe("transient", lambda _p: None)
    assert "transient" in bus._handlers

    # Snapshot carries an empty list for "empty"; restore must skip it.
    snapshot["empty"] = []
    bus._restore_handlers_for_testing(snapshot)

    assert "transient" not in bus._handlers
    assert "empty" not in bus._handlers
    assert "kept" in bus._handlers

    # The restored handler still works.
    bus.publish("kept", {"ok": True})
    assert delivered == [{"ok": True}]


def test_restore_with_empty_snapshot_clears_everything() -> None:
    """Restoring from an empty snapshot wipes the live registry."""
    bus = EventBus()
    bus.subscribe("a", lambda _p: None)
    bus._restore_handlers_for_testing({})
    assert bus._handlers == {}
