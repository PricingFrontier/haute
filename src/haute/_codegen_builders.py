"""Codegen builder registry — per-type ``_gen_*`` functions that emit
Python source for each :class:`NodeType`.

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

SUBMODEL / SUBMODEL_PORT are registered with codegen builders that raise
loudly.  By the time codegen dispatches on a node, the submodel boundary
has already been handled — either ``graph_to_code_multi`` emitted the
submodel as its own file and skipped the placeholder in ``root_nodes``
(see ``codegen.graph_to_code_multi``), or ``flatten_graph`` removed the
placeholder entirely.  Reaching these builders means the preflight filter
has broken; fail loudly rather than emitting silent passthrough code.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from haute._config_io import config_path_for_node
from haute._graph_utils import _sanitize_func_name
from haute._registry import (
    CodegenFn,
)
from haute._registry import (
    register_codegen as _register_codegen_in_registry,
)
from haute._registry import (
    set_codegen as _set_codegen_in_registry,
)
from haute._types import (
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    GraphNode,
    NodeType,
)
from haute.errors import ConfigError
from haute.graph_utils import _resolve_sink_path

# ---------------------------------------------------------------------------
# String-safety helpers — double-quoted Python literals with proper escaping.
# ---------------------------------------------------------------------------


def _safe_str(value: str) -> str:
    """Produce a double-quoted Python string literal with proper escaping.

    Escapes backslashes, double quotes, and newlines to prevent code
    injection via config values.
    """
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _safe_path(value: str) -> str:
    """Produce a double-quoted Python path literal with forward slashes.

    Normalises Windows backslashes to forward slashes before escaping,
    so generated code is cross-platform.  Python and Polars handle
    forward slashes on all operating systems.
    """
    return _safe_str(value.replace("\\", "/"))


def _is_absolute_path(path: str) -> bool:
    """Check whether *path* looks absolute (Unix or Windows)."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return True
    return False


def _portable_path_expr(path: str) -> str:
    """Return a Python expression that resolves *path* relative to ``__file__``.

    Absolute paths are left as-is (returned as a quoted string literal).
    Relative paths become ``Path(__file__).parent / "rel/path"``.
    """
    if _is_absolute_path(path):
        return _safe_path(path)
    return f"Path(__file__).parent / {_safe_path(path)}"


# ---------------------------------------------------------------------------
# Common helpers shared across builders.
# ---------------------------------------------------------------------------


def _build_extra_kwargs(config: dict, keys: tuple[str, ...]) -> list[str]:
    """Build ``"key={value!r}"`` decorator kwarg strings for present config keys.

    Skips keys whose value is ``None``, ``""``, or ``[]``.
    """
    parts: list[str] = []
    for key in keys:
        val = config.get(key)
        if val is not None and val != "" and val != []:
            parts.append(f"{key}={val!r}")
    return parts


def _build_params(source_names: list[str]) -> str:
    """Build the function parameter string from upstream node names."""
    if source_names:
        return ", ".join(f"{s}: pl.LazyFrame" for s in source_names)
    return "df: pl.LazyFrame"


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
       pathological *desc* in the Phase 5 Wave 9D matrix.

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
    - Curly braces are doubled to survive ``str.format`` interpolation
      in the per-type templates.
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
    # Double curly braces so the templates' ``str.format`` doesn't
    # interpret them as placeholders.
    escaped = escaped.replace("{", "{{").replace("}", "}}")
    return escaped


def _common_node_fields(node: GraphNode) -> tuple[str, str, dict]:
    """Extract the (func_name, description, config) triple used by every builder.

    The description is sanitised so that triple-quotes, backslash escape
    sequences, and multi-line content cannot break the generated docstring
    AND so that ``ast.get_docstring`` round-trips it bit-for-bit.  An
    intentionally-empty description is preserved as-is (it becomes
    ``\"\"\"\"\"\"``) — we do not substitute a ``<label> node`` placeholder,
    because that would make the round-trip lossy.
    """
    data = node.data
    return (
        _sanitize_func_name(data.label),
        _sanitize_description(data.description),
        data.config,
    )


def _first_source(source_names: list[str]) -> str:
    """Return the first upstream name, defaulting to ``"df"``."""
    return source_names[0] if source_names else "df"


