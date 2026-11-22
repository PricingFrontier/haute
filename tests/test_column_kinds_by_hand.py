"""No code outside the column-kind module decides a column's kind from a type's name.

``haute._polars_dtypes.column_kind`` answers what kind a Polars type is (integer, float,
decimal, text, date, ...) from Polars' own classification. A list of type names kept by
hand beside it goes stale the day Polars adds a type: every list that named ``Float32``
and ``Float64`` as "the floats" left ``Float16`` out, and each was found as its own
defect (specs/io-layer, "Column kinds"). This scan finds such code in ``src/haute``
outside that module:

- a comparison with a number type (``dtype == pl.Float32``, ``name != "Int64"``);
- an ``isinstance`` against a number type, or against two or more types;
- a membership test against a literal holding a type, or a literal made only of types
  that names a number type or two or more types (``frozenset({"Float32", "Float64"})``);
- a pattern matched against a type's name (``r"Float(?:32|64)"``, ``.startswith("Int")``).

A place that must name one type, such as a cast or a persisted descriptor key, is on
:data:`_NAMED_TYPES` with its reason. The browser's twin is
``frontend/src/utils/__tests__/columnKindsByHand.test.ts``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from tests._source_files import REPO_ROOT, source_files

pytestmark = pytest.mark.meta

_SOURCE_ROOT = REPO_ROOT / "src" / "haute"
#: The one module that answers what kind a type is.
_KIND_MODULE = "src/haute/_polars_dtypes.py"

#: Every type class Polars defines, read from Polars so an upgrade adds its new types.
_TYPE_NAMES: frozenset[str] = frozenset(
    name
    for name in dir(pl)
    if isinstance(getattr(pl, name), type)
    and issubclass(getattr(pl, name), pl.DataType)
    and getattr(pl, name) is not pl.DataType
)
_NUMBER_TYPE_NAMES: frozenset[str] = frozenset(
    name for name in _TYPE_NAMES if getattr(pl, name).is_numeric()
)
_REGEX_SYNTAX = re.compile(r"[\\()\[\]|?*+^${}]")

#: ``path::scope::rule`` -> why the place names a type. The scope is the enclosing
#: function, class or module-level assignment.
_NAMED_TYPES: dict[str, str] = {
    "src/haute/_json_values.py::REQUEST_DTYPES::list": (
        "the types a deploy request's JSON can carry exactly; any other refuses the deploy "
        "naming its type (deploy/_request_frame.py)"
    ),
    "src/haute/_polars_steps.py::CAST_DTYPES::list": (
        "the Change types targets the editor offers, each one type a cast names"
    ),
    "src/haute/routes/_pivot_service.py::_SUM_INTEGER_DTYPES::list": (
        "the two cast targets a pivot's integer Sum widens to; which one a column takes is read "
        "from integer_range, not from the column's type name (XPL-01)"
    ),
    "src/haute/_polars_steps.py::DATE_PARSE_TARGETS::list": (
        "the two cast targets a text column reaches by parsing, each with its parse function"
    ),
    "src/haute/_polars_steps.py::CAST_REFUSALS::list": (
        "the cast targets Polars 2 refuses for each source kind, held equal to the installed "
        "Polars by tests/test_polars_steps_execution.py"
    ),
    "src/haute/_rating.py::_RATING_PRIMITIVE_DESCRIPTOR_KINDS::list": (
        "the persisted Rating factor descriptor kinds: the types a Rating factor supports, "
        "any other refused naming its type"
    ),
    "src/haute/_rating.py::_RATING_PARAMETRIC_DESCRIPTOR_KINDS::list": (
        "the persisted Rating factor descriptor kinds that carry parameters"
    ),
    "src/haute/_rating.py::is_rating_dtype_descriptor::comparison": (
        "reads a persisted descriptor's parameters, which its kind names"
    ),
    "src/haute/_map_columns.py::holds_map::isinstance": (
        "walks a nested type's inner type, which only a list or an array has"
    ),
    "src/haute/_trace_correlation.py::_deduplicable::isinstance": (
        "walks a nested type's inner type, which only a list or an array has"
    ),
    "src/haute/deploy/_mlflow.py::_build_signature._to_colspecs::isinstance": (
        "builds the MLflow form of a struct or a list, each its own MLflow type"
    ),
    "src/haute/_trace_enrichment.py::_coerce_pair_through_dtype::comparison": (
        "the trace boundary widens every cell to Float64, so only a narrower float is "
        "rounded through its own type"
    ),
    "src/haute/chunking.py::_CATEGORICAL_CAST_DTYPE_NAMES::list": (
        "reads the target a cast in pipeline code names, not a column's type"
    ),
}


@dataclass(frozen=True)
class Finding:
    line: int
    scope: str
    rule: str
    text: str


def _type_name(node: ast.AST) -> str | None:
    """The Polars type *node* names: ``pl.Float32``, ``pl.Datetime("us")`` or ``"Int64"``."""
    if isinstance(node, ast.Call):
        node = node.func
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"pl", "polars"}
        and node.attr in _TYPE_NAMES
    ):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _TYPE_NAMES:
        return node.value
    return None


def _members(node: ast.AST) -> list[ast.expr] | None:
    """The members of a literal collection, a ``frozenset(...)`` of one, or an ``A | B`` chain."""
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return [element for element in node.elts if not isinstance(element, ast.Starred)]
    if isinstance(node, ast.Dict):
        return [key for key in node.keys if key is not None]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set", "tuple"}
        and len(node.args) == 1
    ):
        return _members(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*(_members(node.left) or [node.left]), *(_members(node.right) or [node.right])]
    return None


def _names_kind(names: list[str | None]) -> bool:
    """Whether type names listed together decide a kind: a number type, or two types."""
    named = [name for name in names if name is not None]
    return any(name in _NUMBER_TYPE_NAMES for name in named) or len(named) >= 2


def _matches_a_name(text: str, pattern: re.Pattern[str], names: Iterable[str]) -> bool:
    """Whether *pattern* matches the whole of a name, or the start of one and is not prose:
    a message such as "one of int|float|str" is not a pattern anything is matched against."""
    return any(
        pattern.fullmatch(name) or (pattern.match(name) and not re.search(r"\s", text))
        for name in names
    )


def _is_type_pattern(text: str) -> bool:
    """Whether *text* is a pattern matched against a type's name: a number type's in any
    letter case, another type's as Polars writes it."""
    if not _REGEX_SYNTAX.search(text):
        return False
    try:
        if re.search(r"(?i)u?int|float|decimal", text) and _matches_a_name(
            text, re.compile(text, re.IGNORECASE), _NUMBER_TYPE_NAMES
        ):
            return True
        named = [name for name in _TYPE_NAMES - _NUMBER_TYPE_NAMES if name in text]
        return bool(named) and _matches_a_name(text, re.compile(text), named)
    except re.error:
        return False


