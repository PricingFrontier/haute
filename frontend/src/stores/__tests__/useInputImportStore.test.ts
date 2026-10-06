/**
 * An Import's lifecycle as its store runs it: one per node, re-previewing only
 * the node that still reads the same source, and stopping its builds through
 * failed stops and document replacement.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { waitFor } from "@testing-library/react"
import type { Node } from "@xyflow/react"

import type { InputCacheJobStatusResponse } from "../../api/types"

vi.mock("../../api/client", () => ({
  buildInputCache: vi.fn(),
  getInputCacheStatus: vi.fn(),
  getInputCacheJob: vi.fn(),
  cancelInputCacheJob: vi.fn(),
}))

import { buildInputCache, cancelInputCacheJob, getInputCacheJob } from "../../api/client"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGraphStore from "../useGraphStore"
import useInputImportStore, { startInputImport } from "../useInputImportStore"
import useNodeDataStore from "../useNodeDataStore"
import useNodeWorkStore, { stopNodeWork } from "../useNodeWorkStore"
import useToastStore from "../useToastStore"

const csvConfig = { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv" }

function input(id: string, config: Record<string, unknown> = csvConfig): Node {
  return { id, position: { x: 0, y: 0 }, data: { label: id, nodeType: "dataInput", config } }
}

function job(status: InputCacheJobStatusResponse["status"]): InputCacheJobStatusResponse {
  return {
    schema_version: 1,
    job_id: "import-1",
    identity_digest: "digest",
    status,
    terminal_reason: status === "running" ? null : status,
    message: "",
    refresh: true,
    build_class: "bounded",
    progress: { phase: "building", rows: 3, batches: 0, bytes: 0, elapsed_seconds: 0 },
    snapshot: null,
    error_code: null,
  }
}

const cancelled = { schema_version: 1 as const, job_id: "import-1", cancellation_requested: true, status: "cancelled" as const }

function replaceDocument(): void {
  useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
}

function toasted(text: string): boolean {
  return useToastStore.getState().toasts.some((toast) => toast.text.includes(text))
}

describe("startInputImport", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useInputImportStore.setState({ runs: {} })
    useNodeWorkStore.setState({ running: {} })
    useNodeDataStore.getState().reset()
    useToastStore.setState({ toasts: [] })
    useGraphStore.setState({ nodes: [input("csv")] })
    vi.mocked(buildInputCache).mockResolvedValue({
      schema_version: 1,
      job_id: "import-1",
      identity_digest: "digest",
      status: "running",
      joined: false,
      forced: true,
      build_class: "bounded",
    })
  })
  afterEach(() => vi.useRealTimers())

  it("runs one import per node: a second start while it runs is ignored", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running"))
    startInputImport("csv", input("csv"), vi.fn())
    startInputImport("csv", input("csv"), vi.fn())

    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())
    expect(buildInputCache).toHaveBeenCalledTimes(1)
    stopNodeWork("csv")
    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
  })

  it("starts nothing for an input that reads no snapshot", () => {
    startInputImport("direct", input("direct", { inputType: "file", format: "parquet", mode: "scan", path: "a.parquet" }), vi.fn())

    expect(useInputImportStore.getState().runs.direct).toBeUndefined()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("does not re-preview a node deleted while it imported", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const onImported = vi.fn()
    startInputImport("csv", input("csv"), onImported)
    useGraphStore.setState({ nodes: [] })

    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
    expect(onImported).not.toHaveBeenCalled()
  })

  it("does not re-preview a node that no longer reads a snapshot", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const onImported = vi.fn()
    startInputImport("csv", input("csv"), onImported)
    useGraphStore.setState({ nodes: [input("csv", { inputType: "file", format: "parquet", mode: "scan", path: "a.parquet" })] })

    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
    expect(onImported).not.toHaveBeenCalled()
  })

  it("stays running through document changes that keep its fence", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running"))
    startInputImport("csv", input("csv"), vi.fn())
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())

    useDocumentStatusStore.setState({ documentFingerprint: "unrelated-change" } as never)

    expect(useInputImportStore.getState().runs.csv).toBeDefined()
    stopNodeWork("csv")
    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
  })

  it("under a document that cannot execute, neither invalidates nor re-previews", async () => {
    useDocumentStatusStore.setState({ loadStatus: "ready", capabilities: { can_execute: false } } as never)
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const onImported = vi.fn()
    const epoch = useNodeDataStore.getState().epoch
    try {
      startInputImport("csv", input("csv"), onImported)

      await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
      expect(useNodeDataStore.getState().epoch).toBe(epoch)
      expect(onImported).not.toHaveBeenCalled()
    } finally {
      useDocumentStatusStore.getState().reset()
    }
  })

  it("a Stop after a failed stop cancels again, and keeps trying until it succeeds", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running"))
    vi.mocked(cancelInputCacheJob)
      .mockRejectedValueOnce(new Error("unreachable"))
      .mockRejectedValueOnce(new Error("still unreachable"))
      .mockResolvedValue(cancelled)
    startInputImport("csv", input("csv"), vi.fn())
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())

    stopNodeWork("csv")
    await waitFor(() => expect(toasted("Stopping the import failed")).toBe(true))
    useToastStore.setState({ toasts: [] })

    stopNodeWork("csv")
    await waitFor(() => expect(toasted("Press Stop to try again")).toBe(true))
    expect(useInputImportStore.getState().runs.csv).toBeDefined()

    stopNodeWork("csv")
    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
    expect(cancelInputCacheJob).toHaveBeenCalledTimes(3)
  })

  it("reports a retired import whose build never stops", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running"))
    vi.mocked(cancelInputCacheJob).mockRejectedValue(new Error("unreachable"))
    startInputImport("csv", input("csv"), vi.fn())
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())
    vi.useFakeTimers({ shouldAdvanceTime: true })

    replaceDocument()
    await vi.waitFor(() => expect(cancelInputCacheJob).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(5 * 2_000)

    await vi.waitFor(() =>
      expect(toasted("could not be stopped and may still be running")).toBe(true),
    )
    // The retirement's own cancellation, then five retries in the background.
    expect(cancelInputCacheJob).toHaveBeenCalledTimes(6)
    expect(useInputImportStore.getState().runs.csv).toBeUndefined()
  })
})
