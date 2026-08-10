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
from typing import Any

from haute._code_extraction import INCOMPLETE_TRANSFORM_BODY, POLARS_OUTPUT_DECLARATION
from haute._config_io import config_path_for_node
from haute._edge_join import build_edge_join_kwargs, edge_join_config_to_decorator_kwargs
from haute._explore_overview import validate_explore_overview
from haute._graph_utils import (
    _sanitize_func_name,
    duplicate_input_names,
    resolve_input_mapping_names,
)
from haute._rating import _normalise_combined_outputs
from haute._rating_step_config import normalise_rating_tables
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
from haute.errors import ConfigError, ParseError

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


def _build_params(source_names: list[str], *, default_df: bool = True) -> str:
    """Build the function parameter string from supplied per-edge names.

    The graph orchestrator validates duplicate names before reaching a
    builder.  This helper also asserts that upstream invariant defensively;
    inventing suffixes here would make generated signatures disagree with the
    executor's edge-derived bindings.
    """
    names = source_names or (["df"] if default_df else [])
    duplicates = duplicate_input_names(names)
    assert not duplicates, f"duplicate codegen input name(s): {duplicates!r}"
    return ", ".join(f"{name}: pl.LazyFrame" for name in names)


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
       pathological *desc* covered by the docstring round-trip tests.

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
    - Curly braces are left untouched.  The sanitized value is always
      supplied to the per-type templates as a ``str.format`` *keyword
      argument* (or an f-string value) — never spliced into template
      text — and ``str.format`` does not re-scan substituted values
      for replacement fields.  Doubling braces here landed the doubled
      braces literally in the emitted docstring, which the parser read
      back doubled, growing the description on every save/load cycle.
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
    # and prematurely close the enclosing """ literal.  Braces are NOT
    # escaped — see the docstring above.
    escaped = escaped.replace('"', '\\"')
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


def _format_kwarg_source(key: str, value: Any) -> str:
    """Format a Python keyword argument with stable string quoting."""
    if isinstance(value, str):
        return f"{key}={_safe_str(value)}"
    return f"{key}={value!r}"


# ---------------------------------------------------------------------------
# Template fragments for each node type
# ---------------------------------------------------------------------------


_LIVE_SWITCH = '''\
@pipeline.live_switch(input_scenario_map={input_scenario_map_repr})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute._model_scorer import _scenario_ctx
    from haute.graph_utils import select_live_switch_input
    return select_live_switch_input(
        {input_scenario_map_repr}, _scenario_ctx.get(),
        {frames_dict}, {input_order_repr}, switch={switch_repr},
    )
'''

_MODEL_SCORE = '''\
@pipeline.model_score({decorator_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import score_from_config
    base = str(_HAUTE_CONFIG_BASE)
    df = score_from_config({first_param}, config={config_path_repr}, base_dir=base)
    return df
'''


def _retained_api_input_template(config_path: str) -> str:
    """Emit an API input whose loader is entirely driven by its sidecar."""
    return f'''\
@pipeline.api_input()
def {{func_name}}() -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    """{{description}}"""
    from haute.graph_utils import resolve_api_input_from_config
    return resolve_api_input_from_config(
        {_safe_path(config_path)}, base_dir=_HAUTE_CONFIG_BASE
    )
'''


_BANDING_SINGLE = '''\
@pipeline.banding(banding={banding_repr}, column={column_repr},
               output_column={output_column_repr}{rules_kw}{default_kw})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import apply_banding_from_config
    base = _HAUTE_CONFIG_BASE
    df = apply_banding_from_config({first}, {config_path_repr}, base_dir=base)
    return df
'''

_BANDING_MULTI = '''\
@pipeline.banding(factors={factors_repr})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import apply_banding_from_config
    base = _HAUTE_CONFIG_BASE
    df = apply_banding_from_config({first}, {config_path_repr}, base_dir=base)
    return df
'''

_RATING_STEP = '''\
@pipeline.rating_step(tables={tables_repr}{extra_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import apply_rating_step_from_config
    base = _HAUTE_CONFIG_BASE
    df = apply_rating_step_from_config({first}, {config_path_repr}, base_dir=base)
    return df
'''

