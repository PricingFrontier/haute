/**
 * The real FileBrowser and settings store's listing cache, with only the list
 * transport substituted: returning from a directory that failed to a cached one
 * shows the cached files, and they can be picked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { FileBrowser } from "../_shared"
import useSettingsStore from "../../../stores/useSettingsStore"

const listFiles = vi.hoisted(() => vi.fn())
vi.mock("../../../api/client", async () => ({
  ...(await vi.importActual<typeof import("../../../api/client")>("../../../api/client")),
  listFiles,
}))

beforeEach(() => {
  useSettingsStore.getState().clearFileListCache()
  listFiles.mockReset()
})
afterEach(cleanup)

describe("FileBrowser over the listing cache", () => {
  it("shows a cached parent's files, not a child's error, after going back up", async () => {
    const { ApiError } = await import("../../../api/client")
    listFiles.mockImplementation(async (dir: string) => {
      if (dir === "locked") throw new ApiError("HTTP 403", 403, "Permission denied")
      return {
        items: [
          { name: "locked", path: "locked", type: "directory", size: null },
          { name: "quotes.parquet", path: "quotes.parquet", type: "file", size: 10 },
        ],
      }
    })
    const onSelect = vi.fn()
    render(<FileBrowser onSelect={onSelect} />)

    fireEvent.click(await screen.findByText("locked"))
    expect(await screen.findByText("Permission denied")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Up one folder" }))
    fireEvent.click(await screen.findByText("quotes.parquet"))

    expect(screen.queryByText("Permission denied")).toBeNull()
    expect(onSelect).toHaveBeenCalledWith("quotes.parquet")
    // The parent came from the cache: listed once, on the way in.
    expect(listFiles.mock.calls.map(([dir]) => dir)).toEqual([".", "locked"])
  })
})
