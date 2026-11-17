import type { Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import { savedCodeForSteps, withEditedSteps, withSavedStepCode } from "../editedSteps"

const steps = [
  { id: "s", kind: "source", input: "quotes" },
  { id: "w", kind: "with_column", name: "relativity", expr: { type: "operand", operand: { kind: "literal", type: "number", value: 1 } } },
]
const savedCode = 'df = quotes\ndf = df.with_columns(pl.lit(1.0).alias("relativity"))'

function transform(id: string, config: Record<string, unknown>): Node {
  return { id, position: { x: 0, y: 0 }, data: { nodeType: "polars", label: id, config } }
}

function definition(nodes: Node[]) {
  return { definitionId: "sub", file: "modules/sub.py", graph: { nodes, edges: [] }, inputPorts: [], outputPorts: [] }
}

const baseline = {
  nodes: [transform("t", { steps, code: savedCode }), transform("plain", { code: "df = quotes" })],
  submodels: { sub: definition([transform("t", { steps, code: "df = inner" })]) },
}

describe("edited steps", () => {
  it("sends steps as saved with the code saved for them, whatever the live node holds", () => {
    // Undo and Redo restore a stale rendering; the browser's own lost the decimal.
    const live = {
      nodes: [transform("t", { steps: structuredClone(steps), code: 'df = df.with_columns(pl.lit(1).alias("relativity"))' })],
      submodels: { sub: definition([transform("t", { steps: structuredClone(steps), code: "stale" })]) },
    }

    const { document, edited } = withEditedSteps(live, baseline)

    expect(edited).toEqual([])
    expect((document.nodes[0].data.config as Record<string, unknown>).code).toBe(savedCode)
    const inner = (document.submodels.sub as ReturnType<typeof definition>).graph.nodes[0]
    expect((inner.data.config as Record<string, unknown>).code).toBe("df = inner")
  })

  it("names a node whose steps differ, a node the baseline does not hold, and every node of a document never saved", () => {
    const changed = [...steps, { id: "l", kind: "limit", n: 5 }]
    const live = {
      nodes: [transform("t", { steps: changed, code: savedCode }), transform("new", { steps, code: "" })],
      submodels: { sub: definition([transform("t", { steps: changed, code: "df = inner" })]) },
    }

    expect(withEditedSteps(live, baseline).edited).toEqual([
      { submodel: null, node_id: "t" },
      { submodel: null, node_id: "new" },
      { submodel: "sub", node_id: "t" },
    ])
    expect(withEditedSteps(live, null).edited).toHaveLength(3)
  })

  it("compares steps by their content, not their key order", () => {
    const reordered = steps.map((step) => Object.fromEntries(Object.entries(step).reverse()))

    expect(savedCodeForSteps(baseline, null, "t", reordered)).toBe(savedCode)
    expect(savedCodeForSteps(baseline, "sub", "t", steps)).toBe("df = inner")
    expect(savedCodeForSteps(baseline, null, "plain", steps)).toBeNull()
  })

  it("puts the code the server wrote for each named node in the baseline it becomes", () => {
    const saved = withSavedStepCode(baseline, [
      { submodel: "sub", node_id: "t", code: "df = written" },
    ])

    const inner = (saved.submodels.sub as ReturnType<typeof definition>).graph.nodes[0]
    expect((inner.data.config as Record<string, unknown>).code).toBe("df = written")
    expect((saved.nodes[0].data.config as Record<string, unknown>).code).toBe(savedCode)
  })
})
