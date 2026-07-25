import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { loadUiContractFixture } from "../../testSupport/uiContractFixtures"
import {
  ApiError,
  ApiTimeoutError,
  HAUTE_SESSION_EXPIRED_EVENT,
  checkHauteSession,
  hauteSessionToken,
  isHauteSessionExpiredError,
  loadPipeline,
  previewNode,
  savePipeline,
  traceCell,
  writeOutput,
  buildInputCache,
  getInputCacheJob,
  cancelInputCacheJob,
  getInputCacheStatus,
  clearInputCache,
  fetchSchema,
  trainModel,
  solveOptimiser,
  listFiles,
  createSubmodel,
  checkMlflow,
  getTrainStatus,
  estimateTrainingRam,
  getWarehouses,
  getCatalogs,
  getGitStatus,
  gitArchiveBranch,
  gitDeleteBranch,
  getMilestones,
  getMilestoneSaves,
  getPendingSaves,
  getGitGraph,
  commitMilestone,
  getWorkingBranches,
  getGitRemotes,
  gitPush,
  gitFastForward,
  gitBranchAway,
  restoreBranch,
  undeleteBranch,
  getWorkingBranch,
  setWorkingBranch,
  setGitIdentity,
  setGitPrefs,
  getCommitPipeline,
  getCommitContext,
  moveToVersion,
  listUtilityFiles,
  readUtilityFile,
  createUtilityFile,
  updateUtilityFile,
  deleteUtilityFile,
  buildJsonCache,
  cancelJsonCache,
  getJsonCacheProgress,
  getJsonCacheStatus,
  deleteJsonCache,
} from "../client"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let mockFetch: ReturnType<typeof vi.fn>

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

function errorResponse(status: number, body?: unknown) {
  return Promise.resolve({
    ok: false,
    status,
    statusText: "Error",
    json: body !== undefined
      ? () => Promise.resolve(body)
      : () => Promise.reject(new Error("no body")),
  })
}

const dummyGraph = {
  nodes: [{ id: "n1", type: "custom", position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

function makePreviewResponse() {
  return {
    status: "ok",
    node_id: "node1",
    row_count: 1,
    column_count: 1,
    columns: [{ name: "x", dtype: "Int64" }],
    available_columns: [{ name: "x", dtype: "Int64" }],
    preview: [{ x: 1 }],
    error: null,
    error_line: null,
    timing_ms: 0,
    memory_bytes: 0,
    timings: [],
    memory: [],
    schema_warnings: [],
    node_statuses: {},
  }
}

function makeSavePipelineResponse() {
  return {
    status: "saved",
    file: "pipe.py",
    pipeline_name: "test",
    warnings: [],
  }
}

function makeTraceResponse() {
  return {
    status: "ok",
    trace: {
      target_node_id: "n1",
      row_index: 0,
      column: null,
      output_value: 1,
      steps: [],
      row_id_column: null,
      row_id_value: null,
      total_nodes_in_pipeline: 1,
      nodes_in_trace: 1,
      execution_ms: 0,
      waterfall: null,
      omissions: [],
      correlation_diagnostics: [],
      generated_at: "2026-07-23T12:00:00+00:00",
      pipeline_source: null,
      execution_origin: "fresh_execution",
    },
  }
}

function makeSchemaResponse() {
  return {
    path: "data/example.parquet",
    columns: [],
    row_count: 1,
    row_count_estimated: false,
    column_count: 0,
    preview: [],
  }
}

function makeTrainResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "started",
    job_id: "job-1",
    metrics: {},
    feature_importance: [],
    model_path: "",
    train_rows: 0,
    test_rows: 0,
    holdout_rows: 0,
    holdout_metrics: {},
    diagnostics_set: "validation",
    features: [],
    cat_features: [],
    error: null,
    best_iteration: null,
    loss_history: [],
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
    total_source_rows: null,
    glm_coefficients: [],
    glm_relativities: [],
    glm_fit_statistics: {},
    glm_regularization_path: null,
    diagnostics_errors: [],
    ...overrides,
  }
}

function makeTrainStatusResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "running",
    progress: 0.1,
    message: "working",
    iteration: 1,
    total_iterations: 10,
    train_loss: {},
    elapsed_seconds: 1,
    result: null,
    warning: null,
    ...overrides,
  }
}

function makeSubmodelCreateResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    submodel_file: "pricing.py",
    parent_file: "main.py",
    graph: {
      nodes: [dummyGraph.nodes[0]],
      edges: [],
      submodels: { pricing: { path: "pricing.py" } },
    },
    ...overrides,
  }
}

function makeSubmodelGraphResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    submodel_name: "pricing",
    graph: {
      nodes: [dummyGraph.nodes[0]],
      edges: [],
    },
    ...overrides,
  }
}

function makeDissolveSubmodelResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    graph: {
      nodes: dummyGraph.nodes,
      edges: dummyGraph.edges,
    },
    ...overrides,
  }
}

function makeTrainEstimateResponse(overrides: Record<string, unknown> = {}) {
  return {
    total_rows: 1000,
    safe_row_limit: 5000,
    estimated_mb: 12.5,
    training_mb: 25,
    available_mb: 512,
    bytes_per_row: 256,
    was_downsampled: false,
    warning: null,
    gpu_vram_estimated_mb: null,
    gpu_vram_available_mb: null,
    gpu_warning: null,
    ...overrides,
  }
}

function makeSolveOptimiserResponse(overrides: Record<string, unknown> = {}) {
  return {
    status: "started",
    job_id: "opt-job-1",
    error: null,
    ...overrides,
  }
}

function makeGitStatusResponse(overrides: Record<string, unknown> = {}) {
  return {
    branch: "main",
    is_main: true,
    is_read_only: false,
    changed_files: [],
    main_ahead: false,
    main_ahead_by: 0,
    main_last_updated: null,
    ...overrides,
  }
}

