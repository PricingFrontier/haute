/**
 * Tests for useDragResize hook.
 *
 * Tests: initial height, drag-to-resize via DOM mutation, mouseup commit,
 * height clamping (min/max), cleanup on unmount.
 */
import { describe, it, expect, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import { useDragResize } from "../../hooks/useDragResize"

afterEach(cleanup)

const defaultOpts = { initialHeight: 200, minHeight: 100, maxHeight: 500 }

// ── Helpers ─────────────────────────────────────────────────────

/**
 * Simulate a full drag sequence: mousedown (via onDragStart), mousemove, mouseup.
 * clientY decreases = dragging upward = panel grows.
 */
function simulateDrag(
  onDragStart: (e: React.MouseEvent) => void,
  _container: HTMLDivElement,
  startY: number,
  moveY: number,
  endY: number,
) {
  // Start the drag
  act(() => {
    onDragStart({
      clientY: startY,
      preventDefault: () => {},
    } as React.MouseEvent)
  })

  // Move the mouse
  act(() => {
    document.dispatchEvent(new MouseEvent("mousemove", { clientY: moveY }))
  })

  // Release the mouse
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

    // Attach a real DOM element to the containerRef
    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

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

    // Container should have been mutated directly
    // newH = startH + (startY - moveY) = 200 + (400 - 350) = 250
    expect(div.style.height).toBe("250px")

    // Cleanup: release mouse
    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { clientY: 350 }))
    })
  })

  it("commits final height to React state on mouseup", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    // Drag up by 80px: startY=400, endY=320 => newH = 200 + 80 = 280
    simulateDrag(result.current.onDragStart, div, 400, 360, 320)

    expect(result.current.height).toBe(280)
  })

  it("clamps height to minHeight when dragging down", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    // Drag down by 300px: startY=400, endY=700 => newH = 200 + (400-700) = -100 => clamped to 100
    simulateDrag(result.current.onDragStart, div, 400, 700, 700)

    expect(result.current.height).toBe(100)
  })

  it("clamps height to maxHeight when dragging up too far", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    // Drag up by 500px: startY=500, endY=0 => newH = 200 + 500 = 700 => clamped to 500
    simulateDrag(result.current.onDragStart, div, 500, 0, 0)

    expect(result.current.height).toBe(500)
  })

  it("clamps DOM-direct mutation during mousemove (not just on commit)", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    act(() => {
      result.current.onDragStart({
        clientY: 500,
        preventDefault: () => {},
      } as React.MouseEvent)
    })

    // Move way up — should clamp at maxHeight (500)
    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: 0 }))
    })

    expect(div.style.height).toBe("500px")

    act(() => {
      document.dispatchEvent(new MouseEvent("mouseup", { clientY: 0 }))
    })
  })

  it("cleans up event listeners on unmount without errors", () => {
    const { result, unmount } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

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

  it("does not mutate DOM when mousemove fires after mouseup", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    // Complete a drag
    simulateDrag(result.current.onDragStart, div, 400, 350, 350)
    const heightAfterDrag = div.style.height

    // Additional mousemove after mouseup should not change anything
    act(() => {
      document.dispatchEvent(new MouseEvent("mousemove", { clientY: 100 }))
    })

    expect(div.style.height).toBe(heightAfterDrag)
  })

  it("supports programmatic resize for shared panel commands", () => {
    const { result } = renderHook(() => useDragResize(defaultOpts))

    const div = document.createElement("div")
    Object.defineProperty(result.current.containerRef, "current", {
      value: div,
      writable: true,
    })

    act(() => {
      result.current.resizeToHeight(650, { clampToMax: false })
    })

    expect(result.current.height).toBe(650)
    expect(div.style.height).toBe("650px")
  })
})
