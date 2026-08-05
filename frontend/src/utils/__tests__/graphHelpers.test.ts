import { describe, it, expect } from "vitest"
import type { Node, Edge } from "@xyflow/react"
import type { PipelineEdge } from "../../types/node"
import {
  computeNextNodeId,
  filterIncomingEdges,
  normalizeEdges,
} from "../graphHelpers"
import { NODE_TYPES } from "../nodeTypes"

function graphNode(
  id: string,
  nodeType: string,
  config: Record<string, unknown> = {},
  data: Record<string, unknown> = {},
): Node {
  return {
    id,
    position: { x: 1, y: 1 },
    data: { label: id, nodeType, config, ...data },
  } as Node
}

function graphEdge(
  id: string,
  source: string,
  target: string,
  sourceHandle: string | null = null,
  targetHandle: string | null = null,
): Edge {
  return { id, source, target, sourceHandle, targetHandle } as Edge
}

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
      { id: "submodel_instance_main" },
      { id: "transform_2" },
    ] as Node[]
    expect(computeNextNodeId(nodes)).toBe(3)
  })

  it("returns 0 when no node has a numeric suffix", () => {
    const nodes = [
      { id: "submodel_instance_a" },
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

// ---------------------------------------------------------------------------
// filterIncomingEdges
// ---------------------------------------------------------------------------

describe("filterIncomingEdges", () => {
  it("retains ordinary live edges and reports missing endpoint nodes", () => {
    const nodes = [
      graphNode("source", NODE_TYPES.POLARS),
      graphNode("target", NODE_TYPES.RATING_STEP),
    ]
    const result = filterIncomingEdges(nodes, [
      graphEdge("valid", "source", "target"),
      graphEdge("missing-source", "gone", "target"),
      graphEdge("missing-target", "source", "gone"),
    ])

    expect(result.validEdges.map(edge => edge.id)).toEqual(["valid"])
    expect(result.rejectedEdges.map(({ edge }) => edge.id)).toEqual([
      "missing-source",
      "missing-target",
    ])
    expect(result.rejectedEdges.map(({ reason }) => reason)).toEqual([
      expect.stringContaining("source node"),
      expect.stringContaining("target node"),
    ])
  })

  it("checks API Input frame handles against runtime-eligible emitted tables", () => {
    const nodes = [
      graphNode("api", NODE_TYPES.API_INPUT, {
        tables: [
          {
            label: "quotes",
            emit: true,
            columns: [{ name: "quote_id", selected: true }],
          },
          {
            label: "not_emitted",
            emit: false,
            columns: [{ name: "ignored", selected: true }],
          },
        ],
      }),
      graphNode("target", NODE_TYPES.POLARS),
    ]
    const result = filterIncomingEdges(nodes, [
      graphEdge("valid-frame", "api", "target", "quotes"),
      graphEdge("stale-frame", "api", "target", "not_emitted"),
      graphEdge("missing-frame", "api", "target"),
    ])

    expect(result.validEdges.map(edge => edge.id)).toEqual(["valid-frame"])
    expect(result.rejectedEdges.map(({ edge }) => edge.id)).toEqual([
      "stale-frame",
      "missing-frame",
    ])
  })

  it("checks Edge Join roles and rejects handles that are not rendered", () => {
    const nodes = [
      graphNode("source", NODE_TYPES.POLARS),
      graphNode("join", NODE_TYPES.EDGE_JOIN),
    ]
    const result = filterIncomingEdges(nodes, [
      graphEdge("base", "source", "join", null, "base"),
      graphEdge("join", "source", "join", null, "join"),
      graphEdge("join-bottom", "source", "join", null, "join-bottom"),
      graphEdge("default", "source", "join"),
      graphEdge("stale", "source", "join", null, "lookup"),
    ])

    expect(result.validEdges.map(edge => edge.id)).toEqual([
      "base",
      "join",
      "join-bottom",
    ])
    expect(result.rejectedEdges.map(({ edge }) => edge.id)).toEqual([
      "default",
      "stale",
    ])
  })

  it("checks configured submodel ports and composite boundary row handles", () => {
    const nodes = [
      graphNode("source", NODE_TYPES.POLARS),
      graphNode("target", NODE_TYPES.POLARS),
      graphNode("submodel", NODE_TYPES.SUBMODEL, {
        inputPorts: ["features"],
        outputPorts: ["priced"],
      }),
      graphNode("empty-submodel", NODE_TYPES.SUBMODEL, {
        outputPorts: [],
      }),
      graphNode("input-port", NODE_TYPES.SUBMODEL_PORT, {}, {
        portDirection: "input",
        ports: [{ id: "incoming-frame", label: "features" }],
      }),
      graphNode("output-port", NODE_TYPES.SUBMODEL_PORT, {}, {
        portDirection: "output",
        ports: [{ id: "out__priced", label: "priced" }],
      }),
    ]
    const result = filterIncomingEdges(nodes, [
      graphEdge("submodel-in", "source", "submodel", null, "in__features"),
      graphEdge("submodel-out", "submodel", "target", "out__priced"),
      graphEdge("submodel-visible-default-in", "source", "submodel"),
      graphEdge("stale-submodel-in", "source", "submodel", null, "in__gone"),
      graphEdge("stale-submodel-out", "submodel", "target", "out__gone"),
      graphEdge("empty-submodel-out", "empty-submodel", "target"),
      graphEdge("input-port-source", "input-port", "target", "incoming-frame"),
      graphEdge("output-port-target", "source", "output-port", null, "out__priced"),
      graphEdge("stale-input-port-handle", "input-port", "target", "gone"),
      graphEdge("stale-output-port-handle", "source", "output-port", null, "gone"),
      graphEdge("wrong-input-port-direction", "source", "input-port"),
      graphEdge("wrong-output-port-direction", "output-port", "target"),
    ])

    expect(result.validEdges.map(edge => edge.id)).toEqual([
      "submodel-in",
      "submodel-out",
      "submodel-visible-default-in",
      "input-port-source",
      "output-port-target",
    ])
    expect(result.rejectedEdges.map(({ edge }) => edge.id)).toEqual([
      "stale-submodel-in",
      "stale-submodel-out",
      "empty-submodel-out",
      "stale-input-port-handle",
      "stale-output-port-handle",
      "wrong-input-port-direction",
      "wrong-output-port-direction",
    ])
  })

  it("rejects source handles on ordinary nodes and connections against unavailable directions", () => {
    const nodes = [
      graphNode("source", NODE_TYPES.DATA_INPUT),
      graphNode("transform", NODE_TYPES.POLARS),
      graphNode("sink", NODE_TYPES.OUTPUT),
    ]
    const result = filterIncomingEdges(nodes, [
      graphEdge("ordinary-handle", "transform", "sink", "stale"),
      graphEdge("into-source-only", "transform", "source"),
      graphEdge("out-of-sink-only", "sink", "transform"),
      graphEdge("valid-source-to-sink", "source", "sink"),
    ])

    expect(result.validEdges.map(edge => edge.id)).toEqual(["valid-source-to-sink"])
    expect(result.rejectedEdges.map(({ edge }) => edge.id)).toEqual([
      "ordinary-handle",
      "into-source-only",
      "out-of-sink-only",
    ])
  })
})
