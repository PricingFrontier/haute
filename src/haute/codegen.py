"""Code generator: graph JSON → valid pipeline .py file."""

from __future__ import annotations

import math
from collections.abc import Callable

from haute._config_io import config_path_for_node, has_config_folder
from haute._logging import get_logger
from haute._types import (
    MODELLING_CONFIG_KEYS,
    NODE_TYPE_TO_DECORATOR,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
)
from haute.graph_utils import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _resolve_sink_path,
    _sanitize_func_name,
    build_instance_mapping,
    topo_sort_ids,
)

logger = get_logger(component="codegen")


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


__all__ = [
    "graph_to_code",
    "graph_to_code_multi",
]


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
    """Sanitize a description string for safe use inside triple-quoted docstrings.

    Replaces ``\"\"\"`` with ``'''`` so that the generated docstring
    ``\"\"\"{description}\"\"\"`` remains syntactically valid Python.

    Also handles two edge cases that would break the closing ``\"\"\"``:

    - **Trailing double-quotes** merge with the closing ``\"\"\"`` (e.g.
      ``\"\"\"{desc}\"\"\"`` becomes ``\"\"\"foo\"\"\"\"`` which is invalid).
      Every trailing ``"`` is backslash-escaped.  If the text *before* the
      trailing quotes already ends with an odd number of backslashes, an
      extra backslash is inserted so the escape isn't "absorbed".
    - **Trailing backslash** would escape the closing quote.  An extra
      backslash is appended to make the count even.
    """
    desc = desc.replace("{", "{{").replace("}", "}}")
    desc = desc.replace('"""', "'''")

    # ── Trailing double-quotes ────────────────────────────────────────
    stripped = desc.rstrip('"')
    n_quotes = len(desc) - len(stripped)
    if n_quotes > 0:
        # If the text before the quotes ends with an odd number of
        # backslashes, our first \" would be parsed as an escaped
        # backslash + bare quote.  Pad to make the backslash count even.
        core = stripped.rstrip("\\")
        n_backslashes = len(stripped) - len(core)
        if n_backslashes % 2 == 1:
            stripped = stripped + "\\"
        desc = stripped + '\\"' * n_quotes
    else:
        # ── Trailing backslash (no trailing quotes) ───────────────────
        core = desc.rstrip("\\")
        n_backslashes = len(desc) - len(core)
        if n_backslashes % 2 == 1:
            desc = desc + "\\"

    return desc


def _common_node_fields(node: GraphNode) -> tuple[str, str, dict]:
    """Extract the (func_name, description, config) triple used by every builder.

    The description is sanitized so that triple-quotes cannot break the
    generated docstring.
    """
    data = node.data
    raw_desc = data.description or f"{data.label} node"
    return (
        _sanitize_func_name(data.label),
        _sanitize_description(raw_desc),
        data.config,
    )


def _first_source(source_names: list[str]) -> str:
    """Return the first upstream name, defaulting to ``"df"``."""
    return source_names[0] if source_names else "df"


# Template fragments for each node type


