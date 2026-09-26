import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import FrontierChart, { FRONTIER_CHART_LAYOUT, type FrontierChartPoint } from "../FrontierChart"
import type { FrontierPointStatus } from "../frontierSlices"

const WIDTH = 480

/** Slice points in bound order, each with its global index. */
function slicePoints(
  rows: [index: number, x: number, y: number, status?: FrontierPointStatus][],
): FrontierChartPoint[] {
  return rows.map(([index, x, y, status = "feasible"]) => ({ index, x, y, status }))
}

function renderChart(overrides: Partial<Parameters<typeof FrontierChart>[0]> = {}) {
  const onPointClick = vi.fn()
  const utils = render(
    <FrontierChart
      points={slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110],
        [2, 0.59, 120],
      ])}
      xLabel="loss_ratio"
      yLabel="expected_income"
      selectedIdx={null}
      asSolved={{ x: 0.57, y: 110, onSlice: true }}
      onPointClick={onPointClick}
      width={WIDTH}
      {...overrides}
    />,
  )
  return { ...utils, onPointClick }
}

function lineCommands(container: HTMLElement): string[] {
  const line = container.querySelector('[data-series="frontier-line"]')
  if (!line) return []
  return (line.getAttribute("d") ?? "").match(/[ML]/g) ?? []
}

