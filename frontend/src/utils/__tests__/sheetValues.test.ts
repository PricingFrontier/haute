/**
 * The values typed into a sheet (specs/workbench): filled rows as the server counts them,
 * the rows a column gives a table as the server reads them, numbers as it types them, what
 * breaks a column's rules, the basis pricing depends on, and a priced output table's rows
 * lined up with a grid's rows by key.
 */
import { describe, expect, it } from "vitest"
import type { SchemaColumn, SchemaTable } from "../../api/types"
import {
  cellKey,
  cellProblem,
  filled,
  formattedNumber,
  newQuoteValues,
  parseTypedNumber,
  presetColumn,
  presetRows,
  pricedRows,
  quoteFilled,
  quoteProblems,
  quoteTyped,
  rowFilled,
  valuesBasis,
  withPresetRows,
} from "../sheetValues"
import { columnKey, quotedSchema } from "../sheetVisibility"
import { BLANK_THEME, pricingBasis, type SampleRow } from "../workbenchForm"
import presetCases from "./fixtures/presetRows.json"

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
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
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

describe("a new quote", () => {
  // Policy details with a default state and a tick; equipment, an output and the index too,
  // whose defaults a new quote never takes.
  const detailed: SchemaTable = {
    ...policy,
    columns: [column("state", "state", { options: ["CA", "NY"], default_value: "NY" }), column("years", "years", { type: "int" }), column("vat", "vat", { type: "bool", default_value: true })],
  }
  const listed: SchemaTable = { ...equipment, columns: equipment.columns.map((c) => (c.id === "item" ? { ...c, default_value: "Crane" } : c)) }
  const priced: SchemaTable = { ...premiums, rows: "one", columns: [column("premium", "premium", { type: "float", default_value: "100" })] }
  const schema = { tables: [detailed, listed, priced] }

  it("starts with each one-row input table's row holding its columns' defaults, and nothing else", () => {
    expect(newQuoteValues(schema)).toEqual({ policy: [{ state: "NY", vat: true }] })
    // The same for the same schema, so a sheet reads its visibility once.
    expect(newQuoteValues(schema)).toBe(newQuoteValues(schema))
    expect(newQuoteValues({ tables: [policy, equipment] })).toEqual({})
  })

  it("counts nothing typed while the quote holds its defaults alone, and anything typed over them", () => {
    const start = newQuoteValues(schema)
    expect(quoteFilled(schema, start)).toBe(true)
    expect(quoteTyped(schema, start)).toBe(false)
    // A default cleared or unticked is not typing either; a value typed over one, or
    // anywhere else, is.
    expect(quoteTyped(schema, { policy: [{ state: "", vat: false }] })).toBe(false)
    expect(quoteTyped(schema, { policy: [{ state: "CA", vat: true }] })).toBe(true)
    expect(quoteTyped(schema, { policy: [{ ...start.policy[0], years: "3" }] })).toBe(true)
    // A many-row table's column takes no default, so its value is typing.
    expect(quoteTyped(schema, { ...start, equipment: [{ item: "Crane" }] })).toBe(true)
  })
})

describe("a row for each value", () => {
  // The cases the server's preset_rows holds itself to as well.
  type Case = {
    name: string
    table: { role: SchemaTable["role"]; rows: SchemaTable["rows"]; columns: (Partial<SchemaColumn> & { id: string; name: string })[] }
    rows: SampleRow[]
    read: SampleRow[]
  }
  const cases = presetCases.cases as unknown as Case[]
  const tableOf = ({ table }: Case): SchemaTable => ({
    id: "t_cover",
    name: "coverage",
    role: table.role,
    rows: table.rows,
    columns: table.columns.map(({ id, name, ...rest }) => column(id, name, rest)),
  })

  it.each(cases.map((one) => [one.name, one] as const))("%s", (_, one) => {
    expect(presetRows(tableOf(one), one.rows)).toEqual(one.read)
  })

  const coverage: SchemaTable = {
    id: "coverage",
    name: "coverage",
    role: "input",
    rows: "many",
    columns: [column("peril", "peril", { options: ["AOP", "EQ"], row_per_value: true, key: true }), column("limit", "limit", { type: "float" })],
  }

  it("names the column giving a table its rows, the first that may", () => {
    expect(presetColumn(coverage)?.id).toBe("peril")
    expect(presetColumn(equipment)).toBeNull()
  })

  it("reads each table a column gives rows through them, and leaves the values as they are when none has one", () => {
    const values = { policy: [{ state: "CA" }], coverage: [{ peril: "EQ", limit: "5" }] }
    expect(withPresetRows({ tables: [policy, coverage] }, values)).toEqual({
      policy: [{ state: "CA" }],
      coverage: [{ peril: "AOP" }, { peril: "EQ", limit: "5" }],
    })
    expect(withPresetRows({ tables: [policy, coverage] }, {}).coverage).toEqual([{ peril: "AOP" }, { peril: "EQ" }])
    expect(withPresetRows({ tables: [policy, equipment] }, values)).toBe(values)
  })

  it("counts every row a column gives as filled, as the server sends each, but its value is not typing", () => {
    const schema = { tables: [policy, coverage] }
    const given = withPresetRows(schema, {})
    expect(given.coverage.every((row) => rowFilled(coverage, row))).toBe(true)
    expect(quoteFilled(schema, given)).toBe(false)
    expect(quoteTyped(schema, given)).toBe(false)
    expect(quoteProblems(schema, given)).toEqual({ [cellKey("policy", 0, "state")]: "Required" })
    const typed: Record<string, SampleRow[]> = { coverage: [{ peril: "AOP" }, { peril: "EQ", limit: "5" }] }
    expect(quoteFilled(schema, typed)).toBe(true)
    expect(quoteTyped(schema, typed)).toBe(true)
  })

  it("counts nothing typed in a row left out, its value no longer one the column gives, as the server leaves it out", () => {
    const schema = { tables: [policy, coverage] }
    const gone: Record<string, SampleRow[]> = { coverage: [{ peril: "WD", limit: "5000" }] }

    expect(quoteFilled(schema, gone)).toBe(false)
    expect(quoteTyped(schema, gone)).toBe(false)
  })
})