def _api_input_template(path: str) -> str:
    """Return the API input template string for the given file path.

    JSON/JSONL files use ``read_json_flat``, CSV uses ``scan_csv``,
    everything else (parquet / flat) uses ``scan_parquet``.
    """
    lower = path.lower()
    if lower.endswith((".json", ".jsonl")):
        body = (
            "    from haute._json_flatten import read_json_flat\n"
            "    return read_json_flat({path_repr}, config_path={config_path_repr})"
        )
    elif lower.endswith(".csv"):
        body = "    return pl.scan_csv({path_repr})"
    else:
        body = "    return pl.scan_parquet({path_repr})"

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
            parts.append(f"http_path={repr(http_path)}")
        if query:
            parts.append(f"query={repr(query)}")
        decorator = f"@pipeline.data_source({', '.join(parts)})"
        imports = "    from haute._databricks_io import read_cached_table\n"
        load_expr = f"read_cached_table({_safe_str(table)})"
    elif path.lower().endswith(".csv"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = ""
        load_expr = f"pl.scan_csv({_safe_path(path)})"
    elif path.lower().endswith(".jsonl"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = ""
        load_expr = f"pl.scan_ndjson({_safe_path(path)})"
    elif path.lower().endswith(".json"):
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = ""
        load_expr = f"pl.read_json({_safe_path(path)}).lazy()"
    else:
        decorator = f"@pipeline.data_source(path={_safe_path(path)})"
        imports = ""
        load_expr = f"pl.scan_parquet({_safe_path(path)})"

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
    from haute._polars_utils import safe_sink
    safe_sink({first}, {path_repr})
    return {first}
'''

_SINK_CSV = '''\
@pipeline.data_sink(path={path_repr}, format="csv")
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute._polars_utils import safe_sink
    safe_sink({first}, {path_repr}, fmt="csv")
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
    from haute.graph_utils import load_external_object
    obj = load_external_object({path_repr}, {file_type_repr}{extra_load})
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


def _node_to_code(node: GraphNode, source_names: list[str] | None = None) -> str:
    """Generate code for a single node.

    Delegates to ``_generate_node_code`` for the type-specific body, then
    replaces the decorator line with a ``config=`` file reference for node
    types that use external JSON config files.
    """
    code = _generate_node_code(node, source_names)

    node_type = node.data.nodeType
    if has_config_folder(node_type):
        func_name = _sanitize_func_name(node.data.label)
        cfg_path = config_path_for_node(node_type, func_name).as_posix()
        try:
            dec_name = NODE_TYPE_TO_DECORATOR.get(node_type, "polars")
            def_idx = code.index("\ndef ")
            code = f"@pipeline.{dec_name}(config={_safe_path(cfg_path)})" + code[def_idx:]
        except ValueError:
            logger.warning("no_def_in_generated_code", node=node.data.label)
    return code


# ---------------------------------------------------------------------------
# Codegen dispatch table — mirrors _NODE_BUILDERS in executor.py
# ---------------------------------------------------------------------------

#: Builder signature: (node, source_names) -> generated Python code string.
CodegenBuilder = Callable[[GraphNode, list[str]], str]

_CODEGEN_BUILDERS: dict[NodeType, CodegenBuilder] = {}


def _register_codegen(node_type: NodeType) -> Callable[[CodegenBuilder], CodegenBuilder]:
    """Decorator to register a codegen builder for a given NodeType."""

    def decorator(fn: CodegenBuilder) -> CodegenBuilder:
        _CODEGEN_BUILDERS[node_type] = fn
        return fn

    return decorator


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
            f"    return {load_expr}\n"
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

    # Build decorator kwargs (post-processed to config= by _post_process_node_code)
    if source_type == "registered":
        reg_model = config.get("registered_model", "")
        ver = config.get("version", "latest")
        decorator_kwargs = (
            f'source_type="registered", '
            f"registered_model={repr(reg_model)}, version={repr(ver)}, "
            f"task={repr(task_val)}, output_column={repr(output_column)}"
        )
    else:
        rid = config.get("run_id", "")
        apath = config.get("artifact_path", "")
        rname = config.get("run_name", "")
        exp_name = config.get("experiment_name", "")
        exp_id = config.get("experiment_id", "")
        decorator_kwargs = (
            f'source_type="run", '
            f"run_id={repr(rid)}, artifact_path={repr(apath)}, "
            f"task={repr(task_val)}, output_column={repr(output_column)}"
        )
        if rname:
            decorator_kwargs += f", run_name={repr(rname)}"
        if exp_name:
            decorator_kwargs += f", experiment_name={repr(exp_name)}"
        if exp_id:
            decorator_kwargs += f", experiment_id={repr(exp_id)}"

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
        default_kw = f", default={repr(default)}" if default is not None else ""
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


_CODEGEN_BUILDERS[NodeType.OPTIMISER] = _make_passthrough_builder(
    _OPTIMISER,
    OPTIMISER_CONFIG_KEYS,
)
_CODEGEN_BUILDERS[NodeType.OPTIMISER_APPLY] = _make_passthrough_builder(
    _OPTIMISER_APPLY,
    OPTIMISER_APPLY_CONFIG_KEYS,
)
_CODEGEN_BUILDERS[NodeType.MODELLING] = _make_passthrough_builder(
    _MODELLING,
    MODELLING_CONFIG_KEYS,
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
    params = _build_params(source_names)
    body = _wrap_user_code(code, source_names)
    sel = config.get("selected_columns", [])

    if sel:
        decorator = f"@pipeline.polars(selected_columns={sel!r})"
    else:
        decorator = "@pipeline.polars"

    return (
        f"{decorator}\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _generate_node_code(node: GraphNode, source_names: list[str] | None = None) -> str:
    """Generate code for a single node (with legacy inline decorator).

    source_names: sanitized function names of upstream nodes (used as param names).
    """
    if source_names is None:
        source_names = []

    builder = _CODEGEN_BUILDERS.get(node.data.nodeType)
    if builder is not None:
        return builder(node, source_names)

    # Fallback: treat unknown types as transforms
    logger.warning(
        "unknown_node_type_fallback",
        node_type=str(node.data.nodeType),
        node_id=node.id,
        label=node.data.label,
    )
    return _gen_transform(node, source_names)


def _instance_to_code(
    node: GraphNode,
    original_func_name: str,
    source_names: list[str] | None = None,
    orig_source_names: list[str] | None = None,
) -> str:
    """Generate code for an instance node that delegates to the original function.

    When *orig_source_names* is provided the wrapper emits keyword arguments so
    that each original parameter receives the correct instance input regardless
    of edge ordering.
    """
    data = node.data
    label = data.label
    description = _sanitize_description(data.description or f"Instance of {original_func_name}")
    func_name = _sanitize_func_name(label)

    if source_names is None:
        source_names = []

    params = _build_params(source_names)

    # Prefer explicit inputMapping from config (set via the UI)
    explicit_map = data.config.get("inputMapping")

    if orig_source_names and source_names:
        explicit = dict(explicit_map) if explicit_map and isinstance(explicit_map, dict) else None
        mapping = build_instance_mapping(orig_source_names, source_names, explicit)
        args = ", ".join(f"{orig}={mapping[orig]}" for orig in orig_source_names if orig in mapping)
    else:
        args = ", ".join(source_names) if source_names else "df"

    return (
        f'@pipeline.instance(of="{original_func_name}")\n'
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    return {original_func_name}({args})\n"
    )


def _topo_sort(nodes: list[GraphNode], edges: list[GraphEdge]) -> list[GraphNode]:
    """Sort nodes in topological order based on edges."""
    node_map = {n.id: n for n in nodes}
    order = topo_sort_ids(list(node_map.keys()), edges)
    return [node_map[nid] for nid in order if nid in node_map]


def _emit_preserved_blocks(preserved_blocks: list[str]) -> list[str]:
    """Wrap each preserved block in start/end markers and return as lines."""
    lines: list[str] = []
    for block in preserved_blocks:
        lines.append("# haute:preserve-start")
        lines.append(block)
        lines.append("# haute:preserve-end")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Shared helpers for graph_to_code_multi
# ---------------------------------------------------------------------------


def _build_id_to_func(sorted_nodes: list[GraphNode]) -> dict[str, str]:
    """Map node.id → sanitized function name for sorted nodes."""
    return {node.id: _sanitize_func_name(node.data.label) for node in sorted_nodes}


def _build_node_sources(
    edges: list[GraphEdge],
    id_to_func: dict[str, str],
) -> dict[str, list[str]]:
    """Map target node ID → list of source function names."""
    sources: dict[str, list[str]] = {}
    for edge in edges:
        src_name = id_to_func.get(edge.source, edge.source)
        sources.setdefault(edge.target, []).append(src_name)
    return sources


def _build_instance_of_map(sorted_nodes: list[GraphNode]) -> dict[str, str]:
    """Map instance node ID → original node ID for nodes with ``instanceOf``."""
    result: dict[str, str] = {}
    for node in sorted_nodes:
        ref = node.data.config.get("instanceOf")
        if ref:
            result[node.id] = ref
    return result


#: Type alias for a function that generates code for a single node.
_NodeCodeFn = Callable[[GraphNode, list[str] | None], str]


def _generate_pipeline_lines(
    *,
    kind: str,
    name: str,
    description: str,
    preamble: str,
    sorted_nodes: list[GraphNode],
    id_to_func: dict[str, str],
    node_sources: dict[str, list[str]],
    connect_pairs: list[tuple[str, str]],
    preserved_blocks: list[str] | None = None,
    submodel_imports: list[str] | None = None,
    node_to_code_fn: _NodeCodeFn = _node_to_code,
    dedup_connects: bool = False,
    obj_name: str = "pipeline",
) -> list[str]:
    """Generate the body of a pipeline or submodel file as a list of lines.

    Shared by the no-submodel and multi-submodel paths in
    ``graph_to_code_multi`` to eliminate duplicated header / node / connect
    generation logic.
    """
    # ── Header ────────────────────────────────────────────────────────
    if kind == "submodel":
        lines = [
            f'"""Submodel: {name.replace(chr(34), "")}"""',
            "",
            "import polars as pl",
            "import haute",
            "",
            "",
            f"{obj_name} = haute.Submodel({_safe_str(name)})",
            "",
            "",
        ]
    else:
        lines = [
            f'"""Pipeline: {name}"""',
            "",
            "import polars as pl",
            "import haute",
        ]
        if preamble.strip():
            lines.append("")
            lines.append(preamble.rstrip())
        lines += [
            "",
            f"{obj_name} = haute.Pipeline({_safe_str(name)}, description={description!r})",
            "",
            "",
        ]

    # ── Preserved blocks ──────────────────────────────────────────────
    if preserved_blocks:
        lines.extend(_emit_preserved_blocks(preserved_blocks))
        lines.append("")

    # ── Nodes: originals then instances ───────────────────────────────
    instance_of_map = _build_instance_of_map(sorted_nodes)
    originals = [n for n in sorted_nodes if n.id not in instance_of_map]
    instances = [n for n in sorted_nodes if n.id in instance_of_map]

    for node in originals:
        srcs = node_sources.get(node.id, [])
        lines.append(node_to_code_fn(node, srcs))
        lines.append("")

    for node in instances:
        srcs = node_sources.get(node.id, [])
        orig_id = instance_of_map[node.id]
        orig_func = id_to_func.get(orig_id, orig_id)
        orig_src = node_sources.get(orig_id, [])
        inst_code = _instance_to_code(
            node,
            orig_func,
            source_names=srcs,
            orig_source_names=orig_src,
        )
        # Inside submodel files the decorator prefix must be @submodel.*
        if obj_name != "pipeline":
            inst_code = inst_code.replace("@pipeline.", f"@{obj_name}.", 1)
        lines.append(inst_code)
        lines.append("")

    # ── Submodel imports (pipeline files only) ────────────────────────
    if submodel_imports:
        for imp in submodel_imports:
            lines.append(imp)
        lines.append("")

    # ── Connect calls ─────────────────────────────────────────────────
    if connect_pairs:
        lines.append("")
        lines.append("# Wire nodes together - edges define data flow")
        if dedup_connects:
            seen: set[tuple[str, str]] = set()
            for src_func, tgt_func in connect_pairs:
                if (src_func, tgt_func) not in seen:
                    seen.add((src_func, tgt_func))
                    lines.append(f'{obj_name}.connect("{src_func}", "{tgt_func}")')
        else:
            for src_func, tgt_func in connect_pairs:
                lines.append(f'{obj_name}.connect("{src_func}", "{tgt_func}")')
        lines.append("")

    return lines


def graph_to_code(
    graph: PipelineGraph,
    pipeline_name: str = "main",
    description: str = "",
    preamble: str = "",
    preserved_blocks: list[str] | None = None,
) -> str:
    """Convert a React Flow graph to a valid haute pipeline .py file.

    Delegates to :func:`graph_to_code_multi` and returns the single generated
    file's code.
    """
    files = graph_to_code_multi(
        graph,
        pipeline_name=pipeline_name,
        description=description,
        preamble=preamble,
        preserved_blocks=preserved_blocks,
    )
    # graph_to_code_multi returns {filename: code}; extract the sole value.
    return next(iter(files.values()))


def _submodel_node_to_code(node: GraphNode, source_names: list[str] | None = None) -> str:
    """Generate code for a single node inside a submodel file.

    Identical to ``_node_to_code`` but uses ``@submodel.<type>`` instead of
    ``@pipeline.<type>``.
    """
    code = _node_to_code(node, source_names=source_names)
    return code.replace("@pipeline.", "@submodel.", 1)


def graph_to_code_multi(
    graph: PipelineGraph,
    pipeline_name: str = "main",
    description: str = "",
    preamble: str = "",
    source_file: str = "",
    preserved_blocks: list[str] | None = None,
) -> dict[str, str]:
    """Generate code for a pipeline with submodels.

    Returns a dict mapping relative file path → generated Python code.
    E.g. ``{"main.py": "...", "modules/model_scoring.py": "..."}``.

    If the graph has no submodels, the result contains only the main file.
    """
    submodels = graph.submodels or {}

    if not submodels:
        # No submodels — single-file output
        main_key = source_file or f"{pipeline_name}.py"
        nodes = graph.nodes
        edges = graph.edges
        sorted_nodes = _topo_sort(nodes, edges)

        id_to_func = _build_id_to_func(sorted_nodes)
        node_sources = _build_node_sources(edges, id_to_func)

        all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks

        # Build connect pairs from edges
        connect_pairs = [
            (id_to_func.get(e.source, e.source), id_to_func.get(e.target, e.target)) for e in edges
        ]

        lines = _generate_pipeline_lines(
            kind="pipeline",
            name=pipeline_name,
            description=description,
            preamble=preamble,
            sorted_nodes=sorted_nodes,
            id_to_func=id_to_func,
            node_sources=node_sources,
            connect_pairs=connect_pairs,
            preserved_blocks=all_preserved or None,
            node_to_code_fn=_node_to_code,
        )

        logger.info("code_generated", pipeline_name=pipeline_name, node_count=len(sorted_nodes))
        return {main_key: "\n".join(lines)}

    # ── Separate nodes into root-level vs submodel children ──────────
    all_child_ids: set[str] = set()
    submodel_node_ids: set[str] = set()
    for sm_name, sm_meta in submodels.items():
        all_child_ids.update(sm_meta.get("childNodeIds", []))
        submodel_node_ids.add(f"submodel__{sm_name}")

    nodes = graph.nodes
    edges = graph.edges

    # Root-level nodes: not children and not the submodel placeholder itself
    root_nodes = [n for n in nodes if n.id not in all_child_ids and n.id not in submodel_node_ids]

    # Root-level edges: only between root-level nodes OR crossing submodel boundary
    root_node_ids = {n.id for n in root_nodes}

    # Build id → func_name for root nodes (needed by submodel cross-boundary resolution)
    root_id_to_func = _build_id_to_func(root_nodes)

    # ── Generate submodel files ──────────────────────────────────────
    files: dict[str, str] = {}

    for sm_name, sm_meta in submodels.items():
        sm_graph = sm_meta.get("graph", {})
        sm_file = sm_meta.get("file", f"modules/{sm_name}.py").replace("\\", "/")
        raw_nodes = sm_graph.get("nodes", [])
        raw_edges = sm_graph.get("edges", [])
        sm_nodes = [GraphNode.model_validate(n) if isinstance(n, dict) else n for n in raw_nodes]
        sm_edges = [GraphEdge.model_validate(e) if isinstance(e, dict) else e for e in raw_edges]

        sorted_sm_nodes = _topo_sort(sm_nodes, sm_edges)
        sm_id_to_func = _build_id_to_func(sorted_sm_nodes)
        sm_node_sources = _build_node_sources(sm_edges, sm_id_to_func)

        # Also include cross-boundary inputs from parent graph edges
        sm_node_id = f"submodel__{sm_name}"
        sm_child_ids = {n.id for n in sm_nodes}
        for edge in edges:
            if edge.target == sm_node_id and edge.targetHandle:
                child_id = edge.targetHandle.removeprefix("in__")
                if child_id in sm_child_ids:
                    src_name = root_id_to_func.get(edge.source, _sanitize_func_name(edge.source))
                    sm_node_sources.setdefault(child_id, []).append(src_name)

        # Build connect pairs from internal edges
        sm_connect_pairs = [
            (sm_id_to_func.get(e.source, e.source), sm_id_to_func.get(e.target, e.target))
            for e in sm_edges
        ]

        sm_lines = _generate_pipeline_lines(
            kind="submodel",
            name=sm_name,
            description="",
            preamble="",
            sorted_nodes=sorted_sm_nodes,
            id_to_func=sm_id_to_func,
            node_sources=sm_node_sources,
            connect_pairs=sm_connect_pairs,
            node_to_code_fn=_submodel_node_to_code,
            obj_name="submodel",
        )

        files[sm_file] = "\n".join(sm_lines)

    # ── Generate main pipeline file ──────────────────────────────────

    sorted_root = (
        _topo_sort(
            root_nodes,
            [e for e in edges if e.source in root_node_ids and e.target in root_node_ids],
        )
        if root_nodes
        else []
    )

    # Also map submodel child node IDs to func names (for edge generation)
    for sm_name, sm_meta in submodels.items():
        sm_graph = sm_meta.get("graph", {})
        for n in sm_graph.get("nodes", []):
            nd = GraphNode.model_validate(n) if isinstance(n, dict) else n
            root_id_to_func[nd.id] = _sanitize_func_name(nd.data.label)

    # Build source names per root node from root-level edges AND
    # cross-boundary edges (resolving submodel handles to child node names).
    root_node_sources: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.source
        tgt = edge.target
        sh = edge.sourceHandle or ""
        th = edge.targetHandle or ""

        # Resolve submodel handles to actual child node names
        actual_src = src
        if src in submodel_node_ids and sh:
            actual_src = sh.removeprefix("out__")
        actual_tgt = tgt
        if tgt in submodel_node_ids and th:
            actual_tgt = th.removeprefix("in__")

        # Only care about edges feeding into root nodes
        if actual_tgt not in root_node_ids:
            continue
        src_name = root_id_to_func.get(actual_src, _sanitize_func_name(actual_src))
        root_node_sources.setdefault(actual_tgt, []).append(src_name)

    # Build connect pairs for ALL edges (cross-boundary use real node names)
    root_connect_pairs: list[tuple[str, str]] = []
    for edge in edges:
        src = edge.source
        tgt = edge.target
        sh = edge.sourceHandle or ""
        th = edge.targetHandle or ""

        # Resolve submodel handles to actual node names
        actual_src = src
        if src in submodel_node_ids and sh:
            actual_src = sh.removeprefix("out__")
        actual_tgt = tgt
        if tgt in submodel_node_ids and th:
            actual_tgt = th.removeprefix("in__")

        src_func = root_id_to_func.get(actual_src, _sanitize_func_name(actual_src))
        tgt_func = root_id_to_func.get(actual_tgt, _sanitize_func_name(actual_tgt))
        root_connect_pairs.append((src_func, tgt_func))

    # Submodel import lines
    sm_imports = []
    for sm_name, sm_meta in submodels.items():
        sm_path = sm_meta.get("file", f"modules/{sm_name}.py").replace("\\", "/")
        sm_imports.append(f'pipeline.submodel("{sm_path}")')

    all_preserved = preserved_blocks if preserved_blocks is not None else graph.preserved_blocks

    main_lines = _generate_pipeline_lines(
        kind="pipeline",
        name=pipeline_name,
        description=description,
        preamble=preamble,
        sorted_nodes=sorted_root,
        id_to_func=root_id_to_func,
        node_sources=root_node_sources,
        connect_pairs=root_connect_pairs,
        preserved_blocks=all_preserved or None,
        submodel_imports=sm_imports,
        node_to_code_fn=_node_to_code,
        dedup_connects=True,
    )

    main_key = source_file or f"{pipeline_name}.py"
    files[main_key] = "\n".join(main_lines)
    return files
