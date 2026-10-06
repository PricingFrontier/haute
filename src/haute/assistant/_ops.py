"""Pure graph-edit operations used by the assistant mutation tool.

The wire models re-exported here deliberately know nothing about files or the
save service.  ``parse_ops`` validates the provider-shaped payload and
``apply_ops`` evaluates a parsed batch against a deep copy of a
``PipelineGraph``.  This keeps a failed batch from ever changing the graph
that the caller owns.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import json
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAlias, TypeVar, cast

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    # The data check imports this module, so only its type is named here.
    from haute.assistant._data_check import DataCheckResult

from haute._code_extraction import normalise_user_code
from haute._config_builder import _EXTRACTION_KIND_BY_CODE_TYPE
from haute._config_io import NODE_TYPE_TO_FOLDER, palette_default_config
from haute._config_validation import VALID_KEYS
from haute._executable_names import function_name_problem
from haute._graph_utils import (
    _edge_id,
    _sanitize_func_name,
    edge_input_name,
    executable_input_name,
    upstream_node_ids,
)
from haute._lru_cache import LRUCache
from haute._node_config_recovery import _DISCRIMINANTS
from haute._output_assembler import is_active_mapping_entry
from haute._polars_steps import (
    STEP_KINDS,
    STEPPED_NODE_TYPES,
    STEPPED_SURFACE_LABELS,
    PolarsStepError,
    is_stepped_config,
    rename_step_inputs,
    render_node_steps,
    render_polars_steps,
    step_input_names,
    step_input_references,
    stepped_surface_allows_input_references,
    stepped_surface_for,
    validate_polars_steps,
)
from haute._sandbox import _bound_names
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
)
from haute.assistant._catalog import INPUT_NAMING_RULE, capability_manifest, new_logic_steps
from haute.assistant._wire_ops import (
    MAX_DECLARED_POSTCONDITIONS,
    MAX_PLAN_OPERATIONS,
    AddEdgeOp,
    AddNodeOp,
    DeleteEdgeOp,
    DeleteNodeOp,
    EditStepsOp,
    GraphEditOp,
    LocatedPlanError,
    OpValidationError,
    RenameNodeOp,
    StepInsert,
    StepReplace,
    UpdateNodeOp,
    UpdatePreambleOp,
    parse_ops,
)
from haute.schemas import ASSISTANT_MAX_ASSUMPTIONS, ASSISTANT_RECEIPT_TEXT_LIMIT

_SUBMODEL_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})
#: The error a read or an edit of a submodel or a node inside one returns. No
#: correction clears it, so the model reports a blocker instead of retrying.
SUBMODEL_BOUNDARY_CODE = "submodel_boundary"
SUBMODEL_BOUNDARY_TEXT = (
    "submodels and the nodes inside them cannot be read or edited by the assistant."
)
SUBMODEL_BOUNDARY_FIX = (
    "Reply with a line starting BLOCKED: that asks the analyst to make this change in the editor."
)
_X_STEP = 280.0
_Y_STEP = 120.0
_ANY_HANDLE = object()
_MISSING = object()
_GRAPH_EDIT_OP_MODELS = (
    AddNodeOp,
    UpdateNodeOp,
    EditStepsOp,
    RenameNodeOp,
    DeleteNodeOp,
    AddEdgeOp,
    DeleteEdgeOp,
    UpdatePreambleOp,
)


def _invalid(
    message: str,
    *,
    fix: str,
    where: Mapping[str, object] | None = None,
    did_you_mean: Sequence[str] = (),
) -> NoReturn:
    raise OpValidationError(message, where=where, fix=fix, did_you_mean=did_you_mean)


class UnknownNodeReferenceError(OpValidationError):
    """An operation named a node the working graph does not hold.

    ``reference`` is the node, source or target exactly as the model sent it
    and ``role`` says which it was (``"edge target"``). The raise site sees
    one operation, so the batch loop rewrites the message and the fix from the
    whole plan (see ``_explain_unknown_reference``).
    """

    def __init__(
        self,
        reference: str,
        role: str,
        message: str,
        *,
        fix: str,
        did_you_mean: Sequence[str] = (),
    ) -> None:
        super().__init__(message, fix=fix, did_you_mean=did_you_mean)
        self.reference = reference
        self.role = role


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _nested_submodel_node_ids(
    submodels: dict[str, SubmodelDefinition] | None,
) -> set[str]:
    """Collect child ids owned by canonical submodel definitions."""
    return {node.id for definition in (submodels or {}).values() for node in definition.graph.nodes}


def _node_index(graph: PipelineGraph, node_id: str) -> int | None:
    for index, node in enumerate(graph.nodes):
        if node.id == node_id:
            return index
    return None


def _resolve_node_id(
    raw_id: str,
    graph: PipelineGraph,
    refs: Mapping[str, str],
    nested_ids: set[str],
    *,
    role: str,
) -> str:
    if raw_id.startswith("$"):
        ref = raw_id[1:]
        if not ref:
            _invalid(
                f"{role} reference '$' is empty",
                fix="Name the ref an earlier add_node declared, for example $agg.",
            )
        try:
            node_id = refs[ref]
        except KeyError:
            raise UnknownNodeReferenceError(
                raw_id,
                role,
                f"Unknown batch reference {raw_id!r}",
                fix=f"Declare ref {ref!r} on an earlier add_node in the same plan, "
                "or use the node's id.",
            ) from None
    else:
        node_id = raw_id

    if node_id in nested_ids:
        raise AssistantOperationError(
            SUBMODEL_BOUNDARY_CODE,
            f"{role} {raw_id!r} is inside a submodel: {SUBMODEL_BOUNDARY_TEXT}",
            where={"node": node_id},
            fix=SUBMODEL_BOUNDARY_FIX,
        )

    index = _node_index(graph, node_id)
    if index is None:
        raise UnknownNodeReferenceError(
            raw_id,
            role,
            f"Unknown {role} node {raw_id!r}",
            fix="Use a node id get_pipeline lists, or a $ref an earlier add_node declared.",
        )

    node_type = graph.nodes[index].data.nodeType
    if node_type in _SUBMODEL_TYPES:
        raise AssistantOperationError(
            SUBMODEL_BOUNDARY_CODE,
            f"{role} {raw_id!r} is a submodel boundary: {SUBMODEL_BOUNDARY_TEXT}",
            where={"node": node_id},
            fix=SUBMODEL_BOUNDARY_FIX,
        )
    return node_id


def _validate_config(
    node_type: NodeType,
    config: Mapping[str, Any],
    *,
    operation: str,
    removable_keys: set[str] | None = None,
) -> None:
    allowed = VALID_KEYS.get(node_type)
    if allowed is None:
        _invalid(
            f"{operation} does not support config for node type {node_type.value!r}",
            fix=f"Send no config for a {node_type.value!r} node.",
        )
    removable_keys = removable_keys or set()
    unknown = sorted(
        key
        for key, value in config.items()
        if key not in allowed and not (value is None and key in removable_keys)
    )
    if unknown:
        _invalid(
            f"{operation} contains unknown config key(s) for node type "
            f"{node_type.value!r}: {', '.join(unknown)}",
            fix=f"Remove {', '.join(unknown)}; use only the keys of the {node_type.value!r} "
            "descriptor's config schema.",
        )


def _replace_node(graph: PipelineGraph, index: int, node: GraphNode) -> None:
    graph.nodes[index] = node


def _free_code_form(node_type: NodeType) -> str:
    """The step list that writes new logic on *node_type*'s stepped surface."""

    return json.dumps(new_logic_steps(node_type, "..."))


def _refuse_instance_logic(node_id: str, original: str, field: str) -> NoReturn:
    _invalid(
        f"Node {node_id!r} is an instance of {original!r}: it runs {original!r}'s "
        f"steps and code, so its own {field} would never be read. Edit {original!r} "
        "instead; every instance of it follows.",
        where={"node": node_id, "field": field},
        fix=f"Make the change on the original node {original!r}.",
    )


def _check_stepped_write(
    node_type: NodeType,
    node_id: str,
    before: Mapping[str, Any] | None,
    written: Mapping[str, Any],
) -> None:
    """Refuse a write that would change how a stepped-type node is authored.

    *before* is the node's config ahead of the operation, ``None`` for the node
    an ``add_node`` creates (which starts from the palette's ``steps``). A node
    holding steps renders its ``code`` from them, so a ``code`` write would be
    overwritten and removing ``steps`` would switch it to code mode, an analyst
    action in the editor. A code-mode node with code would lose that code to a
    step list, and converting code into steps is out of scope. An instance runs
    its original's configuration, so its own logic would never be read.
    """

    original = written.get("instanceOf", (before or {}).get("instanceOf"))
    if original:
        for field in ("steps", "code"):
            if field in written:
                _refuse_instance_logic(node_id, str(original), field)
    if node_type not in STEPPED_NODE_TYPES:
        return
    label = STEPPED_SURFACE_LABELS[node_type]
    fix = f"Write new logic as steps with a free-code card: {_free_code_form(node_type)}."
    if before is None or is_stepped_config(node_type, before):
        if "code" in written:
            _invalid(
                f"Node {node_id!r} is a {label} authored as steps: its code is rendered "
                f"from them, so a code write would not land. {fix}",
                where={"node": node_id, "field": "code"},
                fix=fix,
            )
        if "steps" in written and not isinstance(written["steps"], list):
            _invalid(
                f"Node {node_id!r} is a {label} authored as steps: removing its steps "
                f"would switch it to code mode, which only the analyst does in the editor. "
                f"{fix}",
                where={"node": node_id, "field": "steps"},
                fix=fix,
            )
    elif isinstance(written.get("steps"), list) and str(before.get("code") or "").strip():
        _invalid(
            f"Node {node_id!r} is a {label} in code mode: setting steps would discard its "
            "code. Edit its code instead.",
            where={"node": node_id, "field": "steps"},
            fix="Edit the node's code instead of setting steps.",
        )


def _require_landed(
    node: GraphNode,
    written: Mapping[str, Any],
    *,
    operation: str,
    null_removes: bool,
) -> None:
    """Fail the plan unless every key *written* holds its value in the materialised config."""

    config = node.data.config
    for key, value in written.items():
        if value is None and null_removes:
            landed = key not in config
        else:
            landed = key in config and config[key] == value
        if not landed:
            raise AssistantOperationError(
                "op_not_applied",
                f"{operation} on node {node.id!r} did not land: {key!r} does not hold the "
                "written value in the node's config.",
                where={"node": node.id, "field": key},
                fix=f"Write {key!r} in the shape the node's descriptor card shows.",
            )
    if "steps" in written and "_steps_error" in config:
        node_type = node.data.nodeType
        try:
            render_node_steps(node_type, config["steps"])
        except PolarsStepError as exc:
            step = _step_id_at(config["steps"], exc.step_index)
            detail, fix = _unrendered_steps(node_type, config["steps"], exc)
        else:  # pragma: no cover - `_steps_error` is this rendering's own failure
            raise AssertionError("A node carrying _steps_error rendered its steps")
        raise AssistantOperationError(
            "op_not_applied",
            f"{operation} on node {node.id!r} did not land: {detail}",
            where={
                "node": node.id,
                "field": "steps",
                **({} if step is None else {"step": step}),
            },
            fix=fix,
        )


def _step_id_at(steps: Sequence[object], index: int | None) -> str | None:
    """The id of the step at *index*, which a step error names; None for the list."""

    if index is None:
        return None
    step = steps[index]
    step_id = step.get("id") if isinstance(step, Mapping) else None
    return step_id if isinstance(step_id, str) and step_id else None


#: Where an analyst's pivot table goes when a pivot step on an Explore does not render.
_EXPLORE_PIVOT_FIX = (
    "An analyst's pivot table is a pivots entry in the Explore's config, not a step; a "
    'pivot step only reshapes the frame. Call read_reference with "node:explore" for the '
    "entry's shape and add it with update_node."
)


def _unrendered_steps(
    node_type: NodeType, steps: Sequence[object], exc: PolarsStepError
) -> tuple[str, str]:
    """What a step list that does not render says after its node, and the fix.

    A step of a kind its surface holds that fails its own fields (a pivot
    without a pivot column, free code that does not parse) is named with its
    problem, to be completed where it stands; a pivot step on an Explore points
    at the node's ``pivots`` config instead, where an analyst's pivot table
    lives. Only what the surface cannot hold points at its free-code form: a
    list-level problem, a step that is not an object or of no known kind, a
    ``source`` step where the surface already binds ``df``, a ``join`` or
    ``concat`` where the code sees only ``df``, and a Transform whose steps do
    not start from their input.
    """

    index = exc.step_index
    step = None if index is None else steps[index]
    kind = step.get("kind") if isinstance(step, Mapping) else None
    surface = stepped_surface_for(node_type)
    if (
        index is None
        or kind not in STEP_KINDS
        or (kind == "source" if surface.start == "frame" else (index == 0) != (kind == "source"))
        or (surface.inputs == "none" and kind in ("join", "concat"))
    ):
        form = _free_code_form(node_type)
        return (
            f"its steps cannot be rendered ({exc}). New logic on a "
            f"{STEPPED_SURFACE_LABELS[node_type]} is written as {form}.",
            f"Write the steps as {form}.",
        )
    step_id = _step_id_at(steps, index)
    name = f"{kind} step {step_id!r}" if step_id else f"{kind} step {index + 1}"
    if kind == "pivot" and node_type == NodeType.EXPLORE:
        fix = _EXPLORE_PIVOT_FIX
    else:
        verb = "Correct" if kind == "free_code" else "Complete"
        fix = f"{verb} the {name}: {exc.message}"
    return f"its {name} cannot be rendered ({exc}).", fix


def _palette_config_for(node_type: NodeType, config: Mapping[str, Any]) -> dict[str, Any]:
    """The palette config a new node starts from beneath the model's *config*.

    An instance takes its configuration from its original, so it gets none. A
    config selecting another branch than the palette's (the discriminants
    config recovery uses) keeps only the palette's ``steps``: the palette
    belongs to one branch and must not fill another's fields.
    """

    if "instanceOf" in config:
        return {}
    defaults = palette_default_config(node_type)
    branch = _DISCRIMINANTS.get(node_type)
    if branch is not None and branch[0] in config and config[branch[0]] != defaults.get(branch[0]):
        return {key: value for key, value in defaults.items() if key == "steps"}
    return defaults


def _resolve_output_rows(
    node_type: NodeType, config: Mapping[str, Any], refs: Mapping[str, str], graph: PipelineGraph
) -> Mapping[str, Any]:
    """*config* with each ``outputMapping`` row's ``$ref`` source_port resolved.

    A ``source_port`` names the incoming frame a row reads, by the input name
    its edge gives: a ``$ref`` resolves to that name for the node the ref
    declared. Any other config is returned as written.
    """

    rows = config.get("outputMapping")
    if node_type != NodeType.OUTPUT or not isinstance(rows, list):
        return config
    resolved: list[object] = []
    for row in rows:
        port = row.get("source_port") if isinstance(row, Mapping) else None
        if isinstance(port, str) and port.startswith("$"):
            ref = port[1:]
            index = None if ref not in refs else _node_index(graph, refs[ref])
            if index is None:
                # Undeclared, or declared for a node an earlier operation deleted.
                raise UnknownNodeReferenceError(
                    port,
                    "output mapping source",
                    f"Unknown batch reference {port!r}",
                    fix=f"Declare ref {ref!r} on an earlier add_node in the same plan, "
                    "or write the incoming edge's name.",
                )
            source = graph.nodes[index]
            try:
                name = executable_input_name(
                    node_type=source.data.nodeType, label=source.data.label, source_handle=None
                )
            except ValueError:
                _invalid(
                    f"Output mapping source {port!r} names a node whose frames are named by "
                    "their ports",
                    where={"field": "outputMapping"},
                    fix="Write the source_port as the incoming edge's port name.",
                )
            row = {**cast(Mapping[str, Any], row), "source_port": name}
        resolved.append(row)
    return {**config, "outputMapping": resolved}


