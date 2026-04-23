/**
 * Phase 3 Wave 7 — package 7E, item #100.
 *
 * Consolidation test for `useGraphStore` (Zustand).
 *
 * Today the graph-shaped state is scattered across:
 *
 *   1. `useGraphCanvasState` — owns `nodes`, `edges` (via ReactFlow's `useNodesState`
 *      / `useEdgesState`) plus `past`/`future` refs and `canUndo`/`canRedo`.
 *   2. `App.tsx` local `useState` — `preamble`.
 *   3. `App.tsx` refs — `lastSavedRef` (JSON snapshot for dirty derivation),
 *      `preambleRef` (live mirror).
 *   4. `useUIStore.dirty` — boolean flag, imperatively set from the
 *      compare-last-state effect in `App.tsx`.
 *
 * Symptoms the consolidation is supposed to fix:
 *
 *   - `dirty` derivation is an effect that runs on *every* nodes/edges
 *     identity change, even position-only drags, because it lives outside
 *     the state owners and can't see the distinction. (Note: 7D fixes the
 *     derivation; 7E is about making it cheap by giving it one source.)
 *   - Undo/redo state (`past`, `future`) lives in refs because re-render
 *     storms would ensue if it lived in state — but that means it cannot
 *     be observed or subscribed to in any React-idiomatic way.
 *   - A component that needs `nodes + edges + preamble + dirty + canUndo`
 *     has to subscribe to 4 hooks/stores and synchronise them manually.
 *
 * ── REVIEWER GATE (from the plan) ────────────────────────────────────
 * "Does consolidation cause re-renders that scatter avoided?"
 *
 * This is the real risk. A monolithic store whose consumers subscribe
 * to the whole object would re-render *more* than the current layout
 * (where, e.g., `nodes` changes don't re-render the `dirty` consumer
 * because it lives in a different store).
 *
 * The ONLY way consolidation pays off is if consumers use Zustand's
 * *selector* pattern (`useGraphStore((s) => s.nodes)`). The tests in
 * this file therefore PIN the selector contract — subscribing to a
 * single slice must NOT re-render when an orthogonal slice changes.
 *
 * If the consolidation PR ships a store that consumers use without
 * selectors (e.g. `const s = useGraphStore()`), these tests will fail
 * and the package should be rejected.
 *
 * ── What this file pins ──────────────────────────────────────────────
 *
 *   A. SHAPE — the store exposes `nodes`, `edges`, `preamble`,
 *      `lastSavedSnapshot`, `undoStack`, `redoStack`, and selector
 *      helpers (`canUndo`, `canRedo`, `isDirty`).
 *
 *   B. SELECTOR ISOLATION — mutating `edges` does NOT re-render a
 *      subscriber that selected only `nodes`; same for `preamble`;
 *      same for `undoStack`.  This is the whole reason to consolidate.
 *
 *   C. UNDO/REDO SEMANTICS — push-new clears redo; undo pops past and
 *      pushes current to redo; redo pops future and pushes current to
 *      past.  Parity with the current `useGraphCanvasState`.
 *
 *   D. DIRTY DERIVATION — `isDirty()` is a pure selector over
 *      `(nodes, edges, preamble, lastSavedSnapshot)`, not an effect.
 *      This is what 7D hinges on: after a save, `lastSavedSnapshot` is
 *      set to the current state and `isDirty()` returns false without
 *      any `setDirty` plumbing.
 *
 *   E. UI STORE BOUNDARY — the old `useUIStore.dirty` imperative API
 *      is removed entirely. `useGraphStore` is the only owner of graph
 *      dirty state.
 *
 *   F. MAX_HISTORY — the 100-entry cap from `useGraphCanvasState` is
 *      preserved; 101st push evicts the oldest entry.
 *
 * Tests are written against the PROPOSED store API (see `GraphStore`
 * interface below). They will fail until the production store exists.
 * That's intentional — this file is the contract.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { renderHook, cleanup, act } from "@testing-library/react"
import { useRef } from "react"
import type { Node, Edge } from "@xyflow/react"
import { makeNode, makeEdge } from "../../test-utils/factories"

// ─────────────────────────────────────────────────────────────────
// Proposed store API (documented here for reviewer reference; the
// production `useGraphStore` must implement this shape).
// ─────────────────────────────────────────────────────────────────

export interface GraphSnapshot {
  nodes: Node[]
  edges: Edge[]
  preamble: string
}

// This interface is exported purely for documentation — the store file
// may redeclare it. It is NOT imported by production code.
export interface GraphStoreShape {
  // State
  nodes: Node[]
  edges: Edge[]
  preamble: string
  lastSavedSnapshot: GraphSnapshot | null
  undoStack: GraphSnapshot[]
  redoStack: GraphSnapshot[]

  // Actions (graph mutations)
  setNodes: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdges: (updater: Edge[] | ((eds: Edge[]) => Edge[])) => void
  setPreamble: (value: string) => void

  // Actions (raw — bypass undo history, used for WebSocket sync / load)
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setPreambleRaw: (value: string) => void

  // Undo/redo
  pushSnapshot: () => void
  undo: () => void
  redo: () => void

  // Dirty derivation — set after save
  markSaved: () => void

  // Selectors (pure, callable from getState())
  isDirty: () => boolean
  canUndo: () => boolean
  canRedo: () => boolean
}

// ─────────────────────────────────────────────────────────────────
// Dynamic import — the store may not exist yet. If import fails,
// we report a clear failure and skip the suite.
// ─────────────────────────────────────────────────────────────────

type UseGraphStore = {
  // Zustand store type — has getState, setState, subscribe, and is callable as a hook
  <T>(selector: (s: GraphStoreShape) => T): T
  (): GraphStoreShape
  getState: () => GraphStoreShape
  setState: (partial: Partial<GraphStoreShape> | ((s: GraphStoreShape) => Partial<GraphStoreShape>)) => void
  subscribe: (listener: (s: GraphStoreShape, prev: GraphStoreShape) => void) => () => void
}

let useGraphStore: UseGraphStore | null = null
let importError: unknown = null

// Dynamic-path import so Vite's static analyser doesn't fail the whole
// suite at transform-time if the file doesn't exist yet. The `/* @vite-ignore */`
// disables rollup's warning; the path is still deterministic at runtime.
try {
  const path = "../useGraphStore"
  const mod = await import(/* @vite-ignore */ path)
  useGraphStore = (mod.default ?? mod.useGraphStore) as UseGraphStore
} catch (e) {
  importError = e
}

function requireStore(): UseGraphStore {
  if (!useGraphStore) {
    throw new Error(
      `useGraphStore does not exist yet. Phase 3 Wave 7 package 7E ` +
        `must ship \`frontend/src/stores/useGraphStore.ts\` implementing ` +
        `the GraphStoreShape contract in this test file. ` +
        `Import error: ${String(importError)}`,
    )
  }
  return useGraphStore
}

