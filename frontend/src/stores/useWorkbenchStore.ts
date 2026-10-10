/**
 * Zustand store for the project's workbench (specs/workbench): whether it is enabled
 * (GET /api/workbench) and where its form is, which view the editor shows (the pipeline
 * editor's or the workbench's), whether the form holds unsaved edits (mirrored from the
 * form store, a lazy chunk, for the editor's navigation guards), and the Workbench
 * Input's tables and sample quote and the Workbench Output's tables from the newest fetch
 * of GET /api/workbench/tables, which the git flows' save can wait for.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { fetchWorkbenchStatus, fetchWorkbenchTables } from "../api/workbench"
import type { WorkbenchTables } from "../utils/workbenchTables"
import useToastStore from "./useToastStore"

/** The editor's views: the pipeline editor's, and the workbench's while it is enabled. */
export type EditorView = "pipeline" | "workbench"

interface WorkbenchState {
  /** Whether the project's workbench is enabled in haute.toml; false until the status says so. */
  enabled: boolean
  /** The form's path as haute.toml names it; null until the status says the workbench is enabled. */
  formPath: string | null
  /** Fetch the status. A failure shows one error toast and leaves the workbench not enabled. */
  load: () => Promise<void>
  /** Which view shows: the pipeline editor's, or the workbench's. */
  activeView: EditorView
  /** Show a view; the workbench's only while it is enabled. */
  showView: (view: EditorView) => void
  /**
   * The form holds unsaved edits: the form store's `dirty`, mirrored here by that store
   * (a lazy chunk) so the editor's guards against losing edits can read it; false until
   * the form store is loaded.
   */
  formDirty: boolean
  /** The workbench's tables from the newest fetch that succeeded; null until one has. */
  tables: WorkbenchTables | null
  /**
   * Fetch the workbench's tables and return the fetch's number, or null, fetching nothing,
   * while the workbench is not enabled. Only the newest fetch publishes: its tables, or one
   * error toast that leaves `tables` as it was. An older fetch that settles later is
   * dropped, toast and all.
   */
  refreshTables: () => number | null
  /**
   * Settles with whether the newest fetch published: true once it has, false once it
   * failed (a fetch a newer one superseded takes the newer one's outcome), and true at
   * once while none is in flight.
   */
  awaitTables: () => Promise<boolean>
}

// Fetches are numbered across the store's life, so a later one always has a larger number.
let latestFetch = 0
/** The newest fetch's settlement: whether it published. */
let settling: Promise<boolean> = Promise.resolve(true)

const useWorkbenchStore = create<WorkbenchState>()((set, get) => ({
  enabled: false,
  formPath: null,
  load: async () => {
    const status = await fetchWorkbenchStatus().catch((error: unknown) => {
      useToastStore.getState().addToast("error", `Could not read the workbench's status: ${apiErrorMessage(error)}`)
      return null
    })
    // While the workbench is not enabled the store stays as it started, so the editor
    // never re-renders on its account.
    if (status?.enabled) set({ enabled: true, formPath: status.form })
  },
  activeView: "pipeline",
  showView: (view) => {
    if (view === "workbench" && !get().enabled) throw new Error("The workbench is not enabled")
    set({ activeView: view })
  },
  formDirty: false,
  tables: null,
  refreshTables: () => {
    if (!get().enabled) return null
    const fetch = ++latestFetch
    settling = fetchWorkbenchTables().then(
      (response): boolean | Promise<boolean> => {
        // Superseded: the newer fetch's outcome is the one that counts.
        if (fetch !== latestFetch) return settling
        set({
          tables: {
            tables: response.tables,
            sample: response.sample,
            responseTables: response.response_tables,
            fetch,
          },
        })
        return true
      },
      (error: unknown): boolean | Promise<boolean> => {
        if (fetch !== latestFetch) return settling
        useToastStore.getState().addToast("error", `Could not fetch the workbench's tables: ${apiErrorMessage(error)}`)
        return false
      },
    )
    return fetch
  },
  awaitTables: () => settling,
}))

export default useWorkbenchStore
