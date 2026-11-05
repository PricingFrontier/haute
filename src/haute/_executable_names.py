"""The executable-name rule: what a node, an occurrence or an input may be called.

A node's label becomes a Python function name (``_sanitize_func_name``), a
submodel occurrence's alias becomes the name its runner is bound to, and every
input a node body receives is bound under its executable input name. Generated
modules bind a few names themselves (``import haute``, ``import polars as pl``,
``pipeline = haute.Pipeline(...)``, ``global_constants = ...``; a submodel file
binds ``submodel``), and node bodies also call Python's built-ins. A node or
input taking one of those names rebinds it for every later node body when the
file is imported or run on its own, while the canvas, which runs each body
against the preamble alone, keeps working.

Node function names and occurrence aliases are unique across the root graph
and every submodel graph, compared ignoring case. That is a policy, not a
consequence of execution: one name means one node in labels, traces, messages
and generated files, and names differing only in case read as one name there.

Save, codegen, the strict parse, the assistant and the standalone
``Pipeline`` registration all ask this module, so they cannot disagree.
"""

from __future__ import annotations

import builtins
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from haute._graph_utils import _sanitize_func_name, edge_input_name
from haute._node_specs import REQUEST_INPUT_NODE_TYPES, node_spec, node_type_table
from haute._types import GLOBAL_CONSTANTS_NAME, GraphNode, NodeType, PipelineGraph

#: The names a generated module binds itself, with what it binds them to.
#: Comparison with them is exact, as Python's is.
RESERVED_NAMES: dict[str, str] = {
    "haute": "the haute package",
    "pl": "polars",
    "pipeline": "the pipeline",
    "submodel": "a submodel file's submodel",
    GLOBAL_CONSTANTS_NAME: "the pipeline's global constants",
}

#: Python's public built-ins, which a node body calls by name.
BUILTIN_NAMES: frozenset[str] = frozenset(
    name for name in vars(builtins) if not name.startswith("_")
)

#: Where a party sits: the root pipeline module, or a submodel definition.
ROOT_MODULE = "the pipeline"


def _structural_role(node: GraphNode) -> str | None:
    """The part of a submodel *node* is: a Port has no function name, an occurrence
    is named by its alias."""
    return node_spec(node.data.nodeType).structural_role


ViolationKind = Literal[
    "duplicate",
    "reserved",
    "builtin",
    "reserved_input",
    "support_collision",
    "support_input",
    "support_conflict",
    "support_reserved",
    "support_unsupported",
    "output_destination",
]


@dataclass(frozen=True)
class NameParty:
    """A node taking part in a violation, and the module it sits in."""

    node_id: str
    label: str
    module: str

    def describe(self) -> str:
        where = self.module if self.module == ROOT_MODULE else f"submodel {self.module!r}"
        return f"{self.label!r} ({where})"


@dataclass(frozen=True)
class FrameBinding:
    """A request input's frame, which names the input its consumers receive.

    *module* is where the request input sits, as a party's is.
    """

    module: str
    node_id: str
    handle: str


@dataclass(frozen=True)
class PortBinding:
    """A submodel's public input port, which names the input its targets receive."""

    definition_id: str
    port: str


#: The editor setting an input's name comes from, where one sets it directly.
InputBinding = FrameBinding | PortBinding


@dataclass(frozen=True)
class NameViolation:
    """One collision bucket or reserved hit, with every node it involves.

    ``name`` is the executable name in question. For ``reserved_input`` the
    single party is the node whose body receives the input, ``origin`` says
    where the input comes from, and ``binding`` is the frame or public input
    port that names it, when it is one.
    """

    kind: ViolationKind
    name: str
    parties: tuple[NameParty, ...]
    origin: str = ""
    binding: InputBinding | None = None

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(party.node_id for party in self.parties)

    def message(self) -> str:
        if self.kind.startswith("support_"):
            return self._support_message()
        if self.kind == "output_destination":
            nodes = " and ".join(party.describe() for party in self.parties)
            return (
                f"Data Output nodes {nodes} write one destination, {self.name}; the second "
                "would fail when it runs. Give each its own destination."
            )
        if self.kind == "duplicate":
            nodes = " and ".join(party.describe() for party in self.parties)
            if self.origin:
                return (
                    f"Nodes {nodes} collide on `{self.name}`, also taken by {self.origin}. "
                    "Rename the conflicting node, instance or definition child."
                )
            return (
                f"Nodes {nodes} take one name, `{self.name}`; node names must differ "
                "by more than case across the pipeline and its submodels."
            )
        party = self.parties[0]
        if self.kind == "reserved":
            return (
                f"Node {party.describe()} takes the name `{self.name}`, which the "
                f"generated module binds to {RESERVED_NAMES[self.name]}."
            )
        if self.kind == "builtin":
            return (
                f"Node {party.describe()} takes the name `{self.name}`, a Python "
                "built-in that node code could no longer call."
            )
        return (
            f"Node {party.describe()} receives an input named `{self.name}` "
            f"({self.origin}), which its code binds to {RESERVED_NAMES[self.name]}."
        )

    def _support_message(self) -> str:
        # ``origin`` says where the support code binds the name.
        if self.kind == "support_unsupported":
            return self.origin
        if self.kind == "support_conflict":
            return (
                f"`{self.name}` is bound by {self.origin}; only the last would be seen. "
                "Remove or rename one."
            )
        if self.kind == "support_reserved":
            return (
                f"{self.origin} binds `{self.name}`, which the generated module binds to "
                f"{RESERVED_NAMES[self.name]}. Rename it."
            )
        party = self.parties[0]
        if self.kind == "support_collision":
            return (
                f"Node {party.describe()} takes the name `{self.name}`, which {self.origin} "
                "binds; the node would replace it for every other node. Rename the node or "
                "the helper."
            )
        return (
            f"Node {party.describe()} receives an input named `{self.name}`, which "
            f"{self.origin} binds. Rename the input or the helper."
        )


