"""The capability manifest: the node, operation and recipe vocabulary of the assistant.

Node descriptors derive their mechanical facts from the same registries that
validate and save a pipeline.  The only hand-authored part is the short usage
note for each node type.  That gives the model useful authoring guidance
without creating a second source of truth for node names, config keys,
decorators, sidecar folders, or singleton rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType, UnionType
from typing import Literal, Required, Union, cast, get_args, get_origin, get_type_hints

from haute._cache import canonical_json
from haute._config_io import NODE_TYPE_TO_FOLDER, palette_default_config
from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE, VALID_KEYS
from haute._polars_steps import (
    AGGREGATIONS,
    BINARY_OPERATORS,
    CAST_DTYPES,
    FILL_STRATEGIES,
    FUNCTIONS,
    JOIN_HOW,
    JOIN_VALIDATE,
    LITERAL_TYPES,
    OPERATORS,
    PIVOT_AGGREGATIONS,
    STEPPED_NODE_TYPES,
    WINDOW_AGGREGATIONS,
    step_fields,
    stepped_surface_for,
)
from haute._standalone_nodes import SOURCE_NODE_TYPES, STANDALONE_PASSTHROUGH_TYPES
from haute._types import NODE_TYPE_TO_DECORATOR, SINK_ONLY_NODE_TYPES, NodeType
from haute.assistant._node_cards import node_card
from haute.assistant._recipes import recipe_manifest
from haute.assistant._wire_ops import MAX_DECLARED_POSTCONDITIONS, graph_edit_operations_schema
from haute.routes._save_pipeline import _SINGLETON_NODE_TYPES
from haute.schemas import ASSISTANT_MAX_ASSUMPTIONS, ASSISTANT_RECEIPT_TEXT_LIMIT

# The save service is the authority for singleton policy.  Keep this derived
# rather than repeating the node list here: a new singleton must be visible to
# both save validation and the capability manifest in the same change.
_SINGLETON_TYPES = frozenset(node_type for node_type, _label in _SINGLETON_NODE_TYPES)


# Usage notes are the manifest's intentionally hand-authored knowledge.  Every
# current NodeType is listed explicitly so adding a NodeType without adding a
# corresponding note leaves the manifest incomplete and fails at import time.
_USAGE_NOTES: dict[NodeType, str] = {
    NodeType.API_INPUT: (
        "Declare the request contract and its input tables; use this as the "
        "pipeline's external quote boundary."
    ),
    NodeType.DATA_INPUT: (
        "Read a file, database, lakehouse, Databricks table, or inline records through "
        "an explicit provider and format; use a snapshot for remote or eager-only inputs."
    ),
    NodeType.DATA_OUTPUT: (
        "Write or sink its one input frame to a file, database or lakehouse target "
        "when the analyst runs the output; it has no output, so it ends its branch "
        "and stays out of the scoring path."
    ),
    NodeType.POLARS: (
        "Apply a Polars transform to connected inputs; each input is named by its "
        "upstream node, and new logic is written as steps."
    ),
    NodeType.EDGE_JOIN: (
        "Join the base input with a connected input; specify the join keys and "
        "join type explicitly, especially when the two key names differ."
    ),
    NodeType.MODEL_SCORE: (
        "Score rows with a saved model selected by run or registered-model "
        "metadata; configure the feature contract and prediction output deliberately."
    ),
    NodeType.BANDING: (
        "Band a number or date column at breakpoints, or map categorical values to "
        "named bands. Breakpoint rules are {boundary, label} rows: each boundary is "
        "its band's upper bound (inclusive unless rightClosed is false), every "
        "boundary is a number, a date, or a date and time of one kind, and one "
        "final row may leave the boundary empty for the open-ended band. "
        "Categorical rules are {value, assignment} rows. Give each factor an "
        "outputColumn and an explicit default for values outside the rules."
    ),
    NodeType.RATING_STEP: (
        "Rate its first input: look up rating factors from one or more tables and "
        "combine their outputs with the chosen operation; make table factors and "
        "miss policy explicit."
    ),
    NodeType.OUTPUT: (
        "Assemble the top-level JSON response from selected upstream columns "
        "with an explicit outputMapping whose source_port names the incoming "
        "frame; it has no output edge."
    ),
    NodeType.EXPLORE: (
        "Summarise, pivot and chart its one input frame for analysis; it has no "
        "output, so it ends an analysis branch and is never a pricing stage."
    ),
    NodeType.EXTERNAL_FILE: (
        "Load a pickle, JSON, joblib or CatBoost file and expose the loaded object "
        "as `obj`; the first input is `df` and further inputs are named by their "
        "edges. Without steps or code the node returns its first input unchanged. "
        "Set fileType, and modelClass for a CatBoost file."
    ),
    NodeType.LIVE_SWITCH: (
        "Route one of several connected frames by scenario; use at most one "
        "switch per pipeline and name the scenario map unambiguously."
    ),
    NodeType.MODELLING: (
        "Train a model from its one input frame; configure target, algorithm, "
        "task, features, and evaluation settings. It has no output, so nothing is "
        "wired downstream of it and training stays outside the live quote path."
    ),
    NodeType.OPTIMISER: (
        "Optimise prices under an objective and constraints: choose mode online or "
        "ratebook, map the quote_id, scenario_index, scenario_value and objective "
        "columns, and name the frame to optimise with data_input when several "
        "inputs are connected (and the Banding input with banding_source in "
        "ratebook mode). The solve runs from the editor and saves a result that "
        "Apply Optimisation reads; the node has no output."
    ),
    NodeType.SCENARIO_EXPANDER: (
        "Repeat each row once per scenario step: stepCount (required, at least 1) "
        "sets how many; step_column names the 0-based index column (default "
        "scenario_index); column_name, when set, adds a value column spaced evenly "
        "from min_value to max_value; quote_id names the column identifying each quote."
    ),
    NodeType.OPTIMISER_APPLY: (
        "Apply a saved optimisation result from a file, run or registered model; a "
        "ratebook result applies to the input named by ratebook_input and any other "
        "to the first input. Keep the version and optimised-value column conventions."
    ),
    NodeType.CONSTANT: (
        "Create a one-row frame of named literal values for defaults or lookup "
        "inputs; use values entries with stable names rather than hidden literals in code."
    ),
    NodeType.SUBMODEL: (
        "Reference a separate pipeline module as a top-level graph boundary; "
        "connect only its declared input and output ports, never its internal nodes."
    ),
    NodeType.SUBMODEL_PORT: (
        "Use only for the structural ports of a submodel boundary; it has no "
        "user config or decorator and must not be edited as an ordinary transform."
    ),
}


# The palette's display names (``NODE_TYPE_META`` in
# ``frontend/src/utils/nodeTypes.ts``), held equal to the editor by test, so
# the model and the analyst call each node by the same name.
_DISPLAY_NAMES: dict[NodeType, str] = {
    NodeType.API_INPUT: "Quote Input",
    NodeType.DATA_INPUT: "Data Input",
    NodeType.DATA_OUTPUT: "Data Output",
    NodeType.POLARS: "Polars",
    NodeType.EDGE_JOIN: "Edge Join",
    NodeType.MODEL_SCORE: "Model Scoring",
    NodeType.BANDING: "Banding",
    NodeType.RATING_STEP: "Rating Step",
    NodeType.OUTPUT: "Quote Response",
    NodeType.EXPLORE: "Explore",
    NodeType.EXTERNAL_FILE: "Load File",
    NodeType.LIVE_SWITCH: "Source Switch",
    NodeType.MODELLING: "Model Training",
    NodeType.OPTIMISER: "Optimisation",
    NodeType.SCENARIO_EXPANDER: "Expander",
    NodeType.OPTIMISER_APPLY: "Apply Optimisation",
    NodeType.CONSTANT: "Constant",
    NodeType.SUBMODEL: "Submodel",
    NodeType.SUBMODEL_PORT: "Port",
}


# One-line purposes shown beside each palette name in the prompt's node index.
_SUMMARIES: dict[NodeType, str] = {
    NodeType.API_INPUT: "The live quote request, one frame per declared request table.",
    NodeType.DATA_INPUT: "Read a file, database, lakehouse, Databricks table or inline records.",
    NodeType.DATA_OUTPUT: "Write a frame to a file, database or lakehouse when the output is run.",
    NodeType.POLARS: "Transform one or more frames with Polars steps.",
    NodeType.EDGE_JOIN: "Join a base frame with a lookup frame on keys.",
    NodeType.MODEL_SCORE: "Score rows with a saved model.",
    NodeType.BANDING: "Group number, date or categorical values into named bands.",
    NodeType.RATING_STEP: "Look up rating factors from tables and combine them.",
    NodeType.OUTPUT: "Assemble the quote's JSON response from upstream columns.",
    NodeType.EXPLORE: "Analyse an upstream frame with summaries, pivots and charts.",
    NodeType.EXTERNAL_FILE: "Load a pickle, JSON, joblib or CatBoost file for use in steps.",
    NodeType.LIVE_SWITCH: "Route the live request or a batch source by scenario.",
    NodeType.MODELLING: "Train a gradient boosting, EBM or GLM model.",
    NodeType.OPTIMISER: "Optimise prices under an objective and constraints.",
    NodeType.SCENARIO_EXPANDER: "Repeat each row across a grid of scenario values.",
    NodeType.OPTIMISER_APPLY: "Apply a saved optimisation result to price rows.",
    NodeType.CONSTANT: "A one-row frame of named constant values.",
    NodeType.SUBMODEL: "An occurrence of a reusable sub-pipeline.",
    NodeType.SUBMODEL_PORT: "A submodel's structural input or output port.",
}


# ASSIST-A04 capability manifest -------------------------------------------------
MANIFEST_SCHEMA_VERSION = "1.0"
_MANIFEST_CACHE: dict[tuple[str, str], CapabilityManifest] = {}


def _json_value_schema() -> dict[str, object]:
    """The permissive JSON value used for parser/executor universal fields."""
    return {"type": ["string", "number", "integer", "boolean", "object", "array", "null"]}


def _schema_for_annotation(annotation: object) -> dict[str, object]:
    """Resolve the useful JSON Schema subset of the config TypedDict vocabulary."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Required:
        return _schema_for_annotation(args[0])
    if origin is Literal:
        values = list(args)
        return {"const": values[0]} if len(values) == 1 else {"enum": values}
    if origin in (Union, UnionType):
        return {"anyOf": [_schema_for_annotation(value) for value in args]}
    if origin is list:
        return {
            "type": "array",
            "items": _schema_for_annotation(args[0]) if args else _json_value_schema(),
        }
    if origin in (dict, Mapping):
        return {
            "type": "object",
            "additionalProperties": _schema_for_annotation(args[1])
            if len(args) > 1
            else _json_value_schema(),
        }
    if isinstance(annotation, type) and hasattr(annotation, "__required_keys__"):
        return _schema_for_typeddict(annotation)
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is type(None):
        return {"type": "null"}
    return _json_value_schema()


