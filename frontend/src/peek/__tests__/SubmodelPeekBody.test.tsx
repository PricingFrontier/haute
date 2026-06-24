/**
 * SubmodelPeekBody — renders the wrapper's internals in a read-only React Flow
 * using haute's real node cards (the shared nodeTypes registry) plus the derived
 * I/O boundary. Tests cover the load states and the render-gate (AGENTS.md rule
 * 3): every internal node + boundary port surfaces as a flow node. The boundary
 * derivation itself is unit-tested in utils/__tests__/submodelBoundary.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import SubmodelPeekBody from "../SubmodelPeekBody"
// ResizeObserver (needed by the inner React Flow) is polyfilled globally in
// src/setupTests.ts.

vi.mock("../../api/client", () => ({
  loadSubmodel: vi.fn(),
}))
// Identity layout — tests assert render-gate completeness, not ELK maths.
vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) =>
    nodes.map((n, i) => ({ ...n, position: { x: i * 300, y: 0 } })),
  ),
}))

import { loadSubmodel } from "../../api/client"
import useUIStore from "../../stores/useUIStore"
const mockLoad = vi.mocked(loadSubmodel)

function childNode(i: number): Node {
  return {
    id: `child_${i}`,
    type: "polars",
    position: { x: 0, y: 0 },
    data: { label: `child_${i}`, nodeType: "polars", config: {} },
  }
}

function peekNode(): Node {
  return {
    id: "submodel__pricing",
    type: "submodel",
    position: { x: 0, y: 0 },
    data: { label: "pricing", nodeType: "submodel", config: {} },
  }
}

function renderBody(
  opts: {
    onDrillIn?: (selectChildId?: string) => void
    parentNodes?: Node[]
    parentEdges?: Edge[]
  } = {},
) {
  return render(
    <SubmodelPeekBody
      node={peekNode()}
      accent="#8b5cf6"
      onDrillIn={opts.onDrillIn ?? (() => {})}
      parentNodes={opts.parentNodes}
      parentEdges={opts.parentEdges}
    />,
  )
}

/** Ids of the nodes React Flow actually rendered (the real cards + ports). */
function flowNodeIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".react-flow__node"))
    .map((el) => el.getAttribute("data-id"))
    .filter((id): id is string => id !== null)
}

