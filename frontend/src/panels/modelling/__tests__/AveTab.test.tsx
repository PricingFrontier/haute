import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { AveTab } from "../AveTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)
const bins = [
  { label: "A very long categorical value", exposure: 0, avg_actual: 1, avg_predicted: 1.2 },
  { label: "Another category", exposure: 200, avg_actual: 2, avg_predicted: 1.8 },
]
describe("AvE charts", () => {
  it("separates exposure and leaves categorical outcomes unconnected", () => {
    render(
      <AveTab
        result={makeTrainResult({
          ave_per_feature: [{ feature: "region", type: "categorical", bins }],
        })}
      />,
    )
    const chart = screen.getByRole("img", { name: "Actual vs expected for region" })
    expect(chart.querySelector('[data-series="actual-line"]')).toBeNull()
    expect(screen.getByRole("img", { name: "Exposure for region" })).toBeInTheDocument()
    fireEvent.click(screen.getByText("View bin values"))
    expect(screen.getByRole("table", { name: "AvE bin values" })).toHaveTextContent(
      "A very long categorical value",
    )
    expect(screen.getByRole("table", { name: "AvE bin values" })).toHaveTextContent("200")
  })
  it("connects numeric bins and makes exact bin values available on focus", () => {
    render(
      <AveTab
        result={makeTrainResult({ ave_per_feature: [{ feature: "age", type: "numeric", bins }] })}
      />,
    )
    const chart = screen.getByRole("img", { name: "Actual vs expected for age" })
    expect(chart.querySelector('[data-series="actual-line"]')).toBeInTheDocument()
    fireEvent.focus(screen.getByRole("button", { name: /A very long categorical value.*Actual/ }))
    expect(screen.getByRole("status")).toHaveTextContent("Exposure: 0")
  })
})
