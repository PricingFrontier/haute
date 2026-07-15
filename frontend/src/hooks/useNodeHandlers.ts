/**
 * Node CRUD handlers extracted from App.tsx FlowEditor.
 *
 * Centralises add, delete, duplicate, rename, create-instance, and
 * auto-layout operations so the main component stays focused on
 * orchestration and rendering.
 */
import { useCallback, useRef, useState, type MutableRefObject } from "react"
import type { Node, Edge } from "@xyflow/react"
import useToastStore from "../stores/useToastStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useUIStore from "../stores/useUIStore"
import { nodeData } from "../types/node"
import { NODE_TYPES, isSingletonType } from "../utils/nodeTypes"
import { getLayoutedElements } from "../utils/layout"
import type { PreviewData } from "../panels/DataPreview"

type UseNodeHandlersParams = {
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  nodeIdCounter: MutableRefObject<number>
  lastSelectedNodeRef: MutableRefObject<Node | null>
  setNodes: (updater: (nds: Node[]) => Node[]) => void
  setNodesAndEdges: (
    nodes: (nds: Node[]) => Node[],
    edges: (eds: Edge[]) => Edge[],
  ) => void
  setSelectedNode: (updater: React.SetStateAction<Node | null>) => void
  setPreviewData: (updater: React.SetStateAction<PreviewData | null>) => void
  fitView: (opts?: { padding?: number }) => void
}

export default function useNodeHandlers({
  graphRef,
  nodeIdCounter: nodeIdCounterRef,
  lastSelectedNodeRef,
  setNodes,
  setNodesAndEdges,
  setSelectedNode,
  setPreviewData,
  fitView,
}: UseNodeHandlersParams) {
  const layoutInFlightRef = useRef(false)
  const [isAutoLayouting, setIsAutoLayouting] = useState(false)
  const addToast = useToastStore((s) => s.addToast)
  const clearNode = useNodeResultsStore((s) => s.clearNode)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)

  const handleDeleteNode = useCallback((id: string) => {
    // Node + its edges removed as ONE undo step. setNodes-then-setEdges would
    // push two snapshots, so a single delete would need two undos to reverse
    // (the undo-atomicity bug class).
    setNodesAndEdges(
      (nds) => nds.filter((node) => node.id !== id),
      (eds) => eds.filter((edge) => edge.source !== id && edge.target !== id),
    )
    setSelectedNode((prev) => (prev?.id === id ? null : prev))
    setPreviewData((prev) => (prev?.nodeId === id ? null : prev))
    // Defer cache cleanup by one task tick (Issue #32). If `clearNode(id)`
    // fires synchronously here, any downstream component reading the store
    // during the same render cycle (before React has committed the
    // setNodes update that removes the node from the graph) will see a
    // state where the node still exists in the graph but its cached result
    // has already been wiped — producing a flicker-crash.
    setTimeout(() => { clearNode(id) }, 0)
    if (lastSelectedNodeRef.current?.id === id) lastSelectedNodeRef.current = null

    // Clear UI dialogs that reference the deleted node (Issues #8, #14)
    const uiState = useUIStore.getState()
    if (uiState.renameDialog?.nodeId === id) setRenameDialog(null)
    const subDlg = uiState.submodelDialog
    if (subDlg && subDlg.nodeIds.includes(id)) setSubmodelDialog(null)
  }, [setNodesAndEdges, lastSelectedNodeRef, setSelectedNode, setPreviewData, clearNode, setRenameDialog, setSubmodelDialog])

  const handleDuplicateNode = useCallback((id: string) => {
    const { nodes: n } = graphRef.current
    const original = n.find((node) => node.id === id)
    if (!original) return
    if (isSingletonType(nodeData(original).nodeType)) return
    nodeIdCounterRef.current += 1
    const newId = `${original.type}_${nodeIdCounterRef.current}`
    const newNode: Node = {
      ...original,
      id: newId,
      position: { x: original.position.x + 40, y: original.position.y + 40 },
      selected: true,
      data: { ...original.data, label: `${original.data.label} copy` },
    }
    setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), newNode])
    setSelectedNode(newNode)
  }, [graphRef, nodeIdCounterRef, setNodes, setSelectedNode])

  const handleCreateInstance = useCallback((id: string) => {
    const { nodes: n } = graphRef.current
    const original = n.find((node) => node.id === id)
    if (!original) return
    nodeIdCounterRef.current += 1
    const origData = nodeData(original)
    const origNodeType = origData.nodeType || NODE_TYPES.POLARS
    const newId = `${origNodeType}_${nodeIdCounterRef.current}`
    const newNode: Node = {
      id: newId,
      type: original.type,
      position: { x: original.position.x + 60, y: original.position.y + 80 },
      selected: true,
      data: {
        label: `${origData.label} instance`,
        description: `Instance of ${origData.label}`,
        nodeType: origNodeType,
        config: { instanceOf: id },
      },
    }
    setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), newNode])
    setSelectedNode(newNode)
    addToast("info", `Created instance of "${origData.label}"`)
  }, [graphRef, nodeIdCounterRef, setNodes, setSelectedNode, addToast])

  const handleRenameNode = useCallback((id: string) => {
    const { nodes: n } = graphRef.current
    const node = n.find((nd) => nd.id === id)
    if (!node) return
    setRenameDialog({ nodeId: id, currentLabel: String(node.data.label) })
  }, [graphRef, setRenameDialog])

  const handleAutoLayout = useCallback(async () => {
    if (layoutInFlightRef.current) return
    const { nodes: n, edges: e } = graphRef.current
    if (n.length === 0) return
    layoutInFlightRef.current = true
    setIsAutoLayouting(true)
    try {
      const layouted = await getLayoutedElements(n, e)
      setNodes(() => layouted)
      setTimeout(() => fitView({ padding: 0.15 }), 50)
      addToast("info", "Auto-layout applied")
    } finally {
      layoutInFlightRef.current = false
      setIsAutoLayouting(false)
    }
  }, [graphRef, setNodes, fitView, addToast])

  return {
    handleDeleteNode,
    handleDuplicateNode,
    handleCreateInstance,
    handleRenameNode,
    handleAutoLayout,
    isAutoLayouting,
  }
}
