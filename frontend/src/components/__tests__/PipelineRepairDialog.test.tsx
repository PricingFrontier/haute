import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ApiError } from "../../api/client"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import PipelineRepairDialog from "../PipelineRepairDialog"

const { applyRemoveUnavailableNode, applyRecoverUnavailableNode } = vi.hoisted(() => ({
  applyRemoveUnavailableNode: vi.fn(),
  applyRecoverUnavailableNode: vi.fn(),
}))

vi.mock("../../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/client")>(),
  applyRemoveUnavailableNode,
  applyRecoverUnavailableNode,
}))

function applied(overrides: Record<string, unknown> = {}) {
  return {
    repair_kind: "remove_unavailable_node" as const,
    applied_artifacts: ["server-main.py"],
    changes: [{ path: "server-main.py", operation: "update" as const, description: "Remove broken node.", diff: "- @pipeline.broken", diff_truncated: false }],
    document: makePipelineEditorDocument(),
    field_changes: [],
    completeness: [],
    previous_config: null,
    ...overrides,
  }
}

function renderDialog(action?: "update" | "reset" | "recover") {
  const onClose = vi.fn()
  const onApplied = vi.fn()
  render(<PipelineRepairDialog target={{ sourceFile: "target.py", recoveryId: "target@1", action }} sourceFile="root.py" sourceRevision="root-rev" onClose={onClose} onApplied={onApplied} />)
  return { onClose, onApplied }
}

describe("PipelineRepairDialog", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("applies a removal once against the displayed revision and adopts its document", async () => {
    const document = makePipelineEditorDocument({ source_file: "authoritative.py" })
    applyRemoveUnavailableNode.mockResolvedValueOnce(applied({ document }))
    const { onApplied } = renderDialog()
    const button = screen.getByRole("button", { name: "Remove node" })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(document))
    expect(applyRemoveUnavailableNode).toHaveBeenCalledTimes(1)
    expect(applyRemoveUnavailableNode).toHaveBeenCalledWith({
      sourceFile: "root.py", sourceRevision: "root-rev", targetSourceFile: "target.py", targetRecoveryId: "target@1", deleteConfig: false,
    })
  })

  it("keeps the config by default and deletes it only on the explicit choice", async () => {
    applyRemoveUnavailableNode.mockResolvedValueOnce(applied())
    renderDialog()
    const checkbox = screen.getByRole("checkbox", { name: "Also delete config" })
    expect(checkbox).not.toBeChecked()
    expect(screen.getByText(/A config file shared with another node is never deleted/)).toBeInTheDocument()
    fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole("button", { name: "Remove node" }))
    await waitFor(() => expect(applyRemoveUnavailableNode).toHaveBeenCalledWith(expect.objectContaining({ deleteConfig: true })))
  })

  it("keeps structured conflict errors visible and open", async () => {
    applyRemoveUnavailableNode.mockRejectedValueOnce(new ApiError("HTTP 409", 409, undefined, undefined, { code: "repair_revision_conflict", message: "Reload before repairing." }))
    const { onClose } = renderDialog()
    fireEvent.click(screen.getByRole("button", { name: "Remove node" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("repair_revision_conflict: Reload before repairing.")
    expect(screen.getByTestId("pipeline-repair-dialog")).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it("updates through the recovery endpoint, states the shared-definition effect and hides config deletion", async () => {
    applyRecoverUnavailableNode.mockResolvedValueOnce(applied({ repair_kind: "update_node" }))
    const { onApplied } = renderDialog("update")
    expect(screen.getByRole("heading", { name: "Update to current format" })).toBeInTheDocument()
    expect(screen.getByText(/the update affects every one of them/)).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Also delete config" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Update to current format" }))
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1))
    expect(applyRecoverUnavailableNode).toHaveBeenCalledWith({
      sourceFile: "root.py", sourceRevision: "root-rev", targetSourceFile: "target.py", targetRecoveryId: "target@1", action: "update",
    })
  })

  it("applies reset with the dialog's action, keeping a conflict open", async () => {
    applyRecoverUnavailableNode.mockRejectedValueOnce(new ApiError(
      "HTTP 409",
      409,
      undefined,
      undefined,
      { code: "repair_revision_conflict", message: "Reload before repairing." },
    ))
    renderDialog("reset")
    fireEvent.click(screen.getByRole("button", { name: "Reset node" }))
    await waitFor(() => expect(applyRecoverUnavailableNode).toHaveBeenCalledWith(expect.objectContaining({ action: "reset" })))
    expect(await screen.findByRole("alert")).toHaveTextContent("repair_revision_conflict: Reload before repairing.")
    expect(screen.getByTestId("pipeline-repair-dialog")).toBeInTheDocument()
  })

  it("cannot close through Escape or the backdrop while apply is in flight", async () => {
    let resolveApply!: (value: ReturnType<typeof applied>) => void
    applyRemoveUnavailableNode.mockReturnValueOnce(new Promise((resolve) => {
      resolveApply = resolve
    }))
    const { onClose } = renderDialog()
    fireEvent.click(screen.getByRole("button", { name: "Remove node" }))

    fireEvent.keyDown(document, { key: "Escape" })
    fireEvent.click(screen.getByTestId("pipeline-repair-dialog"))
    expect(onClose).not.toHaveBeenCalled()

    await waitFor(() => expect(applyRemoveUnavailableNode).toHaveBeenCalledOnce())
    await act(async () => resolveApply(applied()))
  })

  it("records the recover session summary from the apply response", async () => {
    const { recoverySummaryKey, useRecoverySummaryStore } = await import("../../stores/useRecoverySummaryStore")
    useRecoverySummaryStore.getState().reset()
    applyRecoverUnavailableNode.mockResolvedValueOnce(applied({
      repair_kind: "recover_node",
      changes: [{ path: "server-main.py", operation: "update" as const, description: "Regenerate 'broken' from its recovered settings.", diff: "-old / +new", diff_truncated: false }],
      field_changes: [
        { path: "/path", outcome: "retained", reason: "Valid under the current configuration contract." },
        { path: "/cacheMode", outcome: "removed", reason: "Unknown or retired configuration field." },
      ],
      completeness: [{ element_id: "target@1", path: "path", code: "required", message: "Format 'parquet' requires a non-empty 'path'." }],
      previous_config: { cacheMode: "snapshot" },
    }))
    const { onApplied } = renderDialog("recover")

    fireEvent.click(screen.getByRole("button", { name: "Recover settings" }))
    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1))
    expect(applyRecoverUnavailableNode).toHaveBeenCalledWith({
      sourceFile: "root.py", sourceRevision: "root-rev", targetSourceFile: "target.py", targetRecoveryId: "target@1", action: "recover",
    })
    const recorded = useRecoverySummaryStore.getState().summaries[recoverySummaryKey("root.py", "target@1")]
    expect(recorded).toBeDefined()
    expect(recorded.fieldChanges).toHaveLength(2)
    expect(recorded.completeness[0].path).toBe("path")
    expect(recorded.previousConfig).toEqual({ cacheMode: "snapshot" })
    expect(recorded.changes[0].diff).toBe("-old / +new")
  })
})