def format_name_violations(violations: Iterable[NameViolation]) -> str:
    """The one message every entry point raises for *violations*."""
    lines = "\n".join(f"  - {violation.message()}" for violation in violations)
    return (
        "Some names collide with each other or with a name the generated Python "
        f"binds itself. Rename them:\n{lines}"
    )


def reserved_or_builtin(name: str) -> ViolationKind | None:
    """Why a node function name or occurrence alias may not be *name* on its own."""
    if name in RESERVED_NAMES:
        return "reserved"
    if name in BUILTIN_NAMES:
        return "builtin"
    return None


def _named_graphs(graph: PipelineGraph) -> Iterator[tuple[str, PipelineGraph]]:
    yield ROOT_MODULE, graph
    for definition_id, definition in (graph.submodels or {}).items():
        yield definition_id, definition.graph


def _function_name(node: GraphNode) -> str:
    # An occurrence's label is its alias, a canonical identifier.
    return _sanitize_func_name(node.data.label)


def _named_parties(graph: PipelineGraph) -> Iterator[tuple[str, NameParty]]:
    """Every node function name and occurrence alias, with its party."""
    for module, scoped in _named_graphs(graph):
        for node in scoped.nodes:
            if _structural_role(node) == "port":
                continue
            party = NameParty(node_id=node.id, label=node.data.label, module=module)
            yield _function_name(node), party


@dataclass(frozen=True)
class _NameClaim:
    name: str
    parties: tuple[NameParty, ...]
    origin: str = ""


def _name_claims(graph: PipelineGraph) -> Iterator[_NameClaim]:
    """Authored names and generated clone names, with every node that can rename them."""
    for name, party in _named_parties(graph):
        yield _NameClaim(name, (party,))
    definitions = graph.submodels or {}
    for module, scoped in _named_graphs(graph):
        for occurrence in scoped.nodes:
            config = occurrence.data.config
            if _structural_role(occurrence) != "occurrence" or config.get("instance_of") is None:
                continue
            definition_id = config.get("definition_id")
            alias = config.get("alias")
            # Naming also runs on incomplete editor candidates; their shape is validated separately.
            if not isinstance(definition_id, str) or not isinstance(alias, str):
                continue
            definition = definitions.get(definition_id)
            if definition is None:
                continue
            instance_party = NameParty(occurrence.id, occurrence.data.label, module)
            for child in definition.graph.nodes:
                if _structural_role(child) == "port":
                    continue
                child_party = NameParty(child.id, child.data.label, definition_id)
                yield _NameClaim(
                    _sanitize_func_name(f"{alias}__{child.data.label}"),
                    (instance_party, child_party),
                    f"the generated clone of {child_party.describe()} in instance {alias!r}",
                )


