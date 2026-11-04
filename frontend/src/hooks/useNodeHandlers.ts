/**
 * Node CRUD handlers extracted from App.tsx FlowEditor.
 *
 * Centralises add, delete, duplicate, rename, create-instance, and
 * auto-layout operations so the main component stays focused on
 * orchestration and rendering.
 */
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react"
import type { Node, Edge, NodeChange, InternalNode } from "@xyflow/react"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
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
  WRAPPED_INSTANCE_TYPES,
  isSingletonType,
  singletonTypesInSubmodelDefinition,
} from "../utils/nodeTypes"
import { getLayoutedElements } from "../utils/layout"
import type { DrilledOccurrenceIdentity } from "../utils/submodelRuntimeTarget"
import type { PreviewData } from "../panels/DataPreview"
import type { SharedNodeDeletionResult } from "./useSubmodelBoundaryEditing"
import type { SubmodelCreateResult } from "./useSubmodelNavigation"
import { authoredNodeData, createAfterIdentity } from "./createAfterIdentity"

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
  getInternalNode?: (id: string) => InternalNode | undefined
  submodels: Record<string, unknown>
  activeSubmodelIdentity?: DrilledOccurrenceIdentity | null
  resolveNodeIdentities: (nodes: readonly Node[]) => Promise<Node[]>
  createWrappedNodeInstance?: (nodeId: string) => Promise<SubmodelCreateResult>
  commitSharedNodeDeletion?: (
    nodeIds: ReadonlySet<string>,
    selectedEdgeIds?: ReadonlySet<string>,
    nodeChanges?: NodeChange[],
    onSettled?: (committed: boolean) => void,
  ) => SharedNodeDeletionResult
}

function layoutDocumentKey(
  activeSubmodelIdentity: DrilledOccurrenceIdentity | null | undefined,
): string {
  const status = useDocumentStatusStore.getState()
  return JSON.stringify([
    status.executionGeneration,
    status.sourceFile,
    activeSubmodelIdentity?.instanceId ?? null,
    activeSubmodelIdentity?.definitionId ?? null,
  ])
}

