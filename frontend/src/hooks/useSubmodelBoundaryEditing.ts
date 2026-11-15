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
import useGraphStore from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { resolveEditorGraphIdentities } from "../utils/editorIdentities"
import { structuralFingerprint } from "../utils/structuralFingerprint"
import { reconcileGraphInputBindings } from "../utils/nodeUpdatePlan"
import { captureParentOutputState } from "../utils/submodelViewGraph"

type GraphRef = React.MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
type Setter = (nodes: Node[] | ((nodes: Node[]) => Node[]), edges: Edge[] | ((edges: Edge[]) => Edge[]), submodels: Record<string, unknown>) => void

/**
 * The boundary actions of an open submodel: connecting to or from its Input and
 * Output cards, removing a boundary edge or an input, and deleting a child a
 * port uses. Each reads the document from the graph store and commits through
 * `commitSubmodelBoundaryEdit`, one undo step.
 */
export interface UseSubmodelBoundaryEditingParams {
  graphRef: GraphRef
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  setNodesAndEdgesAndSubmodels: Setter
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveGraphIdentities?: typeof resolveEditorGraphIdentities
}

export type SharedNodeDeletionResult = "not-applicable" | "committed" | "pending" | "blocked"

const isBoundaryNode = (node: Node | undefined) => node?.type === "submodelPort"
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
  const expectedHandles = definition.outputPorts.map((port) => `out__${port.name}`)
  return result.parentNodes.every((node) => {
    if (node.data.nodeType !== "submodel") return true
    const config = node.data.config
    if (!isSubmodelInstanceConfig(config) || config.definition_id !== result.definitionId) {
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

/** The open occurrence, as the store's focus names it. */
function focusedOccurrence() {
  const { focus } = useGraphStore.getState()
  if (focus === null) return null
  const occurrence = focus.rootNodes.find((node) => node.id === focus.instanceId)
  const config = occurrence?.data.config
  const name = isSubmodelInstanceConfig(config) ? config.alias : focus.instanceId
  return { focus, submodelName: name }
}

const canvasFingerprint = () => {
  const { nodes, edges } = useGraphStore.getState()
  return structuralFingerprint({ nodes, edges })
}
const rootFingerprint = () => {
  const { focus } = useGraphStore.getState()
  return structuralFingerprint(focus ? { nodes: focus.rootNodes, edges: focus.rootEdges } : null)
}
const submodelsFingerprint = () => structuralFingerprint({ submodels: useGraphStore.getState().submodels })

export default function useSubmodelBoundaryEditing({
  graphRef,
  submodelsRef,
  setNodesAndEdgesAndSubmodels,
  reservedApiInputFrameLabels,
  resolveGraphIdentities = resolveEditorGraphIdentities,
}: UseSubmodelBoundaryEditingParams) {
  const addToast = useToastStore((store) => store.addToast)
  const identityRequestSerialRef = useRef(0)
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
    const open = focusedOccurrence()
    if (open === null) return null
    const { focus, submodelName } = open
    const { nodes: currentNodes, edges: currentEdges, submodels: currentSubmodels } = useGraphStore.getState()
    const pending = pendingBoundaryCandidateRef.current
    if (
      pending
      && pending.expectedViewFingerprint === canvasFingerprint()
      && pending.expectedParentFingerprint === rootFingerprint()
      && pending.expectedSubmodelsFingerprint === submodelsFingerprint()
      && pending.instanceId === focus.instanceId
      && pending.definitionId === focus.definitionId
    ) {
      return mergeViewPresentation(pending.result, currentNodes)
    }
    return {
      submodelName,
      instanceId: focus.instanceId,
      definitionId: focus.definitionId,
      viewNodes: currentNodes,
      viewEdges: currentEdges as PipelineEdge[],
      parentNodes: focus.rootNodes,
      parentEdges: focus.rootEdges,
      submodels: currentSubmodels,
    }
  }, [])
  const commit = useCallback((result: SubmodelBoundaryEditResult) => {
    const outputState = captureParentOutputState(result.definitionId, result.parentNodes, result.parentEdges)
    const viewNodes = result.viewNodes.map((node) => (
      node.type === "submodelPort" && node.data.portDirection === "output"
        ? { ...node, data: { ...node.data, _parentOutputState: outputState } } : node
    ))
    useGraphStore.getState().commitSubmodelBoundaryEdit({
      viewNodes,
      viewEdges: result.viewEdges,
      parentNodes: result.parentNodes,
      parentEdges: result.parentEdges,
      submodels: result.submodels,
    })
    const { nodes, edges, submodels } = useGraphStore.getState()
    graphRef.current = { nodes, edges }
    submodelsRef.current = submodels
  }, [graphRef, submodelsRef])
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
    const expectedViewFingerprint = canvasFingerprint()
    const expectedParentFingerprint = rootFingerprint()
    const expectedSubmodelsFingerprint = submodelsFingerprint()
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
      const focus = useGraphStore.getState().focus
      if (
        focus === null
        || canvasFingerprint() !== expectedViewFingerprint
        || rootFingerprint() !== expectedParentFingerprint
        || submodelsFingerprint() !== expectedSubmodelsFingerprint
        || focus.instanceId !== candidate.instanceId
        || focus.definitionId !== candidate.definitionId
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
      const merged = mergeViewPresentation(result, useGraphStore.getState().nodes)
      const previousParent = { nodes: focus.rootNodes, edges: focus.rootEdges, submodels: useGraphStore.getState().submodels }
      const rebound = reconcileGraphInputBindings(previousParent, {
        nodes: resolved.nodes, edges: resolved.edges, submodels: result.submodels,
      })
      if (!rebound.ok) throw new Error(rebound.error)
      commit({
        ...merged,
        parentNodes: rebound.nodes,
        parentEdges: rebound.edges,
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
    reportBoundaryError,
    reservedApiInputFrameLabels,
    resolveGraphIdentities,
  ])
  useEffect(() => () => {
    identityRequestSerialRef.current += 1
    pendingBoundaryCandidateRef.current = null
  }, [])
  const commitBoundaryConnection = useCallback((connection: Connection): boolean => {
    try {
      const current = state()
      if (!current) {
        if (useGraphStore.getState().focus !== null) return false
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
      const source = current.viewNodes.find((node) => node.id === connection.source)
      const target = current.viewNodes.find((node) => node.id === connection.target)
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
    commitWithParentIdentities,
    graphRef,
    reportBoundaryError,
    setNodesAndEdgesAndSubmodels,
    submodelsRef,
  ])

  const deleteBoundaryEdge = useCallback((id: string): boolean => {
    try {
      const current = state()
      if (!current) return false
      const edge = current.viewEdges.find((candidate) => candidate.id === id)
      if (!edge || !isBoundaryEdge(edge)) return false
      const result = removeSubmodelBoundaryEdges(current, [id])
      if (result) commitWithParentIdentities(result)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, commitWithParentIdentities, reportBoundaryError])

  const deleteBoundaryInputPort = useCallback((portName: string): boolean => {
    try {
      const current = state()
      if (!current) return false
      const result = removeSubmodelInputPort(current, portName)
      if (!result) return false
      commitWithParentIdentities(result)
      return true
    } catch (error: unknown) {
      reportBoundaryError(error)
      return true
    }
  }, [state, commitWithParentIdentities, reportBoundaryError])

  const onBoundaryEdgesChange = useCallback((changes: EdgeChange[]): boolean => {
    const currentEdges = useGraphStore.getState().edges
    const removedBoundaryIds = changes
      .filter((change): change is Extract<EdgeChange, { type: "remove" }> =>
        change.type === "remove")
      .filter((change) =>
        currentEdges.some((edge) => edge.id === change.id && isBoundaryEdge(edge)))
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
  }, [state, commitWithParentIdentities, reportBoundaryError])

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
  }
}
