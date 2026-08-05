"""The node vocabulary exposed to the pricing assistant.

The catalog deliberately keeps its mechanical facts derived from the same
registries that validate and save a pipeline.  The only hand-authored part is
the short usage note for each node type.  That gives the model useful authoring
guidance without creating a second source of truth for node names, config keys,
decorators, sidecar folders, or singleton rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType, UnionType
from typing import Literal, Required, Union, cast, get_args, get_origin, get_type_hints

from haute._cache import canonical_json
from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE, VALID_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.assistant._recipes import recipe_manifest
from haute.routes._save_pipeline import _SINGLETON_NODE_TYPES


@dataclass(frozen=True, slots=True)
class NodeCatalogEntry:
    """Facts and authoring guidance for one :class:`~haute._types.NodeType`."""

    node_type: NodeType
    decorator: str | None
    config_keys: tuple[str, ...]
    config_folder: str | None
    singleton: bool
    usage_note: str

    @property
    def config_shapes(self) -> tuple[tuple[str, str], ...]:
        """Return the TypedDict field shapes behind the config allowlist."""

        config_type = _TYPED_DICT_BY_NODE_TYPE.get(self.node_type)
        if config_type is None:
            return ()
        return tuple(
            (key, str(shape).replace("typing.", ""))
            for key, shape in config_type.__annotations__.items()
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable, JSON-shaped representation used by tools."""

        return {
            "node_type": self.node_type.value,
            "decorator": self.decorator,
            "config_keys": list(self.config_keys),
            "config_shapes": [{"key": key, "shape": shape} for key, shape in self.config_shapes],
            "config_folder": self.config_folder,
            "singleton": self.singleton,
            "usage_note": self.usage_note,
        }


# The save service is the authority for singleton policy.  Keep this derived
# rather than repeating the node list here: a new singleton must be visible to
# both save validation and the assistant catalog in the same change.
_SINGLETON_TYPES = frozenset(node_type for node_type, _label in _SINGLETON_NODE_TYPES)


