import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useKeyboardShortcuts from "../useKeyboardShortcuts"
import useUIStore from "../../stores/useUIStore"
import useToastStore from "../../stores/useToastStore"
import { makeNode } from "../../test-utils/factories"
import type { NodeTypeValue } from "../../utils/nodeTypes"

function resolvedIdentityGraph(
  nodes: readonly Node[],
  edges: readonly Edge[],
): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: nodes.map((node) => ({
      ...node,
      data: {
        ...node.data,
        _functionName: `server_${node.id}`,
        _defaultInputName: `server_input_${node.id}`,
        _sourceHandleInputNames: {},
      },
    })),
    edges: edges.map((edge) => ({
      ...edge,
      data: { ...edge.data, _inputName: `server_input_${edge.source}` },
    })),
  }
}

function makeParams(overrides: Partial<Parameters<typeof useKeyboardShortcuts>[0]> = {}) {
  return {
    handleSave: vi.fn(),
    setNodes: vi.fn(),
    setEdges: vi.fn(),
    setNodesAndEdges: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    fitView: vi.fn(),
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    clipboard: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    clearTrace: vi.fn(),
    closePanel: vi.fn(),
    isInsideSubmodel: false,
    readOnly: false,
    existingSingletonTypes: new Set<NodeTypeValue>(),
    resolveGraphIdentities: vi.fn(async (
      nodes: readonly Node[],
      edges: readonly Edge[],
    ): Promise<{ nodes: Node[]; edges: Edge[] }> => resolvedIdentityGraph(nodes, edges)),
    ...overrides,
  }
}

function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...opts }))
}

