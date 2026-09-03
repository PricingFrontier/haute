"""Input/output schema inference for deployed pipelines."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._api_input_schema import is_json_api_input_path
from haute._cache import (
    CacheConsumer,
    GraphFingerprintMemo,
    checked_cache_inputs,
    graph_fingerprint,
)
from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._worker_isolation import IsolatedWorkerError, run_isolated_worker
from haute.errors import ConfigError
from haute.execution import dataframe_graph_input_fingerprint
from haute.graph_utils import GraphNode, NodeType, PipelineGraph, read_data_source, read_source

logger = get_logger(component="deploy.schema")

# How the deployed target executes a multi-row request: a container target
# spawns a hard-capped worker per batch, the Databricks pyfunc target scores in
# the serving process. The distinction decides whether an unprovable group-by
# is a runtime warning or a bundle-time refusal.
DeployBatchRuntime = Literal["hard_capped_worker", "in_process"]

_SCHEMA_CACHE_FILE = ".haute_cache/output_schema.json"
_DEPLOY_SCHEMA_EXECUTION_POLICY = "deploy-output-schema:v1"


def _resolve_pipeline_path(graph: PipelineGraph, path: str) -> str:
    """Resolve a graph config path against the pipeline file directory."""
    raw = Path(path)
    if raw.is_absolute() or not graph.source_file:
        return str(raw)
    return str((Path(graph.source_file).parent / raw).resolve())


def _read_input_source(graph: PipelineGraph, node: GraphNode, resolved_path: str) -> pl.LazyFrame:
    config = node.data.config
    if node.data.nodeType == NodeType.DATA_INPUT:
        from haute._input_providers import resolve_data_input
        from haute._sandbox import _get_project_root
        from haute._source_cache import SourceCacheStore

        base_dir = Path(graph.source_file).parent if graph.source_file else None
        return resolve_data_input(
            {**config, **({"path": resolved_path} if resolved_path else {})},
            store=SourceCacheStore(_get_project_root()),
            base_dir=base_dir,
        )
    if node.data.nodeType == NodeType.API_INPUT and not is_json_api_input_path(resolved_path):
        return read_data_source({**config, "path": resolved_path})
    return read_source(resolved_path)


def infer_input_schema(graph: PipelineGraph, input_node_id: str) -> dict[str, str]:
    """Infer the input schema by reading the input source node's data file.

    Reads the first 0 rows to get column names + types without loading data.

    Args:
        graph: Pruned graph with nodes and edges.
        input_node_id: The apiInput source node.

    Returns:
        Dict of column_name → polars dtype string (e.g. ``{"Area": "String"}``).

    Raises:
        ValueError: If the input node has no path or the file can't be read.
    """
    node = _find_node(graph, input_node_id)
    config = node.data.config
    path = config.get("path", "")

    if not path and node.data.nodeType != NodeType.DATA_INPUT:
        raise ValueError(
            f"Input node '{input_node_id}' has no path configured. Cannot infer schema."
        )

    try:
        resolved_path = _resolve_pipeline_path(graph, path) if path else ""
        lf = _read_input_source(graph, node, resolved_path)
        schema = lf.collect_schema()
    except Exception as exc:
        raise ValueError(
            f"Failed to read schema from '{path}' for input node '{input_node_id}': {exc}"
        ) from exc

    return {col: str(dtype) for col, dtype in schema.items()}


def deploy_schema_cache_fingerprint(
    graph: PipelineGraph,
    *,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None,
) -> str:
    """Return the checked identity for one deploy output-schema dry-run."""
    from haute.deploy._scorer import artifact_identity_fingerprint

    memo = GraphFingerprintMemo()
    inputs = checked_cache_inputs(
        CacheConsumer.DEPLOY_SCHEMA,
        {
            "graph_fingerprint": graph_fingerprint(graph, memo=memo),
            "runtime_input_fingerprint": dataframe_graph_input_fingerprint(
                graph,
                target_node_id=output_node_id,
                source="live",
                memo=memo,
            ),
            "artifact_fingerprint": artifact_identity_fingerprint(artifact_paths),
            "output_node_id": output_node_id,
            "input_node_ids": tuple(sorted(set(input_node_ids))),
            "source": "live",
            "row_limit": 1,
            "execution_policy": _DEPLOY_SCHEMA_EXECUTION_POLICY,
        },
    )
    return f"deploy-schema:v{inputs.contract.version}:{content_hash_bytes(inputs.canonical_bytes)}"


def _read_sample_row(graph: PipelineGraph, input_node_ids: list[str]) -> pl.DataFrame:
    """Read the one-row sample both the schema dry-run and policy planning use."""
    if not input_node_ids:
        raise ValueError("No API input nodes found in the graph")
    node = _find_node(graph, input_node_ids[0])
    config = node.data.config
    path = config.get("path", "")

    if not path and node.data.nodeType != NodeType.DATA_INPUT:
        raise ValueError(
            f"Input node '{input_node_ids[0]}' has no path - cannot create sample row."
        )

    try:
        resolved_path = _resolve_pipeline_path(graph, path) if path else ""
        from haute._polars_utils import streaming_collect

        return streaming_collect(_read_input_source(graph, node, resolved_path).head(1))
    except Exception as exc:
        raise ValueError(f"Failed to read sample from '{path}': {exc}") from exc


def infer_deploy_execution_policy(
    graph: PipelineGraph,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None = None,
    *,
    batch_runtime: DeployBatchRuntime,
) -> dict[str, Any]:
    """Plan the served batch strategy once at bundle time and record it.

    The record is target-aware because the two backends run a multi-row
    request differently. A container target scores batches in a hard-capped
    spawn worker (``"hard_capped_worker"``), so an unavailable materialisation
    estimate is *warned* and run conservatively there; bundle time has no
    native cap, so the same graph raises
    :class:`~haute.errors.GroupByExecutionUnsupportedError` here and that one
    reason code is translated into the policy the worker will actually apply.
    The Databricks pyfunc target scores in the serving process
    (``"in_process"``, ``_model_code.py::HauteModel.predict``) with no worker
    and no cap, so the same rejection is real at serve time and fails the
    bundle instead of promising conservative execution. Every other rejection
    is a deploy that would fail on every batch request and is refused for both
    runtimes.
    """
    from haute._execution_context import ExecutionProfile
    from haute._graph_utils import upstream_node_ids
    from haute.deploy._scorer import (
        _attach_bundled_feature_contracts,
        _attach_bundled_model_contract_inputs,
        _resolve_runtime_graph_paths,
        admit_deploy_execution,
    )
    from haute.errors import DeployError, GroupByExecutionUnsupportedError
    from haute.execution import plan_execution_strategy
    from haute.projection import ExecutionStrategy, ProjectionRequest

    sample = _read_sample_row(graph, input_node_ids)
    remap = artifact_paths or {}
    planned = _attach_bundled_feature_contracts(_resolve_runtime_graph_paths(graph), remap)
    relevant_node_ids = set(upstream_node_ids(output_node_id, planned.parents_of)) | {
        output_node_id
    }
    planned = _attach_bundled_model_contract_inputs(planned, remap, relevant_node_ids)

    context = admit_deploy_execution(operation="deploy_bundle_policy", row_count=2)
    try:
        result = plan_execution_strategy(
            ProjectionRequest(
                graph=planned,
                target_node_id=output_node_id,
                profile=ExecutionProfile.DEPLOY_BATCH,
                source="live",
            ),
            execution_context=context,
            runtime_source_frames_by_node=dict.fromkeys(input_node_ids, sample),
        )
    except GroupByExecutionUnsupportedError as exc:
        if exc.reason_code != "materialisation_estimate_unavailable":
            raise DeployError(
                f"Deployed batch scoring cannot be planned: {exc.reason_code} at node "
                f"{exc.node_id!r} (operator {exc.operator!r}). {exc.remediation}",
                node_id=exc.node_id,
                operator=exc.operator,
                reason_code=exc.reason_code,
            ) from exc
        # The batch worker plans under an active native cap, where this
        # rejection becomes the conservative warning; record that runtime code.
        if batch_runtime == "in_process":
            raise DeployError(
                "Deployed batch scoring cannot be admitted: this target scores "
                "multi-row requests in the serving process without a hard memory "
                f"cap, so the group-by at node {exc.node_id!r} (operator "
                f"{exc.operator!r}) cannot be admitted without a materialisation "
                "estimate. Deploy to a container target, whose batch worker runs "
                "under a hard cap, or make the estimate provable. "
                f"{exc.remediation}",
                node_id=exc.node_id,
                operator=exc.operator,
                reason_code=exc.reason_code,
            ) from exc
        policy = {
            "schema_version": 1,
            "profile": "deploy_batch",
            "runtime": batch_runtime,
            "status": "warned",
            "strategy": "full-width-conservative",
            "reason_code": "materialisation_estimate_unavailable_conservative",
            "blocking_node_id": exc.node_id,
            "blocking_operator": exc.operator,
            "remediation": (
                "The deployed batch worker runs this group-by under its full hard-capped "
                "memory envelope because the materialisation estimate is unavailable at "
                "bundle time. Provide readable source metadata or rewrite "
                f"'{exc.operator}' at '{exc.node_id}' so Haute can prove the estimate."
            ),
        }
    else:
        diagnostic = result.diagnostic
        if diagnostic.strategy is ExecutionStrategy.UNSUPPORTED:
            raise DeployError(
                "Deployed batch scoring cannot be planned: "
                f"{diagnostic.reason_code} at node {diagnostic.blocking_node_id!r} "
                f"(operator {diagnostic.blocking_operator!r}). {diagnostic.remediation}",
                node_id=diagnostic.blocking_node_id,
                operator=diagnostic.blocking_operator,
                reason_code=diagnostic.reason_code,
            )
        policy = {
            "schema_version": diagnostic.schema_version,
            "profile": "deploy_batch",
            "runtime": batch_runtime,
            "status": str(diagnostic.status),
            "strategy": str(diagnostic.strategy),
            "reason_code": diagnostic.reason_code,
            "blocking_node_id": diagnostic.blocking_node_id,
            "blocking_operator": diagnostic.blocking_operator,
            "remediation": diagnostic.remediation,
        }
    finally:
        context.release_admission(preserve_primary_error=True)

    log = logger.warning if policy["status"] == "warned" else logger.info
    log("deploy_execution_policy", **policy)
    return policy


def _dry_run_output_schema(
    graph: PipelineGraph,
    sample: pl.DataFrame,
    *,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None,
) -> dict[str, str]:
    """Score the one-row sample and read the collected output's dtypes."""
    from haute.deploy._scorer import score_graph

    result = score_graph(
        graph=graph,
        input_df=sample,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifact_paths=artifact_paths,
    )
    return {col: str(result[col].dtype) for col in result.columns}


