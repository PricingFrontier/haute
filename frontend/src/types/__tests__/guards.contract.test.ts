import { describe, expect, it } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import { makeTrainResult } from "../../test-utils/factories"
import {
  parsePreviewInputsResponse,
  parseDissolveSubmodelResponse,
  explorePivotMembersFromContract,
  explorePivotRunFromContract,
  explorePivotStatusFromContract,
  frontierAutoRangeStatusFromContract,
  frontierStatusFromContract,
  optimiserStatusFromContract,
  parseInputCacheSnapshotResponse,
  parseJsonCacheSchemaInferenceResponse,
  parseOutputAssembleDryRunResponse,
  parsePreviewNodeResponse,
  parseSavePipelineResponse,
  parseTraceResponse,
  parseSubmodelCreateResponse,
  parseSubmodelGraphResponse,
  parseExecutionStrategyDiagnostic,
  parseNodeDataProfile,
} from "../guards"
import {
  validateExplorePivotMembersResponse,
  validateExplorePivotRunResponse,
  validateExplorePivotStatusResponse,
  validateNodeDataProfileResponse,
} from "../../generated/api-contracts.explore.validators.mjs"
import { validateRatingLevelsResponse } from "../../generated/api-contracts.factors.validators.mjs"
import {
  validateBrowseFilesResponse,
  validateSessionStatusResponse,
} from "../../generated/api-contracts.session.validators.mjs"
import { parseGitMilestoneFork, parseGitPushRejection } from "../../api/client"
import {
  validateOptimiserApplyResponse,
  validateOptimiserEstimateResponse,
  validateOptimiserFrontierAutoRangeStatusResponse,
  validateOptimiserFrontierSelectResponse,
  validateOptimiserFrontierStatusResponse,
  validateOptimiserMlflowLogResponse,
  validateOptimiserSaveResponse,
  validateOptimiserSolveResponse,
  validateOptimiserStatusResponse,
} from "../../generated/api-contracts.optimiser.validators.mjs"
import {
  validateMlflowDestinationsResponse,
  validateMlflowExperimentList,
  validateMlflowModelList,
  validateMlflowModelVersionList,
  validateMlflowRunList,
  validateMlflowSettingsResponse,
  validateMlflowTestConnectionResponse,
} from "../../generated/api-contracts.mlflow.validators.mjs"
import {
  validateModelSaveDestinationResponse,
  validateModellingGpuStatusResponse,
  validateSaveModelResponse,
} from "../../generated/api-contracts.modelling.validators.mjs"
import {
  validateGitArchiveResponse,
  validateGitBindStorageResponse,
  validateGitBranchAwayResponse,
  validateGitCreateWorkingBranchResponse,
  validateGitDeleteBranchResponse,
  validateGitFastForwardResponse,
  validateGitForkStorageResponse,
  validateGitGraphResponse,
  validateGitMoveResponse,
  validateGitPrefs,
  validateGitPushResponse,
  validateGitRemotesResponse,
  validateGitWorkingBranchResponse,
} from "../../generated/api-contracts.git.validators.mjs"
import { makeGitWorkingBranch } from "../../test-utils/factories"
import { expectGeneratedContract } from "../generatedContractValidation"
import {
  parseTrainEstimateResponse,
  parseTrainFeatureSelection,
  parseTrainResponse,
  parseTrainStatusResponse,
} from "../trainGuards"

function executionStrategyFixture(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    status: "projected",
    strategy: "projected",
    profile: "preview_eager",
    boundedness: "bounded",
    reason_code: "projection_seed",
    detail_state: "available",
    boundaries: { state: "available", total_count: 0, items: [] },
    reasons: { state: "available", total_count: 0, items: [] },
    provenance: { state: "available", total_count: 0, items: [] },
    ...overrides,
  }
}

function featureSelectionFixture(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    mode: "explicit",
    feature_count: 2,
    detail_state: "available",
    features: { state: "available", total_count: 2, items: ["age", "premium"] },
    retained_metadata: { state: "available", total_count: 1, items: [{ column: "policy_id", reason: "identifier" }] },
    excluded_columns: { state: "available", total_count: 1, items: [{ column: "claim", reason: "target" }] },
    ...overrides,
  }
}

function tunedTrainResponseFixture() {
  const response = loadUiContractFixture<Record<string, unknown>>("train_response")
  const objectives = [0.5, 0.4, 0.45, 0.6, 0.55]
  const trials = objectives.map((objective, index) => ({
    schema_version: 1,
    trial_index: index,
    label: index === 0 ? "baseline" : "sampled",
    sampled_params: index === 0 ? {} : { depth: index + 3 },
    resolved_params: { depth: index + 3 },
    fits: [{
      schema_version: 1,
      fit_index: 0,
      train_rows: 80,
      validation_rows: 20,
      metrics: { rmse: objective },
      best_iteration: 6,
    }],
    aggregate_metrics: { rmse: objective },
    objective,
    elapsed_seconds: index + 0.25,
  }))
  return {
    ...response,
    evaluation: {
      schema_version: 1,
      strategy: "random",
      validation_method: "single",
      validation_fit_count: 1,
      fit_count: 6,
      refit_on_development: true,
      development_rows: 100,
      final_test_rows: 10,
      selection_fits: [{
        schema_version: 1,
        fit_index: 0,
        train_rows: 80,
        validation_rows: 20,
        metrics: { rmse: 0.5 },
        best_iteration: 6,
      }],
      selection_metrics: {
        rmse: {
          mean: 0.5,
          stddev: 0,
          min: 0.5,
          max: 0.5,
          fit_count: 1,
          validation_rows: 20,
        },
      },
      plan_sha256: "a".repeat(64),
      results_sha256: "b".repeat(64),
      plan_path: "outputs/evaluation-plan.json",
      results_path: "outputs/evaluation-results.json",
      report_path: "outputs/evaluation-report.json",
      summary: {
        development_rows: 100,
        test_rows: 10,
        validation_fit_count: 1,
        development_group_count: null,
        test_group_count: null,
        development_date_count: null,
        test_date_count: null,
      },
    },
    tuning: {
      schema_version: 1,
      plan_sha256: "c".repeat(64),
      trials_sha256: "d".repeat(64),
      evaluation_plan_sha256: "a".repeat(64),
      metric: "rmse",
      direction: "minimize",
      baseline_objective: 0.5,
      winner_trial_index: 1,
      winner_objective: 0.4,
      improvement: 0.1,
      best_sampled_params: { depth: 4 },
      final_params: { depth: 4, iterations: 7 },
      final_tree_count: 7,
      trial_count: 5,
      trial_fit_count: 5,
      total_fit_count: 6,
      trials,
      plan_path: "outputs/tuning-plan.json",
      trials_path: "outputs/tuning-trials.json",
      report_path: "outputs/tuning-report.json",
    },
  }
}

function executionMetricsFixture() {
  return {
    schema_version: 1,
    operation: "pipeline_preview",
    profile: "preview_eager",
    job_id: "job-1",
    status: "completed",
    terminal_reason: null,
    stage_count: 1,
    retained_stage_count: 1,
    truncated_stage_count: 0,
    stages_truncated: false,
    total_elapsed_ms: 12.5,
    node_elapsed_ms: { score: 12.5 },
    stage_elapsed_ms: { collect: 12.5 },
    rss_start_bytes: 1000,
    rss_end_bytes: 1800,
    rss_delta_bytes: 800,
    rss_peak_bytes: 1800,
    max_rss_bytes: 1800,
    n_collects: 1,
    n_checkpoints: 2,
    memory_pressure_event_count: 1,
    retained_memory_pressure_event_count: 1,
    truncated_memory_pressure_event_count: 0,
    memory_pressure_events_truncated: false,
    memory_limit_bytes: 2000,
    memory_baseline_bytes: 1000,
    rss_limit_bytes: 3000,
    admission: {
      admitted: true,
      operation: "pipeline_preview",
      profile: "preview_eager",
      memory_limit_bytes: 2000,
      rss_at_admission_bytes: 1000,
      rss_limit_bytes: 3000,
      process_rss_limit_bytes: null,
      headroom_bytes: 2000,
      config_key: "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
      budget_policy: "adaptive_local",
      available_ram_bytes: 8000,
      os_reserve_bytes: 2000,
      reason: "within_memory_budget",
    },
    stages: [
      {
        schema_version: 1,
        name: "collect",
        operation: "pipeline_preview",
        profile: "preview_eager",
        elapsed_ms: 12.5,
        node_id: "score",
        job_id: "job-1",
        rss_start_bytes: 1000,
        rss_end_bytes: 1800,
        rss_delta_bytes: 800,
        rss_peak_bytes: 1800,
        rows_in: 10,
        rows_out: 2,
        bytes_read: 512,
        bytes_written: 128,
        columns_scanned: 4,
        n_collects: 1,
        n_checkpoints: 2,
      },
    ],
    memory_pressure_events: [
      {
        schema_version: 1,
        event: "memory_pressure",
        operation: "pipeline_preview",
        profile: "preview_eager",
        job_id: "job-1",
        node_id: "score",
        stage: "collect",
        label: "after_collect",
        threshold_ratio: 0.75,
        threshold_percent: 75,
        rss_bytes: 1750,
        rss_limit_bytes: 3000,
        headroom_bytes: 2000,
        headroom_used_bytes: 1500,
        rss_peak_bytes: 1800,
        memory_limit_bytes: 2000,
        memory_baseline_bytes: 1000,
        baseline_rss_bytes: 1000,
        budget_policy: "adaptive_local",
        config_key: "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
        available_ram_bytes: 8000,
        os_reserve_bytes: 2000,
        pressure_ratio: 0.75,
      },
    ],
    input_preparation: [
      {
        node_id: "policies",
        identity_digest: "digest-1",
        action: "reused",
        build_class: "in_memory",
        execution: "in_process",
        memory_limit_bytes: 2000,
        elapsed_seconds: 0.25,
        row_count: 10,
        size_bytes: 512,
        generation_id: "gen-1",
        warning_code: null,
      },
    ],
    cache_proof: {
      hits: 1,
      misses: 2,
      direct_fallbacks: 1,
      miss_reason_counts: {
        metadata_source_mismatch: 1,
        artifact_integrity_schema_failure: 0,
        unreadable_artifact: 0,
        proof_unavailable: 1,
      },
    },
  }
}

// Explore and factor responses are checked by their generated validators
// (API-R03); the pivot responses then get the UI's member-key and matrix checks.
const parseExplorePivotRunResponse = (value: unknown) =>
  explorePivotRunFromContract(
    expectGeneratedContract("ExplorePivotRunResponse", validateExplorePivotRunResponse, value),
  )
const parseExplorePivotStatusResponse = (value: unknown) =>
  explorePivotStatusFromContract(
    expectGeneratedContract("ExplorePivotStatusResponse", validateExplorePivotStatusResponse, value),
  )
const parseExplorePivotMembersResponse = (value: unknown) =>
  explorePivotMembersFromContract(
    expectGeneratedContract("ExplorePivotMembersResponse", validateExplorePivotMembersResponse, value),
  )
const parseNodeDataProfileResponse = (value: unknown) =>
  expectGeneratedContract("NodeDataProfileResponse", validateNodeDataProfileResponse, value)
const parseRatingLevelsResponse = (value: unknown) =>
  expectGeneratedContract("RatingLevelsResponse", validateRatingLevelsResponse, value)

// Optimiser responses are checked by their generated validators (API-R03);
// the UI then keeps one summary per frontier point and parses execution
// metrics with the shared parser. A frontier and an auto-range result reach
// the browser inside their job status, so they are checked through it.
const parseOptimiserStatusResponse = (value: unknown) =>
  optimiserStatusFromContract(
    expectGeneratedContract("OptimiserStatusResponse", validateOptimiserStatusResponse, value),
  )
const parseSolveOptimiserResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserSolveResponse", validateOptimiserSolveResponse, value)
const parseOptimiserEstimateResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserEstimateResponse", validateOptimiserEstimateResponse, value)
const parseApplyOptimiserResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserApplyResponse", validateOptimiserApplyResponse, value)
const parseFrontierSelectResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserFrontierSelectResponse", validateOptimiserFrontierSelectResponse, value)
const parseSaveOptimiserResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserSaveResponse", validateOptimiserSaveResponse, value)
const parseMlflowLogResponse = (value: unknown) =>
  expectGeneratedContract("OptimiserMlflowLogResponse", validateOptimiserMlflowLogResponse, value)
const parseFrontierAutoRangeStatusResponse = (value: unknown) =>
  frontierAutoRangeStatusFromContract(
    expectGeneratedContract(
      "OptimiserFrontierAutoRangeStatusResponse",
      validateOptimiserFrontierAutoRangeStatusResponse,
      value,
    ),
  )
const completedJob = (result: unknown) => ({
  status: "completed",
  progress: 1,
  message: "",
  elapsed_seconds: 0,
  result,
  terminal_reason: null,
  error_code: null,
  http_status_code: null,
  error_detail: null,
  execution_metrics: null,
})
const parseFrontierResponse = (value: unknown) =>
  frontierStatusFromContract(
    expectGeneratedContract(
      "OptimiserFrontierStatusResponse",
      validateOptimiserFrontierStatusResponse,
      completedJob(value),
    ),
  ).result!
const parseFrontierAutoRangeResponse = (value: unknown) =>
  parseFrontierAutoRangeStatusResponse(completedJob(value)).result!
// A complete execution-metrics payload, as the server sends it.
const completeExecutionMetrics = () =>
  loadUiContractFixture<Record<string, unknown>>("train_status_metrics_response").execution_metrics

const sessionStatus = (value: unknown) =>
  expectGeneratedContract("SessionStatusResponse", validateSessionStatusResponse, value)
const browseFiles = (value: unknown) =>
  expectGeneratedContract("BrowseFilesResponse", validateBrowseFilesResponse, value)

// MLflow responses are checked by their generated validators (API-R03).
const mlflowDestinations = (value: unknown) =>
  expectGeneratedContract("MlflowDestinationsResponse", validateMlflowDestinationsResponse, value)
const mlflowSettings = (value: unknown) =>
  expectGeneratedContract("MlflowSettingsResponse", validateMlflowSettingsResponse, value)
const mlflowTestConnection = (value: unknown) =>
  expectGeneratedContract("MlflowTestConnectionResponse", validateMlflowTestConnectionResponse, value)
const mlflowExperiments = (value: unknown) =>
  expectGeneratedContract("MlflowExperimentList", validateMlflowExperimentList, value)
const mlflowRuns = (value: unknown) =>
  expectGeneratedContract("MlflowRunList", validateMlflowRunList, value)
const mlflowModels = (value: unknown) =>
  expectGeneratedContract("MlflowModelList", validateMlflowModelList, value)
const modellingGpuStatus = (value: unknown) =>
  expectGeneratedContract("ModellingGpuStatusResponse", validateModellingGpuStatusResponse, value)
const modelSaveDestination = (value: unknown) =>
  expectGeneratedContract("ModelSaveDestinationResponse", validateModelSaveDestinationResponse, value)
const savedModel = (value: unknown) =>
  expectGeneratedContract("SaveModelResponse", validateSaveModelResponse, value)
const gitWorkingBranch = (value: unknown) =>
  expectGeneratedContract("GitWorkingBranchResponse", validateGitWorkingBranchResponse, value)
const gitBindStorage = (value: unknown) =>
  expectGeneratedContract("GitBindStorageResponse", validateGitBindStorageResponse, value)
const gitForkStorage = (value: unknown) =>
  expectGeneratedContract("GitForkStorageResponse", validateGitForkStorageResponse, value)
const gitMove = (value: unknown) =>
  expectGeneratedContract("GitMoveResponse", validateGitMoveResponse, value)
const gitRemotes = (value: unknown) =>
  expectGeneratedContract("GitRemotesResponse", validateGitRemotesResponse, value)
const gitFastForward = (value: unknown) =>
  expectGeneratedContract("GitFastForwardResponse", validateGitFastForwardResponse, value)
const gitPush = (value: unknown) =>
  expectGeneratedContract("GitPushResponse", validateGitPushResponse, value)
const gitBranchAway = (value: unknown) =>
  expectGeneratedContract("GitBranchAwayResponse", validateGitBranchAwayResponse, value)
