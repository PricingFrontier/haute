"""Extraction of user code from pipeline function bodies.

A node function's body is the user's code plus a few generated statements:
the docstring, the closing ``return df``, a transform's output declaration
(``df: pl.LazyFrame``), an External File hook's ``df = <first input>``
binding, and the incomplete placeholder. :func:`extract_user_code` removes
exactly those and returns what the user wrote. A declaration's body (nothing
but ``...`` or ``pass``) holds no code at all.

Three kinds share one engine: ``polars`` (transforms and instances), ``hook``
(a configured node's ``df`` hook) and ``external`` (an External File hook).

Return-boundary detection:

* The trailing-return strip and the ``return <expr>`` → ``df = <expr>``
  rewrite both need to know which ``return`` statements belong to the
  OUTER scope (the node body itself) and which belong to nested
  ``def`` / ``async def`` / ``class`` / ``lambda`` constructs that the
  user wrote inside the body.  A line-based heuristic cannot tell the
  difference — it picks up any line whose ``.strip()`` starts with
  ``return``, silently corrupting nested helpers.  The ``_outermost_returns``
  helper below walks the AST and returns only the ``ast.Return`` nodes
  at the module-top scope, skipping nested function / class / lambda
  bodies.  Comments, whitespace, string literals containing ``return``
  and multi-line ``return (...)`` all fall out for free.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import NamedTuple

from haute._ast_helpers import _dedent, _strip_docstring
from haute.errors import ParseError

__all__ = [
    "BOILERPLATE_MATCHERS",
    "INCOMPLETE_STEPS_BODY",
    "INCOMPLETE_STEPS_MESSAGE",
    "INCOMPLETE_TRANSFORM_BODY",
    "INCOMPLETE_TRANSFORM_MESSAGE",
    "POLARS_OUTPUT_DECLARATION",
    "BoilerplateMatcher",
    "MatcherResult",
    "_unwrap_chain_assignment",
    "extract_user_code",
    "is_declaration_body",
    "normalise_user_code",
]


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


# Nodes that open a *new lexical scope* and whose ``Return`` children
# therefore belong to that inner scope, not to the enclosing one.  A
# ``Lambda`` has no ``ast.Return`` at all (its body is an expression),
# but we still recurse-block it so a ``return`` appearing textually
# within the lambda's source span cannot leak into the outer walk.
_NESTED_SCOPE_NODES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


class _UserCodeParseError(ParseError, ValueError):
    """Raised when user code cannot be parsed as a Python module.

    The extractor's callers catch this and surface a clear diagnostic
    back to the end user; the original ``SyntaxError`` is chained via
    ``__cause__`` so the underlying location information survives.

    Multiple inheritance with :class:`ParseError` (via ``HauteError``)
    puts the error into Haute's canonical hierarchy so GUI callers
    that catch ``ParseError`` see this too; ``ValueError`` is kept in
    the bases because the parser treats syntax failures as value-level
    extraction errors.
    """


def _parse_user_code(source: str, *, context: str = "user code") -> ast.Module:
    """Parse *source* as an ``ast.Module`` with a friendly error message.

    The previous line-based heuristics silently mis-parsed invalid code;
    the AST path fails loudly.  That's the intended behaviour (see
    CLAUDE.md — "let code fail loudly") but we wrap ``SyntaxError`` in
    a ``ValueError`` subclass that includes the context (which extractor
    was running) so the diagnostic is actionable.
    """
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise _UserCodeParseError(
            f"cannot parse {context}: {exc.msg} (line {exc.lineno}, offset {exc.offset})"
        ) from exc


def _outermost_returns(source: str, *, context: str = "user code") -> list[ast.Return]:
    """Return every ``ast.Return`` node that belongs to the TOP-LEVEL scope.

    *source* is parsed as a module; the walk descends through control
    flow (``if`` / ``for`` / ``while`` / ``try`` / ``with``) but stops
    at any node that introduces a new lexical scope
    (:data:`_NESTED_SCOPE_NODES`).  Returns inside nested ``def`` /
    ``async def`` / ``class`` / ``lambda`` constructs are therefore
    EXCLUDED from the result.

    The returned list is in source order (ascending line / column).

    Raises:
        _UserCodeParseError: If *source* is not valid Python.
    """
    tree = _parse_user_code(source, context=context)
    returns: list[ast.Return] = []

    def _walk(node: ast.AST) -> None:
        if isinstance(node, ast.Return):
            returns.append(node)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPE_NODES):
                continue
            _walk(child)

    _walk(tree)
    returns.sort(key=lambda r: (r.lineno, r.col_offset))
    return returns


def _rewrite_outer_returns_as_assignment(source: str, target: str) -> str:
    """Rewrite every OUTER-scope ``return <expr>`` in *source* to ``<target> = <expr>``.

    Preserves original whitespace, comments, quoting style and line
    endings — we edit the source TEXTUALLY at the line/column positions
    reported by the AST rather than re-emitting through ``ast.unparse``
    (which would normalise everything).

    A ``Return`` with no value (bare ``return``) is rewritten to an
    assignment to ``None`` — this keeps the extracted body syntactically
    valid in the (unusual) case a user wrote a bare ``return`` at the
    outer scope of a polars-style node.
    """
    returns = _outermost_returns(source, context="user code")
    if not returns:
        return source

    lines = source.splitlines(keepends=True)
    # Rewrite from LAST to FIRST so earlier rewrites never invalidate
    # a later return's line index.
    for node in sorted(returns, key=lambda r: (r.lineno, r.col_offset), reverse=True):
        line_idx = node.lineno - 1
        line = lines[line_idx]
        col = node.col_offset
        before = line[:col]
        rest = line[col:]
        if rest.startswith("return "):
            new_rest = f"{target} = " + rest[len("return ") :]
        elif rest.startswith("return\n") or rest.rstrip() == "return":
            # Bare ``return`` — replace with ``<target> = None`` so the
            # extracted snippet remains syntactically well-formed.
            new_rest = f"{target} = None" + rest[len("return") :]
        else:  # pragma: no cover — defensive; ast placed the node here
            raise AssertionError(f"expected 'return' at line {node.lineno} col {col}, got {line!r}")
        lines[line_idx] = before + new_rest

    return "".join(lines)


def _strip_outer_trailing_return(source: str, return_var: str) -> str:
    """Strip a trailing ``return <return_var>`` at the OUTERMOST scope.

    Returns *source* with the codegen-generated trailing return removed
    if the LAST top-level statement is literally ``return <return_var>``.
    Nested returns (inside ``def`` / ``class`` / ``lambda`` bodies) are
    invisible to this check.

    Also strips trailing blank lines from the result, matching the previous
    line-based implementation.
    """
    if not source.strip():
        return source

    returns = _outermost_returns(source, context="user code")
    if not returns:
        # Nothing to strip — just trim trailing blanks.
        return _rstrip_blank_lines(source)

    last = returns[-1]
    value = last.value
    is_sentinel_return = isinstance(value, ast.Name) and value.id == return_var
    if not is_sentinel_return:
        return _rstrip_blank_lines(source)

    # Confirm this Return is truly TRAILING — i.e. nothing non-blank
    # follows it at any scope in the source.  A Return is typically
    # single-line; use ``end_lineno`` to locate the last source line it
    # occupies.
    end_line = last.end_lineno or last.lineno
    lines = source.splitlines(keepends=True)
    # Check no non-blank source after end_line
    for line in lines[end_line:]:
        if line.strip():
            # Something follows the return — don't strip
            # (shouldn't normally happen since Python requires trailing
            # returns to be LAST, but defensive for comments etc.).
            return _rstrip_blank_lines(source)

    # Drop the return's source span (lineno..end_lineno inclusive, 1-based).
    kept = lines[: last.lineno - 1]
    return _rstrip_blank_lines("".join(kept))


def _rstrip_blank_lines(source: str) -> str:
    """Remove trailing whitespace and blank lines from *source*.

    Matches the behaviour of the previous ``while stripped[-1].strip() == "":
    stripped.pop()`` loop — any tail composed solely of whitespace
    characters (spaces, tabs, newlines) is removed.
    """
    return source.rstrip()


def _strip_redundant_rhs_wrapper_once(code: str) -> str | None:
    """Remove ONE provably-redundant paren pair wrapping the whole RHS.

    *code* must be a single ``df = (...)`` assignment statement.  The
    proof is AST-based: drop the first ``(`` together with the LAST
    ``)`` and require the result to parse to the IDENTICAL AST.  Only a
    matched pair spanning the entire RHS can survive that check —
    parens that are part of a sub-expression (``df = (a + b) * c``),
    unbalanced splits (``df = (x.filter(...)).join(...)``), parens
    inside string literals, and load-bearing parens (multi-line
    continuation, generator expressions, walrus, tuples) all fail it.

    Returns the reduced statement, or ``None`` when redundancy cannot
    be proved.
    """
    if not (code.startswith("df = (") or code.startswith("df=(")):
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return None
    stmt = tree.body[0]
    if len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if not isinstance(target, ast.Name) or target.id != "df":
        return None

    open_idx = code.index("(")
    close_idx = code.rindex(")") if ")" in code else -1
    if close_idx <= open_idx:
        return None
    candidate = code[:open_idx] + code[open_idx + 1 : close_idx] + code[close_idx + 1 :]
    try:
        candidate_tree = ast.parse(candidate)
    except SyntaxError:
        return None
    if ast.dump(candidate_tree) != ast.dump(tree):
        return None
    return candidate.rstrip()


def _unwrap_chain_assignment(code: str) -> str | None:
    """Strip provably-redundant parens wrapping the entire RHS of ``df = (...)``.

    * Output is always STATEMENT form (``df = ...``), never a bare
      expression, so re-emitting through codegen is a fixpoint.
    * A wrapping paren pair is removed only when
      :func:`_strip_redundant_rhs_wrapper_once` PROVES it redundant via
      AST identity; nested redundant wrappers are reduced iteratively.
    * Anything unprovable — including code that does not parse — stays
      verbatim: the caller receives ``None`` and keeps the code
      unchanged.  (Unparseable *bodies* still fail loudly: the
      extraction engine parses them before any finaliser runs and
      raises :class:`_UserCodeParseError`.)

    Returns the normalised statement, or ``None`` when nothing provable
    was removed.
    """
    current = code
    unwrapped = False
    while True:
        reduced = _strip_redundant_rhs_wrapper_once(current)
        if reduced is None:
            break
        current = reduced
        unwrapped = True
    return current if unwrapped else None


def _df_alias_target(line: str) -> str | None:
    """Return the source name for a single-line ``df = source`` alias."""
    try:
        tree = ast.parse(line)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return None
    stmt = tree.body[0]
    if len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if not isinstance(target, ast.Name) or target.id != "df":
        return None
    if not isinstance(stmt.value, ast.Name):
        return None
    return stmt.value.id


def _is_empty_chain_assignment(code: str) -> bool:
    """Return whether *code* is a degenerate empty chain ``df = (\\n)``.

    An empty wrapper pair parses to ``df = ()`` (an empty tuple), which is
    not a runnable polars chain — it is leftover scaffolding from a cleared
    code box.  :func:`_unwrap_chain_assignment` deliberately leaves it
    verbatim (round-trip safety: stripping the parens would be invalid),
    so the GUI-facing finaliser collapses it to empty user code here
    instead.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
        return False
    stmt = tree.body[0]
    if len(stmt.targets) != 1:
        return False
    target = stmt.targets[0]
    if not isinstance(target, ast.Name) or target.id != "df":
        return False
    return isinstance(stmt.value, ast.Tuple) and not stmt.value.elts


