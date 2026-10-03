import { describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useSearchableList, type SearchableListItem } from "../useSearchableList"

const item = (index: number, healthy: boolean): SearchableListItem => ({
  index,
  name: `item_${index}`,
  searchTerms: [],
  healthy,
  issues: healthy ? [] : ["needs a value"],
  badges: [],
})

describe("useSearchableList", () => {
  it("keeps the item being fixed under the Issues filter until a filter is chosen again", () => {
    const select = vi.fn()
    const { result, rerender } = renderHook(
      ({ items, selected }) => useSearchableList(items, selected, select),
      { initialProps: { items: [item(0, true), item(1, false), item(2, false)], selected: 1 } },
    )
    act(() => result.current.setFilter("problems"))
    expect(result.current.visible.map((entry) => entry.index)).toEqual([1, 2])

    // An edit fixes the selected item: it stays listed, and the selection stays on it.
    rerender({ items: [item(0, true), item(1, true), item(2, false)], selected: 1 })
    expect(result.current.visible.map((entry) => entry.index)).toEqual([1, 2])
    expect(select).not.toHaveBeenCalled()

    // Choosing the filter again, even the one already chosen, applies it afresh.
    act(() => result.current.setFilter("problems"))
    expect(result.current.visible.map((entry) => entry.index)).toEqual([2])
  })

  it("hides a healthy item under the Issues filter when it was healthy as it was selected", () => {
    const select = vi.fn()
    const { result } = renderHook(() => useSearchableList([item(0, true), item(1, false)], 0, select))

    act(() => result.current.setFilter("problems"))

    expect(result.current.visible.map((entry) => entry.index)).toEqual([1])
    expect(select).toHaveBeenCalledWith(1)
  })
})
