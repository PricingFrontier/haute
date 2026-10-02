import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { TrainShapCurveFeature } from "../../../api/types"
import { makeTrainResult } from "../../../test-utils/factories"
import { formatChartNumber } from "../../../utils/chartHelpers"
import { ShapCurvesTab } from "../ShapCurvesTab"

afterEach(cleanup)

const AGE: TrainShapCurveFeature = {
  feature: "driver_age",
  kind: "numeric",
  levels_omitted: 0,
  points: [
    { value: 20, low: 17, high: 24, rows: 300, mean_shap: 0.3, p10_shap: 0.1, p90_shap: 0.5 },
    { value: 35, low: 25, high: 45, rows: 500, mean_shap: -0.05, p10_shap: -0.15, p90_shap: 0.05 },
    { value: 60, low: 46, high: 80, rows: 400, mean_shap: -0.1, p10_shap: -0.2, p90_shap: 0 },
    { value: null, low: null, high: null, rows: 20, mean_shap: 0.02, p10_shap: -0.01, p90_shap: 0.05 },
  ],
}

const REGION: TrainShapCurveFeature = {
  feature: "region",
  kind: "categorical",
  levels_omitted: 3,
  points: [
    { value: "north", low: null, high: null, rows: 600, mean_shap: 0.2, p10_shap: 0.15, p90_shap: 0.25 },
    { value: "south", low: null, high: null, rows: 400, mean_shap: -0.1, p10_shap: -0.2, p90_shap: 0 },
    { value: null, low: null, high: null, rows: 20, mean_shap: 0, p10_shap: -0.05, p90_shap: 0.05 },
  ],
}

function renderCurves(link: "log" | "identity") {
  return render(
    <ShapCurvesTab
      result={makeTrainResult({
        feature_importance: [
          { feature: "driver_age", importance: 2 },
          { feature: "region", importance: 1 },
        ],
        shap_curves: [AGE, REGION],
        shap_link: link,
      })}
    />,
  )
}

function detail(): HTMLElement {
  return screen.getByRole("status")
}

describe("ShapCurvesTab", () => {
  it("shows relativities around 1.0 under a log link", () => {
    renderCurves("log")

    expect(screen.getByText("Relativity (exp of mean SHAP)")).toBeInTheDocument()
    expect(screen.getByText("Baseline 1")).toBeInTheDocument()
    fireEvent.focus(screen.getAllByTestId("shap-curve-point")[0])
    expect(detail()).toHaveTextContent(`Relativity: ${formatChartNumber(Math.exp(0.3))}`)
    expect(detail()).toHaveTextContent(
      `10th to 90th percentile: ${formatChartNumber(Math.exp(0.1))} to ${formatChartNumber(Math.exp(0.5))}`,
    )
  })

  it("shows mean SHAP values around 0 under another link", () => {
    renderCurves("identity")

    expect(screen.getByText("Mean SHAP value (link scale)")).toBeInTheDocument()
    expect(screen.getByText("Baseline 0")).toBeInTheDocument()
    fireEvent.focus(screen.getAllByTestId("shap-curve-point")[0])
    expect(detail()).not.toHaveTextContent("Relativity")
    expect(detail()).toHaveTextContent(`10th to 90th percentile: ${formatChartNumber(0.1)} to ${formatChartNumber(0.5)}`)
    const table = screen.getByRole("table", { name: "driver_age SHAP curve values" })
    expect(within(table).queryByText("Relativity")).not.toBeInTheDocument()
  })

  it("draws a numeric curve's bands in order with the percentile range and a separate missing point", () => {
    renderCurves("log")

    const points = screen.getAllByTestId("shap-curve-point")
    expect(points).toHaveLength(4)
    const xs = points.slice(0, 3).map((point) => Number(point.getAttribute("cx")))
    expect(xs).toEqual([...xs].sort((a, b) => a - b))
    expect(screen.getByTestId("shap-curve-range")).toBeInTheDocument()
    expect(screen.getByText(/^numeric · 3 bands and missing values · 1,220 sampled rows/)).toBeInTheDocument()
    const missing = screen.getByTestId("shap-curve-missing")
    expect(within(missing).getByText("(missing)")).toBeInTheDocument()
    expect(Number(points[3].getAttribute("cx"))).toBeGreaterThan(xs[2])
  })

  it("names a focused band's rows, mean SHAP, relativity and range", () => {
    renderCurves("log")

    const [, middle] = screen.getAllByTestId("shap-curve-point")
    expect(middle).toHaveAttribute("aria-label", expect.stringContaining("driver_age 25 to 45: 500 rows"))
    fireEvent.focus(middle)
    expect(within(detail()).getByText("driver_age: 25 to 45")).toBeInTheDocument()
    expect(detail()).toHaveTextContent("Rows: 500")
    expect(detail()).toHaveTextContent(`Mean SHAP: ${formatChartNumber(-0.05)}`)
  })

  it("draws a categorical curve as a bar per level with whiskers and the omitted-level note", () => {
    renderCurves("log")

    fireEvent.click(screen.getByRole("button", { name: "region" }))

    const rows = screen.getAllByTestId("relativity-row")
    expect(rows.map((row) => within(row).getByTestId("relativity-label").textContent)).toEqual([
      "north",
      "south",
      "(missing)",
    ])
    expect(rows[0]).toHaveAttribute(
      "aria-label",
      `region north: 600 rows, mean SHAP ${formatChartNumber(0.2)}, relativity ${formatChartNumber(Math.exp(0.2))}, `
        + `10th to 90th percentile ${formatChartNumber(Math.exp(0.15))} to ${formatChartNumber(Math.exp(0.25))}`,
    )
    expect(screen.getAllByTestId("shap-curve-rows-bar")).toHaveLength(3)
    expect(screen.getByText("3 less frequent levels are not shown.")).toBeInTheDocument()
  })

  it("lists every point in the values table", () => {
    renderCurves("log")

    const table = screen.getByRole("table", { name: "driver_age SHAP curve values" })
    const rows = within(table).getAllByRole("row")
    expect([...rows[0].children].map((cell) => cell.textContent)).toEqual([
      "Band",
      "Rows",
      "Mean value",
      "Mean SHAP",
      "10th pct SHAP",
      "90th pct SHAP",
      "Relativity",
    ])
    expect(rows.slice(1).map((row) => row.children[0].textContent)).toEqual([
      "17 to 24",
      "25 to 45",
      "46 to 80",
      "(missing)",
    ])
    expect([...rows[4].children].map((cell) => cell.textContent)[2]).toBe("—")
  })
})
