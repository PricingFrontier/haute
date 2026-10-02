import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"

vi.mock("../../../../api/client", () => ({
  renderPolarsSteps: vi.fn(),
  resolveFreeCodeColumns: vi.fn(),
}))

import { renderPolarsSteps, resolveFreeCodeColumns } from "../../../../api/client"
import { useRenderedSteps, type KnownColumns } from "../useRenderedSteps"
import type { Step } from "../types"

const mockRender = vi.mocked(renderPolarsSteps)
const mockResolve = vi.mocked(resolveFreeCodeColumns)

const one: Step[] = [{ id: "s", kind: "source", input: "quotes" }]
const two: Step[] = [...one, { id: "l", kind: "limit", n: 3 }]
const NONE: KnownColumns = { inputs: {}, frame: [] }

type Deferred = { resolve: (value: Awaited<ReturnType<typeof renderPolarsSteps>>) => void }

function deferred(): { promise: ReturnType<typeof renderPolarsSteps>; handle: Deferred } {
  const handle = {} as Deferred
  const promise = new Promise<Awaited<ReturnType<typeof renderPolarsSteps>>>((resolve) => {
    handle.resolve = resolve
  })
  return { promise, handle }
}

function deferredColumns(): { promise: ReturnType<typeof resolveFreeCodeColumns>; resolve: (value: Awaited<ReturnType<typeof resolveFreeCodeColumns>>) => void } {
  let resolve!: (value: Awaited<ReturnType<typeof resolveFreeCodeColumns>>) => void
  const promise = new Promise<Awaited<ReturnType<typeof resolveFreeCodeColumns>>>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const okResponse = (code: string) => ({ ok: true, code, step_lines: [[1, 1]], step_index: null, message: "" })

describe("useRenderedSteps", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockRender.mockReset()
    mockResolve.mockReset()
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("requests nothing for an empty list and reports the empty status", () => {
    const { result } = renderHook(() => useRenderedSteps([], ["quotes"], "input", NONE, "rated"))
    expect(result.current.status).toBe("empty")
    expect(mockRender).not.toHaveBeenCalled()
  })

  it("debounces, then reports the rendered code for the current revision", async () => {
    mockRender.mockResolvedValue(okResponse("df = quotes"))
    const onRendered = vi.fn()
    const { result } = renderHook(() => useRenderedSteps(one, ["quotes"], "input", NONE, "rated", onRendered))
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
    const { result } = renderHook(() => useRenderedSteps(two, ["quotes"], "input", NONE, "rated", onRendered))
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
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", NONE, "rated", onRendered), {
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
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", NONE, "rated"), {
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
    const { unmount } = renderHook(() => useRenderedSteps(one, ["quotes"], "input", NONE, "rated", onRendered))
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
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", NONE, "rated"), { initialProps: { steps: one } })
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
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", NONE, "rated"), { initialProps: { steps: two } })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.stepLines).toEqual([[1, 1], [2, 4]])
    rerender({ steps: [] })
    expect(result.current.status).toBe("empty")
    expect(result.current.stepLines).toEqual([])
  })

  const QUOTES = [{ name: "premium", dtype: "Float64" }]
  const KNOWN: KnownColumns = { inputs: { quotes: QUOTES, other: [{ name: "x", dtype: "Int64" }] }, frame: [{ name: "f", dtype: "Int64" }] }
  const code: Step = { id: "code", kind: "free_code", code: "df = df.with_columns(band=pl.lit(1))" }
  const withCode: Step[] = [...one, code]
  const band = [...QUOTES, { name: "band", dtype: "Int32" }]
  const renderedCode = (steps: Step[]) => ({
    ok: true,
    code: "df = quotes\ndf = df.with_columns(band=pl.lit(1))",
    step_lines: steps.map((_, i) => [i + 1, i + 1]),
    step_index: null,
    message: "",
  })
  const resolvedColumns = { free_code_columns: [{ step_index: 1, columns: band, message: "" }] }

  it("asks for free-code columns after the render, with the node id and the eligible inputs' known columns, and the frame's only in frame mode", async () => {
    mockRender.mockResolvedValue(renderedCode(withCode))
    mockResolve.mockResolvedValue(resolvedColumns)
    renderHook(() => useRenderedSteps(withCode, ["quotes"], "input", KNOWN, "rated"))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(Object.keys(mockRender.mock.calls[0][0]).sort()).toEqual(["inputNames", "signal", "start", "steps"])
    expect(mockResolve.mock.calls[0][0]).toMatchObject({ nodeId: "rated", steps: withCode, inputNames: ["quotes"], start: "input", inputColumns: { quotes: QUOTES }, frameColumns: [] })
    const frameCode: Step[] = [{ ...code, id: "frame-code" }]
    renderHook(() => useRenderedSteps(frameCode, [], "frame", KNOWN, "explore"))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(mockResolve.mock.calls[1][0]).toMatchObject({ nodeId: "explore", inputColumns: {}, frameColumns: KNOWN.frame })
  })

  it("asks for no free-code columns for a list without free code", async () => {
    mockRender.mockResolvedValue(okResponse("df = quotes\ndf = df.head(3)"))
    const { result } = renderHook(() => useRenderedSteps(two, ["quotes"], "input", KNOWN, "rated"))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.status).toBe("ok")
    expect(mockResolve).not.toHaveBeenCalled()
    expect(result.current.freeCode.size).toBe(0)
  })

  it("reports the code before the free-code columns answer, and resolves them after", async () => {
    const columns = deferredColumns()
    mockRender.mockResolvedValue(renderedCode(withCode))
    mockResolve.mockReturnValue(columns.promise)
    const onRendered = vi.fn()
    const { result } = renderHook(() => useRenderedSteps(withCode, ["quotes"], "input", KNOWN, "rated", onRendered))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.status).toBe("ok")
    expect(onRendered).toHaveBeenCalledWith("df = quotes\ndf = df.with_columns(band=pl.lit(1))")
    expect(result.current.freeCode.size).toBe(0)
    await act(async () => {
      columns.resolve(resolvedColumns)
      await Promise.resolve()
    })
    expect(result.current.freeCode.get("code")).toEqual({ columns: band, message: "" })
  })

  it("gives each free-code step a failed columns request as its reason", async () => {
    mockRender.mockResolvedValue(renderedCode(withCode))
    mockResolve.mockRejectedValue(new Error("HTTP 500"))
    const { result } = renderHook(() => useRenderedSteps(withCode, ["quotes"], "input", KNOWN, "rated"))
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.freeCode.get("code")).toEqual({ columns: null, message: "the request failed: HTTP 500" })
  })

  it("keys a free-code step's resolved columns by its id and keeps them while later steps are pending or unfinished", async () => {
    mockRender.mockResolvedValueOnce(renderedCode(withCode))
    mockResolve.mockResolvedValueOnce(resolvedColumns)
    const { result, rerender } = renderHook(({ steps }) => useRenderedSteps(steps, ["quotes"], "input", KNOWN, "rated"), { initialProps: { steps: withCode } })
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    const resolved = { columns: band, message: "" }
    expect(result.current.freeCode).toEqual(new Map([["code", resolved]]))

    // A half-built step after the free code: pending, then a failed render.
    const unfinished: Step[] = [...withCode, { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "" } } }]
    mockRender.mockResolvedValueOnce({ ok: false, code: "", step_lines: [], step_index: 2, message: "Column name must be a non-empty string." })
    rerender({ steps: unfinished })
    expect(result.current.status).toBe("pending")
    expect(result.current.freeCode.get("code")).toEqual(resolved)
    await act(async () => {
      vi.advanceTimersByTime(250)
      await Promise.resolve()
    })
    expect(result.current.status).toBe("error")
    expect(result.current.freeCode.get("code")).toEqual(resolved)

    // Editing the free code itself (or anything before it) retires what was resolved for it.
    mockRender.mockReturnValueOnce(deferred().promise)
    rerender({ steps: [one[0], { ...code, code: "df = df" }, unfinished[2]] })
    expect(result.current.freeCode.size).toBe(0)
  })
})
