"""Codegen builder registry — per-type ``_gen_*`` functions describing each node.

Paired with the exec-side builders in :mod:`haute._builders` via the
unified :data:`haute._registry.NODE_REGISTRY`.  The orchestration module
:mod:`haute.codegen` imports this file for its side-effect registrations
and dispatches through ``NODE_REGISTRY`` at codegen time.

Layering:

- :mod:`haute._registry`           — dispatch data model (dataclass + dict).
- :mod:`haute._builders`           — exec-side builders.
- :mod:`haute._codegen_builders`   — codegen-side builders (this module).
- :mod:`haute.codegen`             — orchestration: ``graph_to_code``,
                                     ``graph_to_code_multi``, pipeline-
                                     level assembly.

A builder returns a :class:`NodeSource`: what the node's generated function
says, before layout. Every node type except ``polars`` is configured — its
decorator performs the node's work when the file runs on its own
(:mod:`haute._standalone_nodes`) — so its function is a *declaration* (the
inputs and a ``...`` body) or, when the user wrote code, a *hook* that takes
the configured result as ``df``. :func:`render_node_source` prints a
``NodeSource`` the way ``ruff format`` would.

SUBMODEL / SUBMODEL_PORT are registered with codegen builders that raise
loudly.  By the time codegen dispatches on a node, the submodel boundary
has already been handled — either ``graph_to_code_multi`` emitted the
submodel as its own file and skipped the placeholder in ``root_nodes``
(see ``codegen.graph_to_code_multi``), or ``flatten_graph`` removed the
placeholder entirely.  Reaching these builders means the preflight filter
has broken; fail loudly rather than emitting silent passthrough code.
"""

from __future__ import annotations

import ast
import symtable
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from haute._code_extraction import (
    INCOMPLETE_STEPS_BODY,
    INCOMPLETE_TRANSFORM_BODY,
    POLARS_OUTPUT_DECLARATION,
)
from haute._config_io import config_path_for_node
from haute._config_validation import validate_optimiser_input_selectors
from haute._edge_join import build_edge_join_kwargs, edge_join_config_to_decorator_kwargs
from haute._explore_charts import validate_explore_charts
from haute._explore_overview import validate_explore_overview
from haute._explore_pivots import validate_explore_pivot_state
from haute._graph_utils import (
    _sanitize_func_name,
    duplicate_input_names,
    resolve_input_mapping_names,
)
from haute._polars_steps import (
    STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE,
    PolarsStepError,
    render_polars_steps,
    step_input_names,
)
from haute._rating import _normalise_combined_outputs
from haute._rating_step_config import normalise_rating_tables
from haute._registry import CodegenFn
from haute._registry import (
    register_codegen as _register_codegen_in_registry,
)
from haute._registry import (
    set_codegen as _set_codegen_in_registry,
)
from haute._source_layout import INDENT, Doc, arguments, literal, print_doc
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._types import (
    COLUMN_CONFIG_KEYS,
    NODE_TYPE_TO_DECORATOR,
    GraphNode,
    NodeType,
)
from haute.errors import ConfigError, HauteError, ParseError

#: The annotation every frame parameter and code-carrying function returns.
FRAME = "pl.LazyFrame"


# ---------------------------------------------------------------------------
# What a node's function says, and how it prints.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Param:
    """One function parameter; declarations leave ``annotation`` unset."""

    name: str
    annotation: str | None = None


@dataclass(frozen=True, slots=True)
class NodeSource:
    """A node's generated function before layout.

    ``body`` is ``None`` for a declaration: its body is ``...`` (or the
    docstring alone) and it carries no annotations. Otherwise it is the
    function's statements, unindented, including any closing ``return df``.
    """

    decorator: str
    keywords: tuple[tuple[str, object], ...]
    params: tuple[Param, ...]
    keyword_only: tuple[Param, ...] = ()
    returns: str | None = None
    description: str = ""
    body: str | None = None


