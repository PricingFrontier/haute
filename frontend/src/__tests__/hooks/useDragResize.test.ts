/**
 * Tests for useDragResize hook.
 *
 * Tests: initial height, drag-to-resize via DOM mutation, mouseup commit,
 * height clamping (min / available column space), programmatic resize, cleanup on unmount.
 */
import { describe, it, expect, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { useDragResize } from "../../hooks/useDragResize"

afterEach(cleanup)

const defaultOpts = { initialHeight: 200, minHeight: 100 }

// ── Helpers ─────────────────────────────────────────────────────

function mockBox(element: HTMLElement, height: number, bottom = height) {
  element.getBoundingClientRect = () => ({
    bottom,
    height,
    left: 0,
    right: 1000,
    top: bottom - height,
    width: 1000,
    x: 0,
    y: bottom - height,
    toJSON: () => ({}),
  })
}

/**
 * Attach a panel element to the hook, docked at the bottom of a flex column shaped like the
 * editor: optional fixed banners, a flex-growing canvas, then the panel.
 */
function attachPanel(
  containerRef: React.RefObject<HTMLDivElement | null>,
  { columnHeight = 900, bannerHeights = [] as number[] } = {},
) {
  const column = document.createElement("main")
  mockBox(column, columnHeight)
  for (const bannerHeight of bannerHeights) {
    const banner = document.createElement("div")
    mockBox(banner, bannerHeight)
    column.appendChild(banner)
  }
  const canvas = document.createElement("div")
  canvas.style.flexGrow = "1"
  mockBox(canvas, 500)
  column.appendChild(canvas)
  const panel = document.createElement("div")
  column.appendChild(panel)
  Object.defineProperty(containerRef, "current", { value: panel, writable: true })
  return panel
}

/**
 * Simulate a full drag sequence: mousedown (via onDragStart), mousemove, mouseup.
 * clientY decreases = dragging upward = panel grows.
 */
function simulateDrag(
  onDragStart: (e: React.MouseEvent) => void,
  startY: number,
  moveY: number,
  endY: number,
) {
  act(() => {
    onDragStart({
      clientY: startY,
      preventDefault: () => {},
    } as React.MouseEvent)
  })

  act(() => {
    document.dispatchEvent(new MouseEvent("mousemove", { clientY: moveY }))
  })

  act(() => {
    document.dispatchEvent(new MouseEvent("mouseup", { clientY: endY }))
  })
}

// ── Tests ───────────────────────────────────────────────────────

describe("useDragResize", () => {
  it("returns initialHeight as the starting height", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    expect(result.current.height).toBe(200)
  })

  it("provides a containerRef (starts as null)", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    expect(result.current.containerRef.current).toBeNull()
  })

  it("mutates container style.height during mousemove (DOM-direct)", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    const panel = attachPanel(result.current.containerRef)

    // Start drag at clientY=400, move to clientY=350 (drag up by 50px)
    act(() => {
      result.current.onDragStart({
        clientY: 400,
        preventDefault: () => {},
      } as React.MouseEvent)
    })

    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: 350 }))
    })

    // newH = startH + (startY - moveY) = 200 + (400 - 350) = 250
    expect(panel.style.height).toBe("250px")

    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { clientY: 350 }))
    })
  })

  it("commits final height to React state on mouseup", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    attachPanel(result.current.containerRef)

    // Drag up by 80px: startY=400, endY=320 => newH = 200 + 80 = 280
    simulateDrag(result.current.onDragStart, 400, 360, 320)

    expect(result.current.height).toBe(280)
  })

  it("clamps height to minHeight when dragging down", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    attachPanel(result.current.containerRef)

    // Drag down by 300px: startY=400, endY=700 => newH = 200 + (400-700) = -100 => clamped to 100
    simulateDrag(result.current.onDragStart, 400, 700, 700)

    expect(result.current.height).toBe(100)
  })

  it("lets a drag reach the top of the column, with no fixed pixel cap", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    const panel = attachPanel(result.current.containerRef, { columnHeight: 1400 })

    act(() => {
      result.current.onDragStart({ clientY: 1300, preventDefault: () => {} } as React.MouseEvent)
    })
    // Dragging past the column top clamps the live DOM height to the whole column...
    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: -500 }))
    })
    expect(panel.style.height).toBe("1400px")

    // ...and the committed height matches it.
    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { clientY: -500 }))
    })
    expect(result.current.height).toBe(1400)
  })

  it("keeps room for column siblings that cannot shrink, such as banners", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    attachPanel(result.current.containerRef, { columnHeight: 900, bannerHeights: [28, 32] })

    simulateDrag(result.current.onDragStart, 800, 0, -200)

    expect(result.current.height).toBe(840)
  })

  it("does not mutate DOM when mousemove fires after mouseup", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    const panel = attachPanel(result.current.containerRef)

    simulateDrag(result.current.onDragStart, 400, 350, 350)
    const heightAfterDrag = panel.style.height

    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: 100 }))
    })

    expect(panel.style.height).toBe(heightAfterDrag)
  })

  it("clamps programmatic resizes to the same column ceiling as dragging", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    const panel = attachPanel(result.current.containerRef, { columnHeight: 900, bannerHeights: [30] })

    act(() => {
      result.current.resizeToHeight(Number.POSITIVE_INFINITY)
    })
    expect(result.current.height).toBe(870)
    expect(panel.style.height).toBe("870px")

    act(() => {
      result.current.resizeToHeight(300)
    })
    expect(result.current.height).toBe(300)
    expect(panel.style.height).toBe("300px")
  })

  it("falls back to the panel's bottom edge, then the viewport, when the column has no height", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))
    const panel = attachPanel(result.current.containerRef, { columnHeight: 0 })

    mockBox(panel, 200, 650)
    act(() => {
      result.current.resizeToHeight(Number.POSITIVE_INFINITY)
    })
    expect(result.current.height).toBe(650)

    mockBox(panel, 0, 0)
    act(() => {
      result.current.resizeToHeight(Number.POSITIVE_INFINITY)
    })
    expect(result.current.height).toBe(window.innerHeight)
  })

  it("cleans up event listeners on unmount without errors", () => {
    const { result, unmount } = renderHook(() => useDragResize(defaultOpts))
    attachPanel(result.current.containerRef)

    // Start a drag but don't release
    act(() => {
      result.current.onDragStart({
        clientY: 400,
        preventDefault: () => {},
      } as React.MouseEvent)
    })

    // Unmount while drag is in progress — should not throw
    expect(() => unmount()).not.toThrow()

    // Dispatching mouse events after unmount should not throw
    expect(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: 300 }))
      document.dispatchEvent(new MouseEvent("mouseup", { clientY: 300 }))
    }).not.toThrow()
  })
})
