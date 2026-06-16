import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  applyOptimiser,
  cancelExplore,
  cancelOptimiserFrontierAutoRange,
  checkMlflow,
  createGitBranch,
  createSubmodel,
  createUtilityFile,
  deleteUtilityFile,
  dissolveSubmodel,
  buildJsonCache,
  estimateOptimiserFrontierAutoRange,
  estimateOptimiserSolve,
  estimateTrainingRam,
  fetchDatabricksData,
  fetchSchema,
  getExploreStatus,
  getGitHistory,
  getGitStatus,
  getOptimiserFrontierAutoRangeStatus,
  getOptimiserStatus,
  getTrainStatus,
  gitArchiveBranch,
  gitDeleteBranch,
  gitPull,
  gitRevert,
  gitSave,
  gitSubmit,
  listUtilityFiles,
  loadSubmodel,
  logOptimiserToMlflow,
  logToMlflow,
  previewNode,
  readUtilityFile,
  runExplore,
  runFrontier,
  savePipeline,
  saveOptimiser,
  selectFrontierPoint,
  solveOptimiser,
  startOptimiserFrontierAutoRange,
  switchGitBranch,
  traceCell,
  trainModel,
  updateUtilityFile,
} from "../client"

let mockFetch: ReturnType<typeof vi.fn>

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

