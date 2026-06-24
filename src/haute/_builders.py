"""Node builder registry — per-type factory functions for graph execution.

Each builder receives a ``NodeBuildContext`` and returns
``(func_name, callable, is_source)`` — consumed by
``_execute_eager_core`` / ``_execute_lazy`` in ``graph_utils.py``.

Extracted from ``executor.py`` to keep the orchestration module focused
on ``execute_graph``, ``_eager_execute``, and ``execute_sink``.

Exec-side registrations write into :data:`haute._registry.NODE_REGISTRY` —
the single source of truth shared with ``_codegen_builders.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl

from haute._code_extraction import _strip_generated_boilerplate_from_code

# The Contract dataclass is defined canonically in haute._contracts; re-exported
# here for back-compat (builder source files + the adoption tests import it via
# haute._builders). The tuple aliases / OPAQUE_CONTRACT below stay local.
from haute._contracts import Contract  # noqa: F401
from haute._graph_utils import _sanitize_func_name
from haute._io import load_external_object, read_source
from haute._logging import get_logger
from haute._rating import (
    _apply_banding,
    _apply_rating_step_outputs,
    _apply_rating_table,
    _combine_rating_columns,
    _normalise_banding_factors,
    _normalise_combined_outputs,
)
from haute._rating_step_config import normalise_rating_tables
from haute._registry import (
    NODE_REGISTRY,
)
from haute._registry import (
    register_exec as _register_exec_in_registry,
)
from haute._types import GraphNode, NodeType, _Frame
from haute._user_exec import _exec_user_code

logger = get_logger(component="executor")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_SCENARIO_MIN = 0.8  # scenario expander lower bound
_DEFAULT_SCENARIO_MAX = 1.2  # scenario expander upper bound
_DEFAULT_SCENARIO_STEPS = 21  # number of steps in scenario grid
_DEFAULT_CHUNK_SIZE = 500_000  # rows per chunk for optimiser apply


# ---------------------------------------------------------------------------
# Resolve instance nodes
# ---------------------------------------------------------------------------


def resolve_instance_node(node: GraphNode, node_map: dict[str, GraphNode]) -> GraphNode:
    """If *node* is an instance, return a merged node with the original's config.

    The returned node keeps the instance's own id, label, and position but
    uses the original node's ``nodeType`` and ``config`` (minus the
    ``instanceOf`` key itself).  If the original cannot be found the
    instance is returned unchanged.
    """
    config = node.data.config
    ref = config.get("instanceOf")
    if not ref or ref not in node_map:
        return node
    original = node_map[ref]
    orig_config = {k: v for k, v in original.data.config.items() if k != "instanceOf"}
    # Preserve instance-specific keys (inputMapping) that the UI sets
    instance_keys = {k: v for k, v in config.items() if k in ("inputMapping",)}
    merged_config = {**orig_config, "instanceOf": ref, **instance_keys}
    merged_data = node.data.model_copy(
        update={
            "nodeType": original.data.nodeType,
            "config": merged_config,
        }
    )
    return node.model_copy(update={"data": merged_data})


# ---------------------------------------------------------------------------
# Node builder registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeBuildContext:
    """Parameters shared by all node builder functions."""

    node: GraphNode
    source_names: list[str]
    source_ids: list[str]
    row_limit: int | None
    node_map: dict[str, GraphNode] | None
    orig_source_names: list[str] | None
    preamble_ns: dict[str, Any] | None
    source: str | None

    @property
    def func_name(self) -> str:
        """Sanitized function name derived from the node label."""
        return _sanitize_func_name(self.node.data.label)

    @property
    def config(self) -> dict[str, Any]:
        """Shortcut to the node's config dict."""
        return self.node.data.config


# Type alias for builder functions.
NodeBuilder = Callable[[NodeBuildContext], tuple[str, Callable, bool]]

# Column contract type: (produced_columns, referenced_columns).
# ``produced``: columns the node creates (not in input).  None = opaque.
# ``referenced``: input columns the node reads for computation.  None = opaque.
ColumnContract = tuple[set[str] | None, set[str] | None]
ColumnContractFn = Callable[[dict[str, Any]], ColumnContract]

#: Sentinel for builders that are genuinely opaque — user code, external
#: file schemas, etc.  Registering this explicitly (rather than omitting
#: a contract registration altogether) lets the system distinguish
#: "declared opaque" from "forgot to declare", which is important for
#: adoption tracking and the codegen/parser/executor contract pipeline.
OPAQUE_CONTRACT: ColumnContract = (None, None)

#: String sentinel emitted by codegen for opaque contracts.  Kept in
#: sync with ``tests.fixtures.expected_contracts.OPAQUE_SENTINEL``.
OPAQUE_CONTRACT_SENTINEL = "opaque"


