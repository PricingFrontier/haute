import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Wrench } from "lucide-react"
import { SaveCommit, ToolbarButton, ToolbarLink, ToolbarStatus, UndoRedo, ZoomInOut } from ".."

// The editor's toolbar exercises these through Toolbar.test.tsx; this covers what
// only an extension uses: Save without Commit, and ToolbarButton for its own controls,
// with the link and the status drawn in a toolbar button's shape.
describe("haute-ui toolbar controls", () => {
  afterEach(cleanup)

  it("renders Save alone without onCommit", () => {
    const onSave = vi.fn()
    render(<SaveCommit onSave={onSave} />)

    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(onSave).toHaveBeenCalledOnce()
    expect(screen.queryByRole("button", { name: "Commit" })).toBeNull()
  })

  it("puts Commit beside Save given onCommit, and disables both when disabled", () => {
    const onCommit = vi.fn()
    const { rerender } = render(<SaveCommit onSave={vi.fn()} onCommit={onCommit} />)

    fireEvent.click(screen.getByRole("button", { name: "Commit" }))
    expect(onCommit).toHaveBeenCalledOnce()

    rerender(<SaveCommit onSave={vi.fn()} onCommit={onCommit} disabled />)
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled()
  })

  it("passes a toolbar button's props through and hides its icon from assistive technology", () => {
    const onClick = vi.fn()
    render(
      <ToolbarButton icon={Wrench} aria-pressed title="Edit the form" className="ml-auto" onClick={onClick}>
        Build
      </ToolbarButton>,
    )
    const button = screen.getByRole("button", { name: "Build", pressed: true })

    expect(button).toHaveAttribute("type", "button")
    expect(button).toHaveAttribute("title", "Edit the form")
    expect(button).toHaveClass("toolbar-btn", "toolbar-action", "ml-auto")
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true")
    fireEvent.click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("draws a link as a toolbar button, its layout classes after the kit's", () => {
    render(
      <ToolbarLink icon={Wrench} compact href="/quotes" title="Back to Quotes" className="w-auto">
        Quotes
      </ToolbarLink>,
    )
    const link = screen.getByRole("link", { name: "Quotes" })

    expect(link).toHaveAttribute("href", "/quotes")
    expect(link).toHaveAttribute("title", "Back to Quotes")
    expect(link.className).toBe("toolbar-btn toolbar-action toolbar-action-compact w-auto")
    expect(link.querySelector("svg")).toHaveAttribute("aria-hidden", "true")
  })

  it("draws a status in a toolbar button's shape as a label, not a control", () => {
    render(
      <ToolbarStatus data-testid="status" title="No git executable is available." className="min-w-[136px]">
        Git unavailable
      </ToolbarStatus>,
    )
    const status = screen.getByTestId("status")

    expect(status.tagName).toBe("SPAN")
    expect(status.className).toBe("toolbar-btn toolbar-action min-w-[136px]")
    expect(status).toHaveAttribute("title", "No git executable is available.")
    expect(status).toHaveTextContent("Git unavailable")
    expect(screen.queryByRole("button")).toBeNull()
  })

  it("calls the undo, redo and zoom handlers, with undo and redo off when there is nothing to do", () => {
    const handlers = { onUndo: vi.fn(), onRedo: vi.fn(), onZoomIn: vi.fn(), onZoomOut: vi.fn() }
    render(
      <>
        <UndoRedo canUndo canRedo={false} onUndo={handlers.onUndo} onRedo={handlers.onRedo} />
        <ZoomInOut onZoomIn={handlers.onZoomIn} onZoomOut={handlers.onZoomOut} />
      </>,
    )

    fireEvent.click(screen.getByRole("button", { name: "Undo" }))
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }))
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }))
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled()
    expect(handlers.onUndo).toHaveBeenCalledOnce()
    expect(handlers.onZoomIn).toHaveBeenCalledOnce()
    expect(handlers.onZoomOut).toHaveBeenCalledOnce()
    expect(handlers.onRedo).not.toHaveBeenCalled()
  })
})
