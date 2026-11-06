/**
 * The rules for a Workbench Input, whose tables and sample quote the project's workbench
 * supplies, and a Workbench Output, whose response tables it supplies (specs/workbench):
 * which tables of a fetch are a node's ports, by `<table>_input` and `<table>_output`, which
 * connections a fetch leaves without a table, how a frame's column fills a response column,
 * a table's key, and whether a pricing's answer fits the rows it priced.
 */
import type { Edge } from "@xyflow/react"
import { describe, expect, it } from "vitest"
import type { SchemaColumn, SchemaTable, WorkbenchColumn, WorkbenchTable } from "../../api/types"
import { blankColumn, IDENTITY_COLUMNS } from "../quoteIdentity"
import {
  holdsOutput,
  keyCollisions,
  keyTable,
  outputMisfit,
  workbenchDisconnections,
  workbenchNodePorts,
  workbenchNodeTables,
  workbenchTablePorts,
  dtypeFits,
  type WorkbenchTables,
} from "../workbenchTables"

import misfitCases from "./fixtures/outputMisfit.json"

/** A side of a schema table, as a fetch gives it: its port the table's name and its side. */
const side = (
  name: string,
  which: WorkbenchTable["side"],
  rows: WorkbenchTable["rows"],
  columns: WorkbenchColumn[],
  key: string | null = null,
): WorkbenchTable => ({ name, side: which, port: `${name}_${which}`, rows, columns, key, null_key: null })

const table = side("policy_details", "input", "one", [{ name: "state", type: "str" }])
const sample = { policy_details: { state: "NY" } }
const responseTable = side("layers", "output", "many", [{ name: "layer", type: "int" }])
const fetched: WorkbenchTables = {
  tables: [table],
  sample,
  responseTables: [responseTable],
  fetch: 3,
}

describe("workbenchTablePorts", () => {
  it("makes each table with a column a port, by its port's name, in order, once", () => {
    const tables = [
      responseTable,
      side("bare", "output", "one", []),
      table,
      side("layers", "output", "one", [{ name: "again", type: "int" }]),
    ]

    expect(workbenchTablePorts(tables)).toEqual(["layers_output", "policy_details_input"])
    expect(workbenchTablePorts([])).toEqual([])
  })
})

describe("a workbench node's tables", () => {
  it("are the input tables for a Workbench Input and the response tables for a Workbench Output, from the newest fetch", () => {
    expect(workbenchNodeTables("workbenchInput", fetched)).toBe(fetched.tables)
    expect(workbenchNodeTables("workbenchOutput", fetched)).toBe(fetched.responseTables)
    expect(workbenchNodePorts("workbenchInput", fetched)).toEqual(["policy_details_input"])
    expect(workbenchNodePorts("workbenchOutput", fetched)).toEqual(["layers_output"])
  })

  it("are none before the first fetch lands, and none for any other node", () => {
    expect(workbenchNodePorts("workbenchInput", null)).toEqual([])
    expect(workbenchNodePorts("workbenchOutput", null)).toEqual([])
    expect(workbenchNodeTables("apiInput", fetched)).toEqual([])
    expect(workbenchNodeTables("output", fetched)).toEqual([])
  })
})

describe("workbenchDisconnections", () => {
  const port = (which: WorkbenchTable["side"]) => (name: string) => side(name, which, "one", [{ name: "c", type: "int" }])
  const tablesOf = (inputs: string[], outputs: string[] = []): WorkbenchTables =>
    ({ tables: inputs.map(port("input")), sample: {}, responseTables: outputs.map(port("output")), fetch: 1 })
  const node = (id: string, nodeType: string) => ({ id, data: { label: id, nodeType } })
  const nodes = [
    node("wb_in", "workbenchInput"),
    node("quote", "apiInput"),
    node("priced", "polars"),
    node("wb_out", "workbenchOutput"),
  ]
  const edge = (id: string, source: string, sourceHandle: string | null, target: string, targetHandle: string | null = null): Edge =>
    ({ id, source, sourceHandle, target, targetHandle })
  const policy = edge("e_policy", "wb_in", "policy_input", "priced")
  const account = edge("e_account", "wb_in", "account_input", "priced")
  const filled = edge("e_filled", "priced", null, "wb_out", "pricing_output")

  it("keeps every connection whose port the tables still have, however they were reordered", () => {
    const edges = [policy, account, filled]

    expect(workbenchDisconnections({ nodes, edges }, tablesOf(["account", "policy"], ["pricing"]))).toBeNull()
  })

  it("removes a connection to a port that a table no longer has, by its name or its side", () => {
    // pricing is an input table now, so it has no pricing_output port, and policy no policy_output.
    const result = workbenchDisconnections(
      { nodes, edges: [policy, filled, edge("e_wrong_side", "wb_in", "policy_output", "priced")] },
      tablesOf(["policy", "pricing"], ["policy"]),
    )

    expect(result?.edges).toEqual([policy])
    expect(result?.removed.map(({ node }) => node.id)).toEqual(["wb_in", "wb_out"])
  })

  it("removes a connection to a table whose name has gone, a renamed one's included, by the node that loses it", () => {
    const result = workbenchDisconnections(
      { nodes, edges: [policy, account, filled] },
      tablesOf(["policy_details", "account"], ["premiums"]),
    )

    expect(result?.edges).toEqual([account])
    expect(result?.removed).toEqual([
      { node: nodes[0], edges: [policy] },
      { node: nodes[3], edges: [filled] },
    ])
  })

  it("removes a connection out of a Workbench Input from no table at all", () => {
    const result = workbenchDisconnections({ nodes, edges: [edge("e_bare", "wb_in", null, "priced")] }, tablesOf(["policy"]))

    expect(result?.edges).toEqual([])
  })

  it("never removes a Quote Input's connections, nor one through a submodel's boundary", () => {
    const quoted = edge("e_quote", "quote", "gone", "priced")
    const boundary: Edge = {
      ...edge("e_boundary", "wb_in", "gone", "boundary_out"),
      data: { submodelBoundary: { direction: "output", parentConsumerEdges: [], name: "out" } },
    }

    expect(workbenchDisconnections({ nodes, edges: [quoted, boundary] }, tablesOf([]))).toBeNull()
  })
})

