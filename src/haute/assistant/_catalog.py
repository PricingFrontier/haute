"""The capability manifest: the node, operation and recipe vocabulary of the assistant.

Node descriptors derive their mechanical facts from the same registries that
validate and save a pipeline, and from the node specification
(``haute._node_specs``). The only hand-authored part is each node type's
description (``haute.assistant._node_descriptions``): its summary, usage note
and the other prose the assistant states. That gives the model useful
authoring guidance without creating a second source of truth for node names,
config keys, decorators, sidecar folders, or singleton rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType, UnionType
from typing import Annotated, Literal, Required, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from haute._cache import canonical_json
from haute._config_io import palette_default_config
from haute._config_validation import VALID_KEYS
from haute._node_config_models import NODE_CONFIG_MODELS
from haute._node_specs import (
    NODE_TYPE_TO_DECORATOR,
    NODE_TYPE_TO_FOLDER,
    SINGLETON_NODE_GROUPS,
    SINK_ONLY_NODE_TYPES,
    SOURCE_NODE_TYPES,
    STANDALONE_PASSTHROUGH_TYPES,
    STEPPED_NODE_TYPES,
    STRUCTURAL_NODE_TYPES,
    InputCardinality,
    node_spec,
)
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
    WINDOW_AGGREGATIONS,
    step_fields,
    stepped_surface_for,
)
from haute._types import NodeType
from haute.assistant._node_cards import node_card
from haute.assistant._node_descriptions import ASSISTANT_NODE_DESCRIPTIONS, INPUT_NAMING_RULE
from haute.assistant._recipes import recipe_manifest
from haute.assistant._wire_ops import MAX_DECLARED_POSTCONDITIONS, graph_edit_operations_schema
from haute.schemas import (
    ASSISTANT_BUILD_PLAN_ID_PATTERN,
    ASSISTANT_BUILD_PLAN_TITLE_LIMIT,
    ASSISTANT_MAX_ASSUMPTIONS,
    ASSISTANT_MAX_BUILD_PLAN_ITEMS,
    ASSISTANT_RECEIPT_TEXT_LIMIT,
)

# The singleton groups that save and deploy enforce are the authority for
# singleton policy.  Keep this derived rather than repeating the node list here:
# a new singleton must be visible to both and to the capability manifest in the
# same change.
_SINGLETON_TYPES = frozenset(
    node_type for group, _label in SINGLETON_NODE_GROUPS for node_type in group
)


# ASSIST-A04 capability manifest -------------------------------------------------
MANIFEST_SCHEMA_VERSION = "1.0"
_MANIFEST_CACHE: dict[tuple[str, str], CapabilityManifest] = {}


def _json_value_schema() -> dict[str, object]:
    """The permissive JSON value used for parser/executor universal fields."""
    return {"type": ["string", "number", "integer", "boolean", "object", "array", "null"]}


def _schema_for_annotation(annotation: object) -> dict[str, object]:
    """Resolve the useful JSON Schema subset of the config models' vocabulary."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Required, Annotated):
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
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _schema_for_model(annotation)
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


def _schema_for_model(model: type[BaseModel]) -> dict[str, object]:
    """A pydantic config shape (an Explore chart) as the same subset: its fields by name."""
    fields = model.model_fields
    result: dict[str, object] = {
        "type": "object",
        "properties": {
            name: _schema_for_annotation(field.annotation) for name, field in sorted(fields.items())
        },
        "additionalProperties": model.model_config.get("extra") != "forbid",
    }
    if required := sorted(name for name, field in fields.items() if field.is_required()):
        result["required"] = required
    return result


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


def _config_branches(shape: object) -> tuple[type, ...]:
    """The branches of a config shape that is a union of them (a Data Input's or a Data
    Output's, discriminated by its input or output type); empty for any other shape."""
    if get_origin(shape) is Annotated:
        shape = get_args(shape)[0]
    if get_origin(shape) in (Union, UnionType):
        return cast(tuple[type, ...], get_args(shape))
    return ()


def _config_schema(node_type: NodeType) -> dict[str, object]:
    """Closed top-level schema matching VALID_KEYS, with I/O branch detail."""
    allowed = set(VALID_KEYS.get(node_type, ()))
    shape = NODE_CONFIG_MODELS[node_type].shape
    if branches := _config_branches(shape):
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
    schema = _schema_for_typeddict(shape, keys=allowed)
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


