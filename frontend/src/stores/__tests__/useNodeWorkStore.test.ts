import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"

import useNodeWorkStore, { registerNodeStop, stopNodeWork, useNodeWorkRunning } from "../useNodeWorkStore"

describe("useNodeWorkStore", () => {
  beforeEach(() => useNodeWorkStore.setState({ running: {} }))

  it("reports a node running while any of its registered work runs", () => {
    const { result, rerender } = renderHook(({ nodeId }) => useNodeWorkRunning(nodeId), {
      initialProps: { nodeId: "a" as string | null },
    })
    expect(result.current).toBe(false)

    act(() => {
      useNodeWorkStore.getState().setRunning("build", "a")
      useNodeWorkStore.getState().setRunning("import", "a")
    })
    expect(result.current).toBe(true)

    act(() => useNodeWorkStore.getState().setRunning("build", null))
    expect(result.current).toBe(true)
    act(() => useNodeWorkStore.getState().setRunning("import", null))
    expect(result.current).toBe(false)

    rerender({ nodeId: null })
    expect(result.current).toBe(false)
  })

  it("stops only the handlers still registered for the node", () => {
    const first = vi.fn()
    const second = vi.fn()
    const unregisterFirst = registerNodeStop("a", first)
    const unregisterSecond = registerNodeStop("a", second)
    unregisterFirst()

    stopNodeWork("a")
    stopNodeWork("nobody")

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
    unregisterSecond()
    stopNodeWork("a")
    expect(second).toHaveBeenCalledTimes(1)
  })
})
