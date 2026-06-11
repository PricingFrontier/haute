"""Regex-based fallback parser for pipeline files with syntax errors.

When a .py file has syntax errors and ``ast.parse`` fails, this module
extracts ``@pipeline.<type>`` decorated functions, ``pipeline.connect()``
calls, and pipeline metadata.  Call/decorator *sites* are located with
regular expressions (the file as a whole is unparseable by definition),
but the recovered fragments are re-parsed with the real AST wherever
possible so values keep full fidelity.  The result is a best-effort
PipelineGraph that the GUI can render alongside error markers; fragments
that are visible but unrecoverable fail loud rather than silently
dropping graph content.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from haute._ast_helpers import _extract_connect_calls, _extract_preamble, _get_docstring
from haute._config_builder import _build_node_config
from haute._config_io import find_config_by_func_name, has_config_folder
from haute._graph_builders import _build_edges, _build_rf_nodes
from haute._logging import get_logger
from haute._types import DECORATOR_TO_NODE_TYPE, NodeType, PipelineGraph
from haute.errors import ConfigError, ParseError

logger = get_logger(component="parser.regex")

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_RE_DECORATOR = re.compile(
    r"^(@pipeline\.(\w+)(?:\([^)]*?\))?)\s*\n"
    r"(?:\s*(?:#[^\n]*)?\n)*"
    r"def\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE | re.DOTALL,
)

_RE_PIPELINE_META = re.compile(
    r'pipeline\s*=\s*haute\.Pipeline\(\s*["\']([^"\']*)["\']'
    r'(?:.*?description\s*=\s*["\']([^"\']*)["\'])?',
)

# Anchor for pipeline.connect(...) call sites.  The negative lookbehind
# rejects other receivers (``mypipeline.connect``, ``module.pipeline.connect``).
# The call body itself is NOT regex-matched: port labels are emitted via
# ``json.dumps`` and may contain escaped quotes, parens, or span lines, so
# the span is recovered with a string-aware paren scan and then handed to
# the same AST walk the healthy parser uses (see ``_find_connect_calls``).
_RE_CONNECT_ANCHOR = re.compile(r"(?<![\w.])pipeline\s*\.\s*connect\s*\(")

# A chained method link directly after a balanced call: ``.method(``.
# ``connect()`` returns ``Self``, so ``.connect("a","b").connect("b","c")``
# is valid runtime code; non-connect links are scanned over so a chain's
# later connect calls are not lost.  The gap accepts backslashes as well
# as whitespace so line-continuation chains (``connect(...) \`` newline
# ``.connect(...)``) stay welded; ``ast.parse`` on the welded span is the
# validity arbiter — an invalid weld fails loud rather than silently
# splitting the chain.
_RE_CHAIN_LINK = re.compile(r"(?:\s|\\)*\.\s*\w+\s*\(")


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


def _iter_connect_anchor_matches(source: str):
    """Yield ``pipeline.connect(`` regex matches that occur in code tokens.

    Raw text search is not enough on the fallback path: files that fail
    ``ast.parse`` may still contain comments, single-line strings, and
    triple-quoted strings with connect-looking prose.  Those substrings
    must behave like the healthy AST parser and contribute no edges.
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
        m = _RE_CONNECT_ANCHOR.match(source, i)
        if m is not None:
            yield m
            i = m.end()
            continue
        i += 1


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
    quote: str | None = None
    i = open_idx
    n = len(source)
    while i < n:
        ch = source[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
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
        end = _scan_call_end(source, m.end() - 1, line_no)
        while (chain := _RE_CHAIN_LINK.match(source, end)) is not None:
            end = _scan_call_end(source, chain.end() - 1, line_no)
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


def _find_function_blocks(source: str) -> list[dict]:
    """Find @pipeline.<type> function blocks using regex.

    Returns a list of dicts with keys: func_name, decorator_text,
    decorator_method, explicit_node_type, param_names, body_text,
    start_line.
    """
    lines = source.splitlines()
    blocks: list[dict] = []

    for m in _RE_DECORATOR.finditer(source):
        decorator_text = m.group(1)
        decorator_method = m.group(2)
        func_name = m.group(3)
        params_text = m.group(4)

        # Skip decorators that aren't recognised type-specific methods
        if decorator_method not in DECORATOR_TO_NODE_TYPE:
            continue

        # Extract parameter names (strip type annotations)
        param_names = []
        for p in params_text.split(","):
            p = p.strip()
            if not p:
                continue
            name = p.split(":")[0].strip()
            if name:
                param_names.append(name)

        # Find the body: everything indented after the def line
        # The def line is somewhere after the decorator
        def_line_idx = source[: m.end()].count("\n")
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
                "explicit_node_type": DECORATOR_TO_NODE_TYPE[decorator_method],
                "param_names": param_names,
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

    Value-parsing policy (three tiers):

    1. **Literals** — :func:`ast.literal_eval` evaluates the kwarg value to
       its native Python type (``int``, ``float``, ``str``, ``bool``,
       ``None``, ``list``, ``dict``, ``tuple``, plus nested combinations).
       Downstream config builders rely on these concrete types, so the
       literal policy is preserved wherever possible.

    2. **Bare identifier rejection** — a lone ``ast.Name`` (e.g.
       ``kwarg=some_var``) cannot be resolved at parse time (we are
       already in the syntax-error fallback; the module is not
       importable).  Fail loud with ``ValueError`` so the broken
       reference surfaces to the user rather than leaking an opaque
       identifier string into downstream config.

    3. **Non-literal expressions** — when ``ast.literal_eval`` rejects the
       value (function calls like ``dict(a=1)``, f-strings, ternary
       ``IfExp``, attribute chains like ``pl.FlowMode.LAZY``, etc.) we
       fall back to :func:`ast.unparse` and return the raw source text.
       This preserves the user's intent losslessly without executing
       arbitrary code at parse time.  Downstream callers that need a
       literal value can re-parse the string themselves.

    **Exception:** a bare :class:`ast.Name` (e.g. ``depends=some_var``) is
    *not* a self-contained expression — it is an unresolved reference to
    something outside the decorator's lexical scope that we cannot
    evaluate safely at parse time.  These surface as ``ValueError`` so
    broken pipeline files fail loud rather than silently carrying an
    opaque identifier string downstream.

    Malformed kwargs (e.g. ``percent=50%``) surface as ``SyntaxError`` /
    ``ValueError`` rather than being silently truncated — a wrong-but-
    plausible answer is strictly worse than a loud failure.
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
    tree = ast.parse(f"f({inner})", mode="eval")
    call = tree.body
    if not isinstance(call, ast.Call):
        raise ValueError(f"decorator kwargs body is not a call expression: {inner!r}")
    return {
        kw.arg: _resolve_kwarg_value(kw.arg, kw.value) for kw in call.keywords if kw.arg is not None
    }


def _resolve_kwarg_value(arg_name: str, value_node: ast.expr) -> Any:
    """Resolve a single kwarg value AST node to its Python representation.

    See :func:`_parse_decorator_kwargs_regex` for the two-tier policy
    (literal_eval first, ast.unparse fallback, with ast.Name rejected).
    """
    # Tier 1: literal evaluation.  Preserves native Python types so the
    # downstream config builders (which index by type for e.g. list-of-
    # dicts factors) keep working unchanged.
    try:
        return ast.literal_eval(value_node)
    except (ValueError, SyntaxError):
        pass

    # Tier 2: bare Name is an unresolvable reference — fail loud rather
    # than letting an opaque identifier leak into the config.
    if isinstance(value_node, ast.Name):
        raise ValueError(
            f"decorator kwarg {arg_name!r} references an unresolved name "
            f"{value_node.id!r}; expected a literal or self-contained expression"
        )

    # Tier 3: non-literal expression (Call, JoinedStr, IfExp, Attribute,
    # BinOp, etc.) — round-trip the source text via ast.unparse so the
    # value survives the fallback parser without silently dropping.
    try:
        return ast.unparse(value_node)
    except Exception as exc:  # pragma: no cover — ast.unparse rarely fails on a valid AST
        raise ValueError(
            f"decorator kwarg {arg_name!r} could not be serialised: "
            f"ast.literal_eval and ast.unparse both refused the value "
            f"(node type {type(value_node).__name__})"
        ) from exc


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def fallback_parse(source: str, source_file: str, syntax_error: SyntaxError) -> PipelineGraph:
    """Parse a pipeline file with syntax errors using regex fallback.

    Extracts all @pipeline.<type> decorated functions, marks broken ones
    with an error in their config, and still returns the full graph.
    """
    # Resolve base_dir from source_file for config loading
    base_dir = Path(source_file).parent if source_file else Path.cwd()

    # Pipeline metadata via regex
    meta_match = _RE_PIPELINE_META.search(source)
    pipeline_name = meta_match.group(1) if meta_match else "main"
    pipeline_desc = (meta_match.group(2) or "") if meta_match else ""

    # Find function blocks
    blocks = _find_function_blocks(source)
    raw_nodes: list[dict] = []

    for block in blocks:
        func_name = block["func_name"]
        decorator_kwargs = _parse_decorator_kwargs_regex(block["decorator_text"])
        param_names = block["param_names"]
        node_type: NodeType = block["explicit_node_type"]

        # If the decorator references an external config file, try to
        # load it.  The config= path in the source may be mangled by
        # Windows backslash escapes (the reason we are in the regex
        # fallback), so reconstruct it from the function name.
        config_kwarg = decorator_kwargs.pop("config", None)
        loaded_config: dict | None = None
        if config_kwarg and func_name:
            recovered = find_config_by_func_name(func_name, base_dir)
            if recovered is not None:
                loaded_config, recovered_type = recovered
                # Explicit decorator type takes priority over config-inferred type
                if not block["explicit_node_type"]:
                    node_type = recovered_type

        # Try to parse the function individually to get the docstring
        params_str = ", ".join(param_names)
        func_source = (
            f"{block['decorator_text']}\ndef {func_name}({params_str}):\n{block['body_text']}"
        )
        description = ""
        has_syntax_error = False

        try:
            func_tree = ast.parse(func_source)
            for stmt in ast.iter_child_nodes(func_tree):
                if isinstance(stmt, ast.FunctionDef):
                    description = _get_docstring(stmt)
                    break
        except SyntaxError:
            has_syntax_error = True

        body = block["body_text"] if not has_syntax_error else ""
        if loaded_config is not None:
            config = loaded_config
        elif has_config_folder(node_type):
            raise ConfigError(
                "Node config must be stored in a JSON sidecar and referenced with "
                'config="config/<type>/<name>.json".',
                func_name=func_name,
                node_type=node_type.value,
            )
        else:
            config = _build_node_config(
                node_type,
                decorator_kwargs,
                body,
                param_names,
            )

        raw_nodes.append(
            {
                "func_name": func_name,
                "node_type": node_type,
                "description": description or f"{func_name} node",
                "config": config,
                "param_names": param_names,
            }
        )

    # Build edges + nodes using shared helpers
    connect_pairs = _find_connect_calls(source)
    edges = _build_edges(raw_nodes, connect_pairs)
    rf_nodes = _build_rf_nodes(raw_nodes)
    preamble = _extract_preamble(source)

    return PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=pipeline_name,
        pipeline_description=pipeline_desc,
        preamble=preamble,
        source_file=source_file,
        warning=f"File has syntax errors (line {syntax_error.lineno}); parsed via regex fallback",
    )
