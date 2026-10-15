/**
 * The one question the page asks at a time (specs/frontend-shared), in a dialog of the
 * page's own (`QuestionDialog`), never the browser's: whether to go on with an action that
 * deletes or discards, a second way on beside it (Skip these, beside Replace), and a
 * reason when one is needed. A question asked while another is open answers that one no.
 */
import { create } from "zustand"

export interface QuestionAsked {
  title: string
  /** What follows from the answer, under the title. */
  text?: string
  /** The button that answers yes, named for what it does. */
  confirm: string
  /** The yes in the danger colour: the action deletes or discards. */
  danger?: boolean
  /** A second way on beside the yes, as Skip these beside Replace. */
  other?: string
  /** Names listed under the text, as the files a replace overwrites. */
  list?: readonly string[]
  /** A reason asked for too, required before the question can be answered either way on. */
  reason?: { label: string }
}

/** How a question was answered: yes, or its other way on, with the reason when one was asked for. */
export interface Answer {
  choice: "confirm" | "other"
  reason: string
}

export interface Question extends QuestionAsked {
  /** Which question this is: each asked is another. */
  id: number
  answer: (answer: Answer | null) => void
}

interface QuestionState {
  question: Question | null
  /** Ask `question`: its answer, or null for no. */
  ask: (question: QuestionAsked) => Promise<Answer | null>
  /** Answer the question open; null for no. */
  answer: (answer: Answer | null) => void
}

let asked = 0

const useQuestionStore = create<QuestionState>()((set, get) => ({
  question: null,
  ask: (question) =>
    new Promise((resolve) => {
      get().question?.answer(null)
      asked += 1
      set({ question: { ...question, id: asked, answer: resolve } })
    }),
  answer: (answer) => {
    const { question } = get()
    set({ question: null })
    question?.answer(answer)
  },
}))

/** Ask whether to go on; true for yes. */
export async function confirmed(question: Omit<QuestionAsked, "other" | "reason">): Promise<boolean> {
  return (await useQuestionStore.getState().ask(question))?.choice === "confirm"
}

export default useQuestionStore