const dummyGraph = {
  nodes: [{ id: "n1", type: "custom", position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("client runtime contracts", () => {
  it("savePipeline rejects malformed 200 payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ file: 123, pipeline_name: "pricing" }))

    await expect(
      savePipeline({
        name: "pricing",
        description: "",
        graph: dummyGraph,
        preamble: "",
        source_file: "pricing.py",
      }),
    ).rejects.toThrow(/parseSavePipelineResponse/i)
  })

  it("previewNode rejects malformed preview payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok", node_id: 42 }))

    await expect(previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })).rejects.toThrow(/parsePreviewNodeResponse/i)
  })

  it("previewNode sends requested preview columns when provided", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    await previewNode({
      graph: dummyGraph,
      nodeId: "n1",
      rowLimit: 10,
      source: "live",
      requestedPreviewColumns: ["premium", "segment"],
    })

    const [, init] = mockFetch.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toMatchObject({
      requested_preview_columns: ["premium", "segment"],
    })
  })

  it("previewNode preserves per-node schema maps from preview responses", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    const result = await previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })

    expect(result.node_columns?.source?.map((column) => column.name)).toEqual([
      "premium",
      "segment",
    ])
    expect(result.node_available_columns?.score?.map((column) => column.name)).toEqual([
      "premium",
      "segment",
    ])
    expect(result.node_schema_warnings?.score).toEqual([
      { column: "premium", status: "computed" },
    ])
  })

  it("traceCell rejects malformed trace payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok", trace: { row_index: 0 } }))

    await expect(
      traceCell({ graph: dummyGraph, row_index: 0, target_node_id: "n1" }),
    ).rejects.toThrow(/parseTraceResponse/i)
  })

  it("fetchSchema rejects malformed schema payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ path: "x.parquet", columns: "bad" }))

    await expect(fetchSchema("x.parquet")).rejects.toThrow(/parseSchemaResponse/i)
  })

  it("trainModel accepts the tightened train contract", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("train_started_response")))

    const result = await trainModel({ graph: dummyGraph, node_id: "model1" })

    expect(result.status).toBe("started")
    expect(result.job_id).toBe("job-1")
  })

  it("runExplore sends cache materialisation requests and parses cache descriptors", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("explore_run_response")))

    const result = await runExplore({
      graph: dummyGraph,
      node_id: "explore",
      source: "live",
      streamingChunkSize: 2048,
    })

    const [, init] = mockFetch.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toMatchObject({
      graph: dummyGraph,
      node_id: "explore",
      source: "live",
      streaming_chunk_size: 2048,
    })
    expect(result.cached).toBe(true)
    expect(result.result?.row_count).toBe(150)
    expect(result.result?.dataframe_cache_key).toContain("explore_dataset")
  })

  it("getExploreStatus and cancelExplore parse terminal reports", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("explore_status_response")))

    const status = await getExploreStatus("explore-job-1")
    const cancelled = await cancelExplore("explore-job-1")

    expect(status.result?.row_count).toBe(150)
    expect(cancelled.terminal_reason).toBe("completed")
  })

  it("getTrainStatus rejects malformed nested train results", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("train_status_response")
    const result = fixture.result as Record<string, unknown>

    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        result: {
          ...result,
          glm_coefficients: [{ feature: "x", coefficient: "bad" }],
        },
      }),
    )

    await expect(getTrainStatus("job-1")).rejects.toThrow(/parseTrainResponse/i)
  })

  it("getOptimiserStatus rejects malformed optimiser result payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    const result = fixture.result as Record<string, unknown>

    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        result: {
          ...result,
          lambdas: { loss: "bad" },
        },
      }),
    )

    await expect(getOptimiserStatus("job-1")).rejects.toThrow(/parseOptimiserStatusResponse/i)
  })

  it("preserves optimiser payload budget metadata from contract fixtures", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("optimiser_frontier_response")))

    const frontier = await runFrontier({
      job_id: "opt-job-1",
      threshold_ranges: { loss: [0.8, 1.0] },
    })

    expect(frontier.points_returned).toBe(1)
    expect(frontier.points_limit).toBe(2000)
    expect(frontier.points_truncated).toBe(false)

    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("optimiser_frontier_auto_range_response")))

    const autoRange = await estimateOptimiserFrontierAutoRange({
      graph: dummyGraph,
      node_id: "opt1",
    })

    expect(autoRange.ranges.expected_margin).toEqual({ min: 11, max: 39 })

    mockFetch.mockReturnValue(jsonResponse({ status: "started", job_id: "range-job-1", error: null }))

    const startedRange = await startOptimiserFrontierAutoRange({
      graph: dummyGraph,
      node_id: "opt1",
    })

    expect(startedRange.job_id).toBe("range-job-1")

    mockFetch.mockReturnValue(
      jsonResponse({
        status: "completed",
        progress: 1,
        message: "Completed",
        elapsed_seconds: 2.5,
        result: loadUiContractFixture("optimiser_frontier_auto_range_response"),
      }),
    )

    const rangeStatus = await getOptimiserFrontierAutoRangeStatus("range-job-1")

    expect(rangeStatus.result?.ranges.expected_margin).toEqual({ min: 11, max: 39 })

    mockFetch.mockReturnValue(
      jsonResponse({
        status: "cancelled",
        progress: 0.25,
        message: "Cancelled",
        elapsed_seconds: 2.5,
        result: null,
      }),
    )

    const cancelledRange = await cancelOptimiserFrontierAutoRange("range-job-1")

    expect(cancelledRange.status).toBe("cancelled")

    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("optimiser_apply_response")))

    const applyResult = await applyOptimiser({ job_id: "opt-job-1" })

    expect(applyResult.from_artifact).toBe(false)
    expect(applyResult.preview_row_count).toBe(1)
    expect(applyResult.preview_row_limit).toBe(100)
    expect(applyResult.preview_truncated).toBe(false)
  })

  it("sends explicit frontier point indexes on terminal optimiser actions", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("optimiser_apply_response")))
    await applyOptimiser({ job_id: "opt-job-1", point_index: 3 })
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toMatchObject({
      job_id: "opt-job-1",
      point_index: 3,
    })

    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("optimiser_save_response")))
    await saveOptimiser({ job_id: "opt-job-1", output_path: "output.py", point_index: 3 })
    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toMatchObject({
      job_id: "opt-job-1",
      output_path: "output.py",
      point_index: 3,
    })

    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("mlflow_log_response")))
    await logOptimiserToMlflow({ job_id: "opt-job-1", point_index: 3 })
    expect(JSON.parse(String(mockFetch.mock.calls[2][1]?.body))).toMatchObject({
      job_id: "opt-job-1",
      point_index: 3,
    })
  })

  it("getGitStatus rejects malformed git payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("git_status_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, is_read_only: undefined }))

    await expect(getGitStatus()).rejects.toThrow(/parseGitStatusResponse/i)
  })

  it("buildJsonCache rejects incomplete cache-build payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("json_cache_build_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, data_path: undefined }))

    await expect(buildJsonCache({ path: "/data/input.json" })).rejects.toThrow(/parseJsonCacheBuildResponse/i)
  })

  it("fetchDatabricksData rejects malformed fetch payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("fetch_table_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, column_count: undefined }))

    await expect(fetchDatabricksData({ table: "cat.sch.tbl" })).rejects.toThrow(/parseFetchTableResponse/i)
  })
})