# ---------------------------------------------------------------------------
# Generated statements
# ---------------------------------------------------------------------------


# Body emitted for a transform the user has not written yet — one with no code
# and either no upstream at all, or several (where "pass the input through" has
# no defined meaning). Saving such a node still has to produce SOME valid body;
# this one fails loudly if the pipeline is ever run.
#
# The message is deliberately CONSTANT — naming no node or source — so the
# placeholder is recognisable on the way back in. Interpolating the label would
# leave nothing fixed to match on, and matching loosely (say, any leading
# ``raise NotImplementedError``) would silently swallow a user's own first line
# when the pipeline was reloaded. The failing node is already identified by the
# function name in the traceback. The literal is double-quoted, as ruff writes it.
INCOMPLETE_TRANSFORM_MESSAGE = (
    "This transform has no code yet. Add code that defines what it returns."
)
INCOMPLETE_TRANSFORM_BODY = (
    f'    raise NotImplementedError(\n        "{INCOMPLETE_TRANSFORM_MESSAGE}",\n    )\n'
)
# The same placeholder for a stepped surface whose steps cannot be rendered
# (a Data Input's post-load steps). Also constant, for the same reason; the
# recogniser accepts either message.
INCOMPLETE_STEPS_MESSAGE = (
    "This node's steps are incomplete. Complete or remove them before running."
)
INCOMPLETE_STEPS_BODY = (
    f'    raise NotImplementedError(\n        "{INCOMPLETE_STEPS_MESSAGE}",\n    )\n'
)
_PLACEHOLDER_MESSAGES = frozenset({INCOMPLETE_TRANSFORM_MESSAGE, INCOMPLETE_STEPS_MESSAGE})
POLARS_OUTPUT_DECLARATION = "    df: pl.LazyFrame\n"


