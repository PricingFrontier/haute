import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  applyOptimiser,
  cancelExplore,
  cancelExplorePivot,
  cancelOptimiserFrontierAutoRange,
  checkMlflow,
  createSubmodel,
  createUtilityFile,
  deleteUtilityFile,
  dissolveSubmodel,
  buildJsonCache,
  checkHauteSession,
  deleteJsonCache,
  estimateOptimiserSolve,
  estimateTrainingRam,
  commitMilestone,
  resolveOutputDestination,
  writeOutput,
  fetchIoCapabilities,
  fetchExplorePivotMembers,
  buildInputCache,
  fetchSchema,
  getExploreStatus,
  getExploreCacheSnapshot,
  getExplorePivotStatus,
  getMilestones,
  getMilestoneSaves,
  getOptimiserFrontierAutoRangeStatus,
  getOptimiserStatus,
  getPendingSaves,
  getTrainStatus,
  getWorkingBranches,
  restoreBranch,
  createWorkingBranch,
  getGitPrefs,
  getGitGraph,
  getExperiments,
  getModelVersions,
  getModels,
  getRuns,
  inferJsonCacheSchema,
  JSON_CACHE_INFER_TIMEOUT_MS,
  gitArchiveBranch,
  gitDeleteBranch,
  listUtilityFiles,
  loadSubmodel,
  logOptimiserToMlflow,
  logToMlflow,
  listFiles,
  outputAssembleDryRun,
  previewNode,
  readUtilityFile,
  runExplore,
  runExplorePivot,
  savePipeline,
  saveOptimiser,
  selectFrontierPoint,
  solveOptimiser,
  startOptimiserFrontierAutoRange,
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
        preserved_blocks: [],
      }),
    ).rejects.toThrow(/parseSavePipelineResponse/i)
  })

  it("previewNode rejects malformed preview payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok", node_id: 42 }))

    await expect(previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })).rejects.toThrow(/parsePreviewNodeResponse/i)
  })

  it("writeOutput rejects malformed output payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ message: "done" }))

    await expect(writeOutput({ graph: dummyGraph, nodeId: "sink1" })).rejects.toThrow(/parseWriteOutputResponse/i)
  })

  it("writeOutput validates and returns a well-formed output payload", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({ status: "ok", message: "Wrote 3 rows", row_count: 3, path: "out.parquet", format: "parquet" }),
    )

    const result = await writeOutput({ graph: dummyGraph, nodeId: "sink1" })
    expect(result.status).toBe("ok")
    expect(result.row_count).toBe(3)
    expect(result.path).toBe("out.parquet")
  })

  it("resolveOutputDestination rejects malformed destination payloads", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({
        path: "outputs/report.csv",
        format: "csv",
        suffix_mismatch: "no",
      }),
    )

    await expect(
      resolveOutputDestination({ graph: dummyGraph, nodeId: "sink1" }),
    ).rejects.toThrow(/parseOutputDestinationResponse/i)
  })

  it("resolveOutputDestination validates its complete response", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({
        path: "outputs/report.csv",
        format: "csv",
        suffix_mismatch: false,
      }),
    )

    await expect(
      resolveOutputDestination({ graph: dummyGraph, nodeId: "sink1" }),
    ).resolves.toEqual({
      path: "outputs/report.csv",
      format: "csv",
      suffix_mismatch: false,
    })
  })

  it("fetchIoCapabilities rejects unknown V1 discriminants", async () => {
    mockFetch.mockReturnValue(jsonResponse({ schema_version: 1, groups: [{ name: "file", label: "Files", input_available: true, output_available: true, cache_modes: ["unknown"], input_fields: [], output_fields: [], formats: [] }] }))
    await expect(fetchIoCapabilities()).rejects.toThrow(/parseIoCapabilitiesResponse/i)
  })

  it("input-cache build rejects a malformed V1 response", async () => {
    mockFetch.mockReturnValue(jsonResponse({ schema_version: 2, job_id: "job", identity_digest: "digest", status: "running", joined: false }))
    await expect(buildInputCache({ schema_version: 1, config: {}, refresh: false, profile: "lazy_sink" })).rejects.toThrow(/parseInputCacheBuildResponse/i)
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

  it("previewNode sends port_label when provided, omits it otherwise", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))
    await previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10, portLabel: "drivers" })
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toMatchObject({
      port_label: "drivers",
    })

    mockFetch.mockClear()
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))
    await previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).not.toHaveProperty("port_label")
  })

  it("previewNode sends streaming_chunk_size and honours a custom timeout", async () => {
    // Exercises the `streamingChunkSize !== undefined` present branch and the
    // non-default path of the `timeout = 120_000` default argument.
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    await previewNode({
      graph: dummyGraph,
      nodeId: "n1",
      rowLimit: 10,
      streamingChunkSize: 4096,
      timeout: 5_000,
    })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toMatchObject({
      streaming_chunk_size: 4096,
    })
  })

  it("previewNode omits streaming_chunk_size and uses the default timeout", async () => {
    // Exercises the `streamingChunkSize !== undefined` absent branch and the
    // default path of the `timeout = 120_000` default argument.
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    await previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).not.toHaveProperty(
      "streaming_chunk_size",
    )
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

  it("previewNode preserves per-frame column maps (frame_columns + node_frame_columns)", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    const result = await previewNode({ graph: dummyGraph, nodeId: "n1", rowLimit: 10 })

    // The previewed node's own per-frame schema.
    expect(Object.keys(result.frame_columns ?? {})).toEqual(["policies", "drivers"])
    expect(result.frame_columns?.drivers?.map((c) => c.name)).toEqual([
      "driver_id",
      "age_band",
    ])
    // The route-level node_id → frame label → columns map.
    expect(result.node_frame_columns?.source?.drivers?.map((c) => c.name)).toEqual([
      "driver_id",
      "age_band",
    ])
  })

  const outputAssembleResponse = {
    status: "ok",
    document: [{ premium: 100 }],
    row_count: 1,
    error: null,
  }

  it("outputAssembleDryRun sends every optional field and a custom timeout", async () => {
    // Exercises the present branches of `outputFormat ?? "json"` (an explicit
    // format), the `rowLimit !== undefined` ternary, and `source ?? "live"`
    // (an explicit source), plus the non-default `timeout = 120_000` path.
    mockFetch.mockReturnValue(jsonResponse(outputAssembleResponse))

    const result = await outputAssembleDryRun({
      graph: dummyGraph,
      nodeId: "out1",
      outputMapping: [{ field: "premium" }],
      outputFormat: "csv",
      rowLimit: 25,
      source: "cache",
      timeout: 5_000,
    })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toMatchObject({
      node_id: "out1",
      output_mapping: [{ field: "premium" }],
      output_format: "csv",
      row_limit: 25,
      source: "cache",
    })
    expect(result.row_count).toBe(1)
  })

  it("outputAssembleDryRun applies defaults and omits row_limit when unset", async () => {
    // Exercises the fallback branches: `outputFormat ?? "json"` (absent →
    // "json"), the `rowLimit !== undefined` absent branch (key omitted), and
    // `source ?? "live"` (absent → "live"), plus the default timeout path.
    mockFetch.mockReturnValue(jsonResponse(outputAssembleResponse))

    await outputAssembleDryRun({
      graph: dummyGraph,
      nodeId: "out1",
      outputMapping: [{ field: "premium" }],
    })

    const body = JSON.parse(String(mockFetch.mock.calls[0][1]?.body))
    expect(body).toMatchObject({
      node_id: "out1",
      output_format: "json",
      source: "live",
    })
    expect(body).not.toHaveProperty("row_limit")
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
      refresh: true,
      streamingChunkSize: 2048,
    })

    const [, init] = mockFetch.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toMatchObject({
      graph: dummyGraph,
      node_id: "explore",
      source: "live",
      refresh: true,
      streaming_chunk_size: 2048,
    })
    expect(result.cached).toBe(true)
    expect(result.result?.row_count).toBe(150)
    expect(result.result?.dataframe_cache_key).toContain("explore_dataset")
  })

  it("getExploreCacheSnapshot posts the cache identity and parses its state", async () => {
    const report = loadUiContractFixture<Record<string, unknown>>("explore_run_response").result
    mockFetch.mockReturnValue(jsonResponse({ state: "current", message: "Cached", result: report }))

    const result = await getExploreCacheSnapshot({
      graph: dummyGraph, node_id: "explore", streamingChunkSize: 2048,
    })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/explore/cache-status")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "explore", source: "live", streaming_chunk_size: 2048,
    })
    expect(result.state).toBe("current")
    expect(result.result?.row_count).toBe(150)
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

  it("preserves optimiser auto-range start contract metadata", async () => {
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

  it("getMilestones rejects malformed milestone payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: "w", entries: [{ sha: 123 }] }))
    await expect(getMilestones()).rejects.toThrow(/parseGitMilestonesResponse/i)
  })

  it("getMilestoneSaves rejects malformed ledger-save payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ saves: [{ sha: 123 }] }))
    await expect(getMilestoneSaves("abc")).rejects.toThrow(/parseGitLedgerSavesResponse/i)
  })

  it("getPendingSaves rejects malformed ledger-save payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ saves: [{ message: 5 }] }))
    await expect(getPendingSaves()).rejects.toThrow(/parseGitLedgerSavesResponse/i)
  })

  it("commitMilestone rejects malformed commit payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ sha: "x" }))
    await expect(commitMilestone("m", null)).rejects.toThrow(/parseGitCommitResponse/i)
  })

  it("getWorkingBranches rejects malformed branch payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ current: "demo", branches: [{ name: 123 }] }))
    await expect(getWorkingBranches()).rejects.toThrow(/parseGitWorkingBranchesResponse/i)
  })

  it("restoreBranch rejects malformed restore payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ wrong: 1 }))
    await expect(restoreBranch("archive/x")).rejects.toThrow(/parseGitRestoreResponse/i)
  })

  it("createWorkingBranch rejects malformed fork payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: "x", moved: "no" }))
    await expect(createWorkingBranch("x")).rejects.toThrow(
      /parseGitCreateWorkingBranchResponse/i,
    )
  })

  it("getGitPrefs coerces a missing flag to false (tolerant prefs)", async () => {
    mockFetch.mockReturnValue(jsonResponse({}))
    await expect(getGitPrefs()).resolves.toEqual({ skip_switch_confirm: false })
  })

  it("buildJsonCache rejects incomplete cache-build payloads", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("json_cache_build_response")
    mockFetch.mockReturnValue(jsonResponse({ ...fixture, data_path: undefined }))

    await expect(buildJsonCache({ path: "/data/input.json" })).rejects.toThrow(/parseJsonCacheBuildResponse/i)
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
      name: "getExploreCacheSnapshot",
      response: { state: "ready", message: "wrong state", result: null },
      call: () => getExploreCacheSnapshot({ graph: dummyGraph, node_id: "explore" }),
      error: /parseExploreCacheSnapshotResponse/i,
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
        base_revision: "revision-test",
        preserved_blocks: [],
      }),
      error: /parseSubmodelCreateResponse/i,
    },
    {
      name: "loadSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("submodel_graph_response"), submodel_name: 42 },
      call: () => loadSubmodel("pricing", "main.py"),
      error: /parseSubmodelGraphResponse/i,
    },
    {
      name: "dissolveSubmodel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("dissolve_submodel_response"), graph: { nodes: "bad", edges: [] } },
      call: () => dissolveSubmodel({
        instance_id: "pricing",
        graph: dummyGraph,
        preamble: "",
        source_file: "main.py",
        pipeline_name: "main",
        base_revision: "revision-test",
        preserved_blocks: [],
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
      name: "gitArchiveBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_archive_response"), archived_as: 42 },
      call: () => gitArchiveBranch("feat/pricing-improvements"),
      error: /parseGitArchiveResponse/i,
    },
    {
      name: "gitDeleteBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_delete_branch_response"), branch: 42 },
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