#: Each input cardinality in words. The cardinality is the node specification's
#: (``haute._node_specs.NodeSpec.inputs``), whose ``exactly_one`` types are the
#: palette's ``maxInputs: 1`` entries (``frontend/src/utils/nodeTypes.ts``), held
#: equal by test.
_INPUT_CARDINALITY_TEXT: Mapping[InputCardinality, str] = MappingProxyType(
    {
        "none": "zero",
        "exactly_one": "exactly one",
        "exactly_two": "exactly two",
        "zero_or_more": "zero or more",
        "one_or_more": "one or more, subject to the descriptor configuration",
        "structural": "structural boundary; not directly wireable",
    }
)
if set(_INPUT_CARDINALITY_TEXT) != set(get_args(InputCardinality)):
    raise RuntimeError("Every input cardinality needs its words in the capability manifest.")


def _node_ports(node_type: NodeType) -> dict[str, object]:
    spec = node_spec(node_type)
    description = ASSISTANT_NODE_DESCRIPTIONS[node_type]
    outputs: object = [] if spec.sink_only else ["frame"]
    if spec.request_input:
        return {
            "inputs": [],
            "outputs": "one named output per declared request table",
        }
    if description.output_ports is not None:
        outputs = description.output_ports
    if description.input_ports is not None:
        return {"inputs": description.input_ports, "outputs": outputs}
    if spec.input_roles:
        return {"inputs": list(spec.input_roles), "outputs": outputs}
    if spec.source:
        return {"inputs": [], "outputs": outputs}
    return {"inputs": ["frame"], "outputs": outputs}


def _input_cardinality(node_type: NodeType) -> str:
    return _INPUT_CARDINALITY_TEXT[node_spec(node_type).inputs]


def _schema_effect(node_type: NodeType) -> str:
    effect = ASSISTANT_NODE_DESCRIPTIONS[node_type].schema_effect
    if node_type in SINK_ONLY_NODE_TYPES:
        return f"no downstream frame; {effect}"
    if node_type in STANDALONE_PASSTHROUGH_TYPES:
        return f"returns its first input unless its steps or code transform it; {effect}"
    return effect


def _execution_class(node_type: NodeType) -> tuple[str, str]:
    execution = ASSISTANT_NODE_DESCRIPTIONS[node_type].execution
    if execution is not None:
        return execution
    if node_type in SOURCE_NODE_TYPES:
        return ("lazy/local source resolution", "reads declared project or artifact state")
    if node_type in STRUCTURAL_NODE_TYPES:
        return ("structural graph expansion", "none")
    return ("lazy pipeline execution", "none until an owning execution surface materialises it")