def _schema_for_typeddict(typed_dict: type, *, keys: set[str] | None = None) -> dict[str, object]:
    hints = get_type_hints(typed_dict, include_extras=True)
    selected = set(hints) if keys is None else keys
    properties = {
        key: _schema_for_annotation(hints[key]) for key in sorted(selected) if key in hints
    }
    result: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    # With postponed annotations, TypedDict.__required_keys__ can be empty on
    # CPython even when the source declares Required[T].  Read the resolved
    # annotation as the authority so discriminated I/O alternatives retain
    # their actual required contract.
    required = sorted(
        key
        for key, annotation in hints.items()
        if key in selected
        and (
            key in set(getattr(typed_dict, "__required_keys__", ()))
            or get_origin(annotation) is Required
        )
    )
    if required:
        result["required"] = required
    return result


def _enum_values(schema: Mapping[str, object]) -> list[object] | None:
    """The closed values a schema allows, or None when it is open."""
    if "const" in schema:
        return [schema["const"]]
    if "enum" in schema:
        return list(cast(list[object], schema["enum"]))
    return None


def _merge_branch_schemas(schemas: list[Mapping[str, object]]) -> Mapping[str, object]:
    """One top-level property for a key several I/O branches declare.

    Closed values merge into one enum in branch order; one open branch leaves
    the merged property open, listing each distinct alternative.
    """
    closed = [_enum_values(schema) for schema in schemas]
    if all(values is not None for values in closed):
        merged = list(dict.fromkeys(value for values in closed for value in values or ()))
        return {"const": merged[0]} if len(merged) == 1 else {"enum": merged}
    distinct: list[Mapping[str, object]] = []
    for schema in schemas:
        if schema not in distinct:
            distinct.append(schema)
    return distinct[0] if len(distinct) == 1 else {"anyOf": distinct}


def _config_schema(node_type: NodeType) -> dict[str, object]:
    """Closed top-level schema matching VALID_KEYS, with I/O branch detail."""
    allowed = set(VALID_KEYS.get(node_type, ()))
    if node_type in (NodeType.DATA_INPUT, NodeType.DATA_OUTPUT):
        from haute._types import DATA_INPUT_CONFIG_TYPES, DATA_OUTPUT_CONFIG_TYPES

        branches = (
            DATA_INPUT_CONFIG_TYPES
            if node_type is NodeType.DATA_INPUT
            else DATA_OUTPUT_CONFIG_TYPES
        )
        branch_schemas = [_schema_for_typeddict(branch, keys=allowed) for branch in branches]
        declared: dict[str, list[Mapping[str, object]]] = {}
        branch_required: list[set[str]] = []
        for branch in branch_schemas:
            branch_properties = branch["properties"]
            if not isinstance(branch_properties, Mapping):
                raise TypeError("Derived config schema properties must be a mapping")
            for key, value in branch_properties.items():
                declared.setdefault(key, []).append(cast(Mapping[str, object], value))
            branch_required.append(set(cast(list[str], branch.get("required", []))))
        properties: dict[str, object] = {
            key: _merge_branch_schemas(schemas) for key, schemas in declared.items()
        }
        # Universal keys do not belong to individual I/O alternatives.
        for key in allowed:
            properties.setdefault(key, _json_value_schema())
        schema: dict[str, object] = {
            "type": "object",
            "properties": {key: properties[key] for key in sorted(allowed)},
            "additionalProperties": False,
            "oneOf": branch_schemas,
        }
        if required_everywhere := set.intersection(*branch_required):
            schema["required"] = sorted(required_everywhere)
        return schema
    typed_dict = _TYPED_DICT_BY_NODE_TYPE.get(node_type)
    if typed_dict is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    schema = _schema_for_typeddict(typed_dict, keys=allowed)
    raw_properties = schema["properties"]
    if not isinstance(raw_properties, dict):
        raise TypeError("Derived config schema properties must be a dictionary")
    typed_properties: dict[str, object] = raw_properties
    for key in allowed:
        typed_properties.setdefault(key, _json_value_schema())
    schema["properties"] = {key: typed_properties[key] for key in sorted(allowed)}
    return schema


