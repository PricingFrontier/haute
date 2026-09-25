/**
 * Import: a forced re-read of a snapshot-backed input, owned by the node that
 * started it, stopped by that node's Stop, and invalidating downstream results
 * whatever its outcome.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { Node } from "@xyflow/react"

import type { InputCacheJobStatusResponse, InputCacheSnapshotResponse } from "../../api/types"
import type { SimpleNode } from "../../panels/editors"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useInputImportStore from "../../stores/useInputImportStore"
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
const emittingTable = { path: "$[:]", label: "quotes", emit: true, columns: [{ name: "id", path: "$[:].id", selected: true }] }

function node(id: string, nodeType: string, config: Record<string, unknown>): SimpleNode {
  return { id, data: { label: id, nodeType, config } } as SimpleNode
}

/** The canvas holds *nodes*, as the import's continuation reads them. */
function onCanvas(...nodes: SimpleNode[]): void {
  useGraphStore.setState({ nodes: nodes as unknown as Node[] })
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

function job(
  status: InputCacheJobStatusResponse["status"],
  rows = 0,
  buildClass: InputCacheJobStatusResponse["build_class"] = "bounded",
): InputCacheJobStatusResponse {
  return {
    schema_version: 1,
    job_id: "import-1",
    identity_digest: "digest",
    status,
    terminal_reason: status === "running" ? null : status,
    message: status === "error" ? "The source could not be read." : "",
    refresh: true,
    build_class: buildClass,
    progress: { phase: status === "running" ? "building" : "completed", rows, batches: 0, bytes: 0, elapsed_seconds: 0 },
    snapshot: null,
    error_code: null,
  }
}

function renderImport(target: SimpleNode, allNodes: SimpleNode[] = [target]) {
  const onImported = vi.fn()
  const view = render(<InputImportButton node={target} allNodes={allNodes} onImported={onImported} />)
  return { onImported, ...view }
}

describe("InputImportButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useNodeDataStore.getState().reset()
    useNodeWorkStore.setState({ running: {} })
    useInputImportStore.setState({ runs: {} })
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
    renderImport(node("flat", "apiInput", { path: "quotes.csv", tables: [emittingTable] }))
    renderImport(node("no-emit", "apiInput", { path: "quotes.jsonl", tables: [] }))
    expect(screen.queryByTestId("input-import")).toBeNull()

    renderImport(node("csv", "dataInput", csvConfig))
    renderImport(node("quotes", "apiInput", { path: "quotes.jsonl", tables: [emittingTable] }))
    expect(screen.getAllByTestId("input-import")).toHaveLength(2)
  })

  it("re-reads the original's source for an instance, then re-previews and invalidates downstream", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("completed"))
    const original = node("original", "dataInput", csvConfig)
    const instance = node("instance", "dataInput", { instanceOf: "original" })
    onCanvas(original, instance)
    const { onImported } = renderImport(instance, [original, instance])
    const epoch = useNodeDataStore.getState().epoch

    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })

    await waitFor(() => expect(onImported).toHaveBeenCalledWith("instance"))
    expect(buildInputCache).toHaveBeenCalledWith({ schema_version: 1, config: csvConfig, refresh: true })
    expect(useNodeDataStore.getState().epoch).toBeGreaterThan(epoch)
  })

  it("shows rows read so far, and the node's Stop cancels the import without re-previewing", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 1200))
    vi.mocked(cancelInputCacheJob).mockResolvedValue({ schema_version: 1, job_id: "import-1", cancellation_requested: true, status: "cancelled" })
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    const { onImported } = renderImport(csv)
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

  it("shows no row count for a build that reports none", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 0, "admitted_eager"))
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    renderImport(csv)

    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })

    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())
    expect(screen.getByTestId("input-import")).toHaveTextContent("Importing…")
    await act(async () => stopNodeWork("csv"))
  })

  it("stays with the node that started it when another node is opened", async () => {
    let finish!: (value: InputCacheJobStatusResponse) => void
    vi.mocked(getInputCacheJob)
      .mockResolvedValueOnce(job("running", 10))
      .mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const a = node("a", "dataInput", csvConfig)
    const b = node("b", "dataInput", { ...csvConfig, path: "other.csv" })
    onCanvas(a, b)
    const { onImported, rerender } = renderImport(a, [a, b])
    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalledTimes(2))

    rerender(<InputImportButton node={b} allNodes={[a, b]} onImported={onImported} />)

    // B's button starts nothing and shows nothing of A's import.
    expect(screen.getByTestId("input-import")).toHaveTextContent("Import")
    expect(Object.values(useNodeWorkStore.getState().running)).toEqual(["a"])

    await act(async () => {
      finish(job("completed"))
    })
    await waitFor(() => expect(onImported).toHaveBeenCalledWith("a"))
  })

  it("does not re-preview when the node's source changed while it imported", async () => {
    let finish!: (value: InputCacheJobStatusResponse) => void
    vi.mocked(getInputCacheJob).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    const { onImported } = renderImport(csv)
    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())

    onCanvas(node("csv", "dataInput", { ...csvConfig, path: "changed.csv" }))
    await act(async () => {
      finish(job("completed"))
    })

    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
    expect(onImported).not.toHaveBeenCalled()
  })

  it("retires when the document is replaced, so the new document's node can import", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 5))
    vi.mocked(cancelInputCacheJob).mockResolvedValue({ schema_version: 1, job_id: "import-1", cancellation_requested: true, status: "cancelled" })
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    const { onImported } = renderImport(csv)
    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())

    // Another pipeline is loaded; its node reuses the id "csv".
    act(() => {
      useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    })

    await waitFor(() => expect(useInputImportStore.getState().runs.csv).toBeUndefined())
    expect(Object.values(useNodeWorkStore.getState().running)).not.toContain("csv")
    await waitFor(() => expect(cancelInputCacheJob).toHaveBeenCalledWith("import-1"))
    expect(screen.getByTestId("input-import")).toHaveTextContent("Import")
    expect(onImported).not.toHaveBeenCalled()
  })

  it("keeps cancelling, after the document is replaced, a build an earlier Stop failed to stop", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 5))
    vi.mocked(cancelInputCacheJob)
      .mockRejectedValueOnce(new Error("cancel route unreachable"))
      .mockResolvedValue({ schema_version: 1, job_id: "import-1", cancellation_requested: true, status: "cancelled" })
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    renderImport(csv)
    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())
    await act(async () => stopNodeWork("csv"))
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((toast) => toast.text.includes("Stopping the import failed"))).toBe(true),
    )

    act(() => {
      useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    })

    await waitFor(() => expect(cancelInputCacheJob).toHaveBeenCalledTimes(2))
    expect(useInputImportStore.getState().runs.csv).toBeUndefined()
  })

  it("keeps cancelling a retired import's build whose cancellation fails", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("running", 5))
    vi.mocked(cancelInputCacheJob)
      .mockRejectedValueOnce(new Error("cancel route unreachable"))
      .mockResolvedValue({ schema_version: 1, job_id: "import-1", cancellation_requested: true, status: "cancelled" })
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    renderImport(csv)
    await act(async () => {
      fireEvent.click(await screen.findByTestId("input-import"))
    })
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalled())

    act(() => {
      useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    })

    // The retirement's own cancellation fails; the retry in the background succeeds.
    await waitFor(() => expect(cancelInputCacheJob).toHaveBeenCalledTimes(2), { timeout: 5_000 })
    expect(useInputImportStore.getState().runs.csv).toBeUndefined()
  })

  it("reports a failed import and still invalidates downstream results", async () => {
    vi.mocked(getInputCacheJob).mockResolvedValue(job("error"))
    const csv = node("csv", "dataInput", csvConfig)
    onCanvas(csv)
    const { onImported } = renderImport(csv)
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
