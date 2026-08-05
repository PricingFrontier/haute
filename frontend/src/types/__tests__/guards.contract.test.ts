import { describe, expect, it } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  parseApplyOptimiserResponse,
  parseDissolveSubmodelResponse,
  parseExploreRunResponse,
  parseExploreStatusResponse,
  parseFrontierAutoRangeResponse,
  parseFrontierAutoRangeStatusResponse,
  parseFrontierResponse,
  parseFrontierSelectResponse,
  parseGitArchiveResponse,
  gitStorageClaimFromDetail,
  parseGitBindStorageResponse,
  parseGitForkStorageResponse,
  parseGitDeleteBranchResponse,
  parseGitMoveResponse,
  parseGitPushResponse,
  parseGitRemotesResponse,
  parseGitWorkingBranchResponse,
  parseGitFastForwardResponse,
  parseGitBranchAwayResponse,
  parseGitPushRejection,
  parseGitMilestoneFork,
  parseGitCreateWorkingBranchResponse,
  parseGitGraphResponse,
  parseGitPrefs,
  parseHauteSessionResponse,
  parseJsonCacheDeleteResponse,
  parseJsonCacheBuildResponse,
  parseJsonCacheStatusResponse,
  parseJsonCacheSchemaInferenceResponse,
  parseFileListResponse,
  parseMlflowExperiments,
  parseMlflowCheckResponse,
  parseMlflowLogResponse,
  parseMlflowModels,
  parseMlflowModelVersions,
  parseMlflowRuns,
  parseOutputAssembleDryRunResponse,
  parseOptimiserEstimateResponse,
  parseOptimiserStatusResponse,
  parsePreviewNodeResponse,
  parseSavePipelineResponse,
  parseSaveOptimiserResponse,
  parseTraceResponse,
  parseSubmodelCreateResponse,
  parseSubmodelGraphResponse,
  parseSolveOptimiserResponse,
  parseTrainEstimateResponse,
  parseTrainFeatureSelection,
  parseTrainResponse,
  parseTrainStatusResponse,
  parseExecutionStrategyDiagnostic,
  parseUtilityDeleteResponse,
  parseUtilityListResponse,
  parseUtilityReadResponse,
  parseUtilityWriteResponse,
} from "../guards"

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
  }
}

