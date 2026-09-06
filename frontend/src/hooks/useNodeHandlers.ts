/**
 * Node CRUD handlers extracted from App.tsx FlowEditor.
 *
 * Centralises add, delete, duplicate, rename, create-instance, and
 * auto-layout operations so the main component stays focused on
 * orchestration and rendering.
 */
import { useCallback, useRef, useState, type MutableRefObject } from "react"
import type { Node, Edge, NodeChange } from "@xyflow/react"
import useToastStore from "../stores/useToastStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useUIStore from "../stores/useUIStore"
import {
  isProtectedSubmodelNodeData,
  isSubmodelInstanceConfig,
  nodeData,
} from "../types/node"
import {
  NODE_TYPES,
  NODE_TYPE_META,
  isSingletonType,
  singletonTypesInSubmodelDefinition,
} from "../utils/nodeTypes"
import { getLayoutedElements } from "../utils/layout"
import type { PreviewData } from "../panels/DataPreview"
import type { SharedNodeDeletionResult } from "./useSubmodelBoundaryEditing"

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
  setLastSelectedId?: (id: string | null) => void
  setPreviewData: (updater: React.SetStateAction<PreviewData | null>) => void
  fitView: (opts?: { padding?: number }) => void
  submodels: Record<string, unknown>
  resolveNodeIdentities: (nodes: readonly Node[]) => Promise<Node[]>
  commitSharedNodeDeletion?: (
    nodeIds: ReadonlySet<string>,
    selectedEdgeIds?: ReadonlySet<string>,
    nodeChanges?: NodeChange[],
    onSettled?: (committed: boolean) => void,
  ) => SharedNodeDeletionResult
}
// Strips one trailing copy-number so cloning "scoring_10" counts from
// "scoring": _2–_9 plus any multi-digit suffix (_10, _11, …). A bare "_1"
// is not a copy number — the base itself is the first occurrence.
const SUBMODEL_ALIAS_SUFFIX = /^(.*)_([2-9]|[1-9]\d+)$/

function nextSubmodelAlias(alias: string, aliases: Set<string>): string {
  const match = SUBMODEL_ALIAS_SUFFIX.exec(alias)
  const base = match?.[1] || alias
  if (!aliases.has(base)) return base
  let suffix = 2
  while (aliases.has(`${base}_${suffix}`)) {
    suffix += 1
  }
  return `${base}_${suffix}`
}