def _wiring_rules(node_type: NodeType) -> str:
    description = ASSISTANT_NODE_DESCRIPTIONS[node_type]
    if description.wiring_rules is not None:
        return description.wiring_rules
    spec = node_spec(node_type)
    rules: list[str] = []
    if node_type in SOURCE_NODE_TYPES:
        rules.append("Takes no incoming edges.")
    elif description.incoming_edges is not None:
        rules.append(description.incoming_edges)
    elif spec.inputs == "exactly_one":
        rules.append("Takes exactly one incoming edge.")
    if node_type in SINK_ONLY_NODE_TYPES:
        rules.append("It has no output: never wire an edge out of it; it ends its branch.")
    if description.wiring_extra is not None:
        rules.append(description.wiring_extra)
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
    description = ASSISTANT_NODE_DESCRIPTIONS[node_type]
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
        *description.anti_patterns,
    ]
    return NodeCapabilityDescriptor(
        node_type.value,
        node_spec(node_type).display_name,
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
        description.summary,
        cast(Mapping[str, object], _freeze(_node_ports(node_type))),
        _input_cardinality(node_type),
        _wiring_rules(node_type),
        _schema_effect(node_type),
        execution,
        side_effects,
        description.usage,
        tuple(anti_patterns),
        description.examples,
        description.recipes,
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
            "data",
            "data_omitted",
            "part_errors",
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
            "data_check",
            "data_check_omitted",
            "next",
        ),
        "apply_graph_plan": (
            "plan_hash",
            "applied_operations",
            "verification_tier",
            "evidence",
            "change",
            "item",
        ),
        "update_build_plan": ("items",),
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
        # Each part answers only when the call asked for it and the policy permits it;
        # a data part that does not fit the result is replaced by its omission note,
        # and a part that failed beside one that answered is under `part_errors`.
        "inspect_node": {
            "schema",
            "config",
            "profile",
            "data",
            "data_omitted",
            "part_errors",
            "withheld",
        },
        # A file's schema, and the revision its evidence enters, only for a `path`.
        "find_data": {"schema", "project_revision"},
        # The build-plan item the change was recorded against, only for an apply naming one.
        "apply_graph_plan": {"item"},
        # The data check, or the note that it did not fit, only when the policy runs one,
        # and the reminder of what to do, only when it holds an advisory finding.
        "dry_run_graph_edits": {"data_check", "data_check_omitted", "next"},
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

    The compatible projection keeps only the description of a property whose
    schema differs between branches, so this line is how that lane learns
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
    "update_build_plan",
)
#: The operations that change the project; every other operation leaves it as it is.
MUTATING_OPERATION_IDS = frozenset({"apply_graph_plan"})
#: The one operation that changes session state, the build plan, and nothing in the project.
SESSION_OPERATION_IDS = frozenset({"update_build_plan"})
#: The parts `inspect_node` can return, in the order it answers them.
INSPECT_NODE_PARTS = ("schema", "config", "profile", "data")
#: Ids one `read_reference` call may name.
MAX_REFERENCE_IDS = 12
#: A build-plan item id as `update_build_plan` and `apply_graph_plan` take it.
_BUILD_PLAN_ITEM_ID: dict[str, object] = {
    "type": "string",
    "pattern": ASSISTANT_BUILD_PLAN_ID_PATTERN,
}
#: Each operation's egress class: the most sensitive project material it can send.
_OPERATION_EGRESS = {
    "get_pipeline": "internal-project-metadata",
    # Per part: schema, config, profile, data. The profile is the one part that
    # returns values from data, so its class is distinct and a policy review can see
    # it plainly; the data part runs the node's lineage and returns counts only.
    "inspect_node": (
        "internal-schema-only, restricted-redacted, restricted-value-profile, "
        "internal-aggregate-statistics"
    ),
    "find_data": "internal-schema-only",
    "read_reference": "none",
    "get_project_knowledge": "policy-filtered-project-content",
    "dry_run_graph_edits": "internal-project-metadata",
    "apply_graph_plan": "internal-project-metadata",
    "update_build_plan": "none",
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
            'is withheld. "data": why the node fails or why a column is null, in one call: '
            "the node's lineage runs over the project's data under the active scenario (the "
            "call waits up to 30 seconds) and returns value-free counts: each lineage node's "
            "status, a failure reported once at the node that raised it with its error type "
            "and step or line while the nodes it stops are `upstream_failed`, rows in and out, "
            "Edge Join matches and findings; with `column`, that column's null count at each "
            "node whose output has it and `first_null_node`, where its nulls appear or grow. "
            'A check that cannot run returns `outcome` "not_run" with its reason. A part the '
            "egress policy does not permit is listed under `withheld` with the setting it needs. "
            "A part that fails is reported under `part_errors` with its error while the other "
            "parts still answer."
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
            "evidence summary, warnings and the plan's node and edge changes, and, when the "
            "egress policy permits aggregate statistics, `data_check`: value-free counts "
            "measured by running the changed nodes over the project's data."
        ),
        "apply_graph_plan": (
            "Apply one exact validated plan hash under revision authority. Returns the "
            "change record the analyst sees, built from what was saved."
        ),
        "update_build_plan": (
            "Set the build plan of a request with several stages, or mark one of its items "
            "complete; the analyst sees the plan as a checklist. `items` sets the stages in "
            "order, each a short `id` and a `title`; while the plan has an open item, an item "
            "keeps its saved changes and completion under the same id. Name an item in "
            "`apply_graph_plan`'s `item` to record that apply's saved change against it. "
            "`complete` marks an item complete once its whole stage is saved; an item with "
            "no saved change cannot be complete. Returns each item with whether it is "
            "complete and how many saved changes it has."
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
                "column": {
                    "type": "string",
                    "description": (
                        "A column whose null count the data part follows along the node's "
                        'lineage. Only with the "data" part.'
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
            {
                "plan_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "item": {
                    **_BUILD_PLAN_ITEM_ID,
                    "description": (
                        "The id of the build-plan item this plan implements, when a build "
                        "plan is set; Haute records the saved change against that item."
                    ),
                },
            },
            ["plan_hash"],
        ),
        "update_build_plan": _closed_object(
            {
                "items": {
                    "type": "array",
                    "items": _closed_object(
                        {
                            "id": {
                                **_BUILD_PLAN_ITEM_ID,
                                "description": (
                                    "A short stable id, such as `source` or `rating`: "
                                    "lower-case letters, digits and underscores, starting "
                                    "with a letter, at most 32 characters."
                                ),
                            },
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": ASSISTANT_BUILD_PLAN_TITLE_LIMIT,
                                "description": (
                                    "What the stage builds, in a few words (at most "
                                    f"{ASSISTANT_BUILD_PLAN_TITLE_LIMIT} characters)."
                                ),
                            },
                        },
                        ["id", "title"],
                    ),
                    "minItems": 1,
                    "maxItems": ASSISTANT_MAX_BUILD_PLAN_ITEMS,
                    "description": (
                        "The plan's stages in order, one item per stage; replaces the item "
                        f"list. At most {ASSISTANT_MAX_BUILD_PLAN_ITEMS} items."
                    ),
                },
                "complete": {
                    **_BUILD_PLAN_ITEM_ID,
                    "description": (
                        "The id of an item whose whole stage is now saved; it needs at "
                        "least one saved change recorded against it."
                    ),
                },
            }
        ),
    }
    mutation = name in MUTATING_OPERATION_IDS
    session = name in SESSION_OPERATION_IDS
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
                {
                    "code": "unknown_plan_item",
                    "recovery": (
                        "Name an item of the build plan (valid_ids), or set the plan's items "
                        "with update_build_plan first; nothing was saved."
                    ),
                },
            ]
        )
    elif name == "update_build_plan":
        errors.extend(
            [
                {
                    "code": "empty_plan_update",
                    "recovery": "Send items, complete or both.",
                },
                {
                    "code": "duplicate_plan_item",
                    "recovery": "Give each item its own id.",
                },
                {
                    "code": "unknown_plan_item",
                    "recovery": "Name an item of the plan (valid_ids), or set the items first.",
                },
                {
                    "code": "plan_item_unsaved",
                    "recovery": (
                        "Apply the plan that builds the stage with the item's id in "
                        "apply_graph_plan's item, then mark it complete."
                    ),
                },
            ]
        )
    return OperationCapabilityDescriptor(
        name,
        "1.0",
        descriptions[name],
        _freeze(input_schemas[name]),  # type: ignore[arg-type]
        _freeze(_operation_output_schema(name)),  # type: ignore[arg-type]
        "write" if mutation else ("session" if session else "read"),
        (
            "ready branch required"
            if mutation
            else ("session build plan" if session else "saved project state")
        ),
        (
            "exact base revision and single-use plan hash"
            if plan_bound
            else (
                "transactional graph revision"
                if mutation
                else ("replaces the session's build plan" if session else "snapshot read")
            )
        ),
        "none",
        _OPERATION_EGRESS[name],
        "graph mutation" if mutation else ("session build plan" if session else "none"),
        "bounded",
        "idempotent" if not mutation else "conditional",
        "never automatic",
        False,
        name == "read_reference",
        not (mutation or session),
        "pipeline-save" if mutation else ("assistant-session" if session else "assistant-read"),
        "ordered" if mutation or session else "independent",
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
    "update_build_plan": "Updating the checklist",
}