def _sanitize_description(desc: str) -> str:
    r"""Sanitize a description for safe interpolation between ``\"\"\"`` triple
    double-quotes.

    Produces content *x* such that ``f'\"\"\"{x}\"\"\"'`` is a valid
    Python triple-double-quoted string literal whose
    ``inspect.cleandoc`` / ``ast.get_docstring`` value equals the
    original *desc*.  The invariants (see
    ``tests/test_codegen_docstring_roundtrip.py``):

    1. *Syntactic safety* — the generated source always parses
       (no matter what escape sequences, backslashes, or triple-quote
       runs appear in *desc*).
    2. *Round-trip* — ``ast.get_docstring(fn) == desc`` for every
       pathological *desc* covered by the docstring round-trip tests.

    Implementation:

    - Every backslash is doubled so sequences like ``\U`` (Windows
      paths) and ``\N{...}`` (named escapes) stay literal instead of
      being re-parsed by the Python compiler.
    - Every ``"`` is backslash-escaped so no run of 3 or more quotes
      can form and prematurely close the enclosing ``\"\"\"`` literal.
    - A leading ``\n`` is prepended when *desc* contains a newline or
      has edge whitespace (so the first non-empty line is on line 2).
      This neutralises ``inspect.cleandoc``'s behaviour of stripping
      the first line's leading whitespace and the minimum common
      indent of the remaining lines, which otherwise corrupts user-
      authored indented multi-line descriptions.
    - Curly braces are left untouched: the value is never spliced into a
      format template.
    """
    # Neutralise cleandoc: prepend a newline when desc has newlines or
    # leading/trailing whitespace that cleandoc would strip.  For all-ASCII
    # single-line descriptions without edge whitespace, cleandoc is a no-op.
    if "\n" in desc or desc != desc.strip():
        value = "\n" + desc
    else:
        value = desc
    # Double every backslash so Python's reader does not interpret embedded
    # escape sequences (backslash-U, backslash-N, etc).
    escaped = value.replace("\\", "\\\\")
    # Escape every " so no triple-quote run can form inside the docstring
    # and prematurely close the enclosing """ literal.
    escaped = escaped.replace('"', '\\"')
    return escaped


def _docstring_lines(description: str) -> list[str]:
    """The indented docstring for *description*, continuation lines indented as ruff does.

    ``inspect.cleandoc`` removes that common indentation again on read, so the
    round trip is unchanged. It measures the indentation on lines with text
    only, so continuation lines that are all whitespace stay as they are.
    """
    first, *rest = _sanitize_description(description).split("\n")
    if any(line.strip() for line in rest):
        rest = [f"{INDENT}{line}" if line else "" for line in rest]
    lines = [f'"""{first}', *rest]
    lines[-1] += '"""'
    return [f"{INDENT}{lines[0]}", *lines[1:]]


def _starts_with_string(body: str) -> bool:
    """Whether the body's first statement is a bare string, which would read as a docstring."""
    try:
        statements = ast.parse(body).body
    except SyntaxError:
        return False
    return bool(
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    )


def _param_doc(param: Param) -> Doc:
    return param.name if param.annotation is None else f"{param.name}: {param.annotation}"


def render_node_source(
    source: NodeSource,
    *,
    func_name: str,
    receiver: str = "pipeline",
    extra_keywords: Sequence[tuple[str, object]] = (),
) -> str:
    """Print a node's function the way ``ruff format`` lays it out."""
    keywords = [*source.keywords, *extra_keywords]
    decorator: Doc = f"@{receiver}.{source.decorator}"
    if keywords:
        decorator = [
            decorator,
            arguments("(", [[name, "=", literal(value)] for name, value in keywords], ")"),
        ]
    entries: list[Doc] = [_param_doc(param) for param in source.params]
    if source.keyword_only:
        entries.append("*")
        entries.extend(_param_doc(param) for param in source.keyword_only)
    returns = f" -> {source.returns}" if source.returns else ""
    one_line_declaration = source.body is None and not source.description
    signature = [f"def {func_name}", arguments("(", entries, ")", signature=True), f"{returns}:"]
    if one_line_declaration:
        signature.append(" ...")
    lines = [print_doc(decorator), print_doc(signature)]
    if source.description:
        lines.extend(_docstring_lines(source.description))
    if source.body is not None:
        if not source.description and _starts_with_string(source.body):
            # Keep a leading string in the code: an empty docstring comes first.
            lines.append(f'{INDENT}""""""')
        lines.extend(f"{INDENT}{line}" if line.strip() else "" for line in source.body.splitlines())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Shared builder helpers.
