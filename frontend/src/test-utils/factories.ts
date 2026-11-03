/**
 * Shared test factories for creating Node and Edge objects.
 *
 * Eliminates duplicate `makeNode` / `makeEdge` helpers across 8+ test files.
 */

import type { Node, Edge } from "@xyflow/react"
import type { SimpleNode, SimpleEdge } from "../panels/editors/_shared"

// ---------------------------------------------------------------------------
// makeNode — React Flow Node
// ---------------------------------------------------------------------------

/**
 * Create a React Flow `Node` with sensible defaults.
 *
 * @param id        – Node ID (required).
 * @param nodeType  – The application node type stored in `data.nodeType`.
 *                    Defaults to `"polars"`.
 * @param overrides – Any additional fields to merge (position, data, etc.).
 */
export function makeNode(
  id: string,
  nodeType = "polars",
  overrides: Partial<Node> = {},
): Node {
  const { data: dataOverrides, ...rest } = overrides
  return {
    id,
    position: { x: 0, y: 0 },
    type: nodeType,
    data: {
      label: `Node ${id}`,
      nodeType,
      config: {},
      ...dataOverrides,
    },
    ...rest,
  } as Node
}

// ---------------------------------------------------------------------------
// makeEdge — React Flow Edge
// ---------------------------------------------------------------------------

/**
 * Create a React Flow `Edge` with sensible defaults.
 *
 * @param id        – Edge ID. If omitted, derived as `"e_{source}_{target}"`.
 * @param source    – Source node ID.
 * @param target    – Target node ID.
 * @param overrides – Any additional fields to merge.
 */
export function makeEdge(
  source: string,
  target: string,
  overrides: Partial<Edge> & { id?: string } = {},
): Edge {
  const { id = `e_${source}_${target}`, ...rest } = overrides
  return { id, source, target, ...rest } as Edge
}

// ---------------------------------------------------------------------------
// makeSimpleNode — Lightweight SimpleNode (used by buildGraph, NodePanel)
// ---------------------------------------------------------------------------

/**
 * Create a `SimpleNode` (the lighter type used in graph-building utils).
 *
 * @param id        – Node ID (required).
 * @param nodeType  – Application node type. Defaults to `"polars"`.
 * @param overrides – Partial overrides including `type`, `config`, or any
 *                    extra `data` fields.
 */
export function makeSimpleNode(
  id: string,
  nodeType = "polars",
  overrides: {
    type?: string
    config?: Record<string, unknown>
    description?: string
    [key: string]: unknown
  } = {},
): SimpleNode {
  const { type, config, description = "", ...extraData } = overrides
  return {
    id,
    type,
    data: {
      label: `Node ${id}`,
      description,
      nodeType,
      config,
      ...extraData,
    },
  }
}

// ---------------------------------------------------------------------------
// makeSimpleEdge — Lightweight SimpleEdge
// ---------------------------------------------------------------------------

/**
 * Create a `SimpleEdge`.
 */
export function makeSimpleEdge(
  id: string,
  source: string,
  target: string,
): SimpleEdge {
  return { id, source, target }
}

// ---------------------------------------------------------------------------
// makeGraph — A simple graph (nodes + edges) for panel/editor tests
// ---------------------------------------------------------------------------

/**
 * Create a minimal graph with nodes and edges.
 *
 * @param nodeCount – Number of nodes to create (default 3).
 * @param linear    – If true, chain nodes with edges (n0→n1→n2...).
 */
export function makeGraph(
  nodeCount = 3,
  linear = true,
): { nodes: Node[]; edges: Edge[] } {
  const nodes = Array.from({ length: nodeCount }, (_, i) =>
    makeNode(`n${i}`, "polars"),
  )
  const edges: Edge[] = []
  if (linear) {
    for (let i = 0; i < nodeCount - 1; i++) {
      edges.push(makeEdge(`n${i}`, `n${i + 1}`))
    }
  }
  return { nodes, edges }
}

