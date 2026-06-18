import { useEffect, useCallback, useState, useRef } from "react"
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useReactFlow,
  SelectionMode,
  type Node,
  type Edge,
  BackgroundVariant,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { nodeTypes } from "./utils/nodeTypeRegistry"
import NodePalette from "./panels/NodePalette"
import NodePanel from "./panels/NodePanel"
import { GraphProvider } from "./panels/GraphContext"
import DataPreview from "./panels/DataPreview"
import OptimiserPreview from "./panels/OptimiserPreview"
import OptimiserDataPreview from "./panels/OptimiserDataPreview"
import { ModellingPreview } from "./panels/ModellingPreview"

import TracePanel from "./panels/TracePanel"
import ToastContainer from "./components/Toast"
import { ErrorBoundary } from "./components/ErrorBoundary"
import ContextMenu from "./components/ContextMenu"
import KeyboardShortcuts from "./components/KeyboardShortcuts"
import BreadcrumbBar from "./components/BreadcrumbBar"
import Toolbar from "./components/Toolbar"
import SubmodelDialog from "./components/SubmodelDialog"
import RenameDialog from "./components/RenameDialog"
import WorkingBranchModal from "./components/WorkingBranchModal"
import DivergenceModal from "./components/DivergenceModal"
import MilestoneCommitModal from "./components/MilestoneCommitModal"
import BackgroundJobPolling from "./components/BackgroundJobPolling"
import UtilityPanel from "./panels/UtilityPanel"
import ImportsPanel from "./panels/ImportsPanel"
import GitPanel from "./panels/GitPanel"
import ComparisonView, { type ComparisonInspect } from "./components/ComparisonView"
import ComparisonInspector from "./components/ComparisonInspector"
import NodeSearch from "./components/NodeSearch"

import useGraphCanvasState from "./hooks/useGraphCanvasState"
import useWebSocketSync from "./hooks/useWebSocketSync"
import usePipelineAPI from "./hooks/usePipelineAPI"
import useTracing from "./hooks/useTracing"
import useSubmodelNavigation from "./hooks/useSubmodelNavigation"
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

