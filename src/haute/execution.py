"""Internal execution-engine facade.

Application layers should import execution helpers from this module instead
of reaching into ``haute._execute_lazy`` or underscore re-exports from
``haute.graph_utils``.  The implementation remains deliberately thin: it
keeps one stable internal boundary while the engine underneath continues to
evolve.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import polars as pl

from haute._api_input_schema import is_json_api_input_path
from haute._cache import (
    CacheConsumer,
    CacheIdentityRecord,
    GraphFingerprintMemo,
    LineageCacheKeyRequest,
    canonical_json,
    checked_cache_identity_record,
    checked_cache_inputs,
    lineage_cache_key,
    preamble_execution_fingerprint,
    selected_live_switch_path,
)
from haute._estimate_calibration import calibrate_materialisation_bytes
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_utils import upstream_node_ids
from haute._hashing import HASH_ALGO, content_hash_bytes
from haute._json_shred._source_proof import file_signature
from haute._native_memory_limit import current_native_memory_backend
from haute._node_specs import REQUEST_INPUT_NODE_TYPES, node_type_table
from haute._path_resolution import resolve_node_input_path
from haute._polars_selectors import preamble_selector_aliases
from haute._project_context import current_project, graph_pipeline_dir
from haute._ram_estimate import (
    MaterialisationEstimate,
    MaterialisationEstimateBasis,
    MaterialisationEstimateState,
    RuntimeSourceFrames,
    estimate_materialisation_boundaries,
)
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph, _Frame
from haute.errors import GroupByExecutionUnsupportedError
from haute.projection import (
    AllExceptColumns,
    BoundedDiagnosticCollection,
    DiagnosticDetailState,
    ExecutionBoundedness,
    ExecutionStrategy,
    ExecutionStrategyDiagnostic,
    ExecutionStrategyResult,
    ExecutionStrategyStatus,
    PreparedGraph,
    ProjectionPlan,
    ProjectionRequest,
    build_execution_strategy_result,
    compute_prepared_plan,
    first_materialising_operators,
    materialising_operator_sequences_by_node,
    normalise_required_columns_by_node,
    prepare_graph,
    ratebook_factor_required_columns,
    source_scan_projection,
    with_api_input_port_projection_boundaries,
    with_materialisation_boundaries,
)

if TYPE_CHECKING:
    from haute._chunked_writes import JoinRecipe, WriteRecipe
    from haute._seed_plans import SeedPlan

__all__ = [
    "AllExceptColumns",
    "BoundedDiagnosticCollection",
    "LazyExecutionResult",
    "DiagnosticDetailState",
    "ExecutionBoundedness",
    "ExecutionStrategy",
    "ExecutionStrategyDiagnostic",
    "ExecutionStrategyResult",
    "ExecutionStrategyStatus",
    "MaterialisationEstimate",
    "ProjectionPlan",
    "ProjectionRequest",
    "canonical_dataframe_execution_graph",
    "dataframe_frame_input_fingerprint",
    "dataframe_graph_input_fingerprint",
    "dataframe_paths_input_fingerprint",
    "execute_lazy_graph",
    "plan_prepared_execution_strategy",
    "plan_execution_strategy",
    "preview_lineage_cache_key",
    "prune_source_switch_edges",
    "ratebook_factor_required_columns",
    "source_lineage_graph",
    "source_scan_projection",
]

PREVIEW_EXECUTION_SEMANTICS_VERSION = "preview-materialisation:v2"
_PREVIEW_CONTRACT_FINGERPRINT_VERSION = 1

LazyExecutionResult = tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]

_AUTO_MATERIALISATION_ESTIMATE = object()
_DATAFRAME_ROW_HASH_ENCODING = "polars-u64-le:v1"
# Beside its source file's field, the config fields that say which external
# data or model a node reads: its runtime-input entry signs them beside the
# state they name. Every other field reaches the identity through the
# structural graph or lineage identity a runtime-input identity is paired with.
_MLFLOW_SOURCE_CONFIG_FIELDS = (
    "alias",
    "artifact_path",
    "mlflow_destination",
    "registered_model",
    "run_id",
    "source_type",
    "version",
)


@dataclass(frozen=True)
class _RuntimeInputs:
    """What a node type reads from outside the graph, which an execution identity signs."""

    #: The config fields its runtime-input entry signs: its source file's field
    #: first, when it reads one.
    config_fields: tuple[str, ...]
    #: The config fields naming a local file it reads, given its config.
    local_paths: Callable[[Mapping[str, Any]], tuple[str, ...]]
    #: Path-shaped fields a request's boundary validates though execution never
    #: resolves them as local files (an MLflow ``artifact_path`` identifier).
    identifier_paths: tuple[str, ...] = ()
    #: Its files are its Data Input source's: direct files, or a snapshot pointer.
    data_source: bool = False
    #: A ``run`` or ``registered`` source resolves through an MLflow backend,
    #: whose resolved identity is signed too.
    mlflow_sourced: bool = False


def _path_field(config: Mapping[str, Any]) -> tuple[str, ...]:
    return ("path",)


def _data_input_paths(config: Mapping[str, Any]) -> tuple[str, ...]:
    return ("path",) if config.get("input_type") in {"file", "lakehouse"} else ()


def _model_score_paths(config: Mapping[str, Any]) -> tuple[str, ...]:
    # ``artifact_path`` is an MLflow artifact identifier (for example
    # ``model/model.cbm``), not a path on the Haute project filesystem.
    model_file = ("model_path",) if config.get("source_type") == "file" else ()
    return ("feature_contract_path", *model_file)


def _optimiser_apply_paths(config: Mapping[str, Any]) -> tuple[str, ...]:
    return ("artifact_path",) if config.get("source_type") == "file" else ()


#: The runtime inputs of each node type that reads any; ``None`` for a type whose
#: whole identity is its structure and lineage (a Workbench Input reads no file).
_RUNTIME_INPUTS: Mapping[NodeType, _RuntimeInputs | None] = node_type_table(
    "runtime inputs",
    {
        NodeType.API_INPUT: _RuntimeInputs(config_fields=("path",), local_paths=_path_field),
        NodeType.WORKBENCH_INPUT: None,
        NodeType.WORKBENCH_OUTPUT: None,
        NodeType.DATA_INPUT: _RuntimeInputs(
            config_fields=(
                "path",
                "code",
                "connection",
                "format",
                "http_path",
                "input_type",
                "mode",
                "query",
                "table",
                "uri",
            ),
            local_paths=_data_input_paths,
            data_source=True,
        ),
        NodeType.DATA_OUTPUT: None,
        NodeType.POLARS: None,
        NodeType.EDGE_JOIN: None,
        NodeType.MODEL_SCORE: _RuntimeInputs(
            config_fields=(
                *_MLFLOW_SOURCE_CONFIG_FIELDS,
                "code",
                "feature_contract_path",
                "model_path",
            ),
            local_paths=_model_score_paths,
            identifier_paths=("artifact_path",),
            mlflow_sourced=True,
        ),
        NodeType.BANDING: None,
        NodeType.RATING_STEP: None,
        NodeType.OUTPUT: None,
        NodeType.EXPLORE: None,
        NodeType.EXTERNAL_FILE: _RuntimeInputs(
            config_fields=("path", "code"), local_paths=_path_field
        ),
        NodeType.LIVE_SWITCH: None,
        NodeType.MODELLING: None,
        NodeType.OPTIMISER: None,
        NodeType.SCENARIO_EXPANDER: None,
        NodeType.OPTIMISER_APPLY: _RuntimeInputs(
            config_fields=(*_MLFLOW_SOURCE_CONFIG_FIELDS, "ratebook_input"),
            local_paths=_optimiser_apply_paths,
            mlflow_sourced=True,
        ),
        NodeType.CONSTANT: None,
        NodeType.SUBMODEL: None,
        NodeType.SUBMODEL_PORT: None,
    },
)
# The node types whose runtime inputs an execution identity signs.
_RUNTIME_INPUT_NODE_TYPES = frozenset(
    node_type for node_type, inputs in _RUNTIME_INPUTS.items() if inputs is not None
)


def _runtime_input_config_fields(node_type: NodeType) -> tuple[str, ...]:
    inputs = _RUNTIME_INPUTS[node_type]
    return () if inputs is None else inputs.config_fields


def canonical_dataframe_execution_graph(
    graph: PipelineGraph,
    *,
    memo: GraphFingerprintMemo | None = None,
) -> PipelineGraph:
    """Resolve and contain every local runtime input before execution.

    With *memo*, a graph it has already canonicalised is not resolved again.
    """

    if memo is not None:
        known = memo.canonical_graphs.get(id(graph))
        if known is not None and known[0] is graph:
            return known[1]
    canonical = _canonical_dataframe_execution_graph(graph)
    if memo is not None:
        # Canonicalising a canonical graph gives it back unchanged.
        memo.canonical_graphs[id(graph)] = (graph, canonical)
        memo.canonical_graphs[id(canonical)] = (canonical, canonical)
    return canonical


def _canonical_dataframe_execution_graph(graph: PipelineGraph) -> PipelineGraph:
    root = current_project().root
    pipeline_dir = graph_pipeline_dir(graph)
    if pipeline_dir is None:
        from haute._project import _toml_configured_pipeline

        configured_pipeline = _toml_configured_pipeline(root)
        pipeline_dir = configured_pipeline.parent if configured_pipeline is not None else None
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        resolved_config = dict(config)
        node_changed = False
        for key in _local_runtime_input_path_fields(node):
            raw_path = config.get(key)
            if isinstance(raw_path, str) and raw_path:
                resolved = str(
                    resolve_node_input_path(
                        raw_path,
                        source_file=graph.source_file,
                        pipeline_dir=pipeline_dir,
                        project_root=root,
                    )
                )
                if resolved != raw_path:
                    resolved_config[key] = resolved
                    node_changed = True
        if node_changed:
            nodes.append(node.with_config(resolved_config))
            changed = True
        else:
            nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def plan_execution_strategy(
    request: ProjectionRequest,
    *,
    execution_context: ExecutionContext | None = None,
    materialisation_estimate: MaterialisationEstimate | None | object = (
        _AUTO_MATERIALISATION_ESTIMATE
    ),
    runtime_source_frames_by_node: RuntimeSourceFrames | None = None,
    materialising_node_ids: Iterable[str] | None = None,
    estimation_graph: PipelineGraph | None = None,
) -> ExecutionStrategyResult:
    """Return the sole route-facing V1 execution-planning result.

    ``materialising_node_ids`` limits admission and estimation to the
    materialisations the execution will actually perform; a planned execution
    passes the nodes it builds, so a branch it never builds (or that a seed
    covers) is neither estimated nor refused. ``estimation_graph`` replaces
    the request graph for materialisation estimates only — a planned execution
    passes one whose seeds are Parquet inputs of their leased generations.
    """
    prepared = prepare_graph(
        request.graph,
        request.target_node_id,
        source=request.source,
    )
    children_of = _children_of(prepared.order, prepared.parents_of)
    required_columns_by_node, projection_plan = _prepared_projection_plan(prepared, request)
    materialising_sequences = _only_nodes(
        materialising_operator_sequences_by_node(
            prepared.order,
            prepared.node_map,
            relevant_edges=prepared.relevant_edges,
            submodels=prepared.submodels,
        ),
        materialising_node_ids,
    )
    materialising_operators = first_materialising_operators(materialising_sequences)
    resolved_estimate: MaterialisationEstimate | None
    if materialising_operators:
        if materialisation_estimate is _AUTO_MATERIALISATION_ESTIMATE:
            resolved_estimate = _estimate_materialising_boundaries(
                estimation_graph if estimation_graph is not None else request.graph,
                materialising_sequences,
                source=request.source,
                projection_plan=projection_plan,
                runtime_source_frames_by_node=runtime_source_frames_by_node,
            )
        elif materialisation_estimate is None:
            resolved_estimate = MaterialisationEstimate.unavailable(
                "materialisation_estimate_not_supplied"
            )
        elif isinstance(materialisation_estimate, MaterialisationEstimate):
            resolved_estimate = materialisation_estimate
        else:
            raise TypeError("materialisation_estimate must be a MaterialisationEstimate or None")
    else:
        resolved_estimate = None
    result = _finalise_execution_strategy(
        projection_plan,
        profile=request.profile,
        order=prepared.order,
        children_of=children_of,
        node_map=prepared.node_map,
        has_projection_seed=bool(required_columns_by_node),
        materialising_operators=materialising_operators,
        execution_context=execution_context,
        materialisation_estimate=resolved_estimate,
        required_columns_by_node=required_columns_by_node,
    )
    if execution_context is not None:
        execution_context.projection_plan = result
    return result


def _prepared_projection_plan(
    prepared: PreparedGraph,
    request: ProjectionRequest,
) -> tuple[dict[str, set[str] | AllExceptColumns], ProjectionPlan]:
    required_columns_by_node = normalise_required_columns_by_node(
        request.required_columns_by_node,
        prepared.order,
    )
    projection_plan = compute_prepared_plan(
        prepared.order,
        _children_of(prepared.order, prepared.parents_of),
        prepared.node_map,
        required_columns_by_node=required_columns_by_node,
        relevant_edges=prepared.relevant_edges,
        submodels=prepared.submodels,
        selector_aliases=preamble_selector_aliases(request.graph.preamble or ""),
    )
    projection_plan = with_api_input_port_projection_boundaries(
        projection_plan,
        prepared.node_map,
        prepared.relevant_edges,
    )
    return required_columns_by_node, projection_plan


def _estimate_materialising_boundaries(
    graph: PipelineGraph,
    boundary_operators: Mapping[str, Sequence[str]],
    *,
    source: str,
    projection_plan: ProjectionPlan | None = None,
    runtime_source_frames_by_node: RuntimeSourceFrames | None = None,
) -> MaterialisationEstimate:
    """Return the conservative peak across every declared materialisation boundary."""
    peak_bytes = 0
    assumptions: list[str] = []
    basis = MaterialisationEstimateBasis.PROJECTED_COLUMNS
    depends_on_many_to_many_join = False
    estimates = estimate_materialisation_boundaries(
        graph,
        boundary_operators,
        source=source,
        boundary_operators=boundary_operators,
        edge_demands=(projection_plan.edge_demands if projection_plan is not None else None),
        runtime_source_frames_by_node=runtime_source_frames_by_node,
    )
    for node_id, estimate in estimates:
        if estimate.state is MaterialisationEstimateState.UNAVAILABLE:
            reason = estimate.unavailable_reason or "unknown"
            return MaterialisationEstimate.unavailable(f"{node_id}:{reason}")
        assert estimate.estimated_peak_bytes is not None
        peak_bytes = max(peak_bytes, estimate.estimated_peak_bytes)
        if estimate.basis is not MaterialisationEstimateBasis.PROJECTED_COLUMNS:
            basis = MaterialisationEstimateBasis.COMPLETE_WIDTH_FALLBACK
        depends_on_many_to_many_join = (
            depends_on_many_to_many_join or estimate.depends_on_many_to_many_join
        )
        assumptions.extend(f"{node_id}: {item}" for item in estimate.assumptions)
    return MaterialisationEstimate.available(
        peak_bytes,
        assumptions=assumptions,
        basis=basis,
        depends_on_many_to_many_join=depends_on_many_to_many_join,
    )


def plan_prepared_execution_strategy(
    order: list[str],
    children_of: Mapping[str, list[str]],
    node_map: Mapping[str, GraphNode],
    *,
    profile: ExecutionProfile,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
    materialisation_estimate: MaterialisationEstimate | None = None,
    schema_only: bool = False,
    relevant_edges: Iterable[GraphEdge],
    submodels: Mapping[str, Any] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
    materialising_node_ids: Iterable[str] | None = None,
) -> ExecutionStrategyResult:
    """Plan projection/streaming strategy for an already prepared graph.

    ``schema_only`` declares that the caller resolves lazy schemas and never
    collects a frame or invokes a sink. The group-by admission gate below
    bounds peak memory *during materialisation*; schema resolution
    materialises nothing, so under that declaration the gate is not evaluated
    and no materialisation boundary is inserted. The prepared
    ``relevant_edges`` carry API-input port identity for projection diagnostics.
    """
    prepared_relevant_edges = tuple(relevant_edges)
    required_columns_by_node = normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    projection_plan = compute_prepared_plan(
        order,
        children_of,
        dict(node_map),
        required_columns_by_node=required_columns_by_node,
        relevant_edges=prepared_relevant_edges,
        submodels=submodels,
        selector_aliases=selector_aliases,
    )
    projection_plan = with_api_input_port_projection_boundaries(
        projection_plan,
        node_map,
        prepared_relevant_edges,
    )
    materialising_operators = first_materialising_operators(
        _only_nodes(
            materialising_operator_sequences_by_node(
                order,
                node_map,
                relevant_edges=prepared_relevant_edges,
                submodels=submodels,
            ),
            materialising_node_ids,
        )
    )
    result = _finalise_execution_strategy(
        projection_plan,
        profile=profile,
        order=order,
        children_of=children_of,
        node_map=node_map,
        has_projection_seed=bool(required_columns_by_node),
        materialising_operators=materialising_operators,
        execution_context=execution_context,
        materialisation_estimate=materialisation_estimate,
        required_columns_by_node=required_columns_by_node,
        schema_only=schema_only,
    )
    if execution_context is not None:
        execution_context.projection_plan = result
    return result


_NodeValue = TypeVar("_NodeValue")


def _only_nodes(
    by_node: Mapping[str, _NodeValue],
    node_ids: Iterable[str] | None,
) -> Mapping[str, _NodeValue]:
    """Keep only the named nodes' entries; ``None`` keeps every entry."""
    if node_ids is None:
        return by_node
    keep = frozenset(node_ids)
    return {node_id: value for node_id, value in by_node.items() if node_id in keep}


