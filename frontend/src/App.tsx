import { useEffect, useCallback, useMemo, useState, useRef, lazy, Suspense } from "react"
import type { ReactNode } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useReactFlow,
  SelectionMode,
  ConnectionMode,
  type Node,
  type Edge,
  type Connection,
  type EdgeChange,
  type NodeChange,
  BackgroundVariant,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { moveToVersion } from "./api/client"
import { nodeTypes } from "./utils/nodeTypeRegistry"
import NodePalette from "./panels/NodePalette"
import NodePanel from "./panels/NodePanel"
import { GraphProvider } from "./panels/GraphContext"
import DataPreview from "./panels/DataPreview"
import ExplorePreview from "./panels/ExplorePreview"
import OptimiserDataPreview from "./panels/OptimiserDataPreview"
import { ModellingPreview } from "./panels/ModellingPreview"

import TracePanel, { TraceStatePanel } from "./panels/TracePanel"
import ToastContainer from "./components/Toast"
import { ErrorBoundary } from "./components/ErrorBoundary"
import ContextMenu from "./components/ContextMenu"
import KeyboardShortcuts from "./components/KeyboardShortcuts"
import BreadcrumbBar from "./components/BreadcrumbBar"
import Toolbar from "./components/Toolbar"
import SubmodelDialog from "./components/SubmodelDialog"
import RenameDialog from "./components/RenameDialog"
import BackgroundJobPolling from "./components/BackgroundJobPolling"
import ImportsPanel from "./panels/ImportsPanel"
import type { ComparisonInspect } from "./components/ComparisonView"
import EdgeJoinInsertionFeedback from "./components/EdgeJoinInsertionFeedback"
import { withEdgeJoinInsertionCandidate } from "./utils/edgeJoinInsertionFeedback"

import useGraphCanvasState from "./hooks/useGraphCanvasState"
import useWebSocketSync from "./hooks/useWebSocketSync"
import usePipelineAPI from "./hooks/usePipelineAPI"
import useTracing from "./hooks/useTracing"
import useSubmodelNavigation from "./hooks/useSubmodelNavigation"
import useSubmodelBoundaryEditing from "./hooks/useSubmodelBoundaryEditing"
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts"
import useNodeHandlers from "./hooks/useNodeHandlers"
import useEdgeHandlers from "./hooks/useEdgeHandlers"
import usePanelGraphContext from "./hooks/usePanelGraphContext"
import useSettingsStore from "./stores/useSettingsStore"
import useUIStore from "./stores/useUIStore"
import useGraphStore from "./stores/useGraphStore"
import useGitStore from "./stores/useGitStore"
import useToastStore from "./stores/useToastStore"
import useNodeResultsStore from "./stores/useNodeResultsStore"
import { HAUTE_SESSION_EXPIRED_EVENT } from "./api/client"

import { NODE_TYPES } from "./utils/nodeTypes"
import { previewForActiveNode } from "./utils/activePreview"
import { swapEdgeJoinInputs, type EdgeJoinSwapInputsFailureReason } from "./utils/edgeJoinGraph"
import { validatePipelineConnection, type ConnectionValidationResult } from "./utils/connectionValidation"
import {
  applyApiInputConfigChange,
  edgeInputName,
  incomingEdgeInputNames,
} from "./utils/apiInputPorts"
import type { OnUpdateConfigResult, SimpleEdge, SimpleNode } from "./panels/editors/_shared"
import { shouldUseLiteGraphEffects } from "./utils/graphPerformance"
import type { DrilledOccurrenceIdentity } from "./utils/submodelRuntimeTarget"
import { isSubmodelInstanceConfig, nodeData } from "./types/node"
import { PanelLeftOpen } from "lucide-react"

// ---------------------------------------------------------------------------
// Lazy-loaded version-control surfaces — code-split out of the initial bundle.
// All are user-triggered on-demand (modals, git panel, compare view), so each
// render site wraps the lazy element in a LOCAL <Suspense fallback={null}>.
// ---------------------------------------------------------------------------

const DivergenceModal = lazy(() => import("./components/DivergenceModal"))
const MilestoneCommitModal = lazy(() => import("./components/MilestoneCommitModal"))
const MoveConfirmModal = lazy(() => import("./components/MoveConfirmModal"))
const WorkingBranchModal = lazy(() => import("./components/WorkingBranchModal"))
const GitPanel = lazy(() => import("./panels/GitPanel"))
const UtilityPanel = lazy(() => import("./panels/UtilityPanel"))
const AssistantPanel = lazy(() => import("./panels/assistant/AssistantPanel"))
const ComparisonView = lazy(() => import("./components/ComparisonView"))
const ComparisonInspector = lazy(() => import("./components/ComparisonInspector"))
const NodeSearch = lazy(() => import("./components/NodeSearch"))
// Optimiser results are produced only after a user-triggered solve, so keep
// the comparatively heavy charts out of the initial application bundle.
const OptimiserPreview = lazy(() => import("./panels/OptimiserPreview"))

// ---------------------------------------------------------------------------
// Module-level constants (no dynamic values — avoids re-creating each render)
// ---------------------------------------------------------------------------

const defaultEdgeOptions = {
  type: "default" as const,
  animated: false,
  style: { stroke: 'rgba(255,255,255,.25)', strokeWidth: 1.5 },
}

const connectionLineStyle = { stroke: 'var(--accent)', strokeWidth: 2, strokeDasharray: '6 3' }

const fitViewOptions = { padding: 0.15 }

const proOptions = { hideAttribution: true }

type RenamePair = { from: string; to: string }

type RenameGraphScope = {
  nodes: Node[]
  edges: Edge[]
  submodels: Record<string, unknown>
}