const gitCreateWorkingBranch = (value: unknown) =>
  expectGeneratedContract("GitCreateWorkingBranchResponse", validateGitCreateWorkingBranchResponse, value)
const gitPrefs = (value: unknown) =>
  expectGeneratedContract("GitPrefs", validateGitPrefs, value)
const gitGraph = (value: unknown) =>
  expectGeneratedContract("GitGraphResponse", validateGitGraphResponse, value)
const gitArchive = (value: unknown) =>
  expectGeneratedContract("GitArchiveResponse", validateGitArchiveResponse, value)
const gitDeleteBranch = (value: unknown) =>
  expectGeneratedContract("GitDeleteBranchResponse", validateGitDeleteBranchResponse, value)
const mlflowModelVersions = (value: unknown) =>
  expectGeneratedContract("MlflowModelVersionList", validateMlflowModelVersionList, value)

describe("parseExecutionStrategyDiagnostic", () => {
  it.each([
    ["projected", "projected"],
    ["schema-all-except", "projected"],
    ["full-width-admitted-eager", "admitted_eager"],
    ["unprojected-streaming-boundary", "boundary"],
    ["materialisation-boundary", "boundary"],
    ["full-width-conservative", "warned"],
    ["unsupported", "rejected"],
    ["not-planned", "not_planned"],
  ])("accepts the V1 %s strategy mapping", (strategy, status) => {
    expect(parseExecutionStrategyDiagnostic(executionStrategyFixture({ strategy, status }))?.status).toBe(status)
  })

  it("throws for malformed fields, caps, wrappers, and ordering in a matching version", () => {
    expect(() => parseExecutionStrategyDiagnostic(
      executionStrategyFixture({ reason_code: 3 }),
    )).toThrow(/reason_code/i)
    expect(() => parseExecutionStrategyDiagnostic(executionStrategyFixture({
      reasons: { state: "available", total_count: 33, items: Array.from({ length: 33 }, () => ({ reason_code: "r" })) },
    }))).toThrow()
    expect(() => parseExecutionStrategyDiagnostic(executionStrategyFixture({
      boundaries: {
        state: "available",
        total_count: 2,
        items: [
          { topological_rank: 1, node_id: "b", operator: "x", boundary_kind: "materialisation-boundary" },
          { topological_rank: 0, node_id: "a", operator: "x", boundary_kind: "materialisation-boundary" },
        ],
      },
    }))).toThrow()
  })

  it("accepts V1 additive fields and equal-primary duplicates but rejects higher versions", () => {
    const diagnostic = parseExecutionStrategyDiagnostic(executionStrategyFixture({
      unknown_additive_field: true,
      reasons: {
        state: "available",
        total_count: 2,
        future_collection_field: { nested: true },
        items: [
          { reason_code: "same", future_item_field: [1] },
          { reason_code: "same" },
        ],
      },
    }))
    expect(diagnostic?.reasons.items).toHaveLength(2)
    expect(parseExecutionStrategyDiagnostic(executionStrategyFixture({ schema_version: 2 }))).toBeNull()
  })

  it("detaches retained arrays from the untrusted input", () => {
    const assumptions = ["bounded input"]
    const raw = executionStrategyFixture({ assumptions })
    const diagnostic = parseExecutionStrategyDiagnostic(raw)

    assumptions[0] = "mutated after parsing"

    expect(diagnostic?.assumptions).toEqual(["bounded input"])
  })

  it("rejects browser-unsafe integers through the generated V1 validator", () => {
    expect(() => parseExecutionStrategyDiagnostic(executionStrategyFixture({
      boundaries: {
        state: "available",
        total_count: 1,
        items: [{
          topological_rank: Number.MAX_SAFE_INTEGER + 1,
          node_id: "boundary",
          operator: "collect",
          boundary_kind: "materialisation-boundary",
        }],
      },
    }))).toThrow(/topological_rank/i)
  })

  it("orders absent reason ranks after every browser-safe rank", () => {
    const diagnostic = parseExecutionStrategyDiagnostic(executionStrategyFixture({
      reasons: {
        state: "available",
        total_count: 2,
        items: [
          {
            reason_code: "ranked",
            topological_rank: Number.MAX_SAFE_INTEGER,
            node_id: "z",
          },
          {
            reason_code: "unranked",
            topological_rank: null,
            node_id: "a",
          },
        ],
      },
    }))

    expect(diagnostic?.reasons.items.map(({ reason_code }) => reason_code)).toEqual([
      "ranked",
      "unranked",
    ])
  })

  it.each([
    { estimated_peak_bytes: 10, raw_estimated_peak_bytes: 10 },
    { estimated_peak_bytes: 10, raw_estimated_peak_bytes: 10, estimate_calibration_factor_basis_points: 9_999, estimate_admission_basis: "provided" },
    { estimated_peak_bytes: 81, raw_estimated_peak_bytes: 10, estimate_calibration_factor_basis_points: 80_001, estimate_admission_basis: "provided" },
    { estimated_peak_bytes: 11, raw_estimated_peak_bytes: 10, estimate_calibration_factor_basis_points: 10_000, estimate_admission_basis: "provided" },
  ])("rejects incomplete, reducing, over-cap, or inexact calibration evidence", (calibration) => {
    expect(() => parseExecutionStrategyDiagnostic(executionStrategyFixture(calibration))).toThrow(/calibrat/i)
  })
})

describe("parseTrainFeatureSelection", () => {
  it.each(["explicit", "all_except"] as const)("accepts %s selections and preserves server order", (mode) => {
    const parsed = parseTrainFeatureSelection(featureSelectionFixture({
      mode,
      features: { state: "available", total_count: 2, items: ["premium", "age"] },
    }))

    expect(parsed.features.items).toEqual(["premium", "age"])
  })

  it("accepts truncated wrappers when detail state is truncated", () => {
    const parsed = parseTrainFeatureSelection(featureSelectionFixture({
      detail_state: "truncated",
      excluded_columns: { state: "truncated", total_count: 3, items: [{ column: "claim", reason: "target" }] },
    }))

    expect(parsed.excluded_columns.total_count).toBe(3)
  })

  it("uses the canonical evaluation reason for strategy metadata", () => {
    const parsed = parseTrainFeatureSelection(featureSelectionFixture({
      retained_metadata: {
        state: "available",
        total_count: 1,
        items: [{ column: "policy_date", reason: "evaluation" }],
      },
    }))
    expect(parsed.retained_metadata.items[0]?.reason).toBe("evaluation")

    expect(() => parseTrainFeatureSelection(featureSelectionFixture({
      retained_metadata: {
        state: "available",
        total_count: 1,
        items: [{ column: "policy_date", reason: "split" }],
      },
    }))).toThrow(/reason/i)
  })

  it("rejects malformed schema, count, and exclusion reasons", () => {
    expect(() => parseTrainFeatureSelection(featureSelectionFixture({ schema_version: 2 }))).toThrow(/schema_version/i)
    expect(() => parseTrainFeatureSelection(featureSelectionFixture({ features: { state: "available", total_count: 3, items: ["age"] } }))).toThrow(/count/i)
    expect(() => parseTrainFeatureSelection(featureSelectionFixture({ excluded_columns: { state: "available", total_count: 1, items: [{ column: "claim", reason: "unknown" }] } }))).toThrow(/reason/i)
  })

  it("attaches the nullable selection to train and train-status responses", () => {
    const featureSelection = featureSelectionFixture()
    expect(parseTrainResponse({
      ...loadUiContractFixture<Record<string, unknown>>("train_response"),
      feature_selection: featureSelection,
    }).feature_selection?.mode).toBe("explicit")
    expect(parseTrainStatusResponse({
      ...loadUiContractFixture<Record<string, unknown>>("train_status_response"),
      feature_selection: featureSelection,
    }).feature_selection?.feature_count).toBe(2)
  })
})

