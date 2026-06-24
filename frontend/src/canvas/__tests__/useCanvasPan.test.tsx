/**
 * DOM-level wiring test for useCanvasPan: the gesture rules themselves are
 * exhaustively covered by panController.test.ts — here we only check that the
 * hook translates real pointer events into viewport pans and menu opens, and
 * suppresses the native browser menu over canvas content.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, cleanup, fireEvent, waitFor } from "@testing-library/react"
import useCanvasPan, { type CanvasContextHit } from "../useCanvasPan"

const viewport = { x: 0, y: 0, zoom: 1 }
const setViewport = vi.fn()
const getViewport = vi.fn(() => viewport)

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ getViewport, setViewport }),
}))

// The hover passthrough hit-tests via topmostNodeAtPoint (document.elementsFromPoint,
// which jsdom doesn't implement); stub it to a known node id.
vi.mock("../../utils/dropResolver", () => ({
  topmostNodeAtPoint: () => "n1",
}))

function Harness({
  onContextMenu,
  onHoverNodeChange,
  withSelectionOverlay = true,
}: {
  onContextMenu: (hit: CanvasContextHit, x: number, y: number) => void
  onHoverNodeChange?: (id: string | null) => void
  withSelectionOverlay?: boolean
}) {
  const ref = useCanvasPan({ onContextMenu, onHoverNodeChange })
  return (
    <div ref={ref} data-testid="wrapper">
      <div className="react-flow__pane" data-testid="pane" style={{ width: 100, height: 100 }} />
      {/* Real React Flow tags node wrappers `.nopan`; the gesture must still
          fire on them (middle pan / right menu from a node). */}
      <div className="react-flow__node nopan selectable draggable" data-id="n1" data-testid="node1" />
      <div className="react-flow__edge" data-testid="edge1" />
      {/* The multi-selection drag overlay React Flow renders above selected
          nodes (rect carries pointer-events; container is the gesture target). */}
      {withSelectionOverlay && (
        <div className="react-flow__nodesselection">
          <div className="react-flow__nodesselection-rect" data-testid="selection-rect" />
        </div>
      )}
      <div data-testid="outside" />
    </div>
  )
}

const MIDDLE = 1
const RIGHT = 2

beforeEach(() => {
  setViewport.mockClear()
  getViewport.mockClear()
})
afterEach(cleanup)

