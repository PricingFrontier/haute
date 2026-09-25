/**
 * Import: a forced re-read of a snapshot-backed input, stopped by the node's
 * Stop, and invalidating downstream results whatever its outcome.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

import type { InputCacheJobStatusResponse, InputCacheSnapshotResponse } from "../../api/types"
import type { SimpleNode } from "../../panels/editors"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useNodeWorkStore, { stopNodeWork } from "../../stores/useNodeWorkStore"
import useToastStore from "../../stores/useToastStore"
import { importedTitle } from "../../utils/importedTitle"

vi.mock("../../api/client", () => ({
  buildInputCache: vi.fn(),
  getInputCacheStatus: vi.fn(),
  getInputCacheJob: vi.fn(),
  cancelInputCacheJob: vi.fn(),
}))

import {
  buildInputCache,
  cancelInputCacheJob,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../api/client"
import InputImportButton from "../InputImportButton"

const csvConfig = { inputType: "file", format: "csv", mode: "scan", path: "quotes.csv" }

function node(id: string, nodeType: string, config: Record<string, unknown>): SimpleNode {
  return { id, data: { label: id, nodeType, config } } as SimpleNode
}

function snapshot(overrides: Partial<InputCacheSnapshotResponse> = {}): InputCacheSnapshotResponse {
  return {
    schema_version: 1,
    identity_digest: "digest",
    state: "ready",
    freshness: "unknown",
    generation: {
      generation_id: "g1",
      row_count: 2,
      column_count: 1,
      columns: {},
      size_bytes: 10,
      created_at: Date.now() / 1000 - 120,
      build_class: "bounded",
    },
    ...overrides,
  }
}

function job(status: InputCacheJobStatusResponse["status"], rows = 0): InputCacheJobStatusResponse {
  return {
    schema_version: 1,
    job_id: "import-1",
    identity_digest: "digest",
    status,
    terminal_reason: status === "running" ? null : status,
    message: status === "error" ? "The source could not be read." : "",
    refresh: true,
    build_class: "bounded",
    progress: { phase: status === "running" ? "building" : "completed", rows, batches: 0, bytes: 0, elapsed_seconds: 0 },
    snapshot: null,
    error_code: null,
  }
}

function renderImport(target: SimpleNode, allNodes: SimpleNode[] = [target]) {
  const onImported = vi.fn()
  render(<InputImportButton node={target} allNodes={allNodes} onImported={onImported} />)
  return onImported
}

describe("InputImportButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNodeDataStore.getState().reset()
    useNodeWorkStore.setState({ running: {} })
    useToastStore.setState({ toasts: [] })
    vi.mocked(getInputCacheStatus).mockResolvedValue(snapshot())
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
  afterEach(cleanup)

  it("is offered only for inputs that read a snapshot", () => {
    renderImport(node("direct", "dataInput", { inputType: "file", format: "parquet", mode: "scan", path: "a.parquet" }))
    renderImport(node("flat", "apiInput", { path: "quotes.csv", tables: [] }))
    expect(screen.queryByTestId("input-import")).toBeNull()

    renderImport(node("csv", "dataInput", csvConfig))
    renderImport(node("quotes", "apiInput", { path: "quotes.jsonl", tables: [] }))
    expect(screen.getAllByTestId("input-import")).toHaveLength(2)
  })

  it("re-reads the original's source for an instance, then re-previews and invalidates downstream", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const original = node("original", "dataInput", csvConfig)
    const instance = node("instance", "dataInput", { instanceOf: "original" })
    const onImported = renderImport(instance, [original, instance])
    const epoch = useNodeDataStore.getState().epoch

    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1))
    expect(buildInputCache).toHaveBeenCalledWith({ schema_version: 1, config: csvConfig, refresh: true })
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epoch)
  })

  it("shows rows read so far, and the node's Stop cancels the import without re-previewing", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 1200))
    vi.mocked(cancelInputCacheJob).mockResolvedValue({ schema_version: 1, job_id: "import-1", cancellation_requested: true, status: "cancelled" })
    const onImported = renderImport(node("csv", "dataInput", csvConfig))
    const epoch = useNodeDataStore.getState().epoch

    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    expect(await screen.findByText("Importing · 1,200 rows")).toBeInTheDocument()
    expect(Object.values(useNodeWorkStore.getState().running)).toContain("csv")

    await act(async () => {
      stopNodeWork("csv")
    })

    await waitFor(() => expect(screen.getByTestId("input-import")).toHaveTextContent("Import"))
    expect(cancelInputCacheJob).toHaveBeenCalledWith("import-1")
    expect(onImported).not.toHaveBeenCalled()
    // A stopped import may have published part of the data.
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epoch)
    expect(Object.values(useNodeWorkStore.getState().running)).not.toContain("csv")
  })

  it("reports a failed import and still invalidates downstream results", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("error"))
    const onImported = renderImport(node("csv", "dataInput", csvConfig))
    const epoch = useNodeDataStore.getState().epoch

    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })

    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((toast) => toast.text.includes("Import failed: The source could not be read."))).toBe(true),
    )
    expect(onImported).not.toHaveBeenCalled()
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epoch)
  })

  it("says when the input was last imported", async () => {
    renderImport(node("csv", "dataInput", csvConfig))
    await waitFor(() => expect(screen.getByTestId("input-import").title).toMatch(/^Imported 2 min ago\./))
  })
})

describe("importedTitle", () => {
  const now = new Date("2026-09-25T12:00:00Z")
  const at = now.getTime() / 1000 - 30
  const generation = { generation_id: "g", row_count: 1, column_count: 1, columns: {}, size_bytes: 1, created_at: at, build_class: "bounded" as const }

  it("names a Quote Input whose failed import left some tables unpublished", () => {
    expect(
      importedTitle(
        snapshot({
          generation: null,
          tables: [
            { label: "quotes", identity_digest: "a", state: "ready", freshness: "fresh", generation },
            { label: "drivers", identity_digest: "b", state: "missing", freshness: "unknown", generation: null },
          ],
        }),
        now,
      ),
    ).toBe("Partly imported (1 of 2 tables), just now")
  })

  it("says an input with nothing published was never imported", () => {
    expect(importedTitle(snapshot({ generation: null }), now)).toBe("Not imported yet")
    expect(
      importedTitle(
        snapshot({ generation: null, tables: [{ label: "quotes", identity_digest: "a", state: "missing", freshness: "unknown", generation: null }] }),
        now,
      ),
    ).toBe("Not imported yet")
  })
})
