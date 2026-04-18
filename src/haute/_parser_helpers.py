"""Shared helper functions for the pipeline parser.

These are used by ``parser.py``, ``_parser_regex.py``, and
``_parser_submodels.py``.  Extracting them here breaks the circular
import that would otherwise exist between the parent module and its
extracted submodules.

Import direction:  _parser_helpers  ←  parser / _parser_regex / _parser_submodels
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from haute._config_io import (
    load_node_config,
)
from haute._config_validation import warn_unrecognized_config_keys
from haute._logging import get_logger
from haute._types import DECORATOR_TO_NODE_TYPE
from haute.errors import ConfigError
from haute.graph_utils import (
    MODEL_SCORE_CONFIG_KEYS,
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
)

logger = get_logger(component="parser_helpers")

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _eval_ast_literal(node: ast.expr) -> Any:
    """Safely evaluate an AST literal node."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.dump(node)


def _get_decorator_kwargs(decorator: ast.expr) -> dict[str, Any]:
    """Extract keyword arguments from a decorator.

    Handles both @pipeline.<type> and @pipeline.<type>(key=val, ...).
    """
    if isinstance(decorator, ast.Call):
        kwargs: dict[str, Any] = {}
        for kw in decorator.keywords:
            if kw.arg is not None:
                kwargs[kw.arg] = _eval_ast_literal(kw.value)
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


def _extract_decorated_nodes(
    tree: ast.Module,
    decorator_checker: Callable[[ast.expr], bool],
    func_bodies: dict[str, str],
    base_dir: Path | None,
) -> list[dict[str, Any]]:
    """Extract decorated function nodes from an AST tree.

    Iterates over top-level ``ast.FunctionDef`` nodes, finds those whose
    decorator matches *decorator_checker*, resolves their config, and
    returns a list of raw-node dicts ready for ``_build_rf_nodes`` /
    ``_build_edges``.

    Args:
        tree: The parsed AST module.
        decorator_checker: A callable that returns True for matching
            decorators (e.g. ``_is_pipeline_node_decorator``).
        func_bodies: Pre-extracted function body source, keyed by name
            (from ``_extract_function_bodies``).
        base_dir: Project root for resolving ``config=`` references.

    Returns:
        A list of dicts with keys ``func_name``, ``node_type``,
        ``description``, ``config``, and ``param_names``.
    """
    raw_nodes: list[dict[str, Any]] = []

    for stmt in ast.iter_child_nodes(tree):
        if not isinstance(stmt, ast.FunctionDef):
            continue

        matched_decorator = None
        for dec in stmt.decorator_list:
            if decorator_checker(dec):
                matched_decorator = dec
                break

        if matched_decorator is None:
            continue

        func_name = stmt.name
        decorator_kwargs = _get_decorator_kwargs(matched_decorator)
        param_names = [arg.arg for arg in stmt.args.args]
        n_params = len(param_names)
        description = _get_docstring(stmt)
        body = func_bodies.get(func_name, "")
        explicit_node_type = _get_decorator_node_type(matched_decorator)

        node_type, config = _resolve_node_config(
            decorator_kwargs,
            body,
            param_names,
            n_params,
            base_dir,
            func_name=func_name,
            explicit_node_type=explicit_node_type,
        )

        raw_nodes.append(
            {
                "func_name": func_name,
                "node_type": node_type,
                "description": description,
                "config": config,
                "param_names": param_names,
            }
        )

    return raw_nodes


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


