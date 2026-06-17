import { describe, it, expect } from "vitest"
import {
  deriveColumnRows,
  serializeSelection,
  reorderRows,
  type ColumnSelectorRow,
} from "../columnSelection"
import type { ColumnInfo } from "../../types/node"

const AVAIL: ColumnInfo[] = [
  { name: "quote_id", dtype: "int" },
  { name: "premium", dtype: "float" },
  { name: "region", dtype: "str" },
]

describe("deriveColumnRows", () => {
  it("empty selection keeps every column in incoming order, all ticked", () => {
    const rows = deriveColumnRows(AVAIL, [], {})
    expect(rows.map((r) => r.incomingName)).toEqual(["quote_id", "premium", "region"])
    expect(rows.every((r) => r.selected && !r.stale)).toBe(true)
    expect(rows.map((r) => r.incomingOrder)).toEqual([1, 2, 3])
    expect(rows.map((r) => r.outputName)).toEqual(["quote_id", "premium", "region"])
    expect(rows.map((r) => r.dtype)).toEqual(["int", "float", "str"])
  })

  it("puts kept columns first in saved order, then the rest unticked in incoming order", () => {
    const rows = deriveColumnRows(AVAIL, ["region", "quote_id"], {})
    expect(rows.map((r) => [r.incomingName, r.selected])).toEqual([
      ["region", true],
      ["quote_id", true],
      ["premium", false],
    ])
    // incoming order travels with the row regardless of display position
    expect(rows.map((r) => r.incomingOrder)).toEqual([3, 1, 2])
  })

  it("applies renames to outputName, keyed by the incoming name", () => {
    const rows = deriveColumnRows(AVAIL, [], { premium: "gross_premium" })
    const premium = rows.find((r) => r.incomingName === "premium")
    expect(premium?.outputName).toBe("gross_premium")
    expect(rows.find((r) => r.incomingName === "quote_id")?.outputName).toBe("quote_id")
  })

  it("surfaces a saved column absent upstream as a stale ghost row", () => {
    const rows = deriveColumnRows(AVAIL, ["quote_id", "dropped_col"], {})
    const ghost = rows.find((r) => r.incomingName === "dropped_col")
    expect(ghost).toMatchObject({ selected: true, stale: true, incomingOrder: 0, dtype: "unknown" })
  })

  it("ignores a duplicated name in the saved selection", () => {
    const rows = deriveColumnRows(AVAIL, ["quote_id", "quote_id"], {})
    expect(rows.filter((r) => r.incomingName === "quote_id")).toHaveLength(1)
  })
})

describe("serializeSelection", () => {
  const rowsFrom = (
    spec: Array<[name: string, selected: boolean, outputName?: string]>,
  ): ColumnSelectorRow[] =>
    spec.map(([name, selected, outputName]) => {
      const idx = AVAIL.findIndex((c) => c.name === name)
      return {
        incomingName: name,
        incomingOrder: idx + 1,
        outputName: outputName ?? name,
        dtype: idx >= 0 ? AVAIL[idx].dtype : "unknown",
        selected,
        stale: idx < 0,
      }
    })

  it("keeping all columns in natural order serialises to [] (preserves the no-op)", () => {
    const rows = rowsFrom([["quote_id", true], ["premium", true], ["region", true]])
    expect(serializeSelection(rows, AVAIL)).toEqual({ selectedColumns: [], columnRenames: {} })
  })

  it("a subset serialises to the ticked names in display order", () => {
    const rows = rowsFrom([["region", true], ["quote_id", true], ["premium", false]])
    expect(serializeSelection(rows, AVAIL).selectedColumns).toEqual(["region", "quote_id"])
  })

  it("keeping all columns but REORDERED serialises an explicit list (not [])", () => {
    const rows = rowsFrom([["premium", true], ["quote_id", true], ["region", true]])
    expect(serializeSelection(rows, AVAIL).selectedColumns).toEqual([
      "premium",
      "quote_id",
      "region",
    ])
  })

  it("captures a rename only when output differs from incoming, only for ticked rows", () => {
    const rows = rowsFrom([
      ["quote_id", true, "id"],
      ["premium", true, "premium"], // unchanged → no rename
      ["region", false, "REGION"], // unticked → ignored
    ])
    expect(serializeSelection(rows, AVAIL).columnRenames).toEqual({ quote_id: "id" })
  })

  it("treats a blank rename as no rename", () => {
    const rows = rowsFrom([["quote_id", true, "   "]])
    expect(serializeSelection(rows, AVAIL).columnRenames).toEqual({})
  })
})

describe("reorderRows", () => {
  const rows = deriveColumnRows(AVAIL, [], {})

  it("moves a row from one index to another", () => {
    expect(reorderRows(rows, 0, 2).map((r) => r.incomingName)).toEqual([
      "premium",
      "region",
      "quote_id",
    ])
  })

  it("is a no-op copy when from === to or out of range", () => {
    expect(reorderRows(rows, 1, 1).map((r) => r.incomingName)).toEqual(rows.map((r) => r.incomingName))
    expect(reorderRows(rows, -1, 2)).not.toBe(rows)
    expect(reorderRows(rows, 0, 9).map((r) => r.incomingName)).toEqual(rows.map((r) => r.incomingName))
  })
})

describe("round trip", () => {
  it("derive ∘ serialize is stable for a reordered subset with a rename", () => {
    const config = { selected_columns: ["region", "quote_id"], column_renames: { quote_id: "id" } }
    const rows = deriveColumnRows(AVAIL, config.selected_columns, config.column_renames)
    expect(serializeSelection(rows, AVAIL)).toEqual({
      selectedColumns: ["region", "quote_id"],
      columnRenames: { quote_id: "id" },
    })
  })
})
