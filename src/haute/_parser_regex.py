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

from haute._config_io import find_config_by_func_name
from haute._logging import get_logger
from haute._parser_helpers import (
    _build_edges,
    _build_node_config,
    _build_rf_nodes,
    _extract_preamble,
    _get_docstring,
)
from haute._types import DECORATOR_TO_NODE_TYPE
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

_RE_DECORATOR_KWARG = re.compile(
    r'(\w+)\s*=\s*["\']([^"\']*)["\']',
)

_RE_DECORATOR_BOOL_KWARG = re.compile(
    r"(\w+)\s*=\s*(True|False)",
)

_RE_DECORATOR_NUM_KWARG = re.compile(
    r"(\w+)\s*=\s*(-?[\d.]+(?:e[+-]?\d+)?)\b",
    re.IGNORECASE,
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
    """Extract keyword arguments from a decorator using regex."""
    # Strip the @pipeline.<method>( ... ) wrapper
    if "(" in decorator_text:
        inner = decorator_text.split("(", 1)[1].rstrip(")")
        result: dict[str, Any] = dict(_RE_DECORATOR_KWARG.findall(inner))
        # Also capture boolean kwargs (e.g. output=True)
        for key, val in _RE_DECORATOR_BOOL_KWARG.findall(inner):
            if key not in result:
                result[key] = val == "True"
        # Also capture numeric kwargs (e.g. steps=10, default=1.05)
        for key, val in _RE_DECORATOR_NUM_KWARG.findall(inner):
            if key not in result:
                result[key] = float(val) if "." in val or "e" in val.lower() else int(val)
        return result
    return {}


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
        config = (
            loaded_config
            if loaded_config is not None
            else _build_node_config(
                node_type,
                decorator_kwargs,
                body,
                param_names,
            )
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
