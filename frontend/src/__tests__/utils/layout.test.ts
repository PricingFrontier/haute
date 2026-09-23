/** Contract tests for ELK graph geometry and React Flow handle ports. */
import { beforeEach, describe, expect, it, vi } from "vitest"
import {
  Position,
  type Edge,
  type Handle,
  type InternalNode,
  type Node,
} from "@xyflow/react"
import { makeEdge, makeNode } from "../../test-utils/factories"

const mockLayout = vi.fn()

vi.mock("elkjs/lib/elk.bundled.js", () => ({
  default: class ELK { layout = mockLayout },
}))

const { getLayoutedElements } = await import("../../utils/layout")

beforeEach(() => mockLayout.mockReset())

function internalNode(
  node: Node,
  width = 240,
  height = 70,
  handleBounds?: { source: Handle[] | null; target: Handle[] | null },
): InternalNode {
  return {
    ...node,
    measured: { width, height },
    internals: {
      positionAbsolute: node.position,
      z: 0,
      userNode: node,
      handleBounds,
    },
  }
}

describe("getLayoutedElements", () => {
  it("assigns positions from ELK output while preserving all non-position fields", async () => {
    const nodes = [makeNode("a"), makeNode("b")]
    const edges = [makeEdge("a", "b", { id: "e1" })]
    mockLayout.mockResolvedValue({
      children: [{ id: "a", x: 100, y: 200 }, { id: "b", x: 300, y: 400 }],
    })

    const result = await getLayoutedElements(nodes, edges)

    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ ...nodes[0], position: { x: 100, y: 200 } })
    expect(result[1]).toMatchObject({ ...nodes[1], position: { x: 300, y: 400 } })
  })

  it("returns an empty array for empty input", async () => {
    mockLayout.mockResolvedValue({ children: [] })
    await expect(getLayoutedElements([], [])).resolves.toEqual([])
  })

  it("passes edges through to ELK when no measured handles exist", async () => {
    const nodes = [makeNode("a"), makeNode("b"), makeNode("c")]
    const edges = [
      makeEdge("a", "b", { id: "e1" }),
      makeEdge("b", "c", { id: "e2" }),
    ]
    mockLayout.mockResolvedValue({
      children: nodes.map(({ id }) => ({ id, x: 0, y: 0 })),
    })

    await getLayoutedElements(nodes, edges)

    expect(mockLayout.mock.calls[0][0].edges).toEqual([
      { id: "e1", sources: ["a"], targets: ["b"] },
      { id: "e2", sources: ["b"], targets: ["c"] },
    ])
  })

  it("uses explicit or measured dimensions, falling back only before measurement", async () => {
    const explicit = makeNode("explicit", "polars", { width: 320, height: 90 })
    const measured = makeNode("measured")
    const fallback = makeNode("fallback")
    mockLayout.mockResolvedValue({
      children: ["explicit", "measured", "fallback"].map((id, x) => ({ id, x, y: x })),
    })

    await getLayoutedElements(
      [explicit, measured, fallback],
      [],
      id => id === measured.id ? internalNode(measured, 260, 80) : undefined,
    )

    expect(mockLayout.mock.calls[0][0].children).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "explicit", width: 320, height: 90 }),
      expect.objectContaining({ id: "measured", width: 260, height: 80 }),
      expect.objectContaining({ id: "fallback", width: 240, height: 70 }),
    ]))
  })

  it("converts ELK top-left coordinates using each node origin and preserves near coordinates", async () => {
    const anchored = makeNode("anchored", "polars", {
      origin: [0.5, 1], width: 200, height: 50,
    })
    const nearby = makeNode("nearby")
    mockLayout.mockResolvedValue({
      children: [{ id: "anchored", x: 101, y: 202 }, { id: "nearby", x: 105, y: 208 }],
    })

    const result = await getLayoutedElements([anchored, nearby], [])

    expect(result.map(node => node.position)).toEqual([
      { x: 201, y: 252 },
      { x: 105, y: 208 },
    ])
  })

  it.each([
    ["omits a requested node", { children: [] }],
    ["returns undefined coordinates", { children: [{ id: "a", x: undefined, y: undefined }] }],
    ["returns non-finite coordinates", { children: [{ id: "a", x: Number.NaN, y: 3 }] }],
  ])("rejects when ELK %s", async (_reason, layout) => {
    mockLayout.mockResolvedValue(layout)
    await expect(getLayoutedElements([makeNode("a")], [])).rejects.toThrow(/layout.*a|a.*layout/i)
  })

  it("creates fixed compass ports from measured handles and uses them for edge endpoints", async () => {
    const source = makeNode("source")
    const target = makeNode("target")
    const edge = makeEdge("source", "target", {
      id: "edge", sourceHandle: "out", targetHandle: "in",
    })
    const sourceBounds: Handle[] = [{
      id: "out", nodeId: source.id, type: "source", position: Position.Right,
      x: 236, y: 44, width: 8, height: 8,
    }]
    const targetBounds: Handle[] = [{
      id: "in", nodeId: target.id, type: "target", position: Position.Top,
      x: 20, y: 0, width: 8, height: 8,
    }]
    mockLayout.mockResolvedValue({
      children: [{ id: source.id, x: 0, y: 0 }, { id: target.id, x: 300, y: 0 }],
    })

    await getLayoutedElements([source, target], [edge], id => {
      if (id === source.id) return internalNode(source, 240, 70, { source: sourceBounds, target: [] })
      if (id === target.id) return internalNode(target, 240, 70, { source: [], target: targetBounds })
      return undefined
    })

    const graph = mockLayout.mock.calls[0][0]
    const sourceChild = graph.children.find((child: { id: string }) => child.id === source.id)
    const targetChild = graph.children.find((child: { id: string }) => child.id === target.id)
    const sourcePort = sourceChild.ports[0]
    const targetPort = targetChild.ports[0]
    expect(sourcePort).toMatchObject({
      x: 240, y: 48, layoutOptions: { "elk.port.side": "EAST" },
    })
    expect(targetPort).toMatchObject({ layoutOptions: { "elk.port.side": "NORTH" } })
    expect(sourceChild.layoutOptions).toMatchObject({ "elk.portConstraints": "FIXED_POS" })
    expect(graph.edges[0]).toMatchObject({
      sources: [sourcePort.id],
      targets: [targetPort.id],
    })
  })

  it("shares co-located aliases of one handle type but keeps source and target ids distinct", async () => {
    const node = makeNode("__layout_port_0")
    const other = makeNode("other")
    const edges: Edge[] = [
      makeEdge(node.id, other.id, { id: "__layout_port_1", sourceHandle: "first" }),
      makeEdge(node.id, other.id, { id: "two", sourceHandle: "alias" }),
      makeEdge(other.id, node.id, { id: "three", targetHandle: "first" }),
    ]
    const handles: { source: Handle[]; target: Handle[] } = {
      source: [
        {
          id: "first", nodeId: node.id, type: "source", position: Position.Right,
          x: 236, y: 20, width: 8, height: 8,
        },
        {
          id: "alias", nodeId: node.id, type: "source", position: Position.Right,
          x: 236, y: 20, width: 8, height: 8,
        },
      ],
      target: [{
        id: "first", nodeId: node.id, type: "target", position: Position.Right,
        x: 236, y: 20, width: 8, height: 8,
      }],
    }
    mockLayout.mockResolvedValue({
      children: [{ id: node.id, x: 0, y: 0 }, { id: other.id, x: 1, y: 1 }],
    })

    await getLayoutedElements(
      [node, other],
      edges,
      id => id === node.id ? internalNode(node, 240, 70, handles) : undefined,
    )

    const graph = mockLayout.mock.calls[0][0]
    expect(graph.edges[0].sources[0]).toBe(graph.edges[1].sources[0])
    expect(graph.edges[0].sources[0]).not.toBe(graph.edges[2].targets[0])
    const generatedIds = graph.children.flatMap(
      (child: { ports?: Array<{ id: string }> }) => child.ports?.map(port => port.id) ?? [],
    )
    const occupiedIds = [node.id, other.id, ...edges.map(edge => edge.id)]
    for (const id of generatedIds) expect(occupiedIds).not.toContain(id)
  })

  it("rejects a named handle that is absent from measured bounds", async () => {
    const source = makeNode("a")
    mockLayout.mockResolvedValue({ children: [] })

    await expect(getLayoutedElements(
      [source, makeNode("b")],
      [makeEdge(source.id, "b", { sourceHandle: "missing" })],
      id => id === source.id ? internalNode(source, 240, 70, { source: [], target: [] }) : undefined,
    )).rejects.toThrow(/missing/i)
  })

  it("rejects non-finite measured handle geometry", async () => {
    const source = makeNode("a")
    const invalidHandle: Handle = {
      id: "out", nodeId: source.id, type: "source", position: Position.Right,
      x: Number.NaN, y: 20, width: 8, height: 8,
    }
    mockLayout.mockResolvedValue({ children: [] })

    await expect(getLayoutedElements(
      [source, makeNode("b")],
      [makeEdge(source.id, "b", { sourceHandle: "out" })],
      id => id === source.id
        ? internalNode(source, 240, 70, { source: [invalidHandle], target: [] })
        : undefined,
    )).rejects.toThrow(/invalid handle geometry/i)
  })

  it("rejects an edge that references a node outside the layout", async () => {
    mockLayout.mockResolvedValue({ children: [] })

    await expect(getLayoutedElements(
      [makeNode("a")],
      [makeEdge("a", "missing")],
    )).rejects.toThrow(/missing node/i)
  })
})
