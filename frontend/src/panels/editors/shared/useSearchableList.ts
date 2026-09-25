import { useEffect, useRef, useState } from "react"

/** One row of the list: an item of the editor's own collection. */
export interface SearchableListItem {
  /** The item's position in the editor's collection. */
  index: number
  name: string
  /** Matched by the search as well as the name. */
  searchTerms: string[]
  healthy: boolean
  /** Why the item needs attention; empty when it is healthy. */
  issues: string[]
  badges: string[]
}

export type SearchableListFilter = "all" | "problems"

export interface SearchableList {
  items: SearchableListItem[]
  visible: SearchableListItem[]
  search: string
  setSearch: (search: string) => void
  filter: SearchableListFilter
  setFilter: (filter: SearchableListFilter) => void
  /** Clear the search and the filter, so an item just added is in view. */
  reset: () => void
  /** The search or the filter hides every item, so there is nothing to edit. */
  noneVisible: boolean
}

/**
 * The search and filter state of a {@link SearchableItemList}. When they hide
 * the selected item, the first visible one is selected instead, so the editor
 * never edits an item the list does not show.
 */
export function useSearchableList(
  items: SearchableListItem[],
  selectedIndex: number,
  select: (index: number) => void,
  enabled = true,
): SearchableList {
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState<SearchableListFilter>("all")
  const query = search.trim().toLowerCase()
  const visible = items.filter((item) => {
    if (filter === "problems" && item.healthy) return false
    if (!query) return true
    return [item.name, ...item.searchTerms].some((text) => text.toLowerCase().includes(query))
  })
  const selectedVisible = visible.some((item) => item.index === selectedIndex)
  const firstVisible = visible[0]?.index ?? null
  const selectRef = useRef(select)
  useEffect(() => {
    selectRef.current = select
  })
  useEffect(() => {
    if (!enabled || selectedVisible || firstVisible === null) return
    selectRef.current(firstVisible)
  }, [enabled, selectedVisible, firstVisible])
  return {
    items,
    visible,
    search,
    setSearch,
    filter,
    setFilter,
    reset: () => {
      setSearch("")
      setFilter("all")
    },
    noneVisible: firstVisible === null && (query !== "" || filter === "problems"),
  }
}
