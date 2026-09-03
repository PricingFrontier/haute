import { useCallback, useEffect, useRef } from "react"
import {
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react"
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type PipelineEdge,
  type SubmodelBoundaryEdgeData,
  type SubmodelPortData,
} from "../types/node"
import {
  applySubmodelBoundaryConnection,
  reconcileSubmodelBoundaryState,
  removeSubmodelBoundaryEdges,
  type SubmodelBoundaryEditResult,
  type SubmodelBoundaryEditState,
} from "../utils/submodelBoundaryEditing"
import useToastStore from "../stores/useToastStore"
import { resolveEditorGraphIdentities } from "../utils/editorIdentities"

type GraphRef = React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
type ParentGraphRef = React.MutableRefObject<{ nodes: Node[]; edges: PipelineEdge[]; submodels: Record<string, unknown> } | null>
type Setter = (nodes: Node[] | ((nodes: Node[]) => Node[]), edges: Edge[] | ((edges: Edge[]) => Edge[]), submodels: Record<string, unknown>) => void

export interface UseSubmodelBoundaryEditingParams {
  activeSubmodelName: string | null
  activeSubmodelInstanceId: string | null
  activeSubmodelDefinitionId: string | null
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown>
  graphRef: GraphRef
  parentGraphRef: ParentGraphRef
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesAndEdgesAndSubmodels: Setter
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveGraphIdentities?: typeof resolveEditorGraphIdentities
}

export type SharedNodeDeletionResult = "not-applicable" | "committed" | "blocked"

const isBoundaryNode = (node: Node | undefined) => node?.type === "submodelPort"
const hasBoundaryCard = (nodes: Node[], direction: "input" | "output") =>
  nodes.some(node => node.type === "submodelPort" && (node.data as Partial<SubmodelPortData>).portDirection === direction)
const isBoundaryEdge = (edge: Edge) => {
  const data = edge.data as SubmodelBoundaryEdgeData | undefined
  return data?.submodelBoundary?.direction === "input" || data?.submodelBoundary?.direction === "output"
}

function parentOccurrenceHandlesAreResolved(result: SubmodelBoundaryEditResult): boolean {
  const definition = result.submodels[result.definitionId]
  if (!isSubmodelDefinition(definition, result.definitionId)) return false
  const expectedHandles = definition.outputPorts.map((port) => `out__${port.portId}`)
  return result.parentNodes.every((node) => {
    if (node.data.nodeType !== "submodel") return true
    const config = node.data.config
    if (!isSubmodelInstanceConfig(config) || config.definitionId !== result.definitionId) {
      return true
    }
    const mapping = node.data._sourceHandleInputNames
    return typeof mapping === "object"
      && mapping !== null
      && !Array.isArray(mapping)
      && expectedHandles.every((handle) => {
        const value = (mapping as Record<string, unknown>)[handle]
        return typeof value === "string" && value.length > 0
      })
  })
}

