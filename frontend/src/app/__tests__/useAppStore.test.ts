/**
 * The underwriter app's own state (specs/workbench): the people quotes are with.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({ fetchAppPeople: vi.fn() }))

import { fetchAppPeople } from "../../api/app"
import useAppStore from "../useAppStore"

describe("useAppStore", () => {
  afterEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ people: [] })
  })

  it("reads the people, and none when they cannot be read", async () => {
    vi.mocked(fetchAppPeople).mockResolvedValueOnce({ people: ["ana", "jo"] }).mockRejectedValueOnce(new Error("down"))
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual(["ana", "jo"])
    await useAppStore.getState().loadPeople()
    expect(useAppStore.getState().people).toEqual([])
  })
})
