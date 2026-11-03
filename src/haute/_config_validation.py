"""The one validation boundary for node configs.

Each node type's config model (``haute._node_config_models``) declares its keys,
their types and the engine's value rules. :func:`validate_node_config` holds a
config to it wherever a config is parsed, saved, recovered, written or built, so
a typo, a stale key or a malformed value fails loudly instead of being silently
dropped or reaching the engine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, overload

from pydantic import ValidationError

from haute._api_input_schema import ApiInputSchemaError
from haute._contracts import DEPLOY_ANSWER_PER_ROW_CONFIG_KEY, DEPLOY_PRIVATE_CONFIG_KEYS
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._output_assembler import OutputMappingSchemaError
from haute._types import NodeType
from haute.errors import ConfigError, NodeConfigError, ParseError

if TYPE_CHECKING:
    from haute._types import PipelineGraph

#: Each node type's declared config keys, from its config model.
VALID_KEYS: dict[NodeType, frozenset[str]] = {
    node_type: model.keys for node_type, model in NODE_CONFIG_MODELS.items()
}

#: Every error :func:`validate_node_config` raises for a config it refuses, for a boundary
#: to answer as the request's refusal: its own (``ConfigError`` and ``NodeConfigError``, the
#: removed reuse forms' ``ParseError``) and an engine rule's, in the engine's own type (most
#: are ``ValueError``; the Quote Input's and the Quote Response's are not).
CONFIG_REFUSALS: tuple[type[Exception], ...] = (
    ConfigError,
    ParseError,
    ValueError,
    ApiInputSchemaError,
    OutputMappingSchemaError,
)


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
_REMOVED_RESPONSE_FORMAT_KEYS: dict[NodeType, frozenset[str]] = {
    NodeType.OUTPUT: frozenset({"outputFormat"}),
}
_REMOVED_EVALUATION_KEYS: dict[NodeType, tuple[str, ...]] = {
    NodeType.MODELLING: ("split", "cross_validation"),
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
    config: Mapping[str, Any],
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
    removed_format = sorted(_REMOVED_RESPONSE_FORMAT_KEYS.get(nt, frozenset()).intersection(config))
    if removed_format:
        raise ConfigError(
            f"{nt.value} config contains the removed outputFormat field. The response "
            "document is always JSON: remove outputFormat.",
            removed_config_keys=removed_format,
        )
    removed_evaluation = [key for key in _REMOVED_EVALUATION_KEYS.get(nt, ()) if key in config]
    if removed_evaluation:
        names = " and ".join(repr(key) for key in removed_evaluation)
        plural = len(removed_evaluation) > 1
        raise ConfigError(
            f"The modelling config field{'s' if plural else ''} {names} "
            f"{'were' if plural else 'was'} removed; use the versioned 'evaluation' object.",
            removed_config_keys=sorted(removed_evaluation),
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

    ``code`` is declared by the code-surface types alone, and editor state
    travels beside the config (``NodeData.editor``), never in it, so an
    underscore-prefixed key is as undeclared as any other.
    """
    valid = VALID_KEYS[node_type]
    return sorted(key for key in config if key not in valid)


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
    node_type: NodeType | str,
    config: Mapping[str, Any],
    *,
    require_complete: bool = False,
    persisted: bool = True,
    node_label: str | None = None,
    input_names: Sequence[str] | None = None,
    at_run: bool = False,
    shape_only: bool = False,
) -> dict[str, Any]:
    """Hold *config* to its node type's config model; the one validation boundary.

    In order: the removed keys (whose messages name the replacement), the
    form-given keys of a *persisted* config, the undeclared keys, the shape,
    the engine's value rules (``check``), the rules that need the connected
    *input_names* when they are given (``check_inputs``), and when
    *require_complete* the first thing an unfinished config lacks
    (``completeness``). A config that is not *persisted* (one a run builds)
    may also hold the deploy scorer's private transport keys for its node type.
    A run (``at_run``) leaves ``check`` to a node type whose builder applies it
    as its first step (``builder_checks``: a Rating Step's tables are normalised
    once per build, which a deployed endpoint does per request). ``shape_only``
    checks the keys and the shape alone, for a route that answers with a graph
    it transformed rather than saved.

    Returns a copy of *config*: validation never adds, removes, reorders or
    converts a value.

    Raises:
        NodeConfigError: a value the shape refuses, its ``setting`` the value's
            path, or the first completeness gap.
        ConfigError: an undeclared or removed key, or an engine rule's refusal
            (the engine's own error type, unchanged).
    """
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    model = NODE_CONFIG_MODELS[nt]
    label = node_label or nt.value
    reject_removed_config_keys(nt, config)
    if persisted:
        reject_form_given_keys(nt, config)
        shaped: Mapping[str, Any] = config
    else:
        shaped = _without_deploy_private_keys(nt, config)
    reject_unrecognized_config_keys(nt, shaped, node_label=label)
    try:
        model.adapter.validate_python(dict(shaped))
    except ValidationError as exc:
        raise _shape_error(nt, label, shaped, exc) from None
    if shape_only:
        return dict(config)
    if model.check is not None and not (at_run and model.builder_checks):
        model.check(config)
    if input_names is not None and model.check_inputs is not None:
        model.check_inputs(config, input_names)
    if require_complete and model.completeness is not None:
        gaps = model.completeness(config, input_names)
        if gaps:
            raise NodeConfigError(gaps[0].message, setting=gaps[0].path)
    return dict(config)