import { NODE_TYPES } from "./utils/nodeTypes"
import { previewForActiveNode } from "./utils/activePreview"
import { shouldUseLiteGraphEffects } from "./utils/graphPerformance"
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
    undo, redo, canUndo, canRedo,
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
  const shortcutsOpen = useUIStore((s) => s.shortcutsOpen)
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const submodelDialog = useUIStore((s) => s.submodelDialog)
  const setSubmodelDialog = useUIStore((s) => s.setSubmodelDialog)
  const renameDialog = useUIStore((s) => s.renameDialog)
  const setRenameDialog = useUIStore((s) => s.setRenameDialog)
  // Git working-branch model (P2)
  const gitModal = useGitStore((s) => s.modal)
  const loadGitStatus = useGitStore((s) => s.loadStatus)
  const closeGitModal = useGitStore((s) => s.closeModal)
  // Read-only comparison view (S11): when set, the dual-canvas overlay replaces
  // the editor's content row (the toolbar stays, remaining interactive).
  const comparison = useGitStore((s) => s.comparison)
  const closeComparison = useGitStore((s) => s.closeComparison)
  const addToast = useToastStore((s) => s.addToast)
  const syncBanner = useUIStore((s) => s.syncBanner)
  const setSyncBanner = useUIStore((s) => s.setSyncBanner)
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId)
  const setHoveredNodeId = useUIStore((s) => s.setHoveredNodeId)
  const nodeSearchOpen = useUIStore((s) => s.nodeSearchOpen)
  const setNodeSearchOpen = useUIStore((s) => s.setNodeSearchOpen)

  // Fetch MLflow status once on startup (shared by all panels)
  useEffect(() => { fetchMlflow() }, [fetchMlflow])

  // Local UI state (not worth globalizing)
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  // The node under read-only inspection in the comparison view, or null. Cleared
  // whenever the comparison closes or the inspected version changes (S11).
  const [comparisonInspect, setComparisonInspect] = useState<ComparisonInspect | null>(null)
  useEffect(() => {
    setComparisonInspect(null)
  }, [comparison?.sha])
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string; nodeLabel: string; isSubmodel?: boolean; isSingleton?: boolean } | null>(null)
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
  }, [activePanelNodeId, setPinnedPreviewNodeId, touchModellingPreview, touchOptimiserPreview])

  useEffect(() => {
    if (!activePanelNodeId) return
    if (panelGraph.getNode(activePanelNodeId)) return
    setSelectedNode(null)
    lastSelectedNodeRef.current = null
    setLastSelectedId(null)
    setPreviewDataRef.current(null)
  }, [panelGraph, activePanelNodeId])

  // Store-maintained dirty flag.
  // Subscribe to the primitive so frequent React Flow node updates do not
  // serialize the graph from App's selector.
  const dirty = useGraphStore((s) => s.dirty)

  // ---------------------------------------------------------------------------
  // Hooks
  // ---------------------------------------------------------------------------

  const {
    loading, previewData, setPreviewData,
    nodeStatuses,
    fetchPreview, cancelPreview, refreshPreview, handleSave,
  } = usePipelineAPI({
    selectedNode,
    graphRef, parentGraphRef, submodelsRef, setNodes,
    setNodesRaw, setEdgesRaw, setPreamble,
    preambleRef, pipelineNameRef, descriptionRef, sourceFileRef,
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
  // to its own modal. A genuinely null status (non-git project) saves ungated.
  const requestSave = useCallback(async () => {
    // Resolve status before deciding: during the startup load (status null,
    // loading in-flight) a synchronous read would see null and save ungated,
    // bypassing the gate. Awaiting the in-flight/fresh load closes that race.
    const st = useGitStore.getState().status ?? (await useGitStore.getState().loadStatus())
    if (st === null || st.state === "ready") {
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
      // No git repo (or status unreadable): committing is meaningless here.
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

  // Startup readiness check (S27): load status once, and surface the modal only
  // when something needs attention (unset/invalid → select, divergent → that
  // modal). A healthy clone fires nothing.
  useEffect(() => {
    void loadGitStatus().then((st) => {
      if (!st || st.state === "ready") return
      useGitStore.getState().openModal(st.state === "divergent" ? "divergence" : "select")
    })
  }, [loadGitStatus])

  const wsStatus = useWebSocketSync({
    setNodesRaw, setEdgesRaw, setPreamble, preambleRef, graphRefreshingRef,
    nodeIdCounter, fitView,
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

  useKeyboardShortcuts({
    handleSave: requestSave, setNodes, setEdges, undo, redo, fitView,
    graphRef, clipboard, nodeIdCounter,
    setSelectedNode, setPreviewData: (d: null) => setPreviewData(d),
    clearTrace,
    closePanel,
    isInsideSubmodel: viewStack.length > 1,
  })

  // ---------------------------------------------------------------------------
  // Node + edge interaction handlers (extracted to custom hooks)
  // ---------------------------------------------------------------------------

  const onUpdateNode = useCallback(
    (id: string, data: Record<string, unknown>) => {
      setNodes((nds) => nds.map((n) => (n.id === id ? { ...n, data } : n)))
      setSelectedNode((prev) => (prev && prev.id === id ? { ...prev, data } : prev))
    },
    [setNodes],
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

  const {
    onConnect, onSelectionChange, onNodeClick, handleDeleteEdge,
    onNodeContextMenu, onDragOver, onDrop,
  } = useEdgeHandlers({
    selectedNode, graphRef, nodeIdCounter, lastSelectedNodeRef,
    setNodes, setEdges, setSelectedNode, setContextMenu,
    fetchPreview,
    cancelPreview,
    shouldSkipAutomaticPreview,
    clearTrace,
    screenToFlowPosition,
    graphRefreshingRef,
  })

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
        onCentre={() => fitView({ padding: 0.15 })}
        onAutoLayout={handleAutoLayout}
        isAutoLayouting={isAutoLayouting}
        onSave={requestSave}
        onSaveCommit={requestCommit}
        wsStatus={wsStatus}
        timings={previewData?.timings}
        memory={previewData?.memory}
      />

      {comparison ? (
        <div className="flex-1 flex min-h-0">
          <main className="flex-1 flex flex-col min-w-0">
            <ErrorBoundary name="ComparisonView">
              <ComparisonView
                key={comparison.sha}
                comparison={comparison}
                currentNodes={nodes}
                currentEdges={edges}
                onClose={closeComparison}
                onSelectNode={(p) => { setComparisonInspect(p); setGitOpen(false) }}
              />
            </ErrorBoundary>
          </main>
          {/* Read-only config inspection takes the sidepane slot when a node is
              clicked (displacing the VC panel); the VC panel returns via the
              toolbar commit indicator (which selects the compared version), S11.
              An explicitly-opened VC panel wins over a lingering inspection. */}
          {(comparisonInspect || gitOpen) && (
            <aside aria-label="Comparison inspector">
              <ErrorBoundary name="ComparisonInspector">
                {gitOpen ? (
                  <GitPanel onClose={() => setGitOpen(false)} />
                ) : comparisonInspect ? (
                  <ComparisonInspector
                    inspect={comparisonInspect}
                    onClose={() => setComparisonInspect(null)}
                  />
                ) : null}
              </ErrorBoundary>
            </aside>
          )}
        </div>
      ) : (
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
          {syncBanner && (
            <div className="flex items-center gap-2 px-3 py-1.5 text-[12px] font-medium"
              style={{ background: 'var(--danger-soft-strong)', color: 'var(--danger-text)', borderBottom: '1px solid var(--danger-border-strong)' }}>
              <span className="flex-1 truncate">{syncBanner}</span>
              <button onClick={() => setSyncBanner(null)} className="opacity-60 hover:opacity-100">âœ•</button>
            </div>
          )}
          <ErrorBoundary name="Canvas">
            <div className="flex-1 min-h-0 relative">
              <BreadcrumbBar viewStack={viewStack} onNavigate={handleBreadcrumbNavigate} />
              <ReactFlow
                className={useLiteGraphEffects ? "graph-effects-lite" : undefined}
                nodes={nodesWithStatus}
                edges={edgesWithTrace}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onSelectionChange={onSelectionChange}
                onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
                onNodeMouseLeave={() => setHoveredNodeId(null)}
                onNodeClick={(event, node) => { setUtilityOpen(false); setImportsOpen(false); setGitOpen(false); setHoveredNodeId(null); onNodeClick(event, node) }}
                onNodeContextMenu={onNodeContextMenu}
                onNodeDoubleClick={(_event, node) => {
                  if (nodeData(node).nodeType === NODE_TYPES.SUBMODEL) {
                    handleDrillIntoSubmodel(node.id)
                  }
                }}
                onPaneClick={() => { setContextMenu(null); clearTrace(); closePanel() }}
                onDrop={onDrop}
                onDragOver={onDragOver}
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
              >
                <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,.06)" />
              </ReactFlow>
            </div>
          </ErrorBoundary>

          <ErrorBoundary name="DataPreview">
            {(() => {
              const activeNodeId = selectedNode?.id ?? lastSelectedId
              const activePreviewData = previewForActiveNode(previewData, activeNodeId)
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
              const activeNode = panelGraph.getNode(activeNodeId)
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
                  onRefreshPreview={() => { if (selectedNode) refreshPreview(selectedNode) }}
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
                />
              </GraphProvider>
            )}
          </ErrorBoundary>
        </aside>
      </div>
      )}

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
          onDissolveSubmodel={handleDissolveSubmodel}
        />
      )}

      {shortcutsOpen && <KeyboardShortcuts onClose={() => setShortcutsOpen(false)} />}

      {gitModal === "select" && (
        <WorkingBranchModal onConfirmed={handleGitModalConfirmed} onClose={closeGitModal} />
      )}

      {gitModal === "divergence" && (
        <DivergenceModal onConfirmed={handleGitModalConfirmed} onClose={closeGitModal} />
      )}

      {gitModal === "milestone" && (
        <MilestoneCommitModal onConfirmed={closeGitModal} onClose={closeGitModal} />
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