def _extract_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract the meaningful user code from a function body.

    Strips the docstring and the codegen-appended ``return df``.
    For codegen chain style ``df = (...)`` it unwraps the inner expression.
    For hand-written ``return expr`` it strips the ``return`` keyword.
    For multi-statement bodies (assignments, comments) it returns as-is.
    """
    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)

    if not cleaned:
        return ""

    code = _dedent("\n".join(cleaned)).strip()

    # Strip codegen-appended "return df" and trailing blank lines.
    # _wrap_user_code always appends "return df"; leaving it causes a
    # bare "df" to accumulate on each save/reload roundtrip.
    code_lines = code.splitlines()
    while code_lines and code_lines[-1].strip() in ("return df", ""):
        code_lines.pop()
    if not code_lines:
        return ""
    code = "\n".join(code_lines).strip()

    # Strip codegen-prepended "df = <param_name>" alias to prevent
    # accumulation on save/reload roundtrips.
    first_line = code.splitlines()[0].strip() if code else ""
    if first_line.startswith("df = ") or first_line.startswith("df="):
        alias_target = first_line.split("=", 1)[1].strip()
        if alias_target in (param_names or []):
            remaining = "\n".join(code.splitlines()[1:]).strip()
            if remaining:
                code = remaining

    # Pattern 1: codegen chain style "df = (\n...\n)" — unwrap to inner
    chain = _unwrap_chain_assignment(code, param_names=param_names)
    if chain is not None:
        return chain

    # Pattern 2: hand-written "return <expr>" — convert to "df = <expr>"
    stripped_lines = []
    in_return = False
    for line in code.splitlines():
        s = line.strip()
        is_return = s == "return" or (s.startswith("return ") and not s.startswith("return_"))
        if is_return and not in_return:
            stripped_lines.append(line.replace("return ", "df = ", 1) if "return " in line else "")
            in_return = True
        elif in_return:
            stripped_lines.append(line)
        elif not is_return:
            stripped_lines.append(line)

    return _dedent("\n".join(stripped_lines)).strip()


def _extract_sentinel_user_code(body_source: str, return_var: str = "result") -> str:
    """Extract user code between ``# -- user code --`` sentinel and trailing return.

    **Legacy support** — older pipeline files use a sentinel comment to
    delimit auto-generated boilerplate from user code.  New codegen no
    longer writes the sentinel; the ``_extract_source_user_code`` and
    ``_extract_model_score_user_code`` functions handle both old and new
    formats.

    If no sentinel is found returns an empty string (caller should try
    the non-sentinel extraction path).
    """
    sentinel = "# -- user code --"
    if sentinel not in body_source:
        return ""

    # Take everything after the sentinel
    _, _, after = body_source.partition(sentinel)
    lines = after.strip().splitlines()
    if not lines:
        return ""

    # Strip trailing auto-generated return
    while lines and lines[-1].strip() in (f"return {return_var}", ""):
        lines.pop()

    if not lines:
        return ""

    return _dedent("\n".join(lines)).strip()


def _extract_source_user_code(body_source: str) -> str:
    """Extract user code from a DATA_SOURCE or SCENARIO_EXPANDER body.

    The auto-generated boilerplate is a single assignment line at the
    top (e.g. ``df = pl.scan_parquet("...")``).  Everything after that
    assignment — minus the trailing ``return df`` — is user code.

    Supports both the legacy sentinel format (``# -- user code --``)
    and the new sentinel-free format where user code follows the
    boilerplate directly.
    """
    # Legacy: try sentinel first
    legacy = _extract_sentinel_user_code(body_source, "df")
    if legacy:
        return legacy

    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    # Skip leading import lines (e.g. ``from pathlib import Path``)
    # that codegen adds before the data-load boilerplate.
    while cleaned and cleaned[0].strip().startswith(("from ", "import ")):
        cleaned.pop(0)

    # Skip the first statement — it's always the auto-generated load.
    # Detect the end of the first statement (handles multi-line
    # assignments like ``df = (\n    ...\n)``).
    first_end = 0
    depth = 0
    for i, line in enumerate(cleaned):
        depth += line.count("(") - line.count(")")
        if depth <= 0 and i >= first_end:
            first_end = i + 1
            break

    rest = cleaned[first_end:]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()

    # Strip trailing return df
    code_lines = code.splitlines()
    while code_lines and code_lines[-1].strip() in ("return df", ""):
        code_lines.pop()
    if not code_lines:
        return ""

    code = "\n".join(code_lines).strip()

    # Unwrap codegen chain style: df = (\n...\n)
    chain = _unwrap_chain_assignment(code)
    if chain is not None:
        return chain

    return code


def _extract_model_score_user_code(body_source: str) -> str:
    """Extract user post-processing code from a MODEL_SCORE function body.

    The auto-generated boilerplate is the ``from pathlib ...`` /
    ``score_from_config(...)`` block.  Everything after the
    ``result = score_from_config(...)`` line (minus ``return result``)
    is user code.

    Supports both the legacy sentinel format and the new sentinel-free
    format.
    """
    # Legacy: try sentinel first
    legacy = _extract_sentinel_user_code(body_source, "result")
    if legacy:
        return legacy

    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)
    if not cleaned:
        return ""

    # Find the score_from_config *call* (not the import) — everything
    # after it is user code.  The call line contains "= score_from_config"
    # or "return score_from_config".
    score_idx = None
    for i, line in enumerate(cleaned):
        stripped = line.strip()
        if "score_from_config(" in stripped and not stripped.startswith(("from ", "import ")):
            # Handle multi-line calls: find the closing paren
            depth = line.count("(") - line.count(")")
            j = i
            while depth > 0 and j + 1 < len(cleaned):
                j += 1
                depth += cleaned[j].count("(") - cleaned[j].count(")")
            score_idx = j
            break

    if score_idx is None:
        return ""

    rest = cleaned[score_idx + 1 :]
    if not rest:
        return ""

    code = _dedent("\n".join(rest)).strip()
    code_lines = code.splitlines()
    while code_lines and code_lines[-1].strip() in ("return result", ""):
        code_lines.pop()
    if not code_lines:
        return ""

    return "\n".join(code_lines).strip()


def _extract_external_user_code(body_source: str, param_names: list[str]) -> str:
    """Extract user code from an externalFile function body.

    Strips the docstring, then scans forward to skip the file-loading
    boilerplate (import statements, with-open blocks, obj assignments /
    method calls).  Everything between the boilerplate and a trailing
    ``return df`` is the user code.
    """
    lines = body_source.strip().splitlines()
    cleaned = _strip_docstring(lines)

    if not cleaned:
        return ""

    # Determine the base indentation from the first non-blank line
    base_indent = 0
    for line in cleaned:
        if line.strip():
            base_indent = len(line) - len(line.lstrip())
            break

    # Scan from the start to skip loading boilerplate
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

    user_lines = cleaned[i:]
    if not user_lines:
        return ""

    code = _dedent("\n".join(user_lines)).strip()

    # Strip trailing "return df"
    if code.endswith("\nreturn df"):
        code = code[: -len("\nreturn df")].rstrip()
    elif code == "return df":
        return ""

    return code


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def _extract_function_bodies(
    source: str,
    tree: ast.Module | None = None,
) -> dict[str, str]:
    """Extract raw source of each function body, keyed by function name.

    Args:
        source: The raw source code (needed for line extraction).
        tree: Pre-parsed AST tree.  If *None*, the source is parsed
            internally.  Passing a tree avoids a redundant ``ast.parse()``.
    """
    if tree is None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return {}

    source_lines = source.splitlines()
    bodies: dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            if node.body:
                start = node.body[0].lineno - 1
                end = node.body[-1].end_lineno or (start + 1)
                bodies[node.name] = "\n".join(source_lines[start:end])

    return bodies


def _extract_connect_calls(
    tree: ast.Module,
    receiver: str = "pipeline",
) -> list[tuple[str, str]]:
    """Find all <receiver>.connect("src", "tgt") calls at module level."""
    connects: list[tuple[str, str]] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue

        # Check for <receiver>.connect(...)
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "connect":
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == receiver):
            continue

        args = call.args
        if len(args) >= 2:
            src = _eval_ast_literal(args[0])
            tgt = _eval_ast_literal(args[1])
            if isinstance(src, str) and isinstance(tgt, str):
                connects.append((src, tgt))

    return connects


# ---------------------------------------------------------------------------
# Graph building helpers
# ---------------------------------------------------------------------------


def _copy_config_keys(
    config: dict[str, Any],
    kwargs: dict[str, Any],
    keys: tuple[str, ...] | list[str],
) -> None:
    """Copy matching keys from *kwargs* into *config*.

    Only keys that exist in *kwargs* are copied; missing keys are
    silently skipped.  This is a convenience helper to eliminate the
    repeated ``for key in KEYS: if key in kwargs: config[key] = kwargs[key]``
    pattern in ``_build_node_config``.
    """
    for key in keys:
        if key in kwargs:
            config[key] = kwargs[key]


def _build_node_config(
    node_type: str,
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
) -> dict[str, Any]:
    """Build the config dict for a node given its type and decorator kwargs."""
    config: dict[str, Any] = {}
    if node_type == NodeType.API_INPUT:
        config["path"] = decorator_kwargs.get("path", "")
        if decorator_kwargs.get("row_id_column"):
            config["row_id_column"] = decorator_kwargs["row_id_column"]
    elif node_type == NodeType.DATA_SOURCE:
        config["path"] = decorator_kwargs.get("path", "")
        if "table" in decorator_kwargs:
            config["sourceType"] = "databricks"
            config["table"] = decorator_kwargs["table"]
            if "http_path" in decorator_kwargs:
                config["http_path"] = decorator_kwargs["http_path"]
            if "query" in decorator_kwargs:
                config["query"] = decorator_kwargs["query"]
        else:
            config["sourceType"] = "flat_file"
    elif node_type == NodeType.LIVE_SWITCH:
        config["input_scenario_map"] = decorator_kwargs.get("input_scenario_map", {})
        config["inputs"] = param_names
    elif node_type == NodeType.MODEL_SCORE:
        for key in MODEL_SCORE_CONFIG_KEYS:
            # Decorator uses snake_case "source_type"; config uses camelCase "sourceType"
            decorator_key = "source_type" if key == "sourceType" else key
            if decorator_key in decorator_kwargs:
                config[key] = decorator_kwargs[decorator_key]
        # Only extract user post-processing code (after sentinel), not the
        # auto-generated scoring scaffolding that codegen produces.
        config["code"] = _extract_model_score_user_code(body) if body else ""
    elif node_type == NodeType.BANDING:
        if "factors" in decorator_kwargs:
            # Multi-factor format: factors=[{...}, {...}]
            raw_factors = decorator_kwargs["factors"]
            config["factors"] = [
                {
                    "banding": f.get("banding", "continuous"),
                    "column": f.get("column", ""),
                    "outputColumn": f.get("output_column", f.get("outputColumn", "")),
                    "rules": f.get("rules", []),
                    "default": f.get("default"),
                }
                for f in (raw_factors if isinstance(raw_factors, list) else [])
            ]
        else:
            # Single-factor format → wrap into factors array
            config["factors"] = [
                {
                    "banding": decorator_kwargs.get("banding", "continuous"),
                    "column": decorator_kwargs.get("column", ""),
                    "outputColumn": decorator_kwargs.get("output_column", ""),
                    "rules": decorator_kwargs.get("rules", []),
                    "default": decorator_kwargs.get("default"),
                }
            ]
    elif node_type == NodeType.RATING_STEP:
        if "tables" in decorator_kwargs:
            raw_tables = decorator_kwargs["tables"]
            config["tables"] = [
                {
                    "name": t.get("name", ""),
                    "factors": t.get("factors", []),
                    "outputColumn": t.get("output_column", t.get("outputColumn", "")),
                    "defaultValue": t.get("default_value", t.get("defaultValue")),
                    "entries": t.get("entries", []),
                }
                for t in (raw_tables if isinstance(raw_tables, list) else [])
            ]
        else:
            config["tables"] = []
        for t in config["tables"]:
            if not isinstance(t.get("entries"), list):
                t["entries"] = []
            if not isinstance(t.get("factors"), list):
                t["factors"] = []
        op = decorator_kwargs.get("operation", decorator_kwargs.get("op"))
        if op:
            config["operation"] = str(op)
        combined = decorator_kwargs.get(
            "combined_column",
            decorator_kwargs.get("combinedColumn"),
        )
        if combined:
            config["combinedColumn"] = str(combined)
    elif node_type == NodeType.SCENARIO_EXPANDER:
        _copy_config_keys(config, decorator_kwargs, SCENARIO_EXPANDER_CONFIG_KEYS)
        config["code"] = _extract_source_user_code(body) if body else ""
    elif node_type == NodeType.OPTIMISER_APPLY:
        _copy_config_keys(config, decorator_kwargs, OPTIMISER_APPLY_CONFIG_KEYS)
    elif node_type == NodeType.OPTIMISER:
        _copy_config_keys(config, decorator_kwargs, OPTIMISER_CONFIG_KEYS)
    elif node_type == NodeType.MODELLING:
        _copy_config_keys(config, decorator_kwargs, MODELLING_CONFIG_KEYS)
    elif node_type == NodeType.CONSTANT:
        raw_values = decorator_kwargs.get("values", [])
        config["values"] = [
            {"name": v.get("name", ""), "value": str(v.get("value", ""))}
            for v in (raw_values if isinstance(raw_values, list) else [])
        ]
    elif node_type == NodeType.DATA_SINK:
        config["path"] = decorator_kwargs.get("path", decorator_kwargs.get("sink", ""))
        config["format"] = decorator_kwargs.get("format", "parquet")
    elif node_type == NodeType.EXTERNAL_FILE:
        config["path"] = decorator_kwargs.get("path", decorator_kwargs.get("external", ""))
        config["fileType"] = decorator_kwargs.get("file_type", "pickle")
        if config["fileType"] == "catboost":
            config["modelClass"] = decorator_kwargs.get("model_class", "classifier")
        config["code"] = _extract_external_user_code(body, param_names) if body else ""
    elif node_type == NodeType.OUTPUT:
        config["fields"] = decorator_kwargs.get("fields", [])
    else:
        # transform
        config["code"] = _extract_user_code(body, param_names) if body else ""
        if "selected_columns" in decorator_kwargs:
            config["selected_columns"] = decorator_kwargs["selected_columns"]
    # Instance reference (works for any node type)
    if "instance_of" in decorator_kwargs:
        config["instanceOf"] = decorator_kwargs["instance_of"]
    elif "of" in decorator_kwargs:
        config["instanceOf"] = decorator_kwargs["of"]
    return config


def _build_edges(
    raw_nodes: list[dict],
    explicit_connect_pairs: list[tuple[str, str]],
) -> list[GraphEdge]:
    """Build GraphEdge models from explicit connect() calls and implicit param-name matching."""
    node_names = {n["func_name"] for n in raw_nodes}
    edges: list[GraphEdge] = []
    explicit_edges: set[tuple[str, str]] = set()

    for src, tgt in explicit_connect_pairs:
        if src in node_names and tgt in node_names:
            explicit_edges.add((src, tgt))
            edges.append(GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt))

    # Implicit edges from parameter names matching node names
    for node_info in raw_nodes:
        for param in node_info["param_names"]:
            if param in node_names and param != node_info["func_name"]:
                pair = (param, node_info["func_name"])
                if pair not in explicit_edges:
                    edges.append(
                        GraphEdge(
                            id=f"e_{pair[0]}_{pair[1]}",
                            source=pair[0],
                            target=pair[1],
                        )
                    )

    # Fallback: if still no edges, infer linear chain from definition order
    if not edges and len(raw_nodes) > 1:
        for i in range(1, len(raw_nodes)):
            src = raw_nodes[i - 1]["func_name"]
            tgt = raw_nodes[i]["func_name"]
            edges.append(GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt))

    return edges


def _build_rf_nodes(raw_nodes: list[dict], x_spacing: int = 300) -> list[GraphNode]:
    """Convert raw parsed nodes into GraphNode Pydantic models."""
    return [
        GraphNode(
            id=n["func_name"],
            type=n["node_type"],
            position={"x": i * x_spacing, "y": 0},
            data=NodeData(
                label=n["func_name"],
                description=n["description"],
                nodeType=n["node_type"],
                config=n["config"],
            ),
        )
        for i, n in enumerate(raw_nodes)
    ]


# ---------------------------------------------------------------------------
# Meta extraction
# ---------------------------------------------------------------------------


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
            val = _eval_ast_literal(call.args[0])
            if isinstance(val, str):
                name = val

        for kw in call.keywords:
            if kw.arg == "description":
                val = _eval_ast_literal(kw.value)
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


# ---------------------------------------------------------------------------
# Config resolution (shared by parser.py and _parser_submodels.py)
# ---------------------------------------------------------------------------


def _resolve_node_config(
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
    n_params: int,
    base_dir: Path | None,
    func_name: str = "",
    explicit_node_type: NodeType | None = None,
) -> tuple[NodeType, dict[str, Any]]:
    """Resolve node type and config from decorator kwargs.

    Handles both the new ``config="config/…/name.json"`` format (external
    JSON file) and the legacy inline-kwargs format.

    The *explicit_node_type* is provided by the type-specific decorator
    (e.g. ``@pipeline.polars``) and is used directly as the node type.

    Returns ``(node_type, config_dict)``.
    """
    # Work on a copy to avoid mutating the caller's dict.
    decorator_kwargs = dict(decorator_kwargs)
    node_type = explicit_node_type or NodeType.POLARS
    config_ref = decorator_kwargs.pop("config", None)
    if config_ref:
        normalised_ref = config_ref.replace("\\", "/")
        base = base_dir or Path.cwd()
        try:
            loaded = load_node_config(normalised_ref, base_dir=base)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            raise ConfigError(
                "Failed to load node config; check that the path exists and "
                "contains valid JSON, or create the file.",
                original_path=config_ref,
                normalised_path=normalised_ref,
                func_name=func_name,
                base_dir=str(base),
                cause=str(exc),
            ) from exc
        config = dict(loaded)
        # Code lives in the .py function body, not in the JSON file
        if node_type == NodeType.MODEL_SCORE:
            config["code"] = _extract_model_score_user_code(body) if body else ""
        elif node_type == NodeType.EXTERNAL_FILE:
            config["code"] = _extract_external_user_code(body, param_names) if body else ""
        elif node_type == NodeType.POLARS:
            config["code"] = _extract_user_code(body, param_names) if body else ""
        elif node_type == NodeType.DATA_SOURCE:
            config["code"] = _extract_source_user_code(body) if body else ""
        elif node_type == NodeType.SCENARIO_EXPANDER:
            config["code"] = _extract_source_user_code(body) if body else ""
    else:
        config = _build_node_config(node_type, decorator_kwargs, body, param_names)

    warn_unrecognized_config_keys(node_type, config)
    return node_type, config
