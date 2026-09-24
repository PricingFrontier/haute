import type { Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type {
  InputCacheBuildResponse,
  InputCacheJobStatusResponse,
  InputCacheSnapshotResponse,
  JobStatus,
} from "../../api/types"
import { NODE_TYPES } from "../../utils/nodeTypes"

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>()
  return {
    ...actual,
    buildInputCache: vi.fn(),
    cancelInputCacheJob: vi.fn(),
    getInputCacheJob: vi.fn(),
    getInputCacheStatus: vi.fn(),
  }
})

import {
  ApiError,
  buildInputCache,
  cancelInputCacheJob,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../api/client"
import { ensureInputSnapshots } from "../ensureInputSnapshots"

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

  it("checks the live Quote Input status and awaits its full cache build", async () => {
    const node = quoteInput()
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse())
    let finish!: (value: InputCacheJobStatusResponse) => void
    vi.mocked(getInputCacheJob).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const onProgress = vi.fn()
    const onBuildStart = vi.fn()
    let completed = false
    const pending = ensureInputSnapshots([node], { onProgress, onBuildStart })
      .then(() => { completed = true })
    await vi.waitFor(() => expect(buildInputCache).toHaveBeenCalledOnce())
    expect(completed).toBe(false)
    expect(getInputCacheStatus).toHaveBeenCalledWith({
      schema_version: 1, node_type: "apiInput", config: node.data.config,
    })
    expect(buildInputCache).toHaveBeenCalledWith({
      schema_version: 1, node_type: "apiInput", config: node.data.config,
      refresh: false, profile: "lazy_sink",
    })
    expect(onBuildStart).toHaveBeenCalledOnce()
    expect(onProgress).toHaveBeenCalledWith(expect.stringContaining("Caching Quote Input"))
    finish(job("completed"))
    await pending
    expect(onProgress).toHaveBeenLastCalledWith(null)
  })

  it.each(["quotes.JSON", "quotes.jsonl", "quotes.ndjson", "quotes.xml"])(
    "reuses a matching ready+fresh Quote Input cache for %s", async (path) => {
      vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("ready", "fresh"))
      await ensureInputSnapshots([quoteInput(path)])
      expect(getInputCacheStatus).toHaveBeenCalledOnce()
      expect(buildInputCache).not.toHaveBeenCalled()
    },
  )

  it("rebuilds a ready but stale Quote Input cache", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("ready", "stale"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse())
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))

    await ensureInputSnapshots([quoteInput()])

    expect(getInputCacheStatus).toHaveBeenCalledOnce()
    expect(buildInputCache).toHaveBeenCalledOnce()
  })

  it("does not cache flat-file Quote Inputs", async () => {
    await ensureInputSnapshots([quoteInput("quotes.parquet")])
    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("skips path-only Quote Inputs while preparing configured Quote Inputs", async () => {
    const unfinished = quoteInput("unfinished.json")
    unfinished.data.config = { path: "unfinished.json" }
    const configured = quoteInput("configured.json")
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("ready", "fresh"))

    await ensureInputSnapshots([unfinished, configured])

    expect(getInputCacheStatus).toHaveBeenCalledOnce()
    expect(getInputCacheStatus).toHaveBeenCalledWith({
      schema_version: 1, node_type: "apiInput", config: configured.data.config,
    })
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("propagates cache build errors and clears progress", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache).mockRejectedValue(new Error("Cache disk quota exceeded"))
    const onProgress = vi.fn()
    await expect(ensureInputSnapshots([quoteInput()], { onProgress }))
      .rejects.toThrow("Cache disk quota exceeded")
    expect(onProgress).toHaveBeenLastCalledWith(null)
  })

  it("cancels a build the server admitted while its request was pending", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    let admit!: (value: InputCacheBuildResponse) => void
    vi.mocked(buildInputCache).mockImplementation(() => new Promise((resolve) => { admit = resolve }))
    vi.mocked(cancelInputCacheJob).mockResolvedValue({
      schema_version: 1, job_id: "job-1", cancellation_requested: true, status: "cancelled",
    })
    const controller = new AbortController()
    const onProgress = vi.fn()
    const pending = ensureInputSnapshots([quoteInput()], { signal: controller.signal, onProgress })
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" })
    await vi.waitFor(() => expect(buildInputCache).toHaveBeenCalledOnce())
    // The build request carries no abort signal: the job it admits is cancelled by id.
    expect(vi.mocked(buildInputCache).mock.calls[0]).toHaveLength(1)
    controller.abort()
    const reportedBeforeAbort = onProgress.mock.calls.length
    admit(buildResponse())
    await rejection
    expect(cancelInputCacheJob).toHaveBeenCalledWith("job-1")
    expect(getInputCacheJob).not.toHaveBeenCalled()
    expect(onProgress).toHaveBeenCalledTimes(reportedBeforeAbort)
  })

  it("emits progress messages in order and ends with null", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot("missing"))
    vi.mocked(buildInputCache).mockResolvedValue(buildResponse())
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const onProgress = vi.fn()

    await ensureInputSnapshots([quoteInput()], { onProgress })

    expect(onProgress.mock.calls.map((call) => call[0])).toEqual([
      "Checking Quote Input cache…",
      "Caching Quote Input tables as Parquet…",
      null,
    ])
  })

  it.each([null, { unexpected: true }])(
    "propagates status failures for Quote Inputs with a declared invalid tables value: %j",
    async (tables) => {
      vi.mocked(getInputCacheStatus).mockRejectedValue(new Error("Invalid table schema"))
      const input = quoteInput()
      input.data.config = { path: "quotes.jsonl", tables }
      await expect(ensureInputSnapshots([input])).rejects.toThrow("Invalid table schema")
      expect(getInputCacheStatus).toHaveBeenCalledOnce()
      expect(buildInputCache).not.toHaveBeenCalled()
    },
  )

  it("does not build after a pre-cancelled request", async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(ensureInputSnapshots([quoteInput()], { signal: controller.signal }))
      .rejects.toMatchObject({ name: "AbortError" })
    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
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
    expect(getInputCacheJob).toHaveBeenCalledWith("job-1", { signal: expect.any(AbortSignal) })
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
    expect(getInputCacheJob).toHaveBeenCalledWith("job-1", { signal: expect.any(AbortSignal) })
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