# ---------------------------------------------------------------------------


def _build_extra_kwargs(config: dict, keys: tuple[str, ...]) -> list[tuple[str, object]]:
    """Decorator keywords for present config keys, skipping ``None``, ``""`` and ``[]``."""
    return [
        (key, config[key])
        for key in keys
        if config.get(key) is not None and config.get(key) != "" and config.get(key) != []
    ]


def _params(names: Sequence[str], annotation: str | None = None) -> tuple[Param, ...]:
    """Parameters for the per-edge input names supplied by the orchestrator.

    The graph orchestrator validates duplicate names before reaching a
    builder.  This helper also asserts that upstream invariant defensively;
    inventing suffixes here would make generated signatures disagree with the
    executor's edge-derived bindings.
    """
    duplicates = duplicate_input_names(list(names))
    assert not duplicates, f"duplicate codegen input name(s): {duplicates!r}"
    return tuple(Param(name, annotation) for name in names)


def _func_name(node: GraphNode) -> str:
    return _sanitize_func_name(node.data.label)


def _config_keywords(node: GraphNode, func_name: str) -> tuple[str, tuple[tuple[str, object], ...]]:
    """The decorator name and ``config=`` keyword of a config-backed node.

    The config lives in ``config/<type>/<name>.json`` and is written by the
    config-io save path, so the decorator carries only that path.
    """
    node_type = node.data.nodeType
    try:
        decorator = NODE_TYPE_TO_DECORATOR[node_type]
    except KeyError as exc:
        raise HauteError(
            "config-backed node has no registered decorator; this is a codegen bug",
            node_id=node.id,
            node_label=node.data.label,
            node_type=str(node_type),
        ) from exc
    config_path = config_path_for_node(node_type, func_name).as_posix()
    return decorator, (("config", config_path),)


def _reject_df_input(node: GraphNode, source_names: Sequence[str]) -> None:
    """A node that may carry code cannot take an input named ``df``.

    ``df`` names the frame the node produced; a declaration whose first
    parameter is ``df`` would read as a hook.
    """
    if "df" in source_names:
        raise ConfigError(
            "Input name 'df' is reserved for the frame this node produces; rename the "
            "upstream node or frame.",
            node_id=node.id,
            node_label=node.data.label,
        )


def _stepped_body_code(
    config: dict, node_type: NodeType, edge_names: list[str]
) -> tuple[str, bool]:
    """The user-code lines a frame-mode surface's hook carries.

    For a stepped config: the rendering against the surface's eligible input
    names, or ``incomplete=True`` when the steps cannot be rendered (the body
    then carries the raising placeholder; the save warns which step). For a
    code-only config: the authored code as it is.
    """
    steps = config.get("steps")
    if not isinstance(steps, list):
        return str(config.get("code") or "").strip(), False
    try:
        rendered = render_polars_steps(
            steps, step_input_names(node_type, edge_names), start="frame"
        ).code
    except PolarsStepError:
        return "", True
    return rendered, False


_INCOMPLETE_STEPS = INCOMPLETE_STEPS_BODY.rstrip("\n")


def _unindented(body: str) -> str:
    """A generated body constant written for a function body, at column 0."""
    return "\n".join(line.removeprefix(INDENT) for line in body.rstrip("\n").splitlines())


def _configured_node(
    node: GraphNode,
    source_names: list[str],
    *,
    decorator: str,
    keywords: tuple[tuple[str, object], ...],
) -> NodeSource:
    """A configured node's function: a declaration, or a ``df`` hook holding its code."""
    node_type = node.data.nodeType
    description = node.data.description
    if node_type not in CODE_NODE_TYPES:
        return NodeSource(decorator, keywords, _params(source_names), description=description)
    _reject_df_input(node, source_names)
    code, incomplete = _stepped_body_code(node.data.config, node_type, source_names)
    if not code and not incomplete:
        return NodeSource(decorator, keywords, _params(source_names), description=description)
    tail = _unindented(_INCOMPLETE_STEPS) if incomplete else f"{code}\nreturn df"
    if node_type == NodeType.EXTERNAL_FILE:
        binding = f"df = {source_names[0]}\n" if source_names else ""
        return NodeSource(
            decorator,
            keywords,
            _params(source_names, FRAME),
            keyword_only=(Param("obj"),),
            returns=FRAME,
            description=description,
            body=binding + tail,
        )
    params = (Param("df", FRAME), *_params(source_names[1:], FRAME))
    return NodeSource(
        decorator, keywords, params, returns=FRAME, description=description, body=tail
    )


