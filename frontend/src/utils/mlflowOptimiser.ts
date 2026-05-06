/**
 * Pure helpers for inferring optimiser metadata (mode, etc.) from MLflow
 * Run / ModelVersion records.  Lives in `utils/` so editor components and
 * other consumers can share the logic without dragging the React-state
 * `useMlflowBrowser` hook in transitively.
 */

export type OptimiserSelection = {
  metrics?: Record<string, number>
  params?: Record<string, string>
}

function hasRatebookRunMetrics(metrics: Record<string, number>): boolean {
  return metrics.cd_iterations !== undefined
}

/** Infer the optimiser mode (`"ratebook" | "online" | ""`) from the params /
 *  metrics attached to an MLflow run or registered-model version. Params
 *  win when present; metrics are a fallback for older runs that did not log
 *  `mode` explicitly. */
export function optimiserSelectionMode(selection: OptimiserSelection): "ratebook" | "online" | "" {
  if (selection.params?.mode === "ratebook" || selection.params?.mode === "online") {
    return selection.params.mode
  }
  if (selection.metrics?.converged !== undefined) {
    return hasRatebookRunMetrics(selection.metrics) ? "ratebook" : "online"
  }
  return ""
}
