import useStoreCompareStore, { currentComparison, environmentState } from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"

/** The pane and query window export only the current selected database comparison. */
export default function useStoreSqlExport() {
  const form = useWorkbenchFormStore((s) => s.form)
  const stale = useWorkbenchFormStore((s) => s.stale)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const configured = useWorkbenchStore((s) => s.environments)
  const state = useStoreCompareStore()
  const { environment } = state
  const { read, compared } = environmentState(state)
  const comparison = currentComparison(state, form)
  let reason: string | null = null
  if (previewing) reason = "Switch to Build to export SQL."
  else if (stale) reason = "The schema changed on disk. Reload it before exporting SQL."
  else if (configured.length === 0) reason = "Configure a database before exporting SQL."
  else if (environment === null) reason = "Select a database on the schema pane to export SQL."
  else if (read.status === "failed") reason = read.message
  else if (compared.status === "failed" && compared.form === form && compared.renames === state.renames && compared.read === read) reason = compared.message
  else if (read.status === "idle") reason = "Refresh the database on the schema pane before exporting SQL."
  else if (comparison === null) reason = "Wait for the database comparison to finish."
  return { environment, comparison, reason }
}
