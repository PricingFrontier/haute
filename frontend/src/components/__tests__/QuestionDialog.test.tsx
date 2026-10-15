/**
 * The page's question (specs/frontend-shared): its title, text and list, Cancel, Escape and
 * a press outside answering no, its yes and its other way on, a reason required before
 * either, the reason focused, and the yes in the danger colour for what deletes.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import useQuestionStore, { type Answer, type QuestionAsked } from "../../stores/useQuestionStore"
import QuestionDialog from "../QuestionDialog"

/** Ask `question`, the dialog drawn once in a test; the answer's promise. */
function asking(question: QuestionAsked): Promise<Answer | null> {
  if (document.querySelector("[data-question-host]") === null) render(<div data-question-host><QuestionDialog /></div>)
  let answer: Promise<Answer | null> = Promise.resolve(null)
  act(() => {
    answer = useQuestionStore.getState().ask(question)
  })
  return answer
}

describe("QuestionDialog", () => {
  afterEach(() => {
    cleanup()
    useQuestionStore.setState({ question: null })
  })

  it("draws nothing until a question is asked", () => {
    render(<QuestionDialog />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("shows the question's title, text and list, and answers yes from its button, in the danger colour for a delete", async () => {
    const answer = asking({ title: "Replace 2 files in params?", text: "They can't be recovered.", confirm: "Replace", list: ["a.csv", "b.csv"] })

    expect(screen.getByRole("dialog", { name: "Replace 2 files in params?" })).toHaveTextContent("They can't be recovered.")
    expect(screen.getByTestId("question-list")).toHaveTextContent("a.csvb.csv")
    expect(screen.getByTestId("question-confirm")).toHaveStyle({ background: "var(--structure-action)" })
    fireEvent.click(screen.getByTestId("question-confirm"))
    expect(await answer).toEqual({ choice: "confirm", reason: "" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    void asking({ title: "Delete it?", confirm: "Delete", danger: true })
    expect(screen.getByTestId("question-confirm")).toHaveStyle({ background: "var(--danger)" })
    expect(screen.queryByTestId("question-other")).not.toBeInTheDocument()
  })

  it("answers no from Cancel and from Escape, and the other way on from its button", async () => {
    const cancelled = asking({ title: "Delete it?", confirm: "Delete" })
    fireEvent.click(screen.getByTestId("question-cancel"))
    expect(await cancelled).toBeNull()

    const escaped = asking({ title: "Delete it?", confirm: "Delete" })
    fireEvent.keyDown(document, { key: "Escape" })
    expect(await escaped).toBeNull()

    const skipped = asking({ title: "Replace a.csv?", confirm: "Replace", other: "Skip these" })
    fireEvent.click(screen.getByRole("button", { name: "Skip these" }))
    expect(await skipped).toEqual({ choice: "other", reason: "" })
  })

  it("asks for the reason, focused, before it can be answered yes", async () => {
    const answer = asking({ title: "Reopen Q-1?", confirm: "Reopen", reason: { label: "Why it is being reopened" } })
    const reason = screen.getByLabelText("Why it is being reopened")
    expect(document.activeElement).toBe(reason)
    expect(screen.getByTestId("question-confirm")).toBeDisabled()

    fireEvent.change(reason, { target: { value: "  wrong excess  " } })
    fireEvent.click(screen.getByTestId("question-confirm"))
    expect(await answer).toEqual({ choice: "confirm", reason: "wrong excess" })
  })
})
