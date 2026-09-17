import type { ModellingPane } from "../../stores/useUIStore"

export type ModellingPaneEntry = { key: ModellingPane; label: string }

const CATBOOST_PANES: readonly ModellingPaneEntry[] = [
  { key: "target", label: "Target" },
  { key: "features", label: "Features" },
  { key: "params", label: "Params" },
  { key: "split", label: "Split" },
  { key: "train", label: "Train" },
  { key: "export", label: "Export" },
]
// A GLM's parameters live in its Target and Features panes.
const GLM_PANES: readonly ModellingPaneEntry[] = CATBOOST_PANES.filter((pane) => pane.key !== "params")

/** The panes a modelling node shows, in tab order; empty until an algorithm is chosen. */
export function modellingPanesFor(algorithm: string): readonly ModellingPaneEntry[] {
  const normalized = algorithm.toLowerCase()
  if (normalized === "catboost") return CATBOOST_PANES
  if (normalized === "glm") return GLM_PANES
  return []
}

/** The pane to show: the remembered one when this algorithm has it, else Target. */
export function resolveModellingPane(algorithm: string, remembered: ModellingPane | undefined): ModellingPane {
  const panes = modellingPanesFor(algorithm)
  return remembered !== undefined && panes.some((pane) => pane.key === remembered) ? remembered : "target"
}