def _config_backed(node: GraphNode, source_names: list[str]) -> NodeSource:
    decorator, keywords = _config_keywords(node, _func_name(node))
    return _configured_node(node, source_names, decorator=decorator, keywords=keywords)


# ---------------------------------------------------------------------------
# Codegen builder callable signature and registration.
# ---------------------------------------------------------------------------

#: Builder signature: (node, source_names) -> the node's generated function.
CodegenBuilder = Callable[[GraphNode, list[str]], NodeSource]


def _register_codegen(node_type: NodeType) -> Callable[[CodegenBuilder], CodegenBuilder]:
    """Decorator to register a codegen builder for *node_type*.

    Writes into the unified :data:`haute._registry.NODE_REGISTRY`.
    """
    return _register_codegen_in_registry(node_type)


# ---------------------------------------------------------------------------
# Per-type builders
# ---------------------------------------------------------------------------


@_register_codegen(NodeType.API_INPUT)
def _gen_api_input(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.LIVE_SWITCH)
def _gen_live_switch(node: GraphNode, source_names: list[str]) -> NodeSource:
    # The decorator reads the input-to-scenario map from the sidecar and routes
    # the active scenario's input, as the executor does.
    return _config_backed(node, source_names)


@_register_codegen(NodeType.CONSTANT)
def _gen_constant(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.MODEL_SCORE)
def _gen_model_score(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.BANDING)
def _gen_banding(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.RATING_STEP)
def _gen_rating_step(node: GraphNode, source_names: list[str]) -> NodeSource:
    # Codegen runs at save: a malformed table or combined-output shape fails
    # here as it would at execution, though neither is rendered into the code.
    normalise_rating_tables(node.data.config)
    _normalise_combined_outputs(node.data.config)
    return _config_backed(node, source_names)


@_register_codegen(NodeType.SCENARIO_EXPANDER)
def _gen_scenario_expander(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.OPTIMISER)
def _gen_optimiser(node: GraphNode, source_names: list[str]) -> NodeSource:
    # A multi-input optimiser must name its data edge; the decorator passes that
    # exact input through, so a bad selector fails at save as at execution.
    validate_optimiser_input_selectors(
        node.data.nodeType, node.data.config, source_names, node_label=_func_name(node)
    )
    return _config_backed(node, source_names)


@_register_codegen(NodeType.MODELLING)
def _gen_modelling(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.OPTIMISER_APPLY)
def _gen_optimiser_apply(node: GraphNode, source_names: list[str]) -> NodeSource:
    validate_optimiser_input_selectors(
        node.data.nodeType, node.data.config, source_names, node_label=_func_name(node)
    )
    return _config_backed(node, source_names)


# Explore uses nested decorator kwargs for presentation configuration, rather
# than flat snake_case kwargs. These opaque values let the UI evolve without
# coupling code generation to every presentation-specific field.
def _explore_keywords(
    overview: Any,
    pivot_formulas: Any,
    pivots: Any,
    charts: Any,
    column_config: dict[str, Any],
) -> list[tuple[str, object]]:
    """The keywords for ``@pipeline.explore(...)``, in a stable order.

    Empty values are skipped (so the decorator stays bare when nothing is
    set). Non-empty values are emitted overview, pivot_formulas, pivots, charts,
    then shared column metadata.
    """
    overview = validate_explore_overview(overview, context="explore node config")
    pivot_formulas, pivots = validate_explore_pivot_state(
        pivot_formulas, pivots, context="explore node config"
    )
    charts = validate_explore_charts(charts, context="explore node config")
    keywords: list[tuple[str, object]] = []
    if overview:
        keywords.append(("overview", overview))
    if pivot_formulas:
        keywords.append(("pivot_formulas", pivot_formulas))
    if pivots:
        keywords.append(("pivots", pivots))
    if charts:
        keywords.append(("charts", charts))
    keywords.extend(_build_extra_kwargs(column_config, COLUMN_CONFIG_KEYS))
    return keywords


