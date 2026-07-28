import type { Node } from "@xyflow/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
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
    getInputCacheJob: vi.fn(),
    getInputCacheStatus: vi.fn(),
  }
})

import {
  ApiError,
  buildInputCache,
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
