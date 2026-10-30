import { describe, it, expect } from "vitest"
import { validateConfigRefs, formatConfigRefWarnings } from "../validateConfigRefs"
import type { Node } from "@xyflow/react"

function makeNode(id: string, label: string, config: Record<string, unknown> = {}, nodeType = "polars"): Node {
  return { id, position: { x: 0, y: 0 }, data: { label, nodeType, config:
    nodeType === "submodel" ? { definitionId: "definition", alias: id, ...config } : config,
  } }
}

describe("validateConfigRefs", () => {
  it("returns empty for nodes with no config refs", () => {
    const nodes = [makeNode("n1", "Node1"), makeNode("n2", "Node2")]
    expect(validateConfigRefs(nodes)).toEqual([])
  })

  it("returns empty when data_input references an existing node", () => {
    const nodes = [
      makeNode("ds1", "DataSource"),
      makeNode("opt1", "Optimiser", { data_input: "ds1" }),
    ]
    expect(validateConfigRefs(nodes)).toEqual([])
  })

  it("does not treat data_input names as node references", () => {
    const nodes = [
      makeNode("ds1", "DataSource"),
      makeNode("opt1", "Optimiser", { data_input: "Polars_8" }),
    ]
    const warnings = validateConfigRefs(nodes)
    expect(warnings).toEqual([])
  })

  it("does not treat banding_source names as node references", () => {
    const nodes = [
      makeNode("opt1", "Optimiser", { banding_source: "deleted_node" }),
    ]
    const warnings = validateConfigRefs(nodes)
    expect(warnings).toEqual([])
  })

  it("detects stale instanceOf reference", () => {
    const nodes = [
      makeNode("inst1", "Instance", { instanceOf: "original_gone" }, "submodel"),
    ]
    const warnings = validateConfigRefs(nodes)
    expect(warnings).toHaveLength(1)
    expect(warnings[0].field).toBe("instanceOf")
  })

  it("detects multiple broken refs across nodes", () => {
    const nodes = [
      makeNode("n1", "Node1", { data_input: "missing1" }),
      makeNode("n2", "Node2", { instanceOf: "missing2" }, "submodel"),
    ]
    const warnings = validateConfigRefs(nodes)
    expect(warnings).toHaveLength(1)
  })

  it("ignores empty string references", () => {
    const nodes = [makeNode("n1", "Node1", { data_input: "" })]
    expect(validateConfigRefs(nodes)).toEqual([])
  })

  it("ignores non-string references", () => {
    const nodes = [makeNode("n1", "Node1", { data_input: 42 })]
    expect(validateConfigRefs(nodes)).toEqual([])
  })

  it("ignores nodes without config", () => {
    const nodes: Node[] = [{ id: "n1", position: { x: 0, y: 0 }, data: { label: "NoConfig" } }]
    expect(validateConfigRefs(nodes)).toEqual([])
  })

  it("does not accept an ordinary node as an occurrence owner", () => {
    expect(validateConfigRefs([makeNode("owner", "owner"), makeNode("copy", "copy", { instanceOf: "owner" }, "submodel")]))
      .toEqual([{ nodeId: "copy", nodeLabel: "copy", field: "instanceOf", referencedId: "owner" }])
  })

  it("accepts an occurrence whose owner is in its parent graph", () => {
    expect(validateConfigRefs([makeNode("owner", "owner", {}, "submodel"), makeNode("copy", "copy", { instanceOf: "owner" }, "submodel")])).toEqual([])
  })

})

describe("formatConfigRefWarnings", () => {
  it("returns empty string for no warnings", () => {
    expect(formatConfigRefWarnings([])).toBe("")
  })

  it("formats single warning with node and field detail", () => {
    const result = formatConfigRefWarnings([
      { nodeId: "opt1", nodeLabel: "Optimiser", field: "data_input", referencedId: "Polars_8" },
    ])
    expect(result).toContain("Optimiser")
    expect(result).toContain("Polars_8")
  })

  it("formats multiple warnings with count", () => {
    const result = formatConfigRefWarnings([
      { nodeId: "n1", nodeLabel: "N1", field: "data_input", referencedId: "x" },
      { nodeId: "n2", nodeLabel: "N2", field: "instanceOf", referencedId: "y" },
    ])
    expect(result).toContain("2 nodes")
  })
})
