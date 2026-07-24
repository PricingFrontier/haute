import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import PreviewPanelFrame from "../PreviewPanelFrame"
import { DEFAULT_PREVIEW_PANEL_DIMENSIONS, PREVIEW_PANEL_HEADER_HEIGHT_CLASS } from "../previewPanelLayout"

const { mockResizeToHeight, mockUseDragResize } = vi.hoisted(() => ({
  mockResizeToHeight: vi.fn(),
  mockUseDragResize: vi.fn(),
}))

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: (opts: { initialHeight: number; minHeight: number; maxHeight: number }) => mockUseDragResize(opts),
}))

describe("PreviewPanelFrame", () => {
  afterEach(cleanup)

  beforeEach(() => {
    mockResizeToHeight.mockReset()
    mockUseDragResize.mockReset()
    mockUseDragResize.mockImplementation((opts: { initialHeight: number }) => ({
      height: opts.initialHeight,
      containerRef: { current: null },
      onDragStart: vi.fn(),
      resizeToHeight: mockResizeToHeight,
    }))
  })

  it("centralises default preview panel sizing and node icon rendering", () => {
    render(
      <PreviewPanelFrame nodeLabel="Claims source" nodeType="dataInput">
        <div>Preview body</div>
      </PreviewPanelFrame>,
    )

    expect(mockUseDragResize).toHaveBeenCalledWith(DEFAULT_PREVIEW_PANEL_DIMENSIONS)
    expect(screen.getByTestId("preview-panel-frame-header")).toHaveClass(PREVIEW_PANEL_HEADER_HEIGHT_CLASS)
    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-database")).toBeTruthy()
    expect(screen.getByText("Preview body")).toBeInTheDocument()
  })

  it("keeps the expand control on the right when collapsed", () => {
    render(
      <PreviewPanelFrame
        nodeLabel="Claims source"
        nodeType="dataInput"
        collapsedMeta="100 rows"
        data-testid="preview-panel-frame"
      >
        <div>Preview body</div>
      </PreviewPanelFrame>,
    )

    fireEvent.click(within(screen.getByTestId("preview-panel-frame")).getByLabelText("Collapse preview panel"))

    const expandButton = screen.getByLabelText("Expand preview panel")
    expect(expandButton).toHaveClass("ml-auto")
    expect(expandButton.nextElementSibling).toBe(screen.getByLabelText("Expand preview panel to top"))
    expect(screen.getByText("Claims source")).toBeInTheDocument()
    expect(screen.getByText("100 rows")).toBeInTheDocument()
  })

  it("places the top-expand command to the right of the collapse control and can restore height", () => {
    render(
      <PreviewPanelFrame nodeLabel="Claims source" nodeType="dataInput" data-testid="preview-panel-frame">
        <div>Preview body</div>
      </PreviewPanelFrame>,
    )

    const frame = screen.getByTestId("preview-panel-frame")
    const header = screen.getByTestId("preview-panel-frame-header")
    vi.spyOn(frame.parentElement as HTMLElement, "getBoundingClientRect").mockReturnValue({
      bottom: 720,
      height: 720,
      left: 0,
      right: 1000,
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })

    const headerButtons = within(header).getAllByRole("button")
    expect(headerButtons[headerButtons.length - 2]).toHaveAttribute("aria-label", "Collapse preview panel")
    expect(headerButtons[headerButtons.length - 1]).toHaveAttribute("aria-label", "Expand preview panel to top")
    expect(headerButtons[headerButtons.length - 2]).not.toHaveAttribute("title")
    expect(headerButtons[headerButtons.length - 1]).not.toHaveAttribute("title")

    fireEvent.click(screen.getByLabelText("Expand preview panel to top"))
    expect(mockResizeToHeight).toHaveBeenCalledWith(720, { clampToMax: false })
    expect(screen.getByLabelText("Restore preview panel height").querySelector(".lucide-chevron-down")).toBeTruthy()
    expect(screen.getByLabelText("Collapse preview panel").querySelector(".lucide-chevrons-down")).toBeTruthy()

    fireEvent.click(screen.getByLabelText("Restore preview panel height"))
    expect(mockResizeToHeight).toHaveBeenLastCalledWith(DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight, { clampToMax: false })
  })

  it("collapsing from full height resets the collapsed full-open control instead of showing restore", () => {
    render(
      <PreviewPanelFrame nodeLabel="Claims source" nodeType="dataInput" data-testid="preview-panel-frame">
        <div>Preview body</div>
      </PreviewPanelFrame>,
    )

    const frame = screen.getByTestId("preview-panel-frame")
    vi.spyOn(frame.parentElement as HTMLElement, "getBoundingClientRect").mockReturnValue({
      bottom: 720,
      height: 720,
      left: 0,
      right: 1000,
      top: 0,
      width: 1000,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })

    fireEvent.click(screen.getByLabelText("Expand preview panel to top"))
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))

    expect(screen.queryByLabelText("Restore preview panel height")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Expand preview panel to top").querySelector(".lucide-chevrons-up")).toBeTruthy()
    expect(mockResizeToHeight).toHaveBeenLastCalledWith(DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight, { clampToMax: false })
  })
})