def _apply_add_node(
    graph: PipelineGraph,
    op: AddNodeOp,
    refs: dict[str, str],
    new_node_ids: list[str],
) -> None:
    if op.node_type in _SUBMODEL_TYPES:
        _invalid(
            f"Cannot add node type {op.node_type.value!r}: "
            "assistant operations cannot create submodel boundaries",
            fix="Add ordinary nodes instead; submodels are created by the analyst.",
        )
    _validate_config(op.node_type, op.config, operation="add_node")

    node_id = _sanitize_func_name(op.name)
    if _node_index(graph, node_id) is not None:
        _invalid(
            f"Cannot add node {op.name!r}: sanitized id {node_id!r} already exists",
            where={"node": node_id},
            fix=f"Choose another name, or change {node_id!r} with update_node.",
        )
    problem = function_name_problem(graph, node_id)
    if problem is not None:
        _invalid(
            f"Cannot add node {op.name!r}: {problem}",
            where={"node": node_id},
            fix="Choose another name.",
        )
    _check_stepped_write(op.node_type, node_id, None, op.config)
    written = _resolve_output_rows(op.node_type, op.config, refs, graph)
    node = GraphNode(
        id=node_id,
        type=op.node_type.value,
        data=NodeData(
            label=node_id,
            nodeType=op.node_type,
            config={**_palette_config_for(op.node_type, written), **deepcopy(written)},
        ),
        position={"x": 0.0, "y": 0.0},
    )
    _require_landed(node, written, operation="add_node", null_removes=False)
    graph.nodes.append(node)
    new_node_ids.append(node_id)

    if op.ref is not None:
        if op.ref in refs:
            _invalid(
                f"Duplicate batch reference {op.ref!r}",
                fix=f"Give this add_node a ref other than {op.ref!r}.",
            )
        refs[op.ref] = node_id


#: The field that names each entry of a config list, where that name is pipeline
#: metadata (an output column, an output path, a pivot id): what a refused blind
#: rewrite may say it would change. ``steps`` entries are named by their ids on
#: every stepped node type.
_LIST_ENTRY_NAMES: Mapping[tuple[NodeType, str], str] = MappingProxyType(
    {
        (NodeType.BANDING, "factors"): "outputColumn",
        (NodeType.RATING_STEP, "tables"): "outputColumn",
        (NodeType.RATING_STEP, "combinedOutputs"): "outputColumn",
        (NodeType.OUTPUT, "outputMapping"): "output_path",
        (NodeType.EXPLORE, "pivots"): "id",
        (NodeType.CONSTANT, "values"): "name",
    }
)
#: Config lists whose entries are themselves input or column names.
_NAME_LISTS = frozenset({"inputs", "selected_columns"})
#: Config maps keyed by input or column names; any other map's keys can be
#: category values, so a refusal counts them instead of naming them.
_NAME_KEYED_MAPS = frozenset({"input_scenario_map", "inputMapping", "column_renames"})


@dataclass(frozen=True, slots=True)
class ConfigVisibility:
    """What the planning model has seen of saved node configuration this turn.

    *withheld* says the session's egress policy withholds it, so the model
    sees none. Otherwise *read* holds the nodes whose saved configuration it
    has seen in the running turn: an ``inspect_node`` config part that returned
    it, or a node an earlier apply of the turn added. An earlier turn's reads
    do not count, because compaction drops their results from the history.
    A read follows its node through a rename and ends at its deletion, within
    a plan (``renamed``, ``deleted``) and across the turn's saves (``saved``).
    """

    withheld: bool
    read: frozenset[str] = frozenset()

    def renamed(self, old_id: str, new_id: str) -> ConfigVisibility:
        """This visibility once node *old_id* is renamed *new_id*.

        The read moves with the node: *new_id* is read exactly when *old_id*
        was, whatever node held that id before.
        """

        read = self.read - {old_id, new_id}
        return replace(self, read=read | {new_id} if old_id in self.read else read)

    def deleted(self, node_id: str) -> ConfigVisibility:
        """This visibility once node *node_id* is deleted: its read is dropped."""

        return replace(self, read=self.read - {node_id})

    def saved(self, changes: SemanticChanges) -> ConfigVisibility:
        """This visibility once a plan whose complete identities are *changes* is saved.

        Its renames move reads in operation order, a node it removed loses its
        read, and a node it added counts as read, because the model wrote all
        of it; an id a rename produced takes the renamed node's read instead.
        A node renamed onto an id the plan freed therefore never inherits the
        read of the node that held it.
        """

        visibility = self
        for old_id, new_id in changes.nodes_renamed:
            visibility = visibility.renamed(old_id, new_id)
        renamed_to = {new_id for _old_id, new_id in changes.nodes_renamed}
        added = set(changes.nodes_added) - renamed_to
        return replace(visibility, read=(visibility.read - set(changes.nodes_removed)) | added)


def _saved_entries_changed(
    node_type: NodeType, key: str, saved: object, written: object
) -> str | None:
    """The saved entries of a non-empty list or map *written* would change or drop.

    An entry is kept when *written* holds it unchanged: a list entry equal to
    it, or the same map key with an equal value. The changed entries are named
    by metadata only (see ``_LIST_ENTRY_NAMES``) and otherwise described by
    position or count. None when *saved* is not a non-empty list or map, or
    every entry is kept.
    """

    if isinstance(saved, list) and saved:
        kept = written if isinstance(written, list) else []
        lost = [position for position, entry in enumerate(saved, start=1) if entry not in kept]
        if not lost:
            return None
        field = "id" if key == "steps" else _LIST_ENTRY_NAMES.get((node_type, key))

        def name(position: int) -> str:
            entry = saved[position - 1]
            if key in _NAME_LISTS and isinstance(entry, str):
                return repr(entry)
            label = entry.get(field) if field and isinstance(entry, Mapping) else None
            return repr(label) if isinstance(label, str) and label else f"entry {position}"

        return f"saved {key} entries " + ", ".join(name(position) for position in lost)
    if isinstance(saved, Mapping) and saved:
        target = written if isinstance(written, Mapping) else {}
        lost_keys = [name for name, value in saved.items() if target.get(name, _MISSING) != value]
        if not lost_keys:
            return None
        if key in _NAME_KEYED_MAPS:
            return f"saved {key} entries " + ", ".join(repr(str(name)) for name in lost_keys)
        return f"{len(lost_keys)} of its {len(saved)} keys of the saved {key}"
    return None


_EDIT_STEPS_FIX = (
    "Change the steps with edit_steps, naming them by the ids the graph brief lists; the "
    "steps you do not name stay as saved."
)


def _refuse_blind_rewrite(
    node: GraphNode,
    written: Mapping[str, Any],
    new_node_ids: Sequence[str],
    visibility: ConfigVisibility,
) -> None:
    """Refuse an update that retypes a saved list or map the model has not seen.

    ``update_node`` replaces a key's whole value, so a rewrite of a list or map
    the model has not read loses or corrupts the entries it does not restate.
    A node this plan added holds only what the model wrote, and a node whose
    configuration the running turn read (*visibility*) is known; any other
    rewrite must keep every saved entry. Under a withholding policy the model
    cannot read it at all and is pointed at the analyst; otherwise at reading
    the node first.
    """

    if node.id in new_node_ids or (not visibility.withheld and node.id in visibility.read):
        return
    for key, value in written.items():
        changed = _saved_entries_changed(node.data.nodeType, key, node.data.config.get(key), value)
        if changed is None:
            continue
        where = {"node": node.id, "field": key}
        if visibility.withheld:
            raise AssistantOperationError(
                "config_withheld",
                f"The saved configuration of node {node.id!r} is withheld from you under this "
                f"project's egress policy, so update_node cannot replace {key!r}: it would "
                f"change or drop the {changed}, which you cannot read.",
                where=where,
                fix=_EDIT_STEPS_FIX
                if key == "steps"
                else "Do not retype configuration you cannot read: ask the analyst for what "
                "the change needs instead, in a reply line that starts with NEEDS_INPUT:.",
            )
        raise AssistantOperationError(
            "config_unread",
            f"update_node replaces {key!r} of node {node.id!r} whole, and you have not read "
            f"that node's saved configuration in this turn: it would change or drop the "
            f"{changed}.",
            where=where,
            fix=_EDIT_STEPS_FIX
            if key == "steps"
            else f'Read the node with inspect_node with parts ["config"], then resend the '
            f"update with {key!r} keeping its existing entries beside your change.",
        )


def _apply_update_node(
    graph: PipelineGraph,
    op: UpdateNodeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
    *,
    new_node_ids: Sequence[str],
    visibility: ConfigVisibility | None,
) -> str:
    """Apply one update and return the id of the node it wrote.

    *new_node_ids* are the nodes this plan added. *visibility*, when given,
    says what the planning model has seen of saved configuration, which
    refuses a blind rewrite (see ``_refuse_blind_rewrite``); an apply that
    replays a judged plan passes None.
    """

    node_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="update target")
    index = _node_index(graph, node_id)
    assert index is not None  # _resolve_node_id already checked this
    node = graph.nodes[index]
    _check_stepped_write(node.data.nodeType, node_id, node.data.config, op.config)
    _validate_config(
        node.data.nodeType,
        op.config,
        operation="update_node",
        removable_keys=set(node.data.config),
    )
    written = _resolve_output_rows(node.data.nodeType, op.config, refs, graph)
    if visibility is not None:
        _refuse_blind_rewrite(node, written, new_node_ids, visibility)

    config = dict(node.data.config)
    for key, value in written.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    updated = node.with_config(config)
    _require_landed(updated, written, operation="update_node", null_removes=True)
    _replace_node(graph, index, updated)
    return node_id


def _assigned_step_id(kind: object, taken: Sequence[object]) -> str:
    """``<kind>_<n>`` with the smallest ``n`` from 1 that no step in *taken* holds."""

    prefix = kind if isinstance(kind, str) and kind else "step"
    number = 1
    while f"{prefix}_{number}" in taken:
        number += 1
    return f"{prefix}_{number}"


def _apply_edit_steps(
    graph: PipelineGraph,
    op: EditStepsOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> tuple[str, tuple[str, ...]]:
    """Apply one step-level edit and return its node id and the step ids it changed.

    The operation only changes a list the node already holds, so it can never
    switch a node between steps and code (see ``_check_stepped_write``).
    """

    node_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="edit_steps target")
    index = _node_index(graph, node_id)
    assert index is not None  # _resolve_node_id already checked this
    node = graph.nodes[index]
    node_type = node.data.nodeType
    if node_type not in STEPPED_NODE_TYPES:
        _invalid(
            f"Node {node_id!r} is a {node_type.value!r} node, which does not author steps.",
            where={"node": node_id},
            fix=f"Change {node_id!r} with update_node.",
        )
    label = STEPPED_SURFACE_LABELS[node_type]
    config = node.data.config
    if original := config.get("instanceOf"):
        _refuse_instance_logic(node_id, str(original), "steps")
    steps = config.get("steps")
    if not isinstance(steps, list):
        if str(config.get("code") or "").strip():
            _invalid(
                f"Node {node_id!r} is a {label} in code mode: it has no steps to edit. "
                "Edit its code instead.",
                where={"node": node_id, "field": "code"},
                fix=f"Edit the node's code with update_node on {node_id!r}.",
            )
        form = _free_code_form(node_type)
        _invalid(
            f"Node {node_id!r} is a {label} with neither steps nor code, so it has no "
            "steps to edit.",
            where={"node": node_id, "field": "steps"},
            fix=f"Write its steps with update_node: {form}.",
        )

    edited: list[object] = deepcopy(steps)
    changed: list[str] = []

    def position_of(step_id: str) -> int:
        for position, step in enumerate(edited):
            if isinstance(step, Mapping) and step.get("id") == step_id:
                return position
        _invalid(
            f"Node {node_id!r} has no step {step_id!r}.",
            where={"node": node_id, "field": "steps", "step": step_id},
            fix="Name a step id the node's steps hold, as get_pipeline lists them.",
        )

    for edit in op.edits:
        ids = [step.get("id") if isinstance(step, Mapping) else None for step in edited]
        if not isinstance(edit, (StepInsert, StepReplace)):
            del edited[position_of(edit.remove)]
            changed.append(edit.remove)
            continue
        step = deepcopy(edit.step)
        if isinstance(edit, StepReplace):
            position = position_of(edit.replace)
            step.setdefault("id", edit.replace)
            taken = ids[:position] + ids[position + 1 :]
        else:
            position = 0 if edit.insert_after is None else position_of(edit.insert_after) + 1
            taken = ids
            step.setdefault("id", _assigned_step_id(step.get("kind"), taken))
        if step["id"] in taken:
            _invalid(
                f"Node {node_id!r} already has a step {step['id']!r}; step ids are unique "
                "within a node.",
                where={"node": node_id, "field": "steps", "step": step["id"]},
                fix="Give the step an id no other step holds, or omit id to have one assigned.",
            )
        if isinstance(edit, StepReplace):
            edited[position] = step
            changed.append(edit.replace)
        else:
            edited.insert(position, step)
        changed.append(str(step["id"]))

    updated = node.with_config({**config, "steps": edited})
    _require_landed(updated, {"steps": edited}, operation="edit_steps", null_removes=False)
    _replace_node(graph, index, updated)
    return node_id, tuple(changed)


#: Source types whose outgoing input names come from a handle, not the node's label.
_HANDLE_NAMED_SOURCES = frozenset({NodeType.API_INPUT, NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})
#: Config fields that hold one incoming edge's input name.
_INPUT_NAME_FIELDS = ("data_input", "banding_source", "analysis_input", "ratebook_input")
#: The consumer config fields a rename rewrites when they name the renamed input:
#: those the editor's rename reconciles (``frontend/src/utils/nodeUpdatePlan.ts``)
#: and ``outputMapping`` rows' ``source_port``.
RENAME_RECONCILED_FIELDS = (
    "steps",
    "inputMapping",
    "input_scenario_map",
    *_INPUT_NAME_FIELDS,
    "outputMapping",
)


def _code_reads_name(code: object, name: str) -> bool:
    """Whether *code* reads *name*; unparsable code cannot be shown not to."""

    if not isinstance(code, str):
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return True
    return any(
        isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    )


def _rename_collision(node_id: str, name: str) -> NoReturn:
    _invalid(
        f"Cannot rename to {name!r}: consumer {node_id!r} already has an input named {name!r}.",
        where={"node": node_id},
        fix=f"Choose a new name other than {name!r}, or rename {node_id!r}'s other input first.",
    )


