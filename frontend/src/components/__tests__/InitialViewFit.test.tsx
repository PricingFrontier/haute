import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render } from "@testing-library/react"

import InitialViewFit from "../InitialViewFit"

const flow = vi.hoisted(() => ({
  nodesInitialized: false,
  fitView: vi.fn(),
}))

vi.mock("@xyflow/react", () => ({
  useNodesInitialized: () => flow.nodesInitialized,
  useReactFlow: () => ({ fitView: flow.fitView }),
}))

beforeEach(() => {
  flow.nodesInitialized = false
  flow.fitView.mockReset()
})

afterEach(cleanup)

describe("InitialViewFit", () => {
  it("does not fit while any node is still unmeasured", () => {
    render(<InitialViewFit />)

    expect(flow.fitView).not.toHaveBeenCalled()
  })

  it("fits the whole graph once every node has been measured", () => {
    const { rerender } = render(<InitialViewFit />)

    flow.nodesInitialized = true
    rerender(<InitialViewFit />)

    expect(flow.fitView).toHaveBeenCalledOnce()
    expect(flow.fitView).toHaveBeenCalledWith({ padding: 0.15 })
  })

  it("never fits again when nodes are later re-measured or added", () => {
    flow.nodesInitialized = true
    const { rerender } = render(<InitialViewFit />)

    flow.nodesInitialized = false
    rerender(<InitialViewFit />)
    flow.nodesInitialized = true
    rerender(<InitialViewFit />)

    expect(flow.fitView).toHaveBeenCalledOnce()
  })

  it("fits again when the canvas remounts with a fresh viewport", () => {
    flow.nodesInitialized = true
    const { unmount } = render(<InitialViewFit />)
    unmount()

    render(<InitialViewFit />)

    expect(flow.fitView).toHaveBeenCalledTimes(2)
  })
})
