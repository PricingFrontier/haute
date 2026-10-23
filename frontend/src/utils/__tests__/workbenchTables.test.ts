/**
 * The copy rules for a Workbench Input, whose tables and sample quote the project's
 * workbench supplies, and a Workbench Output, whose response tables it supplies
 * (specs/workbench): how a copy is read, which tables are ports, when a copy needs
 * updating, and what each starts with from the palette.
 */
import { describe, expect, it } from "vitest"
import {
  paletteWorkbenchInputConfig,
  paletteWorkbenchOutputConfig,
  quoteTablesPatch,
  readWorkbenchTables,
  responseTablesPatch,
  workbenchTablePorts,
  dtypeFits,
  type WorkbenchTables,
} from "../workbenchTables"

const table = { name: "policy_details", rows: "one" as const, columns: [{ name: "state", type: "str" as const }] }
const sample = { policy_details: { state: "NY" } }
const responseTable = { name: "layers", rows: "many" as const, columns: [{ name: "layer", type: "int" as const }] }
const fetched: WorkbenchTables = {
  tables: [table],
  sample,
  responseTables: [responseTable],
  fetch: 3,
}

describe("readWorkbenchTables", () => {
  it("reads each table's name, rows and typed columns, leaving out what is not one", () => {
    const config = {
      tables: [
        table,
        { name: "equipment", rows: "many", columns: [{ name: "value", type: "float" }, { name: "x", type: "text" }, 5] },
        { name: "bare", rows: "one", columns: [] },
        { path: "$[:]", label: "old", emit: true, columns: [] },
        { name: "neither", rows: "some", columns: [] },
        "policy",
        null,
      ],
    }

    expect(readWorkbenchTables(config)).toEqual([
      table,
      { name: "equipment", rows: "many", columns: [{ name: "value", type: "float" }] },
      { name: "bare", rows: "one", columns: [] },
    ])
    expect(readWorkbenchTables({})).toEqual([])
    expect(readWorkbenchTables(undefined)).toEqual([])
  })
})

describe("workbenchTablePorts", () => {
  it("makes each table with a column a port, by name, in order, once", () => {
    const config = {
      tables: [
        responseTable,
        { name: "bare", rows: "one", columns: [] },
        table,
        { name: "layers", rows: "one", columns: [{ name: "again", type: "int" }] },
      ],
    }

    expect(workbenchTablePorts(config)).toEqual(["layers", "policy_details"])
    expect(workbenchTablePorts({})).toEqual([])
  })
})

describe("quoteTablesPatch", () => {
  it("updates a copy whose tables or sample differ from the workbench's, with both", () => {
    expect(quoteTablesPatch({ tables: [], sample }, fetched)).toEqual({ tables: [table], sample })
    expect(quoteTablesPatch({}, fetched)).toEqual({ tables: [table], sample })
    expect(quoteTablesPatch({ tables: [table], sample: {} }, fetched)).toEqual({ tables: [table], sample })
  })

  it("leaves a copy that matches alone, whatever the order of its keys", () => {
    const reordered = Object.fromEntries(Object.entries(table).reverse())
    expect(quoteTablesPatch({ tables: [reordered], sample }, fetched)).toBeNull()
  })

  it("reads a copy without a sample as one with an empty sample", () => {
    expect(quoteTablesPatch({ tables: [table] }, { ...fetched, sample: {} })).toBeNull()
  })

  it("changes nothing before a fetch", () => {
    expect(quoteTablesPatch({ tables: [] }, null)).toBeNull()
  })

  it("gives the config its own copy of the fetched tables and sample", () => {
    expect(quoteTablesPatch({}, fetched)?.tables[0]).not.toBe(table)
    expect(quoteTablesPatch({}, fetched)?.sample.policy_details).not.toBe(sample.policy_details)
  })
})

describe("paletteWorkbenchInputConfig", () => {
  it("starts a Workbench Input with the newest tables and sample fetched, or none until they are", () => {
    const defaults = { tables: [] }

    expect(paletteWorkbenchInputConfig(defaults, fetched)).toEqual({ tables: [table], sample })
    expect(paletteWorkbenchInputConfig(defaults, null)).toEqual({ tables: [], sample: {} })
    expect(paletteWorkbenchInputConfig(defaults, fetched).tables).not.toBe(fetched.tables)
  })
})

describe("the response's tables", () => {
  it("updates a Workbench Output's copy whose tables differ, and leaves a matching one alone", () => {
    expect(responseTablesPatch({ tables: [] }, fetched)).toEqual({ tables: [responseTable] })
    const reordered = Object.fromEntries(Object.entries(responseTable).reverse())
    expect(responseTablesPatch({ tables: [reordered] }, fetched)).toBeNull()
    expect(responseTablesPatch({ tables: [] }, null)).toBeNull()
    expect(responseTablesPatch({}, fetched)?.tables[0]).not.toBe(responseTable)
  })

  // The backend's `column_fits` cases (tests/test_workbench_output.py), as previews render the dtypes.
  it.each([
    ["int", "Int32", true],
    ["int", "UInt8", true],
    ["int", "Float64", false],
    ["float", "Int64", true],
    ["float", "Decimal(precision=12, scale=2)", true],
    ["float", "String", false],
    ["str", "String", true],
    ["str", "Categorical", true],
    ["str", "Enum(categories=['a'])", true],
    ["str", "Int64", false],
    ["bool", "Boolean", true],
    ["bool", "Int64", false],
    ["date", "Date", true],
    ["date", "Datetime(time_unit='us', time_zone=None)", false],
    ["date", "String", false],
    ["int", "Null", true],
  ] as const)("judges whether a %s column takes a frame's %s column: %s", (declared, dtype, fits) => {
    expect(dtypeFits(declared, dtype)).toBe(fits)
  })

  it("starts a Workbench Output with the newest response tables fetched, or none until they are", () => {
    expect(paletteWorkbenchOutputConfig({ tables: [] }, fetched)).toEqual({ tables: [responseTable] })
    expect(paletteWorkbenchOutputConfig({ tables: [] }, null)).toEqual({ tables: [] })
  })
})
