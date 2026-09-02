import { describe, it, expect } from "vitest"
import { validateConfigRefs, formatConfigRefWarnings } from "../validateConfigRefs"
import type { Node } from "@xyflow/react"

function makeNode(id: string, label: string, config: Record<string, unknown> = {}): Node {
  return { id, position: { x: 0, y: 0 }, data: { label, nodeType: "polars", config } }
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
      makeNode("inst1", "Instance", { instanceOf: "original_gone" }),
    ]
    const warnings = validateConfigRefs(nodes)
    expect(warnings).toHaveLength(1)
    expect(warnings[0].field).toBe("instanceOf")
  })

  it("detects multiple broken refs across nodes", () => {
    const nodes = [
      makeNode("n1", "Node1", { data_input: "missing1" }),
      makeNode("n2", "Node2", { instanceOf: "missing2" }),
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

  // ── submodel-aware resolution ──────────────────────────────────────
  // A top-level node can legitimately reference a node that is defined
  // *inside* a submodel's graph (e.g. `instanceOf` an original that lives in
  // a submodel). validateConfigRefs must treat submodel-exported node ids as
  // valid targets, mirroring NodePanel.tsx#resolveInstanceOriginal — otherwise
  // it false-positives on valid pipelines (the `competitor_features` bug).

  it("does not warn when instanceOf references a submodel-exported node (nested shape)", () => {
    // Reproduces the reported bug: `competitor_features` is a submodel child,
    // referenced top-level via instanceOf.
    const nodes = [
      makeNode("premium", "premium"),
      makeNode("competitor_features_scenarios", "competitor_features_scenarios", {
        instanceOf: "competitor_features",
      }),
    ]
    const submodels = {
      model_stuff: {
        file: "modules/model_stuff.py",
        graph: {
          nodes: [
            { id: "sale_flag", data: { label: "sale_flag" } },
            { id: "competitor_features", data: { label: "competitor_features" } },
          ],
          edges: [{ id: "e1", source: "sale_flag", target: "competitor_features" }],
        },
      },
    }
    expect(validateConfigRefs(nodes, submodels)).toEqual([])
  })

  it("still warns when instanceOf target is absent from both graph and submodels", () => {
    const nodes = [
      makeNode("inst", "Instance", { instanceOf: "not_anywhere" }),
    ]
    const submodels = {
      model_stuff: { graph: { nodes: [{ id: "competitor_features", data: { label: "x" } }], edges: [] } },
    }
    const warnings = validateConfigRefs(nodes, submodels)
    expect(warnings).toHaveLength(1)
    expect(warnings[0]).toEqual({
      nodeId: "inst",
      nodeLabel: "Instance",
      field: "instanceOf",
      referencedId: "not_anywhere",
    })
  })

  it("does not validate executable input names against submodel node ids", () => {
    // Submodels exist but none export the referenced id → not over-suppressed.
    const nodes = [makeNode("opt", "Optimiser", { data_input: "deleted_node" })]
    const submodels = {
      m: { graph: { nodes: [{ id: "something_else", data: { label: "y" } }], edges: [] } },
    }
    const warnings = validateConfigRefs(nodes, submodels)
    expect(warnings).toEqual([])
  })

  it("handles an omitted submodel collection", () => {
    const nodes = [makeNode("inst", "Instance", { instanceOf: "missing" })]
    expect(validateConfigRefs(nodes)).toHaveLength(1)
    expect(validateConfigRefs(nodes, undefined)).toHaveLength(1)
    expect(validateConfigRefs(nodes, null)).toHaveLength(1)
    expect(validateConfigRefs(nodes, {})).toHaveLength(1)
  })

  it("tolerates malformed submodel metadata without crashing", () => {
    const nodes = [makeNode("inst", "Instance", { instanceOf: "real_target" })]
    const submodels = {
      bad_string: "not an object",
      bad_nodes: { graph: { nodes: "not an array" } },
      no_nodes: { graph: {} },
      good: { graph: { nodes: [{ id: "real_target", data: { label: "ok" } }] } },
    } as unknown as Record<string, unknown>
    // The valid submodel still resolves `real_target`, the junk is ignored.
    expect(validateConfigRefs(nodes, submodels)).toEqual([])
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