# ---------------------------------------------------------------------------
# Template fragments for each node type
# ---------------------------------------------------------------------------


def _api_input_template(path: str) -> str:
    """Return the API input template string for the given file path.

    JSON/JSONL files use ``read_json_flat``, CSV uses ``scan_csv``,
    everything else (parquet / flat) uses ``scan_parquet``.
    """
    lower = path.lower()
    if lower.endswith((".json", ".jsonl")):
        body = (
            "    from pathlib import Path\n"
            "    from haute._json_flatten import read_json_flat\n"
            "    return read_json_flat({portable_path}, config_path={config_path_repr})"
        )
    elif lower.endswith(".csv"):
        body = "    from pathlib import Path\n    return pl.scan_csv({portable_path})"
    else:
        body = "    from pathlib import Path\n    return pl.scan_parquet({portable_path})"

    return (
        "@pipeline.api_input(path={path_repr}{row_id_kw})\n"
        "def {func_name}() -> pl.LazyFrame:\n"
        '    """{description}"""\n' + body + "\n"
    )


_LIVE_SWITCH = '''\
@pipeline.live_switch(input_scenario_map={input_scenario_map_repr})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {active_param}
'''

_MODEL_SCORE = '''\
@pipeline.model_score({decorator_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from pathlib import Path
    from haute.graph_utils import score_from_config
    base = str(Path(__file__).parent)
    return score_from_config({first_param}, config={config_path_repr}, base_dir=base)
'''