function reset() {
  const store = requireStore()
  store.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
  })
}

// ─────────────────────────────────────────────────────────────────

describe("useGraphStore — consolidation", () => {
  beforeEach(() => {
    if (useGraphStore) reset()
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  // ───────────────────────────────────────────────────────────────
  // A. Shape
  // ───────────────────────────────────────────────────────────────

  describe("shape", () => {
    it("exists as a default export from stores/useGraphStore.ts", () => {
      expect(
        useGraphStore,
        `import error: ${String(importError)}`,
      ).not.toBeNull()
    })

    it("exposes the required state keys with correct initial values", () => {
      const store = requireStore()
      const s = store.getState()
      expect(s.nodes).toEqual([])
      expect(s.edges).toEqual([])
      expect(s.preamble).toBe("")
      expect(s.lastSavedSnapshot).toBeNull()
      expect(s.undoStack).toEqual([])
      expect(s.redoStack).toEqual([])
    })

    it("exposes the required action functions", () => {
      const store = requireStore()
      const s = store.getState()
      expect(typeof s.setNodes).toBe("function")
      expect(typeof s.setEdges).toBe("function")
      expect(typeof s.setPreamble).toBe("function")
      expect(typeof s.setNodesRaw).toBe("function")
      expect(typeof s.setEdgesRaw).toBe("function")
      expect(typeof s.setPreambleRaw).toBe("function")
      expect(typeof s.pushSnapshot).toBe("function")
      expect(typeof s.undo).toBe("function")
      expect(typeof s.redo).toBe("function")
      expect(typeof s.markSaved).toBe("function")
      expect(typeof s.isDirty).toBe("function")
      expect(typeof s.canUndo).toBe("function")
      expect(typeof s.canRedo).toBe("function")
    })
  })

  // ───────────────────────────────────────────────────────────────
  // B. Selector isolation — the REVIEWER GATE
  //
  // This is the critical contract. If subscribing to `nodes` causes
  // a re-render when `edges` changes, the whole consolidation is a
  // REGRESSION over the current multi-store layout.
  // ───────────────────────────────────────────────────────────────

  describe("selector isolation (reviewer gate)", () => {
    // Helper — a hook that subscribes to a selector and counts renders.
    // The ref is read back during render on purpose: that's how we observe
    // the render count for the assertion. This pattern is standard for
    // render-counting test helpers and safe because the counter is the
    // observable, not driving a render decision.
    /* eslint-disable react-hooks/refs -- render-counting pattern; the ref IS the observable */
    function useSelectorRenderCount<T>(selector: (s: GraphStoreShape) => T) {
      const store = requireStore()
      const countRef = useRef(0)
      countRef.current += 1
      const value = store(selector)
      return { count: countRef.current, value }
    }
    /* eslint-enable react-hooks/refs */

    it("subscribing to nodes does NOT re-render when edges change", () => {
      const store = requireStore()
      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.nodes),
      )
      const initialCount = result.current.count

      act(() => {
        store.getState().setEdges([makeEdge("a", "b", { id: "e1" })])
      })

      // The nodes-subscriber should not have re-rendered
      expect(result.current.count).toBe(initialCount)
    })

    it("subscribing to nodes does NOT re-render when preamble changes", () => {
      const store = requireStore()
      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.nodes),
      )
      const initialCount = result.current.count

      act(() => {
        store.getState().setPreamble("import polars as pl")
      })

      expect(result.current.count).toBe(initialCount)
    })

    it("subscribing to edges does NOT re-render when nodes change", () => {
      const store = requireStore()
      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.edges),
      )
      const initialCount = result.current.count

      act(() => {
        store.getState().setNodes([makeNode("n1")])
      })

      expect(result.current.count).toBe(initialCount)
    })

    it("subscribing to preamble does NOT re-render when nodes change", () => {
      const store = requireStore()
      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.preamble),
      )
      const initialCount = result.current.count

      act(() => {
        store.getState().setNodes([makeNode("n1")])
      })

      expect(result.current.count).toBe(initialCount)
    })

    it("subscribing to undoStack does NOT re-render when edges change", () => {
      const store = requireStore()
      // Seed an undoStack so its length is stable and not confounded by
      // snapshot-pushing inside setEdges.
      act(() => {
        store.setState({ undoStack: [{ nodes: [], edges: [], preamble: "" }] })
      })

      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.undoStack),
      )
      const initialCount = result.current.count

      // Use the raw setter — that explicitly does NOT push a new snapshot,
      // so any re-render here would be a false subscription.
      act(() => {
        store.getState().setEdgesRaw([makeEdge("a", "b", { id: "e1" })])
      })

      expect(result.current.count).toBe(initialCount)
    })

    it("subscribing to nodes DOES re-render when nodes change", () => {
      // Sanity — make sure the counter actually works.
      const store = requireStore()
      const { result } = renderHook(() =>
        useSelectorRenderCount((s) => s.nodes),
      )
      const initialCount = result.current.count

      act(() => {
        store.getState().setNodesRaw([makeNode("n1")])
      })

      expect(result.current.count).toBeGreaterThan(initialCount)
      expect(result.current.value).toHaveLength(1)
    })
  })

  // ───────────────────────────────────────────────────────────────
  // C. Undo/redo semantics — parity with the current useGraphCanvasState.
  // ───────────────────────────────────────────────────────────────

  describe("undo/redo semantics", () => {
    it("setNodes pushes a snapshot of the prior state onto undoStack", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().setNodes([makeNode("n1"), makeNode("n2")])
      })
      const s = store.getState()
      expect(s.undoStack).toHaveLength(1)
      expect(s.undoStack[0].nodes).toHaveLength(1) // pre-mutation state
      expect(s.nodes).toHaveLength(2)
      expect(s.canUndo()).toBe(true)
    })

    it("setEdges pushes a snapshot onto undoStack", () => {
      const store = requireStore()
      act(() => {
        store.getState().setEdges([makeEdge("a", "b", { id: "e1" })])
      })
      expect(store.getState().canUndo()).toBe(true)
    })

    it("setPreamble pushes a snapshot onto undoStack", () => {
      // Undo must cover preamble changes too — otherwise Ctrl+Z after
      // editing imports leaves an unrecoverable half-state.
      const store = requireStore()
      act(() => {
        store.getState().setPreamble("import polars as pl")
      })
      expect(store.getState().canUndo()).toBe(true)
    })

    it("pushing a new snapshot clears redoStack", () => {
      const store = requireStore()
      // Push → undo → push: the redo from the undo must be cleared.
      act(() => {
        store.getState().setNodes([makeNode("n1")])
      })
      act(() => {
        store.getState().undo()
      })
      expect(store.getState().canRedo()).toBe(true)

      act(() => {
        store.getState().setNodes([makeNode("n2")])
      })
      expect(store.getState().canRedo()).toBe(false)
      expect(store.getState().redoStack).toEqual([])
    })

    it("undo pops undoStack and pushes current state onto redoStack", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().setNodes([makeNode("n1"), makeNode("n2")])
      })

      expect(store.getState().nodes).toHaveLength(2)
      act(() => {
        store.getState().undo()
      })
      expect(store.getState().nodes).toHaveLength(1)
      expect(store.getState().canUndo()).toBe(false)
      expect(store.getState().canRedo()).toBe(true)
      expect(store.getState().redoStack).toHaveLength(1)
      expect(store.getState().redoStack[0].nodes).toHaveLength(2)
    })

    it("redo pops redoStack and pushes current onto undoStack", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().setNodes([makeNode("n1"), makeNode("n2")])
      })
      act(() => {
        store.getState().undo()
      })
      act(() => {
        store.getState().redo()
      })
      expect(store.getState().nodes).toHaveLength(2)
      expect(store.getState().canUndo()).toBe(true)
      expect(store.getState().canRedo()).toBe(false)
    })

    it("undo on empty undoStack is a no-op", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().undo()
      })
      expect(store.getState().nodes).toHaveLength(1)
    })

    it("redo on empty redoStack is a no-op", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().redo()
      })
      expect(store.getState().nodes).toHaveLength(1)
    })

    it("setNodesRaw does NOT push a snapshot (bypasses history)", () => {
      const store = requireStore()
      act(() => {
        store.getState().setNodesRaw([makeNode("n1")])
      })
      expect(store.getState().nodes).toHaveLength(1)
      expect(store.getState().canUndo()).toBe(false)
    })

    it("setEdgesRaw does NOT push a snapshot (bypasses history)", () => {
      const store = requireStore()
      act(() => {
        store.getState().setEdgesRaw([makeEdge("a", "b", { id: "e1" })])
      })
      expect(store.getState().edges).toHaveLength(1)
      expect(store.getState().canUndo()).toBe(false)
    })

    it("setPreambleRaw does NOT push a snapshot", () => {
      const store = requireStore()
      act(() => {
        store.getState().setPreambleRaw("import polars as pl")
      })
      expect(store.getState().preamble).toBe("import polars as pl")
      expect(store.getState().canUndo()).toBe(false)
    })
  })

  // ───────────────────────────────────────────────────────────────
  // F. MAX_HISTORY — eviction after 100 entries.
  // (Grouped with undo/redo in reality; kept labelled for clarity.)
  // ───────────────────────────────────────────────────────────────

  describe("MAX_HISTORY (100)", () => {
    it("101st push evicts the oldest undoStack entry", () => {
      const store = requireStore()
      // Push 101 snapshots
      for (let i = 1; i <= 101; i++) {
        act(() => {
          store.getState().setNodes([makeNode(`n${i}`)])
        })
      }
      expect(store.getState().undoStack).toHaveLength(100)

      // The oldest entry (n1's predecessor — the empty initial state) should be gone.
      // After 101 setNodes calls with pre-states [[], [n1], [n2], ..., [n100]],
      // the first 100 undo entries should be [[n1], [n2], ..., [n100]] —
      // the original empty-array pre-state was evicted.
      expect(store.getState().undoStack[0].nodes).toHaveLength(1)
      expect(store.getState().undoStack[0].nodes[0].id).toBe("n1")
    })
  })

  // ───────────────────────────────────────────────────────────────
  // D. Dirty derivation — pure selector, not an effect.
  // ───────────────────────────────────────────────────────────────

  describe("isDirty() — pure selector", () => {
    it("returns false when no save has ever happened (lastSavedSnapshot null)", () => {
      // The "never saved" state is deliberately not-dirty: a fresh
      // pipeline with no changes shouldn't prompt for save. Dirty only
      // becomes true once we know there's a saved baseline to diverge from.
      const store = requireStore()
      expect(store.getState().isDirty()).toBe(false)
    })

    it("returns false immediately after markSaved() with no changes", () => {
      const store = requireStore()
      act(() => {
        store.setState({
          nodes: [makeNode("n1")],
          edges: [],
          preamble: "",
        })
      })
      act(() => {
        store.getState().markSaved()
      })
      expect(store.getState().isDirty()).toBe(false)
    })

    it("returns true after nodes change post-save", () => {
      const store = requireStore()
      act(() => {
        store.setState({ nodes: [makeNode("n1")] })
      })
      act(() => {
        store.getState().markSaved()
      })
      act(() => {
        store.getState().setNodesRaw([makeNode("n1"), makeNode("n2")])
      })
      expect(store.getState().isDirty()).toBe(true)
    })

    it("returns true after edges change post-save", () => {
      const store = requireStore()
      act(() => {
        store.getState().markSaved()
      })
      act(() => {
        store.getState().setEdgesRaw([makeEdge("a", "b", { id: "e1" })])
      })
      expect(store.getState().isDirty()).toBe(true)
    })

    it("returns true after preamble change post-save", () => {
      const store = requireStore()
      act(() => {
        store.getState().markSaved()
      })
      act(() => {
        store.getState().setPreambleRaw("import polars as pl")
      })
      expect(store.getState().isDirty()).toBe(true)
    })

    it("returns to false after a subsequent markSaved()", () => {
      const store = requireStore()
      act(() => {
        store.getState().markSaved()
      })
      act(() => {
        store.getState().setNodesRaw([makeNode("n1")])
      })
      expect(store.getState().isDirty()).toBe(true)
      act(() => {
        store.getState().markSaved()
      })
      expect(store.getState().isDirty()).toBe(false)
    })

    it("is stable across renders — calling isDirty twice gives same answer", () => {
      const store = requireStore()
      act(() => {
        store.getState().markSaved()
      })
      expect(store.getState().isDirty()).toBe(false)
      expect(store.getState().isDirty()).toBe(false)
    })
  })

  // ───────────────────────────────────────────────────────────────
  // E. UI store boundary.
  // ───────────────────────────────────────────────────────────────

  describe("useUIStore dirty boundary", () => {
    it("does not expose dirty or setDirty on useUIStore", async () => {
      const uiStoreModule = await import("../useUIStore")
      const uiState = uiStoreModule.default.getState() as unknown as Record<string, unknown>

      expect(uiState).not.toHaveProperty("dirty")
      expect(uiState).not.toHaveProperty("setDirty")
    })

    it("graph-shaped state (nodes/edges/preamble) is NOT duplicated in useUIStore", async () => {
      // If this test fails, consolidation hasn't actually happened —
      // we've added a new store without retiring the old location.
      const uiStoreModule = await import("../useUIStore")
      const uiState = uiStoreModule.default.getState() as unknown as Record<string, unknown>
      expect("nodes" in uiState).toBe(false)
      expect("edges" in uiState).toBe(false)
      expect("preamble" in uiState).toBe(false)
    })
  })
})
