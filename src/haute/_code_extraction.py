"""Extraction of user code from pipeline function bodies.

Houses the four per-node extractors plus the consolidated engine that
backs them.  Each extractor (``_extract_user_code``,
``_extract_source_user_code``, ``_extract_model_score_user_code``,
``_extract_external_user_code``) differs only in its *boilerplate
matcher* — the logic that decides how much of the function body is
auto-generated boilerplate to be stripped before user code begins.

Design:

* ``BOILERPLATE_MATCHERS`` is a registry mapping a kind string to a
  ``BoilerplateMatcher`` that returns the *start index* of the user
  code within the cleaned body lines, plus the *return variable* whose
  trailing ``return <var>`` should be stripped.
* ``extract_user_code(body_source, *, kind, param_names)`` is the
  single consolidated entrypoint — it does the shared work (docstring
  strip, dedent, trailing-return strip) and dispatches the
  kind-specific logic through the registry.
* The four ``_extract_*_user_code`` functions remain thin wrappers over
  the engine for the parser modules that call them directly.

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
    "_extract_user_code",
    "_extract_source_user_code",
    "_extract_model_score_user_code",
    "_extract_external_user_code",
    "_unwrap_chain_assignment",
    "extract_user_code",
    "BOILERPLATE_MATCHERS",
    "BoilerplateMatcher",
    "MatcherResult",
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


def _unwrap_chain_assignment(
    code: str,
    param_names: list[str] | None = None,
) -> str | None:
    """Unwrap ``df = (\\n...\\n)`` and strip the leading source variable name.

    When a leading identifier matches a known *param_name* it is kept
    (it's part of the user's code, e.g. ``source.filter(...)``).  Only
    codegen-injected variable names (not in param_names) are stripped to
    prevent accumulation on save/reload roundtrips.

    Returns the extracted chain code, or ``None`` if the pattern doesn't match.
    """
    if not (code.startswith("df = (") or code.startswith("df=(")):
        return None
    inner = code.split("(", 1)[1]
    if inner.rstrip().endswith(")"):
        inner = inner.rstrip()[:-1]
    extracted = _dedent(inner).strip()
    # Strip leading source variable name to prevent accumulation on
    # save/reload roundtrips (e.g. "source_name\n.filter()")
    lines = extracted.splitlines()
    if (
        len(lines) > 1
        and lines[1].lstrip().startswith(".")
        and lines[0].strip().isidentifier()
        and lines[0].strip() not in (param_names or [])
    ):
        extracted = "\n".join(lines[1:])
    return extracted


# ---------------------------------------------------------------------------
# Consolidated engine — pluggable boilerplate matchers
# ---------------------------------------------------------------------------


class MatcherResult(NamedTuple):
    """Result of a boilerplate matcher.

    ``start_idx`` is the first index in ``cleaned_lines`` that is
    considered user code (everything before it is boilerplate to skip).
    ``return_var`` is the variable whose trailing ``return <var>`` should
    be stripped from the extracted tail.
    """

    start_idx: int
    return_var: str


# A matcher inspects the *cleaned* (docstring-stripped) body lines plus
# the node's declared parameter names and decides where user code begins.
BoilerplateMatcher = Callable[[list[str], tuple[str, ...]], MatcherResult]


def _match_polars(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """Polars/transform nodes: keep everything — matcher is a no-op.

    Post-processing (strip ``df = <param>`` alias, unwrap chain, convert
    ``return <expr>``) is applied by ``_finalise_polars``.
    """
    return MatcherResult(start_idx=0, return_var="df")


def _match_source(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """DataSource / scenarioExpander nodes: skip leading imports + first statement.

    The first statement is always the auto-generated data-load
    (``df = pl.scan_parquet(...)`` or similar).  It may span multiple
    lines — we walk parentheses to find its end.
    """
    # Skip leading import lines (from/import) codegen may have added
    idx = 0
    while idx < len(cleaned) and cleaned[idx].strip().startswith(("from ", "import ")):
        idx += 1

    if idx >= len(cleaned):
        return MatcherResult(start_idx=idx, return_var="df")

    # Skip the first statement — walk parens to handle multi-line assignments
    first_end = idx
    depth = 0
    for i in range(idx, len(cleaned)):
        depth += cleaned[i].count("(") - cleaned[i].count(")")
        if depth <= 0 and i >= first_end:
            first_end = i + 1
            break
    return MatcherResult(start_idx=first_end, return_var="df")


def _match_model_score(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ModelScore nodes: skip up to and including the ``score_from_config(...)`` call.

    The call may span multiple lines — we walk parentheses to find the
    closing ``)``.  If no score_from_config call is found, return an
    out-of-range start index so the engine yields an empty result.
    """
    for i, line in enumerate(cleaned):
        stripped = line.strip()
        if "score_from_config(" in stripped and not stripped.startswith(("from ", "import ")):
            # Walk forward to balance the opening paren(s) of this call
            depth = line.count("(") - line.count(")")
            j = i
            while depth > 0 and j + 1 < len(cleaned):
                j += 1
                depth += cleaned[j].count("(") - cleaned[j].count(")")
            return MatcherResult(start_idx=j + 1, return_var="result")

    # No call found — treat the whole body as boilerplate (empty user code)
    return MatcherResult(start_idx=len(cleaned) + 1, return_var="result")


