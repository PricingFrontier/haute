import { useState } from "react"
import { expectRows, followRows, rowIdentity, type RowChange, type RowIdentity } from "../../../utils/rowKeys"

export type RowKeys<R> = {
  /** Each row's key, by position: the identity its drafts are kept under. */
  keys: readonly number[]
  /** The list's own edit of rows in place: returns *next*, for the write. */
  edit: (next: R[]) => R[]
  /** The list's own rows added at the end. */
  add: (next: R[]) => R[]
  /** The list's own removal of the row at *index*. */
  remove: (index: number, next: R[]) => R[]
  /** The list's own move of a row from one place to another. */
  move: (from: number, to: number, next: R[]) => R[]
}

/**
 * Stable keys for a list's rows, so what a row holds (a cell's draft, an expression's
 * drafts, a box's text) stays with that row. The list writes through these, saying what
 * each write does, and a row keeps its key only while the rows that come back are the ones
 * the list's own write produced. Any other change (an undo, a redo, a sync frame), even of
 * the same length, gives every row a new key: its drafts start afresh rather than pass to
 * another row (`rowKeys.ts`).
 */
export function useRowKeys<R>(rows: readonly R[]): RowKeys<R> {
  const [state, setState] = useState<RowIdentity>(() => rowIdentity(rows))
  let current = state
  const followed = followRows(state, rows)
  if (followed !== state) {
    setState(followed)
    current = followed
  }
  const expect = (next: R[], change: RowChange): R[] => {
    setState(expectRows(current, next, change))
    return next
  }
  return {
    keys: current.keys,
    edit: (next) => expect(next, { kind: "edit" }),
    add: (next) => expect(next, { kind: "add" }),
    remove: (index, next) => expect(next, { kind: "remove", index }),
    move: (from, to, next) => expect(next, { kind: "move", from, to }),
  }
}
