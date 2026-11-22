import { evidenceLabel } from "./stageEvidence"
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useLayoutEffect, useState, type CSSProperties, type ReactNode } from "react"
import { ErrorBoundary } from "../components/ErrorBoundary"
import { PaletteColumn, PaletteHeader, PaletteRevealStrip } from "../haute-ui"
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
import { clearCachedResultShape, useUpstreamColumns } from "../panels/nodePanelData"
import { removeStages } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import useSettingsStore from "../stores/useSettingsStore"
import { useResultKey, useRequestScope } from "../panels/useGraph"
import { hasTrainResult } from "../stores/nodeResultsSelectors"
import useModellingViewStore, { type ModelPane, type SettingsPanel, type TrainAction } from "../stores/useModellingViewStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ModellingPane } from "../stores/useUIStore"
import { NODE_TYPES } from "../utils/nodeTypes"
import { documentRequestScope, prepareCanvasRequest } from "../utils/requestGateway"
import ViewSwitcher from "../workbench/ViewSwitcher"
import FactorList from "./FactorList"
import { StageRestorer } from "./StageRestorer"
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
 * The panes of the node's editor each right-hand panel shows, and its heading: the chosen
 * model's own settings, then the toolbar's Model Setup (the settings every model shares)
 * and Training Split. The model's parameter pane contains its staged Training controls.
 */
const PANELS: Readonly<Record<SettingsPanel, { heading: string | null; panes: readonly ModellingPane[] }>> = {
  model: { heading: null, panes: ["features", "params", "train"] },
  setup: { heading: "Model setup", panes: ["target"] },
  split: { heading: "Training split", panes: ["split"] },
}
type ModelSettingsPane = "features" | "params" | "train"
const MODEL_SETTINGS_TABS: readonly PreviewPanelTab<ModelSettingsPane>[] = [
  { key: "features", label: "Features" },
  { key: "params", label: "Parameters" },
  { key: "train", label: "Training" },
]
const SETTINGS_ID = "modelling-settings"
const PANEL_ORDER: readonly SettingsPanel[] = ["model", "setup", "split"]

/**
 * The panes of a model, along the top of the area between its factors and its settings:
 * the one every model shares, then the chosen model's own.
 */
const PANES: readonly PreviewPanelTab<ModelPane>[] = [
  { key: "factor", label: "Factor" },
  { key: "curve", label: "Learning curve" },
  { key: "tables", label: "Tables" },
  { key: "paths", label: "Paths" },
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
  const holder = PANEL_ORDER.find((panel) => PANELS[panel].panes.includes(pane))
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
 * canvas shows, and opens one: its models down the left, its factors in Features on the right,
 * the pane chosen, and on the right the chosen model's settings, or Model Setup or Training
 * Split from the toolbar. The toolbar's Git, Files and Assistant panels open
 * at the view's right, in the settings' place.
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
    <div className="absolute inset-0 flex" data-testid="modelling-view" style={{ background: "var(--bg-base)" }}>
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
  const [settingsPane, setSettingsPane] = useState<ModelSettingsPane>("features")
  const showPanel = useModellingViewStore((s) => s.showPanel)
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
      showPanel("model")
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

  const shown = PANELS[panel]
  const showFeatures = panel === "model" && settingsPane === "features"
  const stagedPane = settingsPane === "train" ? "train" : "params"
  const editorPanes: readonly ModellingPane[] = panel === "model" ? ["params"] : shown.panes
  const visiblePane = panel === "model" ? settingsPane : shown.panes[0]
  const reviewPane = (next: ModellingPane) => {
    showPanel(panelForPane(next))
    if (next === "features" || next === "params" || next === "train") setSettingsPane(next)
  }
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
              showPanel("model")
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
            </div>
          </div>
          {/* Retain the editor and its running stage when a toolbar panel covers it. */}
          <aside
            aria-label={shown.heading ?? `${model.name} settings`}
            hidden={!showSettings}
            className={`${showSettings ? "flex" : "hidden"} min-h-0 w-[400px] shrink-0 flex-col`}
            style={{ background: "var(--bg-panel)", borderLeft: "1px solid var(--chrome-border)" }}
          >
            {shown.heading !== null && <h3 className="shrink-0 px-4 py-3 text-[13px] font-semibold">{shown.heading}</h3>}
            {panel === "model" && (
              <PreviewPanelTabs tabs={MODEL_SETTINGS_TABS} activeTab={settingsPane} onChange={setSettingsPane} ariaLabel="Model settings" accentColor="var(--model-accent)" idPrefix={SETTINGS_ID} />
            )}
            <div
              hidden={!showFeatures}
              className={showFeatures ? "relative flex min-h-0 flex-1 flex-col" : "hidden"}
              role="tabpanel"
              id={`${SETTINGS_ID}-features-pane`}
              aria-labelledby={`${SETTINGS_ID}-features-tab`}
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
              role={panel === "model" ? "tabpanel" : undefined}
              id={panel === "model" ? `${SETTINGS_ID}-${stagedPane}-pane` : undefined}
              aria-labelledby={panel === "model" ? `${SETTINGS_ID}-${stagedPane}-tab` : undefined}
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
                reviewablePanes={PANEL_ORDER.flatMap((other) => PANELS[other].panes).filter((candidate) => candidate !== visiblePane)}
                onTrainAction={setTrainAction}
                stagedTraining
                stagedPane={stagedPane}
                onAppliedStage={(updates) => commitAppliedRecipe(model.id, updates)}
              />
            </div>
            <TrainFailure name={model.name} action={trainAction} />
          </aside>
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
    <label className="mb-4 flex items-center gap-3 text-[12px]">Stage
      <select aria-label="Table stage" className="rounded border px-2 py-1" value={selected}
        style={{ background: "var(--bg-panel)", borderColor: "var(--border)" }}
        onChange={(event) => useStagedViews.getState().update(key, { tableStage: event.target.value as ModelStage | "candidate" })}>
        {candidate && <option value="candidate">Selected candidate</option>}
        {STAGES.map((stage) => <option key={stage} value={stage} disabled={!applied.staged?.stages[stage]}>{STAGE_LABELS[stage]}</option>)}
      </select>
    </label>
    <p className="mb-3 text-[11px]" style={{ color: "var(--text-muted)" }}>{applied.staged.reference_fit === "development" ? "Development reference fit" : "Validation training fit"} · table shapes and support</p>
    {full.error ? <p role="alert">{full.error}</p> : built ? <TBoostTablesTab result={built} />
      : compact?.diagnostics_view === "compact" ? <ChartEmptyState>Loading tables…</ChartEmptyState>
      : <ChartEmptyState>This stage is no longer available. Choose another stage.</ChartEmptyState>}
  </>
  if (cached === undefined || !hasTrainResult(cached)) return <ChartEmptyState>Not boosted yet</ChartEmptyState>
  return <TBoostTablesTab result={cached.result} />
}

