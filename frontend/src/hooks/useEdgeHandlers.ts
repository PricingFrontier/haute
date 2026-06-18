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
  type Connection,
  type Node,
  type Edge,
  type OnConnect,
  type OnConnectEnd,
  type OnSelectionChangeFunc,
} from "@xyflow/react"
import { effectiveNodeType, nodeData } from "../types/node"
import { NODE_TYPES, NODE_TYPE_META, isSingletonType, type NodeTypeValue } from "../utils/nodeTypes"
import { isNodeExplodable } from "../peek/peekRegistry"
import { insertEdgeJoinNode, type EdgeJoinFailureReason, type EdgeJoinInsertResult } from "../utils/edgeJoinGraph"
import { appEdge, appNode, selectOnlyNode } from "../utils/flowElements"
import { edgeJoinCanonicalTargetHandle, edgeJoinRoleConfigKey } from "../utils/edgeJoinRoles"
import { normalizeDefaultTargetHandle } from "../utils/flowHandles"
import {
  inDeadGap,
  resolveBodyDrop,
  type InternalNodeGeometry,
} from "../utils/dropResolver"
import { zoomToBucket } from "../utils/zoomBuckets"
import useToastStore from "../stores/useToastStore"
import type { FetchPreviewOptions } from "./usePipelineAPI"
import type { PreviewData } from "../panels/DataPreview"

const OPTIMISER_CLICK_PREVIEW_DEBOUNCE_MS = 800
const NON_PREVIEWABLE_CLICK_TYPES = new Set<string>([
  NODE_TYPES.DATA_SINK,
  NODE_TYPES.OUTPUT,
  NODE_TYPES.SUBMODEL,
  NODE_TYPES.SUBMODEL_PORT,
])

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

function previewOptionsForClick(node: Node): FetchPreviewOptions | null {
  const nodeType = effectiveNodeType(node)
  if (nodeType === NODE_TYPES.OPTIMISER) {
    return {
      debounceMs: OPTIMISER_CLICK_PREVIEW_DEBOUNCE_MS,
    }
  }
  if (NON_PREVIEWABLE_CLICK_TYPES.has(nodeType)) return null
  return {}
}

function connectionEndPoint(event: MouseEvent | TouchEvent): { x: number; y: number } {
  if ("clientX" in event) return { x: event.clientX, y: event.clientY }
  const touch = event.touches[0] ?? event.changedTouches[0]
  if (!touch) {
    throw new Error("Connection end touch event did not include pointer coordinates")
  }
  return { x: touch.clientX, y: touch.clientY }
}

type ContextMenuData = {
  x: number
  y: number
  nodeId: string
  nodeLabel: string
  isSubmodel?: boolean
  isSingleton?: boolean
  isExplodable?: boolean
}

type UseEdgeHandlersParams = {
  selectedNode: Node | null
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  nodeIdCounter: MutableRefObject<number>
  lastSelectedNodeRef: MutableRefObject<Node | null>
  setNodes: (updater: (nds: Node[]) => Node[]) => void
  setEdges: (updater: (eds: Edge[]) => Edge[]) => void
  setNodesRaw: (updater: Node[] | ((nds: Node[]) => Node[])) => void
  setEdgesRaw: (updater: Edge[] | ((eds: Edge[]) => Edge[])) => void
  pushSnapshot: () => void
  setSelectedNode: (updater: React.SetStateAction<Node | null>) => void
  setPreviewData: (updater: React.SetStateAction<PreviewData | null>) => void
  setContextMenu: (data: ContextMenuData | null) => void
  fetchPreview: (node: Node, options?: FetchPreviewOptions) => void
  cancelPreview: () => void
  shouldSkipAutomaticPreview?: (node: Node) => boolean
  clearTrace: () => void
  screenToFlowPosition: (pos: { x: number; y: number }) => { x: number; y: number }
  graphRefreshingRef: MutableRefObject<number>
  findEdgeIdAtPoint?: (point: { x: number; y: number }) => string | null
  /** Topmost canvas node under a screen point (DOM hit-test, wired from App). */
  findNodeIdAtPoint: (point: { x: number; y: number }) => string | null
  /** React Flow internal-node accessor (geometry + handle bounds), wired from App. */
  getInternalNode: (id: string) => InternalNodeGeometry | undefined
  /** Current viewport zoom, wired from App (drives the dead-band bucket). */
  getZoom: () => number
}