def _rewrite_consumer(
    node: GraphNode, config: dict[str, Any], old: str, new: str
) -> tuple[list[str], list[str]]:
    """Rewrite *config*'s structured references to its input *old* as *new*.

    *config* is *node*'s working copy and is changed in place. Returns the
    rewritten field identities and the fields a rename cannot follow: code is
    never rewritten, so a free-code step or code-mode ``code`` reading *old*
    is returned for refusal. An instance's steps and code are its original's,
    so only its own ``inputMapping`` is considered.
    """

    changes: list[str] = []
    refused: list[str] = []
    steps = config.get("steps")
    names_inputs = stepped_surface_allows_input_references(node.data.nodeType) and not config.get(
        "instanceOf"
    )
    if isinstance(steps, list) and names_inputs:
        try:
            validated = validate_polars_steps(steps)
        except PolarsStepError as exc:
            _invalid(
                f"Cannot rename {old!r}: its consumer {node.id!r} has an invalid step "
                f"list ({exc}). Fix that node's steps first.",
                where={"node": node.id, "field": "steps"},
                fix=f"Correct {node.id!r}'s steps before renaming {old!r}.",
            )
        step_changes: list[str] = []
        for position, step in enumerate(validated, start=1):
            if old in step_input_references(step):
                key = "inputs" if step["kind"] == "concat" else "input"
                step_changes.append(f"steps[{step['id']}].{key}")
            elif step["kind"] == "free_code" and _code_reads_name(step["code"], old):
                refused.append(f"steps[{position}].code")
        if step_changes:
            try:
                config["steps"] = rename_step_inputs(steps, {old: new})
            except PolarsStepError:
                _rename_collision(node.id, new)
            changes.extend(step_changes)
    raw_mapping = config.get("inputMapping")
    mapping = raw_mapping if isinstance(raw_mapping, Mapping) else {}
    if (
        not isinstance(steps, list)
        and names_inputs
        and old not in mapping
        and _code_reads_name(config.get("code"), old)
    ):
        refused.append("code")
    bound = [logical for logical, current in mapping.items() if current == old]
    if bound:
        if new in mapping.values():
            _rename_collision(node.id, new)
        config["inputMapping"] = {
            logical: new if current == old else current for logical, current in mapping.items()
        }
        changes.extend(f"inputMapping.{logical}" for logical in bound)
    scenario_map = config.get("input_scenario_map")
    if isinstance(scenario_map, Mapping) and old in scenario_map:
        if new in scenario_map:
            _rename_collision(node.id, new)
        config["input_scenario_map"] = {
            new if name == old else name: scenario for name, scenario in scenario_map.items()
        }
        changes.append(f"input_scenario_map.{new}")
    for field_name in _INPUT_NAME_FIELDS:
        if config.get(field_name) == old:
            config[field_name] = new
            changes.append(field_name)
    output_mapping = config.get("outputMapping")
    if isinstance(output_mapping, list):
        rows = [
            position
            for position, entry in enumerate(output_mapping, start=1)
            if isinstance(entry, Mapping) and entry.get("source_port") == old
        ]
        if rows:
            config["outputMapping"] = [
                {**entry, "source_port": new} if position in rows else entry
                for position, entry in enumerate(output_mapping, start=1)
            ]
            changes.extend(f"outputMapping[{position}].source_port" for position in rows)
    return changes, refused


def _reconcile_rename(
    graph: PipelineGraph, node: GraphNode, new_id: str
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    """The consumer configs a rename of *node* to *new_id* rewrites, and their changes.

    Consumers name an ordinary source's frame by its sanitised label, so each
    target of an outgoing edge has its structured references to the old input
    rewritten (see :func:`_rewrite_consumer`), as do the ``inputMapping`` keys
    of the target's instances. Code that reads the old name, and instances
    naming the node itself through ``instanceOf``, raise
    :class:`RenameConsumersError`; a rewrite that would give a consumer two
    inputs of one name is an op error. Changes are ``<node>:<field>``
    identities. Nothing in *graph* is changed.
    """

    # The working graph changes op by op, so its cached node map is not used here.
    nodes_by_id = {candidate.id: candidate for candidate in graph.nodes}
    configs: dict[str, dict[str, Any]] = {}
    changes: dict[str, None] = {}
    refused: dict[tuple[str, str], None] = {}
    node_type = node.data.nodeType
    old_name = new_name = ""
    if node_type not in _HANDLE_NAMED_SOURCES:
        old_name = executable_input_name(
            node_type=node_type, label=node.data.label, source_handle=None
        )
        new_name = executable_input_name(node_type=node_type, label=new_id, source_handle=None)
    if old_name != new_name:
        targets = dict.fromkeys(edge.target for edge in graph.edges if edge.source == node.id)
        for target_id in targets:
            for edge in graph.edges:
                if edge.target != target_id or edge.source == node.id:
                    continue
                try:
                    name = edge_input_name(edge, nodes_by_id[edge.source])
                except (KeyError, ValueError):
                    # A malformed edge is the save validator's verdict, not this one's.
                    continue
                if name == new_name:
                    _rename_collision(target_id, new_name)
            config = configs.setdefault(target_id, dict(nodes_by_id[target_id].data.config))
            rewritten, code_fields = _rewrite_consumer(
                nodes_by_id[target_id], config, old_name, new_name
            )
            changes.update(dict.fromkeys(f"{target_id}:{field}" for field in rewritten))
            refused.update(dict.fromkeys((target_id, field) for field in code_fields))
            for instance in graph.nodes:
                if instance.data.config.get("instanceOf") != target_id:
                    continue
                config = configs.setdefault(instance.id, dict(instance.data.config))
                mapping = config.get("inputMapping")
                if not isinstance(mapping, Mapping) or old_name not in mapping:
                    continue
                if new_name in mapping:
                    _rename_collision(instance.id, new_name)
                config["inputMapping"] = {
                    new_name if logical == old_name else logical: current
                    for logical, current in mapping.items()
                }
                changes[f"{instance.id}:inputMapping.{new_name}"] = None
    for instance in graph.nodes:
        if instance.data.config.get("instanceOf") == node.id:
            refused[(instance.id, "instanceOf")] = None
    if refused:
        raise RenameConsumersError(node.id, new_id, tuple(refused))
    touched = {change.partition(":")[0] for change in changes}
    return (
        {node_id: config for node_id, config in configs.items() if node_id in touched},
        tuple(changes),
    )


def _apply_rename_node(
    graph: PipelineGraph,
    op: RenameNodeOp,
    refs: dict[str, str],
    nested_ids: set[str],
    new_node_ids: list[str],
) -> tuple[str, str, tuple[str, ...]]:
    """Apply one rename; return the node's old and new ids and its consumer changes."""

    old_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="rename target")
    index = _node_index(graph, old_id)
    assert index is not None
    new_id = _sanitize_func_name(op.new_name)
    if new_id != old_id and _node_index(graph, new_id) is not None:
        _invalid(
            f"Cannot rename node: sanitized id {new_id!r} already exists",
            where={"node": old_id},
            fix=f"Choose a new name whose id is not {new_id!r}.",
        )
    problem = function_name_problem(graph, new_id, excluding_node_id=old_id)
    if problem is not None:
        _invalid(
            f"Cannot rename node: {problem}", where={"node": old_id}, fix="Choose another name."
        )

    node = graph.nodes[index]
    configs, changes = _reconcile_rename(graph, node, new_id)
    for consumer_id, config in configs.items():
        consumer_index = _node_index(graph, consumer_id)
        assert consumer_index is not None
        _replace_node(graph, consumer_index, graph.nodes[consumer_index].with_config(config))
    data = node.data.model_copy(update={"label": new_id})
    _replace_node(graph, index, node.model_copy(update={"id": new_id, "data": data}))
    graph.edges = [
        edge.model_copy(
            update={
                "source": new_id if edge.source == old_id else edge.source,
                "target": new_id if edge.target == old_id else edge.target,
            }
        )
        for edge in graph.edges
    ]

    for ref, ref_id in list(refs.items()):
        if ref_id == old_id:
            refs[ref] = new_id
    for position, ref_id in enumerate(new_node_ids):
        if ref_id == old_id:
            new_node_ids[position] = new_id
    return old_id, new_id, changes


def _apply_delete_node(
    graph: PipelineGraph,
    op: DeleteNodeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
    new_node_ids: list[str],
) -> str:
    """Apply one delete and return the deleted node's id."""

    node_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="delete target")
    index = _node_index(graph, node_id)
    assert index is not None
    del graph.nodes[index]
    graph.edges = [
        edge for edge in graph.edges if edge.source != node_id and edge.target != node_id
    ]
    new_node_ids[:] = [candidate for candidate in new_node_ids if candidate != node_id]
    return node_id


