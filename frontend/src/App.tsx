import { useEffect, useCallback, useState, useRef } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useReactFlow,
  useStore,
  SelectionMode,
  ConnectionMode,
  type Node,
  type Edge,
  type Connection,
  type OnConnectStart,
  type OnConnectEnd,
  BackgroundVariant,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { nodeTypes } from "./nodes/nodeTypeRegistry"
import NodePalette from "./panels/NodePalette"
import NodePanel from "./panels/NodePanel"
import { GraphProvider } from "./panels/GraphContext"
import DataPreview from "./panels/DataPreview"
import ExplorePreview from "./panels/ExplorePreview"
import OptimiserPreview from "./panels/OptimiserPreview"
import OptimiserDataPreview from "./panels/OptimiserDataPreview"
import { ModellingPreview } from "./panels/ModellingPreview"

import TracePanel from "./panels/TracePanel"
import ToastContainer from "./components/Toast"
import { ErrorBoundary } from "./components/ErrorBoundary"
import ContextMenu from "./components/ContextMenu"
import SelectionContextMenu from "./components/SelectionContextMenu"
import KeyboardShortcuts from "./components/KeyboardShortcuts"
import BreadcrumbBar from "./components/BreadcrumbBar"
import Toolbar from "./components/Toolbar"
import SubmodelDialog from "./components/SubmodelDialog"
import RenameDialog from "./components/RenameDialog"
import BackgroundJobPolling from "./components/BackgroundJobPolling"
import UtilityPanel from "./panels/UtilityPanel"
import ImportsPanel from "./panels/ImportsPanel"
import GitPanel from "./panels/GitPanel"
import NodeSearch from "./components/NodeSearch"
import NodePeek from "./peek/NodePeek"

import useGraphCanvasState from "./hooks/useGraphCanvasState"
import useWebSocketSync from "./hooks/useWebSocketSync"
import usePipelineAPI from "./hooks/usePipelineAPI"
import useTracing from "./hooks/useTracing"
import useSubmodelNavigation from "./hooks/useSubmodelNavigation"
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts"
import useNodeHandlers from "./hooks/useNodeHandlers"
import useEdgeHandlers from "./hooks/useEdgeHandlers"
import usePanelGraphContext from "./hooks/usePanelGraphContext"
import useCanvasPan, { type CanvasContextHit } from "./canvas/useCanvasPan"
import useSettingsStore from "./stores/useSettingsStore"
import useUIStore from "./stores/useUIStore"
import useGraphStore from "./stores/useGraphStore"
import useNodeResultsStore from "./stores/useNodeResultsStore"
import useToastStore from "./stores/useToastStore"
import { HAUTE_SESSION_EXPIRED_EVENT } from "./api/client"

import { NODE_TYPES } from "./utils/nodeTypes"
import { previewForActiveNode } from "./utils/activePreview"
import { swapEdgeJoinInputs, type EdgeJoinSwapInputsFailureReason } from "./utils/edgeJoinGraph"
import { isPipelineConnectionValid } from "./utils/connectionValidation"
import { applyApiInputConfigChange } from "./utils/apiInputPorts"
import { shouldUseLiteGraphEffects } from "./utils/graphPerformance"
import { topmostNodeAtPoint } from "./utils/dropResolver"
import { CONNECTION_RADIUS_BY_BUCKET, zoomSelector } from "./utils/zoomBuckets"
import { nodeData } from "./types/node"
import { PanelLeftOpen } from "lucide-react"

// ---------------------------------------------------------------------------
// Module-level constants (no dynamic values â€” avoids re-creating each render)
// ---------------------------------------------------------------------------

const defaultEdgeOptions = {
  type: "default" as const,
  animated: false,
  style: { stroke: 'rgba(255,255,255,.25)', strokeWidth: 1.5 },
}

const connectionLineStyle = { stroke: 'var(--accent)', strokeWidth: 2, strokeDasharray: '6 3' }

const fitViewOptions = { padding: 0.15 }

const proOptions = { hideAttribution: true }

const edgeJoinSwapFailureMessages: Record<EdgeJoinSwapInputsFailureReason, string> = {
  "edge-join-node-not-found": "Edge join swap rejected: selected edge join is no longer available",
  "target-node-not-edge-join": "Edge join swap rejected: selected node is not an edge join",
  "base-input-not-found": "Edge join swap rejected: dominant input is not connected",
  "join-input-not-found": "Edge join swap rejected: joining input is not connected",
  "base-input-ambiguous": "Edge join swap rejected: dominant input has more than one connection",
  "join-input-ambiguous": "Edge join swap rejected: joining input has more than one connection",
}

// ---------------------------------------------------------------------------
// ReactFlow node type â†’ component registry
// ---------------------------------------------------------------------------

// nodeTypes registry now lives in ./nodes/nodeTypeRegistry, shared with the
// read-only wrapper Peek so it renders the exact same node cards as the canvas.