def _capped_worker_output_schema(
    graph: PipelineGraph,
    sample: pl.DataFrame,
    *,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None,
) -> dict[str, str]:
    """Run the one-row dry-run in the served batch worker and read its output.

    This is the same hard-capped spawn worker the deployed container uses for a
    multi-row request, admitted the same way and configured with the same
    ``haute-deploy-batch`` process cap. The group-by therefore runs once under
    its full hard-capped envelope — exactly the policy the manifest records —
    and the schema is read from the parquet the worker actually produced rather
    than inferred from a plan.

    The one-row sample bounds only the request-derived side of the graph: a
    group-by over a bundled static source still materialises that source in
    full. Running it under the worker's cap is what keeps the bundle build
    bounded, and it is why the group-by admission gate must never simply be
    relaxed here.
    """
    from haute.deploy._batch_scoring import (
        accept_batch_outcome,
        prepare_batch_scoring,
        score_batch_worker,
    )

    plan = prepare_batch_scoring(
        sample.to_dicts(),
        graph=graph,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifact_paths=artifact_paths or {},
        output_fields=None,
        operation="deploy_bundle_schema",
    )
    primary_error: BaseException | None = None
    try:
        outcome = run_isolated_worker(
            score_batch_worker,
            plan.request,
            plan.budget,
            config=plan.worker_config,
        )
        result = accept_batch_outcome(plan, outcome)
        parquet_schema = pl.read_parquet_schema(result.result_path)
        return {name: str(dtype) for name, dtype in parquet_schema.items()}
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        plan.cleanup(primary_error=primary_error)


