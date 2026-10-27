import { useState } from "react"

type RowKeys = { rows: readonly unknown[]; keys: readonly number[]; next: number }

/**
 * The keys for `rows` after `previous`: an edit keeps the list's length and
 * each row its key; when rows are added or removed (by the list's own actions
 * or an undo), a row keeps the key of the earlier row it is, the same object
 * or else the same content, and any other row takes a new key.
 */
function reconcile(previous: RowKeys, rows: readonly unknown[]): RowKeys {
  if (rows.length === previous.rows.length) return { rows, keys: previous.keys, next: previous.next }
  const unused = new Set(previous.rows.keys())
  const content = (row: unknown) => JSON.stringify(row)
  let next = previous.next
  const keys = rows.map((row) => {
    const candidates = [...unused]
    const same = candidates.find((i) => previous.rows[i] === row) ?? candidates.find((i) => content(previous.rows[i]) === content(row))
    if (same === undefined) return next++
    unused.delete(same)
    return previous.keys[same]
  })
  return { rows, keys, next }
}

/**
 * Stable keys for a list's rows, so what a row holds (an expression's drafts,
 * a box's text) stays with that row when rows above it are removed, or come
 * back with an undo.
 */
export function useRowKeys(rows: readonly unknown[]): readonly number[] {
  const [state, setState] = useState<RowKeys>(() => ({ rows, keys: rows.map((_, i) => i), next: rows.length }))
  if (state.rows === rows) return state.keys
  const next = reconcile(state, rows)
  setState(next)
  return next.keys
}
