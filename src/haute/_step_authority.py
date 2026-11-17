"""Which stepped nodes a write takes from their steps (CGEN-11).

A stepped node's code is its program, and its steps are a view of that code:
the load rebuilds them from the code and accepts them only when they render
it exactly. A list that has been held anywhere since, a browser above all, may
have lost what the code holds (a number spelled ``1.0``, an integer past
2**53, a field), so a write renders a node's code from its steps only when the
writer names the node as one whose steps were edited, or when the node carries
no code and its steps are then the only copy. Every other stepped node is
written from the code it carries, byte for byte, and the list it carries is
not read: it is taken off before the node is built, because building a node
renders its steps into its code (``NodeData``).

A node saved with an unfinished step list holds the raising placeholder in its
file and the list in its ``.haute.json``; an unedited one is written from that
record, the server's own copy, never from a list a request carries.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from haute._node_specs import DEFAULT_NODE_TYPE, STEPPED_NODE_TYPES

#: A node by the graph that holds it, ``None`` for the pipeline's own nodes and a
#: submodel definition's id for that definition's nodes, with the node's id.
StepsRef = tuple[str | None, str]


def takes_steps(config: Mapping[str, Any], *, edited: bool) -> bool:
    """Whether a write renders a stepped node's code from the steps *config* holds:
    when its steps were *edited*, or when it carries no code."""
    if not isinstance(config.get("steps"), list):
        return False
    return edited or not str(config.get("code") or "").strip()


def without_unread_steps(config: Mapping[str, Any], *, edited: bool) -> dict[str, Any]:
    """*config*, as a write receives it, with its steps taken off unless the write
    renders them (:func:`takes_steps`); the code it carries is then its program."""
    if takes_steps(config, edited=edited) or "steps" not in config:
        return dict(config)
    return {key: value for key, value in config.items() if key != "steps"}


def _stepped_config(node: object) -> Mapping[str, Any] | None:
    """The config of *node*, a node as a request carries it, when its type authors steps."""
    if not isinstance(node, Mapping):
        return None
    data = node.get("data")
    if not isinstance(data, Mapping):
        return None
    config = data.get("config")
    node_type = data.get("nodeType", DEFAULT_NODE_TYPE)
    if not isinstance(config, Mapping) or node_type not in STEPPED_NODE_TYPES:
        return None
    return config


def graph_without_unread_steps(
    graph: Mapping[str, Any], edited: Collection[StepsRef]
) -> dict[str, Any]:
    """*graph*, a pipeline graph as a write receives it, before any node is built, with
    the steps of every stepped node it does not render taken off.

    *edited* names the nodes whose steps were edited. A name that is not a node holding
    a step list in that scope is refused (``ValueError``), so a writer and the graph it
    sends can never disagree about which list is read.
    """
    wanted = set(edited)
    found: set[StepsRef] = set()

    def visit(scope: str | None, nodes: Any) -> Any:
        if not isinstance(nodes, list):
            return nodes
        kept: list[Any] = []
        for node in nodes:
            config = _stepped_config(node)
            if config is None:
                kept.append(node)
                continue
            ref: StepsRef = (scope, str(node.get("id")))
            named = ref in wanted
            if named:
                if not isinstance(config.get("steps"), list):
                    raise ValueError(
                        f"Node {ref[1]!r} is named as having edited steps but holds no step list."
                    )
                found.add(ref)
            narrowed = without_unread_steps(config, edited=named)
            if len(narrowed) == len(config):
                kept.append(node)
                continue
            data = node["data"]
            kept.append({**node, "data": {**data, "config": narrowed}})
        return kept

    result = dict(graph)
    result["nodes"] = visit(None, graph.get("nodes"))
    submodels = graph.get("submodels")
    if isinstance(submodels, Mapping):
        definitions: dict[Any, Any] = {}
        for definition_id, definition in submodels.items():
            inner = definition.get("graph") if isinstance(definition, Mapping) else None
            if not isinstance(inner, Mapping):
                definitions[definition_id] = definition
                continue
            nodes = visit(str(definition_id), inner.get("nodes"))
            definitions[definition_id] = {**definition, "graph": {**inner, "nodes": nodes}}
        result["submodels"] = definitions
    missing = sorted(wanted - found, key=lambda ref: (ref[0] or "", ref[1]))
    if missing:
        names = ", ".join(
            repr(node_id) if scope is None else f"{node_id!r} in submodel {scope!r}"
            for scope, node_id in missing
        )
        raise ValueError(f"Edited steps name nodes this graph does not hold: {names}.")
    return result


def steps_to_render(graph: Any, before: Any | None = None) -> list[StepsRef]:
    """The stepped nodes a server-side writer of *graph* names: those whose steps differ
    from the same node's in *before*, the graph it changed (or that *before* does not
    hold), and those whose steps are their file's record of unfinished steps
    (``steps_recorded``), for which the steps are the program. Both graphs are
    :class:`haute._types.PipelineGraph` built on the server; *before* is ``None`` for a
    graph saved as it was parsed."""

    def lists(source: Any) -> dict[StepsRef, tuple[Any, bool]]:
        scopes: list[tuple[str | None, Any]] = [(None, source)]
        scopes.extend((key, d.graph) for key, d in (source.submodels or {}).items())
        held: dict[StepsRef, tuple[Any, bool]] = {}
        for scope, graph in scopes:
            for node in graph.nodes:
                steps = node.data.config.get("steps")
                if node.data.nodeType in STEPPED_NODE_TYPES and isinstance(steps, list):
                    held[(scope, node.id)] = (steps, node.data.editor.steps_recorded)
        return held

    previous = {} if before is None else {ref: steps for ref, (steps, _) in lists(before).items()}
    return sorted(
        (
            ref
            for ref, (steps, recorded) in lists(graph).items()
            if recorded or (before is not None and previous.get(ref, _ABSENT) != steps)
        ),
        key=lambda ref: (ref[0] or "", ref[1]),
    )


_ABSENT = object()


def recorded_unfinished_steps(on_disk: Any) -> dict[StepsRef, list[dict[str, Any]]]:
    """The unfinished step lists *on_disk*, a :class:`haute._types.PipelineGraph` parsed
    from the files a write replaces, gives its placeholder nodes from their ``.haute.json``
    records (``NodeEditorState.steps_recorded``), by node."""
    records: dict[StepsRef, list[dict[str, Any]]] = {}
    scopes: list[tuple[str | None, Any]] = [(None, on_disk)]
    scopes.extend(
        (definition_id, d.graph) for definition_id, d in (on_disk.submodels or {}).items()
    )
    for scope, graph in scopes:
        for node in graph.nodes:
            steps = node.data.config.get("steps")
            if node.data.editor.steps_recorded and isinstance(steps, list):
                records[(scope, node.id)] = steps
    return records


def with_recorded_unfinished_steps(
    node_type: str,
    config: Mapping[str, Any],
    recorded: Sequence[dict[str, Any]] | None,
    *,
    edited: bool,
) -> dict[str, Any] | None:
    """The config an unedited node saved with unfinished steps is written from: its
    *recorded* list and no code, so the write renders the record, as the file holds it.

    ``None`` when the node's steps were *edited*, nothing is recorded for it, or the code
    it carries is not the code its record loads as (a graph that does not descend from
    the file, such as an earlier commit's, carries its own)."""
    from haute._polars_steps import PolarsStepError, render_node_steps
    from haute._types import NodeType

    if edited or recorded is None:
        return None
    try:
        loads_as = render_node_steps(NodeType(node_type), list(recorded)).code
    except PolarsStepError:
        loads_as = ""
    if str(config.get("code") or "") != loads_as:
        return None
    narrowed = {key: value for key, value in config.items() if key != "code"}
    narrowed["steps"] = list(recorded)
    return narrowed
