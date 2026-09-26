/** The sort state of a values table and the one cycle every sortable header follows. */

export type SortDirection = "asc" | "desc"

export interface SortState<K extends string> {
  key: K
  dir: SortDirection
}

/** The sort after clicking `key`: a new column sorts ascending, the sorted one reverses. */
export function nextSort<K extends string>(current: SortState<K> | null, key: K): SortState<K> {
  return current?.key === key
    ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
    : { key, dir: "asc" }
}
