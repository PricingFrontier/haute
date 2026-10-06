import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import PreviewPanelFrame from "../PreviewPanelFrame"
import { PREVIEW_PANEL_DIMENSIONS, PREVIEW_PANEL_HEADER_HEIGHT_CLASS } from "../previewPanelLayout"
import { PreviewRunContext } from "../previewRunContext"

const COLUMN_HEIGHT = 900
const BANNER_HEIGHT = 30

function boxOfHeight(height: number): () => DOMRect {
  return () => ({
    bottom: height,
    height,
    left: 0,
    right: 1000,
    top: 0,
    width: 1000,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })
}

/** Render the frame docked under a banner and a flex-growing canvas, like the editor column. */
function renderInEditorColumn({ onRefresh, refreshTitle }: { onRefresh?: () => void; refreshTitle?: string } = {}) {
  render(
    <main data-testid="editor-column">
      <div data-testid="banner" />
      <div style={{ flexGrow: 1 }} />
      <PreviewPanelFrame
        nodeLabel="Claims source"
        nodeType="dataInput"
        collapsedMeta="100 rows"
        onRefresh={onRefresh}
        refreshTitle={refreshTitle}
        data-testid="preview-panel-frame"
      >
        <div>Preview body</div>
      </PreviewPanelFrame>
    </main>,
  )
  screen.getByTestId("editor-column").getBoundingClientRect = boxOfHeight(COLUMN_HEIGHT)
  screen.getByTestId("banner").getBoundingClientRect = boxOfHeight(BANNER_HEIGHT)
}

const frameHeight = () => screen.getByTestId("preview-panel-frame").style.height

function dragHandleBy(pixelsUp: number) {
  const handle = screen.getByTestId("preview-panel-frame").firstElementChild as HTMLElement
  fireEvent.mouseDown(handle, { clientY: 800 })
  fireEvent.mouseMove(document, { clientY: 800 - pixelsUp })
  fireEvent.mouseUp(document, { clientY: 800 - pixelsUp })
}