def _register(
    node_type: NodeType,
    *,
    columns: ColumnContractFn | None = None,
    opaque: bool = False,
) -> Callable[[NodeBuilder], NodeBuilder]:
    """Decorator to register a node builder for a given NodeType.

    Writes the builder into the unified :data:`haute._registry.NODE_REGISTRY`.

    The optional *columns* callback declares the node's column contract —
    which columns it creates and which input columns it reads — given its
    config dict.  This is used by the checkpoint projection pass to avoid
    writing unneeded columns to intermediate parquet files.

    Passing ``opaque=True`` explicitly registers the builder as producing
    an ``OPAQUE_CONTRACT``.  The two ways of declaring opacity exist so
    that callers can either spell out "I don't know, and that's by
    design" (``opaque=True``) or provide a callback that might return a
    concrete contract in some configurations and opaque in others (e.g.
    ``MODEL_SCORE``).  Either way, the registry entry records the
    callback so "forgot to register" is distinct from "declared opaque".
    """

    if columns is not None and opaque:
        raise ValueError(
            f"_register({node_type!r}): pass either columns= or opaque=True, not both.",
        )

    # Resolve the contract callback eagerly so every registry view references
    # the same callable (test_column_contracts asserts identity).
    contract_fn: ColumnContractFn | None
    if opaque:
        contract_fn = _opaque_columns
    else:
        contract_fn = columns

    registrar = _register_exec_in_registry(node_type, column_contract=contract_fn)

    def decorator(fn: NodeBuilder) -> NodeBuilder:
        registrar(fn)
        return fn

    return decorator


def get_column_contract(
    node_type: NodeType,
    config: dict[str, Any],
) -> ColumnContract:
    """Return the column contract for a node type.

    Every registered ``NodeType`` must have a ``column_contract`` entry
    in :data:`haute._registry.NODE_REGISTRY`.  If a future ``NodeType`` is
    added without a contract, this function raises — silently falling back
    to opaque would hide the omission.
    """
    entry = NODE_REGISTRY.get(node_type)
    if entry is None or entry.column_contract is None:
        raise KeyError(
            f"NodeType {node_type!r} has no column contract registered. "
            "Every builder in NODE_REGISTRY must also register a contract "
            "in NODE_REGISTRY (pass columns=... or opaque=True to "
            "_register).",
        )
    # The registry stores the contract fn as ``Callable[[dict], Any]`` for
    # cross-module generality; every registration in this module passes a
    # ``ColumnContractFn``, so the cast is safe by construction.
    result: ColumnContract = entry.column_contract(config)
    return result


def _opaque_columns(_config: dict[str, Any]) -> ColumnContract:
    """Column contract for explicitly opaque nodes: (None, None)."""
    return OPAQUE_CONTRACT


def _passthrough_columns(_config: dict[str, Any]) -> ColumnContract:
    """Column contract for passthrough nodes: creates nothing, reads nothing."""
    return (set(), set())


def _passthrough_fn(*dfs: _Frame) -> _Frame:
    """Shared passthrough: return the first input or an empty LazyFrame."""
    return dfs[0] if dfs else pl.LazyFrame()


