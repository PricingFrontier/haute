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
    "_extract_scenario_expander_user_code",
    "_extract_model_score_user_code",
    "_extract_rating_step_user_code",
    "_extract_external_user_code",
    "_unwrap_chain_assignment",
    "_strip_generated_boilerplate_from_code",
    "_strip_source_load_boilerplate_from_code",
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

    Historically this helper rewrote legacy chain-wrapped bodies
    (``df = (\\n <upstream>\\n .filter(...)\\n)``) into bare expression
    chains for the GUI code box.  That contract was retired for two
    reasons:

    * The textual split it used (first ``(`` / trailing ``)``) corrupted
      any statement whose parens were NOT one wrapping pair —
      ``df = (a + b) * c`` became the invalid ``a + b) * c`` and one
      save/load cycle made the file unrunnable (CODE_REVIEW.md C5).
    * Expression-form output is itself round-trip-unsafe: the save path
      (``_codegen_builders._wrap_user_code``) re-emits code boxes
      verbatim as statement bodies, so a bare multi-line chain re-emits
      as invalid Python and a single-line expression re-emits as a dead
      expression statement that silently turns the node into a
      passthrough.

    The replacement contract — round-trip safety beats cosmetic
    unwrapping:

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


def _code_has_comment_line(source: str) -> bool:
    """Return whether *source* contains a standalone comment line."""
    return any(line.lstrip().startswith("#") for line in source.splitlines())


def _is_exact_passthrough_return(code: str, param_names: tuple[str, ...]) -> bool:
    """Return whether *code* is only ``return <upstream_param>`` scaffolding."""
    if not param_names or _code_has_comment_line(code):
        return False
    tree = _parse_user_code(code, context="passthrough return")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Return):
        return False
    value = tree.body[0].value
    return isinstance(value, ast.Name) and value.id in param_names


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


def _strip_generated_passthrough_from_code(
    code: str,
    param_names: tuple[str, ...] | list[str],
) -> str:
    """Strip generated passthrough aliases/returns from UI-facing code."""
    stripped = code.strip()
    params = tuple(param_names or ())
    if not stripped:
        return ""

    if _is_exact_passthrough_return(stripped, params):
        return ""

    lines = stripped.splitlines()
    first_alias = _df_alias_target(lines[0]) if lines else None
    if first_alias not in params:
        return stripped

    tail = "\n".join(lines[1:]).strip()
    if not tail:
        return ""
    return _strip_outer_trailing_return(tail, "df").strip()


# ---------------------------------------------------------------------------
# Consolidated engine — pluggable boilerplate matchers
# ---------------------------------------------------------------------------


def _strip_source_load_boilerplate_from_code(code: str) -> str:
    """Strip generated data-source load statements from transform code.

    Data-source ``code`` is user-authored post-import transformation code. The
    file/table load itself comes from declarative config and must not round-trip
    into the UI code box, even if an older generated file contains duplicated
    ``df = pl.scan_parquet(...)`` lines.
    """
    stripped = code.strip()
    if not stripped:
        return ""

    lines = stripped.splitlines()
    end_idx = _source_load_boilerplate_end_index(lines)
    has_source_load = any(_is_source_load_statement_start(line) for line in lines[:end_idx])
    if not has_source_load:
        return stripped
    code = "\n".join(lines[end_idx:]).strip()
    code_lines = _strip_trailing_return(code.splitlines(), ("df",))
    return "\n".join(code_lines).strip()


class MatcherResult(NamedTuple):
    """Result of a boilerplate matcher.

    ``start_idx`` is the first index in ``cleaned_lines`` that is
    considered user code (everything before it is boilerplate to skip).
    ``return_vars`` are variables whose trailing ``return <var>`` should
    be stripped from the extracted tail.
    ``generated_scaffold`` marks bodies where we stripped an explicit generated
    setup block, so later references to the original input parameter stay
    intentional user code.
    """

    start_idx: int
    return_vars: tuple[str, ...]
    generated_scaffold: bool = False


# A matcher inspects the *cleaned* (docstring-stripped) body lines plus
# the node's declared parameter names and decides where user code begins.
BoilerplateMatcher = Callable[[list[str], tuple[str, ...]], MatcherResult]


