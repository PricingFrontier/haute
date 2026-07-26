import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import MoveConfirmModal from "../MoveConfirmModal"
import useGitStore from "../../stores/useGitStore"
import useGraphStore from "../../stores/useGraphStore"

describe("MoveConfirmModal", () => {
  beforeEach(() => {
    useGitStore.setState({ moveTarget: { sha: "abc123", label: "v2.0" } })
    useGraphStore.setState({ dirty: false })
  })
  afterEach(cleanup)

  it("names the target version in the header", () => {
    render(<MoveConfirmModal onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByTestId("move-confirm-modal")).toHaveTextContent("v2.0")
  })

  it("on a clean canvas, a single confirm moves without saving", () => {
    const onConfirm = vi.fn()
    render(<MoveConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)
    expect(screen.queryByTestId("move-dirty-warning")).toBeNull()
    fireEvent.click(screen.getByTestId("move-confirm"))
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  it("when dirty, offers save-and-move and discard-and-move with a warning", () => {
    useGraphStore.setState({ dirty: true })
    const onConfirm = vi.fn()
    render(<MoveConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)

    expect(screen.getByTestId("move-dirty-warning")).toBeInTheDocument()
    // The plain confirm is replaced by the two explicit choices.
    expect(screen.queryByTestId("move-confirm")).toBeNull()

    fireEvent.click(screen.getByTestId("move-save"))
    expect(onConfirm).toHaveBeenCalledWith(true)
  })

  it("when dirty, discard-and-move moves without saving", () => {
    useGraphStore.setState({ dirty: true })
    const onConfirm = vi.fn()
    render(<MoveConfirmModal onConfirm={onConfirm} onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId("move-discard"))
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  it("Cancel closes without moving", () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(<MoveConfirmModal onConfirm={onConfirm} onClose={onClose} />)
    fireEvent.click(screen.getByText("Cancel"))
    expect(onClose).toHaveBeenCalledOnce()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("does not close from Escape or the backdrop while a move is pending", () => {
    const onClose = vi.fn()
    render(<MoveConfirmModal onConfirm={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByTestId("move-confirm"))
    fireEvent.keyDown(document, { key: "Escape" })
    fireEvent.click(screen.getByTestId("move-confirm-modal"))
    expect(onClose).not.toHaveBeenCalled()
  })

  it("renders nothing when no move is pending", () => {
    useGitStore.setState({ moveTarget: null })
    const { container } = render(<MoveConfirmModal onConfirm={vi.fn()} onClose={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})
