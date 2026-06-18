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

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import polars as pl

import haute.projection as projection
from haute._api_input_schema import is_json_api_input_path
from haute._code_extraction import _strip_generated_boilerplate_from_code
from haute._edge_join import (
    build_edge_join_kwargs,
    execute_edge_join,
    resolve_edge_join_role_indices,
)
from haute._execution_context import ExecutionProfile
from haute._graph_utils import _sanitize_func_name
from haute._io import load_external_object, read_data_source
from haute._logging import get_logger
from haute._output_assembler import (
    OutputMappingSchemaError,
    assemble_output_from_mapping,
    is_active_mapping_entry,
)
from haute._rating import (
    _apply_banding_factors,
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


def _source_scan_projection(
    profile: str | None,
    columns: frozenset[str] | set[str] | None,
    config: Mapping[str, Any],
) -> projection.SourceScanProjection:
    if profile in {None, ExecutionProfile.PREVIEW_EAGER.value}:
        return projection.SourceScanProjection(columns=None)
    return projection.source_scan_projection(config, columns)


def _allow_empty_source_path(profile: str | None) -> bool:
    return profile in {None, ExecutionProfile.PREVIEW_EAGER.value}


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
    target_handles: list[str | None] | None
    row_limit: int | None
    node_map: dict[str, GraphNode] | None
    orig_source_names: list[str] | None
    preamble_ns: dict[str, Any] | None
    source: str | None
    upstream_ids: list[str] | None = None
    required_output_columns: frozenset[str] | set[str] | None = None
    reuse_loaded_model: bool = False
    execution_profile: str | None = None
    #: Per-incoming-edge source *port* names — ``edge.sourceHandle or
    #: source-node-name`` — aligned positionally with the frames the executor
    #: passes. Distinct from ``source_names`` (the source *node* name, which
    #: repeats when one multi-port node feeds several edges). OUTPUT keys its
    #: frames by this so a multi-port apiInput → OUTPUT resolves each port.
    source_ports: list[str] | None = None

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


@dataclass(frozen=True, slots=True)
class Contract:
    """Small dataclass mirror of the tuple-based ``ColumnContract``.

    Used by the user-facing decorator kwarg (``contract=Contract(...)``
    or ``contract={"inputs": [...], "outputs": [...]}``) and by the
    parser/executor boundary checks.  The tuple form remains the
    builder-internal representation so existing code keeps working;
    ``Contract`` is the normalised shape that carries the distinction
    between "opaque" and a concrete empty set cleanly.

    ``inputs``  — columns the node reads from its upstream frame(s).
                  ``None`` means "opaque; can't determine statically".
    ``outputs`` — columns the node creates on its output frame.
                  ``None`` means "opaque; can't determine statically".
    """

    inputs: frozenset[str] | None
    outputs: frozenset[str] | None
    inputs_by_parent: Mapping[str, frozenset[str] | None] | None = None

    @classmethod
    def opaque(cls) -> Contract:
        """Return the canonical opaque contract (both sides unknown)."""
        return cls(inputs=None, outputs=None)

    @classmethod
    def from_tuple(cls, tup: ColumnContract) -> Contract:
        """Lift a ``(produced, referenced)`` tuple to a ``Contract``.

        Note the swap: the tuple uses ``(produced, referenced)`` — i.e.
        ``(outputs, inputs)`` — while ``Contract`` names them
        ``inputs`` then ``outputs``.  This is deliberate: the
        user-facing form mirrors Python conventions (inputs first), but
        the internal tuple was defined earlier with produced first for
        historical reasons.  The conversion is centralised here.
        """
        produced, referenced = tup
        inputs = _freeze(referenced)
        outputs = _freeze(produced)
        return cls(inputs=inputs, outputs=outputs)

    def to_tuple(self) -> ColumnContract:
        """Return the ``(produced, referenced)`` tuple form."""
        produced = set(self.outputs) if self.outputs is not None else None
        referenced = set(self.inputs) if self.inputs is not None else None
        return produced, referenced

    @classmethod
    def from_user_declared(cls, value: Any) -> Contract | None:
        """Normalise the many user-facing forms into a ``Contract``.

        Accepts:
          - ``None``                           → ``None`` (no contract declared)
          - ``Contract(...)``                  → returned as-is
          - ``"opaque"`` (case-insensitive)    → ``Contract.opaque()``
          - ``(None, None)``                   → ``Contract.opaque()``
          - ``{"inputs": [...], "outputs": [...]}``  → ``Contract(...)``
          - ``(inputs, outputs)`` tuple of iterables  → ``Contract(...)``

        Anything else raises ``ValueError``.  Failing loud is better than
        silently accepting an ill-formed declaration — a typo'd key in
        the pipeline source is precisely the kind of error this feature
        exists to catch.
        """
        if value is None:
            return None
        if isinstance(value, Contract):
            return value
        if hasattr(value, "inputs") and hasattr(value, "outputs"):
            return cls(
                inputs=_freeze(value.inputs),
                outputs=_freeze(value.outputs),
                inputs_by_parent=_freeze_mapping(getattr(value, "inputs_by_parent", None)),
            )
        if isinstance(value, str):
            if value.strip().lower() == OPAQUE_CONTRACT_SENTINEL:
                return cls.opaque()
            raise ValueError(
                f"Invalid contract declaration: unknown string {value!r}. "
                f"The only accepted string form is {OPAQUE_CONTRACT_SENTINEL!r}.",
            )
        if isinstance(value, dict):
            unknown_keys = set(value) - {"inputs", "outputs", "inputs_by_parent"}
            if unknown_keys:
                raise ValueError(
                    "Invalid contract dict: unknown key(s) "
                    f"{sorted(unknown_keys)!r}; expected 'inputs', 'outputs', "
                    "and optional 'inputs_by_parent'.",
                )
            inputs_raw = value.get("inputs", ...)
            outputs_raw = value.get("outputs", ...)
            if inputs_raw is ... or outputs_raw is ...:
                raise ValueError(
                    "Invalid contract dict: expected both 'inputs' and "
                    f"'outputs' keys, got {sorted(value)}.",
                )
            return cls(
                inputs=_freeze(inputs_raw),
                outputs=_freeze(outputs_raw),
                inputs_by_parent=_freeze_mapping(value.get("inputs_by_parent")),
            )
        if isinstance(value, tuple) and len(value) == 2:
            a, b = value
            # Tuple form is (inputs, outputs) on the user-facing side to
            # match Contract's field order.
            return cls(inputs=_freeze(a), outputs=_freeze(b))
        raise ValueError(
            f"Invalid contract declaration: unsupported type {type(value).__name__}; "
            "expected Contract, dict(inputs=..., outputs=...), 'opaque', or None.",
        )


def _freeze(value: Any) -> frozenset[str] | None:
    """Coerce an iterable of column names to ``frozenset[str]`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, frozenset):
        return value
    if isinstance(value, (set, list, tuple)) or (
        isinstance(value, Iterable) and not isinstance(value, (str, bytes))
    ):
        out: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"Contract column names must be strings; got {type(item).__name__} ({item!r}).",
                )
            out.add(item)
        return frozenset(out)
    raise ValueError(
        f"Contract column set must be iterable; got {type(value).__name__}.",
    )


def _freeze_mapping(value: Any) -> dict[str, frozenset[str] | None] | None:
    """Coerce a parent-id -> column-set mapping used by fan-in contracts."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(
            "Contract inputs_by_parent must be a mapping of parent node ids to column sets.",
        )
    out: dict[str, frozenset[str] | None] = {}
    for parent_id, columns in value.items():
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError(
                "Contract inputs_by_parent keys must be non-empty parent node ids.",
            )
        out[parent_id] = _freeze(columns)
    return out


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


