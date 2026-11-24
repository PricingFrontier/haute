import { evidenceLabel } from "./stageEvidence"
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useLayoutEffect, useState, type CSSProperties, type ReactNode } from "react"
import { ErrorBoundary } from "../components/ErrorBoundary"
import { PaletteColumn, PaletteHeader, PaletteRevealStrip, SidePanel, halfWindowWidth } from "../haute-ui"
import { hasModifier, inModalDialog, isTypingTarget, saveCommittingField } from "../hooks/keyboardTargets"
import type { PanelGraphContextSnapshot } from "../hooks/usePanelGraphContext"
import type { PreviewData } from "../panels/DataPreview"
import { GraphProvider } from "../panels/GraphContext"
import ModellingConfig from "../panels/ModellingConfig"
import PreviewPanelTabs, { type PreviewPanelTab } from "../panels/PreviewPanelTabs"
import type { OnUpdateConfig, OnUpdateConfigResult, SimpleNode } from "../panels/editors/_shared"
import { ChartEmptyState, ResponsiveChart } from "../panels/modelling/ChartScaffold"
import { LossHistoryChart, LossTab } from "../panels/modelling/LossTab"
import { TBoostTablesTab } from "../panels/modelling/TBoostTablesTab"
import { FeaturesTab } from "../panels/modelling/FeaturesTab"
import { LiftTab } from "../panels/modelling/LiftTab"
import { PdpTab } from "../panels/modelling/PdpTab"
import { ResidualsTab } from "../panels/modelling/ResidualsTab"
import { ShapCurvesTab } from "../panels/modelling/ShapCurvesTab"
import { ViewSwitch } from "../panels/ViewSwitch"
import type { TrainResult } from "../stores/useNodeResultsStore"
import { clearCachedResultShape, useUpstreamColumns } from "../panels/nodePanelData"
import { removeStages } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import useSettingsStore from "../stores/useSettingsStore"
import { useResultKey, useRequestScope } from "../panels/useGraph"
import { hasTrainResult } from "../stores/nodeResultsSelectors"
import useModellingViewStore, { MODEL_PANELS, type ModelPane, type SettingsPanel, type TrainAction } from "../stores/useModellingViewStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ModellingPane } from "../stores/useUIStore"
import { NODE_TYPES } from "../utils/nodeTypes"
import { documentRequestScope, prepareCanvasRequest } from "../utils/requestGateway"
import ViewSwitcher from "../workbench/ViewSwitcher"
import FactorList from "./FactorList"
import { StageRestorer } from "./StageRestorer"
import { StageButtons } from "./StageButtons"
import { ModelEntries } from "./ModelEntries"
import { ModelsAveChart, type ModelAveSeries } from "./ModelsAveChart"
import { useModelResultKey, useModelRunState } from "./modelRuns"
import useDocumentStatusStore, { documentExecutionKey, captureDocumentExecutionFence, isDocumentExecutionFenceCurrent } from "../stores/useDocumentStatusStore"
import { useStagedViewKey, useStagedViews } from "./useStagedTraining"
import { useStageTableResult } from "./useStageTableResult"
import { selectedCandidate, selectedPathPoint } from "./pathSelection"
import { defaultVisibleStages, stageResult } from "./stageResults"
import { STAGES, STAGE_LABELS, type ModelStage } from "./stageSettings"
import {
  addModelCopy,
  applyModelUpdate,
  chosenModel,
  freeModelName,
  hasModels,
  interactiveModels,
  modelColor,
  modelConfig,
  modelResultKey,
  removeModel,
  renameModel,
  type InteractiveModel,
} from "./interactiveModels"
// The panes, their charts and the tables are styled with the results workspace.
import "../panels/modelling/validation.css"

const ModelPaths = lazy(() => import("./ModelPaths"))
const GitPanel = lazy(() => import("../panels/GitPanel"))
const AssistantPanel = lazy(() => import("../panels/assistant/AssistantPanel"))
const FilesPanel = lazy(() => import("../panels/FilesPanel"))

interface ModellingViewProps {
  /** The graph the canvas shows: its interactive builds are the view's models. */
  graph: PanelGraphContextSnapshot
  submodels?: Record<string, unknown>
  preamble?: string
  /** Applies an edit to a node, as the node panel's editors do. */
  onUpdateNode: (id: string, data: Record<string, unknown>) => OnUpdateConfigResult
  /**
   * Selects a node in the pipeline as a click on it does, which previews it in automatic
   * calculation: the preview brings the columns reaching the node.
   */
  onOpenNode: (nodeId: string) => void
  /** Brings a node's preview up to date, and with it the columns reaching the node. */
  onRefreshNode: (nodeId: string) => void
  /** The pipeline's preview as it stands: whose it is, and whether it is loading or failed. */
  preview: Pick<PreviewData, "nodeId" | "status" | "error"> | null
  /** The Git panel's save, as the node properties panel gives it. */
  onSave: () => Promise<boolean>
  /** Saves the project, as the toolbar's Save does: Ctrl/Cmd+S. */
  onSaveShortcut: () => void
  /** Undoes and redoes the pipeline's last edit, a model's settings among them. */
  onUndo: () => void
  onRedo: () => void
  /**
   * The pipeline cannot be edited now (a read-only document or submodel, or the assistant
   * at work): a model's factors and settings show and change nothing, and nothing trains.
   */
  readOnly: boolean
  /** The Assistant panel's props, as the node properties panel gives them. */
  isInsideSubmodel: boolean
  documentReadOnly: boolean
}

/**
 * The pane of the node's editor each right-hand panel shows, and its heading, as the
 * toolbar picks it: the chosen model's Features, Parameters and Training, then Model Setup
 * (the settings every model shares) and Data Split. The model's parameter pane contains
 * its staged Training controls.
 */