function layoutTopologyKey(nodes: readonly Node[], edges: readonly Edge[]): string {
  const nodeKeys = nodes
    .map((node) => JSON.stringify([node.id, node.origin ?? null]))
    .sort()
  const edgeKeys = edges
    .map((edge) =>
      JSON.stringify([
        edge.id,
        edge.source,
        edge.sourceHandle ?? null,
        edge.target,
        edge.targetHandle ?? null,
      ]),
    )
    .sort()
  return JSON.stringify([nodeKeys, edgeKeys])
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
  getInternalNode,
  submodels,
  activeSubmodelIdentity,
  resolveNodeIdentities,
  createWrappedNodeInstance,
  commitSharedNodeDeletion,
}: UseNodeHandlersParams) {
  const layoutInFlightRef = useRef(false)
  const creationRequestSerialRef = useRef(0)
  /** Resolve one new node's identities; anything but that one node breaks the resolver's contract. */
  const resolveSingleNode = useCallback(async (node: Node): Promise<Node> => {
    const resolved = await resolveNodeIdentities([node])
    if (resolved.length !== 1 || resolved[0]?.id !== node.id) {
      throw new Error("identity resolver returned an invalid node")
    }
    return resolved[0]
  }, [resolveNodeIdentities])
  const activeSubmodelIdentityRef = useRef(activeSubmodelIdentity)
  useEffect(() => {
    activeSubmodelIdentityRef.current = activeSubmodelIdentity
  }, [activeSubmodelIdentity])
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
      // The node has left the graph by now, and React renders both store
      // updates of this task together, so no render shows the node without
      // its cached result — the same order the keyboard delete uses.
      clearNode(id)
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
      // Another node's recovery identity, column stash or editor identities are not the copy's.
      data: { ...authoredNodeData(original.data), label: `${original.data.label} copy` },
    }
    await createAfterIdentity({
      graphRef,
      serialRef: creationRequestSerialRef,
      resolve: () => resolveSingleNode(newNode),
      apply: (resolvedNode) => {
        setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), resolvedNode])
        setSelectedNode(resolvedNode)
        setLastSelectedId?.(resolvedNode.id)
      },
      addToast,
      action: "Node creation",
      failure: "Create node failed",
    })
  }, [graphRef, nodeIdCounterRef, setNodes, setSelectedNode, setLastSelectedId, addToast, resolveSingleNode])

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
    // invariant.
    if (isSingletonType(origNodeType)) {
      addToast(
        "info",
        `Cannot create instance of "${origData.label}": only one node of this type is allowed per pipeline`,
      )
      return
    }
    // A Source Switch routes by its own input names, which an instance's
    // inputs do not share; a second Source Switch is the supported layout.
    if (origNodeType === NODE_TYPES.LIVE_SWITCH) {
      addToast("info", `Cannot create instance of "${origData.label}": add another Source Switch instead`)
      return
    }

    if (WRAPPED_INSTANCE_TYPES.has(origNodeType)) {
      if (!createWrappedNodeInstance) {
        throw new Error("Create Instance requires the parent document transform handler")
      }
      const result = await createWrappedNodeInstance(id)
      if (!result.ok) addToast("error", result.error)
      return
    }

    if (!isSubmodel) {
      addToast("info", origNodeType === NODE_TYPES.DATA_OUTPUT
        ? "Data Output cannot be reused."
        : `Cannot create instance of "${origData.label}": this node type cannot be reused.`)
      return
    }

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
    const config = origData.config
    if (!isSubmodelInstanceConfig(config)) {
      addToast("error", `Cannot create instance of "${origData.label}": missing submodel definition identity`)
      return
    }
    const containedSingletons = singletonTypesInSubmodelDefinition(
      config.definition_id,
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
    const ownerId = config.instance_of ?? original.id
    const owner = n.find((node) => node.id === ownerId)
    const ownerConfig = owner ? nodeData(owner).config : undefined
    if (
      !owner
      || nodeData(owner).nodeType !== NODE_TYPES.SUBMODEL
      || !isSubmodelInstanceConfig(ownerConfig)
      || ownerConfig.definition_id !== config.definition_id
      || ownerConfig.instance_of !== undefined
    ) {
      addToast(
        "error",
        `Cannot create instance of "${origData.label}": its editable definition owner is invalid`,
      )
      return
    }
    const newAlias = nextSubmodelAlias(config.alias, occupiedIdentities)
    occupiedIdentities.add(newAlias)
    const instanceConfig = {
      definition_id: config.definition_id,
      alias: newAlias,
      instance_of: ownerId,
    }

    const newId = String(instanceConfig.alias)
    const newNode: Node = {
      id: newId,
      type: original.type,
      position: { x: original.position.x + 60, y: original.position.y + 80 },
      selected: true,
      data: {
        label: String(instanceConfig.alias),
        description: `Instance of ${origData.label}`,
        nodeType: origNodeType,
        config: instanceConfig,
      },
    }
    await createAfterIdentity({
      graphRef,
      serialRef: creationRequestSerialRef,
      resolve: () => resolveSingleNode(newNode),
      apply: (resolvedNode) => {
        setNodes((nds) => [...nds.map((nd) => ({ ...nd, selected: false })), resolvedNode])
        setSelectedNode(resolvedNode)
        setLastSelectedId?.(resolvedNode.id)
        addToast("info", `Created instance of "${origData.label}"`)
      },
      addToast,
      action: "Node creation",
      failure: "Create node failed",
    })
  }, [graphRef, setNodes, setSelectedNode, setLastSelectedId, addToast, submodels, resolveSingleNode, createWrappedNodeInstance])

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
    const capturedDocumentKey = layoutDocumentKey(activeSubmodelIdentityRef.current)
    const capturedTopologyKey = layoutTopologyKey(n, e)
    layoutInFlightRef.current = true
    setIsAutoLayouting(true)
    try {
      const layouted = await getLayoutedElements(n, e, getInternalNode)
      const positionsById = new Map<string, { x: number; y: number }>()
      for (const layoutedNode of layouted) {
        if (
          typeof layoutedNode.position?.x === "number"
          && Number.isFinite(layoutedNode.position.x)
          && typeof layoutedNode.position?.y === "number"
          && Number.isFinite(layoutedNode.position.y)
        ) {
          positionsById.set(layoutedNode.id, {
            x: layoutedNode.position.x,
            y: layoutedNode.position.y,
          })
        }
      }
      const current = graphRef.current
      const sameDocument =
        layoutDocumentKey(activeSubmodelIdentityRef.current) === capturedDocumentKey
      const sameTopology =
        layoutTopologyKey(current.nodes, current.edges) === capturedTopologyKey
      const completePositions =
        positionsById.size === current.nodes.length
        && current.nodes.every((node) => positionsById.has(node.id))
      if (!sameDocument || !sameTopology || !completePositions) {
        addToast(
          "info",
          "Auto-layout was not applied because the graph changed while layout was running.",
        )
        return
      }
      setNodes((nds) =>
        nds.map((node) => {
          const position = positionsById.get(node.id)
          return position ? { ...node, position } : node
        }),
      )
      setTimeout(() => fitView({ padding: 0.15 }), 50)
      addToast("info", "Auto-layout applied")
    } finally {
      layoutInFlightRef.current = false
      setIsAutoLayouting(false)
    }
  }, [graphRef, setNodes, fitView, getInternalNode, addToast])

  return {
    handleDeleteNode,
    handleDuplicateNode,
    handleCreateInstance,
    handleRenameNode,
    handleAutoLayout,
    isAutoLayouting,
  }
}