describe("FrontierChart", () => {
  afterEach(cleanup)

  it("keeps overlapping frontier rows at the true coordinate without visual spread", () => {
    const { onPointClick } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110],
        [4, 0.57, 110],
        [2, 0.59, 120],
        [3, 0.61, 130],
      ]),
    })

    const pointTwo = screen.getByRole("button", { name: /Select frontier point 2/ })

    expect(pointTwo).toHaveAttribute("data-overlap-count", "2")
    expect(pointTwo).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(pointTwo).toHaveAttribute("r", "4")
    expect(screen.queryByRole("button", { name: "Select frontier point 5" })).not.toBeInTheDocument()
    expect(screen.getAllByRole("button")).toHaveLength(4)

    fireEvent.click(pointTwo)
    expect(onPointClick).toHaveBeenCalledWith(1)

    fireEvent.keyDown(pointTwo, { key: "Enter" })
    expect(onPointClick).toHaveBeenCalledTimes(2)
    expect(onPointClick).toHaveBeenLastCalledWith(1)
  })

  it("highlights the aggregate marker when a duplicate coordinate is selected", () => {
    renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110],
        [3, 0.57, 110],
        [2, 0.59, 120],
      ]),
      selectedIdx: 3,
    })

    const aggregatePoint = screen.getByRole("button", { name: /Select frontier point 2/ })
    expect(aggregatePoint).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(aggregatePoint).toHaveAttribute("r", "6")
    expect(screen.queryByRole("button", { name: "Select frontier point 4" })).not.toBeInTheDocument()
    expect(screen.getAllByRole("button")).toHaveLength(3)
  })

  it("keeps point 2 as the visible aggregate target when the first overlapping row is selected", () => {
    const { onPointClick } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.55, 100],
        [2, 0.59, 120],
      ]),
      selectedIdx: 0,
    })

    const pointTwo = screen.getByRole("button", { name: /Select frontier point 2/ })
    expect(pointTwo).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(pointTwo).toHaveAttribute("r", "6")
    expect(screen.queryByRole("button", { name: "Select frontier point 1" })).not.toBeInTheDocument()

    fireEvent.click(pointTwo)
    expect(onPointClick).toHaveBeenCalledWith(1)
  })

  it("names the points by their global index, not their place in the slice", () => {
    const { onPointClick } = renderChart({
      points: slicePoints([
        [1, 400, 140],
        [3, 450, 128],
        [5, 500, 110],
      ]),
    })
    const labels = screen.getAllByRole("button").map((button) => button.getAttribute("aria-label"))
    expect(labels).toEqual(["Select frontier point 2", "Select frontier point 4", "Select frontier point 6"])
    fireEvent.click(screen.getByRole("button", { name: "Select frontier point 4" }))
    expect(onPointClick).toHaveBeenCalledWith(3)
  })

  it("keeps the as-solved marker decorative so overlapping points remain clickable", () => {
    renderChart()
    expect(screen.getByRole("button", { name: "Select frontier point 2" })).toBeInTheDocument()

    const marker = screen.getByTestId("frontier-as-solved-marker")
    expect(marker).toHaveAttribute("aria-hidden", "true")
    expect(marker).toHaveStyle({ pointerEvents: "none" })
    expect(marker).toHaveAttribute("pointer-events", "none")
    const circles = marker.querySelectorAll("circle")
    expect(circles).toHaveLength(2)
    circles.forEach((circle) => expect(circle).toHaveAttribute("pointer-events", "none"))
  })

  it("draws the as-solved anchor hollow, with the legend note, when it lies off the slice", () => {
    renderChart({ asSolved: { x: 0.57, y: 110, onSlice: false } })
    const marker = screen.getByTestId("frontier-as-solved-marker")
    expect(marker).toHaveAttribute("data-on-slice", "false")
    // The ring stays; the filled centre that marks an on-slice solve does not.
    expect(marker.querySelectorAll("circle")).toHaveLength(1)
    expect(screen.getByText("As solved (different slice)")).toBeInTheDocument()
    expect(screen.queryByText("As solved")).not.toBeInTheDocument()
  })

  it("names both axes at 12 px and offers a legend of every marker", () => {
    const { container } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110, "not_converged"],
        [2, 0.59, 120, "breached"],
      ]),
    })
    const chart = screen.getByRole("group", { name: "Efficient frontier: expected_income against loss_ratio" })
    const xAxis = within(chart).getByText("loss_ratio")
    const yAxis = within(chart).getByText("expected_income")
    expect(xAxis).toHaveAttribute("font-size", "12")
    expect(yAxis).toHaveAttribute("font-size", "12")
    Array.from(container.querySelectorAll('[data-testid="chart-value-tick"] text')).forEach((tick) =>
      expect(tick).toHaveAttribute("font-size", "12"),
    )
    for (const entry of ["Frontier point", "As solved", "Selected point", "Not converged", "Breached"]) {
      expect(screen.getByText(entry)).toBeInTheDocument()
    }
  })

  it("leaves the not-converged and breached legend entries out when the slice has none", () => {
    renderChart()
    expect(screen.getByText("Frontier point")).toBeInTheDocument()
    expect(screen.queryByText("Not converged")).not.toBeInTheDocument()
    expect(screen.queryByText("Breached")).not.toBeInTheDocument()
  })

  it("draws a non-converged point hollow and a converged-but-breached point as a cross, both selectable", () => {
    const { container, onPointClick } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110, "not_converged"],
        [2, 0.59, 120, "breached"],
      ]),
    })
    const feasible = screen.getByRole("button", { name: "Select frontier point 1" })
    const hollow = screen.getByRole("button", { name: "Select frontier point 2 (not converged)" })
    const breached = screen.getByRole("button", { name: "Select frontier point 3 (breached)" })

    expect(feasible).toHaveAttribute("data-status", "feasible")
    expect(feasible.getAttribute("fill")).not.toBe("transparent")
    expect(hollow).toHaveAttribute("data-status", "not_converged")
    expect(hollow).toHaveAttribute("fill", "transparent")
    expect(hollow.getAttribute("stroke")).not.toBe("none")
    expect(breached).toHaveAttribute("data-status", "breached")
    // One cross on the plot (the legend draws its own swatch).
    expect(container.querySelectorAll('svg[role="group"] [data-marker="cross"]')).toHaveLength(1)

    fireEvent.click(hollow)
    fireEvent.keyDown(breached, { key: " " })
    expect(onPointClick.mock.calls).toEqual([[1], [2]])
  })

  it("joins feasible points in bound order and breaks the line at a non-converged point", () => {
    const { container } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110],
        [2, 0.59, 120, "not_converged"],
        [3, 0.61, 130],
        [4, 0.63, 140],
      ]),
    })
    // Two runs, 1–2 and 4–5; point 3 is never joined.
    expect(lineCommands(container)).toEqual(["M", "L", "M", "L"])
  })

  it("never joins a breached point", () => {
    const { container } = renderChart({
      points: slicePoints([
        [0, 0.55, 100],
        [1, 0.57, 110, "breached"],
        [2, 0.59, 120],
      ]),
    })
    expect(lineCommands(container)).toEqual([])
  })

  it("states a hovered or focused point's exact values in the live detail line", () => {
    renderChart()
    const detail = screen.getByRole("status")
    expect(detail).toHaveTextContent("Hover or focus a frontier point to inspect its values.")

    fireEvent.focus(screen.getByRole("button", { name: "Select frontier point 3" }))
    expect(detail).toHaveTextContent("Point 3")
    expect(detail).toHaveTextContent("expected_income: 120")
    expect(detail).toHaveTextContent("loss_ratio: 0.59")
    expect(detail).toHaveTextContent("Feasible")

    fireEvent.mouseEnter(screen.getByRole("button", { name: "Select frontier point 1" }))
    expect(detail).toHaveTextContent("Point 1")
  })

  it("plots on padded domains with ticks over the data range at the container width", () => {
    const { left, right, top, plotBottom } = FRONTIER_CHART_LAYOUT
    const plotWidth = WIDTH - left - right
    const plotHeight = plotBottom - top
    const { container } = renderChart({
      points: slicePoints([
        [0, 0, 10],
        [1, 100, 20],
      ]),
      asSolved: { x: 50, y: 15, onSlice: true },
    })

    // x domain [-8, 108] and y domain [9.2, 20.8].
    const first = screen.getByRole("button", { name: "Select frontier point 1" })
    const second = screen.getByRole("button", { name: "Select frontier point 2" })
    expect(Number(first.getAttribute("cx"))).toBeCloseTo(left + (8 / 116) * plotWidth, 6)
    expect(Number(first.getAttribute("cy"))).toBeCloseTo(plotBottom - (0.8 / 11.6) * plotHeight, 6)
    expect(Number(second.getAttribute("cx"))).toBeCloseTo(left + (108 / 116) * plotWidth, 6)
    expect(Number(second.getAttribute("cy"))).toBeCloseTo(plotBottom - (10.8 / 11.6) * plotHeight, 6)

    const labels = Array.from(container.querySelectorAll("svg text")).map((text) => text.textContent)
    expect(labels).toEqual(expect.arrayContaining(["10.0", "20.0", "0", "100"]))
  })

  it("gives a single repeated point a finite scale and one tick per axis", () => {
    const { left, right, top, plotBottom } = FRONTIER_CHART_LAYOUT
    const { container } = renderChart({
      points: slicePoints([[0, 0.5, 5]]),
      asSolved: { x: 0.5, y: 5, onSlice: true },
    })
    const point = screen.getByRole("button", { name: "Select frontier point 1" })
    expect(Number(point.getAttribute("cx"))).toBeCloseTo(left + (WIDTH - left - right) / 2, 6)
    expect(Number(point.getAttribute("cy"))).toBeCloseTo(top + (plotBottom - top) / 2, 6)
    const tickLabels = Array.from(container.querySelectorAll("svg text"))
      .map((text) => text.textContent)
      .filter((text) => text === "5" || text === "0.5")
    expect(tickLabels).toEqual(["5", "0.5"])
  })
})