def _is_incomplete_transform_placeholder(source: str) -> bool:
    """Return whether *source* is exactly the generated placeholder statement.

    Compares the PARSED STATEMENT, not its source text. Emitted source is not
    what lands on disk — the saved file may be normalised by the user's
    formatter, so a textual comparison would silently stop matching and the
    placeholder would come back as the user's own code, writing a ``raise``
    into a node they left empty. Structural comparison is immune to quote
    style, line wrapping and trailing commas.

    The message must match exactly, so a hand-written ``raise
    NotImplementedError("my own todo")`` is still the user's code.
    """
    try:
        module = ast.parse(source)
    except SyntaxError:
        return False
    if len(module.body) != 1:
        return False
    statement = module.body[0]
    if not isinstance(statement, ast.Raise) or statement.cause is not None:
        return False
    call = statement.exc
    if not isinstance(call, ast.Call) or call.keywords:
        return False
    if not isinstance(call.func, ast.Name) or call.func.id != "NotImplementedError":
        return False
    if len(call.args) != 1:
        return False
    argument = call.args[0]
    return isinstance(argument, ast.Constant) and argument.value in _PLACEHOLDER_MESSAGES


def _is_polars_output_declaration(source: str) -> bool:
    """Recognise the generated unbound local ``df: pl.LazyFrame`` declaration."""

    try:
        module = ast.parse(source)
    except SyntaxError:
        return False
    if len(module.body) != 1 or not isinstance(module.body[0], ast.AnnAssign):
        return False
    statement = module.body[0]
    annotation = statement.annotation
    return (
        isinstance(statement.target, ast.Name)
        and statement.target.id == "df"
        and statement.value is None
        and isinstance(annotation, ast.Attribute)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "pl"
        and annotation.attr == "LazyFrame"
    )


