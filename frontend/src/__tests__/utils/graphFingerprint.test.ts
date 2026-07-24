/**
 * B20: Test that position-only changes don't produce a different graph fingerprint.
 *
 * The fingerprint logic from App.tsx builds a string from node ids + data + edge list,
 * ignoring position. This test validates that pattern in isolation.
 */
import { describe, it, expect } from "vitest"

type Node = { id: string; data: Record<string, unknown>; position: { x: number; y: number } }
type Edge = { id: string; source: string; target: string }

/** The fingerprint function as used in App.tsx */
function graphFingerprint(nodes: Node[], edges: Edge[]): string {
  const nodeFingerprint = nodes.map((n) => `${n.id}:${JSON.stringify(n.data)}`).join("|")
  const edgeFingerprint = edges.map((e) => `${e.id}:${e.source}:${e.target}`).join("|")
  return `${nodeFingerprint}||${edgeFingerprint}`
}

describe("graphFingerprint (B20)", () => {
  const baseNodes: Node[] = [
    { id: "n1", data: { label: "Node A", nodeType: "polars" }, position: { x: 0, y: 0 } },
    { id: "n2", data: { label: "Node B", nodeType: "dataInput" }, position: { x: 100, y: 200 } },
  ]
  const baseEdges: Edge[] = [
    { id: "e1", source: "n1", target: "n2" },
  ]

  it("position-only changes produce the same fingerprint", () => {
    const movedNodes = baseNodes.map(n => ({ ...n, position: { x: n.position.x + 50, y: n.position.y + 100 } }))
    expect(graphFingerprint(movedNodes, baseEdges)).toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("data changes produce a different fingerprint", () => {
    const changedNodes = baseNodes.map((n, i) =>
      i === 0 ? { ...n, data: { ...n.data, label: "Changed Label" } } : n
    )
    expect(graphFingerprint(changedNodes, baseEdges)).not.toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("adding a node produces a different fingerprint", () => {
    const extraNode: Node = { id: "n3", data: { label: "New" }, position: { x: 0, y: 0 } }
    expect(graphFingerprint([...baseNodes, extraNode], baseEdges)).not.toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("removing a node produces a different fingerprint", () => {
    expect(graphFingerprint([baseNodes[0]], baseEdges)).not.toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("adding an edge produces a different fingerprint", () => {
    const extraEdge: Edge = { id: "e2", source: "n2", target: "n1" }
    expect(graphFingerprint(baseNodes, [...baseEdges, extraEdge])).not.toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("removing an edge produces a different fingerprint", () => {
    expect(graphFingerprint(baseNodes, [])).not.toBe(graphFingerprint(baseNodes, baseEdges))
  })

  it("empty graph produces consistent fingerprint", () => {
    expect(graphFingerprint([], [])).toBe(graphFingerprint([], []))
  })
})
