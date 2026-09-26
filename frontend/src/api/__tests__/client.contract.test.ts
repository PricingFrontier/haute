import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import { ApiResponseValidationError } from "../responseValidation"
import {
  applyOptimiser,
  cancelExplorePivot,
  cancelOptimiserFrontierAutoRange,
  getMlflowDestinations,
  getMlflowSettings,
  testMlflowConnection,
  createSubmodel,
  createUtilityFile,
  deleteUtilityFile,
  dissolveSubmodel,
  checkHauteSession,
  estimateOptimiserSolve,
  estimateTrainingRam,
  commitMilestone,
  renderPolarsSteps,
  resolveEditorNodeIdentities,
  resolveOutputDestination,
  writeOutput,
  fetchIoCapabilities,
  fetchExplorePivotMembers,
  buildInputCache,
  fetchSchema,
  getBandingStats,
  getNodeDataPoint,
  getRatingLevels,
  getNodeDataProfile,
  getNodeDataStatus,
  runNodeData,
  cancelNodeData,
  clearNodeData,
  getExplorePivotStatus,
  getMilestones,
  getMilestoneSaves,
  getFrontierStatus,
  getOptimiserFrontierAutoRangeStatus,
  getOptimiserStatus,
  cancelOptimiserSolve,
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
  resolveModelSaveDestination,
  saveTrainedModel,
  listFiles,
  outputAssembleDryRun,
  previewInputs,
  previewNode,
  readUtilityFile,
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
        base_revision: null,
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

  it("resolveEditorNodeIdentities validates the complete strict response", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      identities: [{
        node_id: "source",
        function_name: "node_class",
        default_input_name: "node_class",
        source_handle_input_names: {},
        config_reference: null,
      }],
    }))

    await expect(resolveEditorNodeIdentities({
      nodes: [{
        node_id: "source",
        label: "class",
        node_type: "polars",
        source_handles: [],
      }],
    })).resolves.toEqual({
      identities: [{
        node_id: "source",
        function_name: "node_class",
        default_input_name: "node_class",
        source_handle_input_names: {},
        config_reference: null,
      }],
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/editor-identities")
    expect(JSON.parse(String(init?.body))).toEqual({
      nodes: [{
        node_id: "source",
        label: "class",
        node_type: "polars",
        source_handles: [],
      }],
    })
  })

  it("resolveEditorNodeIdentities rejects nested response drift", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      identities: [{
        node_id: "source",
        function_name: "source",
        default_input_name: "source",
        source_handle_input_names: {},
        config_reference: null,
        unexpected: true,
      }],
    }))

    await expect(resolveEditorNodeIdentities({ nodes: [] })).rejects.toThrow(
      "EditorIdentitiesResponse: invalid contract at /identities/0: additionalProperties",
    )
  })

  it.each([
    ["missing", ["first"]],
    ["reordered", ["second", "first"]],
  ] as const)("resolveEditorNodeIdentities rejects %s response identities", async (
    _case,
    responseNodeIds,
  ) => {
    const identity = (nodeId: string) => ({
      node_id: nodeId,
      function_name: `function_${nodeId}`,
      default_input_name: `input_${nodeId}`,
      source_handle_input_names: {},
      config_reference: null,
    })
    mockFetch.mockReturnValue(jsonResponse({
      identities: responseNodeIds.map(identity),
    }))

    await expect(resolveEditorNodeIdentities({
      nodes: ["first", "second"].map((nodeId) => ({
        node_id: nodeId,
        label: nodeId,
        node_type: "polars",
        source_handles: [],
      })),
    })).rejects.toThrow(/exactly match request node order/)
  })

  it("resolveEditorNodeIdentities rejects incomplete source-handle identity coverage", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      identities: [{
        node_id: "api",
        function_name: "api",
        default_input_name: null,
        source_handle_input_names: { quotes: "quotes" },
        config_reference: "config/quote_input/api.json",
      }],
    }))

    await expect(resolveEditorNodeIdentities({
      nodes: [{
        node_id: "api",
        label: "API",
        node_type: "apiInput",
        source_handles: ["quotes", "vehicles"],
      }],
    })).rejects.toThrow(/source handles must exactly match the request/i)
  })

  it("resolveEditorNodeIdentities rejects invalid default-identity nullability", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      identities: [{
        node_id: "ordinary",
        function_name: "ordinary",
        default_input_name: null,
        source_handle_input_names: {},
        config_reference: null,
      }],
    }))

    await expect(resolveEditorNodeIdentities({
      nodes: [{
        node_id: "ordinary",
        label: "Ordinary",
        node_type: "polars",
        source_handles: [],
      }],
    })).rejects.toThrow(/default input identity/i)
  })

  it("resolveEditorNodeIdentities rejects rewritten API frame identities", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      identities: [{
        node_id: "api",
        function_name: "api",
        default_input_name: null,
        source_handle_input_names: { quotes: "rewritten_quotes" },
        config_reference: "config/quote_input/api.json",
      }],
    }))

    await expect(resolveEditorNodeIdentities({
      nodes: [{
        node_id: "api",
        label: "API",
        node_type: "apiInput",
        source_handles: ["quotes"],
      }],
    })).rejects.toThrow(/API frame identities must preserve raw source handles/i)
  })

  it("renderPolarsSteps resolves rendered code and a failing step as data", async () => {
    const rendered = { ok: true, code: "df = df.filter(x)", step_lines: [[1, 1]], step_index: null, message: "" }
    mockFetch.mockReturnValue(jsonResponse(rendered))

    await expect(
      renderPolarsSteps({ steps: [{ kind: "filter" }], inputNames: ["quotes"], start: "input" }),
    ).resolves.toEqual(rendered)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/polars-steps/render")
    expect(JSON.parse(String(init?.body))).toEqual({
      steps: [{ kind: "filter" }], input_names: ["quotes"], start: "input",
    })

    // A step validation failure is data, not a rejected request.
    const failed = { ok: false, code: "", step_lines: [], step_index: 2, message: "Pick a column." }
    mockFetch.mockReturnValue(jsonResponse(failed))
    await expect(
      renderPolarsSteps({ steps: [], inputNames: [], start: "frame" }),
    ).resolves.toEqual(failed)
  })

  it("renderPolarsSteps rejects a malformed step line range", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      ok: true, code: "df = df", step_lines: [[1, "2"]], step_index: null, message: "",
    }))

    await expect(
      renderPolarsSteps({ steps: [], inputNames: [], start: "frame" }),
    ).rejects.toThrow("PolarsStepsRenderResponse: invalid contract at /step_lines/0/1: type")
  })

  it("fetchIoCapabilities rejects unknown V1 discriminants", async () => {
    mockFetch.mockReturnValue(jsonResponse({ schema_version: 1, groups: [{ name: "file", label: "Files", input_available: true, output_available: true, cache_modes: ["unknown"], input_fields: [], output_fields: [], formats: [] }] }))
    await expect(fetchIoCapabilities()).rejects.toThrow(
      "IoCapabilitiesResponse: invalid contract at /groups/0/cache_modes/0: enum",
    )
  })

  it("input-cache build rejects a malformed V1 response", async () => {
    mockFetch.mockReturnValue(jsonResponse({ schema_version: 2, job_id: "job", identity_digest: "digest", status: "running", joined: false }))
    await expect(buildInputCache({ schema_version: 1, config: {}, refresh: false })).rejects.toThrow(/parseInputCacheBuildResponse/i)
  })

  it("previewInputs asks which inputs a preview reads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ input_node_ids: ["policies"] }))

    const result = await previewInputs({
      graph: dummyGraph,
      nodeId: "n1",
      source: "batch",
      requestedPreviewColumns: ["premium"],
      portLabel: "drivers",
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/pipeline/preview/inputs")
    expect(JSON.parse(String(init?.body))).toEqual({
      graph: dummyGraph,
      node_id: "n1",
      source: "batch",
      requested_preview_columns: ["premium"],
      port_label: "drivers",
    })
    expect(result).toEqual({ input_node_ids: ["policies"] })
  })

  it("previewInputs defaults the source and omits unset columns and port", async () => {
    mockFetch.mockReturnValue(jsonResponse({ input_node_ids: [] }))

    await previewInputs({ graph: dummyGraph, nodeId: "n1" })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph,
      node_id: "n1",
      source: "live",
    })
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

  it("previewNode honours a custom timeout and sends no streaming_chunk_size", async () => {
    // Exercises the non-default path of the `timeout = 120_000` default argument.
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("preview_node")))

    await previewNode({
      graph: dummyGraph,
      nodeId: "n1",
      rowLimit: 10,
      timeout: 5_000,
    })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).not.toHaveProperty(
      "streaming_chunk_size",
    )
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
      traceCell({ graph: dummyGraph, row_index: 0, target_node_id: "n1", seed_plan: [] }),
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

  it("getNodeDataProfile posts the consumer node and parses the profile", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("node_data_profile_response")))

    const result = await getNodeDataProfile({ graph: dummyGraph, node_id: "explore" })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/node-data/profile")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "explore", source: "live",
    })
    expect(result.status).toBe("completed")
    expect(result.result?.row_count).toBe(150)
    expect(result.result?.data_version).toBe(result.point.data_version)
  })
  it("getNodeDataPoint posts the consumer node and parses its point", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("node_data_point_response")))

    const result = await getNodeDataPoint({ graph: dummyGraph, node_id: "explore" })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/node-data/point")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "explore", source: "live",
    })
    expect(result.slot_key).toBe("join||live")
    expect(result.state).toBe("partial")
    expect(result.generation?.columns).toEqual(["premium", "region"])
    expect(result.job?.job_id).toBe("node-data-7f2c")
  })

  it("getBandingStats sends only the limits asked for, under the names the server reads", async () => {
    const stats = {
      status: "ok",
      point: loadUiContractFixture("node_data_point_response"),
      data_version: "v1",
      total_rows: 10,
      null_count: 1,
      non_finite_count: 0,
      minimum: 0,
      maximum: 5,
      bins: [{ lower: 0, upper: 5, count: 9 }],
      values: [],
      distinct_count: null,
      other_count: null,
      rule_counts: [],
      unmatched_count: null,
    }
    mockFetch.mockReturnValue(jsonResponse(stats))
    const factor = { column: "age" }

    const result = await getBandingStats({ graph: dummyGraph, node_id: "banding", factor })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/banding/stats")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "banding", source: "live", factor,
    })
    expect(result.total_rows).toBe(10)
    expect(result.bins).toEqual([{ lower: 0, upper: 5, count: 9 }])

    mockFetch.mockReturnValue(jsonResponse(stats))
    await getBandingStats({
      graph: dummyGraph, node_id: "banding", source: "batch", factor, histogramBins: 20, valueLimit: 50,
    })

    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toEqual({
      graph: dummyGraph,
      node_id: "banding",
      source: "batch",
      factor,
      histogram_bins: 20,
      value_limit: 50,
    })

    mockFetch.mockReturnValue(jsonResponse({ ...stats, bins: [{ lower: 0, upper: 5, count: "many" }] }))
    await expect(
      getBandingStats({ graph: dummyGraph, node_id: "banding", factor }),
    ).rejects.toThrow("BandingStatsResponse: invalid contract at /bins/0/count: type")
  })

  it("getRatingLevels posts the columns and parses the levels of each one", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("rating_levels_response")))

    const result = await getRatingLevels({
      graph: dummyGraph, node_id: "rating", columns: ["region", "cover"],
    })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/rating/levels")
    // No limit asked for is no limit sent: the server owns the default.
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "rating", source: "live", columns: ["region", "cover"],
    })
    expect(result.status).toBe("ok")
    expect(result.total_rows).toBe(1000)
    expect(result.columns.map(column => column.column)).toEqual(["region", "cover"])
    expect(result.columns[0].values[2]).toEqual({ value: "Orkney", count: 1 })
    expect(result.columns[1].null_count).toBe(100)
    expect(result.data_version).toBe(result.point.data_version)
  })

  it("getRatingLevels sends a value limit under the name the server reads", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("rating_levels_response")))

    await getRatingLevels({
      graph: dummyGraph, node_id: "rating", columns: ["region"], valueLimit: 25, source: "staging",
    })

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "rating", source: "staging", columns: ["region"], value_limit: 25,
    })
  })

  it("getRatingLevels refuses a response that is not the levels contract", async () => {
    const fixture = loadUiContractFixture("rating_levels_response") as Record<string, unknown>
    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        columns: [
          { column: "region", values: [{ value: "North", count: "many" }], distinct_count: 1, null_count: 0 },
        ],
      }),
    )

    await expect(
      getRatingLevels({ graph: dummyGraph, node_id: "rating", columns: ["region"] }),
    ).rejects.toThrow("RatingLevelsResponse: invalid contract at /columns/0/values/0/count: type")
  })

  it("runNodeData sends refresh with no streaming chunk size, and parses the started job", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("node_data_run_response")))

    const result = await runNodeData({
      graph: dummyGraph, node_id: "banding", refresh: true,
    })

    expect(mockFetch.mock.calls[0][0]).toBe("/api/node-data/run")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      graph: dummyGraph, node_id: "banding", source: "live", refresh: true,
    })
    expect(result.status).toBe("started")
    expect(result.job_id).toBe("node-data-7f2c")
    expect(result.point.demand).toEqual(["premium"])
  })

  it("getNodeDataStatus, cancelNodeData and clearNodeData parse their terminal payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("node_data_status_response")))
    const status = await getNodeDataStatus("node-data-7f2c")
    const cancelled = await cancelNodeData("node-data-7f2c")
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("node_data_clear_response")))
    const cleared = await clearNodeData({ graph: dummyGraph, node_id: "explore" })

    expect(status.outcome).toBe("published")
    expect(status.generation_id).toBe("b41f0a2c9d5e4f7a")
    expect(cancelled.status).toBe("completed")
    expect(cleared.status).toBe("cleared")
    expect(cleared.point.state).toBe("missing")
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

    await expect(getTrainStatus("job-1")).rejects.toMatchObject({
      name: "ApiResponseValidationError",
      message: expect.stringMatching(/could not read training status.*parseTrainResponse/i),
      cause: expect.any(Error),
    })
    await expect(getTrainStatus("job-1")).rejects.toBeInstanceOf(ApiResponseValidationError)
  })

  it("getOptimiserStatus reports a frontier point missing a constraint as a read error", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    const frontier = fixture.frontier as Record<string, unknown>
    const [point] = frontier.points as Record<string, unknown>[]
    mockFetch.mockReturnValue(
      jsonResponse({
        ...fixture,
        frontier: {
          ...frontier,
          points: [{ ...point, totals: {}, thresholds: {}, bounds: {}, lambdas: {} }],
        },
      }),
    )

    await expect(getOptimiserStatus("job-1")).rejects.toMatchObject({
      name: "ApiResponseValidationError",
      message:
        "Could not read optimiser status: parseOptimiserStatusResponse: expected frontier.points[0].thresholds "
        + "to hold exactly the constraint names [loss], got []",
    })
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

    await expect(getOptimiserStatus("job-1")).rejects.toMatchObject({
      name: "ApiResponseValidationError",
      message: "Could not read optimiser status: OptimiserStatusResponse: invalid contract at /result/lambdas/loss: type",
      cause: expect.any(Error),
    })
    await expect(getOptimiserStatus("job-1")).rejects.toBeInstanceOf(ApiResponseValidationError)
  })

  it("cancelOptimiserSolve posts to the job's cancel route and validates the status it returns", async () => {
    const fixture = loadUiContractFixture<Record<string, unknown>>("optimiser_status_response")
    mockFetch.mockReturnValueOnce(jsonResponse({ ...fixture, status: "cancelled", result: null, frontier: null }))

    await expect(cancelOptimiserSolve("job 1")).resolves.toMatchObject({ status: "cancelled" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/optimiser/solve/cancel/job%201")
    expect(opts.method).toBe("POST")

    mockFetch.mockReturnValueOnce(jsonResponse({ ...fixture, elapsed_seconds: "bad" }))
    await expect(cancelOptimiserSolve("job-1")).rejects.toBeInstanceOf(ApiResponseValidationError)
  })

  it("getNodeDataStatus rejects malformed status payloads as response validation errors", async () => {
    // The background poller only stops on an ApiResponseValidationError, so a
    // malformed reply must arrive as one instead of retrying for a day.
    mockFetch.mockReturnValue(
      jsonResponse({ ...loadUiContractFixture<Record<string, unknown>>("node_data_status_response"), progress: "bad" }),
    )

    await expect(getNodeDataStatus("node-data-7f2c")).rejects.toMatchObject({
      name: "ApiResponseValidationError",
      message: expect.stringMatching(/could not read node data status.*parseNodeDataStatusResponse/i),
      cause: expect.any(Error),
    })
  })

  it("getExplorePivotStatus rejects malformed status payloads as response validation errors", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({ ...loadUiContractFixture<Record<string, unknown>>("explore_pivot_status_response"), progress: "bad" }),
    )

    await expect(getExplorePivotStatus("pivot-job-1")).rejects.toMatchObject({
      name: "ApiResponseValidationError",
      message: "Could not read pivot status: ExplorePivotStatusResponse: invalid contract at /progress: type",
      cause: expect.any(Error),
    })
  })

  it("getFrontierStatus checks the frontier job and its one summary per point", async () => {
    const frontier = loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_response")
    const status = (result: unknown) => ({
      status: "completed",
      progress: 1,
      message: "Frontier ready",
      elapsed_seconds: 3,
      result,
      terminal_reason: null,
      error_code: null,
      http_status_code: null,
      error_detail: null,
      execution_metrics: null,
    })

    mockFetch.mockReturnValue(jsonResponse(status(frontier)))
    const parsed = await getFrontierStatus("frontier-job-1")
    expect(mockFetch.mock.calls[0][0]).toBe("/api/optimiser/frontier/status/frontier-job-1")
    expect(parsed.result?.points[0]?.total_objective).toBe(125)
    expect(parsed.result?.point_summaries[0]?.lambdas).toEqual({ loss: 0.3 })

    const summaries = frontier.point_summaries as Record<string, unknown>[]
    mockFetch.mockReturnValue(jsonResponse(status({
      ...frontier,
      point_summaries: [{ ...summaries[0], converged: "yes" }],
    })))
    await expect(getFrontierStatus("frontier-job-1")).rejects.toThrow(
      "OptimiserFrontierStatusResponse: invalid contract at /result/point_summaries/0/converged: type",
    )

    mockFetch.mockReturnValue(jsonResponse(status({ ...frontier, point_summaries: [] })))
    await expect(getFrontierStatus("frontier-job-1")).rejects.toThrow(
      "parseOptimiserStatusResponse: expected result.point_summaries to hold one summary per point, got 0 for 1 points",
    )
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
        terminal_reason: null,
        error_code: null,
        http_status_code: null,
        error_detail: null,
        execution_metrics: null,
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
        terminal_reason: null,
        error_code: null,
        http_status_code: null,
        error_detail: null,
        execution_metrics: null,
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

  it("posts trained model saves and destination previews to the modelling save routes", async () => {
    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("model_save_destination_response")))
    const destination = await resolveModelSaveDestination({ output_path: "frequency", algorithm: "catboost" })
    expect(mockFetch.mock.calls[0][0]).toBe("/api/modelling/save/destination")
    expect(mockFetch.mock.calls[0][1]?.method).toBe("POST")
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      output_path: "frequency",
      algorithm: "catboost",
    })
    expect(destination.path).toBe("models/frequency.cbm")

    mockFetch.mockReturnValue(jsonResponse(loadUiContractFixture("model_save_response")))
    const saved = await saveTrainedModel({ job_id: "job-1", output_path: "frequency", overwrite: true })
    expect(mockFetch.mock.calls[1][0]).toBe("/api/modelling/save")
    expect(mockFetch.mock.calls[1][1]?.method).toBe("POST")
    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toEqual({
      job_id: "job-1",
      output_path: "frequency",
      overwrite: true,
    })
    expect(saved.feature_contract_path).toBe("models/frequency.feature_contract.json")
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
    await logOptimiserToMlflow({ job_id: "opt-job-1", point_index: 3, destination: "" })
    expect(JSON.parse(String(mockFetch.mock.calls[2][1]?.body))).toMatchObject({
      job_id: "opt-job-1",
      point_index: 3,
    })
  })

  it("getMilestones rejects malformed milestone payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      working_branch: "w",
      entries: [{ sha: 123, short_sha: "s", message: "m", timestamp: "t", version_label: null, is_root: false }],
    }))
    await expect(getMilestones()).rejects.toThrow("GitMilestonesResponse: invalid contract at /entries/0/sha: type")
  })

  it("getMilestoneSaves rejects malformed ledger-save payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ saves: [{ sha: 123, short_sha: "s", message: "m", timestamp: "t", files: [] }] }))
    await expect(getMilestoneSaves("abc")).rejects.toThrow("GitLedgerSavesResponse: invalid contract at /saves/0/sha: type")
  })

  it("getPendingSaves rejects malformed ledger-save payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ saves: [{ sha: "a", short_sha: "a", message: 5, timestamp: "t", files: [] }] }))
    await expect(getPendingSaves()).rejects.toThrow("GitLedgerSavesResponse: invalid contract at /saves/0/message: type")
  })

  it("commitMilestone rejects malformed commit payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ sha: 42, short_sha: "x", working_branch: "dev", version_label: null }))
    await expect(commitMilestone("m", null)).rejects.toThrow("GitCommitResponse: invalid contract at /sha: type")
  })

  it("getWorkingBranches rejects malformed branch payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      current: "demo",
      branches: [{ name: 123, is_current: true, is_archived: false, has_unmerged_saves: false, has_uncommitted_changes: false }],
    }))
    await expect(getWorkingBranches()).rejects.toThrow("GitWorkingBranchesResponse: invalid contract at /branches/0/name: type")
  })

  it("restoreBranch rejects malformed restore payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ restored_as: 1 }))
    await expect(restoreBranch("archive/x")).rejects.toThrow("GitRestoreResponse: invalid contract at /restored_as: type")
  })

  it("createWorkingBranch rejects malformed fork payloads", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: "x", moved: "no", switched: true, last_save_sha: null }))
    await expect(createWorkingBranch("x")).rejects.toThrow(
      "GitCreateWorkingBranchResponse: invalid contract at /moved: type",
    )
  })

  it("getGitPrefs rejects prefs without the flag the server always sends", async () => {
    mockFetch.mockReturnValue(jsonResponse({}))
    await expect(getGitPrefs()).rejects.toThrow("GitPrefs: invalid contract at /skip_switch_confirm: required")
  })

})

