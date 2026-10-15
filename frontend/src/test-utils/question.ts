/**
 * The page's question in tests (specs/frontend-shared): what is asked, read from the
 * store with the dialog undrawn, and an answer given through it, what waits on the answer
 * run before the test goes on.
 */
import { act } from "@testing-library/react"
import useQuestionStore, { type Question } from "../stores/useQuestionStore"

/** The question open, or null. */
export const asked = (): Question | null => useQuestionStore.getState().question

/** Answer the question open (yes, its other way on, or no), letting what waits on the answer run. */
export async function answerQuestion(choice: "confirm" | "other" | null, reason = ""): Promise<void> {
  if (asked() === null) throw new Error("No question is open")
  await act(async () => {
    useQuestionStore.getState().answer(choice === null ? null : { choice, reason })
  })
}
