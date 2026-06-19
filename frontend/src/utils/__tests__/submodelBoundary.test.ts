/**
 * Unit tests for buildSubmodelBoundary — the wrapper I/O boundary derivation.
 *
 * Pins the rule: one wrapper-boundary component per FRAME, in or out, reflecting
 * the wrapper's own frames (not the external neighbours) and invariant to
 * external rewiring beyond the frame set.
 *   - OUTPUT: one per emitting wrapped node (1-1), consumer-count-invariant.
 *   - INPUT: one per cross-boundary link (different links = different frames);
 *     not collapsed by source nor by the internal connector fed.
 */
import { describe, it, expect } from "vitest"
import type { Edge } from "@xyflow/react"
import { buildSubmodelBoundary } from "../submodelBoundary"

const SM = "submodel__sm"
const CHILDREN = new Set(["c1", "c2"])

function edge(
  id: string,
  source: string,
  target: string,
  opts: { sourceHandle?: string; targetHandle?: string } = {},
): Edge {
  return { id, source, target, ...opts } as unknown as Edge
}

function build(edges: Edge[], childIds: Set<string> = CHILDREN) {
  return buildSubmodelBoundary({ smNodeId: SM, parentNodes: [], parentEdges: edges, childIds })
}

function ports(b: ReturnType<typeof build>, d: "input" | "output") {
  return b.portNodes.filter((n) => (n.data as { portDirection?: string }).portDirection === d)
}

const labelOf = (n: { data: unknown }) => (n.data as { label: string }).label

describe("buildSubmodelBoundary — one component per frame", () => {
  it("OUTPUT: one component per emitting node, invariant to consumer count", () => {
    const b = build([
      edge("e1", SM, "y1", { sourceHandle: "out__c1" }),
      edge("e2", SM, "y2", { sourceHandle: "out__c1" }),
    ])
    const out = ports(b, "output")
    expect(out).toHaveLength(1)
    expect(out[0].id).toBe("port_out__c1")
    expect(labelOf(out[0])).toBe("c1")
    // The emitting child feeds its output frame: child → port.
    const links = b.boundaryEdges.filter((e) => e.target === "port_out__c1")
    expect(links).toHaveLength(1)
    expect(links[0].source).toBe("c1")
  })

  it("OUTPUT: one component per distinct emitting node", () => {
    const b = build([
      edge("e1", SM, "y1", { sourceHandle: "out__c1" }),
      edge("e2", SM, "y2", { sourceHandle: "out__c2" }),
    ])
    expect(
      ports(b, "output")
        .map((n) => n.id)
        .sort(),
    ).toEqual(["port_out__c1", "port_out__c2"])
  })

  it("INPUT: one component per link feeding the wrapper, port → child", () => {
    const b = build([edge("e1", "s", SM, { targetHandle: "in__c1" })])
    const inp = ports(b, "input")
    expect(inp).toHaveLength(1)
    expect(labelOf(inp[0])).toBe("c1")
    const links = b.boundaryEdges.filter((e) => e.source === inp[0].id)
    expect(links).toHaveLength(1)
    expect(links[0].target).toBe("c1")
  })

  it("INPUT: two sources feeding one node are two frames", () => {
    const b = build([
      edge("e1", "s1", SM, { targetHandle: "in__c1" }),
      edge("e2", "s2", SM, { targetHandle: "in__c1" }),
    ])
    expect(ports(b, "input")).toHaveLength(2)
    expect(b.boundaryEdges.filter((e) => e.target === "c1")).toHaveLength(2)
  })

  it("INPUT: one source feeding two nodes are two frames (no fan collapse)", () => {
    const b = build([
      edge("e1", "s", SM, { targetHandle: "in__c1" }),
      edge("e2", "s", SM, { targetHandle: "in__c2" }),
    ])
    const inp = ports(b, "input")
    expect(inp).toHaveLength(2)
    const inPortIds = new Set(inp.map((p) => p.id))
    const targets = b.boundaryEdges
      .filter((e) => inPortIds.has(e.source))
      .map((e) => e.target)
      .sort()
    expect(targets).toEqual(["c1", "c2"])
  })

  it("INPUT: a duplicated identical crossing does not produce a colliding port", () => {
    const b = build([
      edge("e1", "s", SM, { targetHandle: "in__c1" }),
      edge("e1b", "s", SM, { targetHandle: "in__c1" }),
    ])
    expect(ports(b, "input")).toHaveLength(1)
  })

  it("drops links to a non-member child (stale/ghost), both directions", () => {
    const b = build([
      edge("e1", "s", SM, { targetHandle: "in__ghost" }),
      edge("e2", SM, "y", { sourceHandle: "out__ghost" }),
    ])
    expect(b.portNodes).toHaveLength(0)
    expect(b.boundaryEdges).toHaveLength(0)
  })

  it("ignores edges unrelated to the submodel node", () => {
    const b = build([edge("e1", "a", "b")])
    expect(b.portNodes).toHaveLength(0)
    expect(b.boundaryEdges).toHaveLength(0)
  })
})
