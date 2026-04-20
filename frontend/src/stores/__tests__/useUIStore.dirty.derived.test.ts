/**
 * Phase 3 Wave 7 package 7D — Item #99
 *
 * Pins the derivation of the `dirty` flag in `useUIStore` from a comparison
 * of the current graph snapshot against the last-saved snapshot.
 *
 * Before this refactor, the store held TWO fields that encoded the same
 * information: a boolean `dirty` (written by several sites via `setDirty`)
 * AND a `lastSavedRef` (a React ref held inside `App.tsx`). Keeping them in
 * sync is a class-of-bugs — forget to flip one and the Unsaved-changes dot
 * lies to the user.
 *
 * The derivation pinned here:
 *
 *   - The Zustand store holds ONE field: `lastSavedSnapshot: string | null`.
 *     `null` means "never saved in this session" (initial state).
 *     A non-null string is the JSON-stringified `{nodes, edges, preamble}`
 *     captured at the moment of save (or at pipeline load, which also counts
 *     as the current on-disk state being the saved state).
 *
 *   - `dirty` is derived. The store exports a `selectIsDirty` selector that
 *     takes the store state plus the current snapshot string and returns a
 *     boolean. There is NO `dirty` field and NO `setDirty` action on the
 *     store.
 *
 *   - Consumers call `markSaved(snapshot)` after a successful save or load
 *     to replace the stored last-saved reference. They call `selectIsDirty`
 *     to read the derived boolean.
 *
 * Rationale for "current snapshot passed in, not held in store": the graph
 * (nodes, edges) lives in React Flow state inside `FlowEditor` (via the
 * `useUndoRedo` hook). Lifting it into Zustand for the sole purpose of
 * making dirty-derivation a store-only selector would be a much bigger
 * refactor and risks re-render storms on every keystroke. Deriving at the
 * call-site keeps the change surgical while still eliminating the two-field
 * inconsistency.
 */

