"""Pure GLM term contract shared by the config builder, routes, job, and adapter.

Terms are the RustyStats ``glm_dict`` mapping ``{name: spec}``. A *native*
spec (``linear``, ``categorical``, ``bs``, ``ns``, ``ms``, ``target_encoding``)
is keyed by the column it fits. An *expression* spec is keyed by a free name
and reads the columns named in its ``expr``. Interactions contribute their
filled factors. Nothing here imports RustyStats: the schema-free half runs
during projection planning before any data exists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from haute.errors import HauteValidationError

SUPPORTED_TERM_TYPES: frozenset[str] = frozenset(
    {"linear", "categorical", "bs", "ns", "ms", "target_encoding", "expression"}
)
NATIVE_TERM_TYPES: frozenset[str] = SUPPORTED_TERM_TYPES - {"expression"}
_REDIRECTING_KEYS: tuple[str, ...] = ("variable", "interaction")

EXPRESSION_GRAMMAR = (
    "Supported forms: 'x ** n', 'x + y', 'x - y', 'x * y', 'x / y' "
    "(y a column or a number), or a bare column 'x'."
)
_IDENT = r"[A-Za-z_]\w*"
_NUMBER = r"\d+(?:\.\d+)?"
_EXPRESSION_RE = re.compile(rf"^\s*({_IDENT})\s*(?:(\*\*|[+\-*/])\s*({_IDENT}|{_NUMBER}))?\s*$")
_NUMBER_RE = re.compile(rf"^{_NUMBER}$")


def expression_identifiers(expr: str) -> list[str]:
    """Return the column identifiers an expression reads, in order.

    Mirrors RustyStats' ``_convert_expression_to_polars`` grammar exactly;
    anything else raises before RustyStats can fail later.
    """
    match = _EXPRESSION_RE.match(expr or "")
    if match is None:
        raise HauteValidationError(
            f"GLM expression {expr!r} is not supported. {EXPRESSION_GRAMMAR}"
        )
    left, _operator, right = match.groups()
    identifiers = [left]
    if right is not None and not _NUMBER_RE.match(right) and right != left:
        identifiers.append(right)
    return identifiers


def _filled_factors(interaction: Mapping[str, Any]) -> list[str]:
    raw = interaction.get("factors", [])
    if not isinstance(raw, list):
        return []
    return [factor for factor in raw if isinstance(factor, str) and factor]


def glm_model_columns(
    terms: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """Resolve the ordered unique columns a GLM reads, without a schema.

    Raises ``HauteValidationError`` naming the offending term for an
    unsupported type, a redirecting key, an expression outside the grammar,
    or an expression key that collides with a native key or with one of the
    columns its own expression reads.
    """
    if any(not isinstance(name, str) or not name for name in terms):
        raise HauteValidationError("GLM term names must be non-empty strings")

    columns: dict[str, None] = {}
    native_keys: set[str] = set()
    expressions: dict[str, list[str]] = {}

    for name, spec in terms.items():
        if not isinstance(spec, Mapping):
            raise HauteValidationError(f"GLM term {name!r} must be a mapping with a 'type'")
        term_type = spec.get("type", "linear")
        if term_type not in SUPPORTED_TERM_TYPES:
            raise HauteValidationError(
                f"GLM term {name!r} has unsupported type {term_type!r}. "
                f"Supported types: {sorted(SUPPORTED_TERM_TYPES)}"
            )
        redirecting = [key for key in _REDIRECTING_KEYS if key in spec]
        if redirecting:
            raise HauteValidationError(
                f"GLM term {name!r} carries {redirecting}; a native term must be keyed by "
                "the column it reads and may not redirect to another column."
            )
        if term_type == "expression":
            expr = spec.get("expr")
            if not isinstance(expr, str) or not expr.strip():
                raise HauteValidationError(f"GLM expression term {name!r} has no 'expr'")
            try:
                identifiers = expression_identifiers(expr)
            except HauteValidationError as exc:
                raise HauteValidationError(f"GLM term {name!r}: {exc}") from None
            if name in identifiers:
                raise HauteValidationError(
                    f"GLM expression term {name!r} is keyed by a column it reads; "
                    "give the expression a name that is not a column."
                )
            expressions[name] = identifiers
        else:
            native_keys.add(name)
            columns[name] = None

    for name, identifiers in expressions.items():
        if name in native_keys:
            raise HauteValidationError(
                f"GLM expression term {name!r} collides with the native term of the same "
                "name; a column key always means a native fit."
            )
        for identifier in identifiers:
            columns[identifier] = None

    for interaction in interactions or []:
        for factor in _filled_factors(interaction):
            columns[factor] = None

    return list(columns)


def validate_glm_model_columns(
    terms: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]] | None,
    schema: Iterable[str],
) -> list[str]:
    """Resolve model columns and check them against a real schema.

    Every resolved column must exist, and no expression may be keyed by a
    schema column (that key would silently shadow a native fit).
    """
    columns = glm_model_columns(terms, interactions)
    schema_set = set(schema)
    missing = sorted(column for column in columns if column not in schema_set)
    if missing:
        raise HauteValidationError(
            f"GLM terms reference columns not found in training data: {missing}. "
            f"Available columns: {sorted(schema_set)}"
        )
    for name, spec in terms.items():
        if isinstance(spec, Mapping) and spec.get("type") == "expression" and name in schema_set:
            raise HauteValidationError(
                f"GLM expression term {name!r} names a column in the training data; "
                "rename the expression so it cannot be mistaken for a native fit."
            )
    return columns
