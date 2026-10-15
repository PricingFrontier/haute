/**
 * The underwriter app's own state (specs/workbench): the release it serves and its sheets,
 * which the quote list's columns are read from even while a quote saved on earlier sheets
 * has those installed; the people quotes are with; and the one question the app asks at a
 * time, answered yes or no.
 */
import { create } from "zustand"
import { fetchAppPeople } from "../api/app"
import type { FormSpec, WorkbenchAppReleaseResponse } from "../api/types"

export interface Question {
  /** Which question this is: each asked is another. */
  id: number
  title: string
  text: string
  confirm: string
  /** A reason is asked for too, required before the question can be answered yes. */
  reason?: { label: string }
  /** Answered: yes with the reason when one was asked for, or null for no. */
  answer: (answer: { reason: string } | null) => void
}

interface AppState {
  release: WorkbenchAppReleaseResponse | null
  /** The release's sheets and their revision, which a price and a save quote. */
  form: FormSpec | null
  revision: string
  people: string[]
  question: Question | null
  /** The page was drawn for another release than the one serving it now, as a price or a save found. */
  releaseChanged: boolean
  noteReleaseChanged: () => void
  setRelease: (release: WorkbenchAppReleaseResponse, form: FormSpec, revision: string) => void
  loadPeople: () => Promise<void>
  /** Ask `question`; the answer, or null for no. */
  ask: (question: Omit<Question, "answer" | "id">) => Promise<{ reason: string } | null>
  answer: (answer: { reason: string } | null) => void
}

let questions = 0

const useAppStore = create<AppState>()((set, get) => ({
  release: null,
  form: null,
  revision: "",
  people: [],
  question: null,
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
  ask: (question) =>
    new Promise((resolve) => {
      get().question?.answer(null)
      questions += 1
      set({ question: { ...question, id: questions, answer: resolve } })
    }),
  answer: (answer) => {
    const { question } = get()
    set({ question: null })
    question?.answer(answer)
  },
}))

/** Ask a yes-or-no question; true for yes. */
export async function confirm(title: string, text: string, confirmLabel: string): Promise<boolean> {
  return (await useAppStore.getState().ask({ title, text, confirm: confirmLabel })) !== null
}

export default useAppStore
