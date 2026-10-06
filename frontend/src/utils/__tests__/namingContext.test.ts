import type { Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"

import { namingContextGraph } from "../namingContext"

function node(id: string, label: string, type = "polars"): Node {
  return { id, type, position: { x: 0, y: 0 }, data: { label, nodeType: type, config: {} } }
}

const definition = {
  definitionId: "rates",
  file: "modules/rates.py",
  graph: {
    nodes: [node("old_child", "old child")],
    edges: [],
    preamble: "def rate_lookup(x):\n    return x",
    preserved_blocks: ["# kept"],
  },
  inputPorts: [],
  outputPorts: [],
}

const root = {
  nodes: [node("occ", "rates_1", "submodel")],
  edges: [],
  submodels: { rates: definition } as Record<string, unknown>,
  preamble: "import numpy as np",
}

describe("namingContextGraph", () => {
  it("carries the document's preserved blocks at the root", () => {
    const graph = namingContextGraph({
      graph: root,
      preservedBlocks: ["def helper():\n    pass"],
      drilledDefinition: null,
      liveNodes: [],
      liveEdges: [],
    })

    expect(graph.preserved_blocks).toEqual(["def helper():\n    pass"])
    expect(graph.submodels).toBe(root.submodels)
  })

  it("swaps in the drilled child's nodes while its definition keeps its own support code", () => {
    const graph = namingContextGraph({
      graph: root,
      preservedBlocks: [],
      drilledDefinition: "rates",
      liveNodes: [node("child", "rate_lookup"), node("port", "inputs", "submodelPort")],
      liveEdges: [],
    })

    const drilled = (graph.submodels as Record<string, { graph: Record<string, unknown> }>).rates.graph
    expect((drilled.nodes as Node[]).map((n) => n.id)).toEqual(["child"])
    expect(drilled.preamble).toBe("def rate_lookup(x):\n    return x")
    expect(drilled.preserved_blocks).toEqual(["# kept"])
  })
})
