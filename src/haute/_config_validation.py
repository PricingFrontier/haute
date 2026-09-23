"""Config validation for pipeline node types.

Each node type declares its config keys through its ``TypedDict``. A key it
does not declare is refused wherever a config is parsed, saved or written, so
a typo or stale key fails loudly instead of being silently dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, overload

from haute._types import (
    COLUMN_CONFIG_KEYS,
    DATA_INPUT_CONFIG_TYPES,
    DATA_OUTPUT_CONFIG_TYPES,
    ApiInputConfig,
    BandingConfig,
    ConstantConfig,
    EdgeJoinConfig,
    ExploreConfig,
    ExternalFileConfig,
    LiveSwitchConfig,
    ModellingConfig,
    ModelScoreConfig,
    NodeType,
    OptimiserApplyConfig,
    OptimiserConfig,
    OutputConfig,
    RatingStepConfig,
    ScenarioExpanderConfig,
    SubmodelConfig,
    TransformConfig,
)
from haute.errors import ConfigError

# ---------------------------------------------------------------------------
# Valid-key registry
# ---------------------------------------------------------------------------
# Built from TypedDict annotations.  We use the TypedDicts as the single
# source of truth (they already list every recognised key for each type).

_TYPED_DICT_BY_NODE_TYPE: dict[NodeType, type] = {
    NodeType.API_INPUT: ApiInputConfig,
    NodeType.POLARS: TransformConfig,
    NodeType.EDGE_JOIN: EdgeJoinConfig,
    NodeType.MODEL_SCORE: ModelScoreConfig,
    NodeType.BANDING: BandingConfig,
    NodeType.RATING_STEP: RatingStepConfig,
    NodeType.OUTPUT: OutputConfig,
    NodeType.EXPLORE: ExploreConfig,
    NodeType.EXTERNAL_FILE: ExternalFileConfig,
    NodeType.LIVE_SWITCH: LiveSwitchConfig,
    NodeType.MODELLING: ModellingConfig,
    NodeType.OPTIMISER: OptimiserConfig,
    NodeType.SCENARIO_EXPANDER: ScenarioExpanderConfig,
    NodeType.OPTIMISER_APPLY: OptimiserApplyConfig,
    NodeType.CONSTANT: ConstantConfig,
    NodeType.SUBMODEL: SubmodelConfig,
}

# Keys that any node type may carry (set by the parser / executor, not by config authors).
# ``selected_columns`` is applied by the executor for *all* node types (column
# filtering for downstream propagation), so it must be universally accepted.
# ``contract`` is the column-contract annotation — may appear on any type.
_UNIVERSAL_KEYS: frozenset[str] = frozenset(
    {
        "instanceOf",
        "inputMapping",
        "contract",
    }
) | frozenset(COLUMN_CONFIG_KEYS)


def _valid_keys_for(node_type: NodeType) -> frozenset[str] | None:
    """Return the set of recognised config keys for *node_type*, or None if unknown."""
    if node_type == NodeType.DATA_INPUT:
        return (
            frozenset().union(*(td.__annotations__ for td in DATA_INPUT_CONFIG_TYPES))
            | _UNIVERSAL_KEYS
        )
    if node_type == NodeType.DATA_OUTPUT:
        return (
            frozenset().union(*(td.__annotations__ for td in DATA_OUTPUT_CONFIG_TYPES))
            | _UNIVERSAL_KEYS
        )
    td = _TYPED_DICT_BY_NODE_TYPE.get(node_type)
    if td is None:
        return None
    return frozenset(td.__annotations__) | _UNIVERSAL_KEYS


# Pre-compute so the per-call cost is a single dict lookup.
VALID_KEYS: dict[NodeType, frozenset[str]] = {
    nt: keys for nt in NodeType if (keys := _valid_keys_for(nt)) is not None
}


_REMOVED_INPUT_IDENTITY_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.EDGE_JOIN: frozenset({"baseInput", "joinInput"}),
    NodeType.OPTIMISER: frozenset({"scored_input", "factors_input"}),
}
_REMOVED_REGISTRY_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.MODELLING: frozenset({"model_name"}),
    NodeType.OPTIMISER: frozenset({"model_name"}),
}


def reject_removed_config_keys(
    node_type: NodeType | str,
    config: dict[str, Any],
) -> None:
    """Reject retired config fields instead of silently migrating or ignoring them."""
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    removed_identity = sorted(
        _REMOVED_INPUT_IDENTITY_KEYS.get(nt, frozenset()).intersection(config)
    )
    if removed_identity:
        if nt == NodeType.EDGE_JOIN:
            guidance = "use incoming target ports 'base' and 'join'"
        else:
            guidance = "use data_input and banding_source with exact connected input names"
        raise ConfigError(
            f"{nt.value} config contains removed input identity fields; {guidance}.",
            removed_config_keys=removed_identity,
        )
    removed_registry = sorted(_REMOVED_REGISTRY_KEYS.get(nt, frozenset()).intersection(config))
    if removed_registry:
        raise ConfigError(
            f"{nt.value} config contains the removed model_name field. Haute no longer "
            "registers models: remove the field and register or promote runs outside haute.",
            removed_config_keys=removed_registry,
        )


@overload
def resolve_exact_input_index(
    selector: Any,
    source_names: Sequence[str],
    *,
    required: Literal[True],
    node_label: str,
    field_name: str,
) -> int: ...


@overload
def resolve_exact_input_index(
    selector: Any,
    source_names: Sequence[str],
    *,
    required: Literal[False],
    node_label: str,
    field_name: str,
) -> int | None: ...


@overload
def resolve_exact_input_index(
    selector: Any,
    source_names: Sequence[str],
    *,
    required: bool,
    node_label: str,
    field_name: str,
) -> int | None: ...


def resolve_exact_input_index(
    selector: Any,
    source_names: Sequence[str],
    *,
    required: bool,
    node_label: str,
    field_name: str,
) -> int | None:
    """Resolve an exact incoming-edge input selector to its position.

    A selector identifies a *physical* incoming edge by its executable input
    name.  Only ``None`` and the empty string mean no selection; matching is
    byte-for-byte, so a whitespace-decorated value is a stale selector, never
    an absent one, and every present value must identify exactly one edge.
    This is the single implementation shared by the executor, code
    generation, tracing, and save validation.
    """
    context = f"{node_label!r} {field_name}"
    if selector is None or selector == "":
        if required:
            raise ConfigError(f"Node {context} is required to name one exact incoming edge.")
        return None
    if not isinstance(selector, str):
        raise ConfigError(f"Node {context} must be an incoming-edge frame name string.")

    matches = [index for index, name in enumerate(source_names) if name == selector]
    if not matches:
        raise ConfigError(
            f"Node {context} {selector!r} is not an exact connected input name: "
            f"{list(source_names)!r}."
        )
    if len(matches) > 1:
        raise ConfigError(
            f"Node {context} {selector!r} is ambiguous across connected input names: "
            f"{list(source_names)!r}."
        )
    return matches[0]


def validate_exact_input_selector(
    selector: Any,
    source_names: Sequence[str],
    *,
    required: bool,
    node_label: str,
    field_name: str,
) -> str | None:
    """Return the validated selector name; see :func:`resolve_exact_input_index`."""
    index = resolve_exact_input_index(
        selector,
        source_names,
        required=required,
        node_label=node_label,
        field_name=field_name,
    )
    return None if index is None else source_names[index]


def resolve_optimiser_data_input(
    config: Mapping[str, Any],
    source_names: Sequence[str],
    *,
    node_label: str,
) -> str | None:
    """Return the optimiser's selected ``data_input`` name when configured.

    A multi-input optimiser must name its data edge; a single-input optimiser
    may leave the selector empty and pass that input through.
    """
    data_input = validate_exact_input_selector(
        config.get("data_input"),
        source_names,
        required=False,
        node_label=node_label,
        field_name="data_input",
    )
    if data_input is None and len(source_names) > 1:
        raise ConfigError(
            f"Node {node_label!r} data_input is required when multiple "
            "incoming edges are connected."
        )
    return data_input


def validate_optimiser_input_selectors(
    node_type: NodeType | str,
    config: Mapping[str, Any],
    source_names: Sequence[str],
    *,
    node_label: str,
) -> str | None:
    """Validate exact-edge selectors shared by save and code generation.

    Returns the optimiser's selected data input when configured. Optimiser
    Apply has no codegen-time return selection, so its successful result is
    always ``None``.
    """
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    if nt is NodeType.OPTIMISER:
        data_input = resolve_optimiser_data_input(config, source_names, node_label=node_label)
        validate_exact_input_selector(
            config.get("banding_source"),
            source_names,
            required=config.get("mode") == "ratebook",
            node_label=node_label,
            field_name="banding_source",
        )
        return data_input

    if nt is NodeType.OPTIMISER_APPLY:
        optimiser_mode = config.get("optimiser_mode")
        if optimiser_mode != "online":
            validate_exact_input_selector(
                config.get("ratebook_input"),
                source_names,
                required=optimiser_mode == "ratebook",
                node_label=node_label,
                field_name="ratebook_input",
            )
        return None

    raise ValueError(f"Unsupported optimiser selector node type: {nt.value!r}.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


CODE_CONFIG_KEYS: frozenset[str] = frozenset({"code"})
"""Config keys whose value lives in the node's ``.py`` body, never in its sidecar."""


