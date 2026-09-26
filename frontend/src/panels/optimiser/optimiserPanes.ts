import type { OptimiserPane } from "../../stores/useUIStore"

export type OptimiserPaneEntry = { key: OptimiserPane; label: string }

const DATA_PANE: OptimiserPaneEntry = { key: "data", label: "Data" }
const FACTORS_PANE: OptimiserPaneEntry = { key: "factors", label: "Factors" }
const TRAILING_PANES: readonly OptimiserPaneEntry[] = [
  { key: "constraints", label: "Constraints" },
  { key: "solve", label: "Solve" },
  { key: "export", label: "Export" },
]

const ONLINE_PANES: readonly OptimiserPaneEntry[] = [DATA_PANE, ...TRAILING_PANES]
const RATEBOOK_PANES: readonly OptimiserPaneEntry[] = [DATA_PANE, FACTORS_PANE, ...TRAILING_PANES]

/** The panes an optimiser node shows, in tab order; Factors exists only in ratebook mode. */
export function optimiserPanesFor(mode: string): readonly OptimiserPaneEntry[] {
  return mode === "ratebook" ? RATEBOOK_PANES : ONLINE_PANES
}

/** The pane to show: the remembered one when this mode has it, else Data. */
export function resolveOptimiserPane(mode: string, remembered: OptimiserPane | undefined): OptimiserPane {
  const panes = optimiserPanesFor(mode)
  return remembered !== undefined && panes.some((pane) => pane.key === remembered) ? remembered : "data"
}
