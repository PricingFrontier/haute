"""Pure, shared identities exposed by the pipeline editor API."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from haute._config_io import config_path_for_node, has_config_folder
from haute._graph_utils import _sanitize_func_name, executable_input_name
from haute._types import NodeType


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
    source_handle_labels: Mapping[str, str] | None = None,
    config_reference_override: str | None = None,
) -> ResolvedEditorIdentity:
    """Resolve one node's editor identities without filesystem access."""
    kind = NodeType(node_type)
    handles = list(source_handles)
    handle_labels = dict(source_handle_labels or {})
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
    labelled_handles = {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
    if kind in labelled_handles:
        if set(handle_labels) != set(handles):
            raise ValueError("Public source-handle labels must exactly cover submodel handles.")
    elif handle_labels:
        raise ValueError("Public source-handle labels are only valid for submodel nodes.")
    mapping = {
        handle: executable_input_name(
            node_type=kind,
            label=label,
            source_handle=handle,
            source_handle_label=handle_labels.get(handle),
        )
        for handle in handles
    }
    return ResolvedEditorIdentity(
        function_name=function_name,
        config_reference=config_reference,
        default_input_name=default_input_name,
        source_handle_input_names=mapping,
    )
