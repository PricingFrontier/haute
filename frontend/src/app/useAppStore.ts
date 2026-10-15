/**
 * The underwriter app's own state (specs/workbench): the release it serves and its sheets,
 * which the quote list's columns are read from even while a quote saved on earlier sheets
 * has those installed; and the people quotes are with. Its questions are the page's
 * (`useQuestionStore`).
 */
import { create } from "zustand"
import { fetchAppPeople } from "../api/app"
import type { FormSpec, WorkbenchAppReleaseResponse } from "../api/types"

interface AppState {
  release: WorkbenchAppReleaseResponse | null
  /** The release's sheets and their revision, which a price and a save quote. */
  form: FormSpec | null
  revision: string
  people: string[]
  /** The page was drawn for another release than the one serving it now, as a price or a save found. */
  releaseChanged: boolean
  noteReleaseChanged: () => void
  setRelease: (release: WorkbenchAppReleaseResponse, form: FormSpec, revision: string) => void
  loadPeople: () => Promise<void>
}

const useAppStore = create<AppState>()((set) => ({
  release: null,
  form: null,
  revision: "",
  people: [],
  releaseChanged: false,
  noteReleaseChanged: () => set({ releaseChanged: true }),
  setRelease: (release, form, revision) => set({ release, form, revision }),
  loadPeople: async () => {
    try {
      set({ people: (await fetchAppPeople()).people })
    } catch {
      // Picking from the people is a convenience: a name can still be typed.
      set({ people: [] })
    }
  },
}))

export default useAppStore
