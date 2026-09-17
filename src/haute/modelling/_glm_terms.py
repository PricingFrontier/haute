"""Pure GLM term contract shared by the config builder, routes, job, and adapter.

Terms are the RustyStats ``glm_dict`` mapping ``{name: spec}``. A *native*
spec (``linear``, ``categorical``, ``bs``, ``ns``, ``ms``, ``target_encoding``,
``frequency_encoding``) is keyed by the column it fits. The two encoding types
can also use a free name and a ``variable`` source column. An *expression*
spec is keyed by a free name and reads the columns named in its ``expr``.
Interactions contribute their filled factors.

Nothing here imports RustyStats or reads data. The schema-free half
(:func:`glm_model_columns`) runs during projection planning; the schema half
(:func:`validate_glm_model_columns`) runs against a column-to-dtype mapping;
design resolution (:func:`resolve_glm_design`) runs against dtype classes and
turns the stored configuration into the exact terms and interactions handed to
RustyStats, independent of interaction-card order.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from haute.errors import HauteValidationError

# ── Term types and keys ──────────────────────────────────────────────────

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
ENCODING_TERM_TYPES: frozenset[str] = frozenset({"target_encoding", "frequency_encoding"})
SPLINE_TERM_TYPES: frozenset[str] = frozenset({"bs", "ns", "ms"})

#: Keys each stored term type accepts. Every key except ``reference`` is a
#: RustyStats ``VALID_KEYS`` key; ``reference`` is translated into ``levels``
#: when a model is fitted (:func:`resolve_categorical_levels`).
TERM_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "linear": frozenset({"type", "monotonicity"}),
        "categorical": frozenset({"type", "levels", "reference"}),
        "bs": frozenset({"type", "df", "k", "degree", "monotonicity", "knots", "boundary_knots"}),
        "ns": frozenset({"type", "df", "k", "knots", "boundary_knots"}),
        "ms": frozenset({"type", "df", "k", "degree", "monotonicity", "knots", "boundary_knots"}),
        "target_encoding": frozenset({"type", "prior_weight", "n_permutations", "variable"}),
        "frequency_encoding": frozenset({"type", "variable"}),
        "expression": frozenset({"type", "expr", "monotonicity"}),
    }
)

#: Keys each product-interaction slot override accepts.
PRODUCT_OVERRIDE_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "linear": frozenset({"type"}),
        "categorical": frozenset({"type"}),
        "bs": frozenset({"type", "df", "k", "degree", "knots", "boundary_knots"}),
        "ns": frozenset({"type", "df", "k", "knots", "boundary_knots"}),
        "target_encoding": frozenset({"type", "prior_weight", "n_permutations"}),
    }
)

INTERACTION_KEYS: frozenset[str] = frozenset(
    {"factors", "specs", "include_main", "encoding", "prior_weight", "n_permutations"}
)
INTERACTION_ENCODINGS: frozenset[str] = frozenset({"target_encoding", "frequency_encoding"})
#: Names RustyStats reads as interaction settings rather than factors.
RESERVED_FACTOR_NAMES: frozenset[str] = frozenset(
    {"include_main", "target_encoding", "frequency_encoding", "prior_weight", "n_permutations"}
)

MONOTONICITY_VALUES: frozenset[str] = frozenset({"increasing", "decreasing"})
DEFAULT_SPLINE_DEGREE = 3
MIN_SPLINE_DEGREE = 1
MAX_SPLINE_DEGREE = 5
MAX_SPLINE_BASIS = 20
MAX_KNOTS = 20
MAX_PERMUTATIONS = 100
#: Most column names any validation message lists before summarising.
MESSAGE_NAME_LIMIT = 20

_TYPE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "linear": "Linear",
        "categorical": "Categorical",
        "bs": "B-spline",
        "ns": "Natural spline",
        "ms": "Monotone spline",
        "target_encoding": "Target encoding",
        "frequency_encoding": "Frequency encoding",
        "expression": "Expression",
    }
)

# ── Column types ─────────────────────────────────────────────────────────

DtypeClass = Literal["continuous", "integer", "boolean", "categorical", "unsupported"]

_DTYPE_CLASS_PATTERNS: tuple[tuple[DtypeClass, re.Pattern[str]], ...] = (
    (
        "continuous",
        re.compile(
            r"float(?:16|32|64)|f(?:16|32|64)|decimal(?:\(.*\))?", re.IGNORECASE | re.DOTALL
        ),
    ),
    ("integer", re.compile(r"u?int(?:8|16|32|64|128)|[iu](?:8|16|32|64|128)", re.IGNORECASE)),
    ("boolean", re.compile(r"bool(?:ean)?", re.IGNORECASE)),
    (
        "categorical",
        re.compile(
            r"string|str|utf8|categorical(?:\(.*\))?|enum(?:\(.*\))?",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)

MAIN_FITS_BY_CLASS: Mapping[DtypeClass, frozenset[str]] = MappingProxyType(
    {
        "continuous": frozenset({"linear", "bs", "ns", "ms"}),
        "integer": frozenset(
            {"linear", "bs", "ns", "ms", "categorical", "target_encoding", "frequency_encoding"}
        ),
        "boolean": frozenset({"categorical", "target_encoding", "frequency_encoding"}),
        "categorical": frozenset({"categorical", "target_encoding", "frequency_encoding"}),
        "unsupported": frozenset(),
    }
)
SLOT_FITS_BY_CLASS: Mapping[DtypeClass, frozenset[str]] = MappingProxyType(
    {
        "continuous": frozenset({"linear", "bs", "ns"}),
        "integer": frozenset({"linear", "bs", "ns", "categorical", "target_encoding"}),
        "boolean": frozenset({"categorical", "target_encoding"}),
        "categorical": frozenset({"categorical", "target_encoding"}),
        "unsupported": frozenset(),
    }
)
DEFAULT_FIT_BY_CLASS: Mapping[DtypeClass, str] = MappingProxyType(
    {
        "continuous": "linear",
        "integer": "linear",
        "boolean": "categorical",
        "categorical": "categorical",
    }
)
EXPRESSION_OPERAND_CLASSES: frozenset[DtypeClass] = frozenset({"continuous", "integer"})
JOINT_ENCODING_CLASSES: frozenset[DtypeClass] = frozenset({"integer", "boolean", "categorical"})


def glm_dtype_class(dtype: str) -> DtypeClass:
    """Classify a Polars dtype name (``str(dtype)``) for GLM fits.

    The frontend classifies the same names identically; a shared fixture pins
    the two together.
    """
    name = dtype.strip()
    for dtype_class, pattern in _DTYPE_CLASS_PATTERNS:
        if pattern.fullmatch(name):
            return dtype_class
    return "unsupported"


# ── Message helpers ──────────────────────────────────────────────────────


def bounded_names(names: Iterable[str], limit: int = MESSAGE_NAME_LIMIT) -> str:
    """Render at most *limit* names and the total, for validation messages."""
    items = list(names)
    shown = ", ".join(repr(name) for name in items[:limit])
    if len(items) > limit:
        return f"[{shown}, … {len(items) - limit} more of {len(items)}]"
    return f"[{shown}]"


def _type_label(term_type: str) -> str:
    return _TYPE_LABELS.get(term_type, term_type)


# ── Expression grammar ───────────────────────────────────────────────────

EXPRESSION_GRAMMAR = (
    "Supported forms: 'x ** n', 'x + y', 'x - y', 'x * y', 'x / y' (y a column or a "
    "number), or a bare column 'x'. Columns must be named with letters, digits, and "
    "underscores, starting with a letter or underscore. Compute log and other "
    "transforms in an upstream Polars node."
)
_EXPRESSION_RE = re.compile(r"^\s*(\w+)\s*(?:(\*\*|[+\-*/])\s*([0-9]+\.[0-9]+|\w+))?\s*$")
_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
#: Identifiers Python's ``float()`` parses; RustyStats would read them as numbers.
_FLOAT_WORD_RE = re.compile(r"inf|infinity|nan", re.IGNORECASE)


def is_expression_identifier(name: str) -> bool:
    """Whether *name* can appear in an expression.

    A Unicode letter or underscore followed by Unicode letters, Unicode numbers,
    or underscores — a subset of RustyStats' ``\\w+`` identifiers.
    """
    if not name:
        return False
    first = name[0]
    if not (first == "_" or first.isalpha()):
        return False
    for character in name[1:]:
        if not (
            character == "_"
            or character.isalpha()
            or unicodedata.category(character).startswith("N")
        ):
            return False
    return re.fullmatch(r"\w+", name) is not None


def expression_identifiers(expr: str) -> list[str]:
    """Return the column identifiers an expression reads, in order.

    Raises ``HauteValidationError`` for anything outside the grammar.
    """
    text = expr if isinstance(expr, str) else ""
    match = _EXPRESSION_RE.match(text)
    if match is None:
        raise HauteValidationError(
            f"GLM expression {expr!r} is not supported. {EXPRESSION_GRAMMAR}"
        )
    left, operator, right = match.groups()
    if not is_expression_identifier(left):
        raise HauteValidationError(
            f"GLM expression {expr!r} must start with a column name. {EXPRESSION_GRAMMAR}"
        )
    identifiers = [left]
    if operator is None:
        return identifiers
    if _NUMBER_RE.fullmatch(right):
        return identifiers
    if not is_expression_identifier(right):
        raise HauteValidationError(
            f"GLM expression {expr!r} has an unsupported operand {right!r}. {EXPRESSION_GRAMMAR}"
        )
    if _FLOAT_WORD_RE.fullmatch(right):
        raise HauteValidationError(
            f"GLM expression {expr!r} names column {right!r}, which RustyStats would read "
            "as a number; rename the column upstream to use it in an expression."
        )
    if right != left:
        identifiers.append(right)
    return identifiers


# ── Term parameter contract ──────────────────────────────────────────────


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_monotonicity(where: str, spec: Mapping[str, Any]) -> None:
    if "monotonicity" in spec and spec["monotonicity"] not in MONOTONICITY_VALUES:
        raise HauteValidationError(
            f"{where}: monotonicity must be 'increasing' or 'decreasing', "
            f"not {spec['monotonicity']!r}"
        )


def _validate_spline(where: str, spec: Mapping[str, Any], kind: str) -> None:
    chosen = [key for key in ("df", "k", "knots") if key in spec]
    if len(chosen) > 1:
        raise HauteValidationError(
            f"{where}: set only one of df, k, or knots (found {', '.join(chosen)}); "
            "RustyStats would silently ignore all but one"
        )
    degree = DEFAULT_SPLINE_DEGREE
    if "degree" in spec:
        degree = spec["degree"]
        if not _is_integer(degree) or not MIN_SPLINE_DEGREE <= degree <= MAX_SPLINE_DEGREE:
            raise HauteValidationError(
                f"{where}: degree must be an integer from {MIN_SPLINE_DEGREE} to "
                f"{MAX_SPLINE_DEGREE}"
            )
    minimum = degree + 1 if kind == "bs" else 2
    for key in ("df", "k"):
        if key in spec:
            value = spec[key]
            if not _is_integer(value) or not minimum <= value <= MAX_SPLINE_BASIS:
                reason = f"degree + 1 = {minimum}" if kind == "bs" else str(minimum)
                raise HauteValidationError(
                    f"{where}: {key} must be an integer from {reason} to {MAX_SPLINE_BASIS}; "
                    "smaller values are silently widened by RustyStats"
                )
    knots: list[float] | None = None
    if "knots" in spec:
        raw = spec["knots"]
        if (
            not isinstance(raw, list)
            or not 1 <= len(raw) <= MAX_KNOTS
            or not all(_is_finite_number(value) for value in raw)
            or any(raw[index] <= raw[index - 1] for index in range(1, len(raw)))
        ):
            raise HauteValidationError(
                f"{where}: knots must be 1 to {MAX_KNOTS} finite, strictly increasing numbers"
            )
        knots = [float(value) for value in raw]
    if "boundary_knots" in spec:
        raw = spec["boundary_knots"]
        if (
            not isinstance(raw, list)
            or len(raw) != 2
            or not all(_is_finite_number(value) for value in raw)
            or raw[1] <= raw[0]
        ):
            raise HauteValidationError(
                f"{where}: boundary_knots must be exactly two finite, increasing numbers"
            )
        if knots is not None and not (raw[0] < knots[0] and knots[-1] < raw[1]):
            raise HauteValidationError(f"{where}: boundary_knots must enclose every knot")
    _validate_monotonicity(where, spec)


def _validate_target_encoding_settings(where: str, spec: Mapping[str, Any]) -> None:
    if "prior_weight" in spec:
        prior = spec["prior_weight"]
        if prior != "auto" and not (_is_finite_number(prior) and prior >= 0):
            raise HauteValidationError(
                f"{where}: prior_weight must be 'auto' or a finite non-negative number"
            )
    if "n_permutations" in spec:
        permutations = spec["n_permutations"]
        if not _is_integer(permutations) or not 1 <= permutations <= MAX_PERMUTATIONS:
            raise HauteValidationError(
                f"{where}: n_permutations must be an integer from 1 to {MAX_PERMUTATIONS}"
            )


def _validate_categorical(where: str, spec: Mapping[str, Any]) -> None:
    if "levels" in spec and "reference" in spec:
        raise HauteValidationError(f"{where}: set levels or reference, not both")
    if "levels" in spec:
        levels = spec["levels"]
        if (
            not isinstance(levels, list)
            or not levels
            or not all(isinstance(level, str) for level in levels)
            or len(set(levels)) != len(levels)
        ):
            raise HauteValidationError(
                f"{where}: levels must be a non-empty list of unique strings; quote numeric "
                "category labels exactly as represented by the column"
            )
    if "reference" in spec:
        reference = spec["reference"]
        if not isinstance(reference, str) or not reference:
            raise HauteValidationError(f"{where}: reference must be a non-empty string")


def validate_term_spec(name: str, spec: Any) -> str:
    """Validate one stored term against the parameter contract; return its type."""
    where = f"GLM term {name!r}"
    if not isinstance(spec, Mapping):
        raise HauteValidationError(f"{where} must be a mapping with a 'type'")
    term_type = spec.get("type")
    if not isinstance(term_type, str) or term_type not in SUPPORTED_TERM_TYPES:
        raise HauteValidationError(
            f"{where} has unsupported type {term_type!r}. "
            f"Supported types: {sorted(SUPPORTED_TERM_TYPES)}"
        )
    unknown = sorted(set(spec) - TERM_KEYS[term_type])
    if unknown:
        raise HauteValidationError(
            f"{where}: {_type_label(term_type)} terms do not accept {unknown}; allowed keys: "
            f"{sorted(TERM_KEYS[term_type] - {'type'})}"
        )
    if term_type in SPLINE_TERM_TYPES:
        _validate_spline(where, spec, term_type)
    elif term_type == "categorical":
        _validate_categorical(where, spec)
    elif term_type in ENCODING_TERM_TYPES:
        if "variable" in spec and (not isinstance(spec["variable"], str) or not spec["variable"]):
            raise HauteValidationError(f"{where}: variable must be a non-empty string")
        _validate_target_encoding_settings(where, spec)
    elif term_type == "expression":
        expr = spec.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            raise HauteValidationError(f"GLM expression term {name!r} has no 'expr'")
        _validate_monotonicity(where, spec)
    else:
        _validate_monotonicity(where, spec)
    return term_type


def _validate_override_spec(where: str, override: Any) -> str:
    if not isinstance(override, Mapping):
        raise HauteValidationError(f"{where}: override must be a mapping with a 'type'")
    kind = override.get("type")
    if not isinstance(kind, str) or kind not in PRODUCT_OVERRIDE_KEYS:
        raise HauteValidationError(
            f"{where}: override type {kind!r} is not allowed inside an interaction; use one of "
            f"{sorted(PRODUCT_OVERRIDE_KEYS)}"
        )
    unapplied = sorted(set(override) & {"monotonicity", "levels", "reference"})
    if unapplied:
        raise HauteValidationError(
            f"{where}: {', '.join(unapplied)} is not applied inside interactions"
        )
    unknown = sorted(set(override) - PRODUCT_OVERRIDE_KEYS[kind])
    if unknown:
        raise HauteValidationError(
            f"{where}: {_type_label(kind)} overrides do not accept {unknown}; allowed keys: "
            f"{sorted(PRODUCT_OVERRIDE_KEYS[kind] - {'type'})}"
        )
    if kind in ("bs", "ns"):
        _validate_spline(where, override, kind)
    elif kind == "target_encoding":
        _validate_target_encoding_settings(where, override)
    return kind


@dataclass(frozen=True)
class InteractionEntry:
    """A structurally valid interaction card."""

    index: int
    factors: tuple[str, ...]
    encoding: str
    include_main: bool
    specs: Mapping[str, Mapping[str, Any]]
    settings: Mapping[str, Any]

    @property
    def where(self) -> str:
        return f"Interaction {self.index + 1}"

    @property
    def complete(self) -> bool:
        return len(self.factors) >= 2


def validate_interaction_entry(interaction: Any, index: int) -> InteractionEntry:
    """Validate one stored interaction card without data or RustyStats.

    Partial cards (fewer than two filled factors) are validated too: the
    editor persists them and the fitter skips them.
    """
    where = f"Interaction {index + 1}"
    if not isinstance(interaction, Mapping):
        raise HauteValidationError(f"{where} must be a mapping with 'factors'")
    unknown = sorted(set(interaction) - INTERACTION_KEYS)
    if unknown:
        raise HauteValidationError(f"{where}: unknown interaction settings {unknown}")
    factors = interaction.get("factors")
    if not isinstance(factors, list) or not all(isinstance(factor, str) for factor in factors):
        raise HauteValidationError(f"{where}: factors must be a list of column names")
    filled = [factor for factor in factors if factor]
    if len(set(filled)) != len(filled):
        raise HauteValidationError(f"{where} names a factor more than once: {filled}")
    collisions = sorted(RESERVED_FACTOR_NAMES.intersection(filled))
    if collisions:
        raise HauteValidationError(
            f"{where}: reserved RustyStats interaction factor names: {collisions}"
        )
    include_main = interaction.get("include_main", True)
    if not isinstance(include_main, bool):
        raise HauteValidationError(f"{where}: include_main must be true or false")
    encoding = interaction.get("encoding", "product")
    if encoding != "product" and encoding not in INTERACTION_ENCODINGS:
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
    stray = sorted(key for key in specs if key not in filled)
    if stray:
        raise HauteValidationError(f"{where}: specs name columns that are not picked: {stray}")
    for factor, override in specs.items():
        _validate_override_spec(f"{where}, factor {factor!r}", override)
    settings = {
        key: interaction[key] for key in ("prior_weight", "n_permutations") if key in interaction
    }
    for parameter in settings:
        if encoding != "target_encoding":
            raise HauteValidationError(f"{where}: {parameter} requires target_encoding")
    _validate_target_encoding_settings(where, settings)
    return InteractionEntry(
        index=index,
        factors=tuple(filled),
        encoding=encoding,
        include_main=include_main,
        specs=MappingProxyType(
            {key: MappingProxyType(dict(value)) for key, value in specs.items()}
        ),
        settings=MappingProxyType(settings),
    )


def _interaction_entries(interactions: Any) -> list[InteractionEntry]:
    if interactions is None:
        return []
    if not isinstance(interactions, Sequence) or isinstance(interactions, (str, bytes)):
        raise HauteValidationError("GLM interactions must be a list of interaction cards")
    return [
        validate_interaction_entry(interaction, index)
        for index, interaction in enumerate(interactions)
    ]


def _term_source(name: str, spec: Mapping[str, Any]) -> str:
    source = spec.get("variable", name)
    assert isinstance(source, str)
    return source


def glm_model_columns(terms: Any, interactions: Any) -> list[str]:
    """Validate terms and interactions and resolve the ordered columns a GLM reads.

    Schema-free: raises ``HauteValidationError`` naming the offending term or
    card for any breach of the term parameter contract, the expression
    grammar, duplicate encodings, or an expression keyed by a column it reads.
    """
    if not isinstance(terms, Mapping):
        raise HauteValidationError("GLM terms must be a mapping of term names to specs")
    if any(not isinstance(name, str) or not name for name in terms):
        raise HauteValidationError("GLM term names must be non-empty strings")

    columns: dict[str, None] = {}
    expressions: dict[str, list[str]] = {}
    encodings: dict[tuple[str, str], str] = {}

    for name, spec in terms.items():
        term_type = validate_term_spec(name, spec)
        if term_type == "expression":
            try:
                identifiers = expression_identifiers(spec["expr"])
            except HauteValidationError as exc:
                raise HauteValidationError(f"GLM term {name!r}: {exc}") from None
            if name in identifiers:
                raise HauteValidationError(
                    f"GLM expression term {name!r} is keyed by a column it reads; "
                    "give the expression a name that is not a column."
                )
            expressions[name] = identifiers
            continue
        source = _term_source(name, spec)
        if term_type in ENCODING_TERM_TYPES:
            identity = (term_type, source)
            if identity in encodings:
                raise HauteValidationError(
                    f"GLM term {name!r} duplicates {term_type} for column {source!r} "
                    f"(already {encodings[identity]!r}); each column can have at most one "
                    "term of each encoding type."
                )
            encodings[identity] = name
        columns[source] = None

    for identifiers in expressions.values():
        for identifier in identifiers:
            columns[identifier] = None

    for entry in _interaction_entries(interactions):
        for factor in entry.factors:
            columns[factor] = None
    return list(columns)


def validate_glm_model_columns(
    terms: Any,
    interactions: Any,
    schema: Mapping[str, str],
    *,
    role_columns: Mapping[str, str] | None = None,
) -> list[str]:
    """Resolve model columns and check them against a column-to-dtype schema.

    Role columns (target, weight, offset, fold, identifiers, evaluation keys)
    cannot be model columns. Every model column must exist, and every fit must
    suit its column's dtype class. Expressions and encoding aliases must not
    shadow another schema column.
    """
    columns = glm_model_columns(terms, interactions)
    roles = role_columns or {}
    role_hits = [column for column in columns if column in roles]
    if role_hits:
        described = ", ".join(f"{column!r} ({roles[column]})" for column in role_hits)
        raise HauteValidationError(
            f"GLM terms and interactions cannot use role columns: {described}. "
            "Remove those terms or choose different role columns."
        )
    missing = [column for column in columns if column not in schema]
    if missing:
        raise HauteValidationError(
            f"GLM terms reference columns not found in training data: {bounded_names(missing)}. "
            f"Available columns: {bounded_names(sorted(schema))}"
        )
    classes = {column: glm_dtype_class(schema[column]) for column in columns}

    def unsupported(column: str) -> HauteValidationError:
        return HauteValidationError(
            f"GLM column {column!r} has dtype {schema[column]}, which GLM fits do not support; "
            "cast it upstream to a number, boolean, or string."
        )

    for name, spec in terms.items():
        term_type = spec["type"]
        if term_type == "expression":
            if name in schema:
                raise HauteValidationError(
                    f"GLM expression term {name!r} names a column in the training data; "
                    "rename the expression so it cannot be mistaken for a native fit."
                )
            for identifier in expression_identifiers(spec["expr"]):
                if classes[identifier] == "unsupported":
                    raise unsupported(identifier)
                if classes[identifier] not in EXPRESSION_OPERAND_CLASSES:
                    raise HauteValidationError(
                        f"GLM expression term {name!r} reads {identifier!r}, a "
                        f"{classes[identifier]} column; expressions need continuous or integer "
                        "columns."
                    )
            continue
        source = _term_source(name, spec)
        if term_type in ENCODING_TERM_TYPES and source != name and name in schema:
            raise HauteValidationError(
                f"GLM encoding term {name!r} names a column in the training data; "
                "rename the encoding so it cannot be mistaken for a native fit."
            )
        if classes[source] == "unsupported":
            raise unsupported(source)
        if term_type not in MAIN_FITS_BY_CLASS[classes[source]]:
            raise HauteValidationError(
                f"GLM term {name!r}: {_type_label(term_type)} is not available for {source!r}, "
                f"a {classes[source]} column ({schema[source]})."
            )

    for entry in _interaction_entries(interactions):
        for factor in entry.factors:
            if classes[factor] == "unsupported":
                raise unsupported(factor)
            if entry.encoding != "product" and classes[factor] not in JOINT_ENCODING_CLASSES:
                raise HauteValidationError(
                    f"{entry.where}: joint encodings need integer, boolean, or categorical "
                    f"factors; {factor!r} is a {classes[factor]} column."
                )
        for factor, override in entry.specs.items():
            if override["type"] not in SLOT_FITS_BY_CLASS[classes[factor]]:
                raise HauteValidationError(
                    f"{entry.where}, factor {factor!r}: {_type_label(override['type'])} is not "
                    f"available for a {classes[factor]} column ({schema[factor]})."
                )
    return columns


# ── Design summaries used by config validation ───────────────────────────


def _is_penalised_spline(spec: Mapping[str, Any]) -> bool:
    return "df" not in spec and "knots" not in spec


def penalised_smooth_terms(terms: Mapping[str, Any], interactions: Any) -> list[str]:
    """Labels of splines RustyStats fits as penalised smooths (no df or knots)."""
    labels = [
        repr(name)
        for name, spec in terms.items()
        if isinstance(spec, Mapping)
        and spec.get("type") in SPLINE_TERM_TYPES
        and _is_penalised_spline(spec)
    ]
    for entry in _interaction_entries(interactions):
        for factor, override in entry.specs.items():
            if override["type"] in ("bs", "ns") and _is_penalised_spline(override):
                labels.append(f"{entry.where} factor {factor!r}")
    return labels


def monotone_constraint_terms(terms: Mapping[str, Any]) -> list[str]:
    """Terms whose monotonicity RustyStats applies as a coefficient constraint."""
    return [
        name
        for name, spec in terms.items()
        if isinstance(spec, Mapping)
        and spec.get("type") in ("linear", "expression", "bs")
        and "monotonicity" in spec
    ]


# ── Categorical levels ───────────────────────────────────────────────────


def resolve_categorical_levels(
    terms: Mapping[str, Mapping[str, Any]],
    observed_labels: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, Any]]:
    """Translate ``reference`` into ``levels`` and check levels against the data.

    *observed_labels* maps each categorical term with ``levels`` or
    ``reference`` to its training labels in RustyStats' string form. A
    reference keeps every other observed label as an indicator, so the
    reference and unseen levels share the intercept. A reference or listed
    level absent from the data is refused rather than becoming an all-zero
    design column.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for name, spec in terms.items():
        if spec.get("type") != "categorical" or not ("levels" in spec or "reference" in spec):
            resolved[name] = dict(spec)
            continue
        labels = list(observed_labels[name])
        shown = bounded_names(labels)
        if "reference" in spec:
            reference = spec["reference"]
            if reference not in labels:
                raise HauteValidationError(
                    f"GLM term {name!r}: reference level {reference!r} is not in the training "
                    f"data. Observed levels: {shown}"
                )
            levels = [label for label in labels if label != reference]
            if not levels:
                raise HauteValidationError(
                    f"GLM term {name!r} has only its reference level {reference!r} in the "
                    "training data, so it has nothing to fit."
                )
            resolved[name] = {"type": "categorical", "levels": levels}
            continue
        absent = [level for level in spec["levels"] if level not in labels]
        if absent:
            raise HauteValidationError(
                f"GLM term {name!r}: levels {bounded_names(absent)} are not in the training "
                f"data. Observed levels: {shown}"
            )
        resolved[name] = dict(spec)
    return resolved


