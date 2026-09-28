import { describe, expect, it } from "vitest"

import {
  columnChange,
  columnNames,
  columnsAtEachStep,
  columnsBeforeStep,
  columnsReadBy,
  joinColumns,
  unknownColumnsOf,
  type ColumnInfo,
  type ColumnSource,
} from "../derivedColumns"
import type { JoinHow, Step } from "../types"
import joinOutputs from "./fixtures/joinOutputs.json"

const QUOTES: ColumnInfo[] = [
  { name: "premium", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "k", dtype: "Int64" },
]
const SOURCE: ColumnSource = { inputs: { quotes: QUOTES }, frame: [] }
const names = (source: ColumnSource, list: Step[], index: number) => columnNames(columnsBeforeStep(source, list, index))
const typeOf = (source: ColumnSource, list: Step[], index: number, name: string) =>
  columnsBeforeStep(source, list, index).columns.find((c) => c.name === name)?.dtype

const steps: Step[] = [
  { id: "s", kind: "source", input: "quotes" },
  { id: "w", kind: "with_column", name: "gross", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
  { id: "r", kind: "rename", renames: [{ from: "region", to: "area" }] },
  { id: "d", kind: "drop", columns: ["k"] },
  { id: "f", kind: "filter", match: "all", conditions: [{ column: "gross", operator: "is_null" }] },
  { id: "g", kind: "group_by", keys: ["area"], aggregations: [{ column: "gross", agg: "sum", name: "total" }] },
  { id: "sel", kind: "select", columns: ["total"] },
]

const source = (input: string): Step => ({ id: "s", kind: "source", input })

describe("columnsBeforeStep", () => {
  it("starts from the start input's columns", () => {
    expect(names(SOURCE, steps, 1)).toEqual(["premium", "region", "k"])
  })

  it("adds, renames and drops columns through earlier steps", () => {
    expect(names(SOURCE, steps, 2)).toEqual(["premium", "region", "k", "gross"])
    expect(names(SOURCE, steps, 3)).toEqual(["premium", "area", "k", "gross"])
    expect(names(SOURCE, steps, 4)).toEqual(["premium", "area", "gross"])
    expect(names(SOURCE, steps, 5)).toEqual(["premium", "area", "gross"])
  })

  it("reduces to keys and aggregation names after a group by, then to the selection", () => {
    expect(names(SOURCE, steps, 6)).toEqual(["area", "total"])
    expect(names(SOURCE, steps, 7)).toEqual(["total"])
  })

  it("ignores blank names and duplicates", () => {
    const blank: Step[] = [
      source("quotes"),
      { id: "w", kind: "with_column", name: "", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
      { id: "w2", kind: "with_column", name: "premium", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
    ]
    expect(names(SOURCE, blank, 3)).toEqual(["premium", "region", "k"])
  })

  it("clears schema suggestions after free code because its output is unknown", () => {
    const withFreeCode: Step[] = [
      source("quotes"),
      { id: "w", kind: "with_column", name: "gross", expr: { type: "operand", operand: { kind: "column", name: "premium" } } },
      { id: "code", kind: "free_code", code: "df = df.select('replacement')" },
      { id: "after", kind: "limit", n: 1 },
    ]
    expect(names(SOURCE, withFreeCode, 3)).toEqual([])
    expect(columnsBeforeStep(SOURCE, withFreeCode, 4).complete).toBe(false)
  })

  it("suggests only the start input's columns, not every connected input's", () => {
    const both: ColumnSource = { inputs: { quotes: QUOTES, claims: [{ name: "amount_paid", dtype: "Float64" }] }, frame: [] }
    expect(names(both, [source("quotes"), { id: "l", kind: "limit", n: 1 }], 1)).toEqual(["premium", "region", "k"])
    expect(names(both, [source("claims"), { id: "l", kind: "limit", n: 1 }], 1)).toEqual(["amount_paid"])
  })
})

describe("column types", () => {
  const twoInputs: ColumnSource = {
    inputs: { quotes: [{ name: "premium", dtype: "Float64" }], claims: [{ name: "premium", dtype: "String" }] },
    frame: [],
  }

  it("come from the start input, so another input with the same name cannot disagree, and follow a change of start", () => {
    expect(typeOf(twoInputs, [source("quotes")], 1, "premium")).toBe("Float64")
    expect(typeOf(twoInputs, [source("claims")], 1, "premium")).toBe("String")
  })

  it("are set by a cast, kept by a rename, and dropped when a step replaces the column", () => {
    const list: Step[] = [
      source("quotes"),
      { id: "c", kind: "cast", casts: [{ column: "k", dtype: "Float64" }] },
      { id: "r", kind: "rename", renames: [{ from: "k", to: "key" }] },
      { id: "w", kind: "with_column", name: "premium", expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "*", right: { kind: "literal", type: "number", value: 2 }, text: "premium * 2" } },
    ]
    expect(typeOf(SOURCE, list, 2, "k")).toBe("Float64")
    expect(typeOf(SOURCE, list, 3, "key")).toBe("Float64")
    expect(typeOf(SOURCE, list, 4, "premium")).toBeNull()
    expect(columnsBeforeStep(SOURCE, list, 4).columns.find((c) => c.name === "premium")?.made).toBe(true)
  })
})

describe("completeness", () => {
  const withStep = (step: Step) => columnsBeforeStep(SOURCE, [source("quotes"), step], 2)

  it("is complete after the start input's columns load, and incomplete while they have not", () => {
    expect(columnsBeforeStep(SOURCE, [source("quotes")], 1).complete).toBe(true)
    expect(columnsBeforeStep({ inputs: {}, frame: [] }, [source("quotes")], 1).complete).toBe(false)
  })

  it("is never complete in frame mode, where the surface builds its own frame", () => {
    const frame: ColumnSource = { inputs: {}, frame: QUOTES }
    expect(columnsBeforeStep(frame, [{ id: "l", kind: "limit", n: 1 }], 0)).toMatchObject({ complete: false })
    expect(columnNames(columnsBeforeStep(frame, [], 0))).toEqual(["premium", "region", "k"])
  })

  it("stays complete through plain steps", () => {
    expect(withStep({ id: "l", kind: "limit", n: 1 })).toMatchObject({ complete: true, exact: true })
  })

  it("ends after free code, an append, a type-wide aggregation and a join whose input is unknown", () => {
    expect(withStep({ id: "c", kind: "free_code", code: "df = df" }).complete).toBe(false)
    expect(withStep({ id: "a", kind: "concat", inputs: ["claims"], how: "diagonal" }).complete).toBe(false)
    expect(withStep({ id: "g", kind: "group_by", keys: [], aggregations: [{ dtype: "Float64", agg: "sum", suffix: "_sum" }] }).complete).toBe(false)
    expect(withStep({ id: "j", kind: "join", input: "claims", how: "left", leftOn: ["k"], rightOn: ["k"], suffix: "_right" }).complete).toBe(false)
  })

  it("keeps a type-wide keep or drop complete but no longer exact", () => {
    expect(withStep({ id: "k", kind: "select", columns: ["premium"], dtypes: ["Int64"] })).toMatchObject({ complete: true, exact: false })
    expect(withStep({ id: "d", kind: "drop", columns: [], dtypes: ["Float64"] })).toMatchObject({ complete: true, exact: false })
  })
})

describe("join output columns", () => {
  const left = joinOutputs.left.map((c) => ({ ...c, made: false }))
  const right = joinOutputs.right.map((c) => ({ ...c, made: false }))

  it.each(joinOutputs.cases.map((c) => [`${c.how} join, ${c.keys} keys`, c] as const))("match Polars for a %s", (_label, c) => {
    const step = { how: c.how as JoinHow, leftOn: c.leftOn, rightOn: c.rightOn, suffix: joinOutputs.suffix }
    expect(joinColumns(left, right, step).map((col) => col.name)).toEqual(c.output)
  })

  it("follow the joined input's columns through the step list, complete once both inputs are known", () => {
    const inputs: ColumnSource = { inputs: { quotes: joinOutputs.left, claims: joinOutputs.right }, frame: [] }
    for (const c of joinOutputs.cases) {
      const list: Step[] = [source("quotes"), { id: "j", kind: "join", input: "claims", how: c.how as JoinHow, leftOn: c.leftOn, rightOn: c.rightOn, suffix: joinOutputs.suffix }]
      const after = columnsBeforeStep(inputs, list, 2)
      expect(columnNames(after)).toEqual(c.output)
      expect(after.complete).toBe(true)
    }
  })
})

describe("columnChange", () => {
  const states = (list: Step[]) => columnsAtEachStep(SOURCE, list)

  it("names the columns a step adds or removes, and the new width after a reshape", () => {
    const [, afterSource, afterAdd, afterDrop, afterGroup] = states(steps.slice(0, 1).concat(
      steps[1],
      { id: "d", kind: "drop", columns: ["k", "region", "premium"] },
      { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "gross", agg: "sum", name: "total" }] },
    ))
    expect(columnChange(steps[1], afterSource, afterAdd)).toBe("+gross")
    expect(columnChange({ id: "d", kind: "drop", columns: ["k", "region", "premium"] }, afterAdd, afterDrop)).toBe("−3 columns")
    expect(columnChange({ id: "g", kind: "group_by", keys: [], aggregations: [] }, afterDrop, afterGroup)).toBe("→ 1 column")
  })

  it("says nothing when the columns are not exactly known", () => {
    const list: Step[] = [source("quotes"), { id: "d", kind: "drop", columns: [], dtypes: ["Int64"] }, { id: "w", kind: "with_column", name: "x", expr: { type: "binary", left: { kind: "column", name: "premium" }, op: "*", right: { kind: "literal", type: "number", value: 2 }, text: "premium * 2" } }]
    const [, s1, s2, s3] = states(list)
    expect(columnChange(list[1], s1, s2)).toBeNull()
    expect(columnChange(list[2], s2, s3)).toBeNull()
  })
})

