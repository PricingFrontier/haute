import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  ChartEmptyState,
  ChartLegend,
  ChartSvg,
  ChartValuesTable,
  ChartValueGrid,
  ResponsiveChart,
  TwoChartLayout,
  MODELLING_CHART_AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR,
} from "../ChartScaffold"

afterEach(cleanup)

describe("ChartScaffold", () => {
  it("remeasures chart geometry without scaling down its label text", () => {
    let resize: ResizeObserverCallback
    const disconnect = vi.fn()
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          resize = callback
        }
        observe() {}
        disconnect = disconnect
      },
    )
    const { unmount } = render(
      <ResponsiveChart>
        {(width) => (
          <ChartSvg width={width} height={240} ariaLabel="Responsive chart">
            <text fontSize={12}>Label</text>
          </ChartSvg>
        )}
      </ResponsiveChart>,
    )
    act(() =>
      resize([{ contentRect: { width: 410 } } as ResizeObserverEntry], {} as ResizeObserver),
    )
    expect(screen.getByRole("img")).toHaveAttribute("width", "410")
    act(() =>
      resize([{ contentRect: { width: 820 } } as ResizeObserverEntry], {} as ResizeObserver),
    )
    expect(screen.getByRole("img")).toHaveAttribute("width", "820")
    expect(screen.getByText("Label")).toHaveAttribute("font-size", "12")
    expect(screen.getByRole("img")).not.toHaveAttribute("viewBox")
    unmount()
    expect(disconnect).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
  it("renders the shared modelling chart svg shell without owning chart geometry", () => {
    const { container } = render(
      <ChartSvg width={320} height={160} className="mt-1" data-testid="chart-shell">
        <path d="M0,0 L10,10" />
      </ChartSvg>,
    )

    const svg = screen.getByTestId("chart-shell")
    expect(svg).toHaveAttribute("width", "320")
    expect(svg).toHaveAttribute("height", "160")
    expect(svg).toHaveClass("mt-1")
    expect(svg.getAttribute("style")).toContain("display: block")
    expect(container.querySelector("path")?.getAttribute("d")).toBe("M0,0 L10,10")
  })

  it("keeps repeated axis constants in one modelling-local module", () => {
    expect(MODELLING_CHART_GRID_COLOR).toBe("var(--border)")
    expect(MODELLING_CHART_AXIS_TEXT_COLOR).toBe("var(--text-muted)")
    expect(MODELLING_CHART_AXIS_FONT_SIZE).toBe(12)
  })

  it("renders the shared full-panel empty state", () => {
    render(<ChartEmptyState>No chart data available</ChartEmptyState>)

    const emptyState = screen.getByText("No chart data available")
    expect(emptyState).toHaveClass("flex", "items-center", "justify-center", "h-full", "text-xs")
    expect(emptyState).toHaveStyle({ color: "var(--text-muted)" })
  })

  it("renders line, dashed, and bar legend swatches", () => {
    render(
      <ChartLegend
        items={[
          { label: "Actual", color: "green" },
          { label: "Best", color: "gold", swatch: "dashed" },
          { label: "Exposure", color: "grey", swatch: "bar", opacity: 0.7 },
        ]}
      />,
    )

    expect(screen.getByText("Actual")).toBeInTheDocument()
    expect(screen.getByText("Best")).toBeInTheDocument()
    expect(screen.getByText("Exposure")).toBeInTheDocument()

    const swatches = screen.getAllByTestId("chart-legend-swatch")
    expect(swatches[0]).toHaveClass("w-3", "h-0.5")
    expect(swatches[0]).toHaveStyle({ background: "green" })
    expect(swatches[1]).not.toHaveStyle({ background: "gold" })
    expect(swatches[1].getAttribute("style")).toContain("border-top: 1px dashed gold")
    expect(swatches[2]).toHaveClass("w-3", "h-2", "rounded-sm")
    expect(swatches[2]).toHaveStyle({ background: "grey", opacity: "0.7" })
  })

  it("draws a gridline and a right-aligned compact label for every value tick", () => {
    render(
      <svg>
        <ChartValueGrid ticks={[0, 12_345]} left={60} right={300} y={(value) => 200 - value / 100} labelGap={8} />
      </svg>,
    )

    const ticks = screen.getAllByTestId("chart-value-tick")
    expect(ticks).toHaveLength(2)
    const [line, label] = [ticks[1].querySelector("line")!, ticks[1].querySelector("text")!]
    expect(line.getAttribute("x1")).toBe("60")
    expect(line.getAttribute("x2")).toBe("300")
    expect(line.getAttribute("y1")).toBe(String(200 - 123.45))
    expect(line.getAttribute("y2")).toBe(String(200 - 123.45))
    expect(label.getAttribute("y")).toBe(String(200 - 123.45 + 4))
    expect(line.getAttribute("stroke")).toBe(MODELLING_CHART_GRID_COLOR)
    expect(label.textContent).toBe("12.3K")
    expect(label.getAttribute("x")).toBe("52")
    expect(label.getAttribute("text-anchor")).toBe("end")
  })
})