describe("formattedNumber", () => {
  it("shows a number column's values in its format, as an Explore pivot does, typed text read as the server reads it", () => {
    const money = column("c", "premium", { type: "float", number_format: "currency_gbp" })
    expect(formattedNumber(money, 1234.5)).toBe("£1,234.50")
    expect(formattedNumber(money, "£1,234.5")).toBe("£1,234.50")
    expect(formattedNumber(column("c", "rate", { type: "float", number_format: "percent", decimal_places: 1 }), "0.125")).toBe("12.5%")
    expect(formattedNumber(column("c", "count", { type: "int", decimal_places: 0, use_grouping: false }), 12345.6)).toBe("12346")
  })

  it("leaves a value as it is in General, outside a number column, or when it is not a number", () => {
    expect(formattedNumber(column("c", "premium", { type: "float" }), "1000")).toBeNull()
    expect(formattedNumber(column("c", "name", { number_format: "number" }), "1000")).toBeNull()
    expect(formattedNumber(column("c", "premium", { type: "float", number_format: "number" }), "lots")).toBeNull()
  })
})

describe("cellProblem", () => {
  it("reads numbers as the server does, a currency sign, separators and spaces allowed", () => {
    expect(parseTypedNumber("$1,250,000.50")).toBe(1250000.5)
    expect(parseTypedNumber(" -3e2 ")).toBe(-300)
    expect(parseTypedNumber(".5")).toBe(0.5)
    expect([parseTypedNumber("lots"), parseTypedNumber("$"), parseTypedNumber("1_000"), parseTypedNumber("0x10"), parseTypedNumber("1.2.3")]).toEqual([null, null, null, null, null])
    // The server keeps a number too large to be finite as the text typed.
    expect(parseTypedNumber("1e999")).toBeNull()
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

  it("takes a Date as a real day written YYYY-MM-DD, as the server reads one", () => {
    const inception = column("c", "inception", { type: "date" })
    expect(cellProblem(inception, "2026-02-28")).toBeNull()
    expect(cellProblem(inception, "")).toBeNull()
    // Text a column retyped to a Date may still hold.
    expect(cellProblem(inception, "not-a-date")).toBe("Not a date")
    expect(cellProblem(inception, "2026-02-30")).toBe("Not a date")
    expect(cellProblem(inception, "28/02/2026")).toBe("Not a date")
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
  it("is what pricing the sample depends on, the schema, the columns hidden and the values", () => {
    const form = { version: 1 as const, name: "x", theme: { ...BLANK_THEME }, schema: { tables: [policy] }, pages: [], sample: { policy: [{ state: "NY" }] } }
    expect(pricingBasis(form)).toBe(valuesBasis(form.schema, new Set(), form.sample))
    expect(valuesBasis(form.schema, new Set(), { policy: [{ state: "CA" }] })).not.toBe(pricingBasis(form))
    expect(valuesBasis(form.schema, new Set(["policy:state"]), form.sample)).not.toBe(pricingBasis(form))
    // The columns hidden are a set: the order they were found in changes nothing.
    expect(valuesBasis(form.schema, new Set(["b", "a"]), {})).toBe(valuesBasis(form.schema, new Set(["a", "b"]), {}))
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

describe("reading through a quoted schema", () => {
  // The values the sheets hide are left out of the quote, as if never typed.
  const quoted = quotedSchema({ tables: [policy, equipment, premiums] }, new Set([columnKey("policy", "state"), columnKey("equipment", "value")]))

  it("does not count a value typed only in a hidden column", () => {
    expect(quoteFilled(quoted, { equipment: [{ value: "100" }] })).toBe(false)
    expect(quoteFilled(quoted, { equipment: [{ value: "100", item: "Crane" }] })).toBe(true)
  })

  it("never checks a hidden cell, a required one included", () => {
    expect(quoteProblems(quoted, { policy: [{ years: "5" }], equipment: [{ item: "Crane", value: "lots" }] })).toEqual({})
  })

  it("does not line up a grid row filled only in a hidden column", () => {
    const input = quoted.tables[1]
    expect(pricedRows(input, [{ value: "100" }, { item: "Crane" }], [{ row_number: 2, item: "Crane" }], [{ row_number: 2, premium: 20 }])).toEqual([
      undefined,
      { row_number: 2, premium: 20 },
    ])
  })
})
