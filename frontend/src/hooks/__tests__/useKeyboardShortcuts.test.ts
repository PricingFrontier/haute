import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useKeyboardShortcuts from "../useKeyboardShortcuts"
import useUIStore from "../../stores/useUIStore"
import useToastStore from "../../stores/useToastStore"

function makeParams(overrides: Partial<Parameters<typeof useKeyboardShortcuts>[0]> = {}) {
  return {
    handleSave: vi.fn(),
    setNodes: vi.fn(),
    setEdges: vi.fn(),
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
    ...overrides,
  }
}

function fireKey(key: string, opts: Partial<KeyboardEventInit> = {}) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...opts }))
}

function fireKeyFrom(target: HTMLElement, key: string, opts: Partial<KeyboardEventInit> = {}) {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...opts }))
}

describe("useKeyboardShortcuts", () => {
  let params: ReturnType<typeof makeParams>

  beforeEach(() => {
    // Reset store state between tests
    useUIStore.setState({
      shortcutsOpen: false, submodelDialog: null, nodeSearchOpen: false,
    })
    useToastStore.setState({
      toasts: [], _toastCounter: 0,
    })
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

  it("Ctrl+V pastes copied nodes", () => {
    params.clipboard.current = {
      nodes: [{ id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, type: "pipelineNode" } as Node],
      edges: [],
    }
    fireKey("v", { ctrlKey: true })
    expect(params.setNodes).toHaveBeenCalledOnce()
    expect(params.setEdges).toHaveBeenCalledOnce()
    const toasts = useToastStore.getState().toasts
    expect(toasts[toasts.length - 1]).toMatchObject({ type: "info", text: "Pasted 1 node" })
  })

  it("Ctrl+V filters out singleton nodes that already exist in the graph", () => {
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
    expect(params.setNodes).toHaveBeenCalledOnce()
    const updater = vi.mocked(params.setNodes).mock.calls[0][0] as (nds: Node[]) => Node[]
    const result = updater(params.graphRef.current.nodes)
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
    expect(params.setNodes).not.toHaveBeenCalled()
  })

  it("Ctrl+V allows pasting singleton when it does not exist in graph", () => {
    params.graphRef.current.nodes = []
    params.clipboard.current = {
      nodes: [
        { id: "api1", position: { x: 0, y: 0 }, data: { label: "Quote Input", nodeType: "apiInput" }, type: "pipelineNode" } as unknown as Node,
      ],
      edges: [],
    }
    fireKey("v", { ctrlKey: true })
    expect(params.setNodes).toHaveBeenCalledOnce()
  })

  it("Ctrl+V with empty clipboard does nothing", () => {
    fireKey("v", { ctrlKey: true })
    expect(params.setNodes).not.toHaveBeenCalled()
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
    expect(params.setNodes).toHaveBeenCalled()
    expect(params.setEdges).toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
    expect(params.setPreviewData).toHaveBeenCalledWith(null)
  })

  it("Delete with no selection does nothing", () => {
    params.graphRef.current.nodes = [
      { id: "n1", position: { x: 0, y: 0 }, data: { label: "A" }, selected: false } as Node,
    ]
    params.graphRef.current.edges = []
    fireKey("Delete")
    expect(params.setNodes).not.toHaveBeenCalled()
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

  it("ignores Ctrl+K while target is INPUT or TEXTAREA", () => {
    const input = document.createElement("input")
    const textarea = document.createElement("textarea")
    document.body.append(input, textarea)

    fireKeyFrom(input, "k", { ctrlKey: true })
    fireKeyFrom(textarea, "k", { ctrlKey: true })

    expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    input.remove()
    textarea.remove()
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
    expect(params.setNodes).toHaveBeenCalled()
    expect(params.setEdges).toHaveBeenCalled()
    expect(params.setSelectedNode).toHaveBeenCalledWith(null)
  })
})