describe("TwoChartLayout", () => {
  const layout = (width: number, bothCharts = true) =>
    render(
      <TwoChartLayout
        width={width}
        ariaLabel="Result charts"
        bothCharts={bothCharts}
        sideBySideFrom={600}
        minChartWidth={290}
        header={(sideBySide) => (sideBySide ? null : <p>narrow header</p>)}
      >
        {({ sideBySide, chartWidth }) => (
          <>
            <span data-testid="layout">{`${sideBySide ? "side" : "stacked"} ${chartWidth}`}</span>
            <span data-testid="second-chart" />
          </>
        )}
      </TwoChartLayout>,
    )

  // The element that holds the two charts.
  const chartRow = () => screen.getByTestId("second-chart").parentElement!

  it("puts both charts side by side from the breakpoint, halving the width less the gap", () => {
    layout(800)
    expect(screen.getByTestId("layout").textContent).toBe("side 388")
    expect(chartRow()).toHaveClass("grid", "grid-cols-2", "gap-6")
    expect(chartRow()).not.toHaveClass("space-y-6")
    expect(chartRow().parentElement).toBe(screen.getByRole("region", { name: "Result charts" }))
    expect(screen.queryByText("narrow header")).toBeNull()
  })

  it("never makes a side-by-side chart narrower than the minimum", () => {
    layout(600)
    expect(screen.getByTestId("layout").textContent).toBe("side 290")
  })

  it("stacks full-width charts below the breakpoint, or when only one chart exists", () => {
    const { unmount } = layout(599)
    expect(screen.getByTestId("layout").textContent).toBe("stacked 599")
    expect(chartRow()).toHaveClass("space-y-6")
    expect(chartRow()).not.toHaveClass("grid")
    expect(chartRow()).not.toHaveClass("grid-cols-2")
    expect(screen.getByText("narrow header")).toBeInTheDocument()
    unmount()
    layout(1200, false)
    expect(screen.getByTestId("layout").textContent).toBe("stacked 1200")
    expect(chartRow()).toHaveClass("space-y-6")
    expect(chartRow()).not.toHaveClass("grid-cols-2")
  })
})

describe("ChartValuesTable", () => {
  it("keeps a chart's values behind a closed disclosure, one header per column and per row", () => {
    render(
      <ChartValuesTable
        summary="View bin values"
        ariaLabel="Bin values"
        headers={["Bin", "Actual"]}
        rows={[
          ["low", "0.1"],
          ["high", <span key="v">0.9</span>],
        ]}
      />,
    )
    expect(screen.getByText("View bin values").closest("details")).not.toHaveAttribute("open")
    const table = screen.getByRole("table", { name: "Bin values" })
    expect(table).toHaveClass("validation-value-table")
    expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Bin", "Actual"])
    expect(screen.getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["low", "high"])
    expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["0.1", "0.9"])
  })
})