def _apply_add_edge(
    graph: PipelineGraph,
    op: AddEdgeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> str:
    """Apply one edge addition and return its target's id."""

    source = _resolve_node_id(op.source, graph, refs, nested_ids, role="edge source")
    target = _resolve_node_id(op.target, graph, refs, nested_ids, role="edge target")
    # An exact duplicate would share its React Flow id with the existing
    # edge and become impossible to remove through delete_edge (the match
    # is ambiguous by construction).  Reject loudly instead.
    if any(
        edge.source == source
        and edge.target == target
        and edge.sourceHandle == op.source_handle
        and edge.targetHandle == op.target_handle
        for edge in graph.edges
    ):
        _invalid(
            f"Edge {source!r} -> {target!r} with these handles already exists; "
            "add_edge does not create duplicates",
            fix="Drop this add_edge; the edge is already there.",
        )
    try:
        graph.edges.append(
            GraphEdge(
                id=_edge_id(source, target, op.source_handle, op.target_handle),
                source=source,
                target=target,
                sourceHandle=op.source_handle,
                targetHandle=op.target_handle,
            )
        )
    except ValidationError as exc:
        raise OpValidationError(
            f"Invalid edge: {exc}",
            fix="Correct the edge's source, target and handles against get_pipeline.",
        ) from exc
    return target


def _apply_delete_edge(
    graph: PipelineGraph,
    op: DeleteEdgeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> str:
    """Apply one edge removal and return its target's id."""

    source = _resolve_node_id(op.source, graph, refs, nested_ids, role="edge source")
    target = _resolve_node_id(op.target, graph, refs, nested_ids, role="edge target")

    # An omitted handle is a wildcard.  An explicitly supplied JSON null is
    # meaningful and matches an edge with no handle, which is why the model's
    # fields-set metadata is used instead of treating both cases identically.
    source_handle: object = (
        op.source_handle if "source_handle" in op.model_fields_set else _ANY_HANDLE
    )
    target_handle: object = (
        op.target_handle if "target_handle" in op.model_fields_set else _ANY_HANDLE
    )

    matches = [
        index
        for index, edge in enumerate(graph.edges)
        if edge.source == source
        and edge.target == target
        and (source_handle is _ANY_HANDLE or edge.sourceHandle == source_handle)
        and (target_handle is _ANY_HANDLE or edge.targetHandle == target_handle)
    ]
    if not matches:
        _invalid(
            f"No edge matches {source!r} -> {target!r} with the requested handles",
            fix="Name an edge exactly as get_pipeline lists it, handles included.",
        )
    if len(matches) > 1:
        _invalid(
            f"Edge match {source!r} -> {target!r} is ambiguous; "
            "specify source_handle and target_handle",
            fix="Add the edge's source_handle and target_handle from get_pipeline.",
        )
    del graph.edges[matches[0]]
    return target


def _parents_by_node(graph: PipelineGraph) -> dict[str, tuple[str, ...]]:
    parents: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in parents and edge.source not in parents[edge.target]:
            parents[edge.target].append(edge.source)
    return {node_id: tuple(sorted(parent_ids)) for node_id, parent_ids in parents.items()}


def _assign_new_positions(graph: PipelineGraph, new_node_ids: Sequence[str]) -> None:
    """Assign deterministic positions after all operations have wired the graph."""

    new_order: list[str] = []
    seen: set[str] = set()
    final_ids = {node.id for node in graph.nodes}
    for node_id in new_node_ids:
        if node_id in final_ids and node_id not in seen:
            new_order.append(node_id)
            seen.add(node_id)
    if not new_order:
        return

    new_set = set(new_order)
    parents = _parents_by_node(graph)
    nodes_by_id = {node.id: node for node in graph.nodes}
    existing_nodes = [node for node in graph.nodes if node.id not in new_set]
    fallback_x = max((float(node.position.get("x", 0.0)) for node in existing_nodes), default=None)
    fallback_x = 0.0 if fallback_x is None else fallback_x + _X_STEP

    group_for = {node_id: parents.get(node_id, ()) for node_id in new_order}
    existing_group_counts: dict[tuple[str, ...], int] = {}
    for node in existing_nodes:
        group = parents.get(node.id, ())
        existing_group_counts[group] = existing_group_counts.get(group, 0) + 1
    sibling_index = {
        node_id: existing_group_counts.get(group_for[node_id], 0)
        + sum(1 for prior in new_order[:index] if group_for[prior] == group_for[node_id])
        for index, node_id in enumerate(new_order)
    }

    positions: dict[str, dict[str, float]] = {
        node.id: {
            "x": float(node.position.get("x", 0.0)),
            "y": float(node.position.get("y", 0.0)),
        }
        for node in existing_nodes
    }
    visiting: set[str] = set()

    def position_for(node_id: str) -> dict[str, float]:
        if node_id in positions:
            return positions[node_id]
        if node_id in visiting:
            # Cyclic graphs are not made invalid by this layout pass.  Use a
            # stable fallback for the cycle, then let every other operation
            # retain its normal validation semantics.
            return {"x": fallback_x, "y": sibling_index.get(node_id, 0) * _Y_STEP}

        visiting.add(node_id)
        parent_ids = group_for.get(node_id, ())
        parent_positions = [position_for(parent) for parent in parent_ids if parent in nodes_by_id]
        if parent_positions:
            x = max(position["x"] for position in parent_positions) + _X_STEP
            y = min(position["y"] for position in parent_positions)
        else:
            x = fallback_x
            y = 0.0
        result = {"x": x, "y": y + sibling_index[node_id] * _Y_STEP}
        visiting.remove(node_id)
        positions[node_id] = result
        return result

    for node_id in new_order:
        position_for(node_id)

    graph.nodes = [
        node.model_copy(update={"position": positions[node.id]}) if node.id in new_set else node
        for node in graph.nodes
    ]


@dataclass(frozen=True, slots=True)
class AppliedOps:
    """A batch applied to a copy of its graph.

    ``writers`` maps each node the batch added, updated or renamed to the
    index of the last operation that did, and a node whose incoming edges
    alone the batch added or removed to the first operation that did, so a
    failure found after the whole batch can name the operation that wrote
    its node. ``step_changes`` holds
    one ``<node>:steps[<id>]`` identity per step an ``edit_steps`` inserted,
    replaced or removed, ids assigned by the operation included.
    ``rename_changes`` holds one ``<node>:<field>`` identity per consumer
    field a ``rename_node`` rewrote, under the consumer's final id; a
    consumer the batch later deletes has none.
    """

    graph: PipelineGraph
    refs: dict[str, str]
    new_node_ids: tuple[str, ...]
    writers: Mapping[str, int]
    step_changes: tuple[str, ...] = ()
    rename_changes: tuple[str, ...] = ()


def locate_plan_error(
    exc: LocatedPlanError, writers: Mapping[str, int], graph: PipelineGraph
) -> None:
    """Stamp a failure found after a batch with its node's writer and graph."""

    node = exc.where.get("node")
    if "op_index" not in exc.where and isinstance(node, str) and node in writers:
        exc.where = {"op_index": writers[node], **exc.where}
    if exc.graph is None:
        exc.graph = graph


def _explain_unknown_reference(
    exc: UnknownNodeReferenceError,
    ops: Sequence[GraphEditOp],
    index: int,
    refs: Mapping[str, str],
    new_node_ids: Sequence[str],
    graph: PipelineGraph,
    position: Callable[[int], int],
) -> UnknownNodeReferenceError:
    """Say why operation *index* named no node, and the exact fix, from the batch.

    The first matching case decides: a bare word an earlier ``add_node``
    declared as its ref; a node a later ``add_node`` adds; an undeclared
    ``$ref``; otherwise a node no operation adds, with close node ids. The
    reference is never resolved on the model's behalf. *position* gives the
    index the model sent for an operation of the batch, which the text names.
    """

    reference, role = exc.reference, exc.role
    is_ref = reference.startswith("$")
    bare = reference[1:] if is_ref else reference
    head = (
        f"Unknown batch reference {reference!r}" if is_ref else f"Unknown {role} node {reference!r}"
    )

    def unknown(message: str, fix: str, close: Sequence[str] = ()) -> UnknownNodeReferenceError:
        return UnknownNodeReferenceError(
            reference, role, f"{head}: {message}", fix=fix, did_you_mean=close
        )

    if not is_ref and reference in refs:
        declared_at = position(
            next(
                offset
                for offset, op in enumerate(ops[:index])
                if isinstance(op, AddNodeOp) and op.ref == reference
            )
        )
        node_id = refs[reference]
        return unknown(
            f"{reference!r} is the ref add_node declared at operation {declared_at} for "
            f"node {node_id!r}, and a ref is used with a leading $.",
            f"Write '${reference}', the ref add_node declared at operation {declared_at}, "
            f"or the node's id {node_id!r}.",
        )

    for later, op in enumerate(ops[index + 1 :], start=index + 1):
        if not isinstance(op, AddNodeOp):
            continue
        added_id = _sanitize_func_name(op.name)
        names = {op.ref} if is_ref else {op.ref, op.name, added_id}
        if bare in names:
            return unknown(
                f"add_node {added_id!r} at operation {position(later)} comes after this "
                "operation, and operations apply in order.",
                f"Move add_node {added_id!r} (operation {position(later)}) before operation "
                f"{position(index)}.",
            )

    if is_ref:
        declared = ", ".join(f"'${ref}' (id {node_id!r})" for ref, node_id in refs.items())
        return unknown(
            f"no add_node before this operation declared ref {bare!r}. "
            + (
                f"Refs declared so far: {declared}."
                if declared
                else "This plan declares no ref before it."
            ),
            f"Declare ref {bare!r} on an add_node before this operation, or use a declared "
            "ref or a node id get_pipeline lists.",
        )

    ref_of = {node_id: ref for ref, node_id in refs.items()}
    added = ", ".join(
        f"{node_id!r} (${ref_of[node_id]})" if node_id in ref_of else repr(node_id)
        for node_id in new_node_ids
    )
    candidates = [node.id for node in graph.nodes if node.data.nodeType not in _SUBMODEL_TYPES]
    close = difflib.get_close_matches(reference, candidates, n=3, cutoff=0.6)
    add = (
        f"add {reference!r} with add_node before operation {position(index)}: each dry run "
        "is a "
        "whole plan, and a node a failed dry run proposed was never kept."
    )
    return unknown(
        "no node has this id and no operation of this plan adds it. "
        + (
            f"Nodes this plan adds so far: {added}."
            if added
            else "This plan adds no node before it."
        ),
        f"Use {close[0]!r} if that is the node you meant; otherwise {add}"
        if close
        else f"A{add[1:]} Otherwise use a node id get_pipeline lists.",
        close,
    )


def _apply_ops_with_refs(
    graph: PipelineGraph,
    ops: Sequence[GraphEditOp],
    positions: Sequence[int] | None = None,
    *,
    config_visibility: ConfigVisibility | None = None,
) -> AppliedOps:
    """Apply a batch to a copy of *graph*.

    Operations are evaluated in order.  A validation error can therefore
    refer to an earlier add, rename, or delete, while the caller's original
    graph remains untouched because no operation runs against it directly.
    A failure is stamped with the index of the operation that raised it and
    the working graph it was judged against. *positions*, when given, holds
    each operation's index in the batch the model sent, before its recipe
    operations expanded; every index a failure or ``writers`` reports is one.
    *config_visibility*, given for a model's dry-run, says what the model has
    seen of saved node configuration, which refuses a blind rewrite of a saved
    list or map (``_refuse_blind_rewrite``); its reads follow the batch's
    renames and deletions as they are applied.
    """

    def position(index: int) -> int:
        return index if positions is None else positions[index]

    raw_ops = list(ops)
    if any(isinstance(op, Mapping) for op in raw_ops):
        parsed_ops = parse_ops(raw_ops, positions)  # type: ignore[arg-type]
    else:
        parsed_ops = raw_ops

    working = graph.model_copy(deep=True)
    nested_ids = _nested_submodel_node_ids(working.submodels)
    refs: dict[str, str] = {}
    new_node_ids: list[str] = []
    writers: dict[str, int] = {}
    step_changes: set[str] = set()
    rename_changes: dict[str, set[str]] = {}
    visibility = config_visibility

    for index, op in enumerate(parsed_ops):
        try:
            if isinstance(op, AddNodeOp):
                _apply_add_node(working, op, refs, new_node_ids)
                writers[new_node_ids[-1]] = position(index)
            elif isinstance(op, UpdateNodeOp):
                written = _apply_update_node(
                    working,
                    op,
                    refs,
                    nested_ids,
                    new_node_ids=new_node_ids,
                    visibility=visibility,
                )
                writers[written] = position(index)
            elif isinstance(op, EditStepsOp):
                edited_id, step_ids = _apply_edit_steps(working, op, refs, nested_ids)
                writers[edited_id] = position(index)
                step_changes.update(
                    f"{_semantic_node_id(op.node, refs)}:steps[{step_id}]" for step_id in step_ids
                )
            elif isinstance(op, RenameNodeOp):
                old_id, new_id, changes = _apply_rename_node(
                    working, op, refs, nested_ids, new_node_ids
                )
                writers.pop(old_id, None)
                writers[new_id] = position(index)
                if old_id in rename_changes:
                    rename_changes[new_id] = rename_changes.pop(old_id)
                for change in changes:
                    consumer, _, field_name = change.partition(":")
                    writers[consumer] = position(index)
                    rename_changes.setdefault(consumer, set()).add(field_name)
                if visibility is not None:
                    visibility = visibility.renamed(old_id, new_id)
            elif isinstance(op, DeleteNodeOp):
                deleted = _apply_delete_node(working, op, refs, nested_ids, new_node_ids)
                rename_changes.pop(deleted, None)
                if visibility is not None:
                    visibility = visibility.deleted(deleted)
            elif isinstance(op, AddEdgeOp):
                writers.setdefault(_apply_add_edge(working, op, refs, nested_ids), position(index))
            elif isinstance(op, DeleteEdgeOp):
                target = _apply_delete_edge(working, op, refs, nested_ids)
                writers.setdefault(target, position(index))
            elif isinstance(op, UpdatePreambleOp):
                working.preamble = op.preamble
            else:
                _invalid(
                    f"Unsupported graph edit operation at index {position(index)}",
                    fix="Use an op the dry_run_graph_edits schema lists.",
                )
        except LocatedPlanError as exc:
            failure = exc
            if isinstance(exc, UnknownNodeReferenceError):
                failure = _explain_unknown_reference(
                    exc, parsed_ops, index, refs, new_node_ids, working, position
                )
            failure.where = {"op_index": position(index), **failure.where}
            if failure.graph is None:
                failure.graph = working
            if failure is exc:
                raise
            raise failure from None
        except ValidationError as exc:
            raise OpValidationError(
                f"Invalid graph edit operation at index {position(index)}: {exc}",
                where={"op_index": position(index)},
                fix=f"Correct operation {position(index)} against the dry_run_graph_edits schema.",
            ) from exc

    _assign_new_positions(working, new_node_ids)
    return AppliedOps(
        working,
        refs,
        tuple(new_node_ids),
        writers,
        tuple(sorted(step_changes)),
        tuple(
            sorted(
                f"{consumer}:{field_name}"
                for consumer, fields in rename_changes.items()
                for field_name in fields
            )
        ),
    )


def apply_ops(graph: PipelineGraph, ops: Sequence[GraphEditOp]) -> PipelineGraph:
    """Apply a batch to a deep copy of *graph* and return the resulting graph."""

    return _apply_ops_with_refs(graph, ops).graph


# The plan domain below is deliberately file-service agnostic.  It records the
# facts which an application service must later re-check under its save lock;
# it does not itself write a source file or a sidecar.
#: Entries per category of the provider-visible diff, and of the automatic
#: structural postcondition summary. Verification never reads a capped list.
_DIFF_LIMIT = 50
#: A sealed plan's postconditions: the declared (or automatic) list plus one
#: ``node_config`` per operation (a rename's rewritten consumers add theirs).
#: Apply replays the sealed list.
MAX_SEALED_POSTCONDITIONS = MAX_DECLARED_POSTCONDITIONS + MAX_PLAN_OPERATIONS


class AssistantOperationError(LocatedPlanError):
    """A stable, machine-readable failure from the assistant plan domain."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        where: Mapping[str, object] | None = None,
        fix: str | None = None,
    ) -> None:
        self.code = code
        super().__init__(message or code, where=where, fix=fix)


class RenameConsumersError(AssistantOperationError):
    """A rename refused because consumers read the node in code or name it by id."""

    def __init__(self, old_id: str, new_id: str, consumers: Sequence[tuple[str, str]]) -> None:
        self.consumers = tuple(consumers)
        listed = "; ".join(f"{node!r} {field}" for node, field in self.consumers)
        fix = (
            f"Rewrite each field to {new_id!r} with update_node or edit_steps earlier in "
            "the same plan, or keep the name."
        )
        super().__init__(
            "rename_has_consumers",
            f"Cannot rename node {old_id!r} to {new_id!r}: a rename rewrites edges and "
            "structured references but never code, and these consumers read it in code "
            f"or name it through instanceOf: {listed}. {fix}",
            where={"node": old_id},
            fix=fix,
        )


def _canonical_json(value: object) -> str:
    """Render JSON with the one representation used for revisions and plans."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _frozen_json(value: object) -> object:
    """Make a JSON-shaped value recursively immutable and equality-friendly."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _frozen_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen_json(item) for item in value)
    return value


def _wire_json(value: object) -> object:
    """Turn the immutable representation back into ordinary JSON values."""

    if isinstance(value, Mapping):
        return {key: _wire_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire_json(item) for item in value]
    return value


def _project_relative_path(project_root: Path, path: Path) -> tuple[Path, str]:
    root = project_root.resolve()
    resolved = path.resolve()
    try:
        return resolved, resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise AssistantOperationError(
            "project_source_forbidden", "Project source is outside the project root"
        ) from exc


def _source_manifest_entry(project_root: Path, path: Path) -> tuple[str, str]:
    resolved, relative = _project_relative_path(project_root, path)
    if not resolved.is_file():
        raise AssistantOperationError(
            "project_source_missing", f"Project source is missing: {relative}"
        )
    return f"content:{relative}", sha256(resolved.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectSourceEvidence:
    """One exact project fact previously returned to the assistant."""

    path: Path
    digest: str
    kind: Literal["content", "schema"] = "content"

    def __post_init__(self) -> None:
        if len(self.digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.digest
        ):
            raise ValueError("project evidence digest must be lowercase SHA-256 hex")


def dataset_schema_digest(schema: Mapping[str, object]) -> str:
    """Hash exactly the schema-only payload exposed to the model."""

    return _digest(schema)


def _evidence_manifest_entry(
    project_root: Path,
    evidence: ProjectSourceEvidence,
) -> tuple[str, str]:
    resolved, relative = _project_relative_path(project_root, evidence.path)
    if not resolved.is_file():
        raise AssistantOperationError(
            "project_source_missing",
            f"Dataset {relative} inspected earlier no longer exists. Call find_data "
            "to see the current datasets, then plan again."
            if evidence.kind == "schema"
            else f"Project source is missing: {relative}. Call get_project_knowledge "
            "again, then plan again.",
        )
    if evidence.kind == "content":
        actual = sha256(resolved.read_bytes()).hexdigest()
    else:
        from haute.routes.files import _read_schema_only_blocking

        actual = dataset_schema_digest(_read_schema_only_blocking(relative, resolved))
    if actual != evidence.digest:
        raise AssistantOperationError(
            "stale_project_evidence",
            f"Dataset {relative} changed after it was inspected. Call find_data with "
            "its path again, then plan again."
            if evidence.kind == "schema"
            else f"Project source {relative} changed after it was retrieved. Call "
            "get_project_knowledge again, then plan again.",
        )
    return f"{evidence.kind}:{relative}", actual


EvidenceKey: TypeAlias = tuple[str, str]
"""A ledger entry's identity: its evidence kind and the path the tool result named."""

# The codes `_evidence_manifest_entry` raises for evidence that no longer holds.
_RELEASABLE_EVIDENCE_CODES = frozenset({"project_source_missing", "stale_project_evidence"})


class SourceEvidenceLedger:
    """One session's evidence ledger: the project facts its tool results returned.

    Each entry is the `ProjectSourceEvidence` a successful `find_data` schema or
    `get_project_knowledge` item returned. It is *current* when the running turn
    observed it and *carried* when an earlier turn did. Every dry-run includes all
    entries in its snapshot (`sources`), so a plan binds the facts returned before
    it: a current entry that no longer holds fails the dry-run, while a carried one
    is released first (`release_stale_carried`), because the model no longer sees
    the result it came from. Live process state: a session never serializes it.
    """

    __slots__ = ("_current", "_entries")

    def __init__(self) -> None:
        self._entries: dict[EvidenceKey, ProjectSourceEvidence] = {}
        self._current: set[EvidenceKey] = set()

    def begin_turn(self) -> None:
        """Start a turn: every entry an earlier turn observed is now carried."""

        self._current.clear()

    def observe(self, key: EvidenceKey, evidence: ProjectSourceEvidence) -> None:
        """Add or replace an entry the running turn's tool result returned."""

        self._entries[key] = evidence
        self._current.add(key)

    def drop_vanished_schemas(self) -> None:
        """Drop schema evidence whose file no longer exists, as a dataset listing does."""

        for key in [
            key
            for key, evidence in self._entries.items()
            if evidence.kind == "schema" and not evidence.path.is_file()
        ]:
            del self._entries[key]
            self._current.discard(key)

    def release_stale_carried(self, project_root: Path) -> tuple[EvidenceKey, ...]:
        """Drop each carried entry whose file is missing or changed; return their keys.

        Any other failure while checking an entry propagates: it is not evidence
        that the fact went stale.
        """

        root = project_root.resolve()
        released: list[EvidenceKey] = []
        for key in sorted(set(self._entries) - self._current):
            try:
                _evidence_manifest_entry(root, self._entries[key])
            except AssistantOperationError as exc:
                if exc.code not in _RELEASABLE_EVIDENCE_CODES:
                    raise
                del self._entries[key]
                released.append(key)
        return tuple(released)

    def sources(self) -> tuple[ProjectSourceEvidence, ...]:
        """Every entry, in key order, for a dry-run's snapshot."""

        return tuple(self._entries[key] for key in sorted(self._entries))


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """An immutable description of the saved state a plan is authorized against."""

    revision: str
    capability_hash: str
    graph: PipelineGraph
    source_manifest: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "capability_hash": self.capability_hash,
            "graph": self.graph.model_dump(mode="json"),
            "source_manifest": dict(self.source_manifest),
        }


def build_project_snapshot(
    project_root: Path,
    source_file: Path,
    graph: PipelineGraph,
    project_sources: Sequence[Path | ProjectSourceEvidence] = (),
) -> ProjectSnapshot:
    """Build a content-addressed snapshot without reading anything outside *root*."""

    root = project_root.resolve()
    source_entries = [_source_manifest_entry(root, source_file)]
    config_path = root / "haute.toml"
    if config_path.exists():
        source_entries.append(_source_manifest_entry(root, config_path))
    for source in project_sources:
        source_entries.append(
            _evidence_manifest_entry(root, source)
            if isinstance(source, ProjectSourceEvidence)
            else _source_manifest_entry(root, source)
        )
    # Duplicate references are one source identity, not an accidental revision change.
    manifest = tuple(sorted(dict(source_entries).items()))
    capability_hash = capability_manifest().capability_hash
    canonical_graph = graph.model_dump(mode="json")
    revision = _digest(
        {
            "capability_hash": capability_hash,
            "graph": canonical_graph,
            "sources": dict(manifest),
        }
    )
    return ProjectSnapshot(
        revision=revision,
        capability_hash=capability_hash,
        graph=graph.model_copy(deep=True),
        source_manifest=manifest,
    )


@dataclass(frozen=True, slots=True)
class SemanticChanges:
    """The complete, untruncated change identities every verification reads."""

    nodes_added: tuple[str, ...] = ()
    nodes_removed: tuple[str, ...] = ()
    nodes_renamed: tuple[tuple[str, str], ...] = ()
    nodes_updated: tuple[str, ...] = ()
    edges_added: tuple[tuple[str, str, str | None, str | None], ...] = ()
    edges_removed: tuple[tuple[str, str, str | None, str | None], ...] = ()
    config_changes: tuple[str, ...] = ()

    @property
    def written_nodes(self) -> frozenset[str]:
        """Every node the plan adds or updates."""

        return frozenset((*self.nodes_added, *self.nodes_updated))


@dataclass(frozen=True, slots=True)
class SemanticDiff:
    """The provider-visible diff: identity lists capped at ``_DIFF_LIMIT``.

    ``complete`` holds the same identities untruncated. ``as_dict`` never
    emits it, but equality compares it, and verification reads only it.
    """

    nodes_added: tuple[str, ...] = ()
    nodes_removed: tuple[str, ...] = ()
    nodes_renamed: tuple[tuple[str, str], ...] = ()
    nodes_updated: tuple[str, ...] = ()
    edges_added: tuple[tuple[str, str, str | None, str | None], ...] = ()
    edges_removed: tuple[tuple[str, str, str | None, str | None], ...] = ()
    config_changes: tuple[str, ...] = ()
    preamble_changed: bool = False
    sidecar_changes: tuple[str, ...] = ()
    complete_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    complete_hash: str = ""
    truncated: bool = False
    complete: SemanticChanges = field(default_factory=SemanticChanges, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "nodes_added": self.nodes_added,
            "nodes_removed": self.nodes_removed,
            "nodes_renamed": self.nodes_renamed,
            "nodes_updated": self.nodes_updated,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "config_changes": self.config_changes,
            "preamble_changed": self.preamble_changed,
            "sidecar_changes": self.sidecar_changes,
            "complete_counts": dict(self.complete_counts),
            "complete_hash": self.complete_hash,
            "truncated": self.truncated,
        }


def _edge_identity(edge: GraphEdge) -> tuple[str, str, str | None, str | None]:
    return (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)


def _complete_edge_identity(
    edge: GraphEdge,
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    return (
        edge.source,
        edge.target,
        edge.sourceHandle,
        edge.targetHandle,
        edge.sourcePort,
        edge.targetPort,
    )


_Identity = TypeVar("_Identity", bound=tuple[Any, ...])


def _sorted_identities(values: set[_Identity]) -> tuple[_Identity, ...]:
    """Sort nullable tuple identities without comparing ``None`` to strings."""

    return tuple(sorted(values, key=_canonical_json))


def _semantic_node_id(raw_id: str, refs: Mapping[str, str]) -> str:
    if raw_id.startswith("$"):
        return refs.get(raw_id[1:], raw_id)
    return raw_id


def _semantic_diff(
    before: PipelineGraph,
    after: PipelineGraph,
    ops: Sequence[GraphEditOp],
    applied: AppliedOps,
) -> SemanticDiff:
    refs = applied.refs
    old_nodes = {node.id: node for node in before.nodes}
    new_nodes = {node.id: node for node in after.nodes}
    renamed = tuple(
        (_semantic_node_id(op.node, refs), _sanitize_func_name(op.new_name))
        for op in ops
        if isinstance(op, RenameNodeOp) and not op.node.startswith("$")
    )
    # Save/codegen/parser may canonicalise untouched source bodies and
    # inferred contracts. The semantic mutation diff is therefore grounded
    # in the explicit operation vocabulary, while added/removed nodes and
    # edges are still derived from the actual before/after graphs.
    updates = tuple(
        sorted(
            {
                *(
                    _semantic_node_id(op.node, refs)
                    for op in ops
                    if isinstance(op, (UpdateNodeOp, EditStepsOp))
                ),
                *(change.partition(":")[0] for change in applied.rename_changes),
            }
        )
    )
    config_changes = tuple(
        sorted(
            [
                *(
                    f"{_semantic_node_id(op.node, refs)}:{key}"
                    for op in ops
                    if isinstance(op, UpdateNodeOp)
                    for key in op.config
                ),
                *applied.step_changes,
                *applied.rename_changes,
            ]
        )
    )
    old_edges = {_edge_identity(edge) for edge in before.edges}
    new_edges = {_edge_identity(edge) for edge in after.edges}
    added_ids = new_nodes.keys() - old_nodes.keys()
    removed_ids = old_nodes.keys() - new_nodes.keys()
    sidecar_ids = added_ids | removed_ids | set(updates)
    sidecar_changes = tuple(
        sorted(
            f"config/{folder}/{node_id}"
            for node_id in sidecar_ids
            if (node := new_nodes.get(node_id) or old_nodes.get(node_id)) is not None
            if (folder := NODE_TYPE_TO_FOLDER.get(node.data.nodeType)) is not None
        )
    )
    nodes_added = tuple(sorted(added_ids))
    nodes_removed = tuple(sorted(removed_ids))
    edges_added = _sorted_identities(new_edges - old_edges)
    edges_removed = _sorted_identities(old_edges - new_edges)
    complete_values: dict[str, tuple[object, ...]] = {
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "nodes_renamed": renamed,
        "nodes_updated": updates,
        "edges_added": edges_added,
        "edges_removed": edges_removed,
        "config_changes": config_changes,
        "sidecar_changes": sidecar_changes,
    }
    complete_counts = {category: len(values) for category, values in complete_values.items()}
    old_complete_edges = {_complete_edge_identity(edge) for edge in before.edges}
    new_complete_edges = {_complete_edge_identity(edge) for edge in after.edges}
    complete_payload = {
        **complete_values,
        "complete_edges_added": _sorted_identities(new_complete_edges - old_complete_edges),
        "complete_edges_removed": _sorted_identities(old_complete_edges - new_complete_edges),
        "preamble_digests": (
            sha256((before.preamble or "").encode("utf-8")).hexdigest(),
            sha256((after.preamble or "").encode("utf-8")).hexdigest(),
        ),
    }
    return SemanticDiff(
        nodes_added=nodes_added[:_DIFF_LIMIT],
        nodes_removed=nodes_removed[:_DIFF_LIMIT],
        nodes_renamed=renamed[:_DIFF_LIMIT],
        nodes_updated=updates[:_DIFF_LIMIT],
        edges_added=edges_added[:_DIFF_LIMIT],
        edges_removed=edges_removed[:_DIFF_LIMIT],
        config_changes=config_changes[:_DIFF_LIMIT],
        preamble_changed=before.preamble != after.preamble,
        sidecar_changes=sidecar_changes[:_DIFF_LIMIT],
        complete_counts=MappingProxyType(complete_counts),
        complete_hash=_digest(complete_payload),
        truncated=any(count > _DIFF_LIMIT for count in complete_counts.values()),
        complete=SemanticChanges(
            nodes_added=nodes_added,
            nodes_removed=nodes_removed,
            nodes_renamed=renamed,
            nodes_updated=updates,
            edges_added=edges_added,
            edges_removed=edges_removed,
            config_changes=config_changes,
        ),
    )


def semantic_diff(
    before: PipelineGraph,
    after: PipelineGraph,
    operations: Sequence[GraphEditOp] | Sequence[Mapping[str, Any]],
) -> SemanticDiff:
    """Return the bounded canonical semantic diff for an exact operation batch."""

    typed_operations = [
        operation if isinstance(operation, _GRAPH_EDIT_OP_MODELS) else parse_ops([operation])[0]
        for operation in operations
    ]
    applied = _apply_ops_with_refs(before, typed_operations)
    return _semantic_diff(before, after, typed_operations, applied)


def _authored_config_projection(
    node_type: NodeType, config: Mapping[str, Any]
) -> dict[str, object] | None:
    """The part of a code-carrying node's config a save must keep exactly.

    A node holding a ``steps`` list is checked by its steps (its ``code`` is
    derived from them); any other node of a code-carrying type by its code as
    reparse extracts it. The projection is narrow on purpose: the save path
    legitimately normalises other fields, and editor-state keys
    (``_steps_error``, ``_steps_discarded``) are derived. Other types and
    instances, whose configuration is their original's, have none.
    """

    kind = _EXTRACTION_KIND_BY_CODE_TYPE.get(node_type)
    if kind is None or config.get("instanceOf"):
        return None
    if is_stepped_config(node_type, config):
        return {"steps": config["steps"]}
    return {"code": normalise_user_code(str(config.get("code") or ""), kind=kind)}


def _config_postconditions(graph: PipelineGraph, diff: SemanticDiff) -> tuple[object, ...]:
    """One ``node_config`` digest per added or updated code-carrying node.

    Every such node, never a truncated subset: nothing else proves the saved
    config. At most one per operation, except that a rename adds one per
    code-carrying consumer it rewrites; a plan whose list would then exceed
    ``MAX_SEALED_POSTCONDITIONS`` fails validation.
    """

    nodes = {node.id: node for node in graph.nodes}
    conditions: list[object] = []
    for node_id in sorted(diff.complete.written_nodes):
        node = nodes.get(node_id)
        if node is None:
            continue
        projection = _authored_config_projection(node.data.nodeType, node.data.config)
        if projection is not None:
            conditions.append(
                _frozen_json(
                    {"kind": "node_config", "node": node_id, "sha256": _digest(projection)}
                )
            )
    return tuple(conditions)


def _automatic_postconditions(graph: PipelineGraph, diff: SemanticDiff) -> tuple[object, ...]:
    """A bounded structural summary of at most ``_DIFF_LIMIT`` conditions.

    Identity conditions give way before the two whole-graph checks. The
    summary may omit identities beyond the bound because post-save exactness
    compares the complete semantic-diff digest, which covers every node and
    edge identity; authored config has no such backstop and is proved by the
    complete ``node_config`` list instead.
    """

    changes = diff.complete
    whole_graph: list[object] = []
    if diff.preamble_changed:
        whole_graph.append(
            _frozen_json(
                {
                    "kind": "preamble_digest",
                    "sha256": sha256((graph.preamble or "").encode("utf-8")).hexdigest(),
                }
            )
        )
    whole_graph.append(
        _frozen_json({"kind": "graph_shape", "nodes": len(graph.nodes), "edges": len(graph.edges)})
    )
    conditions: list[object] = [
        _frozen_json({"kind": "node_exists", "node": node_id}) for node_id in changes.nodes_added
    ]
    conditions.extend(
        _frozen_json(
            {
                "kind": "edge_exists",
                "source": source,
                "target": target,
                "source_handle": source_handle,
                "target_handle": target_handle,
            }
        )
        for source, target, source_handle, target_handle in changes.edges_added
    )
    conditions.extend(
        _frozen_json({"kind": "node_absent", "node": node_id}) for node_id in changes.nodes_removed
    )
    conditions.extend(
        _frozen_json(
            {
                "kind": "edge_absent",
                "source": source,
                "target": target,
                "source_handle": source_handle,
                "target_handle": target_handle,
            }
        )
        for source, target, source_handle, target_handle in changes.edges_removed
    )
    return (*conditions[: _DIFF_LIMIT - len(whole_graph)], *whole_graph)


def verify_postconditions(
    graph: PipelineGraph,
    postconditions: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    """Evaluate closed structural postconditions and return bounded evidence."""

    nodes = {node.id: node for node in graph.nodes}
    edges = {_edge_identity(edge) for edge in graph.edges}
    evidence: list[Mapping[str, object]] = []
    for raw in postconditions:
        if not isinstance(raw, Mapping):
            raise AssistantOperationError("invalid_plan", "Postcondition must be an object")
        condition = _wire_json(raw)
        assert isinstance(condition, dict)
        kind = condition.get("kind")
        passed = False
        if kind == "node_exists":
            passed = condition.get("node") in nodes
        elif kind == "node_absent":
            passed = condition.get("node") not in nodes
        elif kind in {"edge_exists", "edge_absent"}:
            source = condition.get("source")
            target = condition.get("target")
            source_handle = condition.get("source_handle", _ANY_HANDLE)
            target_handle = condition.get("target_handle", _ANY_HANDLE)
            matched = any(
                edge[0] == source
                and edge[1] == target
                and (source_handle is _ANY_HANDLE or edge[2] == source_handle)
                and (target_handle is _ANY_HANDLE or edge[3] == target_handle)
                for edge in edges
            )
            passed = matched if kind == "edge_exists" else not matched
        elif kind == "graph_shape":
            passed = condition.get("nodes") == len(graph.nodes) and condition.get("edges") == len(
                graph.edges
            )
        elif kind == "preamble_digest":
            passed = (
                condition.get("sha256")
                == sha256((graph.preamble or "").encode("utf-8")).hexdigest()
            )
        elif kind == "node_config":
            node = nodes.get(condition.get("node"))  # type: ignore[arg-type]
            projection = (
                None
                if node is None
                else _authored_config_projection(node.data.nodeType, node.data.config)
            )
            passed = projection is not None and _digest(projection) == condition.get("sha256")
        else:
            raise AssistantOperationError(
                "invalid_plan",
                f"Unsupported postcondition kind: {kind!r}",
            )
        item = MappingProxyType({"kind": str(kind), "passed": passed})
        evidence.append(item)
        if not passed:
            raise AssistantOperationError(
                "postcondition_failed",
                f"Postcondition {kind!r} was not satisfied",
            )
    return tuple(evidence)


def _affected_capabilities(
    before: PipelineGraph,
    after: PipelineGraph,
    diff: SemanticDiff,
    ops: Sequence[GraphEditOp],
    refs: Mapping[str, str],
) -> tuple[str, ...]:
    old_nodes = {node.id: node for node in before.nodes}
    new_nodes = {node.id: node for node in after.nodes}
    ids = set(old_nodes).symmetric_difference(new_nodes)
    old_edges = {_edge_identity(edge) for edge in before.edges}
    new_edges = {_edge_identity(edge) for edge in after.edges}
    for source, target, _source_handle, _target_handle in old_edges.symmetric_difference(new_edges):
        ids.update((source, target))
    ids.update(
        _semantic_node_id(op.node, refs)
        for op in ops
        if isinstance(op, (UpdateNodeOp, EditStepsOp, RenameNodeOp))
    )
    capabilities = {
        node.data.nodeType.value
        for node_id in ids
        if (node := new_nodes.get(node_id) or old_nodes.get(node_id)) is not None
    }
    if diff.preamble_changed:
        capabilities.add("pipeline_preamble")
    return tuple(sorted(capabilities))


#: Whether building a plan ran node code over project data: ``none`` when no
#: schema target was resolved, ``schema-resolution`` when at least one was.
PlanEgress = Literal["none", "schema-resolution"]


@dataclass(frozen=True, slots=True)
class GraphEditPlan:
    base_revision: str
    capability_hash: str
    source_manifest: tuple[tuple[str, str], ...]
    normalized_operations: tuple[object, ...]
    diff: SemanticDiff
    affected_capabilities: tuple[str, ...]
    postconditions: tuple[object, ...]
    validation_warnings: tuple[str, ...]
    resulting_graph_shape: Mapping[str, int]
    egress: PlanEgress
    verification_tier: Literal["structural", "schema"]
    verification_evidence: tuple[Mapping[str, object], ...]
    plan_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "base_revision": self.base_revision,
            "capability_hash": self.capability_hash,
            "revision_sources": dict(self.source_manifest),
            "normalized_operations": _wire_json(self.normalized_operations),
            "diff": self.diff.as_dict(),
            "affected_capabilities": self.affected_capabilities,
            "postconditions": _wire_json(self.postconditions),
            "validation_warnings": self.validation_warnings,
            "resulting_graph_shape": dict(self.resulting_graph_shape),
            "egress": self.egress,
            "verification_tier": self.verification_tier,
            "verification_evidence": _wire_json(self.verification_evidence),
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True, slots=True)
class PreparedGraphEdit:
    """The canonical, validated edit facts shared by plan finalisation.

    This deliberately stops before validation warnings and verification
    evidence, which belong to the application layer.  Everything describing
    the user-authored edit itself is computed once here.
    """

    snapshot: ProjectSnapshot
    result_graph: PipelineGraph
    normalized_operations: tuple[object, ...]
    diff: SemanticDiff
    affected_capabilities: tuple[str, ...]
    postconditions: tuple[object, ...]
    #: Each node the batch wrote, mapped to the last operation that wrote it.
    writers: Mapping[str, int]


def _resolve_postcondition_refs(
    condition: Mapping[str, Any],
    refs: Mapping[str, str],
) -> dict[str, Any]:
    """Resolve recipe/user postcondition refs against this exact applied batch."""

    resolved = dict(condition)
    for field_name in ("node", "source", "target"):
        value = resolved.get(field_name)
        if not isinstance(value, str) or not value.startswith("$"):
            continue
        ref = value[1:]
        if ref not in refs:
            raise AssistantOperationError(
                "invalid_plan",
                f"Postcondition references unknown batch-local ref {value!r}",
            )
        resolved[field_name] = refs[ref]
    return resolved


def validate_declared_postconditions(postconditions: Sequence[Mapping[str, Any]]) -> None:
    """Validate a caller-declared list, which holds at most ``MAX_DECLARED_POSTCONDITIONS``.

    A sealed plan's list may be longer (its ``node_config`` digests are
    appended), so this cap applies where a caller's list enters, not on replay.
    """

    _validate_postconditions(postconditions, limit=MAX_DECLARED_POSTCONDITIONS)


def _validate_postconditions(
    postconditions: Sequence[Mapping[str, Any]],
    *,
    limit: int = MAX_SEALED_POSTCONDITIONS,
) -> None:
    """Validate the closed structural proof vocabulary before any save."""

    if isinstance(postconditions, (str, bytes)) or not isinstance(postconditions, Sequence):
        raise AssistantOperationError("invalid_plan", "Postconditions must be a list of objects")
    if len(postconditions) > limit:
        raise AssistantOperationError(
            "invalid_plan", f"A plan may carry at most {limit} postconditions"
        )
    allowed_keys = {
        "node_exists": {"kind", "node"},
        "node_absent": {"kind", "node"},
        "edge_exists": {
            "kind",
            "source",
            "target",
            "source_handle",
            "target_handle",
        },
        "edge_absent": {
            "kind",
            "source",
            "target",
            "source_handle",
            "target_handle",
        },
        "graph_shape": {"kind", "nodes", "edges"},
        "preamble_digest": {"kind", "sha256"},
        "node_config": {"kind", "node", "sha256"},
    }
    required_keys = {
        "node_exists": {"kind", "node"},
        "node_absent": {"kind", "node"},
        "edge_exists": {"kind", "source", "target"},
        "edge_absent": {"kind", "source", "target"},
        "graph_shape": {"kind", "nodes", "edges"},
        "preamble_digest": {"kind", "sha256"},
        "node_config": {"kind", "node", "sha256"},
    }
    for condition in postconditions:
        if not isinstance(condition, Mapping):
            raise AssistantOperationError("invalid_plan", "Postcondition must be an object")
        kind = condition.get("kind")
        if not isinstance(kind, str) or kind not in allowed_keys:
            raise AssistantOperationError(
                "invalid_plan", f"Unsupported postcondition kind: {kind!r}"
            )
        if set(condition) - allowed_keys[kind] or required_keys[kind] - set(condition):
            raise AssistantOperationError(
                "invalid_plan",
                f"Postcondition {kind!r} is not the closed supported shape",
            )
        if kind in {"node_exists", "node_absent", "node_config"}:
            if not isinstance(condition["node"], str) or not condition["node"]:
                raise AssistantOperationError(
                    "invalid_plan", f"Postcondition {kind!r} needs a node id"
                )
        if kind in {"edge_exists", "edge_absent"}:
            if any(
                not isinstance(condition[field], str) or not condition[field]
                for field in ("source", "target")
            ) or any(
                condition.get(field) is not None and not isinstance(condition.get(field), str)
                for field in ("source_handle", "target_handle")
            ):
                raise AssistantOperationError(
                    "invalid_plan", f"Postcondition {kind!r} has invalid edge identity"
                )
        elif kind == "graph_shape":
            if any(
                type(condition[field]) is not int or condition[field] < 0
                for field in ("nodes", "edges")
            ):
                raise AssistantOperationError(
                    "invalid_plan", "graph_shape values must be non-negative integers"
                )
        elif kind in {"preamble_digest", "node_config"}:
            digest = condition["sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AssistantOperationError(
                    "invalid_plan", f"{kind} must be lowercase SHA-256 hex"
                )


def _target_binds_df(target: ast.expr) -> bool:
    if isinstance(target, ast.Name):
        return target.id == "df"
    if isinstance(target, (ast.List, ast.Tuple)):
        return any(_target_binds_df(element) for element in target.elts)
    return False


def _is_df_name(value: ast.expr) -> bool:
    return isinstance(value, ast.Name) and value.id == "df"


def _is_frame_candidate(value: ast.expr) -> bool:
    return not _is_df_name(value) and not isinstance(value, ast.Constant)


class _PolarsResultVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.retained = False

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(_target_binds_df(target) for target in node.targets) and _is_frame_candidate(
            node.value
        ):
            self.retained = True

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            _target_binds_df(node.target)
            and node.value is not None
            and _is_frame_candidate(node.value)
        ):
            self.retained = True

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _target_binds_df(node.target):
            self.retained = True

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None and _is_frame_candidate(node.value):
            self.retained = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass


_BARE_INPUT_NAME = "df"
_NESTED_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _binds_name(scope: ast.AST, name: str) -> bool:
    """Report whether a nested scope binds *name* as its own local.

    A parameter or a comprehension target shadows the module binding outright.
    So does any assignment in a function body, which Python makes local for the
    whole function. Bindings inside a further nested scope belong to that scope
    and must not suppress a real read in the enclosing function.
    """

    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return any(
            _is_name(node, name, ast.Store)
            for generator in scope.generators
            for node in ast.walk(generator.target)
        )
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        isinstance(node, ast.Global) and name in node.names
        for statement in scope.body
        for node in _nodes_in_own_scope(statement)
    ):
        return True
    arguments = scope.args
    declared = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None),
    )
    if any(argument.arg == name for argument in declared):
        return True
    # A lambda's body is one expression, not a statement list, and can still
    # bind through a walrus (`lambda x: (df := x)`).
    body: list[ast.AST] = list(scope.body) if isinstance(scope.body, list) else [scope.body]
    return any(_binds_name_in_own_scope(item, name) for item in body)


