import { describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useGridCellSelection } from "../useGridCellSelection"

const cellText = (row: string, col: string) => `${row}${col}`

function copy(region: { onCopy: (e: React.ClipboardEvent<HTMLElement>) => void }) {
  const setData = vi.fn()
  const preventDefault = vi.fn()
  region.onCopy({ target: document.body, clipboardData: { setData }, preventDefault } as unknown as React.ClipboardEvent<HTMLElement>)
  return { setData, preventDefault }
}

describe("useGridCellSelection", () => {
  it("copies a range dragged up and left row by row from its top-left cell, and only the cells the grid still shows", () => {
    const { result } = renderHook(() => useGridCellSelection())

    act(() => result.current.cellProps(2, 1).onMouseDown())
    act(() => result.current.cellProps(0, 0).onMouseEnter())

    expect(result.current.isCellSelected(1, 1)).toBe(true)
    expect(result.current.isCellSelected(2, 2)).toBe(false)
    expect(copy(result.current.regionProps({ rows: ["a", "b", "c"], cols: ["1", "2", "3"], cellText })).setData)
      .toHaveBeenCalledWith("text/plain", "a1\ta2\nb1\tb2\nc1\tc2")
    expect(copy(result.current.regionProps({ rows: ["a"], cols: ["1"], cellText })).setData)
      .toHaveBeenCalledWith("text/plain", "a1")
  })

  it("leaves the copy to the browser once Escape clears the range", () => {
    const { result } = renderHook(() => useGridCellSelection())
    act(() => result.current.cellProps(0, 0).onMouseDown())

    act(() => result.current.regionProps({ rows: ["a"], cols: ["1"], cellText }).onKeyDown(
      { key: "Escape" } as React.KeyboardEvent<HTMLElement>,
    ))

    expect(result.current.isCellSelected(0, 0)).toBe(false)
    expect(copy(result.current.regionProps({ rows: ["a"], cols: ["1"], cellText })).preventDefault).not.toHaveBeenCalled()
  })
})
