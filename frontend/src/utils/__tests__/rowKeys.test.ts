import { describe, expect, it } from "vitest"

import { expectRows, followRows, rowIdentity } from "../rowKeys"

const rows = (...names: string[]) => names.map((name) => ({ name }))

describe("row identity", () => {
  it("keeps the keys when a parent rebuilds rows that read the same", () => {
    const state = rowIdentity(rows("a", "b"))
    expect(followRows(state, rows("a", "b"))).toBe(state)
  })

  it("follows the list's own edit, addition, removal, move and reshaping", () => {
    let state = rowIdentity(rows("a", "b", "c"))
    const [a, b, c] = state.keys

    state = followRows(expectRows(state, rows("a", "B", "c"), { kind: "edit" }), rows("a", "B", "c"))
    expect(state.keys).toEqual([a, b, c])

    state = followRows(expectRows(state, rows("a", "B", "c", "d"), { kind: "add" }), rows("a", "B", "c", "d"))
    const d = state.keys[3]
    expect(state.keys.slice(0, 3)).toEqual([a, b, c])
    expect([a, b, c]).not.toContain(d)

    state = followRows(expectRows(state, rows("a", "c", "d"), { kind: "remove", index: 1 }), rows("a", "c", "d"))
    expect(state.keys).toEqual([a, c, d])

    state = followRows(expectRows(state, rows("d", "a", "c"), { kind: "move", from: 2, to: 0 }), rows("d", "a", "c"))
    expect(state.keys).toEqual([d, a, c])

    state = followRows(expectRows(state, rows("c", "e"), { kind: "rows", from: [2, null] }), rows("c", "e"))
    expect(state.keys[0]).toBe(c)
    expect([a, c, d]).not.toContain(state.keys[1])
  })

  it("gives every row a new key when the rows change from outside, of the same length too", () => {
    const state = rowIdentity(rows("a", "b"))
    const undone = followRows(state, rows("b", "a"))
    expect(undone.keys).toHaveLength(2)
    expect(undone.keys.some((key) => state.keys.includes(key))).toBe(false)
  })

  it("gives new keys when what comes back is not what the list wrote", () => {
    const state = rowIdentity(rows("a"))
    // The list wrote "x"; an Undo brought "y" instead.
    const after = followRows(expectRows(state, rows("x"), { kind: "edit" }), rows("y"))
    expect(after.keys).not.toEqual(state.keys)
  })
})
