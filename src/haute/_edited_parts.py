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
- the saved steps (``editor.saved_steps``) of a node saved with an unfinished step list,
  whose file holds the raising placeholder and the list in its ``.haute.json``: the list is
  then the node's program, as JSON text, and the node is built from it, so a run refuses it
  as unfinished as the file does and a save writes the record as it was;
- a node's saved settings (``editor.saved_settings``), its settings as JSON text the server
  wrote (:func:`settings_text`): the node is built from them, the settings it carries not
  read.

Edited settings come with the saved settings the edit began from
(``editor.settings_edited_from``): the node is built from the settings it carries, each
number the edit left as it was spelt as that form spells it (:func:`spelled_as`, CGEN-09).

A part sent without a saved form is read from the request: its editor edited it, or the
sender's baseline does not hold the node. A node saved with an unfinished step list holds
the raising placeholder in its file and the list in its ``.haute.json``; one whose steps are
not read is written from that record, the server's own copy, never from a list a request
carries.
"""

from __future__ import annotations

import difflib
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

#: The fields of a node's editor state that carry the saved form of each of its parts: its
#: steps' (the code saved for them, or the list itself for a node saved unfinished) and its
#: settings' (unedited, or the form edited settings began from).
SAVED_FORM_KEYS: tuple[str, ...] = (
    "saved_code",
    "saved_steps",
    "saved_settings",
    "settings_edited_from",
)


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


def _browser_held(value: Any) -> Any:
    """*value* as a browser gives it back, every number one double read back as JSON is."""
    from haute._polars_steps import js_held

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return js_held(value)
    if isinstance(value, Mapping):
        return {key: _browser_held(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_held(item) for item in value]
    return value


def _held_key(value: Any) -> str:
    """*value*, as a browser holds it, as text that compares equal exactly when two values
    are held alike."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=True)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def spelled_as(sent: Any, saved: Any) -> Any:
    """*sent*, settings a request read from a browser, with each number spelt as *saved*, the
    saved settings the edit began from, spells the number at the same place, where a browser
    holds the two alike (CGEN-09).

    An object's values are walked by key; a list's elements in order, aligned by content,
    so a row added, removed or moved does not shift the others, and a changed element is
    walked against the saved one it replaced. A number takes the saved number when a browser
    holds the saved number as the number sent, both whole or both not (``1.0`` for ``1``,
    ``9007199254740993`` for ``9007199254740992``; ``True`` never for ``1``). Every other
    value is *sent*'s: no value changes, only how one a browser cannot hold is spelt.
    """
    if isinstance(sent, Mapping) and isinstance(saved, Mapping):
        return {
            key: spelled_as(value, saved[key]) if key in saved else value
            for key, value in sent.items()
        }
    if isinstance(sent, list) and isinstance(saved, list):
        return _spelled_list(sent, saved)
    if _is_number(sent) and _is_number(saved):
        held = _browser_held(saved)
        return saved if type(held) is type(sent) and held == sent else sent
    return sent


