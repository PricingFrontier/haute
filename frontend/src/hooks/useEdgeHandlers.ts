/**
 * Edge/connection handlers and drag-drop/selection logic extracted from
 * App.tsx FlowEditor.
 *
 * Centralises connect, disconnect, selection-change, context-menu,
 * and drag-drop operations so the main component stays focused on
 * orchestration and rendering.
 */
import { useCallback, type MutableRefObject, type DragEvent } from "react"
import {
  addEdge,
  type Node,
  type Edge,
  type OnConnect,
  type OnSelectionChangeFunc,
} from "@xyflow/react"
import { nodeData } from "../types/node"
import { NODE_TYPES, NODE_TYPE_META, isSingletonType, type NodeTypeValue } from "../utils/nodeTypes"

/** Check whether the target node has reached its maxInputs limit. */
function wouldExceedMaxInputs(
  targetNodeId: string,
  currentNodes: Node[],
  currentEdges: Edge[],
): boolean {
  const targetNode = currentNodes.find((n) => n.id === targetNodeId)
  if (!targetNode) return false
  const meta = NODE_TYPE_META[nodeData(targetNode).nodeType as NodeTypeValue]
  if (!meta?.maxInputs) return false
  const incomingCount = currentEdges.filter((e) => e.target === targetNodeId).length
  return incomingCount >= meta.maxInputs
}

type ContextMenuData = {
  x: number
  y: number
  nodeId: string
  nodeLabel: string
  isSubmodel?: boolean
  isSingleton?: boolean
}

type UseEdgeHandlersParams = {
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  nodeIdCounter: MutableRefObject<number>
  lastSelectedNodeRef: MutableRefObject<Node | null>
  setNodes: (updater: (nds: Node[]) => Node[]) => void
  setEdges: (updater: (eds: Edge[]) => Edge[]) => void
  setSelectedNode: (updater: React.SetStateAction<Node | null>) => void
  setContextMenu: (data: ContextMenuData | null) => void
  fetchPreview: (node: Node) => void
  clearTrace: () => void
  screenToFlowPosition: (pos: { x: number; y: number }) => { x: number; y: number }
  graphRefreshingRef: MutableRefObject<number>
}

export default function useEdgeHandlers({
  graphRef,
  nodeIdCounter: nodeIdCounterRef,
  lastSelectedNodeRef,
  setNodes,
  setEdges,
  setSelectedNode,
  setContextMenu,
  fetchPreview,
  clearTrace,
  screenToFlowPosition,
  graphRefreshingRef,
}: UseEdgeHandlersParams) {
  const onConnect: OnConnect = useCallback(
    (params) => {
      if (params.source === params.target) return
      const { edges: currentEdges, nodes: currentNodes } = graphRef.current
      const exists = currentEdges.some(
        (e) =>
          e.source === params.source &&
          e.target === params.target &&
          e.sourceHandle === (params.sourceHandle ?? null) &&
          e.targetHandle === (params.targetHandle ?? null)
      )
      if (exists) return
      if (wouldExceedMaxInputs(params.target!, currentNodes, currentEdges)) return

      // For submodel nodes, keep the targetHandle so drill-in can route
      // edges to the correct internal port node.

      setEdges((eds) => addEdge(params, eds))
    },
    [graphRef, setEdges],
  )

  const onSelectionChange: OnSelectionChangeFunc = useCallback(({ nodes: selectedNodes }) => {
    if (selectedNodes.length !== 1) {
      // During a WebSocket graph refresh React Flow fires a spurious
      // deselection — ignore it so the open panel doesn't dim.
      if (graphRefreshingRef.current) return
      // Canvas click or multi-select: deselect but keep panel showing last node
      setSelectedNode(null)
      clearTrace()
      // Don't clear previewData or lastSelectedNodeRef -- panel stays visible
    }
  }, [setSelectedNode, clearTrace, graphRefreshingRef])

  /** Opens panel on a full click (mousedown+mouseup) — skipped for drags. */
  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode((prev) => {
      if (prev?.id !== node.id) {
        fetchPreview(node)
        clearTrace()
      }
      return node
    })
    lastSelectedNodeRef.current = node
  }, [setSelectedNode, fetchPreview, clearTrace, lastSelectedNodeRef])

  const handleDeleteEdge = useCallback((edgeId: string) => {
    setEdges((eds) => eds.filter((e) => e.id !== edgeId))
  }, [setEdges])

  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: Node) => {
    event.preventDefault()
    const nt = nodeData(node).nodeType
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      nodeId: node.id,
      nodeLabel: String(node.data.label),
      isSubmodel: nt === NODE_TYPES.SUBMODEL,
      isSingleton: isSingletonType(nt),
    })
  }, [setContextMenu])

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
  }, [])

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault()
      const type = event.dataTransfer.getData("application/reactflow-type")
      if (!type) return

      let config = {}
      try {
        config = JSON.parse(event.dataTransfer.getData("application/reactflow-config") || "{}")
      } catch { /* ignore */ }

      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      nodeIdCounterRef.current += 1
      const id = `${type}_${nodeIdCounterRef.current}`

      const newNode: Node = {
        id,
        type,
        position,
        data: {
          label: `${NODE_TYPE_META[type as NodeTypeValue]?.name || "Node"} ${nodeIdCounterRef.current}`,
          description: "",
          nodeType: type,
          config,
        },
      }

      setNodes((nds) => [
        ...nds.map((n) => ({ ...n, selected: false })),
        { ...newNode, selected: true },
      ])
      setSelectedNode(newNode)
    },
    [screenToFlowPosition, nodeIdCounterRef, setNodes, setSelectedNode],
  )

  return {
    onConnect,
    onSelectionChange,
    onNodeClick,
    handleDeleteEdge,
    onNodeContextMenu,
    onDragOver,
    onDrop,
  }
}