def unrecognized_config_keys(node_type: NodeType, config: Mapping[str, Any]) -> list[str]:
    """Return, sorted, the top-level config keys *node_type* does not declare.

    ``_``-prefixed editor state and the body code are not config keys. A node
    type without a ``TypedDict`` (``SUBMODEL_PORT``) declares nothing to check.
    """
    valid = VALID_KEYS.get(node_type)
    if valid is None:
        return []
    return sorted(
        key
        for key in config
        if not key.startswith("_") and key not in CODE_CONFIG_KEYS and key not in valid
    )


def reject_unrecognized_config_keys(
    node_type: NodeType,
    config: Mapping[str, Any],
    *,
    node_label: str,
) -> None:
    """Refuse a config that carries keys its node type does not declare.

    Dropping such a key would lose persisted work without the user seeing it.

    Raises:
        ConfigError: naming the node, its type and every undeclared key.
    """
    unrecognized = unrecognized_config_keys(node_type, config)
    if unrecognized:
        keys = ", ".join(repr(key) for key in unrecognized)
        raise ConfigError(
            f"Node {node_label!r} has {node_type.value} config keys the node type does "
            f"not declare: {keys}. Remove them from its config, or declare them in the "
            "node type's config.",
            unrecognized_config_keys=unrecognized,
        )


