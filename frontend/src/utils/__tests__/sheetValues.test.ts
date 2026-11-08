/**
 * The values typed into a sheet (specs/workbench): filled rows as the server counts them,
 * the rows an index of values gives a table as the server reads them, numbers as it types
 * them, what breaks a column's rules and a typed index's, the basis pricing depends on, and a
 * priced output table's rows lined up with a grid's rows by index.
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
  parseTypedWhole,
  indexProblemCells,
  presetRows,
  pricedRows,
  quoteProblems,
  quoteTyped,
  rowFilled,
  typedIndexProblems,
  valuesBasis,
  valuesIndex,
  withPresetRows,
} from "../sheetValues"
import { columnKey, quotedSchema } from "../sheetVisibility"
import { answeredOutputs, BLANK_THEME, linedUpOutputs, pricingBasis, type SampleRow } from "../workbenchForm"
import presetCases from "./fixtures/presetRows.json"
import typedCases from "./fixtures/typedText.json"

const column = (name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  name,
  type: "str",
  label: "",
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  index: null,
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
  name: "equipment",
  rows: "many",
  outputs: [],
  inputs: [
    column("row_number", { type: "int", index: "numbered" }),
    column("item", { required: true }),
    column("value", { type: "float", min: 0, max: 1_000_000 }),
  ],
}
const policy: SchemaTable = {
  name: "policy",
  rows: "one",
  outputs: [],
  inputs: [column("state", { options: ["CA", "NY"], required: true }), column("years", { type: "int", min: 1, max: 200 })],
}
const premiums: SchemaTable = {
  name: "premiums",
  rows: "many",
  inputs: [],
  outputs: [column("row_number", { type: "int", index: "numbered" }), column("premium", { type: "float", required: true })],
}

// Sites keyed by the name the underwriter types on each row.
const sites: SchemaTable = {
  name: "sites",
  rows: "many",
  outputs: [],
  inputs: [column("site_name", { index: "typed" }), column("site_value", { type: "float" }), column("site_note")],
}
// A row for each peril a params file lists, keyed by the peril.
const coverage: SchemaTable = {
  name: "coverage",
  rows: "many",
  outputs: [],
  inputs: [column("peril", { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, index: "values" }), column("limit", { type: "float" })],
}

describe("filled rows", () => {
  it("counts text other than blank and a tick as typed, a row holding anything but a numbered index as the server sends one", () => {
    expect([filled("x"), filled(" "), filled(""), filled(true), filled(false), filled(undefined)]).toEqual([true, false, false, true, false, false])
    expect(rowFilled(equipment, { item: "Crane" })).toBe(true)
    expect(rowFilled(equipment, { row_number: "7" })).toBe(false)
    expect(rowFilled(equipment, {})).toBe(false)
    expect(rowFilled(equipment, undefined)).toBe(false)
    // A typed index's value, and the value an index of values gives a row, make a row the server sends.
    expect(rowFilled(sites, { site_name: "Leeds" })).toBe(true)
    expect(rowFilled(coverage, { peril: "AOP" })).toBe(true)
  })

  it("counts a typed index's value as typing, and neither a numbered index's nor an index of values'", () => {
    expect(quoteTyped({ tables: [sites] }, { sites: [{ site_name: "Leeds" }] })).toBe(true)
    expect(quoteTyped({ tables: [equipment] }, { equipment: [{ row_number: "1" }] })).toBe(false)
    expect(quoteTyped({ tables: [coverage] }, { coverage: [{ peril: "AOP" }, { peril: "EQ" }] })).toBe(false)
  })
})

describe("a new quote", () => {
  // Policy details with a default state and a tick; equipment, an output and the index too,
  // whose defaults a new quote never takes.
  const detailed: SchemaTable = {
    ...policy,
    inputs: [column("state", { options: ["CA", "NY"], default_value: "NY" }), column("years", { type: "int" }), column("vat", { type: "bool", default_value: true })],
  }
  const listed: SchemaTable = { ...equipment, inputs: equipment.inputs.map((c) => (c.name === "item" ? { ...c, default_value: "Crane" } : c)) }
  const priced: SchemaTable = { ...premiums, rows: "one", outputs: [column("premium", { type: "float", default_value: "100" })] }
  const schema = { tables: [detailed, listed, priced] }

  it("starts with each one-row input table's row holding its columns' defaults, and nothing else", () => {
    expect(newQuoteValues(schema)).toEqual({ policy: [{ state: "NY", vat: true }] })
    // The same for the same schema, so a sheet reads its visibility once.
    expect(newQuoteValues(schema)).toBe(newQuoteValues(schema))
    expect(newQuoteValues({ tables: [policy, equipment] })).toEqual({})
  })

  it("counts nothing typed while the quote holds its defaults alone, and anything typed over them", () => {
    const start = newQuoteValues(schema)
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
    table: { rows: SchemaTable["rows"]; inputs: (Partial<SchemaColumn> & { name: string })[]; outputs: (Partial<SchemaColumn> & { name: string })[] }
    rows: SampleRow[]
    read: SampleRow[]
  }
  const cases = presetCases.cases as unknown as Case[]
  const tableOf = ({ table }: Case): SchemaTable => ({
    name: "coverage",
    rows: table.rows,
    inputs: table.inputs.map(({ name, ...rest }) => column(name, rest)),
    outputs: table.outputs.map(({ name, ...rest }) => column(name, rest)),
  })

  it.each(cases.map((one) => [one.name, one] as const))("%s", (_, one) => {
    expect(presetRows(tableOf(one), one.rows)).toEqual(one.read)
  })

  it("names a table's index of values, which gives its rows, and no other index", () => {
    expect(valuesIndex(coverage)?.name).toBe("peril")
    expect(valuesIndex(equipment)).toBeNull()
    expect(valuesIndex(sites)).toBeNull()
    // A table of outputs alone holds its index among its outputs, and has no rows to give.
    expect(valuesIndex({ ...coverage, inputs: [], outputs: coverage.inputs })).toBeNull()
  })

  it("reads each table an index of values gives rows through them, and leaves the values as they are when none has one", () => {
    const values = { policy: [{ state: "CA" }], coverage: [{ peril: "EQ", limit: "5" }] }
    expect(withPresetRows({ tables: [policy, coverage] }, values)).toEqual({
      policy: [{ state: "CA" }],
      coverage: [{ peril: "AOP" }, { peril: "EQ", limit: "5" }],
    })
    expect(withPresetRows({ tables: [policy, coverage] }, {}).coverage).toEqual([{ peril: "AOP" }, { peril: "EQ" }])
    expect(withPresetRows({ tables: [policy, equipment] }, values)).toBe(values)
  })

  it("counts every row an index of values gives as filled, as the server sends each, but its value is not typing", () => {
    const schema = { tables: [policy, coverage] }
    const given = withPresetRows(schema, {})
    expect(given.coverage.every((row) => rowFilled(coverage, row))).toBe(true)
    expect(quoteTyped(schema, given)).toBe(false)
    expect(quoteProblems(schema, given)).toEqual({ [cellKey("policy", 0, "state")]: "Required" })
    const typed: Record<string, SampleRow[]> = { coverage: [{ peril: "AOP" }, { peril: "EQ", limit: "5" }] }
    expect(quoteTyped(schema, typed)).toBe(true)
  })

  it("counts nothing typed in a row left out, its value no longer one the index gives, as the server leaves it out", () => {
    const schema = { tables: [policy, coverage] }
    const gone: Record<string, SampleRow[]> = { coverage: [{ peril: "WD", limit: "5000" }] }

    expect(quoteTyped(schema, gone)).toBe(false)
  })
})

describe("formattedNumber", () => {
  it("shows a number column's values in its format, as an Explore pivot does, typed text read as the server reads it", () => {
    const money = column("premium", { type: "float", number_format: "currency_gbp" })
    expect(formattedNumber(money, 1234.5)).toBe("£1,234.50")
    expect(formattedNumber(money, "£1,234.5")).toBe("£1,234.50")
    expect(formattedNumber(column("rate", { type: "float", number_format: "percent", decimal_places: 1 }), "0.125")).toBe("12.5%")
    expect(formattedNumber(column("count", { type: "int", decimal_places: 0, use_grouping: false }), 12345.6)).toBe("12346")
  })

  it("leaves a value as it is in General, outside a number column, or when it is not a number", () => {
    expect(formattedNumber(column("premium", { type: "float" }), "1000")).toBeNull()
    expect(formattedNumber(column("name", { number_format: "number" }), "1000")).toBeNull()
    expect(formattedNumber(column("premium", { type: "float", number_format: "number" }), "lots")).toBeNull()
  })
})

describe("typed text, as the server reads it", () => {
  it.each(typedCases.numbers)("reads $name as the server does", ({ text, number, whole, integer }) => {
    expect(parseTypedNumber(text)).toBe(number)
    expect(parseTypedNumber(` ${text} `)).toBe(number)
    // An Integer column's whole number, exactly however large.
    expect(parseTypedWhole(text)).toBe(whole && number !== null ? BigInt(integer ?? number) : null)
  })

  it.each(typedCases.filled)("counts $name as typed as the server does", ({ value, filled: typed }) => {
    expect(filled(value ?? undefined)).toBe(typed)
  })
})

describe("cellProblem", () => {

  it("requires a value, a number of the column's type within its range, and one of the allowed values", () => {
    const value = equipment.inputs[2]
    expect(cellProblem(equipment.inputs[1], "")).toBe("Required")
    expect(cellProblem(equipment.inputs[1], "  ")).toBe("Required")
    expect(cellProblem(value, "")).toBeNull()
    expect(cellProblem(value, "lots")).toBe("Not a number")
    expect(cellProblem(value, "-1")).toBe("At least 0")
    expect(cellProblem(value, "$1,000,001")).toBe("At most 1,000,000")
    expect(cellProblem(value, "$999,999")).toBeNull()
    expect(cellProblem(policy.inputs[1], "1.5")).toBe("Not a whole number")
    expect(cellProblem(policy.inputs[1], "2.0")).toBeNull()
    expect(cellProblem(policy.inputs[0], "TX")).toBe("Not one of the allowed values")
    expect(cellProblem(policy.inputs[0], "NY")).toBeNull()
  })

  it("takes a Date as a real day written YYYY-MM-DD, as the server reads one", () => {
    const inception = column("inception", { type: "date" })
    expect(cellProblem(inception, "2026-02-28")).toBeNull()
    expect(cellProblem(inception, "")).toBeNull()
    // Text a column retyped to a Date may still hold.
    expect(cellProblem(inception, "not-a-date")).toBe("Not a date")
    expect(cellProblem(inception, "2026-02-30")).toBe("Not a date")
    expect(cellProblem(inception, "28/02/2026")).toBe("Not a date")
  })

  it("never faults a tick box, a numbered index or an index of values, and holds a typed index to its type", () => {
    expect(cellProblem(column("c", { type: "bool", required: true }), undefined)).toBeNull()
    expect(cellProblem(equipment.inputs[0], "x")).toBeNull()
    expect(cellProblem(column("peril", { type: "int", options: ["1", "2"], index: "values" }), "x")).toBeNull()
    expect(cellProblem(column("site_number", { type: "int", index: "typed" }), "x")).toBe("Not a number")
    expect(cellProblem(column("site_number", { type: "int", index: "typed" }), "1.5")).toBe("Not a whole number")
    // Blank, it is the table's rows that say whether it must hold a value (`typedIndexProblems`).
    expect(cellProblem(sites.inputs[0], "")).toBeNull()
  })

  it("marks a value of another type left in a column since retyped, which the server refuses", () => {
    expect(cellProblem(column("c"), true)).toBe("A tick, not a value")
    expect(cellProblem(column("c", { type: "float", required: true }), true)).toBe("A tick, not a value")
    expect(cellProblem(column("c", { type: "bool" }), "yes")).toBe("Not a tick")
    // An unticked box and blank text are nothing the server sends.
    expect(cellProblem(column("c"), false)).toBeNull()
    expect(cellProblem(column("c", { type: "bool" }), " ")).toBeNull()
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

describe("typedIndexProblems", () => {
  const schema = { tables: [policy, equipment, sites, coverage] }

  it("names a row holding something but no index, by the row the sheet numbers, and passes a blank row", () => {
    const problems = typedIndexProblems(schema, { sites: [{ site_name: "Leeds" }, {}, { site_name: "York" }, { site_value: "5" }, { site_note: " " }] })
    expect(problems).toEqual([{ table: "sites", column: "site_name", rows: [4], message: "sites's row 4 holds no site_name" }])
    expect(indexProblemCells(problems)).toEqual({ [cellKey("sites", 3, "site_name")]: "sites's row 4 holds no site_name" })
  })

  it("names the rows holding one value, compared as typed, the value as the server says it", () => {
    const rows: Record<string, SampleRow[]> = { sites: [{ site_name: "Leeds" }, { site_name: "York" }, { site_name: " Leeds " }, { site_name: "Leeds", site_value: "2" }] }
    expect(typedIndexProblems(schema, rows).map((problem) => [problem.rows, problem.message])).toEqual([
      [[1, 3, 4], 'sites\'s rows 1, 3 and 4 hold site_name "Leeds"; each row\'s site_name is its own'],
    ])
    // An Integer index's values are numbers: 7 and 07 are one, written as the number.
    const numbered: SchemaTable = { ...sites, inputs: [column("site_number", { type: "int", index: "typed" }), column("site_value", { type: "float" })] }
    expect(typedIndexProblems({ tables: [numbered] }, { sites: [{ site_number: "7" }, { site_number: "07" }, { site_number: "8" }] }).map((problem) => problem.message)).toEqual([
      "sites's rows 1 and 2 hold site_number 7; each row's site_number is its own",
    ])
    // Text is compared as typed, its case and all.
    expect(typedIndexProblems(schema, { sites: [{ site_name: "Leeds" }, { site_name: "leeds" }] })).toEqual([])
  })

  it("names a Text index beyond either portable character or UTF-16 limit", () => {
    const long = "x".repeat(256)
    expect(typedIndexProblems(schema, { sites: [{ site_name: "x".repeat(255) }, { site_name: long }] }).map((problem) => problem.message)).toEqual([
      "sites's row 2 holds a site_name longer than 255 characters",
    ])
    // Supplementary characters use two of SQL Server's UTF-16 units apiece.
    expect(typedIndexProblems(schema, { sites: [{ site_name: "\u{1F600}".repeat(183) }] })).toEqual([])
    expect(typedIndexProblems(schema, { sites: [{ site_name: "\u{1F600}".repeat(184) }] }).map((problem) => problem.message)).toEqual([
      "sites's row 1 holds a site_name longer than 366 UTF-16 code units",
    ])
  })

  it("names each kind in the server's order, blank rows first, and none for an index Haute or the params file gives", () => {
    const rows: Record<string, SampleRow[]> = { sites: [{ site_name: "x".repeat(300) }, { site_value: "1" }, { site_name: "York" }, { site_name: "York" }], equipment: [{ item: "Crane" }], coverage: [{ limit: "5" }] }
    expect(typedIndexProblems(schema, rows).map((problem) => problem.message)).toEqual([
      "sites's row 2 holds no site_name",
      'sites\'s rows 3 and 4 hold site_name "York"; each row\'s site_name is its own',
      "sites's row 1 holds a site_name longer than 255 characters",
    ])
  })

  it("reads a quoted schema's rows: a value only in a hidden column holds nothing, the index kept whatever hides", () => {
    const quoted = quotedSchema(schema, new Set([columnKey("sites", "site_value"), columnKey("sites", "site_name")]))
    expect(quoted.tables[2].inputs.map((each) => each.name)).toEqual(["site_name", "site_note"])
    expect(typedIndexProblems(quoted, { sites: [{ site_value: "5" }, { site_note: "n", site_name: "" }] }).map((problem) => problem.message)).toEqual([
      "sites's row 2 holds no site_name",
    ])
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

  const keys = { typed: "row_number", answer: "row_number" }

  it("lines a priced table's rows up with the grid's by index, skipping rows with nothing typed and rows no output matches", () => {
    expect(pricedRows(equipment, keys, grid, typed, output)).toEqual([
      { row_number: 1, premium: 10 },
      undefined,
      { row_number: 3, premium: null },
      { row_number: 4, premium: 40 },
    ])
    expect(pricedRows(equipment, keys, grid, typed, output.slice(0, 1))).toEqual([undefined, undefined, undefined, { row_number: 4, premium: 40 }])
  })

  it("matches the answer's index under its own name, as text, as the server typed the row's, and nothing without an index or typed rows", () => {
    // Two indexes of one params file's column line up whatever each is named.
    const answer = [{ cover: "EQ", rate: 2 }, { cover: "1", rate: 1 }]
    expect(pricedRows(coverage, { typed: "peril", answer: "cover" }, [{ peril: "1" }, { peril: "EQ" }], [{ peril: 1 }, { peril: "EQ" }], answer)).toEqual([
      { cover: "1", rate: 1 },
      { cover: "EQ", rate: 2 },
    ])
    // An index the output row lacks, or holds null, matches nothing.
    expect(pricedRows(equipment, keys, [{ item: "Crane" }], [{ row_number: 1, item: "Crane" }], [{ premium: 3 }, { row_number: null, premium: 4 }])).toEqual([undefined])
    expect(pricedRows(equipment, null, grid, typed, output)).toEqual([undefined, undefined, undefined, undefined])
    expect(pricedRows(equipment, keys, grid, [], output)).toEqual([undefined, undefined, undefined, undefined])
  })

  it("gives each row an index of values gives the next typed row, its value alone included, as the server sends each", () => {
    const grid = presetRows(coverage, [{ peril: "EQ", limit: "5" }])
    const sent = [{ peril: "AOP" }, { peril: "EQ", limit: 5 }]
    expect(pricedRows(coverage, { typed: "peril", answer: "peril" }, grid, sent, [{ peril: "EQ", rate: 2 }, { peril: "AOP", rate: 1 }])).toEqual([
      { peril: "AOP", rate: 1 },
      { peril: "EQ", rate: 2 },
    ])
  })
})

describe("a table of both blocks lined up by its key", () => {
  // An equipment schedule's items and each item's premium: one table, keyed by its index.
  const schedule: SchemaTable = { ...equipment, outputs: [column("premium", { type: "float" })] }
  const form = (tables: SchemaTable[], sample: Record<string, SampleRow[]> = {}) => ({
    version: 1 as const,
    name: "lined",
    theme: BLANK_THEME,
    schema: { tables },
    pages: [],
    sample,
  })

  it("lines its own answer up by the key value of each typed row, whatever order the answer comes in", () => {
    const grid: SampleRow[] = [{ item: "Crane" }, {}, { item: "Paver" }]
    const price = {
      sample: { equipment: [{ row_number: 1, item: "Crane" }, { row_number: 3, item: "Paver" }] },
      tables: { equipment: [{ row_number: 3, premium: 30 }, { row_number: 1, premium: 10 }] },
    }
    const lined = linedUpOutputs([schedule], [schedule], { equipment: grid }, 3, price, { tables: [schedule] })
    expect(lined.get("equipment")).toEqual([{ row_number: 1, premium: 10 }, undefined, { row_number: 3, premium: 30 }])
    // A typed row the answer gives no row has no outputs.
    const partial = linedUpOutputs([schedule], [schedule], { equipment: grid }, 3, { ...price, tables: { equipment: [{ row_number: 3, premium: 30 }] } }, { tables: [schedule] })
    expect(partial.get("equipment")).toEqual([undefined, undefined, { row_number: 3, premium: 30 }])
  })

  it("lines a table keyed by its index of values up by that value, a row holding its value alone and one typed lining up as the server's rows do", () => {
    const perils: SchemaTable = {
      name: "perils",
      rows: "many",
      inputs: [
        column("peril", { options: ["AOP", "EQ", "NW"], options_source: { file: "perils.csv", column: "peril" }, index: "values" }),
        column("limit", { type: "float" }),
      ],
      outputs: [column("rate", { type: "float" })],
    }
    // The grid shows the rows the index gives; the server sends each, its value alone included.
    const grid = presetRows(perils, [{ peril: "EQ", limit: "5" }])
    expect(grid).toEqual([{ peril: "AOP" }, { peril: "EQ", limit: "5" }, { peril: "NW" }])
    const price = {
      sample: { perils: [{ peril: "AOP" }, { peril: "EQ", limit: 5 }, { peril: "NW" }] },
      tables: { perils: [{ peril: "NW", rate: 0.3 }, { peril: "AOP", rate: 0.1 }, { peril: "EQ", rate: 0.2 }] },
    }
    const lined = linedUpOutputs([perils], [perils], { perils: grid }, 3, price, { tables: [perils] })
    expect(lined.get("perils")).toEqual([{ peril: "AOP", rate: 0.1 }, { peril: "EQ", rate: 0.2 }, { peril: "NW", rate: 0.3 }])
  })

  it("lines a table keyed by its typed index up by the value typed on each row", () => {
    const priced: SchemaTable = { ...sites, outputs: [column("site_premium", { type: "float" })] }
    const grid: SampleRow[] = [{ site_name: "York" }, {}, { site_name: "Leeds", site_value: "5" }]
    const price = {
      sample: { sites: [{ site_name: "York" }, { site_name: "Leeds", site_value: 5 }] },
      tables: { sites: [{ site_name: "Leeds", site_premium: 50 }, { site_name: "York", site_premium: 10 }] },
    }
    const lined = linedUpOutputs([priced], [priced], { sites: grid }, 3, price, { tables: [priced] })
    expect(lined.get("sites")).toEqual([{ site_name: "York", site_premium: 10 }, undefined, { site_name: "Leeds", site_premium: 50 }])
  })

  it("keeps a hidden index in the quoted schema, as it keys the table", () => {
    const perils: SchemaTable = {
      name: "perils",
      rows: "many",
      inputs: [
        column("peril", { options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, index: "values" }),
        column("limit", { type: "float" }),
        column("note"),
      ],
      outputs: [column("rate", { type: "float" })],
    }
    const quoted = quotedSchema({ tables: [perils] }, new Set([columnKey("perils", "peril"), columnKey("perils", "limit")]))
    expect(quoted.tables[0].inputs.map((each) => each.name)).toEqual(["peril", "note"])
    expect(valuesIndex(quoted.tables[0])?.name).toBe("peril")
    // The schema's basis, which a pricing is for, still holds the column.
    expect(pricingBasis(form([perils]))).toContain("peril")
  })
})

describe("tables of outputs alone lined up in one Table", () => {
  // Two breakdowns keyed by a typed peril, each with an ordinary output number.
  const identity = [column("quote_reference"), column("quote_option", { type: "int" })]
  const perilPremiums: SchemaTable = {
    name: "peril_premiums",
    rows: "many",
    inputs: [],
    outputs: [...identity, column("peril", { index: "typed" }), column("premium_number", { type: "int" }), column("premium", { type: "float" })],
  }
  const perilShares: SchemaTable = {
    name: "peril_shares",
    rows: "many",
    inputs: [],
    outputs: [...identity, column("peril", { index: "typed" }), column("row_number", { type: "int" }), column("share", { type: "float" })],
  }

  it("matches each answer by its index, preserving the other output values when rows arrive in a different order", () => {
    const price = {
      sample: {},
      tables: {
        peril_premiums: [
          { premium_number: 1, peril: "EQ", premium: 20 },
          { premium_number: 2, peril: "AOP", premium: 10 },
        ],
        peril_shares: [
          { row_number: 1, peril: "AOP", share: 0.4 },
          { row_number: 2, peril: "FL", share: 0.1 },
          { row_number: 3, peril: "EQ", share: 0.6 },
        ],
      },
    }
    const lined = answeredOutputs([perilPremiums, perilShares], price)

    // The first's rows as pricing gave them.
    expect(lined.get("peril_premiums")?.map((row) => [row?.peril, row?.premium_number])).toEqual([
      ["EQ", 1],
      ["AOP", 2],
    ])
    // The other's rows matched by the peril, each holding its own output number.
    expect(lined.get("peril_shares")?.map((row) => [row?.peril, row?.row_number, row?.share])).toEqual([
      ["EQ", 3, 0.6],
      ["AOP", 1, 0.4],
    ])
    // Before a price nothing is lined up, so the grid shows its dashes.
    expect(answeredOutputs([perilPremiums, perilShares], null)).toEqual(new Map())
  })
})

describe("reading through a quoted schema", () => {
  // The values the sheets hide are left out of the quote, as if never typed.
  const quoted = quotedSchema({ tables: [policy, equipment, premiums] }, new Set([columnKey("policy", "state"), columnKey("equipment", "value")]))

  it("does not count a value typed only in a hidden column", () => {
    expect(quoteTyped(quoted, { equipment: [{ value: "100" }] })).toBe(false)
    expect(quoteTyped(quoted, { equipment: [{ value: "100", item: "Crane" }] })).toBe(true)
  })

  it("never checks a hidden cell, a required one included", () => {
    expect(quoteProblems(quoted, { policy: [{ years: "5" }], equipment: [{ item: "Crane", value: "lots" }] })).toEqual({})
  })

  it("does not line up a grid row filled only in a hidden column", () => {
    const input = quoted.tables[1]
    expect(pricedRows(input, { typed: "row_number", answer: "row_number" }, [{ value: "100" }, { item: "Crane" }], [{ row_number: 2, item: "Crane" }], [{ row_number: 2, premium: 20 }])).toEqual([
      undefined,
      { row_number: 2, premium: 20 },
    ])
  })
})
