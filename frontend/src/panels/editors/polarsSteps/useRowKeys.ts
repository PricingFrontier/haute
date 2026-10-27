import { useState } from "react"

/** A list's rows as last seen, their keys, and the earlier states a later undo may bring back. */
type RowKeys = {
  rows: readonly unknown[]
  keys: readonly number[]
  next: number
  earlier: ReadonlyArray<{ content: string; keys: readonly number[] }>
}

const EARLIER_LIMIT = 50
const contentOf = (rows: readonly unknown[]) => JSON.stringify(rows)

/** `state`'s earlier states with `state` itself added, the oldest dropped past the limit. */
function withEarlier(state: RowKeys): RowKeys["earlier"] {
  return [...state.earlier, { content: contentOf(state.rows), keys: state.keys }].slice(-EARLIER_LIMIT)
}

/**
 * The keys for `rows` after `previous`, when they did not come from the
 * list's own Remove action (which says which row went). An edit keeps the
 * list's length and each row its key. A list equal to an earlier state (an
 * undo or a redo) takes that state's keys; a row added at the end takes a new
 * key; otherwise each row keeps the key of an earlier row that is the same
 * object or has the same content, and any other row takes a new key.
 */
function reconcile(previous: RowKeys, rows: readonly unknown[]): RowKeys {
  if (rows.length === previous.rows.length) return { ...previous, rows }
  const earlier = withEarlier(previous)
  const content = contentOf(rows)
  const restored = [...previous.earlier].reverse().find((state) => state.content === content)
  if (restored) return { rows, keys: restored.keys, next: previous.next, earlier }
  if (rows.length === previous.rows.length + 1 && contentOf(rows.slice(0, -1)) === contentOf(previous.rows)) {
    return { rows, keys: [...previous.keys, previous.next], next: previous.next + 1, earlier }
  }
  const unused = new Set(previous.rows.keys())
  let next = previous.next
  const keys = rows.map((row) => {
    const candidates = [...unused]
    const same = candidates.find((i) => previous.rows[i] === row) ?? candidates.find((i) => contentOf([previous.rows[i]]) === contentOf([row]))
    if (same === undefined) return next++
    unused.delete(same)
    return previous.keys[same]
  })
  return { rows, keys, next, earlier }
}

/**
 * Stable keys for a list's rows, so what a row holds (an expression's drafts,
 * a box's text) stays with that row when rows above it are removed, or come
 * back with an undo. The list's own Remove action calls `remove`, so a row is
 * never confused with another that reads the same.
 */
export function useRowKeys(rows: readonly unknown[]): { keys: readonly number[]; remove: (index: number) => void } {
  const [state, setState] = useState<RowKeys>(() => ({ rows, keys: rows.map((_, i) => i), next: rows.length, earlier: [] }))
  let current = state
  if (state.rows !== rows) {
    current = reconcile(state, rows)
    setState(current)
  }
  const remove = (index: number) =>
    setState((s) => ({ rows: s.rows.filter((_, i) => i !== index), keys: s.keys.filter((_, i) => i !== index), next: s.next, earlier: withEarlier(s) }))
  return { keys: current.keys, remove }
}