# ── Interaction resolution ───────────────────────────────────────────────


@dataclass(frozen=True)
class _ResolvedSlot:
    entry: InteractionEntry
    factor: str
    spec: dict[str, Any]


def _inheritance_block(spec: Mapping[str, Any]) -> str | None:
    kind = spec["type"]
    if kind == "ms":
        return "its monotone spline main effect cannot be used inside interactions"
    if kind in ("linear", "bs") and "monotonicity" in spec:
        return "its main effect's monotonicity is not applied inside interactions"
    if kind == "categorical" and ("levels" in spec or "reference" in spec):
        return "its main effect's levels or reference level are not applied inside interactions"
    if kind == "frequency_encoding":
        return "its frequency-encoded main effect cannot be used inside a product interaction"
    if kind == "expression":
        return "expressions cannot be used inside interactions"
    return None


def _slot_spec_from_main(spec: Mapping[str, Any]) -> dict[str, Any]:
    kind = spec["type"]
    allowed = PRODUCT_OVERRIDE_KEYS[kind]
    return {key: value for key, value in spec.items() if key in allowed}


def _native_term(terms: Mapping[str, Mapping[str, Any]], column: str) -> Mapping[str, Any] | None:
    spec = terms.get(column)
    if spec is None or spec["type"] == "expression":
        return None
    if spec.get("variable", column) != column:
        return None
    return spec


