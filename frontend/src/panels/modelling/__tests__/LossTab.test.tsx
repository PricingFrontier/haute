import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LossTab } from "../LossTab"
import { makeTrainResult } from "../../../test-utils/factories"

const baseResult = makeTrainResult({
  feature_importance: [],
  model_path: "",
  development_rows: 10,
  final_test_rows: 5,
})

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

describe("LossTab validation fit", () => {
  const holdoutFit = {
    schema_version: 1 as const,
    fit_index: 0,
    train_rows: 80000,
    validation_rows: 20000,
    metrics: { rmse: 0.12 },
    // Zero-based: the row whose iteration is 5.
    best_iteration: 4,
  }
  const holdoutResult = {
    ...baseResult,
    development_rows: 100000,
    final_test_rows: 0,
    final_tree_count: 6,
    best_iteration: null,
    loss_history: [
      { iteration: 1, train_rmse: 1.1 },
      { iteration: 2, train_rmse: 0.9 },
    ],
    validation_loss_history: [
      { iteration: 1, train_rmse: 1.2, eval_rmse: 1.4 },
      { iteration: 3, train_rmse: 0.9, eval_rmse: 1.1 },
      { iteration: 5, train_rmse: 0.8, eval_rmse: 1.05 },
      { iteration: 9, train_rmse: 0.7, eval_rmse: 1.1 },
    ],
    validation_loss_history_truncated: true,
    evaluation: { ...baseResult.evaluation!, selection_fits: [holdoutFit] },
  }

  it("draws the holdout validation fit, its best iteration and the refit after it", () => {
    render(<LossTab width={600} result={holdoutResult} />)

    expect(
      screen.getByText(
        "Validation fit: 80,000 training, 20,000 validation rows. "
          + "The final model was refit on all 100,000 development rows for 6 iterations.",
      ),
    ).toBeInTheDocument()
    expect(screen.getByText("Thinned to 4 of 9 iterations.")).toBeInTheDocument()
    expect(screen.getAllByText("Eval").length).toBeGreaterThan(0)
    // The marker sits on the best row (iteration 5), not at an index clamped to the last row.
    const marker = document.querySelector('[data-marker="Best iteration (4)"]')
    expect(marker).not.toBeNull()
    const trainPath = document.querySelector("svg path")?.getAttribute("d") ?? ""
    const lastX = Number(trainPath.trim().split(/[ML]/).filter(Boolean).at(-1)?.split(",")[0])
    expect(Number(marker!.getAttribute("x1"))).toBeLessThan(lastX)
  })

  it("names the validation fit when it is the final model", () => {
    render(
      <LossTab
        width={600}
        result={{
          ...holdoutResult,
          best_iteration: 1,
          loss_history: holdoutResult.validation_loss_history,
          loss_history_truncated: false,
          validation_loss_history: [],
          validation_loss_history_truncated: false,
          evaluation: { ...holdoutResult.evaluation, refit_on_development: false },
        }}
      />,
    )

    expect(
      screen.getByText("Validation fit: 80,000 training, 20,000 validation rows."),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Thinned/)).toBeNull()
    expect(screen.queryByText(/refit/)).toBeNull()
  })

  it("labels a narrow loss range with distinct values", () => {
    render(
      <LossTab
        width={600}
        result={{
          ...baseResult,
          loss_history: [
            { iteration: 1, train_tweedie: 107.3 },
            { iteration: 2, train_tweedie: 107.1 },
            { iteration: 3, train_tweedie: 107.0 },
          ],
        }}
      />,
    )

    const labels = Array.from(document.querySelectorAll("svg text"))
      .map((node) => node.textContent ?? "")
      .filter((text) => text.startsWith("107"))
    expect(labels.length).toBeGreaterThan(2)
    expect(new Set(labels).size).toBe(labels.length)
  })
})
