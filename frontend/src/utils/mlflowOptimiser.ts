/**
 * Pure helpers for inferring optimiser metadata (mode, etc.) from MLflow
 * Run / ModelVersion records.  Lives in `utils/` so editor components and
 * other consumers can share the logic without dragging the React-state
 * `useMlflowBrowser` hook in transitively.
 */

export type OptimiserSelection = {
  params?: Record<string, string>
}

/** Read the optimiser mode (`"ratebook" | "online" | ""`) from the params
 * attached to an MLflow run or registered-model version. */
export function optimiserSelectionMode(selection: OptimiserSelection): "ratebook" | "online" | "" {
  if (selection.params?.mode === "ratebook" || selection.params?.mode === "online") {
    return selection.params.mode
  }
  return ""
}
