"""Edge-join node validation and Polars join helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import polars as pl

from haute._cardinality import normalise_join_validation
from haute._polars_utils import execution_collect
from haute._types import EDGE_JOIN_CONFIG_KEYS
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
    if len(present_handles) != len(target_handles):
        raise ConfigError(
            "edgeJoin targetHandle roles are required on every incoming edge; "
            "use exactly one 'base' and one 'join' handle.",
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

    raw_validate = config.get("validate")
    if raw_validate is not None and raw_validate != "":
        try:
            kwargs["validate"] = normalise_join_validation(raw_validate)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "edgeJoin validate must be one of the supported Polars uniqueness contracts.",
                validate=raw_validate,
                supported=["1:1", "1:m", "m:1", "m:m"],
            ) from exc

    for config_key, polars_key in (
        ("coalesce", "coalesce"),
        ("maintainOrder", "maintain_order"),
    ):
        if config_key in config and config[config_key] is not None and config[config_key] != "":
            kwargs[polars_key] = config[config_key]

    return kwargs


def edge_join_key_columns_by_role(config: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """Return the (base_keys, join_keys) demanded by an edge-join's keys.

    ``on=[k]`` demands ``k`` from both roles; ``leftOn``/``rightOn`` demand the
    left keys from the base role and the right keys from the join role.  Cross
    joins demand no keys.  Config is validated through :func:`build_edge_join_kwargs`
    so stale/missing keys fail loudly with :class:`ConfigError`.
    """
    kwargs = build_edge_join_kwargs(config)
    if kwargs["how"] == "cross":
        return frozenset(), frozenset()
    on = kwargs.get("on")
    if on is not None:
        keys = frozenset(_key_columns(on))
        return keys, keys
    left_keys = frozenset(_key_columns(kwargs["left_on"]))
    right_keys = frozenset(_key_columns(kwargs["right_on"]))
    return left_keys, right_keys


def _key_columns(value: JoinKey) -> list[str]:
    return list(value) if isinstance(value, list) else [value]


def narrow_join_parent_demand(
    demanded: Iterable[str],
    *,
    left_keys: set[str],
    right_keys: set[str],
    left_schema: set[str],
    right_schema: set[str],
    how: str,
    suffix: str,
) -> tuple[set[str], set[str]] | None:
    """Route a join's demanded OUTPUT columns to ``(left_demand, right_demand)``.

    The single source of truth shared by the static projection rule
    (:class:`haute.projection.EdgeJoinFanInRule`, which passes the parents'
    produced-column contracts as the schemas) and the runtime narrowing helper
    (which passes the live parent frame schemas), so the two cannot drift.

    Returns ``None`` when the demand cannot be mapped mechanically — the caller
    then keeps the full-width boundary rather than guessing or dropping a column.
    Only the join strategies whose column provenance is mechanical are narrowed
    (``inner``/``left``/``semi``/``anti``); ``cross``/``full``/``right`` and
    keyless joins return ``None``.

    Suffix-aware: when both sides produce ``<col>`` (a name collision) Polars
    emits the right-hand copy as ``<col><suffix>``. A demanded ``<col><suffix>``
    therefore maps to ``<col>`` on BOTH parents — the left copy is kept so Polars
    still emits the suffixed right-hand output. Without this the join parent's
    ``<col>`` would be pruned and the suffixed output silently dropped.
    """
    if how not in {"inner", "left", "semi", "anti"}:
        return None
    if not (left_keys or right_keys):
        return None
    if suffix == "":
        return None
    left_demand: set[str] = set(left_keys)
    right_demand: set[str] = set(right_keys)
    for column in demanded:
        if column in left_keys:
            continue
        mapped = False
        if column.endswith(suffix):
            original = column[: -len(suffix)]
            if original and original in left_schema and original in right_schema:
                if column in left_schema or column in right_schema:
                    # The suffixed name is itself a real column — ambiguous.
                    return None
                left_demand.add(original)
                right_demand.add(original)
                mapped = True
        if mapped:
            continue
        if column in left_schema:
            left_demand.add(column)
            mapped = True
        if column in right_schema and column not in left_schema:
            right_demand.add(column)
            mapped = True
        if column in right_keys:
            right_demand.add(column)
            mapped = True
        if not mapped:
            return None
    return left_demand, right_demand


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
        return execution_collect(result)
    return result


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
