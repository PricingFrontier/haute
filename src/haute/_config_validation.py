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
    WorkbenchInputConfig,
    WorkbenchOutputConfig,
)
from haute.errors import ConfigError, ParseError

# ---------------------------------------------------------------------------
# Valid-key registry
# ---------------------------------------------------------------------------
# Built from TypedDict annotations.  We use the TypedDicts as the single
# source of truth (they already list every recognised key for each type).

_TYPED_DICT_BY_NODE_TYPE: dict[NodeType, type] = {
    NodeType.API_INPUT: ApiInputConfig,
    NodeType.WORKBENCH_INPUT: WorkbenchInputConfig,
    NodeType.POLARS: TransformConfig,
    NodeType.EDGE_JOIN: EdgeJoinConfig,
    NodeType.MODEL_SCORE: ModelScoreConfig,
    NodeType.BANDING: BandingConfig,
    NodeType.RATING_STEP: RatingStepConfig,
    NodeType.OUTPUT: OutputConfig,
    NodeType.WORKBENCH_OUTPUT: WorkbenchOutputConfig,
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
_REMOVED_FEATURE_SELECTION_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.MODELLING: frozenset({"exclude"}),
}
_REMOVED_COLUMN_MAPPING_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.WORKBENCH_OUTPUT: frozenset({"mapping"}),
}
#: The keys a workbench node is given from the project's form wherever the pipeline runs
#: (``haute._workbench_nodes``), so a config file or a saved config never holds them: an
#: earlier Haute kept copies there (specs/workbench).
FORM_GIVEN_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.WORKBENCH_INPUT: frozenset({"tables", "sample"}),
    NodeType.WORKBENCH_OUTPUT: frozenset({"tables"}),
}


def removed_node_instance_error(form: str) -> ParseError:
    """One refusal and replacement for every removed node-level reuse form."""
    remediation = (
        "Move the node into its own module modules/<name>.py, import it with "
        "from modules import <name>, and register "
        'pipeline.submodel(<name>.submodel, "<original>") and '
        'pipeline.submodel(<name>.submodel, "<instance>", instance_of="<original>"). '
        'Bind each input with pipeline.connect(..., target_port="<input>").'
    )
    return ParseError(
        f"{form} is a removed node-level instance form. {remediation}",
        removed_node_instance=form,
        remediation=remediation,
    )


def reject_removed_node_reuse_kwargs(kwargs: Mapping[str, Any]) -> None:
    """Reject removed reuse keywords in both parsed and live node decorators."""
    for removed in ("of", "instanceOf"):
        if removed in kwargs:
            raise removed_node_instance_error("of=" if removed == "of" else removed)


def reject_removed_config_keys(
    node_type: NodeType | str,
    config: dict[str, Any],
) -> None:
    """Reject retired config fields instead of silently migrating or ignoring them."""
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    if nt != NodeType.SUBMODEL and "instanceOf" in config:
        raise removed_node_instance_error("instanceOf")
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
    removed_selection = sorted(
        _REMOVED_FEATURE_SELECTION_KEYS.get(nt, frozenset()).intersection(config)
    )
    if removed_selection:
        raise ConfigError(
            f"{nt.value} config contains the removed exclude field. Features are opt-in: "
            "list the columns to train on in feature_columns (tick them on the Features "
            "pane) and remove exclude.",
            removed_config_keys=removed_selection,
        )
    removed_mapping = sorted(_REMOVED_COLUMN_MAPPING_KEYS.get(nt, frozenset()).intersection(config))
    if removed_mapping:
        raise ConfigError(
            f"{nt.value} config contains the removed mapping field. Each table column is "
            "filled from the connected frame's column of exactly its name: name the frame's "
            "columns as the workbench does upstream and remove mapping.",
            removed_config_keys=removed_mapping,
        )


