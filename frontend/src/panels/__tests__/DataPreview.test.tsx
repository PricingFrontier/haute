import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import DataPreview from "../DataPreview"
import type { PreviewData } from "../DataPreview"
import useUIStore from "../../stores/useUIStore"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

const resizeObserverStats = {
  constructed: 0,
  observed: 0,
  disconnected: 0,
}

// jsdom does not provide ResizeObserver
class MockResizeObserver {
  constructor() {
    resizeObserverStats.constructed += 1
  }

  observe() {
    resizeObserverStats.observed += 1
  }

  unobserve() {}

  disconnect() {
    resizeObserverStats.disconnected += 1
  }
}

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 256,
    containerRef: { current: null },
    onDragStart: vi.fn(),
    resizeToHeight: vi.fn(),
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
    resizeObserverStats.constructed = 0
    resizeObserverStats.observed = 0
    resizeObserverStats.disconnected = 0
    // Provide ResizeObserver for jsdom
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver
    // Real-singleton store reset — column width overrides are view state kept
    // in useUIStore and must not leak between tests.
    useUIStore.setState({ previewColumnWidths: {} })
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

  it("uses a node-level frame header above the preview section", () => {
    render(<DataPreview data={makePreview()} />)

    const nodeTitle = screen.getByText("Test Node")
    const previewTitle = screen.getByText("Preview")

    expect(screen.getByLabelText("Collapse preview panel")).toBeInTheDocument()
    expect(nodeTitle.compareDocumentPosition(previewTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("uses the selected node type icon in the frame header", () => {
    render(<DataPreview data={makePreview()} nodeType="dataSource" />)

    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-database")).toBeTruthy()
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

  it("renders as an embedded table body without duplicating the outer frame's title", () => {
    render(<DataPreview data={makePreview()} embedded />)

    expect(screen.getByTestId("data-preview-embedded")).toBeInTheDocument()
    // "Preview" label and Table2 icon are suppressed in embedded mode — the
    // composite parent (e.g. ExplorePreview's PreviewPanelFrame) already shows
    // the node label and status, so duplicating chrome here is redundant.
    expect(screen.queryByText("Preview")).not.toBeInTheDocument()
    expect(screen.getByText(/3 rows/)).toBeInTheDocument()
    expect(screen.getByText("25")).toBeInTheDocument()
    expect(screen.getByText("premium")).toBeInTheDocument()
    expect(screen.queryByText("Test Node")).not.toBeInTheDocument()
  })

  it("surfaces preview memory-pressure diagnostics with technical details", () => {
    render(<DataPreview data={makePreview({ execution_metrics: makeExecutionMetricsFixture() })} />)

    expect(screen.getByText("Memory pressure reached 75% of the preview budget.")).toBeInTheDocument()
    expect(screen.getByText("RSS 1.7 KB of 2.9 KB limit")).toBeInTheDocument()
  })

  it("cell click calls onCellClick with row index and column", () => {
    const onCellClick = vi.fn()
    render(<DataPreview data={makePreview()} onCellClick={onCellClick} />)
    // Click on the first data cell (age = 25)
    fireEvent.click(screen.getByText("25"))
    expect(onCellClick).toHaveBeenCalledWith(0, "age", { age: 25, premium: 100.5 })
  })

  it("marks data cells with stable row and column attributes for delegated clicks", () => {
    render(<DataPreview data={makePreview()} onCellClick={vi.fn()} />)

    const cell = screen.getByText("25").closest("td") as HTMLElement
    expect(cell.dataset.rowIndex).toBe("0")
    expect(cell.dataset.column).toBe("age")
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

  it("shows the API preview cap when truncated even if row counts match", () => {
    render(
      <DataPreview
        data={makePreview({
          row_count: 3,
          preview_row_count: 3,
          preview_row_limit: 3,
          preview_truncated: true,
          preview: [
            { age: 25, premium: 100 },
            { age: 30, premium: 200 },
            { age: 35, premium: 150 },
          ],
        })}
      />,
    )

    expect(screen.getByText(/Showing 3 of 3 rows/)).toBeInTheDocument()
    expect(screen.getByText(/capped at 3/)).toBeInTheDocument()
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

  it("keeps one ResizeObserver when preview data refreshes without remounting the scroll container", async () => {
    const firstData = makePreview({
      preview: [
        { age: 25, premium: 100.5 },
        { age: 30, premium: 200.0 },
      ],
      row_count: 2,
    })
    const nextData = makePreview({
      preview: [
        { age: 41, premium: 301.25 },
        { age: 42, premium: 302.75 },
      ],
      row_count: 2,
    })

    const { rerender, unmount } = render(<DataPreview data={firstData} />)

    await waitFor(() => {
      expect(resizeObserverStats.constructed).toBe(1)
    })
    expect(resizeObserverStats.observed).toBe(1)
    expect(resizeObserverStats.disconnected).toBe(0)

    rerender(<DataPreview data={nextData} />)

    expect(screen.getByText("41")).toBeInTheDocument()
    expect(screen.getByText("302.75")).toBeInTheDocument()
    expect(resizeObserverStats.constructed).toBe(1)
    expect(resizeObserverStats.observed).toBe(1)
    expect(resizeObserverStats.disconnected).toBe(0)

    unmount()

    expect(resizeObserverStats.constructed).toBe(1)
    expect(resizeObserverStats.observed).toBe(1)
    expect(resizeObserverStats.disconnected).toBe(1)
  })

  it("virtualizes wide previews to avoid rendering every column", () => {
    const columns = Array.from({ length: 1000 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
    const preview = [
      Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`])),
    ]

    render(
      <DataPreview
        data={makePreview({
          column_count: columns.length,
          columns,
          preview,
          row_count: 1,
        })}
      />,
    )

    expect(screen.getByText("col_0")).toBeInTheDocument()
    expect(screen.getByText("value-0")).toBeInTheDocument()
    expect(screen.queryByText("col_999")).not.toBeInTheDocument()
    expect(screen.queryByText("value-999")).not.toBeInTheDocument()
    expect(screen.getAllByRole("columnheader").length).toBeLessThan(40)
  })

  it("bounds rendered rows and columns for a 10k by 1000 preview", () => {
    const columns = Array.from({ length: 1000 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
    const preview = Array.from({ length: 10_000 }, (_, i) => ({
      col_0: `row-${i}`,
    }))

    render(
      <DataPreview
        data={makePreview({
          column_count: columns.length,
          columns,
          preview,
          row_count: preview.length,
        })}
      />,
    )

    expect(screen.getByText("row-0")).toBeInTheDocument()
    expect(screen.queryByText("row-9999")).not.toBeInTheDocument()
    expect(screen.getByText("col_0")).toBeInTheDocument()
    expect(screen.queryByText("col_999")).not.toBeInTheDocument()
    expect(screen.getAllByRole("columnheader").length).toBeLessThan(40)
  })

  // NOTE: the `scrollLeft: N * 160` math in the two tests below assumes the
  // uniform responsive default width (160px at the 960px fallback viewport).
  // That assumption is valid here ONLY because no column-width overrides are
  // applied — with overrides, scroll targets must be derived from the
  // cumulative offsets (see the "column resize" suite below).
  it("reveals later columns when horizontally scrolled", async () => {
    const columns = Array.from({ length: 120 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
    const preview = [
      Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`])),
    ]

    render(
      <DataPreview
        data={makePreview({
          column_count: columns.length,
          columns,
          preview,
          row_count: 1,
        })}
      />,
    )

    const scrollRegion = screen.getByTestId("data-preview-scroll")
    fireEvent.scroll(scrollRegion, { target: { scrollLeft: 80 * 160 } })

    await waitFor(() => {
      expect(screen.queryByText("col_0")).not.toBeInTheDocument()
      expect(screen.getByText("col_80")).toBeInTheDocument()
      expect(screen.getByText("value-80")).toBeInTheDocument()
    })
  })

  it("clicks a horizontally virtualized cell with delegated row and column metadata", async () => {
    const columns = Array.from({ length: 120 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
    const row = Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`]))
    const onCellClick = vi.fn()

    render(
      <DataPreview
        data={makePreview({
          column_count: columns.length,
          columns,
          preview: [row],
          row_count: 1,
        })}
        onCellClick={onCellClick}
      />,
    )

    const scrollRegion = screen.getByTestId("data-preview-scroll")
    fireEvent.scroll(scrollRegion, { target: { scrollLeft: 80 * 160 } })

    await waitFor(() => {
      expect(screen.getByText("value-80")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("value-80"))

    expect(onCellClick).toHaveBeenCalledWith(0, "col_80", row)
  })

  it("clamps the visible column window when a scrolled preview changes to fewer columns", async () => {
    const makeWidePreview = (count: number) => {
      const columns = Array.from({ length: count }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
      return makePreview({
        column_count: columns.length,
        columns,
        preview: [
          Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`])),
        ],
        row_count: 1,
      })
    }

    const { rerender } = render(<DataPreview data={makeWidePreview(1000)} />)
    const scrollRegion = screen.getByTestId("data-preview-scroll")
    fireEvent.scroll(scrollRegion, { target: { scrollLeft: 900 * 160 } })

    await waitFor(() => {
      expect(screen.getByText("col_900")).toBeInTheDocument()
    })

    rerender(<DataPreview data={makeWidePreview(80)} />)

    expect(screen.getByText("col_79")).toBeInTheDocument()
    expect(screen.getByText("value-79")).toBeInTheDocument()
  })

  it("column search can show a matching column outside the initial virtual window", () => {
    const columns = Array.from({ length: 1000 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
    const preview = [
      Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`])),
    ]

    render(
      <DataPreview
        data={makePreview({
          column_count: columns.length,
          columns,
          preview,
          row_count: 1,
        })}
      />,
    )

    fireEvent.change(screen.getByPlaceholderText("Search columns..."), { target: { value: "col_999" } })

    expect(screen.getByText("col_999")).toBeInTheDocument()
    expect(screen.getByText("value-999")).toBeInTheDocument()
    expect(screen.queryByText("col_0")).not.toBeInTheDocument()
  })

  it("builds the column search index once across searches, scrolls, and unrelated rerenders", async () => {
    const columns = Array.from({ length: 1000 }, (_, i) => ({ name: `IndexedColumn_${i}`, dtype: "i64" }))
    const preview = [
      Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`])),
    ]
    const data = makePreview({
      column_count: columns.length,
      columns,
      preview,
      row_count: 1,
    })
    const originalToLowerCase = String.prototype.toLowerCase
    let indexedColumnLowerCalls = 0
    const toLowerCaseSpy = vi.spyOn(String.prototype, "toLowerCase").mockImplementation(function toLowerCaseSpy(this: string) {
      const value = String(this)
      if (value.startsWith("IndexedColumn_")) indexedColumnLowerCalls += 1
      return originalToLowerCase.call(value)
    })

    try {
      const { rerender } = render(<DataPreview data={data} />)

      expect(indexedColumnLowerCalls).toBe(columns.length)

      fireEvent.change(screen.getByPlaceholderText("Search columns..."), { target: { value: "INDEXEDCOLUMN_999" } })
      expect(screen.getByText("IndexedColumn_999")).toBeInTheDocument()
      expect(indexedColumnLowerCalls).toBe(columns.length)

      fireEvent.change(screen.getByPlaceholderText("Search columns..."), { target: { value: "" } })
      fireEvent.change(screen.getByPlaceholderText("Search columns..."), { target: { value: "indexedcolumn_998" } })
      expect(screen.getByText("IndexedColumn_998")).toBeInTheDocument()
      expect(indexedColumnLowerCalls).toBe(columns.length)

      fireEvent.scroll(screen.getByTestId("data-preview-scroll"), { target: { scrollLeft: 40 * 160 } })
      await waitFor(() => {
        expect(screen.getByText("IndexedColumn_998")).toBeInTheDocument()
      })
      expect(indexedColumnLowerCalls).toBe(columns.length)

      rerender(<DataPreview data={data} tracedCell={{ rowIndex: 0, column: "IndexedColumn_998" }} />)
      expect(screen.getByText("IndexedColumn_998")).toBeInTheDocument()
      expect(indexedColumnLowerCalls).toBe(columns.length)
    } finally {
      toLowerCaseSpy.mockRestore()
    }
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
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    // In collapsed state, we should still see the node label and row count
    expect(screen.getByText("Test Node")).toBeInTheDocument()
    expect(screen.getByText(/3 rows/)).toBeInTheDocument()
    // But table content should be gone
    expect(screen.queryByText("25")).not.toBeInTheDocument()
  })

  it("expanding from collapsed state shows table again", () => {
    render(<DataPreview data={makePreview()} />)
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    fireEvent.click(screen.getByLabelText("Expand preview panel"))
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

  it("displays only projected preview columns when preview rows omit full-schema columns", () => {
    render(
      <DataPreview
        data={makePreview({
          column_count: 3,
          columns: [
            { name: "age", dtype: "i64" },
            { name: "premium", dtype: "f64" },
            { name: "segment", dtype: "str" },
          ],
          preview_columns: ["premium"],
          preview: [
            { premium: 100.5 },
          ],
          row_count: 1,
        })}
      />,
    )

    expect(screen.getByText("premium")).toBeInTheDocument()
    expect(screen.getByText("100.5")).toBeInTheDocument()
    expect(screen.queryByText("age")).not.toBeInTheDocument()
    expect(screen.queryByText("segment")).not.toBeInTheDocument()
    expect(screen.queryByText("null")).not.toBeInTheDocument()
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

  // ───────────────────────────────────────────────────────────────────────
  // Draggable column resize (design `datapreview-column-resize` §5.3).
  // Default column width in these tests is 160px: jsdom reports zero
  // clientWidth, so the component falls back to FALLBACK_VIEW_WIDTH (960px)
  // whose responsive width is 160.
  // ───────────────────────────────────────────────────────────────────────
  describe("column resize", () => {
    function dragHandle(handle: HTMLElement, fromX: number, toX: number): void {
      fireEvent.mouseDown(handle, { clientX: fromX })
      fireEvent.mouseMove(document, { clientX: toX })
      fireEvent.mouseUp(document, { clientX: toX })
    }

    function getHeaderCell(name: string): HTMLTableCellElement {
      return screen.getByText(name).closest("th") as HTMLTableCellElement
    }

    it("dragging the handle widens the column and commits the width to the store", () => {
      render(<DataPreview data={makePreview()} />)

      dragHandle(screen.getByTestId("data-preview-col-resize-premium"), 300, 460)

      // Store boundary — exact shape, full object (the persistent home of
      // this view state is useUIStore, never the graph or save payload).
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })
      expect(getHeaderCell("premium").style.width).toBe("320px")
      const cell = screen.getByText("100.5").closest("td") as HTMLTableCellElement
      expect(cell.style.width).toBe("320px")
      // The unresized sibling keeps the responsive default.
      expect(getHeaderCell("age").style.width).toBe("160px")
    })

    it("clamps the committed width to [60, 640]", () => {
      render(<DataPreview data={makePreview()} />)
      const handle = screen.getByTestId("data-preview-col-resize-premium")

      dragHandle(handle, 300, 100) // 160 - 200 -> clamp at 60
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 60 } })

      dragHandle(handle, 300, 5_300) // 60 + 5000 -> clamp at 640
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 640 } })
    })

    it("a sub-3px drag commits nothing (accidental click)", () => {
      render(<DataPreview data={makePreview()} />)

      dragHandle(screen.getByTestId("data-preview-col-resize-premium"), 300, 302)

      expect(useUIStore.getState().previewColumnWidths).toEqual({})
      expect(getHeaderCell("premium").style.width).toBe("160px")
    })

    it("double-click resets the column to the responsive default", () => {
      render(<DataPreview data={makePreview()} />)
      const handle = screen.getByTestId("data-preview-col-resize-premium")

      dragHandle(handle, 300, 460)
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })

      fireEvent.doubleClick(handle)

      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: {} })
      expect(getHeaderCell("premium").style.width).toBe("160px")
    })

    it("the virtual window respects overrides: a widened col_0 shifts later columns' offsets", async () => {
      const columns = Array.from({ length: 120 }, (_, i) => ({ name: `col_${i}`, dtype: "i64" }))
      const row = Object.fromEntries(columns.map((col, i) => [col.name, `value-${i}`]))
      const onCellClick = vi.fn()
      useUIStore.getState().setPreviewColumnWidth("n1", "col_0", 640)

      render(
        <DataPreview
          data={makePreview({
            column_count: columns.length,
            columns,
            preview: [row],
            row_count: 1,
          })}
          onCellClick={onCellClick}
        />,
      )

      // Offset-aware scroll target: dataScrollLeft 13392 lands in col_80's
      // span [640 + 79*160, 640 + 80*160) = [13280, 13440).
      const scrollRegion = screen.getByTestId("data-preview-scroll")
      fireEvent.scroll(scrollRegion, { target: { scrollLeft: 13_440 } })

      await waitFor(() => {
        expect(screen.queryByText("col_0")).not.toBeInTheDocument()
        expect(screen.getByText("col_80")).toBeInTheDocument()
        expect(screen.getByText("value-80")).toBeInTheDocument()
      })

      // Cell click delegation still resolves the right column under overrides.
      fireEvent.click(screen.getByText("value-80"))
      expect(onCellClick).toHaveBeenCalledWith(0, "col_80", row)
    })

    it("widths survive the column search filter and its clearing", () => {
      render(<DataPreview data={makePreview()} />)

      dragHandle(screen.getByTestId("data-preview-col-resize-premium"), 300, 460)

      const searchInput = screen.getByPlaceholderText("Search columns...")
      fireEvent.change(searchInput, { target: { value: "prem" } })
      expect(getHeaderCell("premium").style.width).toBe("320px")

      fireEvent.change(searchInput, { target: { value: "" } })
      expect(getHeaderCell("premium").style.width).toBe("320px")
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })
    })

    it("overrides are per-node: another node with the same column names gets defaults", () => {
      const { rerender } = render(<DataPreview data={makePreview()} />)

      dragHandle(screen.getByTestId("data-preview-col-resize-premium"), 300, 460)
      expect(getHeaderCell("premium").style.width).toBe("320px")

      rerender(<DataPreview data={makePreview({ nodeId: "n2" })} />)

      expect(getHeaderCell("premium").style.width).toBe("160px")
      expect(useUIStore.getState().previewColumnWidths).toEqual({ n1: { premium: 320 } })
    })

    it("a resize drag never leaks into the delegated cell-click path", () => {
      const onCellClick = vi.fn()
      render(<DataPreview data={makePreview()} onCellClick={onCellClick} />)
      const handle = screen.getByTestId("data-preview-col-resize-premium")

      dragHandle(handle, 300, 460)
      fireEvent.click(handle)

      expect(onCellClick).not.toHaveBeenCalled()
    })

    it("renders no resize handles in error or loading states", () => {
      render(<DataPreview data={makePreview({ status: "error", error: "boom" })} />)
      expect(screen.queryAllByTestId(/^data-preview-col-resize-/)).toEqual([])
      cleanup()

      render(<DataPreview data={makePreview({ status: "loading" })} />)
      expect(screen.queryAllByTestId(/^data-preview-col-resize-/)).toEqual([])
    })

    it("unmounting mid-drag removes the document listeners", () => {
      const removeSpy = vi.spyOn(document, "removeEventListener")
      try {
        const { unmount } = render(<DataPreview data={makePreview()} />)
        fireEvent.mouseDown(screen.getByTestId("data-preview-col-resize-premium"), { clientX: 300 })
        fireEvent.mouseMove(document, { clientX: 350 })

        unmount()

        const removed = removeSpy.mock.calls.map(([type]) => type)
        expect(removed).toContain("mousemove")
        expect(removed).toContain("mouseup")
        // Nothing was committed — the drag was abandoned, not completed.
        expect(useUIStore.getState().previewColumnWidths).toEqual({})
      } finally {
        removeSpy.mockRestore()
      }
    })
  })
})
