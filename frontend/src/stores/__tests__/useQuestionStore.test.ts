/**
 * The one question the page asks at a time (specs/frontend-shared): a second answering the
 * first no, and `confirmed` true for yes alone.
 */
import { afterEach, describe, expect, it } from "vitest"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useQuestionStore, { confirmed } from "../useQuestionStore"

describe("useQuestionStore", () => {
  afterEach(() => useQuestionStore.setState({ question: null }))

  it("asks one question at a time: a second answers the first no", async () => {
    const first = confirmed({ title: "One?", confirm: "Yes" })
    const firstId = useQuestionStore.getState().question?.id
    const second = useQuestionStore.getState().ask({ title: "Two?", confirm: "Go", other: "Skip", reason: { label: "Why" } })
    expect(useQuestionStore.getState().question).toMatchObject({ title: "Two?", other: "Skip" })
    expect(useQuestionStore.getState().question?.id).not.toBe(firstId)
    useQuestionStore.getState().answer({ choice: "other", reason: "because" })

    expect(await first).toBe(false)
    expect(await second).toEqual({ choice: "other", reason: "because" })
    expect(useQuestionStore.getState().question).toBeNull()
  })

  it("is answered no as the pipeline's document is adopted anew", async () => {
    const asked = confirmed({ title: "Delete it?", confirm: "Delete" })
    useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    expect(await asked).toBe(false)
    expect(useQuestionStore.getState().question).toBeNull()
  })

  it("is confirmed by yes alone", async () => {
    const yes = confirmed({ title: "Delete it?", confirm: "Delete", danger: true })
    useQuestionStore.getState().answer({ choice: "confirm", reason: "" })
    expect(await yes).toBe(true)

    const no = confirmed({ title: "Delete it?", confirm: "Delete" })
    useQuestionStore.getState().answer(null)
    expect(await no).toBe(false)
  })
})
