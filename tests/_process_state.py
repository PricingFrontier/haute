"""Reset or read Haute's process-local state from outside the production modules.

Production code keeps no hooks for tests: each helper here reaches the state the
owning module holds, so a fixture can isolate one test from the next.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

import pytest

from haute._event_bus import EventBus, HandlerType
from haute.routes._supersession import SupersessionCoordinator


def clear_execution_admission_reservations() -> None:
    """Forget every in-flight budget reservation and wake any waiter."""
    from haute import _execution_admission as admission

    with admission._IN_FLIGHT_LOCK:
        admission._IN_FLIGHT_RESERVATIONS.clear()
        admission._IN_FLIGHT_RELEASED.notify_all()


def clear_source_signatures() -> None:
    """Forget every in-process source proof, standing in for a new process.

    Durable records are untouched; a flight in progress keeps its own result.
    """
    from haute._json_shred import _source_proof

    _source_proof._signatures().clear()
    _source_proof._UNAVAILABLE_WARNINGS.clear()


def clear_deploy_artifact_caches() -> None:
    """Drop the deploy scorer's cached models and feature contracts."""
    from haute._mlflow_io import clear_local_model_cache
    from haute.modelling import _feature_contract

    clear_local_model_cache()
    _feature_contract._contract_cache.clear()


def clear_capability_manifest_cache() -> None:
    from haute.assistant import _catalog

    _catalog._MANIFEST_CACHE.clear()


def clear_price_contour_verification() -> None:
    """Forget the verified install so the next call re-imports and re-checks."""
    from haute import _price_contour

    _price_contour._verified.cache_clear()


def reset_input_cache_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the input-cache route fresh job coordination; snapshots are kept."""
    from haute.routes import input_cache
    from haute.routes._background_jobs import CancellableJobRegistry, SingleFlightCoordinator

    with input_cache._start_lock:
        input_cache._store.clear_all()
        monkeypatch.setattr(input_cache, "_jobs", CancellableJobRegistry())
        monkeypatch.setattr(input_cache, "_singleflight", SingleFlightCoordinator())
        monkeypatch.setattr(input_cache, "_active_builds", 0)
        input_cache._building_tables.clear()
        input_cache._source_store.cache_clear()


def event_bus_handlers(bus: EventBus) -> dict[str, list[HandlerType]]:
    """A copy of *bus*'s handler registry, one list per event type."""
    with bus._lock:
        return {event_type: list(handlers) for event_type, handlers in bus._handlers.items()}


def restore_event_bus_handlers(bus: EventBus, handlers: dict[str, list[HandlerType]]) -> None:
    """Replace *bus*'s handler registry with *handlers*, dropping empty lists."""
    with bus._lock:
        bus._handlers.clear()
        for event_type, registered in handlers.items():
            if registered:
                bus._handlers[event_type] = list(registered)


@dataclass(frozen=True, slots=True)
class SupersessionStateSnapshot:
    """Immutable view of one coordinator key's state."""

    latest_generation: int
    active: bool
    references: int


async def supersession_snapshot(
    coordinator: SupersessionCoordinator,
) -> dict[Hashable, SupersessionStateSnapshot]:
    """A copied view of every key *coordinator* currently tracks."""
    async with coordinator._states_lock:
        snapshot: dict[Hashable, SupersessionStateSnapshot] = {}
        for key, state in coordinator._states.items():
            async with state.condition:
                snapshot[key] = SupersessionStateSnapshot(
                    latest_generation=state.latest_generation,
                    active=state.active,
                    references=state.references,
                )
        return snapshot
