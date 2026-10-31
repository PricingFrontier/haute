/**
 * The underwriter app's own state (specs/workbench): the people quotes are with, and the
 * quotes closed by a closed store's refusal, not by a busy pricing's.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({ fetchAppPeople: vi.fn() }))

import { fetchAppPeople } from "../../api/app"
import { ApiError } from "../../api/client"
import useAppStore from "../useAppStore"
import { OPEN } from "./fixtures"

describe("useAppStore", () => {
  afterEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ people: [], quotes: null })
  })

  it("reads the people, and none when they cannot be read", async () => {
    vi.mocked(fetchAppPeople).mockResolvedValueOnce({ people: ["ana", "jo"] }).mockRejectedValueOnce(new Error("down"))
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual(["ana", "jo"])
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual([])
  })

  it("closes the quotes on a closed store's refusal, and not on a busy pricing's", () => {
    useAppStore.setState({ quotes: OPEN })
    const busy = new ApiError("HTTP 503", 503, "The app is busy pricing other quotes: try again in a few seconds.")
    expect(useAppStore.getState().noteQuotesClosed(busy)).toBe(false)
    expect(useAppStore.getState().quotes).toEqual(OPEN)

    const refusal = { message: "The app cannot reach its quote store just now.", error_code: "quote_store_unreachable", differences: [] }
    const closed = new ApiError("HTTP 503", 503, refusal.message, { detail: refusal }, refusal)
    expect(useAppStore.getState().noteQuotesClosed(closed)).toBe(true)
    expect(useAppStore.getState().quotes).toEqual({ ...refusal, open: false })
  })
})
