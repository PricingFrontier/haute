"""Pure AST / source utilities used by the pipeline parser.

These helpers are stateless, dependency-light functions that operate
directly on Python source text or ``ast`` nodes.  They have no
knowledge of node types, configs, or graphs — that logic lives in
``_code_extraction``, ``_config_builder``, and ``_graph_builders``.
"""

from __future__ import annotations

import ast
from typing import Any

from haute._types import DECORATOR_TO_NODE_TYPE, NodeType
from haute.errors import ParseError

__all__ = [
    "_eval_ast_literal",
    "_get_decorator_kwargs",
    "_is_pipeline_node_decorator",
    "_is_submodel_node_decorator",
    "_get_decorator_node_type",
    "_get_docstring",
    "_strip_docstring",
    "_dedent",
    "_extract_function_bodies",
    "_extract_connect_calls",
    "_extract_meta",
    "_extract_pipeline_meta",
    "_extract_submodel_meta",
    "_extract_preamble",
    "_extract_preserved_blocks",
]


# ---------------------------------------------------------------------------
# AST literal evaluation
# ---------------------------------------------------------------------------


def _describe_expr(node: ast.expr) -> str:
    """Render an expression node back to source text for error messages."""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover — unparse does not fail on valid expr nodes
        return f"<{type(node).__name__}>"


def _eval_ast_literal(node: ast.expr) -> Any:
    """Safely evaluate an AST literal node.

    Accepts everything :func:`ast.literal_eval` accepts, plus the
    sanctioned ``Contract(...)`` constructor spelling (lowered to plain
    dict/tuple data by :func:`_eval_contract_constructor`).

    Anything else raises :class:`~haute.errors.ParseError`.  The parser
    never executes pipeline files, so a non-literal expression cannot be
    resolved here — and it cannot round-trip either: downstream consumers
    treat these values as plain data (node configs that codegen re-emits
    via ``repr``), so "preserving" the source text would silently rewrite
    e.g. ``cols=COLS`` into ``cols='COLS'`` on the next save.  The old
    behavior — returning ``ast.dump(node)`` — was worse still: the AST
    repr string (``Call(func=Name(...))``) leaked into configs and was
    re-emitted as a corrupt decorator.  Loud rejection is the only honest
    option (machine-emitted files only ever carry literals; hand-edits
    are the sole vector and get an actionable error instead of silent
    corruption).
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        contract = _eval_contract_constructor(node)
        if contract is not None:
            return contract
        raise ParseError(
            f"non-literal expression {_describe_expr(node)!r} cannot be evaluated at "
            f"parse time; only literal Python values are supported",
            line=node.lineno,
        ) from None


def _eval_contract_constructor(node: ast.expr) -> dict[str, Any] | tuple[Any, Any] | None:
    """Evaluate ``Contract(...)`` decorator kwargs into literal data.

    ``ast.literal_eval`` deliberately rejects constructor calls.  The
    parser still needs to accept the public ``contract=Contract(...)``
    spelling, so we lower only that narrow constructor shape into the
    same dict/tuple forms accepted by ``Contract.from_user_declared``.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    is_contract = isinstance(func, ast.Name) and func.id == "Contract"
    is_qualified_contract = isinstance(func, ast.Attribute) and func.attr == "Contract"
    if not (is_contract or is_qualified_contract):
        return None
    if len(node.args) == 2 and not node.keywords:
        return (_eval_ast_literal(node.args[0]), _eval_ast_literal(node.args[1]))
    if node.args:
        return None
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        key = kw.arg
        if key is None or key not in {"inputs", "outputs", "inputs_by_parent"}:
            return None
        kwargs[key] = _eval_ast_literal(kw.value)
    return kwargs


# ---------------------------------------------------------------------------
# Decorator inspection
# ---------------------------------------------------------------------------