def function_name_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Collisions and reserved or built-in hits among node function names."""
    violations: list[NameViolation] = []
    buckets: dict[str, list[_NameClaim]] = {}
    for claim in _name_claims(graph):
        buckets.setdefault(claim.name.casefold(), []).append(claim)
        kind = reserved_or_builtin(claim.name)
        if kind is not None:
            violations.append(NameViolation(kind=kind, name=claim.name, parties=claim.parties))
    for entries in buckets.values():
        if len(entries) > 1:
            violations.append(
                NameViolation(
                    kind="duplicate",
                    name=entries[0].name,
                    parties=tuple(
                        dict.fromkeys(party for claim in entries for party in claim.parties)
                    ),
                    origin=" and ".join(claim.origin for claim in entries if claim.origin),
                )
            )
    return violations


@dataclass(frozen=True)
class ReceivedInput:
    """One input a node body receives: its name, where it comes from, and its binding."""

    name: str
    origin: str
    binding: InputBinding | None = None


def received_inputs(graph: PipelineGraph) -> Iterator[tuple[NameParty, list[ReceivedInput]]]:
    """Every node with the inputs its body receives, as the executor binds them.

    A node's inputs are its incoming edges' input names, the logical names its
    ``input_mapping`` binds in their place, and, in a submodel definition, the
    public input ports routed to it.
    """
    ports: dict[tuple[str, str], list[str]] = {}
    for definition_id, definition in (graph.submodels or {}).items():
        for port in definition.input_ports:
            for target in port.targets:
                ports.setdefault((definition_id, target.node_id), []).append(port.name)
    for module, scoped in _named_graphs(graph):
        edges = _edge_inputs(module, scoped)
        for node in scoped.nodes:
            party = NameParty(node_id=node.id, label=node.data.label, module=module)
            yield (
                party,
                [
                    *edges.get(node.id, []),
                    *_mapping_inputs(node),
                    *(
                        ReceivedInput(
                            port, f"submodel input port {port!r}", PortBinding(module, port)
                        )
                        for port in ports.get((module, node.id), [])
                    ),
                ],
            )


def _edge_inputs(module: str, scoped: PipelineGraph) -> dict[str, list[ReceivedInput]]:
    """The edge-derived inputs each node receives, as the executor derives them."""
    node_map = scoped.node_map
    inputs: dict[str, list[ReceivedInput]] = {}
    for edge in scoped.edges:
        source = node_map.get(edge.source)
        if source is None:
            continue
        try:
            name = edge_input_name(edge, source, submodels=scoped.submodels)
        except ValueError:
            # A frame edge without its handle is refused where the edge is checked.
            continue
        binding: InputBinding | None = None
        if source.data.nodeType in REQUEST_INPUT_NODE_TYPES:
            origin = f"frame {name!r} of {source.data.label!r}"
            if edge.sourceHandle is not None:
                binding = FrameBinding(module, source.id, edge.sourceHandle)
        elif _structural_role(source) == "occurrence":
            origin = f"output port {name!r} of {source.data.label!r}"
        else:
            origin = f"from {source.data.label!r}"
        inputs.setdefault(edge.target, []).append(ReceivedInput(name, origin, binding))
    return inputs


def _mapping_inputs(node: GraphNode) -> Iterator[ReceivedInput]:
    """Every logical name ``input_mapping`` binds in place of an edge-derived one."""
    mapping = node.data.config.get("input_mapping")
    if isinstance(mapping, dict):
        for logical, current in mapping.items():
            if isinstance(logical, str):
                yield ReceivedInput(logical, f"input_mapping alias for {current!r}")


def input_binding_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Every reserved name a node body would receive as an input.

    ``df`` is not among them: it keeps its own rule, refused where node code
    reads it (codegen and ``_user_exec``) and an ordinary input elsewhere.
    """
    violations: list[NameViolation] = []
    for party, inputs in received_inputs(graph):
        seen: set[str] = set()
        for received in inputs:
            if received.name in RESERVED_NAMES and received.name not in seen:
                seen.add(received.name)
                violations.append(
                    NameViolation(
                        kind="reserved_input",
                        name=received.name,
                        parties=(party,),
                        origin=received.origin,
                        binding=received.binding,
                    )
                )
    return violations


