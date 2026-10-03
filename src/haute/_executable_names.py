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
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from haute._graph_utils import _sanitize_func_name, edge_input_name
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

_STRUCTURAL_TYPES = (NodeType.SUBMODEL_PORT,)

ViolationKind = Literal["duplicate", "reserved", "builtin", "reserved_input"]


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
class NameViolation:
    """One collision bucket or reserved hit, with every node it involves.

    ``name`` is the executable name in question. For ``reserved_input`` the
    single party is the node whose body receives the input, and ``origin``
    says where the input comes from.
    """

    kind: ViolationKind
    name: str
    parties: tuple[NameParty, ...]
    origin: str = ""

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(party.node_id for party in self.parties)

    def message(self) -> str:
        if self.kind == "duplicate":
            nodes = " and ".join(party.describe() for party in self.parties)
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
            if node.data.nodeType in _STRUCTURAL_TYPES:
                continue
            party = NameParty(node_id=node.id, label=node.data.label, module=module)
            yield _function_name(node), party


def function_name_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Collisions and reserved or built-in hits among node function names."""
    violations: list[NameViolation] = []
    buckets: dict[str, list[tuple[str, NameParty]]] = {}
    for name, party in _named_parties(graph):
        buckets.setdefault(name.casefold(), []).append((name, party))
        kind = reserved_or_builtin(name)
        if kind is not None:
            violations.append(NameViolation(kind=kind, name=name, parties=(party,)))
    for entries in buckets.values():
        if len(entries) > 1:
            violations.append(
                NameViolation(
                    kind="duplicate",
                    name=entries[0][0],
                    parties=tuple(party for _, party in entries),
                )
            )
    return violations


def _port_bindings(graph: PipelineGraph) -> dict[tuple[str, str], list[str]]:
    """The submodel input port names each definition child receives, by module and node."""
    bindings: dict[tuple[str, str], list[str]] = {}
    for definition_id, definition in (graph.submodels or {}).items():
        for port in definition.input_ports:
            for target in port.targets:
                bindings.setdefault((definition_id, target.node_id), []).append(port.name)
    return bindings


def _input_bindings(scoped: PipelineGraph, node: GraphNode) -> Iterator[tuple[str, str]]:
    """Each name *node*'s body receives along its edges, with where it comes from.

    The edge-derived names, as the executor derives them, and every logical
    name ``inputMapping`` binds in their place (an instance's mapping binds
    its original's names, which the original's own edges already give).
    """
    node_map = scoped.node_map
    for edge in scoped.edges:
        if edge.target != node.id:
            continue
        source = node_map.get(edge.source)
        if source is None:
            continue
        try:
            name = edge_input_name(edge, source, submodels=scoped.submodels)
        except ValueError:
            # A frame edge without its handle is refused where the edge is checked.
            continue
        if source.data.nodeType == NodeType.API_INPUT:
            yield name, f"frame {name!r} of {source.data.label!r}"
        elif source.data.nodeType == NodeType.SUBMODEL:
            yield name, f"output port {name!r} of {source.data.label!r}"
        else:
            yield name, f"from {source.data.label!r}"
    mapping = node.data.config.get("inputMapping")
    if isinstance(mapping, dict):
        for logical, current in mapping.items():
            if isinstance(logical, str):
                yield logical, f"inputMapping alias for {current!r}"


def input_binding_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Every reserved name a node body would receive as an input.

    ``df`` is not among them: it keeps its own rule, refused where node code
    reads it (codegen and ``_user_exec``) and an ordinary input elsewhere.
    """
    violations: list[NameViolation] = []
    ports = _port_bindings(graph)
    for module, scoped in _named_graphs(graph):
        for node in scoped.nodes:
            party = NameParty(node_id=node.id, label=node.data.label, module=module)
            seen: set[str] = set()
            bindings = [
                *_input_bindings(scoped, node),
                *(
                    (port, f"submodel input port {port!r}")
                    for port in ports.get((module, node.id), [])
                ),
            ]
            for name, origin in bindings:
                if name in RESERVED_NAMES and name not in seen:
                    seen.add(name)
                    violations.append(
                        NameViolation(
                            kind="reserved_input", name=name, parties=(party,), origin=origin
                        )
                    )
    return violations


def executable_name_violations(graph: PipelineGraph) -> list[NameViolation]:
    """Every executable-name violation in *graph* and its submodel graphs."""
    return [*function_name_violations(graph), *input_binding_violations(graph)]


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
    for existing, other in _named_parties(graph):
        if other.node_id == excluding_node_id and other.module == ROOT_MODULE:
            continue
        if existing.casefold() == name.casefold():
            return NameViolation(kind="duplicate", name=name, parties=(other, party)).message()
    return None


def reserved_input_problem(names: Iterable[str]) -> str | None:
    """Why a node body may not receive one of *names*, or ``None``."""
    for name in names:
        if name in RESERVED_NAMES:
            return (
                f"An input is named `{name}`, which node code binds to "
                f"{RESERVED_NAMES[name]}. Rename the upstream node, frame, port "
                "or inputMapping alias."
            )
    return None
