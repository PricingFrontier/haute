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
from haute._node_addresses import named_keys
from haute._types import PipelineGraph
from scripts.generate_api_contracts import RESPONSE_CONTRACT_GROUPS
from tests._preview_targets import node_target, occurrence_output_target
from tests._project import project_app
from tests.test_submodel_instances import _reuse_graph

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
    ("TraceResultResponse", "target_node_id"): MIGRATED,
    ("TraceStepResponse", "node_id"): MIGRATED,
    ("TraceColumnSourceResponse", "node_id"): MIGRATED,
    ("TraceOmissionResponse", "node_id"): MIGRATED,
    ("TraceCorrelationDiagnosticResponse", "node_id"): MIGRATED,
    ("TraceCorrelationDiagnosticResponse", "child_node_id"): MIGRATED,
    ("TraceCorrelationDiagnosticResponse", "seed_node_ids"): MIGRATED,
    # Node data, and the answers that carry a data point.
    ("NodeDataPointRef", "producer_node_id"): MIGRATED,
    ("NodeDataPointResponse", "consumer_node_id"): MIGRATED,
    # The cached-data list.
    ("CacheNodeEntry", "node_id"): PENDING,
    ("CacheNodeEntry", "reads_from"): PENDING,
    ("CacheNodeEntry", "shares_snapshot_with"): PENDING,
    ("CacheOwnerEntry", "node_id"): PENDING,
    # Modelling.
    ("TrainEstimateUnavailable", "blocking_node_id"): MIGRATED,
    ("TrainEstimateResponse", "unbounded_join_node_ids"): MIGRATED,
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


_ROOT = "<answer>"


def _parts(schema: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The answer's own schema and each model it nests, by name."""
    return {_ROOT: schema, **schema.get("$defs", {})}


def _nested(fragment: Any) -> Iterator[str]:
    """The models a schema fragment references."""
    if isinstance(fragment, Mapping):
        ref = fragment.get("$ref")
        if isinstance(ref, str):
            yield ref.rsplit("/", 1)[-1]
        for name, value in fragment.items():
            if name != "$defs":
                yield from _nested(value)
    elif isinstance(fragment, list):
        for value in fragment:
            yield from _nested(value)


def _reachable(start: str, parts: Mapping[str, Mapping[str, Any]]) -> set[str]:
    seen = {start}
    pending = [start]
    while pending:
        for name in _nested(parts[pending.pop()]):
            if name not in seen:
                seen.add(name)
                pending.append(name)
    return seen


def test_an_answer_that_names_a_node_key_addresses_it() -> None:
    # The addresses sit on the model whose nodes they label: the answer itself, or the part
    # of it the browser keeps (a trace's ``trace``).
    for model in _answer_roots():
        schema = model.model_json_schema()
        defs = schema.get("$defs", {})
        parts = _parts(schema)
        naming = {
            name
            for name, part in parts.items()
            if any(_marked(field, defs) for field in part.get("properties", {}).values())
        }
        if not naming:
            continue
        carriers = [
            name for name, part in parts.items() if "node_addresses" in part.get("properties", {})
        ]
        labelled = set().union(*(_reachable(carrier, parts) for carrier in carriers))
        assert naming <= labelled, (
            f"{model.__name__} names nodes by key in {sorted(naming - labelled)}, which no "
            "node_addresses covers"
        )


# Real answers: a pipeline with an owner ``rating`` and its copies ``rating_ie`` and
# ``rating_fr``, each fed by its own source and read by ``consumer``. A copy's children run
# under flat names of their own (``rating_ie__scale``); an answer labels them as authored.


@pytest.fixture
def client() -> Any:
    from fastapi.testclient import TestClient

    return TestClient(project_app())


def _authored_label(graph: PipelineGraph, occurrence: str | None, node_id: str) -> str:
    if occurrence is None:
        return graph.node_map[node_id].data.label
    assert graph.submodels is not None
    definition_id = graph.node_map[occurrence].data.config["definition_id"]
    return graph.submodels[definition_id].graph.node_map[node_id].data.label


def _addresses_every_key(answer_type: type[BaseModel], graph: PipelineGraph, answer: Any) -> None:
    named = named_keys(answer_type, answer)
    assert named, f"{answer_type.__name__} named no node"
    assert set(answer["node_addresses"]) == named
    for address in answer["node_addresses"].values():
        assert address["label"] == _authored_label(graph, address["occurrence"], address["node_id"])


@pytest.mark.parametrize(
    ("target", "row"),
    [
        pytest.param(node_target("consumer"), 2, id="a pipeline node reading copies"),
        pytest.param(node_target("finish", occurrence="rating_fr"), 0, id="a copy's child"),
        pytest.param(
            occurrence_output_target("rating_ie", "premium"), 0, id="an occurrence's output"
        ),
    ],
)
def test_a_trace_through_submodels_addresses_every_node_it_names(
    client: Any, target: dict[str, Any], row: int
) -> None:
    graph = _reuse_graph()
    raw = graph.model_dump(mode="json", by_alias=True)
    preview = client.post("/api/pipeline/preview", json={"graph": raw, "target": target})
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "ok", preview.text
    _addresses_every_key(schemas.PreviewNodeResponse, graph, preview.json())

    traced = client.post(
        "/api/pipeline/trace",
        json={
            "graph": raw,
            "target": target,
            "column": "x",
            "row_index": row,
            "row_values": preview.json()["preview"][row],
            "seed_plan": preview.json()["seed_plan"],
        },
    )
    assert traced.status_code == 200, traced.text
    trace = traced.json()["trace"]
    _addresses_every_key(schemas.TraceResultResponse, graph, trace)
    # The trace reached the copy's children, which the flat graph names otherwise.
    occurrences = {address["occurrence"] for address in trace["node_addresses"].values()}
    assert occurrences & {"rating_ie", "rating_fr"}


@pytest.mark.parametrize(
    "target",
    [
        pytest.param({"occurrence": None, "node_id": "consumer"}, id="a pipeline node"),
        pytest.param({"occurrence": "rating_ie", "node_id": "finish"}, id="a copy's child"),
    ],
)
def test_a_data_point_through_submodels_addresses_its_consumer_and_producer(
    client: Any, target: dict[str, Any]
) -> None:
    graph = _reuse_graph()
    answer = client.post(
        "/api/node-data/point",
        json={"graph": graph.model_dump(mode="json", by_alias=True), "target": target},
    )
    assert answer.status_code == 200, answer.text
    point = answer.json()
    _addresses_every_key(schemas.NodeDataPointResponse, graph, point)
    consumer = point["node_addresses"][point["consumer_node_id"]]
    assert (consumer["occurrence"], consumer["node_id"]) == (
        target["occurrence"],
        target["node_id"],
    )