def _binds_name_in_own_scope(node: ast.AST, name: str) -> bool:
    """Find a binding without descending into a child lexical scope."""

    if _is_name(node, name, ast.Store) or _is_name(node, name, ast.Del):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name or any(
            _binds_name_in_own_scope(expression, name)
            for expression in _scope_entry_expressions(node)
        )
    if isinstance(node, ast.Lambda):
        return any(
            _binds_name_in_own_scope(expression, name)
            for expression in _scope_entry_expressions(node)
        )
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return any(
            isinstance(candidate, ast.NamedExpr) and _is_name(candidate.target, name, ast.Store)
            for candidate in _nodes_in_comprehension(node)
        )
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(
            (alias.asname or alias.name.split(".", maxsplit=1)[0]) == name
            for alias in node.names
            if alias.name != "*"
        )
    if isinstance(node, ast.ExceptHandler) and node.name == name:
        return True
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
        return True
    if isinstance(node, ast.MatchMapping) and node.rest == name:
        return True
    return any(_binds_name_in_own_scope(child, name) for child in ast.iter_child_nodes(node))


def _scope_entry_expressions(node: ast.AST) -> tuple[ast.expr, ...]:
    """Expressions evaluated outside a function, lambda, or class body."""

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        annotations = tuple(
            annotation
            for annotation in (
                *(argument.annotation for argument in node.args.posonlyargs),
                *(argument.annotation for argument in node.args.args),
                *(argument.annotation for argument in node.args.kwonlyargs),
                node.args.vararg.annotation if node.args.vararg is not None else None,
                node.args.kwarg.annotation if node.args.kwarg is not None else None,
                node.returns,
            )
            if annotation is not None
        )
        return (
            *node.decorator_list,
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
            *annotations,
        )
    if isinstance(node, ast.Lambda):
        return (
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
        )
    if isinstance(node, ast.ClassDef):
        return (*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords))
    return ()