describe("next-wave client runtime contracts", () => {
  const malformedCases: Array<{
    name: string
    response: Record<string, unknown>
    call: () => Promise<unknown>
    error: RegExp | string
  }> = [
    {
      name: "getNodeDataPoint",
      response: { ...loadUiContractFixture<Record<string, unknown>>("node_data_point_response"), state: "warm" },
      call: () => getNodeDataPoint({ graph: dummyGraph, node_id: "explore" }),
      error: /parseNodeDataPointResponse/i,
    },
    {
      name: "runNodeData",
      response: { ...loadUiContractFixture<Record<string, unknown>>("node_data_run_response"), status: "queued" },
      call: () => runNodeData({ graph: dummyGraph, node_id: "banding" }),
      error: /parseNodeDataRunResponse/i,
    },
    {
      name: "getNodeDataStatus",
      response: { ...loadUiContractFixture<Record<string, unknown>>("node_data_status_response"), outcome: "kept" },
      call: () => getNodeDataStatus("node-data-7f2c"),
      error: /parseNodeDataStatusResponse/i,
    },
    {
      name: "clearNodeData",
      response: { ...loadUiContractFixture<Record<string, unknown>>("node_data_clear_response"), status: "gone" },
      call: () => clearNodeData({ graph: dummyGraph, node_id: "explore" }),
      error: /parseNodeDataClearResponse/i,
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
      name: "getMlflowDestinations",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_destinations_response"), destinations: "none" },
      call: () => getMlflowDestinations(true),
      error: "MlflowDestinationsResponse: invalid contract at /destinations: type",
    },
    {
      name: "getMlflowSettings",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_settings_response"), section_present: "yes" },
      call: () => getMlflowSettings(),
      error: "MlflowSettingsResponse: invalid contract at /section_present: type",
    },
    {
      name: "testMlflowConnection",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_test_connection_response"), ok: "no" },
      call: () => testMlflowConnection(),
      error: "MlflowTestConnectionResponse: invalid contract at /ok: type",
    },
    {
      name: "estimateTrainingRam",
      response: { ...loadUiContractFixture<Record<string, unknown>>("train_estimate_response"), estimated_mb: "bad" },
      call: () => estimateTrainingRam({ graph: dummyGraph, node_id: "model1" }),
      error: "TrainEstimateResponse: invalid contract at /estimated_mb: type",
    },
    {
      name: "logToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("train_mlflow_log_response"), run_id: 42 },
      call: () => logToMlflow({ job_id: "job-1", destination: "" }),
      error: "LogExperimentResponse: invalid contract at /run_id: type",
    },
    {
      name: "saveTrainedModel",
      response: { ...loadUiContractFixture<Record<string, unknown>>("model_save_response"), path: 42 },
      call: () => saveTrainedModel({ job_id: "job-1", output_path: "frequency", overwrite: false }),
      error: "SaveModelResponse: invalid contract at /path: type",
    },
    {
      name: "resolveModelSaveDestination",
      response: { ...loadUiContractFixture<Record<string, unknown>>("model_save_destination_response"), suffix_mismatch: 1 },
      call: () => resolveModelSaveDestination({ output_path: "frequency", algorithm: "catboost" }),
      error: "ModelSaveDestinationResponse: invalid contract at /suffix_mismatch: type",
    },
    {
      name: "solveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("solve_optimiser_response"), job_id: 42 },
      call: () => solveOptimiser({ graph: dummyGraph, node_id: "opt1" }),
      error: "OptimiserSolveResponse: invalid contract at /job_id: type",
    },
    {
      name: "estimateOptimiserSolve",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_estimate_response"), total_rows: "bad" },
      call: () => estimateOptimiserSolve({ graph: dummyGraph, node_id: "opt1" }),
      error: "OptimiserEstimateResponse: invalid contract at /total_rows: type",
    },
    {
      name: "applyOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_apply_response"), constraints: { loss: "bad" } },
      call: () => applyOptimiser({ job_id: "opt-job-1" }),
      error: "OptimiserApplyResponse: invalid contract at /constraints/loss: type",
    },
    {
      name: "saveOptimiser",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_save_response"), message: 42 },
      call: () => saveOptimiser({ job_id: "opt-job-1", output_path: "output.py" }),
      error: "OptimiserSaveResponse: invalid contract at /message: type",
    },
    {
      name: "logOptimiserToMlflow",
      response: { ...loadUiContractFixture<Record<string, unknown>>("mlflow_log_response"), tracking_uri: 42 },
      call: () => logOptimiserToMlflow({ job_id: "opt-job-1", destination: "" }),
      error: "OptimiserMlflowLogResponse: invalid contract at /tracking_uri: type",
    },
    {
      name: "startOptimiserFrontierAutoRange",
      response: { status: "started", job_id: 42, error: null },
      call: () => startOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1" }),
      error: "OptimiserFrontierAutoRangeStartResponse: invalid contract at /job_id: type",
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
        terminal_reason: null,
        error_code: null,
        http_status_code: null,
        error_detail: null,
        execution_metrics: null,
      },
      call: () => getOptimiserFrontierAutoRangeStatus("range-job-1"),
      error: "OptimiserFrontierAutoRangeStatusResponse: invalid contract at /result/ranges/expected_margin/min: type",
    },
    {
      name: "cancelOptimiserFrontierAutoRange",
      response: {
        status: "cancelled",
        progress: 1,
        message: "Cancelled",
        elapsed_seconds: "bad",
        result: null,
        terminal_reason: null,
        error_code: null,
        http_status_code: null,
        error_detail: null,
        execution_metrics: null,
      },
      call: () => cancelOptimiserFrontierAutoRange("range-job-1"),
      error: "OptimiserFrontierAutoRangeStatusResponse: invalid contract at /elapsed_seconds: type",
    },
    {
      name: "selectFrontierPoint",
      response: { ...loadUiContractFixture<Record<string, unknown>>("optimiser_frontier_select_response"), lambdas: { loss: "bad" } },
      call: () => selectFrontierPoint({ job_id: "opt-job-1", point_index: 0 }),
      error: "OptimiserFrontierSelectResponse: invalid contract at /lambdas/loss: type",
    },
    {
      name: "listUtilityFiles",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_list_response"), files: "bad" },
      call: () => listUtilityFiles(),
      error: /UtilityListResponse: invalid contract at \/files: type/,
    },
    {
      name: "readUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_read_response"), content: 42 },
      call: () => readUtilityFile("helpers"),
      error: /UtilityReadResponse: invalid contract at \/content: type/,
    },
    {
      name: "createUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), import_line: 42 },
      call: () => createUtilityFile({ name: "helpers", content: "" }),
      error: /UtilityWriteResponse: invalid contract at \/import_line: type/,
    },
    {
      name: "updateUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_write_response"), error_line: "bad" },
      call: () => updateUtilityFile("helpers", "print('x')"),
      error: /UtilityWriteResponse: invalid contract at \/error_line: type/,
    },
    {
      name: "deleteUtilityFile",
      response: { ...loadUiContractFixture<Record<string, unknown>>("utility_delete_response"), module: 42 },
      call: () => deleteUtilityFile("helpers"),
      error: /UtilityDeleteResponse: invalid contract at \/module: type/,
    },
    {
      name: "gitArchiveBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_archive_response"), archived_as: 42 },
      call: () => gitArchiveBranch("feat/pricing-improvements"),
      error: "GitArchiveResponse: invalid contract at /archived_as: type",
    },
    {
      name: "gitDeleteBranch",
      response: { ...loadUiContractFixture<Record<string, unknown>>("git_delete_branch_response"), branch: 42 },
      call: () => gitDeleteBranch("feat/pricing-improvements"),
      error: "GitDeleteBranchResponse: invalid contract at /branch: type",
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
      entries: [{ sha: "a", short_sha: "a", message: "init", timestamp: "today", version_label: null, is_root: true, parents: [] }],
    }],
  }

  const cases: Array<{
    name: string
    body: unknown
    call: () => Promise<unknown>
    url: string
    method?: string
    malformed: unknown
    /** The exact failure, when a generated validator checks the response. */
    malformedError?: string
  }> = [
    { name: "checkHauteSession", body: { ok: true }, call: () => checkHauteSession(), url: "/api/session", malformed: { ok: "yes" }, malformedError: "SessionStatusResponse: invalid contract at /ok: type" },
    { name: "outputAssembleDryRun", body: { status: "ok", document: [], row_count: 0, error: null }, call: () => outputAssembleDryRun({ graph: dummyGraph, nodeId: "out", outputMapping: [] }), url: "/api/output-assemble/dry-run", method: "POST", malformed: { status: "ok", document: [], row_count: "1" } },
    { name: "inferJsonCacheSchema", body: { tables: [{ name: "drivers" }] }, call: () => inferJsonCacheSchema({ path: "/data/input.json" }), url: "/api/json-cache/infer", method: "POST", malformed: { tables: ["bad"] } },
    { name: "getExperiments", body: [{ experiment_id: "1", name: "pricing" }], call: () => getExperiments(""), url: "/api/mlflow/experiments", malformed: [{ experiment_id: "1" }], malformedError: "MlflowExperimentList: invalid contract at /0/name: required" },
    { name: "getRuns", body: [{ run_id: "r", run_name: "baseline", status: "FINISHED", start_time: null, metrics: { auc: 0.9 }, params: {}, artifacts: [] }], call: () => getRuns("exp", "model", ""), url: "/api/mlflow/runs?experiment_id=exp&artifact_filter=model", malformed: [{ run_id: "r", run_name: "baseline", status: "FINISHED", start_time: null, metrics: {}, params: {}, artifacts: [1] }], malformedError: "MlflowRunList: invalid contract at /0/artifacts/0: type" },
    { name: "getModels", body: [{ name: "pricing", latest_versions: [{ version: "1", status: "READY", run_id: "r" }] }], call: () => getModels(""), url: "/api/mlflow/models", malformed: [{ name: "pricing", latest_versions: [{ version: "1", status: "READY" }] }], malformedError: "MlflowModelList: invalid contract at /0/latest_versions/0/run_id: required" },
    { name: "getModelVersions", body: [{ version: "1", run_id: "r", status: "READY", creation_timestamp: null, description: "baseline", params: {}, aliases: [] }], call: () => getModelVersions("pricing model", ""), url: "/api/mlflow/model-versions?model_name=pricing%20model", malformed: [{ version: "1", run_id: "r", status: "READY", creation_timestamp: null, params: {}, aliases: [] }], malformedError: "MlflowModelVersionList: invalid contract at /0/description: required" },
    { name: "listFiles", body: { dir: "/data", items: [{ name: "data", path: "/data", type: "directory", size: null }] }, call: () => listFiles("/data", ".json"), url: "/api/files?dir=%2Fdata&extensions=.json", malformed: { dir: "/data", items: [{ name: "data", path: "/data", type: "other", size: null }] }, malformedError: "BrowseFilesResponse: invalid contract at /items/0/type: enum" },
    { name: "getGitGraph", body: validGitGraph, call: () => getGitGraph(5), url: "/api/git/graph?limit=5", malformed: { ...validGitGraph, branches: [{ ...validGitGraph.branches[0], entries: [{ ...validGitGraph.branches[0].entries[0], parents: [1] }] }] }, malformedError: "GitGraphResponse: invalid contract at /branches/0/entries/0/parents/0: type" },
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
      await expect(testCase.call()).rejects.toThrow(testCase.malformedError)
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
    })
    expect(JSON.parse(String(mockFetch.mock.calls[2]?.[1]?.body))).toEqual({})
    expect(JSON.parse(String(mockFetch.mock.calls[3]?.[1]?.body))).toMatchObject({
      graph: dummyGraph,
      node_id: "explore",
      field: "region",
      search: "Nor",
      source: "live",
    })
  })

  it("rejects malformed successful pivot responses", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({ status: "ok", field: "region", members: [], failure: null }),
    )
    await expect(
      runExplorePivot({ graph: dummyGraph, node_id: "explore", pivot: {} }),
    ).rejects.toThrow("ExplorePivotRunResponse: invalid contract at /job_id: required")
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