// ---------------------------------------------------------------------------
// FlowEditor â€” main orchestrator
// ---------------------------------------------------------------------------

function FlowEditor() {
  const graphRefreshingRef = useRef(0)

  // Core ReactFlow state with undo/redo
  const {
    nodes, edges,
    setNodes, setEdges,
    setNodesRaw, setEdgesRaw,
    onNodesChange, onEdgesChange,
    undo, redo, canUndo, canRedo, pushSnapshot,
  } = useGraphCanvasState([], [], graphRefreshingRef)
  const { screenToFlowPosition, fitView, zoomIn, zoomOut, getInternalNode, getZoom } = useReactFlow()
  // Zoom bucket for connection targeting (hot-zone CSS + connectionRadius);
  // bucketed selector so this only re-renders on threshold crossings.
  const zoomBucket = useStore(zoomSelector)
  // True while a connection drag is in progress — drives the `connecting`
  // class (drop-target feedback CSS) and the `data-connecting` test hook.
  const [connecting, setConnecting] = useState(false)
  // The handle TYPE the in-progress drag started from ("source" = an output,
  // "target" = an input), or null when no drag is active. Drives the
  // complementary-only connector lighting: dragging from an output lights
  // only inputs (.target handles), dragging from an input lights only
  // outputs (.source handles). Same-polarity connectors stay at rest.
  const [connectingFrom, setConnectingFrom] = useState<"source" | "target" | null>(null)

  // UI state from Zustand store (leaf-subscribed values live in their own components)
  // Settings store
  const fetchMlflow = useSettingsStore((s) => s.fetchMlflow)
  // UI store (chrome / layout)
  const paletteOpen = useUIStore((s) => s.paletteOpen)
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen)
  const utilityOpen = useUIStore((s) => s.utilityOpen)
  const setUtilityOpen = useUIStore((s) => s.setUtilityOpen)
  const importsOpen = useUIStore((s) => s.importsOpen)
  const setImportsOpen = useUIStore((s) => s.setImportsOpen)
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const shortcutsOpen = useUIStore((s) => s.shortcutsOpen)
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const submodelDialog = useUIStore((s) => s.submodelDialog)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)
  const renameDialog = useUIStore((s) => s.renameDialog)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  const syncBanner = useUIStore((s) => s.syncBanner)
  const setSyncBanner = useUIStore((s) => s.setSyncBanner)
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId)
  const setHoveredNodeId = useUIStore((s) => s.setHoveredNodeId)
  const nodeSearchOpen = useUIStore((s) => s.nodeSearchOpen)
  const setNodeSearchOpen = useUIStore((s) => s.setNodeSearchOpen)
  const peek = useUIStore((s) => s.peek)
  const setPeek = useUIStore((s) => s.setPeek)
  const addToast = useToastStore((s) => s.addToast)
  const [sessionExpired, setSessionExpired] = useState(false)

  // Fetch MLflow status once on startup (shared by all panels)
  useEffect(() => { fetchMlflow() }, [fetchMlflow])

  useEffect(() => {
    const handleSessionExpired = () => setSessionExpired(true)
    window.addEventListener(HAUTE_SESSION_EXPIRED_EVENT, handleSessionExpired)
    return () => {
      window.removeEventListener(HAUTE_SESSION_EXPIRED_EVENT, handleSessionExpired)
    }
  }, [])

  const reloadSession = useCallback(() => {
    window.location.reload()
  }, [])

  // Local UI state (not worth globalizing)
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string; nodeLabel: string; isSubmodel?: boolean; isSingleton?: boolean; isExplodable?: boolean } | null>(null)
  // Right-click menu for a multi-node selection (Group into submodel / Delete).
  const [selectionContextMenu, setSelectionContextMenu] = useState<{ x: number; y: number; nodeIds: string[] } | null>(null)
  // Preamble lives in useGraphStore. Subscribe to the string directly so
  // sibling state slices can change without re-rendering this component.
  // The raw setter avoids adding text edits to the graph undo stack.
  const preamble = useGraphStore((s) => s.preamble)
  const setPreamble = useCallback((value: string) => {
    useGraphStore.getState().setPreambleRaw(value)
  }, [])
  const lastSelectedNodeRef = useRef<Node | null>(null)
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null)

  // Keep lastSelectedId in sync â€” updates only when a node is actively selected
  useEffect(() => {
    if (selectedNode) setLastSelectedId(selectedNode.id)
  }, [selectedNode])

  // Ref for setPreviewData â€” resolved after usePipelineAPI hook below.
  // Needed because closePanel is defined before the hook for hook-ordering rules.
  const setPreviewDataRef = useRef<(d: null) => void>(() => {})

  const closePanel = useCallback(() => {
    setSelectedNode(null)
    lastSelectedNodeRef.current = null
    setLastSelectedId(null)
    setPreviewDataRef.current(null)
    setUtilityOpen(false)
    setImportsOpen(false)
    setGitOpen(false)
  }, [setUtilityOpen, setImportsOpen, setGitOpen])

  // Node results store â€” background jobs + cached results
  const getOptimiserPreview = useNodeResultsStore((s) => s.getOptimiserPreview)
  const getModellingPreview = useNodeResultsStore((s) => s.getModellingPreview)
  const touchOptimiserPreview = useNodeResultsStore((s) => s.touchOptimiserPreview)
  const touchModellingPreview = useNodeResultsStore((s) => s.touchModellingPreview)
  const touchExplorePreview = useNodeResultsStore((s) => s.touchExplorePreview)
  const setPinnedPreviewNodeId = useNodeResultsStore((s) => s.setPinnedPreviewNodeId)

  // Refs
  const submodelsRef = useRef<Record<string, unknown>>({})
  const clipboard = useRef<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] })
  const graphRef = useRef<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] })
  const parentGraphRef = useRef<{ nodes: Node[]; edges: Edge[]; submodels: Record<string, unknown> } | null>(null)
  const preambleRef = useRef("")
  const pipelineNameRef = useRef("main")
  const descriptionRef = useRef("")
  const sourceFileRef = useRef("")
  const nodeIdCounter = useRef(0)

  // Keep graphRef in sync so callbacks never see stale state. Cache freshness
  // is versioned inside useGraphStore, not by an App-level cross-store effect.
  useEffect(() => {
    graphRef.current = { nodes, edges }
  }, [nodes, edges])

  const activePanelNodeId = selectedNode?.id ?? lastSelectedId
  const panelGraph = usePanelGraphContext()
  const panelNode = panelGraph.getNode(activePanelNodeId)

  useEffect(() => {
    setPinnedPreviewNodeId(activePanelNodeId ?? null)
    if (!activePanelNodeId) return
    touchModellingPreview(activePanelNodeId)
    touchOptimiserPreview(activePanelNodeId)
    touchExplorePreview(activePanelNodeId)
  }, [activePanelNodeId, setPinnedPreviewNodeId, touchExplorePreview, touchModellingPreview, touchOptimiserPreview])

  useEffect(() => {
    if (!activePanelNodeId) return
    if (panelGraph.getNode(activePanelNodeId)) return
    setSelectedNode(null)
    lastSelectedNodeRef.current = null
    setLastSelectedId(null)
    setPreviewDataRef.current(null)
  }, [panelGraph, activePanelNodeId])

  // Node-explosion Escape arbitration (design §3.4): topmost-first. Registered
  // only while a peek is open. If a context menu is also open it owns this
  // Escape (ContextMenu's own listener closes it) — return without touching the
  // peek so the first Escape closes only the menu; a second Escape (menu now
  // gone) closes the peek. The contextMenu guard makes the outcome independent
  // of listener registration order.
  //
  // This document-bubble listener fires before the window-bubble global
  // shortcut handler (which would clearTrace()+closePanel()). When it actually
  // closes the peek it stopPropagation()s so that the same Escape never reaches
  // the global handler — the peek is the topmost surface and must be the ONLY
  // thing this keypress dismisses. (The global handler also early-returns while
  // a peek is open, covering the menu+peek case where this listener returns
  // without consuming the event; the two together are robust to listener order.)
  useEffect(() => {
    if (!peek) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return
      if (contextMenu) return
      event.stopPropagation()
      setPeek(null)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [peek, contextMenu, setPeek])

  // Store-maintained dirty flag.
  // Subscribe to the primitive so frequent React Flow node updates do not
  // serialize the graph from App's selector.
  const dirty = useGraphStore((s) => s.dirty)

  // ---------------------------------------------------------------------------
  // Hooks
  // ---------------------------------------------------------------------------

  const {
    loading, previewData, setPreviewData,
    previewBusy,
    nodeStatuses,
    fetchPreview, cancelPreview, refreshPreview, handleSave,
    runBusy, runPipeline,
  } = usePipelineAPI({
    selectedNode,
    graphRef, parentGraphRef, submodelsRef,
    setNodesRaw, setEdgesRaw, setPreamble,
    preambleRef, pipelineNameRef, descriptionRef, sourceFileRef,
    nodeIdCounter,
  })

  const wsStatus = useWebSocketSync({
    setNodesRaw, setEdgesRaw, setPreamble, preambleRef, graphRefreshingRef,
    sourceFileRef, nodeIdCounter, fitView,
    enabled: !loading,
  })
  useEffect(() => { setPreviewDataRef.current = setPreviewData }, [setPreviewData])

  const {
    traceResult, tracedCell,
    handleCellClick, clearTrace,
    nodesWithStatus, edgesWithTrace,
  } = useTracing({
    nodes, edges, selectedNode,
    graphRef, parentGraphRef, submodelsRef,
    preambleRef,
    nodeStatuses,
    hoveredNodeId,
  })

  const {
    viewStack,
    handleDrillIntoSubmodel, handleBreadcrumbNavigate,
    handleCreateSubmodel, handleDissolveSubmodel,
  } = useSubmodelNavigation({
    graphRef, parentGraphRef, submodelsRef,
    setNodesRaw, setEdgesRaw,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    preambleRef, descriptionRef, sourceFileRef, pipelineNameRef,
    fitView,
  })

  // Node-explosion: drill into the peeked submodel via the SAME handler the
  // double-click / header "Open" uses, then close the peek. An optional
  // selectChildId (from a mini-node click) selects that child on the submodel
  // canvas. Read-only with respect to editing — this is navigation only.
  const handlePeekDrillIn = useCallback((nodeId: string, selectChildId?: string) => {
    handleDrillIntoSubmodel(nodeId, selectChildId)
    setPeek(null)
  }, [handleDrillIntoSubmodel, setPeek])

  useKeyboardShortcuts({
    handleSave, setNodes, setEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    clearTrace,
    closePanel,
    isInsideSubmodel: viewStack.length > 1,
    runSelected: (ids: string[]) => runPipeline("selected-no-export", ids),
  })

  // ---------------------------------------------------------------------------
  // Node + edge interaction handlers (extracted to custom hooks)
  // ---------------------------------------------------------------------------

  const onUpdateNode = useCallback(
    (id: string, data: Record<string, unknown>) => {
      // Capture the pre-update node BEFORE committing, so apiInput edge
      // maintenance below can diff old vs new port identities.
      const prevNode = graphRef.current.nodes.find((n) => n.id === id)
      const nextNodes = graphRef.current.nodes.map((n) => (n.id === id ? { ...n, data } : n))
      graphRef.current = { ...graphRef.current, nodes: nextNodes }
      setNodes(nextNodes)
      setSelectedNode((prev) => (prev && prev.id === id ? { ...prev, data } : prev))

      // apiInput edge maintenance (W1.3 / Defect 1) — an apiInput's
      // handle ids ARE its table labels (the only id space that
      // round-trips through codegen → save → parse), so a config commit
      // can change port identities. Two cases, handled in one pass:
      //  - RENAME (W1.3): the same commit that renames a port rebinds
      //    the edges bound to the old handle — rename is migration,
      //    never edge loss.
      //  - genuine orphaning (emit-off / table-delete / single↔multi
      //    transition): the edge is pruned with a visible, named toast,
      //    instead of persisting broken to disk and KeyError-ing at run.
      if (data.nodeType !== NODE_TYPES.API_INPUT) return
      const config = (data.config ?? {}) as Record<string, unknown>
      const prevConfig = ((prevNode?.data as Record<string, unknown> | undefined)?.config ??
        {}) as Record<string, unknown>
      const { rebound, removed } = applyApiInputConfigChange({
        nodeId: id,
        prevConfig,
        nextConfig: config,
        edges: graphRef.current.edges,
      })
      if (rebound.length === 0 && removed.length === 0) return
      const reboundTo = new Map(rebound.map((r) => [r.edge.id, r.to]))
      const removedIds = new Set(removed.map((r) => r.edge.id))
      // Raw (history-skipping) on purpose: the `setNodes` above already
      // snapshotted the pre-commit {nodes, edges}, so applying the edge
      // consequences raw keeps the whole config commit ONE undo entry —
      // undoing a rename restores the old label AND its old bindings
      // atomically, with no per-keystroke or per-phase history churn.
      setEdgesRaw((eds) =>
        eds
          .filter((e) => !removedIds.has(e.id))
          .map((e) =>
            reboundTo.has(e.id) ? { ...e, sourceHandle: reboundTo.get(e.id)! } : e,
          ),
      )
      if (removed.length === 0) return
      const ports = removed
        .map((r) => (r.sourceHandle === null ? "the default port" : `port "${r.sourceHandle}"`))
        .join(", ")
      const label = String(data.label ?? id)
      addToast(
        "warning",
        `Disconnected ${removed.length} edge${removed.length === 1 ? "" : "s"} from ${label}: ${ports} no longer ${removed.length === 1 ? "exists" : "exist"} after your edit.`,
      )
    },
    [setNodes, setEdgesRaw, graphRef, addToast],
  )

  const {
    handleDeleteNode, handleDuplicateNode,
    handleCreateInstance, handleRenameNode, handleAutoLayout, isAutoLayouting,
  } = useNodeHandlers({
    graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setEdges, setSelectedNode,
    setPreviewData, fitView,
  })

  const shouldSkipAutomaticPreview = useCallback(
    (node: Node) => {
      if (nodeData(node).nodeType !== NODE_TYPES.OPTIMISER) return false
      const hasPreview = !!getOptimiserPreview(node.id)
      if (hasPreview) touchOptimiserPreview(node.id)
      return hasPreview
    },
    [getOptimiserPreview, touchOptimiserPreview],
  )

  const findEdgeIdAtPoint = useCallback((point: { x: number; y: number }) => {
    const elements = document.elementsFromPoint(point.x, point.y)
    for (const element of elements) {
      const edgeElement = element.closest?.(".react-flow__edge[data-id]")
      const edgeId = edgeElement?.getAttribute("data-id")
      if (edgeId) return edgeId
    }
    return null
  }, [])

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    return isPipelineConnectionValid(connection)
  }, [])

  const findNodeIdAtPoint = useCallback(
    (point: { x: number; y: number }) => topmostNodeAtPoint(point),
    [],
  )

  const {
    onConnect, onSelectionChange, onNodeClick, handleDeleteEdge, handleSetInputAlias,
    onConnectEnd, onNodeContextMenu, onDragOver, onDrop,
  } = useEdgeHandlers({
    selectedNode, graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setEdges, setNodesRaw, setEdgesRaw, pushSnapshot,
    setSelectedNode, setPreviewData, setContextMenu,
    fetchPreview,
    cancelPreview,
    shouldSkipAutomaticPreview,
    clearTrace,
    screenToFlowPosition,
    graphRefreshingRef,
    findEdgeIdAtPoint,
    findNodeIdAtPoint,
    getInternalNode,
    getZoom,
  })

  const handleConnectStart = useCallback<OnConnectStart>((_event, { handleType }) => {
    setConnecting(true)
    setConnectingFrom(handleType)
  }, [])
  const handleConnectEnd = useCallback<OnConnectEnd>(
    (event, connectionState) => {
      setConnecting(false)
      setConnectingFrom(null)
      onConnectEnd(event, connectionState)
    },
    [onConnectEnd],
  )

  // Canvas context menus are owned by useCanvasPan: it disambiguates pan vs
  // menu on the right button, then asks us to open the right menu for whatever
  // the press landed on. A multi-node selection under the press opens the
  // selection menu (Group into submodel / Delete); a single node opens the node
  // menu; an edge or the bare pane opens nothing (the browser menu is already
  // suppressed at the wrapper). The selected-id set is read live so the actions
  // match exactly what the marquee highlighted.
  const openSelectionMenu = useCallback((clientX: number, clientY: number) => {
    const nodeIds = graphRef.current.nodes.filter((n) => n.selected).map((n) => n.id)
    if (nodeIds.length === 0) return
    setContextMenu(null)
    setSelectionContextMenu({ x: clientX, y: clientY, nodeIds })
  }, [])

  const handleCanvasContextMenu = useCallback(
    (hit: CanvasContextHit, clientX: number, clientY: number) => {
      if (!hit.nodeId) return // edge or bare pane — no menu
      const node = graphRef.current.nodes.find((n) => n.id === hit.nodeId)
      if (!node) return
      const selectedIds = graphRef.current.nodes.filter((n) => n.selected).map((n) => n.id)
      if (selectedIds.length > 1 && selectedIds.includes(hit.nodeId)) {
        openSelectionMenu(clientX, clientY)
      } else {
        onNodeContextMenu(
          { preventDefault: () => {}, clientX, clientY } as unknown as React.MouseEvent,
          node,
        )
      }
    },
    [openSelectionMenu, onNodeContextMenu],
  )

  const canvasWrapperRef = useCanvasPan({ onContextMenu: handleCanvasContextMenu })

  // "Group into submodel" — mirrors Ctrl+G (useKeyboardShortcuts): needs ≥2
  // nodes and cannot run while drilled into a submodel (no nesting).
  const handleGroupSelection = useCallback((nodeIds: string[]) => {
    if (viewStack.length > 1) {
      addToast("info", "Wrappers can't be nested inside other wrappers")
      return
    }
    if (nodeIds.length < 2) {
      addToast("info", "Select at least 2 nodes to create a wrapper")
      return
    }
    // Guard nesting client-side (the backend also rejects it, but only after a
    // round-trip): a selection that includes a wrapper can't be grouped, since
    // that would put a wrapper inside the new one.
    const ids = new Set(nodeIds)
    const includesWrapper = graphRef.current.nodes.some(
      (n) => ids.has(n.id) && nodeData(n).nodeType === NODE_TYPES.SUBMODEL,
    )
    if (includesWrapper) {
      addToast("info", "A wrapper can't contain another wrapper — deselect it first")
      return
    }
    setSubmodelDialog({ nodeIds })
  }, [viewStack.length, addToast, setSubmodelDialog])

  // "Delete" — mirrors the Delete-key path (useKeyboardShortcuts): remove the
  // selected nodes and any edge touching them, clear selection/preview, and
  // wipe each deleted node's cached results. setNodes/setEdges push an undo
  // snapshot, so the delete is undoable.
  const handleDeleteSelection = useCallback((nodeIds: string[]) => {
    const ids = new Set(nodeIds)
    if (ids.size === 0) return
    const { nodes: currentNodes, edges: currentEdges } = graphRef.current
    setNodes(currentNodes.filter((n) => !ids.has(n.id)))
    setEdges(currentEdges.filter((ed) => !ids.has(ed.source) && !ids.has(ed.target)))
    setSelectedNode(null)
    setPreviewData(null)
    if (lastSelectedNodeRef.current && ids.has(lastSelectedNodeRef.current.id)) {
      lastSelectedNodeRef.current = null
    }
    for (const nid of nodeIds) {
      useNodeResultsStore.getState().clearNode(nid)
    }
  }, [setNodes, setEdges, setSelectedNode, setPreviewData])

  const handleSwapEdgeJoinInputs = useCallback((nodeId: string) => {
    const result = swapEdgeJoinInputs({
      nodes: graphRef.current.nodes,
      edges: graphRef.current.edges,
      edgeJoinNodeId: nodeId,
    })
    if (!result.ok) {
      addToast("error", edgeJoinSwapFailureMessages[result.reason])
      return
    }

    const selected = result.nodes.find((node) => node.id === nodeId) ?? null
    pushSnapshot()
    setNodesRaw(result.nodes)
    setEdgesRaw(result.edges)
    setSelectedNode(selected)
    lastSelectedNodeRef.current = selected
    clearTrace()
    cancelPreview()
  }, [
    addToast,
    cancelPreview,
    clearTrace,
    graphRef,
    lastSelectedNodeRef,
    pushSnapshot,
    setEdgesRaw,
    setNodesRaw,
    setSelectedNode,
  ])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="h-full w-full flex items-center justify-center" style={{ background: 'var(--bg-base)' }}>
        <div className="text-sm" style={{ color: 'var(--text-muted)' }}>Loading pipeline...</div>
      </div>
    )
  }

  // eslint-disable-next-line react-hooks/refs -- ref is mutated by hooks; reading here is intentional
  const submodelsSnapshot = submodelsRef.current
  const useLiteGraphEffects = shouldUseLiteGraphEffects(nodes.length, edges.length)
  return (
    <div className="h-full w-full flex flex-col" style={{ background: 'var(--bg-base)' }}>
      <Toolbar
        nodeCount={nodes.length}
        dirty={dirty}
        canUndo={canUndo}
        canRedo={canRedo}
        onUndo={undo}
        onRedo={redo}
        onZoomIn={() => zoomIn()}
        onZoomOut={() => zoomOut()}
        onOpenUtility={() => { setUtilityOpen(true); setSelectedNode(null); lastSelectedNodeRef.current = null; setPreviewDataRef.current(null); setContextMenu(null) }}
        onOpenImports={() => { setImportsOpen(true); setSelectedNode(null); lastSelectedNodeRef.current = null; setPreviewDataRef.current(null); setContextMenu(null) }}
        onOpenGit={() => { setGitOpen(true); setSelectedNode(null); lastSelectedNodeRef.current = null; setPreviewDataRef.current(null); setContextMenu(null) }}
        onCentre={() => fitView({ padding: 0.15 })}
        onAutoLayout={handleAutoLayout}
        isAutoLayouting={isAutoLayouting}
        onSave={handleSave}
        onRun={runPipeline}
        runBusy={runBusy}
        wsStatus={wsStatus}
        timings={previewData?.timings}
        memory={previewData?.memory}
      />

      <div className="flex-1 flex min-h-0">
        <nav aria-label="Node palette">
          {paletteOpen ? (
            <ErrorBoundary name="NodePalette">
              <NodePalette onCollapse={() => setPaletteOpen(false)} nodes={nodes} />
            </ErrorBoundary>
          ) : (
            <button
              onClick={() => setPaletteOpen(true)}
              aria-label="Show node palette"
              className="shrink-0 flex items-center justify-center w-10 h-full hover-chrome-solid"
              style={{ borderRight: '1px solid var(--chrome-border)' }}
              title="Show node palette"
            >
              <PanelLeftOpen size={16} style={{ color: 'var(--text-muted)' }} />
            </button>
          )}
        </nav>

        <main className="flex-1 flex flex-col min-w-0">
          {sessionExpired && (
            <div
              role="alert"
              className="flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium"
              style={{ background: 'var(--danger-soft-strong)', color: 'var(--danger-text)', borderBottom: '1px solid var(--danger-border-strong)' }}
            >
              <span className="flex-1 truncate">Session expired. Reload Haute to reconnect to this server.</span>
              <button
                onClick={reloadSession}
                className="px-2 py-0.5 rounded border border-current opacity-90 hover:opacity-100"
              >
                Reload
              </button>
            </div>
          )}
          {syncBanner && (
            <div className="flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium"
              style={{ background: 'var(--danger-soft-strong)', color: 'var(--danger-text)', borderBottom: '1px solid var(--danger-border-strong)' }}>
              <span className="flex-1 truncate">{syncBanner}</span>
              <button onClick={() => setSyncBanner(null)} className="opacity-60 hover:opacity-100">âœ•</button>
            </div>
          )}
          <ErrorBoundary name="Canvas">
            <div ref={canvasWrapperRef} className="flex-1 min-h-0 relative" data-connecting={connecting ? "true" : undefined}>
              <BreadcrumbBar viewStack={viewStack} onNavigate={handleBreadcrumbNavigate} />
              <ReactFlow
                className={[
                  useLiteGraphEffects ? "graph-effects-lite" : null,
                  zoomBucket === "full" ? null : `zoom-${zoomBucket}`,
                  connecting ? "connecting" : null,
                  connectingFrom === "source" ? "connecting-from-source" : null,
                  connectingFrom === "target" ? "connecting-from-target" : null,
                ].filter(Boolean).join(" ") || undefined}
                nodes={nodesWithStatus}
                edges={edgesWithTrace}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onConnectStart={handleConnectStart}
                onConnectEnd={handleConnectEnd}
                connectionRadius={CONNECTION_RADIUS_BY_BUCKET[zoomBucket]}
                onSelectionChange={onSelectionChange}
                onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
                onNodeMouseLeave={() => setHoveredNodeId(null)}
                onNodeClick={(event, node) => { setUtilityOpen(false); setImportsOpen(false); setGitOpen(false); setHoveredNodeId(null); onNodeClick(event, node) }}
                onNodeDoubleClick={(_event, node) => {
                  if (nodeData(node).nodeType === NODE_TYPES.SUBMODEL) {
                    handleDrillIntoSubmodel(node.id)
                  }
                }}
                onPaneClick={() => { setContextMenu(null); setSelectionContextMenu(null); setPeek(null); clearTrace(); closePanel() }}
                onDrop={onDrop}
                onDragOver={onDragOver}
                nodeTypes={nodeTypes}
                // Panning is owned by useCanvasPan (middle + right-drag, with
                // right-button menu/pan disambiguation); React Flow's own
                // drag-pan is disabled so it can't fight the gesture controller.
                panOnDrag={false}
                selectionOnDrag
                selectNodesOnDrag
                selectionMode={SelectionMode.Partial}
                selectionKeyCode={null}
                minZoom={0.1}
                fitView
                fitViewOptions={fitViewOptions}
                proOptions={proOptions}
                defaultEdgeOptions={defaultEdgeOptions}
                connectionLineStyle={connectionLineStyle}
                connectionMode={ConnectionMode.Loose}
                isValidConnection={isValidConnection}
              >
                <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,.06)" />
                {peek && (
                  <NodePeek
                    nodeId={peek.nodeId}
                    onClose={() => setPeek(null)}
                    onDrillIn={handlePeekDrillIn}
                  />
                )}
              </ReactFlow>
            </div>
          </ErrorBoundary>

          <ErrorBoundary name="DataPreview">
            {(() => {
              const activeNodeId = selectedNode?.id ?? lastSelectedId
              const activePreviewData = previewForActiveNode(previewData, activeNodeId)
              const activeNode = panelGraph.getNode(activeNodeId)
              if (activeNode && nodeData(activeNode).nodeType === NODE_TYPES.EXPLORE) {
                return (
                  <ExplorePreview
                    node={activeNode}
                    allNodes={panelGraph.allNodes}
                    edges={panelGraph.edges}
                    submodels={submodelsSnapshot}
                    preamble={preamble}
                    previewData={activePreviewData}
                    onCellClick={handleCellClick}
                    tracedCell={tracedCell}
                  />
                )
              }
              const modelPreview = activeNodeId ? getModellingPreview(activeNodeId) : null
              if (modelPreview) {
                return (
                  <ModellingPreview
                    data={modelPreview}
                    nodeId={activeNodeId!}
                  />
                )
              }
              const optPreview = activeNodeId ? getOptimiserPreview(activeNodeId) : null
              if (optPreview) {
                return (
                  <OptimiserPreview
                    data={optPreview}
                    nodeId={activeNodeId!}
                    allNodes={panelGraph.allNodes}
                    edges={panelGraph.edges}
                  />
                )
              }
              // Pre-solve chart view for optimiser nodes
              if (
                activeNode &&
                nodeData(activeNode).nodeType === NODE_TYPES.OPTIMISER &&
                activePreviewData &&
                activePreviewData.status === "ok" &&
                activePreviewData.preview.length > 0
              ) {
                return (
                  <OptimiserDataPreview
                    data={activePreviewData}
                    config={nodeData(activeNode).config ?? {}}
                  />
                )
              }
              return (
                <DataPreview
                  data={activePreviewData}
                  nodeType={activeNode ? nodeData(activeNode).nodeType : undefined}
                  onCellClick={handleCellClick}
                  tracedCell={tracedCell}
                />
              )
            })()}
          </ErrorBoundary>
        </main>

        <aside aria-label="Node properties">
          <ErrorBoundary name="NodePanel">
            {gitOpen ? (
              <GitPanel onClose={() => setGitOpen(false)} />
            ) : utilityOpen ? (
              <UtilityPanel
                onClose={() => setUtilityOpen(false)}
                onImportAdded={(importLine) => {
                  const current = preambleRef.current
                  if (!current.includes(importLine)) {
                    const updated = current ? `${current}\n${importLine}` : importLine
                    setPreamble(updated)
                    preambleRef.current = updated
                    // Dirty is derived from the new preamble at next render.
                  }
                }}
              />
            ) : importsOpen ? (
              <ImportsPanel
                preamble={preamble}
                onPreambleChange={(value) => {
                  setPreamble(value)
                  preambleRef.current = value
                  // Dirty is derived from the new preamble at next render.
                }}
                onClose={() => setImportsOpen(false)}
              />
            ) : traceResult ? (
              <TracePanel trace={traceResult} onClose={clearTrace} />
            ) : (
              <GraphProvider
                allNodes={panelGraph.allNodes}
                edges={panelGraph.edges}
                submodels={submodelsSnapshot}
                preamble={preamble}
              >
                <NodePanel
                  node={panelNode}
                  onClose={closePanel}
                  onUpdateNode={onUpdateNode}
                  onDeleteEdge={handleDeleteEdge}
                  onSetInputAlias={handleSetInputAlias}
                  onSwapEdgeJoinInputs={handleSwapEdgeJoinInputs}
                  onRefreshPreview={() => {
                    if (!panelNode) return
                    const refreshTarget = graphRef.current.nodes.find((n) => n.id === panelNode.id)
                    if (refreshTarget) refreshPreview(refreshTarget)
                  }}
                  dimmed={!selectedNode && !!lastSelectedId}
                  errorLine={
                    previewData?.nodeId === activePanelNodeId
                      ? previewData?.error_line ?? null
                      : null
                  }
                  previewRows={
                    previewData?.status === "ok" && previewData?.nodeId === activePanelNodeId
                      ? previewData.preview
                      : undefined
                  }
                  selectedPreviewLoading={previewBusy && selectedNode?.id === activePanelNodeId}
                />
              </GraphProvider>
            )}
          </ErrorBoundary>
        </aside>
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          nodeId={contextMenu.nodeId}
          nodeLabel={contextMenu.nodeLabel}
          onClose={() => setContextMenu(null)}
          onDelete={handleDeleteNode}
          onDuplicate={handleDuplicateNode}
          onRename={handleRenameNode}
          onCreateInstance={handleCreateInstance}
          isSubmodel={contextMenu.isSubmodel}
          isSingleton={contextMenu.isSingleton}
          isExplodable={contextMenu.isExplodable}
          onPeek={(id) => { setPeek({ nodeId: id }); setContextMenu(null) }}
          onOpen={(id) => { handleDrillIntoSubmodel(id); setContextMenu(null) }}
          onDissolveSubmodel={handleDissolveSubmodel}
        />
      )}

      {selectionContextMenu && (
        <SelectionContextMenu
          x={selectionContextMenu.x}
          y={selectionContextMenu.y}
          nodeIds={selectionContextMenu.nodeIds}
          onClose={() => setSelectionContextMenu(null)}
          onGroup={handleGroupSelection}
          onDelete={handleDeleteSelection}
        />
      )}

      {shortcutsOpen && <KeyboardShortcuts onClose={() => setShortcutsOpen(false)} />}

      {submodelDialog && (
        <SubmodelDialog
          nodeCount={submodelDialog.nodeIds.length}
          onClose={() => setSubmodelDialog(null)}
          onSubmit={(name) => {
            handleCreateSubmodel(name, submodelDialog.nodeIds)
            setSubmodelDialog(null)
          }}
        />
      )}

      {renameDialog && (
        <RenameDialog
          defaultValue={renameDialog.currentLabel}
          onCancel={() => setRenameDialog(null)}
          onConfirm={(newName) => {
            const node = graphRef.current.nodes.find((n) => n.id === renameDialog.nodeId)
            if (node) onUpdateNode(renameDialog.nodeId, { ...node.data, label: newName })
            setRenameDialog(null)
          }}
        />
      )}

      {nodeSearchOpen && (
        <NodeSearch
          onClose={() => setNodeSearchOpen(false)}
          onSelectNode={(nodeId) => {
            const node = graphRef.current.nodes.find((n) => n.id === nodeId) ?? null
            if (node) {
              setSelectedNode(node)
              lastSelectedNodeRef.current = node
              setUtilityOpen(false)
              setImportsOpen(false)
              setGitOpen(false)
            }
          }}
        />
      )}

      <ErrorBoundary name="Toast">
        <ToastContainer />
      </ErrorBoundary>
    </div>
  )
}

function App() {
  return (
    <ReactFlowProvider>
      <BackgroundJobPolling />
      <FlowEditor />
    </ReactFlowProvider>
  )
}

export default App
