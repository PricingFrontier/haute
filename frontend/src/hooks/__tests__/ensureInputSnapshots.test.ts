import type { Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type {
  InputCacheBuildResponse,
  InputCacheJobStatusResponse,
  InputCacheSnapshotResponse,
  JobStatus,
  JsonCacheBuildResponse,
  JsonCacheStatusResponse,
} from "../../api/types"
import { NODE_TYPES } from "../../utils/nodeTypes"

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>()
  return {
    ...actual,
    buildInputCache: vi.fn(),
    getInputCacheJob: vi.fn(),
    getInputCacheStatus: vi.fn(),
    buildJsonCache: vi.fn(),
    getJsonCacheStatusForSchema: vi.fn(),
    getJsonCacheProgress: vi.fn(),
  }
})

import {
  ApiError,
  buildInputCache,
  getInputCacheJob,
  getInputCacheStatus,
  buildJsonCache,
  getJsonCacheStatusForSchema,
  getJsonCacheProgress,
} from "../../api/client"
import { ensureInputSnapshots } from "../ensureInputSnapshots"

const jsonBuild: JsonCacheBuildResponse = {
  path: "cache", data_path: "quotes.jsonl", row_count: 10, column_count: 1,
  columns: {}, size_bytes: 100, cached_at: 1, cache_seconds: 2,
  skipped_records: 0, skipped_rows: {},
}
const jsonStatus = (cached: boolean): JsonCacheStatusResponse => ({ ...jsonBuild, cached })

function dataInput(id: string): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {
      nodeType: NODE_TYPES.DATA_INPUT,
      config: {
        inputType: "file",
        format: "csv",
        mode: "scan",
        path: `${id}.csv`,
      },
    },
  }
}

function snapshot(
  state: InputCacheSnapshotResponse["state"],
  freshness: InputCacheSnapshotResponse["freshness"] = "unknown",
): InputCacheSnapshotResponse {
  return {
    schema_version: 1,
    identity_digest: "identity",
    state,
    freshness,
    generation: null,
  }
}

function buildResponse(joined = false): InputCacheBuildResponse {
  return {
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "identity",
    status: "running",
    joined,
  }
}

function job(
  status: JobStatus,
  message = "",
): InputCacheJobStatusResponse {
  return {
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "identity",
    status,
    terminal_reason: status === "completed" || status === "running" ? null : status,
    message,
    refresh: false,
    build_class: "bounded",
    progress: {
      phase: status === "completed" ? "completed" : status === "running" ? "building" : "failed",
      rows: 0,
      batches: 0,
      bytes: 0,
      elapsed_seconds: 0,
    },
    snapshot: status === "completed" ? snapshot("ready", "fresh") : null,
    error_code: status === "completed" || status === "running" ? null : "build_failed",
  }
}

