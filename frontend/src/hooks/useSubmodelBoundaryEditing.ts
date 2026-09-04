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
  connectSubmodelInputFromParentConnection,
  reconcileSubmodelBoundaryState,
  removeSubmodelBoundaryEdges,
  removeSubmodelInputPort,
  type SubmodelBoundaryEditResult,
  type SubmodelBoundaryEditState,
} from "../utils/submodelBoundaryEditing"
import useToastStore from "../stores/useToastStore"
import { resolveEditorGraphIdentities } from "../utils/editorIdentities"
import { structuralFingerprint } from "../utils/structuralFingerprint"

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

export type SharedNodeDeletionResult = "not-applicable" | "committed" | "pending" | "blocked"

const isBoundaryNode = (node: Node | undefined) => node?.type === "submodelPort"
const hasBoundaryCard = (nodes: Node[], direction: "input" | "output") =>
  nodes.some(node => node.type === "submodelPort" && (node.data as Partial<SubmodelPortData>).portDirection === direction)
const isBoundaryEdge = (edge: Edge) => {
  const data = edge.data as SubmodelBoundaryEdgeData | undefined
  return data?.submodelBoundary?.direction === "input" || data?.submodelBoundary?.direction === "output"
}

/**
 * Carry the live canvas presentation onto a candidate's view nodes.
 *
 * A pending candidate is kept across selection and position changes made while
 * parent identities resolve; committing it must not revert those, so the
 * presentation-only fields are taken from the current view by node id.
 */