def is_declaration_body(body_source: str) -> bool:
    """Whether a function body is a declaration: nothing but ``...`` or ``pass``.

    An optional docstring may precede it; a docstring alone is a declaration
    too. The comparison is structural, so a hand-formatted declaration counts.
    """
    lines = body_source.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    cleaned = _strip_docstring(lines)
    if not "".join(cleaned).strip():
        return True
    try:
        module = ast.parse(_dedent("\n".join(cleaned)))
    except SyntaxError:
        return False
    if len(module.body) != 1:
        return False
    statement = module.body[0]
    return isinstance(statement, ast.Pass) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


# ---------------------------------------------------------------------------
# Consolidated engine — pluggable matchers
# ---------------------------------------------------------------------------


class MatcherResult(NamedTuple):
    """Where the user code starts in a cleaned body.

    ``start_idx`` is the first index in ``cleaned_lines`` that is user
    code (everything before it is generated). ``return_vars`` are
    variables whose trailing ``return <var>`` is generated.
    """

    start_idx: int
    return_vars: tuple[str, ...]


# A matcher inspects the *cleaned* (docstring-stripped) body lines plus
# the node's declared parameter names and decides where user code begins.
BoilerplateMatcher = Callable[[list[str], tuple[str, ...]], MatcherResult]


