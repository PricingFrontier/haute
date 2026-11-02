"""A transform's keyword-only parameters: the configuration its signature binds.

``def scaled(source, *, factor=1.2)`` binds ``factor`` for the body as a
parameter does, over any preamble name of the same name. A transform written as
code keeps such a binding in its config's ``keyword_parameters``, so the
generated function and canvas execution read the same value; every keyword-only
form that config cannot carry back is refused where it is read, never dropped
by the next save (specs/pipeline-config, "Keyword-only parameters").
"""

from __future__ import annotations

import ast
import keyword
import math
from typing import Any

from haute._executable_names import RESERVED_NAMES
from haute._types import KeywordParameter, NodeType
from haute.errors import ConfigError, ParseError

__all__ = [
    "KEYWORD_PARAMETERS_KEY",
    "keyword_parameter_values",
    "keyword_parameters_from_signature",
    "reject_input_named_keyword_parameters",
    "validate_keyword_parameters",
]

KEYWORD_PARAMETERS_KEY = "keyword_parameters"

#: Names node code reserves: the output slot, and the module's own bindings.
_RESERVED = frozenset({"df", *RESERVED_NAMES})

#: A list or dict default is one object shared by every call of a Python function, but a
#: canvas run binds the stored value afresh, so code that changed it would compute
#: differently in the two: only immutable scalars are carried.
_LITERAL_RULE = "None, True or False, a finite number, or a string"


def _is_scalar_literal(value: object) -> bool:
    """Whether *value* is an immutable scalar a decorator keyword can carry."""
    if value is None or isinstance(value, (bool, int, str)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _name_problem(name: object) -> str | None:
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        return "is not a Python name"
    if name in _RESERVED:
        return "is reserved for node code"
    return None


def keyword_parameters_from_signature(
    arguments: ast.arguments,
    *,
    node_type: NodeType,
    stepped: bool,
    node_id: str,
) -> list[KeywordParameter]:
    """The bindings a node function's keyword-only parameters declare, in order.

    Only a transform written as code carries them; an External File hook's
    ``obj`` is its loaded object, not a binding. Anything else is a located
    ``ParseError``.
    """

    def refuse(at: ast.AST, message: str) -> ParseError:
        return ParseError(
            f"{node_id}: {message}",
            node_id=node_id,
            line=getattr(at, "lineno", None),
            column=getattr(at, "col_offset", None),
        )

    if arguments.kwarg is not None:
        raise refuse(
            arguments.kwarg,
            f"the **{arguments.kwarg.arg} parameter cannot be kept in the pipeline file. "
            "Remove it; give each setting its own keyword-only parameter with a default.",
        )
    pairs = list(zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True))
    if not pairs:
        return []
    if node_type is NodeType.EXTERNAL_FILE:
        obj, default = pairs[0]
        if len(pairs) == 1 and obj.arg == "obj" and default is None:
            return []
        extra = next(arg for arg, value in pairs if arg.arg != "obj" or value is not None)
        raise refuse(
            extra,
            f"a Load File function takes only the keyword-only parameter obj, without a "
            f"default, but declares {extra.arg!r}. Remove it, and read settings from the "
            "preamble or global constants.",
        )
    if node_type is not NodeType.POLARS or stepped:
        where = "a Transform built from steps" if stepped else f"a {node_type.value} node"
        raise refuse(
            pairs[0][0],
            f"the keyword-only parameter {pairs[0][0].arg!r} cannot be kept on {where}. "
            "Remove it, and read the value from the preamble or global constants.",
        )
    parameters: list[KeywordParameter] = []
    for arg, default in pairs:
        problem = _name_problem(arg.arg)
        if problem is not None:
            raise refuse(arg, f"the keyword-only parameter {arg.arg!r} {problem}; rename it.")
        if default is None:
            raise refuse(
                arg,
                f"the keyword-only parameter {arg.arg!r} has no default, so a run cannot "
                "bind it. Give it a literal default.",
            )
        try:
            value = ast.literal_eval(default)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            literal = False
        else:
            literal = _is_scalar_literal(value)
        if not literal:
            raise refuse(
                default,
                f"the default of the keyword-only parameter {arg.arg!r} must be a literal "
                f"value ({_LITERAL_RULE}). Write the value itself, or read it from the "
                "preamble in the body.",
            )
        parameters.append({"name": arg.arg, "default": value})
    return parameters


def validate_keyword_parameters(
    config: dict[str, Any], *, node_label: str | None = None
) -> list[KeywordParameter]:
    """A config's bindings, held to the contract the parser records them under."""
    if KEYWORD_PARAMETERS_KEY not in config:
        return []
    value = config[KEYWORD_PARAMETERS_KEY]
    context = {"node_label": node_label} if node_label is not None else {}

    def refuse(message: str) -> ConfigError:
        return ConfigError(f"Transform keyword_parameters {message}", **context)

    if "steps" in config:
        raise refuse(
            "cannot be kept beside steps: a Transform built from steps has no signature of "
            "its own. Switch it to code or remove the parameters."
        )
    if not isinstance(value, list) or not value:
        raise refuse("must be a non-empty list of {name, default} records.")
    seen: set[str] = set()
    for record in value:
        if not isinstance(record, dict) or set(record) != {"name", "default"}:
            raise refuse("must be a non-empty list of {name, default} records.")
        name = record["name"]
        problem = _name_problem(name)
        if problem is not None:
            raise refuse(f"name {name!r} {problem}.")
        if name in seen:
            raise refuse(f"name {name!r} appears twice.")
        seen.add(name)
        if not _is_scalar_literal(record["default"]):
            raise refuse(f"default of {name!r} must be {_LITERAL_RULE}.")
    return value


def reject_input_named_keyword_parameters(
    parameters: list[KeywordParameter],
    input_names: list[str],
    *,
    node_id: str,
    node_label: str,
) -> None:
    """Refuse a binding named like an input, which no Python signature can declare."""
    clashes = sorted({parameter["name"] for parameter in parameters} & set(input_names))
    if clashes:
        raise ConfigError(
            f"Transform keyword-only parameter(s) {clashes} have the name of an input; "
            "rename the parameter or the upstream node.",
            node_id=node_id,
            node_label=node_label,
        )


def keyword_parameter_values(parameters: list[KeywordParameter]) -> dict[str, Any]:
    """The values a run binds, by parameter name (immutable scalars)."""
    return {parameter["name"]: parameter["default"] for parameter in parameters}
