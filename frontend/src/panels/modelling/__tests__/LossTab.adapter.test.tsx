import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LossTab } from "../LossTab"
import { makeTrainResult } from "../../../test-utils/factories"

// LossTab adapts the loss history onto the shared IterationLinesChart; these
// pin what the adapter hands over (the LossTab/LossChart suites stay unchanged).
const baseResult = makeTrainResult({
  feature_importance: [],
  model_path: "",
  development_rows: 10,
  final_test_rows: 5,
})

afterEach(cleanup)

describe("LossTab adapter", () => {
  it("draws both the train and the eval curve, each named in the legend", () => {
    render(
      <LossTab
        width={600}
        result={{
          ...baseResult,
          best_iteration: 1,
          loss_history: [
            { iteration: 0, train_rmse: 1.2, eval_rmse: 1.4 },
            { iteration: 1, train_rmse: 0.9, eval_rmse: 1.1 },
            { iteration: 2, train_rmse: 0.7, eval_rmse: 1.0 },
          ],
        }}
      />,
    )
    expect(screen.getAllByText("Train").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Eval").length).toBeGreaterThan(0)
  })

  it("draws the train curve alone when there is no eval series", () => {
    render(
      <LossTab
        width={600}
        result={{
          ...baseResult,
          best_iteration: null,
          loss_history: [
            { iteration: 0, train_rmse: 1.2 },
            { iteration: 1, train_rmse: 0.9 },
          ],
        }}
      />,
    )
    expect(screen.getAllByText("Train").length).toBeGreaterThan(0)
    expect(screen.queryByText("Eval")).toBeNull()
  })
})