const PANELS: Readonly<Record<SettingsPanel, { heading: string; pane: ModellingPane }>> = {
  features: { heading: "Features", pane: "features" },
  params: { heading: "Parameters", pane: "params" },
  train: { heading: "Training", pane: "train" },
  setup: { heading: "Model setup", pane: "target" },
  split: { heading: "Data split", pane: "split" },
}
/** The narrowest the settings panel drags to, as the pipeline's panels. */
const MIN_PANEL_WIDTH = 320
const PANEL_ORDER: readonly SettingsPanel[] = ["features", "params", "train", "setup", "split"]

/**
 * The panes of a model, along the top of the area between its factors and its settings:
 * the one every model shares, then the chosen model's own.
 */
const PANES: readonly PreviewPanelTab<ModelPane>[] = [
  { key: "factor", label: "Factor" },
  { key: "curve", label: "Learning curve" },
  { key: "tables", label: "Tables" },
  { key: "paths", label: "Paths" },
  { key: "explain", label: "Explain" },
  { key: "fit", label: "Fit" },
]

/** The ids `PreviewPanelTabs` gives the tabs, which name their panel. */
const PANE_ID_PREFIX = "modelling-view"

/** The results workspace's accents, which its stylesheet reads, as the model's. */
const PANE_ACCENTS = {
  "--results-accent": "var(--model-accent)",
  "--results-accent-soft": "var(--model-accent-soft)",
} as CSSProperties

/** The height of the learning curve's plot. */
const CURVE_HEIGHT = 340

/** The panel that holds a pane of the node's editor, for the editor's "Go to" links. */
function panelForPane(pane: ModellingPane): SettingsPanel {
  const holder = PANEL_ORDER.find((panel) => PANELS[panel].pane === pane)
  // The editor is told which panes it may link to (`reviewablePanes`), all of them here.
  if (holder === undefined) throw new Error(`The Modelling view has no panel for the ${pane} pane.`)
  return holder
}

const isInteractiveBuild = (node: SimpleNode): boolean =>
  node.data.nodeType === NODE_TYPES.MODELLING
  && (node.data.config as Record<string, unknown> | undefined)?.interactive_build === true

/**
 * The Modelling view (specs/frontend-modelling-optimiser-ui), over the area below the
 * toolbar while it is the active view. It lists the interactive builds of the graph the
 * canvas shows, and opens one: its models down the left, the pane chosen, and on the right
 * the settings panel the toolbar picks: the chosen model's Features (its factors),
 * Parameters or Training, or Model Setup or Data Split. The toolbar's Git, Files and
 * Assistant panels open at the view's right, in the settings' place.
 */
export default function ModellingView({
  graph,
  submodels,
  preamble,
  onUpdateNode,
  onOpenNode,
  onRefreshNode,
  preview,
  onSave,
  onSaveShortcut,
  onUndo,
  onRedo,
  readOnly,
  isInsideSubmodel,
  documentReadOnly,
}: ModellingViewProps) {
  useModellingShortcuts({ onSave: onSaveShortcut, onUndo, onRedo, readOnly })
  const gitOpen = useUIStore((s) => s.gitOpen)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const filesPlace = useUIStore((s) => s.filesPlace)
  const setFilesPlace = useUIStore((s) => s.setFilesPlace)
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  // One panel at a time, in the pipeline editor's order.
  const sidePanel = gitOpen ? (
    <aside aria-label="Version control">
      <Suspense fallback={null}>
        <GitPanel onClose={() => setGitOpen(false)} onSave={onSave} />
      </Suspense>
    </aside>
  ) : filesPlace !== null ? (
    <aside aria-label="Files">
      <Suspense fallback={null}>
        <FilesPanel key={filesPlace} place={filesPlace} onClose={() => setFilesPlace(null)} />
      </Suspense>
    </aside>
  ) : assistantOpen ? (
    <aside aria-label="Assistant">
      <ErrorBoundary name="AssistantPanel">
        <Suspense fallback={null}>
          <AssistantPanel isInsideSubmodel={isInsideSubmodel} readOnly={documentReadOnly} />
        </Suspense>
      </ErrorBoundary>
    </aside>
  ) : null
  const modelId = useModellingViewStore((s) => s.modelId)
  const models = useMemo(() => graph.allNodes.filter(isInteractiveBuild), [graph.allNodes])
  // A model deleted from the pipeline while it was open leaves the list showing.
  const model = models.find((node) => node.id === modelId) ?? null

  return (
    // Clipped, so a side panel sliding in from past the right edge never brings a scrollbar.
    <div className="absolute inset-0 flex overflow-hidden" data-testid="modelling-view" style={{ background: "var(--bg-base)" }}>
      <GraphProvider
        allNodes={graph.allNodes}
        edges={graph.edges}
        submodels={submodels}
        preamble={preamble}
        requestScope={documentRequestScope}
      >
        {model === null ? (
          <ModelList models={models} />
        ) : !hasModels((model.data.config ?? {}) as Record<string, unknown>) ? (
          <NoModels node={model} />
        ) : (
          <ModelWorkspace
            key={model.id}
            node={model}
            graph={graph}
            onUpdateNode={onUpdateNode}
            onOpenNode={onOpenNode}
            onRefreshNode={onRefreshNode}
            preview={preview !== null && preview.nodeId === model.id ? preview : null}
            readOnly={readOnly}
            showSettings={sidePanel === null}
          />
        )}
      </GraphProvider>
      {sidePanel}
    </div>
  )
}

/**
 * The view's keyboard shortcuts, registered while it shows; the pipeline editor's are off
 * meanwhile. Ctrl/Cmd+S saves the project with a focused field's edit included, and outside
 * a text field Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (or Ctrl/Cmd+Y) undo and redo the pipeline's
 * last edit, as the toolbar's buttons do. Keys in a modal dialog are the dialog's.
 */