def _get_decorator_kwargs(decorator: ast.expr) -> dict[str, Any]:
    """Extract keyword arguments from a decorator.

    Handles both @pipeline.<type> and @pipeline.<type>(key=val, ...).

    Raises:
        ParseError: when a kwarg value is not a literal (or the sanctioned
            ``Contract(...)`` form), or when a ``**splat`` is present.
            Neither can be resolved at parse time, and both would be
            silently corrupted or dropped by the next codegen save.
    """
    if isinstance(decorator, ast.Call):
        kwargs: dict[str, Any] = {}
        for kw in decorator.keywords:
            if kw.arg is None:
                raise ParseError(
                    f"decorator kwargs must be literal values: "
                    f"'**{_describe_expr(kw.value)}' cannot be expanded at parse time "
                    f"and would be dropped on the next save",
                    line=kw.value.lineno,
                )
            try:
                kwargs[kw.arg] = _eval_ast_literal(kw.value)
            except ParseError as exc:
                raise ParseError(
                    f"decorator kwargs must be literal values (or Contract(...)): "
                    f"kwarg {kw.arg!r} is the non-literal expression "
                    f"{_describe_expr(kw.value)!r}",
                    kwarg=kw.arg,
                    line=kw.value.lineno,
                ) from exc
        return kwargs
    return {}


def _is_pipeline_node_decorator(decorator: ast.expr) -> bool:
    """Check if a decorator is @pipeline.<type>(...) for any type in DECORATOR_TO_NODE_TYPE."""
    if isinstance(decorator, ast.Attribute):
        if (
            isinstance(decorator.value, ast.Name)
            and decorator.value.id == "pipeline"
            and decorator.attr in DECORATOR_TO_NODE_TYPE
        ):
            return True

    if isinstance(decorator, ast.Call):
        return _is_pipeline_node_decorator(decorator.func)

    return False


def _get_decorator_node_type(decorator: ast.expr) -> NodeType | None:
    """Extract the NodeType from a pipeline decorator's attribute name.

    Returns ``None`` if the decorator is not a recognized pipeline decorator.
    """
    if isinstance(decorator, ast.Attribute):
        if (
            isinstance(decorator.value, ast.Name)
            and decorator.value.id in ("pipeline", "submodel")
            and decorator.attr in DECORATOR_TO_NODE_TYPE
        ):
            return DECORATOR_TO_NODE_TYPE[decorator.attr]
    if isinstance(decorator, ast.Call):
        return _get_decorator_node_type(decorator.func)
    return None


def _is_submodel_node_decorator(decorator: ast.expr) -> bool:
    """Check if a decorator is @submodel.<type>(...) for any type in DECORATOR_TO_NODE_TYPE."""
    if isinstance(decorator, ast.Attribute):
        if isinstance(decorator.value, ast.Name) and decorator.attr in DECORATOR_TO_NODE_TYPE:
            return decorator.value.id == "submodel"
    if isinstance(decorator, ast.Call):
        return _is_submodel_node_decorator(decorator.func)
    return False


# ---------------------------------------------------------------------------
# Docstring / whitespace helpers
# ---------------------------------------------------------------------------


def _get_docstring(func: ast.FunctionDef) -> str:
    """Extract the docstring from a function def."""
    return ast.get_docstring(func) or ""


def _strip_docstring(lines: list[str]) -> list[str]:
    """Remove the leading docstring from function body lines."""
    cleaned: list[str] = []
    in_docstring = False
    docstring_done = False
    opening_quote = '"""'

    for line in lines:
        stripped = line.strip()

        if not docstring_done:
            if in_docstring:
                if opening_quote in stripped:
                    in_docstring = False
                    docstring_done = True
                continue
            if not cleaned and (stripped.startswith('"""') or stripped.startswith("'''")):
                opening_quote = stripped[:3]
                if stripped.count(opening_quote) >= 2 and stripped.endswith(opening_quote):
                    docstring_done = True
                    continue
                else:
                    in_docstring = True
                    continue
            docstring_done = True

        cleaned.append(line)

    return cleaned


def _dedent(code: str) -> str:
    """Remove common leading whitespace."""
    code_lines = code.splitlines()
    if not code_lines:
        return code
    indents = [len(line) - len(line.lstrip()) for line in code_lines if line.strip()]
    if not indents:
        return code
    m = min(indents)
    return "\n".join(line[m:] if len(line) >= m else line for line in code_lines)


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def _extract_function_bodies(
    source: str,
    *,
    tree: ast.Module,
) -> dict[str, str]:
    """Extract raw source of each function body, keyed by function name.

    Args:
        source: The raw source code (needed for line extraction).
        tree: Pre-parsed AST tree.  Required — callers must parse the
            source exactly once and pass the resulting tree.  Making this
            mandatory prevents a class of bug where *source* and *tree*
            are computed from two different snapshots of the file, and
            avoids a silent second ``ast.parse()`` call that masks errors.
    """
    source_lines = source.splitlines()
    bodies: dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            if node.body:
                start = node.body[0].lineno - 1
                end = node.body[-1].end_lineno or (start + 1)
                bodies[node.name] = "\n".join(source_lines[start:end])

    return bodies


