import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import RestoreConfirmModal from "../RestoreConfirmModal"
import useGitStore from "../../stores/useGitStore"
import useGraphStore from "../../stores/useGraphStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

describe("RestoreConfirmModal", () => {
  beforeEach(() => {
    useGitStore.setState({ restoreTarget: { sha: "def456", label: "v1.0" } })
    useGraphStore.setState({ dirty: false })
    useWorkbenchStore.setState({ formDirty: false })
  })
  afterEach(cleanup)

  it("asks to restore the pending restore's version, saying the branch carries on", () => {
    render(<RestoreConfirmModal onConfirm={vi.fn()} onClose={vi.fn()} />)
    const modal = screen.getByTestId("restore-confirm-modal")
    expect(modal).toHaveTextContent("Restore v1.0?")
    expect(modal).toHaveTextContent("Your branch carries on from this version.")
  })

  it("on a clean canvas, a single confirm restores without saving", () => {
    const onConfirm = vi.fn()
    render(<RestoreConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)
    expect(screen.queryByTestId("restore-dirty-warning")).toBeNull()
    fireEvent.click(screen.getByTestId("restore-confirm"))
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  it("when dirty, offers save-and-restore and discard-and-restore with a warning", () => {
    useGraphStore.setState({ dirty: true })
    const onConfirm = vi.fn()
    render(<RestoreConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)

    expect(screen.getByTestId("restore-dirty-warning")).toHaveTextContent("restoring replaces them")
    // The plain confirm is replaced by the two explicit choices.
    expect(screen.queryByTestId("restore-confirm")).toBeNull()
    expect(screen.getByTestId("restore-discard")).toHaveTextContent("Discard & restore")

    fireEvent.click(screen.getByTestId("restore-save"))
    expect(onConfirm).toHaveBeenCalledWith(true)
  })

  it("asks about unsaved edits in the workbench's form as it asks about the canvas", () => {
    useWorkbenchStore.setState({ formDirty: true })
    const onConfirm = vi.fn()
    render(<RestoreConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)

    expect(screen.getByTestId("restore-dirty-warning")).toBeInTheDocument()
    expect(screen.queryByTestId("restore-confirm")).toBeNull()
    fireEvent.click(screen.getByTestId("restore-save"))
    expect(onConfirm).toHaveBeenCalledWith(true)
  })

  it("when dirty, discard-and-restore restores without saving", () => {
    useGraphStore.setState({ dirty: true })
    const onConfirm = vi.fn()
    render(<RestoreConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId("restore-discard"))
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  it("Cancel closes without restoring", () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(<RestoreConfirmModal onConfirm={onConfirm} onClose={onClose} />)
    fireEvent.click(screen.getByText("Cancel"))
    expect(onClose).toHaveBeenCalledOnce()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("does not close from Escape or the backdrop while a restore is pending", () => {
    const onClose = vi.fn()
    render(<RestoreConfirmModal onConfirm={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByTestId("restore-confirm"))
    fireEvent.keyDown(document, { key: "Escape" })
    fireEvent.click(screen.getByTestId("restore-confirm-modal"))
    expect(onClose).not.toHaveBeenCalled()
  })

  it("renders nothing when no restore is pending", () => {
    useGitStore.setState({ restoreTarget: null })
    const { container } = render(<RestoreConfirmModal onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})
