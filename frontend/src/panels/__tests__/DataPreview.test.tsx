import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import DataPreview from "../DataPreview"
import type { PreviewData } from "../DataPreview"

// jsdom does not provide ResizeObserver
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 256,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))

function makePreview(overrides: Partial<PreviewData> = {}): PreviewData {
  return {
    nodeId: "n1",
    nodeLabel: "Test Node",
    status: "ok",
    row_count: 3,
    column_count: 2,
    columns: [
      { name: "age", dtype: "i64" },
      { name: "premium", dtype: "f64" },
    ],
    preview: [
      { age: 25, premium: 100.5 },
      { age: 30, premium: 200.0 },
      { age: 35, premium: 150.75 },
    ],
    error: null,
    ...overrides,
  }
}

describe("DataPreview", () => {
  beforeEach(() => {
    // Provide ResizeObserver for jsdom
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
  })

  afterEach(cleanup)

  it("returns null when data is null", () => {
    const { container } = render(<DataPreview data={null} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders node label in header", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.getByText("Test Node")).toBeInTheDocument()
  })

  it("renders column headers", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("renders row count and column count for ok status", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.getByText(/3 rows/)).toBeInTheDocument()
    expect(screen.getByText(/2 cols/)).toBeInTheDocument()
  })

  it("renders error message for error status", () => {
    render(<DataPreview data={makePreview({ status: "error", error: "Division by zero" })} />)
    expect(screen.getAllByText("Division by zero").length).toBeGreaterThanOrEqual(1)
  })

  it("renders loading state", () => {
    render(<DataPreview data={makePreview({ status: "loading" })} />)
    expect(screen.getByText("Running...")).toBeInTheDocument()
    expect(screen.getByText("Executing pipeline...")).toBeInTheDocument()
  })

  it("cell click calls onCellClick with row index and column", () => {
    const onCellClick = vi.fn()
    render(<DataPreview data={makePreview()} onCellClick={onCellClick} />)
    // Click on the first data cell (age = 25)
    fireEvent.click(screen.getByText("25"))
    expect(onCellClick).toHaveBeenCalledWith(0, "age", { age: 25, premium: 100.5 })
  })

  it("renders null values with italic styling", () => {
    render(
      <DataPreview
        data={makePreview({
          preview: [
            { age: null, premium: 100 },
          ],
          row_count: 1,
        })}
              />,
    )
    // null is rendered as the string "null" via formatValue
    const nullCell = screen.getByText("null")
    expect(nullCell).toBeInTheDocument()
    expect(nullCell.style.fontStyle).toBe("italic")
  })

  it("shows 'Showing X of Y rows' when preview has fewer rows than total", () => {
    render(
      <DataPreview
        data={makePreview({
          row_count: 10000,
          preview: [
            { age: 25, premium: 100 },
            { age: 30, premium: 200 },
          ],
        })}
              />,
    )
    expect(screen.getByText(/Showing 2 of 10,000 rows/)).toBeInTheDocument()
  })

  it("virtualizes large previews on first render before ResizeObserver reports height", () => {
    const rows = Array.from({ length: 500 }, (_, i) => ({
      age: i,
      premium: `row-${i}`,
    }))

    render(
      <DataPreview
        data={makePreview({
          row_count: 500,
          preview: rows,
        })}
      />,
    )

    expect(screen.getByText("row-0")).toBeInTheDocument()
    expect(screen.queryByText("row-499")).not.toBeInTheDocument()
  })

  it("does not show 'Showing X of Y' when preview has all rows", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument()
  })

  it("renders dtype info for columns", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.getByText("i64")).toBeInTheDocument()
    expect(screen.getByText("f64")).toBeInTheDocument()
  })

  it("renders row numbers starting from 1", () => {
    render(<DataPreview data={makePreview()} />)
    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
  })

  it("collapse button hides table and shows collapsed bar", () => {
    render(<DataPreview data={makePreview()} />)
    // Find the collapse button (ChevronDown)
    const buttons = screen.getAllByRole("button")
    // Collapse is the first button in the header bar
    const collapseBtn = buttons[0]
    fireEvent.click(collapseBtn)
    // In collapsed state, we should still see the node label and row count
    expect(screen.getByText("Test Node")).toBeInTheDocument()
    expect(screen.getByText(/3 rows/)).toBeInTheDocument()
    // But table content should be gone
    expect(screen.queryByText("25")).not.toBeInTheDocument()
  })

  it("expanding from collapsed state shows table again", () => {
    render(<DataPreview data={makePreview()} />)
    // Collapse first
    const buttons = screen.getAllByRole("button")
    fireEvent.click(buttons[0]) // collapse
    // Now expand
    const expandBtn = screen.getByRole("button")
    fireEvent.click(expandBtn)
    // Table data should be visible again
    expect(screen.getByText("25")).toBeInTheDocument()
  })

  it("highlights traced cell with accent styling", () => {
    render(
      <DataPreview
        data={makePreview()}
                tracedCell={{ rowIndex: 0, column: "age" }}
      />,
    )
    const cell = screen.getByText("25").closest("td") as HTMLElement
    expect(cell.style.background).toBe("var(--accent-soft)")
  })

  it("column search filters displayed columns", () => {
    render(<DataPreview data={makePreview()} />)
    const searchInput = screen.getByPlaceholderText("Search columns...")
    fireEvent.change(searchInput, { target: { value: "prem" } })
    // "premium" column should still be visible
    expect(screen.getByText("premium")).toBeInTheDocument()
    // "age" column header should be filtered out
    const headers = screen.getAllByRole("columnheader")
    const headerTexts = headers.map((h) => h.textContent)
    expect(headerTexts.some((t) => t?.includes("age"))).toBe(false)
  })

  it("clearing column search shows all columns again", () => {
    render(<DataPreview data={makePreview()} />)
    const searchInput = screen.getByPlaceholderText("Search columns...")
    fireEvent.change(searchInput, { target: { value: "prem" } })
    // Clear via the X button
    const clearBtn = screen.getAllByRole("button").find((b) => b.closest(".flex")?.querySelector("input"))
    if (clearBtn) fireEvent.click(clearBtn)
    else fireEvent.change(searchInput, { target: { value: "" } })
    // Both columns should be back
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("error status shows error icon and message in body", () => {
    render(
      <DataPreview
        data={makePreview({ status: "error", error: "Column not found: xyz" })}
              />,
    )
    // Error message appears in both header and body
    const errors = screen.getAllByText("Column not found: xyz")
    expect(errors.length).toBeGreaterThanOrEqual(1)
  })
})