const edgeJoinFailureMessages: Record<EdgeJoinFailureReason, string> = {
  "target-edge-not-found": "Edge join rejected: drop the connection on an existing edge",
  "source-node-not-found": "Edge join rejected: source node is no longer available",
  "target-edge-node-not-found": "Edge join rejected: target edge is missing an endpoint",
  "self-join": "Edge join rejected: choose a different dataframe to join",
  cycle: "Edge join rejected: that connection would create a cycle",
}

export default function useEdgeHandlers({
  selectedNode,
  graphRef,
  nodeIdCounter: nodeIdCounterRef,
  lastSelectedNodeRef,
  setNodes,
  setEdges,
  setNodesRaw,
  setEdgesRaw,
  pushSnapshot,
  setSelectedNode,
  setPreviewData,
  setContextMenu,
  fetchPreview,
  cancelPreview,
  shouldSkipAutomaticPreview,
  clearTrace,
  screenToFlowPosition,
  graphRefreshingRef,
  findEdgeIdAtPoint = () => null,
  findNodeIdAtPoint,
  getInternalNode,
  getZoom,
}: UseEdgeHandlersParams) {
  const addToast = useToastStore((s) => s.addToast)

  const commitConnection = useCallback(
    (params: Connection) => {
      const targetHandle = normalizeDefaultTargetHandle(params.targetHandle)
      if (!params.source || !params.target) return
      if (params.source === params.target) return
      const { edges: currentEdges, nodes: currentNodes } = graphRef.current
      const exists = currentEdges.some(
        (e) =>
          e.source === params.source &&
          e.target === params.target &&
          e.sourceHandle === (params.sourceHandle ?? null) &&
          e.targetHandle === targetHandle
      )
      if (exists) return
      const targetNode = currentNodes.find((node) => node.id === params.target)
      if (targetNode && nodeData(targetNode).nodeType === NODE_TYPES.EDGE_JOIN) {
        const roleTargetHandle = edgeJoinCanonicalTargetHandle(targetHandle)
        const roleConfigKey = edgeJoinRoleConfigKey(roleTargetHandle)
        if (!roleConfigKey) {
          addToast("error", "Edge join connections must target the base or join handle")
          return
        }
        const incoming = currentEdges.filter((edge) => edge.target === params.target)
        if (incoming.some((edge) => edge.targetHandle === roleTargetHandle)) {
          addToast("error", `Edge join already has a ${roleTargetHandle} input`)
          return
        }
        if (incoming.length >= 2) {
          addToast("error", "Edge join nodes accept exactly two inputs")
          return
        }
        const nextNodes = currentNodes.map((node) => {
          if (node.id !== params.target) return node
          return {
            ...node,
            data: {
              ...node.data,
              config: {
                ...(nodeData(node).config ?? {}),
                [roleConfigKey]: params.source,
              },
            },
          }
        })
        const nextEdges = [
          ...currentEdges,
          appEdge({
            source: params.source,
            target: params.target,
            sourceHandle: params.sourceHandle ?? null,
            targetHandle: roleTargetHandle,
          }),
        ]
        pushSnapshot()
        setNodesRaw(nextNodes)
        setEdgesRaw(nextEdges)
        return
      }
      if (wouldExceedMaxInputs(params.target, currentNodes, currentEdges)) return

      // For submodel nodes, keep the targetHandle so drill-in can route
      // edges to the correct internal port node.

      setEdges((eds) => [
        ...eds,
        appEdge({
          source: params.source,
          target: params.target,
          sourceHandle: params.sourceHandle ?? null,
          targetHandle,
        }),
      ])
    },
    [addToast, graphRef, pushSnapshot, setEdges, setEdgesRaw, setNodesRaw],
  )

  const onConnect: OnConnect = useCallback(() => {
    // ConnectionMode.Loose can report output-to-output drags as valid
    // connections. Commit only in onConnectEnd, where handle directions are
    // available and can be interpreted into either a normal edge or edge-join.
  }, [])

  const onConnectEnd: OnConnectEnd = useCallback(
    (event, connectionState) => {
      const sourceNodeId = connectionState.fromNode?.id
      if (!sourceNodeId) return
      const targetNodeId = connectionState.toNode?.id ?? null
      const fromHandle = connectionState.fromHandle
      const toHandle = connectionState.toHandle

      const idFactory = () => {
        nodeIdCounterRef.current += 1
        return `${NODE_TYPES.EDGE_JOIN}_${nodeIdCounterRef.current}`
      }
      const commitEdgeJoinResult = (result: EdgeJoinInsertResult) => {
        if (!result.ok) {
          addToast("error", edgeJoinFailureMessages[result.reason])
          return
        }

        const selected = result.nodes.find((node) => node.id === result.newNodeId) ?? null
        pushSnapshot()
        setNodesRaw(result.nodes)
        setEdgesRaw(result.edges)
        setSelectedNode(selected)
        lastSelectedNodeRef.current = selected
        clearTrace()
        cancelPreview()
      }

      const screenPoint = connectionEndPoint(event)

      // (Rolled back) The output-onto-output edge-join gesture used to live
      // here as "Arm 1": an exact-connector drop of one output onto another
      // inserted an unconnected edgeJoin node. It was removed because it
      // created a join with nothing to join to. An output→output drop now
      // falls through to the body-drop arm and lands in the target node's
      // output-end dead band (Arm 3's `inDeadGap`) → a silent no-op, which
      // is the intended rollback behaviour.

      // Arm 2 — snapped complementary connectors (shipped). Same-polarity
      // or invalid snaps no longer swallow the gesture: they fall through
      // to the body-drop arm / exposed-edge splice.
      if (targetNodeId && connectionState.isValid) {
        if (fromHandle?.type === "source" && toHandle?.type === "target") {
          commitConnection({
            source: sourceNodeId,
            sourceHandle: fromHandle.id ?? null,
            target: targetNodeId,
            targetHandle: normalizeDefaultTargetHandle(toHandle.id),
          })
          return
        }
        if (fromHandle?.type === "target" && toHandle?.type === "source") {
          commitConnection({
            source: targetNodeId,
            sourceHandle: toHandle.id ?? null,
            target: sourceNodeId,
            targetHandle: normalizeDefaultTargetHandle(fromHandle.id),
          })
          return
        }
      }

      // Arm 3 — whole-node body drop (both drag directions; sits before
      // the backward-drag early return so backward drags get body drops
      // too). The node under the pointer always wins over any edge it
      // covers (ruling 1): every exit from this branch is a return.
      if (fromHandle?.type === "source" || fromHandle?.type === "target") {
        const bodyNodeId = findNodeIdAtPoint(screenPoint)
        if (bodyNodeId) {
          const bodyNode = getInternalNode(bodyNodeId)
          if (!bodyNode) return
          const flowPoint = screenToFlowPosition(screenPoint)
          // Dead band at the non-complementary end (ruling 3): a near-miss
          // around the output connector must never silently become a body
          // connect — to join with that output, drop on its exposed edge.
          if (inDeadGap(flowPoint, bodyNode, fromHandle.type, zoomToBucket(getZoom()))) return
          const wantedKind = fromHandle.type === "source" ? "target" : "source"
          const resolved = resolveBodyDrop(bodyNode, wantedKind, flowPoint)
          // No complementary connector: the node still wins (silent no-op,
          // no fall-through to a hidden edge underneath).
          if (!resolved) return
          if (fromHandle.type === "source") {
            commitConnection({
              source: sourceNodeId,
              sourceHandle: fromHandle.id ?? null,
              target: bodyNodeId,
              targetHandle: normalizeDefaultTargetHandle(resolved.handleId),
            })
          } else {
            commitConnection({
              source: bodyNodeId,
              sourceHandle: resolved.handleId,
              target: sourceNodeId,
              targetHandle: normalizeDefaultTargetHandle(fromHandle.id),
            })
          }
          return
        }
      }

      // Arm 4 — exposed-edge splice (shipped; forward drags only). Only
      // reachable when no node body is under the pointer.
      if (fromHandle?.type !== "source") return
      const targetEdgeId = findEdgeIdAtPoint(screenPoint)
      if (!targetEdgeId) return

      commitEdgeJoinResult(insertEdgeJoinNode({
        nodes: graphRef.current.nodes,
        edges: graphRef.current.edges,
        targetEdgeId,
        connection: {
          source: sourceNodeId,
          sourceHandle: connectionState.fromHandle?.id ?? null,
        },
        position: screenToFlowPosition(screenPoint),
        idFactory,
      }))
    },
    [
      addToast,
      cancelPreview,
      clearTrace,
      commitConnection,
      findEdgeIdAtPoint,
      findNodeIdAtPoint,
      getInternalNode,
      getZoom,
      graphRef,
      lastSelectedNodeRef,
      nodeIdCounterRef,
      pushSnapshot,
      screenToFlowPosition,
      setEdgesRaw,
      setNodesRaw,
      setSelectedNode,
    ],
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
    const previousNodeId = selectedNode?.id ?? lastSelectedNodeRef.current?.id
    const shouldRefreshPreview = previousNodeId !== node.id
    setSelectedNode(node)
    lastSelectedNodeRef.current = node
    if (!shouldRefreshPreview) return

    clearTrace()
    cancelPreview()
    if (shouldSkipAutomaticPreview?.(node)) return
    const previewOptions = previewOptionsForClick(node)
    if (!previewOptions) {
      setPreviewData(null)
      return
    }
    fetchPreview(node, previewOptions)
  }, [
    selectedNode,
    setSelectedNode,
    fetchPreview,
    cancelPreview,
    shouldSkipAutomaticPreview,
    clearTrace,
    setPreviewData,
    lastSelectedNodeRef,
  ])

  const handleDeleteEdge = useCallback((edgeId: string) => {
    setEdges((eds) => eds.filter((e) => e.id !== edgeId))
  }, [setEdges])

  // Set (alias) or clear (alias === null) a connection's input binding name.
  // Goes through setEdges — same path as delete — so it records an undo
  // snapshot and re-triggers codegen (inputAlias is part of the edge
  // structural fingerprint). Dropping the field when cleared keeps edges
  // clean and round-trips to "no alias" on the backend.
  const handleSetInputAlias = useCallback(
    (edgeId: string, alias: string | null) => {
      setEdges((eds) =>
        eds.map((e) => {
          if (e.id !== edgeId) return e
          const { inputAlias: _drop, ...rest } = e as typeof e & { inputAlias?: string | null }
          return alias ? { ...rest, inputAlias: alias } : rest
        }),
      )
    },
    [setEdges],
  )

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
      isExplodable: isNodeExplodable(node),
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

      // Parse the drag-config JSON. Malformed payloads must fail loudly
      // (Issue #35) — silently swallowing the error would create a node
      // with an empty config, which violates downstream node-type
      // invariants and is invisible to the user.
      const rawConfig = event.dataTransfer.getData("application/reactflow-config") || "{}"
      let config: Record<string, unknown>
      try {
        const parsed = JSON.parse(rawConfig)
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          addToast("error", "Drop rejected: node config must be a JSON object")
          return
        }
        config = parsed as Record<string, unknown>
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        addToast("error", `Drop rejected: invalid node config JSON (${message})`)
        return
      }

      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      nodeIdCounterRef.current += 1
      const id = `${type}_${nodeIdCounterRef.current}`

      const newNode = appNode({
        id,
        type: type as NodeTypeValue,
        position,
        config,
      })

      const selectedNewNode = { ...newNode, selected: true }
      setNodes((nds) => selectOnlyNode([...nds, newNode], newNode.id))
      setSelectedNode(selectedNewNode)
    },
    [screenToFlowPosition, nodeIdCounterRef, setNodes, setSelectedNode, addToast],
  )

  return {
    onConnect,
    onConnectEnd,
    onSelectionChange,
    onNodeClick,
    handleDeleteEdge,
    handleSetInputAlias,
    onNodeContextMenu,
    onDragOver,
    onDrop,
  }
}
