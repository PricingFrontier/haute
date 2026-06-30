import { describe, it, expect, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { PdpTab } from "../PdpTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)

describe("PdpTab", () => {
  it("renders a numeric PDP chart with a single grid point", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "constant_age", importance: 10 }],
      pdp_data: [
        {
          feature: "constant_age",
          type: "numeric",
          grid: [{ value: 42, avg_prediction: 1.2 }],
        },
      ],
    })

    const { container } = render(<PdpTab result={result} />)

    expect(screen.getAllByText("constant_age").length).toBeGreaterThanOrEqual(1)
    expect(container.querySelector("svg")).toBeInTheDocument()
    expect(screen.queryByText("No PDP data for constant_age")).not.toBeInTheDocument()
  })

  it("shows per-feature PDP error details instead of an empty chart", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "rating_factor", importance: 10 }],
      pdp_data: [
        {
          feature: "rating_factor",
          type: "numeric",
          grid: [],
          error: "insufficient variation after filtering",
          error_type: "PDPFeatureError",
        },
      ],
    })

    render(<PdpTab result={result} />)

    expect(screen.getByText("PDP unavailable for rating_factor")).toBeInTheDocument()
    expect(screen.getByText("PDPFeatureError")).toBeInTheDocument()
    expect(screen.getByText("insufficient variation after filtering")).toBeInTheDocument()
    expect(screen.queryByText("No PDP data for rating_factor")).not.toBeInTheDocument()
  })

  it("shows the selected failed PDP feature reason after switching features", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "age", importance: 20 },
        { feature: "territory", importance: 10 },
      ],
      pdp_data: [
        {
          feature: "age",
          type: "numeric",
          grid: [
            { value: 30, avg_prediction: 1.2 },
            { value: 40, avg_prediction: 1.4 },
          ],
        },
        {
          feature: "territory",
          type: "categorical",
          grid: [],
          error: "too many distinct categories",
          error_type: "CardinalityError",
        },
      ],
    })

    render(<PdpTab result={result} />)

    fireEvent.click(screen.getByRole("button", { name: /territory/i }))

    expect(screen.getByText("PDP unavailable for territory")).toBeInTheDocument()
    expect(screen.getByText("CardinalityError")).toBeInTheDocument()
    expect(screen.getByText("too many distinct categories")).toBeInTheDocument()
  })
})
