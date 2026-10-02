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
import datetime as _dt
import inspect
import textwrap
import types
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, Final, TypeAlias

from haute._types import (
    GLOBAL_CONSTANTS_FILE,
    GLOBAL_CONSTANTS_NAME,
    GlobalConstant,
    GraphNode,
    PipelineGraph,
)
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


def executed_config(node: GraphNode, node_map: Mapping[str, GraphNode]) -> Mapping[str, Any]:
    """The configuration *node* executes: an instance runs its original's."""
    config = node.data.config
    original = config.get("instanceOf")
    if isinstance(original, str) and original in node_map:
        return node_map[original].data.config
    return config


def graph_constant_reads(
    nodes: Iterable[GraphNode],
    node_map: Mapping[str, GraphNode],
) -> ConstantReads:
    """The constants any of *nodes* reads, instances resolved through *node_map*."""
    reads: set[str] = set()
    for node in nodes:
        node_reads = node_constant_reads(executed_config(node, node_map))
        if isinstance(node_reads, _EveryConstant):
            return EVERY_CONSTANT
        reads |= node_reads
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


# ---------------------------------------------------------------------------
# Run-time namespace
# ---------------------------------------------------------------------------


def _resolved_value(constant_type: str, value: object) -> object:
    if constant_type == "date":
        assert isinstance(value, str)
        return _dt.date.fromisoformat(value)
    return value


class GlobalConstantsNamespace:
    """What node code reads as ``global_constants`` during one run.

    It holds the pipeline's constants resolved for one source as concrete
    values, so a lazy callback reads the run's value on whatever thread runs
    it. :func:`restricted_view` returns the view one piece of code may read: a
    read outside the names that code mentions is refused, which keeps the
    reads the cache identities sign complete. The class has no public
    attribute, so every valid constant name reads its constant.
    """

    __slots__ = ("_allowed", "_defined", "_error", "_source", "_values")

    def __init__(
        self,
        constants: Iterable[GlobalConstant],
        *,
        source: str,
        error: str | None = None,
        allowed: frozenset[str] | None = None,
    ) -> None:
        values: dict[str, object] = {}
        defined: list[str] = []
        for constant in constants:
            defined.append(constant.name)
            if constant.by_source is None:
                values[constant.name] = _resolved_value(constant.type, constant.value)
            elif source in constant.by_source:
                values[constant.name] = _resolved_value(constant.type, constant.by_source[source])
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_defined", tuple(defined))
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_error", error)
        object.__setattr__(self, "_allowed", allowed)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if self._error is not None:
            raise GlobalConstantError(
                f"Global constants could not be loaded from {GLOBAL_CONSTANTS_FILE}: {self._error}",
                constant=name,
                source=self._source,
            )
        if name not in self._defined:
            defined = (
                "Defined: " + ", ".join(self._defined) + "."
                if self._defined
                else "This pipeline has no global constants."
            )
            raise GlobalConstantError(
                f"Global constant {name!r} is not defined. Define it in the Constants pane. "
                f"{defined}",
                constant=name,
                source=self._source,
            )
        if self._allowed is not None and name not in self._allowed:
            raise GlobalConstantError(
                f"Code read global constant {name!r} without naming it. Write "
                f"global_constants.{name} so its value is tracked.",
                constant=name,
                source=self._source,
            )
        if name not in self._values:
            raise GlobalConstantError(
                f"Global constant {name!r} has no value for source {self._source!r}. "
                f"Set its {self._source} value in the Constants pane.",
                constant=name,
                source=self._source,
            )
        return self._values[name]

    def __setattr__(self, name: str, value: object) -> None:
        raise GlobalConstantError(
            "Global constants are read-only in code; change them in the Constants pane.",
            constant=name,
            source=self._source,
        )

    def __delattr__(self, name: str) -> None:
        raise GlobalConstantError(
            "Global constants are read-only in code; change them in the Constants pane.",
            constant=name,
            source=self._source,
        )

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            _rebuild_namespace,
            (self._values, self._defined, self._source, self._error, self._allowed),
        )

    def __dir__(self) -> list[str]:
        names = self._defined if self._allowed is None else sorted(self._allowed)
        return list(names)

    def __repr__(self) -> str:
        return f"<global constants for source {self._source!r}: {', '.join(self._defined)}>"


def namespace_for_graph(graph: PipelineGraph, source: str) -> GlobalConstantsNamespace:
    """*graph*'s constants and load error resolved for *source*."""
    return GlobalConstantsNamespace(
        graph.global_constants, source=source, error=graph.global_constants_error
    )


def restricted_view(
    namespace: GlobalConstantsNamespace, reads: ConstantReads
) -> GlobalConstantsNamespace:
    """The view code with *reads* may use (all of *namespace* for ``EVERY_CONSTANT``)."""
    allowed = None if isinstance(reads, _EveryConstant) else frozenset(reads)
    return _rebuild_namespace(
        namespace._values, namespace._defined, namespace._source, namespace._error, allowed
    )


