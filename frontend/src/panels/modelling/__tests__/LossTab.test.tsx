import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LossTab } from "../LossTab"
import type { TrainResult } from "../../../stores/useNodeResultsStore"

const baseResult: TrainResult = {
  status: "completed",
  metrics: {},
  feature_importance: [],
  model_path: "",
  train_rows: 10,
  test_rows: 5,
}

afterEach(cleanup)

describe("LossTab", () => {
  it("renders a valid loss curve without NaN or Infinity path coordinates", () => {
    render(
      <LossTab
        result={{
          ...baseResult,
          best_iteration: 1,
          loss_history: [
            { iteration: 0, train_rmse: 1.2, eval_rmse: 1.4 },
            { iteration: 1, train_rmse: 0.9, eval_rmse: Number.NaN },
            { iteration: 2, train_rmse: 0.7, eval_rmse: Number.POSITIVE_INFINITY },
          ],
        }}
      />,
    )

    const pathData = Array.from(document.querySelectorAll("svg path")).map((path) => path.getAttribute("d") ?? "")
    expect(pathData.length).toBeGreaterThan(0)
    expect(pathData.join(" ")).not.toMatch(/NaN|Infinity/)
  })

  it("shows an empty state when the train loss history has no finite numeric series", () => {
    render(
      <LossTab
        result={{
          ...baseResult,
          loss_history: [
            { iteration: 0, train_rmse: Number.NaN },
            { iteration: 1, train_rmse: Number.POSITIVE_INFINITY },
          ],
        }}
      />,
    )

    expect(screen.getByText("No valid loss history data available")).toBeInTheDocument()
    expect(document.querySelector("svg")).not.toBeInTheDocument()
  })
})
