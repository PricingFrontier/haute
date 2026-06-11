import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import {
  ChartEmptyState,
  ChartLegend,
  ChartSvg,
  MODELLING_CHART_AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR,
} from "../ChartScaffold"

afterEach(cleanup)

describe("ChartScaffold", () => {
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
    expect(svg.getAttribute("style")).toContain("background: var(--bg-input)")
    expect(svg.getAttribute("style")).toContain("border-radius: 6px")
    expect(svg.getAttribute("style")).toContain("border: 1px solid var(--border)")
    expect(container.querySelector("path")?.getAttribute("d")).toBe("M0,0 L10,10")
  })

  it("keeps repeated axis constants in one modelling-local module", () => {
    expect(MODELLING_CHART_GRID_COLOR).toBe("rgba(255,255,255,.06)")
    expect(MODELLING_CHART_AXIS_TEXT_COLOR).toBe("var(--text-muted)")
    expect(MODELLING_CHART_AXIS_FONT_SIZE).toBe(10)
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
    expect(swatches[1]).toHaveStyle({ background: "gold" })
    expect(swatches[1].getAttribute("style")).toContain("border-top: 1px dashed gold")
    expect(swatches[2]).toHaveClass("w-3", "h-2", "rounded-sm")
    expect(swatches[2]).toHaveStyle({ background: "grey", opacity: "0.7" })
  })
})