def _spelled_list(sent: list[Any], saved: list[Any]) -> list[Any]:
    """*sent*'s elements, each walked against the element of *saved* it stands for."""
    held = [_held_key(_browser_held(item)) for item in saved]
    keys = [_held_key(item) for item in sent]
    if held == keys:
        return list(saved)
    out: list[Any] = []
    matcher = difflib.SequenceMatcher(a=held, b=keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(saved[i1:i2])
        elif tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            out.extend(spelled_as(sent[j1 + k], saved[i1 + k]) for k in range(paired))
            out.extend(sent[j1 + paired : j2])
        elif tag == "insert":
            out.extend(sent[j1:j2])
    return out


def holds_steps(node_type: object, config: Mapping[str, Any]) -> bool:
    """Whether a node of *node_type* configured with *config* holds a step list."""
    return node_type in STEPPED_NODE_TYPES and isinstance(config.get("steps"), list)


def _decoded(text: str, what: str, node: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"The {what} of node {node} are not JSON: {exc}") from exc


def config_as_read(
    node_type: object,
    config: Mapping[str, Any],
    *,
    saved_code: str | None,
    saved_settings: str | None,
    saved_steps: str | None = None,
    settings_edited_from: str | None = None,
    node: str,
) -> dict[str, Any]:
    """*config*, a node's as a request carries it, as the request reads it: its steps from
    *saved_code* or *saved_steps* and its settings from *saved_settings*, when it carries
    them; edited settings with each number the edit left as it was spelt as
    *settings_edited_from*, the saved settings the edit began from, spells it
    (:func:`spelled_as`).

    A saved code is the node's code and takes its step list off; an empty one (steps that
    did not render when saved) leaves the list, then the only copy of the node's program.
    Saved steps replace the list it carries, and its code, which building the node renders
    from them. Saved settings replace every setting *config* carries. *node* names the node
    in a refusal (``ValueError``): a saved form of its steps on a node holding no step list,
    both of them, saved steps that are not a list, saved settings and the settings an edit
    began from together, or either that is not a JSON object of settings.
    """
    read = dict(config)
    if saved_code is not None or saved_steps is not None:
        if not holds_steps(node_type, config):
            raise ValueError(
                f"Node {node} carries the saved form of its steps but holds no step list."
            )
        if saved_code is not None and saved_steps is not None:
            raise ValueError(f"Node {node} carries both a saved code and saved steps.")
    if saved_code is not None:
        if saved_code.strip():
            del read["steps"]
        read["code"] = saved_code
    if saved_steps is not None:
        steps = _decoded(saved_steps, "saved steps", node)
        if not isinstance(steps, list):
            raise ValueError(f"The saved steps of node {node} are not a list.")
        read["steps"] = steps
        read["code"] = ""
    if saved_settings is not None and settings_edited_from is not None:
        raise ValueError(
            f"Node {node} carries both saved settings and the settings an edit began from."
        )
    if saved_settings is not None:
        settings = _settings_form(saved_settings, "saved settings", node)
        read = {**settings, **{key: value for key, value in read.items() if key in STEPS_KEYS}}
    if settings_edited_from is not None:
        began = _settings_form(settings_edited_from, "settings its edit began from", node)
        read = {
            **spelled_as(settings_of(read), began),
            **{key: value for key, value in read.items() if key in STEPS_KEYS},
        }
    return read


def _settings_form(text: str, what: str, node: str) -> dict[str, Any]:
    """The settings *text*, a node's saved form of them, holds."""
    settings = _decoded(text, what, node)
    if not isinstance(settings, dict) or not STEPS_KEYS.isdisjoint(settings):
        raise ValueError(f"The {what} of node {node} are not an object of settings.")
    return settings


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
            saved = (saved_code, saved_steps, saved_settings, settings_edited_from) = tuple(
                editor.get(key) for key in SAVED_FORM_KEYS
            )
            if any(value is not None and not isinstance(value, str) for value in saved):
                out.append(node)
                continue
            ref: NodeRef = (scope, str(node.get("id")))
            node_type = data.get("nodeType", DEFAULT_NODE_TYPE)
            if saved_code is None and saved_steps is None and holds_steps(node_type, config):
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
                saved_steps=saved_steps,
                settings_edited_from=settings_edited_from,
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


def node_inputs(graph: PipelineGraph) -> dict[NodeRef, str]:
    """What each node of *graph* and of its definitions is rendered against, by scope and
    id, as JSON text: the edges into it (by source and handles) and, in a definition, the
    public input ports that reach it. A node's steps render against its inputs, so steps
    as saved keep the code saved for them only while their node's inputs are as saved."""
    scopes: list[tuple[str | None, Any, Any]] = [(None, graph, None)]
    scopes.extend((key, d.graph, d) for key, d in (graph.submodels or {}).items())
    held: dict[NodeRef, str] = {}
    for scope, scoped, definition in scopes:
        for node in scoped.nodes:
            edges = sorted(
                (edge.source, edge.sourceHandle or "", edge.targetHandle or "")
                for edge in scoped.edges
                if edge.target == node.id
            )
            ports = sorted(
                port.name
                for port in (definition.input_ports if definition is not None else ())
                if any(target.node_id == node.id for target in port.targets)
            )
            held[(scope, node.id)] = exact_json([edges, ports])
    return held


def with_saved_forms(
    graph: PipelineGraph, baseline: PipelineGraph, *, edited: Collection[NodeRef] = ()
) -> dict[str, Any]:
    """*graph*, built on the server from *baseline* (*baseline* itself for a graph saved as
    it was read), as a server-side writer's request carries it: each part of a node that is
    as *baseline* holds it with its saved form from *baseline*, as the editor sends it.

    A node's steps are sent without one, and so rendered, when they differ from the same
    node's in *baseline*, when *baseline* does not hold the node, when the node's steps name
    its inputs and those differ from the ones *baseline* gives it, when its steps are its
    file's record of unfinished steps (``steps_recorded``), for which the steps are the
    program, and when *edited* names it. Values are compared as their JSON text, so ``1``
    and ``1.0`` differ.
    """
    from haute._polars_steps import stepped_surface_allows_input_references

    held = scoped_nodes(baseline)
    held_inputs = node_inputs(baseline)
    payload = graph.model_dump(mode="python", by_alias=True)
    built = scoped_nodes(graph)
    inputs = node_inputs(graph)

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
                and (
                    not stepped_surface_allows_input_references(now.nodeType)
                    or inputs[ref] == held_inputs[ref]
                )
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