def _children_of(
    order: Iterable[str],
    parents_of: Mapping[str, Iterable[str]],
) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node_id: [] for node_id in order}
    for child_id, parent_ids in parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children:
                children[parent_id].append(child_id)
    return children


MANY_TO_MANY_JOIN_DETAIL = "join_cardinality_many_to_many"
"""Estimator detail for a join whose only row bound is the many-to-many product."""

# A diagnostic's remediation is capped at 512 characters, and this is appended to a
# message about the run, so it stays short: the execution strategy guide explains.
_MANY_TO_MANY_JOIN_REMEDIATION = (
    "A many-to-many product bounds this join: validate='m:1', '1:m' or '1:1' on a "
    "unique key gives an estimate, but Polars then holds both inputs in memory, up to "
    "76% more peak."
)


def _with_many_to_many_join_remediation(remediation: str, detail: str | None) -> str:
    """Append the validate= contract advice when the gap is an unbounded join."""

    if detail is None or not detail.endswith(MANY_TO_MANY_JOIN_DETAIL):
        return remediation
    return f"{remediation} {_MANY_TO_MANY_JOIN_REMEDIATION}"


def _materialisation_rejection(
    *,
    node_id: str,
    operator: str,
    profile: ExecutionProfile,
    reason_code: str,
    estimated_peak_bytes: int | None,
    headroom_bytes: int | None,
    estimate_detail: str | None = None,
) -> GroupByExecutionUnsupportedError:
    remediation = {
        "execution_admission_unavailable": (
            "Create an admitted execution context with positive memory-limit and "
            f"headroom values before running this '{operator}'."
        ),
        "materialisation_estimate_unavailable": (
            "Provide readable source/schema metadata so Haute can estimate the full "
            f"'{operator}' materialisation boundary before execution."
        ),
        "materialisation_exceeds_headroom": (
            "Increase the configured memory headroom, narrow the input, or pre-aggregate "
            f"the source before this '{operator}'."
        ),
    }[reason_code]
    if estimate_detail:
        # The estimator already knows which node it could not measure and why.
        # Discarding that left the analyst with "provide readable metadata" and
        # no way to tell an unreadable file from an unsummarisable source shape.
        remediation = f"{remediation} Estimator reported: {estimate_detail}."
    if reason_code == "materialisation_estimate_unavailable":
        # Without a hard cap there is no bounded envelope to run inside.
        remediation = (
            f"{remediation} This surface runs without a hard worker memory cap, "
            "so Haute cannot run the plan conservatively here."
        )
    remediation = _with_many_to_many_join_remediation(remediation, estimate_detail)
    return GroupByExecutionUnsupportedError(
        f"Materialisation of '{operator}' could not be admitted for this execution.",
        node_id=node_id,
        operator=operator,
        profile=profile.value,
        reason_code=reason_code,
        remediation=remediation,
        estimated_peak_bytes=estimated_peak_bytes,
        headroom_bytes=headroom_bytes,
    )


