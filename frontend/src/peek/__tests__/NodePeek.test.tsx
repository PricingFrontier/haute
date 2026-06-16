/**
 * T4 — NodePeek window behaviour (node-explosion design §3.5).
 *
 * NodePeek is a child of <ReactFlow> rendered via ViewportPortal. It reads the
 * live anchor from the internal store's nodeLookup, resolves the node via
 * getNode, and renders the peek body via the registry. These tests seed the
 * internal store directly (the FlowGeometrySeed idiom from PipelineNode.test)
 * so the anchor / getNode paths run for real against a known nodeLookup.
 *
 * Covered here:
 *  - opens from a store-driven nodeId, anchored to the node;
 *  - header affordances (node-peek-drill-in / node-peek-close) wired;
 *  - stale-node auto-close: removing the node from nodeLookup closes the peek;
 *  - body mounts and click-to-drill passes the child id up through onDrillIn.
 *
 * The App-level Escape arbitration (T4h) and the persist-inert invariant live in
 * App.integration.test.tsx, where the real contextMenu state and save pipeline
 * are available.
 */
import { useLayoutEffect, useState } from "react"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react"
import {
  ReactFlowProvider,
  useStoreApi,
  type InternalNode,
  type Node,
} from "@xyflow/react"
import NodePeek from "../NodePeek"

// SubmodelPeekBody fetches on mount; mock the network + layout so the body
// renders deterministically without ELK / HTTP.
vi.mock("../../api/client", () => ({
  loadSubmodel: vi.fn(),
}))
vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) =>
    nodes.map((n, i) => ({ ...n, position: { x: i * 300, y: 0 } })),
  ),
}))

import { loadSubmodel } from "../../api/client"
const mockLoad = vi.mocked(loadSubmodel)

/** A submodel internal-store node with a measured height for the anchor calc. */
function submodelInternalNode(id = "submodel__pricing", label = "pricing"): InternalNode {
  const userNode: Node = {
    id,
    type: "submodel",
    position: { x: 100, y: 200 },
    measured: { width: 240, height: 80 },
    data: { label, nodeType: "submodel", config: {} },
  }
  return {
    ...userNode,
    measured: { width: 240, height: 80 },
    internals: {
      positionAbsolute: { x: 100, y: 200 },
      z: 0,
      userNode,
    },
  } as InternalNode
}

/**
 * Seeds the internal store's nodeLookup + nodes so NodePeek's useStore anchor
 * and useReactFlow().getNode both resolve, THEN mounts NodePeek (gated on a
 * ready flag so NodePeek's first render already sees a populated store — in the
 * real app the node always exists before a peek opens, so this models reality
 * and avoids a spurious initial stale-node onClose from an empty store).
 *
 * Also seeds a `domNode` carrying the `.react-flow__viewport-portal` target so
 * ViewportPortal (which a real <ReactFlow> mounts, but a bare
 * <ReactFlowProvider> does not) resolves its portal in jsdom. Exposes a setter
 * so a test can remove the node mid-flight (stale-node auto-close). The full
 * mount-inside-<ReactFlow> path is covered in App.integration.test.tsx.
 */