def _nodes_in_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes without entering a nested lexical scope's body."""

    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        for expression in _scope_entry_expressions(node):
            yield from _nodes_in_own_scope(expression)
        return
    for child in ast.iter_child_nodes(node):
        yield from _nodes_in_own_scope(child)


def _nodes_in_comprehension(node: ast.AST) -> Iterator[ast.AST]:
    """Yield comprehension nodes while excluding child function/lambda scopes."""

    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return
    for child in ast.iter_child_nodes(node):
        yield from _nodes_in_comprehension(child)


def _is_name(node: ast.AST, name: str, context: type[ast.expr_context]) -> bool:
    return isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, context)


def _names_in_owning_scope(node: ast.AST, name: str) -> Iterator[ast.AST]:
    """Yield nodes reachable without entering a scope that shadows *name*.

    A nested `def`, `lambda`, or comprehension that binds the name introduces
    its own variable: `def widen(df)` names its parameter, not the node's
    injected input, so reading it there says nothing about wiring order. A
    nested scope that does *not* bind the name still reads the module's, so
    the walk descends into that one.
    """

    if isinstance(node, _NESTED_SCOPES) and _binds_name(node, name):
        # A comprehension target is not bound until after its first iterable
        # has been evaluated in the enclosing scope.
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            yield node
            yield from _names_in_owning_scope(node.generators[0].iter, name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
            for expression in _scope_entry_expressions(node):
                yield from _names_in_owning_scope(expression, name)
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _names_in_owning_scope(child, name)


def _can_reach_output_with_df_unbound(tree: ast.Module) -> bool:
    """Report whether the code can reach its output with `df` unbound.

    `df` is the node's output variable and is never bound to an input — a
    polars node's inputs are its named parameters. A read before the code's
    own assignment is therefore a guaranteed `NameError` at execution time,
    in the generated module and canvas execution alike.

    Only reads that resolve to the module-level `df` count, so the walk skips
    any nested scope holding a `df` of its own. A statement's loads are
    considered before its stores because an assignment evaluates its value
    first: `df = df.head()` reads the unbound name, `df = left` does not. The
    generated trailing output read is implicit, so a conditional/loop-only
    assignment that may fall through without binding also returns ``True``.
    """

    def reads_unbound(node: ast.AST, bound: bool) -> bool:
        return not bound and any(
            _is_name(candidate, _BARE_INPUT_NAME, ast.Load)
            for candidate in _names_in_owning_scope(node, _BARE_INPUT_NAME)
        )

    def binds_df(node: ast.AST) -> bool:
        return any(
            _is_name(candidate, _BARE_INPUT_NAME, ast.Store)
            for candidate in _names_in_owning_scope(node, _BARE_INPUT_NAME)
        )

    def definitely_binds_df(statement: ast.stmt) -> bool:
        """Recognise straight-line statements whose assignment always runs."""

        if isinstance(statement, ast.Assign):
            return any(binds_df(target) for target in statement.targets)
        if isinstance(statement, ast.AnnAssign):
            return statement.value is not None and binds_df(statement.target)
        if isinstance(statement, ast.AugAssign):
            return binds_df(statement.target)
        return False

    def deletes_df(statement: ast.Delete) -> bool:
        return any(
            _is_name(candidate, _BARE_INPUT_NAME, ast.Del)
            for target in statement.targets
            for candidate in ast.walk(target)
        )

    def examine_block(statements: Sequence[ast.stmt], bound: bool) -> tuple[bool, bool, bool]:
        """Return (has_unbound_read, falls_through, definitely_bound)."""

        for statement in statements:
            has_unbound_read, falls_through, bound = examine_statement(statement, bound)
            if has_unbound_read or not falls_through:
                return has_unbound_read, falls_through, bound
        return False, True, bound

    def examine_statement(statement: ast.stmt, bound: bool) -> tuple[bool, bool, bool]:
        if isinstance(statement, ast.If):
            if reads_unbound(statement.test, bound):
                return True, True, bound
            branches = (
                examine_block(statement.body, bound),
                examine_block(statement.orelse, bound),
            )
            if any(has_unbound_read for has_unbound_read, _, _ in branches):
                return True, True, bound
            fallthrough_bindings = [
                branch_bound for _, falls_through, branch_bound in branches if falls_through
            ]
            if not fallthrough_bindings:
                return False, False, bound
            return False, True, all(fallthrough_bindings)

        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                entry = statement.iter
            else:
                entry = statement.test
            if reads_unbound(entry, bound):
                return True, True, bound
            body_bound = bound or (
                binds_df(statement.target)
                if isinstance(statement, (ast.For, ast.AsyncFor))
                else False
            )
            body_result = examine_block(statement.body, body_bound)
            else_result = examine_block(statement.orelse, bound)
            if body_result[0] or else_result[0]:
                return True, True, bound
            # A loop can execute zero times, so body bindings never dominate a
            # later read.  Keep other loop flow conservative too.
            return False, True, bound

        # These constructs require exception/suppression/pattern exhaustiveness
        # modelling to prove a binding. Reject a read found anywhere in them,
        # and never let a store hidden inside establish the later output.
        if isinstance(statement, (ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match)):
            if reads_unbound(statement, bound):
                return True, True, bound
            return False, True, bound

        if isinstance(statement, ast.AugAssign) and binds_df(statement.target) and not bound:
            # The AST marks an augmented-assignment target as Store, but Python
            # reads its previous value before applying the operator.
            return True, True, bound

        if isinstance(statement, ast.Delete) and deletes_df(statement):
            if not bound:
                return True, True, bound
            return False, True, False

        if reads_unbound(statement, bound):
            return True, True, bound
        falls_through = not isinstance(
            statement,
            (ast.Break, ast.Continue, ast.Raise, ast.Return),
        )
        return False, falls_through, bound or definitely_binds_df(statement)

    has_unbound_read, falls_through, definitely_bound = examine_block(tree.body, False)
    return has_unbound_read or (falls_through and not definitely_bound)


def _validate_polars_named_inputs(code: object, node_id: str, input_names: Sequence[str]) -> None:
    """Require assistant-authored code to bind `df` before reading it."""

    if not isinstance(code, str) or not code.strip():
        return
    where = {"node": node_id, "field": "code"}
    if _BARE_INPUT_NAME in input_names:
        _invalid(
            f"Node {node_id!r} receives an input named 'df', which conflicts with the "
            "reserved output name for Polars code. Rename the upstream node or frame.",
            where=where,
            fix="Rename the upstream node so its input is not named 'df'.",
        )
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # _validate_polars_result_retained owns the syntax verdict
    if not _can_reach_output_with_df_unbound(tree):
        return
    if input_names:
        _invalid(
            f"Node {node_id!r} reads 'df' before assigning it, but 'df' is not bound to "
            "any input — it is the node's output variable. Start from the input you "
            "mean by name (" + ", ".join(sorted(input_names)) + ") and assign the "
            "result to 'df'.",
            where=where,
            fix=f"Bind an input by name before reading df, for example df = {input_names[0]}.",
        )
    _invalid(
        f"Node {node_id!r} reads 'df' before assigning it, but this node has no inputs "
        f"and 'df' is unbound until the code assigns it. Construct a frame and assign "
        f"it to 'df'.",
        where=where,
        fix="Construct a frame and assign it to df before reading df.",
    )


def _validate_polars_result_retained(code: object, node_id: str) -> None:
    where = {"node": node_id, "field": "code"}
    if not isinstance(code, str):
        _invalid(
            "Explicit Polars code must be a string",
            where=where,
            fix="Send the code as one string.",
        )
    if not code.strip():
        return
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise OpValidationError(
            "Explicit Polars code contains invalid Python",
            where=where,
            fix=f"Correct the Python syntax at line {exc.lineno} of the code.",
        ) from exc

    visitor = _PolarsResultVisitor()
    visitor.visit(tree)
    if not visitor.retained:
        _invalid(
            "Explicit Polars code must assign a transformed frame to 'df' or return "
            "a transformed frame; bare Polars expressions are immutable and their "
            "result would be discarded",
            where=where,
            fix="Assign the transformed frame to df (df = df.filter(...)).",
        )


def _incoming_input_names(
    result: PipelineGraph,
    node_id: str,
    nodes_by_id: Mapping[str, GraphNode],
) -> tuple[str, ...]:
    """Return the input names this node's own code binds, in edge order."""

    names: list[str] = []
    for edge in result.edges:
        if edge.target != node_id:
            continue
        source_node = nodes_by_id.get(edge.source)
        if source_node is None:
            continue
        try:
            names.append(
                edge_input_name(
                    edge,
                    source_node,
                    submodels=result.submodels,
                )
            )
        except ValueError:
            # A malformed edge is the save validator's verdict, not this one's.
            continue
    return tuple(names)


def _frame_method_root(expression: ast.expr) -> str | None:
    """The name a method call chain starts from (``df`` in ``df.a().b()``), if any."""

    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Attribute):
        return None
    receiver: ast.expr = expression.func.value
    while isinstance(receiver, (ast.Call, ast.Attribute, ast.Subscript)):
        receiver = receiver.func if isinstance(receiver, ast.Call) else receiver.value
    return receiver.id if isinstance(receiver, ast.Name) else None


