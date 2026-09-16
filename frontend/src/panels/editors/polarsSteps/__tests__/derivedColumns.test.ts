import { describe, expect, it } from "vitest"

import { columnsBeforeStep } from "../derivedColumns"
import type { Step } from "../types"

const UPSTREAM = ["premium", "region", "k"]

const steps: Step[] = [
  { id: "s", kind: "source", input: "quotes" },
  { id: "w", kind: "with_column", name: "gross", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
  { id: "r", kind: "rename", renames: [{ from: "region", to: "area" }] },
  { id: "d", kind: "drop", columns: ["k"] },
  { id: "f", kind: "filter", match: "all", conditions: [{ column: "gross", operator: "is_null" }] },
  { id: "g", kind: "group_by", keys: ["area"], aggregations: [{ column: "gross", agg: "sum", name: "total" }] },
  { id: "sel", kind: "select", columns: ["total"] },
]

describe("columnsBeforeStep", () => {
  it("starts from the upstream columns", () => {
    expect(columnsBeforeStep(UPSTREAM, steps, 1)).toEqual(UPSTREAM)
  })

  it("adds, renames and drops columns through earlier steps", () => {
    expect(columnsBeforeStep(UPSTREAM, steps, 2)).toEqual(["premium", "region", "k", "gross"])
    expect(columnsBeforeStep(UPSTREAM, steps, 3)).toEqual(["premium", "area", "k", "gross"])
    expect(columnsBeforeStep(UPSTREAM, steps, 4)).toEqual(["premium", "area", "gross"])
    expect(columnsBeforeStep(UPSTREAM, steps, 5)).toEqual(["premium", "area", "gross"])
  })

  it("reduces to keys and aggregation names after a group by, then to the selection", () => {
    expect(columnsBeforeStep(UPSTREAM, steps, 6)).toEqual(["area", "total"])
    expect(columnsBeforeStep(UPSTREAM, steps, 7)).toEqual(["total"])
  })

  it("ignores blank names and duplicates", () => {
    const blank: Step[] = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
      { id: "w2", kind: "with_column", name: "premium", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
    ]
    expect(columnsBeforeStep(UPSTREAM, blank, 3)).toEqual(UPSTREAM)
  })

  it("clears schema suggestions after free code because its output is unknown", () => {
    const withFreeCode: Step[] = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "w", kind: "with_column", name: "gross", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
      { id: "code", kind: "free_code", code: "df = df.select('replacement')" },
      { id: "after", kind: "limit", n: 1 },
    ]
    expect(columnsBeforeStep(UPSTREAM, withFreeCode, 3)).toEqual([])
  })
})
