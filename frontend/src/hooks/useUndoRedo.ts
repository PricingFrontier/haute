import { useCallback, useEffect, useRef, useState } from "react"
import {
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
} from "@xyflow/react"

export interface UndoRedoState {
  nodes: Node[]
  edges: Edge[]
}

const MAX_HISTORY = 100

/**
 * Drop-in replacement for useNodesState + useEdgesState that adds undo/redo.
 *
 * Records a snapshot whenever nodes or edges change via a "structural" action
 * (add, remove, connect, data update) but NOT for position-only drags —
 * those are batched: a snapshot is only pushed when dragging ends.
 */
export default function useUndoRedo(initialNodes: Node[] = [], initialEdges: Edge[] = []) {
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<Node>(initialNodes)
  const [edges, setEdges, onEdgesChangeBase] = useEdgesState<Edge>(initialEdges)

  const past = useRef<UndoRedoState[]>([])
  const future = useRef<UndoRedoState[]>([])

  // Keep a live ref so we can read current state without re-renders
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  useEffect(() => {
    nodesRef.current = nodes
    edgesRef.current = edges
  }, [nodes, edges])

  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  const pushSnapshot = useCallback(() => {
    // Shallow-clone each node/edge so in-place mutations (e.g. React Flow
    // updating `position` during drag) don't corrupt historical snapshots.
    // This is O(n) in node count and only runs on structural changes and
    // at drag-start, not on every drag pixel.
    past.current = [
      ...past.current.slice(-(MAX_HISTORY - 1)),
      {
        nodes: nodesRef.current.map((n) => ({ ...n, data: { ...n.data } })),
        edges: edgesRef.current.map((e) => ({ ...e })),
      },
    ]
    future.current = []
    setCanUndo(true)
    setCanRedo(false)
  }, [])

  // Wrap onNodesChange to snapshot before structural changes and drag-starts.
  //
  // Drag-start detection is per-batch: a position change with `dragging:true`
  // is a drag-start only when the targeted node isn't already being dragged.
  // React Flow stores the `dragging` flag on the node itself (see
  // @xyflow/react `applyChange`), so `nodesRef.current` is the authoritative
  // source — no cross-effect ref required.
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const hasStructural = changes.some(
        (c) => c.type === "add" || c.type === "remove" || c.type === "replace"
      )
      const hasDragStart = changes.some((c) => {
        if (c.type !== "position" || c.dragging !== true) return false
        const node = nodesRef.current.find((n) => n.id === c.id)
        return !node?.dragging
      })

      if (hasStructural || hasDragStart) {
        pushSnapshot()
      }

      onNodesChangeBase(changes)
    },
    [onNodesChangeBase, pushSnapshot],
  )

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const hasStructural = changes.some(
        (c) => c.type === "add" || c.type === "remove" || c.type === "replace"
      )
      if (hasStructural) {
        pushSnapshot()
      }
      onEdgesChangeBase(changes)
    },
    [onEdgesChangeBase, pushSnapshot],
  )

  // Wrapped setNodes/setEdges that push snapshots
  const setNodesWithHistory = useCallback(
    (updater: Node[] | ((nds: Node[]) => Node[])) => {
      pushSnapshot()
      setNodes(updater)
    },
    [setNodes, pushSnapshot],
  )

  const setEdgesWithHistory = useCallback(
    (updater: Edge[] | ((eds: Edge[]) => Edge[])) => {
      pushSnapshot()
      setEdges(updater)
    },
    [setEdges, pushSnapshot],
  )

  const undo = useCallback(() => {
    const prev = past.current.pop()
    if (!prev) return
    future.current.push({ nodes: nodesRef.current, edges: edgesRef.current })
    setNodes(prev.nodes)
    setEdges(prev.edges)
    setCanUndo(past.current.length > 0)
    setCanRedo(true)
  }, [setNodes, setEdges])

  const redo = useCallback(() => {
    const next = future.current.pop()
    if (!next) return
    past.current.push({ nodes: nodesRef.current, edges: edgesRef.current })
    setNodes(next.nodes)
    setEdges(next.edges)
    setCanUndo(true)
    setCanRedo(future.current.length > 0)
  }, [setNodes, setEdges])

  return {
    nodes,
    edges,
    setNodes: setNodesWithHistory,
    setEdges: setEdgesWithHistory,
    // Raw setters that bypass history (for loading / external sync)
    setNodesRaw: setNodes,
    setEdgesRaw: setEdges,
    onNodesChange,
    onEdgesChange,
    undo,
    redo,
    canUndo,
    canRedo,
    pushSnapshot,
  }
}