def config_gaps(
    node_type: NodeType | str,
    config: Mapping[str, Any],
    *,
    input_names: Sequence[str] | None = None,
) -> list[Any]:
    """What a valid but unfinished *config* lacks before its node can run."""
    nt = NodeType(node_type) if not isinstance(node_type, NodeType) else node_type
    completeness = NODE_CONFIG_MODELS[nt].completeness
    return [] if completeness is None else completeness(config, input_names)


def setting_path(location: Sequence[str | int]) -> str:
    """A Pydantic error location as a config path: ``factors[0].banding``."""
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        elif path:
            path += f".{part}"
        else:
            path = str(part)
    return path


def _config_location(config: Any, location: Sequence[str | int]) -> list[str | int]:
    """The parts of a Pydantic error location that are keys and indices of *config*.

    A union's member labels (a tagged union's tag, ``list[dict[str,any]]``) are
    not in the config, so walking the value drops them.
    """
    parts: list[str | int] = []
    value = config
    for part in location:
        if isinstance(value, Mapping) and isinstance(part, str) and part in value:
            parts.append(part)
            value = value[part]
        elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
            parts.append(part)
            value = value[part]
        elif not isinstance(value, Mapping | list):
            break
        elif isinstance(value, Mapping) and isinstance(part, str) and not _is_union_label(part):
            # A key the value lacks (a required one) or holds without declaring.
            parts.append(part)
            break
    return parts


def _is_union_label(part: str) -> bool:
    return "[" in part or "-" in part or part[:1].isupper() or part in _UNION_TAGS


_UNION_TAGS = frozenset({"file", "database", "lakehouse", "databricks", "inline"})


def _shape_error(
    nt: NodeType, label: str, config: Mapping[str, Any], exc: ValidationError
) -> NodeConfigError:
    errors = exc.errors(include_url=False)
    # A union reports one error per branch: the deepest names the value the author
    # meant (a contract's column, not its "opaque" spelling; a map's key, not the list
    # it might have been), and the branches refused at that value, as deep, together
    # say what it may be.
    first = max(errors, key=lambda error: len(error["loc"]))
    model = NODE_CONFIG_MODELS[nt]
    if first["type"] in {"union_tag_not_found", "union_tag_invalid"} and model.discriminant:
        setting = model.discriminant
        reason = first["msg"]
    else:
        location = _config_location(config, first["loc"])
        setting = setting_path(location)
        reason = _union_reason(
            [
                error
                for error in errors
                if len(error["loc"]) == len(first["loc"])
                and _config_location(config, error["loc"]) == location
            ]
        )
    where = f" setting {setting!r}" if setting else ""
    return NodeConfigError(f"Node {label!r} ({nt.value}){where}: {reason}.", setting=setting)


def _union_reason(errors: Sequence[Any]) -> str:
    """One reason for the errors at one value: a union's branches as alternatives."""
    messages = list(dict.fromkeys(str(error["msg"]) for error in errors))
    prefix = "Input should be "
    if len(messages) > 1 and all(message.startswith(prefix) for message in messages):
        options = [message.removeprefix(prefix) for message in messages]
        return prefix + ", ".join(options[:-1]) + " or " + options[-1]
    return messages[0]


def _without_deploy_private_keys(nt: NodeType, config: Mapping[str, Any]) -> Mapping[str, Any]:
    """*config* without the deploy scorer's private transport keys its node type may carry.

    Each is checked: a list of distinct non-empty column names, or the
    answer-per-row flag, which is ``True``.
    """
    private = DEPLOY_PRIVATE_CONFIG_KEYS.get(nt, frozenset())
    held = private.intersection(config)
    if not held:
        return config
    for key in held:
        value = config[key]
        if key == DEPLOY_ANSWER_PER_ROW_CONFIG_KEY:
            valid = value is True
        else:
            valid = (
                isinstance(value, list)
                and all(isinstance(column, str) and column for column in value)
                and len(set(value)) == len(value)
            )
        if not valid:
            raise ConfigError(
                f"{nt.value} node has invalid internal deploy config {key!r}.", config_key=key
            )
    return {key: value for key, value in config.items() if key not in held}


def validate_graph_configs(graph: PipelineGraph) -> None:
    """Every node config of *graph* and its embedded submodel graphs, held to its model.

    For a route that answers with a graph it transformed rather than saved: a
    response never carries a config its model's keys and shape refuse.
    """
    for node in graph.nodes:
        validate_node_config(
            node.data.nodeType,
            node.data.config,
            require_complete=False,
            node_label=node.data.label,
            shape_only=True,
        )
    for definition in (graph.submodels or {}).values():
        validate_graph_configs(definition.graph)
