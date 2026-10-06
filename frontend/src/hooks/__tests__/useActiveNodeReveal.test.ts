/**
 * Tests for useActiveNodeReveal: arming on active-node changes, waiting for measurement,
 * immediate compensation of later canvas resizes, glide refinement, user-gesture disarming,
 * and node-search centring. The React Flow store is a vanilla zustand store shaped like the
 * fields the hook reads.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import { createStore, type StoreApi } from "zustand/vanilla"
import type { Viewport } from "@xyflow/react"

import { NODE_REVEAL_DURATION_MS, useActiveNodeReveal } from "../useActiveNodeReveal"
import { NODE_REVEAL_MARGIN_PX } from "../../utils/nodeReveal"

type FakeInternalNode = {
  internals: { positionAbsolute: { x: number; y: number } }
  measured: { width?: number; height?: number }
}

type FakeFlowState = {
  domNode: HTMLDivElement | null
  nodeLookup: Map<string, FakeInternalNode>
  transform: [number, number, number]
  width: number
  height: number
}

type SetViewport = (
  viewport: Viewport,
  options?: { duration?: number; interpolate?: "smooth" | "linear" },
) => Promise<boolean>

const flow = vi.hoisted(() => ({
  store: null as unknown as StoreApi<FakeFlowState>,
  helpers: { setViewport: null as unknown as SetViewport },
}))

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return {
    ...actual,
    useStoreApi: () => flow.store,
    useReactFlow: () => flow.helpers,
  }
})

const canvasSize = { width: 800, height: 600 }
let now = 1_000
let setViewport: Mock<SetViewport>

function mountCanvas(): HTMLDivElement {
  const canvas = document.createElement("div")
  Object.defineProperty(canvas, "clientWidth", { configurable: true, get: () => canvasSize.width })
  Object.defineProperty(canvas, "clientHeight", { configurable: true, get: () => canvasSize.height })
  document.body.appendChild(canvas)
  return canvas
}

function setNode(id: string, rect: { x: number; y: number; width: number; height: number }) {
  const { nodeLookup } = flow.store.getState()
  nodeLookup.set(id, {
    internals: { positionAbsolute: { x: rect.x, y: rect.y } },
    measured: { width: rect.width, height: rect.height },
  })
  flow.store.setState({ nodeLookup })
}

function resizeCanvas(width: number, height: number) {
  canvasSize.width = width
  canvasSize.height = height
  flow.store.setState({ width, height })
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve()
  })
}

function renderReveal(initialId: string | null = null) {
  return renderHook(({ id }: { id: string | null }) => useActiveNodeReveal(id), {
    initialProps: { id: initialId },
  })
}

beforeEach(() => {
  canvasSize.width = 800
  canvasSize.height = 600
  now = 1_000
  vi.spyOn(performance, "now").mockImplementation(() => now)
  flow.store = createStore<FakeFlowState>(() => ({
    domNode: mountCanvas(),
    nodeLookup: new Map(),
    transform: [0, 0, 1],
    width: canvasSize.width,
    height: canvasSize.height,
  }))
  // An immediate move lands at once; a glide leaves the store transform where it started.
  setViewport = vi.fn<SetViewport>((viewport, options) => {
    if (!options?.duration) flow.store.setState({ transform: [viewport.x, viewport.y, viewport.zoom] })
    return Promise.resolve(true)
  })
  flow.helpers.setViewport = setViewport
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

describe("useActiveNodeReveal", () => {
  it("glides a node the narrowed canvas clips into view when it becomes active", () => {
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()

    rerender({ id: "a" })

    expect(setViewport).toHaveBeenCalledOnce()
    expect(setViewport).toHaveBeenCalledWith(
      { x: 800 - NODE_REVEAL_MARGIN_PX - 900, y: 0, zoom: 1 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "linear" },
    )
  })

  it("leaves the view alone when the active node is already fully visible", () => {
    setNode("a", { x: 100, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()

    rerender({ id: "a" })

    expect(setViewport).not.toHaveBeenCalled()
  })

  it("makes no move while the canvas is not mounted", () => {
    flow.store.getState().domNode?.remove()
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()

    rerender({ id: "a" })

    expect(setViewport).not.toHaveBeenCalled()
  })

  it("waits for a just-created node to be measured, then glides it outside the store listener", async () => {
    const { rerender } = renderReveal()
    rerender({ id: "dropped" })
    expect(setViewport).not.toHaveBeenCalled()

    act(() => setNode("dropped", { x: 750, y: 580, width: 200, height: 80 }))
    expect(setViewport).not.toHaveBeenCalled()
    await flushMicrotasks()

    expect(setViewport).toHaveBeenCalledOnce()
    expect(setViewport).toHaveBeenCalledWith(
      { x: 800 - NODE_REVEAL_MARGIN_PX - 950, y: 600 - NODE_REVEAL_MARGIN_PX - 660, zoom: 1 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "linear" },
    )
  })

  it("compensates a later canvas resize in the same frame while the node stays active", async () => {
    setNode("a", { x: 500, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()
    rerender({ id: "a" })
    expect(setViewport).not.toHaveBeenCalled()

    act(() => resizeCanvas(600, 600))
    await flushMicrotasks()

    expect(setViewport.mock.calls).toEqual([[{ x: 600 - NODE_REVEAL_MARGIN_PX - 700, y: 0, zoom: 1 }]])
  })

  it("refines a glide still in progress from its target instead of truncating it", async () => {
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()
    rerender({ id: "a" })
    const glideTargetX = 800 - NODE_REVEAL_MARGIN_PX - 900

    now += NODE_REVEAL_DURATION_MS / 2
    act(() => resizeCanvas(700, 600))
    await flushMicrotasks()

    // At the glide target the node spans 560..760, still past the 700px canvas.
    expect(setViewport).toHaveBeenLastCalledWith(
      { x: glideTargetX - (760 - (700 - NODE_REVEAL_MARGIN_PX)), y: 0, zoom: 1 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "linear" },
    )
  })

  it("does not interrupt a glide whose target already fits a canvas resized mid-glide", async () => {
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()
    rerender({ id: "a" })

    now += NODE_REVEAL_DURATION_MS / 2
    // The node still spans 700..900 at the glide's start but 560..760 at its target.
    act(() => resizeCanvas(780, 600))
    await flushMicrotasks()

    expect(setViewport).toHaveBeenCalledOnce()
  })

  it("compensates immediately again once a glide has finished", async () => {
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()
    rerender({ id: "a" })
    const glideTargetX = 800 - NODE_REVEAL_MARGIN_PX - 900
    act(() => flow.store.setState({ transform: [glideTargetX, 0, 1] }))

    now += NODE_REVEAL_DURATION_MS
    act(() => resizeCanvas(700, 600))
    await flushMicrotasks()

    expect(setViewport).toHaveBeenLastCalledWith({
      x: glideTargetX - (760 - (700 - NODE_REVEAL_MARGIN_PX)),
      y: 0,
      zoom: 1,
    })
  })

  it("places the next active node while an interrupted glide's promise never settles", () => {
    // React Flow resolves setViewport on the transition's end event, which an interruption skips.
    setViewport.mockImplementation(() => new Promise<boolean>(() => {}))
    setNode("a", { x: 700, y: 100, width: 200, height: 80 })
    setNode("b", { x: 100, y: 560, width: 200, height: 80 })
    const { result, rerender } = renderReveal()
    rerender({ id: "a" })
    result.current.handleMoveStart(new WheelEvent("wheel"), { x: 0, y: 0, zoom: 1 })

    rerender({ id: "b" })

    expect(setViewport).toHaveBeenCalledTimes(2)
    expect(setViewport).toHaveBeenLastCalledWith(
      { x: 0, y: 600 - NODE_REVEAL_MARGIN_PX - 640, zoom: 1 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "linear" },
    )
  })

  it("stops following the node once the user pans or zooms", async () => {
    setNode("a", { x: 500, y: 100, width: 200, height: 80 })
    const { result, rerender } = renderReveal()
    rerender({ id: "a" })

    result.current.handleMoveStart(new WheelEvent("wheel"), { x: 0, y: 0, zoom: 1 })
    act(() => resizeCanvas(600, 600))
    await flushMicrotasks()

    expect(setViewport).not.toHaveBeenCalled()
  })

  it("keeps following the node through programmatic viewport moves", async () => {
    setNode("a", { x: 500, y: 100, width: 200, height: 80 })
    const { result, rerender } = renderReveal()
    rerender({ id: "a" })

    result.current.handleMoveStart(null, { x: 0, y: 0, zoom: 1 })
    act(() => resizeCanvas(600, 600))
    await flushMicrotasks()

    expect(setViewport).toHaveBeenCalledOnce()
  })

  it("stops following when no node is active", async () => {
    setNode("a", { x: 500, y: 100, width: 200, height: 80 })
    const { rerender } = renderReveal()
    rerender({ id: "a" })

    rerender({ id: null })
    act(() => resizeCanvas(600, 600))
    await flushMicrotasks()

    expect(setViewport).not.toHaveBeenCalled()
  })

  it("centres a node chosen in node search at the requested zoom, once", () => {
    setNode("a", { x: 100, y: 100, width: 200, height: 80 })
    const { result, rerender } = renderReveal()

    act(() => {
      result.current.centreNode("a", 0.8)
      rerender({ id: "a" })
    })

    expect(setViewport).toHaveBeenCalledOnce()
    expect(setViewport).toHaveBeenCalledWith(
      { x: 400 - 200 * 0.8, y: 300 - 140 * 0.8, zoom: 0.8 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "smooth" },
    )

    // Re-activating the same node later is an ordinary nearest placement.
    now += NODE_REVEAL_DURATION_MS
    rerender({ id: null })
    rerender({ id: "a" })
    expect(setViewport).toHaveBeenCalledOnce()
  })

  it("centres a node search picks when that node is already active", () => {
    setNode("a", { x: 100, y: 100, width: 200, height: 80 })
    const { result, rerender } = renderReveal()
    rerender({ id: "a" })

    act(() => result.current.centreNode("a", 0.8))

    expect(setViewport).toHaveBeenCalledOnce()
    expect(setViewport).toHaveBeenCalledWith(
      { x: 400 - 200 * 0.8, y: 300 - 140 * 0.8, zoom: 0.8 },
      { duration: NODE_REVEAL_DURATION_MS, interpolate: "smooth" },
    )
  })

  it("ignores a centre request for a node that is not the active node, without re-arming", async () => {
    setNode("a", { x: 500, y: 100, width: 200, height: 80 })
    setNode("b", { x: 300, y: 300, width: 200, height: 80 })
    const { result, rerender } = renderReveal()
    rerender({ id: "a" })
    result.current.handleMoveStart(new MouseEvent("mousedown"), { x: 0, y: 0, zoom: 1 })

    act(() => result.current.centreNode("b", 0.8))
    act(() => resizeCanvas(600, 600))
    await flushMicrotasks()

    expect(setViewport).not.toHaveBeenCalled()
  })
})
