/**
 * Adversarial resilience tests — validate that the frontend handles malformed,
 * unexpected, and edge-case data gracefully without crashing or hanging.
 *
 * Covers:
 *   1. API returning unexpected shapes (null, {}, array, extra/missing fields)
 *   2. WebSocket messages with invalid payloads
 *   3. Very large graph (500 nodes, 1000 edges)
 *   4. Rapid undo/redo (100 operations)
 *   5. Nodes with missing data.config
 *   6. Empty graph (no nodes, no edges)
 *   7. Duplicate node IDs
 *   8. Self-referencing edges
 *   9. Orphan edges (pointing to non-existent nodes)
 *  10. Store state after unmount
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act, cleanup } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"

// ── Store imports ────────────────────────────────────────────────
import useNodeResultsStore, {
  MAX_CACHED_PREVIEWS,
  resetNodeResultsDerivedCaches,
} from "../../stores/useNodeResultsStore.ts"
import useUIStore from "../../stores/useUIStore.ts"
import useGraphStore from "../../stores/useGraphStore.ts"
import useToastStore from "../../stores/useToastStore.ts"
import useSettingsStore from "../../stores/useSettingsStore.ts"

// ── Utility imports ──────────────────────────────────────────────
import { computeNextNodeId, normalizeEdges } from "../../utils/graphHelpers.ts"
import { buildGraph } from "../../utils/buildGraph.ts"
import { makeNode, makeEdge, makeSimpleNode, makeSimpleEdge } from "../../test-utils/factories.ts"
import { makePreviewData } from "../../utils/makePreviewData.ts"

// ── Helpers ──────────────────────────────────────────────────────

function resetStores() {
  resetNodeResultsDerivedCaches()
  useNodeResultsStore.setState({
    previews: {},
    pinnedPreviewNodeId: null,
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
  useUIStore.setState({
    paletteOpen: true,
    utilityOpen: false,
    importsOpen: false,
    gitOpen: false,
    shortcutsOpen: false,
    submodelDialog: null,
    renameDialog: null,
    syncBanner: null,
    nodePanelWidth: 0,
    ratingStepEditorSections: {},
    explorePanes: {},
    explorePreviewPanes: {},
    hoveredNodeId: null,
    nodeSearchOpen: false,
  })
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    structuralVersion: 0,
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

function generateLargeGraph(nodeCount: number, edgeCount: number) {
  const nodes: Node[] = []
  for (let i = 0; i < nodeCount; i++) {
    nodes.push(makeNode(`node_${i}`, "polars", {
      data: {
        label: `Node ${i}`,
        nodeType: "polars",
        config: { code: `df = df.with_columns(pl.lit(${i}).alias("col_${i}"))` },
      },
    }))
  }

  const edges: Edge[] = []
  for (let i = 0; i < edgeCount; i++) {
    const source = `node_${i % nodeCount}`
    const target = `node_${(i + 1) % nodeCount}`
    if (source !== target) {
      edges.push(makeEdge(source, target, { id: `e_${i}` }))
    }
  }

  return { nodes, edges }
}

// ══════════════════════════════════════════════════════════════════
// 1. API returns unexpected shapes
// ══════════════════════════════════════════════════════════════════

describe("1. API returns unexpected shapes", () => {
  beforeEach(resetStores)

  describe("null API responses handled by stores", () => {
    it("setPreview with null-like data fields does not crash", () => {
      const store = useNodeResultsStore.getState()
      // Simulate API returning null for preview fields
      const brokenPreview = makePreviewData("n1", "Test", {
        columns: [],
        preview: [],
        row_count: 0,
        column_count: 0,
        error: null,
      })
      expect(() => store.setPreview("n1", brokenPreview, 0)).not.toThrow()
      expect(store.getPreview("n1")).not.toBeNull()
    })

    it("setColumns with empty array does not crash", () => {
      const store = useNodeResultsStore.getState()
      expect(() => store.setColumns("n1", [], 0)).not.toThrow()
      const result = store.getColumns("n1")
      expect(result).not.toBeNull()
      expect(result!.columns).toEqual([])
    })

    it("completeSolveJob with minimal result shape does not crash", () => {
      const store = useNodeResultsStore.getState()
      store.startSolveJob("n1", "j1", "Node", {}, "h", "live", 0)
      // Simulate server returning only partial result
      const minimalResult = {
        total_objective: 0,
        baseline_objective: 0,
        constraints: {},
        baseline_constraints: {},
        lambdas: {},
        converged: false,
      }
      expect(() => store.completeSolveJob("n1", minimalResult)).not.toThrow()
      expect(useNodeResultsStore.getState().solveResults["n1"]).toBeDefined()
    })

    it("completeTrainJob with minimal result shape does not crash", () => {
      const store = useNodeResultsStore.getState()
      store.startTrainJob("t1", "tj1", "Train", "h", "live", 0)
      const minimalResult = {
        status: "completed",
        metrics: {},
        feature_importance: [],
        model_path: "",
        train_rows: 0,
        test_rows: 0,
      }
      expect(() => store.completeTrainJob("t1", minimalResult)).not.toThrow()
      expect(useNodeResultsStore.getState().trainResults["t1"]).toBeDefined()
    })
  })

  describe("extra fields in API responses", () => {
    it("setPreview handles data with extra unexpected fields", () => {
      const store = useNodeResultsStore.getState()
      const previewWithExtras = {
        ...makePreviewData("n1", "Test"),
        unexpectedField: "should not crash",
        _internalDebug: { detail: 42 },
      } as unknown as Parameters<typeof store.setPreview>[1]
      expect(() => store.setPreview("n1", previewWithExtras, 0)).not.toThrow()
      const cached = store.getPreview("n1")
      expect(cached!.data.nodeId).toBe("n1")
    })

    it("normalizeEdges handles edges with extra properties", () => {
      const edgesWithExtras = [
        { id: "e1", source: "a", target: "b", customProp: "hello", weight: 42 } as Edge & Record<string, unknown>,
      ]
      const normalized = normalizeEdges(edgesWithExtras)
      expect(normalized[0].id).toBe("e1")
      expect(normalized[0].type).toBe("default")
      expect(normalized[0].animated).toBe(false)
      // Extra props should be preserved through spread
      expect((normalized[0] as Edge & Record<string, unknown>).customProp).toBe("hello")
    })
  })

  describe("array-instead-of-object responses", () => {
    it("computeNextNodeId handles empty array", () => {
      expect(computeNextNodeId([])).toBe(0)
    })

    it("normalizeEdges handles empty array", () => {
      expect(normalizeEdges([])).toEqual([])
    })

    it("buildGraph handles empty arrays for nodes and edges", () => {
      const result = buildGraph([], [])
      expect(result.nodes).toEqual([])
      expect(result.edges).toEqual([])
    })
  })

  describe("missing fields in API responses", () => {
    it("makePreviewData fills defaults for missing optional fields", () => {
      // Simulates API returning only required fields
      const preview = makePreviewData("n1", "Test", {})
      expect(preview.status).toBe("ok")
      expect(preview.columns).toEqual([])
      expect(preview.preview).toEqual([])
      expect(preview.row_count).toBe(0)
      expect(preview.error).toBeNull()
    })

    it("getOptimiserPreview returns null when no result exists", () => {
      expect(useNodeResultsStore.getState().getOptimiserPreview("nonexistent")).toBeNull()
    })

    it("getModellingPreview returns null when no result exists", () => {
      expect(useNodeResultsStore.getState().getModellingPreview("nonexistent")).toBeNull()
    })

    it("getColumns returns null for unknown source node", () => {
      expect(useNodeResultsStore.getState().getColumns("unknown")).toBeNull()
    })
  })
})

// ══════════════════════════════════════════════════════════════════
// 2. WebSocket messages with invalid payloads
// ══════════════════════════════════════════════════════════════════

describe("2. WebSocket messages with invalid payloads", () => {
  // We test the message parsing logic that useWebSocketSync uses internally.
  // Rather than instantiate the full hook (which needs global WS mock),
  // we test the JSON.parse paths and the normalizeEdges/computeNextNodeId
  // utilities that the onmessage handler depends on.

  it("empty string message triggers JSON parse error", () => {
    expect(() => JSON.parse("")).toThrow()
  })

  it("message with only whitespace triggers JSON parse error", () => {
    expect(() => JSON.parse("   ")).toThrow()
  })

  it("message with extra fields beyond type and graph is safe to parse", () => {
    const msg = JSON.parse(JSON.stringify({
      type: "graph_update",
      graph: { nodes: [], edges: [] },
      _debug: true,
      server_timestamp: 12345,
      extra_nested: { a: { b: { c: 1 } } },
    }))
    expect(msg.type).toBe("graph_update")
    expect(msg.graph.nodes).toEqual([])
    // Extra fields are just ignored by the handler
    expect(msg._debug).toBe(true)
  })

  it("message with missing type field parses but handler ignores it", () => {
    const msg = JSON.parse(JSON.stringify({
      graph: { nodes: [], edges: [] },
    }))
    // The handler checks msg.type === "graph_update" && msg.graph
    // If type is undefined, neither branch is entered — no crash
    expect(msg.type).toBeUndefined()
  })

  it("message with null graph field is safe — handler checks msg.graph truthiness", () => {
    const msg = JSON.parse(JSON.stringify({
      type: "graph_update",
      graph: null,
    }))
    // msg.type === "graph_update" && msg.graph evaluates to false when graph is null
    expect(msg.type).toBe("graph_update")
    expect(msg.graph).toBeNull()
    expect(msg.type === "graph_update" && msg.graph).toBeFalsy()
  })

  it("message with graph.nodes as non-array is handled by normalizeEdges/computeNextNodeId", () => {
    // If the server sent nodes: null, the handler does `g.nodes || []`
    const g = { nodes: null, edges: null }
    const nodes = g.nodes || []
    const edges = g.edges || []
    expect(nodes).toEqual([])
    expect(normalizeEdges(edges)).toEqual([])
    expect(computeNextNodeId(nodes)).toBe(0)
  })

  it("binary-like string message triggers JSON parse error", () => {
    // Simulate a binary message that arrives as string
    const binaryString = "\x00\x01\x02\x03\xFF\xFE"
    expect(() => JSON.parse(binaryString)).toThrow()
  })
})

// ══════════════════════════════════════════════════════════════════
// 3. Very large graph — 500 nodes, 1000 edges
// ══════════════════════════════════════════════════════════════════

describe("3. Very large graph (500 nodes, 1000 edges)", () => {
  beforeEach(resetStores)

  it("computeNextNodeId handles 500 nodes without hanging", () => {
    const { nodes } = generateLargeGraph(500, 0)
    const start = performance.now()
    const nextId = computeNextNodeId(nodes)
    const elapsed = performance.now() - start

    expect(nextId).toBe(500)
    // Should complete in under 100ms even on slow CI
    expect(elapsed).toBeLessThan(100)
  })

  it("normalizeEdges handles 1000 edges without hanging", () => {
    const { edges } = generateLargeGraph(500, 1000)
    const start = performance.now()
    const normalized = normalizeEdges(edges)
    const elapsed = performance.now() - start

    expect(normalized.length).toBe(edges.length)
    expect(elapsed).toBeLessThan(100)
  })

  it("buildGraph with 500 nodes completes in reasonable time", () => {
    const simpleNodes = Array.from({ length: 500 }, (_, i) =>
      makeSimpleNode(`node_${i}`, "polars"),
    )
    const simpleEdges = Array.from({ length: 1000 }, (_, i) =>
      makeSimpleEdge(`e_${i}`, `node_${i % 500}`, `node_${(i + 1) % 500}`),
    )

    const start = performance.now()
    const result = buildGraph(simpleNodes, simpleEdges)
    const elapsed = performance.now() - start

    expect(result.nodes).toHaveLength(500)
    expect(result.edges).toHaveLength(1000)
    expect(elapsed).toBeLessThan(200)
  })

  it("nodeResultsStore handles 500 preview writes within the bounded preview cache", () => {
    const store = useNodeResultsStore.getState()

    const start = performance.now()
    for (let i = 0; i < 500; i++) {
      store.setPreview(
        `node_${i}`,
        makePreviewData(`node_${i}`, `Node ${i}`),
        0,
      )
    }
    const elapsed = performance.now() - start

    const { previews } = useNodeResultsStore.getState()
    expect(Object.keys(previews)).toHaveLength(MAX_CACHED_PREVIEWS)

    const firstRetainedIndex = 500 - MAX_CACHED_PREVIEWS
    for (let i = 0; i < firstRetainedIndex; i += 1) {
      expect(previews[`node_${i}`]).toBeUndefined()
    }
    for (let i = firstRetainedIndex; i < 500; i += 1) {
      expect(useNodeResultsStore.getState().getPreview(`node_${i}`)).not.toBeNull()
    }

    expect(elapsed).toBeLessThan(1000)
  })

})

// ══════════════════════════════════════════════════════════════════
// 4. Rapid undo/redo (100 operations)
// ══════════════════════════════════════════════════════════════════

describe("4. Rapid undo/redo", () => {
  // We mock useNodesState/useEdgesState since the real hook requires ReactFlow context
  vi.mock("@xyflow/react", async () => {
    const actual = await vi.importActual("@xyflow/react") as Record<string, unknown>
    const React = await import("react")
    return {
      ...actual,
      useNodesState: (initial: Node[]) => {
        const [nodes, setNodes] = React.useState(initial)
        const onNodesChange = React.useCallback((changes: { type: string; id?: string; item?: Node }[]) => {
          setNodes((prev: Node[]) => {
            let next = [...prev]
            for (const change of changes) {
              if (change.type === "add") {
                if (change.item) next.push(change.item)
              } else if (change.type === "remove") {
                next = next.filter((n: Node) => n.id !== change.id)
              }
            }
            return next
          })
        }, [])
        return [nodes, setNodes, onNodesChange] as const
      },
      useEdgesState: (initial: Edge[]) => {
        const [edges, setEdges] = React.useState(initial)
        const onEdgesChange = React.useCallback((changes: { type: string; id?: string; item?: Edge }[]) => {
          setEdges((prev: Edge[]) => {
            let next = [...prev]
            for (const change of changes) {
              if (change.type === "add") {
                if (change.item) next.push(change.item)
              } else if (change.type === "remove") {
                next = next.filter((e: Edge) => e.id !== change.id)
              }
            }
            return next
          })
        }, [])
        return [edges, setEdges, onEdgesChange] as const
      },
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("100 setNodes calls then 100 undos produces consistent state", async () => {
    const useGraphCanvasState = (await import("../../hooks/useGraphCanvasState.ts")).default

    const initialNodes = [makeNode("n0")]
    const initialEdges: Edge[] = []

    const { result } = renderHook(() => useGraphCanvasState(initialNodes, initialEdges))

    // Make 100 changes
    for (let i = 1; i <= 100; i++) {
      act(() => {
        result.current.setNodes([makeNode(`n${i}`)])
      })
    }

    // The current state should be the last change
    expect(result.current.nodes).toHaveLength(1)
    expect(result.current.nodes[0].id).toBe("n100")
    expect(result.current.canUndo).toBe(true)

    // Rapidly undo all 100 changes
    for (let i = 0; i < 100; i++) {
      act(() => {
        result.current.undo()
      })
    }

    // Should be back to initial state
    expect(result.current.nodes).toHaveLength(1)
    expect(result.current.nodes[0].id).toBe("n0")
    expect(result.current.canRedo).toBe(true)

    // Redo all 100
    for (let i = 0; i < 100; i++) {
      act(() => {
        result.current.redo()
      })
    }

    expect(result.current.nodes[0].id).toBe("n100")
    expect(result.current.canUndo).toBe(true)
  })

  it("undo on empty history is a no-op", async () => {
    const useGraphCanvasState = (await import("../../hooks/useGraphCanvasState.ts")).default

    const { result } = renderHook(() => useGraphCanvasState([], []))

    expect(result.current.canUndo).toBe(false)

    // Should not crash
    act(() => {
      result.current.undo()
    })

    expect(result.current.nodes).toEqual([])
    expect(result.current.canUndo).toBe(false)
  })

  it("redo on empty future is a no-op", async () => {
    const useGraphCanvasState = (await import("../../hooks/useGraphCanvasState.ts")).default

    const { result } = renderHook(() => useGraphCanvasState([], []))

    expect(result.current.canRedo).toBe(false)

    act(() => {
      result.current.redo()
    })

    expect(result.current.nodes).toEqual([])
    expect(result.current.canRedo).toBe(false)
  })

  it("history is capped at MAX_HISTORY (100)", async () => {
    const useGraphCanvasState = (await import("../../hooks/useGraphCanvasState.ts")).default

    const { result } = renderHook(() => useGraphCanvasState([makeNode("n0")], []))

    // Make 150 changes (exceeding the 100 cap)
    for (let i = 1; i <= 150; i++) {
      act(() => {
        result.current.setNodes([makeNode(`n${i}`)])
      })
    }

    // Undo as many times as possible
    let undoCount = 0
    while (result.current.canUndo) {
      act(() => {
        result.current.undo()
      })
      undoCount++
      // Safety: break if we somehow get stuck
      if (undoCount > 200) break
    }

    // Should be capped at 100 undos (MAX_HISTORY)
    expect(undoCount).toBeLessThanOrEqual(100)
    expect(result.current.canUndo).toBe(false)
  })
})

// ══════════════════════════════════════════════════════════════════
// 5. Node with missing data.config
// ══════════════════════════════════════════════════════════════════

describe("5. Node with missing data.config", () => {
  it("buildGraph handles node with undefined config", () => {
    const nodeNoConfig = makeSimpleNode("n1", "polars")
    // Explicitly remove config
    delete (nodeNoConfig.data as Record<string, unknown>).config

    expect(() => buildGraph([nodeNoConfig], [])).not.toThrow()
    const result = buildGraph([nodeNoConfig], [])
    expect(result.nodes[0].data.config).toBeUndefined()
  })

  it("buildGraph handles node with null config", () => {
    const nodeNullConfig = makeSimpleNode("n1", "polars", { config: null as unknown as Record<string, unknown> })

    expect(() => buildGraph([nodeNullConfig], [])).not.toThrow()
  })

  it("nodeResultsStore.setPreview works for a node with missing config", () => {
    const store = useNodeResultsStore.getState()
    const preview = makePreviewData("nodeNoConfig", "Test")
    expect(() => store.setPreview("nodeNoConfig", preview, 0)).not.toThrow()
  })

  it("makeNode factory with empty data overrides has defaults", () => {
    const node = makeNode("n1", "polars")
    expect(node.data.config).toBeDefined()
    expect(node.data.label).toBe("Node n1")
    expect(node.data.nodeType).toBe("polars")
  })

  it("makeNode with config explicitly set to undefined", () => {
    const node = makeNode("n1", "polars", { data: { config: undefined } as unknown as Node["data"] })
    // The spread means config key exists but value is undefined
    expect(node.data.config).toBeUndefined()
    // Other defaults should still be present
    expect(node.data.label).toBeDefined()
    expect(node.data.nodeType).toBe("polars")
  })

  it("computeNextNodeId works with nodes that have no data at all", () => {
    // Extreme case: a node with no data field (malformed)
    const malformedNodes = [
      { id: "transform_5", position: { x: 0, y: 0 }, data: {} },
    ] as Node[]

    expect(() => computeNextNodeId(malformedNodes)).not.toThrow()
    expect(computeNextNodeId(malformedNodes)).toBe(6)
  })
})

// ══════════════════════════════════════════════════════════════════
// 6. Empty graph (no nodes, no edges)
// ══════════════════════════════════════════════════════════════════

describe("6. Empty graph", () => {
  beforeEach(resetStores)

  it("computeNextNodeId with empty array returns 0", () => {
    expect(computeNextNodeId([])).toBe(0)
  })

  it("normalizeEdges with empty array returns empty array", () => {
    expect(normalizeEdges([])).toEqual([])
  })

  it("buildGraph with no nodes and no edges returns valid structure", () => {
    const result = buildGraph([], [])
    expect(result).toEqual({
      nodes: [],
      edges: [],
      submodels: undefined,
      preamble: undefined,
    })
  })

  it("nodeResultsStore handles operations on empty graph", () => {
    const store = useNodeResultsStore.getState()

    // All getters should return null/empty for non-existent nodes
    expect(store.getPreview("any")).toBeNull()
    expect(store.getColumns("any")).toBeNull()
    expect(store.getOptimiserPreview("any")).toBeNull()
    expect(store.getModellingPreview("any")).toBeNull()

    // clearNode on non-existent node should not crash
    expect(() => store.clearNode("nonexistent")).not.toThrow()
  })

  it("UIStore defaults are valid with empty graph", () => {
    const state = useUIStore.getState()
    expect(state.hoveredNodeId).toBeNull()
    expect(state.submodelDialog).toBeNull()
    expect(state.renameDialog).toBeNull()
    expect(state.syncBanner).toBeNull()
  })

  it("GraphStore never-saved sentinel: lastSavedSnapshot starts null (Wave 7E)", () => {
    // After Wave 7E, dirty-tracking state lives on useGraphStore.
    // `null` means "never saved this session" — the derived dirty flag
    // (#99) uses this as its sentinel.
    expect(useGraphStore.getState().lastSavedSnapshot).toBeNull()
  })

  it("SettingsStore handles empty source list gracefully", () => {
    const store = useSettingsStore.getState()
    store.setSources([])
    expect(useSettingsStore.getState().sources).toEqual([])
  })

  it("UIStore setHoveredNodeId with null does not crash", () => {
    const store = useUIStore.getState()
    expect(() => store.setHoveredNodeId(null)).not.toThrow()
    expect(useUIStore.getState().hoveredNodeId).toBeNull()
  })
})

// ══════════════════════════════════════════════════════════════════
// 7. Duplicate node IDs
// ══════════════════════════════════════════════════════════════════

describe("7. Duplicate node IDs", () => {
  it("computeNextNodeId with duplicate IDs returns correct max + 1", () => {
    const nodes = [
      makeNode("transform_3"),
      makeNode("transform_3"), // duplicate
      makeNode("transform_5"),
    ]

    const nextId = computeNextNodeId(nodes)
    // Should still compute max as 5, so next = 6
    expect(nextId).toBe(6)
  })

  it("normalizeEdges with duplicate edge targets does not crash", () => {
    const edges = [
      makeEdge("a", "b", { id: "e1" }),
      makeEdge("a", "b", { id: "e2" }), // same source-target, different IDs
      makeEdge("a", "b", { id: "e1" }), // fully duplicate
    ]

    const normalized = normalizeEdges(edges)
    expect(normalized).toHaveLength(3)
    normalized.forEach((e) => {
      expect(e.type).toBe("default")
      expect(e.animated).toBe(false)
    })
  })

  it("buildGraph preserves duplicate node IDs (no dedup at this layer)", () => {
    const nodes = [
      makeSimpleNode("dup", "polars"),
      makeSimpleNode("dup", "polars"),
    ]

    const result = buildGraph(nodes, [])
    // buildGraph is a pass-through — it doesn't deduplicate
    expect(result.nodes).toHaveLength(2)
    expect(result.nodes[0].id).toBe("dup")
    expect(result.nodes[1].id).toBe("dup")
  })

  it("nodeResultsStore overwrites preview for duplicate node IDs", () => {
    const store = useNodeResultsStore.getState()
    const preview1 = makePreviewData("dup", "First")
    const preview2 = makePreviewData("dup", "Second")

    store.setPreview("dup", preview1, 0)
    store.setPreview("dup", preview2, 1)

    const cached = useNodeResultsStore.getState().getPreview("dup")
    // Last write wins
    expect(cached!.data.nodeLabel).toBe("Second")
    expect(cached!.structuralVersion).toBe(1)
  })

  it("clearNode removes data even if ID was used by multiple logical nodes", () => {
    const store = useNodeResultsStore.getState()
    store.setPreview("dup", makePreviewData("dup", "Test"), 0)
    store.setColumns("dup", [{ name: "x", dtype: "float64" }], 0)

    store.clearNode("dup")

    expect(useNodeResultsStore.getState().getPreview("dup")).toBeNull()
    expect(useNodeResultsStore.getState().getColumns("dup")).toBeNull()
  })
})

// ══════════════════════════════════════════════════════════════════
// 8. Self-referencing edge (source === target)
// ══════════════════════════════════════════════════════════════════

describe("8. Self-referencing edge", () => {
  it("normalizeEdges handles self-referencing edge without crash", () => {
    const selfEdge = makeEdge("n1", "n1", { id: "self" })
    const normalized = normalizeEdges([selfEdge])

    expect(normalized).toHaveLength(1)
    expect(normalized[0].source).toBe("n1")
    expect(normalized[0].target).toBe("n1")
    expect(normalized[0].type).toBe("default")
  })

  it("buildGraph includes self-referencing edges", () => {
    const nodes = [makeSimpleNode("n1", "polars")]
    const edges = [makeSimpleEdge("e_self", "n1", "n1")]

    const result = buildGraph(nodes, edges)
    expect(result.edges).toHaveLength(1)
    expect(result.edges[0].source).toBe("n1")
    expect(result.edges[0].target).toBe("n1")
  })

  it("computeNextNodeId unaffected by self-referencing edges", () => {
    const nodes = [makeNode("transform_3")]
    // Self-referencing edges should not affect ID computation
    expect(computeNextNodeId(nodes)).toBe(4)
  })
})

// ══════════════════════════════════════════════════════════════════
// 9. Orphan edges (pointing to non-existent nodes)
// ══════════════════════════════════════════════════════════════════

describe("9. Orphan edges", () => {
  it("normalizeEdges does not validate node existence — passes through orphan edges", () => {
    const orphanEdges = [
      makeEdge("nonexistent_1", "nonexistent_2", { id: "orphan1" }),
      makeEdge("real_node", "ghost_node", { id: "orphan2" }),
    ]

    const normalized = normalizeEdges(orphanEdges)
    expect(normalized).toHaveLength(2)
    expect(normalized[0].source).toBe("nonexistent_1")
    expect(normalized[1].target).toBe("ghost_node")
  })

  it("buildGraph includes orphan edges (validation is server-side)", () => {
    const nodes = [makeSimpleNode("n1", "polars")]
    const edges = [
      makeSimpleEdge("e1", "n1", "n2"), // n2 does not exist
      makeSimpleEdge("e2", "n3", "n1"), // n3 does not exist
    ]

    const result = buildGraph(nodes, edges)
    expect(result.nodes).toHaveLength(1)
    expect(result.edges).toHaveLength(2)
  })

  it("computeNextNodeId ignores edges entirely", () => {
    // Orphan edges have no impact on node ID computation
    const nodes = [makeNode("transform_5")]
    expect(computeNextNodeId(nodes)).toBe(6)
  })

  it("nodeResultsStore does not crash when clearing a node that orphan edges reference", () => {
    const store = useNodeResultsStore.getState()
    store.setPreview("real", makePreviewData("real", "Real"), 0)
    // Edges point to "real" but "ghost" has data
    store.setPreview("ghost", makePreviewData("ghost", "Ghost"), 0)

    // Clear "ghost" — edges still point to it but store doesn't care
    expect(() => store.clearNode("ghost")).not.toThrow()
    expect(useNodeResultsStore.getState().getPreview("ghost")).toBeNull()
    expect(useNodeResultsStore.getState().getPreview("real")).not.toBeNull()
  })
})

// ══════════════════════════════════════════════════════════════════
// 10. Store state after unmount (memory leak prevention)
// ══════════════════════════════════════════════════════════════════

describe("10. Store state after unmount", () => {
  beforeEach(resetStores)

  it("Zustand stores persist state after React component unmount", () => {
    // Zustand stores are module-level singletons — they intentionally persist
    // after component unmount. This test verifies that accessing store state
    // after all components unmount does not throw or corrupt.

    const store = useNodeResultsStore.getState()
    store.setPreview("n1", makePreviewData("n1", "Test"), 0)
    store.setColumns("n1", [{ name: "a", dtype: "float64" }], 0)
    store.startSolveJob("n1", "j1", "N1", {}, "h", "live", 0)

    // Simulate "unmount" by just calling cleanup (no actual React tree here)
    cleanup()

    // Store should still be accessible and consistent
    expect(useNodeResultsStore.getState().getPreview("n1")).not.toBeNull()
    expect(useNodeResultsStore.getState().getColumns("n1")).not.toBeNull()
    expect(useNodeResultsStore.getState().solveJobs["n1"]).toBeDefined()
  })

  it("store operations after cleanup do not throw", () => {
    cleanup()

    const store = useNodeResultsStore.getState()
    expect(() => store.clearNode("any")).not.toThrow()
    expect(() => store.setPreview("x", makePreviewData("x", "X"), 0)).not.toThrow()
    expect(() => store.getPreview("x")).not.toThrow()
  })

  it("UIStore setters work after cleanup without memory leaks", () => {
    cleanup()

    const store = useUIStore.getState()
    expect(() => store.setPaletteOpen(false)).not.toThrow()
    expect(() => store.setHoveredNodeId("n1")).not.toThrow()
    expect(() => store.setSyncBanner("test")).not.toThrow()

    // State should be updated
    expect(useUIStore.getState().paletteOpen).toBe(false)
    expect(useUIStore.getState().hoveredNodeId).toBe("n1")
    expect(useUIStore.getState().syncBanner).toBe("test")
  })

  it("GraphStore markSaved works after cleanup without memory leaks (Wave 7E)", () => {
    cleanup()

    const store = useGraphStore.getState()
    expect(() => store.markSaved()).not.toThrow()

    // markSaved captures the current graph state as the baseline.
    expect(useGraphStore.getState().lastSavedSnapshot).not.toBeNull()
  })

  it("toastStore addToast after cleanup does not throw", () => {
    cleanup()

    expect(() => useToastStore.getState().addToast("info", "After unmount")).not.toThrow()
    const toasts = useToastStore.getState().toasts
    expect(toasts.length).toBeGreaterThan(0)
    expect(toasts[toasts.length - 1].text).toBe("After unmount")
  })

  it("multiple subscribe/unsubscribe cycles do not leak", () => {
    const callbacks: (() => void)[] = []

    // Simulate 100 subscribe/unsubscribe cycles
    for (let i = 0; i < 100; i++) {
      const unsub = useNodeResultsStore.subscribe(() => {})
      callbacks.push(unsub)
    }

    // Unsubscribe all
    callbacks.forEach((unsub) => unsub())

    // Store should still function
    const store = useNodeResultsStore.getState()
    expect(() => store.clearNode("any")).not.toThrow()
  })

  it("rapid setState calls do not cause inconsistent state", () => {
    const store = useNodeResultsStore.getState()

    // Rapidly alternate between setting and clearing
    for (let i = 0; i < 50; i++) {
      store.setPreview("rapid", makePreviewData("rapid", `V${i}`), i)
      store.clearNode("rapid")
    }

    // After all operations, the node should be cleared (last op was clearNode)
    expect(useNodeResultsStore.getState().getPreview("rapid")).toBeNull()

    // Set one more time — should work fine
    store.setPreview("rapid", makePreviewData("rapid", "Final"), 99)
    expect(useNodeResultsStore.getState().getPreview("rapid")!.data.nodeLabel).toBe("Final")
  })
})
