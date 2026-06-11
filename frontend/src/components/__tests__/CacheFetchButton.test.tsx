import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { CacheFetchButton, type BaseCacheStatus } from "../CacheFetchButton"

type TestStatus = BaseCacheStatus & {
  cached_at: number
}

const labels = {
  fetchLabel: "Fetch Cache",
  refreshLabel: "Refresh Cache",
  notCachedHint: "Not cached yet",
  pendingLabel: "Fetching...",
}

function renderCacheButton(overrides: Partial<{
  getStatus: (key: string) => Promise<TestStatus>
  startFetch: (key: string) => Promise<TestStatus>
}> = {}) {
  const uncached: TestStatus = {
    cached: false,
    row_count: 0,
    column_count: 0,
    size_bytes: 0,
    cached_at: 0,
  }

  return render(
    <CacheFetchButton<TestStatus>
      resourceKey="rating/data/input.json"
      getStatus={overrides.getStatus ?? vi.fn().mockResolvedValue(uncached)}
      startFetch={overrides.startFetch ?? vi.fn().mockResolvedValue(uncached)}
      getProgress={vi.fn().mockResolvedValue({ active: false })}
      deleteCache={vi.fn().mockResolvedValue(uncached)}
      timestampField="cached_at"
      labels={labels}
    />,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("CacheFetchButton", () => {
  it("surfaces cache status failures without rendering the normal not-cached hint", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {})
    renderCacheButton({
      getStatus: vi.fn().mockRejectedValue(new Error("Status endpoint unavailable")),
    })

    await waitFor(() => {
      expect(screen.getByText("Unable to check cache status: Status endpoint unavailable")).toBeInTheDocument()
    })
    expect(screen.queryByText(labels.notCachedHint)).not.toBeInTheDocument()
  })
})