@dataclass(frozen=True, slots=True)
class _UnprovenMaterialisation:
    """The plan and diagnostic fields for a boundary with no usable estimate."""

    projection_plan: ProjectionPlan
    strategy: ExecutionStrategy
    reason_code: str
    assumptions: tuple[str, ...]
    remediation: str


def _plan_unproven_materialisation(
    projection_plan: ProjectionPlan,
    *,
    node_id: str,
    operator: str,
    profile: ExecutionProfile,
    materialising_operators: Mapping[str, str],
    detail: str,
    headroom_bytes: int,
) -> _UnprovenMaterialisation:
    """Run conservatively under a hard cap, or reject when there is no cap.

    Both the estimator reporting no estimate and the planner refusing a
    many-to-many join's row product land here: neither has a number that bounds
    the boundary, and the only difference is what the analyst is told to fix.
    """
    backend = current_native_memory_backend()
    if backend is None:
        raise _materialisation_rejection(
            node_id=node_id,
            operator=operator,
            profile=profile,
            reason_code="materialisation_estimate_unavailable",
            estimated_peak_bytes=None,
            headroom_bytes=headroom_bytes,
            estimate_detail=detail,
        )
    # A hard worker cap bounds the process, so the run continues under
    # its full reserved envelope instead of being rejected outright.
    remediation = (
        "The run continued under its full reserved memory envelope of "
        f"{headroom_bytes} bytes because the materialisation estimate was "
        f"unavailable ({detail}). Provide readable source metadata or rewrite "
        f"'{operator}' at '{node_id}' so Haute can prove the estimate; the run "
        "may use more memory and time than an estimated boundary."
    )
    return _UnprovenMaterialisation(
        projection_plan=with_materialisation_boundaries(
            projection_plan,
            materialising_operators,
        ),
        strategy=ExecutionStrategy.FULL_WIDTH_CONSERVATIVE,
        reason_code="materialisation_estimate_unavailable_conservative",
        assumptions=(
            f"proof_gap={detail}",
            f"reserved_envelope_bytes={headroom_bytes}",
            f"hard_cap_backend={backend}",
            "disabled_optimisations=estimate_based_admission",
        ),
        # The envelope narrative is capped, then the contract advice is appended
        # so it is never the half that gets cut.
        remediation=_with_many_to_many_join_remediation(remediation[:512], detail),
    )