function useModellingShortcuts({ onSave, onUndo, onRedo, readOnly }: {
  onSave: () => void
  onUndo: () => void
  onRedo: () => void
  readOnly: boolean
}): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented || inModalDialog(e.target) || !hasModifier(e)) return
      const key = e.key.toLowerCase()
      if (key === "s") {
        e.preventDefault()
        saveCommittingField(e.target, onSave)
        return
      }
      if (isTypingTarget(e.target)) return
      const redo = (key === "z" && e.shiftKey) || key === "y"
      if (!redo && key !== "z") return
      e.preventDefault()
      if (readOnly) return
      if (redo) onRedo()
      else onUndo()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [onSave, onUndo, onRedo, readOnly])
}

/**
 * The view's left column: the node's models, then the switcher between editor views.
 */
function LeftColumn({ children }: { children?: ReactNode }) {
  const open = useUIStore((s) => s.paletteOpen)
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen)
  if (!open) {
    return (
      <div className="flex shrink-0 flex-col" style={{ background: "var(--chrome)", borderRight: "1px solid var(--chrome-border)" }}>
        <div className="min-h-0 flex-1">
          <PaletteRevealStrip label="Show model palette" onReveal={() => setPaletteOpen(true)} />
        </div>
        <ViewSwitcher compact />
      </div>
    )
  }
  return (
    <PaletteColumn>
      <PaletteHeader title="Models" onCollapse={() => setPaletteOpen(false)} />
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {children}
      </div>
      <ViewSwitcher compact={false} />
    </PaletteColumn>
  )
}