function mergeViewPresentation<T extends { viewNodes: Node[] }>(result: T, currentNodes: Node[]): T {
  const byId = new Map(currentNodes.map((node) => [node.id, node]))
  return {
    ...result,
    viewNodes: result.viewNodes.map((node) => {
      const live = byId.get(node.id)
      if (!live) return node
      return {
        ...node,
        position: live.position,
        selected: live.selected,
        dragging: live.dragging,
        measured: live.measured,
        width: live.width,
        height: live.height,
      }
    }),
  }
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
    if (typeof mapping !== "object" || mapping === null || Array.isArray(mapping)) return false
    const record = mapping as Record<string, unknown>
    const keys = Object.keys(record)
    return keys.length === expectedHandles.length
      && keys.every((handle) => expectedHandles.includes(handle))
      && expectedHandles.every((handle) => {
        const value = record[handle]
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
  const activeBoundaryIdentityRef = useRef({
    submodelName: activeSubmodelName,
    instanceId: activeSubmodelInstanceId,
    definitionId: activeSubmodelDefinitionId,
  })
  useEffect(() => {
    activeBoundaryIdentityRef.current = {
      submodelName: activeSubmodelName,
      instanceId: activeSubmodelInstanceId,
      definitionId: activeSubmodelDefinitionId,
    }
  }, [activeSubmodelName, activeSubmodelInstanceId, activeSubmodelDefinitionId])
  const pendingBoundaryCandidateRef = useRef<{
    result: SubmodelBoundaryEditResult
    expectedViewFingerprint: string
    expectedParentFingerprint: string
    expectedSubmodelsFingerprint: string
    submodelName: string
    instanceId: string
    definitionId: string
  } | null>(null)
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
    const pending = pendingBoundaryCandidateRef.current
    if (
      pending
      && pending.expectedViewFingerprint === structuralFingerprint(graphRef.current)
      && pending.expectedParentFingerprint === structuralFingerprint(parentGraphRef.current)
      && pending.expectedSubmodelsFingerprint
        === structuralFingerprint({ submodels: submodelsRef.current })
      && pending.submodelName === activeSubmodelName
      && pending.instanceId === activeSubmodelInstanceId
      && pending.definitionId === activeSubmodelDefinitionId
    ) {
      return mergeViewPresentation(pending.result, nodes)
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
    graphRef,
    submodelsRef,
  ])
  const commit = useCallback((result: SubmodelBoundaryEditResult) => {
    graphRef.current = { nodes: result.viewNodes, edges: result.viewEdges }
    parentGraphRef.current = { nodes: result.parentNodes, edges: result.parentEdges, submodels: result.submodels }
    submodelsRef.current = result.submodels
    setNodesAndEdgesAndSubmodels(result.viewNodes, result.viewEdges, result.submodels)
  }, [graphRef, parentGraphRef, submodelsRef, setNodesAndEdgesAndSubmodels])
  const commitWithParentIdentities = useCallback((
    result: SubmodelBoundaryEditResult,
    onSettled?: (committed: boolean) => void,
  ): boolean => {
    const serial = ++identityRequestSerialRef.current
    if (parentOccurrenceHandlesAreResolved(result)) {
      pendingBoundaryCandidateRef.current = null
      commit(result)
      onSettled?.(true)
      return true
    }
    const expectedViewFingerprint = structuralFingerprint(graphRef.current)
    const expectedParentFingerprint = structuralFingerprint(parentGraphRef.current)
    const expectedSubmodelsFingerprint = structuralFingerprint({ submodels: submodelsRef.current })
    const candidate = {
      result,
      expectedViewFingerprint,
      expectedParentFingerprint,
      expectedSubmodelsFingerprint,
      submodelName: result.submodelName,
      instanceId: result.instanceId,
      definitionId: result.definitionId,
    }
    pendingBoundaryCandidateRef.current = candidate
    void resolveGraphIdentities({
      nodes: result.parentNodes,
      edges: result.parentEdges,
      submodels: result.submodels,
      reservedApiInputFrameLabels,
    }).then((resolved) => {
      if (
        identityRequestSerialRef.current !== serial
      ) {
        onSettled?.(false)
        return
      }
      if (
        structuralFingerprint(graphRef.current) !== expectedViewFingerprint
        || structuralFingerprint(parentGraphRef.current) !== expectedParentFingerprint
        || structuralFingerprint({ submodels: submodelsRef.current })
          !== expectedSubmodelsFingerprint
        || activeBoundaryIdentityRef.current.submodelName !== candidate.submodelName
        || activeBoundaryIdentityRef.current.instanceId !== candidate.instanceId
        || activeBoundaryIdentityRef.current.definitionId !== candidate.definitionId
      ) {
        if (pendingBoundaryCandidateRef.current === candidate) {
          pendingBoundaryCandidateRef.current = null
        }
        reportBoundaryError(
          new Error("the workspace changed while parent identities were resolving"),
        )
        onSettled?.(false)
        return
      }
      if (pendingBoundaryCandidateRef.current === candidate) {
        pendingBoundaryCandidateRef.current = null
      }
      const merged = mergeViewPresentation(result, graphRef.current.nodes)
      commit({
        ...merged,
        parentNodes: resolved.nodes,
        parentEdges: resolved.edges,
      })
      onSettled?.(true)
    }).catch((error: unknown) => {
      if (identityRequestSerialRef.current !== serial) {
        onSettled?.(false)
        return
      }
      if (pendingBoundaryCandidateRef.current === candidate) {
        pendingBoundaryCandidateRef.current = null
      }
      reportBoundaryError(error)
      onSettled?.(false)
    })
    return false
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
    pendingBoundaryCandidateRef.current = null
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
      if (!current) {
        if (parentGraphRef.current) return false
        const created = connectSubmodelInputFromParentConnection({
          nodes: graphRef.current.nodes,
          edges: graphRef.current.edges as PipelineEdge[],
          submodels: submodelsRef.current,
        }, connection)
        if (!created) return false
        graphRef.current = { nodes: created.nodes, edges: created.edges }
        submodelsRef.current = created.submodels
        setNodesAndEdgesAndSubmodels(
          created.nodes,
          created.edges,
          created.submodels,
        )
        return true
      }
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
  }, [
    state,
    nodes,
    commitWithParentIdentities,
    graphRef,
    parentGraphRef,
    reportBoundaryError,
    setNodesAndEdgesAndSubmodels,
    submodelsRef,
  ])

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

  const deleteBoundaryInputPort = useCallback((portId: string): boolean => {
    try {
      const current = state()
      if (!current) return false
      const result = removeSubmodelInputPort(current, portId)
      if (!result) return false
      commitWithParentIdentities(result)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, commitWithParentIdentities, reportBoundaryError])

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
    onSettled?: (committed: boolean) => void,
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
      return commitWithParentIdentities(reconciled, onSettled) ? "committed" : "pending"
    } catch (error: unknown) {
      reportBoundaryError(error)
      return "blocked"
    }
  }, [state, commitWithParentIdentities, reportBoundaryError])

  return {
    commitBoundaryConnection,
    deleteBoundaryEdge,
    deleteBoundaryInputPort,
    onBoundaryEdgesChange,
    commitSharedNodeDeletion,
    reconcileActiveSubmodel,
  }
}