def _freeze(value: object) -> object:
    """Recursively freeze cached material without changing its JSON shape."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    """Return ordinary JSON-compatible containers from frozen descriptor material."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def materialise_json(value: object) -> object:
    """Copy immutable manifest material into ordinary JSON containers."""

    return _thaw(value)


def _schema_enums(schema: Mapping[str, object]) -> dict[str, object]:
    properties = schema["properties"]
    if not isinstance(properties, Mapping):
        raise TypeError("Derived config schema properties must be a mapping")
    return {
        key: value["enum"] if "enum" in value else value["const"]
        for key, value in properties.items()
        if isinstance(value, Mapping) and ("enum" in value or "const" in value)
    }


@dataclass(frozen=True, slots=True)
class NodeCapabilityDescriptor:
    id: str
    display_name: str
    decorator: str | None
    config_schema: Mapping[str, object]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    defaults: Mapping[str, object]
    enum_values: Mapping[str, object]
    conditional_branches: tuple[Mapping[str, object], ...]
    cross_field_constraints: tuple[str, ...]
    config_folder: str | None
    singleton: bool
    sidecar_behavior: str
    summary: str
    ports: Mapping[str, object]
    input_cardinality: str
    wiring_rules: str
    schema_effect: str
    execution: str
    side_effects: str
    usage: str
    anti_patterns: tuple[str, ...]
    examples: tuple[str, ...]
    recipes: tuple[str, ...]
    errors: tuple[Mapping[str, str], ...]
    step_authoring: Mapping[str, object] | None
    card: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        return _thaw({name: getattr(self, name) for name in self.__dataclass_fields__})  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class OperationCapabilityDescriptor:
    id: str
    version: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    state_access: str
    project_state: str
    revision_semantics: str
    risk: str
    egress: str
    side_effects: str
    cost: str
    idempotency: str
    retry: str
    cancellable: bool
    cacheable: bool
    parallel_safe: bool
    concurrency_group: str
    ordering: str
    limits: Mapping[str, int]
    errors: tuple[Mapping[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return _thaw({name: getattr(self, name) for name in self.__dataclass_fields__})  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    schema_version: str
    haute_version: str
    capability_hash: str
    installed_capabilities: Mapping[str, object]
    feature_flags: Mapping[str, bool]
    nodes: tuple[NodeCapabilityDescriptor, ...]
    operations: tuple[OperationCapabilityDescriptor, ...]
    recipes: tuple[Mapping[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return _thaw(
            {
                "schema_version": self.schema_version,
                "haute_version": self.haute_version,
                "capability_hash": self.capability_hash,
                "installed_capabilities": self.installed_capabilities,
                "feature_flags": self.feature_flags,
                "nodes": [node.as_dict() for node in self.nodes],
                "operations": [operation.as_dict() for operation in self.operations],
                "recipes": [_thaw(recipe) for recipe in self.recipes],
            }
        )  # type: ignore[return-value]


def _installed_capabilities() -> dict[str, object]:
    from haute._polars_io_registry import registry_capabilities

    return {"io": registry_capabilities()}


def _haute_version() -> str:
    try:
        return version("haute")
    except PackageNotFoundError:
        return "0.0.0-dev"


_TRAINING_NODE_TYPES = frozenset({NodeType.MODELLING, NodeType.OPTIMISER})
# Each non-source node type's input cardinality is declared in exactly one of
# these sets or special-cased below; a type in none fails loudly at import.
# The single-input set is held equal to the palette's ``maxInputs: 1`` entries
# (``frontend/src/utils/nodeTypes.ts``) by test.
_SINGLE_INPUT_TYPES = frozenset(
    {
        NodeType.DATA_OUTPUT,
        NodeType.EXPLORE,
        NodeType.BANDING,
        NodeType.SCENARIO_EXPANDER,
        NodeType.RATING_STEP,
        NodeType.MODELLING,
        NodeType.MODEL_SCORE,
    }
)
_MULTI_INPUT_NODE_TYPES = frozenset(
    {
        NodeType.POLARS,
        NodeType.OUTPUT,
        NodeType.LIVE_SWITCH,
        NodeType.OPTIMISER,
        NodeType.OPTIMISER_APPLY,
        NodeType.SUBMODEL,
    }
)
_EXAMPLE_IDS: dict[NodeType, tuple[str, ...]] = {
    NodeType.API_INPUT: ("minimal_live_quote",),
    NodeType.DATA_INPUT: ("minimal_batch",),
    NodeType.BANDING: ("discrete_banding",),
    NodeType.EDGE_JOIN: ("reference_join",),
    NodeType.RATING_STEP: ("rating_step",),
}
_RECIPE_IDS: dict[NodeType, tuple[str, ...]] = {
    NodeType.BANDING: ("categorical_banding",),
    NodeType.EDGE_JOIN: ("reference_join",),
    NodeType.OUTPUT: ("response_output",),
    NodeType.RATING_STEP: ("rating_step",),
}


_MULTI_INPUT_PORTS: dict[NodeType, str] = {
    NodeType.POLARS: "one or more frames, each named by its edge",
    NodeType.OUTPUT: "one or more frames, each addressed by a mapping row's source_port",
    NodeType.OPTIMISER: (
        "one or more frames; data_input names the frame to optimise when several are connected"
    ),
    NodeType.OPTIMISER_APPLY: (
        "one or more frames; a ratebook result applies to the input named by "
        "ratebook_input, any other result to the first input"
    ),
}


def _node_ports(node_type: NodeType) -> dict[str, object]:
    outputs: object = [] if node_type in SINK_ONLY_NODE_TYPES else ["frame"]
    if node_type is NodeType.API_INPUT:
        return {
            "inputs": [],
            "outputs": "one named output per declared request table",
        }
    if node_type is NodeType.LIVE_SWITCH:
        return {"inputs": "one per configured scenario", "outputs": outputs}
    if node_type is NodeType.SUBMODEL:
        return {
            "inputs": "declared submodel input ports",
            "outputs": "declared submodel output ports",
        }
    if node_type is NodeType.SUBMODEL_PORT:
        return {"inputs": "structural only", "outputs": "structural only"}
    if node_type is NodeType.EDGE_JOIN:
        return {"inputs": ["base", "join"], "outputs": outputs}
    if node_type is NodeType.EXTERNAL_FILE:
        return {
            "inputs": "optional; the first input is `df`, further inputs are named by their edges",
            "outputs": outputs,
        }
    if node_type in SOURCE_NODE_TYPES:
        return {"inputs": [], "outputs": outputs}
    if node_type in _MULTI_INPUT_PORTS:
        return {"inputs": _MULTI_INPUT_PORTS[node_type], "outputs": outputs}
    return {"inputs": ["frame"], "outputs": outputs}


def _input_cardinality(node_type: NodeType) -> str:
    if node_type in SOURCE_NODE_TYPES:
        return "zero"
    if node_type is NodeType.EDGE_JOIN:
        return "exactly two"
    if node_type is NodeType.EXTERNAL_FILE:
        return "zero or more"
    if node_type is NodeType.SUBMODEL_PORT:
        return "structural boundary; not directly wireable"
    if node_type in _SINGLE_INPUT_TYPES:
        return "exactly one"
    if node_type in _MULTI_INPUT_NODE_TYPES:
        return "one or more, subject to the descriptor configuration"
    raise RuntimeError(f"No input cardinality is declared for node type {node_type.value!r}.")


def _schema_effect(node_type: NodeType) -> str:
    effects = {
        NodeType.API_INPUT: "emits the declared request-table schemas",
        NodeType.DATA_INPUT: "emits the selected source schema",
        NodeType.DATA_OUTPUT: "writes its input schema to the destination",
        NodeType.POLARS: "derives the schema from validated Polars expressions",
        NodeType.EDGE_JOIN: "combines base and reference columns under join suffix/coalesce rules",
        NodeType.MODEL_SCORE: "adds the configured prediction output and optional post-processing",
        NodeType.BANDING: "adds each configured factor output column",
        NodeType.RATING_STEP: "adds table-factor and combined-output columns to its first input",
        NodeType.OUTPUT: "projects mapped columns into the declared JSON response",
        NodeType.EXPLORE: "reads its input for summaries, pivots and charts",
        NodeType.EXTERNAL_FILE: "the steps or code see the loaded object as `obj`",
        NodeType.LIVE_SWITCH: "requires compatible schemas across selected scenarios",
        NodeType.MODELLING: "reads its input to train; model artifacts are written out of band",
        NodeType.OPTIMISER: (
            "reads its inputs to solve; the optimisation result is saved for Apply Optimisation"
        ),
        NodeType.SCENARIO_EXPANDER: (
            "repeats rows stepCount times, adding the step index and optional value columns"
        ),
        NodeType.OPTIMISER_APPLY: "adds the configured version and optimised-value columns",
        NodeType.CONSTANT: "emits one row with the configured literal columns",
        NodeType.SUBMODEL: "exposes the declared schemas of its output ports",
        NodeType.SUBMODEL_PORT: "carries its enclosing submodel port schema",
    }
    effect = effects[node_type]
    if node_type in SINK_ONLY_NODE_TYPES:
        return f"no downstream frame; {effect}"
    if node_type in STANDALONE_PASSTHROUGH_TYPES:
        return f"returns its first input unless its steps or code transform it; {effect}"
    return effect


def _execution_class(node_type: NodeType) -> tuple[str, str]:
    if node_type in _TRAINING_NODE_TYPES:
        return (
            "explicit long-running local computation; unavailable to ordinary assistant tools",
            "creates model or optimisation artifacts only through owning services",
        )
    if node_type is NodeType.DATA_OUTPUT:
        return (
            "explicit output execution; unavailable to ordinary assistant tools",
            "writes to the configured destination",
        )
    if node_type in SOURCE_NODE_TYPES:
        return ("lazy/local source resolution", "reads declared project or artifact state")
    if node_type is NodeType.EXTERNAL_FILE:
        return ("lazy pipeline execution; the file loads when the node runs", "reads the file")
    if node_type in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}:
        return ("structural graph expansion", "none")
    return ("lazy pipeline execution", "none until an owning execution surface materialises it")


_WIRING_EXTRAS: dict[NodeType, str] = {
    NodeType.OPTIMISER: (
        "With several inputs, data_input names the frame to optimise and, in ratebook "
        "mode, banding_source names the Banding input."
    ),
    NodeType.OPTIMISER_APPLY: (
        "A ratebook result applies to the input named by ratebook_input; any other "
        "result applies to the first input."
    ),
    NodeType.OUTPUT: "Each outputMapping row's source_port names one incoming frame.",
}


def _wiring_rules(node_type: NodeType) -> str:
    if node_type is NodeType.EDGE_JOIN:
        return (
            'Connect the primary input with target_handle="base" and the lookup '
            'input with target_handle="join"; exactly one incoming edge of each '
            "role is required."
        )
    rules: list[str] = []
    if node_type in SOURCE_NODE_TYPES:
        rules.append("Takes no incoming edges.")
    elif node_type is NodeType.EXTERNAL_FILE:
        rules.append(
            "Incoming edges are optional: the first input is `df` and further inputs "
            "are named by their edges."
        )
    elif node_type in _SINGLE_INPUT_TYPES:
        rules.append("Takes exactly one incoming edge.")
    if node_type in SINK_ONLY_NODE_TYPES:
        rules.append("It has no output: never wire an edge out of it; it ends its branch.")
    if node_type in _WIRING_EXTRAS:
        rules.append(_WIRING_EXTRAS[node_type])
    rules.append("Use only declared ports and preserve top-level submodel boundaries.")
    return " ".join(rules)


#: Stands for the incoming edge a Transform's source step starts from.
EDGE_NAME_PLACEHOLDER = "<edge name>"
#: The free-code card the descriptors and the system prompt show: it opens with
#: its one-line intent, which the step builder shows as the card's title, and
#: names no column, so it applies unchanged on every stepped surface.
NEW_LOGIC_EXAMPLE_CODE = "# Add a unit exposure column\ndf = df.with_columns(exposure=pl.lit(1.0))"


def new_logic_steps(node_type: NodeType, code: str) -> list[dict[str, str]]:
    """The step list that writes new logic *code* on *node_type*'s stepped surface.

    A surface whose steps choose their input starts from a source step on the
    incoming edge; every other surface binds ``df`` itself, so the list is one
    free-code card.
    """

    logic = {"id": "logic", "kind": "free_code", "code": code}
    if stepped_surface_for(node_type).start == "input":
        return [{"id": "start", "kind": "source", "input": EDGE_NAME_PLACEHOLDER}, logic]
    return [logic]


def _step_authoring(node_type: NodeType) -> dict[str, object] | None:
    """How *node_type*'s steps start, what they see and how new logic is written."""

    surface = STEPPED_NODE_TYPES.get(node_type)
    if surface is None:
        return None
    if surface.start == "input":
        rules = ["Start with a source step whose input names the incoming edge that becomes df."]
    elif surface.inputs == "edges":
        rules = ["df is already the first input, so there is no source step."]
    else:
        rules = ["df is already the frame the node produced, so there is no source step."]
    rules.append(
        "Code reads the other inputs by their edge names."
        if surface.inputs == "edges"
        else "Code sees only df."
    )
    rules.append(
        "Write new logic as one free_code step whose code starts with a one-line "
        "`# intent` comment and assigns the transformed result to df."
    )
    if surface.start == "frame":
        rules.append("With no post-processing to do, keep steps: [].")
    rules.append(
        "Change existing steps with edit_steps by step id; steps it does not name keep "
        "their ids and order."
    )
    return {
        "start": surface.start,
        "inputs": surface.inputs,
        "rule": " ".join(rules),
        "new_logic": new_logic_steps(node_type, NEW_LOGIC_EXAMPLE_CODE),
    }


def step_grammar() -> dict[str, object]:
    """The structured step grammar, read from the step renderer's own tables.

    Nested expression shapes are not enumerated; the renderer's errors name
    the offending field.
    """

    return {
        "kinds": step_fields(),
        "operators": list(OPERATORS),
        "binary_operators": list(BINARY_OPERATORS),
        "aggregations": list(AGGREGATIONS),
        "window_aggregations": list(WINDOW_AGGREGATIONS),
        "pivot_aggregations": list(PIVOT_AGGREGATIONS),
        "join_how": list(JOIN_HOW),
        "join_validate": list(JOIN_VALIDATE),
        "cast_dtypes": list(CAST_DTYPES),
        "fill_strategies": list(FILL_STRATEGIES),
        "functions": {name: list(arguments) for name, (arguments, _template) in FUNCTIONS.items()},
        "literal_types": list(LITERAL_TYPES),
    }


def _node_descriptor(node_type: NodeType) -> NodeCapabilityDescriptor:
    config_folder = NODE_TYPE_TO_FOLDER.get(node_type)
    usage = _USAGE_NOTES[node_type]
    schema = _config_schema(node_type)
    raw_required = schema.get("required", ())
    if not isinstance(raw_required, list | tuple):
        raise TypeError("Derived config schema required fields must be a sequence")
    required = tuple(str(field) for field in raw_required)
    raw_properties = schema["properties"]
    if not isinstance(raw_properties, Mapping):
        raise TypeError("Derived config schema properties must be a mapping")
    fields = tuple(sorted(str(field) for field in raw_properties))
    raw_branches = schema.get("oneOf", ())
    if not isinstance(raw_branches, list | tuple) or any(
        not isinstance(branch, Mapping) for branch in raw_branches
    ):
        raise TypeError("Derived config schema branches must be mappings")
    branches = tuple(
        MappingProxyType(dict(cast(Mapping[str, object], branch))) for branch in raw_branches
    )
    branch_constraints = (
        "Choose exactly one input/output type branch and provide its required fields."
        if branches
        else "Configuration keys must satisfy the closed schema."
    )
    execution, side_effects = _execution_class(node_type)
    anti_patterns = [
        "Do not invent config keys or bypass graph wiring.",
        "Do not add disconnected decorative nodes; every new node must be wired.",
    ]
    if node_type == NodeType.POLARS:
        anti_patterns.append("Do not discard immutable Polars results; assign them to df.")
        anti_patterns.append(
            "Do not write new logic as code: start the steps with a source step naming "
            "the input edge, which binds df, then transform df in a free_code step."
        )
        anti_patterns.append(
            "When editing the code of a node already in code mode (it has no steps), do "
            "not read df before assigning it: there df is only the output, so start from "
            "an input by name (df = claims.filter(...))."
        )
    if node_type == NodeType.EDGE_JOIN:
        anti_patterns.append("Do not omit or duplicate edgeJoin target_handle roles.")
    if node_type == NodeType.EXPLORE:
        anti_patterns.append(
            "An analyst's pivot table is a pivots entry on the Explore node, never a step; "
            "Explore steps only shape the frame before it is explored."
        )
    return NodeCapabilityDescriptor(
        node_type.value,
        _DISPLAY_NAMES[node_type],
        NODE_TYPE_TO_DECORATOR.get(node_type),
        cast(Mapping[str, object], _freeze(schema)),
        required,
        tuple(key for key in fields if key not in required),
        cast(Mapping[str, object], _freeze(palette_default_config(node_type))),
        cast(Mapping[str, object], _freeze(_schema_enums(schema))),
        branches,
        (branch_constraints,),
        config_folder,
        node_type in _SINGLETON_TYPES,
        (
            "Persisted in the canonical sidecar folder."
            if config_folder
            else "Inline graph configuration."
        ),
        _SUMMARIES[node_type],
        cast(Mapping[str, object], _freeze(_node_ports(node_type))),
        _input_cardinality(node_type),
        _wiring_rules(node_type),
        _schema_effect(node_type),
        execution,
        side_effects,
        usage,
        tuple(anti_patterns),
        _EXAMPLE_IDS.get(node_type, ()),
        _RECIPE_IDS.get(node_type, ()),
        (
            _freeze(
                {
                    "code": "invalid_config",
                    "remediation": "Inspect the descriptor schema and correct the graph edit.",
                }
            ),
        ),  # type: ignore[arg-type]
        cast(Mapping[str, object] | None, _freeze(_step_authoring(node_type))),
        cast(Mapping[str, object], _freeze(node_card(node_type))),
    )


def _closed_object(
    properties: Mapping[str, object] | None = None, required: list[str] | None = None
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": "object",
        "properties": dict(properties or {}),
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def _postconditions_schema() -> dict[str, object]:
    node = {"type": "string", "minLength": 1}
    handle = {"type": ["string", "null"]}
    variants = [
        _closed_object(
            {"kind": {"enum": ["node_exists", "node_absent"]}, "node": node},
            ["kind", "node"],
        ),
        _closed_object(
            {
                "kind": {"enum": ["edge_exists", "edge_absent"]},
                "source": node,
                "target": node,
                "source_handle": handle,
                "target_handle": handle,
            },
            ["kind", "source", "target"],
        ),
        _closed_object(
            {
                "kind": {"const": "graph_shape"},
                "nodes": {"type": "integer", "minimum": 0},
                "edges": {"type": "integer", "minimum": 0},
            },
            ["kind", "nodes", "edges"],
        ),
        _closed_object(
            {
                "kind": {"const": "preamble_digest"},
                "sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            },
            ["kind", "sha256"],
        ),
    ]
    return {
        "type": "array",
        "items": {"oneOf": variants},
        "maxItems": MAX_DECLARED_POSTCONDITIONS,
    }


def _operation_output_schema(name: str) -> dict[str, object]:
    """Return the closed top-level result contract for one tool operation."""

    fields: dict[str, tuple[str, ...]] = {
        "get_pipeline": (
            "name",
            "description",
            "nodes",
            "edges",
            "preamble",
            "singletons",
            "project_revision",
        ),
        "inspect_node": (
            "node",
            "schema",
            "config",
            "profile",
            "withheld",
            "project_revision",
        ),
        "find_data": (
            "datasets",
            "directories",
            "recursive",
            "truncated",
            "schema",
            "project_revision",
        ),
        "get_project_knowledge": (
            "items",
            "excluded_by_policy_count",
            "cache_hit",
            "policy_hash",
            "trust",
            "max_sensitivity",
            "project_revision",
        ),
        "read_reference": ("count", "references"),
        "dry_run_graph_edits": (
            "plan_hash",
            "operations",
            "verification_tier",
            "evidence",
            "warnings",
            "changes",
        ),
        "apply_graph_plan": (
            "plan_hash",
            "applied_operations",
            "verification_tier",
            "evidence",
            "change",
        ),
    }
    common_fields = ("capability_hash", "operation_version")
    fields = {
        operation: tuple(dict.fromkeys((*operation_fields, *common_fields)))
        for operation, operation_fields in fields.items()
    }
    properties = {field: _json_value_schema() for field in fields[name]}
    properties["capability_hash"] = {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    properties["operation_version"] = {"const": "1.0"}
    error_fields = {
        "code",
        "message",
        "kind",
        "id",
        "name",
        "valid_kinds",
        "valid_ids",
        "valid_names",
        "required_sensitivity",
        "max_sensitivity",
        "part",
        "withheld",
        "missing",
        "unknown",
        "argument",
        "recipe_id",
        "validation_path",
        "validation_reason",
        # A committed save whose post-save verification failed.
        "plan_hash",
        "verification_tier",
        "verification_status",
        "verification_error_code",
        "graph_fingerprint",
        "graph_publication_error",
        "warnings",
        "git_sha",
        "applied_operations",
    }
    properties["error"] = _closed_object(
        {
            field: ({"type": "string"} if field in {"code", "message"} else _json_value_schema())
            for field in sorted(error_fields)
        },
        ["code", "message"],
    )
    optional_success_fields = {
        # Each part answers only when the call asked for it and the policy permits it.
        "inspect_node": {"schema", "config", "profile", "withheld"},
        # A file's schema, and the revision its evidence enters, only for a `path`.
        "find_data": {"schema", "project_revision"},
    }
    success_required = [
        field
        for field in fields[name]
        if field not in {"capability_hash", "operation_version"}
        and field not in optional_success_fields.get(name, set())
    ]
    success_variant: dict[str, object] = {
        "required": success_required,
        "not": {"required": ["error"]},
    }
    result = _closed_object(
        properties,
        ["capability_hash", "operation_version"],
    )
    result["oneOf"] = [
        success_variant,
        {"required": ["error"]},
    ]
    return result


def _argument_names(schema: Mapping[str, object], names: Sequence[str]) -> str:
    """Name each argument, with the keys of an array's object items in braces, recursively."""

    properties = cast(Mapping[str, Mapping[str, object]], schema["properties"])
    rendered: list[str] = []
    for name in names:
        items = properties[name].get("items")
        item_properties = items.get("properties") if isinstance(items, Mapping) else None
        if isinstance(items, Mapping) and isinstance(item_properties, Mapping):
            keys = _argument_names(items, [str(key) for key in item_properties])
            rendered.append(f"{name} [{{{keys}}}]")
        else:
            rendered.append(name)
    return ", ".join(rendered)


def _recipe_arguments_description(recipe_id: str, schema: Mapping[str, object]) -> str:
    """One line naming a recipe's arguments, required first.

    The portable projection keeps only the description of a property whose
    schema differs between branches, so this line is how a provider learns
    each recipe's argument names; the `recipe:<id>` reference has the schema.
    """

    required = [str(name) for name in cast(tuple[str, ...], schema["required"])]
    optional = [
        str(name)
        for name in cast(Mapping[str, object], schema["properties"])
        if name not in required
    ]
    text = f"{recipe_id} arguments: {_argument_names(schema, required)}"
    if optional:
        text += f"; optional {_argument_names(schema, optional)}"
    return text + "."


def _recipe_operation_branches(ref_schema: Mapping[str, object]) -> list[dict[str, object]]:
    """One closed `recipe` operation branch per installed recipe.

    `ref` is exactly `add_node`'s, so the provider projection keeps its one
    description for both.
    """

    branches: list[dict[str, object]] = []
    for descriptor in recipe_manifest():
        recipe_id = descriptor.get("id")
        raw_schema = descriptor.get("argument_schema")
        if not isinstance(recipe_id, str) or not isinstance(raw_schema, Mapping):
            raise TypeError("Recipe descriptor has an invalid argument schema")
        if not isinstance(raw_schema.get("properties"), Mapping) or not isinstance(
            raw_schema.get("required"), (list, tuple)
        ):
            raise TypeError("Recipe argument schema must have properties and required fields")
        arguments = cast(dict[str, object], _thaw(raw_schema))
        arguments["description"] = _recipe_arguments_description(recipe_id, raw_schema)
        branches.append(
            _closed_object(
                {
                    "op": {"const": "recipe"},
                    "recipe": {"const": recipe_id},
                    "arguments": arguments,
                    "ref": dict(ref_schema),
                },
                ["op", "recipe", "arguments"],
            )
        )
    return branches


def _graph_edit_operations_schema() -> dict[str, object]:
    """The `dry_run_graph_edits` operation union: the primitive branches, then the recipes."""

    schema = graph_edit_operations_schema()
    items = cast(dict[str, object], schema["items"])
    branches = cast(list[dict[str, object]], items["oneOf"])
    add_node = cast(Mapping[str, Mapping[str, object]], branches[0]["properties"])
    items["oneOf"] = [*branches, *_recipe_operation_branches(add_node["ref"])]
    return schema


#: The provider-visible operations, in the order the provider receives them.
OPERATION_IDS = (
    "get_pipeline",
    "inspect_node",
    "find_data",
    "read_reference",
    "get_project_knowledge",
    "dry_run_graph_edits",
    "apply_graph_plan",
)
#: The parts `inspect_node` can return, in the order it answers them.
INSPECT_NODE_PARTS = ("schema", "config", "profile")
#: Ids one `read_reference` call may name.
MAX_REFERENCE_IDS = 12
#: Each operation's egress class: the most sensitive project material it can send.
_OPERATION_EGRESS = {
    "get_pipeline": "internal-project-metadata",
    # Per part: schema, config, profile. The profile is the one data-reading
    # capability, so its class is distinct and a policy review can see it plainly.
    "inspect_node": "internal-schema-only, restricted-redacted, restricted-value-profile",
    "find_data": "internal-schema-only",
    "read_reference": "none",
    "get_project_knowledge": "policy-filtered-project-content",
    "dry_run_graph_edits": "internal-project-metadata",
    "apply_graph_plan": "internal-project-metadata",
}


def _operation_descriptor(name: str) -> OperationCapabilityDescriptor:
    descriptions = {
        "get_pipeline": "Inspect the saved pipeline graph and its project revision.",
        "inspect_node": (
            "Inspect one saved top-level node in the parts you name (default "
            '["schema"]). "schema": the node\'s output columns and dtypes and the columns '
            "arriving on each of its inputs, keyed by the name the node's own code uses. "
            "A node reporting unresolved_reason 'node_has_no_code' is an empty transform "
            "already wired into the graph and awaiting its code: write that code onto it "
            'with update_node rather than adding a parallel node beside it. "config": the '
            "node's complete saved configuration. \"profile\": the values in the node's "
            "output, or in the input named by `input`: per column, the distinct levels of a "
            "small-cardinality categorical with their counts, or the min/max of a numeric "
            "or date column, alongside a null count and, where the dtype can be counted, a "
            "distinct count. Temporal and decimal bounds are reported in their written form "
            "('2024-01-01', '12.50'). Profile instead of assuming how a column encodes its "
            "categories - a 'fault' or 'status' column may hold Y/N, true/false, or a "
            "description, and the schema alone cannot tell you which. A profile returns no "
            "rows: a value appears only as a distinct level, and a high-cardinality column "
            "is withheld. A part the egress policy does not permit is listed under "
            "`withheld` with the setting it needs."
        ),
        "find_data": (
            "List the safe installed-format data files in one project directory, "
            "optionally recursively, and, when `path` names a data file, also return its "
            "column names and dtypes without reading rows."
        ),
        "get_project_knowledge": (
            "Retrieve bounded policy-filtered project facts and untrusted documentation."
        ),
        "read_reference": (
            "Read packaged library reference by id, one to twelve ids per call: "
            '"guide" (the authoring guide and the structured step grammar: each step '
            'kind\'s fields and the closed vocabularies), "node:<node type id>" (a node '
            "type's complete descriptor; its card holds a minimal and a realistic "
            "configuration with real values and the meaning of each field), "
            '"recipe:<recipe id>" (a recipe\'s full argument schema) and '
            '"example:<example name>" (a teaching example: its narrative and every '
            "node's configuration with its values)."
        ),
        "dry_run_graph_edits": (
            "Validate an exact graph-edit plan without writing. Say in `summary` what the "
            "plan does and list any `assumptions` you made; the analyst sees both on the "
            'change card. An operation {"op": "recipe", "recipe": <recipe id>, '
            '"arguments": {...}} expands into that recipe\'s nodes and edges inside the '
            "same plan; give it a `ref` to address the node it creates from later "
            "operations. Returns the plan hash to apply, the verification tier, an "
            "evidence summary, warnings and the plan's node and edge changes."
        ),
        "apply_graph_plan": (
            "Apply one exact validated plan hash under revision authority. Returns the "
            "change record the analyst sees, built from what was saved."
        ),
    }
    input_schemas = {
        "get_pipeline": _closed_object(),
        "inspect_node": _closed_object(
            {
                "node": {"type": "string"},
                "parts": {
                    "type": "array",
                    "items": {"enum": list(INSPECT_NODE_PARTS)},
                    "minItems": 1,
                    "maxItems": len(INSPECT_NODE_PARTS),
                    "uniqueItems": True,
                    "description": 'The parts to return; omit for ["schema"].',
                },
                "input": {
                    "type": "string",
                    "description": (
                        "Profile this input of the node instead of the node's own "
                        "output, named exactly as the schema part reports it under "
                        "'inputs'. Only with the \"profile\" part."
                    ),
                },
            },
            ["node"],
        ),
        "find_data": _closed_object(
            {
                "directory": {
                    "type": "string",
                    "description": "Project-relative directory to list; omit for the project root.",
                },
                "recursive": {"type": "boolean"},
                "path": {
                    "type": "string",
                    "description": "Project-relative data file whose columns and dtypes to return.",
                },
            }
        ),
        "get_project_knowledge": _closed_object(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
        ),
        "read_reference": _closed_object(
            {
                "ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": MAX_REFERENCE_IDS,
                    "uniqueItems": True,
                },
            },
            ["ids"],
        ),
        "dry_run_graph_edits": _closed_object(
            {
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": ASSISTANT_RECEIPT_TEXT_LIMIT,
                    "description": "One or two plain sentences saying what the plan does.",
                },
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": ASSISTANT_RECEIPT_TEXT_LIMIT,
                    },
                    "maxItems": ASSISTANT_MAX_ASSUMPTIONS,
                    "description": (
                        "Choices you made that the analyst did not state, one sentence each."
                    ),
                },
                "ops": _graph_edit_operations_schema(),
                "postconditions": _postconditions_schema(),
            },
            ["summary", "ops"],
        ),
        "apply_graph_plan": _closed_object(
            {"plan_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
            ["plan_hash"],
        ),
    }
    mutation = name == "apply_graph_plan"
    plan_bound = name == "apply_graph_plan"
    errors = [
        {
            "code": "invalid_request",
            "recovery": "Correct the closed request payload.",
        },
        {
            "code": "operation_failed",
            "recovery": "Inspect the returned details and retry after correction.",
        },
    ]
    if name == "dry_run_graph_edits":
        errors.extend(
            [
                {
                    "code": "invalid_ops",
                    "recovery": "Correct the primitive operation batch and dry-run again.",
                },
                {
                    "code": "invalid_plan",
                    "recovery": "Correct the graph structure or node code and dry-run again.",
                },
                {
                    "code": "schema_unresolvable",
                    "recovery": "Correct the affected node config or code, then dry-run again.",
                },
                {
                    "code": "node_not_ready",
                    "recovery": (
                        "Complete or correct the named Modelling or Load File node, "
                        "then dry-run again."
                    ),
                },
                *(
                    {
                        "code": code,
                        "recovery": (
                            "Correct the recipe operation's arguments as `fix` says; the "
                            "recipe:<id> reference holds its argument schema."
                        ),
                    }
                    for code in ("unknown_recipe", "recipe_argument_invalid", "recipe_plan_invalid")
                ),
            ]
        )
    elif name == "inspect_node":
        errors.append(
            {
                "code": "egress_policy_denied",
                "recovery": (
                    "Ask only for parts the policy permits; `withheld` names the setting "
                    "each denied part needs."
                ),
            }
        )
    elif name == "read_reference":
        errors.append(
            {
                "code": "unknown_reference",
                "recovery": "Use an id from the prompt's indexes; did_you_mean lists close ids.",
            }
        )
    elif name == "apply_graph_plan":
        errors.extend(
            [
                {
                    "code": "plan_not_found",
                    "recovery": "Dry-run the complete operation batch again.",
                },
                {
                    "code": "plan_expired",
                    "recovery": "Dry-run the complete operation batch again.",
                },
                {
                    "code": "plan_store_busy",
                    "recovery": "Wait for in-flight saves to settle, then dry-run again.",
                },
                {
                    "code": "plan_aborted",
                    "recovery": "Dry-run the complete operation batch again before retrying.",
                },
                {
                    "code": "plan_already_applied",
                    "recovery": "Inspect the saved graph before planning any further edit.",
                },
                {
                    "code": "stale_revision",
                    "recovery": "Inspect the saved graph and dry-run a fresh plan.",
                },
                {
                    "code": "stale_project_evidence",
                    "recovery": "Retrieve changed evidence and dry-run a fresh plan.",
                },
                {
                    "code": "authority_denied",
                    "recovery": "Resolve working-branch readiness before applying.",
                },
                {
                    "code": "verification_failed",
                    "recovery": "Inspect or undo the committed save before continuing.",
                },
            ]
        )
    return OperationCapabilityDescriptor(
        name,
        "1.0",
        descriptions[name],
        _freeze(input_schemas[name]),  # type: ignore[arg-type]
        _freeze(_operation_output_schema(name)),  # type: ignore[arg-type]
        "write" if mutation else "read",
        "ready branch required" if mutation else "saved project state",
        (
            "exact base revision and single-use plan hash"
            if plan_bound
            else ("transactional graph revision" if mutation else "snapshot read")
        ),
        "none",
        _OPERATION_EGRESS[name],
        "graph mutation" if mutation else "none",
        "bounded",
        "idempotent" if not mutation else "conditional",
        "never automatic",
        False,
        name == "read_reference",
        not mutation,
        "pipeline-save" if mutation else "assistant-read",
        "ordered" if mutation else "independent",
        _freeze(
            {
                "timeout_seconds": 30,
                "max_operations": 100,
                "max_payload_bytes": 1_000_000,
                "max_context_bytes": 256_000,
            }
        ),  # type: ignore[arg-type]
        tuple(cast(Mapping[str, str], _freeze(error)) for error in errors),
    )


def capability_manifest() -> CapabilityManifest:
    installed = _installed_capabilities()
    nodes = tuple(_node_descriptor(node_type) for node_type in NodeType)
    recipes = recipe_manifest()
    operations = tuple(_operation_descriptor(name) for name in OPERATION_IDS)
    feature_flags = {
        "capability_registry": True,
        "graph_edits": True,
        "revision_safe_plans": True,
        "recipes": True,
    }
    haute_version = _haute_version()
    material: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "haute_version": haute_version,
        "installed_capabilities": installed,
        "feature_flags": feature_flags,
        "nodes": [node.as_dict() for node in nodes],
        "operations": [operation.as_dict() for operation in operations],
        "recipes": [_thaw(recipe) for recipe in recipes],
    }
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    key = (haute_version, digest)
    if key not in _MANIFEST_CACHE:
        _MANIFEST_CACHE[key] = CapabilityManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            haute_version=haute_version,
            capability_hash=digest,
            installed_capabilities=cast(Mapping[str, object], _freeze(installed)),
            feature_flags=MappingProxyType(feature_flags),
            nodes=nodes,
            operations=operations,
            recipes=recipes,
        )
    return _MANIFEST_CACHE[key]


def _clear_manifest_cache() -> None:
    _MANIFEST_CACHE.clear()


def validate_manifest_complete() -> None:
    """Fail loudly if an exported descriptor becomes incomplete or open."""
    for label, table in (
        ("usage note", _USAGE_NOTES),
        ("palette name", _DISPLAY_NAMES),
        ("one-line summary", _SUMMARIES),
    ):
        missing = [node_type.value for node_type in NodeType if node_type not in table]
        unexpected = [str(key) for key in table if not isinstance(key, NodeType)]
        if missing or unexpected:
            raise RuntimeError(
                f"Every NodeType needs exactly one assistant {label}.\n"
                f"  Missing: {missing}\n"
                f"  Unexpected: {unexpected}"
            )
    if contradictory := SOURCE_NODE_TYPES & (SINK_ONLY_NODE_TYPES | STANDALONE_PASSTHROUGH_TYPES):
        raise RuntimeError(
            "A source node type cannot also be sink-only or pass through its first input: "
            f"{sorted(node_type.value for node_type in contradictory)}"
        )
    manifest = capability_manifest()
    if {node.id for node in manifest.nodes} != {node_type.value for node_type in NodeType}:
        raise RuntimeError("Capability manifest is missing a NodeType descriptor.")
    required_node_fields = (
        "display_name",
        "summary",
        "wiring_rules",
        "usage",
        "anti_patterns",
        "errors",
    )
    for node in manifest.nodes:
        if node.config_schema.get("additionalProperties") is not False:
            raise RuntimeError(f"Capability descriptor {node.id} has an open config schema.")
        if any(not getattr(node, field) for field in required_node_fields):
            raise RuntimeError(f"Capability descriptor {node.id} lacks semantic metadata.")
        if (node.step_authoring is not None) != (NodeType(node.id) in STEPPED_NODE_TYPES):
            raise RuntimeError(
                f"Capability descriptor {node.id} must carry step_authoring exactly "
                "when its type authors steps."
            )
        fields = {*node.required_fields, *node.optional_fields}
        for config in node_card(NodeType(node.id)).get("configs", []):
            if unknown := set(config["config"]) - fields:
                raise RuntimeError(
                    f"Node card {node.id} {config['name']!r} uses keys outside the "
                    f"config schema: {sorted(unknown)}"
                )
    if len({operation.id for operation in manifest.operations}) != len(manifest.operations):
        raise RuntimeError("Capability manifest contains duplicate operation descriptors.")
    for operation in manifest.operations:
        output_required = operation.output_schema.get("required")
        output_variants = operation.output_schema.get("oneOf")
        if (
            operation.input_schema.get("additionalProperties") is not False
            or operation.output_schema.get("additionalProperties") is not False
            or not isinstance(output_required, (list, tuple))
            or set(output_required) != {"capability_hash", "operation_version"}
            or not isinstance(output_variants, (list, tuple))
            or len(output_variants) != 2
            or not operation.errors
        ):
            raise RuntimeError(f"Operation descriptor {operation.id} is incomplete or open.")
    recipe_ids = [str(recipe["id"]) for recipe in manifest.recipes]
    if len(recipe_ids) != len(set(recipe_ids)) or not recipe_ids:
        raise RuntimeError("Capability manifest recipe descriptors are missing or duplicated.")
    for recipe in manifest.recipes:
        schema = recipe.get("argument_schema")
        if not isinstance(schema, Mapping) or schema.get("additionalProperties") is not False:
            raise RuntimeError(f"Recipe descriptor {recipe.get('id')} has an open schema.")
    known_recipes = set(recipe_ids)
    for node in manifest.nodes:
        if unknown := set(node.recipes).difference(known_recipes):
            raise RuntimeError(
                f"Capability descriptor {node.id} references unknown recipes: {sorted(unknown)}"
            )


def compact_manifest(manifest: CapabilityManifest | None = None) -> dict[str, object]:
    manifest = manifest or capability_manifest()
    return {
        "schema_version": manifest.schema_version,
        "haute_version": manifest.haute_version,
        "capability_hash": manifest.capability_hash,
        "installed_capabilities": _thaw(manifest.installed_capabilities),
        "feature_flags": _thaw(manifest.feature_flags),
        "node_index": [
            {
                "id": node.id,
                "display_name": node.display_name,
                "decorator": node.decorator,
                "summary": node.summary,
            }
            for node in manifest.nodes
        ],
        "operation_index": [
            {"id": operation.id, "description": operation.description}
            for operation in manifest.operations
        ],
        "recipe_index": [
            {
                "id": recipe["id"],
                "version": recipe["version"],
                "summary": recipe["summary"],
            }
            for recipe in manifest.recipes
        ],
    }


# A new node type or provider-visible operation must not reach the assistant
# without a complete, closed descriptor.
validate_manifest_complete()


#: Each tool's activity-row title in plain words, while it runs and once it is done.
_TOOL_TITLES: dict[str, str] = {
    "get_pipeline": "Reading the pipeline",
    "inspect_node": "Inspecting a node",
    "find_data": "Finding data",
    "read_reference": "Reading references",
    "get_project_knowledge": "Searching project notes",
    "dry_run_graph_edits": "Checking the plan",
    "apply_graph_plan": "Applying the plan",
}


def _changes(count: int) -> str:
    return f"{count} change" if count == 1 else f"{count} changes"


def tool_title(
    name: str, arguments: Mapping[str, object], result: Mapping[str, object] | None = None
) -> str:
    """The activity row's title for one tool call, in plain words.

    A started row (no *result*) reads the arguments: a dry-run counts its
    operations. A finished row reads only the result, so a resumed row, whose
    arguments are redacted, reads the same as the live one: a dry-run counts
    the result's ``operations`` and an apply its ``applied_operations``; a
    failed call, whose result counts nothing, keeps the tool's plain title. A
    name no tool has is its own title.
    """

    verb = {"dry_run_graph_edits": "Checking", "apply_graph_plan": "Applying"}.get(name)
    count: object
    if result is None:
        ops = arguments.get("ops") if name == "dry_run_graph_edits" else None
        count = len(ops) if isinstance(ops, list) else None
    else:
        count = result.get("operations" if name == "dry_run_graph_edits" else "applied_operations")
    if verb is not None and isinstance(count, int) and not isinstance(count, bool):
        return f"{verb} {_changes(count)}"
    return _TOOL_TITLES.get(name, name)


__all__ = [
    "EDGE_NAME_PLACEHOLDER",
    "INSPECT_NODE_PARTS",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_REFERENCE_IDS",
    "NEW_LOGIC_EXAMPLE_CODE",
    "CapabilityManifest",
    "NodeCapabilityDescriptor",
    "OPERATION_IDS",
    "OperationCapabilityDescriptor",
    "capability_manifest",
    "compact_manifest",
    "materialise_json",
    "new_logic_steps",
    "step_grammar",
    "tool_title",
    "validate_manifest_complete",
]