def _statement_end_index(lines: list[str], start_idx: int) -> int:
    """Return the index just after the statement starting at ``start_idx``."""
    depth = 0
    for idx in range(start_idx, len(lines)):
        depth += lines[idx].count("(") - lines[idx].count(")")
        if depth <= 0 and idx >= start_idx:
            return idx + 1
    return len(lines)


def _match_polars(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """Transforms: skip only the generated unbound output declaration.

    Recognised structurally, so a user's own different annotation stays
    user code. A ``df = <param>`` line is authored code, never generated.
    """
    start_idx = 0
    if cleaned:
        end_idx = _statement_end_index(cleaned, start_idx)
        statement = _dedent("\n".join(cleaned[start_idx:end_idx])).strip()
        if _is_polars_output_declaration(statement):
            start_idx = end_idx
    return MatcherResult(start_idx=start_idx, return_vars=("df",))


def _match_hook(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """A configured node's ``df`` hook: everything but the closing ``return df`` is code."""
    return MatcherResult(start_idx=0, return_vars=("df",))


def _match_external(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """An External File hook: skip its generated ``df = <first input>`` binding.

    The binding is generated only as the first statement and only for the
    first positional parameter; a later alias, or an import the user wrote
    first, is authored code.
    """
    first_input = param_names[0] if param_names else None
    if cleaned and first_input and _df_alias_target(cleaned[0].strip()) == first_input:
        return MatcherResult(start_idx=1, return_vars=("df",))
    return MatcherResult(start_idx=0, return_vars=("df",))


def _skip_incomplete_placeholder(cleaned: list[str], start_idx: int) -> int | None:
    """Return the index after a generated placeholder statement at *start_idx*, or None.

    Every generated body puts the placeholder exactly where the user code
    would go, so it is checked once here for every matcher kind.
    """
    while start_idx < len(cleaned) and not cleaned[start_idx].strip():
        start_idx += 1
    if start_idx >= len(cleaned):
        return None
    end_idx = _statement_end_index(cleaned, start_idx)
    statement = _dedent("\n".join(cleaned[start_idx:end_idx])).strip()
    return end_idx if _is_incomplete_transform_placeholder(statement) else None


#: Matchers, keyed by the extraction kind.
BOILERPLATE_MATCHERS: dict[str, BoilerplateMatcher] = {
    "polars": _match_polars,
    "hook": _match_hook,
    "external": _match_external,
}


def _strip_trailing_return(code_lines: list[str], return_vars: tuple[str, ...]) -> list[str]:
    """Strip the codegen-generated trailing ``return <return_var>`` from *code_lines*.

    Uses an AST walk (via :func:`_strip_outer_trailing_return`) to
    identify whether the LAST OUTER-scope statement is literally
    ``return <return_var>``.  A nested helper whose final line happens
    to be ``return <return_var>`` textually is therefore preserved —
    only the outer return is removed.

    Also pops any trailing blank lines in the process.
    """
    if not code_lines:
        return []
    source = "\n".join(code_lines)
    stripped_source = source
    for return_var in return_vars:
        next_source = _strip_outer_trailing_return(stripped_source, return_var)
        if next_source != stripped_source:
            stripped_source = next_source
            break
    if not stripped_source:
        return []
    return stripped_source.splitlines()


def _finalise_polars(code: str) -> str:
    """Apply polars-specific post-processing: unwrap chain, convert return.

    A leading ``df = <param>`` line is authored code, never scaffold.

    Pattern 2 (``return <expr>`` → ``df = <expr>``) is performed via an
    AST walk that only picks up ``Return`` nodes at the OUTERMOST scope.
    Returns inside nested ``def`` / ``class`` / ``lambda`` bodies are
    left untouched.
    """
    code = code.strip()
    if not code:
        return ""

    # A degenerate empty chain "df = (\n)" (parsed as the empty tuple
    # "df = ()") is cleared-code-box scaffolding, not a runnable chain;
    # collapse it to empty user code.
    if _is_empty_chain_assignment(code):
        return ""

    # Pattern 1: redundant wrapper parens "df = (<expr>)" — normalise to
    # "df = <expr>" when provably safe; otherwise the code stays verbatim.
    chain = _unwrap_chain_assignment(code)
    if chain is not None:
        return chain

    # Pattern 2: hand-written "return <expr>" at the OUTER scope only.
    rewritten = _rewrite_outer_returns_as_assignment(code, target="df")
    return _dedent(rewritten).strip()


def extract_user_code(
    body_source: str,
    *,
    kind: str,
    param_names: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Extract the user code from a function body.

    Strips the docstring, recognises a declaration body, skips the kind's
    generated leading statement and an incomplete placeholder, strips the
    trailing ``return df``, and finalises with the Polars rules.

    Args:
        body_source: The raw function body.
        kind: ``"polars"``, ``"hook"`` or ``"external"``.
        param_names: The function's parameter names; the ``external`` kind
            reads its first input from them.

    Returns:
        The user code (empty for a declaration or a generated-only body).

    Raises:
        KeyError: If *kind* is not in the registry.
    """
    if kind not in BOILERPLATE_MATCHERS:
        raise KeyError(
            f"Unknown boilerplate matcher kind: {kind!r}. "
            f"Available kinds: {sorted(BOILERPLATE_MATCHERS)!r}"
        )
    params = tuple(param_names or ())
    if is_declaration_body(body_source):
        return ""

    lines = body_source.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    result = BOILERPLATE_MATCHERS[kind](cleaned, params)
    start_idx = result.start_idx
    after_placeholder = _skip_incomplete_placeholder(cleaned, start_idx)
    if after_placeholder is not None:
        start_idx = after_placeholder
    rest = cleaned[start_idx:]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()
    code_lines = _strip_trailing_return(code.splitlines(), result.return_vars)
    if not code_lines:
        return ""
    return _finalise_polars("\n".join(code_lines).strip())


def normalise_user_code(code: str, *, kind: str) -> str:
    """Pass *code* through the post-processing extraction ends with.

    A rendering is not always a fixpoint of extraction (the finaliser may
    drop provably redundant brackets), so a caller comparing a rendering
    with code extracted from a body normalises the rendering the same way
    first. Only the finaliser applies: the other stages act on generated
    statements a rendering never contains.
    """
    if kind not in BOILERPLATE_MATCHERS:
        raise KeyError(
            f"Unknown boilerplate matcher kind: {kind!r}. "
            f"Available kinds: {sorted(BOILERPLATE_MATCHERS)!r}"
        )
    return _finalise_polars(code.strip())
