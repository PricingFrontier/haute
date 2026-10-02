import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { TrainShapBeeswarmFeature } from "../../../api/types"
import { BEESWARM_TOKENS, stubBeeswarmCanvas, type PaintedDot } from "../../../test-utils/beeswarmCanvas"
import { formatChartNumber } from "../../../utils/chartHelpers"
import {
  MODELLING_CHART_AXIS_FONT_SIZE,
  MODELLING_CHART_AXIS_TEXT_COLOR,
  MODELLING_CHART_GRID_COLOR,
} from "../ChartScaffold"
import ShapBeeswarm from "../ShapBeeswarm"
import { beeswarmOffsets, parseHexColor, valuePositionRgb } from "../beeswarm"
import { SHAP_BEESWARM_GEOMETRY, layoutShapBeeswarm, nearestDot } from "../shapBeeswarmLayout"

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

const WIDTH = 640
const LOW = parseHexColor(BEESWARM_TOKENS.low)
const HIGH = parseHexColor(BEESWARM_TOKENS.high)
const NEUTRAL = `rgb(${parseHexColor(BEESWARM_TOKENS.none).join(", ")})`

let painting: { dots: PaintedDot[] }

beforeEach(() => {
  painting = stubBeeswarmCanvas()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function detail(): HTMLElement {
  return screen.getByRole("status")
}

/** The fill each laid-out dot was painted with, in layout order. */
function paintedFills(features: TrainShapBeeswarmFeature[]): string[] {
  const layout = layoutShapBeeswarm(features, WIDTH)
  const byPosition = new Map(painting.dots.map((dot) => [`${dot.x},${dot.y}`, dot.fill]))
  return Array.from(layout.x, (x, index) => byPosition.get(`${x},${layout.y[index]}`) ?? "unpainted")
}

describe("ShapBeeswarm", () => {
  it("lays out one row per feature in the given order and paints one dot per row value", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)

    const rows = screen.getAllByTestId("shap-beeswarm-feature")
    expect(rows.map((row) => row.getAttribute("aria-label")?.split(":")[0])).toEqual([
      "driver_age",
      "region",
    ])
    expect(painting.dots).toHaveLength(8)
    const layout = layoutShapBeeswarm([AGE, REGION], WIDTH)
    expect(Array.from(layout.feature)).toEqual([0, 0, 0, 0, 1, 1, 1, 1])
    // Larger SHAP values sit further right.
    expect(layout.x[0]).toBeGreaterThan(layout.x[1])
    expect(screen.getByTestId("shap-beeswarm-dots")).toHaveAttribute("aria-hidden", "true")
  })

  it("paints numeric dots in their rank colour and unordered dots neutral, with the legend note", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)

    expect(paintedFills([AGE, REGION])).toEqual([
      valuePositionRgb(0, LOW, HIGH),
      valuePositionRgb(0.5, LOW, HIGH),
      NEUTRAL,
      valuePositionRgb(1, LOW, HIGH),
      NEUTRAL,
      NEUTRAL,
      NEUTRAL,
      NEUTRAL,
    ])
    expect(valuePositionRgb(0, LOW, HIGH)).toBe("rgb(0, 139, 251)")
    expect(screen.getByText("No value order (categorical level or missing value)")).toBeInTheDocument()
    expect(screen.getByTestId("shap-beeswarm-colour-bar")).toBeInTheDocument()
  })

  it("draws on the panel surface in the modelling charts' text and grid tokens", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)

    const chart = screen.getByRole("img", { name: "SHAP beeswarm of 2 features" })
    expect(chart.style.background).toBe("")
    expect(chart.innerHTML).not.toMatch(/chart-impact|#[0-9a-f]{3,6}\b|rgba?\(/i)
    const axisTitle = within(chart).getByText("SHAP value (link scale)")
    expect(axisTitle).toHaveAttribute("fill", MODELLING_CHART_AXIS_TEXT_COLOR)
    expect(axisTitle).toHaveAttribute("font-size", String(MODELLING_CHART_AXIS_FONT_SIZE))
    expect(chart.querySelector(`line[stroke="${MODELLING_CHART_GRID_COLOR}"]`)).not.toBeNull()
    expect(screen.getByTestId("shap-beeswarm-colour-bar").getAttribute("fill")).toMatch(/^url\(#/)
  })

  it("leaves the legend note out when every dot has a value rank", () => {
    render(
      <ShapBeeswarm
        features={[{ ...AGE, values: [22, 45, 50, 60], value_ranks: [0, 0.33, 0.67, 1] }]}
        width={WIDTH}
      />,
    )

    expect(screen.queryByText(/No value order/)).not.toBeInTheDocument()
  })

  it("refuses a value colour token that is not a hex colour", () => {
    expect(parseHexColor(" #abc ")).toEqual([170, 187, 204])
    expect(() => parseHexColor("var(--other)")).toThrow(/hex colour/)
    document.documentElement.style.setProperty("--chart-value-low", "")
    expect(() => render(<ShapBeeswarm features={[AGE]} width={WIDTH} />)).toThrow(/hex colour/)
  })

  it("stacks dense dots within the row and leaves sparse dots on the row line", () => {
    const stacking = { bucketWidth: 4, laneStep: 3, halfHeight: 14 }
    const dense = beeswarmOffsets(Array(2_000).fill(100), stacking)
    expect(Math.max(...dense.map(Math.abs))).toBeCloseTo(14)
    expect(beeswarmOffsets([0, 50, 100, 150], stacking)).toEqual([0, 0, 0, 0])

    const flat: TrainShapBeeswarmFeature = {
      feature: "flat",
      kind: "numeric",
      shap_values: Array(2_000).fill(0.1),
      values: Array(2_000).fill(1),
      value_ranks: Array(2_000).fill(null),
    }
    const ys = Array.from(layoutShapBeeswarm([flat], WIDTH).y)
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThanOrEqual(
      2 * SHAP_BEESWARM_GEOMETRY.rowHalfHeight + 1e-3,
    )
  })

  it("picks the nearest dot within the pick radius and nothing farther away", () => {
    const layout = layoutShapBeeswarm([AGE, REGION], WIDTH)

    expect(nearestDot(layout, layout.x[0] + 2, layout.y[0] + 1)).toBe(0)
    expect(nearestDot(layout, layout.x[5], layout.y[5])).toBe(5)
    expect(nearestDot(layout, layout.x[0], layout.y[0] + 40)).toBeNull()
  })

  it("names the dot under the pointer: feature, value and SHAP value", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)
    const layout = layoutShapBeeswarm([AGE, REGION], WIDTH)
    const chart = screen.getByTestId("shap-beeswarm")

    fireEvent.mouseMove(chart, { clientX: layout.x[0], clientY: layout.y[0] })
    expect(within(detail()).getByText("driver_age")).toBeInTheDocument()
    expect(detail()).toHaveTextContent("Value: 22")
    expect(detail()).toHaveTextContent(`SHAP: ${formatChartNumber(0.3)}`)
    expect(detail()).toHaveTextContent("Colour: value rank 0.00 of low 0 to high 1")
    expect(screen.getByTestId("shap-beeswarm-pointed")).toHaveAttribute("cx", String(layout.x[0]))

    fireEvent.mouseMove(chart, { clientX: layout.x[2], clientY: layout.y[2] })
    expect(detail()).toHaveTextContent("Value: (missing)")
    expect(detail()).toHaveTextContent("Colour: no value order (missing value)")

    fireEvent.mouseMove(chart, { clientX: layout.x[4], clientY: layout.y[4] })
    expect(detail()).toHaveTextContent("Value: north")
    expect(detail()).toHaveTextContent("Colour: no value order (categorical level)")
  })

  it("gives each feature row one tab stop whose focus states its rows and SHAP range", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)

    const [ageRow] = screen.getAllByTestId("shap-beeswarm-feature")
    expect(ageRow).toHaveAttribute("tabindex", "0")
    expect(ageRow).toHaveAttribute("role", "button")

    fireEvent.focus(ageRow)
    expect(ageRow).toHaveAttribute("aria-pressed", "true")
    expect(detail()).toHaveTextContent("Rows: 4")
    expect(detail()).toHaveTextContent(`SHAP: ${formatChartNumber(-0.2)} to ${formatChartNumber(0.3)}`)
  })

  it("lists each feature's rows and minimum, median and maximum SHAP in the values table", () => {
    render(<ShapBeeswarm features={[AGE, REGION]} width={WIDTH} />)

    const table = screen.getByRole("table", { name: "SHAP beeswarm values" })
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows.map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([
      ["driver_age", "4", formatChartNumber(-0.2), formatChartNumber(-0.025), formatChartNumber(0.3)],
      ["region", "4", formatChartNumber(-0.05), formatChartNumber(0.01), formatChartNumber(0.1)],
    ])
  })
})