def _rebuild_namespace(
    values: dict[str, object],
    defined: tuple[str, ...],
    source: str,
    error: str | None,
    allowed: frozenset[str] | None,
) -> GlobalConstantsNamespace:
    namespace = object.__new__(GlobalConstantsNamespace)
    for slot, value in (
        ("_values", values),
        ("_defined", defined),
        ("_source", source),
        ("_error", error),
        ("_allowed", allowed),
    ):
        object.__setattr__(namespace, slot, value)
    return namespace


def node_code_globals(
    namespace: Mapping[str, Any] | None,
    graph: PipelineGraph,
    source: str,
) -> dict[str, Any]:
    """A new mapping: *namespace* (never mutated) plus ``global_constants`` for *source*."""
    return {
        **(namespace or {}),
        GLOBAL_CONSTANTS_NAME: namespace_for_graph(graph, source),
    }


def bind_code_view(namespace: Mapping[str, Any] | None, code: str) -> Mapping[str, Any] | None:
    """*namespace* with its constants restricted to what *code* names, if it has any."""
    if not namespace:
        return namespace
    constants = namespace.get(GLOBAL_CONSTANTS_NAME)
    if not isinstance(constants, GlobalConstantsNamespace):
        return namespace
    view = restricted_view(constants, code_constant_reads(code))
    return {**namespace, GLOBAL_CONSTANTS_NAME: view}


def function_constant_reads(fn: Callable[..., Any]) -> ConstantReads:
    """The constants *fn*'s source names; every constant when its source cannot be read."""
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        return EVERY_CONSTANT
    return code_constant_reads(source)


def bind_function_view(
    fn: Callable[..., Any], constants: GlobalConstantsNamespace
) -> Callable[..., Any]:
    """A copy of *fn* whose module globals bind ``global_constants`` to its own view.

    The copy keeps *fn*'s code, defaults and closure, so it behaves as *fn*
    does, and anything it defines (a lazy callback, say) reads the same view
    on whatever thread runs it. A callable that is not a plain function is
    returned as it is.
    """
    if not isinstance(fn, types.FunctionType):
        return fn
    view = restricted_view(constants, function_constant_reads(fn))
    copy = types.FunctionType(
        fn.__code__,
        {**fn.__globals__, GLOBAL_CONSTANTS_NAME: view},
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    copy.__kwdefaults__ = fn.__kwdefaults__
    copy.__qualname__ = fn.__qualname__
    copy.__module__ = fn.__module__
    copy.__doc__ = fn.__doc__
    copy.__annotations__ = fn.__annotations__
    copy.__dict__.update(fn.__dict__)
    return copy


# ---------------------------------------------------------------------------
# Step references
# ---------------------------------------------------------------------------


def reference_problem(reference: Any, constants: Mapping[str, GlobalConstant]) -> str | None:
    """Why a step's Constant operand cannot read its constant, or None when it can.

    *reference* is a :class:`haute._polars_steps.ConstantReference`; the
    constant must exist, have a type its slot takes, and hold no value below
    zero where the slot needs one of at least zero.
    """
    constant = constants.get(reference.name)
    if constant is None:
        return f"Global constant {reference.name!r} is not defined."
    if constant.type not in reference.types:
        return (
            f"Global constant {reference.name!r} is {constant.type}; this value takes "
            f"{' or '.join(reference.types)}."
        )
    if reference.non_negative:
        values = (
            {"every source": constant.value}
            if constant.by_source is None
            else dict(constant.by_source)
        )
        for source, value in values.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                where = source if constant.by_source is None else f"source {source!r}"
                return (
                    f"Global constant {reference.name!r} must be zero or more; it is {value} "
                    f"for {where}."
                )
    return None


def node_step_constant_problems(
    node: GraphNode,
    constants: Mapping[str, GlobalConstant],
) -> list[tuple[int, str]]:
    """``(step_index, reason)`` for each Constant operand of *node*'s steps that cannot read.

    A node without steps, or whose steps do not render, has none: an invalid
    step list is refused by its own validation.
    """
    from haute._polars_steps import PolarsStepError, render_polars_steps, stepped_surface_for
    from haute._types import NodeType

    steps = node.data.config.get("steps")
    if not isinstance(steps, list):
        return []
    try:
        surface = stepped_surface_for(NodeType(node.data.nodeType))
        rendered = render_polars_steps(steps, start=surface.start)
    except (ValueError, PolarsStepError):
        return []
    problems: list[tuple[int, str]] = []
    for reference in rendered.constant_references:
        problem = reference_problem(reference, constants)
        if problem is not None:
            problems.append((reference.step_index, problem))
    return problems