def _data_source_parts(config: dict) -> tuple[str, str, str]:
    """Return (decorator, imports, load_expr) for a DataSource node.

    *imports* is empty for flat files, non-empty for Databricks.
    *load_expr* is the bare expression (e.g. ``pl.scan_parquet("path")``).
    """
    source_type = config.get("sourceType", "flat_file")
    path = config.get("path", "")

    if source_type == "databricks":
        table = config.get("table", "catalog.schema.table")
        http_path = config.get("http_path", "")
        query = config.get("query", "")
        parts = [f"table={_safe_str(table)}"]
        if http_path:
            parts.append(f"http_path={http_path!r}")
        if query:
            parts.append(f"query={query!r}")
        decorator = f"@pipeline.data_source({', '.join(parts)})"
        imports = "    from haute._databricks_io import read_cached_table\n"
        load_expr = f"read_cached_table({_safe_str(table)})"
    elif path.lower().endswith(".csv"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = "    from pathlib import Path\n"
        load_expr = f"pl.scan_csv({_portable_path_expr(path)})"
    elif path.lower().endswith(".jsonl"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = "    from pathlib import Path\n"
        load_expr = f"pl.scan_ndjson({_portable_path_expr(path)})"
    elif path.lower().endswith(".json"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = "    from pathlib import Path\n"
        load_expr = f"pl.read_json({_portable_path_expr(path)}).lazy()"
    else:
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = "    from pathlib import Path\n"
        load_expr = f"pl.scan_parquet({_portable_path_expr(path)})"

    return decorator, imports, load_expr


_BANDING_SINGLE = '''\
@pipeline.banding(banding={banding_repr}, column={column_repr},
               output_column={output_column_repr}{rules_kw}{default_kw})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_BANDING_MULTI = '''\
@pipeline.banding(factors={factors_repr})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_RATING_STEP = '''\
@pipeline.rating_step(tables={tables_repr}{extra_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_SINK_PARQUET = '''\
@pipeline.data_sink(path={path_repr}, format="parquet")
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from pathlib import Path
    from haute._polars_utils import safe_sink
    safe_sink({first}, {portable_path})
    return {first}
'''

_SINK_CSV = '''\
@pipeline.data_sink(path={path_repr}, format="csv")
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from pathlib import Path
    from haute._polars_utils import safe_sink
    safe_sink({first}, {portable_path}, fmt="csv")
    return {first}
'''

_SCENARIO_EXPANDER = '''\
@pipeline.scenario_expander({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_OPTIMISER = '''\
@pipeline.optimiser({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_OPTIMISER_APPLY = '''\
@pipeline.optimiser_apply({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_MODELLING = '''\
@pipeline.modelling({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_CONSTANT = '''\
@pipeline.constant(values={values_repr})
def {func_name}() -> pl.LazyFrame:
    """{description}"""
    return pl.LazyFrame({data_dict})
'''

_EXTERNAL = '''\
@pipeline.external_file(path={path_repr}, file_type={file_type_repr}{extra_dec})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from pathlib import Path
    from haute.graph_utils import load_external_object
    obj = load_external_object({portable_path}, {file_type_repr}{extra_load})
{body}
'''


def _wrap_external_code(code: str) -> str:
    """Wrap external file user code: indent each line and append ``return df``.

    Unlike transforms, external file code is multi-statement - the user
    is responsible for assigning a Polars DataFrame to ``df``.
    """
    code = code.strip()
    if not code:
        return "    return df"
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    return f"{indented}\n    return df"


def _wrap_user_code(code: str, source_names: list[str]) -> str:
    """Wrap user code into indented function body lines.

    User code must assign to ``df``.  We indent it and append ``return df``.
    """
    code = code.strip()
    if not code:
        first = source_names[0] if source_names else "df"
        return f"    return {first}"

    indented = "\n".join(f"    {line}" for line in code.splitlines())
    return f"{indented}\n    return df"


# ---------------------------------------------------------------------------
# Codegen builder callable signature.
# ---------------------------------------------------------------------------

#: Builder signature: (node, source_names) -> generated Python code string.
CodegenBuilder = Callable[[GraphNode, list[str]], str]


def _register_codegen(node_type: NodeType) -> Callable[[CodegenBuilder], CodegenBuilder]:
    """Decorator to register a codegen builder for *node_type*.

    Writes into the unified :data:`haute._registry.NODE_REGISTRY`.
    """
    return _register_codegen_in_registry(node_type)


def _assign_codegen(node_type: NodeType, fn: CodegenBuilder) -> None:
    """Assign a pre-built codegen builder (no decorator).

    Used for builders produced by :func:`_make_passthrough_builder` — the
    decorator form would need an extra wrapper to apply at definition time.
    """
    _set_codegen_in_registry(node_type, fn)


# ---------------------------------------------------------------------------
# Per-type builders
# ---------------------------------------------------------------------------


@_register_codegen(NodeType.API_INPUT)
def _gen_api_input(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    path = config.get("path", "")
    row_id_kw = ""
    if config.get("row_id_column"):
        row_id_kw = f", row_id_column={_safe_str(config['row_id_column'])}"
    cfg_path = config_path_for_node(node.data.nodeType, func_name).as_posix()
    template = _api_input_template(path)
    return template.format(
        func_name=func_name,
        description=description,
        path_repr=_safe_path(path),
        portable_path=_portable_path_expr(path),
        row_id_kw=row_id_kw,
        config_path=cfg_path,
        config_path_repr=_safe_path(cfg_path),
    )


@_register_codegen(NodeType.LIVE_SWITCH)
def _gen_live_switch(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    params = ", ".join(f"{s}: pl.LazyFrame" for s in source_names)
    input_scenario_map: dict[str, str] = config.get("input_scenario_map", {})
    first_param = _first_source(source_names)
    # Generated code always routes to the "live" input
    active_param = first_param
    for inp, scn in input_scenario_map.items():
        if scn == "live" and inp in source_names:
            active_param = inp
            break
    return _LIVE_SWITCH.format(
        func_name=func_name,
        description=description,
        params=params,
        input_scenario_map_repr=repr(input_scenario_map),
        active_param=active_param,
    )


@_register_codegen(NodeType.DATA_SOURCE)
def _gen_data_source(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    code = (config.get("code") or "").strip()
    decorator, imports, load_expr = _data_source_parts(config)

    if not code:
        return (
            f"{decorator}\n"
            f"def {func_name}() -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"{imports}"
            f"    df = {load_expr}\n"
            f"    return df\n"
        )

    user_body = _wrap_user_code(code, ["df"])
    return (
        f"{decorator}\n"
        f"def {func_name}() -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"{imports}"
        f"    df = {load_expr}\n"
        f"{user_body}\n"
    )


@_register_codegen(NodeType.CONSTANT)
def _gen_constant(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    raw_values = config.get("values", []) or []
    # Build the repr for the decorator kwarg
    values_repr = repr(
        [{"name": v.get("name", ""), "value": v.get("value", "")} for v in raw_values]
    )
    # Build a dict literal for the LazyFrame constructor
    data_pairs: list[str] = []
    for v in raw_values:
        name = v.get("name", "col")
        val = v.get("value", "")
        # Try numeric coercion for the code literal
        try:
            num = float(val)
            if math.isnan(num):
                data_pairs.append(f"{_safe_str(name)}: [float('nan')]")
            elif math.isinf(num):
                sign = "" if num > 0 else "-"
                data_pairs.append(f"{_safe_str(name)}: [float('{sign}inf')]")
            else:
                data_pairs.append(f"{_safe_str(name)}: [{num!r}]")
        except (ValueError, TypeError):
            data_pairs.append(f"{_safe_str(name)}: [{_safe_str(val)}]")
    data_dict = "{" + ", ".join(data_pairs) + "}" if data_pairs else '{"constant": [0]}'
    return _CONSTANT.format(
        func_name=func_name,
        description=description,
        values_repr=values_repr,
        data_dict=data_dict,
    )


@_register_codegen(NodeType.MODEL_SCORE)
def _gen_model_score(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    source_type = config.get("sourceType", "run")
    task_val = config.get("task", "regression")
    output_column = config.get("output_column", "prediction")
    user_code = (config.get("code") or "").strip()
    params = _build_params(source_names)
    first_param = _first_source(source_names)
    cfg_path = config_path_for_node(NodeType.MODEL_SCORE, func_name).as_posix()

    # Build decorator kwargs (post-processed to config= by _node_to_code)
    if source_type == "registered":
        reg_model = config.get("registered_model", "")
        ver = config.get("version", "latest")
        decorator_kwargs = (
            f'source_type="registered", '
            f"registered_model={reg_model!r}, version={ver!r}, "
            f"task={task_val!r}, output_column={output_column!r}"
        )
    else:
        rid = config.get("run_id", "")
        apath = config.get("artifact_path", "")
        rname = config.get("run_name", "")
        exp_name = config.get("experiment_name", "")
        exp_id = config.get("experiment_id", "")
        decorator_kwargs = (
            f'source_type="run", '
            f"run_id={rid!r}, artifact_path={apath!r}, "
            f"task={task_val!r}, output_column={output_column!r}"
        )
        if rname:
            decorator_kwargs += f", run_name={rname!r}"
        if exp_name:
            decorator_kwargs += f", experiment_name={exp_name!r}"
        if exp_id:
            decorator_kwargs += f", experiment_id={exp_id!r}"

    if user_code:
        indented = "\n".join(f"    {line}" for line in user_code.splitlines())
        return (
            f"@pipeline.model_score({decorator_kwargs})\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"    from pathlib import Path\n"
            f"    from haute.graph_utils import score_from_config\n"
            f"    base = str(Path(__file__).parent)\n"
            f"    result = score_from_config(\n"
            f"        {first_param}, config={_safe_path(cfg_path)},\n"
            f"        base_dir=base,\n"
            f"    )\n"
            f"{indented}\n"
            f"    return result\n"
        )

    return _MODEL_SCORE.format(
        func_name=func_name,
        description=description,
        params=params,
        first_param=first_param,
        decorator_kwargs=decorator_kwargs,
        config_path_repr=_safe_path(cfg_path),
    )


@_register_codegen(NodeType.BANDING)
def _gen_banding(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    factors = config.get("factors", []) or []
    params = _build_params(source_names)
    if len(factors) == 1:
        f = factors[0]
        banding = f.get("banding", "continuous")
        column = f.get("column", "")
        output_column = f.get("outputColumn", "")
        rules = f.get("rules", []) or []
        default = f.get("default")
        rules_kw = f", rules={rules!r}" if rules else ""
        default_kw = f", default={default!r}" if default is not None else ""
        first = _first_source(source_names)
        return _BANDING_SINGLE.format(
            func_name=func_name,
            description=description,
            banding_repr=_safe_str(banding),
            column_repr=_safe_str(column),
            output_column_repr=_safe_str(output_column),
            rules_kw=rules_kw,
            default_kw=default_kw,
            params=params,
            first=first,
        )
    else:
        # Multi-factor: emit factors list with output_column key for decorator
        emit_factors = []
        for f in factors:
            ef: dict = {
                "banding": f.get("banding", "continuous"),
                "column": f.get("column", ""),
                "output_column": f.get("outputColumn", ""),
                "rules": f.get("rules", []),
            }
            if f.get("default") is not None:
                ef["default"] = f["default"]
            emit_factors.append(ef)
        first = _first_source(source_names)
        return _BANDING_MULTI.format(
            func_name=func_name,
            description=description,
            factors_repr=repr(emit_factors),
            params=params,
            first=first,
        )


@_register_codegen(NodeType.RATING_STEP)
def _gen_rating_step(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    tables = config.get("tables", []) or []
    params = _build_params(source_names)
    emit_tables = []
    for t in tables:
        et: dict = {
            "name": t.get("name", ""),
            "factors": t.get("factors", []),
            "output_column": t.get("outputColumn", ""),
            "entries": t.get("entries", []),
        }
        if t.get("defaultValue") is not None:
            et["default_value"] = t["defaultValue"]
        emit_tables.append(et)
    extra_parts: list[str] = []
    op = config.get("operation")
    if op and op != "multiply":
        extra_parts.append(f"operation={op!r}")
    combined = config.get("combinedColumn")
    if combined:
        extra_parts.append(f"combined_column={combined!r}")
    extra_kwargs = (", " + ", ".join(extra_parts)) if extra_parts else ""
    first = _first_source(source_names)
    return _RATING_STEP.format(
        func_name=func_name,
        description=description,
        tables_repr=repr(emit_tables),
        params=params,
        first=first,
        extra_kwargs=extra_kwargs,
    )


def _make_passthrough_builder(
    template: str,
    config_keys: tuple[str, ...],
) -> CodegenBuilder:
    """Factory for codegen builders that share the same passthrough pattern.

    Each returned builder extracts common node fields, builds extra kwargs from
    the given *config_keys*, and formats the *template*.  This eliminates the
    duplication across scenario-expander, optimiser, optimiser-apply, and
    modelling builders.
    """

    def builder(node: GraphNode, source_names: list[str]) -> str:
        func_name, description, config = _common_node_fields(node)
        params = _build_params(source_names)
        first = _first_source(source_names)
        extra_parts = _build_extra_kwargs(config, config_keys)
        dec_kwargs = ", ".join(extra_parts)
        return template.format(
            func_name=func_name,
            description=description,
            params=params,
            first=first,
            dec_kwargs=dec_kwargs,
        )

    return builder


@_register_codegen(NodeType.SCENARIO_EXPANDER)
def _gen_scenario_expander(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    params = _build_params(source_names)
    first = _first_source(source_names)
    extra_parts = _build_extra_kwargs(config, SCENARIO_EXPANDER_CONFIG_KEYS)
    dec_kwargs = ", ".join(extra_parts)
    code = (config.get("code") or "").strip()

    if not code:
        return _SCENARIO_EXPANDER.format(
            func_name=func_name,
            description=description,
            params=params,
            first=first,
            dec_kwargs=dec_kwargs,
        )

    user_body = _wrap_user_code(code, ["df"])
    return (
        f"@pipeline.scenario_expander({dec_kwargs})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    df = {first}\n"
        f"{user_body}\n"
    )


# Factory-produced builders: assigned directly because the factory returns an
# already-bound callable — no decorator needed.
_assign_codegen(
    NodeType.OPTIMISER,
    _make_passthrough_builder(_OPTIMISER, OPTIMISER_CONFIG_KEYS),
)
_assign_codegen(
    NodeType.OPTIMISER_APPLY,
    _make_passthrough_builder(_OPTIMISER_APPLY, OPTIMISER_APPLY_CONFIG_KEYS),
)
_assign_codegen(
    NodeType.MODELLING,
    _make_passthrough_builder(_MODELLING, MODELLING_CONFIG_KEYS),
)


@_register_codegen(NodeType.EXTERNAL_FILE)
def _gen_external_file(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    path = config.get("path", "model.pkl")
    file_type = config.get("fileType", "pickle")
    code = (config.get("code") or "").strip()
    params = _build_params(source_names)
    body = _wrap_external_code(code)
    extra_dec = ""
    extra_load = ""
    if file_type == "catboost":
        model_class = config.get("modelClass", "classifier")
        extra_dec = f", model_class={_safe_str(model_class)}"
        extra_load = f", {_safe_str(model_class)}"
    return _EXTERNAL.format(
        func_name=func_name,
        description=description,
        path_repr=_safe_path(path),
        portable_path=_portable_path_expr(path),
        file_type_repr=_safe_str(file_type),
        params=params,
        body=body,
        extra_dec=extra_dec,
        extra_load=extra_load,
    )


@_register_codegen(NodeType.DATA_SINK)
def _gen_data_sink(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    path = config.get("path", "output.parquet")
    fmt = config.get("format", "parquet")
    path = _resolve_sink_path(path, fmt)
    params = _build_params(source_names)
    first = _first_source(source_names)
    template = _SINK_CSV if fmt == "csv" else _SINK_PARQUET
    return template.format(
        func_name=func_name,
        description=description,
        path_repr=_safe_path(path),
        portable_path=_portable_path_expr(path),
        params=params,
        first=first,
    )


@_register_codegen(NodeType.OUTPUT)
def _gen_output(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    fields = config.get("fields", []) or []
    params = _build_params(source_names)
    first = _first_source(source_names)
    dec_parts: list[str] = []
    if fields:
        dec_parts.append(f"fields={fields!r}")
        select_args = ", ".join(_safe_str(f) for f in fields)
        body = f"    return {first}.select({select_args})"
    else:
        body = f"    return {first}"
    dec = ", ".join(dec_parts)
    return (
        f"@pipeline.output({dec})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"{body}\n"
    )


@_register_codegen(NodeType.POLARS)
def _gen_transform(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    code = (config.get("code") or "").strip()
    first = source_names[0] if source_names else "df"
    params = _build_params(source_names)
    sel = config.get("selected_columns", [])

    if sel:
        decorator = f"@pipeline.polars(selected_columns={sel!r})"
    else:
        decorator = "@pipeline.polars"

    if not code:
        if not source_names:
            raise ConfigError(
                "polars transform has no user code and no upstream sources; "
                "either connect an input or provide code.",
                node_id=node.id,
                label=node.data.label,
            )
        if len(source_names) > 1:
            raise ConfigError(
                "polars transform has no user code but multiple upstream "
                "sources; add code that explicitly combines the inputs or "
                "reduce to a single upstream.",
                node_id=node.id,
                label=node.data.label,
                sources=list(source_names),
            )
        return (
            f"{decorator}\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"    return {source_names[0]}\n"
        )

    body = _wrap_user_code(code, ["df"])
    return (
        f"{decorator}\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    df = {first}\n"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Submodel sentinels — registered so the unified registry is fully populated,
# but they fail loudly if codegen ever dispatches on them.  See module-level
# docstring for the rationale.
# ---------------------------------------------------------------------------


def _gen_submodel_placeholder_unreachable(
    node: GraphNode,
    source_names: list[str],
) -> str:
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
# Public re-exports — symbols callers may still import from this module.
# ---------------------------------------------------------------------------


__all__ = [
    # Type aliases
    "CodegenBuilder",
    "CodegenFn",
    # Helpers
    "_build_extra_kwargs",
    "_build_params",
    "_common_node_fields",
    "_first_source",
    "_is_absolute_path",
    "_make_passthrough_builder",
    "_portable_path_expr",
    "_safe_path",
    "_safe_str",
    "_sanitize_description",
    "_wrap_external_code",
    "_wrap_user_code",
    # Per-type builders (imported by some tests)
    "_gen_api_input",
    "_gen_banding",
    "_gen_constant",
    "_gen_data_sink",
    "_gen_data_source",
    "_gen_external_file",
    "_gen_live_switch",
    "_gen_model_score",
    "_gen_output",
    "_gen_rating_step",
    "_gen_scenario_expander",
    "_gen_transform",
    "_register_codegen",
]
