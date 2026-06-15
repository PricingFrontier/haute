import { describe, it, expect, vi, afterEach } from "vitest"
import {
  deadBandWidth,
  inDeadGap,
  pointerExactlyOnConnector,
  resolveBodyDrop,
  topmostNodeAtPoint,
  type ConnectorBounds,
  type InternalNodeGeometry,
} from "../dropResolver"

function makeNode({
  x = 0,
  y = 0,
  width = 240,
  height = 70,
  source = [] as ConnectorBounds[],
  target = [] as ConnectorBounds[],
}: {
  x?: number
  y?: number
  width?: number
  height?: number
  source?: ConnectorBounds[]
  target?: ConnectorBounds[]
} = {}): InternalNodeGeometry {
  return {
    internals: {
      positionAbsolute: { x, y },
      handleBounds: { source, target },
    },
    measured: { width, height },
  }
}

function connector(partial: Partial<ConnectorBounds>): ConnectorBounds {
  return { id: null, x: 0, y: 0, width: 8, height: 8, ...partial }
}

type ElementsFromPoint = (x: number, y: number) => Element[]

function stubElementsFromPoint(elements: Element[]): ReturnType<typeof vi.fn> {
  const stub = vi.fn(() => elements)
  ;(document as { elementsFromPoint?: ElementsFromPoint }).elementsFromPoint =
    stub as unknown as ElementsFromPoint
  return stub
}

function makeNodeElement(nodeId: string): Element {
  const el = document.createElement("div")
  el.className = "react-flow__node"
  el.setAttribute("data-id", nodeId)
  return el
}

function makeConnectorElement(
  nodeId: string,
  handleId: string | null,
  kind: "source" | "target" = "source",
): Element {
  const el = document.createElement("div")
  el.className = `react-flow__handle ${kind}`
  el.setAttribute("data-nodeid", nodeId)
  if (handleId !== null) el.setAttribute("data-handleid", handleId)
  return el
}

afterEach(() => {
  delete (document as { elementsFromPoint?: ElementsFromPoint }).elementsFromPoint
  vi.restoreAllMocks()
})

describe("topmostNodeAtPoint", () => {
  it("returns null when the environment has no elementsFromPoint (jsdom default)", () => {
    expect(topmostNodeAtPoint({ x: 10, y: 10 })).toBeNull()
  })

  it("returns the first (topmost) node id in paint order", () => {
    stubElementsFromPoint([makeNodeElement("top"), makeNodeElement("under")])
    expect(topmostNodeAtPoint({ x: 10, y: 10 })).toBe("top")
  })

  it("climbs from inner elements to the node wrapper", () => {
    const wrapper = makeNodeElement("n1")
    const inner = document.createElement("span")
    wrapper.appendChild(inner)
    stubElementsFromPoint([inner])
    expect(topmostNodeAtPoint({ x: 10, y: 10 })).toBe("n1")
  })

  it("returns null when only edges/pane elements are under the pointer", () => {
    const edge = document.createElement("div")
    edge.className = "react-flow__edge"
    stubElementsFromPoint([edge])
    expect(topmostNodeAtPoint({ x: 10, y: 10 })).toBeNull()
  })
})