describe("ensureInputSnapshots", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })
  afterEach(() => vi.useRealTimers())

  function quoteInput(path = "quotes.jsonl"): Node {
    return {
      id: "quote", position: { x: 0, y: 0 },
      data: { nodeType: NODE_TYPES.API_INPUT, config: { path, tables: [] } },
    }
  }

  it("checks the live Quote Input schema and awaits its full cache build", async () => {
    const node = quoteInput()
    vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(false))
    let finish!: (value: JsonCacheBuildResponse) => void
    vi.mocked(buildJsonCache).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const onProgress = vi.fn()
    const onBuildStart = vi.fn()
    let completed = false
    const pending = ensureInputSnapshots([node], { onProgress, onBuildStart })
      .then(() => { completed = true })
    await vi.waitFor(() => expect(buildJsonCache).toHaveBeenCalledOnce())
    expect(completed).toBe(false)
    expect(getJsonCacheStatusForSchema).toHaveBeenCalledWith({
      path: "quotes.jsonl", volatile_schema: node.data.config,
    })
    expect(buildJsonCache).toHaveBeenCalledWith({
      path: "quotes.jsonl", volatile_schema: node.data.config,
    }, { signal: expect.any(AbortSignal) })
    expect(onBuildStart).toHaveBeenCalledOnce()
    expect(onProgress).toHaveBeenCalledWith(expect.stringContaining("Caching Quote Input"))
    finish(jsonBuild)
    await pending
    expect(onProgress).toHaveBeenLastCalledWith(null)
  })

  it.each(["quotes.JSON", "quotes.jsonl", "quotes.ndjson", "quotes.xml"])(
    "reuses a matching Quote Input cache for %s", async (path) => {
      vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(true))
      await ensureInputSnapshots([quoteInput(path)])
      expect(getJsonCacheStatusForSchema).toHaveBeenCalledOnce()
      expect(buildJsonCache).not.toHaveBeenCalled()
    },
  )

  it("does not cache flat-file Quote Inputs", async () => {
    await ensureInputSnapshots([quoteInput("quotes.parquet")])
    expect(getJsonCacheStatusForSchema).not.toHaveBeenCalled()
    expect(buildJsonCache).not.toHaveBeenCalled()
  })

  it("skips path-only Quote Inputs while preparing configured Quote Inputs", async () => {
    const unfinished = quoteInput("unfinished.json")
    unfinished.data.config = { path: "unfinished.json" }
    const configured = quoteInput("configured.json")
    vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(true))

    await ensureInputSnapshots([unfinished, configured])

    expect(getJsonCacheStatusForSchema).toHaveBeenCalledOnce()
    expect(getJsonCacheStatusForSchema).toHaveBeenCalledWith({
      path: "configured.json",
      volatile_schema: configured.data.config,
    })
    expect(buildJsonCache).not.toHaveBeenCalled()
  })

  it("propagates cache build errors and clears progress", async () => {
    vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(false))
    vi.mocked(buildJsonCache).mockRejectedValue(new Error("Cache disk quota exceeded"))
    const onProgress = vi.fn()
    await expect(ensureInputSnapshots([quoteInput()], { onProgress }))
      .rejects.toThrow("Cache disk quota exceeded")
    expect(onProgress).toHaveBeenLastCalledWith(null)
  })

  it("cancels a pending cache build and stops reporting progress", async () => {
    vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(false))
    vi.mocked(buildJsonCache).mockImplementation((_payload, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")))
    }))
    const controller = new AbortController()
    const onProgress = vi.fn()
    const pending = ensureInputSnapshots([quoteInput()], { signal: controller.signal, onProgress })
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" })
    await vi.waitFor(() => expect(buildJsonCache).toHaveBeenCalledOnce())
    controller.abort()
    await rejection
    expect(getJsonCacheProgress).not.toHaveBeenCalled()
  })

  it("reports build progress and stops polling when publication completes", async () => {
    vi.useFakeTimers()
    vi.mocked(getJsonCacheStatusForSchema).mockResolvedValue(jsonStatus(false))
    vi.mocked(getJsonCacheProgress).mockResolvedValue({ active: true, rows: 1234, elapsed: 12.8 })
    let finish!: (value: JsonCacheBuildResponse) => void
    vi.mocked(buildJsonCache).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const onProgress = vi.fn()
    const pending = ensureInputSnapshots([quoteInput()], { onProgress })
    await vi.advanceTimersByTimeAsync(800)
    expect(onProgress).toHaveBeenLastCalledWith("Caching Quote Input as Parquet… · 1,234 rows · 12s")
    finish(jsonBuild)
    await pending
    await vi.advanceTimersByTimeAsync(1600)
    expect(getJsonCacheProgress).toHaveBeenCalledOnce()
    expect(onProgress).toHaveBeenLastCalledWith(null)
  })

  it.each([null, { unexpected: true }])(
    "propagates status failures for Quote Inputs with a declared invalid tables value: %j",
    async (tables) => {
      vi.mocked(getJsonCacheStatusForSchema).mockRejectedValue(new Error("Invalid table schema"))
      const input = quoteInput()
      input.data.config = { path: "quotes.jsonl", tables }
      await expect(ensureInputSnapshots([input])).rejects.toThrow("Invalid table schema")
      expect(getJsonCacheStatusForSchema).toHaveBeenCalledOnce()
      expect(buildJsonCache).not.toHaveBeenCalled()
    },
  )

  it("does not build after a pre-cancelled request", async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(ensureInputSnapshots([quoteInput()], { signal: controller.signal }))
      .rejects.toMatchObject({ name: "AbortError" })
    expect(getJsonCacheStatusForSchema).not.toHaveBeenCalled()
    expect(buildJsonCache).not.toHaveBeenCalled()
  })

  it("builds a missing snapshot with the lazy profile and waits for completion", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse())
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))

    await ensureInputSnapshots([dataInput("quotes")])

    expect(buildInputCache).toHaveBeenCalledWith({
      schema_version: 1,
      config: expect.objectContaining({
        path: "quotes.csv",
      }),
      refresh: false,
      profile: "lazy_sink",
    })
    expect(getInputCacheJob).toHaveBeenCalledWith("job-1")
  })

  it("uses ready snapshots without refreshing either fresh or stale data", async () => {
    vi.mocked(getInputCacheStatus)
      .mockResolvedValueOnce(snapshot("ready", "fresh"))
      .mockResolvedValueOnce(snapshot("ready", "stale"))

    await ensureInputSnapshots([dataInput("fresh"), dataInput("stale")])

    expect(getInputCacheStatus).toHaveBeenCalledTimes(2)
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("joins an active build and waits for its job", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("building"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse(true))
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))

    await ensureInputSnapshots([dataInput("quotes")])

    expect(buildInputCache).toHaveBeenCalledOnce()
    expect(getInputCacheJob).toHaveBeenCalledWith("job-1")
  })

  it("retries an unsupported lazy build once with the eager profile", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache)
      .mockRejectedValueOnce(
        new ApiError(
          "Unsupported snapshot build",
          400,
          "snapshot_build_unsupported: use preview eager",
        ),
      )
      .mockResolvedValueOnce(buildResponse())
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))

    await ensureInputSnapshots([dataInput("quotes")])

    expect(buildInputCache).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ profile: "lazy_sink" }),
    )
    expect(buildInputCache).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ profile: "preview_eager" }),
    )
  })

  it("rejects with the server message when the build is not completed", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse())
    vi.mocked(getInputCacheJob).mockResolvedValue(
      job("error", "Snapshot quota is exhausted."),
    )

    await expect(
      ensureInputSnapshots([dataInput("quotes")]),
    ).rejects.toThrow("Snapshot quota is exhausted.")
  })

  it("ignores nodes that are not Data Inputs", async () => {
    const transform: Node = {
      id: "transform",
      position: { x: 0, y: 0 },
      data: {
        nodeType: NODE_TYPES.POLARS,
        config: {},
      },
    }

    await ensureInputSnapshots([transform])

    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("skips canonical direct Parquet inputs", async () => {
    const direct = dataInput("direct")
    direct.data.config = {
      inputType: "file",
      format: "parquet",
      mode: "scan",
      path: "direct.parquet",
    }

    await ensureInputSnapshots([direct])

    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("skips direct Parquet with a blank mode", async () => {
    const direct = dataInput("blank-mode")
    direct.data.config = {
      inputType: "file",
      format: "parquet",
      mode: "",
      path: "blank-mode.parquet",
    }

    await ensureInputSnapshots([direct])

    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("treats Parquet in read mode as snapshot-backed", async () => {
    const readMode = dataInput("read-mode")
    readMode.data.config = {
      inputType: "file",
      format: "parquet",
      mode: "read",
      path: "read-mode.parquet",
    }
    vi.mocked(getInputCacheStatus).mockResolvedValueOnce(snapshot("ready"))

    await ensureInputSnapshots([readMode])

    expect(getInputCacheStatus).toHaveBeenCalledTimes(1)
    expect(buildInputCache).not.toHaveBeenCalled()
  })
})
