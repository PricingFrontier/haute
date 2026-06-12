import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
  resourceKey: string
  getStatus: (key: string) => Promise<TestStatus>
  startFetch: (key: string) => Promise<TestStatus>
  getProgress: (key: string) => Promise<{ active: boolean; rows?: number; elapsed?: number; phase?: string }>
  deleteCache: (key: string) => Promise<TestStatus>
  onCacheReady: (status: TestStatus) => void
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
      resourceKey={overrides.resourceKey ?? "rating/data/input.json"}
      getStatus={overrides.getStatus ?? vi.fn().mockResolvedValue(uncached)}
      startFetch={overrides.startFetch ?? vi.fn().mockResolvedValue(uncached)}
      getProgress={overrides.getProgress ?? vi.fn().mockResolvedValue({ active: false })}
      deleteCache={overrides.deleteCache ?? vi.fn().mockResolvedValue(uncached)}
      timestampField="cached_at"
      labels={labels}
      onCacheReady={overrides.onCacheReady}
    />,
  )
}

function status(overrides: Partial<TestStatus>): TestStatus {
  return {
    cached: false,
    row_count: 0,
    column_count: 0,
    size_bytes: 0,
    cached_at: 0,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
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

  it("ignores stale status success from a previous resource key", async () => {
    const oldStatus = deferred<TestStatus>()
    const getStatus = vi.fn((key: string) =>
      key === "old.json"
        ? oldStatus.promise
        : Promise.resolve(status({ cached: true, row_count: 2, column_count: 1, size_bytes: 10, cached_at: 1 })),
    )
    const onCacheReady = vi.fn()

    const view = renderCacheButton({ resourceKey: "old.json", getStatus, onCacheReady })
    view.rerender(
      <CacheFetchButton<TestStatus>
        resourceKey="new.json"
        getStatus={getStatus}
        startFetch={vi.fn().mockResolvedValue(status({}))}
        getProgress={vi.fn().mockResolvedValue({ active: false })}
        deleteCache={vi.fn().mockResolvedValue(status({}))}
        timestampField="cached_at"
        labels={labels}
        onCacheReady={onCacheReady}
      />,
    )

    await screen.findByText("2 rows")
    await act(async () => {
      oldStatus.resolve(status({ cached: true, row_count: 999, column_count: 1, size_bytes: 10, cached_at: 1 }))
    })

    expect(screen.getByText("2 rows")).toBeInTheDocument()
    expect(screen.queryByText("999 rows")).not.toBeInTheDocument()
    expect(onCacheReady).toHaveBeenCalledTimes(1)
    expect(onCacheReady).toHaveBeenLastCalledWith(expect.objectContaining({ row_count: 2 }))
  })

  it("ignores stale status rejection from a previous resource key", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {})
    const oldStatus = deferred<TestStatus>()
    const getStatus = vi.fn((key: string) =>
      key === "old.json"
        ? oldStatus.promise
        : Promise.resolve(status({ cached: true, row_count: 3, column_count: 1, size_bytes: 10, cached_at: 1 })),
    )

    const view = renderCacheButton({ resourceKey: "old.json", getStatus })
    view.rerender(
      <CacheFetchButton<TestStatus>
        resourceKey="new.json"
        getStatus={getStatus}
        startFetch={vi.fn().mockResolvedValue(status({}))}
        getProgress={vi.fn().mockResolvedValue({ active: false })}
        deleteCache={vi.fn().mockResolvedValue(status({}))}
        timestampField="cached_at"
        labels={labels}
      />,
    )

    await screen.findByText("3 rows")
    await act(async () => {
      oldStatus.reject(new Error("old status failed"))
    })

    expect(screen.getByText("3 rows")).toBeInTheDocument()
    expect(screen.queryByText(/Unable to check cache status/)).not.toBeInTheDocument()
  })

  it("ignores stale same-resource status success after fetch completes", async () => {
    const initialStatus = deferred<TestStatus>()
    const getStatus = vi.fn().mockReturnValue(initialStatus.promise)
    const startFetch = vi.fn().mockResolvedValue(
      status({ cached: true, row_count: 5, column_count: 2, size_bytes: 20, cached_at: 1 }),
    )

    renderCacheButton({ getStatus, startFetch })
    fireEvent.click(screen.getByRole("button", { name: /Fetch Cache/i }))

    await screen.findByText("5 rows")
    await act(async () => {
      initialStatus.resolve(status({ cached: false }))
    })

    expect(screen.getByText("5 rows")).toBeInTheDocument()
    expect(screen.queryByText(labels.notCachedHint)).not.toBeInTheDocument()
  })

  it("ignores stale same-resource status success after delete completes", async () => {
    const initialStatus = deferred<TestStatus>()
    const getStatus = vi.fn().mockReturnValue(initialStatus.promise)
    const startFetch = vi.fn().mockResolvedValue(
      status({ cached: true, row_count: 5, column_count: 2, size_bytes: 20, cached_at: 1 }),
    )
    const deleteCache = vi.fn().mockResolvedValue(status({ cached: false }))

    renderCacheButton({ getStatus, startFetch, deleteCache })
    fireEvent.click(screen.getByRole("button", { name: /Fetch Cache/i }))
    await screen.findByText("5 rows")

    fireEvent.click(screen.getByRole("button", { name: /clear/i }))
    await waitFor(() => {
      expect(screen.queryByText("5 rows")).not.toBeInTheDocument()
    })

    await act(async () => {
      initialStatus.resolve(status({ cached: true, row_count: 999, column_count: 2, size_bytes: 20, cached_at: 1 }))
    })

    expect(screen.queryByText("999 rows")).not.toBeInTheDocument()
    expect(screen.getByText(labels.notCachedHint)).toBeInTheDocument()
  })

  it("clears previous cache details while a new resource status is pending", async () => {
    const newStatus = deferred<TestStatus>()
    const getStatus = vi.fn((key: string) =>
      key === "old.json"
        ? Promise.resolve(status({ cached: true, row_count: 12, column_count: 3, size_bytes: 30, cached_at: 1 }))
        : newStatus.promise,
    )

    const view = renderCacheButton({ resourceKey: "old.json", getStatus })
    await screen.findByText("12 rows")

    view.rerender(
      <CacheFetchButton<TestStatus>
        resourceKey="new.json"
        getStatus={getStatus}
        startFetch={vi.fn().mockResolvedValue(status({}))}
        getProgress={vi.fn().mockResolvedValue({ active: false })}
        deleteCache={vi.fn().mockResolvedValue(status({}))}
        timestampField="cached_at"
        labels={labels}
      />,
    )

    expect(screen.queryByText("12 rows")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /clear/i })).not.toBeInTheDocument()

    await act(async () => {
      newStatus.resolve(status({ cached: true, row_count: 3, column_count: 1, size_bytes: 10, cached_at: 1 }))
    })
    expect(screen.getByText("3 rows")).toBeInTheDocument()
  })

  it("clears previous fetch errors while a new resource status is pending", async () => {
    const newStatus = deferred<TestStatus>()
    const getStatus = vi.fn((key: string) =>
      key === "old.json"
        ? Promise.resolve(status({ cached: false }))
        : newStatus.promise,
    )
    const view = renderCacheButton({
      resourceKey: "old.json",
      getStatus,
      startFetch: vi.fn().mockRejectedValue(new Error("Fetch failed")),
    })

    fireEvent.click(await screen.findByRole("button", { name: /Fetch Cache/i }))
    await screen.findByRole("alert")
    expect(screen.getByText("Fetch failed")).toBeInTheDocument()

    view.rerender(
      <CacheFetchButton<TestStatus>
        resourceKey="new.json"
        getStatus={getStatus}
        startFetch={vi.fn().mockResolvedValue(status({}))}
        getProgress={vi.fn().mockResolvedValue({ active: false })}
        deleteCache={vi.fn().mockResolvedValue(status({}))}
        timestampField="cached_at"
        labels={labels}
      />,
    )

    expect(screen.queryByText("Fetch failed")).not.toBeInTheDocument()

    await act(async () => {
      newStatus.resolve(status({ cached: false }))
    })
  })

  it("keeps building while start fetch is pending even if progress is inactive", async () => {
    vi.useFakeTimers()
    const start = deferred<TestStatus>()
    const startFetch = vi.fn().mockReturnValue(start.promise)
    const getProgress = vi.fn().mockResolvedValue({ active: false })
    renderCacheButton({ startFetch, getProgress })

    await act(async () => {
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole("button", { name: /Fetch Cache/i }))
    expect(screen.getByText("Fetching...")).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(1000)
      await Promise.resolve()
    })

    expect(getProgress).toHaveBeenCalledTimes(1)
    expect(screen.getByText("Fetching...")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Fetching/i }))
    expect(startFetch).toHaveBeenCalledTimes(1)

    await act(async () => {
      start.resolve(status({ cached: true, row_count: 5, column_count: 2, size_bytes: 20, cached_at: 1 }))
    })

    vi.useRealTimers()
    await screen.findByText("5 rows")
  })
})