@_register_codegen(NodeType.EXPLORE)
def _gen_explore(node: GraphNode, source_names: list[str]) -> NodeSource:
    if len(source_names) != 1:
        raise ParseError(
            "Explore nodes must have exactly one incoming edge.",
            node_id=node.id,
            node_label=node.data.label,
            incoming_count=len(source_names),
            incoming_sources=source_names,
        )
    config = node.data.config
    keywords = _explore_keywords(
        config["overview"] if "overview" in config else {},
        config.get("pivot_formulas"),
        config["pivots"] if "pivots" in config else [],
        config["charts"] if "charts" in config else [],
        config,
    )
    steps = config.get("steps")
    if isinstance(steps, list):
        # No sidecar: the steps travel in the decorator beside pivots and charts.
        keywords.append(("steps", steps))
    return _configured_node(node, source_names, decorator="explore", keywords=tuple(keywords))


@_register_codegen(NodeType.EXTERNAL_FILE)
def _gen_external_file(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.DATA_INPUT)
def _gen_data_input(node: GraphNode, source_names: list[str]) -> NodeSource:
    return _config_backed(node, source_names)


@_register_codegen(NodeType.DATA_OUTPUT)
def _gen_data_output(node: GraphNode, source_names: list[str]) -> NodeSource:
    # Persistence happens only through the explicit output-write surface; a
    # standalone run passes the frame through.
    return _config_backed(node, source_names)


@_register_codegen(NodeType.OUTPUT)
def _gen_output(node: GraphNode, source_names: list[str]) -> NodeSource:
    # The decorator assembles the response document from the sidecar mapping,
    # through the same assembler the canvas executor calls.
    return _config_backed(node, source_names)


@_register_codegen(NodeType.EDGE_JOIN)
def _gen_edge_join(node: GraphNode, source_names: list[str]) -> NodeSource:
    if len(source_names) != 2:
        raise ConfigError(
            "edgeJoin codegen requires exactly two incoming sources.",
            node_id=node.id,
            node_label=node.data.label,
            source_names=source_names,
        )
    config = node.data.config
    # Validate at save as the executor does at run time.
    build_edge_join_kwargs(config)
    keywords = tuple(edge_join_config_to_decorator_kwargs(config))
    return NodeSource(
        "edge_join", keywords, _params(source_names), description=node.data.description
    )


def _code_makes_df_local(code: str) -> bool:
    """Whether *code*, as a function body, makes ``df`` the function's own name.

    Read with Python's own scoping (``symtable``): any binding makes ``df``
    local, and a ``global df`` declaration claims it too. Only when neither
    holds could ``return df`` reach a module global.
    """
    wrapped = "def _node():\n" + "\n".join(f"{INDENT}{line}" for line in code.splitlines())
    try:
        table = symtable.symtable(wrapped, "<node>", "exec")
    except SyntaxError:
        return False
    function = table.get_children()[0]
    try:
        symbol = function.lookup("df")
    except KeyError:
        return False
    return symbol.is_local() or symbol.is_declared_global()


def _transform_body(code: str) -> str:
    """A transform's body: the code and ``return df``, first declaring ``df`` if needed.

    The declaration makes ``df`` a function local without binding it, so a
    preamble global named ``df`` cannot stand in for a missing assignment. Code
    that binds ``df`` anywhere already makes it local.
    """
    declaration = "" if _code_makes_df_local(code) else POLARS_OUTPUT_DECLARATION.strip() + "\n"
    return f"{declaration}{code}\nreturn df"