export default function useSubmodelBoundaryEditing({
  activeSubmodelName,
  activeSubmodelInstanceId,
  activeSubmodelDefinitionId,
  nodes,
  edges,
  submodels,
  graphRef,
  parentGraphRef,
  submodelsRef,
  setNodesAndEdgesAndSubmodels,
  reservedApiInputFrameLabels,
  resolveGraphIdentities = resolveEditorGraphIdentities,
}: UseSubmodelBoundaryEditingParams) {
  const addToast = useToastStore((store) => store.addToast)
  const identityRequestSerialRef = useRef(0)
  const reportBoundaryError = useCallback((error: unknown) => {
    addToast(
      "error",
      `Shared submodel edit blocked: ${error instanceof Error ? error.message : String(error)}`,
    )
  }, [addToast])
  const state = useCallback((): SubmodelBoundaryEditState | null => {
    if (!activeSubmodelName || !parentGraphRef.current) return null
    // Without both composite cards the visible graph is not a drilled
    // projection (e.g. history restored a pre-drill snapshot); reconciling it
    // would rewrite the submodel metadata from the parent canvas.
    if (!hasBoundaryCard(nodes, "input") || !hasBoundaryCard(nodes, "output")) return null
    if (
      typeof activeSubmodelInstanceId !== "string"
      || activeSubmodelInstanceId.length === 0
      || activeSubmodelInstanceId.trim() !== activeSubmodelInstanceId
      || typeof activeSubmodelDefinitionId !== "string"
      || activeSubmodelDefinitionId.length === 0
      || activeSubmodelDefinitionId.trim() !== activeSubmodelDefinitionId
    ) {
      throw new Error("The active submodel view requires canonical instance identity")
    }
    return {
      submodelName: activeSubmodelName,
      instanceId: activeSubmodelInstanceId,
      definitionId: activeSubmodelDefinitionId,
      viewNodes: nodes,
      viewEdges: edges,
      parentNodes: parentGraphRef.current.nodes,
      parentEdges: parentGraphRef.current.edges,
      submodels,
    }
  }, [
    activeSubmodelName,
    activeSubmodelInstanceId,
    activeSubmodelDefinitionId,
    nodes,
    edges,
    submodels,
    parentGraphRef,
  ])
  const commit = useCallback((result: SubmodelBoundaryEditResult) => {
    graphRef.current = { nodes: result.viewNodes, edges: result.viewEdges }
    parentGraphRef.current = { nodes: result.parentNodes, edges: result.parentEdges, submodels: result.submodels }
    submodelsRef.current = result.submodels
    setNodesAndEdgesAndSubmodels(result.viewNodes, result.viewEdges, result.submodels)
  }, [graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels])
  const commitWithParentIdentities = useCallback((result: SubmodelBoundaryEditResult) => {
    const serial = ++identityRequestSerialRef.current
    if (parentOccurrenceHandlesAreResolved(result)) {
      commit(result)
      return
    }
    const expectedView = graphRef.current
    const expectedParent = parentGraphRef.current
    const expectedSubmodels = submodelsRef.current
    void resolveGraphIdentities({
      nodes: result.parentNodes,
      edges: result.parentEdges,
      submodels: result.submodels,
      reservedApiInputFrameLabels,
    }).then((resolved) => {
      if (
        identityRequestSerialRef.current !== serial
        || graphRef.current !== expectedView
        || parentGraphRef.current !== expectedParent
        || submodelsRef.current !== expectedSubmodels
      ) {
        reportBoundaryError(
          new Error("the workspace changed while parent identities were resolving"),
        )
        return
      }
      commit({
        ...result,
        parentNodes: resolved.nodes,
        parentEdges: resolved.edges,
      })
    }).catch(reportBoundaryError)
  }, [
    commit,
    graphRef,
    parentGraphRef,
    reportBoundaryError,
    reservedApiInputFrameLabels,
    resolveGraphIdentities,
    submodelsRef,
  ])
  useEffect(() => () => {
    identityRequestSerialRef.current += 1
  }, [])
  const reconcileActiveSubmodel = useCallback(() => {
    try {
      const current = state()
      return current ? reconcileSubmodelBoundaryState(current) : null
    } catch (error: unknown) {
      reportBoundaryError(error)
      return null
    }
  }, [state, reportBoundaryError])

  useEffect(() => {
    const result = reconcileActiveSubmodel()
    if (!result) return
    parentGraphRef.current = {
      nodes: result.parentNodes,
      edges: result.parentEdges,
      submodels: result.submodels,
    }
    submodelsRef.current = result.submodels
  }, [reconcileActiveSubmodel, parentGraphRef, submodelsRef])

  const commitBoundaryConnection = useCallback((connection: Connection): boolean => {
    try {
      const current = state()
      if (!current) return false
      const source = nodes.find((node) => node.id === connection.source)
      const target = nodes.find((node) => node.id === connection.target)
      if (!isBoundaryNode(source) && !isBoundaryNode(target)) return false
      const result = applySubmodelBoundaryConnection(current, connection)
      if (result) commitWithParentIdentities(result)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, nodes, commitWithParentIdentities, reportBoundaryError])

  const deleteBoundaryEdge = useCallback((id: string): boolean => {
    try {
      const current = state()
      if (!current) return false
      const edge = edges.find((candidate) => candidate.id === id)
      if (!edge || !isBoundaryEdge(edge)) return false
      const result = removeSubmodelBoundaryEdges(current, [id])
      if (result) commitWithParentIdentities(result)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, edges, commitWithParentIdentities, reportBoundaryError])

  const onBoundaryEdgesChange = useCallback((changes: EdgeChange[]): boolean => {
    const removedBoundaryIds = changes
      .filter((change): change is Extract<EdgeChange, { type: "remove" }> =>
        change.type === "remove")
      .filter((change) =>
        edges.some((edge) => edge.id === change.id && isBoundaryEdge(edge)))
      .map((change) => change.id)
    if (removedBoundaryIds.length === 0) return false
    try {
      const current = state()
      if (!current) return false
      const removed = removeSubmodelBoundaryEdges(current, removedBoundaryIds)
      if (!removed) return true
      const remaining = changes.filter(
        (change) => !(change.type === "remove" && removedBoundaryIds.includes(change.id)),
      )
      const viewEdges = applyEdgeChanges(remaining, removed.viewEdges)
      const reconciled = reconcileSubmodelBoundaryState({
        ...removed,
        viewEdges: viewEdges as PipelineEdge[],
      })
      if (reconciled) commitWithParentIdentities(reconciled)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, edges, commitWithParentIdentities, reportBoundaryError])

  const commitSharedNodeDeletion = useCallback((
    nodeIds: ReadonlySet<string>,
    selectedEdgeIds: ReadonlySet<string> = new Set(),
    nodeChanges?: NodeChange[],
  ): SharedNodeDeletionResult => {
    if (nodeIds.size === 0) return "not-applicable"
    try {
      const current = state()
      if (!current) return "not-applicable"
      const viewNodeIds = new Set(current.viewNodes.map((node) => node.id))
      if (![...nodeIds].some((id) => viewNodeIds.has(id))) return "not-applicable"
      const reconciled = reconcileSubmodelBoundaryState({
        ...current,
        viewNodes: nodeChanges
          ? applyNodeChanges(nodeChanges, current.viewNodes)
          : current.viewNodes.filter((node) => !nodeIds.has(node.id)),
        viewEdges: current.viewEdges.filter((edge) =>
          !nodeIds.has(edge.source)
          && !nodeIds.has(edge.target)
          && !selectedEdgeIds.has(edge.id)),
      })
      if (!reconciled) throw new Error("The shared submodel boundary could not be reconciled")
      commitWithParentIdentities(reconciled)
      return "committed"
    } catch (error: unknown) {
      reportBoundaryError(error)
      return "blocked"
    }
  }, [state, commitWithParentIdentities, reportBoundaryError])

  return {
    commitBoundaryConnection,
    deleteBoundaryEdge,
    onBoundaryEdgesChange,
    commitSharedNodeDeletion,
    reconcileActiveSubmodel,
  }
}
