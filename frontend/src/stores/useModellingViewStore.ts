/**
 * Zustand store for the Modelling view's own state while it shows
 * (specs/frontend-modelling-optimiser-ui): the node open in it, its model chosen, the
 * models left off the charts, the settings panel that shows on the right, the pane that
 * shows, the factor charted, and the chosen model's train action for the toolbar. None of
 * it is part of the pipeline, so none is saved or undone.
 */
import { create } from "zustand"
import useWorkbenchStore from "./useWorkbenchStore"

/**
 * What the right-hand column shows: the chosen model's own settings, or, from the
 * toolbar, Model Setup (the settings every model shares) or Training Split.
 */
export type SettingsPanel = "model" | "setup" | "split"

/** The panes of a model: what the area between its factors and its settings shows. */
export type ModelPane = "factor" | "curve" | "tables" | "paths"

/** The chosen model's training, as the toolbar's Train button runs it. */
export interface TrainAction {
  /** The model is training: the button cancels it. */
  training: boolean
  /** A request is on its way: the button waits. */
  busy: boolean
  /** Why the model cannot train now, each as the editor words it; empty when it can. */
  issues: readonly string[]
  /** The latest run's failure, when it failed. */
  error: string | null
  train: () => void
  cancel: () => void
}

interface ModellingViewState {
  /** The node of the model open in the view; null while the list of models shows. */
  modelId: string | null
  /** The id of the node's model chosen in the top row; null until one is, the first then. */
  chosenModel: string | null
  /** The ids of the node's models whose box is unticked: they are left off the charts. */
  hiddenModels: readonly string[]
  panel: SettingsPanel
  pane: ModelPane
  /** The factor whose chart shows; null until one is chosen. */
  factor: string | null
  /** The chosen model's train action, which its editor publishes; null while there is none. */
  trainAction: TrainAction | null
  /**
   * Open a model at its first model, its settings and the Factor pane, every model ticked;
   * opening the one already open keeps them.
   */
  openModel: (nodeId: string) => void
  /** Choose one of the node's models: its settings, panes and training show. */
  chooseModel: (modelId: string) => void
  /** Tick or untick a model: whether its line is drawn on the charts. */
  setModelShown: (modelId: string, shown: boolean) => void
  /** Back to the list of models. */
  closeModel: () => void
  /** Show a settings panel; asking for the one that shows goes back to the model's. */
  togglePanel: (panel: SettingsPanel) => void
  showPanel: (panel: SettingsPanel) => void
  showPane: (pane: ModelPane) => void
  /** Chart a factor: choosing one shows the factor chart. */
  selectFactor: (factor: string | null) => void
  setTrainAction: (action: TrainAction | null) => void
}

const useModellingViewStore = create<ModellingViewState>()((set) => ({
  modelId: null,
  chosenModel: null,
  hiddenModels: [],
  panel: "model",
  pane: "factor",
  factor: null,
  trainAction: null,
  openModel: (modelId) =>
    set((state) => (state.modelId === modelId
      ? {}
      : { modelId, chosenModel: null, hiddenModels: [], panel: "model", pane: "factor", factor: null })),
  chooseModel: (chosenModel) => set({ chosenModel }),
  setModelShown: (modelId, shown) => set((state) => ({
    hiddenModels: shown
      ? state.hiddenModels.filter((id) => id !== modelId)
      : [...state.hiddenModels.filter((id) => id !== modelId), modelId],
  })),
  closeModel: () => set({ modelId: null, chosenModel: null, hiddenModels: [], panel: "model", factor: null }),
  togglePanel: (panel) => set((state) => ({ panel: state.panel === panel ? "model" : panel })),
  showPanel: (panel) => set({ panel }),
  showPane: (pane) => set({ pane }),
  selectFactor: (factor) => set((state) => factor === null ? { factor } : { factor, pane: state.pane === "paths" ? "paths" : "factor" }),
  setTrainAction: (trainAction) => set({ trainAction }),
}))

/** Open a model in the Modelling view and show the view. */
export function openModelInModellingView(nodeId: string): void {
  useModellingViewStore.getState().openModel(nodeId)
  useWorkbenchStore.getState().showView("modelling")
}

export default useModellingViewStore