describe("pointerExactlyOnConnector", () => {
  it("returns false when the environment has no elementsFromPoint", () => {
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", null, "source")).toBe(false)
  })

  it("matches the exact connector element (node id + handle id + kind)", () => {
    stubElementsFromPoint([makeConnectorElement("n1", "out", "source")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", "out", "source")).toBe(true)
  })

  it("treats a missing data-handleid attribute as the null handle id", () => {
    stubElementsFromPoint([makeConnectorElement("n1", null, "source")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", null, "source")).toBe(true)
  })

  it("rejects a connector on a different node", () => {
    stubElementsFromPoint([makeConnectorElement("other", "out", "source")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", "out", "source")).toBe(false)
  })

  it("rejects a different handle id on the same node", () => {
    stubElementsFromPoint([makeConnectorElement("n1", "other", "source")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", "out", "source")).toBe(false)
  })

  it("rejects a connector of the wrong kind (input vs output)", () => {
    // SubmodelNode has null-id connectors on BOTH sides — kind must disambiguate.
    stubElementsFromPoint([makeConnectorElement("n1", null, "target")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", null, "source")).toBe(false)
  })

  it("returns false when only the node body is under the pointer (near-miss)", () => {
    stubElementsFromPoint([makeNodeElement("n1")])
    expect(pointerExactlyOnConnector({ x: 1, y: 1 }, "n1", "out", "source")).toBe(false)
  })
})

describe("deadBandWidth", () => {
  it.each([
    { bucket: "full", expected: 28 },
    { bucket: "medium", expected: 32 },
    { bucket: "compact", expected: 36 },
  ] as const)("uses the $bucket bucket constant on wide nodes", ({ bucket, expected }) => {
    expect(deadBandWidth(makeNode({ width: 240 }), bucket)).toBe(expected)
  })

  it("clamps to 25% of node width (40px edge-join root → 10px)", () => {
    expect(deadBandWidth(makeNode({ width: 40 }), "full")).toBe(10)
  })

  it("clamps the compact band on compact-size node bodies (112px → 28px)", () => {
    expect(deadBandWidth(makeNode({ width: 112 }), "compact")).toBe(28)
  })
})

describe("inDeadGap", () => {
  // Node occupies x: 100..340 (width 240). Full bucket → G = 28 → forward
  // band starts at 312; backward band ends at 128.
  const node = makeNode({ x: 100, y: 0, width: 240, height: 70 })

  it("forward drag: drop at the output end is dead, exactly at the boundary", () => {
    expect(inDeadGap({ x: 312, y: 35 }, node, "source", "full")).toBe(true)
    expect(inDeadGap({ x: 311.99, y: 35 }, node, "source", "full")).toBe(false)
    expect(inDeadGap({ x: 339, y: 35 }, node, "source", "full")).toBe(true)
  })

  it("forward drag: the band is unbounded outward (offset connector hit circles)", () => {
    expect(inDeadGap({ x: 352, y: 35 }, node, "source", "full")).toBe(true)
  })

  it("forward drag: the input end is NOT dead", () => {
    expect(inDeadGap({ x: 105, y: 35 }, node, "source", "full")).toBe(false)
  })

  it("backward drag: the band mirrors to the input end", () => {
    expect(inDeadGap({ x: 128, y: 35 }, node, "target", "full")).toBe(true)
    expect(inDeadGap({ x: 128.01, y: 35 }, node, "target", "full")).toBe(false)
    expect(inDeadGap({ x: 90, y: 35 }, node, "target", "full")).toBe(true)
    expect(inDeadGap({ x: 330, y: 35 }, node, "target", "full")).toBe(false)
  })

  it("scales the band with the zoom bucket", () => {
    // compact → G = 36 → forward band starts at 304.
    expect(inDeadGap({ x: 305, y: 35 }, node, "source", "compact")).toBe(true)
    expect(inDeadGap({ x: 305, y: 35 }, node, "source", "full")).toBe(false)
  })
})

describe("resolveBodyDrop", () => {
  it("returns null when the node has no connector of the wanted kind", () => {
    const node = makeNode({ source: [connector({ id: null })], target: [] })
    expect(resolveBodyDrop(node, "target", { x: 10, y: 10 })).toBeNull()
  })

  it("returns null when handleBounds is missing entirely", () => {
    const node: InternalNodeGeometry = {
      internals: { positionAbsolute: { x: 0, y: 0 } },
      measured: { width: 240, height: 70 },
    }
    expect(resolveBodyDrop(node, "target", { x: 10, y: 10 })).toBeNull()
  })

  it("returns the single candidate's id verbatim — sentinel included", () => {
    const node = makeNode({
      target: [connector({ id: "__default_target", x: -4, y: 31 })],
    })
    expect(resolveBodyDrop(node, "target", { x: 10, y: 10 })).toEqual({
      handleId: "__default_target",
    })
  })

  it("returns a null handle id for id-less default connectors", () => {
    const node = makeNode({ source: [connector({ id: null, x: 236, y: 31 })] })
    expect(resolveBodyDrop(node, "source", { x: 10, y: 10 })).toEqual({ handleId: null })
  })

  it("never resolves to an empty-string handle id", () => {
    const node = makeNode({ source: [connector({ id: "", x: 236, y: 31 })] })
    expect(resolveBodyDrop(node, "source", { x: 10, y: 10 })).toEqual({ handleId: null })
  })

  it("picks the geometrically nearest of stacked connectors (above/below midpoint)", () => {
    // Two-port stack at 1/3 and 2/3 of a 70px node at y=0: centres y≈23.3, 46.7.
    const node = makeNode({
      x: 0,
      y: 0,
      source: [
        connector({ id: "upper", x: 236, y: 19 }),
        connector({ id: "lower", x: 236, y: 43 }),
      ],
    })
    expect(resolveBodyDrop(node, "source", { x: 120, y: 10 })).toEqual({ handleId: "upper" })
    expect(resolveBodyDrop(node, "source", { x: 120, y: 60 })).toEqual({ handleId: "lower" })
  })

  it("measures distance in flow coordinates from the node's absolute position", () => {
    const node = makeNode({
      x: 1000,
      y: 500,
      source: [
        connector({ id: "upper", x: 236, y: 19 }),
        connector({ id: "lower", x: 236, y: 43 }),
      ],
    })
    expect(resolveBodyDrop(node, "source", { x: 1120, y: 560 })).toEqual({
      handleId: "lower",
    })
  })

  it("breaks exact ties topmost (smallest y)", () => {
    const node = makeNode({
      target: [
        connector({ id: "lower", x: 0, y: 40 }),
        connector({ id: "upper", x: 0, y: 0 }),
      ],
    })
    // Drop equidistant from both centres (y = 24 between centres at 4 and 44).
    expect(resolveBodyDrop(node, "target", { x: 4, y: 24 })).toEqual({ handleId: "upper" })
  })

  it("resolves nearest regardless of occupancy (ruling 5 — no free-role preference)", () => {
    // The resolver has no occupancy input at all: nearest is nearest.
    const node = makeNode({
      target: [
        connector({ id: "base", x: 4, y: 31 }),
        connector({ id: "join", x: 16, y: 0 }),
      ],
    })
    expect(resolveBodyDrop(node, "target", { x: 8, y: 34 })).toEqual({ handleId: "base" })
  })

  it("excludes zero-area connectors (handle-hidden submodel resolver ports)", () => {
    const node = makeNode({
      target: [
        connector({ id: "in__child1", x: 0, y: 31, width: 0, height: 0 }),
        connector({ id: null, x: -4, y: 31 }),
      ],
    })
    expect(resolveBodyDrop(node, "target", { x: 0, y: 31 })).toEqual({ handleId: null })
  })

  it("excludes ≤4px² connectors when a larger sibling exists (CSS-regression belt-and-braces)", () => {
    const node = makeNode({
      target: [
        connector({ id: "in__child1", x: 0, y: 31, width: 2, height: 2 }),
        connector({ id: null, x: -4, y: 31 }),
      ],
    })
    expect(resolveBodyDrop(node, "target", { x: 0, y: 31 })).toEqual({ handleId: null })
  })

  it("keeps all-tiny candidate sets (edge-join connectors are 2×2 by design)", () => {
    const node = makeNode({
      x: 0,
      y: 0,
      width: 40,
      height: 34,
      target: [
        connector({ id: "base", x: 3, y: 16, width: 2, height: 2 }),
        connector({ id: "join", x: 19, y: 5, width: 2, height: 2 }),
      ],
    })
    expect(resolveBodyDrop(node, "target", { x: 5, y: 20 })).toEqual({ handleId: "base" })
    expect(resolveBodyDrop(node, "target", { x: 20, y: 4 })).toEqual({ handleId: "join" })
  })

  it("returns null when every candidate is zero-area", () => {
    const node = makeNode({
      target: [connector({ id: "in__child1", x: 0, y: 31, width: 0, height: 0 })],
    })
    expect(resolveBodyDrop(node, "target", { x: 0, y: 31 })).toBeNull()
  })
})
