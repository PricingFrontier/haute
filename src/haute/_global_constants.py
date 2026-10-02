"""Global constants: what node code reads, what binds the reserved name, and the sentinel.

Node code reads a pipeline's global constants through one reserved name,
``global_constants``, as attributes (``global_constants.rate``). The reads
analysis here finds the constants a node names, from its structured steps and
from the AST of its code, so cache identities can sign exactly those values.
Code that uses the name any other way may read every constant. The analysis
never executes code.

The binding checks find code that would rebind or shadow the reserved name,
which parse and save refuse. The standalone sentinel is what module-level code
and helper functions see as ``global_constants`` in a pipeline file.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Final, TypeAlias

from haute._types import GLOBAL_CONSTANTS_NAME
from haute.errors import GlobalConstantError


class _EveryConstant:
    """The reads of code that may reach any constant, because it passes the name on."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "EVERY_CONSTANT"


EVERY_CONSTANT: Final = _EveryConstant()

ConstantReads: TypeAlias = frozenset[str] | _EveryConstant
"""The names a node reads, or ``EVERY_CONSTANT``."""


def _parse_body(code: str) -> ast.Module | None:
    """Parse node code as the body of a function, so a closing ``return`` parses."""
    body = textwrap.dedent(code)
    wrapped = "def _haute_node_body():\n" + "\n".join(
        f"    {line}" if line else line for line in body.splitlines()
    )
    try:
        return ast.parse(wrapped)
    except SyntaxError:
        return None


def code_constant_reads(code: str) -> ConstantReads:
    """The constants *code* names as ``global_constants.<name>``.

    Every attribute of the reserved name counts, including one inside an
    f-string's expression (the AST exposes it on every supported Python
    version). Any other use of the name, or code that does not parse, may
    read every constant. String literals and comments are not inspected: a
    read they hide is refused when the code runs.
    """
    if GLOBAL_CONSTANTS_NAME not in code:
        return frozenset()
    tree = _parse_body(code)
    if tree is None:
        return EVERY_CONSTANT
    reads: set[str] = set()
    attribute_receivers: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == GLOBAL_CONSTANTS_NAME
        ):
            reads.add(node.attr)
            attribute_receivers.add(id(node.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == GLOBAL_CONSTANTS_NAME
            and id(node) not in attribute_receivers
        ):
            return EVERY_CONSTANT
    return frozenset(reads)


def step_constant_reads(steps: object) -> frozenset[str]:
    """The constants named by every Constant operand anywhere in *steps*."""
    names: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            name = value.get("name")
            if value.get("kind") == "constant" and isinstance(name, str):
                names.add(name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(steps)
    return frozenset(names)


def node_code_sources(config: Mapping[str, Any]) -> list[str]:
    """The code a node's configuration runs: its ``code`` and each free-code step's."""
    sources: list[str] = []
    code = config.get("code")
    if isinstance(code, str) and code.strip():
        sources.append(code)
    steps = config.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, Mapping) or step.get("kind") != "free_code":
                continue
            step_code = step.get("code")
            if isinstance(step_code, str) and step_code.strip():
                sources.append(step_code)
    return sources


def node_constant_reads(config: Mapping[str, Any]) -> ConstantReads:
    """The constants a node reads, from the configuration it executes."""
    reads = set(step_constant_reads(config.get("steps")))
    for code in node_code_sources(config):
        code_reads = code_constant_reads(code)
        if isinstance(code_reads, _EveryConstant):
            return EVERY_CONSTANT
        reads |= code_reads
    return frozenset(reads)


def _binds_name(node: ast.AST, name: str) -> bool:
    """Whether *node* binds *name* in the scope it appears in."""
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
    if isinstance(node, ast.arg):
        return node.arg == name
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == name
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return node.name == name
    if isinstance(node, ast.MatchMapping):
        return node.rest == name
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return name in node.names
    return False


def code_binds_global_constants(code: str) -> bool:
    """Whether node *code* binds the reserved name anywhere, shadowing the constants.

    Code that does not parse binds nothing here; it fails where it runs.
    """
    if GLOBAL_CONSTANTS_NAME not in code:
        return False
    tree = _parse_body(code)
    return tree is not None and any(
        _binds_name(node, GLOBAL_CONSTANTS_NAME) for node in ast.walk(tree)
    )


def _module_scope_nodes(statements: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of *statements* that runs in module scope.

    A function or class contributes its name, decorators and the expressions
    evaluated where it is defined, but not its body, whose bindings are its own.
    """
    stack: list[ast.AST] = list(statements)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(node.decorator_list)
            stack.extend(node.args.defaults)
            stack.extend(default for default in node.args.kw_defaults if default is not None)
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.decorator_list)
            stack.extend(node.bases)
            stack.extend(keyword.value for keyword in node.keywords)
            continue
        if isinstance(node, ast.Lambda):
            stack.extend(node.args.defaults)
            stack.extend(default for default in node.args.kw_defaults if default is not None)
            continue
        stack.extend(ast.iter_child_nodes(node))


def is_generated_binding(statement: ast.stmt, *, receiver: str) -> bool:
    """Whether *statement* is the generated ``global_constants = <receiver>.global_constants``."""
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return False
    target = statement.targets[0]
    value = statement.value
    return (
        isinstance(target, ast.Name)
        and target.id == GLOBAL_CONSTANTS_NAME
        and isinstance(value, ast.Attribute)
        and value.attr == GLOBAL_CONSTANTS_NAME
        and isinstance(value.value, ast.Name)
        and value.value.id == receiver
    )


def module_binding_lines(statements: Iterable[ast.stmt], *, receiver: str | None) -> list[int]:
    """Lines of module-scope statements that bind the reserved name.

    The generated binding for *receiver* is exempt; with *receiver* ``None``
    (a preamble, which precedes the constructor) nothing is.
    """
    lines: list[int] = []
    for statement in statements:
        if receiver is not None and is_generated_binding(statement, receiver=receiver):
            continue
        if any(
            _binds_name(node, GLOBAL_CONSTANTS_NAME) for node in _module_scope_nodes([statement])
        ):
            lines.append(statement.lineno)
    return lines


def preamble_binds_global_constants(preamble: str) -> bool:
    """Whether a preamble's module-scope statements bind the reserved name.

    A preamble that does not parse binds nothing here; it fails where it runs.
    """
    if GLOBAL_CONSTANTS_NAME not in preamble:
        return False
    try:
        tree = ast.parse(preamble)
    except SyntaxError:
        return False
    return bool(module_binding_lines(tree.body, receiver=None))


_OUTSIDE_A_RUN = (
    "Global constants are read in node code while the pipeline runs: call "
    "pipeline.run(source=...) or pipeline.score(...). A helper function takes a "
    "constant as an argument."
)


class _StandaloneGlobalConstants:
    """What module-level code and helper functions see as ``global_constants``.

    A run binds each node function's own values; everywhere else this sentinel
    answers any read with where constants can be read.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise GlobalConstantError(_OUTSIDE_A_RUN, constant=name)

    def __setattr__(self, name: str, value: object) -> None:
        raise GlobalConstantError(
            "Global constants are read-only in code; change them in the Constants pane.",
            constant=name,
        )

    def __delattr__(self, name: str) -> None:
        raise GlobalConstantError(
            "Global constants are read-only in code; change them in the Constants pane.",
            constant=name,
        )

    def __repr__(self) -> str:
        return "<global constants: bound to node code while the pipeline runs>"


STANDALONE_GLOBAL_CONSTANTS: Final = _StandaloneGlobalConstants()
"""The sentinel ``pipeline.global_constants`` and ``submodel.global_constants`` return."""
