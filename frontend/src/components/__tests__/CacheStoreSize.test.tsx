import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { fetchCacheUsage } from "../../api/client"
import CacheStoreSize from "../CacheStoreSize"

vi.mock("../../api/client", () => ({
  fetchCacheUsage: vi.fn(),
}))

const GIB = 1024 ** 3

function usage(totalBytes: number) {
  return {
    schema_version: 1 as const,
    total_bytes: totalBytes,
    automatic_bytes: 2 * GIB,
    automatic_budget_bytes: 20 * GIB,
  }
}

describe("CacheStoreSize", () => {
  beforeEach(() => {
    vi.mocked(fetchCacheUsage).mockReset()
  })
  afterEach(() => {
    cleanup()
  })

  it("shows the store's size and explains the budget on hover", async () => {
    vi.mocked(fetchCacheUsage).mockResolvedValue(usage(3 * GIB))

    render(<CacheStoreSize refreshKey="first" />)

    const size = await screen.findByTestId("cache-store-size")
    expect(size).toHaveTextContent("3.0 GB cached")
    expect(size).toHaveAttribute("title", expect.stringContaining("Automatic captures hold 2.0 GB of their 20 GB budget"))
    expect(size).toHaveAttribute("title", expect.stringContaining("Input snapshots and explicit builds stay until you clear them"))
  })

  it("reads again when a preview settles and keeps the last reading while one runs", async () => {
    vi.mocked(fetchCacheUsage).mockResolvedValueOnce(usage(GIB)).mockResolvedValueOnce(usage(5 * GIB))
    const { rerender } = render(<CacheStoreSize refreshKey="first" />)
    expect(await screen.findByText("1.0 GB cached")).toBeInTheDocument()

    rerender(<CacheStoreSize refreshKey={null} />)
    expect(screen.getByText("1.0 GB cached")).toBeInTheDocument()
    expect(fetchCacheUsage).toHaveBeenCalledOnce()

    rerender(<CacheStoreSize refreshKey="second" />)
    expect(await screen.findByText("5.0 GB cached")).toBeInTheDocument()
    expect(fetchCacheUsage).toHaveBeenCalledTimes(2)
  })

  it("shows nothing when the size cannot be read", async () => {
    vi.mocked(fetchCacheUsage).mockRejectedValue(new Error("offline"))

    render(<CacheStoreSize refreshKey="first" />)

    await waitFor(() => expect(fetchCacheUsage).toHaveBeenCalledOnce())
    expect(screen.queryByTestId("cache-store-size")).not.toBeInTheDocument()
  })

  it("abandons a reading the next one replaces", async () => {
    let first: AbortSignal | undefined
    vi.mocked(fetchCacheUsage)
      .mockImplementationOnce((options) => {
        first = options?.signal
        return new Promise(() => {})
      })
      .mockResolvedValueOnce(usage(GIB))
    const { rerender } = render(<CacheStoreSize refreshKey="first" />)

    rerender(<CacheStoreSize refreshKey="second" />)

    expect(first?.aborted).toBe(true)
    expect(await screen.findByText("1.0 GB cached")).toBeInTheDocument()
  })
})
