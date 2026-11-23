/**
 * The underwriter app's own state (specs/workbench): the release it serves and its sheets,
 * which every quote opens on and the quote list's columns are read from; and the people
 * quotes are with. Its questions are the page's (`useQuestionStore`).
 */
import { create } from "zustand"
import { fetchAppPeople } from "../api/app"
import type { FormSpec, WorkbenchAppReleaseResponse } from "../api/types"
import { answer, newest, requestKind } from "../utils/answers"

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

/** The people asked for last. */
const peopleAsked = requestKind()

const useAppStore = create<AppState>()((set) => ({
  release: null,
  form: null,
  revision: "",
  people: [],
  releaseChanged: false,
  noteReleaseChanged: () => set({ releaseChanged: true }),
  setRelease: (release, form, revision) => set({ release, form, revision }),
  loadPeople: async () => {
    await answer([newest(peopleAsked)], fetchAppPeople(), {
      ok: ({ people }) => set({ people }),
      // Picking from the people is a convenience: a name can still be typed.
      failed: () => set({ people: [] }),
    })
  },
}))

export default useAppStore
