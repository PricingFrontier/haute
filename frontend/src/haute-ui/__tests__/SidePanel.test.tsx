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
    fireEvent.mouseMove(window, { clientX: 100 }) // 360 + 300 is past the maximum
    expect(onWidthChange).not.toHaveBeenCalled()
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenCalledWith(500)

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 700 }) // 360 - 300 is under the minimum
    fireEvent.mouseUp(window)
    expect(onWidthChange).toHaveBeenLastCalledWith(320)
  })
})