def _is_type_stem(node: ast.AST) -> bool:
    """Whether *node* is text that begins a number type's name without being one: ``"Float"``."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) >= 3
        and node.value not in _TYPE_NAMES
        and any(name.startswith(node.value) for name in _NUMBER_TYPE_NAMES)
    )


def _scopes(tree: ast.AST) -> dict[ast.AST, str]:
    """Each node's enclosing function, class or module-level assignment, dotted."""
    scopes: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{scope}.{child.name}" if scope else child.name
            elif not scope and isinstance(child, ast.Assign | ast.AnnAssign):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                inner = names[0] if names else scope
            scopes[child] = inner
            visit(child, inner)

    visit(tree, "")
    return scopes


def kind_decisions_by_hand(source: str) -> list[Finding]:
    """Every place in *source* that decides a column's kind from a type's name."""
    tree = ast.parse(source)
    scopes = _scopes(tree)
    findings: list[Finding] = []
    seen_lists: set[int] = set()

    def report(node: ast.AST, rule: str) -> None:
        text = ast.get_source_segment(source, node) or rule
        findings.append(Finding(getattr(node, "lineno", 0), scopes.get(node, ""), rule, text))

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for op, left, right in zip(node.ops, operands, operands[1:], strict=False):
                if isinstance(op, ast.Eq | ast.NotEq | ast.Is | ast.IsNot) and any(
                    _type_name(side) in _NUMBER_TYPE_NAMES for side in (left, right)
                ):
                    report(node, "comparison")
                elif isinstance(op, ast.In | ast.NotIn):
                    members = _members(right)
                    if members is not None and any(_type_name(m) for m in members):
                        seen_lists.add(id(right))
                        report(node, "membership")
                    elif _is_type_stem(left):
                        report(node, "pattern")
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id in {"isinstance", "issubclass"}
                and len(node.args) == 2
            ):
                members = _members(node.args[1]) or [node.args[1]]
                seen_lists.add(id(node.args[1]))
                if _names_kind([_type_name(member) for member in members]):
                    report(node, "isinstance")
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in {"startswith", "endswith", "removeprefix"}
                and any(_is_type_stem(arg) for arg in (_members(node.args[0]) or node.args[:1]))
            ):
                report(node, "pattern")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_type_pattern(node.value):
                report(node, "pattern")
        members = _members(node)
        if (
            members
            and id(node) not in seen_lists
            and not isinstance(node, ast.BinOp)
            and all(_type_name(member) for member in members)
            and _names_kind([_type_name(member) for member in members])
        ):
            seen_lists.add(id(node))
            report(node, "list")
    return sorted(findings, key=lambda finding: (finding.line, finding.rule))


