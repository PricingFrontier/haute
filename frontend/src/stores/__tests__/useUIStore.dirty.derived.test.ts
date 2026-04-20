/**
 * Phase 3 Wave 7 package 7D / 7E — Item #99
 *
 * Pins the canonical `serializeSnapshot` + `selectIsDirty` helpers that back
 * the GUI's unsaved-changes indicator.
 *
 * Consolidation history:
 *   - Pre-7D: two fields encoded dirty state (a boolean `dirty` plus a
 *     `lastSavedRef` inside `App.tsx`).  Keeping them in sync was a
 *     class-of-bugs: forget to flip one and the amber dot lies.
 *   - 7D: `dirty` becomes derived.  `useUIStore.lastSavedSnapshot` holds
 *     the on-disk baseline as a canonical string; `selectIsDirty(state,
 *     currentSnapshot)` returns the derived boolean.
 *   - 7E: the `lastSavedSnapshot` state moves into `useGraphStore` (which
 *     owns graph-shaped state), together with `markSaved()`.  The pure
 *     helpers `serializeSnapshot` and `selectIsDirty` move to
 *     `utils/graphSnapshot` and are re-exported from `useUIStore` for
 *     back-compat imports.
 *
 * What this file pins after 7E:
 *
 *   - `serializeSnapshot` and `selectIsDirty` are still importable from
 *     `../useUIStore` (they're re-exports from the utility module).
 *   - `useUIStore` no longer carries `lastSavedSnapshot` / `markSaved` —
 *     those live on `useGraphStore`.
 *   - `selectIsDirty` is a pure function of its arguments; it does not
 *     read any store.
 */

import { describe, it, expect, beforeEach } from "vitest"
import useUIStore, { selectIsDirty, serializeSnapshot } from "../useUIStore"
import useGraphStore from "../useGraphStore"
import type { Node, Edge } from "@xyflow/react"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeNode(id: string, data: Record<string, unknown> = {}): Node {
  return { id, position: { x: 0, y: 0 }, data, type: "default" } as Node
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target } as Edge
}

/**
 * Reset both stores to a clean "never-saved" state for each test.  The UI
 * store no longer carries dirty state itself, but we reset it to guard
 * against test cross-talk on other slices; the graph store is where
 * `lastSavedSnapshot` actually lives.
 */