describe("dtypeFits", () => {
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
})

describe("keyTable", () => {
  const column = (name: string, type: SchemaColumn["type"] = "str", set: Partial<SchemaColumn> = {}): SchemaColumn => ({ ...blankColumn(name, type), ...set })
  const identity = IDENTITY_COLUMNS.map(([name, type]) => blankColumn(name, type))
  const schemaTable = (rows: SchemaTable["rows"], inputs: SchemaColumn[], outputs: SchemaColumn[] = []): SchemaTable => ({
    name: "ce_equipment",
    rows,
    inputs: inputs.length > 0 || outputs.length === 0 ? [...identity, ...inputs] : inputs,
    outputs: inputs.length > 0 || outputs.length === 0 ? outputs : [...identity, ...outputs],
  })
  const index = column("item_number", "int", { index: true })
  const peril = column("peril", "str", { options: ["AOP", "EQ"], row_per_value: true })

  it("is none for a one-row table", () => {
    expect(keyTable(schemaTable("one", [column("limit", "int")], [column("premium", "float")]))).toEqual([])
  })

  it("is a many-row input table's index, which its outputs carry", () => {
    expect(keyTable(schemaTable("many", [index, column("limit", "int")], [column("premium", "float")]))).toEqual([index])
  })

  it("is a many-row input table's column giving it a row for each value while it has one, not its index", () => {
    expect(keyTable(schemaTable("many", [index, column("limit", "int"), peril]))).toEqual([peril])
    // Ticked but with no allowed values, it gives no rows: the index keys the table.
    expect(keyTable(schemaTable("many", [index, { ...peril, options: [] }]))).toEqual([index])
  })

  it("is a many-row table of outputs alone's outputs marked key, in order", () => {
    const layer = column("layer", "int", { key: true })
    const band = column("band", "str", { key: true })
    expect(keyTable(schemaTable("many", [], [band, column("premium", "float"), layer]))).toEqual([band, layer])
    expect(keyTable(schemaTable("many", [], [column("premium", "float")]))).toEqual([])
  })
})

describe("keyCollisions", () => {
  const preset = (type: SchemaColumn["type"], options: string[]): SchemaTable => ({
    name: "ce_layers",
    rows: "many",
    inputs: [
      ...IDENTITY_COLUMNS.map(([name, kind]) => blankColumn(name, kind)),
      { ...blankColumn("row_number", "int"), index: true },
      { ...blankColumn("layer", type), options, row_per_value: true },
    ],
    outputs: [],
  })

  it("names the allowed values of the column giving the rows that read as one value in its type, as the server does", () => {
    expect(keyCollisions(preset("int", ["1", "01", "2", "2.0", "3"]))).toEqual([
      "ce_layers.layer's allowed values 1 and 01 are one Integer, so they cannot key its rows: correct the params file",
      "ce_layers.layer's allowed values 2 and 2.0 are one Integer, so they cannot key its rows: correct the params file",
    ])
    expect(keyCollisions(preset("float", ["1.5", "1.50", "001.5"]))).toEqual([
      "ce_layers.layer's allowed values 1.5, 1.50 and 001.5 are one Decimal, so they cannot key its rows: correct the params file",
    ])
  })

  it("names none for distinct values, for text, and for a table whose rows no column gives", () => {
    expect(keyCollisions(preset("int", ["1", "2", "10"]))).toEqual([])
    expect(keyCollisions(preset("str", ["a", "a ", "A"]))).toEqual([])
    expect(keyCollisions({ ...preset("int", ["1", "01"]), rows: "one" })).toEqual([])
  })
})

describe("holdsOutput", () => {
  it("holds a value the quote store keeps: a finite number, text, a tick or a date", () => {
    for (const value of [0, -1.5, 1e300, "", "text", false, true, "2024-01-01"]) expect(holdsOutput(value)).toBe(true)
  })

  it("holds no null, nothing, and no number that is not finite, as JSON carries one", () => {
    for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) expect(holdsOutput(value)).toBe(false)
    for (const token of ["nan", "inf", "-inf"]) {
      expect(holdsOutput({ __haute_type__: "non_finite_float", value: token })).toBe(false)
    }
  })
})

describe("outputMisfit", () => {
  // The cases tests/test_workbench_tables.py holds output_misfit to: one rule in two languages.
  it.each(misfitCases.cases.map((each) => [each.name, each] as const))("%s", (_, each) => {
    expect(outputMisfit(each.table as WorkbenchTable, each.answer, each.request)).toBe(each.message)
  })

  it("is null for a side with no key: a one-row table's, or a table of outputs alone's", () => {
    const outputs = side("premium", "output", "many", [{ name: "layer", type: "int" }])
    expect(outputMisfit(outputs, [null, 1, 1], [])).toBeNull()
  })

  it("tells a key typed as text from one typed as a number", () => {
    const keyed = side("ce_equipment", "output", "many", [{ name: "item_number", type: "int" }], "item_number")
    expect(outputMisfit(keyed, ["1"], [1])).toBe('ce_equipment_output does not fit ce_equipment\'s rows: item_number 1 is on no row priced.')
  })
})
