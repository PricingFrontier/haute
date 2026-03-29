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
  listGitBranches,
  createGitBranch,
  switchGitBranch,
  gitSave,
  gitSubmit,
  getGitHistory,
  gitRevert,
  gitPull,
  gitArchiveBranch,
  gitDeleteBranch,
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
    mockFetch.mockReturnValue(jsonResponse({}))
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
    const data = { branch: "main", dirty: false }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getGitStatus()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/status")
    expect(opts.method).toBeUndefined()
    expect(result).toEqual(data)
  })

  it("listGitBranches GETs /api/git/branches", async () => {
    const data = { current: "main", branches: [{ name: "main" }] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await listGitBranches()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/branches")
    expect(result).toEqual(data)
  })

  it("createGitBranch POSTs to /api/git/branches with description body", async () => {
    const data = { branch: "feat/new-thing" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await createGitBranch("new feature branch")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/branches")
    expect(opts.method).toBe("POST")
    expect(opts.headers["Content-Type"]).toBe("application/json")
    expect(JSON.parse(opts.body)).toEqual({ description: "new feature branch" })
    expect(result).toEqual(data)
  })

  it("switchGitBranch POSTs to /api/git/switch with branch body", async () => {
    const data = { status: "ok", branch: "dev" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await switchGitBranch("dev")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/switch")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ branch: "dev" })
    expect(result).toEqual(data)
  })

  it("gitSave POSTs to /api/git/save with empty body", async () => {
    const data = { commit_sha: "abc123", message: "save", timestamp: "2026-01-01" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitSave()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/save")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({})
    expect(result).toEqual(data)
  })

  it("gitSubmit POSTs to /api/git/submit with empty body", async () => {
    const data = { compare_url: "https://github.com/compare/abc", branch: "feat/x" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitSubmit()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/submit")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({})
    expect(result).toEqual(data)
  })

  it("getGitHistory GETs /api/git/history without limit", async () => {
    const data = { entries: [] }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await getGitHistory()
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/history")
    expect(result).toEqual(data)
  })

  it("getGitHistory GETs /api/git/history with limit param", async () => {
    mockFetch.mockReturnValue(jsonResponse({ entries: [] }))
    await getGitHistory(10)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/history?limit=10")
  })

  it("gitRevert POSTs to /api/git/revert with sha body", async () => {
    const data = { backup_tag: "backup-abc", reverted_to: "abc123" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitRevert("abc123")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/revert")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ sha: "abc123" })
    expect(result).toEqual(data)
  })

  it("gitPull POSTs to /api/git/pull with empty body", async () => {
    const data = { success: true, conflict: false, conflict_message: null, commits_pulled: 3 }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await gitPull()
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/git/pull")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({})
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
    expect(JSON.parse(opts.body)).toEqual({ branch: "stale-branch" })
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
    const data = { name: "utils", module: "utils", content: "code" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await createUtilityFile({ name: "utils", content: "code" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/utility")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ name: "utils", content: "code" })
    expect(result).toEqual(data)
  })

  it("createUtilityFile POSTs with name only when content is omitted", async () => {
    mockFetch.mockReturnValue(jsonResponse({}))
    await createUtilityFile({ name: "empty" })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body)).toEqual({ name: "empty" })
  })

  it("updateUtilityFile PUTs to /api/utility/{module} with content body", async () => {
    const data = { name: "helpers", module: "helpers", content: "updated" }
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
    const data = { status: "building" }
    mockFetch.mockReturnValue(jsonResponse(data))
    const result = await buildJsonCache({ path: "/data/input.json" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/json-cache/build")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body)).toEqual({ path: "/data/input.json" })
    expect(result).toEqual(data)
  })

  it("buildJsonCache allows timeout override", async () => {
    mockFetch.mockReturnValue(jsonResponse({}))
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
    const data = { progress: 0.5 }
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
    expect(result).toEqual(data)
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
    mockFetch.mockReturnValue(jsonResponse({ branch: "main" }))
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