describe("shared client trust-boundary endpoints", () => {
  const validGitGraph = {
    working_branch: "main",
    order: ["main"],
    branches: [{
      name: "main", is_archived: false, is_current: true, tip_sha: "a",
      fork_point_sha: null, fork_of: null, fork_source_sha: null, fork_credit_sha: null,
      truncated: false,
      entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "today", version_label: null, parents: [] }],
    }],
  }

  const cases: Array<{
    name: string
    body: unknown
    call: () => Promise<unknown>
    url: string
    method?: string
    malformed: unknown
  }> = [
    { name: "checkHauteSession", body: { ok: true }, call: () => checkHauteSession(), url: "/api/session", malformed: { ok: "yes" } },
    { name: "outputAssembleDryRun", body: { status: "ok", document: [], row_count: 0, error: null }, call: () => outputAssembleDryRun({ graph: dummyGraph, nodeId: "out", outputMapping: [] }), url: "/api/output-assemble/dry-run", method: "POST", malformed: { status: "ok", document: [], row_count: "1" } },
    { name: "deleteJsonCache", body: { cached: false, data_path: "cache/data" }, call: () => deleteJsonCache("/data/input.json"), url: "/api/json-cache?path=%2Fdata%2Finput.json", method: "DELETE", malformed: { cached: false } },
    { name: "inferJsonCacheSchema", body: { tables: [{ name: "drivers" }] }, call: () => inferJsonCacheSchema({ path: "/data/input.json" }), url: "/api/json-cache/infer", method: "POST", malformed: { tables: ["bad"] } },
    { name: "getExperiments", body: [{ experiment_id: "1", name: "pricing" }], call: () => getExperiments(), url: "/api/mlflow/experiments", malformed: [{ experiment_id: "1" }] },
    { name: "getRuns", body: [{ run_id: "r", run_name: "baseline", metrics: { auc: 0.9 }, artifacts: [] }], call: () => getRuns("exp", "model"), url: "/api/mlflow/runs?experiment_id=exp&artifact_filter=model", malformed: [{ run_id: "r", run_name: "baseline", metrics: {}, artifacts: [1] }] },
    { name: "getModels", body: [{ name: "pricing", latest_versions: [{ version: "1", status: "READY", run_id: "r" }] }], call: () => getModels(), url: "/api/mlflow/models", malformed: [{ name: "pricing", latest_versions: [{ version: "1", status: "READY" }] }] },
    { name: "getModelVersions", body: [{ version: "1", run_id: "r", status: "READY", description: "baseline" }], call: () => getModelVersions("pricing model"), url: "/api/mlflow/model-versions?model_name=pricing%20model", malformed: [{ version: "1", run_id: "r", status: "READY" }] },
    { name: "listFiles", body: { items: [{ name: "data", path: "/data", type: "directory" }] }, call: () => listFiles("/data", ".json"), url: "/api/files?dir=%2Fdata&extensions=.json", malformed: { items: [{ name: "data", path: "/data", type: "other" }] } },
    { name: "getGitGraph", body: validGitGraph, call: () => getGitGraph(5), url: "/api/git/graph?limit=5", malformed: { ...validGitGraph, branches: [{ ...validGitGraph.branches[0], entries: [{ ...validGitGraph.branches[0].entries[0], parents: [1] }] }] } },
  ]

  for (const testCase of cases) {
    it(`${testCase.name} requests its contract endpoint`, async () => {
      mockFetch.mockReturnValue(jsonResponse(testCase.body))
      await expect(testCase.call()).resolves.toBeDefined()
      expect(mockFetch.mock.calls[0]?.[0]).toBe(testCase.url)
      expect((mockFetch.mock.calls[0]?.[1] as RequestInit | undefined)?.method ?? "GET").toBe(testCase.method ?? "GET")
    })

    it(`${testCase.name} rejects malformed successful payloads`, async () => {
      mockFetch.mockReturnValue(jsonResponse(testCase.malformed))
      await expect(testCase.call()).rejects.toThrow()
    })
  }
})

