import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
import useGraphStore from "../../../stores/useGraphStore"
import { useOptimiserAutoRange } from "../useOptimiserAutoRange"

const api = vi.hoisted(() => ({
  start: vi.fn(),
  status: vi.fn(),
  cancel: vi.fn(),
}))

vi.mock("../../../api/client", () => ({
  startOptimiserFrontierAutoRange: (...args: unknown[]) => api.start(...args),
  getOptimiserFrontierAutoRangeStatus: (...args: unknown[]) => api.status(...args),
  cancelOptimiserFrontierAutoRange: (...args: unknown[]) => api.cancel(...args),
}))

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  return {
    promise: new Promise<T>((done) => { resolve = done }),
    resolve,
  }
}

function renderAutoRange(onUpdate = vi.fn(() => ({ ok: true as const }))) {
  const hook = renderHook(() => useOptimiserAutoRange({
    nodeId: "optimiser-1",
    constraintNames: ["loss_ratio"],
    buildGraph: () => ({ nodes: [], edges: [] }),
    onUpdate,
  }))
  return { ...hook, onUpdate }
}

describe("useOptimiserAutoRange", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.cancel.mockResolvedValue(undefined)
    useDocumentStatusStore.getState().reset()
    useGraphStore.setState({ structuralVersion: 0 })
  })

  it("cancels a job whose start response arrives after unmount", async () => {
    const start = deferred<{ status: "started"; job_id: string }>()
    let requestSignal: AbortSignal | undefined
    api.start.mockImplementation(({ signal }: { signal: AbortSignal }) => {
      requestSignal = signal
      return start.promise
    })
    const { result, unmount, onUpdate } = renderAutoRange()

    act(() => result.current.run())
    expect(result.current.autoRangeLoading).toBe(true)
    unmount()
    expect(requestSignal?.aborted).toBe(true)

    await act(async () => {
      start.resolve({ status: "started", job_id: "late-job" })
      await start.promise
    })

    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith("late-job"))
    expect(api.status).not.toHaveBeenCalled()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("retires an active job when its graph/config fence is replaced", async () => {
    const status = deferred<{ status: "completed"; message: string; result: { ranges: Record<string, { min: number; max: number }> } }>()
    let requestSignal: AbortSignal | undefined
    api.start.mockImplementation(({ signal }: { signal: AbortSignal }) => {
      requestSignal = signal
      return Promise.resolve({ status: "started", job_id: "stale-job" })
    })
    api.status.mockReturnValue(status.promise)
    const { result, onUpdate } = renderAutoRange()

    act(() => result.current.run())
    await waitFor(() => expect(api.status).toHaveBeenCalledWith(
      "stale-job",
      { signal: requestSignal },
    ))

    act(() => {
      useGraphStore.setState((state) => ({ structuralVersion: state.structuralVersion + 1 }))
    })

    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith("stale-job"))
    expect(requestSignal?.aborted).toBe(true)
    expect(result.current.autoRangeLoading).toBe(false)

    await act(async () => {
      status.resolve({
        status: "completed",
        message: "",
        result: { ranges: { loss_ratio: { min: 0.1, max: 0.9 } } },
      })
      await status.promise
    })
    expect(onUpdate).not.toHaveBeenCalled()
  })
})
