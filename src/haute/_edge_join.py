"""Edge-join node validation and Polars join helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from haute._types import EDGE_JOIN_CONFIG_KEYS, GraphNode, NodeType
from haute.errors import ConfigError

EDGE_JOIN_DEFAULT_HOW = "left"
EDGE_JOIN_DEFAULT_SUFFIX = "_right"

EDGE_JOIN_DECORATOR_TO_CONFIG: dict[str, str] = {
    "base_input": "baseInput",
    "join_input": "joinInput",
    "left_on": "leftOn",
    "right_on": "rightOn",
    "maintain_order": "maintainOrder",
}

EDGE_JOIN_CONFIG_TO_DECORATOR: dict[str, str] = {
    value: key for key, value in EDGE_JOIN_DECORATOR_TO_CONFIG.items()
}

_ALLOWED_HOW = frozenset({"inner", "left", "right", "full", "semi", "anti", "cross"})
# Keep these role handles/config keys in sync with frontend/src/utils/edgeJoinRoles.ts.
_ROLE_HANDLE_TO_CONFIG_KEY = {
    "base": "baseInput",
    "join": "joinInput",
}


JoinKey = str | list[str]


def normalise_edge_join_decorator_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Convert edge-join decorator kwargs into graph config keys."""
    config: dict[str, Any] = {}
    for key, value in kwargs.items():
        config[EDGE_JOIN_DECORATOR_TO_CONFIG.get(key, key)] = value
    return config