def infer_output_schema(
    graph: PipelineGraph,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None = None,
) -> dict[str, str]:
    """Infer the output schema by dry-running one sample row.

    Executes the pruned graph with a single sample row injected at each
    input node, and reads the output node's columns + types.

    Results are cached in ``.haute_cache/output_schema.json`` keyed by
    graph fingerprint so unchanged pipelines skip the dry-run.  The bundled
    artifact identity (:func:`artifact_identity_fingerprint`) is mixed into
    the key so a model retrained in place — same graph config but different
    served bytes — busts the cache instead of baking a stale schema into the
    deploy manifest / MLflow ``ModelSignature``.

    The dry-run scores with the exact ``artifact_paths`` the deployed
    container serves, so the inferred schema reflects the pinned model rather
    than whatever a live MLflow lookup would resolve.

    Args:
        graph: Pruned graph.
        output_node_id: The output node to read results from.
        input_node_ids: Source nodes that receive the sample input.
        artifact_paths: The bundled artifacts (``artifact_name → local_path``)
            the container serves, threaded into the dry-run so validate-time
            scoring matches serve-time scoring byte-for-byte.

    Returns:
        Dict of column_name → polars dtype string.
    """
    fp = deploy_schema_cache_fingerprint(
        graph,
        output_node_id=output_node_id,
        input_node_ids=input_node_ids,
        artifact_paths=artifact_paths,
    )

    # Check cache
    cache_path = Path(_SCHEMA_CACHE_FILE)
    if cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp:
                logger.info("output_schema_cache_hit", fingerprint=fp[:8])
                return dict(cached["schema"])
        except Exception as exc:
            logger.warning("corrupt_schema_cache", path=str(cache_path), error=str(exc))

    from haute.deploy._batch_scoring import BatchScoreError
    from haute.errors import DeployError, GroupByExecutionUnsupportedError

    sample = _read_sample_row(graph, input_node_ids)

    try:
        schema = _dry_run_output_schema(
            graph,
            sample,
            output_node_id=output_node_id,
            input_node_ids=input_node_ids,
            artifact_paths=artifact_paths,
        )
    except GroupByExecutionUnsupportedError as exc:
        if exc.reason_code != "materialisation_estimate_unavailable":
            raise
        # The in-process dry-run is the primary path because it proves the
        # graph executes. It runs uncapped under DEPLOY_LIVE, so a group-by
        # whose materialisation cannot be estimated is rejected there — even
        # though the served batch runs that group-by conservatively under the
        # batch worker's hard cap. Re-run the same dry-run inside that worker:
        # the bundle then proves the served batch path can produce the schema,
        # under the very envelope the recorded policy promises.
        logger.info(
            "output_schema_capped_worker_fallback",
            node_id=exc.node_id,
            operator=exc.operator,
        )
        try:
            schema = _capped_worker_output_schema(
                graph,
                sample,
                output_node_id=output_node_id,
                input_node_ids=input_node_ids,
                artifact_paths=artifact_paths,
            )
        except (BatchScoreError, IsolatedWorkerError) as worker_error:
            raise DeployError(
                "Deployed batch scoring could not be proven at bundle time: the "
                f"group-by at node {exc.node_id!r} (operator {exc.operator!r}) has no "
                "materialisation estimate, and the one-row dry-run through the "
                "hard-capped batch worker did not produce an output schema "
                f"({type(worker_error).__name__}: {worker_error}). The deployed "
                "endpoint would fail the same way on every batch request. "
                f"{exc.remediation}",
                node_id=exc.node_id,
                operator=exc.operator,
                reason_code=exc.reason_code,
            ) from worker_error

    # Write cache. A failed disk write (permissions, full disk) is genuinely
    # non-critical — the schema was computed truthfully and can be recomputed
    # next run — so only OSErrors are swallowed (with a warning). Any other
    # exception (e.g. a non-serialisable schema) is a real bug and must
    # propagate rather than be masked by a bare ``except: pass``.
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps({"fingerprint": fp, "schema": schema}), encoding="utf-8")
    except OSError as exc:
        logger.warning("output_schema_cache_write_failed", path=str(cache_path), error=str(exc))

    return schema


def _find_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find a node by ID in a graph using the cached node_map."""
    try:
        return graph.node_map[node_id]
    except KeyError:
        raise ConfigError("Node not found in graph", node_id=node_id) from None
