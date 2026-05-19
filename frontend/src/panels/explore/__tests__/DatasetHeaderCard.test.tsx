import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import type { ExploreCacheReport } from "../../../api/types"
import DatasetHeaderCard from "../DatasetHeaderCard"

function makeReport(overrides: Partial<ExploreCacheReport> = {}): ExploreCacheReport {
  return {
    status: "ok",
    node_id: "explore_1",
    upstream_node_id: "source_1",
    source: "pricing",
    dataframe_cache_key: "explore_dataset:abc123",
    row_count: 1234,
    column_count: 12,
    generated_at: 1710000000,
    columns: [],
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("DatasetHeaderCard", () => {
  it("renders row count and column count with thousands separators", () => {
    render(<DatasetHeaderCard report={makeReport({ row_count: 1234567, column_count: 4200 })} />)
    expect(screen.getByText("1,234,567")).toBeInTheDocument()
    expect(screen.getByText("4,200")).toBeInTheDocument()
  })

  it("renders source string", () => {
    render(<DatasetHeaderCard report={makeReport({ source: "pricing" })} />)
    expect(screen.getByText("pricing")).toBeInTheDocument()
  })

  it("renders relative time for recently generated reports", () => {
    // Pin "now" to a known instant; report was 5 minutes earlier.
    const now = new Date("2026-05-19T12:00:00Z")
    vi.useFakeTimers().setSystemTime(now)

    const generatedAt = Math.floor(now.getTime() / 1000) - 5 * 60
    render(<DatasetHeaderCard report={makeReport({ generated_at: generatedAt })} />)

    const card = screen.getByTestId("explore-dataset-header-card")
    expect(card.textContent).toMatch(/5/)
    expect(card.textContent).toMatch(/min/i)
  })

  it("renders absolute timestamp in the title attribute", () => {
    const generatedAt = 1710000000
    render(<DatasetHeaderCard report={makeReport({ generated_at: generatedAt })} />)

    const expectedIso = new Date(generatedAt * 1000).toISOString()
    const cell = screen.getByTestId("explore-dataset-header-cached")
    expect(cell.getAttribute("title")).toBe(expectedIso)
  })

  it("renders the testid on the outer container", () => {
    render(<DatasetHeaderCard report={makeReport()} />)
    expect(screen.getByTestId("explore-dataset-header-card")).toBeInTheDocument()
  })

  describe("relative-time boundaries", () => {
    const now = new Date("2026-05-19T12:00:00Z")
    const nowSec = Math.floor(now.getTime() / 1000)

    const cases: { name: string; offsetSec: number; expected: string | RegExp }[] = [
      { name: "59s ago → just now", offsetSec: 59, expected: "just now" },
      { name: "60s ago → 1 min ago", offsetSec: 60, expected: "1 min ago" },
      { name: "59 min ago → 59 min ago", offsetSec: 59 * 60, expected: "59 min ago" },
      { name: "60 min ago → 1 h ago", offsetSec: 60 * 60, expected: "1 h ago" },
      { name: "23h ago → 23 h ago", offsetSec: 23 * 60 * 60, expected: "23 h ago" },
    ]

    for (const { name, offsetSec, expected } of cases) {
      it(name, () => {
        vi.useFakeTimers().setSystemTime(now)
        const generatedAt = nowSec - offsetSec
        render(<DatasetHeaderCard report={makeReport({ generated_at: generatedAt })} />)
        expect(screen.getByTestId("explore-dataset-header-cached").textContent).toContain(expected)
      })
    }

    it("24h ago → absolute locale timestamp", () => {
      vi.useFakeTimers().setSystemTime(now)
      const generatedAt = nowSec - 24 * 60 * 60
      const expected = new Date(generatedAt * 1000).toLocaleString()
      render(<DatasetHeaderCard report={makeReport({ generated_at: generatedAt })} />)
      expect(screen.getByTestId("explore-dataset-header-cached").textContent).toContain(expected)
    })
  })
})
