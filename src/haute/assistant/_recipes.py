"""Small, deterministic graph-edit recipes for common pipeline idioms."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, cast

from haute._cache import canonical_json
from haute._graph_utils import _sanitize_func_name
from haute.assistant._wire_ops import OpValidationError, parse_ops


class RecipeError(Exception):
    """A stable recipe failure suitable for returning to an assistant client."""

    def __init__(self, code: str, message: str, /, **context: object) -> None:
        super().__init__(message)
        self.code = code
        self.context = MappingProxyType(dict(context))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _descriptor(
    identifier: str, summary: str, required: list[str], examples: list[str]
) -> Mapping[str, object]:
    def string_schema(description: str) -> dict[str, object]:
        return {"type": "string", "minLength": 1, "description": description}

    graph_name = string_schema(
        "New graph node name exactly as requested; distinct from every output column name."
    )
    source = string_schema("Existing saved graph node id that supplies the recipe input.")
    output_name = string_schema(
        "Optional new response output graph node connected after the recipe node."
    )
    output_columns = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "description": "Columns selected into the optional response output.",
        "items": string_schema(
            "Simple JSON-field column name to map to the same response field name."
        ),
    }
    continuous_rule = {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "One continuous rule with a required first comparison and optional second bound."
        ),
        "properties": {
            "op1": {"enum": ["<", "<=", ">", ">=", "=", "=="]},
            "val1": {"type": "number"},
            "op2": {"enum": ["<", "<=", ">", ">=", "=", "=="]},
            "val2": {"type": "number"},
            "assignment": string_schema("Band label assigned when the comparisons match."),
        },
        "required": ["op1", "val1", "assignment"],
    }
    categorical_rule = {
        "type": "object",
        "additionalProperties": False,
        "description": "One exact categorical value-to-band assignment.",
        "properties": {
            "value": {
                "type": ["string", "number", "boolean"],
                "description": "Non-null finite JSON scalar matched exactly.",
            },
            "assignment": string_schema("Band label assigned when the value matches."),
        },
        "required": ["value", "assignment"],
    }
    rating_entry = {
        "type": "object",
        "additionalProperties": False,
        "description": "One positional rating-table row.",
        "properties": {
            "factor_values": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "description": (
                    "Non-null finite JSON scalar values aligned positionally with factors."
                ),
                "items": {
                    "type": ["string", "number", "boolean"],
                    "description": "One factor value; null and non-finite numbers are invalid.",
                },
            },
            "value": {
                "type": "number",
                "description": "Finite numeric rating value for this factor combination.",
            },
        },
        "required": ["factor_values", "value"],
    }
    rating_table = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "factors": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "items": string_schema("Existing factor column, in row-value order."),
            },
            "output_column": string_schema("New output column produced by this table."),
            "entries": {
                "type": "array",
                "minItems": 1,
                "items": rating_entry,
            },
            "default_value": {
                "type": "number",
                "description": "Finite fallback rating value when no entry matches.",
            },
        },
        "required": ["factors", "output_column", "entries", "default_value"],
    }
    rating_combined_output = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "output_column": string_schema("New combined rating output column."),
            "operation": {"enum": ["multiply", "add", "min", "max"]},
            "base_value": {
                "type": "number",
                "description": "Finite numeric base included in the combination.",
            },
        },
        "required": ["output_column", "operation", "base_value"],
    }
    showcase_source = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": string_schema("Safe project-relative .parquet file path."),
            "name": graph_name,
        },
        "required": ["path", "name"],
    }
    schemas: dict[str, dict[str, object]] = {
        "categorical_banding": {
            "source": source,
            "name": graph_name,
            "column": string_schema("Existing categorical input column to band."),
            "output_column": string_schema(
                "New output column that receives the band label; not the graph node name."
            ),
            "rules": {
                "type": "array",
                "minItems": 1,
                "description": "Closed exact value-to-assignment categorical rules.",
                "items": categorical_rule,
            },
            "default": string_schema("Fallback band label when no rule matches."),
        },
        "continuous_banding": {
            "source": source,
            "name": graph_name,
            "column": string_schema("Existing numeric input column to band."),
            "output_column": string_schema(
                "New output column that receives the band label; not the graph node name."
            ),
            "rules": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Canonical continuous rules. Each object requires op1 (<, <=, >, >=, "
                    "=, or ==), numeric val1, and non-empty assignment. A second bound "
                    "requires both op2 and numeric val2."
                ),
                "items": continuous_rule,
            },
            "default": string_schema("Fallback band label when no rule matches."),
        },
        "reference_join": {
            "base_source": string_schema("Existing main/base graph node id."),
            "reference_source": string_schema("Existing joining/reference graph node id."),
            "name": graph_name,
            "how": {
                "enum": ["inner", "left", "right", "full", "semi", "anti", "cross"],
                "description": "Join mode.",
            },
            "left_on": {
                "type": "array",
                "minItems": 1,
                "description": "Ordered join columns on the main/base input.",
                "items": string_schema("Main/base join column."),
            },
            "right_on": {
                "type": "array",
                "minItems": 1,
                "description": "Ordered join columns on the joining/reference input.",
                "items": string_schema("Joining/reference join column."),
            },
        },
        "parquet_showcase": {
            "base": showcase_source,
            "reference": showcase_source,
            "join_name": graph_name,
            "join_key": string_schema("Column present in both Parquet sources."),
            "transform_name": graph_name,
        },
        "response_output": {
            "source": source,
        },
        "rating_step": {
            "source": source,
            "name": graph_name,
            "tables": {
                "type": "array",
                "minItems": 1,
                "description": "Closed positional rating-table configurations.",
                "items": rating_table,
            },
            "combined_outputs": {
                "type": "array",
                "description": "Optional closed combined-output configurations.",
                "items": rating_combined_output,
            },
        },
    }
    for recipe_id, schema in schemas.items():
        schema["output_name"] = output_name
        if recipe_id != "parquet_showcase":
            schema["output_columns"] = output_columns
    properties = schemas[identifier]
    return cast(
        Mapping[str, object],
        _freeze(
            {
                "id": identifier,
                "version": "1",
                "summary": summary,
                "use_cases": [summary],
                "argument_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": required,
                    "properties": properties,
                },
                "unresolved_decisions": [
                    "Supply every material matching, banding, or rating choice."
                ],
                "preconditions": ["Referenced source nodes and columns have been inspected."],
                "allowed_operations": ["add_node", "add_edge"],
                "postconditions": ["The created node is connected to every declared input."],
                "examples": examples,
                "errors": ["unknown_recipe", "recipe_argument_invalid", "recipe_plan_invalid"],
            }
        ),
    )


_RECIPES = tuple(
    sorted(
        (
            _descriptor(
                "categorical_banding",
                "Create a categorical banding factor.",
                ["source", "name", "column", "output_column", "rules", "default"],
                ["discrete_banding"],
            ),
            _descriptor(
                "continuous_banding",
                "Create a continuous banding factor.",
                ["source", "name", "column", "output_column", "rules", "default"],
                ["continuous_banding"],
            ),
            _descriptor(
                "reference_join",
                "Join a base flow to a reference source.",
                ["base_source", "reference_source", "name", "how", "left_on", "right_on"],
                ["reference_join"],
            ),
            _descriptor(
                "parquet_showcase",
                "Build a coherent multi-node showcase from two Parquet sources.",
                [
                    "base",
                    "reference",
                    "join_name",
                    "join_key",
                    "transform_name",
                    "output_name",
                ],
                ["minimal_batch", "reference_join"],
            ),
            _descriptor(
                "response_output",
                "Create a mapped JSON response output.",
                ["source", "output_name", "output_columns"],
                ["minimal_live_quote"],
            ),
            _descriptor(
                "rating_step",
                "Apply explicit lookup tables and combined outputs.",
                ["source", "name", "tables"],
                ["rating_step"],
            ),
        ),
        key=lambda item: str(item["id"]),
    )
)
_BY_ID: dict[str, Mapping[str, object]] = {str(item["id"]): item for item in _RECIPES}
_BANDING_ROUTE_TERMS = frozenset(
    {"band", "bands", "banded", "banding", "bucket", "bucketed", "bucketing"}
)
_SHOWCASE_AUTHORING_TERMS = frozenset({"author", "build", "create", "make"})
_DATASET_DIRECTORY_PATTERNS = (
    re.compile(
        r"\b(?:in|from|under)\s+(?:the\s+)?([A-Za-z0-9_-]+)\s+(?:folder|directory)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in|from|under)\s+(?:the\s+)?(?:folder|directory)\s+"
        r"(?:called|named)\s+([A-Za-z0-9_-]+)\b",
        re.IGNORECASE,
    ),
)
_JOIN_ROUTE_TERMS = frozenset({"join", "joins", "joined", "joining"})
_CONTINUOUS_ROUTE_CUES = frozenset(
    {
        "continuous",
        "range",
        "continuously",
        "ranges",
        "breakpoint",
        "breakpoints",
        "bucket",
        "bucketed",
        "bucketing",
    }
)
_DISCRETE_ROUTE_CUES = frozenset({"categorical", "categories", "category", "discrete"})
_MATERIAL_RATING_INTENT = re.compile(r"\brating\b.{0,80}\bfactors?\b", re.IGNORECASE)
_EXPLICITLY_WITHHELD_RATING_MATERIAL = re.compile(
    r"(?:\b(?:do\s+not|don['’]t|not)\s+(?:supply|provide|specify)\b.{0,120}"
    r"\b(?:factor\s+values?|missing[- ]factor\s+policy|default)\b"
    r"|\bwithout\b.{0,120}\b(?:factor\s+values?|missing[- ]factor\s+policy|default)\b)",
    re.IGNORECASE,
)
_EXPLANATION_ONLY_REQUEST = re.compile(
    r"^\s*(?:please\s+)?(?:explain\b|describe\b|show\s+me\s+how\b|how\b|what\b)",
    re.IGNORECASE,
)
_SEQUENCED_AUTHORING_REQUEST = re.compile(
    r"(?:[,;]\s*(?:and\s+)?(?:then\s+)?|\b(?:and\s+then|then|also|afterwards)\s+)"
    r"(?:please\s+)?"
    r"(?:build|add|change|update|connect|remove|delete|create|rename|configure|edit|author|make)\b",
    re.IGNORECASE,
)


def is_explanation_only_request(request: str) -> bool:
    """Return whether the request opens as an explanation, not an instruction."""

    return bool(
        _EXPLANATION_ONLY_REQUEST.match(request)
        and _SEQUENCED_AUTHORING_REQUEST.search(request) is None
    )


def request_requires_material_clarification(request: str) -> bool:
    """Identify an explicit refusal to supply required rating decisions."""

    if is_explanation_only_request(request):
        return False
    return bool(
        _MATERIAL_RATING_INTENT.search(request)
        and _EXPLICITLY_WITHHELD_RATING_MATERIAL.search(request)
    )


def explicit_dataset_directory(request: str) -> str | None:
    """Return one safe simple directory explicitly named by the user."""

    for pattern in _DATASET_DIRECTORY_PATTERNS:
        if (match := pattern.search(request)) is not None:
            name = match.group(1)
            if name.casefold() not in {"credentials", "secrets"}:
                return name
    return None


def route_recipe_request(request: str) -> str | None:
    """Suggest one conservative recipe for prompt guidance, never as authority."""

    if is_explanation_only_request(request):
        return None
    tokens = [token.casefold() for token in re.findall("[A-Za-z]+", request)]
    token_set = set(tokens)
    matches: list[str] = []
    has_banding_term = bool(token_set.intersection(_BANDING_ROUTE_TERMS))
    has_continuous_cue = bool(token_set.intersection(_CONTINUOUS_ROUTE_CUES)) or bool(
        re.search(r"(?:<=|>=|<|>)", request)
    )
    has_discrete_cue = bool(token_set.intersection(_DISCRETE_ROUTE_CUES))
    has_showcase_cue = (
        "showcase" in token_set
        or any(left == "node" and right == "types" for left, right in zip(tokens, tokens[1:]))
        or {"many", "types"}.issubset(token_set)
    )
    routes_parquet_showcase = (
        "pipeline" in token_set
        and bool(token_set.intersection({"parquet", "parquets"}))
        and bool(token_set.intersection(_SHOWCASE_AUTHORING_TERMS))
        and has_showcase_cue
    )
    routes_continuous_banding = has_banding_term and has_continuous_cue and not has_discrete_cue
    routes_categorical_banding = has_banding_term and has_discrete_cue and not has_continuous_cue
    if routes_continuous_banding:
        matches.append("continuous_banding")
    if routes_categorical_banding:
        matches.append("categorical_banding")
    if token_set.intersection(_JOIN_ROUTE_TERMS):
        matches.append("reference_join")
    if any(left == "rating" and right == "step" for left, right in zip(tokens, tokens[1:])):
        matches.append("rating_step")
    if routes_parquet_showcase:
        matches.append("parquet_showcase")
    has_response_output = any(
        left == "response" and right == "output" for left, right in zip(tokens, tokens[1:])
    )
    if has_response_output and not matches:
        matches.append("response_output")
    if (
        has_banding_term
        and not routes_continuous_banding
        and not routes_categorical_banding
        and matches
    ):
        return None
    return matches[0] if len(matches) == 1 else None


def recipe_manifest() -> tuple[Mapping[str, object], ...]:
    """Return the versioned immutable recipe descriptors in stable ID order."""
    return _RECIPES


def recipe_descriptor(recipe_id: str) -> Mapping[str, object]:
    try:
        return _BY_ID[recipe_id]
    except KeyError as exc:
        raise RecipeError(
            "unknown_recipe", f"Unknown recipe {recipe_id!r}.", valid_ids=tuple(_BY_ID)
        ) from exc


def _arguments(recipe_id: str, raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RecipeError("recipe_argument_invalid", "Recipe arguments must be an object.")
    descriptor = recipe_descriptor(recipe_id)
    schema = descriptor["argument_schema"]
    assert isinstance(schema, Mapping)
    required = schema["required"]
    assert isinstance(required, tuple)
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    unknown = sorted(set(raw) - set(properties))
    missing = [key for key in required if key not in raw]
    if unknown or missing:
        details = []
        if missing:
            details.append("missing required arguments: " + ", ".join(missing))
        if unknown:
            details.append("unknown arguments: " + ", ".join(unknown))
        raise RecipeError(
            "recipe_argument_invalid",
            "; ".join(details),
            missing=tuple(missing),
            unknown=tuple(unknown),
        )
    values = deepcopy(dict(raw))
    for key, value in values.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise RecipeError(
                "recipe_argument_invalid", f"Argument {key!r} must not be blank.", argument=key
            )
    return values


_SUPPORTED_CONTINUOUS_OPERATORS = frozenset({"<", "<=", ">", ">=", "=", "=="})
_CONTINUOUS_RULE_KEYS = frozenset({"op1", "val1", "op2", "val2", "assignment"})


def _validate_continuous_rules(raw: object) -> None:
    if not isinstance(raw, list) or not raw:
        raise RecipeError(
            "recipe_argument_invalid",
            "Argument 'rules' must be a non-empty list.",
            argument="rules",
        )
    for index, rule in enumerate(raw):
        argument = f"rules[{index}]"
        if not isinstance(rule, Mapping):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} must be an object.",
                argument=argument,
            )
        unknown = set(rule).difference(_CONTINUOUS_RULE_KEYS)
        required = {"op1", "val1", "assignment"}
        if unknown or not required.issubset(rule):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} is not a closed continuous rule.",
                argument=argument,
            )
        if rule["op1"] not in _SUPPORTED_CONTINUOUS_OPERATORS:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} has an unsupported op1.",
                argument=argument,
            )
        val1 = rule["val1"]
        if (
            isinstance(val1, bool)
            or not isinstance(val1, int | float)
            or not math.isfinite(float(val1))
        ):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} requires a finite numeric val1.",
                argument=argument,
            )
        assignment = rule["assignment"]
        if not isinstance(assignment, str) or not assignment.strip():
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} requires a non-empty assignment.",
                argument=argument,
            )
        has_op2 = "op2" in rule
        has_val2 = "val2" in rule
        if has_op2 != has_val2:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} requires op2 and val2 together.",
                argument=argument,
            )
        if has_op2:
            val2 = rule["val2"]
            if (
                rule["op2"] not in _SUPPORTED_CONTINUOUS_OPERATORS
                or isinstance(val2, bool)
                or not isinstance(val2, int | float)
                or not math.isfinite(float(val2))
            ):
                raise RecipeError(
                    "recipe_argument_invalid",
                    f"Argument {argument!r} has an invalid second bound.",
                    argument=argument,
                )


_CATEGORICAL_RULE_KEYS = frozenset({"value", "assignment"})


def _validate_categorical_rules(raw: object) -> None:
    if not isinstance(raw, list) or not raw:
        raise RecipeError(
            "recipe_argument_invalid",
            "Argument 'rules' must be a non-empty list.",
            argument="rules",
        )
    seen_values: set[tuple[type, object]] = set()
    for index, rule in enumerate(raw):
        argument = f"rules[{index}]"
        if not isinstance(rule, Mapping) or set(rule) != _CATEGORICAL_RULE_KEYS:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} is not a closed categorical rule.",
                argument=argument,
            )
        value = rule["value"]
        if (
            value is None
            or not isinstance(value, str | int | float | bool)
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.value must be a non-null finite JSON scalar.",
                argument=f"{argument}.value",
            )
        identity = (type(value), value)
        if identity in seen_values:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.value duplicates an earlier rule.",
                argument=f"{argument}.value",
            )
        seen_values.add(identity)
        assignment = rule["assignment"]
        if not isinstance(assignment, str) or not assignment.strip():
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.assignment must be a non-empty string.",
                argument=f"{argument}.assignment",
            )


_SIMPLE_OUTPUT_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _output_operations(
    values: Mapping[str, Any],
    *,
    source: str,
    source_port: str,
) -> list[dict[str, object]]:
    output_name = values.get("output_name")
    raw_columns = values.get("output_columns")
    has_name = output_name is not None
    has_columns = raw_columns is not None
    if has_name != has_columns:
        raise RecipeError(
            "recipe_argument_invalid",
            "output_name and output_columns must be supplied together.",
            argument="output",
        )
    if not has_name:
        return []
    if not isinstance(output_name, str) or not output_name.strip():
        raise RecipeError(
            "recipe_argument_invalid",
            "output_name must be a non-empty string.",
            argument="output_name",
        )
    if (
        not isinstance(raw_columns, list)
        or not raw_columns
        or any(
            not isinstance(column, str) or _SIMPLE_OUTPUT_COLUMN.fullmatch(column) is None
            for column in raw_columns
        )
        or len(raw_columns) != len(set(raw_columns))
    ):
        raise RecipeError(
            "recipe_argument_invalid",
            "output_columns must be a non-empty unique list of simple JSON-field names.",
            argument="output_columns",
        )
    mappings = [
        {
            "source_port": source_port,
            "source_column": column,
            "output_path": f"$[:].{column}",
            "enabled": True,
        }
        for column in raw_columns
    ]
    return [
        {
            "op": "add_node",
            "node_type": "output",
            "name": output_name,
            "ref": "recipe_output",
            "config": {"outputMapping": mappings, "outputFormat": "json"},
        },
        {"op": "add_edge", "source": source, "target": "$recipe_output"},
    ]


_SHOWCASE_SOURCE_KEYS = frozenset({"path", "name"})


def _showcase_source(value: object, *, argument: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _SHOWCASE_SOURCE_KEYS:
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r} must contain exactly 'path' and 'name'.",
            argument=argument,
        )
    path = value["path"]
    name = value["name"]
    if not isinstance(path, str) or not path.strip():
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r}.path must be a non-empty string.",
            argument=f"{argument}.path",
        )
    if not isinstance(name, str) or not name.strip():
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r}.name must be a non-empty string.",
            argument=f"{argument}.name",
        )
    parsed = PurePosixPath(path)
    if (
        "\\" in path
        or re.match(r"^[A-Za-z]:", path)
        or parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.suffix.casefold() != ".parquet"
    ):
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r}.path must be a safe project-relative .parquet path.",
            argument=f"{argument}.path",
        )
    return path, name


_RATING_TABLE_KEYS = frozenset({"factors", "output_column", "entries", "default_value"})
_RATING_ENTRY_KEYS = frozenset({"factor_values", "value"})
_RATING_COMBINED_KEYS = frozenset({"output_column", "operation", "base_value"})
_RATING_COMBINE_OPERATIONS = frozenset({"multiply", "add", "min", "max"})


def _finite_rating_number(value: object, *, argument: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r} must be a finite number.",
            argument=argument,
        )
    return float(value)


def _closed_rating_object(
    value: object, *, keys: frozenset[str], argument: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise RecipeError(
            "recipe_argument_invalid",
            f"Argument {argument!r} must contain exactly {sorted(keys)!r}.",
            argument=argument,
        )
    return value


def _rating_config(values: Mapping[str, Any]) -> dict[str, object]:
    raw_tables = values["tables"]
    if not isinstance(raw_tables, list) or not raw_tables:
        raise RecipeError(
            "recipe_argument_invalid",
            "Rating tables must be a non-empty list.",
            argument="tables",
        )

    tables: list[dict[str, object]] = []
    for table_index, raw_table in enumerate(raw_tables):
        table_argument = f"tables[{table_index}]"
        table = _closed_rating_object(raw_table, keys=_RATING_TABLE_KEYS, argument=table_argument)
        factors = table["factors"]
        if (
            not isinstance(factors, list)
            or not 1 <= len(factors) <= 3
            or any(not isinstance(factor, str) or not factor.strip() for factor in factors)
            or len(factors) != len(set(factors))
        ):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {table_argument!r}.factors must be one to three unique columns.",
                argument=f"{table_argument}.factors",
            )
        output_column = table["output_column"]
        if not isinstance(output_column, str) or not output_column.strip():
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {table_argument!r}.output_column must be a column name.",
                argument=f"{table_argument}.output_column",
            )
        raw_entries = table["entries"]
        if not isinstance(raw_entries, list) or not raw_entries:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {table_argument!r}.entries must be a non-empty list.",
                argument=f"{table_argument}.entries",
            )

        entries: list[dict[str, object]] = []
        for entry_index, raw_entry in enumerate(raw_entries):
            entry_argument = f"{table_argument}.entries[{entry_index}]"
            entry = _closed_rating_object(
                raw_entry, keys=_RATING_ENTRY_KEYS, argument=entry_argument
            )
            factor_values = entry["factor_values"]
            factor_argument = f"{entry_argument}.factor_values"
            if not isinstance(factor_values, list) or len(factor_values) != len(factors):
                raise RecipeError(
                    "recipe_argument_invalid",
                    f"Argument {factor_argument!r} must align one value to each factor.",
                    argument=factor_argument,
                )
            for value_index, factor_value in enumerate(factor_values):
                value_argument = f"{factor_argument}[{value_index}]"
                if (
                    factor_value is None
                    or not isinstance(factor_value, str | int | float | bool)
                    or (isinstance(factor_value, float) and not math.isfinite(factor_value))
                ):
                    raise RecipeError(
                        "recipe_argument_invalid",
                        f"Argument {value_argument!r} must be a non-null finite JSON scalar.",
                        argument=value_argument,
                    )
            entries.append(
                {
                    **dict(zip(factors, deepcopy(factor_values), strict=True)),
                    "value": _finite_rating_number(
                        entry["value"], argument=f"{entry_argument}.value"
                    ),
                }
            )
        tables.append(
            {
                "factors": deepcopy(factors),
                "outputColumn": output_column,
                "entries": entries,
                "defaultValue": _finite_rating_number(
                    table["default_value"], argument=f"{table_argument}.default_value"
                ),
            }
        )

    raw_combined = values.get("combined_outputs", [])
    if not isinstance(raw_combined, list):
        raise RecipeError(
            "recipe_argument_invalid",
            "combined_outputs must be a list when supplied.",
            argument="combined_outputs",
        )
    combined_outputs: list[dict[str, object]] = []
    for combined_index, raw_output in enumerate(raw_combined):
        combined_argument = f"combined_outputs[{combined_index}]"
        output = _closed_rating_object(
            raw_output, keys=_RATING_COMBINED_KEYS, argument=combined_argument
        )
        output_column = output["output_column"]
        if not isinstance(output_column, str) or not output_column.strip():
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {combined_argument!r}.output_column must be a column name.",
                argument=f"{combined_argument}.output_column",
            )
        operation = output["operation"]
        if operation not in _RATING_COMBINE_OPERATIONS:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {combined_argument!r}.operation is unsupported.",
                argument=f"{combined_argument}.operation",
            )
        combined_outputs.append(
            {
                "outputColumn": output_column,
                "operation": operation,
                "baseValue": _finite_rating_number(
                    output["base_value"], argument=f"{combined_argument}.base_value"
                ),
            }
        )

    config: dict[str, Any] = {
        "tables": tables,
        "combinedOutputs": combined_outputs,
    }
    try:
        from haute._rating import _normalise_combined_outputs
        from haute._rating_step_config import normalise_rating_step_config

        config = normalise_rating_step_config(config)
        config["combinedOutputs"] = _normalise_combined_outputs(config)
    except ValueError as exc:
        raise RecipeError(
            "recipe_argument_invalid",
            str(exc),
            argument="rating_step",
        ) from exc
    return cast(dict[str, object], config)


def plan_recipe(recipe_id: str, args: object) -> dict[str, object]:
    """Produce canonical primitive operations without reading or mutating a graph."""
    values = _arguments(recipe_id, args)
    if recipe_id == "response_output":
        ref = None
        operations = _output_operations(
            values,
            source=values["source"],
            source_port=values["source"],
        )
    elif recipe_id == "parquet_showcase":
        base_path, base_name = _showcase_source(values["base"], argument="base")
        reference_path, reference_name = _showcase_source(values["reference"], argument="reference")
        join_name = values["join_name"]
        join_key = values["join_key"]
        transform_name = values["transform_name"]
        if not isinstance(join_key, str) or not join_key.strip():
            raise RecipeError(
                "recipe_argument_invalid",
                "The parquet showcase join key must be a non-empty column name.",
                argument="join_key",
            )
        key_text = f"{join_key}_text"
        # The transform's single input is the join node; its edge-derived
        # parameter name is the sanitised join label. `df` is only the output
        # variable, so the code must start from that named input.
        join_input = _sanitize_func_name(join_name)
        transform_code = (
            f"df = {join_input}.with_columns(\n"
            f"    pl.col({json.dumps(join_key)}).cast(pl.String).alias({json.dumps(key_text)}),\n"
            '    pl.lit("haute_showcase").alias("showcase_stage"),\n'
            ")"
        )
        showcase_values = {
            **values,
            "output_columns": [join_key, key_text, "showcase_stage"],
        }
        output_name = values["output_name"]
        names = [base_name, reference_name, join_name, transform_name, output_name]
        if len(names) != len(set(names)):
            raise RecipeError(
                "recipe_argument_invalid",
                "Every parquet showcase node name must be distinct.",
                argument="name",
            )
        if base_path == reference_path:
            raise RecipeError(
                "recipe_argument_invalid",
                "The base and reference Parquet paths must be distinct.",
                argument="reference.path",
            )
        ref = None
        operations = [
            {
                "op": "add_node",
                "node_type": "dataInput",
                "name": base_name,
                "ref": "recipe_showcase_base",
                "config": {
                    "inputType": "file",
                    "format": "parquet",
                    "path": base_path,
                    "mode": "scan",
                },
            },
            {
                "op": "add_node",
                "node_type": "dataInput",
                "name": reference_name,
                "ref": "recipe_showcase_reference",
                "config": {
                    "inputType": "file",
                    "format": "parquet",
                    "path": reference_path,
                    "mode": "scan",
                },
            },
            {
                "op": "add_node",
                "node_type": "edgeJoin",
                "name": join_name,
                "ref": "recipe_showcase_join",
                "config": {
                    "baseInput": base_name,
                    "joinInput": reference_name,
                    "how": "left",
                    "leftOn": [join_key],
                    "rightOn": [join_key],
                },
            },
            {
                "op": "add_edge",
                "source": "$recipe_showcase_base",
                "target": "$recipe_showcase_join",
                "target_handle": "base",
            },
            {
                "op": "add_edge",
                "source": "$recipe_showcase_reference",
                "target": "$recipe_showcase_join",
                "target_handle": "join",
            },
            {
                "op": "add_node",
                "node_type": "polars",
                "name": transform_name,
                "ref": "recipe_showcase_transform",
                "config": {"code": transform_code},
            },
            {
                "op": "add_edge",
                "source": "$recipe_showcase_join",
                "target": "$recipe_showcase_transform",
            },
        ]
        operations.extend(
            _output_operations(
                showcase_values,
                source="$recipe_showcase_transform",
                source_port=transform_name,
            )
        )
    elif recipe_id == "continuous_banding":
        _validate_continuous_rules(values["rules"])
        ref = "recipe_banding"
        operations = [
            {
                "op": "add_node",
                "node_type": "banding",
                "name": values["name"],
                "ref": ref,
                "config": {
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": values["column"],
                            "outputColumn": values["output_column"],
                            "rules": values["rules"],
                            "default": values["default"],
                        }
                    ]
                },
            },
            {"op": "add_edge", "source": values["source"], "target": f"${ref}"},
        ]
    elif recipe_id == "categorical_banding":
        _validate_categorical_rules(values["rules"])
        ref = "recipe_categorical_banding"
        operations = [
            {
                "op": "add_node",
                "node_type": "banding",
                "name": values["name"],
                "ref": ref,
                "config": {
                    "factors": [
                        {
                            "banding": "categorical",
                            "column": values["column"],
                            "outputColumn": values["output_column"],
                            "rules": values["rules"],
                            "default": values["default"],
                        }
                    ]
                },
            },
            {"op": "add_edge", "source": values["source"], "target": f"${ref}"},
        ]
    elif recipe_id == "reference_join":
        if not all(
            isinstance(values[key], list)
            and values[key]
            and all(isinstance(item, str) and item for item in values[key])
            for key in ("left_on", "right_on")
        ):
            raise RecipeError("recipe_argument_invalid", "Join keys must be non-empty lists.")
        if len(values["left_on"]) != len(values["right_on"]):
            raise RecipeError(
                "recipe_argument_invalid",
                "left_on and right_on must contain the same number of join keys.",
            )
        if values["base_source"] == values["reference_source"]:
            raise RecipeError(
                "recipe_argument_invalid",
                "base_source and reference_source must be distinct nodes.",
            )
        if values["how"] not in {"inner", "left", "right", "full", "semi", "anti", "cross"}:
            raise RecipeError("recipe_argument_invalid", "Unsupported reference join mode.")
        ref = "recipe_reference_join"
        operations = [
            {
                "op": "add_node",
                "node_type": "edgeJoin",
                "name": values["name"],
                "ref": ref,
                "config": {
                    "baseInput": values["base_source"],
                    "joinInput": values["reference_source"],
                    "how": values["how"],
                    "leftOn": values["left_on"],
                    "rightOn": values["right_on"],
                },
            },
            {
                "op": "add_edge",
                "source": values["base_source"],
                "target": f"${ref}",
                "target_handle": "base",
            },
            {
                "op": "add_edge",
                "source": values["reference_source"],
                "target": f"${ref}",
                "target_handle": "join",
            },
        ]
    elif recipe_id == "rating_step":
        ref = "recipe_rating_step"
        operations = [
            {
                "op": "add_node",
                "node_type": "ratingStep",
                "name": values["name"],
                "ref": ref,
                "config": _rating_config(values),
            },
            {"op": "add_edge", "source": values["source"], "target": f"${ref}"},
        ]
    else:
        raise AssertionError(f"Unhandled recipe: {recipe_id}")
    if ref is not None:
        operations.extend(
            _output_operations(
                values,
                source=f"${ref}",
                source_port=values["name"],
            )
        )
    try:
        parse_ops(operations)
    except OpValidationError as exc:
        raise RecipeError(
            "recipe_plan_invalid", "Recipe generated invalid primitive operations."
        ) from exc
    result: dict[str, object] = {
        "recipe_id": recipe_id,
        "version": "1",
        "operations": operations,
        "postconditions": [
            *[
                {"kind": "node_exists", "node": f"${operation['ref']}"}
                for operation in operations
                if operation["op"] == "add_node" and isinstance(operation.get("ref"), str)
            ],
            *[
                {
                    "kind": "edge_exists",
                    "source": operation["source"],
                    "target": operation["target"],
                    **{
                        key: operation[key]
                        for key in ("source_handle", "target_handle")
                        if key in operation
                    },
                }
                for operation in operations
                if operation["op"] == "add_edge"
            ],
        ],
    }
    result["recipe_plan_hash"] = hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()
    return result


__all__ = [
    "RecipeError",
    "explicit_dataset_directory",
    "is_explanation_only_request",
    "plan_recipe",
    "recipe_descriptor",
    "recipe_manifest",
    "request_requires_material_clarification",
    "route_recipe_request",
]
