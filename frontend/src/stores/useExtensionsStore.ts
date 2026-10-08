/**
 * Zustand store for installed extensions (specs/extensions): the list
 * GET /api/extensions returns, which view is showing (the pipeline editor or
 * an extension's), and the toolbar element the active extension renders its
 * controls into.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { fetchExtensions } from "../api/extensions"
import type { ExtensionInfo } from "../api/types"
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
}

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
    set({ activeView: view })
  },
  setToolbarSlot: (toolbarSlot) => set({ toolbarSlot }),
}))

export default useExtensionsStore
