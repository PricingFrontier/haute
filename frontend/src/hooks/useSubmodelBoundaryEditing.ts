import { useCallback, useEffect } from "react"
import { applyEdgeChanges, type Connection, type Edge, type EdgeChange, type Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelBoundaryEdgeData } from "../types/node"
import {
  applySubmodelBoundaryConnection,
  reconcileSubmodelBoundaryState,
  removeSubmodelBoundaryEdges,
  type SubmodelBoundaryEditResult,
  type SubmodelBoundaryEditState,
} from "../utils/submodelBoundaryEditing"

type GraphRef = React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
type ParentGraphRef = React.MutableRefObject<{ nodes: Node[]; edges: PipelineEdge[]; submodels: Record<string, unknown> } | null>
type Setter = (nodes: Node[] | ((nodes: Node[]) => Node[]), edges: Edge[] | ((edges: Edge[]) => Edge[]), submodels: Record<string, unknown>) => void

export interface UseSubmodelBoundaryEditingParams {
  activeSubmodelName: string | null
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown>
  graphRef: GraphRef
  parentGraphRef: ParentGraphRef
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesAndEdgesAndSubmodels: Setter
}

const isBoundaryNode = (node: Node | undefined) => node?.type === "submodelPort"
const isBoundaryEdge = (edge: Edge) => {
  const data = edge.data as SubmodelBoundaryEdgeData | undefined
  return data?.submodelBoundary?.direction === "input" || data?.submodelBoundary?.direction === "output"
}

export default function useSubmodelBoundaryEditing({
  activeSubmodelName, nodes, edges, submodels, graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels,
}: UseSubmodelBoundaryEditingParams) {
  const state = useCallback((): SubmodelBoundaryEditState | null => {
    if (!activeSubmodelName || !parentGraphRef.current) return null
    return { submodelName: activeSubmodelName, viewNodes: nodes, viewEdges: edges, parentNodes: parentGraphRef.current.nodes, parentEdges: parentGraphRef.current.edges, submodels }
  }, [activeSubmodelName, nodes, edges, submodels, parentGraphRef])
  const commit = useCallback((result: SubmodelBoundaryEditResult) => {
    graphRef.current = { nodes: result.viewNodes, edges: result.viewEdges }
    parentGraphRef.current = { nodes: result.parentNodes, edges: result.parentEdges, submodels: result.submodels }
    submodelsRef.current = result.submodels
    setNodesAndEdgesAndSubmodels(result.viewNodes, result.viewEdges, result.submodels)
  }, [graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels])
  const reconcileActiveSubmodel = useCallback(() => {
    const current = state()
    return current ? reconcileSubmodelBoundaryState(current) : null
  }, [state])

  useEffect(() => {
    const result = reconcileActiveSubmodel()
    if (!result) return
    parentGraphRef.current = { nodes: result.parentNodes, edges: result.parentEdges, submodels: result.submodels }
    submodelsRef.current = result.submodels
  }, [reconcileActiveSubmodel, parentGraphRef, submodelsRef])

  const commitBoundaryConnection = useCallback((connection: Connection): boolean => {
    const current = state()
    if (!current) return false
    const source = nodes.find(node => node.id === connection.source)
    const target = nodes.find(node => node.id === connection.target)
    if (!isBoundaryNode(source) && !isBoundaryNode(target)) return false
    const result = applySubmodelBoundaryConnection(current, connection)
    if (result) commit(result)
    return true
  }, [state, nodes, commit])

  const deleteBoundaryEdge = useCallback((id: string): boolean => {
    const current = state()
    if (!current) return false
    const edge = edges.find(candidate => candidate.id === id)
    if (!edge || !isBoundaryEdge(edge)) return false
    const result = removeSubmodelBoundaryEdges(current, [id])
    if (result) commit(result)
    return true
  }, [state, edges, commit])

  const onBoundaryEdgesChange = useCallback((changes: EdgeChange[]): boolean => {
    const current = state()
    if (!current) return false
    const removedBoundaryIds = changes.filter((change): change is Extract<EdgeChange, { type: "remove" }> => change.type === "remove").filter(change => edges.some(edge => edge.id === change.id && isBoundaryEdge(edge))).map(change => change.id)
    if (removedBoundaryIds.length === 0) return false
    const removed = removeSubmodelBoundaryEdges(current, removedBoundaryIds)
    if (!removed) return true
    const remaining = changes.filter(change => !(change.type === "remove" && removedBoundaryIds.includes(change.id)))
    const viewEdges = applyEdgeChanges(remaining, removed.viewEdges)
    commit(reconcileSubmodelBoundaryState({ ...removed, viewEdges: viewEdges as PipelineEdge[] }))
    return true
  }, [state, edges, commit])

  return { commitBoundaryConnection, deleteBoundaryEdge, onBoundaryEdgesChange, reconcileActiveSubmodel }
}