def resolve_glm_design(
    terms: Mapping[str, Mapping[str, Any]],
    interactions: Any,
    dtype_classes: Mapping[str, DtypeClass],
    *,
    reserved_names: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Resolve stored interactions into RustyStats interactions and effective terms.

    Returns ``(interactions, terms)`` in RustyStats' ``glm_dict`` shapes. Every
    rule is independent of interaction-card order:

    1. A product slot uses its override, else the column's single inheritable
       main effect, else its dtype-class default. A column with several main
       effects, or one interactions cannot honour, needs an override.
    2. Product target encoding has one encoded factor and Linear partners; its
       main effect is registered once with settings every card agrees on.
    3. Include main effects adds, for each column still without a main effect,
       the single spec its include-main cards agree on.
    4. Slots are checked against the effective native main effects.

    RustyStats always receives ``include_main: False``: its own flag adds a
    main effect for every factor and duplicates existing ones.
    """
    glm_model_columns(terms, interactions)
    entries = [entry for entry in _interaction_entries(interactions) if entry.complete]

    main_effects: dict[str, list[str]] = {}
    for name, spec in terms.items():
        if spec["type"] != "expression":
            main_effects.setdefault(_term_source(name, spec), []).append(name)

    effective_terms: dict[str, dict[str, Any]] = {name: dict(spec) for name, spec in terms.items()}
    rs_interactions: list[dict[str, Any]] = []
    slots: list[_ResolvedSlot] = []
    materialise: dict[str, list[tuple[InteractionEntry, dict[str, Any]]]] = {}
    seen: dict[tuple[str, frozenset[str]], InteractionEntry] = {}

    def dtype_class(entry: InteractionEntry, factor: str) -> DtypeClass:
        if factor not in dtype_classes:
            raise HauteValidationError(
                f"{entry.where}: factor {factor!r} is not in the training data"
            )
        factor_class = dtype_classes[factor]
        if factor_class == "unsupported":
            raise HauteValidationError(
                f"{entry.where}: factor {factor!r} has a dtype GLM fits do not support; cast it "
                "upstream to a number, boolean, or string."
            )
        return factor_class

    for entry in entries:
        identity = (entry.encoding, frozenset(entry.factors))
        if identity in seen:
            raise HauteValidationError(
                f"{entry.where} duplicates {seen[identity].where}: both are {entry.encoding} "
                f"interactions over {sorted(identity[1])}"
            )
        seen[identity] = entry

        if entry.encoding != "product":
            encoded: dict[str, Any] = {}
            for factor in entry.factors:
                factor_class = dtype_class(entry, factor)
                if factor_class not in JOINT_ENCODING_CLASSES:
                    raise HauteValidationError(
                        f"{entry.where}: joint encodings need integer, boolean, or categorical "
                        f"factors; {factor!r} is a {factor_class} column."
                    )
                # RustyStats' encoding branch reads only the column names. A
                # categorical placeholder would mark a numeric column
                # categorical globally and re-type its main effect.
                encoded[factor] = {"type": "linear"}
                if entry.include_main and factor not in main_effects:
                    default = {"type": DEFAULT_FIT_BY_CLASS[factor_class]}
                    materialise.setdefault(factor, []).append((entry, default))
            encoded[entry.encoding] = True
            encoded["include_main"] = False
            encoded.update(entry.settings)
            rs_interactions.append(encoded)
            continue

        product: dict[str, Any] = {}
        for factor in entry.factors:
            where = f"{entry.where}, factor {factor!r}"
            factor_class = dtype_class(entry, factor)
            override = entry.specs.get(factor)
            if override is not None:
                spec = dict(override)
            else:
                mains = main_effects.get(factor, [])
                if len(mains) > 1:
                    raise HauteValidationError(
                        f"{where}: the column has several main-effect terms "
                        f"({bounded_names(mains)}); choose a fit for this slot"
                    )
                if mains:
                    main_spec = terms[mains[0]]
                    blocked = _inheritance_block(main_spec)
                    if blocked is not None:
                        raise HauteValidationError(
                            f"{where}: {blocked}; choose a fit for this slot"
                        )
                    spec = _slot_spec_from_main(main_spec)
                else:
                    spec = {"type": DEFAULT_FIT_BY_CLASS[factor_class]}
            if spec["type"] not in SLOT_FITS_BY_CLASS[factor_class]:
                raise HauteValidationError(
                    f"{where}: {_type_label(spec['type'])} is not available for a "
                    f"{factor_class} column"
                )
            product[factor] = spec
            slots.append(_ResolvedSlot(entry, factor, spec))

        encoded_factors = [f for f in entry.factors if product[f]["type"] == "target_encoding"]
        if encoded_factors and (
            len(encoded_factors) != 1
            or any(
                product[factor]["type"] != "linear"
                for factor in entry.factors
                if factor not in encoded_factors
            )
        ):
            raise HauteValidationError(
                f"{entry.where}: product target encoding requires exactly one target-encoded "
                "factor and every other factor fitted Linear"
            )
        if entry.include_main:
            for factor in entry.factors:
                if product[factor]["type"] != "target_encoding" and factor not in main_effects:
                    materialise.setdefault(factor, []).append((entry, product[factor]))

        rs_product: dict[str, Any] = {}
        for factor in entry.factors:
            spec = product[factor]
            if spec["type"] == "target_encoding":
                # The interaction parser reads only prior_weight; the registered
                # main effect below carries n_permutations.
                rs_product[factor] = {
                    key: value for key, value in spec.items() if key in ("type", "prior_weight")
                }
            else:
                rs_product[factor] = dict(spec)
        rs_product["include_main"] = False
        rs_interactions.append(rs_product)

    _register_product_target_encodings(
        slots,
        effective_terms,
        main_effects,
        reserved_names=set(reserved_names) | set(dtype_classes),
    )

    for column, requests in materialise.items():
        if column in main_effects:
            continue
        distinct: list[dict[str, Any]] = []
        for _entry, spec in requests:
            if spec not in distinct:
                distinct.append(spec)
        if len(distinct) > 1:
            cards = sorted({entry.index + 1 for entry, _spec in requests})
            fits = ", ".join(_type_label(spec["type"]) for spec in distinct)
            raise HauteValidationError(
                f"Interactions {', '.join(str(card) for card in cards)} each add a main effect "
                f"for {column!r} with different fits ({fits}). Add a main term for {column!r} "
                "or give the interactions the same fit."
            )
        effective_terms[column] = dict(distinct[0])
        main_effects[column] = [column]

    for slot in slots:
        native = _native_term(effective_terms, slot.factor)
        if native is None:
            continue
        kind = slot.spec["type"]
        native_kind = native["type"]
        where = f"{slot.entry.where}, factor {slot.factor!r}"
        if kind == "categorical" and native_kind != "categorical":
            raise HauteValidationError(
                f"{where}: a categorical interaction fit would re-type the column's "
                f"{_type_label(native_kind)} main effect; keep the main term categorical or "
                "choose another fit"
            )
        if kind in ("linear", "bs", "ns") and native_kind == "categorical":
            raise HauteValidationError(
                f"{where}: {_type_label(kind)} cannot be applied over a categorical main effect"
            )
        if kind == "target_encoding" and native_kind == "categorical":
            raise HauteValidationError(
                f"{where}: target encoding over a categorical main effect is collinear with its "
                "indicators; choose another fit or remove the categorical main term"
            )
    return rs_interactions, effective_terms


_TARGET_ENCODING_DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {"prior_weight": "auto", "n_permutations": 4}
)


def _register_product_target_encodings(
    slots: Sequence[_ResolvedSlot],
    terms: dict[str, dict[str, Any]],
    main_effects: dict[str, list[str]],
    *,
    reserved_names: set[str],
) -> None:
    """Register one target-encoded main effect per product-encoded column.

    RustyStats adds this main effect even with ``include_main=False``;
    registering it explicitly makes ``n_permutations`` apply. Settings merge
    across every card, and conflicting explicit settings are refused, so the
    result does not depend on card order.
    """
    requested: dict[str, dict[str, tuple[Any, str]]] = {}
    for slot in slots:
        if slot.spec["type"] != "target_encoding":
            continue
        merged = requested.setdefault(slot.factor, {})
        for key in _TARGET_ENCODING_DEFAULTS:
            if key not in slot.spec:
                continue
            value = slot.spec[key]
            if key in merged and merged[key][0] != value:
                raise HauteValidationError(
                    f"{slot.entry.where}, factor {slot.factor!r}: target encoding {key} "
                    f"{value!r} conflicts with {merged[key][1]} ({merged[key][0]!r})"
                )
            merged.setdefault(key, (value, slot.entry.where))

    for factor, settings in requested.items():
        existing = [
            name
            for name, spec in terms.items()
            if spec["type"] == "target_encoding" and spec.get("variable", name) == factor
        ]
        if existing:
            name = existing[0]
            for key, (value, where) in settings.items():
                current = terms[name].get(key, _TARGET_ENCODING_DEFAULTS[key])
                if value != current:
                    raise HauteValidationError(
                        f"{where}, factor {factor!r}: target encoding {key} {value!r} must match "
                        f"term {name!r} ({current!r})"
                    )
            continue
        encoding: dict[str, Any] = {"type": "target_encoding"}
        encoding.update({key: value for key, (value, _where) in settings.items()})
        name = factor
        if name in terms:
            base = f"{factor}_te"
            name = base
            suffix = 2
            while name in terms or name in reserved_names:
                name = f"{base}_{suffix}"
                suffix += 1
            encoding["variable"] = factor
        terms[name] = encoding
        main_effects.setdefault(factor, []).append(name)