function fireKeyFrom(target: HTMLElement, key: string, opts: Partial<KeyboardEventInit> = {}) {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...opts }))
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe("useKeyboardShortcuts", () => {
  let params: ReturnType<typeof makeParams>

  beforeEach(() => {
    // Reset store state between tests
    useUIStore.setState({
      shortcutsOpen: false, submodelDialog: null, nodeSearchOpen: false,
    })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
    params = makeParams()
    renderHook(() => useKeyboardShortcuts(params))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  it("Ctrl+S calls handleSave", () => {
    fireKey("s", { ctrlKey: true })
    expect(params.handleSave).toHaveBeenCalledOnce()
  })

  it("Ctrl+Z calls undo", () => {
    fireKey("z", { ctrlKey: true })
    expect(params.undo).toHaveBeenCalledOnce()
  })

  it("Ctrl+Shift+Z calls redo", () => {
    fireKey("z", { ctrlKey: true, shiftKey: true })
    expect(params.redo).toHaveBeenCalledOnce()
  })

  it("Ctrl+Y calls redo", () => {
    fireKey("y", { ctrlKey: true })
    expect(params.redo).toHaveBeenCalledOnce()
  })

  it("uses a committed shared deletion without raw graph mutation but cleans selection", () => {
    cleanup()
    params = makeParams({
      graphRef: { current: { nodes: [{ ...makeNode("n1"), selected: true }], edges: [] } },
      commitSharedNodeDeletion: vi.fn(() => "committed" as const),
    })
    renderHook(() => useKeyboardShortcuts(params))
    fireKey("Delete")
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
  })

  it("keyboard Delete removes selected copies but never a submodel owner", () => {
    cleanup()
    const owner = makeNode("submodel_owner", "submodel", {
      selected: true,
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    const copy = makeNode("submodel_copy", "submodel", {
      selected: true,
      data: {
        label: "Scoring instance",
        nodeType: "submodel",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring_2",
          instanceOf: "submodel_owner",
        },
      },
    })
    const ordinary = { ...makeNode("plain", "polars"), selected: true }
    params = makeParams({
      graphRef: { current: { nodes: [owner, copy, ordinary], edges: [] } },
    })
    renderHook(() => useKeyboardShortcuts(params))
    fireKey("Delete")

    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    const [nextNodes] = vi.mocked(params.setNodesAndEdges).mock.calls[0]
    expect((nextNodes as Node[]).map((node) => node.id)).toEqual(["submodel_owner"])
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Dissolve Submodel/)
  })

  it("keyboard Delete of only a submodel owner changes nothing but explains why", () => {
    cleanup()
    const owner = makeNode("submodel_owner", "submodel", {
      selected: true,
      data: {
        label: "Scoring",
        nodeType: "submodel",
        config: { definitionId: "definition_scoring", alias: "scoring" },
      },
    })
    params = makeParams({
      graphRef: { current: { nodes: [owner], edges: [] } },
    })
    renderHook(() => useKeyboardShortcuts(params))
    fireKey("Delete")

    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts.at(-1)?.text).toMatch(/Dissolve Submodel/)
  })

  it("leaves cleanup untouched when shared deletion is blocked", () => {
    cleanup()
    params = makeParams({
      graphRef: { current: { nodes: [{ ...makeNode("n1"), selected: true }], edges: [] } },
      commitSharedNodeDeletion: vi.fn(() => "blocked" as const),
    })
    renderHook(() => useKeyboardShortcuts(params))
    fireKey("Delete")
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).not.toHaveBeenCalled()
    expect(params.setPreviewData).not.toHaveBeenCalled()
  })

  it("ignores Ctrl+Z while target is INPUT", () => {
    const input = document.createElement("input")
    document.body.appendChild(input)
    fireKeyFrom(input, "z", { ctrlKey: true })
    expect(params.undo).not.toHaveBeenCalled()
    document.body.removeChild(input)
  })

  it("ignores Ctrl+Shift+Z and Ctrl+Y while target is TEXTAREA", () => {
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)
    fireKeyFrom(textarea, "z", { ctrlKey: true, shiftKey: true })
    fireKeyFrom(textarea, "y", { ctrlKey: true })
    expect(params.redo).not.toHaveBeenCalled()
    document.body.removeChild(textarea)
  })

  it("Ctrl+1 calls fitView", () => {
    fireKey("1", { ctrlKey: true })
    expect(params.fitView).toHaveBeenCalledWith({ padding: 0.8 })
  })

  it("Escape calls clearTrace", () => {
    fireKey("Escape")
    expect(params.clearTrace).toHaveBeenCalledOnce()
  })

  it("ignores Escape while target is TEXTAREA", () => {
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)
    fireKeyFrom(textarea, "Escape")
    expect(params.clearTrace).not.toHaveBeenCalled()
    expect(params.closePanel).not.toHaveBeenCalled()
    document.body.removeChild(textarea)
  })

  it("ignores Escape while target is inside .cm-editor", () => {
    const cmEditor = document.createElement("div")
    cmEditor.className = "cm-editor"
    const inner = document.createElement("div")
    cmEditor.appendChild(inner)
    document.body.appendChild(cmEditor)
    fireKeyFrom(inner, "Escape")
    expect(params.clearTrace).not.toHaveBeenCalled()
    expect(params.closePanel).not.toHaveBeenCalled()
    document.body.removeChild(cmEditor)
  })

  it("? toggles shortcuts panel", () => {
    fireKey("?")
    expect(useUIStore.getState().shortcutsOpen).toBe(true)
  })

  it("Ctrl+A selects all nodes", () => {
    fireKey("a", { ctrlKey: true })
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("Ctrl+C copies selected nodes and toasts", () => {
    const selected: Node[] = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
    ]
    params.graphRef.current.nodes = selected
    fireKey("c", { ctrlKey: true })
    expect(params.clipboard.current.nodes).toHaveLength(1)
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: "Copied 1 node" })
  })

  it("Ctrl+V resolves copied node and edge identities before one atomic paste", async () => {
    params.clipboard.current = {
      nodes: [
        {
          id: "n1",
          position: { x: 0, y: 0 },
          data: { label: "A", nodeType: "polars", _functionName: "stale_a" },
          type: "pipelineNode",
        } as Node,
        {
          id: "n2",
          position: { x: 200, y: 0 },
          data: { label: "B", nodeType: "polars", _functionName: "stale_b" },
          type: "pipelineNode",
        } as Node,
      ],
      edges: [{
        id: "old-edge",
        source: "n1",
        target: "n2",
        data: { _inputName: "stale_edge" },
      } as Edge],
    }
    fireKey("v", { ctrlKey: true })
    await waitFor(() => expect(params.resolveGraphIdentities).toHaveBeenCalledOnce())
    const [candidateNodes, candidateEdges] = vi.mocked(params.resolveGraphIdentities).mock.calls[0]
    expect(candidateNodes.map((node) => ({ id: node.id, label: node.data.label }))).toEqual([
      { id: "pipelineNode_1", label: "A copy" },
      { id: "pipelineNode_2", label: "B copy" },
    ])
    expect(candidateEdges).toEqual([
      expect.objectContaining({
        id: "e-pipelineNode_1-pipelineNode_2",
        source: "pipelineNode_1",
        target: "pipelineNode_2",
      }),
    ])
    // Undo-atomicity: paste adds nodes + their edges in ONE combined call, so
    // a single ⌘/Ctrl-Z removes the whole paste — never separate setNodes +
    // setEdges (two undo snapshots).
    await waitFor(() => expect(params.setNodesAndEdges).toHaveBeenCalledOnce())
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
    const [nextNodes, nextEdges] = vi.mocked(params.setNodesAndEdges).mock.calls[0]
    expect(nextNodes).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: "pipelineNode_1",
        data: expect.objectContaining({ _functionName: "server_pipelineNode_1" }),
      }),
      expect.objectContaining({
        id: "pipelineNode_2",
        data: expect.objectContaining({ _functionName: "server_pipelineNode_2" }),
      }),
    ]))
    expect(nextEdges).toEqual([
      expect.objectContaining({
        source: "pipelineNode_1",
        target: "pipelineNode_2",
        data: { _inputName: "server_input_pipelineNode_1" },
      }),
    ])
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: "Pasted 2 nodes" })
  })

  it("Ctrl+V filters out singleton nodes that already exist in the graph", async () => {
    params.graphRef.current.nodes = [
      { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" } } as unknown as Node,
    ]
    params.clipboard.current = {
      nodes: [
        { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" }, type: "pipelineNode" } as unknown as Node,
        { id: "n2", position: { x: 0, y: 0 }, data: { label: "Transform", nodeType: "polars" }, type: "pipelineNode" } as unknown as Node,
      ],
      edges: [],
    }
    fireKey("v", { ctrlKey: true })
    await waitFor(() => expect(params.setNodesAndEdges).toHaveBeenCalledOnce())
    const result = vi.mocked(params.setNodesAndEdges).mock.calls[0][0] as Node[]
    // Original node + 1 pasted (polars), singleton (apiInput) filtered out
    expect(result).toHaveLength(2)
    expect(result.some((n: Node) => n.data.label === "Transform copy")).toBe(true)
  })

  it("Ctrl+V does nothing when all copied nodes are existing singletons", () => {
    params.graphRef.current.nodes = [
      { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" } } as unknown as Node,
    ]
    params.clipboard.current = {
      nodes: [
        { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" }, type: "pipelineNode" } as unknown as Node,
      ],
      edges: [],
    }
    fireKey("v", { ctrlKey: true })
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V treats a singleton inside another document graph as occupied", () => {
    const occupiedSingletonTypes = params.existingSingletonTypes as Set<NodeTypeValue>
    occupiedSingletonTypes.add("apiInput")
    params.clipboard.current = {
      nodes: [
        { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" }, type: "pipelineNode" } as unknown as Node,
      ],
      edges: [],
    }

    fireKey("v", { ctrlKey: true })

    expect(params.resolveGraphIdentities).not.toHaveBeenCalled()
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V allows pasting singleton when it does not exist in graph", async () => {
    params.graphRef.current.nodes = []
    params.clipboard.current = {
      nodes: [
        { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" }, type: "pipelineNode" } as unknown as Node,
      ],
      edges: [],
    }
    fireKey("v", { ctrlKey: true })
    await waitFor(() => expect(params.setNodesAndEdges).toHaveBeenCalledOnce())
  })

  it("Ctrl+V with empty clipboard does nothing", () => {
    fireKey("v", { ctrlKey: true })
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V reports identity-resolution failure without mutating the graph", async () => {
    cleanup()
    params = makeParams({
      resolveGraphIdentities: vi.fn(async () => {
        throw new Error("identity service unavailable")
      }),
    })
    params.clipboard.current = {
      nodes: [makeNode("n1", "polars", { data: { label: "A", nodeType: "polars" } })],
      edges: [],
    }
    renderHook(() => useKeyboardShortcuts(params))

    fireKey("v", { ctrlKey: true })

    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringMatching(/paste failed.*identity service unavailable/i),
    }))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V rejects a malformed identity result without mutating the graph", async () => {
    cleanup()
    params = makeParams({
      resolveGraphIdentities: vi.fn(async () => ({ nodes: [], edges: [] })),
    })
    params.clipboard.current = {
      nodes: [makeNode("n1", "polars", { data: { label: "A", nodeType: "polars" } })],
      edges: [],
    }
    renderHook(() => useKeyboardShortcuts(params))

    fireKey("v", { ctrlKey: true })

    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringMatching(/paste failed.*invalid node/i),
    }))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V discards a resolved paste when the graph changes in flight", async () => {
    cleanup()
    const pending = deferred<{ nodes: Node[]; edges: Edge[] }>()
    params = makeParams({ resolveGraphIdentities: vi.fn(() => pending.promise) })
    params.clipboard.current = {
      nodes: [makeNode("n1", "polars", { data: { label: "A", nodeType: "polars" } })],
      edges: [],
    }
    renderHook(() => useKeyboardShortcuts(params))

    fireKey("v", { ctrlKey: true })
    await waitFor(() => expect(params.resolveGraphIdentities).toHaveBeenCalledOnce())
    const [candidateNodes, candidateEdges] = vi.mocked(params.resolveGraphIdentities).mock.calls[0]
    params.graphRef.current = { nodes: [makeNode("intervening")], edges: [] }
    await act(async () => {
      pending.resolve(resolvedIdentityGraph(candidateNodes, candidateEdges))
      await pending.promise
    })

    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
      type: "error",
      text: expect.stringMatching(/paste was not applied.*graph changed/i),
    }))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+V lets only the latest concurrent paste commit", async () => {
    cleanup()
    const first = deferred<{ nodes: Node[]; edges: Edge[] }>()
    const second = deferred<{ nodes: Node[]; edges: Edge[] }>()
    const resolveGraphIdentities = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
    params = makeParams({ resolveGraphIdentities })
    params.clipboard.current = {
      nodes: [makeNode("n1", "polars", { data: { label: "A", nodeType: "polars" } })],
      edges: [],
    }
    renderHook(() => useKeyboardShortcuts(params))

    fireKey("v", { ctrlKey: true })
    fireKey("v", { ctrlKey: true })
    await waitFor(() => expect(resolveGraphIdentities).toHaveBeenCalledTimes(2))
    const [secondNodes, secondEdges] = resolveGraphIdentities.mock.calls[1] as [Node[], Edge[]]
    await act(async () => {
      second.resolve(resolvedIdentityGraph(secondNodes, secondEdges))
      await second.promise
    })
    await waitFor(() => expect(params.setNodesAndEdges).toHaveBeenCalledOnce())
    expect((vi.mocked(params.setNodesAndEdges).mock.calls[0][0] as Node[]).at(-1)?.id).toBe("polars_2")

    const [firstNodes, firstEdges] = resolveGraphIdentities.mock.calls[0] as [Node[], Edge[]]
    await act(async () => {
      first.resolve(resolvedIdentityGraph(firstNodes, firstEdges))
      await first.promise
    })
    await waitFor(() => expect(useToastStore.getState().toasts.some(
      (toast) => /paste was not applied/i.test(toast.text),
    )).toBe(true))
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
  })

  it("Delete removes selected nodes", () => {
    const nodes: Node[] = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: { label: "B" }, selected: false } as Node,
    ]
    params.graphRef.current.nodes = nodes
    params.graphRef.current.edges = [
      { id: "e1", source: "n1", target: "n2" } as Edge,
    ]
    fireKey("Delete")
    // Undo-atomicity: node + its edges removed in ONE combined call — never
    // separate setNodes + setEdges (two undo snapshots).
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
  })

  it("Delete with no selection does nothing", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: false } as Node,
    ]
    params.graphRef.current.edges = []
    fireKey("Delete")
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+G with 2+ selected opens submodel dialog", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    fireKey("g", { ctrlKey: true })
    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["n1", "n2"] })
  })

  it("Ctrl+G with <2 selected toasts info", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    fireKey("g", { ctrlKey: true })
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: expect.stringContaining("2 nodes") })
  })

  it("ignores Ctrl+G while target is TEXTAREA", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)

    fireKeyFrom(textarea, "g", { ctrlKey: true })

    expect(useUIStore.getState().submodelDialog).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
    document.body.removeChild(textarea)
  })

  it("ignores Ctrl+G while target is INPUT or inside .cm-editor", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    const input = document.createElement("input")
    const cmEditor = document.createElement("div")
    cmEditor.className = "cm-editor"
    const inner = document.createElement("div")
    cmEditor.appendChild(inner)
    document.body.append(input, cmEditor)

    fireKeyFrom(input, "g", { ctrlKey: true })
    fireKeyFrom(inner, "g", { ctrlKey: true })

    expect(useUIStore.getState().submodelDialog).toBeNull()
    expect(useToastStore.getState().toasts).toHaveLength(0)
    input.remove()
    cmEditor.remove()
  })

  it("Ctrl+K toggles node search open", () => {
    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    fireKey("k", { ctrlKey: true })
    expect(useUIStore.getState().nodeSearchOpen).toBe(true)
    fireKey("k", { ctrlKey: true })
    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
  })

  it("ignores Ctrl+K when target is inside .cm-editor", () => {
    const cmEditor = document.createElement("div")
    cmEditor.className = "cm-editor"
    const inner = document.createElement("div")
    cmEditor.appendChild(inner)
    document.body.appendChild(cmEditor)

    fireKeyFrom(inner, "k", { ctrlKey: true })

    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    document.body.removeChild(cmEditor)
  })

  it("ignores Ctrl+K while target is INPUT or TEXTAREA when node search is closed", () => {
    const input = document.createElement("input")
    const textarea = document.createElement("textarea")
    document.body.append(input, textarea)

    fireKeyFrom(input, "k", { ctrlKey: true })
    fireKeyFrom(textarea, "k", { ctrlKey: true })

    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    input.remove()
    textarea.remove()
  })

  it("closes node search with Ctrl+K while the search input is focused", () => {
    useUIStore.setState({ nodeSearchOpen: true })
    const input = document.createElement("input")
    input.setAttribute("aria-label", "Search nodes")
    document.body.append(input)

    fireKeyFrom(input, "k", { ctrlKey: true })

    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    input.remove()
  })

  it("ignores Ctrl+C when target is INPUT", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
    ]
    const input = document.createElement("input")
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "c", ctrlKey: true, bubbles: true }))
    expect(params.clipboard.current.nodes).toHaveLength(0)
    document.body.removeChild(input)
  })

  it("ignores Ctrl+C when target is TEXTAREA", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
    ]
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "c", ctrlKey: true, bubbles: true }))
    expect(params.clipboard.current.nodes).toHaveLength(0)
    document.body.removeChild(textarea)
  })

  it("ignores Ctrl+C when target is inside .cm-editor", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
    ]
    const cmEditor = document.createElement("div")
    cmEditor.className = "cm-editor"
    const inner = document.createElement("div")
    cmEditor.appendChild(inner)
    document.body.appendChild(cmEditor)
    inner.dispatchEvent(new KeyboardEvent("keydown", { key: "c", ctrlKey: true, bubbles: true }))
    expect(params.clipboard.current.nodes).toHaveLength(0)
    document.body.removeChild(cmEditor)
  })

  it("ignores ? when target is INPUT", () => {
    useUIStore.setState({ shortcutsOpen: false })
    const input = document.createElement("input")
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "?", bubbles: true }))
    expect(useUIStore.getState().shortcutsOpen).toBe(false)
    document.body.removeChild(input)
  })

  it("Ctrl+C with no selected nodes is a no-op", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: false } as Node,
    ]
    fireKey("c", { ctrlKey: true })
    expect(params.clipboard.current.nodes).toHaveLength(0)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it("Delete with empty graphRef is a no-op", () => {
    params.graphRef.current.nodes = []
    params.graphRef.current.edges = []
    fireKey("Delete")
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
  })

  it("Ctrl+G with 0 selected nodes shows warning toast", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: false } as Node,
    ]
    fireKey("g", { ctrlKey: true })
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]).toMatchObject({ type: "info", text: expect.stringContaining("2 nodes") })
    expect(useUIStore.getState().submodelDialog).toBeNull()
  })

  it("ignores Delete when target is INPUT", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
    ]
    params.graphRef.current.edges = []
    const input = document.createElement("input")
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete", bubbles: true }))
    expect(params.setNodesAndEdges).not.toHaveBeenCalled()
    expect(params.setNodes).not.toHaveBeenCalled()
    document.body.removeChild(input)
  })

  it("ignores Ctrl+A when target is TEXTAREA", () => {
    const textarea = document.createElement("textarea")
    document.body.appendChild(textarea)
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "a", ctrlKey: true, bubbles: true }))
    expect(params.setNodes).not.toHaveBeenCalled()
    document.body.removeChild(textarea)
  })

  it("cleans up listener on unmount", () => {
    const removeSpy = vi.spyOn(window, "removeEventListener")
    const { unmount } = renderHook(() => useKeyboardShortcuts(params))
    unmount()
    expect(removeSpy).toHaveBeenCalledWith("keydown", expect.any(Function))
    removeSpy.mockRestore()
  })

  it("Ctrl+G inside submodel shows nesting warning instead of dialog", () => {
    cleanup()
    const submodelParams = makeParams({ isInsideSubmodel: true })
    submodelParams.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    renderHook(() => useKeyboardShortcuts(submodelParams))
    fireKey("g", { ctrlKey: true })
    expect(useUIStore.getState().submodelDialog).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({
      type: "info",
      text: expect.stringContaining("cannot be nested"),
    })
  })

  it("blocks mutation shortcuts in a read-only submodel instance", () => {
    cleanup()
    const readOnlyParams = makeParams({ readOnly: true, isInsideSubmodel: true })
    readOnlyParams.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    readOnlyParams.graphRef.current.edges = [
      { id: "e1", source: "n1", target: "n2", selected: true } as Edge,
    ]
    readOnlyParams.clipboard.current = {
      nodes: [readOnlyParams.graphRef.current.nodes[0]],
      edges: [],
    }
    renderHook(() => useKeyboardShortcuts(readOnlyParams))

    fireKey("z", { ctrlKey: true })
    fireKey("y", { ctrlKey: true })
    fireKey("v", { ctrlKey: true })
    fireKey("g", { ctrlKey: true })
    fireKey("Delete")

    expect(readOnlyParams.undo).not.toHaveBeenCalled()
    expect(readOnlyParams.redo).not.toHaveBeenCalled()
    expect(readOnlyParams.setNodesAndEdges).not.toHaveBeenCalled()
    expect(readOnlyParams.setNodes).not.toHaveBeenCalled()
    expect(readOnlyParams.setEdges).not.toHaveBeenCalled()
    expect(useUIStore.getState().submodelDialog).toBeNull()
  })

  it("Cmd+G (Mac) with 2+ selected opens submodel dialog", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    fireKey("g", { metaKey: true })
    expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["n1", "n2"] })
  })

  it("Cmd+G (Mac) inside submodel shows nesting warning", () => {
    cleanup()
    const submodelParams = makeParams({ isInsideSubmodel: true })
    submodelParams.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: {}, selected: true } as Node,
    ]
    renderHook(() => useKeyboardShortcuts(submodelParams))
    fireKey("g", { metaKey: true })
    expect(useUIStore.getState().submodelDialog).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({
      type: "info",
      text: expect.stringContaining("cannot be nested"),
    })
  })

  it("Backspace removes selected nodes", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: true } as Node,
      { id: "n2", position: { x: 0, y: 0 }, data: { label: "B" }, selected: false } as Node,
    ]
    params.graphRef.current.edges = [
      { id: "e1", source: "n1", target: "n2" } as Edge,
    ]
    fireKey("Backspace")
    expect(params.setNodesAndEdges).toHaveBeenCalledOnce()
    expect(params.setNodes).not.toHaveBeenCalled()
    expect(params.setEdges).not.toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
  })
})
