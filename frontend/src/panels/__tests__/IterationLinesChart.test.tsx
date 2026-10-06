import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { IterationLinesChart, type IterationLinesChartProps } from "../IterationLinesChart"

afterEach(cleanup)

function renderChart(overrides: Partial<IterationLinesChartProps> = {}) {
  const props: IterationLinesChartProps = {
    x: [1, 2, 3],
    series: [{ label: "Objective", color: "red", values: [10, 20, 30] }],
    width: 400,
    height: 200,
    title: "Objective by iteration",
    ariaLabel: "Objective chart",
    xLabel: "Iteration",
    yLabel: "Objective",
    ...overrides,
  }
  return render(<IterationLinesChart {...props} />)
}

/** The value axis: each tick's label and the y of its gridline. */
function ticks(container: HTMLElement): Map<string, number> {
  return new Map(
    Array.from(container.querySelectorAll('[data-testid="chart-value-tick"]')).map((tick) => [
      tick.querySelector("text")!.textContent!,
      Number(tick.querySelector("line")!.getAttribute("y1")),
    ]),
  )
}

/** A series path's points as [x, y] pairs. */
function pathPoints(path: Element): Array<[number, number]> {
  return (path.getAttribute("d") ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((command) => command.slice(1).split(",").map(Number) as [number, number])
}

function seriesPath(container: HTMLElement, label: string): Element {
  const path = container.querySelector(`path[data-series="${label}"]`)
  if (!path) throw new Error(`no path for ${label}`)
  return path
}

describe("IterationLinesChart", () => {
  it("draws on a real value axis: a value sits on the gridline of its tick", () => {
    const { container } = renderChart()

    const axis = ticks(container)
    // The padded domain [8.4, 31.6] in five even ticks puts 20 on the middle one.
    expect([...axis.keys()]).toEqual(["8.4", "14.2", "20.0", "25.8", "31.6"])
    const [, middle] = pathPoints(seriesPath(container, "Objective"))
    expect(middle[1]).toBeCloseTo(axis.get("20.0")!, 1)
    expect(screen.getByRole("img", { name: "Objective chart" })).toBeInTheDocument()
    expect(container.querySelector("title")?.textContent).toBe("Objective by iteration")
  })

  it("labels a narrow value range with distinct numbers at one precision", () => {
    // A loss near convergence: three significant figures would label every tick "107".
    const { container } = renderChart({
      series: [{ label: "Objective", color: "red", values: [107.02, 107.1, 107.28] }],
    })

    const labels = [...ticks(container).keys()]
    expect(labels).toEqual(["107.00", "107.07", "107.15", "107.23", "107.30"])
  })

  it("labels the x axis with each index's iteration value", () => {
    const { container } = renderChart({ x: [0, 5, 10] })

    const labels = Array.from(container.querySelectorAll('[data-testid="chart-x-tick"] text')).map(
      (text) => text.textContent,
    )
    expect(labels).toEqual(["0", "5", "10"])
    expect(screen.getByText("Iteration")).toBeInTheDocument()
  })

  it("draws a log axis on decades", () => {
    const { container } = renderChart({
      yScale: "log",
      series: [{ label: "Change", color: "blue", values: [1, 0.1, 0.001] }],
    })

    const axis = ticks(container)
    expect([...axis.keys()]).toEqual(["0.001", "0.01", "0.1", "1"])
    const points = pathPoints(seriesPath(container, "Change"))
    expect(points[0][1]).toBeCloseTo(axis.get("1")!, 1)
    expect(points[1][1]).toBeCloseTo(axis.get("0.1")!, 1)
    expect(points[2][1]).toBeCloseTo(axis.get("0.001")!, 1)
  })

  it("gives a log axis of one value a decade either side", () => {
    const { container } = renderChart({
      yScale: "log",
      series: [{ label: "Change", color: "blue", values: [0.01, 0.01, 0.01] }],
    })

    expect([...ticks(container).keys()]).toEqual(["0.001", "0.01", "0.1"])
  })

  it("refuses a value at or below zero on a log axis", () => {
    expect(() =>
      renderChart({ yScale: "log", series: [{ label: "Change", color: "blue", values: [0.1, 0, 0.01] }] }),
    ).toThrow(/log axis.*Change.*0/)
  })

  it("refuses a non-finite value rather than drawing it", () => {
    expect(() =>
      renderChart({ series: [{ label: "Objective", color: "red", values: [1, Number.NaN, 3] }] }),
    ).toThrow(/Objective.*finite/)
  })

  it("refuses a series that does not align with the x values", () => {
    expect(() =>
      renderChart({ series: [{ label: "Objective", color: "red", values: [1, 2] }] }),
    ).toThrow(/Objective.*2 values.*3/)
  })

  it("bridges a null gap and keeps every coordinate finite", () => {
    const { container } = renderChart({
      x: [1, 2, 3, 4],
      series: [{ label: "Objective", color: "red", values: [10, null, 30, 40] }],
    })

    const points = pathPoints(seriesPath(container, "Objective"))
    expect(points).toHaveLength(3)
    expect(seriesPath(container, "Objective").getAttribute("d")).toMatch(/^M[^M]*$/)
    expect(points.flat().every(Number.isFinite)).toBe(true)
  })

  it("draws a dashed reference line inside the value domain, and names it in the legend", () => {
    const { container } = renderChart({
      referenceLines: [{ label: "max bound 50", value: 50, color: "grey" }],
    })

    const line = container.querySelector('line[data-reference="max bound 50"]')!
    expect(line.getAttribute("stroke-dasharray")).toBe("5,3")
    const tickYs = [...ticks(container).values()]
    const lineY = Number(line.getAttribute("y1"))
    const highestDataY = pathPoints(seriesPath(container, "Objective"))[2][1]
    // 50 lies above the data (10..30), so the domain grows to hold it.
    expect(lineY).toBeGreaterThanOrEqual(Math.min(...tickYs))
    expect(lineY).toBeLessThan(highestDataY)
    expect(screen.getByText("max bound 50")).toBeInTheDocument()
  })

  it("draws a dashed vertical marker at an index, and names it in the legend", () => {
    const { container } = renderChart({ marker: { index: 1, label: "First feasible (iteration 2)", color: "green" } })

    const marker = container.querySelector('line[data-marker="First feasible (iteration 2)"]')!
    const [, middle] = pathPoints(seriesPath(container, "Objective"))
    expect(Number(marker.getAttribute("x1"))).toBeCloseTo(middle[0], 1)
    expect(marker.getAttribute("stroke-dasharray")).toBe("5,3")
    expect(screen.getByText("First feasible (iteration 2)")).toBeInTheDocument()
  })

  it("refuses a marker outside the x values", () => {
    expect(() => renderChart({ marker: { index: 3, label: "Late", color: "green" } })).toThrow(/Late.*index 3/)
  })

  it("marks every point when asked, and a lone point always", () => {
    const withPoints = renderChart({ showPoints: true })
    expect(withPoints.container.querySelectorAll('circle[data-series="Objective"]')).toHaveLength(3)
    cleanup()

    const lone = renderChart({ x: [7], series: [{ label: "Objective", color: "red", values: [5] }] })
    const circle = lone.container.querySelector('circle[data-series="Objective"]')!
    expect(Number.isFinite(Number(circle.getAttribute("cx")))).toBe(true)
    expect(Number.isFinite(Number(circle.getAttribute("cy")))).toBe(true)
  })

  it("lists each series in the legend", () => {
    const { container } = renderChart({
      series: [
        { label: "region", color: "red", values: [1, 2, 3] },
        { label: "age", color: "blue", values: [3, 2, 1] },
      ],
    })

    const legend = container.querySelectorAll('[data-testid="chart-legend-swatch"]')
    expect(legend).toHaveLength(2)
    expect(within(container).getByText("region")).toBeInTheDocument()
    expect(within(container).getByText("age")).toBeInTheDocument()
  })
})
