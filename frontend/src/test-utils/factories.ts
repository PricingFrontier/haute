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
    exclude: [],
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
    best_iteration: null,
    loss_history: [],
    loss_history_truncated: false,
    double_lift: [],
    shap_summary: [],
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
    glm_regularization_path: null,
    diagnostics_errors: [],
    feature_selection: null,
    evaluation: {
      schema_version: 1,
      strategy: "random",
      validation_method: "single",
      validation_fit_count: 1,
      fit_count: 2,
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
// makeTrainEstimate — RAM estimate fixture
// ---------------------------------------------------------------------------

import type { TrainEstimate } from "../api/types"

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
    gpu_vram_available_mb: null,
    gpu_warning: null,
    evaluation_preview: null,
    ...overrides,
  }
}
