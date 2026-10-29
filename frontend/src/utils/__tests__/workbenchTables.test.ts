/**
 * The rules for a Workbench Input, whose tables and sample quote the project's workbench
 * supplies, and a Workbench Output, whose response tables it supplies (specs/workbench):
 * which tables of a fetch are a node's ports, which connections a fetch leaves without a
 * table, and how a frame's column fills a response column.
 */
import type { Edge } from "@xyflow/react"
import { describe, expect, it } from "vitest"
import {
  workbenchDisconnections,
  workbenchNodePorts,
  workbenchNodeTables,
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

describe("workbenchTablePorts", () => {
  it("makes each table with a column a port, by name, in order, once", () => {
    const tables = [
      responseTable,
      { name: "bare", rows: "one" as const, columns: [] },
      table,
      { name: "layers", rows: "one" as const, columns: [{ name: "again", type: "int" as const }] },
    ]

    expect(workbenchTablePorts(tables)).toEqual(["layers", "policy_details"])
    expect(workbenchTablePorts([])).toEqual([])
  })
})

describe("a workbench node's tables", () => {
  it("are the input tables for a Workbench Input and the response tables for a Workbench Output, from the newest fetch", () => {
    expect(workbenchNodeTables("workbenchInput", fetched)).toBe(fetched.tables)
    expect(workbenchNodeTables("workbenchOutput", fetched)).toBe(fetched.responseTables)
    expect(workbenchNodePorts("workbenchInput", fetched)).toEqual(["policy_details"])
    expect(workbenchNodePorts("workbenchOutput", fetched)).toEqual(["layers"])
  })

  it("are none before the first fetch lands, and none for any other node", () => {
    expect(workbenchNodePorts("workbenchInput", null)).toEqual([])
    expect(workbenchNodePorts("workbenchOutput", null)).toEqual([])
    expect(workbenchNodeTables("apiInput", fetched)).toEqual([])
    expect(workbenchNodeTables("output", fetched)).toEqual([])
  })
})

describe("workbenchDisconnections", () => {
  const port = (name: string) => ({ name, rows: "one" as const, columns: [{ name: "c", type: "int" as const }] })
  const tablesOf = (inputs: string[], outputs: string[] = []): WorkbenchTables =>
    ({ tables: inputs.map(port), sample: {}, responseTables: outputs.map(port), fetch: 1 })
  const node = (id: string, nodeType: string) => ({ id, data: { label: id, nodeType } })
  const nodes = [
    node("wb_in", "workbenchInput"),
    node("quote", "apiInput"),
    node("priced", "polars"),
    node("wb_out", "workbenchOutput"),
  ]
  const edge = (id: string, source: string, sourceHandle: string | null, target: string, targetHandle: string | null = null): Edge =>
    ({ id, source, sourceHandle, target, targetHandle })
  const policy = edge("e_policy", "wb_in", "policy", "priced")
  const account = edge("e_account", "wb_in", "account", "priced")
  const filled = edge("e_filled", "priced", null, "wb_out", "pricing_output")

  it("keeps every connection whose table's name the tables still have, however they were reordered", () => {
    const edges = [policy, account, filled]

    expect(workbenchDisconnections({ nodes, edges }, tablesOf(["account", "policy"], ["pricing_output"]))).toBeNull()
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