def reject_form_given_keys(node_type: NodeType | str, config: Mapping[str, Any]) -> None:
    """Refuse a config file's or a saved config's ``tables`` or ``sample`` on a workbench
    node, which it is given from the form folder wherever it runs (:data:`FORM_GIVEN_KEYS`).

    Only where a config is persisted or read back: the config a run builds a node from
    holds them, given.
    """
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    given = FORM_GIVEN_KEYS.get(nt, frozenset())
    held = [key for key in ("tables", "sample") if key in given and key in config]
    if not held:
        return
    if nt is NodeType.WORKBENCH_INPUT:
        reads = "A Workbench Input reads its tables and sample from the form folder"
    else:
        reads = "A Workbench Output reads its tables from the form folder"
    fields = " and ".join(held)
    raise ConfigError(
        f"{nt.value} config contains the removed {fields} "
        f"field{'s' if len(held) > 1 else ''}. {reads}: delete "
        f"{'them' if len(held) > 1 else 'it'}.",
        removed_config_keys=held,
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
        validate_exact_input_selector(
            config.get("analysis_input"),
            source_names,
            required=False,
            node_label=node_label,
            field_name="analysis_input",
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

    The body code is not a config key, and editor state travels beside the
    config (``NodeData.editor``), never in it, so an underscore-prefixed key is
    as undeclared as any other. A node type without a ``TypedDict``
    (``SUBMODEL_PORT``) declares nothing to check.
    """
    valid = VALID_KEYS.get(node_type)
    if valid is None:
        return []
    return sorted(key for key in config if key not in CODE_CONFIG_KEYS and key not in valid)


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
    if node_type != NodeType.SUBMODEL and "instanceOf" in config:
        raise removed_node_instance_error("instanceOf")
    unrecognized = unrecognized_config_keys(node_type, config)
    if unrecognized:
        keys = ", ".join(repr(key) for key in unrecognized)
        raise ConfigError(
            f"Node {node_label!r} has {node_type.value} config keys the node type does "
            f"not declare: {keys}. Remove them from its config, or declare them in the "
            "node type's config.",
            unrecognized_config_keys=unrecognized,
        )


MAX_ANALYSIS_COLUMNS = 12
"""The most analysis columns an optimiser keeps for result breakdowns (OPT-V09A)."""
RESERVED_ANALYSIS_COLUMN_PREFIX = "__haute_"


def validate_optimiser_analysis_config(config: Mapping[str, Any]) -> None:
    """The optimiser's analysis-column shape rules, shared by save and solve start.

    ``analysis_input`` is an optional input name; ``analysis_columns`` an optional
    list of at most ``MAX_ANALYSIS_COLUMNS`` distinct, non-empty column names,
    none of them the configured quote-id column or a reserved ``__haute_`` name.
    Whether the input is connected and has the columns is checked where the
    graph and the data are known.
    """
    analysis_input = config.get("analysis_input")
    if analysis_input is not None and not isinstance(analysis_input, str):
        raise ConfigError(
            "Optimiser analysis_input must be an input name.",
            analysis_input=type(analysis_input).__name__,
        )
    columns = config.get("analysis_columns")
    if columns is None:
        return
    if not isinstance(columns, list):
        raise ConfigError(
            "Optimiser analysis_columns must be a list of column names.",
            analysis_columns=type(columns).__name__,
        )
    if len(columns) > MAX_ANALYSIS_COLUMNS:
        raise ConfigError(
            f"Optimiser analysis_columns can hold at most {MAX_ANALYSIS_COLUMNS} columns; "
            f"{len(columns)} are configured. Remove some to keep the breakdowns bounded.",
            analysis_column_count=len(columns),
        )
    if any(not isinstance(column, str) or not column for column in columns):
        raise ConfigError("Optimiser analysis_columns must be non-empty column names.")
    duplicates = sorted({column for column in columns if columns.count(column) > 1})
    if duplicates:
        raise ConfigError(
            f"Optimiser analysis_columns lists a duplicate column: {duplicates}.",
            duplicate_analysis_columns=duplicates,
        )
    quote_id = str(config.get("quote_id") or "quote_id")
    if quote_id in columns:
        raise ConfigError(
            f"Optimiser analysis_columns cannot include the quote-id column {quote_id!r}: "
            "the analysis table is keyed by it already."
        )
    reserved = sorted(
        column for column in columns if column.startswith(RESERVED_ANALYSIS_COLUMN_PREFIX)
    )
    if reserved:
        raise ConfigError(
            f"Optimiser analysis_columns {reserved} use the reserved "
            f"{RESERVED_ANALYSIS_COLUMN_PREFIX!r} prefix. Rename the columns upstream.",
            reserved_analysis_columns=reserved,
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
    are legal. Banding's discriminant controls its rule schema. A Scenario
    Expander's grid size is required with no incomplete form. A Modelling
    node's configured values must be trainable, though it may be unfinished.
    A transform's keyword-only parameters must be ones its signature can carry.
    Invalid configured branches must not be silently persisted and ignored.
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
    if nt == NodeType.OPTIMISER:
        validate_optimiser_analysis_config(config)
    if nt == NodeType.MODELLING:
        from haute.modelling._train_config import validate_modelling_config_values

        # Malformed values only: an unfinished node (a new node is ``{}``) saves,
        # and its completeness is checked when training starts.
        validate_modelling_config_values(config)
    if nt == NodeType.DATA_INPUT:
        from haute._polars_io_registry import validate_data_input_config

        return validate_data_input_config(config, require_complete=require_complete)
    if nt == NodeType.DATA_OUTPUT:
        from haute._polars_io_registry import validate_data_output_config

        return validate_data_output_config(config, require_complete=require_complete)
    if nt == NodeType.BANDING:
        from haute._rating import validate_banding_config

        validate_banding_config(config)
    if nt == NodeType.SCENARIO_EXPANDER:
        from haute._node_apply import scenario_step_count

        # The grid size has no incomplete form: a new node carries an
        # explicit count, so its absence is a defect the builder would reject.
        scenario_step_count(config)
    if nt == NodeType.POLARS:
        from haute._keyword_parameters import validate_keyword_parameters

        validate_keyword_parameters(config)
    return dict(config)