describe("API response guards", () => {
  it("parses savePipeline responses with warnings", () => {
    const parsed = parseSavePipelineResponse(loadUiContractFixture("save_pipeline"))

    expect(parsed.warnings).toEqual(["renamed duplicate node"])
    expect(parsed.status).toBe("saved")
    expect(parsed.source_revision).toBe("revision-save-1")
  })

  it("rejects savePipeline responses without a committed revision", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("save_pipeline")
    expect(() => parseSavePipelineResponse({
      ...fixture,
      source_revision: undefined,
    })).toThrow(/source_revision/i)
  })

  it("rejects blank committed revisions and submodel paths", () => {
    const save = loadUiContractFixture<Record<string, unknown>>("save_pipeline")
    const create = loadUiContractFixture<Record<string, unknown>>("submodel_create_response")
    const loaded = loadUiContractFixture<Record<string, unknown>>("submodel_graph_response")
    const dissolve = loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response")

    expect(() => parseSavePipelineResponse({ ...save, source_revision: "   " })).toThrow(/source_revision/i)
    expect(() => parseSubmodelCreateResponse({ ...create, source_revision: "" })).toThrow(/source_revision/i)
    expect(() => parseSubmodelCreateResponse({ ...create, parent_file: " " })).toThrow(/parent_file/i)
    expect(() => parseSubmodelGraphResponse({ ...loaded, submodel_file: "" })).toThrow(/submodel_file/i)
    expect(() => parseDissolveSubmodelResponse({ ...dissolve, source_revision: "\t" })).toThrow(/source_revision/i)
  })

  it("fills preview defaults for sparse error payloads", () => {
    const parsed = parsePreviewNodeResponse({
      status: "error",
      node_id: "bad_node",
      error: "contract mismatch",
    })

    expect(parsed.timings).toEqual([])
    expect(parsed.memory).toEqual([])
    expect(parsed.node_statuses).toEqual({})
    expect(parsed.error).toBe("contract mismatch")
  })

  it("parses valid per-node statuses into the closed union", () => {
    const parsed = parsePreviewNodeResponse({
      status: "ok",
      node_id: "n1",
      node_statuses: { n1: "ok", n2: "error" },
    })

    expect(parsed.node_statuses).toEqual({ n1: "ok", n2: "error" })
  })

  it("rejects an unknown per-node status value (fails loud, no silent widening)", () => {
    expect(() =>
      parsePreviewNodeResponse({
        status: "ok",
        node_id: "n1",
        node_statuses: { n1: "pending" },
      }),
    ).toThrow(/node_statuses\.n1/i)
  })

  it("rejects client-only running status in backend preview payloads", () => {
    expect(() =>
      parsePreviewNodeResponse({
        status: "ok",
        node_id: "n1",
        node_statuses: { n1: "running" },
      }),
    ).toThrow(/node_statuses\.n1/i)
  })

  it("rejects unknown top-level preview status values", () => {
    expect(() =>
      parsePreviewNodeResponse({
        status: "running",
        node_id: "n1",
      }),
    ).toThrow(/status/i)
  })

  it("parses the generations a preview was computed from", () => {
    const parsed = parsePreviewNodeResponse(loadUiContractFixture("preview_node"))

    expect(parsed.seed_plan).toEqual([
      expect.objectContaining({
        node_id: "join",
        port_label: null,
        node_label: "Join",
        columns: ["policy_id", "premium", "region"],
        kind: "seeded",
      }),
      expect.objectContaining({ node_id: "rates", columns: null, kind: "captured" }),
    ])
  })

  it("defaults the seed plan to empty and rejects malformed entries", () => {
    const { seed_plan: _omitted, ...withoutPlan } = loadUiContractFixture<Record<string, unknown>>(
      "preview_node",
    )
    expect(parsePreviewNodeResponse(withoutPlan).seed_plan).toEqual([])

    const fixture = loadUiContractFixture<{ seed_plan: Record<string, unknown>[] }>("preview_node")
    const entry = fixture.seed_plan[0]
    expect(() =>
      parsePreviewNodeResponse({ ...fixture, seed_plan: [{ ...entry, kind: "borrowed" }] }),
    ).toThrow(/kind/)
    expect(() =>
      parsePreviewNodeResponse({ ...fixture, seed_plan: [{ ...entry, port_label: "drivers" }] }),
    ).toThrow(/port_label/)
  })

  it.each([
    ["an empty string", ""],
    ["a whitespace-only string", "   "],
    ["an omitted field", undefined],
    ["a non-string value", 123],
  ])(
    "rejects a seed plan entry with %s generation_id",
    (_label, invalidValue) => {
      const fixture = loadUiContractFixture<{ seed_plan: Record<string, unknown>[] }>("preview_node")
      const { generation_id: _omitted, ...entryWithoutGenId } = fixture.seed_plan[0]
      const entry =
        invalidValue === undefined
          ? entryWithoutGenId
          : { ...entryWithoutGenId, generation_id: invalidValue }
      expect(() =>
        parsePreviewNodeResponse({ ...fixture, seed_plan: [entry] }),
      ).toThrow(/generation_id/)
    },
  )

  it("parses the inputs a preview would read", () => {
    expect(parsePreviewInputsResponse({ input_node_ids: ["policies", "quotes"] })).toEqual({
      input_node_ids: ["policies", "quotes"],
    })
    expect(() => parsePreviewInputsResponse({ input_node_ids: [1] })).toThrow(/input_node_ids/)
  })

  it("parses preview truncation metadata", () => {
    const parsed = parsePreviewNodeResponse(loadUiContractFixture("preview_node"))

    expect(parsed.preview_row_count).toBe(2)
    expect(parsed.preview_row_limit).toBe(10_000)
    expect(parsed.preview_truncated).toBe(false)
    expect(parsed.preview_columns).toEqual(["premium", "segment"])
  })

  it("preserves typed execution metrics on preview responses", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: executionMetricsFixture(),
    })

    expect(parsed.execution_metrics?.admission?.budget_policy).toBe("adaptive_local")
    expect(parsed.execution_metrics?.admission?.available_ram_bytes).toBe(8000)
    expect(parsed.execution_metrics?.memory_pressure_events[0]?.pressure_ratio).toBe(0.75)
    expect(parsed.execution_metrics?.memory_pressure_events[0]?.budget_policy).toBe("adaptive_local")
    expect(parsed.execution_metrics?.input_preparation).toHaveLength(1)
    expect(parsed.execution_metrics?.input_preparation[0]?.node_id).toBe("policies")
    expect(parsed.execution_metrics?.input_preparation[0]?.action).toBe("reused")
    expect(parsed.execution_metrics?.input_preparation[0]?.execution).toBe("in_process")
    expect(parsed.execution_metrics?.input_preparation[0]?.elapsed_seconds).toBe(0.25)
    expect(parsed.execution_metrics?.input_preparation[0]?.warning_code).toBeNull()
  })

  it("defaults input preparation records to an empty list when omitted", () => {
    const { input_preparation: _omitted, ...metrics } = executionMetricsFixture()
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: metrics,
    })

    expect(parsed.execution_metrics?.input_preparation).toEqual([])
  })

  it("rejects an input preparation record with an unknown action literal", () => {
    const metrics = executionMetricsFixture()

    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...metrics,
          input_preparation: [{ ...metrics.input_preparation[0], action: "recycled" }],
        },
      }),
    ).toThrow(/action/i)
  })

  it("preserves shared snapshot seeds, captures, and warnings", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_seeds: [
          { node_id: "join", identity_digest: "d1", generation_id: "g1", columns: "all" },
        ],
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "chunked_join",
            write_parts: 20,
            write_chunk_rows: 500,
            write_staged_inputs: 0,
            write_input_slices: 4,
            write_native_reason: "unsupported_frame_method",
            write_blocking_operator: "head",
          },
        ],
        shared_snapshot_capture_skips: [
          { node_id: "select_1", reason: "cheap_segment" },
        ],
        warnings: [{ code: "snapshot_capture_superseded", node_id: "banding", reason: null }],
      },
    })

    expect(parsed.execution_metrics?.shared_snapshot_seeds).toEqual([
      { node_id: "join", identity_digest: "d1", generation_id: "g1", columns: "all" },
    ])
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.outcome).toBe("superseded")
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.columns).toEqual(["premium", "region"])
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_strategy).toBe("chunked_join")
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_parts).toBe(20)
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_chunk_rows).toBe(500)
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_staged_inputs).toBe(0)
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_input_slices).toBe(4)
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_native_reason).toBe("unsupported_frame_method")
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_blocking_operator).toBe("head")
    expect(parsed.execution_metrics?.shared_snapshot_capture_skips).toEqual([
      { node_id: "select_1", reason: "cheap_segment" },
    ])
    expect(parsed.execution_metrics?.warnings).toEqual([
      { code: "snapshot_capture_superseded", node_id: "banding", reason: null },
    ])
  })

  it("accepts null write_chunk_rows in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "sliced",
            write_parts: 20,
            write_chunk_rows: null,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_chunk_rows).toBeNull()
  })

  it("rejects a negative write_parts in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "sliced",
              write_parts: -1,
            },
          ],
        },
      }),
    ).toThrow(/write_parts/i)
  })

  it("rejects a negative write_chunk_rows in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "sliced",
              write_chunk_rows: -1,
            },
          ],
        },
      }),
    ).toThrow(/write_chunk_rows/i)
  })

  it("rejects a non-numeric write_chunk_rows in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "sliced",
              write_chunk_rows: "twenty",
            },
          ],
        },
      }),
    ).toThrow(/write_chunk_rows/i)
  })

  it("rejects a zero write_parts in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "sliced",
              write_parts: 0,
            },
          ],
        },
      }),
    ).toThrow(/write_parts/i)
  })

  it("rejects a zero write_chunk_rows in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "sliced",
              write_chunk_rows: 0,
            },
          ],
        },
      }),
    ).toThrow(/write_chunk_rows/i)
  })

  it("accepts a count of 1 for write_parts and write_chunk_rows in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "sliced",
            write_parts: 1,
            write_chunk_rows: 1,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_parts).toBe(1)
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_chunk_rows).toBe(1)
  })

  it("accepts zero write_staged_inputs in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "sliced",
            write_staged_inputs: 0,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_staged_inputs).toBe(0)
  })

  it("rejects an unknown write_strategy in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "unknown_strategy",
            },
          ],
        },
      }),
    ).toThrow(/write_strategy/i)
  })

  it("accepts input_sliced as write_strategy in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "input_sliced",
            write_input_slices: 3,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_strategy).toBe("input_sliced")
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_input_slices).toBe(3)
  })

  it("accepts null write_input_slices, write_native_reason, and write_blocking_operator in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "native",
            write_input_slices: null,
            write_native_reason: null,
            write_blocking_operator: null,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_input_slices).toBeNull()
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_native_reason).toBeNull()
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_blocking_operator).toBeNull()
  })

  it("rejects a negative write_input_slices in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "input_sliced",
              write_input_slices: -1,
            },
          ],
        },
      }),
    ).toThrow(/write_input_slices/i)
  })

  it("rejects a zero write_input_slices in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "input_sliced",
              write_input_slices: 0,
            },
          ],
        },
      }),
    ).toThrow(/write_input_slices/i)
  })

  it("rejects a non-numeric write_input_slices in shared snapshot captures", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome: "superseded",
              generation_id: null,
              columns: ["premium", "region"],
              write_strategy: "input_sliced",
              write_input_slices: "two",
            },
          ],
        },
      }),
    ).toThrow(/write_input_slices/i)
  })

  it("accepts a count of 1 for write_input_slices in shared snapshot captures", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        shared_snapshot_captures: [
          {
            node_id: "banding",
            identity_digest: "d2",
            kind: "consumed",
            outcome: "superseded",
            generation_id: null,
            columns: ["premium", "region"],
            write_strategy: "input_sliced",
            write_input_slices: 1,
          },
        ],
      },
    })
    expect(parsed.execution_metrics?.shared_snapshot_captures[0]?.write_input_slices).toBe(1)
  })

  it("preserves training write metrics evidence", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        training_write_strategy: "input_sliced",
        training_write_input_slices: 3,
        training_write_native_reason: "unsupported_frame_method",
        training_write_blocking_operator: "head",
      },
    })
    expect(parsed.execution_metrics?.training_write_strategy).toBe("input_sliced")
    expect(parsed.execution_metrics?.training_write_input_slices).toBe(3)
    expect(parsed.execution_metrics?.training_write_native_reason).toBe("unsupported_frame_method")
    expect(parsed.execution_metrics?.training_write_blocking_operator).toBe("head")
  })

  it("preserves a Data Output's write metrics evidence", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        data_output_write_strategy: "sliced",
        data_output_write_input_slices: 4,
        data_output_write_native_reason: null,
      },
    })
    expect(parsed.execution_metrics?.data_output_write_strategy).toBe("sliced")
    expect(parsed.execution_metrics?.data_output_write_input_slices).toBe(4)
    expect(parsed.execution_metrics?.data_output_write_native_reason).toBeNull()
  })

  it("rejects a non-positive Data Output slice count", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          data_output_write_input_slices: 0,
        },
      }),
    ).toThrow()
  })

  it("accepts null training write fields in execution metrics", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        training_write_strategy: null,
        training_write_input_slices: null,
        training_write_native_reason: null,
        training_write_blocking_operator: null,
      },
    })
    expect(parsed.execution_metrics?.training_write_strategy).toBeNull()
    expect(parsed.execution_metrics?.training_write_input_slices).toBeNull()
    expect(parsed.execution_metrics?.training_write_native_reason).toBeNull()
    expect(parsed.execution_metrics?.training_write_blocking_operator).toBeNull()
  })

  it("rejects a negative training_write_input_slices in execution metrics", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          training_write_input_slices: -1,
        },
      }),
    ).toThrow(/training_write_input_slices/i)
  })

  it("rejects a zero training_write_input_slices in execution metrics", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          training_write_input_slices: 0,
        },
      }),
    ).toThrow(/training_write_input_slices/i)
  })

  it("defaults shared snapshot evidence to empty lists when omitted", () => {
    // The fixture predates shared snapshots, so it carries none of these fields.
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: executionMetricsFixture(),
    })

    expect(parsed.execution_metrics?.shared_snapshot_seeds).toEqual([])
    expect(parsed.execution_metrics?.shared_snapshot_captures).toEqual([])
    expect(parsed.execution_metrics?.shared_snapshot_capture_skips).toEqual([])
    expect(parsed.execution_metrics?.warnings).toEqual([])
  })

  it.each(["evicted", "quota"])("rejects a shared snapshot capture with outcome %s", (outcome) => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_captures: [
            {
              node_id: "banding",
              identity_digest: "d2",
              kind: "consumed",
              outcome,
              generation_id: null,
              columns: "all",
            },
          ],
        },
      }),
    ).toThrow(/outcome/i)
  })

  it("rejects a shared snapshot capture skip with an unknown reason", () => {
    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...executionMetricsFixture(),
          shared_snapshot_capture_skips: [
            {
              node_id: "banding",
              reason: "unknown_reason",
            },
          ],
        },
      }),
    ).toThrow(/reason/i)
  })

  it("makes malformed execution-strategy diagnostics unavailable without rejecting metrics", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        execution_strategy: executionStrategyFixture({ schema_version: 2 }),
      },
    })

    expect(parsed.execution_metrics?.execution_strategy).toBeNull()
    expect(parsed.execution_metrics?.memory_pressure_events).toHaveLength(1)
  })

  it("parses P12 streamability counters and deterministic bounded evidence", () => {
    const parsed = parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        streamability: "streaming",
        streamability_evidence: { state: "available", total_count: 2, items: ["filter", "scan"] },
        column_widths: {
          state: "available",
          total_count: 2,
          items: [
            { node_id: "filter", input_width: 4, output_width: 3, requested_width: null, physically_scanned_width: 4 },
            { node_id: "scan", input_width: null, output_width: 4, requested_width: 3, physically_scanned_width: 4 },
          ],
        },
        bytes_read: 1024,
        bytes_written: null,
        estimated_bytes: 2048,
        observed_peak_rss_bytes: null,
        checkpoint_count: 2,
        chunk_count: 3,
      },
    })

    expect(parsed.execution_metrics?.streamability_evidence.items).toEqual(["filter", "scan"])
    expect(parsed.execution_metrics?.column_widths.items.map((item) => item.node_id)).toEqual(["filter", "scan"])
    expect(parsed.execution_metrics?.bytes_written).toBeNull()
    expect(parsed.execution_metrics?.checkpoint_count).toBe(2)
  })

  it.each([
    { estimated_bytes: 10, raw_estimated_bytes: 10 },
    { estimated_bytes: 10, raw_estimated_bytes: 10, estimate_calibration_factor_basis_points: 9_999, estimate_admission_basis: "provided" },
    { estimated_bytes: 81, raw_estimated_bytes: 10, estimate_calibration_factor_basis_points: 80_001, estimate_admission_basis: "provided" },
    { estimated_bytes: 11, raw_estimated_bytes: 10, estimate_calibration_factor_basis_points: 10_000, estimate_admission_basis: "provided" },
  ])("rejects invalid calibrated execution metrics", (calibration) => {
    expect(() => parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: { ...executionMetricsFixture(), ...calibration },
    })).toThrow(/calibrat/i)
  })

  it("requires the closed cache-proof evidence on execution metrics", () => {
    const withoutCacheProof = { ...executionMetricsFixture() } as Record<string, unknown>
    delete withoutCacheProof.cache_proof
    expect(() => parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: withoutCacheProof,
    })).toThrow(/cache_proof/i)
    expect(() => parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        cache_proof: {
          hits: 1,
          misses: 3,
          direct_fallbacks: 0,
          miss_reason_counts: { metadata_source_mismatch: 1, artifact_integrity_schema_failure: 0, unreadable_artifact: 0, proof_unavailable: 1 },
        },
      },
    })).toThrow(/closed reason-count total/i)
  })

  it("rejects over-cap or non-deterministically ordered P12 wrappers", () => {
    expect(() => parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        streamability_evidence: { state: "available", total_count: 2, items: ["scan", "filter"] },
      },
    })).toThrow(/streamability_evidence/i)
    expect(() => parsePreviewNodeResponse({
      ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
      execution_metrics: {
        ...executionMetricsFixture(),
        column_widths: {
          state: "available",
          total_count: 129,
          items: Array.from({ length: 129 }, (_, index) => ({ node_id: `n${index}`, input_width: null, output_width: null, requested_width: null, physically_scanned_width: null })),
        },
      },
    })).toThrow(/column_widths/i)
  })

  it("rejects malformed preview node ids", () => {
    expect(() =>
      parsePreviewNodeResponse({
        status: "ok",
        node_id: 42,
      }),
    ).toThrow(/node_id/i)
  })

  it("parses a git move response", () => {
    const parsed = gitMove({
      sha: "a".repeat(40),
      short_sha: "aaaaaaaa",
      prior_branch: "pricing/test/dev-save",
      is_detached: true,
    })

    expect(parsed.sha).toBe("a".repeat(40))
    expect(parsed.prior_branch).toBe("pricing/test/dev-save")
    expect(parsed.is_detached).toBe(true)
  })

  it("rejects a git move response missing prior_branch", () => {
    expect(() =>
      gitMove({ sha: "abc", short_sha: "abc", is_detached: true }),
    ).toThrow(/prior_branch/i)
  })

  it("parses trace responses including waterfall entries", () => {
    const parsed = parseTraceResponse(loadUiContractFixture("trace_response"))

    expect(Array.isArray(parsed.trace?.waterfall)).toBe(true)
    expect(parsed.trace?.omissions).toEqual([])
    expect(parsed.trace?.correlation_diagnostics).toEqual([])
    expect(parsed.trace?.generated_at).toBe("2026-07-23T12:00:00+00:00")
    expect(parsed.trace?.execution_origin).toBe("fresh_execution")
  })

  it("parses a step that is one of several identical rows and rejects a count below two", () => {
    const fixture = loadUiContractFixture<{ trace: Record<string, unknown> }>("trace_response")
    const [step] = fixture.trace.steps as Record<string, unknown>[]
    const withCount = (count: unknown) => ({
      ...fixture,
      trace: { ...fixture.trace, steps: [{ ...step, identical_row_count: count }] },
    })

    expect(parseTraceResponse(withCount(3)).trace?.steps[0]?.identical_row_count).toBe(3)
    expect(parseTraceResponse(withCount(null)).trace?.steps[0]?.identical_row_count).toBeNull()
    expect(() => parseTraceResponse(withCount(1))).toThrow("identical_row_count")
  })

  it("parses where a seeded trace read its rows and what it skipped", () => {
    const fixture = loadUiContractFixture<{ trace: Record<string, unknown> }>("trace_response")
    const [step] = fixture.trace.steps as Record<string, unknown>[]
    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...fixture.trace,
        steps: [{ ...step, node_id: "join", snapshot_generation_id: "generation-1" }],
        omissions: [{
          node_id: "policies",
          node_name: "policies",
          node_type: "dataInput",
          topological_rank: 0,
          reason: "snapshot_seed",
          diagnostic_index: 0,
        }],
        correlation_diagnostics: [{
          code: "snapshot_seed",
          severity: "info",
          reason: "snapshot_seed",
          message: "Not computed: the trace read the snapshot of join.",
          node_id: "policies",
          seed_node_ids: ["join"],
        }],
      },
    })

    expect(parsed.trace?.steps[0]?.snapshot_generation_id).toBe("generation-1")
    expect(parsed.trace?.omissions[0]?.reason).toBe("snapshot_seed")
    expect(parsed.trace?.correlation_diagnostics[0]?.seed_node_ids).toEqual(["join"])
  })

  it("keeps why a traced value was not computed from the row", () => {
    const fixture = loadUiContractFixture<{ trace: Record<string, unknown> }>("trace_response")
    const [step] = fixture.trace.steps as Record<string, unknown>[]
    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...fixture.trace,
        steps: [{
          ...step,
          calculation: {
            substituted_text: "sum of x over g",
            result_value: 30,
            input_values: {},
            not_computable_reason: "not_row_local: sum",
            result_source: "trace_execution",
            expression_chain: [{
              expression_text: "x.shift(1)",
              target_column: "lagged",
              result_value: null,
              not_computable_reason: "not_row_local: shift",
            }],
            input_sources: {
              x: { node_name: "source", result_value: null, not_computable_reason: "traced_row_unavailable" },
            },
          },
        }],
      },
    })

    const calculation = parsed.trace?.steps[0]?.calculation
    expect(calculation?.not_computable_reason).toBe("not_row_local: sum")
    expect(calculation?.result_source).toBe("trace_execution")
    expect(calculation?.expression_chain?.[0]?.not_computable_reason).toBe("not_row_local: shift")
    expect(calculation?.input_sources?.x?.not_computable_reason).toBe("traced_row_unavailable")
  })

  it("rejects a trace response with no trace (backend always returns one)", () => {
    expect(() => parseTraceResponse({ status: "ok" })).toThrow(/trace/i)
  })

  it("requires trace omissions, provenance, and typed waterfall evidence", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("trace_response")
    const trace = fixture.trace as Record<string, unknown>
    const withoutOmissions = { ...trace }
    delete withoutOmissions.omissions

    expect(() => parseTraceResponse({ ...fixture, trace: withoutOmissions }))
      .toThrow(/omissions/i)
    expect(() => parseTraceResponse({
      ...fixture,
      trace: { ...trace, generated_at: "2026-07-23T12:00:00+01:00" },
    })).toThrow(/UTC/i)
    expect(() => parseTraceResponse({
      ...fixture,
      trace: { ...trace, execution_origin: "unknown_cache" },
    })).toThrow(/execution_origin/i)
    // A trace never reads the preview cache, so that origin is not a value.
    expect(() => parseTraceResponse({
      ...fixture,
      trace: { ...trace, execution_origin: "preview_cache" },
    })).toThrow(/execution_origin/i)
    expect(() => parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        waterfall: [{
          label: "base",
          operation: "set",
          value: 10,
          delta: 10,
          cumulative: 10,
        }],
      },
    })).toThrow(/default_used/i)
    expect(() => parseTraceResponse({
      ...fixture,
      trace: { ...trace, waterfall: { error: "cannot reconcile" } },
    })).toThrow(/error_type/i)
    expect(() => parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        omissions: [{
          node_id: "source",
          node_name: "Source",
          node_type: "dataInput",
          topological_rank: 0,
          reason: "duplicate_exact_match",
          diagnostic_index: 0,
        }],
        correlation_diagnostics: [],
      },
    })).toThrow(/references missing diagnostic/i)
  })

  it("parses trace correlation diagnostics", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("trace_response")
    const trace = fixture.trace as Record<string, unknown>
    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        correlation_diagnostics: [
          {
            code: "ambiguous_row_match",
            severity: "warning",
            reason: "relaxed_match_ambiguous",
            message: "Row correlation for node 'source' is ambiguous.",
            node_id: "source",
            child_node_id: "aggregate",
            match_strategy: "relaxed",
            match_columns: ["region"],
            ignored_columns: ["premium"],
            matched_row_count: 2,
            matched_row_indices: [0, 1],
          },
        ],
      },
    })

    expect(parsed.trace?.correlation_diagnostics).toEqual([
      expect.objectContaining({
        code: "ambiguous_row_match",
        reason: "relaxed_match_ambiguous",
        matched_row_indices: [0, 1],
      }),
    ])
  })

  it("parses trace responses with rich rating_step node details intact", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("trace_response")
    const trace = fixture.trace as Record<string, unknown>
    const steps = trace.steps as Array<Record<string, unknown>>
    const sourceStep = steps[0] ?? {}

    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        target_node_id: "adjustments",
        column: "technical_premium_factor",
        output_value: 0.9,
        steps: [
          {
            ...sourceStep,
            node_id: "adjustments",
            node_name: "Adjustments",
            node_type: "ratingStep",
            schema_diff: {
              columns_added: ["technical_premium_factor"],
              columns_removed: [],
              columns_modified: [],
              columns_passed: ["vehicle_age_band"],
            },
            output_values: { vehicle_age_band: "1-3", technical_premium_factor: 0.9 },
            node_detail: {
              detail_type: "rating_step",
              tables: [
                {
                  name: "vehicle_factor",
                  output_column: "vehicle_factor",
                  factors: [{ column: "vehicle_age_band", value: "1-3" }],
                  selected_value: 0.9,
                  status: "matched",
                  matched: true,
                  default_used: false,
                },
              ],
              combined_outputs: [
                {
                  column: "technical_premium_factor",
                  operation: "multiply",
                  base_value: 1,
                  input_values: { vehicle_factor: 0.9 },
                  value: 0.9,
                },
              ],
            },
          },
        ],
      },
    })

    expect(parsed.trace).toBeDefined()
    const parsedTrace = parsed.trace!
    const detail = parsedTrace.steps[0]?.node_detail as Record<string, unknown>
    const tables = detail.tables as Array<Record<string, unknown>>
    const factors = tables[0]?.factors as Array<Record<string, unknown>>
    const combinedOutputs = detail.combined_outputs as Array<Record<string, unknown>>

    expect(detail.detail_type).toBe("rating_step")
    expect(tables[0]?.status).toBe("matched")
    expect(factors[0]).toEqual({ column: "vehicle_age_band", value: "1-3" })
    expect(combinedOutputs[0]?.input_values).toEqual({ vehicle_factor: 0.9 })
  })

  it("preserves trace calculation input source lineage", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("trace_response")
    const trace = fixture.trace as Record<string, unknown>
    const steps = trace.steps as Array<Record<string, unknown>>
    const sourceStep = steps[0] ?? {}

    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        target_node_id: "age_band",
        column: "age_band",
        output_value: "young",
        steps: [
          {
            ...sourceStep,
            node_id: "age_band",
            node_name: "Age banding",
            node_type: "banding",
            schema_diff: {
              columns_added: ["age_band"],
              columns_removed: [],
              columns_modified: [],
              columns_passed: ["driver_age"],
            },
            expression: {
              expression_text: "driver_age -> age_band",
              expression_type: "banding",
              referenced_columns: ["driver_age"],
            },
            calculation: {
              substituted_text: '22 -> "young"',
              result_value: "young",
              input_values: { driver_age: 22 },
              input_sources: {
                driver_age: {
                  node_name: "Prepare",
                  expression_text: "raw_age + 1",
                  substituted_text: "21 + 1",
                  result_value: 22,
                  input_sources: {
                    raw_age: {
                      node_name: "Policies",
                      result_value: 21,
                    },
                  },
                },
              },
            },
          },
        ],
      },
    })

    const source = parsed.trace?.steps[0]?.calculation?.input_sources?.driver_age
    expect(source?.node_name).toBe("Prepare")
    expect(source?.input_sources?.raw_age?.result_value).toBe(21)
  })

  it("preserves nested conditional taken-branch metadata in trace calculations", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("trace_response")
    const trace = fixture.trace as Record<string, unknown>
    const steps = trace.steps as Array<Record<string, unknown>>
    const sourceStep = steps[0] ?? {}

    const parsed = parseTraceResponse({
      ...fixture,
      trace: {
        ...trace,
        target_node_id: "premium",
        column: "premium",
        output_value: 0,
        steps: [
          {
            ...sourceStep,
            node_id: "premium",
            node_name: "Conditional premium",
            schema_diff: {
              columns_added: [],
              columns_removed: [],
              columns_modified: ["premium"],
              columns_passed: ["tier"],
            },
            expression: {
              expression_text: "when tier = 'A' then 0 when tier = 'B' then 0 otherwise 1",
              expression_type: "conditional",
              referenced_columns: ["tier"],
            },
            calculation: {
              substituted_text: "when 'B' = 'A' then 0 when 'B' = 'B' then 0 otherwise 1",
              result_value: 0,
              input_values: { tier: "B" },
              taken_branch: "then",
              taken_branch_index: 1,
            },
          },
        ],
      },
    })

    expect(parsed.trace?.steps[0]?.calculation?.taken_branch).toBe("then")
    expect(parsed.trace?.steps[0]?.calculation?.taken_branch_index).toBe(1)
  })

  it("parses completed training responses with GLM diagnostics", () => {
    const parsed = parseTrainResponse(loadUiContractFixture("train_response"))

    expect(parsed.glm_coefficients?.[0].feature).toBe("x")
    expect(parsed.diagnostics_errors?.[0].diagnostic).toBe("shap")
  })

  it("parses canonical evaluation reports and rejects retired result fields", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")
    const parsed = parseTrainResponse(fixture)

    expect(parsed.evaluation?.strategy).toBe("random")
    expect(() => parseTrainResponse({ ...fixture, metrics: { rmse: 1 } })).toThrow(
      "TrainResponse: invalid contract at /: additionalProperties",
    )
    expect(() => parseTrainResponse({ ...fixture, cross_validation: {} })).toThrow(
      "TrainResponse: invalid contract at /: additionalProperties",
    )
  })

  it("accepts a saved holdout validation fit without a final refit", () => {
    const base = makeTrainResult()
    const evaluation = base.evaluation!
    const response = makeTrainResult({
      final_test_rows: 0,
      final_test_metrics: {},
      diagnostic_metrics: { gini: 0.45, rmse: 0.12 },
      diagnostics_set: "validation",
      evaluation: {
        ...evaluation,
        refit_on_development: false,
        fit_count: 1,
        final_test_rows: 0,
        summary: { ...evaluation.summary, test_rows: 0 },
      },
    })
    const parsed = parseTrainResponse(response)
    expect(parsed.evaluation?.fit_count).toBe(1)
    expect(parsed.diagnostics_set).toBe("validation")
  })

  it("parses canonical tuning reports with weighted validation evidence", () => {
    const parsed = parseTrainResponse(tunedTrainResponseFixture())

    expect(parsed.evaluation?.validation_method).toBe("single")
    expect(parsed.tuning?.winner_trial_index).toBe(1)
    expect(parsed.tuning?.total_fit_count).toBe(6)
  })

  it("keeps the final fit's evidence (MOD-F01)", () => {
    const fixture = {
      ...tunedTrainResponseFixture(),
      fit_evidence: {
        threads: 4,
        rounds_configured: 500,
        rounds_fitted: 120,
        term_update_steps: null,
        stopping_reason: "validation",
        device: null,
      },
    }
    expect(parseTrainResponse(fixture).fit_evidence).toEqual({
      threads: 4,
      rounds_configured: 500,
      rounds_fitted: 120,
      term_update_steps: null,
      stopping_reason: "validation",
      device: null,
    })
    expect(() =>
      parseTrainResponse({
        ...fixture,
        fit_evidence: { ...fixture.fit_evidence, stopping_reason: "bored" },
      }),
    ).toThrow("TrainResponse: invalid contract at /fit_evidence/stopping_reason: enum")
  })

  it("parses the XGBoost GPU status (MOD-F06)", () => {
    expect(modellingGpuStatus({
      xgboost: { available: false, detail: "Run `haute gpu-setup`.", device: null },
    })).toEqual({ xgboost: { available: false, detail: "Run `haute gpu-setup`.", device: null } })
    expect(() => modellingGpuStatus({ xgboost: { available: "yes", detail: "", device: null } }))
      .toThrow("ModellingGpuStatusResponse: invalid contract at /xgboost/available: type")
  })

  it("keeps the device an XGBoost GPU fit trained on (MOD-F06)", () => {
    const evidence = {
      threads: 4, rounds_configured: 100, rounds_fitted: 100, term_update_steps: null, stopping_reason: "none", device: "cuda:0",
    }
    const gpu = parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: evidence })
    expect(gpu.fit_evidence?.device).toBe("cuda:0")
    expect(() =>
      parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: { ...evidence, device: "" } }),
    ).toThrow("TrainResponse: invalid contract at /fit_evidence/device: minLength")
  })

  it("requires fit evidence to carry its null fields (MOD-F04)", () => {
    // The server sends every evidence field; a GLM's is its thread allotment and nulls.
    const glmEvidence = {
      threads: 4, rounds_configured: null, rounds_fitted: null, term_update_steps: null, stopping_reason: null, device: null,
    }
    expect(parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: glmEvidence }).fit_evidence).toEqual(glmEvidence)
    const ebm = parseTrainResponse({
      ...tunedTrainResponseFixture(),
      fit_evidence: { ...glmEvidence, threads: 1, rounds_configured: 200, stopping_reason: "none", term_update_steps: [400, 200] },
    })
    expect(ebm.fit_evidence?.term_update_steps).toEqual([400, 200])
    expect(() =>
      parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: { threads: 4 } }),
    ).toThrow("TrainResponse: invalid contract at /fit_evidence/rounds_configured: required")
    expect(() =>
      parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: { ...glmEvidence, trees: 3 } }),
    ).toThrow("TrainResponse: invalid contract at /fit_evidence: additionalProperties")
    expect(() =>
      parseTrainResponse({ ...tunedTrainResponseFixture(), fit_evidence: { ...glmEvidence, term_update_steps: [-1] } }),
    ).toThrow("TrainResponse: invalid contract at /fit_evidence/term_update_steps/0: minimum")
  })

  it("accepts an EBM study that refits with the winning budget and no tree count (MOD-F04)", () => {
    const fixture = tunedTrainResponseFixture()
    type LooseTrial = {
      resolved_params: Record<string, unknown>
      sampled_params: Record<string, unknown>
      fits: Record<string, unknown>[]
    }
    for (const [index, trial] of (fixture.tuning.trials as LooseTrial[]).entries()) {
      trial.resolved_params = { max_rounds: 100 * (index + 1), interactions: 0 }
      trial.sampled_params = index === 0 ? {} : { max_rounds: 100 * (index + 1) }
      for (const fit of trial.fits) Object.assign(fit, { best_iteration: null })
    }
    // A fixed-budget refit has no tree count; the server sends null.
    const ebm = {
      ...fixture,
      tuning: {
        ...fixture.tuning,
        final_tree_count: null,
        best_sampled_params: { max_rounds: 200 },
        final_params: { max_rounds: 200, interactions: 0 },
      },
    }
    const parsed = parseTrainResponse(ebm)
    expect(parsed.tuning?.final_params).toEqual({ max_rounds: 200, interactions: 0 })
    expect(parsed.tuning?.final_tree_count).toBeNull()
  })

  it("parses EBM term shapes and surfaces and rejects scores that miss their axes (MOD-F04)", () => {
    const terms = [
      {
        term: "region",
        features: ["region"],
        kind: "main",
        importance: 0.3,
        axes: [{ feature: "region", type: "nominal", labels: ["Missing", "east", "north"] }],
        scores: [0.1, -0.2, 0.4],
      },
      {
        term: "region & age",
        features: ["region", "age"],
        kind: "interaction",
        importance: 0.1,
        axes: [
          { feature: "region", type: "nominal", labels: ["Missing", "east"] },
          { feature: "age", type: "continuous", labels: ["Missing", "< 30", ">= 30"], cuts: [30] },
        ],
        scores: [[0, 0.1, 0.2], [0.3, 0.4, 0.5]],
      },
    ]
    const parsed = parseTrainResponse({ ...tunedTrainResponseFixture(), ebm_terms: terms })
    expect(parsed.ebm_terms.map((term) => term.term)).toEqual(["region", "region & age"])
    expect(parsed.ebm_terms[1].axes[1].cuts).toEqual([30])
    expect(parseTrainResponse(tunedTrainResponseFixture()).ebm_terms).toEqual([])
    expect(() =>
      parseTrainResponse({
        ...tunedTrainResponseFixture(),
        ebm_terms: [{ ...terms[1], scores: [[0, 0.1], [0.3, 0.4]] }],
      }),
    ).toThrow(/scores must match its axes/)
  })

  it("preserves per-feature PDP diagnostic errors", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")

    const parsed = parseTrainResponse({
      ...fixture,
      pdp_data: [
        {
          feature: "age",
          type: "numeric",
          grid: [],
          error: "PDP failed for age",
          error_type: "ValueError",
        },
      ],
    })

    expect(parsed.pdp_data?.[0]).toMatchObject({
      feature: "age",
      type: "numeric",
      grid: [],
      error: "PDP failed for age",
      error_type: "ValueError",
    })
  })

  it("preserves null categorical PDP levels from completed training status", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_status_response")
    const result = fixture.result as Record<string, unknown>
    const pdpData = result.pdp_data as Array<Record<string, unknown>>
    pdpData[0] = {
      feature: "category",
      type: "categorical",
      grid: [
        { value: "known", avg_prediction: 1.1 },
        { value: null, avg_prediction: 1.2 },
      ],
    }

    const parsed = parseTrainStatusResponse(fixture)

    expect(parsed.result?.pdp_data?.[0]?.grid[1]).toEqual({
      value: null,
      avg_prediction: 1.2,
    })
  })

  it.each([
    ["missing", undefined],
    ["boolean", true],
    ["object", { category: "missing" }],
  ])("rejects invalid categorical PDP grid value: %s", (_name, value) => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")

    expect(() =>
      parseTrainResponse({
        ...fixture,
        pdp_data: [{ feature: "category", type: "categorical", grid: [{ value, avg_prediction: 1.2 }] }],
      }),
    ).toThrow(/pdp_data.*value/i)
  })

  it("rejects a null numeric PDP grid value", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")

    expect(() =>
      parseTrainResponse({
        ...fixture,
        pdp_data: [{ feature: "age", type: "numeric", grid: [{ value: null, avg_prediction: 1.2 }] }],
      }),
    ).toThrow(/pdp_data.*value/i)
  })

  it("rejects malformed per-feature PDP diagnostics errors", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")

    expect(() =>
      parseTrainResponse({
        ...fixture,
        pdp_data: [
          {
            feature: "rating_factor",
            type: "numeric",
            grid: [],
            error: ["not", "a", "string"],
          },
        ],
      }),
    ).toThrow(/pdp_data.*error/i)
  })

  it("rejects malformed GLM coefficient rows", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_response")

    expect(() =>
      parseTrainResponse(
        {
          ...fixture,
          glm_coefficients: [{ feature: "x", coefficient: "bad" }],
        },
      ),
    ).toThrow(/glm_coefficients/i)
  })

  it("parses train status responses with nested results", () => {
    const parsed = parseTrainStatusResponse(loadUiContractFixture("train_status_response"))

    expect(parsed.result?.status).toBe("completed")
    expect(Object.keys(parsed.result?.diagnostic_metrics ?? {}).length).toBeGreaterThan(0)
    expect(parsed.result?.evaluation?.plan_sha256).toMatch(/^[0-9a-f]{64}$/)
    expect(parsed.train_loss.learn).toBe(0.1)
  })

  it("validates the bounds of tuning progress", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>(
      "train_status_response",
    )
    const progress = {
      ...fixture,
      phase: "trial_fit",
      trial_index: 1,
      trial_count: 5,
      fold_index: 1,
      fold_count: 2,
      completed_fits: 0,
      total_fits: 11,
      best_objective: 0.4,
    }
    expect(parseTrainStatusResponse(progress).phase).toBe("trial_fit")

    // The phase/index relationships are the server's (TrainStatusResponse);
    // the browser checks the generated bounds.
    expect(() => parseTrainStatusResponse({
      ...progress,
      trial_count: 4,
    })).toThrow("TrainStatusResponse: invalid contract at /trial_count: minimum")
    expect(() => parseTrainStatusResponse({
      ...progress,
      fold_count: 11,
    })).toThrow("TrainStatusResponse: invalid contract at /fold_count: maximum")
    expect(() => parseTrainStatusResponse({
      ...progress,
      phase: "stalled",
    })).toThrow("TrainStatusResponse: invalid contract at /phase: enum")
  })

  it("retains the authoritative live loss-history snapshot and truncation flag", () => {
    const parsed = parseTrainStatusResponse({
      ...loadUiContractFixture<Record<string, unknown>>("train_status_response"),
      train_loss_history: [
        { iteration: 20, train_rmse: 0.9, eval_rmse: 1.1 },
        { iteration: 30, train_rmse: 0.8, eval_rmse: 1.0 },
      ],
      train_loss_history_truncated: true,
    })

    expect(parsed.train_loss_history).toEqual([
      { iteration: 20, train_rmse: 0.9, eval_rmse: 1.1 },
      { iteration: 30, train_rmse: 0.8, eval_rmse: 1.0 },
    ])
    expect(parsed.train_loss_history_truncated).toBe(true)
  })

  it("requires the live loss history the server always sends", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>(
      "train_status_response",
    )
    delete fixture.train_loss_history

    expect(() => parseTrainStatusResponse(fixture)).toThrow(
      "TrainStatusResponse: invalid contract at /train_loss_history: required",
    )
  })

  it.each([
    [{ iteration: "20", train_rmse: 0.9 }],
    [{ iteration: 20, train_rmse: "0.9" }],
    [null],
  ])("rejects a malformed present live loss-history snapshot", (history) => {
    expect(() =>
      parseTrainStatusResponse({
        ...loadUiContractFixture<Record<string, unknown>>(
          "train_status_response",
        ),
        train_loss_history: history,
      }),
    ).toThrow(/train_loss_history/i)
  })

  it("rejects a non-Boolean live loss-history truncation flag", () => {
    expect(() =>
      parseTrainStatusResponse({
        ...loadUiContractFixture<Record<string, unknown>>(
          "train_status_response",
        ),
        train_loss_history_truncated: "yes",
      }),
    ).toThrow(/train_loss_history_truncated/i)
  })

  it("preserves structured terminal training error detail", () => {
    const detail = {
      error_code: "gpu_vram_limit",
      message: "Select CPU and retry.",
      reason: "gpu_vram_limit_exceeded",
    }
    const parsed = parseTrainStatusResponse({
      ...loadUiContractFixture<Record<string, unknown>>("train_status_response"),
      status: "memory_limited",
      result: null,
      terminal_reason: "memory_limited",
      error_code: "gpu_vram_limit",
      http_status_code: 507,
      error_detail: detail,
    })

    expect(parsed.error_code).toBe("gpu_vram_limit")
    expect(parsed.http_status_code).toBe(507)
    expect(parsed.error_detail).toEqual(detail)
  })

  it("parses a data-profile response as the statistics of one data version", () => {
    const parsed = parseNodeDataProfileResponse(
      loadUiContractFixture("node_data_profile_response"),
    )

    expect(parsed.status).toBe("completed")
    expect(parsed.result?.row_count).toBe(150)
    expect(parsed.result?.column_count).toBe(3)
    expect(parsed.result?.data_version).toBe(parsed.point.data_version)
    expect(parsed.result?.overview_summary.data_quality.issue_count).toBe(1)
    expect(parsed.result?.columns.map((column: { name: string }) => column.name)).toEqual([
      "policy_id",
      "premium",
      "region",
    ])
  })

  it("parses typed pivot matrices, job status, and exact members", () => {
    const run = parseExplorePivotRunResponse(loadUiContractFixture("explore_pivot_run_response"))
    const status = parseExplorePivotStatusResponse(loadUiContractFixture("explore_pivot_status_response"))
    const members = parseExplorePivotMembersResponse(loadUiContractFixture("explore_pivot_members_response"))

    expect(run.cached).toBe(true)
    expect(run.result?.row_paths[0].members[0]).toEqual({ kind: "string", value: "North" })
    expect(run.result?.cells[0].value).toBe(12.5)
    expect(status.result?.calculation_key).toBe(run.result?.calculation_key)
    expect(members.members[1].key).toEqual({ kind: "null", value: null })
  })

  it("parses formula identities in pivot results", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>(
      "explore_pivot_run_response",
    )
    const result = fixture.result as Record<string, unknown>
    const values = result.values as unknown[]
    const parsed = parseExplorePivotRunResponse({
      ...fixture,
      result: {
        ...result,
        values: [
          ...values,
          { id: "formula_1", field: "loss_ratio", aggregation: "formula" },
        ],
      },
    })

    expect(parsed.result?.values.at(-1)).toEqual({
      id: "formula_1",
      field: "loss_ratio",
      aggregation: "formula",
    })
  })

  it("parses every typed pivot member key", () => {
    const parsed = parseExplorePivotMembersResponse({
      status: "ok",
      field: "value",
      members: [
        { key: { kind: "null", value: null }, label: "null", count: 1 },
        { key: { kind: "nan", value: null }, label: "nan", count: 1 },
        { key: { kind: "string", value: "North" }, label: "North", count: 1 },
        { key: { kind: "boolean", value: true }, label: "true", count: 1 },
        { key: { kind: "integer", value: "-42" }, label: "-42", count: 1 },
        { key: { kind: "float", value: 12.5 }, label: "12.5", count: 1 },
        { key: { kind: "decimal", value: "12.50" }, label: "12.50", count: 1 },
        { key: { kind: "date", value: "2026-08-13" }, label: "date", count: 1 },
        { key: { kind: "datetime", value: "2026-08-13T14:30:00+01:00" }, label: "datetime", count: 1 },
        { key: { kind: "time", value: "14:30:00+01:00" }, label: "time", count: 1 },
      ],
      failure: null,
    })

    expect(parsed.members.map(({ key }) => key)).toEqual([
      { kind: "null", value: null },
      { kind: "nan", value: null },
      { kind: "string", value: "North" },
      { kind: "boolean", value: true },
      { kind: "integer", value: "-42" },
      { kind: "float", value: 12.5 },
      { kind: "decimal", value: "12.50" },
      { kind: "date", value: "2026-08-13" },
      { kind: "datetime", value: "2026-08-13T14:30:00+01:00" },
      { kind: "time", value: "14:30:00+01:00" },
    ])
  })

  it("rejects malformed pivot path/cell/member payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("explore_pivot_run_response")
    const result = fixture.result as Record<string, unknown>
    const cell = (result.cells as Record<string, unknown>[])[0]
    expect(() =>
      parseExplorePivotRunResponse({
        ...fixture,
        result: { ...result, cells: [{ ...cell, row_index: -1 }] },
      }),
    ).toThrow("ExplorePivotRunResponse: invalid contract at /result/cells/0/row_index: minimum")

    expect(() =>
      parseExplorePivotMembersResponse({
        status: "ok",
        field: "region",
        members: [{ key: { kind: "wat", value: null }, label: "bad", count: 1 }],
        failure: null,
      }),
    ).toThrow("ExplorePivotMembersResponse: invalid contract at /members/0/key/kind: enum")
  })

  it("keeps the pivot matrix closed and each member key's value true to its kind", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("explore_pivot_run_response")
    const result = fixture.result as Record<string, unknown>
    const cell = (result.cells as Record<string, unknown>[])[0]
    const rowCount = (result.row_paths as unknown[]).length

    expect(() =>
      parseExplorePivotRunResponse({
        ...fixture,
        result: { ...result, cells: [{ ...cell, row_index: rowCount }] },
      }),
    ).toThrow("parseExplorePivotResult: pivot cell index is outside the declared matrix")
    expect(() =>
      parseExplorePivotRunResponse({
        ...fixture,
        result: { ...result, cells: [{ ...cell, value_id: "undeclared" }] },
      }),
    ).toThrow("parseExplorePivotResult: pivot cell references an unknown value id")
    expect(() =>
      parseExplorePivotMembersResponse({
        status: "ok",
        field: "region",
        members: [{ key: { kind: "null", value: "North" }, label: "bad", count: 1 }],
        failure: null,
      }),
    ).toThrow("parseExplorePivotMemberKey: expected members[0].key.value to be null for null")
    expect(() =>
      parseExplorePivotMembersResponse({
        status: "ok",
        field: "policy_count",
        members: [{ key: { kind: "integer", value: "1.5" }, label: "bad", count: 1 }],
        failure: null,
      }),
    ).toThrow("parseExplorePivotMemberKey: expected members[0].key.value to be a canonical integer")
  })

  it("reads rating levels, and requires the counts a cache-required answer sends", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("rating_levels_response")

    const answered = parseRatingLevelsResponse(fixture)
    expect(answered.columns[0].values).toEqual([
      { value: "North", count: 750 },
      { value: "South", count: 249 },
      { value: "Orkney", count: 1 },
    ])
    expect(answered.columns[0].distinct_count).toBe(3)

    // A cache-required answer sends its empty levels and zero counts.
    const unanswered = parseRatingLevelsResponse({
      status: "cache_required",
      point: fixture.point,
      data_version: null,
      total_rows: 0,
      columns: [],
    })
    expect(unanswered.columns).toEqual([])
    expect(unanswered.total_rows).toBe(0)
    expect(() =>
      parseRatingLevelsResponse({ status: "cache_required", point: fixture.point }),
    ).toThrow("RatingLevelsResponse: invalid contract at /data_version: required")
  })

  it("rejects rating levels that are not levels", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("rating_levels_response")

    expect(() =>
      parseRatingLevelsResponse({ ...fixture, status: "partial" }),
    ).toThrow("RatingLevelsResponse: invalid contract at /status: enum")
    expect(() =>
      parseRatingLevelsResponse({
        ...fixture,
        columns: [{ column: 7, values: [], distinct_count: 0, null_count: 0 }],
      }),
    ).toThrow("RatingLevelsResponse: invalid contract at /columns/0/column: type")
    expect(() =>
      parseRatingLevelsResponse({
        ...fixture,
        columns: [{ column: "region", values: [{ value: null, count: 1 }], distinct_count: 1, null_count: 0 }],
      }),
    ).toThrow("RatingLevelsResponse: invalid contract at /columns/0/values/0/value: type")
  })

  it("rejects malformed profile payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("node_data_profile_response")
    const result = fixture.result as Record<string, unknown>

    expect(() =>
      parseNodeDataProfileResponse({
        ...fixture,
        result: { ...result, row_count: "bad" },
      }),
    ).toThrow("NodeDataProfileResponse: invalid contract at /result/row_count: type")
  })

  describe("parseExploreColumnStat (via parseNodeDataProfile.columns)", () => {
    // The node-data status still reads its profile with this hand parser until
    // the node-data responses are generated (API-R03).
    function parseProfileOf(response: Record<string, unknown>) {
      return { result: parseNodeDataProfile(response.result) }
    }

    function withColumns(columns: unknown): Record<string, unknown> {
      const fixture = loadUiContractFixture<Record<string, unknown>>(
        "node_data_profile_response",
      )
      const result = fixture.result as Record<string, unknown>
      return { ...fixture, result: { ...result, columns } }
    }

    function withoutResultField(key: string): Record<string, unknown> {
      const fixture = loadUiContractFixture<Record<string, unknown>>(
        "node_data_profile_response",
      )
      const result = fixture.result as Record<string, unknown>
      const { [key]: _removed, ...nextResult } = result
      void _removed
      return { ...fixture, result: nextResult }
    }

    const numericColumn = {
      name: "premium",
      dtype: "Float64",
      kind: "Numeric",
      null_count: 0,
      distinct_count: 3,
      unique_ratio: 1,
      is_high_cardinality: false,
      is_identifier_candidate: false,
      text_min_length: null,
      text_mean_length: null,
      text_max_length: null,
      temporal_span: null,
    }

    it("parses a server-binned histogram and reads a missing or null one as null", () => {
      const histogram = {
        status: "ok",
        bins: [
          { start: 0, end: 5, count: 2 },
          { start: 5, end: 10, count: 1 },
        ],
        finite_count: 3,
        non_finite_count: 1,
        skipped_reason: null,
      }
      const parsed = parseProfileOf(
        withColumns([
          { ...numericColumn, histogram },
          { ...numericColumn, name: "no_histogram" },
          { ...numericColumn, name: "null_histogram", histogram: null },
        ]),
      )
      const [binned, missing, empty] = parsed.result!.columns
      expect(binned.histogram).toEqual(histogram)
      expect(missing.histogram).toBeNull()
      expect(empty.histogram).toBeNull()
    })

    it("accepts an integer column skipped for browser precision", () => {
      const histogram = {
        status: "skipped",
        bins: [],
        finite_count: 3,
        non_finite_count: 0,
        skipped_reason: "integer_precision",
      }
      const parsed = parseProfileOf(withColumns([{ ...numericColumn, histogram }]))
      expect(parsed.result!.columns[0].histogram).toEqual(histogram)
    })

    it.each([
      ["an unknown status", { status: "partial", bins: [], finite_count: 0, non_finite_count: 0, skipped_reason: null }, /status/],
      ["a bin without a count", { status: "ok", bins: [{ start: 0, end: 1 }], finite_count: 1, non_finite_count: 0, skipped_reason: null }, /bins\[0\]\.count/],
      ["an unknown skip reason", { status: "skipped", bins: [], finite_count: null, non_finite_count: null, skipped_reason: "budget" }, /skipped_reason/],
    ])("rejects a histogram with %s", (_label, histogram, message) => {
      expect(() =>
        parseProfileOf(withColumns([{ ...numericColumn, histogram }])),
      ).toThrow(message)
    })

    it("parses a fully populated column stat", () => {
      const parsed = parseProfileOf(
        withColumns([
          {
            name: "premium",
            dtype: "Float64",
            kind: "Numeric",
            null_count: 3,
            nan_count: 4,
            distinct_count: 42,
            min_value: "10",
            p25_value: "25",
            median_value: "50",
            mean_value: "52.5",
            p75_value: "75",
            max_value: "100",
            std_value: "12.5",
            zero_count: 1,
            negative_count: 2,
            unique_ratio: 0.42,
            is_high_cardinality: false,
            is_identifier_candidate: false,
            text_min_length: null,
            text_mean_length: null,
            text_max_length: null,
            temporal_span: null,
          },
        ]),
      )
      expect(parsed.result?.columns).toHaveLength(1)
      const col = parsed.result!.columns[0]
      expect(col.name).toBe("premium")
      expect(col.dtype).toBe("Float64")
      expect(col.kind).toBe("Numeric")
      expect(col.null_count).toBe(3)
      expect(col.nan_count).toBe(4)
      expect(col.distinct_count).toBe(42)
      expect(col.p25_value).toBe("25")
      expect(col.median_value).toBe("50")
      expect(col.mean_value).toBe("52.5")
      expect(col.p75_value).toBe("75")
      expect(col.std_value).toBe("12.5")
      expect(col.zero_count).toBe(1)
      expect(col.negative_count).toBe(2)
    })

    it("accepts null distinct_count", () => {
      const parsed = parseProfileOf(
        withColumns([
          {
            name: "sparse",
            dtype: "String",
            kind: "Text",
            null_count: 10,
            distinct_count: null,
            unique_ratio: null,
            is_high_cardinality: false,
            is_identifier_candidate: false,
            text_min_length: null,
            text_mean_length: null,
            text_max_length: null,
            temporal_span: null,
          },
        ]),
      )
      const col = parsed.result!.columns[0]
      expect(col.distinct_count).toBeNull()
    })

    it("accepts a profile without columns as an empty column list", () => {
      const parsed = parseProfileOf(withoutResultField("columns"))

      expect(parsed.result?.columns).toEqual([])
    })

    it.each(["row_count", "column_count", "generated_at", "data_version"])(
      "throws when %s is missing from a profile",
      (field) => {
        expect(() => parseProfileOf(withoutResultField(field))).toThrow(
          /parseNodeDataProfile/i,
        )
      },
    )

    it("throws when overview_summary is missing from a cache report", () => {
      expect(() => parseProfileOf(withoutResultField("overview_summary"))).toThrow(
        /parseExploreOverviewSummary/i,
      )
    })

    it("throws when distinct_count is missing", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            { name: "minimal", dtype: "Int64", kind: "Numeric", null_count: 0 },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when overview summary issue severity is invalid", () => {
      const fixture = loadUiContractFixture<Record<string, unknown>>(
        "node_data_profile_response",
      )
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      expect(() =>
        parseProfileOf({
          ...fixture,
          result: {
            ...result,
            overview_summary: {
              ...overview,
              data_quality: {
                issue_count: 1,
                issues: [{ severity: "info", label: "x", detail: "y" }],
              },
            },
          },
        }),
      ).toThrow(/parseExploreOverviewSummary/i)
    })

    it("parses categorical summary profiles", () => {
      const fixture = loadUiContractFixture<Record<string, unknown>>(
        "node_data_profile_response",
      )
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      const parsed = parseProfileOf({
        ...fixture,
        result: {
          ...result,
          overview_summary: {
            ...overview,
            categorical_summary: [
              {
                field: "region",
                distinct_count: 2,
                expandable: true,
                values_truncated: false,
                values: [
                  { value: "north", count: 3 },
                  { value: null, count: 1 },
                ],
              },
            ],
          },
        },
      })

      expect(parsed.result?.overview_summary.categorical_summary[0]).toEqual({
        field: "region",
        distinct_count: 2,
        expandable: true,
        values_truncated: false,
        values: [
          { value: "north", count: 3 },
          { value: null, count: 1 },
        ],
      })
    })

    it("throws when categorical summary value counts are malformed", () => {
      const fixture = loadUiContractFixture<Record<string, unknown>>(
        "node_data_profile_response",
      )
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      expect(() =>
        parseProfileOf({
          ...fixture,
          result: {
            ...result,
            overview_summary: {
              ...overview,
              categorical_summary: [
                {
                  field: "region",
                  distinct_count: 2,
                  expandable: true,
                  values_truncated: false,
                  values: [{ value: "north", count: "3" }],
                },
              ],
            },
          },
        }),
      ).toThrow(/parseExploreOverviewSummary/i)
    })

    it("throws when name is missing", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              dtype: "Float64",
              kind: "Numeric",
              null_count: 0,
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when dtype is missing", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "x",
              kind: "Numeric",
              null_count: 0,
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when kind is missing", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "x",
              dtype: "Float64",
              null_count: 0,
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when kind is invalid", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "x",
              dtype: "Float64",
              kind: "Money",
              null_count: 0,
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when null_count is missing", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "x",
              dtype: "Float64",
              kind: "Numeric",
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when null_count is a string", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "x",
              dtype: "Float64",
              kind: "Numeric",
              null_count: "3",
              distinct_count: 1,
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when numeric profile counts are malformed", () => {
      expect(() =>
        parseProfileOf(
          withColumns([
            {
              name: "premium",
              dtype: "Float64",
              kind: "Numeric",
              null_count: 0,
              distinct_count: 1,
              zero_count: "1",
            },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })
  })

  it("preserves typed execution metrics on train status responses", () => {
    const parsed = parseTrainStatusResponse(loadUiContractFixture("train_status_metrics_response"))

    expect(parsed.execution_metrics?.admission?.budget_policy).toBe("adaptive_local")
    expect(parsed.execution_metrics?.memory_pressure_events[0]?.threshold_percent).toBe(75)
  })

  it("parses optimiser status responses with completed solve payloads", () => {
    const parsed = parseOptimiserStatusResponse(loadUiContractFixture("optimiser_status_response"))

    expect(parsed.result?.lambdas.loss).toBe(0.3)
    expect(parsed.frontier?.constraint_names).toEqual(["loss"])
  })

  it("preserves typed execution metrics on optimiser status responses", () => {
    const parsed = parseOptimiserStatusResponse({
      ...loadUiContractFixture<Record<string, unknown>>("optimiser_status_response"),
      execution_metrics: completeExecutionMetrics(),
    })

    expect(parsed.execution_metrics?.admission?.os_reserve_bytes).toBe(2000)
    expect(parsed.execution_metrics?.memory_pressure_events[0]?.headroom_used_bytes).toBe(1500)
  })

  it("preserves optimiser status frontier errors", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    const result = fixture.result as Record<string, unknown>

    const parsed = parseOptimiserStatusResponse({
      ...fixture,
      result: {
        ...result,
        frontier: null,
        frontier_error: "Frontier unavailable: frontier exploded",
      },
      frontier: null,
    })

    expect(parsed.result?.frontier).toBeNull()
    expect(parsed.result?.frontier_error).toBe("Frontier unavailable: frontier exploded")
  })

  it("parses submodel response payloads", () => {
    const created = parseSubmodelCreateResponse(loadUiContractFixture("submodel_create_response"))
    const loaded = parseSubmodelGraphResponse(loadUiContractFixture("submodel_graph_response"))
    const dissolved = parseDissolveSubmodelResponse(loadUiContractFixture("dissolve_submodel_response"))

    expect(created.submodel_file).toBe("modules/pricing.py")
    expect(created.source_revision).toBe("revision-create-1")
    expect(created.graph.edges[0].targetPort).toBe("base")
    expect(created.graph.edges[1].sourcePort).toBe("quotes")
    expect(loaded.submodel_name).toBe("pricing")
    expect(loaded.definition_id).toBe("pricing-definition")
    expect(loaded.submodel_file).toBe("modules/pricing.py")
    expect(dissolved.source_revision).toBe("revision-dissolve-1")
    expect(dissolved.instance_id).toBe("pricing")
    expect(dissolved.definition_id).toBe("pricing-definition")
    expect(dissolved.graph.nodes).toHaveLength(2)
  })

  it("rejects missing mandatory submodel identity and revision metadata", () => {
    const create = loadUiContractFixture<Record<string, unknown>>("submodel_create_response")
    const loaded = loadUiContractFixture<Record<string, unknown>>("submodel_graph_response")
    const dissolve = loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response")

    expect(() => parseSubmodelCreateResponse({ ...create, source_revision: undefined })).toThrow(/source_revision/i)
    expect(() => parseSubmodelCreateResponse({ ...create, parent_file: undefined })).toThrow(/parent_file/i)
    expect(() => parseSubmodelGraphResponse({ ...loaded, submodel_file: undefined })).toThrow(/submodel_file/i)
    expect(() => parseSubmodelGraphResponse({ ...loaded, definition_id: undefined })).toThrow(/definition_id/i)
    expect(() => parseDissolveSubmodelResponse({ ...dissolve, source_revision: undefined })).toThrow(/source_revision/i)
    expect(() => parseDissolveSubmodelResponse({ ...dissolve, instance_id: undefined })).toThrow(/instance_id/i)
    expect(() => parseDissolveSubmodelResponse({ ...dissolve, definition_id: undefined })).toThrow(/definition_id/i)
  })

  it("rejects removed dissolve file-lifecycle fields", () => {
    const dissolve = loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response")

    expect(() => parseDissolveSubmodelResponse({
      ...dissolve,
      submodel_file_deleted: false,
    })).toThrow(/submodel_file_deleted.*no longer supported/i)
    expect(() => parseDissolveSubmodelResponse({
      ...dissolve,
      retained_submodel_file: "modules/pricing.py",
    })).toThrow(/retained_submodel_file.*no longer supported/i)
  })

  it("parses modelling preflight payloads", () => {
    const mlflow = mlflowDestinations(
      loadUiContractFixture("mlflow_destinations_response"),
    )
    const estimate = parseTrainEstimateResponse(loadUiContractFixture("train_estimate_response"))
    const log = parseMlflowLogResponse(loadUiContractFixture("mlflow_log_response"))
    const saved = savedModel(loadUiContractFixture("model_save_response"))
    const destination = modelSaveDestination(
      loadUiContractFixture("model_save_destination_response"),
    )

    expect(mlflow.mlflow_installed).toBe(true)
    expect(mlflow.mlflow_importable).toBe(true)
    expect("auto" in mlflow).toBe(false)
    expect(mlflow.destinations.map((entry) => entry.key)).toEqual([
      "databricks",
      "server",
      "local",
    ])
    expect(mlflow.destinations[0]?.category).toBe("authentication")
    expect(mlflow.destinations[0]?.configured).toBe(true)
    expect(mlflow.destinations[0]?.probed).toBe(true)
    expect(mlflow.destinations[0]?.ok).toBe(false)
    expect(mlflow.destinations[0]?.destination).toBe("databricks://team")
    expect(mlflow.destinations[0]?.config_source).toBe("env")
    expect(mlflow.destinations[2]?.probed).toBe(false)
    expect(mlflow.detail).toBe("")
    expect(estimate.estimated_mb).toBe(12.5)
    expect(log.run_id).toBe("run-123")
    expect(saved).toEqual({
      status: "ok",
      path: "models/frequency.cbm",
      feature_contract_path: "models/frequency.feature_contract.json",
    })
    expect(destination).toEqual({ path: "models/frequency.cbm", suffix_mismatch: false })
    expect(() => modelSaveDestination({ ...destination, suffix_mismatch: "no" })).toThrow(
      "ModelSaveDestinationResponse: invalid contract at /suffix_mismatch: type",
    )
    expect(() => savedModel({ ...saved, status: "error" })).toThrow(
      "SaveModelResponse: invalid contract at /status: const",
    )
    expect(() => savedModel({ ...saved, feature_contract_path: null })).toThrow(
      "SaveModelResponse: invalid contract at /feature_contract_path: type",
    )
  })

  it("parses a strict bounded evaluation preview and defaults an omitted preview to null", () => {
    const estimate = parseTrainEstimateResponse({
      total_rows: 1000, safe_row_limit: null, estimated_mb: 12.5,
      training_mb: 25, available_mb: 512, bytes_per_row: 256,
      was_downsampled: false, warning: null, gpu_vram_estimated_mb: null,
      gpu_vram_available_mb: null, gpu_warning: null, unavailable: null,
      evaluation_preview: {
        schema_version: 1, strategy: "temporal", validation_method: "cross_validation",
        development_rows: 800, final_test_rows: 200, validation_fit_count: 5,
        min_selection_train_rows: 500, max_selection_train_rows: 640,
        min_selection_validation_rows: 160, max_selection_validation_rows: 200,
        development_date_range: { start: "2024-01-01", end: "2024-09-30" },
        final_test_date_range: { start: "2024-10-01", end: "2024-12-31" },
      },
    })
    const withoutPreview = { ...estimate }
    delete (withoutPreview as Partial<typeof estimate>).evaluation_preview

    expect(estimate.evaluation_preview).toMatchObject({ strategy: "temporal", validation_fit_count: 5 })
    expect(parseTrainEstimateResponse(withoutPreview).evaluation_preview).toBeNull()
    expect(parseTrainEstimateResponse({
      ...withoutPreview,
      evaluation_preview: {
        schema_version: 1, strategy: "random", validation_method: "none",
        development_rows: 1000, final_test_rows: 0, validation_fit_count: 0,
      },
    }).evaluation_preview).toMatchObject({ validation_method: "none", validation_fit_count: 0 })
  })

  it("parses an unavailable estimate's reason and rejects figures that disagree with it", () => {
    const unavailable = parseTrainEstimateResponse(
      loadUiContractFixture("train_estimate_unavailable_response"),
    )
    expect(unavailable.unavailable).toEqual({
      reason: "row_count_unprovable",
      blocking_node_id: "explode_items",
    })
    expect([unavailable.total_rows, unavailable.estimated_mb, unavailable.bytes_per_row]).toEqual([
      null,
      null,
      null,
    ])

    const sized = loadUiContractFixture<Record<string, unknown>>("train_estimate_response")
    const blank = { estimated_mb: null, training_mb: null, bytes_per_row: null }
    const schema = { reason: "schema_unresolvable", blocking_node_id: null }
    expect(parseTrainEstimateResponse({ ...sized, ...blank, unavailable: schema }).unavailable).toEqual(schema)

    const rejected: [Record<string, unknown>, RegExp][] = [
      [{ unavailable: undefined }, /unavailable/],
      [{ estimated_mb: null }, /requires a row total and memory figures/],
      [{ total_rows: null }, /requires a row total and memory figures/],
      [{ estimated_mb: undefined }, /estimated_mb/],
      [{ unavailable: schema }, /has no memory figures/],
      [{ ...blank, unavailable: schema, was_downsampled: true }, /no downsampling verdict or warning/],
      [{ ...blank, unavailable: schema, gpu_vram_estimated_mb: 12 }, /no GPU VRAM check/],
      [
        { ...blank, unavailable: { reason: "row_count_unprovable", blocking_node_id: "join" } },
        /only a row_count_unprovable estimate lacks a row total/,
      ],
      [
        { ...blank, total_rows: null, unavailable: { reason: "row_count_unprovable", blocking_node_id: null } },
        /names the blocking node/,
      ],
      [{ ...blank, unavailable: { ...schema, blocking_node_id: "join" } }, /names no blocking node/],
      [{ ...blank, unavailable: { ...schema, extra: true } }, /\/unavailable: additionalProperties/],
      [{ ...blank, unavailable: { reason: "cardinality", blocking_node_id: null } }, /\/unavailable\/reason: enum/],
    ]
    for (const [overrides, error] of rejected) {
      expect(() => parseTrainEstimateResponse({ ...sized, ...overrides })).toThrow(error)
    }
  })

  it("rejects malformed bounded evaluation previews", () => {
    const estimate = {
      total_rows: 1000, safe_row_limit: null, estimated_mb: 12.5,
      training_mb: 25, available_mb: 512, bytes_per_row: 256,
      was_downsampled: false, warning: null, gpu_vram_estimated_mb: null,
      gpu_vram_available_mb: null, gpu_warning: null, unavailable: null,
    }
    expect(() => parseTrainEstimateResponse({
      ...estimate,
      evaluation_preview: {
        schema_version: 1, strategy: "random", validation_method: "single",
        development_rows: 800, final_test_rows: 200, validation_fit_count: 1,
        unexpected: true,
      },
    })).toThrow("TrainEstimateResponse: invalid contract at /evaluation_preview: additionalProperties")
    expect(() => parseTrainEstimateResponse({
      ...estimate,
      evaluation_preview: {
        schema_version: 1, strategy: "random", validation_method: "single",
        development_rows: 800, final_test_rows: 200, validation_fit_count: 2,
        min_selection_train_rows: 600, max_selection_train_rows: 600,
        min_selection_validation_rows: 200, max_selection_validation_rows: 200,
      },
    })).toThrow(/inconsistent fit count/i)
    expect(() => parseTrainEstimateResponse({
      ...estimate,
      evaluation_preview: {
        schema_version: 1, strategy: "group", validation_method: "none",
        development_rows: 800, final_test_rows: 200, validation_fit_count: 0,
      },
    })).toThrow(/requires group counts/i)
    expect(() => parseTrainEstimateResponse({
      ...estimate,
      evaluation_preview: {
        schema_version: 1, strategy: "temporal", validation_method: "none",
        development_rows: 800, final_test_rows: 200, validation_fit_count: 0,
        development_date_range: { start: "2024-01-01", end: "2024-09-30" },
      },
    })).toThrow(/inconsistent date ranges/i)
  })

  it("parses optimiser action payloads", () => {
    const solve = parseSolveOptimiserResponse(loadUiContractFixture("solve_optimiser_response"))
    const estimate = parseOptimiserEstimateResponse(loadUiContractFixture("optimiser_estimate_response"))
    const apply = parseApplyOptimiserResponse(loadUiContractFixture("optimiser_apply_response"))
    const frontier = parseFrontierResponse(loadUiContractFixture("optimiser_frontier_response"))
    const frontierAutoRange = parseFrontierAutoRangeResponse(loadUiContractFixture("optimiser_frontier_auto_range_response"))
    const selected = parseFrontierSelectResponse(loadUiContractFixture("optimiser_frontier_select_response"))
    const saved = parseSaveOptimiserResponse(loadUiContractFixture("optimiser_save_response"))

      expect(solve.job_id).toBe("opt-job-1")
      expect(estimate.total_rows).toBe(10000)
      expect(estimate.quote_count).toBe(500)
      expect(estimate.scenarios_per_quote_min).toBe(20)
      expect(estimate.scenarios_per_quote_max).toBe(20)
      expect(estimate.expanded_row_count).toBe(10000)
      expect(apply.from_artifact).toBe(false)
    expect(apply.preview[0]?.scenario).toBe("A")
    const parsedApply = parseApplyOptimiserResponse({
      status: "ok",
      total_objective: 0,
      constraints: {},
      from_artifact: false,
      error: null,
      preview: [{ scenario: "A" }],
      row_count: 200,
      preview_row_count: 100,
      preview_row_limit: 100,
      preview_truncated: true,
    })
    expect(parsedApply.preview_truncated).toBe(true)
    expect(parsedApply.from_artifact).toBe(false)
    expect(frontier.constraint_names).toEqual(["loss"])
    expect(frontierAutoRange.ranges.expected_margin).toEqual({ min: 11, max: 39 })
    expect(parseFrontierAutoRangeStatusResponse({
      status: "superseded",
      progress: 0.25,
      message: "Superseded by a newer request.",
      elapsed_seconds: 1.5,
      result: null,
      terminal_reason: "superseded",
      error_code: null,
      http_status_code: null,
      error_detail: null,
      execution_metrics: completeExecutionMetrics(),
    }).status).toBe("superseded")
    const contractErrorStatus = parseFrontierAutoRangeStatusResponse({
      status: "contract_error",
      progress: 1,
      message: "Frontier auto range cannot run in bounded streaming mode",
      elapsed_seconds: 2.5,
      result: null,
      terminal_reason: "contract_error",
      error_code: "contract_error",
      http_status_code: 422,
      error_detail: "Fan-in projection contract does not cover columns required by the node.",
      execution_metrics: null,
    })
    expect(contractErrorStatus.status).toBe("contract_error")
    expect(contractErrorStatus.terminal_reason).toBe("contract_error")
    expect(contractErrorStatus.error_code).toBe("contract_error")
    expect(contractErrorStatus.http_status_code).toBe(422)
    expect(parseFrontierAutoRangeStatusResponse({
      status: "completed",
      progress: 1,
      message: "done",
      elapsed_seconds: 2.5,
      result: loadUiContractFixture("optimiser_frontier_auto_range_response"),
      terminal_reason: null,
      error_code: null,
      http_status_code: null,
      error_detail: null,
      execution_metrics: completeExecutionMetrics(),
    }).execution_metrics?.admission?.budget_policy).toBe("adaptive_local")
    expect(parseFrontierResponse({
      status: "ok",
      points: [{ total_objective: 1 }],
      point_summaries: [{
        total_objective: 1,
        constraints: { loss: 1 },
        effective_bounds: { loss: { kind: "max", bound: 1 } },
        lambdas: { loss: 0.1 },
        converged: true,
        iterations: null,
        cd_iterations: null,
        clamp_rate: null,
        history: null,
        scenario_value_stats: null,
        scenario_value_histogram: null,
        factor_tables: null,
        warning: null,
        frontier_error: null,
      }],
      n_points: 2001,
      points_returned: 1,
      constraint_names: ["loss"],
      swept_axes: ["loss"],
      points_limit: 2000,
      points_truncated: true,
      job_id: null,
    }).points_truncated).toBe(true)
    expect(selected.lambdas.loss).toBe(0.3)
    expect(saved.path).toBe("optimiser_output.py")
  })

  it("rejects frontier payloads without one point summary per point", () => {
    const summary = {
      total_objective: 1,
      constraints: { loss: 1 },
      effective_bounds: { loss: { kind: "max", bound: 1 } },
      lambdas: { loss: 0.1 },
      converged: true,
      iterations: null,
      cd_iterations: null,
      clamp_rate: null,
      history: null,
      scenario_value_stats: null,
      scenario_value_histogram: null,
      factor_tables: null,
      warning: null,
      frontier_error: null,
    }
    expect(() =>
      parseFrontierResponse({
        status: "ok",
        points: [{ total_objective: 1 }, { total_objective: 2 }],
        point_summaries: [summary],
        n_points: 2,
        points_returned: 2,
        constraint_names: ["loss"],
        swept_axes: ["loss"],
        points_limit: 2000,
        points_truncated: false,
        job_id: null,
      }),
    ).toThrow(
      "parseOptimiserStatusResponse: expected result.point_summaries to hold one summary per point, got 1 for 2 points",
    )
  })

  it("rejects a frontier point summary missing a field but accepts it as null", () => {
    const summary = {
      total_objective: 1,
      constraints: { loss: 1 },
      effective_bounds: { loss: { kind: "max", bound: 1 } },
      lambdas: { loss: 0.1 },
      converged: true,
      iterations: null,
      cd_iterations: null,
      clamp_rate: null,
      history: null,
      scenario_value_stats: null,
      scenario_value_histogram: null,
      factor_tables: null,
      warning: null,
      frontier_error: null,
    }
    const payload = (pointSummary: Record<string, unknown>) => ({
      status: "ok",
      points: [{ total_objective: 1 }],
      point_summaries: [pointSummary],
      n_points: 1,
      points_returned: 1,
      constraint_names: ["loss"],
      swept_axes: ["loss"],
      points_limit: 2000,
      points_truncated: false,
      job_id: null,
    })
    const { warning: _warning, ...withoutWarning } = summary; void _warning
    expect(() => parseFrontierResponse(payload(withoutWarning))).toThrow(
      "OptimiserFrontierStatusResponse: invalid contract at /result/point_summaries/0/warning: required",
    )
    expect(parseFrontierResponse(payload(summary)).point_summaries[0]?.warning).toBeNull()
    const { effective_bounds: _bounds, ...withoutBounds } = summary; void _bounds
    expect(() => parseFrontierResponse(payload(withoutBounds))).toThrow(
      "OptimiserFrontierStatusResponse: invalid contract at /result/point_summaries/0/effective_bounds: required",
    )
    expect(() => parseFrontierResponse(payload({
      ...summary,
      effective_bounds: { loss: { kind: "min_pct", bound: 1 } },
    }))).toThrow("OptimiserFrontierStatusResponse: invalid contract at /result/point_summaries/0/effective_bounds/loss/kind: enum")
  })

  it("parses every field of a fully populated frontier point summary", () => {
    const stats = {
      mean: 1.08, std: 0.03, min: 0.95, max: 1.2, p5: 0.99, p25: 1.03,
      p50: 1.07, p75: 1.12, p95: 1.18, pct_increase: 0.8, pct_decrease: 0.2,
    }
    const summary = {
      total_objective: 151,
      constraints: { loss: 0.93 },
      effective_bounds: { loss: { kind: "max", bound: 1 } },
      lambdas: { loss: 0.4 },
      converged: false,
      iterations: 19,
      cd_iterations: 4,
      clamp_rate: 0.01,
      history: [{
        iteration: 1, total_objective: 150, max_lambda_change: 0.1,
        all_constraints_satisfied: null, lambdas: {}, total_constraints: {},
      }],
      scenario_value_stats: stats,
      scenario_value_histogram: { counts: [1, 2], edges: [0.9, 1.0, 1.1] },
      factor_tables: { region: [{ __factor_group__: "North", optimal_scenario_value: 1.05 }] },
      warning: "Solver did not converge.",
      frontier_error: "Frontier unavailable: example",
    }
    const parsed = parseFrontierResponse({
      status: "ok",
      points: [{ total_objective: 151 }],
      point_summaries: [summary],
      n_points: 1,
      points_returned: 1,
      constraint_names: ["loss"],
      swept_axes: ["loss"],
      points_limit: 2000,
      points_truncated: false,
      job_id: null,
    }).point_summaries[0]

    expect(parsed).toEqual(summary)
    expect(() =>
      parseFrontierResponse({
        status: "ok",
        points: [{ total_objective: 151 }],
        point_summaries: [{ ...summary, scenario_value_histogram: { counts: ["x"], edges: [] } }],
        n_points: 1,
        points_returned: 1,
        constraint_names: ["loss"],
        swept_axes: ["loss"],
        points_limit: 2000,
        points_truncated: false,
        job_id: null,
      }),
    ).toThrow(
      "OptimiserFrontierStatusResponse: invalid contract at /result/point_summaries/0/scenario_value_histogram/counts/0: type",
    )
  })

  it("rejects malformed execution metric pressure and admission fields", () => {
    const metrics = executionMetricsFixture()

    expect(() =>
      parsePreviewNodeResponse({
        ...loadUiContractFixture<Record<string, unknown>>("preview_node"),
        execution_metrics: {
          ...metrics,
          admission: {
            ...metrics.admission,
            budget_policy: 42,
          },
        },
      }),
    ).toThrow(/budget_policy/i)

    const completeMetrics = completeExecutionMetrics() as typeof metrics
    expect(() =>
      parseOptimiserStatusResponse({
        ...loadUiContractFixture<Record<string, unknown>>("optimiser_status_response"),
        execution_metrics: {
          ...completeMetrics,
          memory_pressure_events: [
            {
              ...completeMetrics.memory_pressure_events[0],
              pressure_ratio: "high",
            },
          ],
        },
      }),
    ).toThrow(
      "OptimiserStatusResponse: invalid contract at /execution_metrics/memory_pressure_events/0/pressure_ratio: type",
    )
  })

  it("parses git action payloads", () => {
    const archived = gitArchive(loadUiContractFixture("git_archive_response"))
    const deleted = gitDeleteBranch(loadUiContractFixture("git_delete_branch_response"))

    expect(archived.archived_as).toContain("archive/")
    expect(deleted.branch).toContain("feat/")
  })

  it("rejects scenario_value_histogram payloads missing counts or edges", () => {
    // CLAUDE.md: do not silently fall back.  A present histogram object
    // missing one of its required arrays is a contract violation; throw so
    // we surface the bug instead of rendering with empty arrays.
    const selected = {
      status: "ok",
      point_index: 0,
      total_objective: 1,
      constraints: { loss: 1 },
      baseline_objective: 1,
      baseline_constraints: { loss: 1 },
      effective_bounds: { loss: { kind: "max", bound: 1 } },
      lambdas: { loss: 0.1 },
      converged: true,
      iterations: null,
      cd_iterations: null,
      factor_tables: {},
      history: null,
      warning: null,
      scenario_value_stats: null,
      scenario_value_histogram: null,
      clamp_rate: null,
      combined_factor_bounds: null,
      error: null,
    }
    expect(() =>
      parseFrontierSelectResponse({ ...selected, scenario_value_histogram: { counts: [1, 2] } }),
    ).toThrow("OptimiserFrontierSelectResponse: invalid contract at /scenario_value_histogram/edges: required")
    expect(() =>
      parseFrontierSelectResponse({ ...selected, scenario_value_histogram: { edges: [0, 1, 2] } }),
    ).toThrow("OptimiserFrontierSelectResponse: invalid contract at /scenario_value_histogram/counts: required")
  })

  it("rejects malformed optimiser lambda maps", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    const result = fixture.result as Record<string, unknown>

    expect(() =>
      parseOptimiserStatusResponse({
        ...fixture,
        result: {
          ...result,
          lambdas: { loss: "bad" },
        },
      }),
    ).toThrow("OptimiserStatusResponse: invalid contract at /result/lambdas/loss: type")
  })

  it("parses a Data Input input-cache snapshot with tables null", () => {
    const parsed = parseInputCacheSnapshotResponse({
      schema_version: 1,
      identity_digest: "digest-1",
      state: "ready",
      freshness: "fresh",
      generation: null,
      tables: null,
    })

    expect(parsed.tables).toBeNull()

    const parsedAbsent = parseInputCacheSnapshotResponse({
      schema_version: 1,
      identity_digest: "digest-1",
      state: "missing",
      freshness: "unknown",
      generation: null,
    })

    expect(parsedAbsent.tables).toBeNull()
  })

  it("parses an API Input input-cache snapshot with two tables", () => {
    const parsed = parseInputCacheSnapshotResponse({
      schema_version: 1,
      identity_digest: "digest-2",
      state: "ready",
      freshness: "fresh",
      generation: null,
      tables: [
        {
          label: "drivers",
          identity_digest: "digest-drivers",
          state: "ready",
          freshness: "fresh",
          generation: {
            generation_id: "gen-1",
            row_count: 10,
            column_count: 3,
            columns: { premium: "Float64" },
            size_bytes: 2048,
            created_at: 123,
            build_class: "bounded",
          },
        },
        {
          label: "vehicles",
          identity_digest: "digest-vehicles",
          state: "ready",
          freshness: "fresh",
          generation: {
            generation_id: "gen-2",
            row_count: 5,
            column_count: 2,
            columns: { make: "Utf8" },
            size_bytes: 1024,
            created_at: 456,
            build_class: "bounded",
          },
        },
      ],
    })

    expect(parsed.tables).toHaveLength(2)
    expect(parsed.tables?.[0]?.label).toBe("drivers")
    expect(parsed.tables?.[1]?.generation?.row_count).toBe(5)
  })

  it("rejects an input-cache table entry with a bad state", () => {
    expect(() =>
      parseInputCacheSnapshotResponse({
        schema_version: 1,
        identity_digest: "digest-3",
        state: "ready",
        freshness: "fresh",
        generation: null,
        tables: [
          {
            label: "drivers",
            identity_digest: "digest-drivers",
            state: "not-a-real-state",
            freshness: "fresh",
            generation: null,
          },
        ],
      }),
    ).toThrow(/state/i)
  })

  it("rejects malformed submodel graph payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("submodel_graph_response")

    expect(() =>
      parseSubmodelGraphResponse({
        ...fixture,
        graph: {
          ...(fixture.graph as Record<string, unknown>),
          nodes: "bad",
        },
      }),
    ).toThrow(/nodes/i)
  })

  it("parses mlflow settings and test-connection payloads", () => {
    const settings = mlflowSettings(loadUiContractFixture("mlflow_settings_response"))
    expect(settings.section_present).toBe(true)
    expect(settings.tracking_uri).toBe("http://localhost:5000")
    expect(settings.folder).toBe("team-runs")
    expect(settings.resolved_folder).toBe("C:/proj/team-runs")
    expect(settings.detail).toBe("")

    const probe = mlflowTestConnection(
      loadUiContractFixture("mlflow_test_connection_response"),
    )
    expect(probe.ok).toBe(false)
    expect(probe.category).toBe("connectivity")
  })

  it("rejects malformed mlflow settings and test-connection payloads", () => {
    const settings = loadUiContractFixture<Record<string, unknown>>("mlflow_settings_response")
    expect(() =>
      mlflowSettings({ ...settings, section_present: "yes" }),
    ).toThrow(/section_present/i)
    expect(() =>
      mlflowSettings({ ...settings, resolved_folder: 42 }),
    ).toThrow(/resolved_folder/i)

    const probe = loadUiContractFixture<Record<string, unknown>>(
      "mlflow_test_connection_response",
    )
    expect(() => mlflowTestConnection({ ...probe, ok: "no" })).toThrow(/\/ok: type/)
    expect(() =>
      mlflowTestConnection({ ...probe, category: "offline" }),
    ).toThrow(/category/i)
  })

  it("rejects malformed mlflow destinations payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>(
      "mlflow_destinations_response",
    )
    const entries = fixture.destinations as Array<Record<string, unknown>>
    const withFirstEntry = (patch: Record<string, unknown>) => ({
      ...fixture,
      destinations: [{ ...entries[0], ...patch }, ...entries.slice(1)],
    })

    expect(() =>
      mlflowDestinations({
        ...fixture,
        mlflow_installed: "yes",
      }),
    ).toThrow(/mlflow_installed/i)

    expect(() =>
      mlflowDestinations({
        ...fixture,
        destinations: "none",
      }),
    ).toThrow(/MlflowDestinationsResponse: invalid contract at \/destinations: type/)

    expect(() => mlflowDestinations(withFirstEntry({ key: "file" }))).toThrow(
      /key/i,
    )

    expect(() => mlflowDestinations(withFirstEntry({ probed: "yes" }))).toThrow(
      /probed/i,
    )

    expect(() => mlflowDestinations(withFirstEntry({ configured: "yes" }))).toThrow(
      /configured/i,
    )

    expect(() =>
      mlflowDestinations(withFirstEntry({ config_source: "registry" })),
    ).toThrow(/config_source/i)

    expect(() =>
      mlflowDestinations(withFirstEntry({ category: "offline" })),
    ).toThrow(/category/i)
  })

  it("parses every working-branch readiness state and detached commit context", () => {
    const base = makeGitWorkingBranch({ current_branch: "", identity_set: false })

    for (const state of [
      "no-repository",
      "unset",
      "detached",
      "invalid",
      "divergent",
      "ready",
    ] as const) {
      const parsed = gitWorkingBranch({
        ...base,
        state,
        head_sha: state === "detached" ? "a".repeat(40) : null,
      })
      expect(parsed.state).toBe(state)
      if (state === "detached") expect(parsed.head_sha).toBe("a".repeat(40))
    }
  })

  it("rejects an unknown working-branch readiness state", () => {
    expect(() =>
      gitWorkingBranch({ ...makeGitWorkingBranch(), state: "missing" }),
    ).toThrow("GitWorkingBranchResponse: invalid contract at /state: enum")
  })

  it("rejects a readiness payload that omits the storage surface", () => {
    // The server always sends it; its absence is drift, not an older backend.
    const { storage: _storage, ...withoutStorage } = makeGitWorkingBranch({ state: "ready", current_branch: "dev" })
    expect(() => gitWorkingBranch(withoutStorage)).toThrow(
      "GitWorkingBranchResponse: invalid contract at /storage: required",
    )
  })

  it("parses a bound, synced storage surface", () => {
    const parsed = gitWorkingBranch(makeGitWorkingBranch({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      storage_remote: "https://github.com/org/repo.git",
      sync: { state: "synced", pending: 0, failure: null, message: null },
    }))
    expect(parsed.storage).toBe("bound")
    expect(parsed.storage_remote).toBe("https://github.com/org/repo.git")
    expect(parsed.sync).toEqual({ state: "synced", pending: 0, failure: null, message: null })
  })

  it("parses a failed sync with its failure kind and message", () => {
    const parsed = gitWorkingBranch(makeGitWorkingBranch({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      sync: { state: "failed", pending: 2, failure: "rejected", message: "Push was rejected" },
    }))
    expect(parsed.sync).toEqual({
      state: "failed",
      pending: 2,
      failure: "rejected",
      message: "Push was rejected",
    })
  })

  it("rejects an unknown storage state", () => {
    expect(() =>
      gitWorkingBranch({
        ...makeGitWorkingBranch({ state: "ready", current_branch: "dev" }),
        storage: "bogus",
      }),
    ).toThrow("GitWorkingBranchResponse: invalid contract at /storage: enum")
  })

  it("parses the accepted bind-storage response", () => {
    const parsed = gitBindStorage({
      outcome: "pending",
      remote_url: "https://github.com/org/repo.git",
      message: "Saving this project to storage - you can keep working.",
    })
    expect(parsed).toEqual({
      outcome: "pending",
      remote_url: "https://github.com/org/repo.git",
      message: "Saving this project to storage - you can keep working.",
    })
  })

  it("rejects the old synchronous bind outcomes", () => {
    // Binding is asynchronous: the route only ever accepts the request, and
    // the real outcome arrives on the readiness response's storage_bind.
    for (const outcome of ["adopted", "restart-required"]) {
      expect(() =>
        gitBindStorage({
          outcome,
          remote_url: "https://github.com/org/repo.git",
          message: "x",
        }),
      ).toThrow("GitBindStorageResponse: invalid contract at /outcome: const")
    }
  })

  it("parses background bind progress, null when no bind runs", () => {
    const running = gitWorkingBranch(makeGitWorkingBranch({
      state: "ready",
      current_branch: "dev",
      storage_bind: {
        state: "running",
        outcome: null,
        message: null,
        claim: null,
        remote_url: "uc://workspace.default.projects/demo",
      },
    }))
    expect(running.storage_bind?.state).toBe("running")
    expect(running.storage_bind?.remote_url).toBe("uc://workspace.default.projects/demo")

    const failed = gitWorkingBranch(makeGitWorkingBranch({
      state: "ready",
      current_branch: "dev",
      storage_bind: {
        state: "failed",
        outcome: null,
        message: "in use by app 'other-app'",
        claim: { app_name: "other-app", user: null, refreshed_at: null, message: "in use" },
        remote_url: null,
      },
    }))
    expect(failed.storage_bind?.claim?.app_name).toBe("other-app")

    const absent = gitWorkingBranch(makeGitWorkingBranch({ state: "ready", current_branch: "dev" }))
    expect(absent.storage_bind).toBeNull()
  })

  it("parses fork provenance on the readiness surface, defaulting to null", () => {
    const withLineage = gitWorkingBranch(makeGitWorkingBranch({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      storage_forked_from: "uc://workspace.default.projects/demo",
    }))
    expect(withLineage.storage_forked_from).toBe("uc://workspace.default.projects/demo")
    const without = gitWorkingBranch(makeGitWorkingBranch({ state: "ready", current_branch: "dev" }))
    expect(without.storage_forked_from).toBeNull()
  })

  it("parses a fork-storage response", () => {
    const parsed = gitForkStorage({
      outcome: "forked",
      target_url: "uc://workspace.default.projects/demo-fork",
      parent_url: "uc://workspace.default.projects/demo",
      parent_generation: 7,
      message: "Forked generation 7. Bind the new location to work on the copy.",
    })
    expect(parsed.parent_generation).toBe(7)
    expect(parsed.target_url).toBe("uc://workspace.default.projects/demo-fork")
  })

  // --- P7 remote catch-up surface: per-leg divergence, fast-forward, branch-away,
  //     and the two 409 advisory bodies (push rejection + milestone fork). ---

  it("parses a remotes response with per-leg ahead/behind detail", () => {
    const parsed = gitRemotes({
      working_branch: "dev",
      remotes: [
        {
          name: "origin",
          url: "git@example.com:x.git",
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "diverged", ahead: 2, behind: 1 },
        },
        // A remote we have no tracking detail for carries null legs.
        { name: "backup", url: null, working: null, ledger: null },
      ],
    })

    expect(parsed.remotes[0].working).toEqual({ status: "behind", ahead: 0, behind: 1 })
    expect(parsed.remotes[0].ledger?.status).toBe("diverged")
    expect(parsed.remotes[1].working).toBeNull()
    expect(parsed.remotes[1].ledger).toBeNull()
  })

  it("accepts every known leg status", () => {
    for (const status of ["untracked", "unknown", "synced", "ahead", "behind", "diverged"]) {
      const parsed = gitRemotes({
        working_branch: null,
        remotes: [{ name: "origin", url: null, working: { status, ahead: null, behind: null }, ledger: null }],
      })
      expect(parsed.remotes[0].working?.status).toBe(status)
    }
  })

  it("rejects an unknown leg status", () => {
    expect(() =>
      gitRemotes({
        working_branch: null,
        remotes: [{ name: "origin", url: null, working: { status: "wat", ahead: null, behind: null }, ledger: null }],
      }),
    ).toThrow("GitRemotesResponse: invalid contract at /remotes/0/working/status: enum")
  })

  it("parses a fast-forward response with the advanced refs", () => {
    const parsed = gitFastForward({
      remote: "origin",
      working_branch: "dev",
      fast_forwarded: ["dev", "dev-save"],
    })

    expect(parsed.remote).toBe("origin")
    expect(parsed.fast_forwarded).toEqual(["dev", "dev-save"])
  })

  it("parses required bootstrap metadata for either bootstrap outcome", () => {
    const base = loadUiContractFixture<Record<string, unknown>>("git_push_response")

    expect(gitPush({ ...base, bootstrapped_default: true })).toMatchObject({
      default_branch: base.default_branch,
      bootstrapped_default: true,
    })
    expect(
      gitPush({ ...base, bootstrapped_default: false }).bootstrapped_default,
    ).toBe(false)
  })

  it("rejects missing or malformed bootstrap metadata instead of inventing it", () => {
    const base = loadUiContractFixture<Record<string, unknown>>("git_push_response")

    expect(() => gitPush({ ...base, default_branch: undefined })).toThrow(
      /default_branch/i,
    )
    expect(() => gitPush({ ...base, bootstrapped_default: undefined })).toThrow(
      /bootstrapped_default/i,
    )
    expect(() => gitPush({ ...base, default_branch: 42 })).toThrow(/default_branch/i)
    expect(() => gitPush({ ...base, bootstrapped_default: "false" })).toThrow(/bootstrapped_default/i)
  })

  it("rejects a fast-forward response missing working_branch", () => {
    expect(() =>
      gitFastForward({ remote: "origin", fast_forwarded: [] }),
    ).toThrow(/working_branch/i)
  })

  it("parses a branch-away response with the set-aside name", () => {
    const parsed = gitBranchAway({
      working_branch: "dev",
      set_aside_as: "dev-2026-06-21",
    })

    expect(parsed.set_aside_as).toBe("dev-2026-06-21")
  })

  it("parses a 409 push-rejection body, including a rewrite flag", async () => {
    const parsed = await parseGitPushRejection({
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: { status: "behind", ahead: 0, behind: 1 },
      message: "Remote has work you don't.",
      is_rewrite: true,
    })

    expect(parsed?.status).toBe("rejected_diverged")
    expect(parsed?.working.status).toBe("diverged")
    expect(parsed?.ledger?.status).toBe("behind")
    expect(parsed?.is_rewrite).toBe(true)
  })

  it("returns null for a push-rejection body of the wrong status", async () => {
    await expect(parseGitPushRejection({ status: "ok" })).resolves.toBeNull()
  })

  it("throws for a malformed matching push-rejection body", async () => {
    await expect(parseGitPushRejection({ status: "rejected_diverged" })).rejects.toThrow(
      "GitPushRejection: invalid contract at /remote: required",
    )
    await expect(parseGitPushRejection({
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: null,
      message: "Remote has work you don't.",
      is_rewrite: "yes",
    })).rejects.toThrow("GitPushRejection: invalid contract at /is_rewrite: type")
  })

  it("returns null for a non-object push-rejection discriminator", async () => {
    await expect(parseGitPushRejection(null)).resolves.toBeNull()
  })

  it("parses a 409 milestone-fork body", async () => {
    const parsed = await parseGitMilestoneFork({
      status: "would_fork",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 1 },
      message: "Committing here would fork the published line.",
    })

    expect(parsed?.status).toBe("would_fork")
    expect(parsed?.working.status).toBe("diverged")
  })

  it("returns null for a milestone-fork body of the wrong status", async () => {
    await expect(parseGitMilestoneFork({ status: "ok" })).resolves.toBeNull()
  })

  it("throws for a malformed matching milestone-fork body", async () => {
    await expect(parseGitMilestoneFork({
      status: "would_fork",
      remote: "origin",
    })).rejects.toThrow("GitMilestoneFork: invalid contract at /working: required")
  })

  it("parses a create-working-branch response", () => {
    const parsed = gitCreateWorkingBranch({
      working_branch: "feature",
      moved: false,
      switched: true,
      last_save_sha: null,
    })

    expect(parsed.working_branch).toBe("feature")
    expect(parsed.switched).toBe(true)
    expect(parsed.last_save_sha).toBeNull()
  })

  it("parses git prefs and requires skip_switch_confirm", () => {
    expect(gitPrefs({ skip_switch_confirm: true }).skip_switch_confirm).toBe(true)
    expect(() => gitPrefs({})).toThrow("GitPrefs: invalid contract at /skip_switch_confirm: required")
  })

  it("parses shared client trust-boundary payloads", () => {
    expect(sessionStatus({ ok: true })).toEqual({ ok: true })
    expect(parseOutputAssembleDryRunResponse({ status: "ok", document: [{ premium: 1 }], row_count: 1, error: null })).toMatchObject({ status: "ok", row_count: 1 })
    expect(parseJsonCacheSchemaInferenceResponse({ tables: [{ name: "drivers" }] }).tables).toEqual([{ name: "drivers" }])
    expect(mlflowExperiments([{ experiment_id: "1", name: "pricing" }])[0]?.name).toBe("pricing")
    expect(mlflowRuns([{ run_id: "run-1", run_name: "baseline", status: "FINISHED", start_time: null, metrics: { auc: 0.9 }, params: {}, artifacts: ["model"] }])[0]?.metrics.auc).toBe(0.9)
    expect(mlflowModels([{ name: "pricing", latest_versions: [{ version: "1", status: "READY", run_id: "run-1" }] }])[0]?.latest_versions).toHaveLength(1)
    expect(mlflowModelVersions([{ version: "1", run_id: "run-1", status: "READY", creation_timestamp: null, description: "baseline", params: {}, aliases: [] }])[0]?.description).toBe("baseline")
    expect(
      mlflowModelVersions([
        { version: "1", run_id: "run-1", status: "READY", creation_timestamp: 1, description: "", params: {}, aliases: ["champion"] },
      ])[0]?.aliases,
    ).toEqual(["champion"])
    expect(() =>
      mlflowModelVersions([{ version: "1", run_id: "r", status: "READY", creation_timestamp: null, description: "", params: {}, aliases: [1] }]),
    ).toThrow(/aliases/)
    expect(browseFiles({ dir: "/", items: [{ name: "data", path: "/data", type: "directory", size: null }] }).items[0]?.size).toBeNull()
    expect(browseFiles({ dir: "/", items: [{ name: "a.csv", path: "/a.csv", type: "file", size: 12 }] }).items[0]?.size).toBe(12)
    // The server sends every file field, a directory's null size included.
    expect(() => browseFiles({ dir: "/", items: [{ name: "data", path: "/data", type: "directory" }] })).toThrow(
      "BrowseFilesResponse: invalid contract at /items/0/size: required",
    )
    expect(gitGraph({
      working_branch: "main",
      order: ["main"],
      branches: [{ name: "main", is_archived: false, is_current: true, tip_sha: "a", fork_point_sha: null, fork_of: null, fork_source_sha: null, fork_credit_sha: null, truncated: false, entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "2026-01-01", version_label: null, is_root: true, parents: [] }] }],
    }).branches[0]?.entries[0]?.parents).toEqual([])
  })

  it.each([
    ["session boolean", () => sessionStatus({ ok: "yes" })],
    ["output document", () => parseOutputAssembleDryRunResponse({ status: "ok", document: {}, row_count: 1 })],
    ["inferred nested table", () => parseJsonCacheSchemaInferenceResponse({ tables: ["bad"] })],
    ["file item type", () => browseFiles({ dir: "/", items: [{ name: "x", path: "/x", type: "link", size: null }] })],
    ["git graph nested parents", () => gitGraph({ working_branch: null, order: [], branches: [{ name: "main", is_archived: false, is_current: true, tip_sha: "a", fork_point_sha: null, fork_of: null, fork_source_sha: null, fork_credit_sha: null, truncated: false, entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "today", version_label: null, is_root: true, parents: [1] }] }] })],
  ])("rejects malformed shared client payload: %s", (_name, parse) => {
    expect(parse).toThrow()
  })

  it("rejects a malformed MLflow discovery listing at the offending field", () => {
    // Each payload is complete except for the one field under test.
    const run = { run_id: "r", run_name: "n", status: "FINISHED", start_time: null, metrics: { auc: 0.9 }, params: {}, artifacts: [] }
    const version = { version: "1", run_id: "r", status: "READY", creation_timestamp: null, description: "", params: {}, aliases: [] }
    const { description: _description, ...versionWithoutDescription } = version

    expect(() => mlflowExperiments([{ experiment_id: "1" }])).toThrow(
      "MlflowExperimentList: invalid contract at /0/name: required",
    )
    expect(() => mlflowRuns([{ ...run, metrics: { auc: "high" } }])).toThrow(
      "MlflowRunList: invalid contract at /0/metrics/auc: type",
    )
    expect(() => mlflowRuns([{ ...run, artifacts: [1] }])).toThrow(
      "MlflowRunList: invalid contract at /0/artifacts/0: type",
    )
    expect(() => mlflowModels([{ name: "m", latest_versions: [{ version: "1", status: "READY" }] }])).toThrow(
      "MlflowModelList: invalid contract at /0/latest_versions/0/run_id: required",
    )
    expect(() => mlflowModelVersions([versionWithoutDescription])).toThrow(
      "MlflowModelVersionList: invalid contract at /0/description: required",
    )
  })

})