describe("pivot client contracts", () => {
  it("uses the pivot endpoints, preserves request bodies, and guards responses", async () => {
    const run = loadUiContractFixture("explore_pivot_run_response")
    const status = loadUiContractFixture("explore_pivot_status_response")
    const members = loadUiContractFixture("explore_pivot_members_response")
    mockFetch
      .mockReturnValueOnce(jsonResponse(run))
      .mockReturnValueOnce(jsonResponse(status))
      .mockReturnValueOnce(jsonResponse(status))
      .mockReturnValueOnce(jsonResponse(members))

    await expect(
      runExplorePivot({
        graph: dummyGraph,
        node_id: "explore",
        pivot: { rows: ["region"] },
        source: "pricing",
        streamingChunkSize: 500,
      }),
    ).resolves.toMatchObject({ status: "completed" })
    await expect(getExplorePivotStatus("job / 1")).resolves.toMatchObject({
      status: "completed",
    })
    await expect(cancelExplorePivot("job / 1")).resolves.toMatchObject({
      status: "completed",
    })
    await expect(
      fetchExplorePivotMembers({
        graph: dummyGraph,
        node_id: "explore",
        field: "region",
        search: "Nor",
        streamingChunkSize: 50,
      }),
    ).resolves.toMatchObject({ status: "ok" })

    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/explore/pivots/run",
      "/api/explore/pivots/status/job%20%2F%201",
      "/api/explore/pivots/cancel/job%20%2F%201",
      "/api/explore/pivots/members",
    ])
    expect(JSON.parse(String(mockFetch.mock.calls[0]?.[1]?.body))).toMatchObject({
      graph: dummyGraph,
      node_id: "explore",
      pivot: { rows: ["region"] },
      source: "pricing",
      streaming_chunk_size: 500,
    })
    expect(JSON.parse(String(mockFetch.mock.calls[2]?.[1]?.body))).toEqual({})
    expect(JSON.parse(String(mockFetch.mock.calls[3]?.[1]?.body))).toMatchObject({
      graph: dummyGraph,
      node_id: "explore",
      field: "region",
      search: "Nor",
      source: "live",
      streaming_chunk_size: 50,
    })
  })

  it("rejects malformed successful pivot responses", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({ status: "ok", field: "region", members: [], failure: null }),
    )
    await expect(
      runExplorePivot({ graph: dummyGraph, node_id: "explore", pivot: {} }),
    ).rejects.toThrow(/parseExplorePivotRunResponse/i)
  })
})

describe("inferJsonCacheSchema completeness contract", () => {
  const sentBody = () =>
    JSON.parse((mockFetch.mock.calls[0]?.[1] as RequestInit).body as string)

  it("keeps complete inference as the default and gives it a build-sized timeout", async () => {
    // A hidden head sample can silently miss a field that first appears later:
    // build ignores unknown fields, so it is not a completeness backstop.
    mockFetch.mockReturnValue(jsonResponse({ tables: [] }))
    const timeoutSpy = vi.spyOn(globalThis, "setTimeout")

    await inferJsonCacheSchema({ path: "/data/quotes.jsonl" })

    expect(sentBody()).toEqual({ path: "/data/quotes.jsonl" })
    expect(JSON_CACHE_INFER_TIMEOUT_MS).toBe(1_800_000)
    expect(timeoutSpy).toHaveBeenCalledWith(
      expect.any(Function),
      JSON_CACHE_INFER_TIMEOUT_MS,
    )
  })

  it("passes an explicitly requested sample_size through unchanged", async () => {
    mockFetch.mockReturnValue(jsonResponse({ tables: [] }))

    await inferJsonCacheSchema({ path: "/data/quotes.jsonl", sample_size: 50 })

    expect(sentBody().sample_size).toBe(50)
  })
})
