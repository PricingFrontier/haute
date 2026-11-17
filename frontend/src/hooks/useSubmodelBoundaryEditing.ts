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
  removeSubmodelBoundaryEdges,
  removeSubmodelElements,
  removeSubmodelInputPort,
  type SubmodelBoundaryEditResult,
  type SubmodelBoundaryEditState,
} from "../utils/submodelBoundaryEditing"
import useGraphStore, { canvasGraph } from "../stores/useGraphStore"
import useToastStore from "../stores/useToastStore"
import { resolveEditorGraphIdentities } from "../utils/editorIdentities"
import { reconcileGraphInputBindings } from "../utils/nodeUpdatePlan"

type Setter = (nodes: Node[] | ((nodes: Node[]) => Node[]), edges: Edge[] | ((edges: Edge[]) => Edge[]), submodels: Record<string, unknown>) => void

/**
 * The boundary actions of an open submodel: connecting to or from its Input and
 * Output cards, removing a boundary edge or an input, and deleting a child a
 * port uses. Each reads the document from the graph store and commits through
 * `commitSubmodelBoundaryEdit`, one undo step.
 */
export interface UseSubmodelBoundaryEditingParams {
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

/** `nodes` placed where `placedNodes` has nodes of the same ids. */
function placedAs(nodes: Node[], placedNodes: readonly Node[]): Node[] {
  const positions = new Map(placedNodes.map((node) => [node.id, node.position]))
  return nodes.map((node) => {
    const position = positions.get(node.id)
    return position && (position.x !== node.position.x || position.y !== node.position.y)
      ? { ...node, position }
      : node
  })
}

/**
 * `result` with the layout the document has now. A boundary edit moves no node, so
 * one prepared before nodes were moved (while it waited for server names, or built
 * on such a candidate) keeps those moves: the canvas's presentation, the open
 * definition's children and the pipeline's own nodes keep where they are.
 */
function withCurrentLayout(result: SubmodelBoundaryEditResult): SubmodelBoundaryEditResult {
  const { nodes, focus, submodels } = useGraphStore.getState()
  const merged = mergeViewPresentation(result, nodes)
  const definition = merged.submodels[merged.definitionId]
  const current = submodels[merged.definitionId]
  const placedSubmodels = isSubmodelDefinition(definition, merged.definitionId)
    && isSubmodelDefinition(current, merged.definitionId)
    ? {
        ...merged.submodels,
        [merged.definitionId]: {
          ...definition,
          graph: { ...definition.graph, nodes: placedAs(definition.graph.nodes, current.graph.nodes) },
        },
      }
    : merged.submodels
  return {
    ...merged,
    parentNodes: focus ? placedAs(merged.parentNodes, focus.rootNodes) : merged.parentNodes,
    submodels: placedSubmodels,
  }
}

export default function useSubmodelBoundaryEditing({
  
  
  setNodesAndEdgesAndSubmodels,
  reservedApiInputFrameLabels,
  resolveGraphIdentities = resolveEditorGraphIdentities,
}: UseSubmodelBoundaryEditingParams) {
  const addToast = useToastStore((store) => store.addToast)
  const identityRequestSerialRef = useRef(0)
  // An edit waiting for server names, prepared at one semantic generation of
  // the document: it commits, and later boundary gestures build on it, only while
  // that generation is current.
  const pendingBoundaryCandidateRef = useRef<{
    result: SubmodelBoundaryEditResult
    generation: number
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
    const {
      nodes: currentNodes,
      edges: currentEdges,
      submodels: currentSubmodels,
      semanticGeneration,
    } = useGraphStore.getState()
    const pending = pendingBoundaryCandidateRef.current
    if (
      pending
      && pending.generation === semanticGeneration
      && pending.instanceId === focus.instanceId
      && pending.definitionId === focus.definitionId
    ) {
      return withCurrentLayout(pending.result)
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
    useGraphStore.getState().commitSubmodelBoundaryEdit({
      viewNodes: result.viewNodes,
      viewEdges: result.viewEdges,
      parentNodes: result.parentNodes,
      parentEdges: result.parentEdges,
      submodels: result.submodels,
    })
  }, [])
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
    const candidate = {
      result,
      generation: useGraphStore.getState().semanticGeneration,
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
      // A semantic edit, Undo, Redo, navigation or a reload made meanwhile, even one
      // back to identical content, voids the candidate; moving nodes does not.
      const { focus, semanticGeneration } = useGraphStore.getState()
      if (
        focus === null
        || semanticGeneration !== candidate.generation
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
      const previousParent = { nodes: focus.rootNodes, edges: focus.rootEdges, submodels: useGraphStore.getState().submodels }
      const rebound = reconcileGraphInputBindings(previousParent, {
        nodes: resolved.nodes, edges: resolved.edges, submodels: result.submodels,
      })
      if (!rebound.ok) throw new Error(rebound.error)
      commit(withCurrentLayout({
        ...result,
        parentNodes: rebound.nodes,
        parentEdges: rebound.edges,
      }))
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
          nodes: canvasGraph().nodes,
          edges: canvasGraph().edges as PipelineEdge[],
          submodels: useGraphStore.getState().submodels,
        }, connection)
        if (!created) return false
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
    
    reportBoundaryError,
    setNodesAndEdgesAndSubmodels,
    
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
      // One command for the whole batch: its boundary and internal edges, with the batch's
      // other changes (a selection) kept on the canvas.
      const removedIds = new Set(changes.flatMap((change) => (change.type === "remove" ? [change.id] : [])))
      const kept = applyEdgeChanges(changes.filter((change) => change.type !== "remove"), current.viewEdges)
      commitWithParentIdentities(removeSubmodelElements(
        current,
        { edgeIds: removedIds },
        { nodes: current.viewNodes, edges: kept as PipelineEdge[] },
      ))
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
      const removed = removeSubmodelElements(
        current,
        { nodeIds, edgeIds: selectedEdgeIds },
        {
          nodes: nodeChanges ? applyNodeChanges(nodeChanges, current.viewNodes) : current.viewNodes,
          edges: current.viewEdges,
        },
      )
      return commitWithParentIdentities(removed, onSettled) ? "committed" : "pending"
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