def _finalise_execution_strategy(
    projection_plan: ProjectionPlan,
    *,
    profile: ExecutionProfile,
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
    node_map: Mapping[str, GraphNode],
    has_projection_seed: bool,
    materialising_operators: Mapping[str, str],
    execution_context: ExecutionContext | None,
    materialisation_estimate: MaterialisationEstimate | None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
    schema_only: bool = False,
) -> ExecutionStrategyResult:
    strategy: ExecutionStrategy | None = None
    reason_code: str | None = None
    remediation: str | None = None
    estimated_peak_bytes: int | None = None
    raw_estimated_peak_bytes: int | None = None
    estimate_calibration_factor_basis_points: int | None = None
    estimate_admission_basis: str | None = None
    headroom_bytes: int | None = None
    assumptions: tuple[str, ...] = ()

    if materialising_operators and not schema_only:
        node_id, operator = next(iter(materialising_operators.items()))
        admission = execution_context.admission if execution_context is not None else None
        if (
            admission is None
            or not admission.admitted
            or not isinstance(admission.memory_limit_bytes, int)
            or isinstance(admission.memory_limit_bytes, bool)
            or admission.memory_limit_bytes <= 0
            or not isinstance(admission.headroom_bytes, int)
            or isinstance(admission.headroom_bytes, bool)
            or admission.headroom_bytes <= 0
        ):
            raise _materialisation_rejection(
                node_id=node_id,
                operator=operator,
                profile=profile,
                reason_code="execution_admission_unavailable",
                estimated_peak_bytes=None,
                headroom_bytes=None,
            )
        headroom_bytes = min(admission.memory_limit_bytes, admission.headroom_bytes)
        if (
            materialisation_estimate is None
            or materialisation_estimate.state is MaterialisationEstimateState.UNAVAILABLE
        ):
            detail: str
            if materialisation_estimate is None:
                detail = "no materialisation estimate was requested"
            else:
                # ``MaterialisationEstimate.unavailable`` rejects an empty reason.
                assert materialisation_estimate.unavailable_reason is not None
                detail = materialisation_estimate.unavailable_reason
            unproven = _plan_unproven_materialisation(
                projection_plan,
                node_id=node_id,
                operator=operator,
                profile=profile,
                materialising_operators=materialising_operators,
                detail=detail,
                headroom_bytes=headroom_bytes,
            )
            projection_plan = unproven.projection_plan
            strategy = unproven.strategy
            reason_code = unproven.reason_code
            assumptions = unproven.assumptions
            remediation = unproven.remediation
        else:
            raw_estimated_peak_bytes = materialisation_estimate.estimated_peak_bytes
            assert raw_estimated_peak_bytes is not None
            calibrated = calibrate_materialisation_bytes(profile, raw_estimated_peak_bytes)
            estimated_peak_bytes = calibrated.calibrated_bytes
            estimate_calibration_factor_basis_points = calibrated.factor_basis_points
            estimate_admission_basis = materialisation_estimate.basis.value
            if (
                estimated_peak_bytes > headroom_bytes
                and materialisation_estimate.depends_on_many_to_many_join
            ):
                # The row product is not an estimate of anything the join will
                # actually hold; it is the absence of one. Rejecting on it would
                # report a measured over-run that was never measured, so the
                # boundary is treated as unproven instead.
                unproven = _plan_unproven_materialisation(
                    projection_plan,
                    node_id=node_id,
                    operator=operator,
                    profile=profile,
                    materialising_operators=materialising_operators,
                    detail=f"{node_id}:{MANY_TO_MANY_JOIN_DETAIL}",
                    headroom_bytes=headroom_bytes,
                )
                projection_plan = unproven.projection_plan
                strategy = unproven.strategy
                reason_code = unproven.reason_code
                assumptions = unproven.assumptions
                remediation = unproven.remediation
                # The product was never an estimate, so none is reported.
                raw_estimated_peak_bytes = None
                estimated_peak_bytes = None
                estimate_calibration_factor_basis_points = None
                estimate_admission_basis = None
            elif estimated_peak_bytes > headroom_bytes:
                raise _materialisation_rejection(
                    node_id=node_id,
                    operator=operator,
                    profile=profile,
                    reason_code="materialisation_exceeds_headroom",
                    estimated_peak_bytes=estimated_peak_bytes,
                    headroom_bytes=headroom_bytes,
                )
            else:
                projection_plan = with_materialisation_boundaries(
                    projection_plan,
                    materialising_operators,
                )
                strategy = ExecutionStrategy.MATERIALISATION_BOUNDARY
                reason_code = "materialisation_admitted"
                remediation = (
                    f"Keep the admitted '{operator}' boundary at '{node_id}' within "
                    "its reported memory headroom."
                )
                assumptions = (
                    *materialisation_estimate.assumptions,
                    f"raw_estimated_peak_bytes={raw_estimated_peak_bytes}",
                    f"calibrated_estimated_peak_bytes={estimated_peak_bytes}",
                    (
                        "estimate_calibration_factor_basis_points="
                        f"{estimate_calibration_factor_basis_points}"
                    ),
                    f"estimate_admission_basis={estimate_admission_basis}",
                )

    return build_execution_strategy_result(
        projection_plan,
        profile=profile,
        order=order,
        children_of=children_of,
        node_map=node_map,
        has_projection_seed=has_projection_seed,
        required_columns_by_node=required_columns_by_node,
        strategy=strategy,
        reason_code=reason_code,
        boundary_operators=materialising_operators,
        remediation=remediation,
        estimated_peak_bytes=estimated_peak_bytes,
        raw_estimated_peak_bytes=raw_estimated_peak_bytes,
        estimate_calibration_factor_basis_points=(estimate_calibration_factor_basis_points),
        estimate_admission_basis=estimate_admission_basis,
        headroom_bytes=headroom_bytes,
        assumptions=assumptions,
    )


