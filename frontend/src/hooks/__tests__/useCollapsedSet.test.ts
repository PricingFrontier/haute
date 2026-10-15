/**
 * The items of a list shown collapsed (specs/frontend-shared): all open to start, each
 * toggled shut and open again.
 */
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import useCollapsedSet from "../useCollapsedSet"

describe("useCollapsedSet", () => {
  it("toggles each id shut and open again, the others left as they are", () => {
    const { result } = renderHook(() => useCollapsedSet())
    expect(result.current.collapsed.size).toBe(0)

    act(() => result.current.toggle("a"))
    act(() => result.current.toggle("b"))
    expect([...result.current.collapsed]).toEqual(["a", "b"])
    act(() => result.current.toggle("a"))
    expect([...result.current.collapsed]).toEqual(["b"])
  })
})
