"""Regex-based fallback parser for pipeline files with syntax errors.

When a .py file has syntax errors and ``ast.parse`` fails, this module
extracts ``@pipeline.<type>`` decorated functions, ``pipeline.connect()``
calls, and pipeline metadata using regular expressions.  The result is a
best-effort PipelineGraph that the GUI can render alongside error markers.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from haute._ast_helpers import _extract_preamble, _get_docstring
from haute._config_builder import _build_node_config
from haute._config_io import find_config_by_func_name, has_config_folder
from haute._graph_builders import _build_edges, _build_rf_nodes
from haute._logging import get_logger
from haute._types import DECORATOR_TO_NODE_TYPE
from haute.errors import ConfigError
from haute.graph_utils import NodeType, PipelineGraph

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

_RE_CONNECT = re.compile(
    r'pipeline\.connect\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

        # Find the line number of the def
        def_pos = m.start()
        start_line = source[:def_pos].count("\n")

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
    connect_pairs = _RE_CONNECT.findall(source)
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