def _input_indexing_df(tree: ast.AST, input_names: Sequence[str]) -> str | None:
    """The input name a step indexes ``df`` by (``df['claims']``), if any.

    Only a string key equal to an incoming input's name counts, so indexing a
    frame by one of its own columns is never mistaken for it.
    """

    names = set(input_names)
    for item in ast.walk(tree):
        if (
            isinstance(item, ast.Subscript)
            and isinstance(item.value, ast.Name)
            and item.value.id == _BARE_INPUT_NAME
            and isinstance(item.slice, ast.Constant)
            and isinstance(item.slice.value, str)
            and item.slice.value in names
        ):
            return item.slice.value
    return None


def _df_indexing_fix(
    node_type: NodeType, steps: Sequence[Mapping[str, Any]], input_names: Sequence[str]
) -> str:
    """How a surface's code reaches the frame ``df`` holds and each other input."""

    surface = stepped_surface_for(node_type)
    if surface.inputs == "none":
        return "df is this node's own frame; transform df directly (df = df.filter(...))."
    if surface.start == "input":
        first = steps[0].get("input")
        bound = f"df is already the {first!r} frame the source step chose"
    else:
        first = input_names[0]
        bound = f"df is already the first input, {first!r}"
    others = [name for name in input_names if name != first]
    if not others:
        return f"{bound}; transform df directly (df = df.filter(...))."
    return (
        f"{bound}; read "
        + ", ".join(repr(name) for name in others)
        + f" by edge name (for example pl.concat([df, {others[0]}]) or "
        f"df.join({others[0]}, ...))."
    )


def _plain_edge_input_name(node: GraphNode) -> str | None:
    """The input name an edge from *node* gives without a source handle, if it can."""

    try:
        return executable_input_name(
            node_type=node.data.nodeType, label=node.data.label, source_handle=None
        )
    except ValueError:
        return None  # a Quote Input's or a submodel's edge selects a frame or port


def _unwired_step_input(
    graph: PipelineGraph,
    node: GraphNode,
    steps: Sequence[object],
    step_index: int | None,
    input_names: Sequence[str],
) -> str | None:
    """The node a failing source, join or concat step reads that no edge connects.

    Only an input name that is the id of a node in *graph* counts, and only
    when a plain edge from that node gives that name, so the fix can name the
    ``add_edge`` that connects it.
    """

    if step_index is None:
        return None
    step = steps[step_index]
    if not isinstance(step, Mapping):
        return None
    names = step.get("inputs") if step.get("kind") == "concat" else [step.get("input")]
    nodes = {
        candidate.id
        for candidate in graph.nodes
        if candidate.id != node.id and _plain_edge_input_name(candidate) == candidate.id
    }
    return next(
        (
            name
            for name in names or ()
            if isinstance(name, str) and name not in input_names and name in nodes
        ),
        None,
    )


def _unread_source_input(
    node_type: NodeType,
    steps: Sequence[object],
    step_index: int | None,
    input_names: Sequence[str],
) -> str | None:
    """The input a failing source step names that no incoming edge gives, if any."""

    if stepped_surface_for(node_type).start != "input" or step_index != 0:
        return None
    step = steps[0]
    if not isinstance(step, Mapping) or step.get("kind") != "source":
        return None
    name = step.get("input")
    return name if isinstance(name, str) and name not in input_names else None


def _input_name_fix(
    node_id: str,
    written: str,
    input_names: Sequence[str],
    *,
    reader: str,
    field: str,
    purpose: str,
) -> tuple[str, list[str]]:
    """The fix for *field* of *node_id* naming *written*, an input no incoming edge gives.

    *reader* is what reads the input (``its source step``, ``row 1``) and
    *purpose* what its frame is for (``df starts from``). With no incoming
    edge there is nothing to name, so the fix adds an edge; otherwise it lists
    the names the edges give and suggests one: the only one, or the one
    closest to *written*. Either way it states the naming rule. Returns the
    fix and its suggestion.
    """

    if not input_names:
        return (
            f"{node_id!r} has no incoming edge, so {reader} reads nothing. Add "
            f'{{"op": "add_edge", "source": "<upstream node>", "target": "{node_id}"}} to '
            'this plan, with "source_handle": "<frame>" when the source is a Quote Input, '
            f"and set {field} to the name that edge gives. {INPUT_NAMING_RULE}",
            [],
        )
    names = list(dict.fromkeys(input_names))
    provided = (
        f"The edges into {node_id!r} provide the input{'s' if len(names) > 1 else ''} "
        + ", ".join(repr(item) for item in names)
        + f"; set {field} to "
    )
    if len(names) == 1:
        return f"{provided}{names[0]!r}. {INPUT_NAMING_RULE}", names
    close = difflib.get_close_matches(written, names, n=1, cutoff=0.6)
    choice = f"{close[0]!r} if that is the frame {purpose}" if close else f"the one {purpose}"
    return f"{provided}{choice}. {INPUT_NAMING_RULE}", close


def _validate_assistant_authored_steps(
    result: PipelineGraph,
    node: GraphNode,
    nodes_by_id: Mapping[str, GraphNode],
) -> None:
    """Check an assistant-authored step list against its surface's contract.

    The product's renderer is the syntax and step-schema verdict. Each
    free-code step is then refused when a top-level frame method call discards
    its result, when it indexes ``df`` by an incoming input's name as if ``df``
    held every input, and, on a surface whose code sees only ``df``, when it
    reads an incoming input or an upstream node by name that nothing in scope
    binds.
    """

    node_type = node.data.nodeType
    surface = stepped_surface_for(node_type)
    input_names = _incoming_input_names(result, node.id, nodes_by_id)
    steps = node.data.config["steps"]
    form = _free_code_form(node_type)
    label = STEPPED_SURFACE_LABELS[node_type]
    try:
        rendered = render_polars_steps(
            steps, step_input_names(node_type, input_names), start=surface.start
        )
    except PolarsStepError as exc:
        step = _step_id_at(steps, exc.step_index)
        unwired = _unwired_step_input(result, node, steps, exc.step_index, input_names)
        unread = _unread_source_input(node_type, steps, exc.step_index, input_names)
        detail, fix = _unrendered_steps(node_type, steps, exc)
        suggested: list[str] = []
        if unwired is not None:
            fix = (
                f"Connect {unwired!r} to {node.id!r} in the same plan: "
                f'{{"op": "add_edge", "source": "{unwired}", "target": "{node.id}"}}.'
            )
        elif unread is not None:
            fix, suggested = _input_name_fix(
                node.id,
                unread,
                input_names,
                reader="its source step",
                field="the source step's input",
                purpose="df starts from",
            )
        _invalid(
            f"Node {node.id!r} has an invalid step list: {detail}",
            where={"node": node.id, "field": "steps", **({} if step is None else {"step": step})},
            fix=fix,
            did_you_mean=suggested,
        )
    lines = rendered.code.split("\n")
    frame_names = {_BARE_INPUT_NAME, *input_names}
    out_of_scope = (
        set()
        if surface.inputs == "edges"
        else {*input_names, *upstream_node_ids(node.id, result.parents_of)}
    )
    for index, (step, (first, last)) in enumerate(zip(steps, rendered.step_lines, strict=True)):
        if step["kind"] != "free_code":
            continue
        site = f"Node {node.id!r} step {index + 1} ({step['id']!r})"
        where = {"node": node.id, "field": "steps", "step": step["id"]}
        tree = ast.parse("\n".join(lines[first - 1 : last]))
        for statement in tree.body:
            if not isinstance(statement, ast.Expr):
                continue
            if _frame_method_root(statement.value) in frame_names:
                _invalid(
                    f"{site} calls a frame method whose result is discarded; Polars frames "
                    "are immutable, so assign the result to df (df = df.filter(...)).",
                    where=where,
                    fix="Assign the call's result to df (df = df.filter(...)).",
                )
        indexed = _input_indexing_df(tree, input_names)
        if indexed is not None:
            _invalid(
                f"{site} indexes df by the input name {indexed!r}, but df is one frame, "
                f"not a mapping of the node's inputs. New logic on a {label} is written "
                f"as {form}.",
                where=where,
                fix=_df_indexing_fix(node_type, steps, input_names),
            )
        reads = {
            name.id
            for name in ast.walk(tree)
            if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
        }
        suspect = sorted(reads & out_of_scope)
        if not suspect:
            continue
        try:
            preamble = ast.parse(result.preamble or "")
        except SyntaxError as exc:
            raise OpValidationError(
                "The pipeline preamble is not valid Python",
                fix=f"Correct the preamble's Python syntax at line {exc.lineno}.",
            ) from exc
        in_scope = {
            _BARE_INPUT_NAME,
            "pl",
            *dir(builtins),
            *_bound_names(preamble),
            *_bound_names(ast.parse("\n".join(lines[:last]))),
        }
        unbound = [name for name in suspect if name not in in_scope]
        if unbound:
            _invalid(
                f"{site}: {label} code sees only df; "
                f"{unbound[0]} is not in scope. Transform df, the node's own input.",
                where=where,
                fix=f"Replace {unbound[0]} with df, the node's own input.",
            )


def _validate_output_rows_read_inputs(
    result: PipelineGraph, node: GraphNode, nodes_by_id: Mapping[str, GraphNode]
) -> None:
    """Refuse an assistant-written output row whose source_port names no incoming edge.

    The engine lets a one-input output's rows name any frame, so a wrong name
    saves silently; the assistant's rows must name the frame they read. A row
    naming an unconnected node a plain edge would name the same is told that
    edge; any other row, such as one naming a Quote Input or a submodel node,
    is told the names the incoming edges give and the naming rule.
    """

    rows = node.data.config.get("outputMapping")
    names = _incoming_input_names(result, node.id, nodes_by_id)
    for position, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, Mapping) or "enabled" not in row:
            continue  # the mapping's own validator refuses a malformed row
        port = row.get("source_port")
        if not is_active_mapping_entry(dict(row)) or port in names:
            continue
        connected = ", ".join(repr(name) for name in names) or "none"
        source = nodes_by_id.get(port) if isinstance(port, str) and port != node.id else None
        suggested: list[str] = []
        if source is not None and _plain_edge_input_name(source) == port:
            fix = (
                f"Connect {port!r} to {node.id!r} in the same plan: "
                f'{{"op": "add_edge", "source": "{port}", "target": "{node.id}"}}.'
            )
        else:
            fix, suggested = _input_name_fix(
                node.id,
                port if isinstance(port, str) else "",
                names,
                reader=f"row {position}",
                field=f"row {position}'s source_port",
                purpose="the column comes from",
            )
        _invalid(
            f"Row {position} of the outputMapping of node {node.id!r} reads source_port {port!r}, "
            f"which no incoming edge provides; its incoming edges are {connected}.",
            where={"node": node.id, "field": "outputMapping"},
            fix=fix,
            did_you_mean=suggested,
        )