def _stat_gated_runtime_path_fingerprint(path: Path) -> Mapping[str, object]:
    """Return the runtime identity of one local path.

    A file is signed by the shared source proof
    (:func:`haute._json_shred._source_proof.file_signature`): its content hash
    is computed once per unchanged freshness token and shared with every other
    consumer of the same file (a Data Input or API Input snapshot's freshness,
    a utility hash), so preview/trace keys cost a hash per edit, not per
    request. A missing path or a directory is signed by its stat alone. OS
    errors propagate: an unreadable input fails the request loudly rather than
    fingerprinting as something it is not.
    """
    resolved = path.resolve()
    if not resolved.exists():
        return {
            "path": str(resolved),
            "exists": False,
        }
    if not resolved.is_file():
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "exists": True,
            "is_file": False,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    signature = file_signature(resolved)
    return {
        "path": str(resolved),
        "exists": True,
        "is_file": True,
        "size": signature.size,
        "mtime_ns": signature.mtime_ns,
        "hash_algo": HASH_ALGO,
        "content_hash": signature.digest,
    }


def dataframe_paths_input_fingerprint(paths: Mapping[str, str]) -> Mapping[str, object]:
    """Return stable file-state fingerprints for named external path inputs."""

    payload: dict[str, object] = {}
    for key, raw_path in sorted(paths.items()):
        if not isinstance(key, str) or not key:
            raise ValueError("external path fingerprint keys must be non-empty strings")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("external path fingerprint values must be non-empty strings")
        path = Path(raw_path).resolve()
        payload[key] = _stat_gated_runtime_path_fingerprint(path)
    return payload


def dataframe_frame_input_fingerprint(input_df: Any) -> Mapping[str, object]:
    """Fingerprint an in-memory Polars input frame for dynamic lazy execution."""

    import polars as pl

    if not isinstance(input_df, pl.DataFrame):
        raise TypeError("input_df must be a polars DataFrame")
    schema = {name: str(dtype) for name, dtype in input_df.schema.items()}
    # The payload tag below versions this canonical byte encoding, not Polars'
    # hash algorithm. A dependency upgrade may therefore cold-start these keys,
    # which is safe: changed hashes over-invalidate instead of serving stale data.
    row_hash_bytes = (
        input_df.hash_rows(seed=0).to_numpy().astype("<u8", copy=False).tobytes(order="C")
    )
    return {
        "height": input_df.height,
        "width": input_df.width,
        "schema": schema,
        "row_hash_encoding": _DATAFRAME_ROW_HASH_ENCODING,
        "row_hash": content_hash_bytes(row_hash_bytes),
    }


def _runtime_path_from_graph_config(
    graph: PipelineGraph,
    raw_path: str,
) -> Path:
    return (
        resolve_node_input_path(raw_path, source_file=graph.source_file)
        if graph.source_file
        else Path(raw_path)
    )


def _config_subset(config: Mapping[str, Any], keys: Iterable[str]) -> Mapping[str, object]:
    return {
        key: config[key]
        for key in sorted(keys)
        if key in config and isinstance(config[key], str | int | float | bool | type(None))
    }


def _local_runtime_input_path_fields(node: GraphNode) -> tuple[str, ...]:
    """Return config fields that cause reads from the local filesystem."""
    inputs = _RUNTIME_INPUTS[node.data.nodeType]
    return () if inputs is None else inputs.local_paths(node.data.config)


def _runtime_input_path_fields(node: GraphNode) -> tuple[str, ...]:
    """Return path-shaped inputs that require request-boundary validation.

    This includes the MLflow ``modelScore.artifact_path`` identifier so route
    validation continues to reject traversal-shaped values, even though that
    identifier must never be resolved as a local project file by execution.
    """
    inputs = _RUNTIME_INPUTS[node.data.nodeType]
    identifiers = () if inputs is None else inputs.identifier_paths
    return (*_local_runtime_input_path_fields(node), *identifiers)


def _snapshot_source_signature(
    graph: PipelineGraph,
    config: Mapping[str, object],
) -> str | None:
    """Current source signature of a snapshot-backed input (``None`` when none)."""
    from haute._input_providers import source_signature
    from haute._project_context import current_project

    try:
        return source_signature(
            config,
            base_dir=graph_pipeline_dir(graph) or current_project().pipeline_dir,
        )
    except (TypeError, ValueError):
        return None


def _snapshot_source_files(graph: PipelineGraph, config: Mapping[str, object]) -> tuple[Path, ...]:
    """The files :func:`_snapshot_source_signature` hashes (empty when none).

    Called only for an input whose generation pointer resolved, whose cache
    identity already validated and anchored this configuration.
    """
    from haute._input_providers import signed_source_files
    from haute._project_context import current_project

    return signed_source_files(
        config,
        base_dir=graph_pipeline_dir(graph) or current_project().pipeline_dir,
    )