describe("next-wave client runtime contracts", () => {
  const malformedCases: Array<{
    name: string
    response: Record<string, unknown>
    call: () => Promise<unknown>
    error: RegExp
  }> = [
    {
      name: "runExplore",
      response: { ...loadUiContractFixture<Record<string, unknown>>("explore_run_response"), cached: "yes" },
      call: () => runExplore({ graph: dummyGraph, node_id: "explore" }),
      error: /parseExploreRunResponse/i,
    },
    {
      name: "getExploreStatus",
      response: { ...loadUiContractFixture<Record<string, unknown>>("explore_status_response"), progress: "bad" },
      call: () => getExploreStatus("explore-job-1"),
      error: /parseExploreStatusResponse/i,
    },
    {
      name: "cancelExplore",
      response: { ...loadUiContractFixture<Record<string, unknown>>("explore_status_response"), status: "weird" },
      call: () => cancelExplore("explore-job-1"),
      error: /parseExploreStatusResponse/i,
    },
    {
      name: "createSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("submodel_create_response"), graph: { edges: [] } },
      call: () => createSubmodel({
        name: "pricing",
        node_ids: ["n1"],
        graph: dummyGraph,
        preamble: "",
        source_file: "main.py",
        pipeline_name: "main",
      }),
      error: /parseSubmodelCreateResponse/i,
    },
    {
      name: "loadSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("submodel_graph_response"), submodel_name: 42 },
      call: () => loadSubmodel("pricing"),
      error: /parseSubmodelGraphResponse/i,
    },
    {
      name: "dissolveSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response"), graph: { nodes: "bad", edges: [] } },
      call: () => dissolveSubmodel({
        submodel_name: "pricing",
        graph: dummyGraph,
        preamble: "",
        source_file: "main.py",
        pipeline_name: "main",
      }),
      error: /parseDissolveSubmodelResponse/i,
    },
    {
      name: "checkMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_check_response"), mlflow_installed: "yes" },
      call: () => checkMlflow(),
      error: /parseMlflowCheckResponse/i,
    },
    {
      name: "estimateTrainingRam",
      response: { ...loadUiContractFixture<Record<string, unknown>>("train_estimate_response"), estimated_mb: "bad" },
      call: () => estimateTrainingRam({ graph: dummyGraph, node_id: "model1" }),
      error: /parseTrainEstimateResponse/i,
    },
    {
      name: "logToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_log_response"), run_id: 42 },
      call: () => logToMlflow({ job_id: "job-1" }),
      error: /parseMlflowLogResponse/i,
    },
    {
      name: "solveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("solve_optimiser_response"), job_id: 42 },
      call: () => solveOptimiser({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseSolveOptimiserResponse/i,
    },
    {
      name: "estimateOptimiserSolve",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_estimate_response"), total_rows: "bad" },
      call: () => estimateOptimiserSolve({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseOptimiserEstimateResponse/i,
    },
    {
      name: "applyOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_apply_response"), constraints: { loss: "bad" } },
      call: () => applyOptimiser({ job_id: "opt-job-1" }),
      error: /parseApplyOptimiserResponse/i,
    },
    {
      name: "saveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_save_response"), message: 42 },
      call: () => saveOptimiser({ job_id: "opt-job-1", output_path: "output.py" }),
      error: /parseSaveOptimiserResponse/i,
    },
    {
      name: "logOptimiserToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_log_response"), tracking_uri: 42 },
      call: () => logOptimiserToMlflow({ job_id: "opt-job-1" }),
      error: /parseMlflowLogResponse/i,
    },
    {
      name: "runFrontier",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_response"), constraint_names: "bad" },
      call: () => runFrontier({ job_id: "opt-job-1", threshold_ranges: { loss: [0.8, 1.0] } }),
      error: /constraint_names/i,
    },
    {
      name: "estimateOptimiserFrontierAutoRange",
      response: {
        ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_auto_range_response"),
        ranges: { expected_margin: { min: "bad", max: 39 } },
      },
      call: () => estimateOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseFrontierAutoRangeResponse/i,
    },
    {
      name: "startOptimiserFrontierAutoRange",
      response: { status: "started", job_id: 42, error: null },
      call: () => startOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1" }),
      error: /parseFrontierAutoRangeStartResponse/i,
    },
    {
      name: "getOptimiserFrontierAutoRangeStatus",
      response: {
        status: "completed",
        progress: 1,
        message: "Completed",
        elapsed_seconds: 1,
        result: {
          ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_auto_range_response"),
          ranges: { expected_margin: { min: "bad", max: 39 } },
        },
      },
      call: () => getOptimiserFrontierAutoRangeStatus("range-job-1"),
      error: /parseFrontierAutoRangeResponse/i,
    },
    {
      name: "cancelOptimiserFrontierAutoRange",
      response: {
        status: "cancelled",
        progress: 1,
        message: "Cancelled",
        elapsed_seconds: "bad",
        result: null,
      },
      call: () => cancelOptimiserFrontierAutoRange("range-job-1"),
      error: /elapsed_seconds/i,
    },
    {
      name: "selectFrontierPoint",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_select_response"), lambdas: { loss: "bad" } },
      call: () => selectFrontierPoint({ job_id: "opt-job-1", point_index: 0 }),
      error: /parseFrontierSelectResponse/i,
    },
    {
      name: "listUtilityFiles",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_list_response"), files: "bad" },
      call: () => listUtilityFiles(),
      error: /parseUtilityListResponse/i,
    },
    {
      name: "readUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_read_response"), content: 42 },
      call: () => readUtilityFile("helpers"),
      error: /parseUtilityReadResponse/i,
    },
    {
      name: "createUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), import_line: 42 },
      call: () => createUtilityFile({ name: "helpers", content: "" }),
      error: /parseUtilityWriteResponse/i,
    },
    {
      name: "updateUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), error_line: "bad" },
      call: () => updateUtilityFile("helpers", "print('x')"),
      error: /parseUtilityWriteResponse/i,
    },
    {
      name: "deleteUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_delete_response"), module: 42 },
      call: () => deleteUtilityFile("helpers"),
      error: /parseUtilityDeleteResponse/i,
    },
    {
      name: "createGitBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_create_branch_response"), branch: 42 },
      call: () => createGitBranch("new feature branch"),
      error: /parseGitCreateBranchResponse/i,
    },
    {
      name: "switchGitBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_switch_branch_response"), branch: 42 },
      call: () => switchGitBranch("feat/pricing-improvements"),
      error: /parseGitSwitchBranchResponse/i,
    },
    {
      name: "gitSave",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_save_response"), pushed: "yes" },
      call: () => gitSave(),
      error: /parseGitSaveResponse/i,
    },
    {
      name: "gitSubmit",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_submit_response"), push_error: 42 },
      call: () => gitSubmit(),
      error: /parseGitSubmitResponse/i,
    },
    {
      name: "getGitHistory",
      response: {
        ...loadUiContractFixture<Record<string, unknown>>("git_history_response"),
        entries: [
          {
            ...(loadUiContractFixture<Record<string, unknown>>("git_history_response").entries as Array<Record<string, unknown>>)[0],
            files_changed: "bad",
          },
        ],
      },
      call: () => getGitHistory(),
      error: /parseGitHistoryResponse/i,
    },
    {
      name: "gitRevert",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_revert_response"), backup_tag: 42 },
      call: () => gitRevert("abc123def456"),
      error: /parseGitRevertResponse/i,
    },
    {
      name: "gitPull",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_pull_response"), success: "yes" },
      call: () => gitPull(),
      error: /parseGitPullResponse/i,
    },
    {
      name: "gitArchiveBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_archive_response"), archived_as: 42 },
      call: () => gitArchiveBranch("feat/pricing-improvements"),
      error: /parseGitArchiveResponse/i,
    },
    {
      name: "gitDeleteBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_delete_branch_response"), backup_tag: 42 },
      call: () => gitDeleteBranch("feat/pricing-improvements"),
      error: /parseGitDeleteBranchResponse/i,
    },
  ]

  for (const testCase of malformedCases) {
    it(`${testCase.name} rejects malformed 200 payloads`, async () => {
      mockFetch.mockReturnValue(jsonResponse(testCase.response))

      await expect(testCase.call()).rejects.toThrow(testCase.error)
    })
  }
})