# Usage notes are the catalog's intentionally hand-authored knowledge.  Every
# current NodeType is listed explicitly so adding a NodeType without adding a
# corresponding note leaves the catalog incomplete and fails at import time.
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
        "Write or sink a Polars frame to a file or database target; keep it at "
        "a deliberate branch endpoint rather than in the scoring path."
    ),
    NodeType.POLARS: (
        "Apply a Polars transform to connected inputs; keep reusable logic in "
        "code and use inputMapping when named inputs need explicit binding."
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
        "Turn continuous or discrete factor values into named bands; define an "
        "output column and an explicit default for values outside the rules."
    ),
    NodeType.RATING_STEP: (
        "Look up rating factors from one or more tables and combine their "
        "outputs with the chosen operation; make table factors and miss policy explicit."
    ),
    NodeType.OUTPUT: (
        "Assemble the top-level JSON response from selected upstream columns "
        "with an explicit outputMapping."
    ),
    NodeType.EXPLORE: (
        "Request exploratory summaries or apply an exploration code block; "
        "treat it as an analysis branch, not as a required pricing stage."
    ),
    NodeType.EXTERNAL_FILE: (
        "Load a serialized external object such as a model or lookup artifact; "
        "provide the file type and any type-specific model class."
    ),
    NodeType.LIVE_SWITCH: (
        "Route one of several connected frames by scenario; use at most one "
        "switch per pipeline and name the scenario map unambiguously."
    ),
    NodeType.MODELLING: (
        "Train a model from the connected frame; configure target, algorithm, "
        "task, features, and evaluation settings, and keep training outside the live quote path."
    ),
    NodeType.OPTIMISER: (
        "Search for better factor or quote values under an objective and "
        "constraints; select the mode and wire the required scored/factor inputs explicitly."
    ),
    NodeType.SCENARIO_EXPANDER: (
        "Expand each quote into deterministic scenario values for optimisation "
        "or analysis; set the source column, range, and number of steps together."
    ),
    NodeType.OPTIMISER_APPLY: (
        "Apply a saved optimisation artifact to an upstream frame; identify "
        "the artifact source and preserve the version/output-column conventions."
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


def _make_entry(node_type: NodeType) -> NodeCatalogEntry:
    """Build one entry from the canonical registries and the local usage note."""

    return NodeCatalogEntry(
        node_type=node_type,
        decorator=NODE_TYPE_TO_DECORATOR.get(node_type),
        config_keys=tuple(sorted(VALID_KEYS.get(node_type, frozenset()))),
        config_folder=NODE_TYPE_TO_FOLDER.get(node_type),
        singleton=node_type in _SINGLETON_TYPES,
        usage_note=_USAGE_NOTES[node_type],
    )


# Iterate over the enum, rather than the hand-authored notes, so the enum's
# order is the catalog's order and an omitted note cannot silently hide a new
# node type.  The explicit membership check lets the completeness validator
# report the missing type with its normal diagnostic instead of using a broad
# fallback note.
NODE_CATALOG: dict[NodeType, NodeCatalogEntry] = {
    node_type: _make_entry(node_type) for node_type in NodeType if node_type in _USAGE_NOTES
}


def validate_catalog_complete() -> None:
    """Assert that every canonical node type has a complete catalog entry.

    This mirrors ``haute._registry.validate_registry_complete``: a missing
    entry is a release-time programming error, not a condition for the model
    to recover from.  Mechanical fields are checked too, so a hand-edited
    catalog entry cannot quietly disagree with save/config behaviour.
    """

    canonical_types = frozenset(NodeType)
    catalog_types = frozenset(NODE_CATALOG)
    missing = [node_type for node_type in NodeType if node_type not in catalog_types]
    unexpected = [node_type for node_type in NODE_CATALOG if node_type not in canonical_types]

    invalid_entries: list[str] = []
    for node_type in NodeType:
        entry = NODE_CATALOG.get(node_type)
        if entry is None:
            continue
        if entry.node_type is not node_type:
            invalid_entries.append(f"{node_type.value}: entry.node_type={entry.node_type!r}")
        if entry.decorator != NODE_TYPE_TO_DECORATOR.get(node_type):
            invalid_entries.append(f"{node_type.value}: decorator")
        expected_keys = tuple(sorted(VALID_KEYS.get(node_type, frozenset())))
        if entry.config_keys != expected_keys:
            invalid_entries.append(f"{node_type.value}: config_keys")
        if entry.config_folder != NODE_TYPE_TO_FOLDER.get(node_type):
            invalid_entries.append(f"{node_type.value}: config_folder")
        if entry.singleton != (node_type in _SINGLETON_TYPES):
            invalid_entries.append(f"{node_type.value}: singleton")
        if not entry.usage_note.strip():
            invalid_entries.append(f"{node_type.value}: usage_note")

    if missing or unexpected or invalid_entries:
        raise RuntimeError(
            "NODE_CATALOG is incomplete or disagrees with canonical registries — "
            "every NodeType needs matching assistant metadata.\n"
            f"  Missing: {[node_type.value for node_type in missing]}\n"
            f"  Unexpected: {[str(node_type) for node_type in unexpected]}\n"
            f"  Invalid: {invalid_entries}"
        )


def render_catalog() -> str:
    """Render the catalog section in a stable, model-readable Markdown form."""

    lines = [
        "## Haute node catalog",
        "Use only these canonical node types and config keys when authoring a graph.",
    ]
    for node_type in NodeType:
        entry = NODE_CATALOG[node_type]
        lines.extend(
            (
                f"### `{node_type.value}`",
                (
                    f"- Decorator: `{entry.decorator}`"
                    if entry.decorator
                    else "- Decorator: structural-only"
                ),
                (
                    "- Config keys: " + ", ".join(f"`{key}`" for key in entry.config_keys)
                    if entry.config_keys
                    else "- Config keys: none"
                ),
                (
                    "- Config shapes: "
                    + ", ".join(f"`{key}`: `{shape}`" for key, shape in entry.config_shapes)
                    if entry.config_shapes
                    else "- Config shapes: none"
                ),
                (
                    f"- Sidecar folder: `config/{entry.config_folder}/`"
                    if entry.config_folder
                    else "- Sidecar folder: none"
                ),
                f"- Singleton: {'yes' if entry.singleton else 'no'}",
                f"- Usage: {entry.usage_note}",
                "",
            )
        )
    return "\n".join(lines).rstrip()


# Fail during import if a new node type is not represented here.  This is
# intentionally eager: an incomplete catalog must not reach a configured model.
validate_catalog_complete()


# ASSIST-A04 capability manifest -------------------------------------------------
# This intentionally lives beside the legacy catalogue: it is the authoritative
# descriptor and the latter remains a small compatibility projection.
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
        properties: dict[str, object] = {}
        for branch in branch_schemas:
            branch_properties = branch["properties"]
            if not isinstance(branch_properties, Mapping):
                raise TypeError("Derived config schema properties must be a mapping")
            properties.update(branch_properties)
        # Universal keys do not belong to individual I/O alternatives.
        for key in allowed:
            properties.setdefault(key, _json_value_schema())
        return {
            "type": "object",
            "properties": {key: properties[key] for key in sorted(allowed)},
            "additionalProperties": False,
            "oneOf": branch_schemas,
        }
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


_SOURCE_NODE_TYPES = frozenset(
    {
        NodeType.API_INPUT,
        NodeType.DATA_INPUT,
        NodeType.CONSTANT,
        NodeType.EXTERNAL_FILE,
    }
)
_SINK_NODE_TYPES = frozenset({NodeType.DATA_OUTPUT, NodeType.OUTPUT})
_TRAINING_NODE_TYPES = frozenset({NodeType.MODELLING, NodeType.OPTIMISER})
_MULTI_INPUT_NODE_TYPES = frozenset(
    {
        NodeType.POLARS,
        NodeType.RATING_STEP,
        NodeType.OUTPUT,
        NodeType.LIVE_SWITCH,
        NodeType.OPTIMISER,
        NodeType.SUBMODEL,
    }
)
_EXAMPLE_IDS: dict[NodeType, tuple[str, ...]] = {
    NodeType.API_INPUT: ("minimal_live_quote",),
    NodeType.DATA_INPUT: ("minimal_batch",),
    NodeType.BANDING: ("continuous_banding",),
    NodeType.EDGE_JOIN: ("reference_join",),
    NodeType.RATING_STEP: ("rating_step",),
}
_RECIPE_IDS: dict[NodeType, tuple[str, ...]] = {
    NodeType.DATA_INPUT: ("parquet_showcase",),
    NodeType.BANDING: ("categorical_banding", "continuous_banding"),
    NodeType.EDGE_JOIN: ("parquet_showcase", "reference_join"),
    NodeType.POLARS: ("parquet_showcase",),
    NodeType.OUTPUT: ("parquet_showcase", "response_output"),
    NodeType.RATING_STEP: ("rating_step",),
}


def _node_ports(node_type: NodeType) -> dict[str, object]:
    if node_type is NodeType.API_INPUT:
        return {
            "inputs": [],
            "outputs": "one named output per declared request table",
        }
    if node_type is NodeType.LIVE_SWITCH:
        return {"inputs": "one per configured scenario", "outputs": ["frame"]}
    if node_type is NodeType.SUBMODEL:
        return {
            "inputs": "declared submodel input ports",
            "outputs": "declared submodel output ports",
        }
    if node_type is NodeType.SUBMODEL_PORT:
        return {"inputs": "structural only", "outputs": "structural only"}
    if node_type in _SOURCE_NODE_TYPES:
        return {"inputs": [], "outputs": ["frame"]}
    if node_type in _SINK_NODE_TYPES:
        outputs = [] if node_type is NodeType.DATA_OUTPUT else ["response"]
        return {"inputs": ["frame"], "outputs": outputs}
    if node_type is NodeType.EDGE_JOIN:
        return {"inputs": ["base", "join"], "outputs": ["frame"]}
    return {"inputs": ["frame"], "outputs": ["frame"]}


def _input_cardinality(node_type: NodeType) -> str:
    if node_type in _SOURCE_NODE_TYPES:
        return "zero"
    if node_type is NodeType.EDGE_JOIN:
        return "exactly two"
    if node_type in _MULTI_INPUT_NODE_TYPES:
        return "one or more, subject to the descriptor configuration"
    if node_type is NodeType.SUBMODEL_PORT:
        return "structural boundary; not directly wireable"
    return "exactly one"


def _schema_effect(node_type: NodeType) -> str:
    effects = {
        NodeType.API_INPUT: "emits the declared request-table schemas",
        NodeType.DATA_INPUT: "emits the selected source schema",
        NodeType.DATA_OUTPUT: "preserves its input schema while publishing a sink",
        NodeType.POLARS: "derives the schema from validated Polars expressions",
        NodeType.EDGE_JOIN: "combines base and reference columns under join suffix/coalesce rules",
        NodeType.MODEL_SCORE: "adds the configured prediction output and optional post-processing",
        NodeType.BANDING: "adds each configured factor output column",
        NodeType.RATING_STEP: "adds table-factor and combined-output columns",
        NodeType.OUTPUT: "projects mapped columns into the declared response contract",
        NodeType.EXPLORE: "preserves or derives columns according to exploration code",
        NodeType.EXTERNAL_FILE: "emits an external artifact rather than a tabular schema",
        NodeType.LIVE_SWITCH: "requires compatible schemas across selected scenarios",
        NodeType.MODELLING: (
            "preserves the input frame while producing training artifacts out of band"
        ),
        NodeType.OPTIMISER: "adds optimiser result columns for the selected mode",
        NodeType.SCENARIO_EXPANDER: "adds scenario value and index columns and expands rows",
        NodeType.OPTIMISER_APPLY: "adds configured optimized-value/version columns",
        NodeType.CONSTANT: "emits one row with the configured literal columns",
        NodeType.SUBMODEL: "exposes the declared schemas of its output ports",
        NodeType.SUBMODEL_PORT: "carries its enclosing submodel port schema",
    }
    return effects[node_type]


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
    if node_type in _SOURCE_NODE_TYPES:
        return ("lazy/local source resolution", "reads declared project or artifact state")
    if node_type in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}:
        return ("structural graph expansion", "none")
    return ("lazy pipeline execution", "none until an owning execution surface materialises it")


