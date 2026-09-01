/**
 * Focused behavioral tests for less common useNodeHandlers paths.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useNodeHandlers from "../useNodeHandlers"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"
import useToastStore from "../../stores/useToastStore"
import { makeNode } from "../../test-utils/factories"

vi.mock("../../utils/layout", () => ({
  getLayoutedElements: vi.fn(async (nodes: Node[]) => nodes),
}))

function makeParams() {
  return {
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 10 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn(),
    setNodesAndEdges: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    fitView: vi.fn(),
    resolveNodeIdentities: vi.fn(async (nodes: readonly Node[]) => [...nodes]),
  }
}

describe("useNodeHandlers — handleRenameNode", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({ previews: {}, columnCache: {} })
    useUIStore.setState({ renameDialog: null, submodelDialog: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("opens the rename dialog with correct nodeId and currentLabel", () => {
    // Catches: if handleRenameNode is accidentally removed or the
    // setRenameDialog call is broken, the user would click "Rename"
    // in the context menu and nothing would happen.
    const params = makeParams()
    const n1 = makeNode("n1", "polars", { data: { label: "My Transform", nodeType: "polars", config: {} } })
    params.graphRef.current = { nodes: [n1], edges: [] }

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleRenameNode("n1")
    })

    const dialog = useUIStore.getState().renameDialog
    expect(dialog).toEqual({
      nodeId: "n1",
      currentLabel: "My Transform",
    })
  })

  it("does nothing when the node is not found in graphRef", () => {
    // Catches: if the early return guard is removed, setRenameDialog
    // would be called with undefined data, potentially crashing the
    // RenameDialog component.
    const params = makeParams()
    params.graphRef.current = { nodes: [], edges: [] }

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleRenameNode("nonexistent")
    })

    const dialog = useUIStore.getState().renameDialog
    expect(dialog).toBeNull()
  })

  it("handleDeleteNode clears lastSelectedNodeRef when deleting the selected node", () => {
    // Catches: stale lastSelectedNodeRef causes the next node-click to
    // skip selection because the ref still points to the deleted node.
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    params.lastSelectedNodeRef.current = n1

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(params.lastSelectedNodeRef.current).toBeNull()
  })

  it("handleDeleteNode does NOT clear lastSelectedNodeRef when deleting a different node", () => {
    // Catches: if the id guard is missing, deleting any node would
    // clear the selection ref, confusing keyboard navigation.
    const params = makeParams()
    const n1 = makeNode("n1")
    const n2 = makeNode("n2")
    params.graphRef.current = { nodes: [n1, n2], edges: [] }
    params.lastSelectedNodeRef.current = n2

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(params.lastSelectedNodeRef.current).toBe(n2)
  })

  // ── Issue #14: renameDialog cleared on node delete ───────────

  it("handleDeleteNode clears renameDialog when it references the deleted node", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useUIStore.setState({ renameDialog: { nodeId: "n1", currentLabel: "Test" } })

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(useUIStore.getState().renameDialog).toBeNull()
  })

  it("handleDeleteNode does NOT clear renameDialog when it references a different node", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useUIStore.setState({ renameDialog: { nodeId: "n2", currentLabel: "Other" } })

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(useUIStore.getState().renameDialog).toEqual({ nodeId: "n2", currentLabel: "Other" })
  })

  // ── Issue #8: submodelDialog cleared on node delete ──────────

  it("handleDeleteNode clears submodelDialog when it references the deleted node", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useUIStore.setState({ submodelDialog: { nodeIds: ["n1", "n2"] } })

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(useUIStore.getState().submodelDialog).toBeNull()
  })

  it("handleDeleteNode does NOT clear submodelDialog when it does not reference the deleted node", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useUIStore.setState({ submodelDialog: { nodeIds: ["n3", "n4"] } })

    const { result } = renderHook(() => useNodeHandlers(params))

    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["n3", "n4"] })
  })

  it("handleDeleteNode handles null submodelDialog and renameDialog gracefully", () => {
    const params = makeParams()
    const n1 = makeNode("n1")
    params.graphRef.current = { nodes: [n1], edges: [] }
    useUIStore.setState({ submodelDialog: null, renameDialog: null })

    const { result } = renderHook(() => useNodeHandlers(params))

    // Should not throw
    act(() => {
      result.current.handleDeleteNode("n1")
    })

    expect(useUIStore.getState().submodelDialog).toBeNull()
    expect(useUIStore.getState().renameDialog).toBeNull()
  })

  it("rejects an instance whose resolved identity does not match", async () => {
    const params = makeParams()
    const source = makeNode("source", "submodel", { data: { label: "Score", nodeType: "submodel", config: { definitionId: "def", alias: "score" } } })
    params.graphRef.current = { nodes: [source], edges: [] }
    params.resolveNodeIdentities = vi.fn(async () => [makeNode("wrong")])
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => { await result.current.handleCreateInstance("source") })
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/invalid node/)
  })

  it("applies automatic layout to a non-empty graph", async () => {
    const params = makeParams()
    params.graphRef.current = { nodes: [makeNode("layout")], edges: [] }
    const { result } = renderHook(() => useNodeHandlers(params))
    await act(async () => { await result.current.handleAutoLayout() })
    expect(params.setNodes).toHaveBeenCalled()
  })

  it("does not apply a resolved instance after the graph changes", async () => {
    const params = makeParams()
    const source = makeNode("source")
    params.graphRef.current = { nodes: [source], edges: [] }
    let resolveIdentity!: (nodes: Node[]) => void
    params.resolveNodeIdentities = vi.fn(() => new Promise((resolve) => {
      resolveIdentity = resolve
    }))
    const { result } = renderHook(() => useNodeHandlers(params))

    let creation!: Promise<void>
    act(() => {
      creation = result.current.handleCreateInstance("source")
    })
    await vi.waitFor(() => expect(params.resolveNodeIdentities).toHaveBeenCalledOnce())

    act(() => {
      params.graphRef.current = { nodes: [source, makeNode("newer-edit")], edges: [] }
      resolveIdentity([makeNode("polars_11")])
    })
    await act(async () => {
      await creation
    })

    expect(params.setNodes).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/graph changed/)
  })
})
