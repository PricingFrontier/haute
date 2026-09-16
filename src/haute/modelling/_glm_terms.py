"""Pure GLM term contract shared by the config builder, routes, job, and adapter.

Terms are the RustyStats ``glm_dict`` mapping ``{name: spec}``. A *native*
spec (``linear``, ``categorical``, ``bs``, ``ns``, ``ms``, ``target_encoding``,
``frequency_encoding``)
is keyed by the column it fits. The two encoding types can also use a free
name and a ``variable`` source column. An *expression* spec is keyed by a free name
and reads the columns named in its ``expr``. Interactions contribute their
filled factors. Nothing here imports RustyStats: the schema-free half runs
during projection planning before any data exists.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from haute.errors import HauteValidationError

SUPPORTED_TERM_TYPES: frozenset[str] = frozenset(
    {
        "linear",
        "categorical",
        "bs",
        "ns",
        "ms",
        "target_encoding",
        "frequency_encoding",
        "expression",
    }
)
NATIVE_TERM_TYPES: frozenset[str] = SUPPORTED_TERM_TYPES - {"expression"}
ENCODING_TERM_TYPES: frozenset[str] = frozenset({"target_encoding", "frequency_encoding"})

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


def interaction_encoding(interaction: Mapping[str, Any], index: int) -> str:
    """Validate interaction mode/settings without needing data or RustyStats.

    Absence means the existing product mode. Encoding modes operate on raw
    factor values; accepting product overrides there would silently discard
    the user's chosen fits. Validate even partial editor cards before they
    are skipped by the fitter.
    """
    where = f"Interaction {index + 1}"
    allowed_keys = {
        "factors",
        "specs",
        "include_main",
        "encoding",
        "prior_weight",
        "n_permutations",
    }
    unknown = set(interaction) - allowed_keys
    if unknown:
        raise HauteValidationError(f"{where}: unknown interaction settings {sorted(unknown)}")
    reserved = {
        "include_main",
        "target_encoding",
        "frequency_encoding",
        "prior_weight",
        "n_permutations",
    }
    collisions = reserved.intersection(_filled_factors(interaction))
    if collisions:
        raise HauteValidationError(
            f"{where}: reserved RustyStats interaction factor names: {sorted(collisions)}"
        )
    encoding = interaction.get("encoding", "product")
    if not isinstance(encoding, str) or encoding not in (
        "product",
        "target_encoding",
        "frequency_encoding",
    ):
        raise HauteValidationError(
            f"{where}: unsupported encoding {encoding!r}; "
            "use product, target_encoding, or frequency_encoding"
        )
    specs = interaction.get("specs", {})
    if not isinstance(specs, Mapping):
        raise HauteValidationError(f"{where}: 'specs' must be a mapping")
    if encoding != "product" and specs:
        raise HauteValidationError(
            f"{where}: joint encoding uses raw factors and cannot carry per-factor 'specs'"
        )
    for parameter in ("prior_weight", "n_permutations"):
        if parameter in interaction and encoding != "target_encoding":
            raise HauteValidationError(f"{where}: {parameter} requires target_encoding")
    if "prior_weight" in interaction:
        prior = interaction["prior_weight"]
        if prior != "auto" and (
            isinstance(prior, bool)
            or not isinstance(prior, (int, float))
            or not math.isfinite(prior)
            or prior < 0
        ):
            raise HauteValidationError(
                f"{where}: prior_weight must be a finite nonnegative number or 'auto'"
            )
    if "n_permutations" in interaction:
        permutations = interaction["n_permutations"]
        if isinstance(permutations, bool) or not isinstance(permutations, int) or permutations < 1:
            raise HauteValidationError(f"{where}: n_permutations must be a positive integer")
    return encoding


def glm_model_columns(
    terms: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """Resolve the ordered unique columns a GLM reads, without a schema.

    Raises ``HauteValidationError`` naming the offending term for an
    unsupported type, an unsupported redirect, a duplicate encoding, an
    expression outside the grammar, or an expression key that collides with
    a native key or with one of the columns its own expression reads.
    """
    if any(not isinstance(name, str) or not name for name in terms):
        raise HauteValidationError("GLM term names must be non-empty strings")

    columns: dict[str, None] = {}
    native_keys: set[str] = set()
    expressions: dict[str, list[str]] = {}
    encodings: set[tuple[str, str]] = set()

    for name, spec in terms.items():
        if not isinstance(spec, Mapping):
            raise HauteValidationError(f"GLM term {name!r} must be a mapping with a 'type'")
        term_type = spec.get("type", "linear")
        if term_type not in SUPPORTED_TERM_TYPES:
            raise HauteValidationError(
                f"GLM term {name!r} has unsupported type {term_type!r}. "
                f"Supported types: {sorted(SUPPORTED_TERM_TYPES)}"
            )
        redirecting = [key for key in ("variable", "interaction") if key in spec]
        if term_type in ENCODING_TERM_TYPES and "variable" in redirecting:
            redirecting.remove("variable")
        if redirecting:
            raise HauteValidationError(
                f"GLM term {name!r} carries {redirecting}; a native term must be keyed by "
                "the column it reads and may not redirect to another column."
            )
        source = spec.get("variable", name)
        if not isinstance(source, str) or not source:
            raise HauteValidationError(f"GLM term {name!r}: variable must be a non-empty string")
        if term_type in ENCODING_TERM_TYPES:
            identity = (term_type, source)
            if identity in encodings:
                raise HauteValidationError(
                    f"GLM term {name!r} duplicates {term_type} for column {source!r}; "
                    "each column can have at most one term of each encoding type."
                )
            encodings.add(identity)
        if term_type == "categorical" and "levels" in spec:
            levels = spec["levels"]
            if (
                not isinstance(levels, list)
                or not levels
                or not all(isinstance(level, str) for level in levels)
                or len(set(levels)) != len(levels)
            ):
                raise HauteValidationError(
                    f"GLM term {name!r}: levels must be a non-empty list of unique strings; "
                    "quote numeric category labels exactly as represented by the column"
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
            columns[source] = None

    for name, identifiers in expressions.items():
        if name in native_keys:
            raise HauteValidationError(
                f"GLM expression term {name!r} collides with the native term of the same "
                "name; a column key always means a native fit."
            )
        for identifier in identifiers:
            columns[identifier] = None

    for index, interaction in enumerate(interactions or []):
        interaction_encoding(interaction, index)
        for factor in _filled_factors(interaction):
            columns[factor] = None

    return list(columns)


def validate_glm_model_columns(
    terms: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]] | None,
    schema: Iterable[str],
) -> list[str]:
    """Resolve model columns and check them against a real schema.

    Every resolved column must exist. Expressions and encoding aliases must
    not shadow another schema column.
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
        if (
            spec.get("type") in ENCODING_TERM_TYPES
            and spec.get("variable", name) != name
            and name in schema_set
        ):
            raise HauteValidationError(
                f"GLM encoding term {name!r} names a column in the training data; "
                "rename the encoding so it cannot be mistaken for a native fit."
            )
        if isinstance(spec, Mapping) and spec.get("type") == "expression" and name in schema_set:
            raise HauteValidationError(
                f"GLM expression term {name!r} names a column in the training data; "
                "rename the expression so it cannot be mistaken for a native fit."
            )
    return columns