function ModelList({ models }: { models: readonly SimpleNode[] }) {
  const openModel = useModellingViewStore((s) => s.openModel)
  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <LeftColumn />
      <main className="min-w-0 flex-1 overflow-y-auto px-6 py-5" aria-label="Models">
        <h2 className="text-[15px] font-semibold">Models</h2>
        {models.length === 0 ? (
          <p className="mt-3 text-[13px]" style={{ color: "var(--text-muted)" }}>No interactive builds in this pipeline.</p>
        ) : (
          <table className="mt-3 w-full max-w-3xl text-left text-[13px]">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="py-1.5 pr-4 font-medium">Model</th>
                <th className="py-1.5 pr-4 font-medium">Target</th>
                <th className="py-1.5 font-medium">Last run</th>
              </tr>
            </thead>
            <tbody>
              {models.map((node) => (
                <ModelRow key={node.id} node={node} onOpen={() => openModel(node.id)} />
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  )
}

function ModelRow({ node, onOpen }: { node: SimpleNode; onOpen: () => void }) {
  const config = (node.data.config ?? {}) as Record<string, unknown>
  const target = typeof config.target === "string" && config.target !== "" ? config.target : "Not set"
  return (
    <tr style={{ borderTop: "1px solid var(--border)" }}>
      <td className="py-2 pr-4">
        <button type="button" className="font-semibold focus-ring" style={{ color: "var(--model-accent)" }} onClick={onOpen}>
          {node.data.label}
        </button>
      </td>
      <td className="py-2 pr-4 font-mono">{target}</td>
      <td className="py-2">
        {!hasModels(config) ? "No models" : interactiveModels(config).map((model, index) => (
          <span key={model.id}>
            {index > 0 && " · "}
            <ModelLastRun nodeId={node.id} model={model} />
          </span>
        ))}
      </td>
    </tr>
  )
}

/**
 * An interactive build made before its models were listed under `models`: the view has
 * no model to open, and training refuses it, so it says so.
 */
function NoModels({ node }: { node: SimpleNode }) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <LeftColumn />
      <main className="min-w-0 flex-1 px-6 py-5" aria-label={String(node.data.label)}>
        <p role="alert" className="mt-3 text-[13px]">{node.data.label} has no models.</p>
      </main>
    </div>
  )
}

/** A model's name and where its last run stands, in the list of models. */
function ModelLastRun({ nodeId, model }: { nodeId: string; model: InteractiveModel }) {
  const { state } = useModelRunState(nodeId, model.id)
  return <>{`${model.name}: ${state}`}</>
}

function ModelWorkspace({ node, graph, onUpdateNode, onOpenNode, onRefreshNode, preview, readOnly, showSettings }: {
  node: SimpleNode
  graph: PanelGraphContextSnapshot
  onUpdateNode: ModellingViewProps["onUpdateNode"]
  onOpenNode: ModellingViewProps["onOpenNode"]
  onRefreshNode: ModellingViewProps["onRefreshNode"]
  /** The pipeline's preview while it is this model's node's. */
  preview: ModellingViewProps["preview"]
  readOnly: boolean
  /** False while a toolbar panel shows in the settings' place. */
  showSettings: boolean
}) {
  const panel = useModellingViewStore((s) => s.panel)
  const showPanel = useModellingViewStore((s) => s.showPanel)
  const showModelPanel = useModellingViewStore((s) => s.showModelPanel)
  const closePanel = useModellingViewStore((s) => s.closePanel)
  const panelWidth = useModellingViewStore((s) => s.panelWidth)
  const setPanelWidth = useModellingViewStore((s) => s.setPanelWidth)
  const hiddenModels = useModellingViewStore((s) => s.hiddenModels)
  const setModelShown = useModellingViewStore((s) => s.setModelShown)
  const setTrainAction = useModellingViewStore((s) => s.setTrainAction)
  const trainAction = useModellingViewStore((s) => s.trainAction)
  const factor = useModellingViewStore((s) => s.factor)
  const selectFactor = useModellingViewStore((s) => s.selectFactor)
  const pane = useModellingViewStore((s) => s.pane)
  const showPane = useModellingViewStore((s) => s.showPane)
  const chosenId = useModellingViewStore((s) => s.chosenModel)
  const chooseModel = useModellingViewStore((s) => s.chooseModel)
  const addToast = useToastStore((s) => s.addToast)
  const config = useMemo(() => (node.data.config ?? {}) as Record<string, unknown>, [node.data.config])
  const models = interactiveModels(config)
  // The chosen model, or the first while none is or the chosen one has gone (an undo).
  const model = chosenModel(config, chosenId)
  const flat = useMemo(() => modelConfig(config, model), [config, model])

  // Opening a model selects its node in the pipeline, once, as a click on it does: the
  // preview that follows brings the columns reaching the node, which are the factors.
  const openNode = useRef(onOpenNode)
  useEffect(() => {
    openNode.current = onOpenNode
  }, [onOpenNode])
  useEffect(() => {
    openNode.current(node.id)
  }, [node.id])

  const updateConfig = useCallback((next: Record<string, unknown>): OnUpdateConfigResult => {
    if (readOnly) return { ok: false, error: "The pipeline cannot be edited now." }
    return onUpdateNode(node.id, clearCachedResultShape({ ...node.data, config: next }))
  }, [node, onUpdateNode, readOnly])
  // The editor edits the chosen model's flat config; each key goes to its home.
  const onUpdate = useCallback<OnUpdateConfig>((keyOrUpdates, value) => (
    updateConfig(applyModelUpdate(
      config,
      model.id,
      typeof keyOrUpdates === "string" ? { [keyOrUpdates]: value } : keyOrUpdates,
    ))
  ), [config, model.id, updateConfig])
  // Add and Duplicate copy a model's settings, never its run, and choose the copy.
  const addCopy = (source: InteractiveModel, name: string) => {
    const added = addModelCopy(config, source, name)
    if (updateConfig(added.config).ok) {
      chooseModel(added.model.id)
      showModelPanel()
    }
  }
  const renameTo = (modelId: string, name: string): string | null => {
    if (models.some((candidate) => candidate.id !== modelId && candidate.name === name)) {
      return `A model is already named ${name}.`
    }
    const result = updateConfig(renameModel(config, modelId, name))
    return result.ok ? null : result.error
  }
  const removalScope = useRequestScope()
  const commitAppliedRecipe = (modelId: string, updates: Record<string, unknown>): OnUpdateConfigResult => {
    if (readOnly) return { ok: false, error: "The pipeline cannot be edited now." }
    // Several restored models may publish in one render. Merge each into the live
    // canonical node, so the later completion retains the earlier model's recipe.
    const target = prepareCanvasRequest(removalScope, node.id)
    const live = target.graph.nodes.find((entry) => entry.id === target.nodeId)
    if (!live) return { ok: false, error: "The modelling node is no longer available." }
    return updateConfig(applyModelUpdate(live.data.config as Record<string, unknown>, modelId, updates))
  }
  const removal = useRef<{ config: Record<string, unknown>; updateConfig: typeof updateConfig } | null>(null)
  useLayoutEffect(() => {
    removal.current = { config, updateConfig }
    return () => { removal.current = null }
  }, [config, updateConfig])
  const remove = async (modelId: string) => {
    try {
      const removed = models.find((entry) => entry.id === modelId)
      const fence = captureDocumentExecutionFence()
      if (removed?.stages !== undefined) {
        const target = prepareCanvasRequest(removalScope, node.id)
        await removeStages({ graph: target.graph, node_id: target.nodeId, model: modelId, source: useSettingsStore.getState().activeSource })
      }
      const latest = removal.current
      if (!latest || !isDocumentExecutionFenceCurrent(fence)) return
      const result = latest.updateConfig(removeModel(latest.config, modelId))
      if (!result.ok) addToast("error", result.error)
    } catch (error) { addToast("error", apiErrorMessage(error)) }
  }

  // The columns arriving at the node, as its panel reads them.
  const nodeMap = useMemo(() => Object.fromEntries(graph.allNodes.map((entry) => [entry.id, entry])), [graph.allNodes])
  const upstreamEdges = useMemo(() => graph.edges.filter((edge) => edge.target === node.id), [graph.edges, node.id])
  const columns = useUpstreamColumns(node.id, upstreamEdges, nodeMap)

  // Closed, the panel keeps the editor as the model's own panels do, hidden.
  const shown = PANELS[panel ?? "features"]
  const modelPanel = panel === null || MODEL_PANELS.includes(panel)
  const settingsVisible = showSettings && panel !== null
  const showFeatures = panel === "features"
  const stagedPane = panel === "train" ? "train" : "params"
  // The model's own panels share one editor pane, which holds its staged controls.
  const editorPanes: readonly ModellingPane[] = modelPanel ? ["params"] : [shown.pane]
  const reviewPane = (next: ModellingPane) => showPanel(panelForPane(next))
  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      {models.filter((entry) => entry.id !== model.id).map((entry) => <StageRestorer key={entry.id}
        nodeId={node.id} modelId={entry.id} config={modelConfig(config, entry)}
        onUpdate={(updates) => commitAppliedRecipe(entry.id, updates)} />)}
      <LeftColumn>
        <nav aria-label="Models">
          <ModelEntries
            nodeId={node.id}
            models={models}
            chosenId={model.id}
            hiddenIds={hiddenModels}
            readOnly={readOnly}
            onChoose={(modelId) => {
              chooseModel(modelId)
              showModelPanel()
            }}
            onShow={setModelShown}
            // A new model starts from the chosen one's settings (proposal 59).
            onAdd={() => addCopy(model, freeModelName(models, "t-boost"))}
            onDuplicate={(modelId) => {
              const source = models.find((candidate) => candidate.id === modelId) ?? model
              addCopy(source, freeModelName(models, `${source.name} copy`))
            }}
            onRename={renameTo}
            onRemove={remove}
          />
        </nav>
      </LeftColumn>
      <main className="flex min-w-0 flex-1 flex-col" aria-label={String(node.data.label)}>
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <PreviewPanelTabs
              tabs={PANES}
              activeTab={pane}
              onChange={showPane}
              ariaLabel="Panes"
              accentColor="var(--model-accent)"
              idPrefix={PANE_ID_PREFIX}
            />
            <div
              id={`${PANE_ID_PREFIX}-${pane}-pane`}
              role="tabpanel"
              aria-labelledby={`${PANE_ID_PREFIX}-${pane}-tab`}
              // The results workspace's text size and accents, which its charts and tables
              // are laid out and coloured for.
              className="validation-workspace min-h-0 flex-1 overflow-y-auto px-4 py-3"
              style={PANE_ACCENTS}
            >
              {pane === "factor" && (
                <FactorChart nodeId={node.id} models={models} chosenId={model.id} hiddenIds={hiddenModels} factor={factor} />
              )}
              {pane === "curve" && <LearningCurve nodeId={node.id} modelId={model.id} />}
              {pane === "tables" && <Tables nodeId={node.id} modelId={model.id} />}
              {pane === "paths" && <Suspense fallback={<ChartEmptyState>Loading paths…</ChartEmptyState>}>
                <ModelPaths nodeId={node.id} modelId={model.id} readOnly={readOnly} />
              </Suspense>}
              {pane === "explain" && <Explain nodeId={node.id} modelId={model.id} factor={factor} onSelectFactor={selectFactor} />}
              {pane === "fit" && <Fit nodeId={node.id} modelId={model.id} />}
            </div>
          </div>
          {/* The pipeline's side panel: it slides in, its left edge resizes it and its red X
              closes it. Retain the editor and its running stage while it is closed or a
              toolbar panel covers it. */}
          <SidePanel
            title={<h3 className="side-panel-title">{shown.heading}</h3>}
            subtitle={modelPanel ? <span className="block truncate text-[11px]" style={{ color: "var(--text-muted)" }}>{model.name}</span> : undefined}
            onClose={closePanel}
            width={panelWidth}
            onWidthChange={setPanelWidth}
            minWidth={MIN_PANEL_WIDTH}
            maxWidth={() => halfWindowWidth(MIN_PANEL_WIDTH)}
            style={settingsVisible ? undefined : { display: "none" }}
            testId="modelling-settings-panel"
          >
            <aside
              aria-label={modelPanel ? `${model.name} settings` : shown.heading}
              hidden={!settingsVisible}
              className={`${settingsVisible ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}
            >
              <div
                hidden={!showFeatures}
                className={showFeatures ? "relative flex min-h-0 flex-1 flex-col" : "hidden"}
              >
                <nav aria-label="Factors" className="flex min-h-0 flex-1 flex-col px-3 py-3">
                  {columns.length === 0
                    ? <ColumnsStatus preview={preview} onRefresh={() => onRefreshNode(node.id)} />
                    : <FactorList config={flat} columns={columns} selected={factor} onSelect={selectFactor} onUpdate={onUpdate} readOnly={readOnly} />}
                </nav>
              </div>
              {/* Retain drafts and running-stage controls across tab changes. */}
              <div
                hidden={showFeatures}
                className={showFeatures ? "hidden" : "relative min-h-0 flex-1 overflow-y-auto"}
                inert={readOnly ? true : undefined}
              >
                {/* One editor per model across every panel: its drafts, estimate and training job are its own. */}
                <ModellingConfig
                  key={model.id}
                  nodeId={node.id}
                  modelId={model.id}
                  config={flat}
                  onUpdate={onUpdate}
                  upstreamColumns={columns}
                  panes={editorPanes}
                  chosenFactor={factor}
                  onReviewPane={reviewPane}
                  reviewablePanes={PANEL_ORDER.map((other) => PANELS[other].pane).filter((candidate) => candidate !== shown.pane)}
                  onTrainAction={setTrainAction}
                  stagedTraining
                  stagedPane={stagedPane}
                  onAppliedStage={(updates) => commitAppliedRecipe(model.id, updates)}
                />
              </div>
              <TrainFailure name={model.name} action={trainAction} />
            </aside>
          </SidePanel>
        </div>
      </main>
    </div>
  )
}

/**
 * What stands in the factor list's place while the columns reaching the model are not
 * known: that they are loading, or why the preview that brings them failed, and the way to
 * ask again (in manual calculation, the way to ask at all).
 */
function ColumnsStatus({ preview, onRefresh }: { preview: ModellingViewProps["preview"]; onRefresh: () => void }) {
  if (preview?.status === "loading") {
    return <p role="status" className="px-1 text-[12px]" style={{ color: "var(--text-muted)" }}>Loading columns</p>
  }
  return (
    <div className="space-y-2">
      {preview?.status === "error" && preview.error !== null ? (
        <p role="alert" className="rounded-lg border px-3 py-2 text-[12px] leading-5" style={{ borderColor: "var(--danger-border)", color: "var(--danger-text-soft)", background: "var(--danger-soft-subtle)" }}>
          {preview.error}
        </p>
      ) : (
        <p className="px-1 text-[12px]" style={{ color: "var(--text-muted)" }}>Columns not known yet</p>
      )}
      <button type="button" className="toolbar-btn rounded-md px-2.5 py-1 text-[12px] font-medium" onClick={onRefresh}>
        Refresh
      </button>
    </div>
  )
}

/** A failed run stays visible at the settings column's foot without shifting the chart. */
function TrainFailure({ name, action }: { name: string; action: TrainAction | null }) {
  if (action === null || action.training || action.error === null) return null
  return (
    <p role="alert" className="shrink-0 px-4 py-3 text-[12px]" style={{ color: "var(--danger-text-soft)", background: "var(--danger-soft-subtle)", borderTop: "1px solid var(--chrome-border)" }}>
      {`${name} failed: ${action.error}`}
    </p>
  )
}

/**
 * The selected factor's actual by level over its exposure, with the expected of every
 * ticked model from its latest run: a model that left the factor out is drawn from its
 * predictions, which do not use it, and says so.
 */
function FactorChart({ nodeId, models, chosenId, hiddenIds, factor }: {
  nodeId: string
  models: readonly InteractiveModel[]
  chosenId: string
  hiddenIds: readonly string[]
  factor: string | null
}) {
  const nodeKey = useResultKey(nodeId)
  const results = useNodeResultsStore((s) => s.trainResults)
  const saved = useStagedViews((state) => state.models)
  const document = useDocumentStatusStore(documentExecutionKey)
  if (factor === null) return <ChartEmptyState>No factor selected</ChartEmptyState>
  const comparisonKey = JSON.stringify([document, modelResultKey(nodeKey, chosenId)])
  const comparing = saved[comparisonKey]?.comparison
  const ticked = models.filter((model) => comparing ? model.id === chosenId : !hiddenIds.includes(model.id))
  if (ticked.length === 0) return <ChartEmptyState>No model ticked</ChartEmptyState>
  const series: ModelAveSeries[] = []
  const reasons: string[] = []
  const evidence = new Set<string>()
  let anyRun = false
  for (const model of ticked) {
    const key = modelResultKey(nodeKey, model.id)
    const view = saved[JSON.stringify([document, key])]
    const applied = view?.state?.current?.result.response
    if (applied?.staged) {
      anyRun = true
      const visible = comparing ? [comparing.parent] : view?.visible ?? defaultVisibleStages(applied)
      const snapshots = visible.map((stage) => ({ stage, result: stageResult(applied, stage), preview: false }))
      const candidate = selectedCandidate(view)
      if (candidate?.staged) {
        const stage = candidate.staged.selected_stage
        if (!comparing || stage === comparing.stage) snapshots.push({ stage, result: stageResult(candidate, stage), preview: true })
      }
      for (const snapshot of snapshots) {
        if (!snapshot.result) continue
        evidence.add(evidenceLabel(snapshot.result))
        const fitted = snapshot.result.ave_per_feature.find((entry) => entry.feature === factor)
        const data = fitted ?? snapshot.result.ave_left_out.find((entry) => entry.feature === factor)
        if (!data) { reasons.push(`${model.name} · ${STAGE_LABELS[snapshot.stage]} has no chart of ${factor}`); continue }
        series.push({ id: `${model.id}-${snapshot.preview ? "preview" : snapshot.stage}`,
          name: `${model.name} · ${snapshot.preview ? `${STAGE_LABELS[snapshot.stage]} selected` : STAGE_LABELS[snapshot.stage]}${view?.state?.stale_reason ? " (saved settings)" : ""}`,
          color: modelColor(models.indexOf(model)), chosen: model.id === chosenId, data,
          leftOut: !fitted, stage: snapshot.stage, preview: snapshot.preview,
          evaluationIdentity: snapshot.result.staged?.evaluation_identity })
      }
      continue
    }
    const cached = results[key]
    if (cached === undefined || !hasTrainResult(cached)) continue
    anyRun = true
    const { result } = cached
    evidence.add(evidenceLabel(result))
    const fitted = result.ave_per_feature.find((entry) => entry.feature === factor)
    const leftOut = fitted === undefined ? result.ave_left_out.find((entry) => entry.feature === factor) : undefined
    const data = fitted ?? leftOut
    const base = { id: model.id, name: model.name, color: modelColor(models.indexOf(model)), chosen: model.id === chosenId }
    if (data !== undefined) {
      series.push({ ...base, data, leftOut: fitted === undefined })
      continue
    }
    // The run names the column first in the reason it could not chart it.
    const prefix = `${factor}: `
    const skipped = result.diagnostics_errors.find(
      (entry) => entry.diagnostic === "ave_left_out" && entry.error.startsWith(prefix),
    )
    reasons.push(skipped === undefined
      ? `${model.name}'s latest run does not have ${factor}`
      : `${model.name} has no chart of ${factor}: ${skipped.error.slice(prefix.length)}`)
  }
  if (!anyRun) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  if (!series.length && !reasons.length) return <ChartEmptyState>No stage predictions selected</ChartEmptyState>
  return (
    <>
      {comparing && <div className="mb-3 flex items-center gap-3 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <span>{STAGE_LABELS[comparing.stage]} preview vs {STAGE_LABELS[comparing.parent]}</span>
        <button className="hover-chrome focus-ring rounded px-2 py-1 underline" onClick={() => useStagedViews.getState().update(comparisonKey, { comparison: undefined })}>End comparison</button>
      </div>}
      {evidence.size > 0 && <p className="mb-3 text-[11px]" style={{ color: "var(--text-muted)" }}>{[...evidence].join(" · ")} · actual and predicted outcomes</p>}
      {reasons.map((reason) => (
        <p key={reason} className="mb-2 text-[12px]" style={{ color: "var(--text-muted)" }}>{reason}</p>
      ))}
      {series.length > 0 && <ModelsAveChart key={factor} feature={factor} series={series} />}
    </>
  )
}

/**
 * Per-bag training/held-back curves while boosting; afterwards the saved full
 * fit, with its aligned mean. Post-fit preparation never replaces these curves.
 */
function LearningCurve({ nodeId, modelId }: { nodeId: string; modelId: string }) {
  const resultKey = useModelResultKey(nodeId, modelId)
  const job = useNodeResultsStore((s) => s.trainJobs[resultKey])
  const cached = useNodeResultsStore((s) => s.trainResults[resultKey])
  const key = useStagedViewKey(resultKey)
  const saved = useStagedViews((state) => state.models[key]?.state)
  const full = saved?.current?.result.response
  if (job !== undefined && (!saved?.job || saved.job.operation === "full")) {
    const history = job.progress?.train_loss_history ?? []
    if (history.length < 2) return <ChartEmptyState>Boosting</ChartEmptyState>
    return (
      <ResponsiveChart>
        {(width) => (
          <LossHistoryChart
            fit={{ history, thinned: false, bestIteration: null, intro: null, live: true }}
            width={width}
            height={CURVE_HEIGHT}
          />
        )}
      </ResponsiveChart>
    )
  }
  const built = full && stageResult(full, "full")
  if (built) return <LossTab result={built} height={CURVE_HEIGHT} />
  if (cached === undefined || !hasTrainResult(cached)) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  return <LossTab result={cached.result} height={CURVE_HEIGHT} />
}

/** The latest run's t-boost tables, as the node panel's results show them. */
function Tables({ nodeId, modelId }: { nodeId: string; modelId: string }) {
  const resultKey = useModelResultKey(nodeId, modelId)
  const cached = useNodeResultsStore((s) => s.trainResults[resultKey])
  const key = useStagedViewKey(resultKey)
  const view = useStagedViews((state) => state.models[key])
  const applied = view?.state?.current?.result.response
  const candidate = selectedCandidate(view)
  const selected = view?.tableStage ?? (candidate ? "candidate" : applied?.staged?.selected_stage ?? "full")
  const compact = selected === "candidate" ? candidate : applied
  const requestScope = useRequestScope()
  const activeSource = useSettingsStore((state) => state.activeSource)
  const request = useCallback(() => {
    const target = prepareCanvasRequest(requestScope, nodeId)
    return { graph: target.graph, node_id: target.nodeId, model: modelId, source: activeSource }
  }, [requestScope, nodeId, modelId, activeSource])
  const full = useStageTableResult({ request, viewKey: key, currentRevision: view?.state?.current?.revision,
    selection: selected === "candidate" ? selectedPathPoint(view) : undefined,
    enabled: compact?.diagnostics_view === "compact" })
  const result = compact?.diagnostics_view === "compact" ? full.response : compact
  const built = selected === "candidate" ? result : result && stageResult(result, selected)
  if (applied?.staged) return <>
    <div className="mb-4"><StageButtons<ModelStage | "candidate"> label="Table stage" value={selected}
      onChange={(stage) => useStagedViews.getState().update(key, { tableStage: stage })}
      options={[
        ...(candidate ? [{ key: "candidate" as const, label: "Selected candidate" }] : []),
        ...STAGES.map((stage) => ({ key: stage, label: STAGE_LABELS[stage],
          disabled: !applied.staged?.stages[stage], title: applied.staged?.stages[stage] ? undefined : "Not built yet" })),
      ]} /></div>
    <p className="mb-3 text-[11px]" style={{ color: "var(--text-muted)" }}>{applied.staged.reference_fit === "development" ? "Development reference fit" : "Validation training fit"} · table shapes and support</p>
    {full.error ? <p role="alert">{full.error}</p> : built ? <TBoostTablesTab result={built} />
      : compact?.diagnostics_view === "compact" ? <ChartEmptyState>Loading tables…</ChartEmptyState>
      : <ChartEmptyState>This stage is no longer available. Choose another stage.</ChartEmptyState>}
  </>
  if (cached === undefined || !hasTrainResult(cached)) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  return <TBoostTablesTab result={cached.result} />
}

/** Diagnostics only the applied stage's run keeps, at its result's top level. */
const APPLIED_ONLY = {
  pdp_data: [], shap_summary: [], shap_beeswarm: [], shap_curves: [], feature_importance_loss: [],
  double_lift: [], lorenz_curve: [], lorenz_curve_perfect: [], residuals_histogram: [], residuals_stats: {},
  actual_vs_predicted: [],
} satisfies Partial<TrainResult>
const otherStageResults = new WeakMap<TrainResult, TrainResult>()

/**
 * A model's diagnostics by stage, else the latest run's. A staged model's compact state
 * already holds importance, lift and the residual histogram, so they draw at once; only its
 * SHAP, PDP and scatter are read on demand (`pending` until they arrive), never its tables.
 * Every built stage keeps its own importance, but SHAP, PDP, lift and residuals belong to
 * the applied stage only, so another stage has none.
 */
function useStageDiagnostics(nodeId: string, modelId: string): {
  staged: { applied: ModelStage; built: ModelStage[]; chosen: ModelStage; choose: (stage: ModelStage) => void } | null
  at: (stage: ModelStage | null) => TrainResult | null
  pending: boolean
  error?: string
} {
  const resultKey = useModelResultKey(nodeId, modelId)
  const cached = useNodeResultsStore((s) => s.trainResults[resultKey])
  const key = useStagedViewKey(resultKey)
  const view = useStagedViews((state) => state.models[key])
  const applied = view?.state?.current?.result.response
  const compact = applied?.diagnostics_view === "compact"
  const requestScope = useRequestScope()
  const activeSource = useSettingsStore((state) => state.activeSource)
  const request = useCallback(() => {
    const target = prepareCanvasRequest(requestScope, nodeId)
    return { graph: target.graph, node_id: target.nodeId, model: modelId, source: activeSource }
  }, [requestScope, nodeId, modelId, activeSource])
  const full = useStageTableResult({ request, viewKey: key, currentRevision: view?.state?.current?.revision,
    enabled: compact, read: "panels" })
  if (applied?.staged) {
    const appliedStage = applied.staged.selected_stage
    const built = STAGES.filter((stage) => applied.staged?.stages[stage])
    const chosen = view?.diagnosticStage
    const response = full.response ?? applied
    const at = (stage: ModelStage | null): TrainResult | null => {
      const result = response ? stageResult(response, stage ?? appliedStage) : null
      if (result === null || stage === null || stage === appliedStage) return result
      const other = otherStageResults.get(result) ?? { ...result, ...APPLIED_ONLY }
      otherStageResults.set(result, other)
      return other
    }
    return {
      staged: { applied: appliedStage, built, chosen: chosen !== undefined && built.includes(chosen) ? chosen : appliedStage,
        choose: (stage) => useStagedViews.getState().update(key, { diagnosticStage: stage }) },
      at, pending: compact && !full.response && !full.error, error: full.error,
    }
  }
  const result = cached !== undefined && hasTrainResult(cached) ? cached.result : null
  return { staged: null, at: () => result, pending: false }
}

/** Why a pane's on-demand diagnostics could not be read, above what it can still draw. */
function PaneError({ error }: { error?: string }) {
  return error ? <p role="alert" className="mb-3 text-[12px]" style={{ color: "var(--danger-text-soft)" }}>{error}</p> : null
}

/**
 * A note under a pane's stage buttons. A staged model's panes keep its line while it is
 * empty, so a loading note that comes and goes never moves the charts.
 */
function PaneNote({ children, reserve = false }: { children?: ReactNode; reserve?: boolean }) {
  if (!children && !reserve) return null
  return <p className="mb-3 min-h-4 text-[11px] leading-4" style={{ color: "var(--text-muted)" }}>{children}</p>
}

type ExplainView = "importance" | "pdp" | "shap"

/**
 * What the model learned: its feature importance, and each factor's partial dependence and
 * SHAP curve, the factor picked from a list ranked by importance. The stage buttons pick
 * whose importance; SHAP and PDP come with the applied stage only.
 */
function Explain({ nodeId, modelId, factor, onSelectFactor }: {
  nodeId: string; modelId: string; factor: string | null; onSelectFactor: (factor: string) => void
}) {
  const { staged, at, pending, error } = useStageDiagnostics(nodeId, modelId)
  const [view, setView] = useState<ExplainView>("importance")
  const [search, setSearch] = useState("")
  const stage = staged?.chosen ?? null
  const result = at(stage)
  if (result === null) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  const views: { key: ExplainView; label: string }[] = [
    { key: "importance", label: "Importance" },
    ...(result.pdp_data.length ? [{ key: "pdp" as const, label: "PDP" }] : []),
    ...(result.shap_curves.length ? [{ key: "shap" as const, label: "SHAP curves" }] : []),
  ]
  const shown = views.some((entry) => entry.key === view) ? view : "importance"
  const importance = new Map(result.feature_importance.map((item) => [item.feature, item.importance]))
  const names = [...new Set([...result.pdp_data.map((item) => item.feature), ...result.shap_curves.map((item) => item.feature)])]
  const featureBrowser = {
    features: names.map((feature) => ({ feature, importance: importance.get(feature) ?? 0 })),
    selected: factor !== null && names.includes(factor) ? factor : names[0] ?? null,
    onSelect: onSelectFactor,
    search,
    onSearch: setSearch,
  }
  const note = staged === null || views.length > 1 ? null
    : staged.chosen !== staged.applied ? `SHAP and partial dependence are kept for the applied stage, ${STAGE_LABELS[staged.applied]}, only`
      : pending ? "Loading SHAP and partial dependence…"
        : staged.applied !== "full" ? "SHAP and partial dependence are worked out for the full GBM only" : null
  return <>
    {staged && <div className="mb-3"><StageButtons label="Explain stage" value={staged.chosen} onChange={staged.choose}
      options={STAGES.map((key) => ({ key, label: STAGE_LABELS[key], disabled: !staged.built.includes(key),
        title: staged.built.includes(key) ? undefined : "Not built yet" }))} /></div>}
    <div className="mb-3"><ViewSwitch label="Explain view" options={views} value={shown} onChange={setView} /></div>
    <PaneError error={error} />
    <PaneNote reserve={staged !== null}>{note}</PaneNote>
    {shown === "importance" && <FeaturesTab result={result} />}
    {shown === "pdp" && <PdpTab result={result} featureBrowser={featureBrowser} />}
    {shown === "shap" && <ShapCurvesTab result={result} featureBrowser={featureBrowser} />}
  </>
}

/**
 * How well the model's predictions fit: lift and Lorenz, then the residuals. Only the
 * applied stage's are kept, so its button is the one that can be chosen.
 */
function Fit({ nodeId, modelId }: { nodeId: string; modelId: string }) {
  const { staged, at, pending, error } = useStageDiagnostics(nodeId, modelId)
  const result = at(staged?.applied ?? null)
  if (result === null) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  return <>
    {staged && <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <StageButtons label="Fit stage" value={staged.applied} onChange={() => {}}
        options={STAGES.map((key) => ({ key, label: STAGE_LABELS[key], disabled: key !== staged.applied,
          title: key === staged.applied ? undefined : staged.built.includes(key) ? "Lift and residuals are kept for the applied stage only" : "Not built yet" }))} />
      <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{evidenceLabel(result)} rows</span>
    </div>}
    <PaneError error={error} />
    <PaneNote reserve={staged !== null}>{pending ? "Loading the actual against predicted scatter…" : null}</PaneNote>
    <section aria-label="Lift" className="mb-6"><LiftTab result={result} /></section>
    <section aria-label="Residuals"><ResidualsTab result={result} /></section>
  </>
}