export default function useNodeHandlers({
  graphRef,
  nodeIdCounter: nodeIdCounterRef,
  lastSelectedNodeRef,
  setNodes,
  setNodesAndEdges,
  setSelectedNode,
  setLastSelectedId,
  setPreviewData,
  fitView,
  submodels,
  resolveNodeIdentities,
  commitSharedNodeDeletion,
}: UseNodeHandlersParams) {
  const layoutInFlightRef = useRef(false)
  const creationRequestSerialRef = useRef(0)
  const [isAutoLayouting, setIsAutoLayouting] = useState(false)
  const addToast = useToastStore((s) => s.addToast)
  const clearNode = useNodeResultsStore((s) => s.clearNode)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)

  const handleDeleteNode = useCallback((id: string) => {
    const target = graphRef.current.nodes.find((node) => node.id === id)
    if (target && isProtectedSubmodelNodeData(nodeData(target))) {
      addToast("error", 'Use "Dissolve Submodel" to remove a submodel owner; instance copies delete directly')
      return
    }
    // Selection, preview and cached-result cleanup only runs once the node has
    // actually left the graph; a shared-boundary deletion may still be
    // resolving parent identities and can yet fail.
    const cleanupAfterRemoval = () => {
      setSelectedNode((prev) => (prev?.id === id ? null : prev))
      setPreviewData((prev) => (prev?.nodeId === id ? null : prev))
      // Defer cache cleanup by one task tick (Issue #32). If `clearNode(id)`
      // fires synchronously here, any downstream component reading the store
      // during the same render cycle (before React has committed the
      // setNodes update that removes the node from the graph) will see a
      // state where the node still exists in the graph but its cached result
      // has already been wiped — producing a flicker-crash.
      setTimeout(() => { clearNode(id) }, 0)
      if (lastSelectedNodeRef.current?.id === id) {
        lastSelectedNodeRef.current = null
        setLastSelectedId?.(null)
      }

      // Clear UI dialogs that reference the deleted node (Issues #8, #14)
      const uiState = useUIStore.getState()
      if (uiState.renameDialog?.nodeId === id) setRenameDialog(null)
      const subDlg = uiState.submodelDialog
      if (subDlg && subDlg.nodeIds.includes(id)) setSubmodelDialog(null)
    }

    let cleanedUp = false
    const sharedDeletion = commitSharedNodeDeletion?.(
      new Set([id]),
      undefined,
      undefined,
      (committed) => {
        if (!committed) return
        cleanedUp = true
        cleanupAfterRemoval()
      },
    )
    if (sharedDeletion === "blocked") return
    if (sharedDeletion === "pending") return

    // Node + its edges removed as ONE undo step. setNodes-then-setEdges would
    // push two snapshots, so a single delete would need two undos to reverse
    // (the undo-atomicity bug class).
    if (sharedDeletion !== "committed") {
      setNodesAndEdges(
        (nds) => nds.filter((node) => node.id !== id),
        (eds) => eds.filter((edge) => edge.source !== id && edge.target !== id),
      )
    }
    if (!cleanedUp) cleanupAfterRemoval()
  }, [graphRef, setNodesAndEdges, lastSelectedNodeRef, setSelectedNode, setLastSelectedId, setPreviewData, clearNode, setRenameDialog, setSubmodelDialog, addToast, commitSharedNodeDeletion])

  const handleDuplicateNode = useCallback(async (id: string): Promise<void> => {
    const { nodes: n } = graphRef.current
    const original = n.find((node) => node.id === id)
    if (!original) return
    if (nodeData(original).nodeType === NODE_TYPES.SUBMODEL) {
      addToast("error", "Use Create Instance to duplicate a reusable submodel")
      return
    }
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
    const capturedGraph = graphRef.current
    const requestSerial = ++creationRequestSerialRef.current
    try {
      const resolved = await resolveNodeIdentities([newNode])
      if (resolved.length !== 1 || resolved[0]?.id !== newNode.id) {
        throw new Error("identity resolver returned an invalid node")
      }
      if (creationRequestSerialRef.current !== requestSerial || graphRef.current !== capturedGraph) {
        addToast("error", "Node creation was not applied because the graph changed while identity resolution was running.")
        return
      }
      const resolvedNode = resolved[0]
      setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), resolvedNode])
      setSelectedNode(resolvedNode)
      setLastSelectedId?.(resolvedNode.id)
    } catch (err: unknown) {
      addToast("error", `Create node failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, nodeIdCounterRef, setNodes, setSelectedNode, setLastSelectedId, addToast, resolveNodeIdentities])

  const handleCreateInstance = useCallback(async (id: string): Promise<void> => {
    const { nodes: n } = graphRef.current
    const original = n.find((node) => node.id === id)
    if (!original) return
    const origData = nodeData(original)
    const origNodeType = origData.nodeType || NODE_TYPES.POLARS
    const isSubmodel = origNodeType === NODE_TYPES.SUBMODEL

    // Singleton types allow exactly one node per pipeline, and an instance is a
    // second node like any other. The guard lives here rather than in the
    // callers' enabled-state predicates so every entry point inherits it — the
    // paste path (useKeyboardShortcuts) and handleDuplicateNode enforce the same
    // invariant, and downstream assembly relies on it (conflicting live_switch
    // nodes raise during execution).
    if (isSingletonType(origNodeType)) {
      addToast(
        "info",
        `Cannot create instance of "${origData.label}": only one node of this type is allowed per pipeline`,
      )
      return
    }

    let instanceConfig: Record<string, unknown>
    const occupiedIdentities = new Set(n.map((node) => node.id))
    for (const node of n) {
      const nodeConfig = nodeData(node).config
      if (
        nodeData(node).nodeType === NODE_TYPES.SUBMODEL
        && typeof nodeConfig?.alias === "string"
      ) {
        occupiedIdentities.add(nodeConfig.alias)
      }
    }
    if (isSubmodel) {
      const config = origData.config
      if (!isSubmodelInstanceConfig(config)) {
        addToast("error", `Cannot create instance of "${origData.label}": missing submodel definition identity`)
        return
      }
      const containedSingletons = singletonTypesInSubmodelDefinition(
        config.definitionId,
        submodels,
      )
      if (containedSingletons.size > 0) {
        const names = [...containedSingletons]
          .map((nodeType) => NODE_TYPE_META[nodeType].name)
          .join(", ")
        addToast(
          "info",
          `Cannot create instance of "${origData.label}": its definition contains ${names}, and only one node of each singleton type is allowed per pipeline`,
        )
        return
      }
      const ownerId = config.instanceOf ?? original.id
      const owner = n.find((node) => node.id === ownerId)
      const ownerConfig = owner ? nodeData(owner).config : undefined
      if (
        !owner
        || nodeData(owner).nodeType !== NODE_TYPES.SUBMODEL
        || !isSubmodelInstanceConfig(ownerConfig)
        || ownerConfig.definitionId !== config.definitionId
        || ownerConfig.instanceOf !== undefined
      ) {
        addToast(
          "error",
          `Cannot create instance of "${origData.label}": its editable definition owner is invalid`,
        )
        return
      }
      const newAlias = nextSubmodelAlias(config.alias, occupiedIdentities)
      occupiedIdentities.add(newAlias)
      instanceConfig = {
        definitionId: config.definitionId,
        alias: newAlias,
        instanceOf: ownerId,
      }
    } else {
      // Point at the ORIGINAL, not at whatever was selected: instancing an
      // instance must produce a sibling, not a chain. `resolveInstanceOriginal`
      // (NodePanel) does no chain-walking, so a chained `instanceOf` would
      // resolve the "original" to another pointer with no content of its own.
      // This mirrors the `ownerId` flattening the submodel branch does above.
      const instanceOf = origData.config?.instanceOf
      let ownerId = id
      if (instanceOf !== undefined) {
        if (typeof instanceOf !== "string" || instanceOf.trim() === "") {
          addToast("error", `Cannot create instance of "${origData.label}": its original node identity is invalid`)
          return
        }
        const owner = n.find((node) => node.id === instanceOf)
        if (!owner) {
          addToast("error", `Cannot create instance of "${origData.label}": original node "${instanceOf}" does not exist`)
          return
        }
        const ownerData = nodeData(owner)
        if (ownerData.nodeType !== origNodeType) {
          addToast("error", `Cannot create instance of "${origData.label}": original node "${instanceOf}" has a different node type`)
          return
        }
        if (ownerData.config?.instanceOf !== undefined) {
          addToast("error", `Cannot create instance of "${origData.label}": original node "${instanceOf}" is itself an instance`)
          return
        }
        ownerId = instanceOf
      }
      instanceConfig = { instanceOf: ownerId }
    }

    let newId: string
    if (isSubmodel) {
      newId = String(instanceConfig.alias)
    } else {
      do {
        nodeIdCounterRef.current += 1
        newId = `${origNodeType}_${nodeIdCounterRef.current}`
      } while (occupiedIdentities.has(newId))
    }
    const newNode: Node = {
      id: newId,
      type: original.type,
      position: { x: original.position.x + 60, y: original.position.y + 80 },
      selected: true,
      data: {
        label: isSubmodel ? String(instanceConfig.alias) : `${origData.label} instance`,
        description: `Instance of ${origData.label}`,
        nodeType: origNodeType,
        config: instanceConfig,
      },
    }
    const capturedGraph = graphRef.current
    const requestSerial = ++creationRequestSerialRef.current
    try {
      const resolved = await resolveNodeIdentities([newNode])
      if (resolved.length !== 1 || resolved[0]?.id !== newNode.id) {
        throw new Error("identity resolver returned an invalid node")
      }
      if (creationRequestSerialRef.current !== requestSerial || graphRef.current !== capturedGraph) {
        addToast("error", "Node creation was not applied because the graph changed while identity resolution was running.")
        return
      }
      const resolvedNode = resolved[0]
      setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), resolvedNode])
      setSelectedNode(resolvedNode)
      setLastSelectedId?.(resolvedNode.id)
      addToast("info", `Created instance of "${origData.label}"`)
    } catch (err: unknown) {
      addToast("error", `Create node failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }, [graphRef, nodeIdCounterRef, setNodes, setSelectedNode, setLastSelectedId, addToast, submodels, resolveNodeIdentities])

  const handleRenameNode = useCallback((id: string) => {
    const { nodes: n } = graphRef.current
    const node = n.find((nd) => nd.id === id)
    if (!node) return
    setRenameDialog({ nodeId: id, currentLabel: nodeData(node).label })
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