describe("PreviewPanelFrame", () => {
  afterEach(cleanup)

  it("centralises preview panel sizing and node icon rendering", () => {
    renderInEditorColumn()

    expect(frameHeight()).toBe(`${PREVIEW_PANEL_DIMENSIONS.initialHeight}px`)
    expect(screen.getByTestId("preview-panel-frame-header")).toHaveClass(PREVIEW_PANEL_HEADER_HEIGHT_CLASS)
    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-database")).toBeTruthy()
    expect(screen.getByText("Preview body")).toBeInTheDocument()
  })

  it("keeps the expand control on the right when collapsed", () => {
    renderInEditorColumn()

    fireEvent.click(within(screen.getByTestId("preview-panel-frame")).getByLabelText("Collapse preview panel"))

    const expandButton = screen.getByLabelText("Expand preview panel")
    expect(expandButton.parentElement).toHaveClass("ml-auto")
    expect(expandButton.nextElementSibling).toBe(screen.getByLabelText("Expand preview panel to top"))
    expect(screen.getByText("Claims source")).toBeInTheDocument()
    expect(screen.getByText("100 rows")).toBeInTheDocument()
  })

  it("renders Refresh only when supplied, before the panel controls, and invokes it", () => {
    renderInEditorColumn()
    expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument()

    cleanup()
    const onRefresh = vi.fn()
    renderInEditorColumn({ onRefresh, refreshTitle: "Reload claims" })

    const headerButtons = within(screen.getByTestId("preview-panel-frame-header")).getAllByRole("button")
    const refreshButton = screen.getByRole("button", { name: "Refresh" })
    expect(refreshButton).toHaveAttribute("title", "Reload claims")
    expect(headerButtons.at(-3)).toBe(refreshButton)
    fireEvent.click(refreshButton)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it("keeps Refresh clickable in collapsed and full-height states", () => {
    const onRefresh = vi.fn()
    renderInEditorColumn({ onRefresh })

    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(onRefresh).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByLabelText("Expand preview panel"))
    fireEvent.click(screen.getByLabelText("Expand preview panel to top"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })

  it("places the top-expand command to the right of the collapse control and can restore height", () => {
    renderInEditorColumn()

    const headerButtons = within(screen.getByTestId("preview-panel-frame-header")).getAllByRole("button")
    expect(headerButtons[headerButtons.length - 2]).toHaveAttribute("aria-label", "Collapse preview panel")
    expect(headerButtons[headerButtons.length - 1]).toHaveAttribute("aria-label", "Expand preview panel to top")
    expect(headerButtons[headerButtons.length - 2]).not.toHaveAttribute("title")
    expect(headerButtons[headerButtons.length - 1]).not.toHaveAttribute("title")

    fireEvent.click(screen.getByLabelText("Expand preview panel to top"))
    expect(frameHeight()).toBe(`${COLUMN_HEIGHT - BANNER_HEIGHT}px`)
    expect(screen.getByLabelText("Restore preview panel height").querySelector(".lucide-chevron-down")).toBeTruthy()
    expect(screen.getByLabelText("Collapse preview panel").querySelector(".lucide-chevrons-down")).toBeTruthy()

    fireEvent.click(screen.getByLabelText("Restore preview panel height"))
    expect(frameHeight()).toBe(`${PREVIEW_PANEL_DIMENSIONS.initialHeight}px`)
  })

  it("lets a drag reach the same top edge as the expand command", () => {
    renderInEditorColumn()

    dragHandleBy(5000)

    expect(frameHeight()).toBe(`${COLUMN_HEIGHT - BANNER_HEIGHT}px`)
  })

  it("collapsing from full height resets the collapsed full-open control instead of showing restore", () => {
    renderInEditorColumn()

    fireEvent.click(screen.getByLabelText("Expand preview panel to top"))
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))

    expect(screen.queryByLabelText("Restore preview panel height")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Expand preview panel to top").querySelector(".lucide-chevrons-up")).toBeTruthy()

    fireEvent.click(screen.getByLabelText("Expand preview panel"))
    expect(frameHeight()).toBe(`${PREVIEW_PANEL_DIMENSIONS.initialHeight}px`)
  })

  it("expands to the top from the collapsed bar and restores the height dragged before collapsing", () => {
    renderInEditorColumn()

    dragHandleBy(100)
    const draggedHeight = `${PREVIEW_PANEL_DIMENSIONS.initialHeight + 100}px`
    expect(frameHeight()).toBe(draggedHeight)

    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    fireEvent.click(within(screen.getByTestId("preview-panel-frame-collapsed")).getByLabelText("Expand preview panel to top"))
    expect(frameHeight()).toBe(`${COLUMN_HEIGHT - BANNER_HEIGHT}px`)

    fireEvent.click(screen.getByLabelText("Restore preview panel height"))
    expect(frameHeight()).toBe(draggedHeight)
  })

  it("reads Stop while the node's work runs, and stops it instead of refreshing", () => {
    const onRefresh = vi.fn()
    const onStop = vi.fn()
    const frame = (running: boolean) => (
      <PreviewRunContext.Provider value={{ running, onStop }}>
        <PreviewPanelFrame nodeLabel="Claims" onRefresh={onRefresh}>
          <div />
        </PreviewPanelFrame>
      </PreviewRunContext.Provider>
    )
    const { rerender } = render(frame(true))

    expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Stop" }))
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(onRefresh).not.toHaveBeenCalled()

    rerender(frame(false))
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it("offers no Stop on a frame without Refresh, even while work runs", () => {
    render(
      <PreviewRunContext.Provider value={{ running: true, onStop: vi.fn() }}>
        <PreviewPanelFrame nodeLabel="Claims">
          <div />
        </PreviewPanelFrame>
      </PreviewRunContext.Provider>,
    )
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument()
  })
})
