"""Node addresses: a request's target resolved through the expansion's record, and an
answer's keys labelled from it (specs/server-api/low-level.md, "Node addresses")."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from haute._flatten import flatten_graph
from haute._node_address_schemas import (
    NodeAddressEntry,
    NodeAddressPayload,
    NodeFrameTargetPayload,
    OccurrenceInputTargetPayload,
    OccurrenceOutputTargetPayload,
    OccurrencePortProvenance,
    RuntimeEndpointPayload,
    RuntimeNodeKey,
)
from haute._node_addresses import (
    ResolvedTarget,
    address_entry,
    labelled,
    occurrence_ports,
    resolve_target,
)
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.errors import NotFoundError


def _node(node_id: str, node_type: NodeType = NodeType.POLARS, **config: object) -> GraphNode:
    label = str(config.pop("label", node_id))
    return GraphNode(
        id=node_id,
        type="submodel" if node_type == NodeType.SUBMODEL else "pipelineNode",
        position={"x": 0, "y": 0},
        data=NodeData(label=label, nodeType=node_type, config=dict(config)),
    )


def _occurrence(instance_id: str, *, instance_of: str | None = None) -> GraphNode:
    config: dict[str, object] = {"definition_id": "scoring", "alias": instance_id}
    if instance_of is not None:
        config["instance_of"] = instance_of
    return _node(instance_id, NodeType.SUBMODEL, label=instance_id, **config)


def _pipeline() -> PipelineGraph:
    """`upstream` feeds occurrence `pricing`, whose `premium` output feeds the copy
    `pricing_2`. Both leave `extra` unbound; `premium` and `premium_alias` are one
    endpoint."""
    definition = SubmodelDefinition(
        definition_id="scoring",
        file="modules/scoring.py",
        graph=PipelineGraph(
            nodes=[
                _node("rate", label="Rate", code="df = policy"),
                _node("finish", label="Finish", code="df = rate"),
            ],
            edges=[GraphEdge(id="inner", source="rate", target="finish")],
        ),
        input_ports=[
            SubmodelInputPort(
                name="policy", targets=[SubmodelEndpoint(node_id="rate", handle_id="frame")]
            ),
            SubmodelInputPort(
                name="extra", targets=[SubmodelEndpoint(node_id="rate", handle_id="other")]
            ),
        ],
        output_ports=[
            SubmodelOutputPort(
                name="premium", source=SubmodelEndpoint(node_id="finish", handle_id="scored")
            ),
            SubmodelOutputPort(
                name="premium_alias",
                source=SubmodelEndpoint(node_id="finish", handle_id="scored"),
            ),
        ],
    )
    return PipelineGraph(
        nodes=[
            _node("upstream", label="Upstream"),
            _occurrence("pricing"),
            _occurrence("pricing_2", instance_of="pricing"),
            _node("downstream", label="Downstream", code="df = pricing_2"),
        ],
        edges=[
            GraphEdge(
                id="feed",
                source="upstream",
                target="pricing",
                sourceHandle="batch",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="chain",
                source="pricing",
                target="pricing_2",
                sourceHandle="out__premium",
                targetHandle="in__policy",
            ),
            GraphEdge(
                id="out",
                source="pricing_2",
                target="downstream",
                sourceHandle="out__premium",
            ),
        ],
        submodels={"scoring": definition},
    )


@pytest.fixture
def flat() -> PipelineGraph:
    return flatten_graph(_pipeline())


def _node_target(occurrence: str | None, node_id: str, **ports: object) -> NodeFrameTargetPayload:
    return NodeFrameTargetPayload.model_validate(
        {
            "kind": "node",
            "occurrence": occurrence,
            "node_id": node_id,
            "port_label": ports.get("port_label"),
            "port_labels": ports.get("port_labels"),
        }
    )


def _input_card(occurrence: str, port: str) -> OccurrenceInputTargetPayload:
    return OccurrenceInputTargetPayload(kind="occurrence_input", occurrence=occurrence, port=port)


def _output_card(occurrence: str, port: str) -> OccurrenceOutputTargetPayload:
    return OccurrenceOutputTargetPayload(kind="occurrence_output", occurrence=occurrence, port=port)


OWNER_FINISH = qualified_runtime_node_id("pricing", "finish")
COPY_FINISH = qualified_runtime_node_id("pricing_2", "finish")
COPY_RATE = qualified_runtime_node_id("pricing_2", "rate")


class TestResolution:
    def test_a_pipeline_node_is_its_own_key(self, flat: PipelineGraph) -> None:
        assert resolve_target(
            flat, NodeAddressPayload(occurrence=None, node_id="upstream")
        ) == ResolvedTarget(key="upstream")

    def test_an_owner_child_and_the_same_child_of_a_copy_run_apart(
        self, flat: PipelineGraph
    ) -> None:
        owner = resolve_target(flat, NodeAddressPayload(occurrence="pricing", node_id="finish"))
        copy = resolve_target(flat, NodeAddressPayload(occurrence="pricing_2", node_id="finish"))

        assert (owner.key, copy.key) == (OWNER_FINISH, COPY_FINISH)
        assert {owner.key, copy.key} <= set(flat.node_map)

    def test_a_node_frame_keeps_its_port_or_ports(self, flat: PipelineGraph) -> None:
        one = resolve_target(flat, _node_target("pricing", "finish", port_label="scored"))
        several = resolve_target(flat, _node_target(None, "upstream", port_labels=["a", "b"]))

        assert one == ResolvedTarget(key=OWNER_FINISH, port_label="scored")
        assert several == ResolvedTarget(key="upstream", port_labels=("a", "b"))

    def test_an_input_card_reads_the_endpoint_feeding_its_port(self, flat: PipelineGraph) -> None:
        owner = resolve_target(flat, _input_card("pricing", "policy"))
        # Fed by another occurrence's output: that output's producing child and handle.
        copy = resolve_target(
            flat,
            _input_card("pricing_2", "policy"),
        )

        assert owner == ResolvedTarget(key="upstream", port_label="batch")
        assert copy == ResolvedTarget(key=OWNER_FINISH, port_label="scored")

    def test_an_output_card_reads_its_producing_child(self, flat: PipelineGraph) -> None:
        resolved = resolve_target(
            flat,
            _output_card("pricing_2", "premium_alias"),
        )

        assert resolved == ResolvedTarget(key=COPY_FINISH, port_label="scored")

    @pytest.mark.parametrize(
        ("target", "message"),
        [
            (NodeAddressPayload(occurrence=None, node_id="nowhere"), "Node 'nowhere' not found"),
            # A child's runtime key names no node of the pipeline.
            (
                NodeAddressPayload(occurrence=None, node_id=COPY_FINISH),
                f"Node '{COPY_FINISH}' not found",
            ),
            (
                NodeAddressPayload(occurrence=None, node_id="pricing"),
                "'pricing' is a submodel: name one of its nodes or ports",
            ),
            (NodeAddressPayload(occurrence="gone", node_id="rate"), "Submodel 'gone' not found"),
            (
                NodeAddressPayload(occurrence="pricing", node_id="upstream"),
                "Submodel 'pricing' has no node 'upstream'",
            ),
            (
                _input_card("pricing", "nope"),
                "Submodel 'pricing' has no input 'nope'",
            ),
            (
                _output_card("pricing", "nope"),
                "Submodel 'pricing' has no output 'nope'",
            ),
            (
                _input_card("pricing", "extra"),
                "Input 'extra' of submodel 'pricing' is not connected",
            ),
        ],
    )
    def test_a_target_the_document_does_not_hold_is_refused_by_name(
        self, flat: PipelineGraph, target: BaseModel, message: str
    ) -> None:
        with pytest.raises(NotFoundError) as refused:
            resolve_target(flat, target)  # type: ignore[arg-type]

        assert refused.value.status_code == 404
        assert refused.value.detail == message

    def test_a_graph_without_occurrences_resolves_its_own_nodes(self) -> None:
        graph = PipelineGraph(nodes=[_node("only")], edges=[])
        flat = flatten_graph(graph)

        only = NodeAddressPayload(occurrence=None, node_id="only")
        assert resolve_target(flat, only).key == "only"
        assert occurrence_ports(flat) == []


class TestFrameProvenance:
    def test_every_declared_port_of_every_occurrence_is_listed(self, flat: PipelineGraph) -> None:
        ports = occurrence_ports(flat)

        def entry(occurrence: str, direction: str, port: str, endpoint: tuple[str, str] | None):
            return OccurrencePortProvenance(
                occurrence=occurrence,
                direction=direction,  # type: ignore[arg-type]
                port=port,
                endpoint=None
                if endpoint is None
                else RuntimeEndpointPayload(key=endpoint[0], handle=endpoint[1]),
            )

        assert ports == [
            entry("pricing", "input", "policy", ("upstream", "batch")),
            entry("pricing", "input", "extra", None),
            entry("pricing", "output", "premium", (OWNER_FINISH, "scored")),
            entry("pricing", "output", "premium_alias", (OWNER_FINISH, "scored")),
            entry("pricing_2", "input", "policy", (OWNER_FINISH, "scored")),
            entry("pricing_2", "input", "extra", None),
            entry("pricing_2", "output", "premium", (COPY_FINISH, "scored")),
            entry("pricing_2", "output", "premium_alias", (COPY_FINISH, "scored")),
        ]

    def test_an_expansion_in_stages_records_what_one_pass_does(self) -> None:
        # The copy first (an owner waits for its copies), then the owner feeding it: the
        # copy's input then runs from the owner's producing child, not the owner itself.
        staged = flatten_graph(
            flatten_graph(_pipeline(), target_instance_id="pricing_2"),
            target_instance_id="pricing",
        )
        single = flatten_graph(_pipeline())

        assert occurrence_ports(staged) == occurrence_ports(single)
        assert resolve_target(staged, _input_card("pricing_2", "policy")) == ResolvedTarget(
            key=OWNER_FINISH, port_label="scored"
        )


class TestLabelling:
    def test_an_address_names_a_copy_child_by_its_definition_label(
        self, flat: PipelineGraph
    ) -> None:
        # The copy's clone runs under an aliased name; its address keeps the authored one.
        assert flat.node_map[COPY_FINISH].data.label == "pricing_2__Finish"

        assert address_entry(flat, COPY_FINISH) == NodeAddressEntry(
            occurrence="pricing_2", node_id="finish", label="Finish"
        )
        assert address_entry(flat, "upstream") == NodeAddressEntry(
            occurrence=None, node_id="upstream", label="Upstream"
        )
        assert address_entry(flat, "pricing") == NodeAddressEntry(
            occurrence=None, node_id="pricing", label="pricing"
        )

    def test_a_key_the_graph_does_not_hold_is_a_defect(self, flat: PipelineGraph) -> None:
        with pytest.raises(ValueError, match="does not hold"):
            address_entry(flat, "invented")

    def test_an_answer_addresses_every_key_it_names_wherever_it_names_it(
        self, flat: PipelineGraph
    ) -> None:
        class Step(BaseModel):
            node_id: RuntimeNodeKey

        class Answer(BaseModel):
            target: RuntimeNodeKey
            maybe: RuntimeNodeKey | None
            listed: list[RuntimeNodeKey]
            by_key: dict[RuntimeNodeKey, int]
            steps: list[Step]
            # Not a node: a string that embeds a key is never read for one.
            slot_key: str
            node_addresses: dict[str, NodeAddressEntry]

        answer = labelled(
            Answer,
            flat,
            target=COPY_FINISH,
            maybe=None,
            listed=["upstream"],
            by_key={COPY_RATE: 1},
            steps=[Step(node_id="pricing")],
            slot_key=f"{OWNER_FINISH}|scored|live",
        )

        assert set(answer.node_addresses) == {COPY_FINISH, "upstream", COPY_RATE, "pricing"}
        assert answer.node_addresses[COPY_RATE].label == "Rate"