def mlflow_backend_signature(config: Mapping[str, object]) -> object:
    """Secret-free identity of the backend an MLflow-sourced node reads from.

    Returns ``resolve_backend(<node destination>).identity`` — never the
    tracking URI, which may carry environment credentials — or an
    ``{"unresolved": <reason>}`` marker when the destination's prerequisites
    are missing, so a warm entry minted from a resolvable state can never be
    served once configuration is removed. Execution itself still raises.
    """
    from haute._mlflow_utils import resolve_backend
    from haute.errors import MlflowConfigError

    destination = str(config.get("mlflow_destination", "") or "")
    try:
        return resolve_backend(destination).identity
    except MlflowConfigError as exc:
        return {"unresolved": str(exc)}


def _mlflow_registered_version_signature(config: Mapping[str, object]) -> object:
    """The concrete version a registered-model source targets right now.

    An alias, ``"latest"`` or an empty version resolves through the registry,
    and its target moves while the node config stays identical; keying on the
    reference text would replay predictions from the version it left. A
    concrete version is immutable and needs no lookup. A failed lookup returns
    an ``{"unresolved": <exception type>, "attempt": <random id>}`` marker —
    never the message, which can carry a server URL. The random id makes every
    failed lookup a distinct identity, so an entry written after one failure
    (for example when the registry recovers before the model load) is never
    served during a later failure; execution itself reports the failure.
    """
    version = str(config.get("version", "") or "")
    alias = str(config.get("alias", "") or "")
    if not alias and version not in ("", "latest"):
        return version
    from haute._mlflow_utils import resolve_mlflow_source

    try:
        _run_id, resolved_version, _mlflow, _client, _backend = resolve_mlflow_source(
            source_type="registered",
            registered_model=str(config.get("registered_model", "") or ""),
            version=version,
            destination=str(config.get("mlflow_destination", "") or ""),
            alias=alias,
        )
    except Exception as exc:
        return {"unresolved": type(exc).__name__, "attempt": uuid.uuid4().hex}
    return resolved_version


def _runtime_input_fingerprint_entry(
    graph: PipelineGraph,
    node: GraphNode,
) -> Mapping[str, object]:
    """Identity material for one node's runtime inputs.

    Beyond the signed files and the node's runtime-input config fields, an
    MLflow-sourced ``MODEL_SCORE`` or ``OPTIMISER_APPLY`` node records the
    *resolved* backend identity, because its stored config (``run_id``,
    ``version``, ``source_type``, ``mlflow_destination``) stays identical when
    the server URL, the local folder, or the node's destination changes
    underneath it. A registered source also records the version its alias or
    ``latest`` reference currently targets.
    """
    config = node.data.config
    files: dict[str, object] = {
        path_field: _stat_gated_runtime_path_fingerprint(path)
        for path_field, path in _runtime_file_signature_paths(graph, node).items()
    }
    if "snapshot_pointer" in files:
        # A snapshot-backed input is signed by its generation pointer *and* the
        # current source signature, so a rewritten source misses every cache
        # and reaches automatic preparation instead of serving a stale
        # generation from a warm entry.
        files["source_signature"] = _snapshot_source_signature(graph, config)
    inputs = _RUNTIME_INPUTS[node.data.nodeType]
    if (
        inputs is not None
        and inputs.mlflow_sourced
        and config.get("source_type") in ("run", "registered")
    ):
        files["mlflow_backend"] = mlflow_backend_signature(config)
        if config.get("source_type") == "registered":
            files["registered_version"] = _mlflow_registered_version_signature(config)
    return checked_cache_identity_record(
        CacheIdentityRecord.RUNTIME_INPUT_ENTRY,
        {
            "node_id": node.id,
            "node_type": node.data.nodeType.value,
            "config": _config_subset(config, _runtime_input_config_fields(node.data.nodeType)),
            "files": files,
        },
    )


def _reused_runtime_input_entry(
    graph: PipelineGraph,
    node: GraphNode,
    entries: dict[str, tuple[GraphNode, str | None, Mapping[str, object]]],
) -> Mapping[str, object]:
    # An entry reads the graph only through its source file.
    known = entries.get(node.id)
    if known is not None and known[0] is node and known[1] == graph.source_file:
        return known[2]
    entry = _runtime_input_fingerprint_entry(graph, node)
    entries[node.id] = (node, graph.source_file, entry)
    return entry


@dataclass(frozen=True, slots=True)
class RuntimeInputIdentity:
    """The runtime inputs of one scope, read once.

    Hashing it, with or without extra entries, reads nothing: a fingerprint
    built from it describes the inputs as they were when it was read, however
    long ago that was.
    """

    payload: Mapping[str, object]

    def fingerprint(self, extra: Mapping[str, object] | None = None) -> str:
        inputs = checked_cache_inputs(
            CacheConsumer.RUNTIME_GRAPH_INPUT,
            {**self.payload, "extra": dict(sorted((extra or {}).items()))},
        )
        digest = content_hash_bytes(inputs.canonical_bytes)
        return f"runtime-input:v{inputs.contract.version}:{digest}"

    @property
    def digest(self) -> str:
        """The identity itself, for comparing two reads of the same scope."""
        return self.fingerprint()


