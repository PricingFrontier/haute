import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useChartEnvironmentRevision } from "../chartTheme"

describe("useChartEnvironmentRevision", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-theme")
    vi.unstubAllGlobals()
  })

  it("changes when the theme or the reduced-motion preference does, and stops on unmount", async () => {
    let motionChanged: (() => void) | null = null
    const removeEventListener = vi.fn()
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: (_event: string, listener: () => void) => {
          motionChanged = listener
        },
        removeEventListener,
      }),
    )
    const { result, unmount } = renderHook(() => useChartEnvironmentRevision())
    expect(result.current).toBe(0)

    act(() => document.documentElement.setAttribute("data-theme", "dark"))
    await waitFor(() => expect(result.current).toBe(1))

    act(() => motionChanged?.())
    expect(result.current).toBe(2)

    unmount()
    expect(removeEventListener).toHaveBeenCalledWith("change", motionChanged)
  })
})
