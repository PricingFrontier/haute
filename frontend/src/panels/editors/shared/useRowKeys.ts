import { useState } from "react"

/** A list's rows as last seen, their keys, and the keys the list's own add or remove has just asked for. */
type RowKeys = { rows: readonly unknown[]; keys: readonly number[]; next: number; pending: readonly number[] | null }

const fresh = (count: number, from: number) => Array.from({ length: count }, (_, i) => from + i)

/**
 * Stable keys for a list's rows, so what a row holds (an expression's drafts,
 * a box's text) stays with that row. The list's own actions say what they do:
 * `remove` drops that row's key and `add` gives the new last row a new one,
 * so rows that read the same are never confused. An edit keeps the list's
 * length and every key. Any other change that adds or removes rows (an undo,
 * a redo) gives every row a new key: their drafts start afresh rather than
 * risk passing to another row.
 */
export function useRowKeys(rows: readonly unknown[]): { keys: readonly number[]; remove: (index: number) => void; add: () => void } {
  const [state, setState] = useState<RowKeys>(() => ({ rows, keys: fresh(rows.length, 0), next: rows.length, pending: null }))
  let current = state
  if (state.rows !== rows) {
    if (state.pending && state.pending.length === rows.length) current = { ...state, rows, keys: state.pending, pending: null }
    else if (rows.length === state.rows.length) current = { ...state, rows, pending: null }
    else current = { rows, keys: fresh(rows.length, state.next), next: state.next + rows.length, pending: null }
    setState(current)
  }
  return {
    keys: current.keys,
    remove: (index) => setState((s) => ({ ...s, pending: s.keys.filter((_, i) => i !== index) })),
    add: () => setState((s) => ({ ...s, pending: [...s.keys, s.next], next: s.next + 1 })),
  }
}