def _passthrough_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
    """Shared passthrough: return the first incoming frame or an empty LazyFrame.

    Per MULTI_FRAME_PLAN §4b the executor binds incoming edges to the
    consumer function as keyword arguments keyed by ``sourceHandle or
    source_node_label``. The function also accepts positional args so
    direct callers (tests that drive the builder-produced function
    in isolation) keep working. When both forms are supplied the keyword
    form takes precedence; ``next(iter(dfs_by_name.values()))`` preserves
    the edge-declaration order the executor inserts into the dict.
    """
    if dfs_by_name:
        return next(iter(dfs_by_name.values()))
    return dfs_positional[0] if dfs_positional else pl.LazyFrame()


def _explore_fn(df: _Frame) -> _Frame:
    """Explore is a terminal analysis node, but preview still reflects its input."""
    return df


def _explore_columns(config: dict[str, Any]) -> ColumnContract:
    """Explore code can derive/filter arbitrary analysis columns."""
    return OPAQUE_CONTRACT if (config.get("code") or "").strip() else _passthrough_columns(config)


def _make_api_source_v2(
    data_path: str,
    config: dict[str, Any],
) -> Callable[..., Any]:
    """Build the runtime source function for a v2 apiInput.

    Behaviour at call time:

    - 0 emit-true tables → raise a clear RuntimeError (the editor's
      empty-state message; the user has to tick at least one ``emit``
      before previewing).
    - 1 emit-true table → return a bare LazyFrame (single-frame shorthand;
      existing edges with null sourceHandle keep binding to this node via
      MULTI_FRAME_PLAN §4b's source-label fallback).
    - 2+ emit-true tables → return a ``dict[port_label, LazyFrame]``. The
      executor's edge-resolution picks one frame per outgoing edge using
      ``edge.sourceHandle``.

    The cache directory is the dual-cache ``working/<hash>/`` layer (commit
    3's per-frame shred output). If the cache isn't valid, raise with the
    "click Cache as Parquet" message — same UX shape as the v1 path. No
    auto-build in this commit; that's intentional ergonomic discipline
    (see DUAL_CACHE.md §4 — caching is an explicit user action).
    """
    from haute._api_input_schema import validate_v2_schema
    from haute._json_shred import load_v2_api_source

    # Validate at build time so a malformed config fails before any data is
    # fetched. The emit-state checks + cache resolution + single/multi-frame
    # return live in the shared `load_v2_api_source` so the generated/deploy
    # code path (codegen) and this runtime path can't drift.
    validate_v2_schema(config)

    def _api_source_v2(
        _data_path: str = data_path,
        _config: dict[str, Any] = config,
    ) -> _Frame | dict[str, _Frame]:
        return load_v2_api_source(_data_path, _config)

    return _api_source_v2


