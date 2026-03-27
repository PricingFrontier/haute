"""Runtime scoring engine for deployed pipelines.

Uses the same ``_execute_lazy`` / ``_build_node_fn`` infrastructure as
the development executor, with ``NodeBuildHooks`` to override specific
node types for live scoring:

- Injects live input DataFrames at apiInput source nodes
- Remaps artifact paths for externalFile and static dataSource nodes
- Returns a single collected DataFrame from the output node
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from haute._execute_lazy import _execute_lazy
from haute._io import load_external_object, read_source
from haute._node_builder import NodeBuildHooks, NodeFnResult, node_fn_name, wrap_builder
from haute._types import (
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.executor import _build_node_fn, _exec_user_code


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


def score_graph(
    graph: PipelineGraph,
    input_df: pl.DataFrame,
    input_node_ids: list[str],
    output_node_id: str,
    artifact_paths: dict[str, str] | None = None,
    output_fields: list[str] | None = None,
) -> pl.DataFrame:
    """Execute a pruned pipeline graph with injected input data.

    Instead of loading from files, input source nodes receive the provided
    DataFrame.  Artifact paths are remapped to the MLflow artifact directory
    when ``artifact_paths`` is provided.

    Args:
        graph: Pruned React Flow graph JSON.
        input_df: The live input data (1 or N rows).
        input_node_ids: Source node IDs that receive the live input.
        output_node_id: The node whose output is the API response.
        artifact_paths: Optional remapped artifact paths
            (``artifact_name → local_path``).
        output_fields: Optional list of columns to select from output.

    Returns:
        Output DataFrame (1 or N rows).
    """
    input_set = set(input_node_ids)
    input_lf = input_df.lazy()
    remap = artifact_paths or {}

    def _intercept(node: GraphNode, source_names: list[str]) -> NodeFnResult | None:
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
            _st = config.get("sourceType", "")

            # File-based with remap
            if remap:
                remapped_path = _remap_artifact(nid, config, remap, "artifact_path")
                if remapped_path is not None:
                    _opt_remapped: str = remapped_path

                    def optimiser_apply_fn(
                        *dfs: _Frame,
                        _path: str = _opt_remapped,
                        _version_col: str = _vcol,
                    ) -> _Frame:
                        from haute._optimiser_io import load_optimiser_artifact
                        from haute.executor import _dispatch_apply

                        artifact = load_optimiser_artifact(_path)
                        lf = dfs[0] if dfs else pl.LazyFrame()
                        return _dispatch_apply(lf, artifact, _version_col)

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
                ) -> _Frame:
                    from haute._optimiser_io import load_mlflow_optimiser_artifact
                    from haute.executor import _dispatch_apply

                    artifact = load_mlflow_optimiser_artifact(
                        source_type=_source_type,
                        run_id=_run_id,
                        registered_model=_reg_model,
                        version=_opt_ver,
                    )
                    lf = dfs[0] if dfs else pl.LazyFrame()
                    return _dispatch_apply(lf, artifact, _version_col)

                return func_name, optimiser_apply_mlflow_fn, False

        # Intercept: modelScore with remapped artifact path — load from
        # bundled .cbm instead of downloading from MLflow at runtime.
        if node_type == NodeType.MODEL_SCORE and remap:
            remapped_path = _remap_artifact(nid, config, remap, "artifact_path")
            if remapped_path is not None:
                _score_remapped: str = remapped_path
                _task = config.get("task", "regression")
                _output_col = config.get("output_column", "prediction")
                _code = config.get("code", "").strip()
                _src_names = list(source_names)

                def model_score_fn(
                    *dfs: _Frame,
                    _p: str = _score_remapped,
                    _t: str = _task,
                    _oc: str = _output_col,
                    _c: str = _code,
                    _sn: list[str] = _src_names,
                ) -> _Frame:
                    from haute._mlflow_io import load_local_model
                    from haute._model_scorer import _run_score_pipeline

                    scoring_model = load_local_model(_p, _t)
                    lf = dfs[0] if dfs else pl.LazyFrame()
                    return _run_score_pipeline(
                        scoring_model,
                        lf,
                        task=_t,
                        output_col=_oc,
                        code=_c,
                        source_names=_sn,
                        source="live",
                    )

                return func_name, model_score_fn, False

        # Intercept: static dataSource with remapped artifact path
        if node_type == NodeType.DATA_SOURCE and nid not in input_set and remap:
            remapped_path = _remap_artifact(nid, config, remap, "path")
            if remapped_path is not None:
                _ds_remapped: str = remapped_path

                def static_source(_p: str = _ds_remapped) -> _Frame:
                    return read_source(_p)

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

    # Deployed API always runs in "live" source — eager scoring, live
    # switch routes to the live input.
    lazy_outputs, order, _parents, _names = _execute_lazy(
        graph,
        builder,
        target_node_id=output_node_id,
        preamble_ns=preamble_ns,
        source="live",
    )

    output_lf = lazy_outputs.get(output_node_id)
    if output_lf is None:
        raise RuntimeError(
            f"Output node '{output_node_id}' produced no result. Executed nodes: {order}"
        )

    if output_fields:
        output_lf = output_lf.select(output_fields)

    return output_lf.collect()
