/**
 * Zustand store for the project's workbench (specs/workbench): whether it is enabled
 * (GET /api/workbench), and the Workbench Input's tables and sample quote and the
 * Workbench Output's tables from the newest fetch of GET /api/workbench/tables.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { fetchWorkbenchStatus, fetchWorkbenchTables } from "../api/workbench"
import type { WorkbenchTables } from "../utils/workbenchTables"
import useToastStore from "./useToastStore"

interface WorkbenchState {
  /** Whether the project's workbench is enabled in haute.toml; false until the status says so. */
  enabled: boolean
  /** Fetch the status. A failure shows one error toast and leaves the workbench not enabled. */
  load: () => Promise<void>
  /** The workbench's tables from the newest fetch that succeeded; null until one has. */
  tables: WorkbenchTables | null
  /**
   * Fetch the workbench's tables and return the fetch's number, or null, fetching nothing,
   * while the workbench is not enabled. Only the newest fetch publishes: its tables, or one
   * error toast that leaves `tables` as it was. An older fetch that settles later is
   * dropped, toast and all.
   */
  refreshTables: () => number | null
}

// Fetches are numbered across the store's life, so a later one always has a larger number.
let latestFetch = 0

const useWorkbenchStore = create<WorkbenchState>()((set, get) => ({
  enabled: false,
  load: async () => {
    const status = await fetchWorkbenchStatus().catch((error: unknown) => {
      useToastStore.getState().addToast("error", `Could not read the workbench's status: ${apiErrorMessage(error)}`)
      return null
    })
    // While the workbench is not enabled the store stays as it started, so the editor
    // never re-renders on its account.
    if (status?.enabled) set({ enabled: true })
  },
  tables: null,
  refreshTables: () => {
    if (!get().enabled) return null
    const fetch = ++latestFetch
    fetchWorkbenchTables().then(
      (response) => {
        if (fetch === latestFetch) {
          set({
            tables: {
              tables: response.tables,
              sample: response.sample,
              responseTables: response.response_tables,
              fetch,
            },
          })
        }
      },
      (error: unknown) => {
        if (fetch !== latestFetch) return
        useToastStore.getState().addToast("error", `Could not fetch the workbench's tables: ${apiErrorMessage(error)}`)
      },
    )
    return fetch
  },
}))

export default useWorkbenchStore
