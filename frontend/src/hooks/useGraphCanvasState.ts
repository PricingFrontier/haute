/**
 * React Flow canvas-state adapter backed by useGraphStore.
 *
 * The graph store owns nodes, edges, and history. This hook translates
 * React Flow change deltas into store updates, keeping drag updates cheap
 * while still snapshotting structural edits for undo/redo.
 */
import { useCallback, useEffect, useRef, type MutableRefObject } from "react"
import {
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
} from "@xyflow/react"
import useGraphStore, { computePanelContextFingerprint, computeStructuralFingerprint } from "../stores/useGraphStore"

export interface GraphCanvasState {
  nodes: Node[]
  edges: Edge[]
}

export default function useGraphCanvasState(
  initialNodes: Node[] = [],
  initialEdges: Edge[] = [],
  graphRefreshingRef?: MutableRefObject<number>,
) {
  // Selector-isolated subscriptions keep unrelated store updates from
  // re-rendering this hook's consumers.
  const nodes = useGraphStore((s) => s.nodes)
  const edges = useGraphStore((s) => s.edges)
  const canUndo = useGraphStore((s) => s.undoStack.length > 0)
  const canRedo = useGraphStore((s) => s.redoStack.length > 0)

  // On first mount, seed the graph store from the caller's initial graph.
  // Production mounts this once through FlowEditor; tests render the hook
  // directly, so history is reset here to keep each render isolated.
  const seededRef = useRef(false)
  useEffect(() => {
    if (seededRef.current) return
    seededRef.current = true
    useGraphStore.setState({
      nodes: initialNodes,
      edges: initialEdges,
      undoStack: [],
      redoStack: [],
      structuralVersion: 0,
      structuralFingerprint: computeStructuralFingerprint(initialNodes, initialEdges),
      panelContextVersion: 0,
      panelContextFingerprint: computePanelContextFingerprint(initialNodes, initialEdges),
    })
    // initialNodes/initialEdges are constructor-style seeds, not reactive.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seed-once semantics
  }, [])

  // React Flow emits delta arrays. We apply them against the current store
  // state and push history only for structural edits or the start of a drag.
  // Mid-drag position events stay raw so one drag produces one undo entry.

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
    state.setNodesRaw(nextNodes)
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
    state.setEdgesRaw(nextEdges)
  }, [])

  // Store-backed actions exposed as stable callbacks. They re-read state at
  // call time so rapid successive calls do not capture stale snapshots.

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

  // Combined node+edge mutation as one undo step. Delete/paste gestures must
  // use this instead of setNodes-then-setEdges (which pushes two snapshots).
  const setNodesAndEdges = useCallback(
    (
      nodesUpdater: Node[] | ((nds: Node[]) => Node[]),
      edgesUpdater: Edge[] | ((eds: Edge[]) => Edge[]),
    ) => {
      useGraphStore.getState().setNodesAndEdges(nodesUpdater, edgesUpdater)
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
    if ((graphRefreshingRef?.current ?? 0) > 0) return
    useGraphStore.getState().undo()
  }, [graphRefreshingRef])

  const redo = useCallback(() => {
    if ((graphRefreshingRef?.current ?? 0) > 0) return
    useGraphStore.getState().redo()
  }, [graphRefreshingRef])

  const pushSnapshot = useCallback(() => {
    useGraphStore.getState().pushSnapshot()
  }, [])

  return {
    nodes,
    edges,
    setNodes,
    setEdges,
    setNodesAndEdges,
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
