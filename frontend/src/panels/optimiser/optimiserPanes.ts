import type { OptimiserPane } from "../../stores/useUIStore"

export type OptimiserPaneEntry = { key: OptimiserPane; label: string }

/** The panes an optimiser node shows, in tab order. Factors holds the ratebook
 * and validation factors, so both modes have it. */
export const OPTIMISER_PANES: readonly OptimiserPaneEntry[] = [
  { key: "data", label: "Data" },
  { key: "factors", label: "Factors" },
  { key: "constraints", label: "Constraints" },
  { key: "solve", label: "Solve" },
  { key: "export", label: "Export" },
]

/** The pane to show: the remembered one, else Data. */
export function resolveOptimiserPane(remembered: OptimiserPane | undefined): OptimiserPane {
  return remembered !== undefined && OPTIMISER_PANES.some((pane) => pane.key === remembered) ? remembered : "data"
}