function makeJsonCacheBuildResponse(overrides: Record<string, unknown> = {}) {
  return {
    path: "/data/input.json",
    data_path: "/data/input.parquet",
    row_count: 10,
    column_count: 2,
    columns: { x: "Int64" },
    size_bytes: 128,
    cached_at: 123,
    cache_seconds: 0.5,
    ...overrides,
  }
}

function makeJsonCacheProgressResponse(overrides: Record<string, unknown> = {}) {
  return {
    active: true,
    rows: 10,
    elapsed: 0.5,
    phase: "scan",
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Setup / Teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
  delete window.__HAUTE_SESSION_TOKEN__
})

// ═══════════════════════════════════════════════════════════════════════════
// request() core function — tested through loadPipeline (a thin GET wrapper)
// ═══════════════════════════════════════════════════════════════════════════

describe("request() core via loadPipeline", () => {
  it("makes a GET request to the correct URL", async () => {
    mockFetch.mockReturnValue(jsonResponse({ nodes: [], edges: [] }))
    await loadPipeline()
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline")
  })

  it("attaches the local session token header when present", async () => {
    window.__HAUTE_SESSION_TOKEN__ = "frontend-session-token"
    mockFetch.mockReturnValue(jsonResponse({ nodes: [], edges: [] }))

    await loadPipeline()

    const [, options] = mockFetch.mock.calls[0]
    expect(options.headers["x-haute-session-token"]).toBe("frontend-session-token")
    expect(hauteSessionToken()).toBe("frontend-session-token")
  })

  it("returns parsed JSON on success", async () => {
    const data = { nodes: [{ id: "1" }], edges: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await loadPipeline()
    expect(result).toEqual(data)
  })

  it("throws ApiError with status and detail on 4xx response", async () => {
    mockFetch.mockReturnValue(errorResponse(422, { detail: "Validation failed" }))
    // loadPipeline catches 404 specifically, so use 422 to test the generic path
    await expect(loadPipeline()).rejects.toThrow(ApiError)
    try {
      await loadPipeline()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(422)
      expect((err as ApiError).detail).toBe("Validation failed")
    }
  })

  it("emits a session-expired event for local session token mismatches", async () => {
    const listener = vi.fn()
    window.addEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
    mockFetch.mockReturnValue(errorResponse(403, {
      detail: "Missing or invalid Haute session token",
    }))

    await expect(checkMlflow()).rejects.toThrow(ApiError)

    expect(listener).toHaveBeenCalledTimes(1)
    const event = listener.mock.calls[0][0] as CustomEvent<{ reason: string }>
    expect(event.detail.reason).toBe("Missing or invalid Haute session token")

    window.removeEventListener(HAUTE_SESSION_EXPIRED_EVENT, listener)
  })

  it("checks the protected session status endpoint without retrying expired tokens", async () => {
    mockFetch.mockReturnValue(errorResponse(403, {
      detail: "Missing or invalid Haute session token",
    }))

    try {
      await checkHauteSession()
      throw new Error("expected checkHauteSession to reject")
    } catch (err) {
      expect(isHauteSessionExpiredError(err)).toBe(true)
    }

    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/session")
  })

  it("throws ApiError with status and detail on 5xx response", async () => {
    mockFetch.mockReturnValue(errorResponse(500, { detail: "Internal server error" }))
    await expect(loadPipeline()).rejects.toThrow(ApiError)
  })

  it("uses statusText as detail when response body is not JSON", async () => {
    mockFetch.mockReturnValue(errorResponse(503))
    try {
      // Use checkMlflow as it doesn't catch errors like loadPipeline
      await checkMlflow()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).detail).toBe("Error")
    }
  })

  it("handles network error (fetch throws)", async () => {
    mockFetch.mockRejectedValue(new TypeError("Failed to fetch"))
    await expect(checkMlflow()).rejects.toThrow("Failed to fetch")
  })

  it("passes AbortController signal to fetch", async () => {
    mockFetch.mockReturnValue(jsonResponse({ nodes: [], edges: [] }))
    await loadPipeline()
    const [, options] = mockFetch.mock.calls[0]
    expect(options.signal).toBeInstanceOf(AbortSignal)
  })

  it("loadPipeline returns empty graph on 404", async () => {
    mockFetch.mockReturnValue(errorResponse(404, { detail: "Not found" }))
    const result = await loadPipeline()
    expect(result).toEqual({ nodes: [], edges: [] })
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// POST requests — tested through specific endpoints
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// Per-endpoint contract tests
// ═══════════════════════════════════════════════════════════════════════════

describe("endpoint contracts", () => {
  beforeEach(() => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/pipeline/preview") return jsonResponse(makePreviewResponse())
      if (url === "/api/pipeline/save") return jsonResponse(makeSavePipelineResponse())
      if (url === "/api/pipeline/write-output") return jsonResponse({ status: "ok", message: "Written", row_count: 3, path: "out.parquet", format: "parquet" })
      if (url === "/api/pipeline/trace") return jsonResponse(makeTraceResponse())
      if (url.startsWith("/api/schema")) return jsonResponse(makeSchemaResponse())
      if (url === "/api/modelling/train") return jsonResponse(makeTrainResponse())
      if (url === "/api/modelling/train/status/job-123") return jsonResponse(makeTrainStatusResponse())
      if (url === "/api/modelling/estimate") return jsonResponse(makeTrainEstimateResponse())
      if (url === "/api/optimiser/solve") return jsonResponse(makeSolveOptimiserResponse())
      if (url === "/api/submodel/create") return jsonResponse(makeSubmodelCreateResponse())
      if (url === "/api/submodel/dissolve") return jsonResponse(makeDissolveSubmodelResponse())
      if (url === "/api/submodel/pricing") return jsonResponse(makeSubmodelGraphResponse())
      if (url === "/api/databricks/warehouses") return jsonResponse({ warehouses: [] })
      if (url === "/api/databricks/catalogs") return jsonResponse({ catalogs: [] })
      return jsonResponse({})
    })
  })

  it("previewNode posts to /api/pipeline/preview with correct body", async () => {
    await previewNode({ graph: dummyGraph, nodeId: "node1", rowLimit: 50, source: "live" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/preview")
    expect(opts.method).toBe("POST")
    const body = JSON.parse(opts.body)
    expect(body.graph).toEqual(dummyGraph)
    expect(body.node_id).toBe("node1")
    expect(body.row_limit).toBe(50)
    expect(body.source).toBe("live")
  })

  it("savePipeline posts to /api/pipeline/save", async () => {
    const payload = {
      name: "test",
      description: "desc",
      graph: dummyGraph,
      preamble: "",
      source_file: "pipe.py",
    }
    await savePipeline(payload)
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/save")
    expect(opts.method).toBe("POST")
    const body = JSON.parse(opts.body)
    expect(body.name).toBe("test")
    expect(body.source_file).toBe("pipe.py")
  })

  it("traceCell posts to /api/pipeline/trace with correct body", async () => {
    await traceCell({ graph: dummyGraph, row_index: 0, target_node_id: "n1" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/trace")
    expect(opts.method).toBe("POST")
    const body = JSON.parse(opts.body)
    expect(body.row_index).toBe(0)
    expect(body.target_node_id).toBe("n1")
  })

  it("writeOutput posts to /api/pipeline/write-output", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok" }))
    await writeOutput({ graph: dummyGraph, nodeId: "sink1" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/write-output")
    const body = JSON.parse(opts.body)
    expect(body.node_id).toBe("sink1")
    expect(body.source).toBe("live")
    expect(body.overwrite).toBe(false)
  })

  it("fetchSchema GETs /api/schema with encoded path", async () => {
    await fetchSchema("data/test file.csv")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/schema?path=data%2Ftest%20file.csv")
  })

  it("trainModel posts to /api/modelling/train with default source", async () => {
    await trainModel({ graph: dummyGraph, node_id: "model1" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/train")
    const body = JSON.parse(opts.body)
    expect(body.node_id).toBe("model1")
    expect(body.source).toBe("live")
  })

  it("solveOptimiser posts to /api/optimiser/solve", async () => {
    await solveOptimiser({ graph: dummyGraph, node_id: "opt1" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/optimiser/solve")
    const body = JSON.parse(opts.body)
    expect(body.node_id).toBe("opt1")
  })

  it("listFiles GETs /api/files with dir and optional extensions", async () => {
    await listFiles("data", ".csv,.parquet")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toContain("/api/files?")
    expect(url).toContain("dir=data")
    expect(url).toContain("extensions=.csv%2C.parquet")
  })

  it("createSubmodel posts to /api/submodel/create", async () => {
    const payload = {
      name: "sub1",
      node_ids: ["n1", "n2"],
      graph: dummyGraph,
      preamble: "",
      source_file: "pipe.py",
      pipeline_name: "main",
    }
    await createSubmodel(payload)
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/submodel/create")
    const body = JSON.parse(opts.body)
    expect(body.name).toBe("sub1")
    expect(body.node_ids).toEqual(["n1", "n2"])
  })

  it("getTrainStatus GETs /api/modelling/train/status/{jobId}", async () => {
    await getTrainStatus("job-123")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/train/status/job-123")
  })

  it("estimateTrainingRam posts to /api/modelling/estimate", async () => {
    await estimateTrainingRam({ graph: dummyGraph, node_id: "m1" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/estimate")
    expect(opts.method).toBe("POST")
  })

  it("getWarehouses GETs /api/databricks/warehouses", async () => {
    await getWarehouses()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/databricks/warehouses")
  })

  it("getCatalogs GETs /api/databricks/catalogs", async () => {
    await getCatalogs()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/databricks/catalogs")
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Git endpoints
// ═══════════════════════════════════════════════════════════════════════════

describe("git endpoints", () => {
  beforeEach(() => {
    mockFetch.mockReturnValue(jsonResponse({}))
  })

  it("getGitStatus GETs /api/git/status", async () => {
    const data = makeGitStatusResponse()
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getGitStatus()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/status")
    expect(opts.method).toBeUndefined()
    expect(result).toEqual(data)
  })

  it("gitArchiveBranch POSTs to /api/git/archive with branch body", async () => {
    const data = { archived_as: "archive/old-branch" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitArchiveBranch("old-branch")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/archive")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ branch: "old-branch" })
    expect(result).toEqual(data)
  })

  it("gitDeleteBranch DELETEs /api/git/branches with branch body", async () => {
    const data = {
      status: "deleted",
      branch: "stale-branch",
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitDeleteBranch("stale-branch")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/branches")
    expect(opts.method).toBe("DELETE")
    expect(opts.headers["Content-Type"]).toBe("application/json")
    expect(JSON.parse(opts.body)).toEqual({ branch: "stale-branch", confirm: false })
    expect(result).toEqual(data)
  })

  it("gitDeleteBranch sends confirm=true when overriding the unmerged guard", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "deleted", branch: "wip" }))
    await gitDeleteBranch("wip", true)
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ branch: "wip", confirm: true })
  })

  it("getGitRemotes GETs /api/git/remotes and parses per-leg divergence", async () => {
    const data = {
      remotes: [
        {
          name: "origin",
          url: "git@example.com:x.git",
          ahead: 2,
          behind: 0,
          working: { status: "ahead", ahead: 2, behind: 0 },
          ledger: { status: "behind", ahead: 0, behind: 1 },
        },
        // A remote with no leg detail → the legs fill to null (back-compat input).
        { name: "backup", url: null, ahead: null, behind: null },
      ],
      working_branch: "dev",
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getGitRemotes()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/remotes")
    expect(result.remotes[0].working).toEqual({ status: "ahead", ahead: 2, behind: 0 })
    expect(result.remotes[0].ledger).toEqual({ status: "behind", ahead: 0, behind: 1 })
    expect(result.remotes[1].working).toBeNull()
    expect(result.remotes[1].ledger).toBeNull()
  })

  it("gitPush POSTs /api/git/push with the remote", async () => {
    const data = loadUiContractFixture("git_push_response")
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitPush("origin")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/push")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ remote: "origin" })
    expect(result).toEqual(data)
  })

  it("gitPush rejects a success payload without required bootstrap metadata", async () => {
    mockFetch.mockReturnValue(
      jsonResponse({
        remote: "origin",
        working_branch: "dev",
        ledger_branch: "dev-save",
        pushed_refs: ["dev", "dev-save"],
      }),
    )

    await expect(gitPush("origin")).rejects.toThrow(/default_branch/i)
  })

  it("getWorkingBranches GETs /api/git/working-branches", async () => {
    const data = { current: "demo", branches: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getWorkingBranches()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/working-branches")
    expect(result).toEqual(data)
  })

  it("restoreBranch POSTs to /api/git/restore with branch body", async () => {
    mockFetch.mockReturnValue(jsonResponse({ restored_as: "demo" }))
    const result = await restoreBranch("archive/demo")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/restore")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ branch: "archive/demo" })
    expect(result).toEqual({ restored_as: "demo" })
  })

  it("undeleteBranch POSTs to /api/git/undelete and parses {status, branch}", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "restored", branch: "demo" }))
    const result = await undeleteBranch("demo")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/undelete")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ branch: "demo" })
    expect(result).toEqual({ status: "restored", branch: "demo" })
  })

  // Milestone / ledger-history endpoints (P3 commit + milestones; P5a saves)

  it("getMilestones GETs /api/git/milestones without limit", async () => {
    const data = { working_branch: "pricing-dev", entries: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getMilestones()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/milestones")
    expect(result).toEqual(data)
  })

  it("getMilestones GETs /api/git/milestones with a limit param", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: null, entries: [] }))
    await getMilestones(10)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/milestones?limit=10")
  })

  it("getMilestones forwards an explicit limit of 0 (does not drop it via truthiness)", async () => {
    // limit=0 is invalid backend-side (Query(ge=1)); forwarding it lets the
    // backend reject loudly rather than silently substituting the default.
    mockFetch.mockReturnValue(jsonResponse({ working_branch: null, entries: [] }))
    await getMilestones(0)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/milestones?limit=0")
  })

  it("getMilestoneSaves GETs /api/git/milestones/{sha}/saves", async () => {
    const data = { saves: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getMilestoneSaves("abc123")
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/milestones/abc123/saves")
    expect(result).toEqual(data)
  })

  it("getMilestoneSaves URL-encodes the sha path segment", async () => {
    mockFetch.mockReturnValue(jsonResponse({ saves: [] }))
    await getMilestoneSaves("weird/ sha")
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/milestones/weird%2F%20sha/saves")
  })

  it("getPendingSaves GETs /api/git/pending-saves", async () => {
    const data = { saves: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getPendingSaves()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/pending-saves")
    expect(result).toEqual(data)
  })

  it("getGitGraph GETs /api/git/graph without a limit param", async () => {
    const data = { working_branch: "pricing-dev", order: [], branches: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getGitGraph()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/graph")
    expect(result).toEqual(data)
  })

  it("getGitGraph GETs /api/git/graph with a limit param", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: null, order: [], branches: [] }))
    await getGitGraph(25)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/graph?limit=25")
  })

  it("getGitGraph sends an explicit limit of 0 (falsy but defined)", async () => {
    mockFetch.mockReturnValue(jsonResponse({ working_branch: null, order: [], branches: [] }))
    await getGitGraph(0)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/graph?limit=0")
  })

  it("commitMilestone POSTs to /api/git/commit with snake_case body", async () => {
    const data = {
      sha: "deadbeef",
      short_sha: "deadbee",
      working_branch: "pricing-dev",
      version_label: "2.0",
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await commitMilestone("My milestone", "2.0")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/commit")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({
      message: "My milestone",
      version_label: "2.0",
      allow_fork: false,
    })
    expect(result).toEqual(data)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Git remote catch-up + read-only history endpoints (P6/P7)
// ═══════════════════════════════════════════════════════════════════════════

describe("git remote catch-up + history endpoints", () => {
  beforeEach(() => {
    mockFetch.mockReturnValue(jsonResponse({}))
  })

  it("getWorkingBranch GETs /api/git/working-branch and parses the readiness signal", async () => {
    const data = {
      working_branch: "dev",
      state: "ready",
      errors: [],
      current_branch: "dev-save",
      last_save_sha: "abc1234",
      eligible_branches: ["dev"],
      identity_set: true,
      user_name: "U",
      user_email: "u@x.y",
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getWorkingBranch()
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/working-branch")
    expect(result.state).toBe("ready")
    expect(result.working_branch).toBe("dev")
  })

  it("setWorkingBranch POSTs /api/git/working-branch with branch + create", async () => {
    const data = { working_branch: "fresh", state: "ready", last_save_sha: null }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await setWorkingBranch("fresh", true)
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/working-branch")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ branch: "fresh", create: true })
    expect(result.working_branch).toBe("fresh")
  })

  it("setGitIdentity POSTs /api/git/identity with snake_case body", async () => {
    const data = { user_name: "Ada", user_email: "ada@x.y", scope: "local" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await setGitIdentity("Ada", "ada@x.y", false)
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/identity")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({
      user_name: "Ada",
      user_email: "ada@x.y",
      set_global: false,
    })
    expect(result.scope).toBe("local")
  })

  it("setGitPrefs POSTs /api/git/prefs with the prefs body", async () => {
    const data = { skip_switch_confirm: true }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await setGitPrefs({ skip_switch_confirm: true })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/prefs")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ skip_switch_confirm: true })
    expect(result.skip_switch_confirm).toBe(true)
  })

  it("gitFastForward POSTs /api/git/fast-forward and reports the advanced refs", async () => {
    const data = {
      remote: "origin",
      working_branch: "dev",
      fast_forwarded: ["dev", "dev-save"],
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitFastForward("origin")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/fast-forward")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ remote: "origin" })
    expect(result.fast_forwarded).toEqual(["dev", "dev-save"])
  })

  it("gitBranchAway POSTs /api/git/branch-away and reports the set-aside name", async () => {
    const data = { working_branch: "dev", set_aside_as: "dev-2026-06-21" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitBranchAway("origin")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/branch-away")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ remote: "origin" })
    expect(result.set_aside_as).toBe("dev-2026-06-21")
  })

  it("getCommitPipeline GETs /api/git/show/{sha} and parses the graph", async () => {
    mockFetch.mockReturnValue(jsonResponse(dummyGraph))
    const result = await getCommitPipeline("abc123")
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/show/abc123")
    expect(result.nodes).toHaveLength(1)
  })

  it("getCommitPipeline URL-encodes the sha path segment", async () => {
    mockFetch.mockReturnValue(jsonResponse(dummyGraph))
    await getCommitPipeline("weird/ sha")
    expect(mockFetch.mock.calls[0][0]).toBe("/api/git/show/weird%2F%20sha")
  })

  it("getCommitContext GETs /api/git/commit-context/{sha} without a base", async () => {
    const data = {
      sha: "a".repeat(40),
      short_sha: "aaaaaaaa",
      message: "Milestone 1",
      timestamp: "2026-06-21T00:00:00Z",
      is_milestone: true,
      version_label: "1.0",
      nearest_milestone: {
        sha: "a".repeat(40),
        short_sha: "aaaaaaaa",
        message: "Milestone 1",
      },
      distance: 0,
      delta_from_base: null,
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getCommitContext("a".repeat(40))
    expect(mockFetch.mock.calls[0][0]).toBe(`/api/git/commit-context/${"a".repeat(40)}`)
    expect(result.distance).toBe(0)
    expect(result.nearest_milestone.message).toBe("Milestone 1")
  })

  it("getCommitContext appends the base query param when provided", async () => {
    const data = {
      sha: "b".repeat(40),
      short_sha: "bbbbbbbb",
      message: "save",
      timestamp: "2026-06-21T00:00:00Z",
      nearest_milestone: {
        sha: "a".repeat(40),
        short_sha: "aaaaaaaa",
        message: "Milestone 1",
      },
      distance: 3,
      delta_from_base: 2,
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getCommitContext("b".repeat(40), { base: "a".repeat(40) })
    expect(mockFetch.mock.calls[0][0]).toBe(
      `/api/git/commit-context/${"b".repeat(40)}?base=${"a".repeat(40)}`,
    )
    expect(result.delta_from_base).toBe(2)
  })

  it("moveToVersion POSTs /api/git/move with the sha", async () => {
    const data = {
      sha: "a".repeat(40),
      short_sha: "aaaaaaaa",
      prior_branch: "dev-save",
      is_detached: true,
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await moveToVersion("a".repeat(40))
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/move")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ sha: "a".repeat(40) })
    expect(result.is_detached).toBe(true)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Utility endpoints
// ═══════════════════════════════════════════════════════════════════════════

describe("utility endpoints", () => {
  beforeEach(() => {
    mockFetch.mockReturnValue(jsonResponse({}))
  })

  it("listUtilityFiles GETs /api/utility", async () => {
    const data = { files: [{ name: "helpers", module: "helpers" }] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await listUtilityFiles()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility")
    expect(opts.method).toBeUndefined()
    expect(result).toEqual(data)
  })

  it("readUtilityFile GETs /api/utility/{module} with encoded module", async () => {
    const data = { name: "helpers", module: "my helpers", content: "def foo(): pass" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await readUtilityFile("my helpers")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility/my%20helpers")
    expect(result).toEqual(data)
  })

  it("createUtilityFile POSTs to /api/utility with name and content", async () => {
    const data = {
      status: "ok",
      name: "utils",
      module: "utils",
      import_line: "from utility.utils import *",
      error: null,
      error_line: null,
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await createUtilityFile({ name: "utils", content: "code" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ name: "utils", content: "code" })
    expect(result).toEqual(data)
  })

  it("createUtilityFile POSTs with name only when content is omitted", async () => {
    mockFetch.mockReturnValue(jsonResponse({
      status: "ok",
      name: "empty",
      module: "empty",
      import_line: "from utility.empty import *",
      error: null,
      error_line: null,
    }))
    await createUtilityFile({ name: "empty" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).toEqual({ name: "empty" })
  })

  it("updateUtilityFile PUTs to /api/utility/{module} with content body", async () => {
    const data = {
      status: "ok",
      name: "helpers",
      module: "helpers",
      import_line: "from utility.helpers import *",
      error: null,
      error_line: null,
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await updateUtilityFile("helpers", "updated")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility/helpers")
    expect(opts.method).toBe("PUT")
    expect(opts.headers["Content-Type"]).toBe("application/json")
    expect(JSON.parse(opts.body)).toEqual({ content: "updated" })
    expect(result).toEqual(data)
  })

  it("deleteUtilityFile DELETEs /api/utility/{module}", async () => {
    const data = { status: "deleted", module: "old-util" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await deleteUtilityFile("old-util")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility/old-util")
    expect(opts.method).toBe("DELETE")
    expect(result).toEqual(data)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// JSON cache endpoints
// ═══════════════════════════════════════════════════════════════════════════

describe("json cache endpoints", () => {
  beforeEach(() => {
    mockFetch.mockReturnValue(jsonResponse({}))
  })

  it("buildJsonCache POSTs to /api/json-cache/build with 1800s timeout", async () => {
    const data = makeJsonCacheBuildResponse()
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await buildJsonCache({ path: "/data/input.json" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/build")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ path: "/data/input.json" })
    expect(result).toEqual({
      ...data,
      skipped_records: 0,
      skipped_rows: {},
    })
  })

  it("buildJsonCache allows timeout override", async () => {
    mockFetch.mockReturnValue(jsonResponse(makeJsonCacheBuildResponse()))
    await buildJsonCache({ path: "x.json" }, { timeout: 5000 })
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it("cancelJsonCache POSTs to /api/json-cache/cancel with path body", async () => {
    const data = { cancelled: true, data_path: "/data/input.json" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await cancelJsonCache("/data/input.json")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/cancel")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ path: "/data/input.json" })
    expect(result).toEqual(data)
  })

  it("getJsonCacheProgress GETs /api/json-cache/progress with encoded path", async () => {
    const data = makeJsonCacheProgressResponse()
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getJsonCacheProgress("my file.json")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/progress?path=my%20file.json")
    expect(result).toEqual(data)
  })

  it("getJsonCacheStatus GETs /api/json-cache/status with encoded path", async () => {
    const data = {
      cached: true,
      skipped_records: 2,
      skipped_rows: { drivers: 3 },
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getJsonCacheStatus("data/file.json")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/status?path=data%2Ffile.json")
    expect(result.cached).toBe(true)
    expect(result.data_path).toBe("")
    expect(result.skipped_records).toBe(2)
    expect(result.skipped_rows).toEqual({ drivers: 3 })
  })

  it("deleteJsonCache DELETEs /api/json-cache with encoded path", async () => {
    const data = { cached: false, data_path: "file.json" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await deleteJsonCache("file.json")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache?path=file.json")
    expect(opts.method).toBe("DELETE")
    expect(result).toEqual(data)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// request() edge cases
// ═══════════════════════════════════════════════════════════════════════════

describe("request() edge cases", () => {
  it("creates an AbortController and passes its signal to fetch", async () => {
    mockFetch.mockReturnValue(jsonResponse(makeGitStatusResponse()))
    await getGitStatus()
    const [, opts] = mockFetch.mock.calls[0]
    expect(opts.signal).toBeInstanceOf(AbortSignal)
  })

  it("extracts string detail from error response", async () => {
    mockFetch.mockReturnValue(errorResponse(400, { detail: "bad request" }))
    try {
      await getGitStatus()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(400)
      expect((err as ApiError).detail).toBe("bad request")
    }
  })

  it("JSON-stringifies nested detail object from error response", async () => {
    const nestedDetail = { field: "name", error: "required" }
    mockFetch.mockReturnValue(errorResponse(422, { detail: nestedDetail }))
    try {
      await getGitStatus()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).detail).toBe(JSON.stringify(nestedDetail))
    }
  })

  it("preserves structured detail objects on ApiError for execution diagnostics", async () => {
    const structuredDetail = {
      message: "Training rejected by admission control",
      terminal_reason: "memory_limited",
      execution_metrics: makeExecutionMetricsFixture({ profile: "training_prep", terminal_reason: "memory_limited" }),
    }
    mockFetch.mockReturnValue(errorResponse(507, { detail: structuredDetail }))

    try {
      await getGitStatus()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).detail).toBe(JSON.stringify(structuredDetail))
      expect((err as ApiError).rawDetail).toEqual(structuredDetail)
    }
  })

  it("uses raw body as detail when detail key is absent", async () => {
    const body = { message: "something went wrong" }
    mockFetch.mockReturnValue(errorResponse(500, body))
    try {
      await getGitStatus()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).detail).toBe(JSON.stringify(body))
    }
  })

  it("falls back to statusText when response body is not JSON", async () => {
    mockFetch.mockReturnValue(errorResponse(502))
    try {
      await getGitStatus()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(502)
      expect((err as ApiError).detail).toBe("Error")
    }
  })

  it("propagates network errors when fetch throws", async () => {
    mockFetch.mockRejectedValue(new TypeError("Network request failed"))
    await expect(getGitStatus()).rejects.toThrow("Network request failed")
  })

  it("propagates network errors as non-ApiError", async () => {
    mockFetch.mockRejectedValue(new TypeError("Failed to fetch"))
    try {
      await getGitStatus()
    } catch (err) {
      expect(err).not.toBeInstanceOf(ApiError)
      expect(err).toBeInstanceOf(TypeError)
    }
  })

  it("surfaces client-side request timeouts as ApiTimeoutError, not AbortError", async () => {
    vi.useFakeTimers()
    try {
      mockFetch.mockImplementation((_url: string, options?: RequestInit) =>
        new Promise((_resolve, reject) => {
          const signal = options?.signal as AbortSignal | undefined
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          )
        }),
      )

      const promise = previewNode({ graph: dummyGraph, nodeId: "node1", rowLimit: 50, timeout: 5 })
      promise.catch(() => {})

      expect(mockFetch).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(5)

      await expect(promise).rejects.toBeInstanceOf(ApiTimeoutError)
      await expect(promise).rejects.toMatchObject({
        name: "ApiTimeoutError",
        timeoutMs: 5,
        url: "/api/pipeline/preview",
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it("preserves caller-initiated aborts as AbortError", async () => {
    mockFetch.mockImplementation((_url: string, options?: RequestInit) =>
      new Promise((_resolve, reject) => {
        const signal = options?.signal as AbortSignal | undefined
        signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        )
      }),
    )

    const controller = new AbortController()
    const promise = previewNode({
      graph: dummyGraph,
      nodeId: "node1",
      rowLimit: 50,
      signal: controller.signal,
      timeout: 30_000,
    })
    promise.catch(() => {})

    controller.abort()

    await expect(promise).rejects.toMatchObject({ name: "AbortError" })
    await expect(promise).rejects.not.toBeInstanceOf(ApiTimeoutError)
  })

  it("does not reclassify delayed caller abort rejection after the timeout fires", async () => {
    vi.useFakeTimers()
    try {
      let rejectFetch!: (reason: unknown) => void
      mockFetch.mockImplementation((_url: string, options?: RequestInit) =>
        new Promise((_resolve, reject) => {
          rejectFetch = reject
          const signal = options?.signal as AbortSignal | undefined
          signal?.addEventListener(
            "abort",
            () => {
              setTimeout(() => reject(new DOMException("Aborted", "AbortError")), 20)
            },
            { once: true },
          )
        }),
      )

      const controller = new AbortController()
      const promise = previewNode({
        graph: dummyGraph,
        nodeId: "node1",
        rowLimit: 50,
        signal: controller.signal,
        timeout: 10,
      })
      promise.catch(() => {})

      controller.abort()
      vi.advanceTimersByTime(10)
      vi.advanceTimersByTime(10)
      expect(rejectFetch).toBeDefined()

      await expect(promise).rejects.toMatchObject({ name: "AbortError" })
      await expect(promise).rejects.not.toBeInstanceOf(ApiTimeoutError)
    } finally {
      vi.useRealTimers()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// streaming_chunk_size plumbed through pipeline / modelling / optimiser
// endpoints. Each function takes the chunk size and must emit it on the
// request body so the backend can size its streaming buffers. Asserting
// per-endpoint catches future regressions where the param is added to the
// signature but dropped from the body (or vice versa).
// ═══════════════════════════════════════════════════════════════════════════

describe("streaming_chunk_size in request bodies", () => {
  beforeEach(() => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/pipeline/preview") return jsonResponse(makePreviewResponse())
      if (url === "/api/pipeline/trace") return jsonResponse(makeTraceResponse())
      if (url === "/api/pipeline/write-output") return jsonResponse({ status: "ok" })
      if (url === "/api/modelling/train") return jsonResponse(makeTrainResponse())
      if (url === "/api/optimiser/solve") return jsonResponse(makeSolveOptimiserResponse())
      if (url === "/api/optimiser/estimate") return jsonResponse({})
      if (url === "/api/optimiser/frontier/auto-range") return jsonResponse({ status: "ok", method: "auto", ranges: {} })
      return jsonResponse({})
    })
  })

  it("previewNode body includes streaming_chunk_size when supplied", async () => {
    await previewNode({ graph: dummyGraph, nodeId: "node1", rowLimit: 50, source: "live", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("previewNode body omits streaming_chunk_size when not supplied", async () => {
    await previewNode({ graph: dummyGraph, nodeId: "node1", rowLimit: 50, source: "live" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("traceCell body includes streaming_chunk_size when supplied", async () => {
    await traceCell({
      graph: dummyGraph,
      row_index: 0,
      target_node_id: "n1",
      streamingChunkSize: 42,
    })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("traceCell body omits streaming_chunk_size when not supplied", async () => {
    await traceCell({ graph: dummyGraph, row_index: 0, target_node_id: "n1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("writeOutput body includes streaming_chunk_size when supplied", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok" }))
    await writeOutput({ graph: dummyGraph, nodeId: "sink1", source: "live", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("writeOutput body omits streaming_chunk_size when not supplied", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "ok" }))
    await writeOutput({ graph: dummyGraph, nodeId: "sink1", source: "live" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("uses the exact input-cache V1 paths, methods, and request bodies", async () => {
    const source = { schema_version: 1 as const, config: { path: "data.csv" } }
    mockFetch.mockReturnValueOnce(jsonResponse({ schema_version: 1, job_id: "job / 1", identity_digest: "digest", status: "running", joined: false }))
    await buildInputCache({ ...source, refresh: true, profile: "preview_eager" })
    expect(mockFetch.mock.calls[0][0]).toBe("/api/input-cache/build")
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ ...source, refresh: true, profile: "preview_eager" })

    mockFetch.mockReturnValueOnce(jsonResponse({ schema_version: 1, job_id: "job / 1", identity_digest: "digest", status: "running", terminal_reason: null, message: "", refresh: false, build_class: "bounded", progress: { phase: "queued", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 }, snapshot: null, error_code: null }))
    await getInputCacheJob("job / 1")
    expect(mockFetch.mock.calls[1][0]).toBe("/api/input-cache/jobs/job%20%2F%201")

    mockFetch.mockReturnValueOnce(jsonResponse({ schema_version: 1, job_id: "job / 1", cancellation_requested: true, status: "running" }))
    await cancelInputCacheJob("job / 1")
    expect(mockFetch.mock.calls[2][0]).toBe("/api/input-cache/jobs/job%20%2F%201")
    expect(mockFetch.mock.calls[2][1].method).toBe("DELETE")

    const snapshot = { schema_version: 1, identity_digest: "digest", state: "missing", freshness: "unknown", generation: null }
    mockFetch.mockReturnValueOnce(jsonResponse(snapshot))
    await getInputCacheStatus(source)
    expect(mockFetch.mock.calls[3][0]).toBe("/api/input-cache/status")
    expect(JSON.parse(mockFetch.mock.calls[3][1].body)).toEqual(source)
    mockFetch.mockReturnValueOnce(jsonResponse(snapshot))
    await clearInputCache(source)
    expect(mockFetch.mock.calls[4][0]).toBe("/api/input-cache/clear")
    expect(JSON.parse(mockFetch.mock.calls[4][1].body)).toEqual(source)
  })

  it("trainModel body includes streaming_chunk_size when supplied", async () => {
    await trainModel({ graph: dummyGraph, node_id: "model1", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("trainModel body omits streaming_chunk_size when not supplied", async () => {
    await trainModel({ graph: dummyGraph, node_id: "model1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("solveOptimiser body includes streaming_chunk_size when supplied", async () => {
    await solveOptimiser({ graph: dummyGraph, node_id: "opt1", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("solveOptimiser body omits streaming_chunk_size when not supplied", async () => {
    await solveOptimiser({ graph: dummyGraph, node_id: "opt1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("estimateOptimiserSolve body includes streaming_chunk_size when supplied", async () => {
    const { estimateOptimiserSolve } = await import("../client")
    await estimateOptimiserSolve({ graph: dummyGraph, node_id: "opt1", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("estimateOptimiserSolve body omits streaming_chunk_size when not supplied", async () => {
    const { estimateOptimiserSolve } = await import("../client")
    await estimateOptimiserSolve({ graph: dummyGraph, node_id: "opt1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("estimateOptimiserFrontierAutoRange body includes streaming_chunk_size when supplied", async () => {
    const { estimateOptimiserFrontierAutoRange } = await import("../client")
    await estimateOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1", streamingChunkSize: 42 })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("estimateOptimiserFrontierAutoRange body omits streaming_chunk_size when not supplied", async () => {
    const { estimateOptimiserFrontierAutoRange } = await import("../client")
    await estimateOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })

  it("startOptimiserFrontierAutoRange body includes streaming_chunk_size when supplied", async () => {
    const { startOptimiserFrontierAutoRange } = await import("../client")
    mockFetch.mockReturnValue(jsonResponse({ status: "started", job_id: "range-job-1", error: null }))
    await startOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1", streamingChunkSize: 42 })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/optimiser/frontier/auto-range/start")
    expect(JSON.parse(opts.body).streaming_chunk_size).toBe(42)
  })

  it("startOptimiserFrontierAutoRange body omits streaming_chunk_size when not supplied", async () => {
    const { startOptimiserFrontierAutoRange } = await import("../client")
    mockFetch.mockReturnValue(jsonResponse({ status: "started", job_id: "range-job-1", error: null }))
    await startOptimiserFrontierAutoRange({ graph: dummyGraph, node_id: "opt1" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).not.toHaveProperty("streaming_chunk_size")
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// runFrontier — the backend now returns a pollable job handle and the client
// polls /frontier/status/{job_id} until the sweep finishes, preserving the
// old resolve-with-final-payload contract.
// ═══════════════════════════════════════════════════════════════════════════

describe("runFrontier background polling", () => {
  const startedBody = { status: "started", job_id: "frontier-job-1", points: [], n_points: 0, points_returned: 0, constraint_names: [], points_limit: null, points_truncated: false }
  const completedResult = { status: "ok", points: [{ total_objective: 1.0 }], n_points: 1, points_returned: 1, constraint_names: ["volume"], points_limit: 2000, points_truncated: false }
  const statusBody = (status: string, extra: Record<string, unknown> = {}) => ({
    status,
    progress: 1,
    message: "",
    elapsed_seconds: 0.1,
    result: null,
    ...extra,
  })

  it("starts the sweep then resolves with the polled result", async () => {
    const { runFrontier } = await import("../client")
    mockFetch
      .mockReturnValueOnce(jsonResponse(startedBody))
      .mockReturnValueOnce(jsonResponse(statusBody("completed", { result: completedResult })))

    const frontier = await runFrontier({ job_id: "opt-job-1", threshold_ranges: { volume: [0.8, 1.0] } })

    expect(frontier.n_points).toBe(1)
    expect(frontier.constraint_names).toEqual(["volume"])
    expect(mockFetch.mock.calls[0][0]).toBe("/api/optimiser/frontier")
    expect(mockFetch.mock.calls[1][0]).toBe("/api/optimiser/frontier/status/frontier-job-1")
  })

  it("keeps polling while the job is running", async () => {
    vi.useFakeTimers()
    try {
      const { runFrontier } = await import("../client")
      mockFetch
        .mockReturnValueOnce(jsonResponse(startedBody))
        .mockReturnValueOnce(jsonResponse(statusBody("running")))
        .mockReturnValueOnce(jsonResponse(statusBody("completed", { result: completedResult })))

      const promise = runFrontier({ job_id: "opt-job-1", threshold_ranges: { volume: [0.8, 1.0] } })
      await vi.advanceTimersByTimeAsync(600)

      await expect(promise).resolves.toMatchObject({ n_points: 1 })
      expect(mockFetch).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it("rejects with an ApiError carrying the job message on terminal failure", async () => {
    const { runFrontier } = await import("../client")
    mockFetch
      .mockReturnValueOnce(jsonResponse(startedBody))
      .mockReturnValueOnce(jsonResponse(statusBody("contract_error", { message: "Optimiser job state changed", http_status_code: 409 })))

    const promise = runFrontier({ job_id: "opt-job-1", threshold_ranges: { volume: [0.8, 1.0] } })

    await expect(promise).rejects.toBeInstanceOf(ApiError)
    await expect(promise).rejects.toMatchObject({ status: 409, message: "Optimiser job state changed" })
  })

  it("rejects when a completed job has no result payload", async () => {
    const { runFrontier } = await import("../client")
    mockFetch
      .mockReturnValueOnce(jsonResponse(startedBody))
      .mockReturnValueOnce(jsonResponse(statusBody("completed")))

    await expect(
      runFrontier({ job_id: "opt-job-1", threshold_ranges: { volume: [0.8, 1.0] } }),
    ).rejects.toMatchObject({ message: "Frontier job completed without a result" })
  })

  it("returns the immediate payload when the backend answers inline", async () => {
    const { runFrontier } = await import("../client")
    mockFetch.mockReturnValueOnce(jsonResponse(completedResult))

    const frontier = await runFrontier({ job_id: "opt-job-1", threshold_ranges: { volume: [0.8, 1.0] } })

    expect(frontier.status).toBe("ok")
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})