def _node_descriptor(node_type: NodeType) -> NodeCapabilityDescriptor:
    entry = NODE_CATALOG[node_type]
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
    wiring_rules = (
        'Connect the primary input with target_handle="base" and the lookup '
        'input with target_handle="join"; exactly one incoming edge of each '
        "role is required."
        if node_type == NodeType.EDGE_JOIN
        else (
            "Source nodes cannot have upstream edges; otherwise use only compatible "
            "declared ports and preserve top-level submodel boundaries."
        )
    )
    anti_patterns = [
        "Do not invent config keys or bypass graph wiring.",
        "Do not add disconnected decorative nodes; every new node must be wired.",
    ]
    if node_type == NodeType.POLARS:
        anti_patterns.append(
            "Do not discard immutable Polars results; assign them to df or return them."
        )
    if node_type == NodeType.EDGE_JOIN:
        anti_patterns.append("Do not omit or duplicate edgeJoin target_handle roles.")
    return NodeCapabilityDescriptor(
        node_type.value,
        entry.decorator,
        cast(Mapping[str, object], _freeze(schema)),
        required,
        tuple(key for key in fields if key not in required),
        MappingProxyType({}),  # defaults are absent unless a canonical TypedDict declares them.
        cast(Mapping[str, object], _freeze(_schema_enums(schema))),
        branches,
        (branch_constraints,),
        entry.config_folder,
        entry.singleton,
        (
            "Persisted in the canonical sidecar folder."
            if entry.config_folder
            else "Inline graph configuration."
        ),
        entry.usage_note,
        cast(Mapping[str, object], _freeze(_node_ports(node_type))),
        _input_cardinality(node_type),
        wiring_rules,
        _schema_effect(node_type),
        execution,
        side_effects,
        entry.usage_note,
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


def _graph_edit_operations_schema() -> dict[str, object]:
    ref = {
        "type": "string",
        "description": "Node id, or a batch-local $ref declared by an earlier add_node.",
    }
    variants: list[dict[str, object]] = [
        _closed_object(
            {
                "op": {"const": "add_node"},
                "node_type": {"type": "string"},
                "name": {"type": "string"},
                "config": {"type": "object"},
                "ref": {"type": "string"},
            },
            ["op", "node_type", "name"],
        ),
        _closed_object(
            {"op": {"const": "update_node"}, "node": ref, "config": {"type": "object"}},
            ["op", "node", "config"],
        ),
        _closed_object(
            {"op": {"const": "rename_node"}, "node": ref, "new_name": {"type": "string"}},
            ["op", "node", "new_name"],
        ),
        _closed_object(
            {"op": {"const": "delete_node"}, "node": ref},
            ["op", "node"],
        ),
    ]
    for operation in ("add_edge", "delete_edge"):
        variants.append(
            _closed_object(
                {
                    "op": {"const": operation},
                    "source": ref,
                    "target": ref,
                    "source_handle": {"type": ["string", "null"]},
                    "target_handle": {"type": ["string", "null"]},
                },
                ["op", "source", "target"],
            )
        )
    variants.append(
        _closed_object(
            {
                "op": {"const": "update_preamble"},
                "preamble": {"type": ["string", "null"]},
            },
            ["op", "preamble"],
        )
    )
    return {
        "type": "array",
        "items": {"oneOf": variants},
        "maxItems": 100,
    }


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
        "maxItems": 100,
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
        "get_node_schema": ("node", "columns", "ports", "project_revision"),
        "get_node_config": ("node", "sensitivity", "config", "project_revision"),
        "list_node_types": ("node_types",),
        "list_datasets": ("datasets", "directories", "recursive", "truncated"),
        "get_dataset_schema": (
            "path",
            "columns",
            "row_count",
            "row_count_estimated",
            "column_count",
            "source_digest",
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
        "get_example": ("name", "attribution", "narrative", "graph"),
        "get_authoring_guide": (
            "id",
            "version",
            "sha256",
            "source",
            "sensitivity",
            "evidence_class",
            "approval_status",
            "content",
        ),
        "plan_recipe": (
            "recipe_id",
            "version",
            "recipe_plan_hash",
        ),
        "dry_run_recipe_plan": (
            "base_revision",
            "capability_hash",
            "revision_sources",
            "normalized_operations",
            "diff",
            "affected_capabilities",
            "postconditions",
            "validation_warnings",
            "resulting_graph_shape",
            "egress",
            "verification_tier",
            "plan_hash",
            "verification_evidence",
        ),
        "dry_run_graph_edits": (
            "base_revision",
            "capability_hash",
            "revision_sources",
            "normalized_operations",
            "diff",
            "affected_capabilities",
            "postconditions",
            "validation_warnings",
            "resulting_graph_shape",
            "egress",
            "verification_tier",
            "plan_hash",
            "verification_evidence",
        ),
        "apply_graph_plan": (
            "plan_hash",
            "capability_hash",
            "base_revision",
            "result_revision",
            "expected_diff",
            "actual_diff",
            "verification_tier",
            "verification_evidence",
            "verification_status",
            "verification_error_code",
            "graph_fingerprint",
            "graph_publication_error",
            "warnings",
            "git_sha",
            "applied_operations",
        ),
        "get_capability_manifest": (
            "schema_version",
            "haute_version",
            "capability_hash",
            "installed_capabilities",
            "feature_flags",
            "node_index",
            "operation_index",
            "recipe_index",
        ),
        "get_capability_descriptors": ("kind", "count", "descriptors"),
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
        "missing",
        "unknown",
        "argument",
        "recipe_id",
        "validation_path",
        "validation_reason",
        *fields["apply_graph_plan"],
    }
    properties["error"] = _closed_object(
        {
            field: ({"type": "string"} if field in {"code", "message"} else _json_value_schema())
            for field in sorted(error_fields)
        },
        ["code", "message"],
    )
    optional_success_fields = {
        "get_node_schema": {"columns", "ports"},
        "apply_graph_plan": {
            "verification_status",
            "verification_error_code",
            "graph_publication_error",
        },
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
    if name == "get_node_schema":
        success_variant["oneOf"] = [
            {"required": ["columns"]},
            {"required": ["ports"]},
        ]
    result = _closed_object(
        properties,
        ["capability_hash", "operation_version"],
    )
    result["oneOf"] = [
        success_variant,
        {"required": ["error"]},
    ]
    return result


def _recipe_invocation_schema() -> dict[str, object]:
    """Expose recipe arguments as a provider-friendly discriminated union."""

    variants: list[dict[str, object]] = []
    for descriptor in recipe_manifest():
        recipe_id = descriptor.get("id")
        raw_schema = descriptor.get("argument_schema")
        if not isinstance(recipe_id, str) or not isinstance(raw_schema, Mapping):
            raise TypeError("Recipe descriptor has an invalid invocation schema")
        raw_properties = raw_schema.get("properties")
        raw_required = raw_schema.get("required")
        if not isinstance(raw_properties, Mapping) or not isinstance(raw_required, (list, tuple)):
            raise TypeError("Recipe argument schema must have properties and required fields")
        properties = {
            "recipe_id": {"const": recipe_id},
            **{
                str(key): cast(dict[str, object], _thaw(value))
                for key, value in raw_properties.items()
            },
        }
        variants.append(
            _closed_object(
                properties,
                ["recipe_id", *(str(item) for item in raw_required)],
            )
        )
    return {
        "oneOf": variants,
        "additionalProperties": False,
    }


def _operation_descriptor(name: str) -> OperationCapabilityDescriptor:
    descriptions = {
        "get_pipeline": "Inspect the saved pipeline graph and its project revision.",
        "get_node_schema": "Resolve output columns and dtypes for a saved pipeline node.",
        "get_node_config": "Inspect one saved node's complete configuration.",
        "list_node_types": "List the manifest-backed node catalogue compatibility view.",
        "list_datasets": (
            "List safe installed-format datasets in one project directory, optionally recursively."
        ),
        "get_dataset_schema": "Inspect a dataset schema without implicitly reading rows.",
        "get_project_knowledge": (
            "Retrieve bounded policy-filtered project facts and untrusted documentation."
        ),
        "get_example": "Load one packaged, versioned teaching example.",
        "get_authoring_guide": (
            "Retrieve the packaged canonical authoring guide with attribution."
        ),
        "dry_run_graph_edits": (
            "Validate an exact graph-edit plan and report revision, semantic diff, "
            "postconditions, authority and verification tier without writing."
        ),
        "dry_run_recipe_plan": (
            "Dry-run exactly one pending canonical recipe by recipe_plan_hash."
        ),
        "apply_graph_plan": "Apply one exact validated plan hash under revision authority.",
        "get_capability_manifest": "Read manifest identity and its compact capability index.",
        "get_capability_descriptors": "Read ordered complete capability descriptors in one batch.",
        "plan_recipe": (
            "Mandatory first planner before dry-run when an installed recipe matches. "
            "Supply output_name and explicit output_columns together for a response output. "
            "Pass only the returned recipe_plan_hash to dry_run_recipe_plan; canonical "
            "operations remain server-side."
        ),
    }
    input_schemas = {
        "get_pipeline": _closed_object(),
        "get_node_schema": _closed_object({"node": {"type": "string"}}, ["node"]),
        "get_node_config": _closed_object({"node": {"type": "string"}}, ["node"]),
        "list_node_types": _closed_object(),
        "list_datasets": _closed_object(
            {
                "project_root": {"type": "string"},
                "recursive": {"type": "boolean"},
            }
        ),
        "get_dataset_schema": _closed_object({"path": {"type": "string"}}, ["path"]),
        "get_project_knowledge": _closed_object(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
        ),
        "get_example": _closed_object({"name": {"type": "string"}}, ["name"]),
        "get_authoring_guide": _closed_object(),
        "dry_run_graph_edits": _closed_object(
            {
                "ops": _graph_edit_operations_schema(),
                "postconditions": _postconditions_schema(),
            },
            ["ops"],
        ),
        "dry_run_recipe_plan": _closed_object(
            {
                "recipe_plan_hash": {
                    "type": "string",
                    "pattern": "^[0-9a-f]{64}$",
                },
            },
            ["recipe_plan_hash"],
        ),
        "apply_graph_plan": _closed_object(
            {"plan_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
            ["plan_hash"],
        ),
        "get_capability_manifest": _closed_object(),
        "get_capability_descriptors": _closed_object(
            {
                "kind": {"enum": ["node", "operation", "recipe"]},
                "ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 12,
                },
            },
            ["kind", "ids"],
        ),
        "plan_recipe": _recipe_invocation_schema(),
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
    if name in {
        "plan_recipe",
        "dry_run_recipe_plan",
        "dry_run_graph_edits",
        "apply_graph_plan",
    }:
        errors.append(
            {
                "code": "material_input_required",
                "recovery": "Ask for the explicitly withheld rating choices before planning.",
            }
        )
    if name in {"dry_run_graph_edits", "dry_run_recipe_plan"}:
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
            ]
        )
        if name == "dry_run_graph_edits":
            errors.extend(
                [
                    {
                        "code": "recipe_plan_requires_handle",
                        "recovery": "Pass the pending hash to dry_run_recipe_plan.",
                    },
                    {
                        "code": "recipe_route_required",
                        "recovery": "Call plan_recipe with the returned required recipe id.",
                    },
                ]
            )
        else:
            errors.append(
                {
                    "code": "recipe_plan_not_found",
                    "recovery": "Call plan_recipe again and use its latest returned hash.",
                }
            )
    elif name == "plan_recipe":
        errors.extend(
            [
                {
                    "code": "recipe_route_mismatch",
                    "recovery": "Use the deterministic recipe id returned in the error.",
                },
                {
                    "code": "recipe_name_mismatch",
                    "recovery": "Use the explicit primary node name returned in the error.",
                },
            ]
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
        (
            "policy-filtered-project-content"
            if name == "get_project_knowledge"
            else (
                "restricted-redacted"
                if name == "get_node_config"
                else (
                    "internal-schema-only"
                    if name in {"get_dataset_schema", "get_node_schema"}
                    else (
                        "internal-project-metadata"
                        if name
                        in {
                            "get_pipeline",
                            "list_datasets",
                            "dry_run_recipe_plan",
                            "dry_run_graph_edits",
                            "apply_graph_plan",
                        }
                        else "none"
                    )
                )
            )
        ),
        "graph mutation" if mutation else "none",
        "bounded",
        "idempotent" if not mutation else "conditional",
        "never automatic",
        False,
        name
        in {
            "get_capability_manifest",
            "get_capability_descriptors",
            "get_example",
            "get_authoring_guide",
            "list_node_types",
            "plan_recipe",
        },
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
    operations = tuple(
        _operation_descriptor(name)
        for name in (
            *(
                "get_pipeline",
                "get_node_schema",
                "get_node_config",
                "list_node_types",
                "list_datasets",
                "get_dataset_schema",
                "get_project_knowledge",
                "get_example",
                "get_authoring_guide",
                "plan_recipe",
                "dry_run_recipe_plan",
                "dry_run_graph_edits",
                "apply_graph_plan",
            ),
            "get_capability_manifest",
            "get_capability_descriptors",
        )
    )
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
    manifest = capability_manifest()
    if {node.id for node in manifest.nodes} != {node_type.value for node_type in NodeType}:
        raise RuntimeError("Capability manifest is missing a NodeType descriptor.")
    required_node_fields = (
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
            {"id": node.id, "decorator": node.decorator, "summary": node.summary}
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


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "NODE_CATALOG",
    "CapabilityManifest",
    "NodeCapabilityDescriptor",
    "NodeCatalogEntry",
    "OperationCapabilityDescriptor",
    "capability_manifest",
    "compact_manifest",
    "materialise_json",
    "render_catalog",
    "validate_catalog_complete",
    "validate_manifest_complete",
]