def data_output_destination(
    graph: PipelineGraph, config: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Where a Data Output writes, as ``(identity, display)``, or ``None``.

    A file or lakehouse target is the path the writer resolves
    (``executor.resolve_data_output_path``: a bare file name under
    ``outputs/``, the format's default extension added, relative paths
    anchored as the writer anchors them), compared ignoring case on Windows
    and macOS, whose file systems do. A database target is its connection (or
    URI) and its table. A Databricks target is its table, spelled canonically,
    since every warehouse in the workspace writes the same one. A destination
    missing a required part has none: it is
    a draft, saved and compared with nothing, and its completeness is required
    only when it runs. A destination the writer refuses (outside the project)
    has none either; the writer's own refusal reports it.
    """
    from haute._path_resolution import RuntimePathError
    from haute._polars_io_registry import (
        PolarsIoConfigError,
        data_output_completeness,
        format_for_config,
        format_group,
        is_databricks_output,
    )
    from haute.executor import resolve_data_output_path

    try:
        if data_output_completeness(config):
            return None
        if is_databricks_output(config):
            from haute._databricks_io import _canonical_table

            table = str(config.get("table"))
            return f"Databricks table {_canonical_table(table)}", f"Databricks table {table}"
        fmt = format_for_config(config)
        if format_group(fmt) == "database":
            locator = config.get("connection") or config.get("uri")
            described = f"table {config.get('table')!r} of {locator!r}"
            return described, described
        resolved, display = resolve_data_output_path(graph, config)
    except (PolarsIoConfigError, RuntimePathError, ValueError):
        # An invalid config or path is refused by its own validation.
        return None
    identity = str(resolved)
    return (identity.casefold() if sys.platform in ("win32", "darwin") else identity), display


#: Where a node of each type writes, for the types that write a destination of
#: their own; ``None`` for every other type.
_OUTPUT_DESTINATION: Mapping[
    NodeType, Callable[[PipelineGraph, Mapping[str, Any]], tuple[str, str] | None] | None
] = node_type_table(
    "output destination",
    {
        NodeType.API_INPUT: None,
        NodeType.WORKBENCH_INPUT: None,
        NodeType.WORKBENCH_OUTPUT: None,
        NodeType.DATA_INPUT: None,
        NodeType.DATA_OUTPUT: data_output_destination,
        NodeType.POLARS: None,
        NodeType.EDGE_JOIN: None,
        NodeType.MODEL_SCORE: None,
        NodeType.BANDING: None,
        NodeType.RATING_STEP: None,
        NodeType.OUTPUT: None,
        NodeType.EXPLORE: None,
        NodeType.EXTERNAL_FILE: None,
        NodeType.LIVE_SWITCH: None,
        NodeType.MODELLING: None,
        NodeType.OPTIMISER: None,
        NodeType.SCENARIO_EXPANDER: None,
        NodeType.OPTIMISER_APPLY: None,
        NodeType.CONSTANT: None,
        NodeType.SUBMODEL: None,
        NodeType.SUBMODEL_PORT: None,
    },
)


def data_output_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Two Data Output nodes writing one destination, submodel occurrences expanded.

    A Data Output inside a submodel definition writes once per occurrence, so
    a definition used twice writes its destination twice.
    """
    writers: dict[str, list[NameParty]] = {}
    displays: dict[str, str] = {}

    def record(node: GraphNode, module: str) -> None:
        destination_of = _OUTPUT_DESTINATION[node.data.nodeType]
        destination = None if destination_of is None else destination_of(graph, node.data.config)
        if destination is not None:
            identity, display = destination
            displays.setdefault(identity, display)
            party = NameParty(node_id=node.id, label=node.data.label, module=module)
            writers.setdefault(identity, []).append(party)

    definitions = graph.submodels or {}
    for node in graph.nodes:
        if _OUTPUT_DESTINATION[node.data.nodeType] is not None:
            record(node, ROOT_MODULE)
        elif _structural_role(node) == "occurrence":
            definition_id = node.data.config.get("definition_id")
            definition = definitions.get(str(definition_id))
            if definition is None:
                continue
            for child in definition.graph.nodes:
                if _OUTPUT_DESTINATION[child.data.nodeType] is not None:
                    record(child, str(definition_id))
    return [
        NameViolation(kind="output_destination", name=displays[identity], parties=tuple(parties))
        for identity, parties in writers.items()
        if len(parties) > 1
    ]


def executable_name_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Every executable-name violation in *graph* and its submodel graphs.

    Two Data Output nodes writing one destination are refused here too, so
    every entry point that checks names checks destinations.
    """
    return [
        *function_name_violations(graph),
        *input_binding_violations(graph),
        *data_output_violations(graph),
    ]


def function_name_problem(
    graph: PipelineGraph,
    label: str,
    *,
    excluding_node_id: str | None = None,
) -> str | None:
    """Why a node labelled *label* may not join *graph*, or ``None``.

    *excluding_node_id* is the node being renamed, which does not collide
    with itself.
    """
    name = _sanitize_func_name(label)
    kind = reserved_or_builtin(name)
    party = NameParty(node_id=excluding_node_id or "", label=label, module=ROOT_MODULE)
    if kind is not None:
        return NameViolation(kind=kind, name=name, parties=(party,)).message()
    for claim in _name_claims(graph):
        if any(
            other.node_id == excluding_node_id and other.module == ROOT_MODULE
            for other in claim.parties
        ):
            continue
        if claim.name.casefold() == name.casefold():
            return NameViolation(
                kind="duplicate", name=name, parties=(*claim.parties, party), origin=claim.origin
            ).message()
    return None


def reserved_input_problem(names: Iterable[str]) -> str | None:
    """Why a node body may not receive one of *names*, or ``None``."""
    for name in names:
        if name in RESERVED_NAMES:
            return (
                f"An input is named `{name}`, which node code binds to "
                f"{RESERVED_NAMES[name]}. Rename the upstream node, frame, port "
                "or input_mapping alias."
            )
    return None
