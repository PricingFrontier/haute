import { useEffect, useCallback, useMemo, useState, useRef, lazy, Suspense } from "react"
import type { ComponentProps, ReactNode } from "react"
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
import type { OnUpdateConfigResult, SimpleEdge, SimpleNode } from "./panels/editors/_shared"
import { GraphProvider } from "./panels/GraphContext"
import DataPreview, { type PreviewData } from "./panels/DataPreview"
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
import PipelineLoadFailureView from "./components/PipelineLoadFailureView"
import PipelineRecoveryBanner from "./components/PipelineRecoveryBanner"
import PipelineRepairDialog, { type PipelineRepairTarget } from "./components/PipelineRepairDialog"
import SourceRecoveryView from "./components/SourceRecoveryView"
import StalePipelineReferenceBanner from "./components/StalePipelineReferenceBanner"
import ImportsPanel from "./panels/ImportsPanel"
import type { ComparisonInspect } from "./components/ComparisonView"
import EdgeJoinInsertionFeedback from "./components/EdgeJoinInsertionFeedback"
import { withEdgeJoinInsertionCandidate } from "./utils/edgeJoinInsertionFeedback"

import useGraphCanvasState from "./hooks/useGraphCanvasState"
import useWebSocketSync from "./hooks/useWebSocketSync"
import usePipelineAPI from "./hooks/usePipelineAPI"
import useTracing, { type TraceRequestState } from "./hooks/useTracing"
import useSubmodelNavigation from "./hooks/useSubmodelNavigation"
import useSubmodelBoundaryEditing from "./hooks/useSubmodelBoundaryEditing"
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts"
import useNodeHandlers from "./hooks/useNodeHandlers"
import useEdgeHandlers from "./hooks/useEdgeHandlers"
import usePanelGraphContext from "./hooks/usePanelGraphContext"
import type { PanelGraphContextSnapshot } from "./hooks/usePanelGraphContext"
import useGraphCommitController from "./hooks/useGraphCommitController"
import useSettingsStore from "./stores/useSettingsStore"
import useUIStore from "./stores/useUIStore"
import useGraphStore from "./stores/useGraphStore"
import useGitStore from "./stores/useGitStore"
import useToastStore from "./stores/useToastStore"
import useNodeResultsStore from "./stores/useNodeResultsStore"
import useDocumentStatusStore from "./stores/useDocumentStatusStore"
import { HAUTE_SESSION_EXPIRED_EVENT } from "./api/client"

import {
  NODE_TYPES,
  isSingletonType,
  singletonTypesInDocument,
  singletonTypesInSubmodelDefinition,
} from "./utils/nodeTypes"
import { previewForActiveNode } from "./utils/activePreview"
import { swapEdgeJoinInputs, type EdgeJoinSwapInputsFailureReason } from "./utils/edgeJoinGraph"
import { validatePipelineConnection, type ConnectionValidationResult } from "./utils/connectionValidation"
import { shouldUseLiteGraphEffects } from "./utils/graphPerformance"
import type { DrilledOccurrenceIdentity } from "./utils/submodelRuntimeTarget"
import { isSubmodelInstanceConfig, nodeData } from "./types/node"
import { withNativeDeletePolicy } from "./utils/submodelDeletionPolicy"
import { requestSubmodelCreation } from "./utils/submodelCreation"
import { resolveEditorGraphIdentities } from "./utils/editorIdentities"
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
const StorageBindModal = lazy(() => import("./components/StorageBindModal"))
const UpstreamSyncModal = lazy(() => import("./components/UpstreamSyncModal"))
const IdentityPromptModal = lazy(() => import("./components/IdentityPromptModal"))
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

type NodeResultsState = ReturnType<typeof useNodeResultsStore.getState>
type NodeContextMenuState = {
  x: number
  y: number
  nodeId: string
  nodeLabel: string
  isSubmodel?: boolean
  isSubmodelCopy?: boolean
  isSingleton?: boolean
}

type ActiveNodePreviewProps = {
  documentCanExecute: boolean
  activeNodeId: string | null
  activeNode: SimpleNode | null
  panelNodes: SimpleNode[]
  panelEdges: SimpleEdge[]
  submodels: Record<string, unknown>
  preamble: string
  previewData: PreviewData | null
  getModellingPreview: NodeResultsState["getModellingPreview"]
  getOptimiserPreview: NodeResultsState["getOptimiserPreview"]
  onCellClick: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell: { rowIndex: number; column: string } | null
  previewNodeFrame: (nodeId: string, portLabel: string) => unknown
}

