import type { Edge, Node } from "@xyflow/react"
import { describe, expect, it } from "vitest"
import {
  analyzeEdgeJoinNode,
  findFirstInvalidEdgeJoin,
  formatEdgeJoinValidationIssue,
} from "../edgeJoinValidation"

function node(id: string, extra: Partial<Node> = {}): Node {
  return {
    id,
    type: "pipelineNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      nodeType: "polars",
      config: {},
      _columns: [
        { name: "policy_id", dtype: "String" },
        { name: "state", dtype: "String" },
      ],
    },
    ...extra,
  }
}

function edge(id: string, source: string, target = "join", targetHandle: string | null = null): Edge {
  return { id, source, target, targetHandle }
}

function edgeJoin(config: Record<string, unknown>): Node {
  return node("join", {
    data: {
      label: "Edge Join",
      nodeType: "edgeJoin",
      config,
    },
  })
}

const connectedEdges = [
  edge("e-base", "base", "join", "base"),
  edge("e-lookup", "lookup", "join", "join"),
]

describe("edgeJoinValidation", () => {
  it("accepts a connected non-cross edgeJoin with same-name keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ baseInput: "base", joinInput: "lookup", how: "left", on: ["policy_id"] }),
    ]

    const analysis = analyzeEdgeJoinNode({
      nodeId: "join",
      config: nodes[2].data.config as Record<string, unknown>,
      nodes,
      edges: connectedEdges,
    })

    expect(analysis.diagnostics).toEqual([])
  })

  it("flags non-cross edgeJoin configs without join keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ baseInput: "base", joinInput: "lookup", how: "left" }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain("Non-cross joins need join keys.")
    expect(issue ? formatEdgeJoinValidationIssue(issue) : "").toBe(
      "Edge Join: Non-cross joins need join keys.",
    )
  })

  it("flags cross edgeJoin configs that still carry join keys", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ baseInput: "base", joinInput: "lookup", how: "cross", on: ["policy_id"] }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain("Cross joins must not configure join keys.")
  })

  it("flags mismatched paired key counts", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({
        baseInput: "base",
        joinInput: "lookup",
        how: "left",
        leftOn: ["policy_id", "state"],
        rightOn: ["policy_id"],
      }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain(
      "leftOn and rightOn must contain the same number of keys.",
    )
  })

  it("flags configured roles that drift from connected role handles", () => {
    const nodes = [
      node("base"),
      node("lookup"),
      edgeJoin({ baseInput: "lookup", joinInput: "base", how: "left", on: ["policy_id"] }),
    ]

    const issue = findFirstInvalidEdgeJoin(nodes, connectedEdges)

    expect(issue?.analysis.diagnostics).toContain(
      "Base Input is set to lookup, but the connected base handle is base.",
    )
  })
})