def _eval_connect_value(receiver: str, role: str, node: ast.expr) -> Any:
    """Evaluate one ``connect()`` argument, naming the role on failure."""
    try:
        return _eval_ast_literal(node)
    except ParseError as exc:
        raise ParseError(
            f"{receiver}.connect() arguments must be string literals: "
            f"{role} is the non-literal expression {_describe_expr(node)!r}",
            role=role,
            line=node.lineno,
        ) from exc


def _connect_call_edge(
    call: ast.Call,
    receiver: str,
) -> tuple[str, str, str | None, str | None] | None:
    """Extract ``(src, tgt, source_port, target_port)`` from one connect call.

    ``source`` / ``target`` are positional-or-keyword in the runtime
    signature, so both spellings are accepted.  Returns ``None`` for calls
    that carry no derivable edge (missing source/target, or literal values
    that are not strings — both are runtime errors with no edge to record).
    Non-literal values raise :class:`ParseError`: silently skipping them
    would drop the edge from the graph and lose the line on the next save.
    """
    src_expr: ast.expr | None = call.args[0] if len(call.args) >= 1 else None
    tgt_expr: ast.expr | None = call.args[1] if len(call.args) >= 2 else None
    port_exprs: dict[str, ast.expr] = {}
    for kw in call.keywords:
        if kw.arg == "source" and src_expr is None:
            src_expr = kw.value
        elif kw.arg == "target" and tgt_expr is None:
            tgt_expr = kw.value
        elif kw.arg in ("source_port", "target_port"):
            port_exprs[kw.arg] = kw.value

    if src_expr is None or tgt_expr is None:
        return None

    src = _eval_connect_value(receiver, "source", src_expr)
    tgt = _eval_connect_value(receiver, "target", tgt_expr)
    if not (isinstance(src, str) and isinstance(tgt, str)):
        return None

    source_port: str | None = None
    target_port: str | None = None
    for role, expr in port_exprs.items():
        val = _eval_connect_value(receiver, role, expr)
        if isinstance(val, str) and val:
            if role == "source_port":
                source_port = val
            else:
                target_port = val
    return (src, tgt, source_port, target_port)


def _extract_connect_calls(
    tree: ast.Module,
    receiver: str = "pipeline",
) -> list[tuple[str, str, str | None, str | None]]:
    """Find all <receiver>.connect(...) calls at module level.

    Returns ``(src, tgt, source_port, target_port)`` tuples. Port values
    come from ``source_port="..."`` / ``target_port="..."`` keywords when
    present, or ``None`` for the single-port bare two-arg form.

    ``connect()`` returns ``Self`` and is documented as chainable, so
    ``pipeline.connect("a", "b").connect("b", "c")`` contributes both
    edges (in source order).  The chain is walked down to its base
    receiver, which must be a bare ``ast.Name`` matching *receiver* —
    ``module.pipeline.connect(...)`` and ``get_pipeline().connect(...)``
    stay rejected.
    """
    connects: list[tuple[str, str, str | None, str | None]] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue

        # Collect every .connect link in the method chain, walking from
        # the outermost call down to the base receiver.
        links: list[ast.Call] = []
        cur: ast.expr = call
        while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            if cur.func.attr == "connect":
                links.append(cur)
            cur = cur.func.value
        if not links:
            continue
        if not (isinstance(cur, ast.Name) and cur.id == receiver):
            continue

        # links were collected outermost-first; reverse for source order.
        for link in reversed(links):
            edge = _connect_call_edge(link, receiver)
            if edge is not None:
                connects.append(edge)

    return connects


# ---------------------------------------------------------------------------
# Meta extraction
# ---------------------------------------------------------------------------


def _eval_meta_value(var_name: str, field: str, node: ast.expr) -> Any:
    """Evaluate one metadata argument, naming the field on failure.

    A non-literal here is unrecoverable: the old ``ast.dump`` fallback
    stored the AST repr as the pipeline name, and a silent skip would
    rewrite the construction line to the default name on the next save.
    """
    try:
        return _eval_ast_literal(node)
    except ParseError as exc:
        raise ParseError(
            f"{var_name} {field} must be a string literal: got the non-literal "
            f"expression {_describe_expr(node)!r}",
            field=field,
            line=node.lineno,
        ) from exc


