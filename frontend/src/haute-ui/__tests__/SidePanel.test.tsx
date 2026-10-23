import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { SidePanel } from ".."

describe("haute-ui side panel", () => {
  afterEach(cleanup)

  it("shows its header, and closes from it", () => {
    const onClose = vi.fn()
    render(
      <SidePanel width={360} onWidthChange={vi.fn()} minWidth={320} maxWidth={() => 600} title="Table input" onClose={onClose}>
        <p>Fields</p>
      </SidePanel>,
    )

    expect(screen.getByText("Table input")).toBeInTheDocument()
    expect(screen.getByText("Fields")).toBeInTheDocument()
    fireEvent.click(screen.getByTitle("Close"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("tells the host its dragged width once the drag ends, within its limits", () => {
    const onWidthChange = vi.fn()
    render(
      <SidePanel width={360} onWidthChange={onWidthChange} minWidth={320} maxWidth={() => 500}>
        <p>Fields</p>
      </SidePanel>,
    )
    const handle = screen.getByTestId("panel-resize-handle")

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 100, buttons: 1 }) // 360 + 300 is past the maximum
    expect(onWidthChange).not.toHaveBeenCalled()
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenCalledWith(500)

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 700, buttons: 1 }) // 360 - 300 is under the minimum
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenLastCalledWith(320)
  })

  it("opens on the left with its handle on its right edge, widening as that edge is dragged right", () => {
    const onWidthChange = vi.fn()
    render(
      <SidePanel side="left" width={360} onWidthChange={onWidthChange} minWidth={320} maxWidth={() => 500} testId="panel">
        <p>Fields</p>
      </SidePanel>,
    )
    const panel = screen.getByTestId("panel")
    const handle = screen.getByTestId("panel-resize-handle")
    expect(panel).toHaveClass("side-panel", "left")
    expect(panel.lastElementChild).toBe(handle)

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 460, buttons: 1 })
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenLastCalledWith(420)

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 100, buttons: 1 }) // 360 - 300 is under the minimum
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenLastCalledWith(320)
  })

  it("ends a resize however it ends: released outside the window, the window losing the focus, or the panel going", () => {
    const onWidthChange = vi.fn()
    const { unmount } = render(
      <SidePanel width={360} onWidthChange={onWidthChange} minWidth={320} maxWidth={() => 500}>
        <p>Fields</p>
      </SidePanel>,
    )
    const handle = screen.getByTestId("panel-resize-handle")

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 350, buttons: 1 })
    // Released outside the window: the next move has no button down.
    fireEvent.mouseMove(window, { clientX: 100, buttons: 0 })
    expect(onWidthChange).toHaveBeenLastCalledWith(410)
    expect(document.body.style.cursor).toBe("")

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent(window, new Event("blur"))
    expect(onWidthChange).toHaveBeenCalledTimes(2)
    expect(document.body.style.userSelect).toBe("")

    fireEvent.mouseDown(handle, { clientX: 400 })
    expect(document.body.style.cursor).toBe("col-resize")
    unmount()
    expect(document.body.style.cursor).toBe("")
    expect(document.body.style.userSelect).toBe("")
  })
})
