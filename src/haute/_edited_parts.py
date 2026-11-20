"""Which parts of a node a request reads (CGEN-11, CGEN-13).

A node has two parts: its steps, the step list with the code it renders (``steps`` and
``code``), and its settings, every other key of its config. A value held in a browser may
have lost what the file holds (a number spelled ``1.0``, an integer past 2**53, a field), so
a request that carries the editor's document reads a part from the request only when its
editor edited it. For every other part the request carries the part's saved form, which the
server issued in a form a browser keeps exactly, and the node is built from that:

- a stepped node's saved code (``editor.saved_code``), the code its baseline holds for its
  steps: the node is built from it with its step list taken off, since building a node
  renders its steps into its code (``NodeData``);
- a node's saved settings (``editor.saved_settings``), its settings as JSON text the server
  wrote (:func:`settings_text`): the node is built from them, the settings it carries not
  read.

A part sent without a saved form is read from the request: its editor edited it, or the
sender's baseline does not hold the node. A node saved with an unfinished step list holds
the raising placeholder in its file and the list in its ``.haute.json``; one whose steps are
not read is written from that record, the server's own copy, never from a list a request
carries.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

import pydantic_core

from haute._node_specs import DEFAULT_NODE_TYPE, STEPPED_NODE_TYPES

if TYPE_CHECKING:
    from haute._types import PipelineGraph

#: A node by the graph that holds it, ``None`` for the pipeline's own nodes and a
#: submodel definition's id for that definition's nodes, with the node's id.
NodeRef = tuple[str | None, str]

#: The config keys of a node's steps; every other key is one of its settings.
STEPS_KEYS: frozenset[str] = frozenset({"steps", "code"})

#: The fields of a node's editor state that carry the saved form of each of its parts.
SAVED_FORM_KEYS: tuple[str, str] = ("saved_code", "saved_settings")


def exact_json(value: Any) -> str:
    """*value* as JSON text that keeps what a browser loses: a decimal's ``.0``, every
    digit of an integer, a number that is not finite."""
    return pydantic_core.to_json(value, inf_nan_mode="constants").decode("utf-8")


def settings_of(config: Mapping[str, Any]) -> dict[str, Any]:
    """The settings part of *config*: every key but its steps'."""
    return {key: value for key, value in config.items() if key not in STEPS_KEYS}


def settings_text(config: Mapping[str, Any]) -> str:
    """The saved form of *config*'s settings: their JSON text, which a browser keeps exactly."""
    return exact_json(settings_of(config))


def holds_steps(node_type: object, config: Mapping[str, Any]) -> bool:
    """Whether a node of *node_type* configured with *config* holds a step list."""
    return node_type in STEPPED_NODE_TYPES and isinstance(config.get("steps"), list)


def config_as_read(
    node_type: object,
    config: Mapping[str, Any],
    *,
    saved_code: str | None,
    saved_settings: str | None,
    node: str,
) -> dict[str, Any]:
    """*config*, a node's as a request carries it, as the request reads it: its steps from
    *saved_code* and its settings from *saved_settings*, when it carries them.

    A saved code is the node's code and takes its step list off; an empty one (steps that
    did not render when saved) leaves the list, then the only copy of the node's program.
    Saved settings replace every setting *config* carries. *node* names the node in a
    refusal (``ValueError``): a saved code on a node holding no step list, or saved settings
    that are not a JSON object of settings.
    """
    read = dict(config)
    if saved_code is not None:
        if not holds_steps(node_type, config):
            raise ValueError(
                f"Node {node} carries the code saved for its steps but holds no step list."
            )
        if saved_code.strip():
            del read["steps"]
        read["code"] = saved_code
    if saved_settings is not None:
        try:
            settings = json.loads(saved_settings)
        except ValueError as exc:
            raise ValueError(f"The saved settings of node {node} are not JSON: {exc}") from exc
        if not isinstance(settings, dict) or not STEPS_KEYS.isdisjoint(settings):
            raise ValueError(f"The saved settings of node {node} are not an object of settings.")
        read = {**settings, **{key: value for key, value in read.items() if key in STEPS_KEYS}}
    return read


def _describe(ref: NodeRef) -> str:
    scope, node_id = ref
    return repr(node_id) if scope is None else f"{node_id!r} in submodel {scope!r}"


class DocumentAsRead(NamedTuple):
    """A document as a request reads it, and the parts it read from the request."""

    graph: dict[str, Any]
    #: The nodes holding a step list that came without a saved code: their steps are read.
    edited_steps: frozenset[NodeRef]
    #: The nodes that came without saved settings: their settings are read.
    edited_settings: frozenset[NodeRef]


