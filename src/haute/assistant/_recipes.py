"""Small, deterministic graph-edit recipes for common pipeline idioms."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

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


_CATEGORICAL_VALUE_DESCRIPTION = (
    "Column value this rule matches, as text: banding casts the column to text before "
    'matching, so a boolean is "true" or "false", an integer is its digits (e.g. "3"), '
    "and a string is matched exactly."
)
_REFERENCE_JOIN_MODES = ("inner", "left", "right", "full", "semi", "anti")


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
    categorical_rule = {
        "type": "object",
        "additionalProperties": False,
        "description": "One exact categorical value-to-band assignment.",
        "properties": {
            "value": string_schema(_CATEGORICAL_VALUE_DESCRIPTION),
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
        "reference_join": {
            "base_source": string_schema("Existing main/base graph node id."),
            "reference_source": string_schema("Existing joining/reference graph node id."),
            "name": graph_name,
            "how": {
                "enum": list(_REFERENCE_JOIN_MODES),
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
                "reference_join",
                "Join a base flow to a reference source.",
                ["base_source", "reference_source", "name", "how", "left_on", "right_on"],
                ["reference_join"],
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


_CATEGORICAL_RULE_KEYS = frozenset({"value", "assignment"})


def _categorical_text_form(value: object) -> str:
    """The text a non-string rule value must be written as to match its rows."""

    if isinstance(value, bool):
        return json.dumps("true" if value else "false")
    if isinstance(value, int):
        return json.dumps(str(value))
    return "the text the column holds for that value"


def _validate_categorical_rules(raw: object) -> None:
    if not isinstance(raw, list) or not raw:
        raise RecipeError(
            "recipe_argument_invalid",
            "Argument 'rules' must be a non-empty list.",
            argument="rules",
        )
    seen_values: set[str] = set()
    for index, rule in enumerate(raw):
        argument = f"rules[{index}]"
        if not isinstance(rule, Mapping) or set(rule) != _CATEGORICAL_RULE_KEYS:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r} is not a closed categorical rule.",
                argument=argument,
            )
        value = rule["value"]
        if not isinstance(value, str):
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.value must be a string: banding matches the "
                f"column's text form, so write it as {_categorical_text_form(value)}.",
                argument=f"{argument}.value",
            )
        if not value:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.value must be a non-empty string.",
                argument=f"{argument}.value",
            )
        if value in seen_values:
            raise RecipeError(
                "recipe_argument_invalid",
                f"Argument {argument!r}.value duplicates an earlier rule's value.",
                argument=f"{argument}.value",
            )
        seen_values.add(value)
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
    ref: str,
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
            "ref": ref,
            "config": {"outputMapping": mappings, "outputFormat": "json"},
        },
        {"op": "add_edge", "source": source, "target": f"${ref}"},
    ]


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


def expand_recipe(recipe_id: str, args: object, *, ref: str) -> list[dict[str, object]]:
    """Expand one recipe into canonical primitive operations, without reading a graph.

    The node the recipe creates declares the batch-local *ref*; a response output
    the recipe adds declares *ref* followed by ``_output``. The standalone
    ``response_output`` recipe's output node is the node it creates, so it
    declares *ref* itself.
    """

    values = _arguments(recipe_id, args)
    created: str | None = ref
    if recipe_id == "response_output":
        created = None
        operations = _output_operations(
            values,
            source=values["source"],
            source_port=values["source"],
            ref=ref,
        )
    elif recipe_id == "categorical_banding":
        _validate_categorical_rules(values["rules"])
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
        if values["how"] not in _REFERENCE_JOIN_MODES:
            raise RecipeError("recipe_argument_invalid", "Unsupported reference join mode.")
        operations = [
            {
                "op": "add_node",
                "node_type": "edgeJoin",
                "name": values["name"],
                "ref": ref,
                "config": {
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
    if created is not None:
        operations.extend(
            _output_operations(
                values,
                source=f"${created}",
                source_port=values["name"],
                ref=f"{created}_output",
            )
        )
    try:
        parse_ops(operations)
    except OpValidationError as exc:
        raise RecipeError(
            "recipe_plan_invalid", "Recipe generated invalid primitive operations."
        ) from exc
    return operations


class RecipeOperationError(RecipeError):
    """A recipe failure, located at the ``recipe`` operation of the batch that raised it."""

    def __init__(self, error: RecipeError, *, op_index: int, recipe_id: str) -> None:
        super().__init__(error.code, str(error), **dict(error.context))
        self.op_index = op_index
        self.recipe_id = recipe_id


@dataclass(frozen=True, slots=True)
class ExpandedBatch:
    """A graph-edit batch with each ``recipe`` operation expanded in place.

    ``positions[i]`` is the index, in the batch the model sent, of the
    operation expanded operation ``i`` came from; ``recipes`` maps the index
    of each ``recipe`` operation to its recipe id.
    """

    operations: list[object]
    positions: tuple[int, ...]
    recipes: Mapping[int, str]


def expand_recipe_operations(ops: object) -> ExpandedBatch:
    """Expand every ``recipe`` operation of a batch, in place and in batch order.

    The operation at index ``i`` declares the node it creates with its own
    ``ref``, or ``recipe_<i>`` without one, so no two expansions share a ref.
    Anything that is not a list passes through unchanged, for the operation
    parser to refuse.
    """

    if not isinstance(ops, list):
        return ExpandedBatch(operations=ops, positions=(), recipes={})  # type: ignore[arg-type]
    operations: list[object] = []
    positions: list[int] = []
    recipes: dict[int, str] = {}
    for index, operation in enumerate(ops):
        if not isinstance(operation, Mapping) or operation.get("op") != "recipe":
            operations.append(operation)
            positions.append(index)
            continue
        recipe_id = operation.get("recipe")
        if not isinstance(recipe_id, str):
            raise RecipeOperationError(
                RecipeError("unknown_recipe", "A recipe operation must name its recipe."),
                op_index=index,
                recipe_id="",
            )
        recipes[index] = recipe_id
        ref = operation.get("ref")
        try:
            expansion = expand_recipe(
                recipe_id,
                operation.get("arguments"),
                ref=ref if isinstance(ref, str) and ref else f"recipe_{index}",
            )
        except RecipeError as exc:
            raise RecipeOperationError(exc, op_index=index, recipe_id=recipe_id) from exc
        operations.extend(expansion)
        positions.extend(index for _operation in expansion)
    return ExpandedBatch(operations=operations, positions=tuple(positions), recipes=recipes)


__all__ = [
    "ExpandedBatch",
    "RecipeError",
    "RecipeOperationError",
    "expand_recipe",
    "expand_recipe_operations",
    "recipe_descriptor",
    "recipe_manifest",
]