type AffectedRenameTarget = {
  scope: RenameGraphScope
  target: Node
  incomingScope: RenameGraphScope
  incomingTargetId: string
  pairs: RenamePair[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function remapRecordKeys(
  value: unknown,
  renames: readonly RenamePair[],
): { value: unknown; collision?: string } {
  if (!isRecord(value) || renames.length === 0) return { value }
  const renameByFrom = new Map(renames.map(({ from, to }) => [from, to]))
  const next: Record<string, unknown> = {}
  for (const [key, entry] of Object.entries(value)) {
    const nextKey = renameByFrom.get(key) ?? key
    if (Object.hasOwn(next, nextKey)) return { value, collision: nextKey }
    next[nextKey] = entry
  }
  return { value: next }
}

function remapRecordValues(
  value: unknown,
  renames: readonly RenamePair[],
): unknown {
  if (!isRecord(value) || renames.length === 0) return value
  const renameByFrom = new Map(renames.map(({ from, to }) => [from, to]))
  let changed = false
  const next = Object.fromEntries(
    Object.entries(value).map(([key, entry]) => {
      if (typeof entry !== "string") return [key, entry]
      const replacement = renameByFrom.get(entry)
      if (replacement === undefined) return [key, entry]
      changed = true
      return [key, replacement]
    }),
  )
  return changed ? next : value
}

// One-shot sessionStorage flag set just before the post-move reload, read once on
// the next startup so it can confirm the move (toast) and skip the auto branch
// prompt — the moved-to detached HEAD is intended, not a divergence (P6 §3.4).
const JUST_MOVED_KEY = "haute:justMoved"

const edgeJoinSwapFailureMessages: Record<EdgeJoinSwapInputsFailureReason, string> = {
  "edge-join-node-not-found": "Edge join swap rejected: selected edge join is no longer available",
  "target-node-not-edge-join": "Edge join swap rejected: selected node is not an edge join",
  "base-input-not-found": "Edge join swap rejected: dominant input is not connected",
  "join-input-not-found": "Edge join swap rejected: joining input is not connected",
  "base-input-ambiguous": "Edge join swap rejected: dominant input has more than one connection",
  "join-input-ambiguous": "Edge join swap rejected: joining input has more than one connection",
}

// Note: the ReactFlow node-type → component registry now lives in
// ./utils/nodeTypeRegistry (imported as `nodeTypes` above), shared with the
// read-only comparison canvases so the two never drift on which component
// renders a given node type. The edgeJoin/explore types added for multi-frame
// work are carried into that registry module.

// ---------------------------------------------------------------------------
// FlowEditor — main orchestrator
// ---------------------------------------------------------------------------

function FlowEditor() {
  const graphRefreshingRef = useRef(0)

  // Core ReactFlow state with undo/redo
  const {
    nodes, edges,
    setNodes, setEdges, setNodesAndEdges, setNodesAndEdgesAndSubmodels,
    setNodesRaw, setEdgesRaw, setSubmodelsRaw,
    onNodesChange, onEdgesChange,
    undo, redo, canUndo, canRedo, pushSnapshot,
  } = useGraphCanvasState([], [], graphRefreshingRef)
  const { screenToFlowPosition, fitView, zoomIn, zoomOut } = useReactFlow()

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
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const setAssistantOpen = useUIStore((s) => s.setAssistantOpen)
  const shortcutsOpen = useUIStore((s) => s.shortcutsOpen)
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const submodelDialog = useUIStore((s) => s.submodelDialog)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)
  const renameDialog = useUIStore((s) => s.renameDialog)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  // Git working-branch model (P2)
  const gitModal = useGitStore((s) => s.modal)
  const loadGitReadiness = useGitStore((s) => s.loadStatus)
  const closeGitModal = useGitStore((s) => s.closeModal)
  // Read-only comparison view (S11): when set, the dual-canvas overlay replaces
  // the editor's content row (the toolbar stays, remaining interactive).
  const comparison = useGitStore((s) => s.comparison)
  const closeComparison = useGitStore((s) => s.closeComparison)
  // Move-through-history (P6 §3.4): the version queued for a real checkout,
  // pending the pre-move save/discard/confirm prompt.
  const moveTarget = useGitStore((s) => s.moveTarget)
  const closeMove = useGitStore((s) => s.closeMove)
  const addToast = useToastStore((s) => s.addToast)
  const syncBanner = useUIStore((s) => s.syncBanner)
  const setSyncBanner = useUIStore((s) => s.setSyncBanner)
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId)
  const setHoveredNodeId = useUIStore((s) => s.setHoveredNodeId)
  const nodeSearchOpen = useUIStore((s) => s.nodeSearchOpen)
  const setNodeSearchOpen = useUIStore((s) => s.setNodeSearchOpen)
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
  const [selectedNodeState, setSelectedNode] = useState<Node | null>(null)
  // The node under read-only inspection in the comparison view, or null. Cleared
  // whenever the comparison closes or the inspected version changes (S11).
  const [comparisonInspectState, setComparisonInspectState] = useState<{
    comparisonSha: string
    inspect: ComparisonInspect
  } | null>(null)
  const comparisonInspect = comparisonInspectState && comparisonInspectState.comparisonSha === comparison?.sha
    ? comparisonInspectState.inspect
    : null
  // Exiting comparison must also clear gitOpen, else the VC panel (which is how
  // compare mode is entered) would pop open unbidden back in the normal editor.
  const exitComparison = useCallback(() => {
    closeComparison()
    setComparisonInspectState(null)
    setGitOpen(false)
  }, [closeComparison, setGitOpen])
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string; nodeLabel: string; isSubmodel?: boolean; isSingleton?: boolean } | null>(null)
  // Preamble lives in useGraphStore. Subscribe to the string directly so
  // sibling state slices can change without re-rendering this component.
  // The raw setter avoids adding text edits to the graph undo stack.
  const preamble = useGraphStore((s) => s.preamble)
  const submodels = useGraphStore((s) => s.submodels)
  const setPreamble = useCallback((value: string) => {
    useGraphStore.getState().setPreambleRaw(value)
  }, [])
  const lastSelectedNodeRef = useRef<Node | null>(null)
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null)

  // The last selected id is updated at selection event boundaries so the panel
  // can remain visible while React Flow reports a canvas deselection.
  // Ref for setPreviewData — resolved after usePipelineAPI hook below.
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

  // Node results store — background jobs + cached results
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
  const [activeSubmodelIdentity, setActiveSubmodelIdentity] = useState<DrilledOccurrenceIdentity | null>(null)
  const preambleRef = useRef("")
  const pipelineNameRef = useRef("main")
  const descriptionRef = useRef("")
  const sourceFileRef = useRef("")
  const sourceRevisionRef = useRef("")
  const preservedBlocksRef = useRef<string[]>([])
  const [currentSourceFile, setCurrentSourceFile] = useState<string | null>(null)
  const nodeIdCounter = useRef(0)

  // Keep graphRef in sync so callbacks never see stale state. Cache freshness
  // is versioned inside useGraphStore, not by an App-level cross-store effect.
  useEffect(() => {
    graphRef.current = { nodes, edges }
  }, [nodes, edges])

  useEffect(() => {
    submodelsRef.current = submodels
  }, [submodels])

  const panelGraph = usePanelGraphContext()
  const selectedNode = selectedNodeState && panelGraph.getNode(selectedNodeState.id)
    ? selectedNodeState
    : null
  const activePanelNodeCandidate = selectedNode?.id ?? lastSelectedId
  const panelNode = panelGraph.getNode(activePanelNodeCandidate)
  const activePanelNodeId = panelNode ? activePanelNodeCandidate : null

  useEffect(() => {
    setPinnedPreviewNodeId(activePanelNodeId ?? null)
    if (!activePanelNodeId) return
    touchModellingPreview(activePanelNodeId)
    touchOptimiserPreview(activePanelNodeId)
    touchExplorePreview(activePanelNodeId)
  }, [activePanelNodeId, setPinnedPreviewNodeId, touchExplorePreview, touchModellingPreview, touchOptimiserPreview])

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
    fetchPreview, cancelPreview, refreshPreview, previewNodeFrame, handleSave,
  } = usePipelineAPI({
    selectedNode,
    graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef,
    setNodesRaw, setEdgesRaw, setSubmodelsRaw, setCurrentSourceFile, setPreamble,
    preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef,
    nodeIdCounter,
  })

  // Flush the editor to the ledger, then open the milestone-commit modal —
  // but only once the save has actually landed, so the milestone never commits
  // a stale ledger (and we don't prompt for a message after a failed save).
  const flushSaveThenMilestone = useCallback(async () => {
    const ok = await handleSave()
    if (ok) useGitStore.getState().openModal("milestone")
  }, [handleSave])

  // Save-gate (S5/S13): if no working branch is ready, the action opens the
  // selection modal first and runs once a branch is chosen. Divergence routes
  // to its own modal. Pipeline Save remains available when Git is absent or
  // readiness could not be loaded; only Git Commit requires a ready repository.
  const requestSave = useCallback(async () => {
    // Resolve status before deciding: during the startup load (status null,
    // loading in-flight) a synchronous read would see null and save ungated,
    // bypassing the gate. Awaiting the in-flight/fresh load closes that race.
    const st = useGitStore.getState().status ?? (await useGitStore.getState().loadStatus())
    if (st === null || st.state === "no-repository" || st.state === "ready") {
      void handleSave()
      return
    }
    useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select", {
      pendingAction: "save",
    })
  }, [handleSave])

  // Save & commit (S7): same gate, but the queued action flushes a save then
  // opens the milestone modal.
  const requestCommit = useCallback(async () => {
    const st = useGitStore.getState().status ?? (await useGitStore.getState().loadStatus())
    if (st === null) {
      const detail = useGitStore.getState().statusError
      addToast(
        "error",
        detail ? `Git unavailable: ${detail}` : "Git readiness is unavailable — commit is disabled.",
      )
      return
    }
    if (st.state === "no-repository") {
      addToast("error", "No git repository — commit is unavailable.")
      return
    }
    if (st.state === "ready") {
      void flushSaveThenMilestone()
      return
    }
    useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select", {
      pendingAction: "commit",
    })
  }, [flushSaveThenMilestone, addToast])

  // A working-branch / divergence modal confirmed a branch: run the queued
  // action (read it before closeModal clears it).
  const handleGitModalConfirmed = useCallback(() => {
    const pending = useGitStore.getState().pendingAction
    useGitStore.getState().closeModal()
    if (pending === "save") void handleSave()
    else if (pending === "commit") void flushSaveThenMilestone()
  }, [handleSave, flushSaveThenMilestone])

  // Pre-move prompt confirmed (P6 §3.4): optionally flush unsaved edits onto the
  // current branch (parking IS saving, S12), then move — a real detached
  // checkout. A move replaces the whole working tree, so we reload to re-init the
  // canvas from the moved-to state; the one-shot flag tells the next startup it
  // arrived via a move (don't auto-prompt; the modal fires on first SAVE, S13).
  const handleMoveConfirmed = useCallback(
    async (saveFirst: boolean) => {
      const target = useGitStore.getState().moveTarget
      if (!target) return
      try {
        if (saveFirst) {
          const ok = await handleSave()
          if (!ok) {
            addToast("error", "Save failed — staying on the current version.")
            useGitStore.getState().closeMove()
            return
          }
        }
        await moveToVersion(target.sha)
        sessionStorage.setItem(JUST_MOVED_KEY, target.label)
        window.location.reload()
      } catch (err: unknown) {
        const { gitErrorMessage } = await import("./utils/gitError")
        const detail = gitErrorMessage(err, "unknown error")
        addToast("error", `Could not move to this version: ${detail}`)
        useGitStore.getState().closeMove()
      }
    },
    [handleSave, addToast],
  )

  // Startup readiness check (S27): load status once, and surface the modal only
  // when something needs attention (unset/invalid → select, divergent → that
  // modal). A healthy clone fires nothing. Exception: when we've just arrived via
  // a move (one-shot flag), HEAD is detached / working branch unset BY DESIGN —
  // skip the auto-prompt and confirm the move with a toast; the branch modal is
  // meant to fire on the first SAVE here, not on arrival (P6 §3.4 / S13).
  useEffect(() => {
    const justMoved = sessionStorage.getItem(JUST_MOVED_KEY)
    if (justMoved !== null) sessionStorage.removeItem(JUST_MOVED_KEY)
    void loadGitReadiness().then((st) => {
      if (justMoved !== null) {
        addToast("info", `Moved to ${justMoved} — save to start a new version line here.`)
        return
      }
      if (!st || st.state === "ready" || st.state === "no-repository" || st.state === "detached") return
      useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select")
    })
  }, [loadGitReadiness, addToast])

  const wsStatus = useWebSocketSync({
    preambleRef, submodelsRef, graphRefreshingRef, sourceFileRef,
    sourceRevisionRef, preservedBlocksRef, nodeIdCounter, fitView,
    enabled: !loading,
  })
  useEffect(() => { setPreviewDataRef.current = setPreviewData }, [setPreviewData])

  const {
    traceResult, tracedCell, traceState,
    handleCellClick, clearTrace, cancelTrace, retryTrace,
    nodesWithStatus, edgesWithTrace,
  } = useTracing({
    nodes, edges, selectedNode,
    submodels,
    graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef,
    preambleRef,
    nodeStatuses,
    hoveredNodeId,
    refreshPreview,
  })
  const canvasNodes = useMemo(
    () => nodesWithStatus.map((node) => (
      nodeData(node).nodeType === NODE_TYPES.SUBMODEL && node.deletable !== false
        ? { ...node, deletable: false }
        : node
    )),
    [nodesWithStatus],
  )


  const {
    viewStack,
    handleDrillIntoSubmodel, handleBreadcrumbNavigate,
    handleCreateSubmodel, handleDissolveSubmodel,
  } = useSubmodelNavigation({
    graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef,
    setNodesRaw, setEdgesRaw, setSubmodelsRaw,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    setLastSelectedId,
    setCurrentSourceFile,
    preambleRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef, pipelineNameRef,
    fitView,
  })

  const activeView = viewStack[viewStack.length - 1]
  const activeSubmodelName = activeView?.type === "submodel" ? activeView.name : null
  const activeSubmodelInstanceId = activeView?.type === "submodel" ? activeView.instanceId ?? null : null
  const activeSubmodelDefinitionId = activeView?.type === "submodel" ? activeView.definitionId ?? null : null
  const activeSubmodelReadOnly = activeView?.type === "submodel" && activeView.readOnly

  const {
    commitBoundaryConnection,
    deleteBoundaryEdge,
    onBoundaryEdgesChange,
    commitSharedNodeDeletion,
  } = useSubmodelBoundaryEditing({
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
  })
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    if (!activeSubmodelReadOnly) {
      const removedNodeIds = new Set(changes
        .filter((change): change is Extract<NodeChange, { type: "remove" }> => change.type === "remove")
        .map((change) => change.id))
      if (commitSharedNodeDeletion(removedNodeIds, new Set(), changes) !== "not-applicable") return
      onNodesChange(changes)
      return
    }
    const presentationChanges = changes.filter(
      (change) => change.type === "select" || change.type === "dimensions",
    )
    if (presentationChanges.length > 0) onNodesChange(presentationChanges)
  }, [activeSubmodelReadOnly, commitSharedNodeDeletion, onNodesChange])

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (activeSubmodelReadOnly) {
      const selectionChanges = changes.filter((change) => change.type === "select")
      if (selectionChanges.length > 0) onEdgesChange(selectionChanges)
      return
    }
    if (onBoundaryEdgesChange(changes)) return
    onEdgesChange(changes)
  }, [activeSubmodelReadOnly, onBoundaryEdgesChange, onEdgesChange])

  useKeyboardShortcuts({
    handleSave: requestSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    setLastSelectedId,
    clearTrace,
    closePanel,
    isInsideSubmodel: viewStack.length > 1,
    readOnly: activeSubmodelReadOnly,
    commitSharedNodeDeletion,
  })

  // ---------------------------------------------------------------------------
  // Node + edge interaction handlers (extracted to custom hooks)
  // ---------------------------------------------------------------------------

  const onUpdateNode = useCallback(
    (id: string, data: Record<string, unknown>): OnUpdateConfigResult => {
      if (activeSubmodelReadOnly) {
        return { ok: false, error: "This submodel instance is read-only." }
      }
      // Capture the pre-update node BEFORE committing, so apiInput edge
      // maintenance below can diff old vs new frame identities.
      const currentGraph = graphRef.current
      const prevNode = currentGraph.nodes.find((n) => n.id === id)
      if (!prevNode) {
        return { ok: false, error: `Cannot update missing node "${id}".` }
      }

      // Compute the entire candidate graph before touching either the store
      // or graphRef. A failed preflight therefore cannot leave a config,
      // edge, mapping, or history entry behind.
      let tentativeEdges = currentGraph.edges
      let rebound: Array<{ edge: Edge; from: string; to: string }> = []
      let removed: Array<{ edge: Edge; sourceHandle: string | null }> = []
      if (data.nodeType === NODE_TYPES.API_INPUT) {
        const config = (data.config ?? {}) as Record<string, unknown>
        const prevConfig = ((prevNode.data as Record<string, unknown>).config ??
          {}) as Record<string, unknown>
        const result = applyApiInputConfigChange({
          nodeId: id,
          prevConfig,
          nextConfig: config,
          edges: currentGraph.edges,
        })
        tentativeEdges = result.edges
        rebound = result.rebound
        removed = result.removed
      } else if (prevNode.data.label !== data.label) {
        const nextNode = { ...prevNode, data }
        rebound = currentGraph.edges
          .filter((edge) => edge.source === id)
          .map((edge) => ({
            edge,
            from: edgeInputName(
              edge as unknown as SimpleEdge,
              prevNode as unknown as SimpleNode,
              submodelsRef.current,
            ),
            to: edgeInputName(
              edge as unknown as SimpleEdge,
              nextNode as unknown as SimpleNode,
              submodelsRef.current,
            ),
          }))
          .filter((change) => change.from !== change.to)
      }

      let tentativeNodes = currentGraph.nodes.map((node) =>
        node.id === id ? { ...node, data } : node,
      )
      const tentativeSubmodels = structuredClone(submodelsRef.current)
      const rootScope: RenameGraphScope = {
        nodes: tentativeNodes,
        edges: tentativeEdges,
        submodels: tentativeSubmodels,
      }
      const nodeById = new Map(tentativeNodes.map((node) => [node.id, node]))
      const affectedByScope = new Map<
        RenameGraphScope,
        Map<string, AffectedRenameTarget>
      >()

      for (const change of rebound) {
        const boundaryTarget = nodeById.get(change.edge.target)
        if (!boundaryTarget) throw new Error(`Cannot derive rename target ${change.edge.target}`)
        const targetScope = rootScope
        const target = boundaryTarget
        if (boundaryTarget.data.nodeType === NODE_TYPES.SUBMODEL) {
          if (!isSubmodelInstanceConfig(boundaryTarget.data.config)) {
            throw new Error(`Submodel instance ${boundaryTarget.id} has malformed identity config`)
          }
          // Public port ids are immutable definition-owned input names, so an
          // external frame rename changes only the parent edge binding.
          continue
        }
        const targets = affectedByScope.get(targetScope) ?? new Map<string, AffectedRenameTarget>()
        const affected = targets.get(target.id) ?? {
          scope: targetScope,
          target,
          incomingScope: rootScope,
          incomingTargetId: boundaryTarget.id,
          pairs: [],
        }
        if (!affected.pairs.some((pair) => pair.from === change.from && pair.to === change.to)) {
          affected.pairs.push({ from: change.from, to: change.to })
        }
        targets.set(target.id, affected)
        affectedByScope.set(targetScope, targets)
      }

      const mappingChanges = new Map<
        RenameGraphScope,
        Map<string, Record<string, unknown>>
      >()

      const applyConfigMapping = (
        scope: RenameGraphScope,
        node: Node,
        field: "input_scenario_map" | "inputMapping",
        pairs: readonly RenamePair[],
        keys: boolean,
      ): OnUpdateConfigResult => {
        const scopeChanges = mappingChanges.get(scope) ?? new Map<string, Record<string, unknown>>()
        const config = scopeChanges.get(node.id) ??
          ((node.data.config ?? {}) as Record<string, unknown>)
        if (keys) {
          const mapped = remapRecordKeys(config[field], pairs)
          if (mapped.collision !== undefined) {
            return {
              ok: false,
              error: `Target "${String(node.data.label ?? node.id)}" already has an input named "${mapped.collision}".`,
            }
          }
          if (mapped.value === config[field]) return { ok: true }
          scopeChanges.set(node.id, { ...config, [field]: mapped.value })
          mappingChanges.set(scope, scopeChanges)
          return { ok: true }
        }
        const mappedValue = remapRecordValues(config[field], pairs)
        if (mappedValue === config[field]) return { ok: true }
        scopeChanges.set(node.id, { ...config, [field]: mappedValue })
        mappingChanges.set(scope, scopeChanges)
        return { ok: true }
      }

      // Directly affected targets receive the new frame name in the fields
      // that use an input identity: live-switch keys and instance values.
      for (const targets of affectedByScope.values()) {
        for (const affected of targets.values()) {
          if (affected.target.data.nodeType === NODE_TYPES.LIVE_SWITCH) {
            const result = applyConfigMapping(
              affected.scope,
              affected.target,
              "input_scenario_map",
              affected.pairs,
              true,
            )
            if (!result.ok) return result
          }
          const valueResult = applyConfigMapping(
            affected.scope,
            affected.target,
            "inputMapping",
            affected.pairs,
            false,
          )
          if (!valueResult.ok) return valueResult
        }
      }

      // The original node's input names are the keys in every instance's
      // mapping. Rename those keys on all visible instances of each affected
      // original, including instances that have no direct renamed edge.
      const instanceScopes = new Set<RenameGraphScope>([rootScope])
      for (const scope of affectedByScope.keys()) instanceScopes.add(scope)
      for (const targets of affectedByScope.values()) {
        for (const affected of targets.values()) {
          for (const scope of instanceScopes) {
            for (const node of scope.nodes) {
              const config = (node.data.config ?? {}) as Record<string, unknown>
              if (config.instanceOf !== affected.target.id) continue
              const result = applyConfigMapping(
                scope,
                node,
                "inputMapping",
                affected.pairs,
                true,
              )
              if (!result.ok) return result
            }
          }
        }
      }

      // Apply all mapping changes only after every scope has passed its
      // collision checks. Nested submodel graph arrays are updated in place
      // so the cloned metadata retains the same graph references.
      for (const [scope, changes] of mappingChanges) {
        const mappedNodes = scope.nodes.map((node) => {
          const config = changes.get(node.id)
          return config ? { ...node, data: { ...node.data, config } } : node
        })
        scope.nodes.splice(0, scope.nodes.length, ...mappedNodes)
      }
      tentativeNodes = rootScope.nodes
      nodeById.clear()
      for (const node of tentativeNodes) nodeById.set(node.id, node)

      const targetInputCollisionFor = (affected: AffectedRenameTarget): string | null => {
        const names = incomingEdgeInputNames({
          targetNodeId: affected.target.id,
          boundaryNodeId: affected.incomingTargetId,
          nodes: affected.incomingScope.nodes as unknown as SimpleNode[],
          edges: affected.incomingScope.edges as unknown as SimpleEdge[],
          submodels: affected.incomingScope.submodels,
        })
        if (affected.scope !== affected.incomingScope) {
          names.push(...incomingEdgeInputNames({
            targetNodeId: affected.target.id,
            nodes: affected.scope.nodes as unknown as SimpleNode[],
            edges: affected.scope.edges as unknown as SimpleEdge[],
            submodels: affected.scope.submodels,
          }))
        }
        const seen = new Set<string>()
        for (const name of names) {
          if (seen.has(name)) return name
          seen.add(name)
        }
        return null
      }

      // Only targets whose incoming edge identity changed need duplicate
      // preflight. Names come from the shared edgeInputName helper, exactly as
      // the executor and panel surfaces derive them.
      for (const targets of affectedByScope.values()) {
        for (const affected of targets.values()) {
          const collision = targetInputCollisionFor(affected)
          if (collision !== null) {
            return {
              ok: false,
              error: `Target "${String(affected.target.data.label ?? affected.target.id)}" already has an input named "${collision}".`,
            }
          }
        }
      }

      // One history-aware store action commits the config, all migrated node
      // mappings, and all rebound/pruned edges together.
      graphRef.current = { nodes: tentativeNodes, edges: tentativeEdges }
      setNodesAndEdgesAndSubmodels(tentativeNodes, tentativeEdges, tentativeSubmodels)
      submodelsRef.current = tentativeSubmodels
      setSelectedNode((prev) => (prev && prev.id === id ? { ...prev, data } : prev))

      if (removed.length > 0) {
        const label = String(data.label ?? id)
        addToast(
          "warning",
          `Disconnected ${removed.length} edge${removed.length === 1 ? "" : "s"} from ${label}: the source ${removed.length === 1 ? "frame no longer exists" : "frames no longer exist"} after your edit.`,
        )
      }
      return { ok: true }
    },
    [activeSubmodelReadOnly, setNodesAndEdgesAndSubmodels, graphRef, addToast, setSelectedNode, submodelsRef],
  )

  const {
    handleDeleteNode, handleDuplicateNode,
    handleCreateInstance, handleRenameNode, handleAutoLayout, isAutoLayouting,
  } = useNodeHandlers({
    graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setNodesAndEdges, setSelectedNode,
    setLastSelectedId,
    setPreviewData, fitView,
    commitSharedNodeDeletion,
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
      if (element.closest?.(".react-flow__handle, .react-flow__node")) return null
      const edgeElement = element.closest?.(".react-flow__edge[data-id]")
      const edgeId = edgeElement?.getAttribute("data-id")
      if (edgeId) return edgeId
    }
    return null
  }, [])

  const isBoundaryConnection = useCallback((connection: Connection | Edge) => {
    if (!activeSubmodelName) return false
    return graphRef.current.nodes.some(
      (node) =>
        (node.id === connection.source || node.id === connection.target)
        && nodeData(node).nodeType === NODE_TYPES.SUBMODEL_PORT,
    )
  }, [activeSubmodelName, graphRef])

  const isValidConnection = useCallback((connection: Connection | Edge) => {
    if (activeSubmodelReadOnly) return false
    if (isBoundaryConnection(connection)) return true
    return validatePipelineConnection(
      connection,
      panelGraph.allNodes,
      panelGraph.edges,
      submodelsRef.current,
    ).ok
  }, [activeSubmodelReadOnly, isBoundaryConnection, panelGraph])

  const validateConnection = useCallback((connection: Connection): ConnectionValidationResult => {
    if (activeSubmodelReadOnly) {
      return {
        ok: false,
        reason: { kind: "invalid-connection", message: "This submodel instance is read-only." },
      }
    }
    if (isBoundaryConnection(connection)) return { ok: true }
    return validatePipelineConnection(
      connection,
      panelGraph.allNodes,
      panelGraph.edges,
      submodelsRef.current,
    )
  }, [activeSubmodelReadOnly, isBoundaryConnection, panelGraph])

  const {
    onConnect, onSelectionChange, onNodeClick, handleDeleteEdge,
    onConnectStart, onConnectEnd, onConnectionPointerMove, clearEdgeJoinCandidate,
    edgeJoinCandidateEdgeId, onNodeContextMenu, onDragOver, onDrop,
  } = useEdgeHandlers({
    selectedNode, graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setEdges, setNodesRaw, setEdgesRaw, pushSnapshot,
    setSelectedNode, setPreviewData, setContextMenu,
    setLastSelectedId,
    fetchPreview,
    cancelPreview,
    shouldSkipAutomaticPreview,
    clearTrace,
    screenToFlowPosition,
    graphRefreshingRef,
    findEdgeIdAtPoint,
    validateConnection,
    commitBoundaryConnection,
    deleteBoundaryEdge,
  })

  const presentedEdgeJoinCandidateEdgeId = useMemo(
    () => (
      !activeSubmodelReadOnly && edgeJoinCandidateEdgeId
        && edgesWithTrace.some((edge) => edge.id === edgeJoinCandidateEdgeId)
        ? edgeJoinCandidateEdgeId
        : null
    ),
    [activeSubmodelReadOnly, edgeJoinCandidateEdgeId, edgesWithTrace],
  )
  const edgesWithEdgeJoinCandidate = useMemo(
    () => withEdgeJoinInsertionCandidate(edgesWithTrace, presentedEdgeJoinCandidateEdgeId),
    [edgesWithTrace, presentedEdgeJoinCandidateEdgeId],
  )

  const handleSwapEdgeJoinInputs = useCallback((nodeId: string) => {
    if (activeSubmodelReadOnly) return
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
    setLastSelectedId(selected?.id ?? null)
    lastSelectedNodeRef.current = selected
    clearTrace()
    cancelPreview()
  }, [
    activeSubmodelReadOnly,
    addToast,
    cancelPreview,
    clearTrace,
    graphRef,
    lastSelectedNodeRef,
    pushSnapshot,
    setEdgesRaw,
    setLastSelectedId,
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

  const submodelsSnapshot = submodels
  const useLiteGraphEffects = shouldUseLiteGraphEffects(nodes.length, edges.length)

  // Pick the preview pane for the active node. Computed here (not as an inline
  // Keep the preview derivation in this render scope so it shares the same
  // metadata snapshot as the panel graph.
  const activeNodeId = activePanelNodeId
  const activePreviewData = previewForActiveNode(previewData, activeNodeId)
  const activeNode = panelGraph.getNode(activeNodeId)
  let dataPreviewContent: ReactNode
  if (activeNode && nodeData(activeNode).nodeType === NODE_TYPES.EXPLORE) {
    dataPreviewContent = (
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
  } else {
    const modelPreview = activeNodeId ? getModellingPreview(activeNodeId) : null
    const optPreview = activeNodeId ? getOptimiserPreview(activeNodeId) : null
    if (modelPreview) {
      dataPreviewContent = <ModellingPreview data={modelPreview} nodeId={activeNodeId!} />
    } else if (optPreview) {
      dataPreviewContent = (
        <Suspense fallback={null}>
          <OptimiserPreview
            data={optPreview}
            nodeId={activeNodeId!}
            allNodes={panelGraph.allNodes}
            edges={panelGraph.edges}
          />
        </Suspense>
      )
    } else if (
      // Pre-solve chart view for optimiser nodes
      activeNode &&
      nodeData(activeNode).nodeType === NODE_TYPES.OPTIMISER &&
      activePreviewData &&
      activePreviewData.status === "ok" &&
      activePreviewData.preview.length > 0
    ) {
      dataPreviewContent = (
        <OptimiserDataPreview
          data={activePreviewData}
          config={nodeData(activeNode).config ?? {}}
        />
      )
    } else {
      dataPreviewContent = (
        <DataPreview
          data={activePreviewData}
          nodeType={activeNode ? nodeData(activeNode).nodeType : undefined}
          onCellClick={handleCellClick}
          tracedCell={tracedCell}
          onSelectFrame={
            activeNodeId ? (portLabel) => previewNodeFrame(activeNodeId, portLabel) : undefined
          }
        />
      )
    }
  }
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
        onOpenUtility={() => { setUtilityOpen(true); setSelectedNode(null); setLastSelectedId(null); lastSelectedNodeRef.current = null; setPreviewDataRef.current(null); setContextMenu(null) }}
        onOpenImports={() => { setImportsOpen(true); setSelectedNode(null); setLastSelectedId(null); lastSelectedNodeRef.current = null; setPreviewDataRef.current(null); setContextMenu(null) }}
        onCentre={() => fitView({ padding: 0.15 })}
        onAutoLayout={handleAutoLayout}
        isAutoLayouting={isAutoLayouting}
        onSave={requestSave}
        onSaveCommit={requestCommit}
        wsStatus={wsStatus}
        timings={previewData?.timings}
        memory={previewData?.memory}
        editingDisabled={activeSubmodelReadOnly}
      />

      {comparison ? (
        <div className="flex-1 flex min-h-0">
          <main className="flex-1 flex flex-col min-w-0">
            <ErrorBoundary name="ComparisonView">
              <Suspense fallback={null}>
                <ComparisonView
                  key={comparison.sha}
                  comparison={comparison}
                  currentNodes={nodes}
                  currentEdges={edges}
                  onClose={exitComparison}
                  onSelectNode={(p) => {
                    setComparisonInspectState(
                      p ? { comparisonSha: comparison.sha, inspect: p } : null,
                    )
                    setGitOpen(false)
                  }}
                />
              </Suspense>
            </ErrorBoundary>
          </main>
          {/* The sidepane is ALWAYS present in compare mode so the canvases never
              resize as you click around. It shows the read-only config inspector
              while a node is selected, otherwise the version-control panel — which
              anchors the whole compare experience. Clicking blank canvas (or the
              inspector ×) deselects → the VC panel returns. The toolbar commit
              indicator force-opens the VC panel (gitOpen wins), S11. */}
          <aside aria-label="Comparison sidepane">
            <ErrorBoundary name="ComparisonSidepane">
              <Suspense fallback={null}>
                {comparisonInspect && !gitOpen ? (
                  <ComparisonInspector
                    key={comparisonInspect.id}
                    inspect={comparisonInspect}
                    onClose={() => setComparisonInspectState(null)}
                  />
                ) : (
                  <GitPanel onClose={exitComparison} onSave={handleSave} />
                )}
              </Suspense>
            </ErrorBoundary>
          </aside>
        </div>
      ) : (
      <div className="flex-1 flex min-h-0">
        <nav
          aria-label="Node palette"
          aria-disabled={activeSubmodelReadOnly}
          inert={activeSubmodelReadOnly ? true : undefined}
          style={activeSubmodelReadOnly ? { opacity: 0.45 } : undefined}
        >
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
              <button onClick={() => setSyncBanner(null)} className="opacity-60 hover:opacity-100">✕</button>
            </div>
          )}
          <ErrorBoundary name="Canvas">
            <div
              className="flex-1 min-h-0 relative"
              onPointerMove={(event) => { if (!activeSubmodelReadOnly) onConnectionPointerMove(event) }}
              onPointerLeave={clearEdgeJoinCandidate}
            >
              <BreadcrumbBar viewStack={viewStack} onNavigate={handleBreadcrumbNavigate} />
              <EdgeJoinInsertionFeedback candidateEdgeId={presentedEdgeJoinCandidateEdgeId} />
              <ReactFlow
                className={useLiteGraphEffects ? "graph-effects-lite" : undefined}
                nodes={canvasNodes}
                edges={edgesWithEdgeJoinCandidate}
                onNodesChange={handleNodesChange}
                onEdgesChange={handleEdgesChange}
                onConnect={activeSubmodelReadOnly ? undefined : onConnect}
                onConnectStart={activeSubmodelReadOnly ? undefined : onConnectStart}
                onConnectEnd={activeSubmodelReadOnly ? undefined : onConnectEnd}
                nodesDraggable={!activeSubmodelReadOnly}
                nodesConnectable={!activeSubmodelReadOnly}
                onSelectionChange={onSelectionChange}
                onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
                onNodeMouseLeave={() => setHoveredNodeId(null)}
                onNodeClick={(event, node) => { setUtilityOpen(false); setImportsOpen(false); setGitOpen(false); setHoveredNodeId(null); onNodeClick(event, node) }}
                onNodeContextMenu={activeSubmodelReadOnly ? undefined : onNodeContextMenu}
                onNodeDoubleClick={(_event, node) => {
                  if (nodeData(node).nodeType === NODE_TYPES.SUBMODEL) {
                    const config = nodeData(node).config
                    if (isSubmodelInstanceConfig(config) && config.instanceOf !== undefined) {
                      setUtilityOpen(false)
                      setImportsOpen(false)
                      setAssistantOpen(false)
                      setContextMenu(null)
                      setRenameDialog(null)
                      setSubmodelDialog(null)
                    }
                    handleDrillIntoSubmodel(node.id)
                  }
                }}
                onPaneClick={() => { setContextMenu(null); clearTrace(); closePanel() }}
                onDrop={activeSubmodelReadOnly ? undefined : onDrop}
                onDragOver={activeSubmodelReadOnly ? undefined : onDragOver}
                nodeTypes={nodeTypes}
                panOnDrag={[2]}
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
              </ReactFlow>
            </div>
          </ErrorBoundary>

          <ErrorBoundary name="DataPreview">
            {dataPreviewContent}
          </ErrorBoundary>
        </main>

        <aside aria-label="Node properties">
          <ErrorBoundary name="NodePanel">
            {gitOpen ? (
              <Suspense fallback={null}>
                <GitPanel onClose={() => setGitOpen(false)} onSave={handleSave} />
              </Suspense>
            ) : utilityOpen ? (
              <Suspense fallback={null}>
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
              </Suspense>
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
            ) : assistantOpen ? (
              <ErrorBoundary name="AssistantPanel">
                <Suspense fallback={null}>
                  <AssistantPanel
                    isInsideSubmodel={viewStack.length > 1}
                    currentSourceFile={currentSourceFile}
                  />
                </Suspense>
              </ErrorBoundary>
            ) : traceResult ? (
              <TracePanel trace={traceResult} onClose={clearTrace} />
            ) : traceState.status === "error" || (traceState.status === "loading" && traceState.progressVisible) ? (
              <TraceStatePanel state={traceState} onCancel={cancelTrace} onRetry={retryTrace} onClose={clearTrace} />
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
                  onDeleteEdge={activeSubmodelReadOnly ? undefined : handleDeleteEdge}
                  onSwapEdgeJoinInputs={activeSubmodelReadOnly ? undefined : handleSwapEdgeJoinInputs}
                  readOnly={activeSubmodelReadOnly}
                  onRefreshPreview={() => {
                    if (!panelNode) return
                    const refreshTarget = graphRef.current.nodes.find((n) => n.id === panelNode.id)
                    if (refreshTarget) refreshPreview(refreshTarget)
                  }}
                  dimmed={!selectedNode && !!activePanelNodeId}
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
      )}

      {contextMenu && !activeSubmodelReadOnly && (
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
          onDissolveSubmodel={handleDissolveSubmodel}
        />
      )}

      {shortcutsOpen && <KeyboardShortcuts onClose={() => setShortcutsOpen(false)} />}

      {gitModal === "select" && (
        <Suspense fallback={null}>
          <WorkingBranchModal onConfirmed={handleGitModalConfirmed} onClose={closeGitModal} />
        </Suspense>
      )}

      {gitModal === "divergence" && (
        <Suspense fallback={null}>
          <DivergenceModal onConfirmed={handleGitModalConfirmed} onClose={closeGitModal} />
        </Suspense>
      )}

      {gitModal === "milestone" && (
        <Suspense fallback={null}>
          <MilestoneCommitModal onConfirmed={closeGitModal} onClose={closeGitModal} />
        </Suspense>
      )}

      {moveTarget && (
        <Suspense fallback={null}>
          <MoveConfirmModal onConfirm={handleMoveConfirmed} onClose={closeMove} />
        </Suspense>
      )}

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
        <Suspense fallback={null}>
          <NodeSearch
            onClose={() => setNodeSearchOpen(false)}
            onSelectNode={(nodeId) => {
              const node = graphRef.current.nodes.find((n) => n.id === nodeId) ?? null
              if (node) {
                setSelectedNode(node)
                setLastSelectedId(node.id)
                lastSelectedNodeRef.current = node
                setUtilityOpen(false)
                setImportsOpen(false)
                setGitOpen(false)
              }
            }}
          />
        </Suspense>
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
