"""Runtime scoring engine for deployed pipelines.

Uses the same lazy execution / ``_build_node_fn`` infrastructure as
the development executor, with ``NodeBuildHooks`` to override specific
node types for live scoring:

- Injects live input DataFrames at apiInput source nodes
- Remaps artifact paths for externalFile and static dataSource nodes
- Returns a single collected DataFrame from the output node
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import polars as pl

import haute.projection as projection
from haute._cache import canonical_json
from haute._code_extraction import _strip_generated_boilerplate_from_code
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_utils import upstream_node_ids
from haute._hashing import content_hash_bytes
from haute._io import load_external_object, read_data_source
from haute._logging import get_logger
from haute._node_builder import NodeBuildHooks, NodeFnResult, node_fn_name, wrap_builder
from haute._polars_utils import streaming_collect
from haute._stat_gated_cache import StatGatedCache, artifact_cache_key, resolve_artifact_path
from haute._types import (
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.execution import (
    _stat_gated_runtime_path_fingerprint,
    build_dataframe_execution_cache_request,
    dataframe_frame_input_fingerprint,
    dataframe_graph_input_fingerprint,
    execute_lazy_graph,
)
from haute.executor import _build_node_fn

if TYPE_CHECKING:
    from haute._mlflow_io import ScoringModel
    from haute.modelling._feature_contract import FeatureContract

_RUNTIME_PATH_NODE_TYPES = frozenset(
    {
        NodeType.API_INPUT,
        NodeType.DATA_SOURCE,
        NodeType.EXTERNAL_FILE,
        NodeType.DATA_SINK,
    }
)
logger = get_logger(component="deploy_scorer")

# ---------------------------------------------------------------------------
# Stat-gated artifact caches (model + feature contract)
# ---------------------------------------------------------------------------
#
# A deployed container serves every ``/quote`` from the same bundled
# artifacts; reloading the model and re-reading/re-hashing the feature
# contract per request turns disk parsing into per-quote latency.  Models
# are cached by ``(resolved path, task)``, contracts by resolved path, both
# gated on ``(st_mtime_ns, st_size)`` — the same invalidation discipline as
# :func:`haute.execution._stat_gated_runtime_path_fingerprint`.  One slot
# per key, replaced when the stat gate changes, so the caches stay bounded
# by the bundle's artifact count.
#
# Concurrency: the first ``/quote`` to need an artifact loads it under a
# per-key lock; concurrent requests wait and reuse the cached value, so a
# thundering herd on container start performs exactly one disk load.
# Failed loads are never cached, and cached values are shared across
# requests/threads — treated as immutable.  (See
# :class:`haute._stat_gated_cache.StatGatedCache` for the full contract.)

_local_model_cache: StatGatedCache[tuple[str, str], ScoringModel] = StatGatedCache(
    artifact_kind="deploy model artifact"
)


def _load_local_model_cached(path: str, task: str) -> ScoringModel:
    """Stat-gated process cache over :func:`haute._mlflow_io.load_local_model`."""
    # The SLOT key is case-folded (normcase; a no-op on POSIX, so a macOS
    # case-variant spelling still gets its own slot — accepted, as in
    # haute._json_flatten._path_hash). The stat/open path keeps the on-disk
    # case: a folded spelling need not exist on a case-sensitive filesystem.
    io_path = resolve_artifact_path(path)
    key = artifact_cache_key(io_path)

    def _load() -> ScoringModel:
        from haute._mlflow_io import load_local_model

        return load_local_model(io_path, task)

    return _local_model_cache.get_or_load((key, task), io_path, _load)


def _load_feature_contract_cached(path: str) -> FeatureContract:
    """Stat-gated process cache over the bundled feature-contract read.

    Only the disk read + hash verification is cached — contract MATCHING
    against the live request schema still runs per request.  Shared with
    the executor's column-contract planner via
    :func:`haute.modelling._feature_contract.load_contract_cached`.
    """
    from haute.modelling._feature_contract import load_contract_cached

    return load_contract_cached(path)


def _clear_deploy_artifact_caches() -> None:
    """Drop every cached deploy artifact (test isolation / targeted resets)."""
    from haute.modelling._feature_contract import _clear_contract_cache

    _local_model_cache.clear()
    _clear_contract_cache()


@dataclass(slots=True)
class DeployScorePlan:
    """Lazy deployed scoring result plus resources that must survive collection."""

    lazy_frame: pl.LazyFrame
    execution_context: ExecutionContext
    temporary_paths: list[str] = field(default_factory=list)
    retained_lazy_frames: list[pl.LazyFrame] = field(default_factory=list, repr=False)
    _cleaned_up: bool = False

    def cleanup(self, *, preserve_primary_error: bool) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            _cleanup_model_score_temp_paths(
                self.temporary_paths,
                preserve_primary_error=preserve_primary_error,
            )
        finally:
            self.retained_lazy_frames.clear()
            self.execution_context.release_admission()


def deploy_execution_profile(row_count: int) -> ExecutionProfile:
    """Return the execution profile for a deploy scoring payload."""
    if row_count < 0:
        raise ValueError("row_count must be non-negative")
    return ExecutionProfile.DEPLOY_BATCH if row_count > 1 else ExecutionProfile.DEPLOY_LIVE


def admit_deploy_execution(*, operation: str, row_count: int) -> ExecutionContext:
    """Create an admitted deploy execution context from request metadata."""
    return create_admitted_execution_context(
        operation=operation,
        profile=deploy_execution_profile(row_count),
    )


def _deploy_model_score_source(execution_context: ExecutionContext) -> str:
    """Return the modelScore source contract for the admitted deploy profile."""
    if execution_context.profile == ExecutionProfile.DEPLOY_BATCH:
        return ExecutionProfile.DEPLOY_BATCH.value
    return "live"


def _model_score_has_configured_source(config: dict[str, Any]) -> bool:
    """Return whether a modelScore node has enough config to load a model."""
    source_type = config.get("sourceType", "")
    if not source_type:
        return False
    if source_type == "run" and not config.get("run_id", ""):
        return False
    if source_type == "registered" and not config.get("registered_model", ""):
        return False
    return True


def _cleanup_model_score_temp_paths(
    paths: list[str],
    *,
    preserve_primary_error: bool,
) -> None:
    if not paths:
        return
    from haute._model_scorer import _cleanup_registered_temp_files

    try:
        _cleanup_registered_temp_files(paths)
    except OSError as exc:
        if preserve_primary_error:
            logger.warning(
                "deploy_model_score_temp_cleanup_failed_after_error",
                error=str(exc),
                paths=list(paths),
            )
            return
        raise


def _deploy_artifact_paths_input_fingerprint(paths: Mapping[str, str]) -> Mapping[str, object]:
    """Return stat-gated fingerprints for deployed artifact path inputs."""

    payload: dict[str, object] = {}
    for key, raw_path in sorted(paths.items()):
        if not isinstance(key, str) or not key:
            raise ValueError("external path fingerprint keys must be non-empty strings")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("external path fingerprint values must be non-empty strings")
        payload[key] = _stat_gated_runtime_path_fingerprint(Path(raw_path))
    return payload


def artifact_identity_fingerprint(artifact_paths: Mapping[str, str] | None) -> str:
    """Deterministic identity fingerprint of the resolved deploy artifacts.

    Folds every bundled artifact's key together with a stat-gated fingerprint
    of its bytes (resolved path + ``(mtime_ns, size)`` / content hash, via
    :func:`_deploy_artifact_paths_input_fingerprint`) into a single string.

    Cache keys that must reflect the *served* model — the deploy output-schema
    cache above all — mix this in so retraining a model in place (same
    ``run_id`` / ``version="latest"``, byte-identical graph config) still busts
    the cache instead of baking a stale ``ModelSignature`` into the manifest.

    Returns ``""`` when there are no artifacts so graphs that bundle none keep
    byte-identical cache keys (and existing cache entries stay valid).
    """
    if not artifact_paths:
        return ""
    payload = _deploy_artifact_paths_input_fingerprint(artifact_paths)
    return "artifacts=" + content_hash_bytes(canonical_json(dict(payload)).encode())


def _resolve_runtime_graph_paths(graph: PipelineGraph) -> PipelineGraph:
    """Resolve path config values against ``graph.source_file`` for deploy scoring."""
    if not graph.source_file:
        return graph
    base_dir = Path(graph.source_file).parent
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        raw_path = config.get("path")
        if (
            node.data.nodeType in _RUNTIME_PATH_NODE_TYPES
            and isinstance(raw_path, str)
            and raw_path
            and not Path(raw_path).is_absolute()
        ):
            resolved = str((base_dir / raw_path).resolve())
            data = node.data.model_copy(update={"config": {**config, "path": resolved}})
            nodes.append(node.model_copy(update={"data": data}))
            changed = True
        else:
            nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def _attach_bundled_feature_contracts(
    graph: PipelineGraph,
    remap: dict[str, str],
) -> PipelineGraph:
    """Annotate bundled modelScore nodes with their local feature contract."""
    if not remap:
        return graph
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        if node.data.nodeType != NodeType.MODEL_SCORE:
            nodes.append(node)
            continue
        contract_path = _bundled_contract_path(node.id, remap)
        if not contract_path:
            nodes.append(node)
            continue
        config = node.data.config
        if config.get("feature_contract_path") == contract_path:
            nodes.append(node)
            continue
        data = node.data.model_copy(
            update={"config": {**config, "feature_contract_path": contract_path}}
        )
        nodes.append(node.model_copy(update={"data": data}))
        changed = True
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def _remap_artifact(
    node_id: str,
    config: dict,
    remap: dict[str, str],
    key_field: str,
) -> str | None:
    """Look up a remapped artifact path for a node.

    Builds the artifact key from *node_id* and the basename of the config
    value at *key_field*, then checks the *remap* dict.

    Returns the remapped local path if found, otherwise ``None``.
    """
    raw_path = config.get(key_field, "")
    # Use Path (platform-aware) to match the bundler's Path(abs_path).name.
    # PurePosixPath would fail on Windows backslash paths.
    artifact_key = f"{node_id}__{Path(raw_path).name}" if raw_path else f"{node_id}__"
    return remap.get(artifact_key)


def _bundled_contract_path(node_id: str, remap: dict[str, str]) -> str | None:
    """Return the bundled feature-contract path for *node_id*, if any.

    Bundler writes the contract with the key ``{node_id}__feature_contract.json``;
    the scorer looks up the same key.
    """
    from haute.modelling._feature_contract import CONTRACT_FILENAME

    return remap.get(f"{node_id}__{CONTRACT_FILENAME}")


def _declared_categorical_levels_for_model_score(
    node: GraphNode,
    source_ids: list[str],
    node_by_id: dict[str, GraphNode],
    upstream_ids: list[str] | None = None,
) -> dict[str, list[str | None]]:
    """Merge explicit categorical level declarations at the score boundary."""
    from haute.modelling._feature_contract import merge_categorical_level_declarations

    declarations: list[tuple[str, Any]] = [(node.id, node.data.config.get("categorical_levels"))]
    candidate_ids = list(dict.fromkeys([*source_ids, *(upstream_ids or [])]))
    declarations.extend(
        (source_id, node_by_id[source_id].data.config.get("categorical_levels"))
        for source_id in candidate_ids
        if source_id in node_by_id
    )
    return merge_categorical_level_declarations(declarations)


def _assert_runtime_contract_matches(
    lf: pl.LazyFrame,
    contract_path: str,
    task: str,
    *,
    categorical_levels: dict[str, list[str | None]] | None = None,
    validate_values: bool = False,
) -> dict[str, list[str | None]]:
    """Raise FeatureMismatchError if the live schema drifts from the bundled
    contract, returning categorical levels safe to enforce during scoring.

    Rebuilds a :class:`FeatureContract` from the live LazyFrame's schema
    (restricted to the features the bundled contract cares about) and
    compares it against the training-time contract via
    :func:`assert_contracts_match`.  A disagreement on any field —
    feature set, dtype, categorical membership — raises immediately so
    deploy operators see the mismatch at load time rather than via
    cryptic downstream errors.
    """
    from haute.modelling._feature_contract import (
        assert_contracts_match,
        build_contract,
        normalise_categorical_levels,
        validate_categorical_value_domains,
    )

    expected = _load_feature_contract_cached(contract_path)
    schema = lf.collect_schema()
    feature_types: dict[str, str] = {}
    expected_feature_set = set(expected.features)
    runtime_features = [name for name in schema.names() if name in expected_feature_set]
    seen_runtime_features = set(runtime_features)
    runtime_features.extend(name for name in expected.features if name not in seen_runtime_features)
    categorical_features: list[str] = []
    for name in expected.features:
        dtype = schema.get(name)
        if dtype is None:
            # Missing feature at runtime — build a contract with the
            # placeholder so the diff names the missing column.
            feature_types[name] = "MISSING"
            continue
        canonical = _canonical_dtype(dtype)
        feature_types[name] = canonical
    for name in runtime_features:
        if feature_types.get(name) == "String":
            categorical_features.append(name)
    runtime_feature_set = set(runtime_features)
    runtime_declared_levels = {
        column: levels
        for column, levels in normalise_categorical_levels(categorical_levels).items()
        if column in runtime_feature_set
    }
    mismatched_levels = {
        column: levels
        for column, levels in runtime_declared_levels.items()
        if column in expected.categorical_levels and levels != expected.categorical_levels[column]
    }
    if mismatched_levels:
        from haute.errors import FeatureMismatchError

        raise FeatureMismatchError(
            "contract mismatch: categorical_levels",
            field="categorical_levels",
            expected=expected.categorical_levels,
            actual=mismatched_levels,
            feature_contract_path=contract_path,
        )
    contract_levels = expected.categorical_levels if expected.categorical_levels else {}

    actual = build_contract(
        features=runtime_features,
        feature_types=feature_types,
        categorical_features=categorical_features,
        categorical_levels=contract_levels,
        target_name=expected.target_name,
        target_type=expected.target_type,
        task=expected.task,
    )
    assert_contracts_match(expected, actual)
    score_levels = (
        expected.categorical_levels if expected.categorical_levels else runtime_declared_levels
    )
    if validate_values:
        validate_categorical_value_domains(lf, score_levels)
    return {column: list(levels) for column, levels in score_levels.items()}


def _canonical_dtype(dtype: Any) -> str:
    """Map a polars dtype to the canonical contract dtype string.

    Matches the convention used by ``haute.modelling._training_job``.
    """
    if dtype == pl.Boolean:
        return "Boolean"
    if dtype in (pl.Utf8, pl.String, pl.Categorical):
        return "String"
    if hasattr(dtype, "is_integer") and dtype.is_integer():
        return "Int64"
    if hasattr(dtype, "is_float") and dtype.is_float():
        return "Float64"
    return str(dtype)


def score_graph_lazy(
    graph: PipelineGraph,
    input_df: pl.DataFrame,
    input_node_ids: list[str],
    output_node_id: str,
    artifact_paths: dict[str, str] | None = None,
    output_fields: list[str] | None = None,
    execution_context: ExecutionContext | None = None,
) -> DeployScorePlan:
    """Build a lazy deployed scoring plan with injected input data.

    Instead of loading from files, input source nodes receive the provided
    DataFrame.  Artifact paths are remapped to the MLflow artifact directory
    when ``artifact_paths`` is provided.  The returned plan owns any temporary
    files created while constructing model-score batch nodes; callers must call
    :meth:`DeployScorePlan.cleanup` after collecting or streaming the frame.

    Args:
        graph: Pruned React Flow graph JSON.
        input_df: The live input data (1 or N rows).
        input_node_ids: Source node IDs that receive the live input.
        output_node_id: The node whose output is the API response.
        artifact_paths: Optional remapped artifact paths
            (``artifact_name → local_path``).
        output_fields: Optional list of columns to select from output.

    Returns:
        A lazy output plan and execution context.
    """
    if execution_context is None:
        execution_context = admit_deploy_execution(
            operation="deploy_score_graph",
            row_count=input_df.height,
        )
    remap = artifact_paths or {}
    graph = _attach_bundled_feature_contracts(
        _resolve_runtime_graph_paths(graph),
        remap,
    )
    node_by_id = {node.id: node for node in graph.nodes}
    parents_of = graph.parents_of
    input_set = set(input_node_ids)
    input_lf = input_df.lazy()
    model_score_temp_paths: list[str] = []
    retained_lazy_frames: list[pl.LazyFrame] = []

    def _intercept(
        node: GraphNode,
        source_names: list[str],
        source_ids: list[str],
        **build_kwargs: Any,
    ) -> NodeFnResult | None:
        nid = node.id
        node_type = node.data.nodeType
        config = node.data.config
        func_name = node_fn_name(node)

        # Intercept: apiInput source → inject live DataFrame
        if node_type == NodeType.API_INPUT and nid in input_set:

            def inject_input() -> _Frame:
                return input_lf

            return func_name, inject_input, True

        # Intercept: externalFile with remapped artifact path
        if node_type == NodeType.EXTERNAL_FILE and remap:
            remapped_path = _remap_artifact(nid, config, remap, "path")
            if remapped_path is not None:
                code = config.get("code", "").strip()
                file_type = config.get("fileType", "pickle")
                model_class = config.get("modelClass", "classifier")
                _src_names = list(source_names)

                _remapped: str = remapped_path  # narrowed by the `is not None` guard above
                if code:

                    def external_fn(
                        *dfs: _Frame,
                        _p: str = _remapped,
                        _ft: str = file_type,
                        _mc: str = model_class,
                        _code: str = code,
                        _sn: list[str] = _src_names,
                    ) -> _Frame:
                        from haute._user_exec import _exec_user_code

                        obj = load_external_object(_p, _ft, _mc)
                        return _exec_user_code(_code, _sn, dfs, extra_ns={"obj": obj})

                    return func_name, external_fn, False
                else:

                    def external_passthrough(*dfs: _Frame) -> _Frame:
                        return dfs[0] if dfs else pl.LazyFrame()

                    return func_name, external_passthrough, False

        # Intercept: optimiserApply with remapped artifact path or MLflow source
        if node_type == NodeType.OPTIMISER_APPLY:
            _vcol = config.get("version_column", "__optimiser_version__")
            _opt_col = config.get("optimised_value_column", "")
            _ratebook_input = config.get("ratebook_input", "")
            _src_names = list(source_names)
            _src_ids = list(source_ids)
            _st = config.get("sourceType", "")

            # File-based with remap
            if _st == "file" and remap:
                remapped_path = _remap_artifact(nid, config, remap, "artifact_path")
                if remapped_path is not None:
                    _opt_remapped: str = remapped_path

                    def optimiser_apply_fn(
                        *dfs: _Frame,
                        _path: str = _opt_remapped,
                        _version_col: str = _vcol,
                        _optimised_value_col: str = _opt_col,
                        _rb_input: str = _ratebook_input,
                        _src_names_arg: list[str] = _src_names,
                        _src_ids_arg: list[str] = _src_ids,
                    ) -> _Frame:
                        from haute._builders import _select_optimiser_apply_input
                        from haute._optimiser_io import load_optimiser_artifact
                        from haute.executor import _dispatch_apply

                        artifact = load_optimiser_artifact(_path)
                        lf = _select_optimiser_apply_input(
                            dfs,
                            artifact,
                            _rb_input,
                            _src_names_arg,
                            _src_ids_arg,
                        )
                        return _dispatch_apply(lf, artifact, _version_col, _optimised_value_col)

                    return func_name, optimiser_apply_fn, False

            # MLflow-sourced (downloads from MLflow at runtime)
            if _st in ("run", "registered"):
                _rid = config.get("run_id", "")
                _rm = config.get("registered_model", "")
                _ver = config.get("version", "latest")

                def optimiser_apply_mlflow_fn(
                    *dfs: _Frame,
                    _source_type: str = _st,
                    _run_id: str = _rid,
                    _reg_model: str = _rm,
                    _opt_ver: str = _ver,
                    _version_col: str = _vcol,
                    _optimised_value_col: str = _opt_col,
                    _rb_input: str = _ratebook_input,
                    _src_names_arg: list[str] = _src_names,
                    _src_ids_arg: list[str] = _src_ids,
                ) -> _Frame:
                    from haute._builders import _select_optimiser_apply_input
                    from haute._optimiser_io import load_mlflow_optimiser_artifact
                    from haute.executor import _dispatch_apply

                    artifact = load_mlflow_optimiser_artifact(
                        source_type=_source_type,
                        run_id=_run_id,
                        registered_model=_reg_model,
                        version=_opt_ver,
                    )
                    lf = _select_optimiser_apply_input(
                        dfs,
                        artifact,
                        _rb_input,
                        _src_names_arg,
                        _src_ids_arg,
                    )
                    return _dispatch_apply(lf, artifact, _version_col, _optimised_value_col)

                return func_name, optimiser_apply_mlflow_fn, False

        # Intercept: modelScore with bundled feature contract — verify
        # the live input schema matches the training contract BEFORE any
        # model loading.  This catches drift even when the model itself
        # wasn't pre-bundled into artifact_paths.
        # This branch also covers configured non-bundled modelScore nodes.
        if node_type == NodeType.MODEL_SCORE:
            bundled_contract_path = _bundled_contract_path(nid, remap) if remap else None
            remapped_path = _remap_artifact(nid, config, remap, "artifact_path") if remap else None
            _task = config.get("task", "regression")
            _output_col = config.get("output_column", "prediction")
            _src_names = list(source_names)
            _src_ids = list(source_ids)
            _declared_levels = _declared_categorical_levels_for_model_score(
                node,
                _src_ids,
                node_by_id,
                upstream_node_ids(nid, parents_of),
            )
            _code = _strip_generated_boilerplate_from_code(
                config.get("code") or "",
                kind="model_score",
                param_names=_src_names,
            )
            _score_source = _deploy_model_score_source(execution_context)
            _required_output_columns = projection.model_score_required_output_columns(
                config,
                build_kwargs.get("required_output_columns"),
                post_processing_code=_code,
            )
            if remapped_path is not None:
                _score_remapped: str = remapped_path
                _contract_path_model = bundled_contract_path

                def model_score_fn(
                    *dfs: _Frame,
                    _p: str = _score_remapped,
                    _t: str = _task,
                    _oc: str = _output_col,
                    _c: str = _code,
                    _sn: list[str] = _src_names,
                    _contract_path: str | None = _contract_path_model,
                    _categorical_levels: dict[str, list[str | None]] = _declared_levels,
                    _source: str = _score_source,
                    _required: Any = _required_output_columns,
                ) -> _Frame:
                    from haute._model_scorer import _run_score_pipeline

                    lf = dfs[0] if dfs else pl.LazyFrame()
                    score_categorical_levels = dict(_categorical_levels)
                    if _contract_path is not None:
                        score_categorical_levels = _assert_runtime_contract_matches(
                            lf,
                            _contract_path,
                            _t,
                            categorical_levels=_categorical_levels,
                        )
                    scoring_model = _load_local_model_cached(_p, _t)
                    return _run_score_pipeline(
                        scoring_model,
                        lf,
                        task=_t,
                        output_col=_oc,
                        code=_c,
                        source_names=_sn,
                        extra_dfs=dfs[1:],
                        source=_source,
                        required_output_columns=_required,
                        temporary_paths=model_score_temp_paths,
                        categorical_levels=score_categorical_levels,
                    )

                return func_name, model_score_fn, False

            # No model artifact in the remap, but a contract WAS bundled:
            # validate the live schema so drift errors stay precise, then
            # fail loudly. Deploy scoring must never manufacture null
            # predictions when no real model artifact is available.
            if bundled_contract_path is not None:
                _contract_path_only = bundled_contract_path
                _node_id = nid

                def model_score_missing_artifact(
                    *dfs: _Frame,
                    _t: str = _task,
                    _contract_path: str = _contract_path_only,
                    _categorical_levels: dict[str, list[str | None]] = _declared_levels,
                    _nid: str = _node_id,
                ) -> _Frame:
                    lf = dfs[0] if dfs else pl.LazyFrame()
                    _assert_runtime_contract_matches(
                        lf,
                        _contract_path,
                        _t,
                        categorical_levels=_categorical_levels,
                        validate_values=True,
                    )
                    # The contract matched, but without a model artifact
                    # there is no honest prediction to return.
                    raise RuntimeError(
                        f"modelScore node {_nid!r} has a bundled feature contract "
                        "but no bundled model artifact; deployed scoring cannot "
                        "produce predictions without a model artifact."
                    )

                return func_name, model_score_missing_artifact, False

            # Neither a bundled model artifact nor a bundled contract exists
            # for this modelScore node.  If the node also lacks a usable model
            # source it would fall through to the base builder, which returns a
            # silent identity passthrough for unconfigured/misconfigured
            # modelScore nodes.  A deployed pricing endpoint must never serve a
            # modelScore as a passthrough — the served quote would silently omit
            # the model.  Fail loud at build/validate time instead of shipping a
            # container that quietly returns unscored inputs.
            if not _model_score_has_configured_source(config):
                from haute.errors import DeployError

                raise DeployError(
                    f"modelScore node {nid!r} cannot be served: it has no usable "
                    "model source (set sourceType with a run_id or "
                    "registered_model) and no bundled model artifact, so the "
                    "deployed endpoint would serve it as a silent identity "
                    "passthrough that omits the model from every quote.",
                    node_id=nid,
                )

        # Intercept: static dataSource with remapped artifact path
        if node_type == NodeType.DATA_SOURCE and nid not in input_set and remap:
            remapped_path = _remap_artifact(nid, config, remap, "path")
            if remapped_path is not None:
                _ds_remapped: str = remapped_path
                _profile = (
                    execution_context.profile.value if execution_context is not None else None
                )
                _required_output_columns = build_kwargs.get("required_output_columns")
                _static_config = MappingProxyType(dict(config))

                def static_source(
                    _p: str = _ds_remapped,
                    _execution_profile: str | None = _profile,
                    _config: Mapping[str, Any] = _static_config,
                    _required: Any = _required_output_columns,
                ) -> _Frame:
                    projected = projection.source_scan_projection(_config, _required)
                    return read_data_source(
                        {**_config, "path": _p},
                        profile=_execution_profile,
                        columns=projected.columns,
                        validate_columns=projected.validate_columns,
                    )

                return func_name, static_source, True

        return None  # fall through to base builder

    builder = wrap_builder(
        _build_node_fn,
        NodeBuildHooks(before_build=_intercept),
    )

    # Compile preamble so utility imports are available in transform nodes.
    from haute.executor import _compile_preamble, _pipeline_dir

    preamble_ns = (
        _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
        )
        or None
    )

    # Deployed graph routing stays on the live source so source-switch nodes
    # select the API input branch. Individual modelScore nodes choose their
    # eager/batch scoring mode from the admitted execution profile.
    from haute.executor import ENFORCE_CONTRACTS

    required_columns_by_node: dict[str, frozenset[str]] | None = None
    if output_fields:
        if isinstance(output_fields, str | bytes):
            raise ValueError("output_fields must be a list of column names")
        output_seed: set[str] = set()
        for column in output_fields:
            if not isinstance(column, str) or not column:
                raise ValueError("output_fields must contain non-empty string names")
            output_seed.add(column)
        required_columns_by_node = {output_node_id: frozenset(output_seed)}
    deploy_model_score_source = _deploy_model_score_source(execution_context)
    source_by_node = {
        node.id: deploy_model_score_source
        for node in graph.nodes
        if node.data.nodeType == NodeType.MODEL_SCORE
        and _model_score_has_configured_source(node.data.config)
    }
    from haute._model_scorer import model_score_temp_file_scope

    try:
        with model_score_temp_file_scope(model_score_temp_paths):
            remapped_node_ids = {key.split("__", 1)[0] for key in remap}
            dataframe_cache_request = (
                build_dataframe_execution_cache_request(
                    graph,
                    node_ids=[output_node_id],
                    namespace="deploy_score",
                    source="live",
                    profile=execution_context.profile,
                    input_fingerprint=dataframe_graph_input_fingerprint(
                        graph,
                        target_node_id=output_node_id,
                        source="live",
                        ignore_node_ids=input_set | remapped_node_ids,
                        extra_fingerprints={
                            "input_df": dataframe_frame_input_fingerprint(input_df),
                            "input_node_ids": sorted(input_set),
                            "artifact_paths": _deploy_artifact_paths_input_fingerprint(remap),
                        },
                    ),
                    target_node_id=output_node_id,
                    source_by_node=source_by_node,
                    required_columns_by_node=required_columns_by_node,
                    enforce_contracts=ENFORCE_CONTRACTS,
                    preamble_ns_supplied=preamble_ns is not None,
                )
                if output_node_id in graph.node_map
                and execution_context.profile != ExecutionProfile.DEPLOY_LIVE
                else None
            )
            lazy_outputs, order, _parents, _names = execute_lazy_graph(
                graph,
                builder,
                target_node_id=output_node_id,
                preamble_ns=preamble_ns,
                source="live",
                enforce_contracts=ENFORCE_CONTRACTS,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                source_by_node=source_by_node,
                dataframe_cache_request=dataframe_cache_request,
            )

        output_lf = lazy_outputs.get(output_node_id)
        if output_lf is None:
            raise RuntimeError(
                f"Output node '{output_node_id}' produced no result. Executed nodes: {order}"
            )

        if output_fields:
            retained_lazy_frames.append(output_lf)
            output_lf = output_lf.select(output_fields)

    except BaseException:
        _cleanup_model_score_temp_paths(
            model_score_temp_paths,
            preserve_primary_error=True,
        )
        raise

    return DeployScorePlan(
        lazy_frame=output_lf,
        execution_context=execution_context,
        temporary_paths=model_score_temp_paths,
        retained_lazy_frames=retained_lazy_frames,
    )


def score_graph(
    graph: PipelineGraph,
    input_df: pl.DataFrame,
    input_node_ids: list[str],
    output_node_id: str,
    artifact_paths: dict[str, str] | None = None,
    output_fields: list[str] | None = None,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    """Execute a pruned pipeline graph with injected input data and collect output."""
    plan = score_graph_lazy(
        graph=graph,
        input_df=input_df,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifact_paths=artifact_paths,
        output_fields=output_fields,
        execution_context=execution_context,
    )
    preserve_primary_error = False
    try:
        plan.execution_context.checkpoint(
            label="before_deploy_collect",
            node_id=output_node_id,
        )
        with plan.execution_context.stage("deploy_collect", node_id=output_node_id):
            result = streaming_collect(
                plan.lazy_frame,
                profile=plan.execution_context.profile,
            )
        plan.execution_context.checkpoint(
            label="after_deploy_collect",
            node_id=output_node_id,
        )
        return result
    except BaseException:
        preserve_primary_error = True
        raise
    finally:
        plan.cleanup(preserve_primary_error=preserve_primary_error)
