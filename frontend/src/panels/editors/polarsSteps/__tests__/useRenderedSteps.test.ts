import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

vi.mock("../../../../api/client", () => ({
  renderPolarsSteps: vi.fn(),
}))

import { renderPolarsSteps } from "../../../../api/client"
import { useRenderedSteps } from "../useRenderedSteps"
import type { Step } from "../types"

const mockRender = vi.mocked(renderPolarsSteps)

const one: Step[] = [{ id: "s", kind: "source", input: "quotes" }]
const two: Step[] = [...one, { id: "l", kind: "limit", n: 3 }]

type Deferred = { resolve: (value: Awaited<ReturnType<typeof renderPolarsSteps>>) => void }

function deferred(): { promise: ReturnType<typeof renderPolarsSteps>; handle: Deferred } {
  const handle = {} as Deferred
  const promise = new Promise<Awaited<ReturnType<typeof renderPolarsSteps>>>((resolve) => {
    handle.resolve = resolve
  })
  return { promise, handle }
}

const okResponse = (code: string) => ({ ok: true, code, step_lines: [[1, 1]], step_index: null, message: "" })

describe("useRenderedSteps", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockRender.mockReset()
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("requests nothing for an empty list and reports the empty status", () => {
    const { result } = renderHook(() => useRenderedSteps([], ["quotes"], "input"))
    expect(result.current.status).toBe("empty")
    expect(mockRender).not.toHaveBeenCalled()
  })

  it("debounces, then reports the rendered code for the current revision", async () => {
    mockRender.mockResolvedValue(okResponse("df = quotes"))
    const onRendered = vi.fn()
    const { result } = renderHook(() => useRenderedSteps(one, ["quotes"], "input", onRendered))
    expect(result.current.status).toBe("pending")
    expect(mockRender).not.toHaveBeenCalled()
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(mockRender).toHaveBeenCalledTimes(1)
    expect(result.current.status).toBe("ok")
    expect(result.current.code).toBe("df = quotes")
    expect(result.current.revisionRendered).toBe(result.current.revision)
    expect(onRendered).toHaveBeenCalledWith("df = quotes")
  })

  it("surfaces a failed render with the failing step and does not report code", async () => {
    mockRender.mockResolvedValue({ ok: false, code: "", step_lines: [], step_index: 1, message: "Add at least one condition." })
    const onRendered = vi.fn()
    const { result } = renderHook(() => useRenderedSteps(two, ["quotes"], "input", onRendered))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.status).toBe("error")
    expect(result.current.error).toEqual({ stepIndex: 1, message: "Add at least one condition." })
    expect(onRendered).not.toHaveBeenCalled()
  })

  it("ignores an older revision's response that arrives after a newer edit", async () => {
    const first = deferred()
    const second = deferred()
    mockRender.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const onRendered = vi.fn()
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", onRendered), {
      initialProps: { steps: one },
    })
    await act(async () => {
      vi.advanceTimersByTime(250)
    })
    rerender({ steps: two })
    await act(async () => {
      vi.advanceTimersByTime(250)
    })
    expect(mockRender).toHaveBeenCalledTimes(2)
    await act(async () => {
      second.handle.resolve(okResponse("df = quotes\ndf = df.head(3)"))
      await Promise.resolve()
    })
    expect(result.current.code).toBe("df = quotes\ndf = df.head(3)")
    const rendered = result.current.revisionRendered
    await act(async () => {
      first.handle.resolve(okResponse("df = quotes"))
      await Promise.resolve()
    })
    expect(result.current.code).toBe("df = quotes\ndf = df.head(3)")
    expect(result.current.revisionRendered).toBe(rendered)
    expect(onRendered).toHaveBeenCalledTimes(1)
    expect(onRendered).toHaveBeenCalledWith("df = quotes\ndf = df.head(3)")
  })

  it("keeps the last good code but clears its ranges while a newer render is pending", async () => {
    mockRender.mockResolvedValueOnce(okResponse("df = quotes"))
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input"), {
      initialProps: { steps: one },
    })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.status).toBe("ok")
    expect(result.current.stepLines).toEqual([[1, 1]])
    mockRender.mockReturnValueOnce(deferred().promise)
    rerender({ steps: two })
    expect(result.current.status).toBe("pending")
    expect(result.current.code).toBe("df = quotes")
    expect(result.current.revisionRendered).not.toBe(result.current.revision)
    expect(result.current.stepLines).toEqual([])
  })

  it("never writes code back after the editor unmounts, even if a response has already escaped cancellation", async () => {
    const pending = deferred()
    mockRender.mockReturnValue(pending.promise)
    const onRendered = vi.fn()
    const { unmount } = renderHook(() => useRenderedSteps(one, ["quotes"], "input", onRendered))
    await act(async () => { vi.advanceTimersByTime(250) })
    unmount()
    expect(mockRender.mock.calls[0][0].signal?.aborted).toBe(true)
    await act(async () => {
      pending.handle.resolve(okResponse("df = quotes"))
      await Promise.resolve()
    })
    expect(onRendered).not.toHaveBeenCalled()
  })

  it("reports a transport failure as a list-level error, keeps the last code, and clears its ranges", async () => {
    mockRender.mockResolvedValueOnce(okResponse("df = quotes"))
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input"), { initialProps: { steps: one } })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.code).toBe("df = quotes")
    expect(result.current.stepLines).toEqual([[1, 1]])
    mockRender.mockRejectedValueOnce(new Error("boom"))
    rerender({ steps: two })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.status).toBe("error")
    expect(result.current.error).toEqual({ stepIndex: null, message: "Could not render steps: boom" })
    expect(result.current.code).toBe("df = quotes")
    expect(result.current.stepLines).toEqual([])
  })

  it("keeps successful line ranges only for the current revision and clears them for empty steps", async () => {
    mockRender.mockResolvedValue({ ok: true, code: "df = quotes\ndf = df.with_columns(\n  pl.col('premium')\n)", step_lines: [[1, 1], [2, 4]], step_index: null, message: "" })
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input"), { initialProps: { steps: two } })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.stepLines).toEqual([[1, 1], [2, 4]])
    rerender({ steps: [] })
    expect(result.current.status).toBe("empty")
    expect(result.current.stepLines).toEqual([])
  })
})