def _validate_assistant_authored_graph(
    result: PipelineGraph,
    diff: SemanticDiff,
    authored_added: Sequence[str],
) -> None:
    """Enforce assistant-only authoring invariants on the final planned graph."""

    nodes_by_id = {node.id: node for node in result.nodes}
    config_changes = diff.complete.config_changes
    code_changed = {
        change.removesuffix(":code") for change in config_changes if change.endswith(":code")
    }
    # `<node>:steps` from update_node, `<node>:steps[<id>]` from edit_steps. A
    # rename's `<node>:steps[<id>].input` rewrites an input reference only, so
    # the consumer's steps are not the assistant's to judge.
    steps_changed = {
        node_id
        for node_id, _, key in (change.partition(":") for change in config_changes)
        if key == "steps" or (key.startswith("steps[") and key.endswith("]"))
    }
    for node_id in sorted(set(authored_added) | steps_changed):
        node = nodes_by_id.get(node_id)
        if (
            node is not None
            and is_stepped_config(node.data.nodeType, node.data.config)
            and not node.data.config.get("instanceOf")
        ):
            _validate_assistant_authored_steps(result, node, nodes_by_id)
    for node_id in set(authored_added) | code_changed:
        node = nodes_by_id.get(node_id)
        if (
            node is not None
            and node.data.nodeType == NodeType.POLARS
            and "code" in node.data.config
        ):
            _validate_polars_result_retained(node.data.config["code"], node_id)
            _validate_polars_named_inputs(
                node.data.config["code"],
                node_id,
                _incoming_input_names(result, node_id, nodes_by_id),
            )

    mapping_changed = {
        change.removesuffix(":outputMapping")
        for change in config_changes
        if change.endswith(":outputMapping")
    }
    for node_id in sorted(set(authored_added) | mapping_changed):
        node = nodes_by_id.get(node_id)
        if node is not None and node.data.nodeType == NodeType.OUTPUT:
            _validate_output_rows_read_inputs(result, node, nodes_by_id)

    incident_nodes = {node_id for edge in result.edges for node_id in (edge.source, edge.target)}
    disconnected = sorted(set(authored_added) - incident_nodes)
    if disconnected:
        raise AssistantOperationError(
            "invalid_plan",
            "New assistant-authored node(s) are disconnected: "
            + ", ".join(disconnected)
            + ". Connect every new node in the same edit plan.",
            where={"node": disconnected[0]},
            fix=f"Add an add_edge operation that connects {disconnected[0]!r} in the same plan.",
        )


def prepare_graph_edit(
    snapshot: ProjectSnapshot,
    raw_ops: Sequence[Mapping[str, Any]],
    postconditions: Sequence[Mapping[str, Any]] = (),
    *,
    positions: Sequence[int] | None = None,
    config_visibility: ConfigVisibility | None = None,
) -> PreparedGraphEdit:
    """Parse, apply, and validate an edit once against one exact snapshot.

    *positions* holds each operation's index in the batch the model sent,
    which every located failure reports. *config_visibility* refuses a blind
    rewrite of saved configuration the model has not seen (see
    ``_apply_ops_with_refs``).
    """

    ops = parse_ops(raw_ops, positions)
    applied = _apply_ops_with_refs(
        snapshot.graph, ops, positions, config_visibility=config_visibility
    )
    result, refs = applied.graph, applied.refs
    diff = _semantic_diff(snapshot.graph, result, ops, applied)
    try:
        _validate_assistant_authored_graph(result, diff, applied.new_node_ids)
    except LocatedPlanError as exc:
        locate_plan_error(exc, applied.writers, result)
        raise
    normalized = tuple(_frozen_json(op.model_dump(mode="json")) for op in ops)
    _validate_postconditions(postconditions)
    resolved_conditions = tuple(
        _resolve_postcondition_refs(condition, refs) for condition in postconditions
    )
    _validate_postconditions(resolved_conditions)
    supplied_conditions = tuple(_frozen_json(condition) for condition in resolved_conditions)
    base_conditions = supplied_conditions or _automatic_postconditions(result, diff)
    # A replayed plan's supplied list already holds its config digests.
    all_conditions = (
        *base_conditions,
        *(
            condition
            for condition in _config_postconditions(result, diff)
            if condition not in base_conditions
        ),
    )
    _validate_postconditions(cast(Sequence[Mapping[str, Any]], all_conditions))
    verify_postconditions(result, all_conditions)
    capability_ids = _affected_capabilities(snapshot.graph, result, diff, ops, refs)
    return PreparedGraphEdit(
        snapshot=snapshot,
        result_graph=result,
        normalized_operations=normalized,
        diff=diff,
        affected_capabilities=capability_ids,
        postconditions=all_conditions,
        writers=MappingProxyType(dict(applied.writers)),
    )


def finalize_graph_edit_plan(
    prepared: PreparedGraphEdit,
    *,
    validation_warnings: Sequence[str] = (),
    verification_tier: Literal["structural", "schema"] = "structural",
    verification_evidence: Sequence[Mapping[str, object]] = (),
    egress: PlanEgress,
) -> GraphEditPlan:
    """Seal one prepared edit with application-layer verification facts."""

    snapshot = prepared.snapshot
    frozen_evidence_values = tuple(_frozen_json(item) for item in verification_evidence)
    if not all(isinstance(item, Mapping) for item in frozen_evidence_values):
        raise AssistantOperationError(
            "invalid_plan",
            "Verification evidence entries must be objects",
        )
    frozen_evidence = cast(
        tuple[Mapping[str, object], ...],
        frozen_evidence_values,
    )
    if verification_tier == "schema" and not frozen_evidence:
        raise AssistantOperationError("invalid_plan", "Schema verification requires evidence")
    if verification_tier == "structural" and frozen_evidence:
        raise AssistantOperationError(
            "invalid_plan", "Structural plans cannot contain schema evidence"
        )
    if verification_tier == "schema" and egress == "none":
        raise AssistantOperationError(
            "invalid_plan", "Schema evidence comes from running node code over project data"
        )
    authority = {
        "base_revision": snapshot.revision,
        "capability_hash": snapshot.capability_hash,
        "revision_sources": dict(snapshot.source_manifest),
        "normalized_operations": _wire_json(prepared.normalized_operations),
        "semantic_diff_hash": prepared.diff.complete_hash,
        "postconditions": _wire_json(prepared.postconditions),
        "validation_warnings": list(validation_warnings),
        "resulting_graph_shape": {
            "nodes": len(prepared.result_graph.nodes),
            "edges": len(prepared.result_graph.edges),
        },
        "egress": egress,
        "verification_tier": verification_tier,
        "verification_evidence": _wire_json(frozen_evidence),
        "affected_capabilities": prepared.affected_capabilities,
    }
    return GraphEditPlan(
        base_revision=snapshot.revision,
        capability_hash=snapshot.capability_hash,
        source_manifest=snapshot.source_manifest,
        normalized_operations=prepared.normalized_operations,
        diff=prepared.diff,
        affected_capabilities=prepared.affected_capabilities,
        postconditions=prepared.postconditions,
        validation_warnings=tuple(validation_warnings),
        resulting_graph_shape=MappingProxyType(
            {
                "nodes": len(prepared.result_graph.nodes),
                "edges": len(prepared.result_graph.edges),
            }
        ),
        egress=egress,
        verification_tier=verification_tier,
        verification_evidence=frozen_evidence,
        plan_hash=_digest(authority),
    )


def build_graph_edit_plan(
    snapshot: ProjectSnapshot,
    raw_ops: Sequence[Mapping[str, Any]],
    postconditions: Sequence[Mapping[str, Any]] = (),
    validation_warnings: Sequence[str] = (),
    verification_tier: Literal["structural", "schema"] = "structural",
    verification_evidence: Sequence[Mapping[str, object]] = (),
) -> GraphEditPlan:
    """Build a sealed plan, retaining the established public API.

    Nothing here resolves a schema, so no node code runs over project data.
    """

    return finalize_graph_edit_plan(
        prepare_graph_edit(snapshot, raw_ops, postconditions),
        validation_warnings=validation_warnings,
        verification_tier=verification_tier,
        verification_evidence=verification_evidence,
        egress="none",
    )


# The control characters a receipt may hold: ordinary whitespace.
_RECEIPT_WHITESPACE = frozenset("\t\n\r")


@dataclass(frozen=True, slots=True)
class PlanReceipt:
    """What the model says a plan does: its summary and the assumptions it made.

    The receipt is stored beside the plan, outside the hashed plan authority,
    so its wording never changes a plan hash. It is the model's own text and
    the change card shows it as written.
    """

    summary: str
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        texts = (self.summary, *self.assumptions)
        if any(not text.strip() or len(text) > ASSISTANT_RECEIPT_TEXT_LIMIT for text in texts):
            raise AssistantOperationError(
                "invalid_request",
                "A plan summary and each assumption must be non-empty and at most "
                f"{ASSISTANT_RECEIPT_TEXT_LIMIT} characters.",
                fix=(
                    "Say in one or two plain sentences, at most "
                    f"{ASSISTANT_RECEIPT_TEXT_LIMIT} characters, what the plan does."
                ),
            )
        if any(
            unicodedata.category(char) == "Cc" and char not in _RECEIPT_WHITESPACE
            for text in texts
            for char in text
        ):
            # The summary is the save's Git commit message, and the ledger
            # history parser delimits commits with control characters.
            raise AssistantOperationError(
                "invalid_request",
                "A plan summary and its assumptions must not contain control characters.",
                fix="Write the summary and assumptions as plain sentences.",
            )
        if len(self.assumptions) > ASSISTANT_MAX_ASSUMPTIONS:
            raise AssistantOperationError(
                "invalid_request",
                f"A plan records at most {ASSISTANT_MAX_ASSUMPTIONS} assumptions.",
                fix="Keep only the assumptions that change the result.",
            )


@dataclass(slots=True)
class _StoredPlan:
    plan: GraphEditPlan
    receipt: PlanReceipt
    expires_at: float
    state: Literal["validated", "applying", "applied", "aborted"] = "validated"
    result: object | None = None
    data_check: DataCheckResult | None = None


class PlanStore:
    """Thread-safe, bounded single-use plan authority ledger.

    Plans awaiting use live in a bounded :class:`LRUCache`. An ``applying``
    record is a pinned lease: neither capacity pressure nor its TTL removes it
    until ``complete_apply`` or ``abort_apply`` records its terminal result.
    Beside each plan it keeps the whole data check of the plan's latest
    dry-run, outside the plan's authority: the check never changes the plan
    or its hash, and a fresh dry-run of the same plan clears it.
    """

    def __init__(self, *, max_size: int = 100, ttl_seconds: float = 600.0) -> None:
        if max_size < 1 or ttl_seconds <= 0:
            raise ValueError("max_size and ttl_seconds must be positive")
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        # Expiry stays per record: an applying lease must outlive its TTL, which
        # the cache's own TTL would not allow for a pinned entry.
        self._records: LRUCache[str, _StoredPlan] = LRUCache(max_size=max_size)
        self._lock = RLock()

    def _record(self, plan_hash: str) -> _StoredPlan:
        record = self._records.get(plan_hash)
        if record is None:
            raise AssistantOperationError("plan_not_found")
        if record.state != "applying" and monotonic() >= record.expires_at:
            self._records.pop(plan_hash)
            raise AssistantOperationError("plan_expired")
        return record

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def put(self, plan: GraphEditPlan, receipt: PlanReceipt) -> None:
        with self._lock:
            # A hit is promoted, which is all a still-valid identical plan
            # needs; a plan not yet applied takes the latest receipt.
            existing = self._records.get(plan.plan_hash)
            if existing is not None:
                if existing.state in {"aborted", "applied"}:
                    # A fresh dry-run that produces an applied plan's hash proves
                    # its base revision is current again (an undo restored it):
                    # the earlier apply's record must not outlive that revision.
                    self._records.pop(plan.plan_hash)
                elif existing.state == "applying" or monotonic() < existing.expires_at:
                    if existing.state == "validated":
                        existing.receipt = receipt
                        # This dry-run's own check, or none when its policy
                        # attempts none, replaces the earlier one.
                        existing.data_check = None
                    return
                else:
                    self._records.pop(plan.plan_hash)

            leased = sum(
                1
                for stored_hash in self._records
                if (record := self._records.peek(stored_hash)) is not None
                and record.state == "applying"
            )
            if leased >= self._max_size:
                raise AssistantOperationError(
                    "plan_store_busy",
                    "Every plan-store slot is reserved by an in-flight apply; "
                    "retry the dry-run after those saves settle.",
                )
            self._records.put(
                plan.plan_hash, _StoredPlan(plan, receipt, monotonic() + self._ttl_seconds)
            )

    def get(self, plan_hash: str) -> GraphEditPlan:
        with self._lock:
            return self._record(plan_hash).plan

    def receipt(self, plan_hash: str) -> PlanReceipt:
        with self._lock:
            return self._record(plan_hash).receipt

    def record_data_check(self, plan_hash: str, check: DataCheckResult) -> bool:
        """Keep *check* beside its validated plan; ``False`` when it cannot stay there.

        The check runs after its dry-run stored the plan and returned the
        save lock. A plan that has left the store meanwhile (expired or
        evicted) is answered ``plan_not_found`` by its apply, and one another
        session has begun applying keeps the check its apply read, so neither
        takes this check.
        """

        with self._lock:
            record = self._records.get(plan_hash)
            if record is None or record.state != "validated":
                return False
            record.data_check = check
            return True

    def data_check(self, plan_hash: str) -> DataCheckResult | None:
        """The stored check of the plan's latest dry-run, or ``None`` when none ran."""

        with self._lock:
            return self._record(plan_hash).data_check

    def applied_changes(self, plan_hash: str) -> SemanticChanges | None:
        """The complete identity changes of a plan this store saw applied.

        ``None`` while the plan is not applied or once it has left the store.
        A read has no side effect: unlike ``get`` it neither promotes nor
        expires the record, because what an apply saved stays true after the
        plan's authority to apply has lapsed.
        """

        with self._lock:
            record = self._records.peek(plan_hash)
            if record is None or record.state != "applied":
                return None
            return record.plan.diff.complete

    def begin_apply(self, plan_hash: str) -> GraphEditPlan:
        with self._lock:
            record = self._record(plan_hash)
            if record.state == "aborted":
                raise AssistantOperationError(
                    "plan_aborted",
                    "This plan's previous apply attempt was aborted; dry-run the "
                    "operations again before retrying.",
                )
            if record.state in {"applying", "applied"}:
                raise AssistantOperationError("plan_already_applied")
            record.state = "applying"
            self._records.pin(plan_hash)
            return record.plan

    def complete_apply(self, plan_hash: str, result: object) -> None:
        with self._lock:
            record = self._record(plan_hash)
            if record.state != "applying":
                raise AssistantOperationError("plan_already_applied")
            record.state = "applied"
            record.result = _frozen_json(result)
            self._records.unpin(plan_hash)

    def abort_apply(self, plan_hash: str) -> None:
        """Invalidate a reserved plan after a pre-save failure.

        A correction is always a fresh dry-run. Keeping the record prevents a
        racing or retried apply from reusing authority whose checks did not
        complete.
        """

        with self._lock:
            record = self._record(plan_hash)
            if record.state == "applying":
                record.state = "aborted"
                record.result = _frozen_json({"error": "plan_aborted"})
                self._records.unpin(plan_hash)


__all__ = [
    "AddEdgeOp",
    "AddNodeOp",
    "AssistantOperationError",
    "ConfigVisibility",
    "DeleteEdgeOp",
    "DeleteNodeOp",
    "GraphEditOp",
    "GraphEditPlan",
    "AppliedOps",
    "LocatedPlanError",
    "OpValidationError",
    "PlanReceipt",
    "PlanStore",
    "PreparedGraphEdit",
    "ProjectSourceEvidence",
    "ProjectSnapshot",
    "RenameConsumersError",
    "SourceEvidenceLedger",
    "RenameNodeOp",
    "UpdateNodeOp",
    "UpdatePreambleOp",
    "apply_ops",
    "build_graph_edit_plan",
    "build_project_snapshot",
    "dataset_schema_digest",
    "parse_ops",
    "locate_plan_error",
    "prepare_graph_edit",
    "finalize_graph_edit_plan",
    "SemanticDiff",
    "semantic_diff",
    "SemanticChanges",
    "validate_declared_postconditions",
    "verify_postconditions",
]
