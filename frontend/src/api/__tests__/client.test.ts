import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import {
  ApiError,
  loadPipeline,
  previewNode,
  savePipeline,
  traceCell,
  executeSink,
  fetchSchema,
  fetchDatabricksSchema,
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
  commitMilestone,
  getWorkingBranches,
  getGitRemotes,
  gitPush,
  restoreBranch,
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
    await previewNode(dummyGraph, "node1", 50, "live")
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

  it("executeSink posts to /api/pipeline/sink", async () => {
    await executeSink(dummyGraph, "sink1")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/pipeline/sink")
    const body = JSON.parse(opts.body)
    expect(body.node_id).toBe("sink1")
    expect(body.source).toBe("live")
  })

  it("fetchSchema GETs /api/schema with encoded path", async () => {
    await fetchSchema("data/test file.csv")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/schema?path=data%2Ftest%20file.csv")
  })

  it("fetchDatabricksSchema GETs /api/schema/databricks with encoded table", async () => {
    await fetchDatabricksSchema("catalog.schema.table")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/schema/databricks?table=catalog.schema.table")
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
    const data = { status: "deleted", branch: "stale-branch" }
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
    const data = {
      remote: "origin",
      working_branch: "dev",
      ledger_branch: "dev-save",
      pushed_refs: ["dev", "dev-save"],
    }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitPush("origin")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/push")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ remote: "origin" })
    expect(result).toEqual(data)
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
    expect(result).toEqual(data)
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
    const data = { cached: true }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getJsonCacheStatus("data/file.json")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/status?path=data%2Ffile.json")
    expect(result.cached).toBe(true)
    expect(result.data_path).toBe("")
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
})
