import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LossHistoryChart, LossTab } from "../LossTab"
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


it("shows every bag at its own rounds with split and visibility controls", () => {
  const history = [
    { iteration: 1, train_deviance: 6, eval_deviance: 7, bag_1_iteration: 1, bag_1_train_deviance: 8, bag_1_eval_deviance: 9, bag_2_iteration: 1, bag_2_train_deviance: 4, bag_2_eval_deviance: 5 },
    { iteration: 3, train_deviance: 4, eval_deviance: 5, bag_1_iteration: 2, bag_1_train_deviance: 6, bag_1_eval_deviance: 7, bag_2_iteration: 3, bag_2_train_deviance: 2, bag_2_eval_deviance: 3 },
  ]
  render(<LossTab width={600} result={{ ...baseResult, loss_history: history }} />)
  expect(screen.getByRole("checkbox", { name: "Bag 1" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "Bag 2" })).toBeChecked()
  const first = document.querySelector('path[data-series="Bag 1"]')!
  const second = document.querySelector('path[data-series="Bag 2"]')!
  expect(first.getAttribute("d")).toMatch(/L322\.0,/)
  expect(second.getAttribute("d")).toMatch(/L584\.0,/)
  expect(document.querySelector('path[data-series="Mean of bags"]')).not.toBeNull()
  fireEvent.click(screen.getByRole("checkbox", { name: "Bag 1" }))
  expect(document.querySelector('path[data-series="Bag 1"]')).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Training" }))
  expect(screen.getByRole("button", { name: "Training" })).toHaveAttribute("aria-pressed", "true")
  expect(document.querySelector('path[data-series="Bag 2"]')).not.toBeNull()
  cleanup()
  render(<LossHistoryChart width={600} height={280} fit={{ history, thinned: false, bestIteration: null, intro: null, live: true }} />)
  expect(screen.queryByRole("checkbox", { name: "Mean of bags" })).toBeNull()
  expect(screen.getByRole("checkbox", { name: "Bag 1" })).toBeChecked()
})
