"""Every answer field that names a node is an opaque runtime key, and every answer that names
one addresses it (specs/server-api/low-level.md, "Node addresses").

The inventory below lists every position at which an answer names a node, by model and
field (``field{}`` for a mapping keyed by node). A position is ``migrated`` once its field is
a :data:`~haute._node_address_schemas.RuntimeNodeKey` and the answers carrying it address it,
or ``pending`` until its operation family moves onto addresses. Nothing may stay pending when
the package is done.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import BaseModel

import haute._execution_schemas as execution_schemas
import haute._node_address_schemas as address_schemas
import haute.schemas as schemas
from haute._node_address_schemas import RUNTIME_NODE_KEY_MARKER
from scripts.generate_api_contracts import RESPONSE_CONTRACT_GROUPS

MIGRATED = "migrated"
PENDING = "pending"

NODE_KEY_POSITIONS: dict[tuple[str, str], str] = {
    # Frame provenance's endpoints.
    ("RuntimeEndpointPayload", "key"): MIGRATED,
    # Preview.
    ("PreviewNodeResponse", "node_statuses{}"): MIGRATED,
    ("PreviewNodeResponse", "node_columns{}"): MIGRATED,
    ("PreviewNodeResponse", "node_available_columns{}"): MIGRATED,
    ("PreviewNodeResponse", "node_frame_columns{}"): MIGRATED,
    ("PreviewNodeResponse", "node_schema_warnings{}"): MIGRATED,
    ("NodeTimingInfo", "node_id"): MIGRATED,
    ("NodeMemoryInfo", "node_id"): MIGRATED,
    ("PreviewInputsResponse", "input_node_ids"): MIGRATED,
    # Trace.
    ("TraceResultResponse", "target_node_id"): PENDING,
    ("TraceStepResponse", "node_id"): PENDING,
    ("TraceColumnSourceResponse", "node_id"): PENDING,
    ("TraceOmissionResponse", "node_id"): PENDING,
    ("TraceCorrelationDiagnosticResponse", "node_id"): PENDING,
    ("TraceCorrelationDiagnosticResponse", "child_node_id"): PENDING,
    ("TraceCorrelationDiagnosticResponse", "seed_node_ids"): PENDING,
    # Node data, and the answers that carry a data point.
    ("NodeDataPointRef", "producer_node_id"): PENDING,
    ("NodeDataPointResponse", "consumer_node_id"): PENDING,
    # The cached-data list.
    ("CacheNodeEntry", "node_id"): PENDING,
    ("CacheNodeEntry", "reads_from"): PENDING,
    ("CacheNodeEntry", "shares_snapshot_with"): PENDING,
    ("CacheOwnerEntry", "node_id"): PENDING,
    # Explore, modelling and the optimiser.
    ("ExplorePivotResult", "node_id"): PENDING,
    ("TrainEstimateUnavailable", "blocking_node_id"): PENDING,
    ("TrainEstimateResponse", "unbounded_join_node_ids"): PENDING,
    ("OptimiserInputSummary", "node_id"): PENDING,
    # Execution metrics, which ride on many answers.
    ("ExecutionColumnWidthsPayload", "node_id"): PENDING,
    ("ExecutionMemoryPressureEventPayload", "node_id"): PENDING,
    ("ExecutionMetricsPayload", "node_elapsed_ms{}"): PENDING,
    ("ExecutionStageMetricsPayload", "node_id"): PENDING,
    ("ExecutionStrategyBoundaryPayload", "node_id"): PENDING,
    ("ExecutionStrategyDiagnosticPayload", "blocking_node_id"): PENDING,
    ("ExecutionStrategyProjectionCausePayload", "node_id"): PENDING,
    ("ExecutionStrategyProjectionCausePayload", "parent_node_id"): PENDING,
    ("ExecutionStrategyProvenancePayload", "source_node_id"): PENDING,
    ("ExecutionStrategyReasonPayload", "node_id"): PENDING,
    ("ExecutionStrategyReasonPayload", "parent_node_id"): PENDING,
    ("ExecutionWarningPayload", "node_id"): PENDING,
    ("InputPreparationRecordPayload", "node_id"): PENDING,
    ("SharedSnapshotCapturePayload", "node_id"): PENDING,
    ("SharedSnapshotCaptureSkipPayload", "node_id"): PENDING,
    ("SharedSnapshotSeedPayload", "node_id"): PENDING,
}

#: Strings that embed a runtime key and are echoed exactly, never read for a node.
OPAQUE_KEY_STRINGS: frozenset[tuple[str, str]] = frozenset(
    {
        ("NodeDataPointResponse", "slot_key"),
        ("PreviewSeedPlanEntry", "node_id"),
    }
)

_MODULES = (schemas, execution_schemas, address_schemas)


def _models() -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for module in _MODULES:
        for name, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, BaseModel) and member.__module__ == module.__name__:
                found[name] = member
    return found


def _marked(field_schema: Mapping[str, Any], defs: Mapping[str, Any]) -> bool:
    """Whether a field's schema is a node key, a list of them, or a mapping keyed by them."""
    while "$ref" in field_schema:
        field_schema = defs[str(field_schema["$ref"]).rsplit("/", 1)[-1]]
    if field_schema.get(RUNTIME_NODE_KEY_MARKER):
        return True
    if field_schema.get("propertyNames", {}).get(RUNTIME_NODE_KEY_MARKER):
        return True
    items = field_schema.get("items")
    if isinstance(items, Mapping) and _marked(items, defs):
        return True
    return any(_marked(branch, defs) for branch in field_schema.get("anyOf", ()))


