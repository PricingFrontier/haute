/**
 * The underwriter app's own state (specs/workbench): the people quotes are with, and one
 * question asked at a time, a second answering the first no.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({ fetchAppPeople: vi.fn() }))

import { fetchAppPeople } from "../../api/app"
import useAppStore, { confirm } from "../useAppStore"

describe("useAppStore", () => {
  afterEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ people: [], question: null })
  })

  it("reads the people, and none when they cannot be read", async () => {
    vi.mocked(fetchAppPeople).mockResolvedValueOnce({ people: ["ana", "jo"] }).mockRejectedValueOnce(new Error("down"))
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual(["ana", "jo"])
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual([])
  })

  it("asks one question at a time: a second answers the first no", async () => {
    const first = confirm("One?", "", "Yes")
    const firstId = useAppStore.getState().question?.id
    const second = useAppStore.getState().ask({ title: "Two?", text: "", confirm: "Go", reason: { label: "Why" } })
    expect(useAppStore.getState().question?.id).not.toBe(firstId)
    useAppStore.getState().answer({ reason: "because" })

    expect(await first).toBe(false)
    expect(await second).toEqual({ reason: "because" })
    expect(useAppStore.getState().question).toBeNull()
  })
})