#: A dry-run's activity-row title while its data check runs.
DATA_CHECK_PROGRESS_TITLE = "Checking the data"
#: The running tool call's progress reporter; the loop sets one for each call.
_TOOL_PROGRESS: ContextVar[Callable[[str], None] | None] = ContextVar(
    "assistant_tool_progress", default=None
)


@contextmanager
def tool_progress_reporter(report: Callable[[str], None]) -> Iterator[None]:
    """Make *report* receive the progress titles of the tool calls started inside."""

    token = _TOOL_PROGRESS.set(report)
    try:
        yield
    finally:
        _TOOL_PROGRESS.reset(token)


def report_tool_progress(title: str) -> None:
    """Retitle the running tool call's activity row while it runs.

    The loop streams the title as a ``tool_progress`` event. A tool called
    outside a turn has no activity row, so nothing receives it.
    """

    report = _TOOL_PROGRESS.get()
    if report is not None:
        report(title)


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
    "DATA_CHECK_PROGRESS_TITLE",
    "EDGE_NAME_PLACEHOLDER",
    "INPUT_NAMING_RULE",
    "INSPECT_NODE_PARTS",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_REFERENCE_IDS",
    "MUTATING_OPERATION_IDS",
    "NEW_LOGIC_EXAMPLE_CODE",
    "CapabilityManifest",
    "NodeCapabilityDescriptor",
    "OPERATION_IDS",
    "SESSION_OPERATION_IDS",
    "OperationCapabilityDescriptor",
    "capability_manifest",
    "compact_manifest",
    "materialise_json",
    "new_logic_steps",
    "report_tool_progress",
    "step_grammar",
    "tool_progress_reporter",
    "tool_title",
    "validate_manifest_complete",
]