_SCENARIO_EXPANDER = '''\
@pipeline.scenario_expander({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import expand_scenarios_from_config
    base = _HAUTE_CONFIG_BASE
    return expand_scenarios_from_config({first}, {config_path_repr}, base_dir=base)
'''

# optimiser / modelling are genuine passthroughs in the executor (preview /
# training happen via dedicated API routes), so a first-frame passthrough
# body is runtime-equivalent — they are NOT registered as behavioural.
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
    from haute.graph_utils import apply_optimiser_apply_from_config
    base = _HAUTE_CONFIG_BASE
    return apply_optimiser_apply_from_config(
        {args}, config={config_path_repr}, base_dir=base,
        source_names={source_names_repr}, source_ids={source_ids_repr},
    )
'''

_MODELLING = '''\
@pipeline.modelling({dec_kwargs})
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    return {first}
'''

_EXPLORE = '''\
@pipeline.explore({decorator_args})
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

_RETAINED_EXTERNAL = '''\
@pipeline.external_file()
def {func_name}({params}) -> pl.LazyFrame:
    """{description}"""
    from haute.graph_utils import load_external_object_from_config
    obj = load_external_object_from_config(
        {config_path_repr}, base_dir=_HAUTE_CONFIG_BASE
    )
{body}
'''


def _wrap_external_code(code: str, *, input_name: str | None = None) -> str:
    """Wrap external-file code around its documented implicit ``df`` frame.

    Generated external functions bind their first input to ``df`` before the
    user-authored multi-statement body, then append ``return df``.
    """
    code = code.strip()
    lines = [f"    df = {input_name}"] if input_name else []
    if code:
        lines.extend(f"    {line}" for line in code.splitlines())
    lines.append("    return df")
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Per-type builders
# ---------------------------------------------------------------------------