// ---------------------------------------------------------------------------
// makeConfig — Modelling config with sensible defaults
// ---------------------------------------------------------------------------

/**
 * Create a modelling config record with sensible defaults.
 *
 * @param overrides – Any config keys to override.
 */
export function makeConfig(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    target: "loss_amount",
    weight: "",
    task: "regression",
    metrics: ["gini", "rmse"],
    evaluation: {
      schema_version: 1,
      strategy: "random",
      seed: 42,
      test: { size: 0.2 },
      validation: { method: "single", size: 0.2 },
    },
    params: { iterations: 1000, learning_rate: 0.05, depth: 6 },
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// makeTrainResult — Training result fixture
// ---------------------------------------------------------------------------

import type { TrainResult } from "../stores/useNodeResultsStore"

/**
 * Create a `TrainResult` with sensible defaults.
 *
 * @param overrides – Partial overrides for any TrainResult field.
 */
export function makeTrainResult(
  overrides: Partial<TrainResult> = {},
): TrainResult {
  return {
    status: "completed",
    job_id: "job-123",
    diagnostic_metrics: { gini: 0.45, rmse: 0.12 },
    final_test_metrics: { gini: 0.44, rmse: 0.13 },
    feature_importance: [
      { feature: "age", importance: 25.3 },
      { feature: "income", importance: 18.7 },
      { feature: "region", importance: 12.1 },
    ],
    model_path: "/tmp/model.cbm",
    development_rows: 8000,
    final_test_rows: 2000,
    diagnostics_set: "final_test",
    features: ["age", "income", "region"],
    cat_features: ["region"],
    error: null,
    final_tree_count: null,
    fit_evidence: null,
    best_iteration: null,
    loss_history: [],
    loss_history_truncated: false,
    validation_loss_history: [],
    validation_loss_history_truncated: false,
    double_lift: [],
    shap_summary: [],
    shap_beeswarm: [],
    shap_curves: [],
    shap_link: null,
    feature_importance_loss: [],
    ave_per_feature: [],
    residuals_histogram: [],
    residuals_stats: {},
    actual_vs_predicted: [],
    lorenz_curve: [],
    lorenz_curve_perfect: [],
    pdp_data: [],
    warning: null,
    total_source_rows: 10000,
    glm_coefficients: [],
    glm_relativities: [],
    glm_fit_statistics: {},
    glm_inference: null,
    glm_smooth_terms: [],
    glm_regularization: null,
    ebm_terms: [],
    tboost_tables: null,
    diagnostics_errors: [],
    feature_selection: null,
    evaluation: {
      schema_version: 1,
      strategy: "random",
      validation_method: "single",
      validation_fit_count: 1,
      fit_count: 2,
      refit_on_development: true,
      development_rows: 8000,
      final_test_rows: 2000,
      selection_fits: [
        {
          schema_version: 1,
          fit_index: 0,
          train_rows: 6000,
          validation_rows: 2000,
          metrics: { gini: 0.45, rmse: 0.12 },
          best_iteration: null,
        },
      ],
      selection_metrics: {
        gini: {
          mean: 0.45,
          stddev: 0,
          min: 0.45,
          max: 0.45,
          fit_count: 1,
          validation_rows: 2000,
        },
        rmse: {
          mean: 0.12,
          stddev: 0,
          min: 0.12,
          max: 0.12,
          fit_count: 1,
          validation_rows: 2000,
        },
      },
      plan_sha256: "a".repeat(64),
      results_sha256: "b".repeat(64),
      plan_path: "outputs/model.evaluation-plan.json",
      results_path: "outputs/model.evaluation-results.json",
      report_path: "outputs/model.evaluation-report.json",
      summary: {
        development_rows: 8000,
        test_rows: 2000,
        validation_fit_count: 1,
        development_group_count: null,
        test_group_count: null,
        development_date_count: null,
        test_date_count: null,
      },
    },
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// makeTuningReport — a tuned model's report: twelve trials, the last the winner
// ---------------------------------------------------------------------------

import type { TuningReport } from "../api/types"

export function makeTuningReport(overrides: Partial<TuningReport> = {}): TuningReport {
  const trials = Array.from({ length: 12 }, (_, trialIndex) => ({
    schema_version: 1 as const,
    trial_index: trialIndex,
    label: trialIndex === 0 ? "baseline" as const : "sampled" as const,
    sampled_params: trialIndex === 0 ? {} : { depth: trialIndex + 4 },
    resolved_params: { iterations: 500, depth: trialIndex + 4 },
    fits: [],
    aggregate_metrics: { gini: 0.4 + trialIndex / 100 },
    objective: 0.4 + trialIndex / 100,
    elapsed_seconds: 1,
  }))

  return {
    schema_version: 1,
    plan_sha256: "c".repeat(64),
    trials_sha256: "d".repeat(64),
    evaluation_plan_sha256: "a".repeat(64),
    metric: "gini",
    direction: "maximize",
    baseline_objective: 0.4,
    winner_trial_index: 11,
    winner_objective: 0.51,
    improvement: 0.11,
    best_sampled_params: { depth: 15 },
    final_params: { iterations: 412, depth: 15 },
    final_tree_count: 412,
    trial_count: 12,
    trial_fit_count: 12,
    total_fit_count: 13,
    trials,
    plan_path: "outputs/model.tuning-plan.json",
    trials_path: "outputs/model.tuning-trials.json",
    report_path: "outputs/model.tuning-report.json",
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// makeTrainEstimate — RAM estimate fixture
// ---------------------------------------------------------------------------

import type {
  FrontierResponse,
  FrontierSelectResponse,
  GitWorkingBranchResponse,
  OptimiserHistoryEntry,
  OptimiserInputSummary,
  OptimiserSolveResult,
  OptimiserStatusResponse,
  TrainEstimate,
  TrainStatusResponse,
} from "../api/types"

/**
 * Create a `TrainEstimate` with sensible defaults.
 *
 * @param overrides – Partial overrides for any TrainEstimate field.
 */
export function makeTrainEstimate(
  overrides: Partial<TrainEstimate> = {},
): TrainEstimate {
  return {
    total_rows: 10000,
    safe_row_limit: null,
    estimated_mb: 256,
    training_mb: 512,
    available_mb: 16384,
    bytes_per_row: 2048,
    was_downsampled: false,
    warning: null,
    gpu_vram_estimated_mb: null,
    gpu_vram_rows: null,
    gpu_vram_available_mb: null,
    gpu_warning: null,
    unavailable: null,
    unbounded_join_node_ids: [],
    evaluation_preview: null,
    ...overrides,
  }
}

/** A complete git readiness payload with the backend model's defaults. */
export function makeGitWorkingBranch(
  overrides: Partial<GitWorkingBranchResponse> = {},
): GitWorkingBranchResponse {
  return {
    working_branch: null,
    storage: "unsupported",
    storage_remote: null,
    storage_forked_from: null,
    sync: null,
    storage_bind: null,
    state: "unset",
    errors: [],
    current_branch: "",
    head_sha: null,
    last_save_sha: null,
    eligible_branches: [],
    identity_set: true,
    user_name: null,
    user_email: null,
    broken_pairs: [],
    ...overrides,
  }
}

/** A complete training status payload with the backend model's defaults. */
export function makeTrainStatus(
  overrides: Partial<TrainStatusResponse> = {},
): TrainStatusResponse {
  return {
    status: "running",
    progress: 0,
    message: "",
    iteration: 0,
    total_iterations: 0,
    train_loss: {},
    train_loss_history: [],
    train_loss_history_truncated: false,
    elapsed_seconds: 0,
    result: null,
    warning: null,
    terminal_reason: null,
    execution_metrics: null,
    feature_selection: null,
    error_code: null,
    http_status_code: null,
    error_detail: null,
    worker_remote_traceback: null,
    phase: null,
    trial_index: null,
    trial_count: null,
    fold_index: null,
    fold_count: null,
    completed_fits: null,
    total_fits: null,
    best_objective: null,
    export_receipts: { mlflow: [], model_files: [] },
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Optimiser payloads: every field the server sends, with the backend models'
// defaults, so a test states only what it is about.
// ---------------------------------------------------------------------------

export function makeInputSummary(overrides: Partial<OptimiserInputSummary> = {}): OptimiserInputSummary {
  return {
    node_id: "optimiser_1",
    data_source: "batch",
    source_file: "main.py",
    graph_fingerprint: "3f9a1c",
    solver_settings: { max_iter: 50, tolerance: 1e-6 },
    ...overrides,
  }
}

export function makeSolveResult(overrides: Partial<OptimiserSolveResult> = {}): OptimiserSolveResult {
  return {
    mode: "online",
    total_objective: 0,
    baseline_objective: 0,
    constraints: {},
    baseline_constraints: {},
    effective_bounds: {},
    lambdas: {},
    converged: true,
    iterations: null,
    n_quotes: null,
    n_steps: null,
    cd_iterations: null,
    factor_tables: {},
    history: null,
    ratebook_cd_trace: null,
    warning: null,
    adjustments: null,
    clamp_rate: null,
    combined_factor_bounds: null,
    frontier: null,
    frontier_error: null,
    selected_frontier_point: null,
    input_summary: makeInputSummary(),
    diagnostics_errors: [],
    scenario_grid: [
      { optimal_step: 0, scenario_value: 0.9 },
      { optimal_step: 1, scenario_value: 1.0 },
      { optimal_step: 2, scenario_value: 1.1 },
    ],
    segment_keys: [],
    ...overrides,
  }
}

export function makeOptimiserStatus(
  overrides: Partial<OptimiserStatusResponse> = {},
): OptimiserStatusResponse {
  return {
    status: "running",
    progress: 0,
    message: "",
    elapsed_seconds: 0,
    result: null,
    frontier: null,
    terminal_reason: null,
    execution_metrics: null,
    ...overrides,
  }
}

export function makeFrontier(overrides: Partial<FrontierResponse> = {}): FrontierResponse {
  return {
    status: "ok",
    points: [],
    point_summaries: [],
    n_points: 0,
    points_returned: 0,
    constraint_names: [],
    swept_axes: [],
    points_limit: null,
    points_truncated: false,
    ...overrides,
  }
}

export function makeHistoryEntry(
  overrides: Partial<OptimiserHistoryEntry> & Pick<OptimiserHistoryEntry, "iteration">,
): OptimiserHistoryEntry {
  return {
    total_objective: 0,
    max_lambda_change: 0,
    all_constraints_satisfied: null,
    lambdas: {},
    total_constraints: {},
    ...overrides,
  }
}

export function makeFrontierSelect(
  overrides: Partial<FrontierSelectResponse> = {},
): FrontierSelectResponse {
  return {
    status: "ok",
    point_index: null,
    total_objective: 0,
    constraints: {},
    baseline_objective: 0,
    baseline_constraints: {},
    effective_bounds: {},
    lambdas: {},
    converged: true,
    iterations: null,
    cd_iterations: null,
    factor_tables: {},
    history: null,
    ratebook_cd_trace: null,
    warning: null,
    adjustments: null,
    clamp_rate: null,
    combined_factor_bounds: null,
    diagnostics_errors: [],
    error: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Node-data payloads: every field the server sends, with the backend models'
// defaults, so a test states only what it is about.
// ---------------------------------------------------------------------------

import type {
  ExploreColumnStat,
  NodeDataPointResponse,
  NodeDataProfile,
  NodeDataStatusResponse,
} from "../api/types"

export function makeNodeDataPoint(
  overrides: Partial<NodeDataPointResponse> = {},
): NodeDataPointResponse {
  return {
    consumer_node_id: "explore",
    point: { producer_node_id: "source", port_label: null },
    slot_key: "slot",
    kind: "node_output",
    state: "missing",
    demand: "all",
    data_version: null,
    row_count: null,
    size_bytes: null,
    retention: null,
    generation: null,
    job: null,
    reads_directly: false,
    build_endpoint: null,
    clear_endpoint: null,
    ...overrides,
  }
}

export function makeNodeDataStatus(
  overrides: Partial<NodeDataStatusResponse> = {},
): NodeDataStatusResponse {
  return {
    status: "running",
    progress: 0,
    message: "",
    terminal_reason: null,
    error: null,
    error_code: null,
    error_detail: null,
    execution_metrics: null,
    generation_id: null,
    outcome: null,
    profile: null,
    ...overrides,
  }
}

export function makeExploreColumnStat(
  overrides: Partial<ExploreColumnStat> = {},
): ExploreColumnStat {
  return {
    name: "column",
    dtype: "String",
    kind: "Text",
    null_count: 0,
    nan_count: null,
    distinct_count: null,
    min_value: null,
    p25_value: null,
    median_value: null,
    mean_value: null,
    p75_value: null,
    max_value: null,
    std_value: null,
    zero_count: null,
    negative_count: null,
    unique_ratio: null,
    is_high_cardinality: false,
    is_identifier_candidate: false,
    text_min_length: null,
    text_mean_length: null,
    text_max_length: null,
    temporal_span: null,
    histogram: null,
    ...overrides,
  }
}

export function makeNodeDataProfile(
  overrides: Partial<NodeDataProfile> = {},
): NodeDataProfile {
  return {
    row_count: 0,
    column_count: 0,
    columns: [],
    overview_summary: {
      data_quality: { issue_count: 0, issues: [], duplicate_row_count: null, duplicate_ratio: null },
      categorical_summary: [],
    },
    data_version: "v1",
    generated_at: 0,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Output and input-cache payloads, complete as the server sends them.
// ---------------------------------------------------------------------------

import type {
  InputCacheSnapshotResponse,
  OutputAssembleDryRunResponse,
  WriteOutputResponse,
} from "../api/types"

export function makeWriteOutput(
  overrides: Partial<WriteOutputResponse> = {},
): WriteOutputResponse {
  return {
    status: "ok",
    message: "",
    row_count: 0,
    path: "",
    format: "parquet",
    execution_metrics: null,
    ...overrides,
  }
}

export function makeOutputDryRun(
  overrides: Partial<OutputAssembleDryRunResponse> = {},
): OutputAssembleDryRunResponse {
  return {
    status: "ok",
    document: [],
    row_count: 0,
    error: null,
    ...overrides,
  }
}

export function makeInputCacheSnapshot(
  overrides: Partial<InputCacheSnapshotResponse> = {},
): InputCacheSnapshotResponse {
  return {
    schema_version: 1,
    identity_digest: "digest",
    state: "missing",
    freshness: "unknown",
    generation: null,
    tables: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// makePreviewResponse — a whole POST /api/pipeline/preview answer
// ---------------------------------------------------------------------------

import type { PreviewNodeResponse } from "../api/types"

/** A preview response as the server sends it, every field present. */
export function makePreviewResponse(
  overrides: Partial<PreviewNodeResponse> = {},
): PreviewNodeResponse {
  return {
    status: "ok",
    node_id: "n1",
    row_count: 0,
    column_count: 0,
    columns: [],
    available_columns: [],
    frame_columns: {},
    preview: [],
    preview_columns: [],
    preview_row_count: 0,
    preview_row_limit: null,
    preview_truncated: false,
    port_previews: {},
    error: null,
    error_line: null,
    timing_ms: 0,
    memory_bytes: 0,
    schema_warnings: [],
    notes: [],
    timings: [],
    memory: [],
    node_statuses: {},
    node_columns: {},
    node_available_columns: {},
    node_frame_columns: {},
    node_schema_warnings: {},
    execution_metrics: null,
    seed_plan: [],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Trace factories — a trace as POST /api/pipeline/trace answers it
// ---------------------------------------------------------------------------

import type { TraceCorrelationDiagnostic, TraceResult, TraceStep } from "../types/trace"

/** A trace step with every field the server sends. */
export function makeTraceStep(overrides: Partial<TraceStep> = {}): TraceStep {
  return {
    node_id: "n1",
    node_name: "Step",
    node_type: "polars",
    schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] },
    input_values: {},
    output_values: {},
    topological_rank: 0,
    column_relevant: true,
    contributed_columns: [],
    derivations: [],
    expression: null,
    calculation: null,
    node_detail: null,
    row_lineage_type: null,
    snapshot_generation_id: null,
    identical_row_count: null,
    ...overrides,
  }
}

/** A trace result with every field the server sends. */
export function makeTraceResult(overrides: Partial<TraceResult> = {}): TraceResult {
  return {
    target_node_id: "n1",
    row_index: 0,
    column: null,
    output_value: null,
    steps: [],
    omissions: [],
    row_id_column: null,
    row_id_value: null,
    total_nodes_in_pipeline: 0,
    nodes_in_trace: 0,
    execution_ms: 0,
    waterfall: null,
    correlation_diagnostics: [],
    generated_at: "2026-01-01T00:00:00+00:00",
    pipeline_source: null,
    execution_origin: "fresh_execution",
    ...overrides,
  }
}

/** A trace correlation diagnostic with every field the server sends. */
export function makeTraceDiagnostic(
  overrides: Partial<TraceCorrelationDiagnostic> = {},
): TraceCorrelationDiagnostic {
  return {
    code: "ambiguous_source_frame",
    severity: "warning",
    reason: "ambiguous_source_frame",
    message: "",
    node_id: null,
    child_node_id: null,
    match_strategy: null,
    match_columns: [],
    ignored_columns: [],
    matched_row_count: null,
    matched_row_indices: [],
    seed_node_ids: [],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// makeSavePipelineResponse — POST /api/pipeline/save's answer
// ---------------------------------------------------------------------------

import type { SavePipelineResponse } from "../api/types"

/** A pipeline save's answer with every field the server sends. */
export function makeSavePipelineResponse(
  overrides: Partial<SavePipelineResponse> = {},
): SavePipelineResponse {
  return {
    status: "saved",
    file: "main.py",
    pipeline_name: "main",
    source_revision: "revision-save",
    document_fingerprint: "fingerprint-save",
    warnings: [],
    git_sha: null,
    identity_required: false,
    uncaptured: [],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Canonical graphs — a pipeline graph as the server sends one
// ---------------------------------------------------------------------------

import type {
  GraphNode as CanonicalGraphNode,
  NodeType as CanonicalNodeType,
  PipelineGraph as CanonicalPipelineGraph,
} from "../generated/api-contracts.generated"

/** A canonical graph node with every field the server sends. */
export function makeCanonicalNode(
  id: string,
  nodeType: CanonicalNodeType = "polars",
  config: Record<string, unknown> = {},
): CanonicalGraphNode {
  return {
    id,
    type: nodeType,
    position: { x: 0, y: 0 },
    data: {
      label: id,
      description: "",
      nodeType,
      config,
      editor: { steps_error: null, steps_discarded: null },
    },
  }
}

/** A canonical pipeline graph with every field the server sends. */
export function makeCanonicalGraph(
  overrides: Partial<CanonicalPipelineGraph> = {},
): CanonicalPipelineGraph {
  return {
    nodes: [],
    edges: [],
    pipeline_name: null,
    pipeline_description: null,
    preamble: null,
    preserved_blocks: [],
    global_constants: [],
    global_constants_error: null,
    source_file: null,
    source_revision: null,
    submodels: null,
    sources: ["live"],
    active_source: "live",
    warning: null,
    ...overrides,
  }
}
