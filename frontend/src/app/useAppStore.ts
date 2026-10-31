/**
 * The underwriter app's own state (specs/workbench): the release it serves and its sheets,
 * which every quote opens on and the quote list's columns are read from; whether its quotes
 * are open, which every failure of the app's routes may close; and the people quotes are
 * with. Its questions are the page's (`useQuestionStore`).
 */
import { create } from "zustand"
import { fetchAppPeople } from "../api/app"
import type { FormSpec, WorkbenchAppQuoteStore, WorkbenchAppReleaseResponse } from "../api/types"
import { quotesClosedBy } from "./quoteValues"

interface AppState {
  release: WorkbenchAppReleaseResponse | null
  /** The release's sheets and their revision, which a price and a save quote. */
  form: FormSpec | null
  revision: string
  people: string[]
  /** The page was drawn for another release than the one serving it now, as a price or a save found. */
  releaseChanged: boolean
  noteReleaseChanged: () => void
  /** The release's quote store as it last answered: open, or closed naming why. */
  quotes: WorkbenchAppQuoteStore | null
  setQuotes: (quotes: WorkbenchAppQuoteStore) => void
  /** Close the quotes when `error` is a route's refusal of a closed store; whether it was. */
  noteQuotesClosed: (error: unknown) => boolean
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
  quotes: null,
  setQuotes: (quotes) => set({ quotes }),
  noteQuotesClosed: (error) => {
    const closed = quotesClosedBy(error)
    if (closed !== null) set({ quotes: closed })
    return closed !== null
  },
  setRelease: (release, form, revision) => set({ release, form, revision, quotes: release.quotes }),
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
