/**
 * Zustand store for the project's workbench (specs/workbench): whether it is enabled
 * (GET /api/workbench) and where its form is, which view the editor shows (the pipeline
 * editor's or the workbench's), whether the form holds unsaved edits (mirrored from the
 * form store, a lazy chunk, for the editor's navigation guards), and the Workbench
 * Input's tables and sample quote and the Workbench Output's tables from the newest fetch
 * of GET /api/workbench/tables, which are the nodes' ports and which the project's save
 * waits for, with the columns the quote store keeps in each table that the form does not.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchStoreColumn } from "../api/types"
import { fetchWorkbenchStatus, fetchWorkbenchTables } from "../api/workbench"
import { canonicalJson } from "../utils/canonicalJson"
import type { WorkbenchTables } from "../utils/workbenchTables"
import useGraphStore from "./useGraphStore"
import useNodeDataStore from "./useNodeDataStore"
import useToastStore from "./useToastStore"
import { latest } from "../utils/latest"

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
  /**
   * How many times the form's file may have changed on disk: each change the server
   * announces (an edit by hand, a pull or a merge outside Haute, a save from another
   * window), and each connection to it, since a change while disconnected went
   * unannounced. Counted here, on the eager store, for the form store (a lazy chunk),
   * which reads the file again at each.
   */
  formChanges: number
  /** The workbench's tables from the newest fetch that succeeded; null until one has. */
  tables: WorkbenchTables | null
  /**
   * The columns the quote store keeps in each of the form's tables that the form does not,
   * by table id, from the same fetch: quote_details' own, and a many-row table's `quote_row`.
   * They are no node's ports.
   */
  storeColumns: Readonly<Record<string, readonly WorkbenchStoreColumn[]>>
  /**
   * Fetch the workbench's tables and return the fetch's number, or null, fetching nothing,
   * while the workbench is not enabled. Only the newest fetch publishes: its tables, or one
   * error toast that leaves `tables` as it was. An older fetch that settles later is
   * dropped, toast and all. A workbench node is given its tables and sample wherever it
   * runs, so they are its config, though nothing in the graph holds them: a fetch whose
   * tables, sample or response tables differ from the last one's advances the graph's
   * structural version and raises the node-data epoch, so every preview, recorded column and
   * reader of node data is as stale as a config change makes it; a fetch that brings the
   * same leaves them be.
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
const fetches = latest()
/** What a fetch gives the workbench nodes, compared by content: the order of keys never reads as a change. */
const given = (workbench: WorkbenchTables): string =>
  canonicalJson([workbench.tables, workbench.sample, workbench.responseTables])
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
  formChanges: 0,
  tables: null,
  storeColumns: {},
  refreshTables: () => {
    if (!get().enabled) return null
    const fetch = fetches.take()
    settling = fetchWorkbenchTables().then(
      (response): boolean | Promise<boolean> => {
        // Superseded: the newer fetch's outcome is the one that counts.
        if (!fetches.is(fetch)) return settling
        const previous = get().tables
        const tables = {
          tables: response.tables,
          sample: response.sample,
          responseTables: response.response_tables,
          fetch,
        }
        set({ tables, storeColumns: response.store_columns })
        if (previous !== null && given(previous) !== given(tables)) {
          // The graph's structure as every reader keys it: a preview, a recorded column, a
          // job's staleness.
          useGraphStore.getState().noteStructureOutsideGraph()
          useNodeDataStore.getState().bumpEpoch()
        }
        return true
      },
      (error: unknown): boolean | Promise<boolean> => {
        if (!fetches.is(fetch)) return settling
        useToastStore.getState().addToast("error", `Could not fetch the workbench's tables: ${apiErrorMessage(error)}`)
        return false
      },
    )
    return fetch
  },
  awaitTables: () => settling,
}))

export default useWorkbenchStore
