"""Pure, shared identities exposed by the pipeline editor API."""

from __future__ import annotations

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


def api_input_source_handles(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every runtime-emitting API-input handle from a valid V2 config."""
    if "tables" not in config:
        return ()
    # Imports stay local so the cheap identity endpoint does not import the
    # JSON shred runtime; canonical document construction already has it loaded.
    from haute._api_input_schema import validate_v2_schema
    from haute._json_shred._shred import table_is_emitting

    candidate = dict(config)
    validate_v2_schema(candidate)
    tables = candidate["tables"]
    assert isinstance(tables, list)  # guaranteed by validate_v2_schema
    return tuple(table["label"] for table in tables if table_is_emitting(table))


def resolve_editor_identity(
    *,
    node_type: NodeType | str,
    label: str,
    source_handles: list[str] | tuple[str, ...] = (),
    submodel_alias: str | None = None,
    config_reference_override: str | None = None,
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
    if kind == NodeType.SUBMODEL and handles and not submodel_alias:
        raise ValueError("Submodel aliases are required when resolving output handles.")
    mapping = {
        handle: executable_input_name(
            node_type=kind,
            label=label,
            source_handle=handle,
            submodel_alias=submodel_alias,
        )
        for handle in handles
    }
    return ResolvedEditorIdentity(
        function_name=function_name,
        config_reference=config_reference,
        default_input_name=default_input_name,
        source_handle_input_names=mapping,
    )
