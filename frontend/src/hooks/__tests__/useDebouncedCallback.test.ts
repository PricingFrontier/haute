import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderHook } from "@testing-library/react"
import { useDebouncedCallback } from "../useDebouncedCallback"

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useDebouncedCallback", () => {
  it("runs once, with the latest arguments, after the delay passes without another schedule", () => {
    const callback = vi.fn()
    const { result } = renderHook(() => useDebouncedCallback(callback, 100))

    result.current.schedule(["a"])
    vi.advanceTimersByTime(60)
    result.current.schedule(["b"])
    vi.advanceTimersByTime(99)
    expect(callback).not.toHaveBeenCalled()
    expect(result.current.pending()).toEqual(["b"])

    vi.advanceTimersByTime(1)
    expect(callback).toHaveBeenCalledTimes(1)
    expect(callback).toHaveBeenCalledWith("b")
    expect(result.current.pending()).toBeNull()
  })

  it("takes a per-call delay over the hook's", () => {
    const callback = vi.fn()
    const { result } = renderHook(() => useDebouncedCallback(callback, 100))

    result.current.schedule(["slow"], 800)
    vi.advanceTimersByTime(799)
    expect(callback).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(callback).toHaveBeenCalledWith("slow")
  })

  it("flushes the waiting call now and returns its result, then does not run it again", () => {
    const callback = vi.fn((value: string) => `saved ${value}`)
    const { result } = renderHook(() => useDebouncedCallback(callback, 100))

    expect(result.current.flush()).toBeUndefined()
    result.current.schedule(["x"])
    expect(result.current.flush()).toBe("saved x")
    vi.advanceTimersByTime(500)
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it("drops a cancelled call", () => {
    const callback = vi.fn()
    const { result } = renderHook(() => useDebouncedCallback(callback, 100))

    result.current.schedule(["x"])
    result.current.cancel()
    vi.advanceTimersByTime(500)
    expect(callback).not.toHaveBeenCalled()
    expect(result.current.pending()).toBeNull()
  })

  it("runs the latest callback and keeps one stable object across renders", () => {
    const first = vi.fn()
    const second = vi.fn()
    const { result, rerender } = renderHook(({ callback }) => useDebouncedCallback(callback, 100), {
      initialProps: { callback: first },
    })
    const debounced = result.current

    debounced.schedule(["x"])
    rerender({ callback: second })
    expect(result.current).toBe(debounced)
    vi.advanceTimersByTime(100)
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith("x")
  })

  it("drops a waiting call on unmount by default", () => {
    const callback = vi.fn()
    const { result, unmount } = renderHook(() => useDebouncedCallback(callback, 100))

    result.current.schedule(["x"])
    unmount()
    vi.advanceTimersByTime(500)
    expect(callback).not.toHaveBeenCalled()
  })

  it("runs a waiting call on unmount when asked to flush", () => {
    const callback = vi.fn()
    const { result, unmount } = renderHook(() =>
      useDebouncedCallback(callback, 100, { onUnmount: "flush" }),
    )

    result.current.schedule(["x"])
    unmount()
    expect(callback).toHaveBeenCalledTimes(1)
    expect(callback).toHaveBeenCalledWith("x")
    vi.advanceTimersByTime(500)
    expect(callback).toHaveBeenCalledTimes(1)
  })
})
