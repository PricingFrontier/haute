/**
 * Phase 1 Package 1H — Item #35: drag-drop JSON parse must fail loudly.
 *
 * Pre-fix: the onDrop handler swallows JSON.parse errors silently:
 *     try { config = JSON.parse(...) } catch { /* ignore *\/ }
 * resulting in a new node with `config: {}`, which (depending on node
 * type) may silently violate node-type invariants or produce surprising
 * downstream behaviour that's hard to diagnose.
 *
 * Fix: malformed drag JSON should surface as a toast (or throw).  This
 * matches the project-wide "fail loudly, no unnecessary fallbacks"
 * principle in CLAUDE.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import useEdgeHandlers from "../useEdgeHandlers"
import useToastStore from "../../stores/useToastStore"
import { NODE_TYPES } from "../../utils/nodeTypes"

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return {
    ...actual,
    addEdge: (params: Record<string, unknown>, eds: Edge[]) =>
      [...eds, { id: `e_${params.source}_${params.target}`, ...params }],
  }
})

function makeParams() {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes: [] as Node[], edges: [] as Edge[] } },
    nodeIdCounter: { current: 0 },
    lastSelectedNodeRef: { current: null as Node | null },
    setNodes: vi.fn(),
    setEdges: vi.fn(),
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    pushSnapshot: vi.fn(),
    setSelectedNode: vi.fn(),
    setPreviewData: vi.fn(),
    setContextMenu: vi.fn(),
    fetchPreview: vi.fn(),
    cancelPreview: vi.fn(),
    shouldSkipAutomaticPreview: vi.fn(() => false),
    clearTrace: vi.fn(),
    screenToFlowPosition: vi.fn((pos: { x: number; y: number }) => pos),
    graphRefreshingRef: { current: 0 },
    findNodeIdAtPoint: vi.fn(() => null as string | null),
    getInternalNode: vi.fn(() => undefined),
    getZoom: vi.fn(() => 1),
  }
}

function makeDragEvent(type: string, rawConfig: string | undefined) {
  return {
    preventDefault: vi.fn(),
    clientX: 100,
    clientY: 200,
    dataTransfer: {
      getData: vi.fn((key: string) => {
        if (key === "application/reactflow-type") return type
        if (key === "application/reactflow-config") return rawConfig ?? ""
        return ""
      }),
    },
  } as unknown as React.DragEvent
}

describe("useEdgeHandlers.onDrop — malformed drag JSON fails loudly (#35)", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("malformed drag-config JSON produces a user-visible error toast OR throws", () => {
    // Pre-fix: silent catch → node created with empty config, no error
    // shown, user sees inexplicable empty node. This test requires EITHER
    // an error toast OR an exception — both are acceptable for "fail
    // loudly".
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = makeDragEvent(NODE_TYPES.POLARS, "this is not valid JSON{{{")

    let threw = false
    try {
      act(() => {
        result.current.onDrop(event)
      })
    } catch {
      threw = true
    }

    const toasts = useToastStore.getState().toasts
    const toastedError = toasts.some((t) => t.type === "error")
    expect(threw || toastedError).toBe(true)
  })

  it("malformed drag-config does NOT silently create a node with empty config", () => {
    // This is the key observable behaviour: the bug manifests as an
    // inexplicable node on the canvas.  Post-fix, setNodes must NOT
    // have been called with a node whose config was silently defaulted.
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = makeDragEvent(NODE_TYPES.POLARS, "{not json")

    try {
      act(() => {
        result.current.onDrop(event)
      })
    } catch {
      /* acceptable: the fix may throw */
    }

    // Either setNodes was never called (threw before creating node),
    // or if it was, an error toast was emitted so the user is informed.
    const setNodesCalled = params.setNodes.mock.calls.length > 0
    const toasts = useToastStore.getState().toasts
    const hasErrorToast = toasts.some((t) => t.type === "error")
    expect(setNodesCalled === false || hasErrorToast).toBe(true)
  })

  it("well-formed drag-config JSON still creates node successfully", () => {
    // Regression guard: the fix must not break the happy path.
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = makeDragEvent(
      NODE_TYPES.POLARS,
      JSON.stringify({ expression: "df" }),
    )

    act(() => {
      result.current.onDrop(event)
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
    const updater = params.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[]
    const result_nodes = updater([])
    // Node should exist with the parsed config applied (expression="df")
    expect(result_nodes).toHaveLength(1)
    expect(result_nodes[0].data).toMatchObject({ config: { expression: "df" } })
  })

  it("empty drag-config string (default) creates a node with {} config", () => {
    // Edge case: the drag source intentionally sent an empty string for
    // the config blob.  Post-fix, this must still work because "" falls
    // back to "{}" via `|| "{}"` before parsing.
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = makeDragEvent(NODE_TYPES.POLARS, "")

    act(() => {
      result.current.onDrop(event)
    })

    expect(params.setNodes).toHaveBeenCalledOnce()
    const updater = params.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[]
    const result_nodes = updater([])
    expect(result_nodes[0].data).toMatchObject({ config: {} })
  })

  it("non-object JSON (e.g. a bare array or string) fails loudly", () => {
    // JSON.parse("[1,2,3]") succeeds but the resulting config is not a
    // record — downstream node editors assume an object. Post-fix: an
    // error toast fires (or the drop throws).
    const params = makeParams()
    const { result } = renderHook(() => useEdgeHandlers(params))
    const event = makeDragEvent(NODE_TYPES.POLARS, "[1,2,3]")

    let threw = false
    try {
      act(() => {
        result.current.onDrop(event)
      })
    } catch {
      threw = true
    }

    const toasts = useToastStore.getState().toasts
    const toastedError = toasts.some((t) => t.type === "error")
    // Accepted: either the drop surfaced an error, or (if the codebase
    // decides bare arrays are coerced silently) setNodes is NOT called
    // with a non-object config.
    if (params.setNodes.mock.calls.length > 0) {
      const updater = params.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[]
      const out = updater([])
      const cfg = out[0].data.config
      expect(cfg === null || typeof cfg === "object" && !Array.isArray(cfg))
        .toBe(true)
    } else {
      expect(threw || toastedError).toBe(true)
    }
  })
})