function ActiveNodePreview({
  documentCanExecute,
  activeNodeId,
  activeNode,
  panelNodes,
  panelEdges,
  submodels,
  preamble,
  previewData,
  getModellingPreview,
  getOptimiserPreview,
  onCellClick,
  tracedCell,
  previewNodeFrame,
}: ActiveNodePreviewProps) {
  if (
    documentCanExecute
    && activeNode
    && nodeData(activeNode).nodeType === NODE_TYPES.EXPLORE
  ) {
    return (
      <ExplorePreview
        node={activeNode}
        allNodes={panelNodes}
        edges={panelEdges}
        submodels={submodels}
        preamble={preamble}
        previewData={previewData}
        onCellClick={onCellClick}
        tracedCell={tracedCell}
      />
    )
  }

  const modellingPreview = activeNodeId ? getModellingPreview(activeNodeId) : null
  if (documentCanExecute && modellingPreview) {
    return <ModellingPreview data={modellingPreview} nodeId={activeNodeId!} />
  }
  const optimiserPreview = activeNodeId ? getOptimiserPreview(activeNodeId) : null
  if (documentCanExecute && optimiserPreview) {
    return (
      <Suspense fallback={null}>
        <OptimiserPreview
          data={optimiserPreview}
          nodeId={activeNodeId!}
          allNodes={panelNodes}
          edges={panelEdges}
        />
      </Suspense>
    )
  }
  if (
    documentCanExecute
    && activeNode
    && nodeData(activeNode).nodeType === NODE_TYPES.OPTIMISER
    && previewData?.status === "ok"
    && previewData.preview.length > 0
  ) {
    return (
      <OptimiserDataPreview
        data={previewData}
        config={nodeData(activeNode).config ?? {}}
      />
    )
  }
  return (
    <DataPreview
      data={previewData}
      nodeType={activeNode ? nodeData(activeNode).nodeType : undefined}
      onCellClick={documentCanExecute ? onCellClick : undefined}
      tracedCell={tracedCell}
      onSelectFrame={
        activeNodeId ? (portLabel) => previewNodeFrame(activeNodeId, portLabel) : undefined
      }
    />
  )
}

type FlowEditorOverlaysProps = {
  editingReadOnly: boolean
  contextMenu: NodeContextMenuState | null
  setContextMenu: (menu: NodeContextMenuState | null) => void
  onDeleteNode: ComponentProps<typeof ContextMenu>["onDelete"]
  onDuplicateNode: ComponentProps<typeof ContextMenu>["onDuplicate"]
  onRenameNodeMenu: ComponentProps<typeof ContextMenu>["onRename"]
  onCreateInstance: ComponentProps<typeof ContextMenu>["onCreateInstance"]
  onDissolveSubmodel: ComponentProps<typeof ContextMenu>["onDissolveSubmodel"]
  onGitModalConfirmed: () => void
  onSave: () => Promise<boolean>
  onMoveConfirmed: (saveFirst: boolean) => Promise<void>
  onCreateSubmodel: (name: string, nodeIds: string[]) => void
  onRenameNode: (nodeId: string, label: string) => Promise<OnUpdateConfigResult>
  pipelineRepairTarget: PipelineRepairTarget | null
  documentSourceFile: string
  documentSourceRevision: string | null
  onClosePipelineRepair: () => void
  onRepairApplied: ComponentProps<typeof PipelineRepairDialog>["onApplied"]
  onNodeSearchSelect: (nodeId: string) => void
}

function FlowEditorOverlays({
  editingReadOnly,
  contextMenu,
  setContextMenu,
  onDeleteNode,
  onDuplicateNode,
  onRenameNodeMenu,
  onCreateInstance,
  onDissolveSubmodel,
  onGitModalConfirmed,
  onSave,
  onMoveConfirmed,
  onCreateSubmodel,
  onRenameNode,
  pipelineRepairTarget,
  documentSourceFile,
  documentSourceRevision,
  onClosePipelineRepair,
  onRepairApplied,
  onNodeSearchSelect,
}: FlowEditorOverlaysProps) {
  const shortcutsOpen = useUIStore((state) => state.shortcutsOpen)
  const setShortcutsOpen = useUIStore((state) => state.setShortcutsOpen)
  const submodelDialog = useUIStore((state) => state.submodelDialog)
  const setSubmodelDialog = useUIStore((state) => state.setSubmodelDialog)
  const renameDialog = useUIStore((state) => state.renameDialog)
  const setRenameDialog = useUIStore((state) => state.setRenameDialog)
  const nodeSearchOpen = useUIStore((state) => state.nodeSearchOpen)
  const setNodeSearchOpen = useUIStore((state) => state.setNodeSearchOpen)
  const gitModal = useGitStore((state) => state.modal)
  const closeGitModal = useGitStore((state) => state.closeModal)
  const moveTarget = useGitStore((state) => state.moveTarget)
  const closeMove = useGitStore((state) => state.closeMove)

  return (
    <>
      {contextMenu && !editingReadOnly && (
        <ContextMenu
          {...contextMenu}
          onClose={() => setContextMenu(null)}
          onDelete={onDeleteNode}
          onDuplicate={onDuplicateNode}
          onRename={onRenameNodeMenu}
          onCreateInstance={onCreateInstance}
          onDissolveSubmodel={onDissolveSubmodel}
        />
      )}
      {shortcutsOpen && <KeyboardShortcuts onClose={() => setShortcutsOpen(false)} />}
      {gitModal === "select" && (
        <Suspense fallback={null}>
          <WorkingBranchModal onConfirmed={onGitModalConfirmed} onClose={closeGitModal} />
        </Suspense>
      )}
      {gitModal === "divergence" && (
        <Suspense fallback={null}>
          <DivergenceModal onConfirmed={onGitModalConfirmed} onClose={closeGitModal} />
        </Suspense>
      )}
      {gitModal === "milestone" && (
        <Suspense fallback={null}>
          <MilestoneCommitModal onConfirmed={closeGitModal} onClose={closeGitModal} />
        </Suspense>
      )}
      {gitModal === "storage" && (
        <Suspense fallback={null}>
          <StorageBindModal onClose={closeGitModal} />
        </Suspense>
      )}
      {gitModal === "upstream" && (
        <Suspense fallback={null}>
          <UpstreamSyncModal onClose={closeGitModal} />
        </Suspense>
      )}
      {gitModal === "identity" && (
        <Suspense fallback={null}>
          <IdentityPromptModal onSaved={() => void onSave()} onClose={closeGitModal} />
        </Suspense>
      )}
      {moveTarget && (
        <Suspense fallback={null}>
          <MoveConfirmModal onConfirm={onMoveConfirmed} onClose={closeMove} />
        </Suspense>
      )}
      {submodelDialog && (
        <SubmodelDialog
          nodeCount={submodelDialog.nodeIds.length}
          onClose={() => setSubmodelDialog(null)}
          onSubmit={(name) => {
            onCreateSubmodel(name, submodelDialog.nodeIds)
            setSubmodelDialog(null)
          }}
        />
      )}
      {renameDialog && (
        <RenameDialog
          defaultValue={renameDialog.currentLabel}
          onCancel={() => setRenameDialog(null)}
          onConfirm={async (newName) => {
            const result = await onRenameNode(renameDialog.nodeId, newName)
            if (result.ok) setRenameDialog(null)
            return result
          }}
        />
      )}
      {pipelineRepairTarget && (
        <PipelineRepairDialog
          key={`${documentSourceRevision ?? ""}:${pipelineRepairTarget.sourceFile}:${pipelineRepairTarget.recoveryId}`}
          target={pipelineRepairTarget}
          sourceFile={documentSourceFile}
          sourceRevision={documentSourceRevision ?? ""}
          onClose={onClosePipelineRepair}
          onApplied={onRepairApplied}
        />
      )}
      {nodeSearchOpen && (
        <Suspense fallback={null}>
          <NodeSearch
            onClose={() => setNodeSearchOpen(false)}
            onSelectNode={onNodeSearchSelect}
          />
        </Suspense>
      )}
    </>
  )
}