@_register_codegen(NodeType.API_INPUT)
def _gen_api_input(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    cfg_path = config_path_for_node(node.data.nodeType, func_name).as_posix()
    template = _retained_api_input_template(cfg_path)
    return template.format(
        func_name=func_name,
        description=description,
    )


@_register_codegen(NodeType.LIVE_SWITCH)
def _gen_live_switch(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    params = ", ".join(f"{s}: pl.LazyFrame" for s in source_names)
    input_scenario_map: dict[str, str] = config.get("input_scenario_map", {})
    # The body reads the active runtime source from the shared scenario
    # contextvar (set by Pipeline.run/score) and delegates to the same
    # selector the executor uses, so a standalone file routes the SAME branch
    # instead of hard-wiring the "live" input.
    frames_dict = "{" + ", ".join(f"{s!r}: {s}" for s in source_names) + "}"
    return _LIVE_SWITCH.format(
        func_name=func_name,
        description=description,
        params=params,
        input_scenario_map_repr=repr(input_scenario_map),
        frames_dict=frames_dict,
        input_order_repr=repr(list(source_names)),
        switch_repr=repr(func_name),
    )


@_register_codegen(NodeType.CONSTANT)
def _gen_constant(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    raw_values = config.get("values", []) or []
    # Build the repr for the decorator kwarg
    values_repr = repr(
        [{"name": v.get("name") or "", "value": v.get("value", "")} for v in raw_values]
    )
    # Build a dict literal for the LazyFrame constructor.  Mirror the executor
    # (_build_constant): a missing/empty name is skipped (not emitted as a
    # default "col" column), and a None value becomes a null literal rather
    # than raising AttributeError on ``_safe_str(None)``.
    data_pairs: list[str] = []
    for v in raw_values:
        name = v.get("name") or ""
        if not name:
            continue
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
            if val is None:
                data_pairs.append(f"{_safe_str(name)}: [None]")
            else:
                data_pairs.append(f"{_safe_str(name)}: [{_safe_str(str(val))}]")
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
    user_code = str(config.get("code") or "").strip()
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
        return (
            f"@pipeline.model_score({decorator_kwargs})\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"    from haute.graph_utils import score_from_config\n"
            f"    base = str(_HAUTE_CONFIG_BASE)\n"
            f"    df = score_from_config(\n"
            f"        {first_param}, config={_safe_path(cfg_path)},\n"
            f"        base_dir=base,\n"
            f"    )\n"
            f"{_wrap_user_code(user_code, ['df'])}\n"
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
    first = _first_source(source_names)
    # The body applies the sidecar config at runtime — the same pattern
    # rating bodies use — so a standalone `pipeline.run()` of the saved
    # file bands instead of silently passing the frame through.
    config_path_repr = _safe_path(config_path_for_node(NodeType.BANDING, func_name).as_posix())
    if len(factors) == 1:
        f = factors[0]
        banding = f.get("banding", "continuous")
        column = f.get("column", "")
        output_column = f.get("outputColumn", "")
        rules = f.get("rules", []) or []
        default = f.get("default")
        rules_kw = f", rules={rules!r}" if rules else ""
        default_kw = f", default={default!r}" if default is not None else ""
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
            config_path_repr=config_path_repr,
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
        return _BANDING_MULTI.format(
            func_name=func_name,
            description=description,
            factors_repr=repr(emit_factors),
            params=params,
            first=first,
            config_path_repr=config_path_repr,
        )


@_register_codegen(NodeType.RATING_STEP)
def _gen_rating_step(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    tables = normalise_rating_tables(config)
    params = _build_params(source_names)
    first = _first_source(source_names)
    code = str(config.get("code") or "").strip()
    emit_tables = []
    for t in tables:
        et: dict = {
            "factors": t.get("factors", []),
            "output_column": t.get("outputColumn", ""),
            "entries": t.get("entries", []),
        }
        if t.get("defaultValue") is not None:
            et["default_value"] = t["defaultValue"]
        emit_tables.append(et)
    extra_parts: list[str] = []
    combined_outputs = _normalise_combined_outputs(config)
    if combined_outputs:
        decorator_outputs = [
            {
                "output_column": output["outputColumn"],
                "operation": output["operation"],
                "base_value": output["baseValue"],
            }
            for output in combined_outputs
        ]
        extra_parts.append(f"combined_outputs={decorator_outputs!r}")
    extra_kwargs = (", " + ", ".join(extra_parts)) if extra_parts else ""
    config_path_repr = _safe_path(config_path_for_node(NodeType.RATING_STEP, func_name).as_posix())
    if code:
        user_body = _wrap_user_code(code, ["df"])
        return (
            f"@pipeline.rating_step(tables={emit_tables!r}{extra_kwargs})\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"    from haute.graph_utils import apply_rating_step_from_config\n"
            f"    base = _HAUTE_CONFIG_BASE\n"
            f"    df = apply_rating_step_from_config({first}, {config_path_repr}, base_dir=base)\n"
            f"{user_body}\n"
        )
    return _RATING_STEP.format(
        func_name=func_name,
        description=description,
        tables_repr=repr(emit_tables),
        params=params,
        first=first,
        extra_kwargs=extra_kwargs,
        config_path_repr=config_path_repr,
    )


def _passthrough_decorator_kwargs(config: dict, keys: tuple[str, ...]) -> str:
    """Build the ``", ".join(...)`` decorator kwargs for a flat-config node.

    Shared by the pure-passthrough builders (optimiser, modelling) and the
    stateful scenario-expander so the decorator-kwarg construction lives in
    one place.  The decorator kwargs survive only in the raw builder output;
    ``_node_to_code`` rewrites the decorator to a ``config=`` sidecar path for
    every node type that has a config folder.
    """
    return ", ".join(_build_extra_kwargs(config, keys))


@_register_codegen(NodeType.SCENARIO_EXPANDER)
def _gen_scenario_expander(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    params = _build_params(source_names)
    first = _first_source(source_names)
    dec_kwargs = _passthrough_decorator_kwargs(config, SCENARIO_EXPANDER_CONFIG_KEYS)
    config_path_repr = _safe_path(
        config_path_for_node(NodeType.SCENARIO_EXPANDER, func_name).as_posix()
    )
    code = str(config.get("code") or "").strip()

    # The body applies the sidecar config at runtime — the same shared helper
    # the executor calls — so a standalone ``pipeline.run()`` expands the
    # scenario grid instead of silently passing the frame through.
    if not code:
        return _SCENARIO_EXPANDER.format(
            func_name=func_name,
            description=description,
            params=params,
            first=first,
            dec_kwargs=dec_kwargs,
            config_path_repr=config_path_repr,
        )

    user_body = _wrap_user_code(code, ["df"])
    return (
        f"@pipeline.scenario_expander({dec_kwargs})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    from haute.graph_utils import expand_scenarios_from_config\n"
        f"    base = _HAUTE_CONFIG_BASE\n"
        f"    df = expand_scenarios_from_config({first}, {config_path_repr}, base_dir=base)\n"
        f"{user_body}\n"
    )


@_register_codegen(NodeType.OPTIMISER)
def _gen_optimiser(node: GraphNode, source_names: list[str]) -> str:
    # Genuine passthrough in the executor (solving happens via the optimiser
    # solve route) — the first-frame body is runtime-equivalent.
    func_name, description, config = _common_node_fields(node)
    return _OPTIMISER.format(
        func_name=func_name,
        description=description,
        params=_build_params(source_names),
        first=_first_source(source_names),
        dec_kwargs=_passthrough_decorator_kwargs(config, OPTIMISER_CONFIG_KEYS),
    )


@_register_codegen(NodeType.MODELLING)
def _gen_modelling(node: GraphNode, source_names: list[str]) -> str:
    # Genuine passthrough in the executor (training happens via the modelling
    # train route) — the first-frame body is runtime-equivalent.
    func_name, description, config = _common_node_fields(node)
    return _MODELLING.format(
        func_name=func_name,
        description=description,
        params=_build_params(source_names),
        first=_first_source(source_names),
        dec_kwargs=_passthrough_decorator_kwargs(config, MODELLING_CONFIG_KEYS),
    )


@_register_codegen(NodeType.OPTIMISER_APPLY)
def _gen_optimiser_apply(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    dec_kwargs = _passthrough_decorator_kwargs(config, OPTIMISER_APPLY_CONFIG_KEYS)
    param_names = source_names or ["df"]
    # Frames are passed positionally; source_names/source_ids let the shared
    # helper resolve the configured ratebook_input.  In the sidecar,
    # ratebook_input is remapped to the source function name (see
    # _config_io._remap_config_ids_for_saved_graph), so the parameter-name
    # list doubles as the id list — selection is positional either way.
    args = ", ".join(param_names)
    names_repr = repr(list(source_names))
    return _OPTIMISER_APPLY.format(
        func_name=func_name,
        description=description,
        params=_build_params(source_names),
        dec_kwargs=dec_kwargs,
        args=args,
        config_path_repr=_safe_path(
            config_path_for_node(NodeType.OPTIMISER_APPLY, func_name).as_posix()
        ),
        source_names_repr=names_repr,
        source_ids_repr=names_repr,
    )


# Explore uses a single nested-dict decorator kwarg (``overview={...}``)
# instead of flat snake_case kwargs (the pattern modelling/optimiser/scenario
# expander use via ``*_CONFIG_KEYS`` tuples).  The UI evolves Overview-card
# toggles independently of backend keys, so an opaque dict insulates the
# codegen from churn.
def _explore_decorator_args(overview: Any) -> str:
    """Build the decorator argument string for ``@pipeline.explore(...)``.

    Returns ``""`` when *overview* is empty (so the decorator stays bare),
    and ``"overview={...}"`` otherwise — using :func:`repr` on a plain
    ``dict`` so the emitted form is a valid Python literal that round-trips
    through :mod:`ast`.
    """
    overview = validate_explore_overview(overview, context="explore node config")
    if not overview:
        return ""
    return f"overview={overview!r}"


@_register_codegen(NodeType.EXPLORE)
def _gen_explore(node: GraphNode, source_names: list[str]) -> str:
    if len(source_names) != 1:
        raise ParseError(
            "Explore nodes must have exactly one incoming edge.",
            node_id=node.id,
            node_label=node.data.label,
            incoming_count=len(source_names),
            incoming_sources=source_names,
        )
    func_name, description, config = _common_node_fields(node)
    params = _build_params(source_names)
    first = source_names[0]
    code = str(config.get("code") or "").strip()
    overview = config["overview"] if "overview" in config else {}
    decorator_args = _explore_decorator_args(overview)
    if code:
        user_body = _wrap_user_code(code, ["df"])
        return (
            f"@pipeline.explore({decorator_args})\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"    df = {first}\n"
            f"{user_body}\n"
        )
    return _EXPLORE.format(
        func_name=func_name,
        description=description,
        params=params,
        first=first,
        decorator_args=decorator_args,
    )


@_register_codegen(NodeType.EXTERNAL_FILE)
def _gen_external_file(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    code = str(config.get("code") or "").strip()
    params = _build_params(source_names)
    body = _wrap_external_code(code, input_name=_first_source(source_names))
    cfg_path = config_path_for_node(node.data.nodeType, func_name).as_posix()
    return _RETAINED_EXTERNAL.format(
        func_name=func_name,
        description=description,
        config_path_repr=_safe_path(cfg_path),
        params=params,
        body=body,
    )


@_register_codegen(NodeType.DATA_INPUT)
def _gen_data_input(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    # The config (format/mode/source fields/arguments) lives in the JSON
    # sidecar like every other config-folder node; the decorator is rewritten
    # to ``config=`` by codegen, and the body executes the same registry
    # invocation the canvas executor uses, anchored to the pipeline dir.
    cfg_path = config_path_for_node(node.data.nodeType, func_name).as_posix()
    code = str(config.get("code") or "").strip()
    body = _wrap_external_code(code)
    return (
        f"@pipeline.data_input(config={_safe_path(cfg_path)})\n"
        f"def {func_name}() -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    from haute._project import get_project_root\n"
        f"    from haute.graph_utils import resolve_data_input_from_config\n"
        f"    project_root = get_project_root(_HAUTE_CONFIG_BASE)\n"
        f"    df = resolve_data_input_from_config(\n"
        f"        {_safe_path(cfg_path)}, base_dir=_HAUTE_CONFIG_BASE, "
        f"project_root=project_root\n"
        f"    )\n"
        f"{body}\n"
    )


@_register_codegen(NodeType.DATA_OUTPUT)
def _gen_data_output(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, _config = _common_node_fields(node)
    params = _build_params(source_names)
    first = _first_source(source_names)
    return (
        f"@pipeline.data_output(config="
        f"{_safe_path(config_path_for_node(node.data.nodeType, func_name).as_posix())})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    return {first}\n"
    )


@_register_codegen(NodeType.OUTPUT)
def _gen_output(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, _config = _common_node_fields(node)
    param_names = source_names or ["df"]
    params = _build_params(source_names)
    # v2: the outputMapping lives in a JSON schema mapping (like every other
    # config-folder node — apiInput, dataInput, …), referenced by
    # ``config=``. The body routes through the SAME assembler the canvas
    # executor calls, so a standalone ``pipeline.run()`` / ``score()``
    # returns the assembled response document — not a passthrough of the
    # raw upstream frame.
    cfg_path = config_path_for_node(node.data.nodeType, func_name).as_posix()
    args = "".join(f"        {p},\n" for p in param_names)
    return (
        f"@pipeline.output(config={_safe_path(cfg_path)})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    from haute.graph_utils import assemble_output_from_config\n"
        f"    return assemble_output_from_config(\n"
        f"{args}"
        f"        config={_safe_path(cfg_path)},\n"
        f"        base_dir=_HAUTE_CONFIG_BASE,\n"
        f"        source_names={param_names!r},\n"
        f"    )\n"
    )


@_register_codegen(NodeType.POLARS)
def _gen_transform(node: GraphNode, source_names: list[str]) -> str:
    func_name, description, config = _common_node_fields(node)
    code = str(config.get("code") or "").strip()
    input_mapping = config.get("inputMapping")
    logical_source_names = (
        resolve_input_mapping_names(source_names, input_mapping)
        if input_mapping is not None
        else source_names
    )
    if code and ("df" in source_names or "df" in logical_source_names):
        raise ConfigError(
            "Polars input name 'df' conflicts with the reserved output name; rename the "
            "upstream node or frame.",
            node_id=node.id,
            node_label=node.data.label,
        )
    params = _build_params(logical_source_names, default_df=False)
    sel = config.get("selected_columns", [])

    decorator_args: list[str] = []
    if sel:
        decorator_args.append(f"selected_columns={sel!r}")
    if input_mapping is not None:
        # ``resolve_input_mapping_names`` validated the persisted value before
        # it reaches source interpolation.
        decorator_args.append(f"inputMapping={input_mapping!r}")
    decorator = (
        f"@pipeline.polars({', '.join(decorator_args)})" if decorator_args else "@pipeline.polars"
    )

    if not code:
        # Not written yet. A polars node's output is whatever its code assigns
        # to ``df``; with no code there is nothing to return — there is no
        # implicit passthrough, whatever the input count. Not a reason to block
        # a SAVE — a half-built graph is a normal state to leave the editor in
        # — so emit a body that is valid Python, keeps the node's inputs bound,
        # and fails loudly if the pipeline is run. Save surfaces this as a
        # warning (see `_validate_transforms_are_runnable`), and the
        # placeholder round-trips back to "no code" in the editor.
        return (
            f"{decorator}\n"
            f"def {func_name}({params}) -> pl.LazyFrame:\n"
            f'    """{description}"""\n'
            f"{INCOMPLETE_TRANSFORM_BODY}"
        )

    # Inputs are the named parameters; ``df`` is only the output variable, so
    # no binding is prepended — the code starts from the input it names.
    body = _wrap_user_code(code, ["df"])
    return (
        f"{decorator}\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"{POLARS_OUTPUT_DECLARATION}"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# Submodel sentinels — registered so the unified registry is fully populated,
# but they fail loudly if codegen ever dispatches on them.  See module-level
# docstring for the rationale.
# ---------------------------------------------------------------------------


@_register_codegen(NodeType.EDGE_JOIN)
def _gen_edge_join(node: GraphNode, source_names: list[str]) -> str:
    if len(source_names) != 2:
        raise ConfigError(
            "edgeJoin codegen requires exactly two incoming sources.",
            node_id=node.id,
            node_label=node.data.label,
            source_names=source_names,
        )
    func_name, description, config = _common_node_fields(node)
    base_name, join_name = source_names
    params = _build_params(source_names)
    missing_roles = [key for key in ("baseInput", "joinInput") if not config.get(key)]
    if missing_roles:
        # Unreachable via graph_to_code — `_role_order_node_sources` resolves
        # roles before dispatch — but guards direct callers against silently
        # emitting a decorator with no role kwargs to rewrite.
        raise ConfigError(
            "edgeJoin codegen requires baseInput and joinInput in config.",
            node_id=node.id,
            node_label=node.data.label,
            missing=missing_roles,
        )
    # Role kwargs must name the functions this pass emits, not the raw config
    # node ids: live canvas ids do not survive a parse
    # round-trip, where node ids become sanitized function names, so verbatim
    # ids would make the saved file unloadable.  `_role_order_node_sources`
    # has already resolved baseInput/joinInput against the connected node ids
    # — failing loudly when a role references a missing or unconnected node —
    # and ordered sources base-first, so source_names[0]/[1] ARE the base and
    # join nodes' emitted function names.
    role_names = {"base_input": base_name, "join_input": join_name}
    decorator_args = ", ".join(
        _format_kwarg_source(key, role_names.get(key, value))
        for key, value in edge_join_config_to_decorator_kwargs(config)
    )
    # Keep codegen-time validation without duplicating join semantics in the body.
    build_edge_join_kwargs(config)
    return (
        f"@pipeline.edge_join({decorator_args})\n"
        f"def {func_name}({params}) -> pl.LazyFrame:\n"
        f'    """{description}"""\n'
        f"    return pipeline._apply_edge_join({_safe_str(func_name)}, {base_name}, {join_name})\n"
    )


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
# Module export surface.
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
    "_safe_path",
    "_safe_str",
    "_sanitize_description",
    "_wrap_external_code",
    "_wrap_user_code",
    # Per-type builders (imported by some tests)
    "_gen_api_input",
    "_gen_banding",
    "_gen_constant",
    "_gen_data_input",
    "_gen_data_output",
    "_gen_edge_join",
    "_gen_external_file",
    "_gen_live_switch",
    "_gen_model_score",
    "_gen_output",
    "_gen_rating_step",
    "_gen_scenario_expander",
    "_gen_transform",
    "_register_codegen",
]