@_register(NodeType.API_INPUT, opaque=True)
def _build_api_input(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    path = config.get("path", "")
    flat_schema = config.get("flattenSchema")

    api_source_fn: Callable[..., _Frame]
    if path.lower().endswith((".json", ".jsonl")):

        def _api_source_json(_path: str = path, _schema: dict | None = flat_schema) -> _Frame:
            from haute._json_flatten import json_cache_path_if_valid

            cache_path = json_cache_path_if_valid(_path, schema=_schema)
            if cache_path is not None:
                return pl.scan_parquet(cache_path)
            raise RuntimeError(
                "JSON data has not been cached yet, or the existing cache is stale "
                "or schema-incompatible. "
                "Click 'Cache as Parquet' on the API Input node to process it."
            )

        api_source_fn = _api_source_json
    else:

        def _api_source_flat() -> _Frame:
            return read_source(path)

        api_source_fn = _api_source_flat

    return ctx.func_name, api_source_fn, True


@_register(NodeType.DATA_SOURCE, opaque=True)
def _build_data_source(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    path = config.get("path", "")
    source_type = config.get("sourceType", "flat_file")
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="data_source",
    )
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    base_fn: Callable[..., _Frame]
    if source_type == "databricks":
        table = config.get("table", "")

        def _databricks_source(_table: str = table) -> _Frame:
            from haute._databricks_io import read_cached_table

            return read_cached_table(_table)

        base_fn = _databricks_source
    else:

        def source_fn() -> _Frame:
            if not path:
                return pl.LazyFrame()
            return read_source(path)

        base_fn = source_fn

    if not code:
        return ctx.func_name, base_fn, True

    def source_with_code() -> _Frame:
        raw = base_fn()
        return _exec_user_code(code, ["df"], (raw,), extra_ns=_preamble)

    return ctx.func_name, source_with_code, True


def _constant_columns(config: dict[str, Any]) -> ColumnContract:
    raw_values = config.get("values", []) or []
    produced = {v.get("name", "") for v in raw_values if v.get("name")}
    return produced or {"constant"}, set()


@_register(NodeType.CONSTANT, columns=_constant_columns)
def _build_constant(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    raw_values = config.get("values", []) or []

    def constant_fn() -> _Frame:
        data: dict[str, list] = {}
        for v in raw_values:
            name = v.get("name", "")
            if not name:
                continue
            val = v.get("value", "")
            try:
                data[name] = [float(val)]
            except (ValueError, TypeError):
                data[name] = [val]
        if not data:
            data = {"constant": [0]}
        return pl.LazyFrame(data)

    return ctx.func_name, constant_fn, True


@_register(NodeType.LIVE_SWITCH, columns=_passthrough_columns)
def _build_live_switch(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    input_scenario_map: dict[str, str] = config.get("input_scenario_map", {})
    input_names = list(ctx.source_names)
    _source = ctx.source or "live"

    def switch_fn(*dfs: _Frame) -> _Frame:
        # Find the input mapped to the active source
        for inp, scn in input_scenario_map.items():
            if scn == _source:
                for i, name in enumerate(input_names):
                    if name == inp:
                        return dfs[i]
        # Fallback: first input + log warning
        if input_scenario_map:
            logger.warning(
                "live_switch_unmapped_scenario",
                source=_source,
                mapped_scenarios=list(input_scenario_map.values()),
                falling_back_to=input_names[0] if input_names else "<none>",
            )
        if not dfs:
            raise ValueError("live_switch received no input DataFrames")
        return dfs[0]

    return ctx.func_name, switch_fn, False


@_register(NodeType.DATA_SINK, columns=_passthrough_columns)
def _build_data_sink(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    # During normal run/preview, dataSink is a pass-through.
    # Actual writing happens via execute_sink() on explicit user action.
    return ctx.func_name, _passthrough_fn, False


@_register(NodeType.EXTERNAL_FILE, opaque=True)
def _build_external_file(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="external",
        param_names=ctx.source_names,
    )
    path = config.get("path", "")
    file_type = config.get("fileType", "pickle")
    model_class = config.get("modelClass", "classifier")
    _src_names = list(ctx.source_names)

    _orig_src = list(ctx.orig_source_names) if ctx.orig_source_names else None
    _in_map = dict(config.get("inputMapping", {})) or None
    _preamble_ext = dict(ctx.preamble_ns) if ctx.preamble_ns else {}
    if code:

        def external_fn(*dfs: _Frame) -> _Frame:
            ens = {"obj": load_external_object(path, file_type, model_class)}
            ens.update(_preamble_ext)
            return _exec_user_code(
                code,
                _src_names,
                dfs,
                extra_ns=ens,
                orig_source_names=_orig_src,
                input_mapping=_in_map,
            )

        return ctx.func_name, external_fn, False
    else:
        return ctx.func_name, _passthrough_fn, False


@_register(NodeType.OUTPUT, columns=_passthrough_columns)
def _build_output(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    fields = config.get("fields", []) or []

    def output_fn(*dfs: _Frame) -> _Frame:
        lf = dfs[0] if dfs else pl.LazyFrame()
        if fields:
            lf = lf.select(fields)
        return lf

    return ctx.func_name, output_fn, False


def _banding_columns(config: dict[str, Any]) -> ColumnContract:
    factors = config.get("factors") or []
    produced = {f["outputColumn"] for f in factors if f.get("outputColumn")}
    referenced = {f["column"] for f in factors if f.get("column")}
    return produced, referenced


@_register(NodeType.BANDING, columns=_banding_columns)
def _build_banding(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    factors = _normalise_banding_factors(config)

    def banding_fn(*dfs: _Frame, _factors: tuple = tuple(dict(f) for f in factors)) -> _Frame:
        lf = dfs[0] if dfs else pl.LazyFrame()
        for f in _factors:
            col = f.get("column", "")
            out = f.get("outputColumn", "")
            rules = f.get("rules", []) or []
            if not col or not out or not rules:
                continue
            lf = _apply_banding(
                lf,
                col,
                out,
                f.get("banding", "continuous"),
                rules,
                f.get("default"),
                right_closed=f.get("rightClosed", True),
            )
        return lf

    return ctx.func_name, banding_fn, False


def _rating_step_columns(config: dict[str, Any]) -> ColumnContract:
    if (config.get("code") or "").strip():
        return OPAQUE_CONTRACT
    tables = normalise_rating_tables(config)
    produced: set[str] = set()
    referenced: set[str] = set()
    for t in tables:
        out = t.get("outputColumn", "")
        if out:
            produced.add(out)
        referenced.update(t.get("factors") or [])
    table_out_cols = [t.get("outputColumn", "") for t in tables if t.get("outputColumn")]
    for combined in _normalise_combined_outputs(config):
        if combined.get("_legacy") and len(table_out_cols) < 2:
            continue
        produced.add(combined["outputColumn"])
    return produced, referenced


@_register(NodeType.RATING_STEP, columns=_rating_step_columns)
def _build_rating_step(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    tables = normalise_rating_tables(config)
    combined_outputs = _normalise_combined_outputs(config)
    first = ctx.source_names[0] if ctx.source_names else "df"
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="rating_step",
        param_names=(first,),
    )
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    def rating_fn(
        *dfs: _Frame,
        _tables: list = list(tables),
        _combined_outputs: list[dict[str, Any]] = list(combined_outputs),
        _code: str = code,
    ) -> _Frame:
        lf = dfs[0] if dfs else pl.LazyFrame()
        lf = _apply_rating_step_outputs(lf, _tables, _combined_outputs)
        if _code:
            lf = _exec_user_code(_code, ["df"], (lf,), extra_ns=_preamble)
        return lf

    return ctx.func_name, rating_fn, False


def _scenario_expander_columns(config: dict[str, Any]) -> ColumnContract:
    # Post-expansion user code can reference arbitrary columns — opaque.
    if (config.get("code") or "").strip():
        return (None, None)
    produced: set[str] = set()
    cn = (config.get("column_name") or "").strip()
    if cn:
        produced.add(cn)
    sc = config.get("step_column", "scenario_index")
    if sc:
        produced.add(sc)
    return produced, set()


@_register(NodeType.SCENARIO_EXPANDER, columns=_scenario_expander_columns)
def _build_scenario_expander(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    _col_name = (config.get("column_name") or "").strip()
    raw_min = config.get("min_value")
    _min_val = float(raw_min) if raw_min is not None else _DEFAULT_SCENARIO_MIN
    raw_max = config.get("max_value")
    _max_val = float(raw_max) if raw_max is not None else _DEFAULT_SCENARIO_MAX
    raw_steps = config.get("steps")
    _steps = int(raw_steps) if raw_steps is not None else _DEFAULT_SCENARIO_STEPS
    if _steps < 1:
        raise ValueError(f"Scenario expander requires steps >= 1, got {_steps}")
    _step_col = config.get("step_column") or "scenario_index"
    first = ctx.source_names[0] if ctx.source_names else "df"
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="scenario_expander",
        param_names=(first,),
    )
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    def scenario_expand_fn(
        *dfs: _Frame,
        _cn: str = _col_name,
        _mn: float = _min_val,
        _mx: float = _max_val,
        _st: int = _steps,
        _sc: str = _step_col,
    ) -> _Frame:
        lf = dfs[0] if dfs else pl.LazyFrame()
        scenario_exprs = [pl.lit(list(range(_st))).alias(_sc)]
        explode_cols = [_sc]
        if _cn:
            import numpy as np

            vals = np.linspace(_mn, _mx, _st)
            # Float32 to match Rust QuoteGrid schema (price-contour ingests f32)
            scenario_exprs.append(pl.lit(vals.astype("float32").tolist()).alias(_cn))
            explode_cols.append(_cn)
        cast_exprs = [pl.col(_sc).cast(pl.Int32)]
        if _cn:
            cast_exprs.append(pl.col(_cn).cast(pl.Float32))
        return lf.with_columns(scenario_exprs).explode(explode_cols).with_columns(cast_exprs)

    if not code:
        return ctx.func_name, scenario_expand_fn, False

    def scenario_expand_with_code(
        *dfs: _Frame,
    ) -> _Frame:
        expanded = scenario_expand_fn(*dfs)
        return _exec_user_code(code, ["df"], (expanded,), extra_ns=_preamble)

    return ctx.func_name, scenario_expand_with_code, False


@_register(NodeType.OPTIMISER, columns=_passthrough_columns)
def _build_optimiser(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    # Pass-through in preview mode. Solving happens via /api/optimiser/solve.
    # When data_input is configured, select that specific input so the
    # preview shows scenario-expanded data rather than a banding source.
    data_input_id = ctx.config.get("data_input")
    if data_input_id and ctx.node_map and data_input_id in ctx.node_map:
        target_name = _sanitize_func_name(ctx.node_map[data_input_id].data.label)
        if target_name in ctx.source_names:
            idx = ctx.source_names.index(target_name)

            def _optimiser_select(*dfs: _Frame, _i: int = idx) -> _Frame:
                if len(dfs) <= _i:
                    raise ValueError(
                        f"Optimiser expected input at index {_i} but only "
                        f"received {len(dfs)} input(s)",
                    )
                return dfs[_i]

            return ctx.func_name, _optimiser_select, False
    return ctx.func_name, _passthrough_fn, False


def _optimiser_apply_columns(config: dict[str, Any]) -> ColumnContract:
    # Mirror the "do we have a source configured?" check in
    # _build_optimiser_apply: without an artifact path or a valid
    # MLflow source the builder returns _passthrough_fn, meaning the
    # node reads nothing from its input and produces no new columns.
    # Only once a source is configured do the referenced columns become
    # artifact-driven and therefore opaque.
    source_type = config.get("sourceType", "")
    if config.get("artifact_path", "") and not source_type:
        from haute.errors import ConfigError

        raise ConfigError(
            "optimiserApply node with artifact_path requires sourceType='file'",
            missing_field="sourceType",
        )
    has_file = bool(config.get("artifact_path", "")) and source_type == "file"
    has_mlflow = source_type in ("run", "registered") and (
        (source_type == "run" and config.get("run_id"))
        or (source_type == "registered" and config.get("registered_model"))
    )
    if not has_file and not has_mlflow:
        return set(), set()

    # Produced: configured apply sources append a version column and can
    # rename the final optimiser value column when requested.
    vcol = config.get("version_column", "__optimiser_version__")
    produced = {vcol} if vcol else set()
    opt_value_col = config.get("optimised_value_column", "")
    if opt_value_col:
        produced.add(opt_value_col)
    return produced, None


@_register(NodeType.OPTIMISER_APPLY, columns=_optimiser_apply_columns)
def _build_optimiser_apply(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    _artifact_path = config.get("artifact_path", "")
    _version_col = config.get("version_column", "__optimiser_version__")
    _optimised_value_col = config.get("optimised_value_column", "")
    _ratebook_input = config.get("ratebook_input", "")
    _source_names = list(ctx.source_names)
    _source_ids = list(ctx.source_ids)
    _source_type = config.get("sourceType", "")
    _run_id = config.get("run_id", "")
    _registered_model = config.get("registered_model", "")
    _opt_version = config.get("version", "latest")

    # Determine if we have a valid source configured
    if _artifact_path and not _source_type:
        from haute.errors import ConfigError

        raise ConfigError(
            "optimiserApply node with artifact_path requires sourceType='file'",
            missing_field="sourceType",
        )
    _has_file = bool(_artifact_path) and _source_type == "file"
    _has_mlflow = _source_type in ("run", "registered") and (
        (_source_type == "run" and _run_id) or (_source_type == "registered" and _registered_model)
    )

    if not _has_file and not _has_mlflow:
        return ctx.func_name, _passthrough_fn, False

    def optimiser_apply_fn(
        *dfs: _Frame,
        _path: str = _artifact_path,
        _vcol: str = _version_col,
        _st: str = _source_type,
        _rid: str = _run_id,
        _rm: str = _registered_model,
        _ver: str = _opt_version,
        _opt_col: str = _optimised_value_col,
        _rb_input: str = _ratebook_input,
        _src_names: list[str] = _source_names,
        _src_ids: list[str] = _source_ids,
    ) -> _Frame:
        if _st in ("run", "registered"):
            from haute._optimiser_io import load_mlflow_optimiser_artifact

            artifact = load_mlflow_optimiser_artifact(
                source_type=_st,
                run_id=_rid,
                registered_model=_rm,
                version=_ver,
            )
        else:
            from haute._optimiser_io import load_optimiser_artifact

            artifact = load_optimiser_artifact(_path)

        input_lf = _select_optimiser_apply_input(
            dfs,
            artifact,
            _rb_input,
            _src_names,
            _src_ids,
        )
        return _dispatch_apply(input_lf, artifact, _vcol, _opt_col)

    return ctx.func_name, optimiser_apply_fn, False


@_register(NodeType.MODELLING, columns=_passthrough_columns)
def _build_modelling(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    # Pass-through in preview mode. Training happens via /api/modelling/train.
    return ctx.func_name, _passthrough_fn, False


def _model_score_columns(config: dict[str, Any]) -> ColumnContract:
    out = config.get("output_column", "prediction")
    produced = {out} if out else {"prediction"}

    # Post-processing code can reference arbitrary columns — opaque.
    if (config.get("code") or "").strip():
        return produced, None

    # Feature columns are only known after loading the model.
    source_type = config.get("sourceType", "")
    if not source_type:
        # Distinguish two sub-cases cleanly:
        #
        # 1. ``output_column`` missing entirely and no source configured
        #    — the node is a freshly-dragged placeholder from the UI
        #    with no information at all.  We cannot claim anything
        #    concrete about its referenced columns; return ``None``
        #    (opaque).
        #
        # 2. ``output_column`` present but no source configured — the
        #    builder will return ``_passthrough_fn``.  The executor
        #    detects the passthrough wiring and skips the output-side
        #    boundary assertion, so reporting
        #    ``produced={output_column}`` here is forward-looking
        #    documentation rather than a runtime lie.
        if "output_column" in config:
            return produced, set()
        return produced, None

    # Validate required fields per sourceType on the spot; a blank
    # required field is a config bug, not a reason to silently fall
    # back to opaque-column detection and confuse downstream nodes.
    from haute.errors import ConfigError

    run_id = config.get("run_id", "")
    registered_model = config.get("registered_model", "")
    if source_type == "run" and not run_id:
        raise ConfigError(
            "modelScore node is misconfigured: sourceType='run' but run_id is empty",
            sourceType=source_type,
            missing_field="run_id",
        )
    if source_type == "registered" and not registered_model:
        raise ConfigError(
            "modelScore node is misconfigured: sourceType='registered' but "
            "registered_model is empty",
            sourceType=source_type,
            missing_field="registered_model",
        )

    # With required config present, attempt the MLflow load.  Failures here
    # (run not found, artifact missing, MLflow down) propagate — the old
    # debug-log swallow hid real config/infra problems from downstream nodes.
    from haute._mlflow_io import load_mlflow_model

    scoring_model = load_mlflow_model(
        source_type=source_type,
        run_id=run_id,
        artifact_path=config.get("artifact_path", ""),
        registered_model=registered_model,
        version=config.get("version", "latest"),
        task=config.get("task", "regression"),
    )
    if scoring_model.feature_names:
        return produced, set(scoring_model.feature_names)
    return produced, None


@_register(NodeType.MODEL_SCORE, columns=_model_score_columns)
def _build_model_score(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="model_score",
        param_names=ctx.source_names,
    )
    # Default to "" (not "run") — empty sourceType means the node is
    # unconfigured and should passthrough.  Codegen and score_from_config
    # default to "run" because they only execute for configured nodes.
    source_type = config.get("sourceType", "")
    _run_id = config.get("run_id", "")
    _artifact_path = config.get("artifact_path", "")
    _registered_model = config.get("registered_model", "")
    _task = config.get("task", "regression")

    # If no model source configured, passthrough
    if (
        not source_type
        or (source_type == "run" and not _run_id)
        or (source_type == "registered" and not _registered_model)
    ):
        return ctx.func_name, _passthrough_fn, False

    from haute._model_scorer import ModelScorer

    scorer = ModelScorer(
        source_type=source_type,
        run_id=_run_id,
        artifact_path=_artifact_path,
        registered_model=config.get("registered_model", ""),
        version=config.get("version", "latest"),
        task=_task,
        output_col=config.get("output_column", "prediction"),
        code=code,
        source_names=list(ctx.source_names),
        source=ctx.source or "live",
        row_limit=ctx.row_limit,
    )

    return ctx.func_name, scorer.score, False


@_register(NodeType.POLARS, opaque=True)
def _build_transform(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    _src_names = list(ctx.source_names)
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="polars",
        param_names=_src_names,
    )
    _orig_src = list(ctx.orig_source_names) if ctx.orig_source_names else None
    _in_map = dict(config.get("inputMapping", {})) or None
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    if code:

        def transform_fn(*dfs: _Frame) -> _Frame:
            return _exec_user_code(
                code,
                _src_names,
                dfs,
                extra_ns=_preamble,
                orig_source_names=_orig_src,
                input_mapping=_in_map,
            )

        # A polars node with self-contained code and no upstream wiring
        # is effectively a source: there is no dataframe to receive, so
        # the code block must construct its own (``df = pl.DataFrame(
        # ...)``).  Marking it as a source lets the executor call the
        # function with no args and skip the "no input data available"
        # guard — which exists to catch genuinely-broken graphs where a
        # downstream node lost its parents, not self-contained code
        # snippets.
        is_source = not _src_names
        return ctx.func_name, transform_fn, is_source
    else:
        return ctx.func_name, _passthrough_fn, False


# SUBMODEL and SUBMODEL_PORT are placeholder/port node types used by the
# submodel boundary machinery.  For execution they pass through because
# ``_flatten.flatten_graph`` removes the placeholder before the executor
# runs — but that's a defensive passthrough: if an unflatted graph ever
# reaches the executor, returning an empty LazyFrame beats a cryptic
# KeyError.  Codegen takes the strict stance (see
# ``_codegen_builders._gen_submodel``): by the time codegen dispatches, the
# submodel must have been split into its own file via ``graph_to_code_multi``.
@_register(NodeType.SUBMODEL, columns=_passthrough_columns)
def _build_submodel(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    return ctx.func_name, _passthrough_fn, False


@_register(NodeType.SUBMODEL_PORT, columns=_passthrough_columns)
def _build_submodel_port(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    return ctx.func_name, _passthrough_fn, False


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


def _build_node_fn(
    node: GraphNode,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
    row_limit: int | None = None,
    node_map: dict[str, GraphNode] | None = None,
    orig_source_names: list[str] | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str | None = None,
) -> tuple[str, Callable, bool]:
    """Build an executable function from a graph node dict.

    Returns (func_name, fn, is_source).
    source_names: sanitized names of upstream nodes (used as variable names).
    row_limit: if set, Databricks sources push this into SQL LIMIT so the
        full table is never fetched during preview/trace.
    node_map: full graph node_map — used to resolve ``instanceOf`` references.
    source: the active execution source (``"live"`` for eager scoring,
        anything else for batched parquet scoring).
    """
    # Resolve instance → use original's config/nodeType
    if node_map:
        node = resolve_instance_node(node, node_map)

    if source_names is None:
        source_names = []
    if source_ids is None:
        source_ids = []

    ctx = NodeBuildContext(
        node=node,
        source_names=source_names,
        source_ids=source_ids,
        row_limit=row_limit,
        node_map=node_map,
        orig_source_names=orig_source_names,
        preamble_ns=preamble_ns,
        source=source,
    )

    # Dispatch through the unified registry — the single source of truth.
    # Missing entries are a registration bug, not a condition to silently
    # paper over: the previous passthrough fallback hid typos and drift
    # between the exec and codegen tables.  Every ``NodeType`` is validated
    # at import time (:func:`validate_registry_complete`), so reaching this
    # branch means someone added a NodeType without wiring it in.
    entry = NODE_REGISTRY.get(node.data.nodeType)
    if entry is None or entry.exec is None:
        raise KeyError(
            f"no exec builder registered for {node.data.nodeType!r} "
            f"(node id={node.id!r} label={node.data.label!r})"
        )
    return entry.exec(ctx)


# ---------------------------------------------------------------------------
# OptimiserApply helpers
# ---------------------------------------------------------------------------


def _select_optimiser_apply_input(
    dfs: tuple[_Frame, ...],
    artifact: dict[str, Any],
    ratebook_input: str,
    source_names: list[str],
    source_ids: list[str],
) -> _Frame:
    """Select the dataframe to apply without letting config override artifact mode."""
    if artifact.get("mode", "online") != "ratebook":
        return dfs[0] if dfs else pl.LazyFrame()

    if not ratebook_input:
        # Ratebook apply with no configured ``ratebook_input`` falls back to the
        # first connected input.  Surface the unconfigured state so the user can
        # set the picker explicitly when the graph has multiple ratebook inputs.
        logger.warning(
            "optimiser_apply_ratebook_input_unset",
            source_ids=source_ids,
            source_names=source_names,
        )
        return dfs[0] if dfs else pl.LazyFrame()

    if not source_ids:
        raise ValueError(
            "optimiserApply ratebook_input requires connected input source_ids; "
            f"got ratebook_input={ratebook_input!r} with source_names={source_names!r}",
        )

    if ratebook_input not in source_ids:
        raise ValueError(
            "optimiserApply ratebook_input "
            f"{ratebook_input!r} is not one of the connected input node ids: {source_ids!r}",
        )

    index = source_ids.index(ratebook_input)
    if index >= len(dfs):
        raise ValueError(
            "optimiserApply expected ratebook_input "
            f"{ratebook_input!r} at index {index}, but received {len(dfs)} input(s)",
        )
    return dfs[index]


def _dispatch_apply(
    lf: _Frame,
    artifact: dict[str, Any],
    version_col: str,
    optimised_value_col: str = "",
) -> _Frame:
    """Route to the correct apply function based on artifact mode."""
    mode = artifact.get("mode", "online")
    version = artifact.get("version", "")
    if mode == "ratebook":
        return _apply_ratebook(lf, artifact, version, version_col, optimised_value_col)
    return _apply_online(lf, artifact, version, version_col, optimised_value_col)


def _apply_online(
    lf: _Frame,
    artifact: dict[str, Any],
    version: str,
    version_col: str,
    optimised_value_col: str = "",
) -> _Frame:
    """Apply online optimisation: Lagrangian argmax with stored lambdas."""
    from price_contour import ApplyOptimiser

    qid_col = artifact.get("quote_id", "quote_id")
    step_col = artifact.get("scenario_index", "scenario_index")
    mult_col = artifact.get("scenario_value", "scenario_value")
    objective = artifact.get("objective", "expected_income")
    constraints = artifact.get("constraints") or {}

    df_eager = _prepare_online_apply_frame(lf, artifact)

    applier = ApplyOptimiser(
        lambdas=artifact["lambdas"],
        objective=objective,
        constraints=constraints,
        quote_id=qid_col,
        scenario_index=step_col,
        scenario_value=mult_col,
    )
    result = applier.apply(df_eager)
    result_df: pl.DataFrame = result.dataframe
    result_df = _rename_column_if_configured(
        result_df,
        "optimal_scenario_value",
        optimised_value_col,
    )

    if version:
        result_df = result_df.with_columns(pl.lit(version).alias(version_col))

    return result_df.lazy()


def _prepare_online_apply_frame(lf: _Frame, artifact: dict[str, Any]) -> pl.DataFrame:
    """Materialise an online apply input frame using runtime apply dtypes."""
    qid_col = artifact.get("quote_id", "quote_id")
    step_col = artifact.get("scenario_index", "scenario_index")
    mult_col = artifact.get("scenario_value", "scenario_value")
    objective = artifact.get("objective", "expected_income")
    constraints = artifact.get("constraints") or {}

    # Filter out null quote IDs before casting (null -> "null" would become
    # a real quote identifier and diverge from the optimiser apply path).
    lf = lf.filter(pl.col(qid_col).is_not_null())

    cast_exprs = [
        pl.col(qid_col).cast(pl.Utf8),
        pl.col(step_col).cast(pl.Int32),
        pl.col(mult_col).cast(pl.Float32),
        pl.col(objective).cast(pl.Float32),
    ]
    cast_names = {qid_col, step_col, mult_col, objective}
    for name, spec in constraints.items():
        if isinstance(spec, dict) and {"numerator", "denominator"}.issubset(spec):
            for col in (spec["numerator"], spec["denominator"]):
                col_name = str(col)
                if col_name not in cast_names:
                    cast_exprs.append(pl.col(col_name).cast(pl.Float32))
                    cast_names.add(col_name)
        elif name not in cast_names:
            cast_exprs.append(pl.col(name).cast(pl.Float32))
            cast_names.add(name)

    return lf.with_columns(cast_exprs).collect(engine="streaming")


def _apply_ratebook(
    lf: _Frame,
    artifact: dict[str, Any],
    version: str,
    version_col: str,
    optimised_value_col: str = "",
) -> _Frame:
    """Apply ratebook optimisation: factor table lookups with stored tables.

    Each factor group produces a ``{name}_optimised_factor`` column, and
    they are multiplied together into ``optimised_factor`` so that
    downstream nodes have a single combined relativity.
    """
    factor_tables = artifact.get("factor_tables", {})
    if not factor_tables:
        logger.warning("ratebook_apply_no_factor_tables", artifact_keys=list(artifact.keys()))
        result_lf = lf
    else:
        result_lf = lf
        factor_cols: list[str] = []
        for _name, entries in factor_tables.items():
            if not entries:
                continue
            # factor_tables format from save: list of
            # {"__factor_group__": level, "optimal_scenario_value": value}
            # Convert to the rating table format expected by _apply_rating_table
            factor_col = "__factor_group__"
            out_col = f"{_name}_optimised_factor"
            valid_entries = [
                {_name: e[factor_col], "value": e["optimal_scenario_value"]}
                for e in entries
                if factor_col in e
            ]
            skipped = len(entries) - len(valid_entries)
            if skipped:
                logger.warning(
                    "ratebook_entries_missing_factor_group",
                    factor=_name,
                    skipped=skipped,
                    total=len(entries),
                )
            table = {
                "factors": [_name],
                "outputColumn": out_col,
                "entries": valid_entries,
                "defaultValue": "1.0",
            }
            result_lf = _apply_rating_table(result_lf, table)
            factor_cols.append(out_col)

        # Combine individual factor columns into a single relativity
        if len(factor_cols) > 1:
            result_lf = _combine_rating_columns(
                result_lf,
                factor_cols,
                "multiply",
                "optimised_factor",
            )
        elif len(factor_cols) == 1:
            result_lf = result_lf.with_columns(
                pl.col(factor_cols[0]).alias("optimised_factor"),
            )

    if optimised_value_col and optimised_value_col != "optimised_factor":
        existing = result_lf.collect_schema().names()
        if "optimised_factor" not in existing:
            raise ValueError(
                "optimiserApply configured optimised_value_column but ratebook apply "
                "did not produce optimised_factor",
            )
        result_lf = result_lf.rename({"optimised_factor": optimised_value_col})

    if version:
        result_lf = result_lf.with_columns(pl.lit(version).alias(version_col))

    return result_lf


def _rename_column_if_configured(
    df: pl.DataFrame,
    source_column: str,
    configured_column: str,
) -> pl.DataFrame:
    if not configured_column or configured_column == source_column:
        return df
    if source_column not in df.columns:
        raise ValueError(
            "optimiserApply configured optimised_value_column but online apply "
            f"did not produce {source_column!r}",
        )
    return df.rename({source_column: configured_column})