@_register(NodeType.API_INPUT, opaque=True)
def _build_api_input(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    path = config.get("path", "")

    api_source_fn: Callable[..., Any]
    if is_json_api_input_path(path):
        # v2 per-frame shred is the only JSON apiInput codec. When the
        # config carries `tables[]` we dispatch into the v2 source
        # builder (emit-true count decides bare frame vs dict[label,
        # frame]). Anything else is an editor-state error: the user must
        # populate `tables[]` via the Infer Tables button before the
        # pipeline can run.
        from haute._api_input_schema import is_v2_shape as _is_v2_shape

        if _is_v2_shape(config):
            return ctx.func_name, _make_api_source_v2(path, config), True

        def _api_source_no_tables(
            _label: str = ctx.func_name,
        ) -> _Frame:
            raise RuntimeError(
                f"API Input '{_label}' has no v2 schema (tables[]). Open the "
                "node and click 'Infer Tables' to populate the schema "
                "mapping, then click 'Cache as Parquet'."
            )

        api_source_fn = _api_source_no_tables
    else:

        def _api_source_flat(
            _profile: str | None = ctx.execution_profile,
            _columns: frozenset[str] | set[str] | None = ctx.required_output_columns,
            _config: dict[str, Any] = config,
        ) -> _Frame:
            projected = _source_scan_projection(_profile, _columns, _config)
            return read_data_source(
                _config,
                profile=_profile,
                columns=projected.columns,
                validate_columns=projected.validate_columns,
            )

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
    code_preserves_projection = projection.source_user_code_preserves_column_projection(code)
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    base_fn: Callable[..., _Frame]
    if not code:

        def plain_source_fn(
            _config: Mapping[str, Any] = config,
            _profile: str | None = ctx.execution_profile,
            _columns: frozenset[str] | set[str] | None = ctx.required_output_columns,
        ) -> _Frame:
            if source_type != "databricks" and not path and _allow_empty_source_path(_profile):
                return pl.LazyFrame()
            projected = _source_scan_projection(_profile, _columns, _config)
            return read_data_source(
                _config,
                profile=_profile,
                columns=projected.columns,
                validate_columns=projected.validate_columns,
            )

        return ctx.func_name, plain_source_fn, True

    if source_type == "databricks":
        table = config.get("table", "")

        def _databricks_source(
            _table: str = table,
            _profile: str | None = ctx.execution_profile,
            _columns: frozenset[str] | set[str] | None = ctx.required_output_columns,
            _config: dict[str, Any] = config,
        ) -> _Frame:
            from haute._databricks_io import read_cached_table

            lf = read_cached_table(_table)
            projected = _source_scan_projection(
                _profile,
                _columns if code_preserves_projection else None,
                _config,
            )
            if projected.validate_columns:
                source_columns = set(lf.collect_schema().names())
                missing = projected.validate_columns - source_columns
                if missing:
                    raise ValueError(
                        "source selected_columns references columns missing from "
                        f"the source schema: {sorted(missing)!r}"
                    )
            if projected.columns is not None:
                return lf.select(list(projected.columns))
            return lf

        base_fn = _databricks_source
    else:

        def source_fn(
            _config: Mapping[str, Any] = config,
            _profile: str | None = ctx.execution_profile,
            _columns: frozenset[str] | set[str] | None = ctx.required_output_columns,
        ) -> _Frame:
            if not path and _allow_empty_source_path(_profile):
                return pl.LazyFrame()
            projected = _source_scan_projection(
                _profile,
                _columns if code_preserves_projection else None,
                _config,
            )
            return read_data_source(
                _config,
                profile=_profile,
                columns=projected.columns,
                validate_columns=projected.validate_columns,
            )

        base_fn = source_fn

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

    def switch_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        # Build a positional view in declared-source order so existing
        # logic that picks by index still works. The wrapper accepts
        # both kwarg (executor) and positional (direct test caller) forms.
        if dfs_by_name:
            dfs = tuple(dfs_by_name[name] for name in input_names if name in dfs_by_name)
        else:
            dfs = dfs_positional
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


@_register(NodeType.EXPLORE, columns=_explore_columns)
def _build_explore(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    code = _strip_generated_boilerplate_from_code(
        ctx.config.get("code") or "",
        kind="polars",
        param_names=ctx.source_names,
    )
    if not code:
        return ctx.func_name, _explore_fn, False

    _src_names = list(ctx.source_names)
    _orig_src = list(ctx.orig_source_names) if ctx.orig_source_names else None
    _in_map = dict(ctx.config.get("inputMapping", {})) or None
    _preamble = dict(ctx.preamble_ns) if ctx.preamble_ns else None

    def explore_with_code(df: _Frame) -> _Frame:
        return _exec_user_code(
            code,
            _src_names,
            (df,),
            extra_ns=_preamble,
            orig_source_names=_orig_src,
            input_mapping=_in_map,
        )

    return ctx.func_name, explore_with_code, False


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

        def external_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
            ens = {"obj": load_external_object(path, file_type, model_class)}
            ens.update(_preamble_ext)
            if dfs_by_name:
                dfs = tuple(dfs_by_name[name] for name in _src_names if name in dfs_by_name)
            else:
                dfs = dfs_positional
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


def _output_columns(config: dict[str, Any]) -> ColumnContract:
    """Column contract for an OUTPUT node: it reads the mapping's source columns.

    Produces nothing into the column space (it is terminal and emits a JSON
    document, not projectable columns); references every ACTIVE source column
    (enabled + fully filled in) so projection keeps them alive upstream. An
    incomplete row (blank source column, e.g. a half-built editor row) is
    skipped — it must not demand a ``""`` column from the upstream frame.
    """
    mapping = config.get("outputMapping") or []
    referenced = {e["source_column"] for e in mapping if is_active_mapping_entry(e)}
    return (set(), referenced)


@_register(NodeType.OUTPUT, columns=_output_columns)
def _build_output(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    config = ctx.config
    mapping = config.get("outputMapping")
    if mapping is None:
        raise OutputMappingSchemaError(
            f"OUTPUT node {ctx.node.data.label!r} has no `outputMapping`; the "
            "legacy `fields` shape is no longer supported — open the OUTPUT "
            "editor to migrate.",
        )

    # The executor binds incoming edges positionally — ``fn(*input_lfs)`` in
    # _execute_lazy, ordered by incoming edge — not as kwargs-by-port. So
    # recover the ``{source_port: frame}`` map the assembler wants from the
    # positional order. ``ctx.source_ports[i]`` is edge *i*'s port name
    # (``sourceHandle or source-node-name``), which both aligns with
    # ``input_lfs[i]`` and disambiguates a multi-port source (one apiInput
    # feeding several edges has one node name but distinct sourceHandles).
    # Fall back to ``source_names`` when a caller didn't supply ports (e.g. a
    # direct ``_build_node_fn`` call in a unit test).
    source_ports = list(ctx.source_ports if ctx.source_ports is not None else ctx.source_names)
    referenced_ports = {e["source_port"] for e in mapping if e.get("enabled", True)}
    label = ctx.node.data.label

    def output_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        positional = [lf.lazy() for lf in dfs_positional]
        named = {name: lf.lazy() for name, lf in dfs_by_name.items()}
        frames: dict[str, _Frame] = dict(zip(source_ports, positional, strict=False))
        # A future kwarg-by-port executor binding would win over the positional
        # reconstruction; until then ``dfs_by_name`` is empty here.
        frames.update(named)
        # Single-parent OUTPUT carries exactly one frame, so whichever
        # ``source_port`` the editor named (the upstream *table* label) resolves
        # to it — that name need not equal the sanitized *node* label the
        # executor uses as the positional key (and may be absent entirely when a
        # builder is invoked without edge wiring). A genuine multi-frame OUTPUT
        # (≥ 2 incoming frames) requires the names to line up and fails loud
        # below. Gate on the incoming-frame *count*, not the reconstructed dict,
        # so an unnamed lone frame still resolves.
        incoming = positional + list(named.values())
        if len(incoming) == 1 and referenced_ports:
            frames = {port: incoming[0] for port in referenced_ports}
        missing = referenced_ports - frames.keys()
        if missing:
            raise OutputMappingSchemaError(
                f"OUTPUT node {label!r} maps source frame(s) {sorted(missing)!r} "
                f"that no incoming edge provides; available frames: "
                f"{sorted(frames.keys())!r}.",
            )
        document = assemble_output_from_mapping(frames, mapping)
        return pl.LazyFrame(document)

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
    # Capture immutable copy at builder-time so a later mutation of the
    # original ``factors`` list can't leak into a built function. Previously
    # achieved via a default-arg trick that doesn't compose with **kwargs.
    _factors_captured: tuple[dict[str, Any], ...] = tuple(dict(f) for f in factors)

    def banding_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        if dfs_by_name:
            lf = next(iter(dfs_by_name.values()))
        else:
            lf = dfs_positional[0] if dfs_positional else pl.LazyFrame()
        # Shared with apply_banding_from_config (generated standalone code)
        # so the canvas and the saved file cannot drift.
        return _apply_banding_factors(lf, _factors_captured)

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

    _tables_captured: list = list(tables)
    _combined_outputs_captured: list[dict[str, Any]] = list(combined_outputs)
    _code_captured: str = code

    def rating_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        if dfs_by_name:
            lf = next(iter(dfs_by_name.values()))
        else:
            lf = dfs_positional[0] if dfs_positional else pl.LazyFrame()
        lf = _apply_rating_step_outputs(lf, _tables_captured, _combined_outputs_captured)
        if _code_captured:
            lf = _exec_user_code(_code_captured, ["df"], (lf,), extra_ns=_preamble)
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

    def scenario_expand_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        if dfs_by_name:
            lf = next(iter(dfs_by_name.values()))
        else:
            lf = dfs_positional[0] if dfs_positional else pl.LazyFrame()
        scenario_exprs = [pl.lit(list(range(_steps))).alias(_step_col)]
        explode_cols = [_step_col]
        if _col_name:
            import numpy as np

            vals = np.linspace(_min_val, _max_val, _steps)
            # Float32 to match Rust QuoteGrid schema (price-contour ingests f32)
            scenario_exprs.append(pl.lit(vals.astype("float32").tolist()).alias(_col_name))
            explode_cols.append(_col_name)
        cast_exprs = [pl.col(_step_col).cast(pl.Int32)]
        if _col_name:
            cast_exprs.append(pl.col(_col_name).cast(pl.Float32))
        return lf.with_columns(scenario_exprs).explode(explode_cols).with_columns(cast_exprs)

    if not code:
        return ctx.func_name, scenario_expand_fn, False

    def scenario_expand_with_code(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        if dfs_by_name:
            expanded = scenario_expand_fn(**dfs_by_name)
        else:
            expanded = scenario_expand_fn(*dfs_positional)
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

            _i_captured = idx
            _src_names_captured = list(ctx.source_names)

            def _optimiser_select(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
                if dfs_by_name:
                    dfs = tuple(
                        dfs_by_name[name] for name in _src_names_captured if name in dfs_by_name
                    )
                else:
                    dfs = dfs_positional
                if len(dfs) <= _i_captured:
                    raise ValueError(
                        f"Optimiser expected input at index {_i_captured} but only "
                        f"received {len(dfs)} input(s)",
                    )
                return dfs[_i_captured]

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

    def optimiser_apply_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        # Closure-captured to mirror the previous default-arg snapshot pattern.
        _path = _artifact_path
        _vcol = _version_col
        _st = _source_type
        _rid = _run_id
        _rm = _registered_model
        _ver = _opt_version
        _opt_col = _optimised_value_col
        _rb_input = _ratebook_input
        _src_names = _source_names
        _src_ids = _source_ids
        # Reconstruct positional tuple in declared-source order for the
        # downstream helper that still consumes positionals.
        if dfs_by_name:
            dfs = tuple(dfs_by_name[name] for name in _src_names if name in dfs_by_name)
        else:
            dfs = dfs_positional
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
    code = _strip_generated_boilerplate_from_code(
        config.get("code") or "",
        kind="model_score",
        param_names=("df",),
    )
    if code:
        return produced, None

    feature_contract_path = config.get("feature_contract_path")
    if isinstance(feature_contract_path, str) and feature_contract_path:
        # Stat-gated cache: this planner runs during graph construction on
        # every deployed /quote and every preview — re-reading/re-hashing an
        # unchanged contract per request is pure latency (W2 4a.3).
        from haute.modelling._feature_contract import load_contract_cached

        contract = load_contract_cached(feature_contract_path)
        return produced, set(contract.features)

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


def _declared_categorical_levels_for_model_score(
    config: dict[str, Any],
    source_ids: list[str],
    node_map: dict[str, GraphNode] | None,
    upstream_ids: list[str] | None = None,
) -> dict[str, list[str | None]]:
    """Merge explicit categorical level declarations at a modelScore boundary."""
    from haute.modelling._feature_contract import merge_categorical_level_declarations

    declarations: list[tuple[str, Any]] = [("modelScore", config.get("categorical_levels"))]
    candidate_ids = list(dict.fromkeys([*source_ids, *(upstream_ids or [])]))
    if node_map is not None:
        declarations.extend(
            (source_id, node_map[source_id].data.config.get("categorical_levels"))
            for source_id in candidate_ids
            if source_id in node_map
        )
    return merge_categorical_level_declarations(declarations)


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

    required_output_columns = projection.model_score_required_output_columns(
        config,
        ctx.required_output_columns,
        post_processing_code=code,
    )
    declared_categorical_levels = _declared_categorical_levels_for_model_score(
        config,
        ctx.source_ids,
        ctx.node_map,
        ctx.upstream_ids,
    )

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
        required_output_columns=required_output_columns,
        feature_contract_path=config.get("feature_contract_path") or None,
        categorical_levels=declared_categorical_levels,
        reuse_loaded_model=ctx.reuse_loaded_model,
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

        def transform_fn(*dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
            if dfs_by_name:
                dfs = tuple(dfs_by_name[name] for name in _src_names if name in dfs_by_name)
            else:
                dfs = dfs_positional
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


@_register(NodeType.EDGE_JOIN, opaque=True)
def _build_edge_join(ctx: NodeBuildContext) -> tuple[str, Callable, bool]:
    base_index, join_index = resolve_edge_join_role_indices(
        ctx.config,
        ctx.source_ids,
        ctx.target_handles,
    )
    build_edge_join_kwargs(ctx.config)
    source_ids = list(ctx.source_ids)

    def edge_join_fn(*dfs: _Frame) -> _Frame:
        if len(dfs) != len(source_ids):
            from haute.errors import ConfigError

            raise ConfigError(
                "edgeJoin received a different number of frames than connected inputs.",
                expected=len(source_ids),
                received=len(dfs),
                connected_input_node_ids=source_ids,
            )
        return cast(_Frame, execute_edge_join(dfs[base_index], dfs[join_index], ctx.config))

    return ctx.func_name, edge_join_fn, False


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
    target_handles: list[str | None] | None = None,
    source_ports: list[str] | None = None,
    row_limit: int | None = None,
    node_map: dict[str, GraphNode] | None = None,
    orig_source_names: list[str] | None = None,
    upstream_ids: list[str] | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str | None = None,
    required_output_columns: frozenset[str] | set[str] | None = None,
    reuse_loaded_model: bool = False,
    execution_profile: str | None = None,
) -> tuple[str, Callable, bool]:
    """Build an executable function from a graph node dict.

    Returns (func_name, fn, is_source).
    source_names: sanitized names of upstream nodes (used as variable names).
    row_limit: if set, Databricks sources push this into SQL LIMIT so the
        full table is never fetched during preview/trace.
    node_map: full graph node_map — used to resolve ``instanceOf`` references.
    source: the active execution source (``"live"`` for eager scoring,
        anything else for batched parquet scoring).
    reuse_loaded_model: opts modelScore nodes into scorer-instance model
        reuse for chunked callers that rebuild data but not node functions.
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
        target_handles=target_handles,
        source_ports=source_ports,
        row_limit=row_limit,
        node_map=node_map,
        orig_source_names=orig_source_names,
        upstream_ids=upstream_ids,
        preamble_ns=preamble_ns,
        source=source,
        required_output_columns=required_output_columns,
        reuse_loaded_model=reuse_loaded_model,
        execution_profile=execution_profile,
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
    from haute._execution_context import ExecutionProfile
    from haute._polars_utils import streaming_collect

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

    return streaming_collect(
        lf.with_columns(cast_exprs),
        profile=ExecutionProfile.LAZY_SINK,
    )


# Composite ratebook factor groups (3b.2): price-contour names a composite
# factor table by colon-joining its component columns (``":".join(spec)``)
# and keys each level by joining the component values with the ASCII unit
# separator (its documented ``separator`` default, mirrored verbatim by the
# save path through JSON's ``\u001f`` escape).  Splitting on these two
# separators is therefore the library-canonical decoding — the same one
# ``RatebookResult.to_rating_entries`` performs.
_RATEBOOK_GROUP_NAME_SEPARATOR = ":"
_RATEBOOK_GROUP_LEVEL_SEPARATOR = "\x1f"
_RATEBOOK_FACTOR_GROUP_KEY = "__factor_group__"


def _ratebook_table_is_composite(levels: Iterable[Any]) -> bool:
    """A saved factor table is composite iff any level embeds the unit separator.

    A join of two or more component values always contains the separator, and
    an ASCII control character never appears in real level labels — so the
    artifact is self-describing.  A table whose NAME contains ``":"`` but
    whose levels carry no separator is a literal single column named
    ``"a:b"`` and joins as such.
    """
    return any(
        isinstance(level, str) and _RATEBOOK_GROUP_LEVEL_SEPARATOR in level for level in levels
    )


def _ratebook_join_columns(table_name: str) -> list[str]:
    """Component join columns of a composite factor table name.

    Only called for tables whose levels are unit-separator joined, so the
    name MUST decompose into two or more distinct, non-empty column names.
    Anything else is a malformed artifact and fails loudly.
    """
    columns = table_name.split(_RATEBOOK_GROUP_NAME_SEPARATOR)
    if (
        len(columns) < 2
        or any(not column for column in columns)
        or len(set(columns)) != len(columns)
    ):
        raise ValueError(
            f"optimiserApply ratebook factor table {table_name!r} has composite "
            "levels (unit-separator joined) but its name does not decompose into "
            "two or more distinct non-empty column names joined by "
            f"{_RATEBOOK_GROUP_NAME_SEPARATOR!r}"
        )
    return columns


def _split_ratebook_level(level: Any, join_columns: list[str], table_name: str) -> list[str]:
    """Split one composite level into its component values, arity-checked."""
    parts = str(level).split(_RATEBOOK_GROUP_LEVEL_SEPARATOR)
    if len(parts) != len(join_columns):
        raise ValueError(
            f"optimiserApply ratebook factor table {table_name!r} level {level!r} "
            f"splits into {len(parts)} component value(s) but the table joins on "
            f"{len(join_columns)} column(s) {join_columns!r}"
        )
    return parts


def _ratebook_lookup_table(name: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convert one saved factor table into a rating-table lookup spec.

    Returns ``None`` when no entry carries ``__factor_group__`` (skipped with
    a warning, matching the long-standing behaviour for malformed rows).

    Miss policy (3b.5): the spec deliberately opts in to ``onMissing:
    "neutral"`` with NO ``defaultValue``.  An optimiser relativity is a
    multiplicative adjustment on an already-rated price, so a level the
    solver never saw keeps the base price (factor 1.0) — failing the whole
    apply would turn one novel quote into a deploy-scoring outage.  Unlike
    the old blanket ``defaultValue: "1.0"``, the neutral path is LOUD: the
    rating miss guard counts every miss and logs ``rating_table_lookup_misses``
    (table, count, missing keys) at materialisation, and the explainability
    ladder flags the row as ``unseen``.
    """
    raw_entries = [entry for entry in entries if _RATEBOOK_FACTOR_GROUP_KEY in entry]
    skipped = len(entries) - len(raw_entries)
    if skipped:
        logger.warning(
            "ratebook_entries_missing_factor_group",
            factor=name,
            skipped=skipped,
            total=len(entries),
        )
    if not raw_entries:
        return None

    levels = [entry[_RATEBOOK_FACTOR_GROUP_KEY] for entry in raw_entries]
    if _ratebook_table_is_composite(levels):
        join_columns = _ratebook_join_columns(name)
        lookup_entries = []
        for entry in raw_entries:
            parts = _split_ratebook_level(entry[_RATEBOOK_FACTOR_GROUP_KEY], join_columns, name)
            lookup_entry: dict[str, Any] = dict(zip(join_columns, parts))
            lookup_entry["value"] = entry["optimal_scenario_value"]
            lookup_entries.append(lookup_entry)
    else:
        join_columns = [name]
        lookup_entries = [
            {name: entry[_RATEBOOK_FACTOR_GROUP_KEY], "value": entry["optimal_scenario_value"]}
            for entry in raw_entries
        ]

    return {
        "name": name,
        "factors": join_columns,
        "outputColumn": f"{name}_optimised_factor",
        "entries": lookup_entries,
        "onMissing": "neutral",
    }


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

    Composite groups (table name ``"channel:age_band"``) join on their
    component columns, decoded from the artifact's unit-separator level
    keys — see :func:`_ratebook_lookup_table`.  Unseen factor levels rate
    1.0 with a counted ``rating_table_lookup_misses`` WARNING per table
    (3b.5) — neutral, never silent.
    """
    factor_tables = artifact.get("factor_tables", {})
    schema_by_name: dict[str, Any]
    if hasattr(lf, "collect_schema"):
        collected_schema = lf.collect_schema()
        schema_by_name = {name: collected_schema[name] for name in collected_schema.names()}
    else:
        schema_by_name = dict(zip(lf.columns, lf.dtypes))
    available = set(schema_by_name)

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
            table = _ratebook_lookup_table(_name, entries)
            if table is None:
                continue
            out_col: str = table["outputColumn"]
            missing = [column for column in table["factors"] if column not in available]
            if missing:
                raise ValueError(
                    f"optimiserApply ratebook factor table {_name!r} requires join "
                    f"column(s) {table['factors']!r} but the input frame is missing "
                    f"{missing!r}"
                )
            result_lf = _apply_rating_table(result_lf, table, input_schema=schema_by_name)
            # Neutral fill AFTER the miss guard has counted and logged the
            # misses inside the plan: per-factor columns stay 1.0 for unseen
            # levels (the multiplicative neutral element), so the combined
            # relativity and any downstream price arithmetic never see nulls.
            result_lf = result_lf.with_columns(pl.col(out_col).fill_null(1.0))
            factor_cols.append(out_col)
            available.add(out_col)
            schema_by_name[out_col] = pl.Float64

        # Combine individual factor columns into a single relativity
        if len(factor_cols) > 1:
            result_lf = _combine_rating_columns(
                result_lf,
                factor_cols,
                "multiply",
                "optimised_factor",
            )
            available.add("optimised_factor")
            schema_by_name["optimised_factor"] = pl.Float64
        elif len(factor_cols) == 1:
            result_lf = result_lf.with_columns(
                pl.col(factor_cols[0]).alias("optimised_factor"),
            )
            available.add("optimised_factor")
            schema_by_name["optimised_factor"] = pl.Float64

    if optimised_value_col and optimised_value_col != "optimised_factor":
        if "optimised_factor" not in available:
            raise ValueError(
                "optimiserApply configured optimised_value_column but ratebook apply "
                "did not produce optimised_factor",
            )
        result_lf = result_lf.rename({"optimised_factor": optimised_value_col})
        available.discard("optimised_factor")
        available.add(optimised_value_col)
        schema_by_name[optimised_value_col] = schema_by_name.pop("optimised_factor")

    if version:
        result_lf = result_lf.with_columns(pl.lit(version).alias(version_col))
        available.add(version_col)
        schema_by_name[version_col] = pl.String

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