describe("parseExecutionStrategyDiagnostic", () => {
  it.each([
    ["projected", "projected"],
    ["schema-all-except", "projected"],
    ["full-width-admitted-eager", "admitted_eager"],
    ["unprojected-streaming-boundary", "boundary"],
    ["materialisation-boundary", "boundary"],
    ["unsupported", "rejected"],
    ["not-planned", "not_planned"],
  ])("accepts the V1 %s strategy mapping", (strategy, status) => {
    expect(parseExecutionStrategyDiagnostic(executionStrategyFixture({ strategy, status }))?.status).toBe(status)
  })

  it("throws for malformed fields, caps, wrappers, and ordering in a matching version", () => {
    expect(() => parseExecutionStrategyDiagnostic(
      executionStrategyFixture({ reason_code: 3 }),
    )).toThrow()
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
    const diagnostic = parseExecutionStrategyDiagnostic(executionStrategyFixture({ unknown_additive_field: true, reasons: { state: "available", total_count: 2, items: [{ reason_code: "same" }, { reason_code: "same" }] } }))
    expect(diagnostic?.reasons.items).toHaveLength(2)
    expect(parseExecutionStrategyDiagnostic(executionStrategyFixture({ schema_version: 2 }))).toBeNull()
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
    const parsed = parseGitMoveResponse({
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
      parseGitMoveResponse({ sha: "abc", short_sha: "abc", is_detached: true }),
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
    expect(parsed.train_loss.learn).toBe(0.1)
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

  it("parses explore run and status responses as cache descriptors", () => {
    const run = parseExploreRunResponse(loadUiContractFixture("explore_run_response"))
    const status = parseExploreStatusResponse(loadUiContractFixture("explore_status_response"))

    expect(run.cached).toBe(true)
    expect(run.result?.row_count).toBe(150)
    expect(run.result?.column_count).toBe(3)
    expect(run.result?.dataframe_cache_key).toContain("explore_dataset")
    expect(run.result?.overview_summary.data_quality.issue_count).toBe(0)
    expect(status.result?.dataframe_cache_key).toBe(run.result?.dataframe_cache_key)
  })

  it("rejects malformed explore result payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
    const result = fixture.result as Record<string, unknown>

    expect(() =>
      parseExploreStatusResponse({
        ...fixture,
        result: {
          ...result,
          row_count: "bad",
        },
      }),
    ).toThrow(/parseExploreCacheReport/i)
  })

  describe("parseExploreColumnStat (via parseExploreCacheReport.columns)", () => {
    function withColumns(columns: unknown): Record<string, unknown> {
      const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
      const result = fixture.result as Record<string, unknown>
      return { ...fixture, result: { ...result, columns } }
    }

    function withoutResultField(key: string): Record<string, unknown> {
      const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
      const result = fixture.result as Record<string, unknown>
      const { [key]: _removed, ...nextResult } = result
      void _removed
      return { ...fixture, result: nextResult }
    }

    it("parses a fully populated column stat", () => {
      const parsed = parseExploreStatusResponse(
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
      const parsed = parseExploreStatusResponse(
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

    it("throws when columns is missing from a cache report", () => {
      expect(() => parseExploreStatusResponse(withoutResultField("columns"))).toThrow(
        /parseExploreCacheReport/i,
      )
    })

    it.each(["source", "row_count", "column_count", "generated_at"])(
      "throws when %s is missing from a cache report",
      (field) => {
        expect(() => parseExploreStatusResponse(withoutResultField(field))).toThrow(
          /parseExploreCacheReport/i,
        )
      },
    )

    it("throws when overview_summary is missing from a cache report", () => {
      expect(() => parseExploreStatusResponse(withoutResultField("overview_summary"))).toThrow(
        /parseExploreOverviewSummary/i,
      )
    })

    it("throws when distinct_count is missing", () => {
      expect(() =>
        parseExploreStatusResponse(
          withColumns([
            { name: "minimal", dtype: "Int64", kind: "Numeric", null_count: 0 },
          ]),
        ),
      ).toThrow(/parseExploreColumnStat/i)
    })

    it("throws when overview summary issue severity is invalid", () => {
      const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      expect(() =>
        parseExploreStatusResponse({
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
      const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      const parsed = parseExploreStatusResponse({
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
      const fixture = loadUiContractFixture<Record<string, unknown>>("explore_status_response")
      const result = fixture.result as Record<string, unknown>
      const overview = result.overview_summary as Record<string, unknown>

      expect(() =>
        parseExploreStatusResponse({
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
        parseExploreStatusResponse(
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
    const parsed = parseTrainStatusResponse({
      ...loadUiContractFixture<Record<string, unknown>>("train_status_response"),
      execution_metrics: executionMetricsFixture(),
    })

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
      execution_metrics: executionMetricsFixture(),
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

    expect(created.submodel_file).toBe("pricing.py")
    expect(created.graph.edges[0].targetPort).toBe("base")
    expect(created.graph.edges[1].sourcePort).toBe("quotes")
    expect(loaded.submodel_name).toBe("pricing")
    expect(dissolved.graph.nodes).toHaveLength(2)
  })

  it("parses modelling preflight payloads", () => {
    const mlflow = parseMlflowCheckResponse(loadUiContractFixture("mlflow_check_response"))
    const estimate = parseTrainEstimateResponse(loadUiContractFixture("train_estimate_response"))
    const log = parseMlflowLogResponse(loadUiContractFixture("mlflow_log_response"))

    expect(mlflow.mlflow_installed).toBe(true)
    expect(mlflow.mlflow_importable).toBe(true)
    expect(mlflow.tracking_configured).toBe(true)
    expect(mlflow.detail).toBe("")
    expect(estimate.estimated_mb).toBe(12.5)
    expect(log.run_id).toBe("run-123")
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
      execution_metrics: executionMetricsFixture(),
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
      execution_metrics: executionMetricsFixture(),
    }).execution_metrics?.admission?.budget_policy).toBe("adaptive_local")
    expect(parseFrontierResponse({
      status: "ok",
      points: [{ total_objective: 1 }],
      n_points: 2001,
      points_returned: 1,
      constraint_names: ["loss"],
      points_limit: 2000,
      points_truncated: true,
    }).points_truncated).toBe(true)
    expect(selected.lambdas.loss).toBe(0.3)
    expect(saved.path).toBe("optimiser_output.py")
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

    expect(() =>
      parseOptimiserStatusResponse({
        ...loadUiContractFixture<Record<string, unknown>>("optimiser_status_response"),
        execution_metrics: {
          ...metrics,
          memory_pressure_events: [
            {
              ...metrics.memory_pressure_events[0],
              pressure_ratio: "high",
            },
          ],
        },
      }),
    ).toThrow(/pressure_ratio/i)
  })

  it("parses utility response payloads", () => {
    const listed = parseUtilityListResponse(loadUiContractFixture("utility_list_response"))
    const read = parseUtilityReadResponse(loadUiContractFixture("utility_read_response"))
    const written = parseUtilityWriteResponse(loadUiContractFixture("utility_write_response"))
    const deleted = parseUtilityDeleteResponse(loadUiContractFixture("utility_delete_response"))

    expect(listed.files[0]?.module).toBe("helpers")
    expect(read.content).toContain("helper")
    expect(written.import_line).toContain("utility.helpers")
    expect(deleted.module).toBe("helpers")
  })

  it("parses git action payloads", () => {
    const archived = parseGitArchiveResponse(loadUiContractFixture("git_archive_response"))
    const deleted = parseGitDeleteBranchResponse(loadUiContractFixture("git_delete_branch_response"))

    expect(archived.archived_as).toContain("archive/")
    expect(deleted.branch).toContain("feat/")
  })

  it("rejects scenario_value_histogram payloads missing counts or edges", () => {
    // CLAUDE.md: do not silently fall back.  A present histogram object
    // missing one of its required arrays is a contract violation; throw so
    // we surface the bug instead of rendering with empty arrays.
    expect(() =>
      parseFrontierSelectResponse({
        status: "ok",
        point_index: 0,
        total_objective: 1,
        constraints: { loss: 1 },
        baseline_objective: 1,
        baseline_constraints: { loss: 1 },
        lambdas: { loss: 0.1 },
        converged: true,
        scenario_value_histogram: { counts: [1, 2] },
      }),
    ).toThrow(/scenario_value_histogram\.edges/)
    expect(() =>
      parseFrontierSelectResponse({
        status: "ok",
        point_index: 0,
        total_objective: 1,
        constraints: { loss: 1 },
        baseline_objective: 1,
        baseline_constraints: { loss: 1 },
        lambdas: { loss: 0.1 },
        converged: true,
        scenario_value_histogram: { edges: [0, 1, 2] },
      }),
    ).toThrow(/scenario_value_histogram\.counts/)
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
    ).toThrow(/lambdas/i)
  })

  it("rejects incomplete json cache build payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("json_cache_build_response")

    expect(() =>
      parseJsonCacheBuildResponse({
        ...fixture,
        data_path: undefined,
      }),
    ).toThrow(/data_path/i)
  })

  it("preserves json cache build skipped-record metadata", () => {
    const parsed = parseJsonCacheBuildResponse({
      ...loadUiContractFixture<Record<string, unknown>>("json_cache_build_response"),
      skipped_records: 1,
      skipped_rows: { drivers: 4 },
    })

    expect(parsed.skipped_records).toBe(1)
    expect(parsed.skipped_rows).toEqual({ drivers: 4 })
  })

  it("preserves json cache skipped-record metadata", () => {
    const parsed = parseJsonCacheStatusResponse({
      cached: true,
      data_path: "cache/data.parquet",
      row_count: 10,
      column_count: 3,
      size_bytes: 2048,
      cached_at: 123,
      columns: { premium: "Float64" },
      skipped_records: 2,
      skipped_rows: { drivers: 3 },
    })

    expect(parsed.skipped_records).toBe(2)
    expect(parsed.skipped_rows).toEqual({ drivers: 3 })
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

  it("rejects malformed mlflow check payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("mlflow_check_response")

    expect(() =>
      parseMlflowCheckResponse({
        ...fixture,
        mlflow_installed: "yes",
      }),
    ).toThrow(/mlflow_installed/i)

    expect(() =>
      parseMlflowCheckResponse({
        ...fixture,
        tracking_configured: "yes",
      }),
    ).toThrow(/tracking_configured/i)
  })

  it("rejects malformed utility write payloads", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("utility_write_response")

    expect(() =>
      parseUtilityWriteResponse({
        ...fixture,
        import_line: 123,
      }),
    ).toThrow(/import_line/i)
  })

  it("parses every working-branch readiness state and detached commit context", () => {
    const base = {
      working_branch: null,
      current_branch: "",
      eligible_branches: [],
      identity_set: false,
    }

    for (const state of [
      "no-repository",
      "unset",
      "detached",
      "invalid",
      "divergent",
      "ready",
    ] as const) {
      const parsed = parseGitWorkingBranchResponse({
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
      parseGitWorkingBranchResponse({
        state: "missing",
        current_branch: "",
      }),
    ).toThrow(/expected field `state` to be one of/i)
  })

  it("defaults storage to unsupported and sync to null when an older backend omits them", () => {
    const parsed = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
    })
    expect(parsed.storage).toBe("unsupported")
    expect(parsed.storage_remote).toBeNull()
    expect(parsed.sync).toBeNull()
  })

  it("parses a bound, synced storage surface", () => {
    const parsed = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      storage_remote: "https://github.com/org/repo.git",
      sync: { state: "synced", pending: 0, failure: null, message: null },
    })
    expect(parsed.storage).toBe("bound")
    expect(parsed.storage_remote).toBe("https://github.com/org/repo.git")
    expect(parsed.sync).toEqual({ state: "synced", pending: 0, failure: null, message: null })
  })

  it("parses a failed sync with its failure kind and message", () => {
    const parsed = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      sync: { state: "failed", pending: 2, failure: "rejected", message: "Push was rejected" },
    })
    expect(parsed.sync).toEqual({
      state: "failed",
      pending: 2,
      failure: "rejected",
      message: "Push was rejected",
    })
  })

  it("rejects an unknown storage state", () => {
    expect(() =>
      parseGitWorkingBranchResponse({
        state: "ready",
        current_branch: "dev",
        storage: "bogus",
      }),
    ).toThrow(/expected field `storage` to be one of/i)
  })

  it("parses the accepted bind-storage response", () => {
    const parsed = parseGitBindStorageResponse({
      outcome: "pending",
      remote_url: "https://github.com/org/repo.git",
      message: "Saving this project to storage — you can keep working.",
    })
    expect(parsed).toEqual({
      outcome: "pending",
      remote_url: "https://github.com/org/repo.git",
      message: "Saving this project to storage — you can keep working.",
    })
  })

  it("rejects the old synchronous bind outcomes", () => {
    // Binding is asynchronous: the route only ever accepts the request, and
    // the real outcome arrives on the readiness response's storage_bind.
    for (const outcome of ["adopted", "restart-required"]) {
      expect(() =>
        parseGitBindStorageResponse({
          outcome,
          remote_url: "https://github.com/org/repo.git",
          message: "x",
        }),
      ).toThrow(/expected field `outcome` to be one of/i)
    }
  })

  it("parses background bind progress, defaulting to absent", () => {
    const running = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
      storage_bind: {
        state: "running",
        outcome: null,
        message: null,
        claim: null,
        remote_url: "uc://workspace.default.projects/demo",
      },
    })
    expect(running.storage_bind?.state).toBe("running")
    expect(running.storage_bind?.remote_url).toBe("uc://workspace.default.projects/demo")

    const failed = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
      storage_bind: {
        state: "failed",
        message: "in use by app 'other-app'",
        claim: { app_name: "other-app", user: null, refreshed_at: null, message: "in use" },
      },
    })
    expect(failed.storage_bind?.claim?.app_name).toBe("other-app")

    // An older backend omitting the field reads as absent, not an error.
    const absent = parseGitWorkingBranchResponse({ state: "ready", current_branch: "dev" })
    expect(absent.storage_bind).toBeNull()
  })

  it("parses fork provenance on the readiness surface, defaulting to null", () => {
    const withLineage = parseGitWorkingBranchResponse({
      state: "ready",
      current_branch: "dev",
      storage: "bound",
      storage_forked_from: "uc://workspace.default.projects/demo",
    })
    expect(withLineage.storage_forked_from).toBe("uc://workspace.default.projects/demo")
    const without = parseGitWorkingBranchResponse({ state: "ready", current_branch: "dev" })
    expect(without.storage_forked_from).toBeNull()
  })

  it("parses a fork-storage response", () => {
    const parsed = parseGitForkStorageResponse({
      outcome: "forked",
      target_url: "uc://workspace.default.projects/demo-fork",
      parent_url: "uc://workspace.default.projects/demo",
      parent_generation: 7,
      message: "Forked generation 7. Bind the new location to work on the copy.",
    })
    expect(parsed.parent_generation).toBe(7)
    expect(parsed.target_url).toBe("uc://workspace.default.projects/demo-fork")
  })

  it("reads a claim-shaped 409 detail and rejects non-claim shapes", () => {
    const claim = gitStorageClaimFromDetail({
      app_name: "other-app",
      user: "colleague@example.com",
      refreshed_at: "2026-08-04T17:00:00+00:00",
      message: "This storage location is in use by app 'other-app'.",
    })
    expect(claim).not.toBeNull()
    expect(claim?.app_name).toBe("other-app")
    expect(claim?.user).toBe("colleague@example.com")
    // A plain-string detail (older backend, other error) is not a claim.
    expect(gitStorageClaimFromDetail("location is busy")).toBeNull()
    expect(gitStorageClaimFromDetail({ message: "no holder name" })).toBeNull()
    // Missing optionals degrade to null rather than throwing.
    const bare = gitStorageClaimFromDetail({ app_name: "a", message: "m" })
    expect(bare).toEqual({ app_name: "a", user: null, refreshed_at: null, message: "m" })
  })

  // --- P7 remote catch-up surface: per-leg divergence, fast-forward, branch-away,
  //     and the two 409 advisory bodies (push rejection + milestone fork). ---

  it("parses a remotes response with per-leg ahead/behind detail", () => {
    const parsed = parseGitRemotesResponse({
      working_branch: "dev",
      remotes: [
        {
          name: "origin",
          url: "git@example.com:x.git",
          working: { status: "behind", ahead: 0, behind: 1 },
          ledger: { status: "diverged", ahead: 2, behind: 1 },
        },
        // legs absent → fill to null (a remote we have no tracking detail for).
        { name: "backup" },
      ],
    })

    expect(parsed.remotes[0].working).toEqual({ status: "behind", ahead: 0, behind: 1 })
    expect(parsed.remotes[0].ledger?.status).toBe("diverged")
    expect(parsed.remotes[1].working).toBeNull()
    expect(parsed.remotes[1].ledger).toBeNull()
  })

  it("accepts every known leg status", () => {
    for (const status of ["untracked", "unknown", "synced", "ahead", "behind", "diverged"]) {
      const parsed = parseGitRemotesResponse({
        remotes: [{ name: "origin", working: { status } }],
      })
      expect(parsed.remotes[0].working?.status).toBe(status)
    }
  })

  it("rejects an unknown leg status", () => {
    expect(() =>
      parseGitRemotesResponse({ remotes: [{ name: "origin", working: { status: "wat" } }] }),
    ).toThrow(/status has unexpected value/i)
  })

  it("parses a fast-forward response with the advanced refs", () => {
    const parsed = parseGitFastForwardResponse({
      remote: "origin",
      working_branch: "dev",
      fast_forwarded: ["dev", "dev-save"],
    })

    expect(parsed.remote).toBe("origin")
    expect(parsed.fast_forwarded).toEqual(["dev", "dev-save"])
  })

  it("parses required bootstrap metadata for either bootstrap outcome", () => {
    const base = loadUiContractFixture<Record<string, unknown>>("git_push_response")

    expect(parseGitPushResponse({ ...base, bootstrapped_default: true })).toMatchObject({
      default_branch: base.default_branch,
      bootstrapped_default: true,
    })
    expect(
      parseGitPushResponse({ ...base, bootstrapped_default: false }).bootstrapped_default,
    ).toBe(false)
  })

  it("rejects missing or malformed bootstrap metadata instead of inventing it", () => {
    const base = loadUiContractFixture<Record<string, unknown>>("git_push_response")

    expect(() => parseGitPushResponse({ ...base, default_branch: undefined })).toThrow(
      /default_branch/i,
    )
    expect(() => parseGitPushResponse({ ...base, bootstrapped_default: undefined })).toThrow(
      /bootstrapped_default/i,
    )
    expect(() => parseGitPushResponse({ ...base, default_branch: 42 })).toThrow(/default_branch/i)
    expect(() => parseGitPushResponse({ ...base, bootstrapped_default: "false" })).toThrow(/bootstrapped_default/i)
  })

  it("rejects a fast-forward response missing working_branch", () => {
    expect(() =>
      parseGitFastForwardResponse({ remote: "origin", fast_forwarded: [] }),
    ).toThrow(/working_branch/i)
  })

  it("parses a branch-away response with the set-aside name", () => {
    const parsed = parseGitBranchAwayResponse({
      working_branch: "dev",
      set_aside_as: "dev-2026-06-21",
    })

    expect(parsed.set_aside_as).toBe("dev-2026-06-21")
  })

  it("parses a 409 push-rejection body, including a rewrite flag", () => {
    const parsed = parseGitPushRejection({
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

  it("returns null for a push-rejection body of the wrong status", () => {
    expect(parseGitPushRejection({ status: "ok" })).toBeNull()
  })

  it("throws for a malformed matching push-rejection body", () => {
    expect(() => parseGitPushRejection({ status: "rejected_diverged" })).toThrow()
    expect(() => parseGitPushRejection({
      status: "rejected_diverged",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 2 },
      ledger: null,
      message: "Remote has work you don't.",
      is_rewrite: "yes",
    })).toThrow()
  })

  it("returns null for a non-object push-rejection discriminator", () => {
    expect(parseGitPushRejection(null)).toBeNull()
  })

  it("parses a 409 milestone-fork body", () => {
    const parsed = parseGitMilestoneFork({
      status: "would_fork",
      remote: "origin",
      working: { status: "diverged", ahead: 1, behind: 1 },
      message: "Committing here would fork the published line.",
    })

    expect(parsed?.status).toBe("would_fork")
    expect(parsed?.working.status).toBe("diverged")
  })

  it("returns null for a milestone-fork body of the wrong status", () => {
    expect(parseGitMilestoneFork({ status: "ok" })).toBeNull()
  })

  it("throws for a malformed matching milestone-fork body", () => {
    expect(() => parseGitMilestoneFork({
      status: "would_fork",
      remote: "origin",
    })).toThrow()
  })

  it("parses a create-working-branch response", () => {
    const parsed = parseGitCreateWorkingBranchResponse({
      working_branch: "feature",
      moved: false,
      switched: true,
      last_save_sha: null,
    })

    expect(parsed.working_branch).toBe("feature")
    expect(parsed.switched).toBe(true)
    expect(parsed.last_save_sha).toBeNull()
  })

  it("parses git prefs, defaulting skip_switch_confirm to false", () => {
    expect(parseGitPrefs({}).skip_switch_confirm).toBe(false)
    expect(parseGitPrefs({ skip_switch_confirm: true }).skip_switch_confirm).toBe(true)
  })

  it("parses shared client trust-boundary payloads", () => {
    expect(parseHauteSessionResponse({ ok: true })).toEqual({ ok: true })
    expect(parseOutputAssembleDryRunResponse({ status: "ok", document: [{ premium: 1 }], row_count: 1, error: null })).toMatchObject({ status: "ok", row_count: 1 })
    expect(parseJsonCacheDeleteResponse({ cached: false, data_path: "cache/data.parquet" })).toEqual({ cached: false, data_path: "cache/data.parquet" })
    expect(parseJsonCacheSchemaInferenceResponse({ tables: [{ name: "drivers" }] }).tables).toEqual([{ name: "drivers" }])
    expect(parseMlflowExperiments([{ experiment_id: "1", name: "pricing" }])[0]?.name).toBe("pricing")
    expect(parseMlflowRuns([{ run_id: "run-1", run_name: "baseline", metrics: { auc: 0.9 }, artifacts: ["model"] }])[0]?.metrics.auc).toBe(0.9)
    expect(parseMlflowModels([{ name: "pricing", latest_versions: [{ version: "1", status: "READY", run_id: "run-1" }] }])[0]?.latest_versions).toHaveLength(1)
    expect(parseMlflowModelVersions([{ version: "1", run_id: "run-1", status: "READY", description: "baseline" }])[0]?.description).toBe("baseline")
    expect(parseFileListResponse({ items: [{ name: "data", path: "/data", type: "directory" }] }).items?.[0]?.type).toBe("directory")
    expect(parseFileListResponse({ items: [{ name: "data", path: "/data", type: "directory", size: null }] }).items?.[0]?.size).toBeNull()
    expect(parseGitGraphResponse({
      working_branch: "main",
      order: ["main"],
      branches: [{ name: "main", is_archived: false, is_current: true, tip_sha: "a", fork_point_sha: null, fork_of: null, fork_source_sha: null, fork_credit_sha: null, truncated: false, entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "2026-01-01", version_label: null, parents: [] }] }],
    }).branches[0]?.entries[0]?.parents).toEqual([])
  })

  it.each([
    ["session boolean", () => parseHauteSessionResponse({ ok: "yes" })],
    ["output document", () => parseOutputAssembleDryRunResponse({ status: "ok", document: {}, row_count: 1 })],
    ["cache deletion path", () => parseJsonCacheDeleteResponse({ cached: true })],
    ["inferred nested table", () => parseJsonCacheSchemaInferenceResponse({ tables: ["bad"] })],
    ["experiment required name", () => parseMlflowExperiments([{ experiment_id: "1" }])],
    ["run nested metric", () => parseMlflowRuns([{ run_id: "r", run_name: "n", metrics: { auc: "high" }, artifacts: [] }])],
    ["model nested version", () => parseMlflowModels([{ name: "m", latest_versions: [{ version: "1", status: "READY" }] }])],
    ["model version description", () => parseMlflowModelVersions([{ version: "1", run_id: "r", status: "READY" }])],
    ["file item type", () => parseFileListResponse({ items: [{ name: "x", path: "/x", type: "link" }] })],
    ["git graph nested parents", () => parseGitGraphResponse({ working_branch: null, order: [], branches: [{ name: "main", is_archived: false, is_current: true, tip_sha: "a", fork_point_sha: null, fork_of: null, fork_source_sha: null, fork_credit_sha: null, truncated: false, entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "today", version_label: null, parents: [1] }] }] })],
  ])("rejects malformed shared client payload: %s", (_name, parse) => {
    expect(parse).toThrow()
  })

})