def graph_as_read(graph: Mapping[str, Any]) -> DocumentAsRead:
    """*graph*, a pipeline graph as a request carries it, as the request reads it before any
    node is built: each node built from the saved forms it carries (:func:`config_as_read`),
    which are then taken off its editor state, so a built node never holds one.

    A node or a graph of another shape is left as it is, for the request's own validation
    to refuse.
    """
    edited_steps: set[NodeRef] = set()
    edited_settings: set[NodeRef] = set()

    def read(scope: str | None, nodes: Any) -> Any:
        if not isinstance(nodes, list):
            return nodes
        out: list[Any] = []
        for node in nodes:
            data = node.get("data") if isinstance(node, Mapping) else None
            if not isinstance(node, Mapping) or not isinstance(data, Mapping):
                out.append(node)
                continue
            config = data.get("config", {})
            editor = data.get("editor", {})
            if not isinstance(config, Mapping) or not isinstance(editor, Mapping):
                out.append(node)
                continue
            saved = (saved_code, saved_settings) = tuple(editor.get(key) for key in SAVED_FORM_KEYS)
            if any(value is not None and not isinstance(value, str) for value in saved):
                out.append(node)
                continue
            ref: NodeRef = (scope, str(node.get("id")))
            node_type = data.get("nodeType", DEFAULT_NODE_TYPE)
            if saved_code is None and holds_steps(node_type, config):
                edited_steps.add(ref)
            if saved_settings is None:
                edited_settings.add(ref)
            if not any(key in editor for key in SAVED_FORM_KEYS):
                out.append(node)
                continue
            as_read = config_as_read(
                node_type,
                config,
                saved_code=saved_code,
                saved_settings=saved_settings,
                node=_describe(ref),
            )
            state = {key: value for key, value in editor.items() if key not in SAVED_FORM_KEYS}
            out.append({**node, "data": {**data, "config": as_read, "editor": state}})
        return out

    result = dict(graph)
    if "nodes" in graph:
        result["nodes"] = read(None, graph["nodes"])
    submodels = graph.get("submodels")
    if isinstance(submodels, Mapping):
        definitions: dict[Any, Any] = {}
        for definition_id, definition in submodels.items():
            inner = definition.get("graph") if isinstance(definition, Mapping) else None
            if not isinstance(inner, Mapping) or "nodes" not in inner:
                definitions[definition_id] = definition
                continue
            nodes = read(str(definition_id), inner["nodes"])
            definitions[definition_id] = {**definition, "graph": {**inner, "nodes": nodes}}
        result["submodels"] = definitions
    return DocumentAsRead(result, frozenset(edited_steps), frozenset(edited_settings))


def scoped_nodes(graph: PipelineGraph) -> dict[NodeRef, Any]:
    """Every node of *graph* and of its submodel definitions, by scope and id."""
    scopes: list[tuple[str | None, PipelineGraph]] = [(None, graph)]
    scopes.extend((key, d.graph) for key, d in (graph.submodels or {}).items())
    return {(scope, node.id): node for scope, scoped in scopes for node in scoped.nodes}


def with_saved_forms(
    graph: PipelineGraph, baseline: PipelineGraph, *, edited: Collection[NodeRef] = ()
) -> dict[str, Any]:
    """*graph*, built on the server from *baseline* (*baseline* itself for a graph saved as
    it was read), as a server-side writer's request carries it: each part of a node that is
    as *baseline* holds it with its saved form from *baseline*, as the editor sends it.

    A node's steps are sent without one, and so rendered, when they differ from the same
    node's in *baseline*, when *baseline* does not hold the node, when its steps are its
    file's record of unfinished steps (``steps_recorded``), for which the steps are the
    program, and when *edited* names it. Values are compared as their JSON text, so ``1``
    and ``1.0`` differ.
    """
    held = scoped_nodes(baseline)
    payload = graph.model_dump(mode="python", by_alias=True)
    built = scoped_nodes(graph)

    def given(scope: str | None, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            ref: NodeRef = (scope, node["id"])
            now = built[ref].data
            before = held.get(ref)
            editor = node["data"]["editor"]
            if before is None:
                continue
            config = before.data.config
            if (
                holds_steps(now.nodeType, now.config)
                and holds_steps(before.data.nodeType, config)
                and ref not in edited
                and not now.editor.steps_recorded
                and exact_json(now.config["steps"]) == exact_json(config["steps"])
            ):
                editor["saved_code"] = str(config.get("code") or "")
            if exact_json(settings_of(now.config)) == exact_json(settings_of(config)):
                editor["saved_settings"] = settings_text(config)

    given(None, payload["nodes"])
    for definition_id, definition in (payload.get("submodels") or {}).items():
        given(str(definition_id), definition["graph"]["nodes"])
    return payload


def recorded_unfinished_steps(on_disk: Any) -> dict[NodeRef, list[dict[str, Any]]]:
    """The unfinished step lists *on_disk*, a :class:`haute._types.PipelineGraph` parsed
    from the files a write replaces, gives its placeholder nodes from their ``.haute.json``
    records (``NodeEditorState.steps_recorded``), by node."""
    records: dict[NodeRef, list[dict[str, Any]]] = {}
    for ref, node in scoped_nodes(on_disk).items():
        steps = node.data.config.get("steps")
        if node.data.editor.steps_recorded and isinstance(steps, list):
            records[ref] = steps
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
