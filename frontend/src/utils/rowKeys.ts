import { canonicalJson } from "./canonicalJson"

/**
 * A list's rows and the identity each holds: what a row's draft and anything remembered
 * beside it are keyed by, never its position (specs/frontend-shared high-level, "Panel
 * values and Undo"). A row keeps its key only through the list's own writes: the list says
 * what its write does, and when the rows that come back are the ones that write produced,
 * the keys follow it. Any other change (an Undo, a Redo, a sync frame), of the same length
 * or not, gives every row a new key, so nothing held for one row passes to another.
 */
export type RowIdentity = {
  /** The rows as last seen, as canonical JSON: a parent that rebuilds equal rows changes nothing. */
  readonly seen: string
  readonly keys: readonly number[]
  readonly next: number
  /** The rows the list's own last write produced, and the keys they take. */
  readonly expected: { readonly rows: string; readonly keys: readonly number[] } | null
}

/** What a list's own write does to its rows. */
export type RowChange =
  | { kind: "edit" }
  | { kind: "add" }
  | { kind: "remove"; index: number }
  | { kind: "move"; from: number; to: number }
  /** Any reshaping: for each row written, the row it was, or null for a new one. */
  | { kind: "rows"; from: readonly (number | null)[] }

const fresh = (count: number, from: number): number[] => Array.from({ length: count }, (_, i) => from + i)

export function rowIdentity(rows: readonly unknown[], from = 0): RowIdentity {
  return { seen: canonicalJson(rows), keys: fresh(rows.length, from), next: from + rows.length, expected: null }
}

/** The identity for *rows* now: unchanged, the list's own write, or a change from outside. */
export function followRows(state: RowIdentity, rows: readonly unknown[]): RowIdentity {
  const seen = canonicalJson(rows)
  if (seen === state.seen && state.keys.length === rows.length) return state
  if (state.expected && seen === state.expected.rows && state.expected.keys.length === rows.length) {
    return { ...state, seen, keys: state.expected.keys, expected: null }
  }
  return rowIdentity(rows, state.next)
}

/** The identity once the list's own write of *next*, doing *change*, comes back. */
export function expectRows(state: RowIdentity, next: readonly unknown[], change: RowChange): RowIdentity {
  let keys: readonly number[]
  let counter = state.next
  switch (change.kind) {
    case "edit":
      keys = state.keys
      break
    case "add": {
      const added = next.length - state.keys.length
      keys = [...state.keys, ...fresh(Math.max(0, added), counter)]
      counter += Math.max(0, added)
      break
    }
    case "remove":
      keys = state.keys.filter((_, i) => i !== change.index)
      break
    case "rows":
      keys = change.from.map((index) => {
        if (index !== null && index >= 0 && index < state.keys.length) return state.keys[index]
        return counter++
      })
      break
    case "move": {
      const moved = [...state.keys]
      moved.splice(change.to, 0, ...moved.splice(change.from, 1))
      keys = moved
      break
    }
  }
  if (keys.length !== next.length) return rowIdentity(next, counter)
  return { ...state, next: counter, expected: { rows: canonicalJson(next), keys } }
}