type NodePropertiesPanelProps = {
  gitOpen: boolean
  utilityOpen: boolean
  importsOpen: boolean
  assistantOpen: boolean
  onCloseGit: () => void
  onCloseUtility: () => void
  onCloseImports: () => void
  onSave: () => Promise<boolean>
  preamble: string
  onImportAdded: (importLine: string) => void
  onPreambleChange: (value: string) => void
  isInsideSubmodel: boolean
  currentSourceFile: string | null
  documentReadOnly: boolean
  traceResult: ComponentProps<typeof TracePanel>["trace"] | null
  traceState: TraceRequestState
  clearTrace: () => void
  cancelTrace: ComponentProps<typeof TraceStatePanel>["onCancel"]
  retryTrace: ComponentProps<typeof TraceStatePanel>["onRetry"]
  panelGraph: PanelGraphContextSnapshot
  submodels: Record<string, unknown>
  panelNode: SimpleNode | null
  onUpdateNode: NonNullable<ComponentProps<typeof NodePanel>["onUpdateNode"]>
  onRenameNode: NonNullable<ComponentProps<typeof NodePanel>["onRenameNode"]>
  onDeleteEdge?: ComponentProps<typeof NodePanel>["onDeleteEdge"]
  onDeleteSubmodelInputPort?: ComponentProps<typeof NodePanel>["onDeleteSubmodelInputPort"]
  onSwapEdgeJoinInputs?: ComponentProps<typeof NodePanel>["onSwapEdgeJoinInputs"]
  editingReadOnly: boolean
  onRefreshPreview: () => void
  selectedNode: Node | null
  activePanelNodeId: string | null
  previewData: PreviewData | null
  previewBusy: boolean
  onClosePanel: () => void
  onRemoveUnavailableNode: NonNullable<ComponentProps<typeof NodePanel>["onRemoveUnavailableNode"]>
}

