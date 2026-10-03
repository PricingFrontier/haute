"""Pure, shared identities exposed by the pipeline editor API."""

from __future__ import annotations

import functools
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from haute._config_io import config_path_for_node, has_config_folder
from haute._executable_names import ROOT_MODULE
from haute._graph_utils import _sanitize_func_name, executable_input_name
from haute._support_code_names import UtilityReader, name_violations
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph


@dataclass(frozen=True)
class ResolvedEditorIdentity:
    """All executable identities derived for one editor node."""

    function_name: str
    config_reference: str | None
    default_input_name: str | None
    source_handle_input_names: dict[str, str]


_ASCII_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def recoverable_api_input_source_handles(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bindable handles while retaining an incomplete config for repair.

    Editor document recovery must not validate the whole V2 schema before it
    can render that schema: an invalid persisted row is precisely what the
    editor needs to surface. This derives only identities that are already
    unambiguous and runtime-eligible, matching the browser's render gate.
    Execution and save retain their strict schema validation boundaries.
    """
    tables = config.get("tables")
    if not isinstance(tables, list):
        return ()
    from haute._json_shred._shred import table_is_emitting

    handles: list[str] = []
    seen: set[str] = set()
    for table in tables:
        if not table_is_emitting(table):
            continue
        assert isinstance(table, dict)  # guaranteed by table_is_emitting
        label = table.get("label")
        if (
            not isinstance(label, str)
            or _ASCII_IDENTIFIER.fullmatch(label) is None
            or keyword.iskeyword(label)
        ):
            continue
        folded = label.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        handles.append(label)
    return tuple(handles)


def resolve_editor_identity(
    *,
    node_type: NodeType | str,
    label: str,
    source_handles: list[str] | tuple[str, ...] = (),
    config_reference_override: str | None = None,
    alias: str | None = None,
) -> ResolvedEditorIdentity:
    """Resolve one node's editor identities without filesystem access."""
    kind = NodeType(node_type)
    handles = list(source_handles)
    if len(handles) != len(set(handles)):
        raise ValueError("Source handles must be unique.")
    function_name = _sanitize_func_name(label)
    if config_reference_override is not None:
        config_reference = config_reference_override
    elif has_config_folder(kind):
        config_reference = config_path_for_node(kind, function_name).as_posix()
    else:
        config_reference = None
    special = {NodeType.API_INPUT, NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
    default_input_name = (
        None
        if kind in special
        else executable_input_name(
            node_type=kind,
            label=label,
            source_handle=None,
        )
    )
    if kind == NodeType.SUBMODEL:
        if not isinstance(alias, str) or not alias:
            raise ValueError(f"Submodel node {label!r} requires an alias.")
    mapping = {
        handle: executable_input_name(node_type=kind, label=label, source_handle=handle)
        for handle in handles
    }
    return ResolvedEditorIdentity(
        function_name=function_name,
        config_reference=config_reference,
        default_input_name=default_input_name,
        source_handle_input_names=mapping,
    )


@dataclass(frozen=True)
class NamingCandidate:
    """A node being created or renamed, as the editor would apply it."""

    node_id: str
    label: str
    node_type: NodeType
    alias: str | None


@dataclass(frozen=True)
class NamedCandidate:
    """A candidate's resolved name, and why it may not have it (without allocation)."""

    label: str
    alias: str | None
    collision: str | None


#: The violation kinds a node's own name causes, which renaming it resolves.
_NAME_KINDS = frozenset({"duplicate", "reserved", "builtin", "support_collision"})

#: How many suffixes allocation tries before giving up on a base name.
_MAX_ALLOCATION_ATTEMPTS = 10_000


def _located(graph: PipelineGraph, node_id: str) -> str:
    """The module holding *node_id*: the root, else the first definition holding it."""
    if node_id in graph.node_map:
        return ROOT_MODULE
    for definition_id, definition in (graph.submodels or {}).items():
        if node_id in definition.graph.node_map:
            return definition_id
    return ROOT_MODULE


def _with_name(graph: PipelineGraph, candidate: NamingCandidate, label: str) -> PipelineGraph:
    """*graph* with the candidate present under *label* (and alias, for an occurrence)."""
    alias = label if candidate.node_type == NodeType.SUBMODEL else None

    def renamed(node: GraphNode) -> GraphNode:
        config = dict(node.data.config)
        if alias is not None:
            config["alias"] = alias
        data = node.data.model_copy(update={"label": label, "config": config})
        return node.model_copy(update={"data": data})

    module = _located(graph, candidate.node_id)
    scoped = graph if module == ROOT_MODULE else (graph.submodels or {})[module].graph
    if candidate.node_id in scoped.node_map:
        nodes = [renamed(n) if n.id == candidate.node_id else n for n in scoped.nodes]
    else:
        config: dict[str, Any] = {} if alias is None else {"alias": alias}
        added = GraphNode(
            id=candidate.node_id,
            data=NodeData(label=label, nodeType=candidate.node_type, config=config),
        )
        nodes = [*scoped.nodes, added]
    updated = scoped.model_copy(update={"nodes": nodes})
    if module == ROOT_MODULE:
        return updated
    submodels = dict(graph.submodels or {})
    submodels[module] = submodels[module].model_copy(update={"graph": updated})
    return graph.model_copy(update={"submodels": submodels})


def _name_problem(
    graph: PipelineGraph, candidate: NamingCandidate, read_utility: UtilityReader
) -> str | None:
    module = _located(graph, candidate.node_id)
    for violation in name_violations(graph, read_utility):
        if violation.kind in _NAME_KINDS and any(
            party.node_id == candidate.node_id and party.module == module
            for party in violation.parties
        ):
            return violation.message()
    return None


def _suffixed(candidate: NamingCandidate, base: str, attempt: int) -> str:
    if attempt == 1:
        return base
    # An occurrence's alias stays a canonical identifier.
    return f"{base}_{attempt}" if candidate.node_type == NodeType.SUBMODEL else f"{base} {attempt}"


def name_candidates(
    graph: PipelineGraph,
    candidates: list[NamingCandidate],
    read_utility: UtilityReader,
    *,
    allocate: bool,
) -> tuple[list[NamedCandidate], PipelineGraph]:
    """Name each candidate in request order against *graph*, and return the graph after.

    With *allocate*, a candidate takes the first free name the naming rule
    allows (``label``, ``label 2``, ... or ``alias``, ``alias_2``, ...),
    counting the names earlier candidates took. Without it, a candidate keeps
    its name, and a name the rule refuses comes back as its collision.
    """
    read_utility = functools.cache(read_utility)
    named: list[NamedCandidate] = []
    for candidate in candidates:
        base = candidate.alias if candidate.node_type == NodeType.SUBMODEL else candidate.label
        if base is None:
            raise ValueError(f"Submodel node {candidate.label!r} requires an alias.")
        attempts = range(1, _MAX_ALLOCATION_ATTEMPTS + 1) if allocate else range(1, 2)
        for attempt in attempts:
            label = _suffixed(candidate, base, attempt)
            trial = _with_name(graph, candidate, label)
            problem = _name_problem(trial, candidate, read_utility)
            if problem is None or not allocate:
                break
        else:
            raise ValueError(f"No free name for node {candidate.label!r}.")
        graph = trial
        alias = label if candidate.node_type == NodeType.SUBMODEL else None
        named.append(NamedCandidate(label=label, alias=alias, collision=problem))
    return named, graph