def _field_schema(
    model: type[BaseModel], field: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    schema = model.model_json_schema()
    return schema["properties"][field.removesuffix("{}")], schema.get("$defs", {})


@pytest.mark.parametrize(("position", "state"), sorted(NODE_KEY_POSITIONS.items()))
def test_a_listed_position_is_a_node_key_exactly_when_it_is_migrated(
    position: tuple[str, str], state: str
) -> None:
    model_name, field = position
    model = _models()[model_name]
    assert field.removesuffix("{}") in model.model_fields, f"{model_name} has no field {field}"

    field_schema, defs = _field_schema(model, field)

    assert _marked(field_schema, defs) is (state == MIGRATED), (
        f"{model_name}.{field} is {state}: mark it as a RuntimeNodeKey exactly when it moves "
        "onto addresses, and update this inventory in the same commit"
    )


def test_no_field_is_a_node_key_without_being_listed() -> None:
    listed = {(model, field.removesuffix("{}")) for model, field in NODE_KEY_POSITIONS}
    for name, model in _models().items():
        schema = model.model_json_schema()
        defs = schema.get("$defs", {})
        for field, field_schema in schema.get("properties", {}).items():
            if _marked(field_schema, defs) and (name, field) not in listed:
                # A field typed by a listed model (a nested payload) is listed there.
                if "$ref" in field_schema or "items" in field_schema or "anyOf" in field_schema:
                    continue
                pytest.fail(f"{name}.{field} is a RuntimeNodeKey not in NODE_KEY_POSITIONS")


def test_an_opaque_string_stays_a_string() -> None:
    for model_name, field in OPAQUE_KEY_STRINGS:
        field_schema, defs = _field_schema(_models()[model_name], field)
        assert not _marked(field_schema, defs), f"{model_name}.{field} is echoed, not read"


def _answer_roots() -> Iterator[type[BaseModel]]:
    for group in RESPONSE_CONTRACT_GROUPS.values():
        for model in group:
            if isinstance(model, type) and issubclass(model, BaseModel):
                yield model


def _names_a_node_key(schema: Mapping[str, Any]) -> bool:
    defs = schema.get("$defs", {})
    roots = [schema, *defs.values()]
    return any(
        _marked(field_schema, defs)
        for root in roots
        for field_schema in root.get("properties", {}).values()
    )


def test_an_answer_that_names_a_node_key_addresses_it() -> None:
    for model in _answer_roots():
        schema = model.model_json_schema()
        if _names_a_node_key(schema):
            assert "node_addresses" in model.model_fields, (
                f"{model.__name__} names nodes by key and must carry node_addresses"
            )
