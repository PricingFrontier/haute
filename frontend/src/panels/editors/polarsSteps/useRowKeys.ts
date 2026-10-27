import { useState } from "react"

/**
 * Stable keys for a list's rows, so what a row holds (an expression's drafts,
 * a box's text) stays with it when an earlier row is removed. `remove` drops
 * a row's key as the row is removed; rows added or dropped any other way (an
 * undo) are matched at the end of the list.
 */
export function useRowKeys(length: number): { keys: readonly number[]; remove: (index: number) => void } {
  const [state, setState] = useState(() => ({ keys: Array.from({ length }, (_, i) => i), next: length }))
  let current = state
  if (state.keys.length !== length) {
    const added = Math.max(0, length - state.keys.length)
    current = { keys: [...state.keys.slice(0, length), ...Array.from({ length: added }, (_, i) => state.next + i)], next: state.next + added }
    setState(current)
  }
  return { keys: current.keys, remove: (index) => setState((s) => ({ ...s, keys: s.keys.filter((_, i) => i !== index) })) }
}