def _extract_meta(
    tree: ast.Module,
    var_name: str,
    default_name: str = "main",
) -> tuple[str, str]:
    """Find ``<var_name> = haute.<Class>("name", description="...")`` at module level."""
    name = default_name
    description = ""

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != var_name:
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue

        if call.args:
            val = _eval_meta_value(var_name, "name", call.args[0])
            if isinstance(val, str):
                name = val

        for kw in call.keywords:
            if kw.arg == "description":
                val = _eval_meta_value(var_name, "description", kw.value)
                if isinstance(val, str):
                    description = val

        break

    return name, description


def _extract_pipeline_meta(tree: ast.Module) -> tuple[str, str]:
    """Find pipeline = haute.Pipeline("name", description="...") at module level."""
    return _extract_meta(tree, "pipeline", "main")


def _extract_submodel_meta(tree: ast.Module) -> tuple[str, str]:
    """Find submodel = haute.Submodel("name", description="...") at module level."""
    return _extract_meta(tree, "submodel", "unnamed")


# ---------------------------------------------------------------------------
# Preamble extraction
# ---------------------------------------------------------------------------


_STANDARD_IMPORTS = {"import polars as pl", "import haute"}


def _extract_preamble(source: str) -> str:
    """Extract user-defined preamble between standard imports and pipeline code.

    The preamble is any code that appears after the standard imports
    (``import polars as pl``, ``import haute``) but before the first
    ``@pipeline.<type>`` decorator or ``pipeline = haute.Pipeline(...)`` line.
    """
    lines = source.splitlines()
    # Find the end of standard imports region
    last_standard_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in _STANDARD_IMPORTS:
            last_standard_idx = i

    if last_standard_idx == -1:
        return ""

    # Find the start of pipeline code (pipeline = ... or @pipeline.<type>)
    pipeline_start_idx = len(lines)
    for i in range(last_standard_idx + 1, len(lines)):
        stripped = lines[i].strip()
        starts_pipeline = stripped.startswith("pipeline =") or stripped.startswith("pipeline=")
        is_pipeline_def = starts_pipeline and (
            "haute.Pipeline" in stripped or "= haute.Pipeline" in stripped
        )
        if is_pipeline_def:
            pipeline_start_idx = i
            break
        if stripped.startswith("@pipeline."):
            # Check if the decorator name after @pipeline. is a known type
            dot_rest = stripped[len("@pipeline.") :]
            dec_name = dot_rest.split("(")[0].split()[0] if dot_rest else ""
            if dec_name in DECORATOR_TO_NODE_TYPE:
                pipeline_start_idx = i
                break

    # Extract lines between standard imports and pipeline code
    preamble_lines = lines[last_standard_idx + 1 : pipeline_start_idx]

    # Strip leading/trailing blank lines
    while preamble_lines and not preamble_lines[0].strip():
        preamble_lines.pop(0)
    while preamble_lines and not preamble_lines[-1].strip():
        preamble_lines.pop()

    return "\n".join(preamble_lines)


# ---------------------------------------------------------------------------
# Preserved block extraction
# ---------------------------------------------------------------------------


_PRESERVE_START = "# haute:preserve-start"
_PRESERVE_END = "# haute:preserve-end"


def _extract_preserved_blocks(source: str) -> list[str]:
    """Extract code between ``# haute:preserve-start`` / ``# haute:preserve-end`` markers.

    Returns a list of strings, one per matched block, with the marker
    lines themselves stripped.  Blocks are returned in source order.
    Unmatched start markers (no corresponding end) are silently ignored.
    """
    blocks: list[str] = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == _PRESERVE_START:
            # Collect lines until the matching end marker
            block_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != _PRESERVE_END:
                block_lines.append(lines[i])
                i += 1
            if i < len(lines):
                # Found the end marker — store the block
                # Strip leading/trailing blank lines but keep internal structure
                while block_lines and not block_lines[0].strip():
                    block_lines.pop(0)
                while block_lines and not block_lines[-1].strip():
                    block_lines.pop()
                blocks.append("\n".join(block_lines))
            # else: unmatched start marker — skip
        i += 1
    return blocks