describe("unknown columns", () => {
  it("lists the names a step reads that the data does not have, only while the columns are complete", () => {
    const filter: Step = { id: "f", kind: "filter", match: "all", conditions: [{ column: "quot_id", operator: "is_null" }] }
    expect(unknownColumnsOf(filter, columnsBeforeStep(SOURCE, [source("quotes"), filter], 1), SOURCE)).toEqual(["quot_id"])
    const afterCode: Step[] = [source("quotes"), { id: "c", kind: "free_code", code: "df = df" }, filter]
    expect(unknownColumnsOf(filter, columnsBeforeStep(SOURCE, afterCode, 2), SOURCE)).toEqual([])
  })

  it("checks a join's right keys against the joined input", () => {
    const inputs: ColumnSource = { inputs: { quotes: QUOTES, claims: [{ name: "claim_region", dtype: "String" }] }, frame: [] }
    const join: Step = { id: "j", kind: "join", input: "claims", how: "left", leftOn: ["region"], rightOn: ["region"], suffix: "_right" }
    expect(unknownColumnsOf(join, columnsBeforeStep(inputs, [source("quotes"), join], 1), inputs)).toEqual(["region"])
  })

  it("reads no column for a columnless window saved without one", () => {
    const rowCount = { id: "n", kind: "with_column", name: "n", expr: { type: "window", agg: "len", over: [] } } as unknown as Step
    const rowNumber = { id: "r", kind: "with_column", name: "r", expr: { type: "window", agg: "row_number", over: ["k"] } } as unknown as Step
    expect(columnsReadBy(rowCount)).toEqual([])
    expect(columnsReadBy(rowNumber)).toEqual(["k"])
    expect(unknownColumnsOf(rowCount, columnsBeforeStep(SOURCE, [source("quotes"), rowCount], 1), SOURCE)).toEqual([])
  })

  it("reads the columns a step names, leaving out blanks", () => {
    expect(columnsReadBy({ id: "g", kind: "group_by", keys: ["region", ""], aggregations: [{ column: "premium", agg: "sum", name: "t" }, { column: "", agg: "len", name: "n" }] })).toEqual(["region", "premium"])
  })
})
