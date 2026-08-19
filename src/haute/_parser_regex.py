"""Neutral fragment recovery for pipeline files with syntax errors.

When a .py file has syntax errors and ``ast.parse`` fails, this module
extracts ``@pipeline.<type>`` decorated functions, ``pipeline.connect()``
calls, and pipeline metadata.  Call/decorator *sites* are located with
regular expressions (the file as a whole is unparseable by definition),
but the recovered fragments are re-parsed with the real AST wherever
possible so values keep full fidelity. Editor recovery consumes the neutral
fragments; strict parser entry points never import this module.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from haute._ast_helpers import (
    _eval_ast_literal,
    _extract_connect_calls,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
)
from haute._parser_submodels import (
    SubmodelRegistration,
    extract_submodel_registrations,
)
from haute._types import DECORATOR_TO_NODE_TYPE, NodeType
from haute.errors import ParseError

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_RE_DECORATOR_ANCHOR = re.compile(r"(?m)^@pipeline\.(\w+)\b")

_RE_IMPORT_LINE = re.compile(r"(?m)^[ \t]*(?:from|import)\b[^\r\n]*")
_RE_PIPELINE_ASSIGNMENT = re.compile(r"(?m)^pipeline\s*=")

# Anchor for pipeline.connect(...) call sites.  The negative lookbehind
# rejects other receivers (``mypipeline.connect``, ``module.pipeline.connect``).
# The call body itself is NOT regex-matched: port labels are emitted via
# ``json.dumps`` and may contain escaped quotes, parens, or span lines, so
# the span is recovered with a string-aware paren scan and then handed to
# the same AST walk the healthy parser uses (see ``_find_connect_calls``).
_RE_CONNECT_ANCHOR = re.compile(r"(?<![\w.])pipeline\s*\.\s*connect\s*\(")

# Anchor for pipeline.submodel(...) call sites.  Same receiver constraints
# as connect: the submodel paths live at module top level, so they usually
# survive a syntax error deeper in a function body and can be recovered for
# the fallback graph (mirroring the healthy submodel path).
_RE_SUBMODEL_ANCHOR = re.compile(r"(?<![\w.])pipeline\s*\.\s*submodel\s*\(")

# A chained method link directly after a balanced call: ``.method(``.
# ``connect()`` returns ``Self``, so ``.connect("a","b").connect("b","c")``
# is valid runtime code; non-connect links are scanned over so a chain's
# later connect calls are not lost.  The gap accepts backslashes as well
# as whitespace so line-continuation chains (``connect(...) \`` newline
# ``.connect(...)``) stay welded; ``ast.parse`` on the welded span is the
# validity arbiter — an invalid weld fails loud rather than silently
# splitting the chain.
_RE_CHAIN_LINK = re.compile(r"(?:\s|\\)*\.\s*\w+\s*\(")
_RE_COMPOUND_SUITE_PREFIX = re.compile(
    r"^(?:"
    r"if(?=\s|\()|elif(?=\s|\()|else(?=:)|"
    r"for(?=\s|\()|while(?=\s|\()|with(?=\s|\()|"
    r"try(?=:)|except(?=\s|\(|:)|finally(?=:)|"
    r"def\s|class\s|match(?=\s|\()|case(?=\s|\()|"
    r"async\s+(?:for(?=\s|\()|with(?=\s|\()|def\s)"
    r")"
)


@dataclass(frozen=True, slots=True)
class RecoveredFunctionFragment:
    """One syntax-recovered decorated function before config resolution."""

    authored_id: str
    decorator_name: str
    decorator_text: str
    explicit_node_type: NodeType | None
    param_names: tuple[str, ...]
    edge_param_names: tuple[str, ...]
    params_text: str
    body_text: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class RecoveredPipelineFragments:
    """Source-wide fragments safe to hand to the editor recovery boundary."""

    pipeline_name: str
    pipeline_description: str
    preamble: str
    preserved_blocks: tuple[str, ...]
    functions: tuple[RecoveredFunctionFragment, ...]
    connections: tuple[tuple[str, str, str | None, str | None], ...]
    submodel_registrations: tuple[SubmodelRegistration, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRING_PREFIX_CHARS = frozenset("rRuUbBfF")


def _string_quote_start(source: str, idx: int) -> int | None:
    """Return the quote index when a Python string literal starts at *idx*."""
    if source[idx] in "\"'":
        return idx
    if idx > 0 and (source[idx - 1].isalnum() or source[idx - 1] == "_"):
        return None
    j = idx
    while j < len(source) and source[j] in _STRING_PREFIX_CHARS and j - idx < 3:
        j += 1
    if j > idx and j < len(source) and source[j] in "\"'":
        return j
    return None


def _line_start(source: str, idx: int) -> int:
    return source.rfind("\n", 0, idx) + 1


def _parenthesized_wrapper_depth_before(source: str, idx: int) -> int:
    """Return pure top-level parenthesis wrapper depth before *idx*.

    ``(\npipeline.connect(...)\n)`` parses as a top-level ``ast.Expr`` call,
    so it should behave like the healthy AST parser.  Assignment/list/dict
    continuations such as ``disabled = [\npipeline.connect(...)\n]`` must
    remain rejected.
    """
    stack: list[str] = []
    statement_code: list[str] = []
    i = 0
    while i < idx:
        string_end = _skip_string_literal(source, i)
        if string_end is not None:
            i = string_end
            continue
        ch = source[i]
        if ch == "#":
            line_end = source.find("\n", i)
            if line_end == -1 or line_end >= idx:
                break
            i = line_end + 1
            continue
        if ch in "([{":
            stack.append(ch)
            statement_code.append(ch)
        elif ch in ")]}":
            if stack:
                stack.pop()
            statement_code.append(ch)
        elif not stack and ch in "\n;":
            statement_code = []
        elif not ch.isspace():
            statement_code.append(ch)
        i += 1

    if not stack:
        return 0
    if all(ch == "(" for ch in stack) and "".join(statement_code) == "(" * len(stack):
        return len(stack)
    return -1


def _code_before_comment(line: str) -> str:
    """Return *line* truncated at its first code-level ``#`` comment.

    String literals are skipped so a ``#`` (or a trailing ``\\``) inside a
    string is not mistaken for a comment marker / line continuation.
    """
    i = 0
    n = len(line)
    while i < n:
        string_end = _skip_string_literal(line, i)
        if string_end is not None:
            i = string_end
            continue
        if line[i] == "#":
            return line[:i]
        i += 1
    return line


def _has_backslash_continuation_before(source: str, idx: int) -> bool:
    line_start = _line_start(source, idx)
    if line_start == 0:
        return False
    previous_line = source[_line_start(source, line_start - 1) : line_start - 1]
    # Only a trailing backslash in the *code* portion is a real line
    # continuation; a ``\`` inside a trailing comment (``x = 1  # foo \``) or
    # a string literal must not suppress a valid top-level statement anchor.
    return _code_before_comment(previous_line).rstrip().endswith("\\")


def _parenthesized_wrapper_tail_closes(source: str, idx: int, depth: int) -> bool:
    closed = 0
    i = idx
    while i < len(source) and closed < depth:
        ch = source[i]
        if ch == "#":
            line_end = source.find("\n", i)
            if line_end == -1:
                return False
            i = line_end + 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == ")":
            closed += 1
            i += 1
            continue
        return False
    return closed == depth


def _is_top_level_statement_anchor(source: str, idx: int) -> bool:
    """Return True when *idx* begins a recoverable module-level statement."""
    wrapper_depth = _parenthesized_wrapper_depth_before(source, idx)
    if wrapper_depth < 0 or _has_backslash_continuation_before(source, idx):
        return False
    if wrapper_depth:
        return True
    line_start = _line_start(source, idx)
    if line_start == idx:
        return True
    if source[line_start] in " \t":
        return False

    last_code = ""
    code_prefix: list[str] = []
    i = line_start
    while i < idx:
        string_end = _skip_string_literal(source, i)
        if string_end is not None:
            code_prefix.append(" ")
            i = string_end
            continue
        ch = source[i]
        if ch == "#":
            return False
        code_prefix.append(ch)
        if not ch.isspace():
            last_code = ch
        i += 1
    if last_code != ";":
        return False

    stripped_prefix = "".join(code_prefix).strip()
    if ":" in stripped_prefix and _RE_COMPOUND_SUITE_PREFIX.match(stripped_prefix):
        return False
    return True


def _skip_string_literal(source: str, idx: int) -> int | None:
    """Return the first index after the string literal starting at *idx*.

    The fallback runs on syntactically broken files, so this intentionally
    avoids ``tokenize``: an unrelated unclosed delimiter must not prevent
    recovery of code before it.  The scanner is conservative — when a
    triple-quoted string never closes it skips to EOF, because everything
    that follows is part of the broken string from Python's perspective.
    """
    quote_idx = _string_quote_start(source, idx)
    if quote_idx is None:
        return None

    quote = source[quote_idx]
    triple = source.startswith(quote * 3, quote_idx)
    end_quote = quote * (3 if triple else 1)
    i = quote_idx + len(end_quote)
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if source.startswith(end_quote, i):
            return i + len(end_quote)
        if not triple and ch == "\n":
            return i
        i += 1
    return n


def _iter_top_level_anchor_matches(
    source: str,
    pattern: re.Pattern[str],
) -> Iterator[re.Match[str]]:
    """Yield *pattern* matches that occur in top-level code tokens.

    Raw text search is not enough on the fallback path: files that fail
    ``ast.parse`` may still contain comments, single-line strings, and
    triple-quoted strings with call-looking prose.  Those substrings must
    behave like the healthy AST parser and contribute nothing.
    """
    i = 0
    n = len(source)
    while i < n:
        string_end = _skip_string_literal(source, i)
        if string_end is not None:
            i = string_end
            continue
        if source[i] == "#":
            line_end = source.find("\n", i)
            if line_end == -1:
                break
            i = line_end + 1
            continue
        m = pattern.match(source, i)
        if m is not None:
            if _is_top_level_statement_anchor(source, m.start()):
                yield m
            i = m.end()
            continue
        i += 1


def _iter_connect_anchor_matches(source: str) -> Iterator[re.Match[str]]:
    """Yield ``pipeline.connect(`` regex matches that occur in code tokens."""
    return _iter_top_level_anchor_matches(source, _RE_CONNECT_ANCHOR)


def _scan_call_end(source: str, open_idx: int, line_no: int) -> int:
    """Return the index one past the ``)`` closing the call opened at *open_idx*.

    Tracks string state (single/double quotes, backslash escapes) so parens
    and quotes inside port labels — codegen emits them via ``json.dumps`` —
    do not unbalance the scan.  Spans may cross newlines (multi-line calls).

    Raises:
        ParseError: when the call never closes.  An edge we can see but
            cannot recover must fail loud: returning a graph without it
            would silently drop the connect line on the next save.
    """
    depth = 0
    i = open_idx
    n = len(source)
    while i < n:
        string_end = _skip_string_literal(source, i)
        if string_end is not None:
            i = string_end
            continue
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ParseError(
        "pipeline.connect() call is never closed; the edge cannot be recovered "
        "from this file — fix the syntax error at the connect() call",
        line=line_no,
    )


def _find_connect_calls(source: str) -> list[tuple[str, str, str | None, str | None]]:
    """Locate every ``pipeline.connect(...)`` call in possibly-broken source.

    The *call site* is found textually (the file failed ``ast.parse``, so
    regex anchoring is unavoidable), but the *call body* is parsed with the
    real AST and extracted by :func:`haute._ast_helpers._extract_connect_calls`
    — the exact walk the healthy parser uses.  Every form codegen emits
    (bare two-arg, ``source_port=``, ``target_port=``, both) and chained
    calls therefore behave identically on both paths.

    Per-form policy:

    * codegen-emitted forms (bare / port kwargs, json-escaped labels) —
      recovered with full fidelity;
    * chained ``.connect(...)`` links — recovered (each link is an edge);
    * commented-out calls — skipped (standard way to disable an edge);
    * a call we can see but cannot parse (unclosed paren, malformed args)
      — :class:`ParseError`.  A recovery parse that silently returns a
      plausible-but-incomplete graph corrupts the file on the next save,
      which is strictly worse than a loud failure;
    * non-literal arguments — :class:`ParseError`, same policy as the
      healthy parser.
    """
    connects: list[tuple[str, str, str | None, str | None]] = []
    for m in _iter_connect_anchor_matches(source):
        line_no = source.count("\n", 0, m.start()) + 1
        wrapper_depth = _parenthesized_wrapper_depth_before(source, m.start())
        end = _scan_call_end(source, m.end() - 1, line_no)
        while (chain := _RE_CHAIN_LINK.match(source, end)) is not None:
            end = _scan_call_end(source, chain.end() - 1, line_no)
        if wrapper_depth and not _parenthesized_wrapper_tail_closes(
            source,
            end,
            wrapper_depth,
        ):
            continue
        span = source[m.start() : end]
        try:
            span_tree = ast.parse(span)
        except SyntaxError as exc:
            raise ParseError(
                "pipeline.connect() call could not be parsed; the edge cannot be "
                "recovered from this file — fix the syntax error at the connect() call",
                line=line_no,
            ) from exc
        connects.extend(_extract_connect_calls(span_tree, receiver="pipeline"))
    return connects


def _position_is_code(source: str, idx: int) -> bool:
    """Return True when *idx* is not inside a string literal or comment."""
    i = 0
    while i < idx:
        string_end = _skip_string_literal(source, i)
        if string_end is not None:
            if string_end > idx:
                return False
            i = string_end
            continue
        if source[i] == "#":
            line_end = source.find("\n", i)
            if line_end == -1 or line_end >= idx:
                return False
            i = line_end + 1
            continue
        i += 1
    return True


def _line_end(source: str, idx: int) -> int:
    end = source.find("\n", idx)
    return len(source) if end == -1 else end


def _recover_pipeline_import_aliases(source: str) -> tuple[set[str], set[str]]:
    """Recover Haute module and Pipeline constructor aliases from valid import lines."""
    module_aliases = {"haute"}
    constructor_aliases: set[str] = set()
    for match in _RE_IMPORT_LINE.finditer(source):
        if not _position_is_code(source, match.start()):
            continue
        try:
            import_tree = ast.parse(match.group(0).lstrip())
        except SyntaxError:
            continue
        if len(import_tree.body) != 1:
            continue
        statement = import_tree.body[0]
        if isinstance(statement, ast.Import):
            module_aliases.update(
                alias.asname or "haute" for alias in statement.names if alias.name == "haute"
            )
        elif (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == "haute"
        ):
            constructor_aliases.update(
                alias.asname or "Pipeline" for alias in statement.names if alias.name == "Pipeline"
            )
    return module_aliases, constructor_aliases


def _recover_pipeline_meta(source: str) -> tuple[str, str]:
    """Recover alias-aware ``pipeline = Pipeline(...)`` fallback metadata."""
    module_aliases, constructor_aliases = _recover_pipeline_import_aliases(source)
    spellings = [*(rf"{re.escape(alias)}\.Pipeline" for alias in module_aliases)]
    spellings.extend(re.escape(alias) for alias in constructor_aliases)
    constructor_pattern = "|".join(sorted(spellings, key=len, reverse=True))
    meta_anchor = re.compile(rf"(?m)^pipeline\s*=\s*(?:{constructor_pattern})\s*\(")

    for match in meta_anchor.finditer(source):
        if not _position_is_code(source, match.start()):
            continue
        line_no = source.count("\n", 0, match.start()) + 1
        try:
            end = _scan_call_end(source, match.end() - 1, line_no)
        except ParseError as exc:
            raise ParseError(
                "pipeline metadata argument list is never closed; the pipeline "
                "metadata cannot be recovered from this file",
                line=line_no,
            ) from exc

        tail = source[end : _line_end(source, end)]
        comment_idx = tail.find("#")
        if comment_idx != -1:
            tail = tail[:comment_idx]
        if tail.strip():
            raise ParseError(
                "pipeline metadata has trailing text after the Pipeline(...) call; "
                "the pipeline metadata cannot be recovered from this file",
                line=line_no,
            )

        snippet = source[_line_start(source, match.start()) : end]
        try:
            meta_tree = ast.parse(snippet)
        except SyntaxError as exc:
            raise ParseError(
                "pipeline metadata could not be parsed; the pipeline metadata "
                "cannot be recovered from this file",
                line=line_no,
            ) from exc
        return _extract_pipeline_meta(meta_tree)

    if any(
        _position_is_code(source, match.start())
        for match in _RE_PIPELINE_ASSIGNMENT.finditer(source)
    ):
        raise ParseError(
            "pipeline metadata assignment is visible but its Pipeline constructor "
            "cannot be recovered; use `import haute`/`import haute as ...` or "
            "`from haute import Pipeline as ...`"
        )
    return "main", ""


def _recover_decorator_text(source: str, match: re.Match[str]) -> str:
    """Recover a full ``@pipeline.<type>(...)`` decorator span.

    The original fallback regex captured decorator arguments only until
    the first ``)``.  A visible decorator with nested calls (for example
    ``path=Path("x")``) was therefore silently dropped.  This helper uses
    the same balanced scan as connect recovery: recover the full decorator
    when possible, otherwise fail loud instead of returning a plausible
    graph with the node missing.
    """
    line_no = source.count("\n", 0, match.start()) + 1
    line_end = _line_end(source, match.start())
    pos = match.end()
    while pos < line_end and source[pos] in " \t":
        pos += 1
    if pos < line_end and source[pos] == "(":
        try:
            end = _scan_call_end(source, pos, line_no)
        except ParseError as exc:
            raise ParseError(
                "pipeline decorator argument list is never closed; the decorated node "
                "cannot be recovered from this file",
                line=line_no,
            ) from exc
        tail = source[end : _line_end(source, end)]
        comment_idx = tail.find("#")
        if comment_idx != -1:
            tail = tail[:comment_idx]
        if tail.strip():
            raise ParseError(
                "pipeline decorator has trailing text after the argument list; "
                "the decorated node cannot be recovered from this file",
                line=line_no,
            )
        return source[match.start() : end]

    tail = source[pos:line_end]
    comment_idx = tail.find("#")
    if comment_idx != -1:
        tail = tail[:comment_idx]
    if tail.strip():
        raise ParseError(
            "pipeline decorator is malformed; the decorated node cannot be recovered "
            "from this file",
            line=line_no,
        )
    return source[match.start() : pos].rstrip()


def _find_decorated_def(source: str, decorator_end: int) -> tuple[str, str, int]:
    """Return ``(func_name, params_text, def_end_line_idx)`` after a decorator.

    ``def_end_line_idx`` is the 0-based line index of the signature's closing
    ``)`` so callers extract the body from the line *after* the full
    signature — a signature wrapped across several lines therefore recovers
    correctly instead of aborting the whole fallback parse.
    """
    cursor = source.find("\n", decorator_end)
    if cursor == -1:
        raise ParseError(
            "pipeline decorator is not followed by a function definition; "
            "the decorated node cannot be recovered from this file",
        )
    cursor += 1
    while cursor < len(source):
        end = _line_end(source, cursor)
        line = source[cursor:end]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            cursor = end + 1
            continue
        # Match only the ``def <name>(`` header; the parameter list may wrap
        # across lines, so its extent is found with a balanced paren scan
        # rather than a single-line ``([^)]*)`` capture.
        header = re.match(r"(async\s+)?def\s+(\w+)\s*\(", line)
        if header is None:
            raise ParseError(
                "pipeline decorator is not followed by a recoverable function "
                "definition; the decorated node cannot be recovered from this file",
                line=source.count("\n", 0, cursor) + 1,
            )
        line_no = source.count("\n", 0, cursor) + 1
        if header.group(1):
            raise ParseError(
                f"@pipeline node {header.group(2)!r} is declared `async def`; "
                "pipeline node bodies must be synchronous — remove the `async` "
                "keyword.",
                line=line_no,
            )
        func_name = header.group(2)
        open_idx = cursor + header.end() - 1
        try:
            params_end = _scan_call_end(source, open_idx, line_no)
        except ParseError as exc:
            raise ParseError(
                "pipeline node signature parentheses are never closed; the "
                "decorated node cannot be recovered from this file",
                line=line_no,
            ) from exc
        params_text = source[open_idx + 1 : params_end - 1]
        def_end_line_idx = source.count("\n", 0, params_end - 1)
        return func_name, params_text, def_end_line_idx

    raise ParseError(
        "pipeline decorator is not followed by a function definition; "
        "the decorated node cannot be recovered from this file",
    )


def _find_function_blocks(source: str) -> list[dict]:
    """Find @pipeline.<type> function blocks in fallback source.

    Returns a list of dicts with keys: func_name, decorator_text,
    decorator_method, explicit_node_type, param_names, body_text,
    start_line.
    """
    lines = source.splitlines()
    blocks: list[dict] = []

    for m in _RE_DECORATOR_ANCHOR.finditer(source):
        if not _position_is_code(source, m.start()):
            continue
        decorator_method = m.group(1)

        decorator_text = _recover_decorator_text(source, m)
        func_name, params_text, def_line_idx = _find_decorated_def(
            source,
            m.start() + len(decorator_text),
        )

        try:
            signature_tree = ast.parse(f"def _recovered({params_text}):\n    pass\n")
        except SyntaxError as exc:
            raise ParseError(
                "decorated function signature could not be recovered",
                line=def_line_idx + 1,
            ) from exc
        signature = signature_tree.body[0]
        if not isinstance(signature, ast.FunctionDef):
            raise ParseError("decorated function signature could not be recovered")
        positional_param_names = [
            arg.arg for arg in (*signature.args.posonlyargs, *signature.args.args)
        ]
        param_names = [
            *positional_param_names,
            *(arg.arg for arg in signature.args.kwonlyargs),
        ]

        # Find the body: everything indented after the def line
        start_line = def_line_idx
        body_lines = []
        for i in range(def_line_idx + 1, len(lines)):
            line = lines[i]
            if line.strip() == "":
                body_lines.append(line)
                continue
            if line[0] == " " or line[0] == "\t":
                body_lines.append(line)
            else:
                break

        # Strip trailing empty lines
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()

        blocks.append(
            {
                "func_name": func_name,
                "decorator_text": decorator_text,
                "decorator_method": decorator_method,
                "explicit_node_type": DECORATOR_TO_NODE_TYPE.get(decorator_method),
                "param_names": param_names,
                "edge_param_names": positional_param_names,
                "params_text": params_text,
                "body_text": "\n".join(body_lines),
                "start_line": start_line,
            }
        )

    return blocks


def _parse_decorator_kwargs_regex(decorator_text: str) -> dict[str, Any]:
    """Extract keyword arguments from a decorator.

    The decorator-shape detection (``@pipeline.<method>(...)``) stays regex-
    driven — it has to, because we're in the regex fallback path for files
    ``ast.parse`` already rejected.  The *kwarg body* itself, however, is
    almost always a valid Python expression even when the surrounding file
    is broken, so we delegate to :func:`ast.parse` to recover structured
    literals (lists, dicts, tuples, ``None``, booleans) that the previous
    hand-rolled regex silently dropped or mangled.

    Value-parsing policy:

    1. **Literals** — :func:`ast.literal_eval` evaluates the kwarg value to
       its native Python type (``int``, ``float``, ``str``, ``bool``,
       ``None``, ``list``, ``dict``, ``tuple``, plus nested combinations).
       Downstream config builders rely on these concrete types, so the
       literal policy is preserved wherever possible.

    2. **Contract(...)** — the sanctioned public constructor spelling is
       lowered through the same helper as the healthy AST parser.

    3. **Everything else** — unresolved names, calls, f-strings, attribute
       chains, and ``**`` expansion fail loud.  Returning source text here
       would turn computed config into plain strings on the next save.

    Malformed kwargs (e.g. ``percent=50%``) surface as ``ParseError`` rather
    than being silently truncated — a wrong-but-plausible answer is strictly
    worse than a loud failure.
    """
    if "(" not in decorator_text:
        return {}
    # Strip the @pipeline.<method>( ... ) wrapper.  ``rpartition`` on the
    # trailing ``)`` lets us keep any nested parens inside the kwargs body
    # intact (e.g. ``tuple_val=(1, 2)``).
    _, _, inner = decorator_text.partition("(")
    inner = inner.rstrip()
    if inner.endswith(")"):
        inner = inner[:-1]
    inner = inner.strip()
    if not inner:
        return {}

    # Wrap in a synthetic call so ast.parse produces an ast.Call we can
    # walk — this handles every shape ``ast.literal_eval`` supports, plus
    # multi-line bodies and nested structures, in one pass.
    try:
        tree = ast.parse(f"f({inner})", mode="eval")
    except SyntaxError as exc:
        raise ParseError(
            "decorator kwargs could not be parsed during syntax-error recovery",
            line=exc.lineno,
            offset=exc.offset,
        ) from exc
    call = tree.body
    if not isinstance(call, ast.Call):
        raise ValueError(f"decorator kwargs body is not a call expression: {inner!r}")
    kwargs: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise ParseError(
                "decorator kwargs cannot use ** expansion in the regex fallback; "
                f"'**{ast.unparse(kw.value)}' cannot be resolved at parse time"
            )
        kwargs[kw.arg] = _resolve_kwarg_value(kw.arg, kw.value)
    return kwargs


def _resolve_kwarg_value(arg_name: str, value_node: ast.expr) -> Any:
    """Resolve a single kwarg value AST node to its Python representation.

    See :func:`_parse_decorator_kwargs_regex` for the policy: literal
    values and sanctioned ``Contract(...)`` are preserved; all other
    expressions fail loud before they can be serialized as config data.
    """
    try:
        return _eval_ast_literal(value_node)
    except ParseError as exc:
        raise ParseError(
            f"decorator kwargs must be literal values (or Contract(...)): "
            f"kwarg {arg_name!r} is the non-literal expression "
            f"{ast.unparse(value_node)!r}",
            kwarg=arg_name,
            line=getattr(value_node, "lineno", None),
        ) from exc


def _recover_submodel_registrations(source: str) -> list[SubmodelRegistration]:
    """Recover canonical ``pipeline.submodel(...)`` calls from broken source.

    The submodel calls live at module top level, so a syntax error deeper in
    a function body usually leaves them intact and extractable.  The scan is
    scoped to each submodel call span. Visible spans that cannot be recovered
    are accumulated into one typed diagnostic; returning the other references
    while silently dropping these would make the fallback graph unsafe to
    save.
    """
    registrations: list[SubmodelRegistration] = []
    unrecoverable: list[dict[str, str | int]] = []
    for m in _iter_top_level_anchor_matches(source, _RE_SUBMODEL_ANCHOR):
        line_no = source.count("\n", 0, m.start()) + 1
        try:
            end = _scan_call_end(source, m.end() - 1, line_no)
            while (chain := _RE_CHAIN_LINK.match(source, end)) is not None:
                end = _scan_call_end(source, chain.end() - 1, line_no)
        except ParseError:
            unrecoverable.append(
                {
                    "line": line_no,
                    "source": source[m.start() : _line_end(source, m.start())].strip(),
                }
            )
            continue
        span = source[m.start() : end]
        try:
            span_tree = ast.parse(span)
        except SyntaxError:
            unrecoverable.append({"line": line_no, "source": span.strip()})
            continue
        try:
            registrations.extend(extract_submodel_registrations(span_tree))
        except ParseError:
            unrecoverable.append({"line": line_no, "source": span.strip()})
            continue
    if unrecoverable:
        raise ParseError(
            "Regex fallback could not recover submodel reference(s).",
            unrecoverable_references=unrecoverable,
        )

    aliases: dict[str, int | None] = {}
    instance_ids: dict[str, int | None] = {}
    for registration in registrations:
        if registration.alias in aliases:
            raise ParseError(
                "Submodel instance alias is duplicated in the parent source.",
                alias=registration.alias,
                lines=[aliases[registration.alias], registration.line],
            )
        if registration.instance_id in instance_ids:
            raise ParseError(
                "Submodel instance id is duplicated in the parent source.",
                instance_id=registration.instance_id,
                lines=[instance_ids[registration.instance_id], registration.line],
            )
        aliases[registration.alias] = registration.line
        instance_ids[registration.instance_id] = registration.line
    return registrations


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def recover_pipeline_fragments(source: str) -> RecoveredPipelineFragments:
    """Recover source structure without constructing or validating a graph."""
    pipeline_name, pipeline_description = _recover_pipeline_meta(source)
    recovered_functions: list[RecoveredFunctionFragment] = []
    for block in _find_function_blocks(source):
        start_line = int(block["start_line"]) + 1
        recovered_functions.append(
            RecoveredFunctionFragment(
                authored_id=str(block["func_name"]),
                decorator_name=str(block["decorator_method"]),
                decorator_text=str(block["decorator_text"]),
                explicit_node_type=block["explicit_node_type"],
                param_names=tuple(str(value) for value in block["param_names"]),
                edge_param_names=tuple(str(value) for value in block["edge_param_names"]),
                params_text=str(block["params_text"]),
                body_text=str(block["body_text"]),
                start_line=start_line,
                end_line=start_line + str(block["body_text"]).count("\n") + 1,
            )
        )
    return RecoveredPipelineFragments(
        pipeline_name=pipeline_name,
        pipeline_description=pipeline_description,
        preamble=_extract_preamble(source),
        preserved_blocks=tuple(_extract_preserved_blocks(source)),
        functions=tuple(recovered_functions),
        connections=tuple(_find_connect_calls(source)),
        submodel_registrations=tuple(_recover_submodel_registrations(source)),
    )
