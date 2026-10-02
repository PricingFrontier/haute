import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { TrainShapBeeswarmFeature } from "../../../api/types"
import { formatChartNumber } from "../../../utils/chartHelpers"
import ShapBeeswarm from "../ShapBeeswarm"
import { VALUE_NEUTRAL_COLOR, beeswarmOffsets, valuePositionColor } from "../beeswarm"

afterEach(cleanup)

const AGE: TrainShapBeeswarmFeature = {
  feature: "driver_age",
  kind: "numeric",
  shap_values: [0.3, -0.1, 0.05, -0.2],
  values: [22, 45, null, 60],
  value_ranks: [0, 0.5, null, 1],
}

const REGION: TrainShapBeeswarmFeature = {
  feature: "region",
  kind: "categorical",
  shap_values: [0.1, -0.05, 0, 0.02],
  values: ["north", "south", null, "east"],
  value_ranks: [null, null, null, null],
}

function dots(feature?: number): SVGCircleElement[] {
  const all = [...screen.getByTestId("shap-beeswarm-dots").querySelectorAll("circle")]
  return feature === undefined ? all : all.filter((dot) => dot.dataset.feature === String(feature))
}

function detail(): HTMLElement {
  return screen.getByRole("status")
}

describe("ShapBeeswarm", () => {
  it("draws one row per feature in the given order and one dot per row value", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={640} />)

    const rows = screen.getAllByTestId("shap-beeswarm-feature")
    expect(rows.map((row) => row.getAttribute("aria-label")?.split(":")[0])).toEqual([
      "driver_age",
      "region",
    ])
    expect(dots(0)).toHaveLength(4)
    expect(dots(1)).toHaveLength(4)
    // Larger SHAP values sit further right.
    const [first, second] = dots(0)
    expect(Number(first.getAttribute("cx"))).toBeGreaterThan(Number(second.getAttribute("cx")))
  })

  it("colours numeric dots by value rank and unordered dots neutral, with the legend note", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={640} />)

    expect(dots(0).map((dot) => dot.getAttribute("fill"))).toEqual([
      valuePositionColor(0),
      valuePositionColor(0.5),
      VALUE_NEUTRAL_COLOR,
      valuePositionColor(1),
    ])
    expect(new Set(dots(1).map((dot) => dot.getAttribute("fill")))).toEqual(
      new Set([VALUE_NEUTRAL_COLOR]),
    )
    expect(screen.getByText("No value order (categorical level or missing value)")).toBeInTheDocument()
    expect(screen.getByTestId("shap-beeswarm-colour-bar")).toBeInTheDocument()
  })

  it("leaves the legend note out when every dot has a value rank", () => {
    render(
      <ShapBeeswarm
        features={[{ ...AGE, values: [22, 45, 50, 60], value_ranks: [0, 0.33, 0.67, 1] }]}
        width={640}
      />,
    )

    expect(screen.queryByText(/No value order/)).not.toBeInTheDocument()
  })

  it("stacks dense dots within the row and leaves sparse dots on the row line", () => {
    const stacking = { bucketWidth: 4.8, laneStep: 3.6, halfHeight: 12 }
    const dense = beeswarmOffsets(Array(500).fill(100), stacking)
    expect(Math.max(...dense.map(Math.abs))).toBeCloseTo(12)
    expect(new Set(dense).size).toBeGreaterThan(100)
    expect(beeswarmOffsets([0, 50, 100, 150], stacking)).toEqual([0, 0, 0, 0])

    render(
      <ShapBeeswarm
        features={[
          {
            feature: "flat",
            kind: "numeric",
            shap_values: Array(500).fill(0.1),
            values: Array(500).fill(1),
            value_ranks: Array(500).fill(null),
          },
        ]}
        width={640}
      />,
    )
    const ys = dots(0).map((dot) => Number(dot.getAttribute("cy")))
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThanOrEqual(24)
  })

  it("names a pointed-at dot's feature, value and SHAP value", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={640} />)

    fireEvent.mouseOver(dots(0)[0])
    expect(within(detail()).getByText("driver_age")).toBeInTheDocument()
    expect(detail()).toHaveTextContent("Value: 22")
    expect(detail()).toHaveTextContent(`SHAP: ${formatChartNumber(0.3)}`)
    expect(detail()).toHaveTextContent("Colour: value rank 0.00 of low 0 to high 1")
    expect(screen.getByTestId("shap-beeswarm-pointed")).toHaveAttribute("cx", dots(0)[0].getAttribute("cx"))

    fireEvent.mouseOver(dots(0)[2])
    expect(detail()).toHaveTextContent("Value: (missing)")
    expect(detail()).toHaveTextContent("Colour: no value order (missing value)")

    fireEvent.mouseOver(dots(1)[0])
    expect(detail()).toHaveTextContent("Value: north")
    expect(detail()).toHaveTextContent("Colour: no value order (categorical level)")
  })

  it("gives each feature row one tab stop whose focus states its rows and SHAP range", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={640} />)

    expect(dots().every((dot) => !dot.hasAttribute("tabindex"))).toBe(true)
    const [ageRow] = screen.getAllByTestId("shap-beeswarm-feature")
    expect(ageRow).toHaveAttribute("tabindex", "0")
    expect(ageRow).toHaveAttribute("role", "button")

    fireEvent.focus(ageRow)
    expect(ageRow).toHaveAttribute("aria-pressed", "true")
    expect(detail()).toHaveTextContent("Rows: 4")
    expect(detail()).toHaveTextContent(`SHAP: ${formatChartNumber(-0.2)} to ${formatChartNumber(0.3)}`)
  })

  it("lists each feature's rows and minimum, median and maximum SHAP in the values table", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={640} />)

    const table = screen.getByRole("table", { name: "SHAP beeswarm values" })
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows.map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([
      ["driver_age", "4", formatChartNumber(-0.2), formatChartNumber(-0.025), formatChartNumber(0.3)],
      ["region", "4", formatChartNumber(-0.05), formatChartNumber(0.01), formatChartNumber(0.1)],
    ])
  })
})