describe("useCanvasPan", () => {
  it("middle-drag pans the viewport by the move delta, from a node", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    // Press starts on a NODE — middle button must still pan (pan from anywhere).
    fireEvent.pointerDown(getByTestId("node1"), { button: MIDDLE, clientX: 10, clientY: 10 })
    fireEvent.pointerMove(window, { clientX: 30, clientY: 25 })
    fireEvent.pointerUp(window, { button: MIDDLE })

    expect(setViewport).toHaveBeenCalledTimes(1)
    expect(setViewport).toHaveBeenCalledWith({ x: 20, y: 15, zoom: 1 })
    expect(onContextMenu).not.toHaveBeenCalled()
  })

  it("right-click without moving opens the node's context menu", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    fireEvent.pointerDown(getByTestId("node1"), { button: RIGHT, clientX: 40, clientY: 60 })
    fireEvent.pointerUp(window, { button: RIGHT })

    expect(onContextMenu).toHaveBeenCalledTimes(1)
    expect(onContextMenu).toHaveBeenCalledWith({ nodeId: "n1" }, 40, 60)
    expect(setViewport).not.toHaveBeenCalled()
  })

  it("right-drag past the threshold pans and does NOT open a menu", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    fireEvent.pointerDown(getByTestId("node1"), { button: RIGHT, clientX: 40, clientY: 60 })
    fireEvent.pointerMove(window, { clientX: 70, clientY: 60 }) // crosses threshold → anchors pan
    fireEvent.pointerMove(window, { clientX: 90, clientY: 65 }) // pans by (20, 5)
    fireEvent.pointerUp(window, { button: RIGHT })

    expect(setViewport).toHaveBeenCalledTimes(1)
    expect(setViewport).toHaveBeenCalledWith({ x: 20, y: 5, zoom: 1 })
    expect(onContextMenu).not.toHaveBeenCalled()
  })

  it("right-click on an edge opens with a null node id (no node menu, browser menu suppressed)", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    fireEvent.pointerDown(getByTestId("edge1"), { button: RIGHT, clientX: 5, clientY: 5 })
    fireEvent.pointerUp(window, { button: RIGHT })

    expect(onContextMenu).toHaveBeenCalledWith({ nodeId: null }, 5, 5)
  })

  it("right-click on the multi-selection overlay targets the whole selection", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    // The overlay sits above the selected nodes; a press there must resolve to
    // the selection (onSelection), not a null/per-node hit — otherwise no menu.
    fireEvent.pointerDown(getByTestId("selection-rect"), { button: RIGHT, clientX: 70, clientY: 80 })
    fireEvent.pointerUp(window, { button: RIGHT })

    expect(onContextMenu).toHaveBeenCalledWith({ nodeId: null, onSelection: true }, 70, 80)
    expect(setViewport).not.toHaveBeenCalled()
  })

  it("right-drag starting on the multi-selection overlay pans and opens no menu", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    fireEvent.pointerDown(getByTestId("selection-rect"), { button: RIGHT, clientX: 40, clientY: 60 })
    fireEvent.pointerMove(window, { clientX: 70, clientY: 60 }) // crosses threshold → anchors pan
    fireEvent.pointerMove(window, { clientX: 90, clientY: 65 }) // pans by (20, 5)
    fireEvent.pointerUp(window, { button: RIGHT })

    expect(setViewport).toHaveBeenCalled()
    expect(onContextMenu).not.toHaveBeenCalled()
  })

  it("held-still right press opens the menu after the debounce", () => {
    vi.useFakeTimers()
    try {
      const onContextMenu = vi.fn()
      const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
      fireEvent.pointerDown(getByTestId("node1"), { button: RIGHT, clientX: 12, clientY: 14 })
      expect(onContextMenu).not.toHaveBeenCalled()
      vi.advanceTimersByTime(200)
      expect(onContextMenu).toHaveBeenCalledWith({ nodeId: "n1" }, 12, 14)
    } finally {
      vi.useRealTimers()
    }
  })

  it("suppresses the native context menu over canvas content", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    const evt = new MouseEvent("contextmenu", { bubbles: true, cancelable: true })
    getByTestId("node1").dispatchEvent(evt)
    expect(evt.defaultPrevented).toBe(true)
  })

  it("suppresses the native context menu over the multi-selection overlay", () => {
    const { getByTestId } = render(<Harness onContextMenu={vi.fn()} />)
    const evt = new MouseEvent("contextmenu", { bubbles: true, cancelable: true })
    getByTestId("selection-rect").dispatchEvent(evt)
    expect(evt.defaultPrevented).toBe(true)
  })

  it("ignores presses that are not over canvas content", () => {
    const onContextMenu = vi.fn()
    const { getByTestId } = render(<Harness onContextMenu={onContextMenu} />)
    fireEvent.pointerDown(getByTestId("outside"), { button: RIGHT, clientX: 1, clientY: 1 })
    fireEvent.pointerUp(window, { button: RIGHT })
    expect(onContextMenu).not.toHaveBeenCalled()
    expect(setViewport).not.toHaveBeenCalled()
  })

  it("reports the node under the cursor through the selection overlay (hover passthrough)", async () => {
    const onHoverNodeChange = vi.fn()
    const { getByTestId } = render(
      <Harness onContextMenu={vi.fn()} onHoverNodeChange={onHoverNodeChange} />,
    )
    fireEvent.pointerMove(getByTestId("selection-rect"), { clientX: 70, clientY: 80 })
    await waitFor(() => expect(onHoverNodeChange).toHaveBeenCalledWith("n1"))
  })

  it("clears hover on pointer leave", async () => {
    const onHoverNodeChange = vi.fn()
    const { getByTestId } = render(
      <Harness onContextMenu={vi.fn()} onHoverNodeChange={onHoverNodeChange} />,
    )
    fireEvent.pointerMove(getByTestId("selection-rect"), { clientX: 70, clientY: 80 })
    await waitFor(() => expect(onHoverNodeChange).toHaveBeenCalledWith("n1"))
    fireEvent.pointerLeave(getByTestId("wrapper"))
    expect(onHoverNodeChange).toHaveBeenLastCalledWith(null)
  })

  it("does not hit-test for hover when there is no multi-selection overlay", async () => {
    const onHoverNodeChange = vi.fn()
    const { getByTestId } = render(
      <Harness
        onContextMenu={vi.fn()}
        onHoverNodeChange={onHoverNodeChange}
        withSelectionOverlay={false}
      />,
    )
    fireEvent.pointerMove(getByTestId("node1"), { clientX: 40, clientY: 60 })
    await new Promise((r) => setTimeout(r, 0))
    expect(onHoverNodeChange).not.toHaveBeenCalled()
  })
})