function Harness({
  nodeId,
  onClose,
  onDrillIn,
  nodes,
  seedRef,
}: {
  nodeId: string
  onClose: () => void
  onDrillIn: (nodeId: string, selectChildId?: string) => void
  nodes: InternalNode[]
  seedRef?: { current: ((nodes: InternalNode[]) => void) | null }
}) {
  const store = useStoreApi()
  const [ready, setReady] = useState(false)
  useLayoutEffect(() => {
    const domNode = document.createElement("div")
    const portal = document.createElement("div")
    portal.className = "react-flow__viewport-portal"
    domNode.appendChild(portal)
    document.body.appendChild(domNode)
    const apply = (ns: InternalNode[]) =>
      store.setState({
        domNode,
        nodeLookup: new Map(ns.map((n) => [n.id, n])),
        nodes: ns.map((n) => n.internals.userNode),
      })
    apply(nodes)
    if (seedRef) seedRef.current = apply
    setReady(true)
    return () => {
      domNode.remove()
    }
    // Seed once on mount; the seedRef setter handles later mutations.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (!ready) return null
  return <NodePeek nodeId={nodeId} onClose={onClose} onDrillIn={onDrillIn} />
}

function renderPeek({
  nodeId = "submodel__pricing",
  onClose = vi.fn(),
  onDrillIn = vi.fn(),
  nodes = [submodelInternalNode()],
  seedRef,
}: {
  nodeId?: string
  onClose?: () => void
  onDrillIn?: (nodeId: string, selectChildId?: string) => void
  nodes?: InternalNode[]
  seedRef?: { current: ((nodes: InternalNode[]) => void) | null }
} = {}) {
  return render(
    <ReactFlowProvider>
      <Harness nodeId={nodeId} onClose={onClose} onDrillIn={onDrillIn} nodes={nodes} seedRef={seedRef} />
    </ReactFlowProvider>,
  )
}

describe("NodePeek (T4)", () => {
  beforeEach(() => {
    mockLoad.mockReset()
    // Default: an empty submodel so the body settles quickly (empty state).
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: { nodes: [], edges: [] },
    } as Awaited<ReturnType<typeof loadSubmodel>>)
  })
  afterEach(cleanup)

  it("opens and anchors the peek when the node is present in the store", async () => {
    renderPeek()
    const peek = await screen.findByTestId("node-peek")
    expect(peek).toBeInTheDocument()
    // Anchored below the node: y = posY (200) + height (80) + gap (12) = 292.
    expect(peek.style.transform).toContain("translate(100px, 292px)")
  })

  it("renders the header with the node label and the drill-in + close affordances", async () => {
    renderPeek()
    await screen.findByTestId("node-peek")
    expect(screen.getByTestId("node-peek-header")).toHaveTextContent(/PEEK · pricing/)
    expect(screen.getByTestId("node-peek-drill-in")).toBeInTheDocument()
    expect(screen.getByTestId("node-peek-close")).toBeInTheDocument()
  })

  it("the close button calls onClose", async () => {
    const onClose = vi.fn()
    renderPeek({ onClose })
    await screen.findByTestId("node-peek")
    fireEvent.click(screen.getByTestId("node-peek-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("the header Open button drills in with the peeked node id and no child id", async () => {
    const onDrillIn = vi.fn()
    renderPeek({ onDrillIn })
    await screen.findByTestId("node-peek")
    fireEvent.click(screen.getByTestId("node-peek-drill-in"))
    expect(onDrillIn).toHaveBeenCalledWith("submodel__pricing")
  })

  it("renders nothing for a node id that is not in the store", () => {
    renderPeek({ nodeId: "submodel__ghost", nodes: [submodelInternalNode()] })
    expect(screen.queryByTestId("node-peek")).not.toBeInTheDocument()
  })

  it("auto-closes when the peeked node disappears from the store (stale-node)", async () => {
    const onClose = vi.fn()
    const seedRef: { current: ((nodes: InternalNode[]) => void) | null } = { current: null }
    renderPeek({ onClose, seedRef })
    await screen.findByTestId("node-peek")
    expect(onClose).not.toHaveBeenCalled()

    // Remove the node from the store — anchor selector returns null next render.
    act(() => {
      seedRef.current!([])
    })
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1)
    })
    expect(screen.queryByTestId("node-peek")).not.toBeInTheDocument()
  })

  it("mounts the submodel body and click-to-drill bubbles the child id up", async () => {
    const onDrillIn = vi.fn()
    mockLoad.mockResolvedValue({
      status: "ok",
      submodel_name: "pricing",
      graph: {
        nodes: [
          { id: "child_a", type: "polars", position: { x: 0, y: 0 }, data: { label: "child_a", nodeType: "polars", config: {} } },
        ],
        edges: [],
      },
    } as Awaited<ReturnType<typeof loadSubmodel>>)
    renderPeek({ onDrillIn })
    await screen.findByTestId("node-peek")
    const mini = await screen.findByTestId("node-peek-mini-node-child_a")
    fireEvent.click(mini)
    // NodePeek wraps the body's onDrillIn(childId) into onDrillIn(nodeId, childId).
    expect(onDrillIn).toHaveBeenCalledWith("submodel__pricing", "child_a")
  })

  it("a click inside the peek does not propagate to ancestor (pane) handlers", async () => {
    const onClose = vi.fn()
    const ancestorClick = vi.fn()
    // The ancestor onClick stands in for the App-level pane/click handlers. A
    // React portal still bubbles events through the React parent tree, so the
    // container's stopPropagation is what must keep this handler from firing.
    render(
      <div onClick={ancestorClick}>
        <ReactFlowProvider>
          <Harness nodeId="submodel__pricing" onClose={onClose} onDrillIn={vi.fn()} nodes={[submodelInternalNode()]} />
        </ReactFlowProvider>
      </div>,
    )
    const peek = await screen.findByTestId("node-peek")
    // A bare click on the chrome (not a button) must be swallowed by the
    // container's stopPropagation — the pane-click handler never sees it.
    fireEvent.click(peek)
    expect(ancestorClick).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })
})
