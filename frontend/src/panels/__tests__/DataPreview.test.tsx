import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import DataPreview from "../DataPreview"
import type { PreviewData } from "../DataPreview"
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
    render(<DataPreview data={makePreview()} nodeType="dataInput" />)

    expect(screen.getByTestId("preview-panel-node-icon").querySelector(".lucide-database")).toBeTruthy()
  })

  it("renders struct and list cell values as JSON, not '[object Object]'", () => {
    render(
      <DataPreview
        data={makePreview({
          column_count: 2,
          columns: [
            { name: "meta", dtype: "struct[2]" },
            { name: "tags", dtype: "list[str]" },
          ],
          row_count: 1,
          preview: [{ meta: { region: "uk", score: 3 }, tags: ["a", "b"] }],
        })}
      />,
    )
    expect(screen.getByText('{"region":"uk","score":3}')).toBeInTheDocument()
    expect(screen.getByText('["a","b"]')).toBeInTheDocument()
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument()
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

  it("places preview memory-pressure details behind the status warning icon", () => {
    render(<DataPreview data={makePreview({ execution_metrics: makeExecutionMetricsFixture() })} />)

    const warning = screen.getByLabelText("Preview execution warning details")
    expect(screen.getByText(/3 rows/).compareDocumentPosition(warning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(warning)
    expect(screen.getByText("Preview memory pressure")).toBeInTheDocument()
    expect(screen.getByText("Memory pressure reached 75% of the preview budget.")).toBeInTheDocument()
  })

  it("explains a projection boundary beside the preview dimensions without raw planner JSON", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: {
        schema_version: 1,
        status: "boundary",
        strategy: "unprojected-streaming-boundary",
        profile: "preview_eager",
        boundedness: "bounded",
        reason_code: "unprojected_streaming_boundary",
        detail_state: "available",
        boundaries: { state: "available", total_count: 1, items: [{ topological_rank: 0, node_id: "competitor_premiums", operator: "dataInput", boundary_kind: "unprojected-streaming-boundary" }] },
        reasons: { state: "available", total_count: 0, items: [] },
        provenance: { state: "available", total_count: 0, items: [] },
        blocking_node_id: "competitor_premiums",
        blocking_operator: "dataInput",
        remediation: "Narrow the requested output or define the node's columns.",
      },
    })
    render(<DataPreview data={makePreview({ execution_metrics: metrics })} />)

    const warning = screen.getByLabelText("Preview execution warning details")
    fireEvent.click(warning)
    expect(screen.getByText("Column projection was limited")).toBeInTheDocument()
    expect(screen.getByText(/preview result is still correct/i)).toBeInTheDocument()
    expect(screen.getByText(/competitor_premiums/)).toBeInTheDocument()
    expect(screen.queryByText(/Boundaries:/)).not.toBeInTheDocument()
  })

  it("keeps an admitted materialisation-only boundary silent unless memory pressure is reported", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: {
        schema_version: 1,
        status: "boundary",
        strategy: "materialisation-boundary",
        profile: "preview_eager",
        boundedness: "unbounded",
        reason_code: "group_by_materialisation_admitted",
        detail_state: "available",
        boundaries: {
          state: "available",
          total_count: 1,
          items: [
            { topological_rank: 1, node_id: "claims_agg", operator: "group_by", boundary_kind: "materialisation-boundary" },
          ],
        },
        reasons: { state: "available", total_count: 0, items: [] },
        provenance: { state: "available", total_count: 0, items: [] },
        blocking_node_id: "claims_agg",
        blocking_operator: "group_by",
        remediation: "Keep the admitted boundary within its reported memory headroom.",
        estimated_peak_bytes: 126_233_883,
        headroom_bytes: 4_294_967_296,
      },
    })
    const { rerender } = render(<DataPreview data={makePreview({ execution_metrics: metrics })} />)

    expect(screen.queryByLabelText("Preview execution warning details")).not.toBeInTheDocument()
    expect(screen.queryByText("Column projection was limited")).not.toBeInTheDocument()

    rerender(
      <DataPreview data={makePreview({
        execution_metrics: makeExecutionMetricsFixture({
          execution_strategy: metrics.execution_strategy,
        }),
      })} />,
    )
    fireEvent.click(screen.getByLabelText("Preview execution warning details"))
    expect(screen.getByText("Preview memory pressure")).toBeInTheDocument()
  })

  it("keeps an unprojected source visible in an admitted materialisation plan", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: {
        schema_version: 1,
        status: "boundary",
        strategy: "materialisation-boundary",
        profile: "preview_eager",
        boundedness: "unbounded",
        reason_code: "group_by_materialisation_admitted",
        detail_state: "available",
        boundaries: {
          state: "available",
          total_count: 2,
          items: [
            { topological_rank: 0, node_id: "Quote_Input_1", operator: "apiInput", boundary_kind: "unprojected-streaming-boundary" },
            { topological_rank: 1, node_id: "claims_agg", operator: "group_by", boundary_kind: "materialisation-boundary" },
          ],
        },
        reasons: { state: "available", total_count: 0, items: [] },
        provenance: { state: "available", total_count: 0, items: [] },
        blocking_node_id: "claims_agg",
        blocking_operator: "group_by",
        remediation: "Keep the admitted boundary within its reported memory headroom.",
        estimated_peak_bytes: 126_233_883,
        headroom_bytes: 4_294_967_296,
      },
    })
    render(<DataPreview data={makePreview({ execution_metrics: metrics })} />)

    fireEvent.click(screen.getByLabelText("Preview execution warning details"))
    expect(screen.getByText("Column projection was limited")).toBeInTheDocument()
    expect(screen.getByText(/Quote_Input_1/)).toBeInTheDocument()
    expect(screen.queryByText(/Keep the admitted boundary/)).not.toBeInTheDocument()
  })

  it("surfaces a rejected execution strategy as an actionable preview error", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: {
        schema_version: 1,
        status: "rejected",
        strategy: "unsupported",
        profile: "preview_eager",
        boundedness: "unbounded",
        reason_code: "unsafe_materialisation",
        detail_state: "available",
        boundaries: { state: "available", total_count: 0, items: [] },
        reasons: {
          state: "available",
          total_count: 1,
          items: [{
            reason_code: "unsafe_materialisation",
            topological_rank: 0,
            node_id: "competitor_premiums",
            operator: "dataInput",
          }],
        },
        provenance: { state: "available", total_count: 0, items: [] },
        blocking_node_id: "competitor_premiums",
        blocking_operator: "dataInput",
        remediation: "Define the node columns before previewing.",
      },
    })
    render(<DataPreview data={makePreview({ execution_metrics: metrics })} />)

    const error = screen.getByLabelText("Preview execution error details")
    fireEvent.click(error)
    expect(screen.getByText("Execution could not use a safe strategy")).toBeInTheDocument()
    expect(screen.getByText(/competitor_premiums/)).toBeInTheDocument()
    expect(screen.getByText(/Define the node columns before previewing/)).toBeInTheDocument()
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

  // ─── Frame-select dropdown (multi-frame producers) ──────────────
  describe("frame-select dropdown", () => {
    const multiFrame = (overrides: Partial<PreviewData> = {}) =>
      makePreview({
        frame_columns: {
          policies: [{ name: "policy_id", dtype: "i64" }],
          drivers: [
            { name: "driver_id", dtype: "i64" },
            { name: "age_band", dtype: "str" },
          ],
        },
        ...overrides,
      })

    it("renders the dropdown with one option per frame when 2+ frames AND a handler", () => {
      render(<DataPreview data={multiFrame()} onSelectFrame={vi.fn()} />)
      const select = screen.getByTestId("data-preview-frame-select").querySelector("select")!
      const options = Array.from(select.options).map((o) => o.value)
      expect(options).toEqual(["policies", "drivers"])
      // Default selection is the first frame.
      expect(select.value).toBe("policies")
    })

    it("does NOT render the dropdown for a single-frame node (one frame)", () => {
      render(
        <DataPreview
          data={multiFrame({ frame_columns: { policies: [{ name: "policy_id", dtype: "i64" }] } })}
          onSelectFrame={vi.fn()}
        />,
      )
      expect(screen.queryByTestId("data-preview-frame-select")).toBeNull()
    })

    it("does NOT render the dropdown for an ordinary node (no frame_columns)", () => {
      render(<DataPreview data={makePreview()} onSelectFrame={vi.fn()} />)
      expect(screen.queryByTestId("data-preview-frame-select")).toBeNull()
    })

    it("does NOT render the dropdown without a handler (unchanged UI)", () => {
      render(<DataPreview data={multiFrame()} />)
      expect(screen.queryByTestId("data-preview-frame-select")).toBeNull()
    })

    it("selecting a frame calls onSelectFrame with that frame's label", () => {
      const onSelectFrame = vi.fn()
      render(<DataPreview data={multiFrame()} onSelectFrame={onSelectFrame} />)
      const select = screen.getByTestId("data-preview-frame-select").querySelector("select")!
      fireEvent.change(select, { target: { value: "drivers" } })
      expect(onSelectFrame).toHaveBeenCalledWith("drivers")
    })

    it("reflects the active selection from selected_frame", () => {
      render(<DataPreview data={multiFrame({ selected_frame: "drivers" })} onSelectFrame={vi.fn()} />)
      const select = screen.getByTestId("data-preview-frame-select").querySelector("select")!
      expect(select.value).toBe("drivers")
    })
  })

  describe("multi-frame column rendering (empty flat `columns`)", () => {
    // A multi-frame producer reports column_count:0 / columns:[] by design —
    // it has no single representative schema; the selected frame's schema lives
    // in `frame_columns`. The preview must still render that frame's columns
    // (joining preview_columns ∩ frame_columns for dtypes); the regression was
    // a join against the EMPTY flat `columns`, which dropped every column and
    // left only row numbers.
    const multiFrameSelected = (overrides: Partial<PreviewData> = {}): PreviewData =>
      makePreview({
        column_count: 0,
        columns: [],
        frame_columns: {
          policies: [{ name: "policy_id", dtype: "i64" }],
          drivers: [
            { name: "driver_id", dtype: "i64" },
            { name: "age_band", dtype: "str" },
          ],
        },
        preview_columns: ["driver_id", "age_band"],
        preview: [
          { driver_id: 1, age_band: "30-59" },
          { driver_id: 2, age_band: "60+" },
        ],
        row_count: 2,
        selected_frame: "drivers",
        ...overrides,
      })

    it("renders the selected frame's columns from frame_columns (not just row numbers)", () => {
      render(<DataPreview data={multiFrameSelected()} onSelectFrame={vi.fn()} />)
      expect(screen.getByText("driver_id")).toBeInTheDocument()
      expect(screen.getByText("age_band")).toBeInTheDocument()
    })

    it("reports the selected frame's column count, not the empty flat count", () => {
      render(<DataPreview data={multiFrameSelected()} onSelectFrame={vi.fn()} />)
      expect(screen.getByText(/2 cols/)).toBeInTheDocument()
    })

    it("defaults to the FIRST frame's columns when no frame is explicitly selected", () => {
      render(
        <DataPreview
          data={multiFrameSelected({
            selected_frame: undefined,
            preview_columns: ["policy_id"],
            preview: [{ policy_id: 1001 }],
            row_count: 1,
          })}
          onSelectFrame={vi.fn()}
        />,
      )
      expect(screen.getByText("policy_id")).toBeInTheDocument()
    })

    it("still renders a previewed column whose dtype is absent from every schema", () => {
      // Defensive: a preview_column missing from both `columns` and
      // `frame_columns` must appear (unknown dtype) rather than vanish.
      render(
        <DataPreview
          data={multiFrameSelected({
            preview_columns: ["driver_id", "mystery"],
            preview: [{ driver_id: 1, mystery: "x" }],
          })}
          onSelectFrame={vi.fn()}
        />,
      )
      expect(screen.getByText("mystery")).toBeInTheDocument()
    })
  })
})
