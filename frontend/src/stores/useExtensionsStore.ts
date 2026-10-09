/**
 * Zustand store for installed extensions (specs/extensions): the list
 * GET /api/extensions returns, which view is showing (the pipeline editor or
 * an extension's), the toolbar element the active extension renders its
 * controls into, the showing extension's save, and the Quote Input's tables
 * from the extension that supplies them.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { fetchExtensions, fetchQuoteTables } from "../api/extensions"
import type { ExtensionInfo } from "../api/types"
import { quoteTablesSupplier, type QuoteTables } from "../utils/extensionQuoteTables"
import useToastStore from "./useToastStore"

/** The pipeline editor's own view. The server rejects an extension with this name. */
export const PIPELINE_VIEW = "pipeline"

interface ExtensionsState {
  /** Installed extensions in name order; empty until loaded, and after a failed load. */
  extensions: readonly ExtensionInfo[]
  /** `PIPELINE_VIEW`, or the name of the extension whose view is showing. */
  activeView: string
  /** The toolbar's space for the active extension's controls, while the toolbar renders one. */
  toolbarSlot: HTMLElement | null
  /** Fetch the list. A failure shows one error toast and leaves the list empty. */
  load: () => Promise<void>
  /** Show the pipeline editor or an installed extension's view. */
  showView: (view: string) => void
  setToolbarSlot: (element: HTMLElement | null) => void
  /** The showing extension's save, from its module, while its view is mounted. */
  viewSave: (() => Promise<boolean>) | null
  setViewSave: (save: (() => Promise<boolean>) | null) => void
  /**
   * The toolbar's Save in an extension's view: the view saves its own work and a toast
   * says how it went. False, doing nothing, when the pipeline editor shows or the
   * extension has no save, so Save saves the pipeline instead.
   */
  saveView: () => boolean
  /** The supplier's tables from the newest fetch that succeeded; null until one has. */
  quoteTables: QuoteTables | null
  /**
   * Fetch the Quote Input's tables from the extension that supplies them and return the
   * fetch's number, or null, fetching nothing, when no installed extension does. Only the
   * newest fetch publishes: its tables, or one error toast that leaves `quoteTables` as it
   * was. An older fetch that settles later is dropped, toast and all.
   */
  refreshQuoteTables: () => number | null
}

// Fetches are numbered across the store's life, so a later one always has a larger number.
let latestQuoteTablesFetch = 0

const useExtensionsStore = create<ExtensionsState>()((set, get) => ({
  extensions: [],
  activeView: PIPELINE_VIEW,
  toolbarSlot: null,
  load: async () => {
    const response = await fetchExtensions().catch((error: unknown) => {
      useToastStore.getState().addToast("error", `Could not list Haute extensions: ${apiErrorMessage(error)}`)
      return null
    })
    // With none installed the store stays as it started, so the editor never
    // re-renders on their account.
    if (response && response.extensions.length > 0) set({ extensions: response.extensions })
  },
  showView: (view) => {
    if (view !== PIPELINE_VIEW && !get().extensions.some((extension) => extension.name === view)) {
      throw new Error(`No installed extension is called "${view}"`)
    }
    const leaving = get().activeView
    set({ activeView: view })
    // The extension's view may have saved new tables for the Quote Input.
    if (view === PIPELINE_VIEW && leaving !== PIPELINE_VIEW) get().refreshQuoteTables()
  },
  setToolbarSlot: (toolbarSlot) => set({ toolbarSlot }),
  viewSave: null,
  setViewSave: (viewSave) => set({ viewSave }),
  saveView: () => {
    const { activeView, viewSave, extensions } = get()
    if (activeView === PIPELINE_VIEW || !viewSave) return false
    const label = extensions.find((extension) => extension.name === activeView)?.label ?? activeView
    const { addToast } = useToastStore.getState()
    viewSave().then(
      (saved) => addToast(saved ? "success" : "error", saved ? `Saved ${label}` : `${label} could not be saved; its toolbar says why`),
      (error: unknown) => addToast("error", `${label} could not be saved: ${apiErrorMessage(error)}`),
    )
    return true
  },
  quoteTables: null,
  refreshQuoteTables: () => {
    const supplier = quoteTablesSupplier(get().extensions)
    if (supplier === null) return null
    const fetch = ++latestQuoteTablesFetch
    fetchQuoteTables().then(
      (response) => {
        if (fetch === latestQuoteTablesFetch) {
          set({
            quoteTables: { extension: response.extension, tables: response.tables, sample: response.sample, fetch },
          })
        }
      },
      (error: unknown) => {
        if (fetch !== latestQuoteTablesFetch) return
        useToastStore.getState().addToast(
          "error",
          `Could not fetch the Quote Input's tables from ${supplier.label}: ${apiErrorMessage(error)}`,
        )
      },
    )
    return fetch
  },
}))

export default useExtensionsStore
