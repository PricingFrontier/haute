"""Lightweight config validation for pipeline node types.

Warns on unrecognized config keys so typos and stale keys surface early
instead of being silently ignored.  Returns the unexpected keys so callers
can choose whether to warn, fail, or report them in tests.
"""

from __future__ import annotations

from typing import Any

from haute._logging import get_logger
from haute._types import (
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

logger = get_logger(component="config_validation")

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
        "selected_columns",
        "column_renames",
        "categorical_levels",
        "contract",
    }
)


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def warn_unrecognized_config_keys(
    node_type: NodeType | str,
    config: dict[str, Any],
    *,
    node_label: str = "",
) -> list[str]:
    """Log warnings for config keys not recognised by *node_type*.

    Returns the list of unrecognised key names (handy for testing).
    Never raises.
    """
    try:
        nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    except ValueError:
        # Unknown node type string -- nothing to validate against.
        return []

    valid = VALID_KEYS.get(nt)
    if valid is None:
        return []

    bad = sorted(k for k in config if k not in valid and not k.startswith("_"))
    if bad:
        label = node_label or nt.value
        logger.warning(
            "unrecognized_config_keys",
            node_type=nt.value,
            node_label=label,
            keys=bad,
        )
    return bad


def validate_node_config(node_type: NodeType | str, config: dict[str, Any]) -> dict[str, Any]:
    """Strictly validate configs whose runtime contract is discriminated.

    Most historical node configs still use the warning-only key registry.
    The retained Data Input/Output types are intentionally stricter: their
    provider branch controls which keys and capabilities are legal, so an
    inactive key cannot be silently persisted and ignored.
    """
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    if nt == NodeType.DATA_INPUT:
        from haute._polars_io_registry import validate_data_input_config

        return validate_data_input_config(config)
    if nt == NodeType.DATA_OUTPUT:
        from haute._polars_io_registry import validate_data_output_config

        return validate_data_output_config(config)
    return dict(config)