function resetStore() {
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
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useUIStore — derived dirty flag (item #99)", () => {
  beforeEach(resetStore)

  // -----------------------------------------------------------------------
  // Shape / surface area
  //
  // Pins that the post-7E `useUIStore` has no dirty-state fields left,
  // and that the helper exports are still in place.
  // -----------------------------------------------------------------------

  describe("store shape", () => {
    it("does not expose a `dirty` field on the store state", () => {
      const state = useUIStore.getState() as unknown as Record<string, unknown>
      expect(state).not.toHaveProperty("dirty")
    })

    it("does not expose a `setDirty` action on the store state", () => {
      const state = useUIStore.getState() as unknown as Record<string, unknown>
      expect(state).not.toHaveProperty("setDirty")
    })

    it("no longer exposes `lastSavedSnapshot` on useUIStore (moved to useGraphStore)", () => {
      // 7E migration: the saved-baseline moved to useGraphStore.  Any
      // import that still reaches for `useUIStore.lastSavedSnapshot`
      // should fail loudly rather than silently read undefined.
      const initial = useUIStore.getInitialState() as unknown as Record<string, unknown>
      expect(initial).not.toHaveProperty("lastSavedSnapshot")
    })

    it("no longer exposes a `markSaved` action on useUIStore (moved to useGraphStore)", () => {
      const initial = useUIStore.getInitialState() as unknown as Record<string, unknown>
      expect(initial).not.toHaveProperty("markSaved")
    })

    it("exports a `selectIsDirty` pure selector", () => {
      expect(typeof selectIsDirty).toBe("function")
    })

    it("exports a `serializeSnapshot` helper used to produce the canonical string", () => {
      expect(typeof serializeSnapshot).toBe("function")
    })
  })

  // -----------------------------------------------------------------------
  // serializeSnapshot — the canonical shape being compared
  //
  // The saved snapshot encodes `{nodes, edges, preamble}`. Extra keys
  // (preserved_blocks, submodels, etc.) are explicitly out of scope —
  // changing a preserved block should not mark the graph dirty, because
  // preserved blocks are round-tripped verbatim and aren't user-editable
  // via the GUI.
  // -----------------------------------------------------------------------

  describe("serializeSnapshot", () => {
    it("encodes nodes, edges, and preamble", () => {
      const snap = serializeSnapshot({
        nodes: [makeNode("a")],
        edges: [makeEdge("e1", "a", "b")],
        preamble: "import x",
      })
      expect(typeof snap).toBe("string")
      const parsed = JSON.parse(snap) as { nodes: unknown; edges: unknown; preamble: unknown }
      expect(parsed.preamble).toBe("import x")
      expect(Array.isArray(parsed.nodes)).toBe(true)
      expect(Array.isArray(parsed.edges)).toBe(true)
    })

    it("is deterministic for equal inputs (value-equality, not reference)", () => {
      const nodesA = [makeNode("a", { config: 1 })]
      const edgesA = [makeEdge("e1", "a", "b")]
      const nodesB = [makeNode("a", { config: 1 })]
      const edgesB = [makeEdge("e1", "a", "b")]
      const snapA = serializeSnapshot({ nodes: nodesA, edges: edgesA, preamble: "p" })
      const snapB = serializeSnapshot({ nodes: nodesB, edges: edgesB, preamble: "p" })
      expect(snapA).toBe(snapB)
    })

    it("differs when a node's data changes", () => {
      const snap1 = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "",
      })
      const snap2 = serializeSnapshot({
        nodes: [makeNode("a", { value: 2 })],
        edges: [],
        preamble: "",
      })
      expect(snap1).not.toBe(snap2)
    })

    it("differs when edges change", () => {
      const snap1 = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      const snap2 = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [makeEdge("e", "a", "b")],
        preamble: "",
      })
      expect(snap1).not.toBe(snap2)
    })

    it("differs when preamble changes", () => {
      const nodes = [makeNode("a")]
      const edges: Edge[] = []
      const snap1 = serializeSnapshot({ nodes, edges, preamble: "import pandas" })
      const snap2 = serializeSnapshot({ nodes, edges, preamble: "import numpy" })
      expect(snap1).not.toBe(snap2)
    })
  })

  // -----------------------------------------------------------------------
  // selectIsDirty — the derived boolean.
  //
  // selectIsDirty is pure: it takes a `{ lastSavedSnapshot }` object and
  // the current snapshot string and returns a boolean.  It does not read
  // any store, so these tests pass raw state objects.
  // -----------------------------------------------------------------------

  describe("selectIsDirty", () => {
    it("returns false for initial state with an empty graph (lastSavedSnapshot=null, current=empty)", () => {
      // Pinned choice: untouched + never-saved counts as NOT dirty.
      // The rationale is that on fresh load the backend sends {nodes, edges,
      // preamble}; an untouched empty workspace is treated as clean.
      const empty = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      expect(selectIsDirty({ lastSavedSnapshot: null }, empty)).toBe(false)
    })

    it("returns false when current snapshot equals the saved snapshot", () => {
      const snap = serializeSnapshot({
        nodes: [makeNode("a")],
        edges: [],
        preamble: "import x",
      })
      expect(selectIsDirty({ lastSavedSnapshot: snap }, snap)).toBe(false)
    })

    it("returns true when current !== saved (node added after save)", () => {
      const saved = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      const current = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, current)).toBe(true)
    })

    it("returns true when node config changed after save", () => {
      const saved = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "",
      })
      const edited = serializeSnapshot({
        nodes: [makeNode("a", { value: 2 })],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, edited)).toBe(true)
    })

    it("returns true when preamble changed after save", () => {
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "old" })
      const edited = serializeSnapshot({ nodes: [], edges: [], preamble: "new" })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, edited)).toBe(true)
    })

    it("returns true when an edge is added after save", () => {
      const nodes = [makeNode("a"), makeNode("b")]
      const saved = serializeSnapshot({ nodes, edges: [], preamble: "" })
      const current = serializeSnapshot({
        nodes,
        edges: [makeEdge("e1", "a", "b")],
        preamble: "",
      })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, current)).toBe(true)
    })

    // --------------------- Crucial: save → edit → save → edit → save ---------------------

    it("returns false again after a second save (dirty clears)", () => {
      const v1 = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      const v2 = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      // Between v1-save and v2-save: dirty.
      expect(selectIsDirty({ lastSavedSnapshot: v1 }, v2)).toBe(true)
      // After v2-save (saved is now v2): clean.
      expect(selectIsDirty({ lastSavedSnapshot: v2 }, v2)).toBe(false)
    })

    // --------------------- The big one: undo-to-saved-state ---------------------
    //
    // This is the class-of-bug the derived approach fixes outright. With a
    // boolean `dirty` set imperatively, undoing back to the exact saved
    // state leaves dirty=true — the user sees an unsaved indicator even
    // though their graph matches disk. Because dirty is now PURE, this
    // cannot happen.

    it("returns false after undoing back to the last-saved graph state", () => {
      const saved = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "import x",
      })

      // Simulate an edit:
      const edited = serializeSnapshot({
        nodes: [makeNode("a", { value: 2 })],
        edges: [],
        preamble: "import x",
      })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, edited)).toBe(true)

      // Simulate undo, which produces exactly the saved snapshot:
      const undone = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "import x",
      })
      expect(selectIsDirty({ lastSavedSnapshot: saved }, undone)).toBe(false)
    })

    it("undo past save-point is still reported as dirty (we undid into pre-save history)", () => {
      // User workflow: edit → save → edit → undo (back to saved) → undo (past saved)
      // The final undone state should NOT equal the saved snapshot, and
      // selectIsDirty should therefore return true. This test pins that
      // "pre-save history is still dirty" — undo does not reach further
      // back and accidentally show clean.
      const preSave = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      const saved = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })

      // preSave != saved, so undoing past the save-point is dirty:
      expect(preSave).not.toBe(saved)
      expect(selectIsDirty({ lastSavedSnapshot: saved }, preSave)).toBe(true)
    })
  })

  // -----------------------------------------------------------------------
  // Performance / selector semantics
  //
  // `selectIsDirty` is read on every render of the toolbar (so it shows
  // the amber dot).  It accepts an already-serialized snapshot string so
  // that the call-site controls memoization of the serialization step;
  // the selector itself must therefore be O(string-compare), not
  // O(graph-size re-serialization).
  // -----------------------------------------------------------------------

  describe("selector semantics", () => {
    it("selectIsDirty is a pure function of (state, snapshot)", () => {
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      const state = { lastSavedSnapshot: saved }
      // Identical inputs → identical outputs across repeated calls, no
      // mutation of state.
      const r1 = selectIsDirty(state, saved)
      const r2 = selectIsDirty(state, saved)
      expect(r1).toBe(r2)
      expect(r1).toBe(false)
      // State unchanged by selector:
      expect(state.lastSavedSnapshot).toBe(saved)
    })

    it("selectIsDirty returns a referentially stable boolean (primitive) so Zustand equality works", () => {
      // Booleans are value-compared by Zustand's default equality. A
      // regression where the selector returns a fresh object wrapper each
      // call would cause every component that subscribes to dirty-ness to
      // re-render on every store update. Pin: the selector returns a
      // boolean primitive.
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      const result = selectIsDirty({ lastSavedSnapshot: saved }, saved)
      expect(typeof result).toBe("boolean")
    })

    it("selectIsDirty with `null` lastSavedSnapshot treats the empty graph as clean", () => {
      // Fresh store, never saved. Current = empty graph = initial app state.
      const empty = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      expect(selectIsDirty({ lastSavedSnapshot: null }, empty)).toBe(false)
    })

    it("selectIsDirty with `null` lastSavedSnapshot AND a non-empty current graph returns true", () => {
      // Edge case: the user types/drags a node before the initial load
      // completes (or the initial load failed). A non-empty graph with
      // nothing saved = dirty.
      const nonEmpty = serializeSnapshot({
        nodes: [makeNode("a")],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty({ lastSavedSnapshot: null }, nonEmpty)).toBe(true)
    })
  })

  // -----------------------------------------------------------------------
  // Integration-ish: walks the full save/edit/save lifecycle using the
  // post-7E API (useGraphStore owns markSaved and lastSavedSnapshot).
  // -----------------------------------------------------------------------

  describe("full lifecycle (via useGraphStore)", () => {
    it("load → edit → save → edit → undo-to-save → save", () => {
      // 1. Initial pipeline load: backend returns {nodes:[a], edges:[], preamble:""}
      //    usePipelineAPI writes the graph into useGraphStore and calls markSaved().
      useGraphStore.setState({
        nodes: [makeNode("a", { v: 1 })],
        edges: [],
        preamble: "",
      })
      useGraphStore.getState().markSaved()
      expect(useGraphStore.getState().isDirty()).toBe(false)

      // 2. User edits node config:
      useGraphStore.setState({ nodes: [makeNode("a", { v: 2 })] })
      expect(useGraphStore.getState().isDirty()).toBe(true)

      // 3. User saves. handleSave calls markSaved:
      useGraphStore.getState().markSaved()
      expect(useGraphStore.getState().isDirty()).toBe(false)

      // 4. User edits again:
      useGraphStore.setState({ nodes: [makeNode("a", { v: 3 })] })
      expect(useGraphStore.getState().isDirty()).toBe(true)

      // 5. User undoes (Ctrl+Z) — current state is now exactly the previous save:
      useGraphStore.setState({ nodes: [makeNode("a", { v: 2 })] })
      expect(useGraphStore.getState().isDirty()).toBe(false)

      // 6. User saves again (even though not dirty — should still work):
      useGraphStore.getState().markSaved()
      expect(useGraphStore.getState().isDirty()).toBe(false)
    })

    it("websocket sync pulls file changes → markSaved syncs to disk → clean", () => {
      // 1. Local edit makes the graph dirty:
      useGraphStore.setState({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useGraphStore.getState().markSaved()
      useGraphStore.setState({ nodes: [makeNode("a"), makeNode("b")] })
      expect(useGraphStore.getState().isDirty()).toBe(true)

      // 2. File changes on disk; websocket pushes a new graph. useWebSocketSync
      //    swaps the store's nodes/edges/preamble and calls markSaved:
      useGraphStore.setState({
        nodes: [makeNode("c")],
        edges: [],
        preamble: "# file was rewritten",
      })
      useGraphStore.getState().markSaved()

      // 3. Current state now equals on-disk state → not dirty:
      expect(useGraphStore.getState().isDirty()).toBe(false)
    })
  })
})