describe("SubmodelPeekBody", () => {
  beforeEach(() => {
    mockLoad.mockReset()
    useUIStore.setState({ hoveredNodeId: null })
  })
  afterEach(() => {
    cleanup()
    useUIStore.setState({ hoveredNodeId: null })
  })

  /** Inline opacity React Flow applied to a peek node's wrapper element. */
  function nodeOpacity(container: HTMLElement, id: string): string {
    const el = container.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`)
    if (!el) throw new Error(`node ${id} not rendered`)
    return el.style.opacity
  }

  it("shows the loading state before the fetch resolves", () => {
    let resolve!: (v: Awaited<ReturnType<typeof loadSubmodel>>) => void
    mockLoad.mockReturnValue(
      new Promise((r) => {
        resolve = r
      }) as ReturnType<typeof loadSubmodel>,
    )
    renderBody()
    expect(screen.getByTestId("node-peek-loading")).toBeInTheDocument()
    resolve({ status: "ok", submodel_name: "pricing", graph: { nodes: [], edges: [] } })
  })

  it("shows the empty state when the submodel has no internal nodes", async () => {
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [], edges: [] } })
    renderBody()
    expect(await screen.findByTestId("node-peek-empty")).toBeInTheDocument()
  })

  it("surfaces a fetch error with a retry that re-fetches", async () => {
    mockLoad.mockRejectedValueOnce(new Error("boom"))
    renderBody()
    expect(await screen.findByTestId("node-peek-error")).toBeInTheDocument()
    mockLoad.mockResolvedValueOnce({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0)], edges: [] },
    })
    fireEvent.click(screen.getByText("Retry"))
    expect(await screen.findByTestId("node-peek-canvas")).toBeInTheDocument()
  })

  it.each([3, 12])("renders every internal node as a real flow card (M=%i)", async (m) => {
    const nodes = Array.from({ length: m }, (_v, i) => childNode(i))
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    const { container } = renderBody()
    await screen.findByTestId("node-peek-canvas")
    await waitFor(() => {
      const ids = flowNodeIds(container)
      for (let i = 0; i < m; i++) expect(ids).toContain(`child_${i}`)
    })
  })

  it("renders the derived I/O boundary ports alongside the internal nodes", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0), childNode(1)], edges: [] },
    })
    const parentNodes: Node[] = [
      { id: "src_a", type: "polars", position: { x: 0, y: 0 }, data: { label: "Source A", nodeType: "polars", config: {} } },
      { id: "tgt_b", type: "polars", position: { x: 0, y: 0 }, data: { label: "Target B", nodeType: "polars", config: {} } },
      peekNode(),
    ]
    const parentEdges = [
      { id: "e1", source: "src_a", target: "submodel__pricing", targetHandle: "in__child_0" },
      { id: "e2", source: "submodel__pricing", target: "tgt_b", sourceHandle: "out__child_1" },
    ] as unknown as Edge[]
    const { container } = renderBody({ parentNodes, parentEdges })
    await screen.findByTestId("node-peek-canvas")
    await waitFor(() => {
      const ids = flowNodeIds(container)
      expect(ids).toContain("port_in__child_0__src_a") // one input frame, per link
      expect(ids).toContain("port_out__child_1") // one output frame, per emitter
    })
    // Counts reflect the wrapper's CONTENTS (2 nodes), not the derived boundary.
    expect(screen.getByTestId("node-peek-counts")).toHaveTextContent("2 nodes")
  })

  it("renders no boundary ports when the submodel has no parent connections", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0)], edges: [] },
    })
    const { container } = renderBody()
    await screen.findByTestId("node-peek-canvas")
    await waitFor(() => expect(flowNodeIds(container)).toEqual(["child_0"]))
  })

  it("reports a bounding-box preferred panel size after layout", async () => {
    const onPreferredSize = vi.fn<(size: { width: number; height: number }) => void>()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0), childNode(1)], edges: [] },
    })
    render(
      <SubmodelPeekBody
        node={peekNode()}
        accent="#8b5cf6"
        onDrillIn={() => {}}
        onPreferredSize={onPreferredSize}
      />,
    )
    await waitFor(() =>
      expect(onPreferredSize).toHaveBeenCalledWith(
        expect.objectContaining({ width: expect.any(Number), height: expect.any(Number) }),
      ),
    )
  })

  it("lights the internal cone and dims the rest when an external producer is hovered (#3)", async () => {
    // child_0 is fed by external src_a; child_1 is unrelated. Hovering src_a on
    // the parent canvas must light child_0 (+ its port) and dim child_1.
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0), childNode(1)], edges: [] },
    })
    const parentEdges = [
      { id: "e1", source: "src_a", target: "submodel__pricing", targetHandle: "in__child_0" },
    ] as unknown as Edge[]
    const { container } = renderBody({ parentEdges })
    await screen.findByTestId("node-peek-canvas")
    await waitFor(() => expect(flowNodeIds(container)).toContain("port_in__child_0__src_a"))

    useUIStore.setState({ hoveredNodeId: "src_a" })
    await waitFor(() => expect(nodeOpacity(container, "child_1")).toBe("0.18"))
    // The lit child is left untouched (not dimmed).
    expect(nodeOpacity(container, "child_0")).not.toBe("0.18")
  })

  it("dims nothing when the hovered node is unrelated to the wrapper (#3)", async () => {
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0), childNode(1)], edges: [] },
    })
    const { container } = renderBody()
    await screen.findByTestId("node-peek-canvas")
    await waitFor(() => expect(flowNodeIds(container)).toContain("child_0"))

    useUIStore.setState({ hoveredNodeId: "some_other_node" })
    // No boundary crossing → lighting inactive → nothing dimmed.
    await new Promise((r) => setTimeout(r, 0))
    expect(nodeOpacity(container, "child_0")).not.toBe("0.18")
    expect(nodeOpacity(container, "child_1")).not.toBe("0.18")
  })

  it("drills into a clicked internal node", async () => {
    const onDrillIn = vi.fn<(selectChildId?: string) => void>()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [childNode(0)], edges: [] },
    })
    const { container } = renderBody({ onDrillIn })
    await screen.findByTestId("node-peek-canvas")
    const card = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('.react-flow__node[data-id="child_0"]')
      if (!el) throw new Error("node not rendered")
      return el
    })
    fireEvent.click(card)
    await waitFor(() => expect(onDrillIn).toHaveBeenCalledWith("child_0"))
  })
})