def edge_join_config_to_decorator_kwargs(config: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return decorator kwargs in stable snake-case order."""
    items: list[tuple[str, Any]] = []
    for key in EDGE_JOIN_CONFIG_KEYS:
        if key not in config:
            continue
        value = config[key]
        if value is None or value == "" or value == []:
            continue
        decorator_key = EDGE_JOIN_CONFIG_TO_DECORATOR.get(key, key)
        items.append((decorator_key, value))
    return items


def resolve_edge_join_role_indices(
    config: dict[str, Any],
    source_ids: Sequence[str],
    target_handles: Sequence[str | None] | None = None,
) -> tuple[int, int]:
    """Resolve base/join input roles against connected source ids."""
    if len(source_ids) != 2:
        raise ConfigError(
            "edgeJoin nodes require exactly two connected inputs.",
            connected_input_node_ids=list(source_ids),
        )
    if len(set(source_ids)) != len(source_ids):
        raise ConfigError(
            "edgeJoin connected inputs must be distinct.",
            connected_input_node_ids=list(source_ids),
        )

    base_input = _required_role(config, "baseInput")
    join_input = _required_role(config, "joinInput")
    if base_input == join_input:
        raise ConfigError(
            "edgeJoin baseInput and joinInput must be distinct.",
            baseInput=base_input,
            joinInput=join_input,
        )

    missing = [role for role in (base_input, join_input) if role not in source_ids]
    if missing:
        raise ConfigError(
            "edgeJoin role input is not connected to the node.",
            missing=missing,
            connected_input_node_ids=list(source_ids),
        )

    if target_handles is not None:
        validate_edge_join_target_handles(config, source_ids, target_handles)

    return source_ids.index(base_input), source_ids.index(join_input)


def validate_edge_join_target_handles(
    config: dict[str, Any],
    source_ids: Sequence[str],
    target_handles: Sequence[str | None],
) -> None:
    """Validate optional React Flow role handles against edge-join config."""
    if len(source_ids) != len(target_handles):
        raise ConfigError(
            "edgeJoin targetHandle metadata must align with connected inputs.",
            connected_input_node_ids=list(source_ids),
            targetHandles=list(target_handles),
        )

    present_handles = [handle for handle in target_handles if handle is not None]
    if not present_handles:
        return
    if len(present_handles) != len(target_handles):
        raise ConfigError(
            "edgeJoin targetHandle roles must be set on every incoming edge "
            "when any role handle is set.",
            connected_input_node_ids=list(source_ids),
            targetHandles=list(target_handles),
        )

    if set(present_handles) != set(_ROLE_HANDLE_TO_CONFIG_KEY):
        raise ConfigError(
            "edgeJoin targetHandle roles must be exactly 'base' and 'join'.",
            connected_input_node_ids=list(source_ids),
            targetHandles=list(target_handles),
        )

    for source_id, target_handle in zip(source_ids, target_handles, strict=True):
        if target_handle is None:
            raise ConfigError(
                "edgeJoin targetHandle must be 'base' or 'join'.",
                source=source_id,
                targetHandle=target_handle,
            )
        config_key = _ROLE_HANDLE_TO_CONFIG_KEY.get(target_handle)
        if config_key is None:
            raise ConfigError(
                "edgeJoin targetHandle must be 'base' or 'join'.",
                source=source_id,
                targetHandle=target_handle,
            )
        expected_source = _required_role(config, config_key)
        if source_id != expected_source:
            raise ConfigError(
                "edgeJoin targetHandle conflicts with configured baseInput/joinInput.",
                source=source_id,
                targetHandle=target_handle,
                baseInput=config.get("baseInput"),
                joinInput=config.get("joinInput"),
            )


def build_edge_join_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """Validate config and return kwargs for ``LazyFrame.join``."""
    how = config.get("how") or EDGE_JOIN_DEFAULT_HOW
    if not isinstance(how, str) or how not in _ALLOWED_HOW:
        raise ConfigError(
            "edgeJoin how must be one of the supported Polars join strategies.",
            how=how,
            supported=sorted(_ALLOWED_HOW),
        )

    on = _normalise_key(config.get("on"), "on")
    left_on = _normalise_key(config.get("leftOn"), "leftOn")
    right_on = _normalise_key(config.get("rightOn"), "rightOn")

    if how == "cross":
        if on is not None or left_on is not None or right_on is not None:
            raise ConfigError("edgeJoin cross joins must not configure join keys.")
    else:
        if on is not None and (left_on is not None or right_on is not None):
            raise ConfigError("edgeJoin config cannot combine on with leftOn/rightOn.")
        if on is None:
            if left_on is None or right_on is None:
                raise ConfigError(
                    "edgeJoin non-cross joins require join keys via on or leftOn/rightOn.",
                )
            if _key_count(left_on) != _key_count(right_on):
                raise ConfigError(
                    "edgeJoin leftOn and rightOn must contain the same number of keys.",
                    leftOn=left_on,
                    rightOn=right_on,
                )

    kwargs: dict[str, Any] = {"how": how}
    if on is not None:
        kwargs["on"] = on
    if left_on is not None:
        kwargs["left_on"] = left_on
    if right_on is not None:
        kwargs["right_on"] = right_on

    suffix = config.get("suffix")
    if suffix is None or suffix == "":
        suffix = EDGE_JOIN_DEFAULT_SUFFIX
    if not isinstance(suffix, str):
        raise ConfigError("edgeJoin suffix must be a string.", suffix=suffix)
    kwargs["suffix"] = suffix

    for config_key, polars_key in (
        ("validate", "validate"),
        ("coalesce", "coalesce"),
        ("maintainOrder", "maintain_order"),
    ):
        if config_key in config and config[config_key] is not None and config[config_key] != "":
            kwargs[polars_key] = config[config_key]

    return kwargs


def execute_edge_join(
    base: pl.LazyFrame | pl.DataFrame,
    join: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
    *,
    collect_eager: bool = False,
) -> pl.LazyFrame | pl.DataFrame:
    """Execute an edge-join from shared config."""
    base_is_lazy = isinstance(base, pl.LazyFrame)
    join_is_lazy = isinstance(join, pl.LazyFrame)
    if isinstance(base, pl.LazyFrame):
        base_lf: pl.LazyFrame = base
    else:
        base_lf = base.lazy()
    if isinstance(join, pl.LazyFrame):
        join_lf: pl.LazyFrame = join
    else:
        join_lf = join.lazy()
    result = base_lf.join(join_lf, **build_edge_join_kwargs(config))
    if collect_eager and not base_is_lazy and not join_is_lazy:
        return result.collect()
    return result


def build_edge_join_boundary_target_roles(
    submodels: Mapping[str, Mapping[str, Any]],
    names_to_include: set[str] | None = None,
) -> dict[tuple[str, str, str], str]:
    """Map submodel boundary inputs to internal edge-join target roles."""
    roles: dict[tuple[str, str, str], str] = {}
    for sm_name, sm_meta in submodels.items():
        if names_to_include is not None and sm_name not in names_to_include:
            continue
        sm_node_id = f"submodel__{sm_name}"
        sm_graph = sm_meta.get("graph", {})
        if not isinstance(sm_graph, Mapping):
            continue
        raw_nodes = sm_graph.get("nodes", [])
        if not isinstance(raw_nodes, list):
            continue
        for raw_node in raw_nodes:
            node = GraphNode.model_validate(raw_node) if isinstance(raw_node, dict) else raw_node
            if not isinstance(node, GraphNode) or node.data.nodeType != NodeType.EDGE_JOIN:
                continue
            for config_key, role in (("baseInput", "base"), ("joinInput", "join")):
                source_id = node.data.config.get(config_key)
                if isinstance(source_id, str) and source_id:
                    roles[(sm_node_id, node.id, source_id)] = role
    return roles


def _required_role(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or value == "":
        raise ConfigError(f"edgeJoin requires a non-empty {key}.", key=key, value=value)
    return value


def _normalise_key(value: Any, field: str) -> JoinKey | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not all(isinstance(item, str) and item for item in value):
            raise ConfigError(
                f"edgeJoin {field} must contain non-empty string column names.",
                field=field,
                value=value,
            )
        return list(value)
    raise ConfigError(
        f"edgeJoin {field} must be a string, list of strings, or empty.",
        field=field,
        value=value,
    )


def _key_count(value: JoinKey) -> int:
    return len(value) if isinstance(value, list) else 1