_MLFLOW_DESTINATION_NODE_TYPES = frozenset(
    {
        NodeType.MODELLING,
        NodeType.OPTIMISER,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
    }
)


def validate_mlflow_destination(node_type: NodeType, config: Mapping[str, Any]) -> None:
    """A node names a remote MLflow destination, or has none and uses the local folder."""
    value = config.get("mlflow_destination", "")
    if value in ("", None):
        return
    if not isinstance(value, str) or value not in ("databricks", "server"):
        raise ConfigError(
            f"{node_type.value} config has an invalid mlflow_destination; expected databricks "
            "or server. Remove the field to use the local MLflow folder.",
            mlflow_destination=value if isinstance(value, str) else type(value).__name__,
        )


def validate_registered_model_alias(node_type: NodeType, config: Mapping[str, Any]) -> None:
    """A registered source names a version or an alias, never both."""
    alias = config.get("alias")
    if alias in (None, ""):
        return
    if not isinstance(alias, str) or not alias.strip() or alias != alias.strip():
        raise ConfigError(
            f"{node_type.value} config has an invalid alias; expected a registered model "
            "alias name such as champion.",
            alias=alias if isinstance(alias, str) else type(alias).__name__,
        )
    if config.get("version") not in (None, ""):
        raise ConfigError(
            f"{node_type.value} config names both a version and an alias; choose one. "
            "Remove version to follow the alias, or remove alias to pin the version.",
            alias=alias,
            version=config.get("version"),
        )


def validate_node_config(
    node_type: NodeType | str, config: dict[str, Any], *, require_complete: bool = True
) -> dict[str, Any]:
    """Strictly validate configs whose runtime contract is discriminated.

    Data Input/Output provider branches control which keys and capabilities
    are legal. Banding's discriminant controls its rule schema. Invalid
    configured branches must not be silently persisted and ignored.
    ``require_complete=False`` tolerates absent/empty required Data
    Input/Output locators (declared-incomplete forms); structural rules and
    every other node type stay strict.
    """
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    reject_removed_config_keys(nt, config)
    if nt in _MLFLOW_DESTINATION_NODE_TYPES:
        validate_mlflow_destination(nt, config)
    if nt in (NodeType.MODEL_SCORE, NodeType.OPTIMISER_APPLY):
        validate_registered_model_alias(nt, config)
    if nt == NodeType.DATA_INPUT:
        from haute._polars_io_registry import validate_data_input_config

        return validate_data_input_config(config, require_complete=require_complete)
    if nt == NodeType.DATA_OUTPUT:
        from haute._polars_io_registry import validate_data_output_config

        return validate_data_output_config(config, require_complete=require_complete)
    if nt == NodeType.BANDING:
        from haute._rating import validate_banding_config

        validate_banding_config(config)
    return dict(config)