@_register_codegen(NodeType.POLARS)
def _gen_transform(node: GraphNode, source_names: list[str]) -> NodeSource:
    config = node.data.config
    code = str(config.get("code") or "").strip()
    input_mapping = config.get("inputMapping")
    steps = config.get("steps")
    if isinstance(steps, list) and input_mapping is not None:
        raise ConfigError(
            STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE,
            node_id=node.id,
            node_label=node.data.label,
        )
    logical_source_names = (
        resolve_input_mapping_names(source_names, input_mapping)
        if input_mapping is not None
        else source_names
    )
    if isinstance(steps, list):
        # The sidecar owns the steps; the body is their rendering against the
        # generated parameter names, or the incomplete placeholder when they
        # cannot be rendered yet (the save warns which step is incomplete).
        try:
            code = render_polars_steps(steps, logical_source_names, start="input").code
        except PolarsStepError:
            code = ""
    if code and ("df" in source_names or "df" in logical_source_names):
        raise ConfigError(
            "Polars input name 'df' conflicts with the reserved output name; rename the "
            "upstream node or frame.",
            node_id=node.id,
            node_label=node.data.label,
        )
    keywords = _build_extra_kwargs(config, COLUMN_CONFIG_KEYS)
    if input_mapping is not None:
        # ``resolve_input_mapping_names`` validated the persisted value before
        # it reaches source interpolation.
        keywords.append(("inputMapping", input_mapping))
    if isinstance(steps, list):
        sidecar = config_path_for_node(NodeType.POLARS, _func_name(node)).as_posix()
        keywords.append(("config", sidecar))
    # Not written yet: a polars node's output is whatever its code assigns to
    # ``df``, so with no code there is nothing to return — there is no implicit
    # passthrough, whatever the input count. A half-built graph is a normal
    # state to save, so the body is valid Python that keeps the inputs bound
    # and fails loudly if run; save warns about it, and the placeholder
    # round-trips back to "no code" in the editor.
    body = _transform_body(code) if code else _unindented(INCOMPLETE_TRANSFORM_BODY)
    return NodeSource(
        "polars",
        tuple(keywords),
        _params(logical_source_names, FRAME),
        returns=FRAME,
        description=node.data.description,
        body=body,
    )


# ---------------------------------------------------------------------------
# Submodel sentinels — registered so the unified registry is fully populated,
# but they fail loudly if codegen ever dispatches on them.  See module-level
# docstring for the rationale.
# ---------------------------------------------------------------------------


def _gen_submodel_placeholder_unreachable(
    node: GraphNode,
    source_names: list[str],
) -> NodeSource:
    """Should never be reached.

    Submodels are handled at the ``graph_to_code_multi`` level: the
    placeholder node is excluded from ``root_nodes`` and the submodel's
    children are emitted into their own file.  If codegen ever dispatches
    on a ``SUBMODEL`` / ``SUBMODEL_PORT`` node, ``graph_to_code_multi``
    has a bug — fail loudly rather than emitting silent passthrough code.
    """
    raise RuntimeError(
        f"codegen dispatched on a submodel placeholder node "
        f"({node.data.nodeType.value!r}, id={node.id!r}, label={node.data.label!r}). "
        "graph_to_code_multi should have handled the placeholder at the "
        "pipeline-assembly level (see _build_instance_of_map / root_nodes "
        "filter).  This means the submodel boundary wiring is broken; do "
        "not silently emit transform fallback code."
    )


_set_codegen_in_registry(NodeType.SUBMODEL, _gen_submodel_placeholder_unreachable)
_set_codegen_in_registry(NodeType.SUBMODEL_PORT, _gen_submodel_placeholder_unreachable)


# ---------------------------------------------------------------------------
# Module export surface.
# ---------------------------------------------------------------------------


__all__ = [
    # Types
    "CodegenBuilder",
    "CodegenFn",
    "NodeSource",
    "Param",
    # Rendering
    "render_node_source",
    # Helpers
    "_build_extra_kwargs",
    "_params",
    "_sanitize_description",
    # Per-type builders (imported by some tests)
    "_gen_api_input",
    "_gen_banding",
    "_gen_constant",
    "_gen_data_input",
    "_gen_data_output",
    "_gen_edge_join",
    "_gen_external_file",
    "_gen_live_switch",
    "_gen_model_score",
    "_gen_output",
    "_gen_rating_step",
    "_gen_scenario_expander",
    "_gen_transform",
    "_register_codegen",
]