def _repository_findings() -> dict[str, list[Finding]]:
    found: dict[str, list[Finding]] = {}
    for path in source_files(_SOURCE_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == _KIND_MODULE:
            continue
        for finding in kind_decisions_by_hand(path.read_text(encoding="utf-8")):
            found.setdefault(f"{rel}::{finding.scope}::{finding.rule}", []).append(finding)
    return found


def test_no_source_outside_the_kind_module_decides_a_kind_from_a_type_name() -> None:
    found = _repository_findings()
    unexplained = {
        key: [f"line {finding.line}: {finding.text}" for finding in findings]
        for key, findings in found.items()
        if key not in _NAMED_TYPES
    }
    assert not unexplained, (
        "Decide a column's kind with haute._polars_dtypes (column_kind and its groups), "
        "not by naming types. A place that must name one type, such as a cast, goes on "
        f"_NAMED_TYPES with its reason: {unexplained}"
    )


def test_every_named_type_place_is_still_in_the_source() -> None:
    stale = sorted(set(_NAMED_TYPES) - set(_repository_findings()))
    assert not stale, f"_NAMED_TYPES entries that no longer name a type: {stale}"


@pytest.mark.parametrize(
    "source",
    [
        "dtype.base_type() in (pl.Float32, pl.Float64)",
        "dtype == pl.Float32",
        "name != 'Int64'",
        "isinstance(dtype, pl.Decimal)",
        "isinstance(dtype, pl.Date | pl.Datetime)",
        "FLOATS = frozenset({'Float32', 'Float64'})",
        "RANGES = {pl.Int8: (-128, 127), pl.UInt8: (0, 255)}",
        "base in (pl.String, pl.Categorical)",
        "PATTERN = re.compile(r'float(?:16|32|64)|f(?:16|32|64)', re.IGNORECASE)",
        "name.startswith('Float')",
        "'UInt' in rendered",
        r"DATES = re.compile(r'(?:Date|Datetime)(?:\(.*\))?')",
        "TEXT = frozenset({'String', 'Categorical'})",
    ],
)
def test_the_scan_finds_a_kind_decided_by_hand(source: str) -> None:
    assert kind_decisions_by_hand(source), source


@pytest.mark.parametrize(
    "source",
    [
        "dtype.is_float()",
        "column_kind(dtype) == 'float'",
        "pl.col('premium').cast(pl.Float64)",
        "schema = {'premium': pl.Float32(), 'quote_id': pl.String()}",
        "return pl.Float64, proba_dtype",
        "isinstance(dtype, pl.Datetime)",
        "dtype == pl.Boolean",
        "raise ValueError('Cast it upstream to Int64 (or Float64).')",
        "kind in {'integer', 'float'}",
    ],
)
def test_the_scan_leaves_kinds_asked_of_the_module_and_single_type_facts(source: str) -> None:
    assert not kind_decisions_by_hand(source), source


def test_the_kind_module_exists_where_the_scan_exempts_it() -> None:
    assert (REPO_ROOT / _KIND_MODULE).is_file()
    assert Path(_KIND_MODULE).name == "_polars_dtypes.py"
