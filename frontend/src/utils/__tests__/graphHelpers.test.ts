import { describe, it, expect } from "vitest"
import type { Node, Edge } from "@xyflow/react"
import type { PipelineEdge } from "../../types/node"
import { computeNextNodeId, normalizeEdges } from "../graphHelpers"

// ---------------------------------------------------------------------------
// computeNextNodeId
// ---------------------------------------------------------------------------

describe("computeNextNodeId", () => {
  it("returns 0 for empty node array", () => {
    expect(computeNextNodeId([])).toBe(0)
  })

  it("returns max suffix + 1 from single node", () => {
    const nodes = [{ id: "transform_3" }] as Node[]
    expect(computeNextNodeId(nodes)).toBe(4)
  })

  it("returns max suffix + 1 from multiple nodes", () => {
    const nodes = [
      { id: "transform_1" },
      { id: "dataInput_5" },
      { id: "banding_3" },
    ] as Node[]
    expect(computeNextNodeId(nodes)).toBe(6)
  })

  it("ignores nodes with no numeric suffix", () => {
    const nodes = [
      { id: "submodel__my_model" },
      { id: "transform_2" },
    ] as Node[]
    expect(computeNextNodeId(nodes)).toBe(3)
  })

  it("returns 0 when no node has a numeric suffix", () => {
    const nodes = [
      { id: "submodel__a" },
      { id: "port_in__x" },
    ] as Node[]
    expect(computeNextNodeId(nodes)).toBe(0)
  })

  it("handles single-digit and multi-digit suffixes", () => {
    const nodes = [
      { id: "transform_99" },
      { id: "banding_7" },
    ] as Node[]
    expect(computeNextNodeId(nodes)).toBe(100)
  })

  it("handles suffix of 0", () => {
    const nodes = [{ id: "transform_0" }] as Node[]
    expect(computeNextNodeId(nodes)).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// normalizeEdges
// ---------------------------------------------------------------------------

describe("normalizeEdges", () => {
  it("returns empty array for empty input", () => {
    expect(normalizeEdges([])).toEqual([])
  })

  it("sets type to 'default' and animated to false", () => {
    const edges = [
      { id: "e1", source: "a", target: "b", type: "custom", animated: true },
    ] as Edge[]
    const result = normalizeEdges(edges)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe("default")
    expect(result[0].animated).toBe(false)
  })

  it("preserves other edge properties", () => {
    const edges = [
      { id: "e1", source: "a", target: "b", type: "step", animated: true, style: { stroke: "red" } },
    ] as Edge[]
    const result = normalizeEdges(edges)
    expect(result[0].id).toBe("e1")
    expect(result[0].source).toBe("a")
    expect(result[0].target).toBe("b")
    expect(result[0].style).toEqual({ stroke: "red" })
  })

  it("preserves authored submodel boundary ports", () => {
    const edges: PipelineEdge[] = [
      {
        id: "e1",
        source: "submodel__pricing",
        target: "submodel__scoring",
        sourceHandle: "out__priced",
        targetHandle: "in__score",
        sourcePort: "quotes",
        targetPort: "base",
      },
    ]

    const result = normalizeEdges(edges)

    expect(result[0].sourcePort).toBe("quotes")
    expect(result[0].targetPort).toBe("base")
  })

  it("does not mutate the original array", () => {
    const edges = [
      { id: "e1", source: "a", target: "b", type: "custom", animated: true },
    ] as Edge[]
    normalizeEdges(edges)
    expect(edges[0].type).toBe("custom")
    expect(edges[0].animated).toBe(true)
  })

  it("normalizes multiple edges", () => {
    const edges = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "c", type: "smoothstep" },
    ] as Edge[]
    const result = normalizeEdges(edges)
    expect(result).toHaveLength(2)
    expect(result[0].type).toBe("default")
    expect(result[1].type).toBe("default")
  })
})
