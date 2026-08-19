import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ApiError } from "../../api/client"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import PipelineRepairDialog from "../PipelineRepairDialog"

const { dryRunRemoveUnavailableNode, applyRemoveUnavailableNode } = vi.hoisted(() => ({
  dryRunRemoveUnavailableNode: vi.fn(),
  applyRemoveUnavailableNode: vi.fn(),
}))

vi.mock("../../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/client")>(),
  dryRunRemoveUnavailableNode,
  applyRemoveUnavailableNode,
}))

const hash = (letter: string) => letter.repeat(64)
function plan(overrides: Record<string, unknown> = {}) {
  return {
    repair_kind: "remove_unavailable_node" as const,
    source_file: "server-main.py",
    source_revision: "server-rev",
    target_source_file: "server-main.py",
    target_recovery_id: "broken@10",
    target_authored_id: "broken",
    delete_config: false,
    plan_hash: hash("a"),
    changes: [{ path: "server-main.py", operation: "update" as const, description: "Remove broken node.", diff: "- @pipeline.broken", diff_truncated: false }],
    retained_artifacts: ["custom/sidecar.json"],
    warnings: ["Config will be retained."],
    predicted_load_status: "ready" as const,
    ...overrides,
  }
}

function renderDialog() {
  const onClose = vi.fn()
  const onApplied = vi.fn()
  render(<PipelineRepairDialog target={{ sourceFile: "target.py", recoveryId: "target@1" }} sourceFile="root.py" sourceRevision="root-rev" onClose={onClose} onApplied={onApplied} />)
  return { onClose, onApplied }
}

describe("PipelineRepairDialog", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("dry-runs before apply and displays the bounded plan", async () => {
    dryRunRemoveUnavailableNode.mockResolvedValueOnce(plan())
    renderDialog()
    await screen.findByText("Remove broken node.")
    expect(dryRunRemoveUnavailableNode).toHaveBeenCalledWith({
      sourceFile: "root.py", sourceRevision: "root-rev", targetSourceFile: "target.py", targetRecoveryId: "target@1", deleteConfig: false,
    }, expect.anything())
    expect(screen.getByText("update: server-main.py")).toBeInTheDocument()
    expect(screen.getByText("- @pipeline.broken")).toBeInTheDocument()
    expect(screen.getByText("ready")).toBeInTheDocument()
    expect(screen.getByText("Config will be retained.")).toBeInTheDocument()
    expect(screen.getByText("Retained config: custom/sidecar.json")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Also delete config" })).not.toBeChecked()
    expect(applyRemoveUnavailableNode).not.toHaveBeenCalled()
  })

  it("invalidates the old plan and replans config deletion while retaining the choice", async () => {
    let resolveSecond!: (value: ReturnType<typeof plan>) => void
    dryRunRemoveUnavailableNode
      .mockResolvedValueOnce(plan())
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve }))
      .mockResolvedValueOnce(plan())
    renderDialog()
    await screen.findByText("Remove broken node.")
    fireEvent.click(screen.getByRole("checkbox", { name: "Also delete config" }))
    expect(screen.getByRole("button", { name: "Remove node" })).toBeDisabled()
    expect(dryRunRemoveUnavailableNode).toHaveBeenLastCalledWith(expect.objectContaining({ deleteConfig: true }), expect.anything())
    expect(screen.getByRole("checkbox", { name: "Also delete config" })).toBeChecked()
    resolveSecond(plan({ delete_config: true, plan_hash: hash("b"), retained_artifacts: [] }))
    await screen.findByText("Remove broken node.")
    expect(screen.getByRole("checkbox", { name: "Also delete config" })).toBeChecked()
    expect(screen.getByText("Config selected for deletion: custom/sidecar.json")).toBeInTheDocument()
    expect(screen.queryByText("Retained config: custom/sidecar.json")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("checkbox", { name: "Also delete config" }))
    expect(dryRunRemoveUnavailableNode).toHaveBeenLastCalledWith(expect.objectContaining({ deleteConfig: false }), expect.anything())
  })

  it("applies only the current returned plan once and adopts its document", async () => {
    const current = plan({ source_file: "authoritative.py", source_revision: "authoritative-rev", target_source_file: "target-authoritative.py", target_recovery_id: "target-authoritative@2", delete_config: true, plan_hash: hash("c"), retained_artifacts: [] })
    dryRunRemoveUnavailableNode.mockResolvedValueOnce(current)
    const document = makePipelineEditorDocument({ source_file: "authoritative.py" })
    applyRemoveUnavailableNode.mockResolvedValueOnce({ repair_kind: "remove_unavailable_node", plan_hash: hash("c"), applied_artifacts: ["authoritative.py"], document })
    const { onApplied } = renderDialog()
    await screen.findByText("Remove broken node.")
    const button = screen.getByRole("button", { name: "Remove node" })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(document))
    expect(applyRemoveUnavailableNode).toHaveBeenCalledTimes(1)
    expect(applyRemoveUnavailableNode).toHaveBeenCalledWith({
      sourceFile: "authoritative.py", sourceRevision: "authoritative-rev", targetSourceFile: "target-authoritative.py", targetRecoveryId: "target-authoritative@2", deleteConfig: true, planHash: hash("c"),
    })
  })

  it("keeps structured conflict errors visible and open", async () => {
    dryRunRemoveUnavailableNode.mockResolvedValueOnce(plan())
    applyRemoveUnavailableNode.mockRejectedValueOnce(new ApiError("HTTP 409", 409, undefined, undefined, { code: "repair_plan_stale", message: "Plan changed." }))
    const { onClose } = renderDialog()
    await screen.findByText("Remove broken node.")
    fireEvent.click(screen.getByRole("button", { name: "Remove node" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("repair_plan_stale: Plan changed.")
    expect(screen.getByTestId("pipeline-repair-dialog")).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it("cannot close through Escape or the backdrop while apply is in flight", async () => {
    dryRunRemoveUnavailableNode.mockResolvedValueOnce(plan())
    let resolveApply!: (value: {
      repair_kind: "remove_unavailable_node"
      plan_hash: string
      applied_artifacts: string[]
      document: ReturnType<typeof makePipelineEditorDocument>
    }) => void
    applyRemoveUnavailableNode.mockReturnValueOnce(new Promise((resolve) => {
      resolveApply = resolve
    }))
    const { onClose } = renderDialog()
    await screen.findByText("Remove broken node.")
    fireEvent.click(screen.getByRole("button", { name: "Remove node" }))

    fireEvent.keyDown(document, { key: "Escape" })
    fireEvent.click(screen.getByTestId("pipeline-repair-dialog"))
    expect(onClose).not.toHaveBeenCalled()

    await waitFor(() => expect(applyRemoveUnavailableNode).toHaveBeenCalledOnce())
    await act(async () => resolveApply({
        repair_kind: "remove_unavailable_node",
        plan_hash: hash("a"),
        applied_artifacts: ["server-main.py"],
        document: makePipelineEditorDocument(),
    }))
  })
})