def _match_polars(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """Polars/transform nodes: keep everything — matcher is a no-op.

    Post-processing (strip ``df = <param>`` alias, unwrap chain, convert
    ``return <expr>``) is applied by ``_finalise_polars``.
    """
    return MatcherResult(start_idx=0, return_vars=("df",))


_SOURCE_LOAD_PREFIXES: tuple[str, ...] = (
    "df=pl.scan_parquet(",
    "df=pl.scan_csv(",
    "df=pl.scan_ndjson(",
    "df=pl.read_json(",
    "df=read_source(",
    "df=read_data_source(",
    "df=resolve_data_input_from_config(",
    "df=resolve_api_input_from_config(",
    "returnpl.scan_parquet(",
    "returnpl.scan_csv(",
    "returnpl.scan_ndjson(",
    "returnpl.read_json(",
    "returnread_source(",
    "returnread_data_source(",
    "returnresolve_data_input_from_config(",
    "returnresolve_api_input_from_config(",
)


def _is_source_load_statement_start(line: str) -> bool:
    """Return whether *line* starts generated source-load boilerplate."""
    compact = line.strip().replace(" ", "")
    return compact.startswith(_SOURCE_LOAD_PREFIXES)


def _statement_end_index(lines: list[str], start_idx: int) -> int:
    """Return the index just after the statement starting at ``start_idx``."""
    depth = 0
    for idx in range(start_idx, len(lines)):
        depth += lines[idx].count("(") - lines[idx].count(")")
        if depth <= 0 and idx >= start_idx:
            return idx + 1
    return len(lines)


def _source_load_boilerplate_end_index(cleaned: list[str]) -> int:
    """Return the first line index after generated source-load boilerplate."""
    idx = 0
    while idx < len(cleaned):
        stripped = cleaned[idx].strip()
        if stripped.startswith(("from ", "import ")):
            idx += 1
            continue
        if _is_source_load_statement_start(stripped):
            idx = _statement_end_index(cleaned, idx)
            continue
        break
    # When no generated source-load statement was seen, the body carries no
    # source-load boilerplate, so ``idx`` already points at the first user
    # statement. Advancing past it here silently dropped the user's first
    # statement — return ``idx`` so all authored code is preserved.
    return idx


def _match_source(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """Data Input nodes: skip generated source-load blocks.

    The code editor for these nodes contains only optional transforms that run
    after ``df`` has already been loaded. Codegen emits import/load scaffolding
    in the Python function body so saved files execute.
    """
    return MatcherResult(
        start_idx=_source_load_boilerplate_end_index(cleaned),
        return_vars=("df",),
    )


def _match_scenario_expander(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ScenarioExpander nodes: skip generated expansion scaffold or legacy alias.

    Current codegen emits the shared-helper scaffold (imports + ``df =
    expand_scenarios_from_config(...)``) exactly like ratingStep; older files
    carried only a bare ``df = <param>`` / ``return <param>`` alias.  Both are
    generated boilerplate to skip before the user's post-expansion code.
    """
    if not cleaned:
        return MatcherResult(start_idx=0, return_vars=("df",))

    for i, line in enumerate(cleaned):
        stripped = line.strip()
        if "expand_scenarios_from_config(" not in stripped:
            continue
        if stripped.startswith(("from ", "import ")):
            continue
        depth = line.count("(") - line.count(")")
        j = i
        while depth > 0 and j + 1 < len(cleaned):
            j += 1
            depth += cleaned[j].count("(") - cleaned[j].count(")")
        return MatcherResult(start_idx=j + 1, return_vars=("df",), generated_scaffold=True)

    first = param_names[0] if param_names else "df"
    first_line = cleaned[0].strip().replace(" ", "")
    generated_aliases = {f"df={first}", f"return{first}"}
    if first_line in generated_aliases:
        return MatcherResult(
            start_idx=_statement_end_index(cleaned, 0),
            return_vars=("df",),
        )
    return MatcherResult(start_idx=0, return_vars=("df",))


def _call_func_name(func: ast.expr) -> str | None:
    """Return the bare callee name for ``name(...)`` / ``pkg.name(...)`` calls."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _outer_boilerplate_call_end_line(cleaned: list[str], call_names: frozenset[str]) -> int | None:
    """Locate the first outer-scope generated call and return its end index.

    Finds the generated ``score_from_config(...)`` /
    ``apply_rating_step_from_config(...)`` call via the AST rather than a
    substring scan, so an occurrence of the token inside a string literal or
    comment can no longer anchor boilerplate stripping on the wrong line
    (which silently dropped every line of user code).

    The cleaned body is wrapped in a synthetic function so a top-level
    ``return`` stays syntactically valid; line numbers are mapped back by
    subtracting the single wrapper line. Returns the ``start_idx`` (index in
    ``cleaned`` of the first user line after the call), or ``None`` when the
    body cannot be parsed or has no such call at the outer scope.
    """
    body = _dedent("\n".join(cleaned))
    wrapped = "def __haute_body__():\n" + "\n".join(
        f"    {line}" if line.strip() else line for line in body.splitlines()
    )
    try:
        tree = ast.parse(wrapped)
    except SyntaxError:
        return None
    fn = tree.body[0]
    if not isinstance(fn, ast.FunctionDef):
        return None

    best: tuple[int, int] | None = None

    def _walk(node: ast.AST) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPE_NODES):
                continue
            if isinstance(child, ast.Call) and _call_func_name(child.func) in call_names:
                end = child.end_lineno or child.lineno
                if best is None or child.lineno < best[0]:
                    best = (child.lineno, end)
            _walk(child)

    _walk(fn)
    if best is None:
        return None
    # Subtract the synthetic ``def`` wrapper line to map back to ``cleaned``.
    # ``end`` is the 1-based last source line of the call within the wrapper;
    # the first user line index in ``cleaned`` is exactly ``end - 1``.
    return best[1] - 1


_MODEL_SCORE_CALL_NAMES = frozenset({"score_from_config"})
_RATING_STEP_CALL_NAMES = frozenset({"apply_rating_step_from_config"})


def _match_model_score(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ModelScore nodes: skip up to and including the ``score_from_config(...)`` call.

    The call is located by AST walk (outer scope only), so multi-line calls
    are handled for free and tokens in strings/comments are ignored.  If no
    score_from_config call is found, return an out-of-range start index so
    the engine yields an empty result.
    """
    start_idx = _outer_boilerplate_call_end_line(cleaned, _MODEL_SCORE_CALL_NAMES)
    if start_idx is None:
        # No call found — treat the whole body as boilerplate (empty user code)
        return MatcherResult(start_idx=len(cleaned) + 1, return_vars=("df", "result"))
    return MatcherResult(start_idx=start_idx, return_vars=("df", "result"))


def _match_rating_step(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """RatingStep nodes: skip generated config-application scaffold."""
    start_idx = _outer_boilerplate_call_end_line(cleaned, _RATING_STEP_CALL_NAMES)
    if start_idx is None:
        return _match_polars(cleaned, param_names)
    return MatcherResult(start_idx=start_idx, return_vars=("df",), generated_scaffold=True)


def _match_external(cleaned: list[str], param_names: tuple[str, ...]) -> MatcherResult:
    """ExternalFile nodes: skip the generated file-loading prefix only.

    The generated boilerplate (see the ``_RETAINED_EXTERNAL`` template in
    ``_codegen_builders``) is loader imports followed by the obj-load —
    either ``obj = load_external_object(...)`` or a legacy
    ``with open(...)`` block.  User code is emitted strictly AFTER the
    load, so the load is the boundary that makes import position
    meaningful:

    * Imports BEFORE the load belong to the loader and are stripped —
      codegen regenerates them on every save.
    * Imports AFTER the load are user code and are preserved.  The old
      scan treated every import in the prefix as boilerplate, silently
      dropping user imports that directly followed the load and
      re-emitting files that no longer ran standalone.
    * A body with NO load at all contains no generated boilerplate, so
      its imports are user code too.

    ``with open(...)`` blocks and ``obj = …`` / ``obj.…`` statements in
    the prefix are still treated as load boilerplate wherever they
    appear (legacy multi-step loads); only import handling is
    position-aware.
    """
    if not cleaned:
        return MatcherResult(start_idx=0, return_vars=("df",))

    # Determine the base indentation from the first non-blank line
    base_indent = 0
    for line in cleaned:
        if line.strip():
            base_indent = len(line) - len(line.lstrip())
            break

    i = 0
    in_with = False
    saw_load = False
    first_import_idx: int | None = None
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
            if saw_load:
                break  # user import after the load — user code starts here
            if first_import_idx is None:
                first_import_idx = i
            i += 1
            continue
        if s.startswith("with open("):
            saw_load = True
            in_with = True
            i += 1
            continue
        if s.startswith("obj = ") or s.startswith("obj."):
            saw_load = True
            i = _statement_end_index(cleaned, i)
            continue

        break  # first user-code line

    if not saw_load and first_import_idx is not None:
        # No load boilerplate exists, so nothing here was generated —
        # the imports the scan skipped belong to the user.
        i = first_import_idx

    return MatcherResult(start_idx=i, return_vars=("df",))


# Registry of boilerplate matchers, keyed by *kind*.  The kind string
# is the public key callers use to select a matcher; aliases for the
# same matcher are allowed so callers can use either the NodeType style
# ("dataInput") or the Python-snake style ("data_input", "source").
BOILERPLATE_MATCHERS: dict[str, BoilerplateMatcher] = {
    "polars": _match_polars,
    "transform": _match_polars,
    "source": _match_source,
    "dataInput": _match_source,
    "data_input": _match_source,
    "scenario_expander": _match_scenario_expander,
    "scenarioExpander": _match_scenario_expander,
    "model_score": _match_model_score,
    "modelScore": _match_model_score,
    "rating_step": _match_rating_step,
    "ratingStep": _match_rating_step,
    "external": _match_external,
    "externalFile": _match_external,
    "external_file": _match_external,
}


def _strip_trailing_return(code_lines: list[str], return_vars: tuple[str, ...]) -> list[str]:
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
    stripped_source = source
    for return_var in return_vars:
        next_source = _strip_outer_trailing_return(stripped_source, return_var)
        if next_source != stripped_source:
            stripped_source = next_source
            break
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
    code = _strip_generated_passthrough_from_code(code, param_names)
    if not code.strip():
        return ""

    # Strip codegen-prepended "df = <param_name>" alias to prevent
    # accumulation on save/reload roundtrips.
    first_line = code.splitlines()[0].strip() if code else ""
    if first_line.startswith("df = ") or first_line.startswith("df="):
        alias_target = first_line.split("=", 1)[1].strip()
        if alias_target in param_names:
            remaining = "\n".join(code.splitlines()[1:]).strip()
            if remaining:
                code = remaining
            else:
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

    if not code.strip():
        return code

    # Pattern 2: hand-written "return <expr>" at the OUTER scope only.
    rewritten = _rewrite_outer_returns_as_assignment(code, target="df")
    return _dedent(rewritten).strip()


def _finalise_source(code: str, param_names: tuple[str, ...]) -> str:
    """Source-specific post-processing: unwrap chain-assignment pattern if present."""
    code = _strip_source_load_boilerplate_from_code(code)
    chain = _unwrap_chain_assignment(code)
    if chain is not None:
        return chain
    return code


def _rewrite_identifier_tokens(source: str, *, old: str, new: str, context: str) -> str:
    """Rewrite identifier tokens while preserving user formatting/comments."""
    if old not in source:
        return source

    try:
        tree = _parse_user_code(source, context=context)
    except _UserCodeParseError:
        return source

    edits: dict[int, list[tuple[int, int]]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == old
            and node.end_lineno == node.lineno
            and node.end_col_offset is not None
        ):
            edits.setdefault(node.lineno, []).append((node.col_offset, node.end_col_offset))

    if not edits:
        return source

    lines = source.splitlines(keepends=True)
    for line_no, spans in edits.items():
        line_idx = line_no - 1
        line = lines[line_idx]
        for start, end in sorted(spans, reverse=True):
            line = line[:start] + new + line[end:]
        lines[line_idx] = line
    return "".join(lines).strip()


def _finalise_model_score(code: str, param_names: tuple[str, ...]) -> str:
    """Normalise legacy modelScore post-code from ``result`` to ``df``."""
    rewritten = _rewrite_identifier_tokens(
        code,
        old="result",
        new="df",
        context="model score user code",
    )
    return _strip_generated_passthrough_from_code(rewritten, ("df",))


def _finalise_rating_step(code: str, param_names: tuple[str, ...]) -> str:
    """RatingStep post-processing runs after the declarative tables create ``df``."""
    finalised = _finalise_polars(code, param_names)
    first_param = param_names[0] if param_names else ""
    if not first_param or first_param == "df":
        return finalised
    return _rewrite_identifier_tokens(
        finalised,
        old=first_param,
        new="df",
        context="rating step user code",
    )


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
# engine pass.
_FINALISERS: dict[str, Callable[[str, tuple[str, ...]], str]] = {
    "polars": _finalise_polars,
    "transform": _finalise_polars,
    "source": _finalise_source,
    "dataInput": _finalise_source,
    "data_input": _finalise_source,
    "scenario_expander": _finalise_polars,
    "scenarioExpander": _finalise_polars,
    "model_score": _finalise_model_score,
    "modelScore": _finalise_model_score,
    "rating_step": _finalise_rating_step,
    "ratingStep": _finalise_rating_step,
    "external": _finalise_external,
    "externalFile": _finalise_external,
    "external_file": _finalise_external,
}


def _strip_generated_boilerplate_from_code(
    code: str,
    *,
    kind: str,
    param_names: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Strip generated scaffold from a persisted code-editor snippet.

    Parser extraction handles full function bodies. Codegen also needs a narrow
    guard for older or polluted configs where generated scaffold has already
    leaked into ``config["code"]``. This helper only invokes the heavier
    extractor when a scaffold marker is present; ordinary user code is returned
    unchanged.
    """
    stripped = code.strip()
    if not stripped:
        return ""

    params = tuple(param_names or ())
    if kind in {"source", "dataInput", "data_input"}:
        return _strip_source_load_boilerplate_from_code(stripped)
    if kind in {"scenario_expander", "scenarioExpander"}:
        if "expand_scenarios_from_config(" in stripped:
            return extract_user_code(stripped, kind="scenario_expander", param_names=params)
        first = params[0] if params else ""
        first_line = stripped.splitlines()[0].strip().replace(" ", "")
        if first and first_line in {f"df={first}", f"return{first}"}:
            return extract_user_code(stripped, kind="scenario_expander", param_names=params)
        return stripped
    if kind in {"model_score", "modelScore"}:
        if "score_from_config(" in stripped:
            return extract_user_code(stripped, kind="model_score", param_names=params)
        return _finalise_model_score(stripped, params)
    if kind in {"rating_step", "ratingStep"}:
        if "apply_rating_step_from_config(" in stripped:
            return extract_user_code(stripped, kind="rating_step", param_names=params)
        return _finalise_rating_step(stripped, params)
    if kind in {"external", "externalFile", "external_file"}:
        markers = (
            "load_external_object(",
            "with open(",
            "pickle.load(",
            "joblib.load(",
            "obj = load",
        )
        if any(marker in stripped for marker in markers):
            return extract_user_code(stripped, kind="external", param_names=params)
        return stripped
    if kind in {"polars", "transform"}:
        first = params[0] if params else ""
        first_line = stripped.splitlines()[0].strip().replace(" ", "")
        if len(params) == 1 and first and first_line in {f"df={first}", f"return{first}"}:
            return extract_user_code(stripped, kind="polars", param_names=params)
        return stripped

    return stripped


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

    lines = body_source.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    result = matcher(cleaned, params)
    rest = cleaned[result.start_idx :]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()
    code_lines = _strip_trailing_return(code.splitlines(), result.return_vars)
    if not code_lines:
        return ""

    code = "\n".join(code_lines).strip()
    if result.generated_scaffold:
        # The generated ``df = <helper>(...)`` scaffold already produced ``df``;
        # remaining lines are pure user code referencing it, so finalise
        # without treating the first param as a strippable alias.
        return _finalise_polars(code, ())
    return finaliser(code, params)


# ---------------------------------------------------------------------------
# Per-kind wrappers over the consolidated engine
# ---------------------------------------------------------------------------


def _extract_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract the meaningful user code from a polars/transform function body.

    Strips the docstring and the codegen-appended ``return df``.
    For ``df = (<expr>)`` whose parens provably wrap the whole RHS it
    drops the redundant wrapper (statement form is preserved).
    For hand-written ``return expr`` it strips the ``return`` keyword.
    For multi-statement bodies (assignments, comments) it returns as-is.
    """
    return extract_user_code(body_source, kind="polars", param_names=tuple(param_names))


def _extract_source_user_code(body_source: str) -> str:
    """Extract user code from a Data Input body.

    The auto-generated boilerplate is a source-load assignment or return
    statement at the top (for example ``df = pl.scan_parquet("...")`` or
    ``return resolve_api_input_from_config(...)``). Everything after that
    load — minus the trailing ``return df`` — is user code.

    User code follows the generated boilerplate directly.
    """
    return extract_user_code(body_source, kind="source")


def _extract_scenario_expander_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract post-expansion custom code from a SCENARIO_EXPANDER body."""
    return extract_user_code(
        body_source,
        kind="scenario_expander",
        param_names=tuple(param_names),
    )


def _extract_model_score_user_code(body_source: str) -> str:
    """Extract user post-processing code from a MODEL_SCORE function body.

    The auto-generated boilerplate is the ``from pathlib ...`` /
    ``score_from_config(...)`` block.  Everything after the
    ``result = score_from_config(...)`` line (minus ``return result``)
    is user code.

    User code follows the generated scoring block directly.
    """
    return extract_user_code(body_source, kind="model_score")


def _extract_rating_step_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract post-rating custom code from a RATING_STEP function body."""
    return extract_user_code(body_source, kind="rating_step", param_names=tuple(param_names))


def _extract_external_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract user code from an externalFile function body.

    Strips the docstring, then scans forward to skip the generated
    file-loading boilerplate (loader imports, with-open blocks, obj
    assignments / method calls).  Imports are loader boilerplate only
    while they precede the obj-load; user imports after the load — and
    all imports in a body with no load — are preserved (see
    :func:`_match_external`).  Everything between the boilerplate and a
    trailing ``return df`` is the user code.
    """
    return extract_user_code(body_source, kind="external", param_names=tuple(param_names))
