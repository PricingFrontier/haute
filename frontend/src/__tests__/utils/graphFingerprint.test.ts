import { describe, expect, it } from "vitest"

import { computeStructuralFingerprint } from "../../stores/useGraphStore"

type TestNode = {
  id: string
  data: Record<string, unknown>
  position: { x: number; y: number }
}

type TestEdge = {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

describe("computeStructuralFingerprint", () => {
  const baseNodes: TestNode[] = [
    { id: "n1", data: { label: "Node A", nodeType: "polars" }, position: { x: 0, y: 0 } },
    { id: "n2", data: { label: "Node B", nodeType: "dataInput" }, position: { x: 100, y: 200 } },
  ]
  const baseEdges: TestEdge[] = [
    { id: "e1", source: "n1", target: "n2", sourceHandle: "out", targetHandle: "in" },
  ]

  it("ignores position-only changes and edge ids", () => {
    const movedNodes = baseNodes.map((node) => ({
      ...node,
      position: { x: node.position.x + 50, y: node.position.y + 100 },
    }))
    const renamedEdges = baseEdges.map((edge) => ({ ...edge, id: "replacement-id" }))

    expect(computeStructuralFingerprint(movedNodes, renamedEdges)).toBe(
      computeStructuralFingerprint(baseNodes, baseEdges),
    )
  })

  it("is stable across node and edge ordering", () => {
    const secondEdge: TestEdge = { id: "e2", source: "n2", target: "n1" }

    expect(
      computeStructuralFingerprint([...baseNodes].reverse(), [secondEdge, ...baseEdges]),
    ).toBe(computeStructuralFingerprint(baseNodes, [...baseEdges, secondEdge]))
  })

  it("changes for semantic node data, membership, and edge structure", () => {
    const changedNodes = baseNodes.map((node, index) =>
      index === 0 ? { ...node, data: { ...node.data, label: "Changed Label" } } : node,
    )
    const extraNode: TestNode = {
      id: "n3",
      data: { label: "New", nodeType: "polars" },
      position: { x: 0, y: 0 },
    }
    const extraEdge: TestEdge = { id: "e2", source: "n2", target: "n1" }
    const baseline = computeStructuralFingerprint(baseNodes, baseEdges)

    expect(computeStructuralFingerprint(changedNodes, baseEdges)).not.toBe(baseline)
    expect(computeStructuralFingerprint([...baseNodes, extraNode], baseEdges)).not.toBe(baseline)
    expect(computeStructuralFingerprint([baseNodes[0]], baseEdges)).not.toBe(baseline)
    expect(computeStructuralFingerprint(baseNodes, [...baseEdges, extraEdge])).not.toBe(baseline)
    expect(computeStructuralFingerprint(baseNodes, [])).not.toBe(baseline)
  })

  it("includes source and target handles", () => {
    const changedHandle = baseEdges.map((edge) => ({ ...edge, targetHandle: "other" }))

    expect(computeStructuralFingerprint(baseNodes, changedHandle)).not.toBe(
      computeStructuralFingerprint(baseNodes, baseEdges),
    )
  })

  it("includes the preamble and is deterministic for an empty graph", () => {
    expect(computeStructuralFingerprint(baseNodes, baseEdges, "import polars as pl")).not.toBe(
      computeStructuralFingerprint(baseNodes, baseEdges, ""),
    )
    expect(computeStructuralFingerprint([], [])).toBe(computeStructuralFingerprint([], []))
  })
})
