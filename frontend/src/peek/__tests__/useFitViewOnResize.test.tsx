/**
 * useFitViewOnResize — the wrapper Peek's "dynamic rescale": resizing the panel
 * refits the inner flow so the whole submodel stays framed. jsdom can't measure
 * a real React Flow pane, so we drive a controllable ResizeObserver and a spy
 * fitView and assert the wiring (regression guard for the behaviour Nick liked).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { useRef } from "react"
import { render, cleanup, act } from "@testing-library/react"
import { useFitViewOnResize } from "../useFitViewOnResize"

let roCallbacks: ResizeObserverCallback[] = []
let observed: Element[] = []
let disconnects = 0

// Controllable ResizeObserver: captures its callback so a test can fire it.
class ControllableResizeObserver {
  constructor(cb: ResizeObserverCallback) {
    roCallbacks.push(cb)
  }
  observe(el: Element) {
    observed.push(el)
  }
  unobserve() {}
  disconnect() {
    disconnects += 1
  }
}

function Harness({
  fitView,
  padding = 0.15,
}: {
  fitView: (opts?: { padding?: number }) => void
  padding?: number
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  useFitViewOnResize(ref, fitView, padding)
  return <div ref={ref} data-testid="box" />
}

describe("useFitViewOnResize", () => {
  beforeEach(() => {
    roCallbacks = []
    observed = []
    disconnects = 0
    vi.stubGlobal("ResizeObserver", ControllableResizeObserver)
    // Run rAF synchronously so the refit is observable without flushing frames.
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
    vi.stubGlobal("cancelAnimationFrame", () => {})
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("observes the container and refits when it resizes", () => {
    const fitView = vi.fn()
    render(<Harness fitView={fitView} padding={0.2} />)
    expect(observed).toHaveLength(1)
    // No refit until a resize is reported.
    expect(fitView).not.toHaveBeenCalled()

    act(() => roCallbacks[0]([], {} as ResizeObserver))
    expect(fitView).toHaveBeenCalledWith({ padding: 0.2 })
  })

  it("refits on every subsequent resize (dynamic rescale)", () => {
    const fitView = vi.fn()
    render(<Harness fitView={fitView} />)
    act(() => roCallbacks[0]([], {} as ResizeObserver))
    act(() => roCallbacks[0]([], {} as ResizeObserver))
    expect(fitView).toHaveBeenCalledTimes(2)
  })

  it("disconnects the observer on unmount", () => {
    const { unmount } = render(<Harness fitView={vi.fn()} />)
    unmount()
    expect(disconnects).toBe(1)
  })
})
