import { describe, expect, it } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  parseApplyOptimiserResponse,
  parseDissolveSubmodelResponse,
  parseFrontierAutoRangeResponse,
  parseFrontierResponse,
  parseFrontierSelectResponse,
  parseGitArchiveResponse,
  parseGitDeleteBranchResponse,
  parseGitStatusResponse,
  parseJsonCacheBuildResponse,
  parseMlflowCheckResponse,
  parseMlflowLogResponse,
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
  parseTrainResponse,
  parseTrainStatusResponse,
  parseUtilityDeleteResponse,
  parseUtilityListResponse,
  parseUtilityReadResponse,
  parseUtilityWriteResponse,
} from "../guards"

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

  it("parses preview truncation metadata", () => {
    const parsed = parsePreviewNodeResponse(loadUiContractFixture("preview_node"))

    expect(parsed.preview_row_count).toBe(2)
    expect(parsed.preview_row_limit).toBe(10_000)
    expect(parsed.preview_truncated).toBe(false)
    expect(parsed.preview_columns).toEqual(["premium", "segment"])
  })

  it("rejects malformed preview node ids", () => {
    expect(() =>
      parsePreviewNodeResponse({
        status: "ok",
        node_id: 42,
      }),
    ).toThrow(/node_id/i)
  })

  it("parses trace responses including waterfall entries", () => {
    const parsed = parseTraceResponse(loadUiContractFixture("trace_response"))

    expect(Array.isArray(parsed.trace?.waterfall)).toBe(true)
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

  it("parses completed training responses with GLM diagnostics", () => {
    const parsed = parseTrainResponse(loadUiContractFixture("train_response"))

    expect(parsed.glm_coefficients?.[0].feature).toBe("x")
    expect(parsed.diagnostics_errors?.[0].diagnostic).toBe("shap")
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

  it("parses optimiser status responses with completed solve payloads", () => {
    const parsed = parseOptimiserStatusResponse(loadUiContractFixture("optimiser_status_response"))

    expect(parsed.result?.lambdas.loss).toBe(0.3)
    expect(parsed.frontier?.constraint_names).toEqual(["loss"])
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

  it("rejects git status payloads missing readonly fields", () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("git_status_response")

    expect(() =>
      parseGitStatusResponse({
        ...fixture,
        is_read_only: undefined,
      }),
    ).toThrow(/is_read_only/i)
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

  it("normalises legacy mlflow tracking_available payloads", () => {
    const parsed = parseMlflowCheckResponse({
      mlflow_installed: true,
      tracking_available: false,
      backend: "",
      databricks_host: "",
    })

    expect(parsed.mlflow_importable).toBe(true)
    expect(parsed.tracking_configured).toBe(false)
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

})
