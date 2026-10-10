/**
 * The values typed into a sheet (specs/workbench): filled rows as the server counts them,
 * numbers as it types them, what breaks a column's rules, the basis pricing depends on,
 * and a priced output table's rows lined up with a grid's rows by key.
 */
import { describe, expect, it } from "vitest"
import type { SchemaColumn, SchemaTable } from "../../api/types"
import { cellKey, cellProblem, filled, parseTypedNumber, pricedRows, quoteProblems, rowFilled, valuesBasis } from "../sheetValues"
import { pricingBasis, type SampleRow } from "../workbenchForm"

const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
  ...overrides,
})

const equipment: SchemaTable = {
  id: "equipment",
  name: "equipment",
  role: "input",
  rows: "many",
  columns: [
    column("row", "row_number", { type: "int", index: true, key: true }),
    column("item", "item", { required: true }),
    column("value", "value", { type: "float", min: 0, max: 1_000_000 }),
  ],
}
const policy: SchemaTable = {
  id: "policy",
  name: "policy",
  role: "input",
  rows: "one",
  columns: [column("state", "state", { options: ["CA", "NY"], required: true }), column("years", "years", { type: "int", min: 1, max: 200 })],
}
const premiums: SchemaTable = {
  id: "premiums",
  name: "premiums",
  role: "output",
  rows: "many",
  columns: [column("p_row", "row_number", { type: "int", key: true }), column("premium", "premium", { type: "float", required: true })],
}

describe("filled rows", () => {
  it("counts text other than blank and a tick as typed, in any column but the index", () => {
    expect([filled("x"), filled(" "), filled(""), filled(true), filled(false), filled(undefined)]).toEqual([true, false, false, true, false, false])
    expect(rowFilled(equipment, { item: "Crane" })).toBe(true)
    expect(rowFilled(equipment, { row: "7" })).toBe(false)
    expect(rowFilled(equipment, {})).toBe(false)
    expect(rowFilled(equipment, undefined)).toBe(false)
  })
})

describe("cellProblem", () => {
  it("reads numbers as the server does, a currency sign, separators and spaces allowed", () => {
    expect(parseTypedNumber("$1,250,000.50")).toBe(1250000.5)
    expect(parseTypedNumber(" -3e2 ")).toBe(-300)
    expect(parseTypedNumber(".5")).toBe(0.5)
    expect([parseTypedNumber("lots"), parseTypedNumber("$"), parseTypedNumber("1_000"), parseTypedNumber("0x10"), parseTypedNumber("1.2.3")]).toEqual([null, null, null, null, null])
  })

  it("requires a value, a number of the column's type within its range, and one of the allowed values", () => {
    const value = equipment.columns[2]
    expect(cellProblem(equipment.columns[1], "")).toBe("Required")
    expect(cellProblem(equipment.columns[1], "  ")).toBe("Required")
    expect(cellProblem(value, "")).toBeNull()
    expect(cellProblem(value, "lots")).toBe("Not a number")
    expect(cellProblem(value, "-1")).toBe("At least 0")
    expect(cellProblem(value, "$1,000,001")).toBe("At most 1,000,000")
    expect(cellProblem(value, "$999,999")).toBeNull()
    expect(cellProblem(policy.columns[1], "1.5")).toBe("Not a whole number")
    expect(cellProblem(policy.columns[1], "2.0")).toBeNull()
    expect(cellProblem(policy.columns[0], "TX")).toBe("Not one of the allowed values")
    expect(cellProblem(policy.columns[0], "NY")).toBeNull()
  })

  it("never faults a tick box or the index", () => {
    expect(cellProblem(column("c", "c", { type: "bool", required: true }), undefined)).toBeNull()
    expect(cellProblem(equipment.columns[0], "x")).toBeNull()
  })
})

describe("quoteProblems", () => {
  it("checks a one-row table's row always and a many-row table's filled rows only, by cell", () => {
    const schema = { tables: [policy, equipment, premiums] }
    const problems = quoteProblems(schema, {
      policy: [{ years: "500" }],
      equipment: [{ item: "Crane", value: "lots" }, {}, { value: "10" }],
      premiums: [{ premium: "" }],
    })

    expect(problems).toEqual({
      [cellKey("policy", 0, "state")]: "Required",
      [cellKey("policy", 0, "years")]: "At most 200",
      [cellKey("equipment", 0, "value")]: "Not a number",
      [cellKey("equipment", 2, "item")]: "Required",
    })
    expect(quoteProblems({ tables: [equipment] }, {})).toEqual({})
    // A one-row table with nothing typed still owes its required cells.
    expect(quoteProblems({ tables: [policy] }, {})).toEqual({ [cellKey("policy", 0, "state")]: "Required" })
  })
})

describe("valuesBasis", () => {
  it("is what pricing the sample depends on, the schema and the values", () => {
    const form = { version: 1 as const, name: "x", schema: { tables: [policy] }, pages: [], sample: { policy: [{ state: "NY" }] } }
    expect(pricingBasis(form)).toBe(valuesBasis(form.schema, form.sample))
    expect(valuesBasis(form.schema, { policy: [{ state: "CA" }] })).not.toBe(pricingBasis(form))
  })
})

describe("pricedRows", () => {
  const grid: SampleRow[] = [{ item: "Crane", value: "100" }, {}, { item: "Paver" }, { item: "Dozer" }]
  // The server types the filled rows in order, numbering each as the grid does.
  const typed = [
    { row_number: 1, item: "Crane", value: 100 },
    { row_number: 3, item: "Paver" },
    { row_number: 4, item: "Dozer" },
  ]
  const output = [
    { row_number: 4, premium: 40 },
    { row_number: 1, premium: 10 },
    { row_number: 3, premium: null },
  ]

  it("lines a priced table's rows up with the grid's by key, skipping rows with nothing typed and rows no output matches", () => {
    expect(pricedRows(equipment, grid, typed, output)).toEqual([
      { row_number: 1, premium: 10 },
      undefined,
      { row_number: 3, premium: null },
      { row_number: 4, premium: 40 },
    ])
    expect(pricedRows(equipment, grid, typed, output.slice(0, 1))).toEqual([undefined, undefined, undefined, { row_number: 4, premium: 40 }])
  })

  it("matches on every key column, as the server typed it, and nothing without keys or typed rows", () => {
    const keyedByItem: SchemaTable = { ...equipment, columns: [column("item", "item", { key: true }), column("value", "value", { type: "float", key: true })] }
    const byValue = [{ item: "Crane", value: 100, premium: 1 }, { item: "Crane", value: 200, premium: 2 }]
    expect(pricedRows(keyedByItem, [{ item: "Crane", value: "$200" }], [{ item: "Crane", value: 200 }], byValue)).toEqual([{ item: "Crane", value: 200, premium: 2 }])
    // A key the output row lacks, or holds null, matches nothing.
    expect(pricedRows(keyedByItem, [{ item: "Crane", value: "1" }], [{ item: "Crane", value: 1 }], [{ item: "Crane", premium: 3 }, { item: "Crane", value: null, premium: 4 }])).toEqual([undefined])
    expect(pricedRows({ ...equipment, columns: [column("item", "item")] }, grid, typed, output)).toEqual([undefined, undefined, undefined, undefined])
    expect(pricedRows(equipment, grid, [], output)).toEqual([undefined, undefined, undefined, undefined])
  })
})
