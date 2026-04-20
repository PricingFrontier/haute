/**
 * React Flow compatibility facade over {@link useGraphStore}.
 *
 * After package 7E (item #100) the authoritative source of graph-shaped
 * state is `useGraphStore`. This hook preserves the `useNodesState` +
 * `useEdgesState` + undo/redo API that the existing canvas wiring (App.tsx,
 * WebSocket sync, submodel navigation, pipeline load) depends on.
 *
 * Migration note: new call-sites should subscribe to `useGraphStore`
 * directly with selectors (e.g. `useGraphStore((s) => s.nodes)`). This
 * hook exists so the large block of canvas plumbing doesn't need to be
 * rewritten in one commit.
 *
 * ── Behaviour ─────────────────────────────────────────────────────────
 *
 *   - `nodes` / `edges` are subscribed via Zustand selectors, so
 *     unrelated store changes do not re-render consumers.
 *   - `initialNodes` / `initialEdges` seed the store once on first mount
 *     when the store is empty. Subsequent mounts re-use whatever the
 *     store already holds (matching the old `useNodesState` semantics
 *     where mounting a component with initial values and then updating
 *     via setNodes preserves updates).
 *   - `onNodesChange` / `onEdgesChange` apply React Flow's change-deltas
 *     against the current store state. Snapshotting logic exactly mirrors
 *     the pre-consolidation behaviour: structural changes + drag-start
 *     push a snapshot; mid-drag position events and pure select events
 *     do not.
 *   - `canUndo` / `canRedo` are subscribed reactively so the toolbar
 *     icons update without re-render of the whole canvas.
 */
import { useCallback, useEffect, useRef } from "react"
import {
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
} from "@xyflow/react"
import useGraphStore from "../stores/useGraphStore"

export interface UndoRedoState {
  nodes: Node[]
  edges: Edge[]
}

export default function useUndoRedo(initialNodes: Node[] = [], initialEdges: Edge[] = []) {
  // Selector-isolated subscriptions — no whole-store reads. Each returns
  // a stable reference from the store, so a change to an unrelated slice
  // (e.g. undoStack) does not trigger a re-render here.
  const nodes = useGraphStore((s) => s.nodes)
  const edges = useGraphStore((s) => s.edges)
  const canUndo = useGraphStore((s) => s.undoStack.length > 0)
  const canRedo = useGraphStore((s) => s.redoStack.length > 0)

  // On first mount, reset the graph store to the caller's initial values.
  // This matches the pre-consolidation semantics where `useNodesState`
  // owned per-instance state — each FlowEditor mount started from its
  // declared initial state. The production app only mounts once, so
  // there is no thrash; tests get the fresh state they expect per
  // `renderHook` call.
  //
  // Undo/redo history is also reset: a fresh mount should not expose
  // leftover undo entries from a previous mount (again, prod has only
  // one mount — but the semantic parity prevents test cross-talk and
  // matches the old `useUndoRedo` where past/future refs were local).
  const seededRef = useRef(false)
  useEffect(() => {
    if (seededRef.current) return
    seededRef.current = true
    useGraphStore.setState({
      nodes: initialNodes,
      edges: initialEdges,
      undoStack: [],
      redoStack: [],
    })
    // initialNodes/initialEdges are the constructor-style seed — not reactive.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seed-once semantics
  }, [])

  // ── onNodesChange / onEdgesChange ────────────────────────────────────
  //
  // React Flow emits delta arrays. We apply them against the store's
  // current nodes/edges and write the result back via the raw setter so
  // only drag-start / structural events push a history snapshot — the
  // mid-drag position events (dozens per second) must not pollute undo.
  //
  // Drag-start detection: a `position` change with `dragging: true` is
  // the START of a drag only when the target node isn't already
  // dragging. React Flow stores the flag on the node itself, so reading
  // store state is the authoritative source.

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const state = useGraphStore.getState()
    const hasStructural = changes.some(
      (c) => c.type === "add" || c.type === "remove" || c.type === "replace",
    )
    const hasDragStart = changes.some((c) => {
      if (c.type !== "position" || c.dragging !== true) return false
      const node = state.nodes.find((n) => n.id === c.id)
      return !node?.dragging
    })

    if (hasStructural || hasDragStart) {
      state.pushSnapshot()
    }

    const nextNodes = applyNodeChanges(changes, state.nodes)
    useGraphStore.setState({ nodes: nextNodes })
  }, [])

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const state = useGraphStore.getState()
    const hasStructural = changes.some(
      (c) => c.type === "add" || c.type === "remove" || c.type === "replace",
    )
    if (hasStructural) {
      state.pushSnapshot()
    }
    const nextEdges = applyEdgeChanges(changes, state.edges)
    useGraphStore.setState({ edges: nextEdges })
  }, [])

  // ── Store-backed actions exposed as stable callbacks ────────────────
  //
  // These re-read from `useGraphStore.getState()` at call time rather
  // than capturing a snapshot in closure, so they remain valid across
  // rapid successive calls (e.g. a save-then-undo sequence).

  const setNodes = useCallback(
    (updater: Node[] | ((nds: Node[]) => Node[])) => {
      useGraphStore.getState().setNodes(updater)
    },
    [],
  )

  const setEdges = useCallback(
    (updater: Edge[] | ((eds: Edge[]) => Edge[])) => {
      useGraphStore.getState().setEdges(updater)
    },
    [],
  )

  const setNodesRaw = useCallback((updater: Node[] | ((nds: Node[]) => Node[])) => {
    useGraphStore.getState().setNodesRaw(updater)
  }, [])

  const setEdgesRaw = useCallback((updater: Edge[] | ((eds: Edge[]) => Edge[])) => {
    useGraphStore.getState().setEdgesRaw(updater)
  }, [])

  const undo = useCallback(() => {
    useGraphStore.getState().undo()
  }, [])

  const redo = useCallback(() => {
    useGraphStore.getState().redo()
  }, [])

  const pushSnapshot = useCallback(() => {
    useGraphStore.getState().pushSnapshot()
  }, [])

  return {
    nodes,
    edges,
    setNodes,
    setEdges,
    setNodesRaw,
    setEdgesRaw,
    onNodesChange,
    onEdgesChange,
    undo,
    redo,
    canUndo,
    canRedo,
    pushSnapshot,
  }
}