import { describe, it, expect, beforeEach } from "vitest"
import useUIStore, { selectIsDirty, serializeSnapshot } from "../useUIStore"
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
 * Reset the store to a clean "never-saved" state for each test.
 * Uses setState with a full object to exercise the full state shape.
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
    lastSavedSnapshot: null,
    nodePanelWidth: 0,
    hoveredNodeId: null,
    nodeSearchOpen: false,
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
  // These first few tests pin the new API shape so that any accidental
  // reintroduction of `dirty` / `setDirty` as writable store fields is
  // caught immediately.
  // -----------------------------------------------------------------------

  describe("store shape", () => {
    it("does not expose a `dirty` field on the store state", () => {
      const state = useUIStore.getState() as Record<string, unknown>
      expect(state).not.toHaveProperty("dirty")
    })

    it("does not expose a `setDirty` action on the store state", () => {
      const state = useUIStore.getState() as Record<string, unknown>
      expect(state).not.toHaveProperty("setDirty")
    })

    it("exposes `lastSavedSnapshot` (initial value: null) natively — not from test setup", () => {
      // Zustand's setState merges by default, so any key injected by a
      // prior test's setState would linger. We use `getInitialState()` to
      // read the pristine shape defined by the store's create() call.
      // This will fail today because the current store does not define
      // `lastSavedSnapshot` in its creator function — which is the right
      // behavior pre-refactor.
      const initial = useUIStore.getInitialState() as Record<string, unknown>
      expect(initial).toHaveProperty("lastSavedSnapshot")
      expect(initial.lastSavedSnapshot).toBeNull()
    })

    it("exposes a `markSaved` action", () => {
      const initial = useUIStore.getInitialState() as Record<string, unknown>
      expect(typeof initial.markSaved).toBe("function")
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
  // selectIsDirty — the derived boolean
  // -----------------------------------------------------------------------

  describe("selectIsDirty", () => {
    it("returns false for initial state with an empty graph (lastSavedSnapshot=null, current=empty)", () => {
      // Pinned choice: untouched + never-saved counts as NOT dirty.
      // The rationale is that on fresh load the backend sends {nodes, edges,
      // preamble}; the dirty-tracking effect in App.tsx guards on
      // `lastSavedRef.current` being set before flagging dirty, so an
      // untouched empty workspace is treated as clean.
      const empty = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      expect(selectIsDirty(useUIStore.getState(), empty)).toBe(false)
    })

    it("returns false after markSaved() with current === saved", () => {
      const snap = serializeSnapshot({
        nodes: [makeNode("a")],
        edges: [],
        preamble: "import x",
      })
      useUIStore.getState().markSaved(snap)
      expect(selectIsDirty(useUIStore.getState(), snap)).toBe(false)
    })

    it("returns true when current !== saved (node added after save)", () => {
      const saved = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useUIStore.getState().markSaved(saved)
      const current = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), current)).toBe(true)
    })

    it("returns true when node config changed after save", () => {
      const saved = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "",
      })
      useUIStore.getState().markSaved(saved)
      const edited = serializeSnapshot({
        nodes: [makeNode("a", { value: 2 })],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), edited)).toBe(true)
    })

    it("returns true when preamble changed after save", () => {
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "old" })
      useUIStore.getState().markSaved(saved)
      const edited = serializeSnapshot({ nodes: [], edges: [], preamble: "new" })
      expect(selectIsDirty(useUIStore.getState(), edited)).toBe(true)
    })

    it("returns true when an edge is added after save", () => {
      const nodes = [makeNode("a"), makeNode("b")]
      const saved = serializeSnapshot({ nodes, edges: [], preamble: "" })
      useUIStore.getState().markSaved(saved)
      const current = serializeSnapshot({
        nodes,
        edges: [makeEdge("e1", "a", "b")],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), current)).toBe(true)
    })

    // --------------------- Crucial: save → edit → save → edit → save ---------------------

    it("returns false again after a second save (dirty clears)", () => {
      const v1 = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useUIStore.getState().markSaved(v1)

      const v2 = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      // Currently dirty between v1-save and v2-save:
      expect(selectIsDirty(useUIStore.getState(), v2)).toBe(true)

      useUIStore.getState().markSaved(v2)
      expect(selectIsDirty(useUIStore.getState(), v2)).toBe(false)
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
      useUIStore.getState().markSaved(saved)

      // Simulate an edit:
      const edited = serializeSnapshot({
        nodes: [makeNode("a", { value: 2 })],
        edges: [],
        preamble: "import x",
      })
      expect(selectIsDirty(useUIStore.getState(), edited)).toBe(true)

      // Simulate undo, which produces exactly the saved snapshot:
      const undone = serializeSnapshot({
        nodes: [makeNode("a", { value: 1 })],
        edges: [],
        preamble: "import x",
      })
      expect(selectIsDirty(useUIStore.getState(), undone)).toBe(false)
    })

    it("undo past save-point is still reported as dirty (we undid into pre-save history)", () => {
      // User workflow: edit → save → edit → undo (back to saved) → undo (past saved)
      // The final undone state should NOT equal the saved snapshot, and
      // selectIsDirty should therefore return true. This test pins that
      // "pre-save history is still dirty" — undo does not reach further
      // back and accidentally show clean.
      const preSave = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      const saved = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useUIStore.getState().markSaved(saved)

      // preSave != saved, so undoing past the save-point is dirty:
      expect(preSave).not.toBe(saved)
      expect(selectIsDirty(useUIStore.getState(), preSave)).toBe(true)
    })
  })

  // -----------------------------------------------------------------------
  // markSaved — the only writable entry point for the saved snapshot
  // -----------------------------------------------------------------------

  describe("markSaved", () => {
    it("updates lastSavedSnapshot to the argument string", () => {
      const snap = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useUIStore.getState().markSaved(snap)
      expect(useUIStore.getState().lastSavedSnapshot).toBe(snap)
    })

    it("is idempotent when called twice with the same snapshot", () => {
      const snap = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      useUIStore.getState().markSaved(snap)
      useUIStore.getState().markSaved(snap)
      expect(useUIStore.getState().lastSavedSnapshot).toBe(snap)
    })

    it("replaces the previous saved snapshot on subsequent saves", () => {
      const v1 = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      const v2 = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      useUIStore.getState().markSaved(v1)
      useUIStore.getState().markSaved(v2)
      expect(useUIStore.getState().lastSavedSnapshot).toBe(v2)
    })
  })

  // -----------------------------------------------------------------------
  // Performance / selector semantics
  //
  // `selectIsDirty` is read on every render of the toolbar (so it shows the
  // amber dot). It accepts an already-serialized snapshot string so that
  // the call-site controls memoization of the serialization step; the
  // selector itself must therefore be O(string-compare), not O(graph-size
  // re-serialization).
  // -----------------------------------------------------------------------

  describe("selector semantics", () => {
    it("selectIsDirty is a pure function of (state, snapshot)", () => {
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      useUIStore.getState().markSaved(saved)
      // Identical inputs → identical outputs across repeated calls, no
      // mutation of state.
      const state = useUIStore.getState()
      const r1 = selectIsDirty(state, saved)
      const r2 = selectIsDirty(state, saved)
      expect(r1).toBe(r2)
      expect(r1).toBe(false)
      // State unchanged by selector:
      expect(useUIStore.getState().lastSavedSnapshot).toBe(saved)
    })

    it("selectIsDirty returns a referentially stable boolean (primitive) so Zustand equality works", () => {
      // Booleans are value-compared by Zustand's default equality. A
      // regression where the selector returns a fresh object wrapper each
      // call would cause every component that subscribes to dirty-ness to
      // re-render on every store update. Pin: the selector returns a
      // boolean primitive.
      const saved = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      useUIStore.getState().markSaved(saved)
      const result = selectIsDirty(useUIStore.getState(), saved)
      expect(typeof result).toBe("boolean")
    })

    it("selectIsDirty with `null` lastSavedSnapshot treats the empty graph as clean", () => {
      // Fresh store, never saved. Current = empty graph = initial app state.
      const empty = serializeSnapshot({ nodes: [], edges: [], preamble: "" })
      expect(useUIStore.getState().lastSavedSnapshot).toBeNull()
      expect(selectIsDirty(useUIStore.getState(), empty)).toBe(false)
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
      expect(useUIStore.getState().lastSavedSnapshot).toBeNull()
      expect(selectIsDirty(useUIStore.getState(), nonEmpty)).toBe(true)
    })
  })

  // -----------------------------------------------------------------------
  // Integration-ish: walks the full save/edit/save lifecycle
  // -----------------------------------------------------------------------

  describe("full lifecycle", () => {
    it("load → edit → save → edit → undo-to-save → save", () => {
      // 1. Initial pipeline load: backend returns {nodes:[a], edges:[], preamble:""}
      //    usePipelineAPI calls markSaved with this snapshot.
      const loaded = serializeSnapshot({
        nodes: [makeNode("a", { v: 1 })],
        edges: [],
        preamble: "",
      })
      useUIStore.getState().markSaved(loaded)
      expect(selectIsDirty(useUIStore.getState(), loaded)).toBe(false)

      // 2. User edits node config:
      const edit1 = serializeSnapshot({
        nodes: [makeNode("a", { v: 2 })],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), edit1)).toBe(true)

      // 3. User saves. handleSave calls markSaved:
      useUIStore.getState().markSaved(edit1)
      expect(selectIsDirty(useUIStore.getState(), edit1)).toBe(false)

      // 4. User edits again:
      const edit2 = serializeSnapshot({
        nodes: [makeNode("a", { v: 3 })],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), edit2)).toBe(true)

      // 5. User undoes (Ctrl+Z) — current state is now exactly edit1:
      expect(selectIsDirty(useUIStore.getState(), edit1)).toBe(false)

      // 6. User saves again (even though not dirty — should still work):
      useUIStore.getState().markSaved(edit1)
      expect(selectIsDirty(useUIStore.getState(), edit1)).toBe(false)
    })

    it("websocket sync pulls file changes → markSaved syncs to disk → clean", () => {
      // 1. Local edit makes the graph dirty:
      const loaded = serializeSnapshot({ nodes: [makeNode("a")], edges: [], preamble: "" })
      useUIStore.getState().markSaved(loaded)

      const localEdit = serializeSnapshot({
        nodes: [makeNode("a"), makeNode("b")],
        edges: [],
        preamble: "",
      })
      expect(selectIsDirty(useUIStore.getState(), localEdit)).toBe(true)

      // 2. File changes on disk; websocket pushes a new graph. useWebSocketSync
      //    receives the new graph, swaps local state, and calls markSaved
      //    with the new on-disk snapshot:
      const onDisk = serializeSnapshot({
        nodes: [makeNode("c")],
        edges: [],
        preamble: "# file was rewritten",
      })
      useUIStore.getState().markSaved(onDisk)

      // 3. Current state now equals on-disk state → not dirty:
      expect(selectIsDirty(useUIStore.getState(), onDisk)).toBe(false)
    })
  })
})