def _match_external(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ExternalFile nodes: skip imports, ``with open(...)`` blocks, and ``obj = …`` lines.

    This is the most heuristic of the four — user code starts at the
    first line that is *not* recognised file-loading boilerplate.
    """
    if not cleaned:
        return MatcherResult(start_idx=0, return_var="df")

    # Determine the base indentation from the first non-blank line
    base_indent = 0
    for line in cleaned:
        if line.strip():
            base_indent = len(line) - len(line.lstrip())
            break

    i = 0
    in_with = False
    while i < len(cleaned):
        s = cleaned[i].strip()
        line_indent = len(cleaned[i]) - len(cleaned[i].lstrip()) if s else 0

        if not s:
            i += 1
            continue

        # Inside a with-block: skip indented body lines
        if in_with:
            if line_indent > base_indent:
                i += 1
                continue
            in_with = False
            # fall through to check this line normally

        if s.startswith("import ") or s.startswith("from "):
            i += 1
            continue
        if s.startswith("with open("):
            in_with = True
            i += 1
            continue
        if s.startswith("obj = ") or s.startswith("obj."):
            i += 1
            continue

        break  # first user-code line

    return MatcherResult(start_idx=i, return_var="df")


# Registry of boilerplate matchers, keyed by *kind*.  The kind string
# is the public key callers use to select a matcher; aliases for the
# same matcher are allowed so callers can use either the NodeType style
# ("dataSource") or the Python-snake style ("data_source", "source").
BOILERPLATE_MATCHERS: dict[str, BoilerplateMatcher] = {
    "polars": _match_polars,
    "transform": _match_polars,
    "source": _match_source,
    "dataSource": _match_source,
    "data_source": _match_source,
    "model_score": _match_model_score,
    "modelScore": _match_model_score,
    "external": _match_external,
    "externalFile": _match_external,
    "external_file": _match_external,
}


def _strip_trailing_return(code_lines: list[str], return_var: str) -> list[str]:
    """Strip the codegen-generated trailing ``return <return_var>`` from *code_lines*.

    Uses an AST walk (via :func:`_strip_outer_trailing_return`) to
    identify whether the LAST OUTER-scope statement is literally
    ``return <return_var>``.  A nested helper whose final line happens
    to be ``return <return_var>`` textually is therefore preserved —
    only the outer return is removed.

    Also pops any trailing blank lines in the process.

    Internally we glue the lines back together, delegate to the AST helper,
    and split again.
    """
    if not code_lines:
        return []
    source = "\n".join(code_lines)
    stripped_source = _strip_outer_trailing_return(source, return_var)
    if not stripped_source:
        return []
    return stripped_source.splitlines()


def _finalise_polars(code: str, param_names: tuple[str, ...]) -> str:
    """Apply polars-specific post-processing: strip df=<param>, unwrap chain, convert return.

    Pattern 2 (``return <expr>`` → ``df = <expr>``) is performed via an
    AST walk that only picks up ``Return`` nodes at the OUTERMOST scope.
    Returns inside nested ``def`` / ``class`` / ``lambda`` bodies are
    left untouched.
    """
    # Strip codegen-prepended "df = <param_name>" alias to prevent
    # accumulation on save/reload roundtrips.
    first_line = code.splitlines()[0].strip() if code else ""
    if first_line.startswith("df = ") or first_line.startswith("df="):
        alias_target = first_line.split("=", 1)[1].strip()
        if alias_target in param_names:
            remaining = "\n".join(code.splitlines()[1:]).strip()
            if remaining:
                code = remaining

    # Pattern 1: codegen chain style "df = (\n...\n)" — unwrap to inner
    chain = _unwrap_chain_assignment(code, param_names=list(param_names))
    if chain is not None:
        return chain

    if not code.strip():
        return code

    # Pattern 2: hand-written "return <expr>" at the OUTER scope only.
    rewritten = _rewrite_outer_returns_as_assignment(code, target="df")
    return _dedent(rewritten).strip()


def _finalise_source(code: str, param_names: tuple[str, ...]) -> str:
    """Source-specific post-processing: unwrap chain-assignment pattern if present."""
    chain = _unwrap_chain_assignment(code)
    if chain is not None:
        return chain
    return code


def _finalise_external(code: str, param_names: tuple[str, ...]) -> str:
    """External-specific post-processing: handle the edge case of a lone ``return df``.

    ``_strip_trailing_return`` has already removed any trailing outer
    ``return df``.  In the rare case where the extracted tail consists
    of a lone outer ``return df`` (e.g. the body was just boilerplate
    plus a single return, and the trailing-strip ran against a tail
    that isn't the absolute end of the source) we wipe it here.  The
    check is scoped via the AST helper so that ``return df`` inside a
    nested helper is left alone.
    """
    stripped = code.strip()
    if not stripped:
        return code

    try:
        returns = _outermost_returns(stripped, context="external user code")
    except _UserCodeParseError:
        # Invalid Python — defer the error to the caller; passthrough.
        return code

    # If the only outer-level statement is a lone ``return df``, wipe.
    if len(returns) == 1:
        only = returns[0]
        if (
            isinstance(only.value, ast.Name)
            and only.value.id == "df"
            and stripped == ast.unparse(only)
        ):
            return ""
    return code


# Per-kind finaliser registry.  The finaliser runs after the shared
# "strip docstring → dedent → skip boilerplate → strip trailing return"
# engine pass.  Defaults to no-op for kinds without special handling.
def _finalise_noop(code: str, param_names: tuple[str, ...]) -> str:
    return code


_FINALISERS: dict[str, Callable[[str, tuple[str, ...]], str]] = {
    "polars": _finalise_polars,
    "transform": _finalise_polars,
    "source": _finalise_source,
    "dataSource": _finalise_source,
    "data_source": _finalise_source,
    "model_score": _finalise_noop,
    "modelScore": _finalise_noop,
    "external": _finalise_external,
    "externalFile": _finalise_external,
    "external_file": _finalise_external,
}


def extract_user_code(
    body_source: str,
    *,
    kind: str,
    param_names: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Extract user code from a function body, dispatching via the matcher registry.

    This is the consolidated engine that backs the four per-kind
    ``_extract_*_user_code`` extractors.  It performs the
    shared work (strip docstring → dedent → skip matcher-specific
    boilerplate → strip trailing ``return <var>``) and delegates the
    kind-specific pre- and post-processing to the registry.

    Args:
        body_source: The raw function body (as produced by
            ``_extract_function_bodies``).
        kind: The matcher key — one of the entries in
            ``BOILERPLATE_MATCHERS`` (``"polars"``, ``"source"``,
            ``"model_score"``, ``"external"`` and their aliases).
        param_names: Parameter names of the decorated function, used by
            polars post-processing to distinguish user identifiers from
            codegen-injected ones.

    Returns:
        The extracted user code as a string (empty string if the body
        contains only boilerplate).

    Raises:
        KeyError: If *kind* is not in the registry.
    """
    if kind not in BOILERPLATE_MATCHERS:
        raise KeyError(
            f"Unknown boilerplate matcher kind: {kind!r}. "
            f"Available kinds: {sorted(BOILERPLATE_MATCHERS)!r}"
        )
    matcher = BOILERPLATE_MATCHERS[kind]
    finaliser = _FINALISERS[kind]
    params = tuple(param_names or ())

    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    result = matcher(cleaned, params)
    rest = cleaned[result.start_idx :]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()
    code_lines = _strip_trailing_return(code.splitlines(), result.return_var)
    if not code_lines:
        return ""

    code = "\n".join(code_lines).strip()
    return finaliser(code, params)


# ---------------------------------------------------------------------------
# Per-kind wrappers over the consolidated engine
# ---------------------------------------------------------------------------


def _extract_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract the meaningful user code from a polars/transform function body.

    Strips the docstring and the codegen-appended ``return df``.
    For codegen chain style ``df = (...)`` it unwraps the inner expression.
    For hand-written ``return expr`` it strips the ``return`` keyword.
    For multi-statement bodies (assignments, comments) it returns as-is.
    """
    return extract_user_code(body_source, kind="polars", param_names=tuple(param_names))


def _extract_source_user_code(body_source: str) -> str:
    """Extract user code from a DATA_SOURCE or SCENARIO_EXPANDER body.

    The auto-generated boilerplate is a single assignment line at the
    top (e.g. ``df = pl.scan_parquet("...")``).  Everything after that
    assignment — minus the trailing ``return df`` — is user code.

    User code follows the generated boilerplate directly.
    """
    return extract_user_code(body_source, kind="source")


def _extract_model_score_user_code(body_source: str) -> str:
    """Extract user post-processing code from a MODEL_SCORE function body.

    The auto-generated boilerplate is the ``from pathlib ...`` /
    ``score_from_config(...)`` block.  Everything after the
    ``result = score_from_config(...)`` line (minus ``return result``)
    is user code.

    User code follows the generated scoring block directly.
    """
    return extract_user_code(body_source, kind="model_score")


def _extract_external_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract user code from an externalFile function body.

    Strips the docstring, then scans forward to skip the file-loading
    boilerplate (import statements, with-open blocks, obj assignments /
    method calls).  Everything between the boilerplate and a trailing
    ``return df`` is the user code.
    """
    return extract_user_code(body_source, kind="external", param_names=tuple(param_names))