def dataframe_graph_input_fingerprint(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    extra_fingerprints: Mapping[str, object] | None = None,
    ignore_node_ids: Iterable[str] = (),
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Fingerprint source-side inputs that sit outside graph structure.

    The target lineage is scoped once and every maintained runtime-input
    class is routed through the checked ``RUNTIME_GRAPH_INPUT`` contract. This
    component is not a standalone execution identity: callers pair it with
    their checked graph or lineage fingerprint.
    """
    return dataframe_graph_input_identity(
        graph,
        target_node_id=target_node_id,
        source=source,
        ignore_node_ids=ignore_node_ids,
        memo=memo,
    ).fingerprint(extra_fingerprints)


def dataframe_graph_input_identity(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    ignore_node_ids: Iterable[str] = (),
    memo: GraphFingerprintMemo | None = None,
) -> RuntimeInputIdentity:
    """Read the runtime inputs :func:`dataframe_graph_input_fingerprint` signs."""

    return canonical_graph_input_identity(
        canonical_dataframe_execution_graph(graph),
        target_node_id=target_node_id,
        source=source,
        ignore_node_ids=ignore_node_ids,
        memo=memo,
    )


def canonical_graph_input_identity(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    ignore_node_ids: Iterable[str] = (),
    memo: GraphFingerprintMemo | None = None,
    reuse_entries: bool = False,
) -> RuntimeInputIdentity:
    """:func:`dataframe_graph_input_identity` of a graph that is already canonical.

    A subgraph of a canonical graph is canonical. With *reuse_entries*, a node's
    runtime-input entry is read once per *memo*, as a resolution signing every
    node of one lineage needs it.
    """
    if target_node_id is not None and target_node_id not in graph.node_map:
        raise ValueError(f"Cannot fingerprint inputs for unknown node {target_node_id!r}")
    ignored = set(ignore_node_ids)

    included_node_ids = (
        set(upstream_node_ids(target_node_id, graph.parents_of)) | {target_node_id}
        if target_node_id is not None
        else {node.id for node in graph.nodes}
    )
    included_node_ids -= ignored
    scoped_graph = graph.model_copy(
        update={
            "nodes": [node for node in graph.nodes if node.id in included_node_ids],
            "edges": [
                edge
                for edge in graph.edges
                if edge.source in included_node_ids and edge.target in included_node_ids
            ],
        }
    )
    entries = memo.runtime_input_entries if memo is not None and reuse_entries else None
    source_entries = [
        _runtime_input_fingerprint_entry(scoped_graph, node)
        if entries is None
        else _reused_runtime_input_entry(scoped_graph, node, entries)
        for node in sorted(scoped_graph.nodes, key=lambda item: item.id)
        if node.data.nodeType in _RUNTIME_INPUT_NODE_TYPES
    ]
    return RuntimeInputIdentity(
        {
            "source": source,
            "sources": source_entries,
            "preamble_fingerprint": preamble_execution_fingerprint(
                scoped_graph.preamble,
                pipeline_dir=graph_pipeline_dir(scoped_graph),
                memo=memo,
            ),
        }
    )


def runtime_input_signed_paths(graph: PipelineGraph) -> tuple[Path, ...]:
    """The resolved local files :func:`dataframe_graph_input_identity` signs for every node.

    Each node's signed paths, and for a snapshot-backed input the source files
    its source signature hashes beside the generation pointer, listed only while
    they exist: a source that is gone is signed as missing, not as a file. Read
    from configuration plus a listing of the files, so listing them touches no
    file's content. The preamble's imported utility modules, which the identity
    signs through the preamble fingerprint, are not listed.
    """
    canonical = canonical_dataframe_execution_graph(graph)
    paths: set[Path] = set()
    for node in canonical.nodes:
        if node.data.nodeType not in _RUNTIME_INPUT_NODE_TYPES:
            continue
        signed = _runtime_file_signature_paths(canonical, node)
        paths.update(path.resolve() for path in signed.values())
        if "snapshot_pointer" in signed:
            paths.update(_snapshot_source_files(canonical, node.data.config))
    return tuple(sorted(paths, key=str))


def _runtime_file_signature_paths(graph: PipelineGraph, node: GraphNode) -> dict[str, Path]:
    """Resolved paths of every file *node* actually consumes at preview.

    The map mirrors each builder's runtime dispatch — sign exactly what
    gets read, nothing else:

    * **apiInput** - signs the configured raw path for both flat files
      and structured (JSON, JSONL, NDJSON, XML) sources. A structured source
      executes from its tables' input snapshots, so it also signs each
      emitting table's generation pointer: a rebuild, refresh, or clear of a
      table invalidates execution caches, and a rewritten source misses them
      and reaches automatic preparation.
    * **dataInput** — direct Parquet signs the configured source and, for a
      folder or glob pattern, every file it names (``path:<n>``), so a rewritten,
      added or removed file changes the identity; snapshot-backed inputs sign the
      active generation pointer, so only an explicit refresh invalidates
      execution caches.
    * **everything else** — the per-node config path fields shared with
      :func:`_local_runtime_input_path_fields`: ``externalFile`` paths,
      ``modelScore`` feature-contract paths and model files, and file-sourced
      ``optimiserApply`` artifacts. MLflow artifact identifiers are config
      identity, not local files. A file-sourced ``modelScore`` without an
      explicit contract also signs both contract candidates beside its model
      (``contract_candidate:<n>``), so replacing, adding or deleting the
      contract it would score under changes the identity.
    """
    node_type = node.data.nodeType
    config = node.data.config
    if node_type in REQUEST_INPUT_NODE_TYPES:
        raw_path = config.get("path")
        if isinstance(raw_path, str) and raw_path:
            path = _runtime_path_from_graph_config(graph, raw_path)
            return {"path": path, **_api_input_table_pointer_paths(config, path)}
        return {}
    inputs = _RUNTIME_INPUTS[node_type]
    if inputs is not None and inputs.data_source:
        from haute._polars_io_registry import data_input_is_direct

        if data_input_is_direct(config):
            raw_path = config.get("path")
            if isinstance(raw_path, str) and raw_path:
                path = _runtime_path_from_graph_config(graph, raw_path)
                return {"path": path, **_direct_source_files(config, path)}
            return {}
        try:
            from haute._input_providers import source_cache_identity
            from haute._project_context import current_project
            from haute._source_cache import SourceCacheStore

            identity = source_cache_identity(
                config,
                base_dir=graph_pipeline_dir(graph) or current_project().pipeline_dir,
            )
            pointer = (
                SourceCacheStore(current_project().root).identity_path(identity) / "current.json"
            )
            return {"snapshot_pointer": pointer}
        except (TypeError, ValueError):
            return {}
    paths: dict[str, Path] = {}
    for path_field in _local_runtime_input_path_fields(node):
        raw = config.get(path_field)
        if isinstance(raw, str) and raw:
            paths[path_field] = _runtime_path_from_graph_config(graph, raw)
    if "model_path" in paths and not config.get("feature_contract_path"):
        from haute._mlflow_io import model_contract_candidates

        for index, candidate in enumerate(model_contract_candidates(paths["model_path"])):
            # Contained before it is hashed: a sibling that resolves outside the
            # project through a symlink is refused, never read.
            paths[f"contract_candidate:{index}"] = resolve_node_input_path(
                str(candidate), source_file=graph.source_file
            )
    return paths


def _direct_source_files(config: Mapping[str, Any], path: Path) -> dict[str, Path]:
    """Every file a direct Data Input's folder or glob pattern names, keyed ``path:<n>``.

    A single file is signed by its own ``path`` entry and adds none.
    """
    from haute._input_providers import named_source_files

    if path.is_file():
        return {}
    files = named_source_files(config, path)
    return {f"path:{index}": file for index, file in enumerate(files)}


def _api_input_table_pointer_paths(config: Mapping[str, Any], path: Path) -> dict[str, Path]:
    """The generation pointer of each emitting table of a structured API input.

    Keyed ``snapshot_pointer:<label>``. A flat-file source, or a schema the
    node builder will reject, has none.
    """
    if not is_json_api_input_path(str(path)) or not isinstance(config.get("tables"), list):
        return {}
    from haute._api_input_schema import ApiInputSchemaError
    from haute._json_shred._snapshots import api_input_snapshot_source
    from haute._project_context import current_project
    from haute._source_cache import SourceCacheStore

    try:
        source = api_input_snapshot_source(config, path)
    except (ApiInputSchemaError, TypeError, ValueError):
        return {}
    store = SourceCacheStore(current_project().root)
    return {
        f"snapshot_pointer:{table.label}": store.identity_path(table.identity) / "current.json"
        for table in source.tables
    }


def source_lineage_graph(
    graph: PipelineGraph, target_node_id: str | None, *, source: str
) -> PipelineGraph:
    """Return *target_node_id* and its lineage as the executor runs it for *source*.

    The executor's own live-switch pruning (``prepare_graph``) drops every
    switch input *source* does not read, so a branch it never executes is
    neither signed nor hashed by an identity built on this graph.
    """
    prepared = prepare_graph(graph, target_node_id, source=source)
    relevant_ids = set(prepared.order)
    return graph.model_copy(
        update={
            "nodes": [node for node in graph.nodes if node.id in relevant_ids],
            "edges": list(prepared.relevant_edges),
        }
    )


def _preview_contract_fingerprint(
    *,
    enforce_contracts: bool,
    materialisation_scope: str,
) -> str:
    if type(enforce_contracts) is not bool:
        raise TypeError("enforce_contracts must be a bool")
    if materialisation_scope not in {"full", "target_only"}:
        raise ValueError("materialisation_scope must be 'full' or 'target_only'")
    payload = {
        "schema_version": _PREVIEW_CONTRACT_FINGERPRINT_VERSION,
        "enforce_contracts": enforce_contracts,
        "materialisation_scope": materialisation_scope,
    }
    digest = content_hash_bytes(canonical_json(payload).encode())
    return f"preview-contract-v{_PREVIEW_CONTRACT_FINGERPRINT_VERSION}:{digest}"


def lineage_runtime_input_identity(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    memo: GraphFingerprintMemo | None = None,
) -> RuntimeInputIdentity:
    """Read the runtime inputs of *target_node_id*'s source-pruned lineage.

    The identity the preview/trace cache key signs; a caller that must key an
    entry by the inputs its execution read, not by a later read, keeps it.
    """
    return dataframe_graph_input_identity(
        source_lineage_graph(graph, target_node_id, source=source),
        target_node_id=None,
        source=source,
        memo=memo,
    )


def preview_lineage_cache_key(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    requested_columns: Iterable[str] | None,
    initial_column_limit: int | None,
    row_limit: int | None,
    port_label: str | None,
    enforce_contracts: bool,
    materialisation_scope: str,
    memo: GraphFingerprintMemo | None = None,
    runtime_input_identity: RuntimeInputIdentity | None = None,
    seed_plan_fingerprint: str | None = None,
) -> str:
    """Return the sole preview/trace cache identity for one target lineage.

    *seed_plan_fingerprint* names the snapshot generations the execution
    seeded from; it joins the runtime-input fingerprint, so an entry computed
    from one seed generation is never served for another. An execution that
    seeds nothing passes ``None`` and computes, and is keyed as, the same data
    as one without a plan. *runtime_input_identity*, when given, is the read
    of the lineage's inputs to sign instead of reading them again.
    """
    prepared = prepare_graph(graph, target_node_id, source=source)
    identity = (
        runtime_input_identity
        if runtime_input_identity is not None
        else lineage_runtime_input_identity(
            graph, target_node_id=target_node_id, source=source, memo=memo
        )
    )
    request = LineageCacheKeyRequest(
        graph=graph,
        prepared=prepared,
        target_node_id=target_node_id,
        source=source,
        requested_columns=None if requested_columns is None else tuple(requested_columns),
        initial_column_limit=initial_column_limit,
        row_limit=row_limit,
        port_label=port_label,
        contract_fingerprint=_preview_contract_fingerprint(
            enforce_contracts=enforce_contracts,
            materialisation_scope=materialisation_scope,
        ),
        selected_live_switch_path=selected_live_switch_path(prepared),
        runtime_input_fingerprint=identity.fingerprint(
            {"seed_plan": seed_plan_fingerprint} if seed_plan_fingerprint is not None else None
        ),
        execution_semantics_version=PREVIEW_EXECUTION_SEMANTICS_VERSION,
    )
    return lineage_cache_key(request)


def execute_lazy_graph(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    target_node_id: str | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str = "live",
    enforce_contracts: bool = True,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
    schema_only: bool = False,
    runtime_source_frames_by_node: RuntimeSourceFrames | None = None,
    prepare_inputs: bool = True,
    snapshot_plan: SeedPlan | None = None,
    join_recipes: dict[str, JoinRecipe] | None = None,
    write_recipes: dict[str, WriteRecipe] | None = None,
    unshaped_frames: dict[str, pl.LazyFrame] | None = None,
) -> LazyExecutionResult:
    """Execute a graph lazily: one sink walk of the graph walker.

    Set ``schema_only`` when the caller resolves schemas through
    ``collect_schema()`` and never collects a frame or invokes a sink; see
    ``plan_prepared_execution_strategy`` for what that declaration relaxes.
    Supply ``runtime_source_frames_by_node`` when source nodes are injected
    DataFrames and group-by admission must estimate those request-local inputs.
    A ``snapshot_plan`` (``haute._seed_plans``) makes the run seed from and
    capture into shared snapshots; without one nothing is captured.
    ``join_recipes``, when given, receives the recipe of every edge join the
    run builds, so a caller writing one in full can write it in chunks;
    ``write_recipes``, when given, receives the recipe of every single-input
    node the run builds, so a caller writing one in full can write it in chunks;
    ``unshaped_frames`` receives, for every node that selects or renames its
    columns, its frame before that step.
    """
    from haute._graph_walker import CollectPolicy, walk_graph

    walked = walk_graph(
        graph,
        build_node_fn,
        policy=CollectPolicy.sink(),
        target_node_id=target_node_id,
        preamble_ns=preamble_ns,
        source=source,
        enforce_contracts=enforce_contracts,
        preserve_node_ids=preserve_node_ids or (),
        required_columns_by_node=required_columns_by_node,
        execution_context=execution_context,
        source_by_node=source_by_node,
        schema_only=schema_only,
        runtime_source_frames_by_node=runtime_source_frames_by_node,
        prepare_inputs=prepare_inputs,
        snapshot_plan=snapshot_plan,
    )
    if join_recipes is not None:
        join_recipes.update(walked.join_recipes)
    if write_recipes is not None:
        write_recipes.update(walked.write_recipes)
    if unshaped_frames is not None:
        unshaped_frames.update(walked.unshaped_frames)
    return walked.frames, walked.order, walked.parents_of, walked.id_to_name


def prune_source_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
    *,
    submodels: Mapping[str, Any] | None = None,
) -> list[GraphEdge]:
    """Return graph edges pruned to the active source-switch branch."""
    from haute._execute_lazy import _prune_live_switch_edges

    return _prune_live_switch_edges(
        edges,
        node_map,
        source,
        submodels=submodels,
    )