function NodePropertiesPanel({
  gitOpen,
  utilityOpen,
  importsOpen,
  assistantOpen,
  onCloseGit,
  onCloseUtility,
  onCloseImports,
  onSave,
  preamble,
  onImportAdded,
  onPreambleChange,
  isInsideSubmodel,
  currentSourceFile,
  documentReadOnly,
  traceResult,
  traceState,
  clearTrace,
  cancelTrace,
  retryTrace,
  panelGraph,
  submodels,
  panelNode,
  onUpdateNode,
  onRenameNode,
  onDeleteEdge,
  onDeleteSubmodelInputPort,
  onSwapEdgeJoinInputs,
  editingReadOnly,
  onRefreshPreview,
  selectedNode,
  activePanelNodeId,
  previewData,
  previewBusy,
  onClosePanel,
  onRemoveUnavailableNode,
}: NodePropertiesPanelProps) {
  const visibleTraceState = traceState.status === "error"
    || (traceState.status === "loading" && traceState.progressVisible)
    ? traceState
    : null
  let content: ReactNode
  if (gitOpen) {
    content = (
      <Suspense fallback={null}>
        <GitPanel onClose={onCloseGit} onSave={onSave} />
      </Suspense>
    )
  } else if (utilityOpen) {
    content = (
      <Suspense fallback={null}>
        <UtilityPanel onClose={onCloseUtility} onImportAdded={onImportAdded} />
      </Suspense>
    )
  } else if (importsOpen) {
    content = (
      <ImportsPanel
        preamble={preamble}
        onPreambleChange={onPreambleChange}
        onClose={onCloseImports}
      />
    )
  } else if (assistantOpen) {
    content = (
      <ErrorBoundary name="AssistantPanel">
        <Suspense fallback={null}>
          <AssistantPanel
            isInsideSubmodel={isInsideSubmodel}
            currentSourceFile={currentSourceFile}
            readOnly={documentReadOnly}
          />
        </Suspense>
      </ErrorBoundary>
    )
  } else if (traceResult) {
    content = <TracePanel trace={traceResult} onClose={clearTrace} />
  } else if (visibleTraceState) {
    content = (
      <TraceStatePanel
        state={visibleTraceState}
        onCancel={cancelTrace}
        onRetry={retryTrace}
        onClose={clearTrace}
      />
    )
  } else {
    content = (
      <GraphProvider
        allNodes={panelGraph.allNodes}
        edges={panelGraph.edges}
        submodels={submodels}
        preamble={preamble}
      >
        <NodePanel
          node={panelNode}
          onClose={onClosePanel}
          onUpdateNode={onUpdateNode}
          onRenameNode={onRenameNode}
          onDeleteEdge={onDeleteEdge}
          onDeleteSubmodelInputPort={onDeleteSubmodelInputPort}
          onSwapEdgeJoinInputs={onSwapEdgeJoinInputs}
          readOnly={editingReadOnly}
          documentReadOnly={documentReadOnly}
          onRefreshPreview={onRefreshPreview}
          dimmed={!selectedNode && !!activePanelNodeId}
          errorLine={
            previewData?.nodeId === activePanelNodeId
              ? previewData.error_line ?? null
              : null
          }
          previewRows={
            previewData?.status === "ok" && previewData.nodeId === activePanelNodeId
              ? previewData.preview
              : undefined
          }
          selectedPreviewLoading={previewBusy && selectedNode?.id === activePanelNodeId}
          onRemoveUnavailableNode={onRemoveUnavailableNode}
        />
      </GraphProvider>
    )
  }
  return (
    <aside aria-label="Node properties">
      <ErrorBoundary name="NodePanel">{content}</ErrorBoundary>
    </aside>
  )
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
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  // Git working-branch model (P2)
  const loadGitReadiness = useGitStore((s) => s.loadStatus)
  // Read-only comparison view (S11): when set, the dual-canvas overlay replaces
  // the editor's content row (the toolbar stays, remaining interactive).
  const comparison = useGitStore((s) => s.comparison)
  const closeComparison = useGitStore((s) => s.closeComparison)
  // Move-through-history (P6 §3.4): the version queued for a real checkout,
  // pending the pre-move save/discard/confirm prompt.
  const addToast = useToastStore((s) => s.addToast)
  const syncBanner = useUIStore((s) => s.syncBanner)
  const setSyncBanner = useUIStore((s) => s.setSyncBanner)
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId)
  const setHoveredNodeId = useUIStore((s) => s.setHoveredNodeId)
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
  const [contextMenu, setContextMenu] = useState<NodeContextMenuState | null>(null)
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
  const [pipelineRepairTarget, setPipelineRepairTarget] = useState<PipelineRepairTarget | null>(null)

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
  const documentLoadStatus = useDocumentStatusStore((s) => s.loadStatus)
  const documentCapabilities = useDocumentStatusStore((s) => s.capabilities)
  const reservedApiInputFrameLabels = useMemo(
    () => new Set(documentCapabilities?.reserved_api_input_frame_labels ?? []),
    [documentCapabilities?.reserved_api_input_frame_labels],
  )
  const resolveCandidateGraphIdentities = useCallback(
    async (
      candidateNodes: readonly Node[],
      candidateEdges: readonly Edge[],
    ): Promise<{ nodes: Node[]; edges: Edge[] }> => {
      return resolveEditorGraphIdentities({
        nodes: candidateNodes,
        edges: candidateEdges,
        submodels: submodelsRef.current,
        reservedApiInputFrameLabels,
      })
    },
    [reservedApiInputFrameLabels],
  )
  const resolveNodeIdentities = useCallback(
    async (candidateNodes: readonly Node[]): Promise<Node[]> => (
      await resolveCandidateGraphIdentities(candidateNodes, [])
    ).nodes,
    [resolveCandidateGraphIdentities],
  )
  const documentSourceRevision = useDocumentStatusStore((s) => s.sourceRevision)
  const documentSourceFile = useDocumentStatusStore((s) => s.sourceFile)
  const retainedPipelineCanvas = useDocumentStatusStore((s) => s.retainedCanvas)
  const documentGraphSynchronized = useDocumentStatusStore((s) => s.graphSynchronized)
  const documentSystemFailure = useDocumentStatusStore((s) => s.systemFailure)
  const documentSourceSelectionTrusted = useDocumentStatusStore(
    (s) => s.sourceSelectionTrusted,
  )

  // ---------------------------------------------------------------------------
  // Hooks
  // ---------------------------------------------------------------------------

  const {
    loading, loadError, previewData, setPreviewData,
    previewBusy,
    nodeStatuses,
    fetchPreview, cancelPreview, refreshPreview, previewNodeFrame, handleSave, adoptPipelineDocument,
  } = usePipelineAPI({
    selectedNode,
    graphRef, parentGraphRef, activeSubmodelIdentity, submodelsRef,
    setNodesRaw, setEdgesRaw, setSubmodelsRaw, setCurrentSourceFile, setPreamble,
    preambleRef, pipelineNameRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef,
    nodeIdCounter,
  })

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
      if (!st || st.state === "ready" || st.state === "no-repository" || st.state === "git-unavailable" || st.state === "detached") return
      useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select")
    })
  }, [loadGitReadiness, addToast])

  const wsStatus = useWebSocketSync({
    preambleRef, submodelsRef, graphRefreshingRef, sourceFileRef,
    sourceRevisionRef, preservedBlocksRef, nodeIdCounter, fitView,
    enabled: !loading && loadError === null,
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
  const previousDocumentRevisionRef = useRef<string | null>(null)
  useEffect(() => {
    const previousRevision = previousDocumentRevisionRef.current
    if (
      previousRevision !== null &&
      documentSourceRevision !== null &&
      documentSourceRevision !== previousRevision
    ) {
      cancelPreview()
      setPreviewData(null)
      cancelTrace()
      clearTrace()
    }
    previousDocumentRevisionRef.current = documentSourceRevision
  }, [cancelPreview, cancelTrace, clearTrace, documentSourceRevision, setPreviewData])
  const canvasNodes = useMemo(
    () => withNativeDeletePolicy(nodesWithStatus),
    [nodesWithStatus],
  )


  const {
    viewStack,
    handleDrillIntoSubmodel, handleBreadcrumbNavigate,
    resetToAuthoritativeRoot,
    handleCreateSubmodel, handleDissolveSubmodel,
  } = useSubmodelNavigation({
    graphRef, parentGraphRef, setActiveSubmodelIdentity, submodelsRef,
    setNodesRaw, setEdgesRaw, setSubmodelsRaw,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    setLastSelectedId,
    setCurrentSourceFile,
    preambleRef, descriptionRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef, pipelineNameRef,
    fitView,
    reservedApiInputFrameLabels,
  })

  // Drilling replaces the visible graph in the store. The reactive navigation
  // snapshot retains the root graph while a definition is on screen, so the
  // document-wide singleton policy does not depend on the current view.
  const documentRootNodes = viewStack.length > 1
    ? (viewStack[0]._savedNodes ?? nodes)
    : nodes
  const existingSingletonTypes = useMemo(
    () => singletonTypesInDocument(documentRootNodes, submodels),
    [documentRootNodes, submodels],
  )

  const handleRepairApplied = useCallback((
    document: import("./types/pipelineDocument").PipelineEditorDocument,
  ) => {
    adoptPipelineDocument(document)
    resetToAuthoritativeRoot(
      document.source_file,
      document.pipeline_name ?? "main",
    )
    closePanel()
    setPipelineRepairTarget(null)
  }, [adoptPipelineDocument, closePanel, resetToAuthoritativeRoot])

  const activeView = viewStack[viewStack.length - 1]
  const activeSubmodelName = activeView?.type === "submodel" ? activeView.name : null
  const activeSubmodelInstanceId = activeView?.type === "submodel" ? activeView.instanceId ?? null : null
  const activeSubmodelDefinitionId = activeView?.type === "submodel" ? activeView.definitionId ?? null : null
  const activeSubmodelReadOnly = activeView?.type === "submodel" && activeView.readOnly
  const documentReadOnly = documentCapabilities?.can_mutate !== true || !documentGraphSynchronized
  const documentCanExecute = documentCapabilities?.can_execute === true && documentGraphSynchronized
  const editingReadOnly = documentReadOnly || Boolean(activeSubmodelReadOnly)

  const {
    commitBoundaryConnection,
    deleteBoundaryEdge,
    deleteBoundaryInputPort,
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
    reservedApiInputFrameLabels,
  })
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    if (!editingReadOnly) {
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
  }, [editingReadOnly, commitSharedNodeDeletion, onNodesChange])

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (editingReadOnly) {
      const selectionChanges = changes.filter((change) => change.type === "select")
      if (selectionChanges.length > 0) onEdgesChange(selectionChanges)
      return
    }
    if (onBoundaryEdgesChange(changes)) return
    onEdgesChange(changes)
  }, [editingReadOnly, onBoundaryEdgesChange, onEdgesChange])

  // ---------------------------------------------------------------------------
  // Node + edge interaction handlers (extracted to custom hooks)
  // ---------------------------------------------------------------------------

  const readDocumentIdentity = useCallback((): string => {
    const status = useDocumentStatusStore.getState()
    return JSON.stringify([
      status.sourceFile,
      status.sourceRevision,
      status.loadStatus,
      status.capabilities?.can_mutate === true,
      status.graphSynchronized,
    ])
  }, [])

  const { onUpdateNode, onRenameNode, waitForPendingCommits } = useGraphCommitController({
    graphRef,
    submodelsRef,
    readDocumentIdentity,
    readOnly: editingReadOnly,
    reservedApiInputFrameLabels,
    resolveNodeIdentities,
    commitGraph: setNodesAndEdgesAndSubmodels,
    setSelectedNode,
    addToast,
  })

  const saveWithPendingCommits = useCallback(async (): Promise<boolean> => {
    const pending = await waitForPendingCommits()
    if (!pending.ok) {
      addToast("error", pending.error)
      return false
    }
    return handleSave()
  }, [addToast, handleSave, waitForPendingCommits])

  // Flush the editor through the graph-commit fence before opening the
  // milestone modal, so Commit can never capture an older ledger snapshot.
  const flushSaveThenMilestone = useCallback(async () => {
    const ok = await saveWithPendingCommits()
    if (ok) useGitStore.getState().openModal("milestone")
  }, [saveWithPendingCommits])

  // Save-gate: resolve Git readiness before deciding whether to save now or
  // queue the action behind branch/divergence setup.
  const requestSave = useCallback(async () => {
    const st = useGitStore.getState().status ?? (await useGitStore.getState().loadStatus())
    if (st === null || st.state === "no-repository" || st.state === "git-unavailable" || st.state === "ready") {
      void saveWithPendingCommits()
      return
    }
    useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select", {
      pendingAction: "save",
    })
  }, [saveWithPendingCommits])

  // Commit uses the same readiness gate, but a ready repository first flushes
  // the fenced graph and only then opens the milestone modal.
  const requestCommit = useCallback(async () => {
    const st = useGitStore.getState().status ?? (await useGitStore.getState().loadStatus())
    if (st === null) {
      const detail = useGitStore.getState().statusError
      addToast("error", detail ? `Git unavailable: ${detail}` : "Git readiness is unavailable — commit is disabled.")
      return
    }
    if (st.state === "no-repository") {
      addToast("error", "No git repository — commit is unavailable.")
      return
    }
    if (st.state === "git-unavailable") {
      addToast("error", "Git is not available in this environment — commit is unavailable.")
      return
    }
    if (st.state === "ready") {
      void flushSaveThenMilestone()
      return
    }
    useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select", {
      pendingAction: "commit",
    })
  }, [addToast, flushSaveThenMilestone])

  const handleGitModalConfirmed = useCallback(() => {
    const pending = useGitStore.getState().pendingAction
    useGitStore.getState().closeModal()
    if (pending === "save") void saveWithPendingCommits()
    else if (pending === "commit") void flushSaveThenMilestone()
  }, [flushSaveThenMilestone, saveWithPendingCommits])

  // Moving versions replaces the working tree. If requested, park the fenced
  // graph on the current branch first; a failed save keeps the user in place.
  const handleMoveConfirmed = useCallback(
    async (saveFirst: boolean) => {
      const target = useGitStore.getState().moveTarget
      if (!target) return
      try {
        if (saveFirst && !await saveWithPendingCommits()) {
          addToast("error", "Save failed — staying on the current version.")
          useGitStore.getState().closeMove()
          return
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
    [addToast, saveWithPendingCommits],
  )

  useKeyboardShortcuts({
    handleSave: requestSave, setNodes, setEdges, setNodesAndEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    setLastSelectedId,
    clearTrace,
    closePanel,
    isInsideSubmodel: viewStack.length > 1,
    readOnly: editingReadOnly,
    existingSingletonTypes,
    resolveGraphIdentities: resolveCandidateGraphIdentities,
    commitSharedNodeDeletion,
  })

  const {
    handleDeleteNode, handleDuplicateNode,
    handleCreateInstance, handleRenameNode, handleAutoLayout, isAutoLayouting,
  } = useNodeHandlers({
    graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setNodesAndEdges, setSelectedNode,
    setLastSelectedId,
    setPreviewData, fitView,
    submodels,
    resolveNodeIdentities,
    commitSharedNodeDeletion,
  })

  // Toolbar selection actions. These mirror the rules the Ctrl+G shortcut and
  // the node context menu already enforce, so the buttons are a second entry
  // point rather than a second policy: grouping needs 2+ nodes and a context
  // that can hold a submodel (they cannot nest), instancing needs exactly one
  // non-singleton node — the generic `instanceOf` path, not just submodels.
  const selectedNodes = useMemo(
    () => nodes.filter((n) => n.selected),
    [nodes],
  )
  const selectedNodeIds = useMemo(() => selectedNodes.map((n) => n.id), [selectedNodes])
  const selectedSubmodelHasSingleton = useMemo(() => {
    if (selectedNodes.length !== 1) return false
    const data = nodeData(selectedNodes[0])
    if (data.nodeType !== NODE_TYPES.SUBMODEL || !isSubmodelInstanceConfig(data.config)) {
      return false
    }
    return singletonTypesInSubmodelDefinition(data.config.definitionId, submodels).size > 0
  }, [selectedNodes, submodels])
  const canCreateSubmodel = !editingReadOnly
    && viewStack.length <= 1
    && selectedNodeIds.length >= 2
  const canCreateInstance = !editingReadOnly
    && selectedNodes.length === 1
    && !isSingletonType(nodeData(selectedNodes[0]).nodeType)
    && !selectedSubmodelHasSingleton
  // The `can*` flags above drive presentation only. The request paths below
  // enforce policy AND say why they refused, exactly as Ctrl+G does — a
  // toolbar button that swallows the click in silence is the one case where the
  // user most needs the explanation, and the `title` carrying it needs a hover
  // dwell the keyboard and touch never perform.
  const handleToolbarCreateSubmodel = useCallback(() => {
    requestSubmodelCreation({
      nodes,
      readOnly: editingReadOnly,
      isInsideSubmodel: viewStack.length > 1,
      setSubmodelDialog,
      addToast,
    })
  }, [nodes, editingReadOnly, viewStack.length, setSubmodelDialog, addToast])
  const handleToolbarCreateInstance = useCallback(() => {
    if (editingReadOnly) {
      addToast("info", "This pipeline document is read-only")
      return
    }
    if (selectedNodeIds.length !== 1) {
      addToast("info", "Select exactly one node to create an instance of it")
      return
    }
    handleCreateInstance(selectedNodeIds[0])
  }, [editingReadOnly, selectedNodeIds, handleCreateInstance, addToast])

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
    if (editingReadOnly) return false
    if (isBoundaryConnection(connection)) return true
    return validatePipelineConnection(
      connection,
      panelGraph.allNodes,
      panelGraph.edges,
      submodelsRef.current,
    ).ok
  }, [editingReadOnly, isBoundaryConnection, panelGraph])

  const validateConnection = useCallback((connection: Connection): ConnectionValidationResult => {
    if (editingReadOnly) {
      return {
        ok: false,
        reason: { kind: "invalid-connection", message: "This pipeline document is read-only." },
      }
    }
    if (isBoundaryConnection(connection)) return { ok: true }
    return validatePipelineConnection(
      connection,
      panelGraph.allNodes,
      panelGraph.edges,
      submodelsRef.current,
    )
  }, [editingReadOnly, isBoundaryConnection, panelGraph])

  const {
    onConnect, onSelectionChange, onNodeClick, handleDeleteEdge,
    onConnectStart, onConnectEnd, onConnectionPointerMove, clearEdgeJoinCandidate,
    edgeJoinCandidateEdgeId, onNodeContextMenu, onDragOver, onDrop,
  } = useEdgeHandlers({
    selectedNode, graphRef, submodels, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setEdges, setNodesRaw, setEdgesRaw, pushSnapshot,
    setSelectedNode, setPreviewData, setContextMenu,
    setLastSelectedId,
    fetchPreview,
    cancelPreview,
    shouldSkipAutomaticPreview,
    clearTrace,
    screenToFlowPosition,
    graphRefreshingRef,
    existingSingletonTypes,
    resolveGraphIdentities: resolveCandidateGraphIdentities,
    findEdgeIdAtPoint,
    validateConnection,
    commitBoundaryConnection,
    deleteBoundaryEdge,
  })

  const presentedEdgeJoinCandidateEdgeId = useMemo(
    () => (
      !editingReadOnly && edgeJoinCandidateEdgeId
        && edgesWithTrace.some((edge) => edge.id === edgeJoinCandidateEdgeId)
        ? edgeJoinCandidateEdgeId
        : null
    ),
    [editingReadOnly, edgeJoinCandidateEdgeId, edgesWithTrace],
  )
  const edgesWithEdgeJoinCandidate = useMemo(
    () => withEdgeJoinInsertionCandidate(edgesWithTrace, presentedEdgeJoinCandidateEdgeId),
    [edgesWithTrace, presentedEdgeJoinCandidateEdgeId],
  )

  const handleSwapEdgeJoinInputs = useCallback((nodeId: string) => {
    if (editingReadOnly) return
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
    editingReadOnly,
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

  const handleSelectRecoveryElement = useCallback((elementId: string) => {
    const node = graphRef.current.nodes.find((candidate) => candidate.id === elementId)
    if (!node) return
    setSelectedNode(node)
    setLastSelectedId(node.id)
    lastSelectedNodeRef.current = node
    setUtilityOpen(false)
    setImportsOpen(false)
    setGitOpen(false)
  }, [setGitOpen, setImportsOpen, setUtilityOpen])

  const handleNodeSearchSelect = useCallback((nodeId: string) => {
    const node = graphRef.current.nodes.find((candidate) => candidate.id === nodeId) ?? null
    if (!node) return
    setSelectedNode(node)
    setLastSelectedId(node.id)
    lastSelectedNodeRef.current = node
    setUtilityOpen(false)
    setImportsOpen(false)
    setGitOpen(false)
  }, [setGitOpen, setImportsOpen, setUtilityOpen])

  const handleImportAdded = useCallback((importLine: string) => {
    const current = preambleRef.current
    if (current.includes(importLine)) return
    const updated = current ? `${current}\n${importLine}` : importLine
    setPreamble(updated)
    preambleRef.current = updated
  }, [setPreamble])

  const handlePreambleChange = useCallback((value: string) => {
    setPreamble(value)
    preambleRef.current = value
  }, [setPreamble])

  const handlePanelPreviewRefresh = useCallback(() => {
    if (!activePanelNodeId) return
    const refreshTarget = graphRef.current.nodes.find((node) => node.id === activePanelNodeId)
    if (refreshTarget) refreshPreview(refreshTarget)
  }, [activePanelNodeId, refreshPreview])

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

  const activeNodeId = activePanelNodeId
  const activePreviewData = previewForActiveNode(previewData, activeNodeId)
  const activeNode = panelGraph.getNode(activeNodeId)
  const dataPreviewContent = (
    <ActiveNodePreview
      documentCanExecute={documentCanExecute}
      activeNodeId={activeNodeId}
      activeNode={activeNode}
      panelNodes={panelGraph.allNodes}
      panelEdges={panelGraph.edges}
      submodels={submodelsSnapshot}
      preamble={preamble}
      previewData={activePreviewData}
      getModellingPreview={getModellingPreview}
      getOptimiserPreview={getOptimiserPreview}
      onCellClick={handleCellClick}
      tracedCell={tracedCell}
      previewNodeFrame={previewNodeFrame}
    />
  )
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
        canCreateSubmodel={canCreateSubmodel}
        onCreateSubmodel={handleToolbarCreateSubmodel}
        canCreateInstance={canCreateInstance}
        onCreateInstance={handleToolbarCreateInstance}
        onCentre={() => fitView({ padding: 0.15 })}
        onAutoLayout={handleAutoLayout}
        isAutoLayouting={isAutoLayouting}
        onSave={requestSave}
        onSaveCommit={requestCommit}
        wsStatus={wsStatus}
        timings={previewData?.timings}
        memory={previewData?.memory}
        editingDisabled={editingReadOnly}
        sourceSelectionTrusted={documentSourceSelectionTrusted}
      />

      {loadError || documentSystemFailure ? (
        <PipelineLoadFailureView detail={loadError ?? documentSystemFailure ?? "Unknown failure"} />
      ) : documentLoadStatus === "source_only" && retainedPipelineCanvas === null ? (
        <SourceRecoveryView />
      ) : comparison ? (
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
                  <GitPanel onClose={exitComparison} onSave={saveWithPendingCommits} />
                )}
              </Suspense>
            </ErrorBoundary>
          </aside>
        </div>
      ) : (
      <div className="flex-1 flex min-h-0">
        <nav
          aria-label="Node palette"
          aria-disabled={editingReadOnly}
          inert={editingReadOnly ? true : undefined}
          style={editingReadOnly ? { opacity: 0.45 } : undefined}
        >
          {paletteOpen ? (
            <ErrorBoundary name="NodePalette">
              <NodePalette
                onCollapse={() => setPaletteOpen(false)}
                existingSingletonTypes={existingSingletonTypes}
              />
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
          <StalePipelineReferenceBanner />
          <PipelineRecoveryBanner onSelectElement={handleSelectRecoveryElement} />
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
              onPointerMove={(event) => { if (!editingReadOnly) onConnectionPointerMove(event) }}
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
                onConnect={editingReadOnly ? undefined : onConnect}
                onConnectStart={editingReadOnly ? undefined : onConnectStart}
                onConnectEnd={editingReadOnly ? undefined : onConnectEnd}
                nodesDraggable={!editingReadOnly}
                nodesConnectable={!editingReadOnly}
                onSelectionChange={onSelectionChange}
                onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
                onNodeMouseLeave={() => setHoveredNodeId(null)}
                onNodeClick={(event, node) => { setUtilityOpen(false); setImportsOpen(false); setGitOpen(false); setHoveredNodeId(null); onNodeClick(event, node) }}
                onNodeContextMenu={editingReadOnly ? undefined : onNodeContextMenu}
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
                onDrop={editingReadOnly ? undefined : onDrop}
                onDragOver={editingReadOnly ? undefined : onDragOver}
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

        <NodePropertiesPanel
          gitOpen={gitOpen}
          utilityOpen={utilityOpen}
          importsOpen={importsOpen}
          assistantOpen={assistantOpen}
          onCloseGit={() => setGitOpen(false)}
          onCloseUtility={() => setUtilityOpen(false)}
          onCloseImports={() => setImportsOpen(false)}
          onSave={saveWithPendingCommits}
          preamble={preamble}
          onImportAdded={handleImportAdded}
          onPreambleChange={handlePreambleChange}
          isInsideSubmodel={viewStack.length > 1}
          currentSourceFile={currentSourceFile}
          documentReadOnly={documentReadOnly}
          traceResult={traceResult}
          traceState={traceState}
          clearTrace={clearTrace}
          cancelTrace={cancelTrace}
          retryTrace={retryTrace}
          panelGraph={panelGraph}
          submodels={submodelsSnapshot}
          panelNode={panelNode}
          onUpdateNode={onUpdateNode}
          onRenameNode={onRenameNode}
          onDeleteEdge={editingReadOnly ? undefined : handleDeleteEdge}
          onDeleteSubmodelInputPort={editingReadOnly ? undefined : deleteBoundaryInputPort}
          onSwapEdgeJoinInputs={editingReadOnly ? undefined : handleSwapEdgeJoinInputs}
          editingReadOnly={editingReadOnly}
          onRefreshPreview={handlePanelPreviewRefresh}
          selectedNode={selectedNode}
          activePanelNodeId={activePanelNodeId}
          previewData={previewData}
          previewBusy={previewBusy}
          onClosePanel={closePanel}
          onRemoveUnavailableNode={setPipelineRepairTarget}
        />
      </div>
      )}

      <FlowEditorOverlays
        editingReadOnly={editingReadOnly}
        contextMenu={contextMenu}
        setContextMenu={setContextMenu}
        onDeleteNode={handleDeleteNode}
        onDuplicateNode={handleDuplicateNode}
        onRenameNodeMenu={handleRenameNode}
        onCreateInstance={handleCreateInstance}
        onDissolveSubmodel={handleDissolveSubmodel}
        onGitModalConfirmed={handleGitModalConfirmed}
        onSave={saveWithPendingCommits}
        onMoveConfirmed={handleMoveConfirmed}
        onCreateSubmodel={handleCreateSubmodel}
        onRenameNode={onRenameNode}
        pipelineRepairTarget={pipelineRepairTarget}
        documentSourceFile={documentSourceFile}
        documentSourceRevision={documentSourceRevision}
        onClosePipelineRepair={() => setPipelineRepairTarget(null)}
        onRepairApplied={handleRepairApplied}
        onNodeSearchSelect={handleNodeSearchSelect}
      />

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
