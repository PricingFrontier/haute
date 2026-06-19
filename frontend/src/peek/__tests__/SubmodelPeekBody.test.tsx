/**
 * T3 — submodel peek body render gate (AGENTS.md rule 3).
 *
 * Every internal node must surface as a `node-peek-mini-node-<label>` testid;
 * scale-to-fit must not drop nodes (M=25 proves it). Zero children → explicit
 * empty state. The fetch-error path must surface a retry that re-calls the
 * fetch. Click-to-drill: a mini-node click drills in with that child id.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react"
import type { Node } from "@xyflow/react"
import SubmodelPeekBody from "../SubmodelPeekBody"

vi.mock("../../api/client", () => ({
  loadSubmodel: vi.fn(),
}))
// Identity layout — the test asserts render-gate completeness, not ELK maths.
vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) =>
    nodes.map((n, i) => ({ ...n, position: { x: i * 300, y: 0 } })),
  ),
}))

import { loadSubmodel } from "../../api/client"
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

function renderBody(onDrillIn = vi.fn()) {
  return render(
    <SubmodelPeekBody node={peekNode()} accent="#8b5cf6" onDrillIn={onDrillIn} />,
  )
}

describe("SubmodelPeekBody (T3 render gate)", () => {
  beforeEach(() => mockLoad.mockReset())
  afterEach(cleanup)

  it("shows the loading state before the fetch resolves", () => {
    let resolve!: (v: Awaited<ReturnType<typeof loadSubmodel>>) => void
    mockLoad.mockReturnValue(new Promise((r) => { resolve = r }) as ReturnType<typeof loadSubmodel>)
    renderBody()
    expect(screen.getByTestId("node-peek-loading")).toBeInTheDocument()
    resolve({ status: "ok", submodel_name: "pricing", graph: { nodes: [], edges: [] } })
  })

  it.each([3, 25])("surfaces every internal node as a mini-node (M=%i)", async (m) => {
    const nodes = Array.from({ length: m }, (_v, i) => childNode(i))
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    renderBody()
    await waitFor(() => {
      expect(screen.getAllByTestId(/^node-peek-mini-node-/)).toHaveLength(m)
    })
    // Spot-check a couple of specific labels surface.
    expect(screen.getByTestId("node-peek-mini-node-child_0")).toBeInTheDocument()
    expect(screen.getByTestId(`node-peek-mini-node-child_${m - 1}`)).toBeInTheDocument()
  })

  it("renders the graph in a scrollable window (overflow container)", async () => {
    const nodes = Array.from({ length: 12 }, (_v, i) => childNode(i))
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    renderBody()
    const scroll = await screen.findByTestId("node-peek-scroll")
    // It's a window into the canvas: content overflows and scrolls, it does not
    // shrink large graphs to an illegible thumbnail.
    expect(scroll).toHaveStyle({ overflow: "auto" })
  })

  it("derives I/O ports + dashed boundary links from the parent graph", async () => {
    // Submodel has two children; the parent feeds child_0 from "Source A" and
    // draws child_1 out to "Target B". The peek must surface both as ports with
    // dashed links — the same boundary a drill-in would build.
    const nodes = [childNode(0), childNode(1)]
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    const parentNodes: Node[] = [
      { id: "src_a", type: "polars", position: { x: 0, y: 0 }, data: { label: "Source A", nodeType: "polars", config: {} } },
      { id: "tgt_b", type: "polars", position: { x: 0, y: 0 }, data: { label: "Target B", nodeType: "polars", config: {} } },
      peekNode(),
    ]
    const parentEdges = [
      { id: "e1", source: "src_a", target: "submodel__pricing", targetHandle: "in__child_0" },
      { id: "e2", source: "submodel__pricing", target: "tgt_b", sourceHandle: "out__child_1" },
    ] as unknown as import("@xyflow/react").Edge[]
    render(
      <SubmodelPeekBody
        node={peekNode()}
        accent="#8b5cf6"
        onDrillIn={vi.fn()}
        parentNodes={parentNodes}
        parentEdges={parentEdges}
      />,
    )
    // Ports reflect the wrapper's own frames: labelled by the wrapped node each
    // binds to (the frame), NOT by the external neighbour it connects to.
    expect(await screen.findByTestId("node-peek-port-child_0")).toBeInTheDocument()
    expect(screen.getByTestId("node-peek-port-child_1")).toBeInTheDocument()
    // Ports are NOT drillable mini-nodes — the internal count stays at 2.
    expect(screen.getAllByTestId(/^node-peek-mini-node-/)).toHaveLength(2)
    expect(screen.getByText("2 nodes")).toBeInTheDocument()
    // Both boundary links render dashed; the submodel has no internal edges.
    expect(document.querySelectorAll('line[stroke-dasharray="4 2"]')).toHaveLength(2)
  })

  it("suppresses an orphan input port whose child is no longer in the submodel", async () => {
    // child_0 is the only internal node, but the parent still feeds a stale
    // `in__ghost` handle (ghost was deleted from the submodel). No floating,
    // edgeless port pill — the input path mirrors the output path's filtering.
    const nodes = [childNode(0)]
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    const parentNodes: Node[] = [
      { id: "src_a", type: "polars", position: { x: 0, y: 0 }, data: { label: "Source A", nodeType: "polars", config: {} } },
      peekNode(),
    ]
    const parentEdges = [
      { id: "e1", source: "src_a", target: "submodel__pricing", targetHandle: "in__ghost" },
    ] as unknown as import("@xyflow/react").Edge[]
    render(
      <SubmodelPeekBody
        node={peekNode()}
        accent="#8b5cf6"
        onDrillIn={vi.fn()}
        parentNodes={parentNodes}
        parentEdges={parentEdges}
      />,
    )
    await screen.findByTestId("node-peek-mini-node-child_0")
    expect(screen.queryAllByTestId(/^node-peek-port-/)).toHaveLength(0)
  })

  it("renders no ports when the submodel has no parent connections", async () => {
    const nodes = [childNode(0)]
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes, edges: [] } })
    render(
      <SubmodelPeekBody node={peekNode()} accent="#8b5cf6" onDrillIn={vi.fn()} parentNodes={[]} parentEdges={[]} />,
    )
    await screen.findByTestId("node-peek-mini-node-child_0")
    expect(screen.queryAllByTestId(/^node-peek-port-/)).toHaveLength(0)
  })

  it("M=0 renders the empty state, not a mini-DAG", async () => {
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [], edges: [] } })
    renderBody()
    await waitFor(() => {
      expect(screen.getByTestId("node-peek-empty")).toBeInTheDocument()
    })
    expect(screen.queryAllByTestId(/^node-peek-mini-node-/)).toHaveLength(0)
  })

  it("fetch rejection shows the error state with a retry that re-calls loadSubmodel", async () => {
    mockLoad.mockRejectedValueOnce(new Error("boom"))
    renderBody()
    await waitFor(() => {
      expect(screen.getByTestId("node-peek-error")).toBeInTheDocument()
    })
    expect(mockLoad).toHaveBeenCalledTimes(1)
    // Retry now succeeds.
    mockLoad.mockResolvedValueOnce({ status: "ok", submodel_name: "pricing", graph: { nodes: [childNode(0)], edges: [] } })
    fireEvent.click(screen.getByText("Retry"))
    await waitFor(() => {
      expect(mockLoad).toHaveBeenCalledTimes(2)
      expect(screen.getByTestId("node-peek-mini-node-child_0")).toBeInTheDocument()
    })
  })

  it("clicking a mini-node drills in with that child's id (click-to-drill)", async () => {
    const onDrillIn = vi.fn()
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [childNode(1)], edges: [] } })
    renderBody(onDrillIn)
    await waitFor(() => {
      expect(screen.getByTestId("node-peek-mini-node-child_1")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("node-peek-mini-node-child_1"))
    expect(onDrillIn).toHaveBeenCalledWith("child_1")
  })

  it("Enter on a focused mini-node drills in (keyboard activation)", async () => {
    const onDrillIn = vi.fn()
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [childNode(2)], edges: [] } })
    renderBody(onDrillIn)
    await waitFor(() => {
      expect(screen.getByTestId("node-peek-mini-node-child_2")).toBeInTheDocument()
    })
    fireEvent.keyDown(screen.getByTestId("node-peek-mini-node-child_2"), { key: "Enter" })
    expect(onDrillIn).toHaveBeenCalledWith("child_2")
  })

  it("mini-nodes are role=button (accessible activation)", async () => {
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [childNode(0)], edges: [] } })
    renderBody()
    await waitFor(() => {
      expect(screen.getByTestId("node-peek-mini-node-child_0")).toHaveAttribute("role", "button")
    })
  })

  it("derives the submodel name from the node id (strips submodel__ prefix)", async () => {
    mockLoad.mockResolvedValue({ status: "ok", submodel_name: "pricing", graph: { nodes: [], edges: [] } })
    renderBody()
    await waitFor(() => {
      expect(mockLoad).toHaveBeenCalledWith("pricing")
    })
  })
})
