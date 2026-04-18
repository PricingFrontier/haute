"""Node builder registry — per-type factory functions for graph execution.

Each builder receives a ``NodeBuildContext`` and returns
``(func_name, callable, is_source)`` — consumed by
``_execute_eager_core`` / ``_execute_lazy`` in ``graph_utils.py``.

Extracted from ``executor.py`` to keep the orchestration module focused
on ``execute_graph``, ``_eager_execute``, and ``execute_sink``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl

from haute._io import load_external_object, read_source
from haute._logging import get_logger
from haute._rating import (
    _apply_banding,
    _apply_rating_table,
    _combine_rating_columns,
    _normalise_banding_factors,
)
from haute._types import (
    GraphNode,
    NodeType,
    _Frame,
    _sanitize_func_name,
)

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

_NODE_BUILDERS: dict[NodeType, NodeBuilder] = {}

# Column contract type: (produced_columns, referenced_columns).
# ``produced``: columns the node creates (not in input).  None = opaque.
# ``referenced``: input columns the node reads for computation.  None = opaque.
ColumnContract = tuple[set[str] | None, set[str] | None]
ColumnContractFn = Callable[[dict[str, Any]], ColumnContract]

_COLUMN_CONTRACTS: dict[NodeType, ColumnContractFn] = {}


def _register(
    node_type: NodeType,
    *,
    columns: ColumnContractFn | None = None,
) -> Callable[[NodeBuilder], NodeBuilder]:
    """Decorator to register a node builder for a given NodeType.

    The optional *columns* callback declares the node's column contract —
    which columns it creates and which input columns it reads — given its
    config dict.  This is used by the checkpoint projection pass to avoid
    writing unneeded columns to intermediate parquet files.
    """

    def decorator(fn: NodeBuilder) -> NodeBuilder:
        _NODE_BUILDERS[node_type] = fn
        if columns is not None:
            _COLUMN_CONTRACTS[node_type] = columns
        return fn

    return decorator


def get_column_contract(
    node_type: NodeType,
    config: dict[str, Any],
) -> ColumnContract:
    """Return the column contract for a node type, or ``(None, None)`` if opaque."""
    fn = _COLUMN_CONTRACTS.get(node_type)
    if fn is None:
        return (None, None)
    return fn(config)


def _passthrough_columns(_config: dict[str, Any]) -> ColumnContract:
    """Column contract for passthrough nodes: creates nothing, reads nothing."""
    return (set(), set())


def _passthrough_fn(*dfs: _Frame) -> _Frame:
    """Shared passthrough: return the first input or an empty LazyFrame."""
    return dfs[0] if dfs else pl.LazyFrame()


@_register(NodeType.API_INPUT)
def _build_api_input(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    path = config.get("path", "")
    flat_schema = config.get("flattenSchema")

    api_source_fn: Callable[..., _Frame]
    if path.endswith((".json", ".jsonl")):

        def _api_source_json(_path: str = path, _schema: dict | None = flat_schema) -> _Frame:
            from haute._json_flatten import _json_cache_path

            cache_path = _json_cache_path(_path)
            if cache_path.exists():
                return pl.scan_parquet(cache_path)
            raise RuntimeError(
                "JSON data has not been cached yet. "
                "Click 'Cache as Parquet' on the API Input node to process it."
            )

        api_source_fn = _api_source_json
    else:

        def _api_source_flat() -> _Frame:
            return read_source(path)

        api_source_fn = _api_source_flat

    return ctx.func_name, api_source_fn, True


@_register(NodeType.DATA_SOURCE)
def _build_data_source(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    path = config.get("path", "")
    source_type = config.get("sourceType", "flat_file")
    code = (config.get("code") or "").strip()
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
        from haute.executor import _exec_user_code

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


@_register(NodeType.EXTERNAL_FILE)
def _build_external_file(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    code = config.get("code", "").strip()
    path = config.get("path", "")
    file_type = config.get("fileType", "pickle")
    model_class = config.get("modelClass", "classifier")
    _src_names = list(ctx.source_names)

    _orig_src = list(ctx.orig_source_names) if ctx.orig_source_names else None
    _in_map = dict(config.get("inputMapping", {})) or None
    _preamble_ext = dict(ctx.preamble_ns) if ctx.preamble_ns else {}
    if code:

        def external_fn(*dfs: _Frame) -> _Frame:
            from haute.executor import _exec_user_code

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
    tables = config.get("tables") or []
    produced: set[str] = set()
    referenced: set[str] = set()
    for t in tables:
        out = t.get("outputColumn", "")
        if out:
            produced.add(out)
        referenced.update(t.get("factors") or [])
    combined = config.get("combinedColumn", "")
    table_out_cols = [t.get("outputColumn", "") for t in tables if t.get("outputColumn")]
    if combined and len(table_out_cols) >= 2:
        produced.add(combined)
    return produced, referenced


@_register(NodeType.RATING_STEP, columns=_rating_step_columns)
def _build_rating_step(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    tables: list[dict[str, Any]] = config.get("tables", []) or []
    # GUI config may send None for these fields, so `or` ensures a usable default
    _rs_operation: str = config.get("operation", "multiply") or "multiply"
    _rs_combined: str = config.get("combinedColumn", "") or ""

    def rating_fn(
        *dfs: _Frame,
        _tables: list = list(tables),
        _op: str = _rs_operation,
        _combined: str = _rs_combined,
    ) -> _Frame:
        lf = dfs[0] if dfs else pl.LazyFrame()
        out_cols: list[str] = []
        for t in _tables:
            lf = _apply_rating_table(lf, t)
            oc = t.get("outputColumn", "")
            if oc:
                out_cols.append(oc)
        if _combined and len(out_cols) >= 2:
            logger.info(
                "combining_rating_columns",
                columns=out_cols,
                operation=_op,
                output=_combined,
            )
            lf = _combine_rating_columns(lf, out_cols, _op, _combined)
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
    code = (config.get("code") or "").strip()
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
        data: dict[str, pl.Series] = {
            _sc: pl.Series(range(_st), dtype=pl.Int32),
        }
        if _cn:
            import numpy as np

            vals = np.linspace(_mn, _mx, _st)
            # Float32 to match Rust QuoteGrid schema (price-contour ingests f32)
            data[_cn] = pl.Series(vals.tolist(), dtype=pl.Float32)
        scenarios = pl.DataFrame(data).lazy()
        return lf.join(scenarios, how="cross")

    if not code:
        return ctx.func_name, scenario_expand_fn, False

    def scenario_expand_with_code(
        *dfs: _Frame,
    ) -> _Frame:
        from haute.executor import _exec_user_code

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
    # Produced: version column is always added.
    vcol = config.get("version_column", "__optimiser_version__")
    produced = {vcol} if vcol else set()
    # Referenced: column dependencies come from a runtime artifact
    # (quote_id, scenario_index, objective, constraints) — opaque.
    return produced, None


@_register(NodeType.OPTIMISER_APPLY, columns=_optimiser_apply_columns)
def _build_optimiser_apply(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    _artifact_path = config.get("artifact_path", "")
    _version_col = config.get("version_column", "__optimiser_version__")
    _source_type = config.get("sourceType", "")
    _run_id = config.get("run_id", "")
    _registered_model = config.get("registered_model", "")
    _opt_version = config.get("version", "latest")

    # Determine if we have a valid source configured
    _has_file = bool(_artifact_path) and _source_type in ("", "file")
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

        return _dispatch_apply(dfs[0] if dfs else pl.LazyFrame(), artifact, _vcol)

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
        # Unconfigured node (dev UX: blank modelScore dragged onto canvas).
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
        code=config.get("code", "").strip(),
        source_names=list(ctx.source_names),
        source=ctx.source or "live",
        row_limit=ctx.row_limit,
    )

    return ctx.func_name, scorer.score, False


@_register(NodeType.POLARS)
def _build_transform(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    code = config.get("code", "").strip()
    _src_names = list(ctx.source_names)
    _orig_src = list(ctx.orig_source_names) if ctx.orig_source_names else None
    _in_map = dict(config.get("inputMapping", {})) or None
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    if code:

        def transform_fn(*dfs: _Frame) -> _Frame:
            from haute.executor import _exec_user_code

            return _exec_user_code(
                code,
                _src_names,
                dfs,
                extra_ns=_preamble,
                orig_source_names=_orig_src,
                input_mapping=_in_map,
            )

        return ctx.func_name, transform_fn, False
    else:
        return ctx.func_name, _passthrough_fn, False


# SUBMODEL and SUBMODEL_PORT are pass-through types with no special logic.
# Register them explicitly so _build_node_fn doesn't raise on unknown types.
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

    ctx = NodeBuildContext(
        node=node,
        source_names=source_names,
        row_limit=row_limit,
        node_map=node_map,
        orig_source_names=orig_source_names,
        preamble_ns=preamble_ns,
        source=source,
    )

    builder = _NODE_BUILDERS.get(node.data.nodeType)
    if builder is not None:
        return builder(ctx)

    # Fallback for any unrecognised node type: pass-through
    return ctx.func_name, _passthrough_fn, False


# ---------------------------------------------------------------------------
# OptimiserApply helpers
# ---------------------------------------------------------------------------


def _dispatch_apply(
    lf: _Frame,
    artifact: dict[str, Any],
    version_col: str,
) -> _Frame:
    """Route to the correct apply function based on artifact mode."""
    mode = artifact.get("mode", "online")
    version = artifact.get("version", "")
    if mode == "ratebook":
        return _apply_ratebook(lf, artifact, version, version_col)
    return _apply_online(lf, artifact, version, version_col)


def _apply_online(
    lf: _Frame,
    artifact: dict[str, Any],
    version: str,
    version_col: str,
) -> _Frame:
    """Apply online optimisation: Lagrangian argmax with stored lambdas."""
    from price_contour import ApplyOptimiser

    qid_col = artifact.get("quote_id", "quote_id")
    step_col = artifact.get("scenario_index", "scenario_index")
    mult_col = artifact.get("scenario_value", "scenario_value")
    objective = artifact.get("objective", "expected_income")
    constraints = artifact.get("constraints") or {}

    # Filter out null/NaN quote IDs before casting (null → "null" string is invalid)
    lf = lf.filter(pl.col(qid_col).is_not_null())

    # Cast columns to the types price-contour expects (same as solve endpoint)
    cast_exprs = [
        pl.col(qid_col).cast(pl.Utf8),
        pl.col(step_col).cast(pl.Int32),
        pl.col(mult_col).cast(pl.Float32),
        pl.col(objective).cast(pl.Float32),
    ]
    for c in constraints:
        cast_exprs.append(pl.col(c).cast(pl.Float32))

    df_eager = lf.with_columns(cast_exprs).collect(engine="streaming")

    applier = ApplyOptimiser(
        lambdas=artifact["lambdas"],
        objective=objective,
        constraints=constraints,
        quote_id=qid_col,
        scenario_index=step_col,
        scenario_value=mult_col,
        chunk_size=artifact.get("chunk_size", _DEFAULT_CHUNK_SIZE),
    )
    result = applier.apply(df_eager)
    result_df: pl.DataFrame = result.dataframe

    if version:
        result_df = result_df.with_columns(pl.lit(version).alias(version_col))

    return result_df.lazy()


def _apply_ratebook(
    lf: _Frame,
    artifact: dict[str, Any],
    version: str,
    version_col: str,
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

    if version:
        result_lf = result_lf.with_columns(pl.lit(version).alias(version_col))

    return result_lf
