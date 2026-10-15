import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"

import { makeTrainResult, makeTuningReport } from "../../../test-utils/factories"
import { SummaryTab } from "../SummaryTab"

afterEach(cleanup)

describe("SummaryTab", () => {
  it("offers no export action or model path: those live in the Export pane", () => {
    render(<SummaryTab result={makeTrainResult({ model_path: "/models/test.cbm" })} />)

    expect(screen.queryByText("Experiment tracking")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /log run to mlflow/i })).not.toBeInTheDocument()
    expect(screen.queryByText("Model path")).not.toBeInTheDocument()
    expect(screen.queryByText("/models/test.cbm")).not.toBeInTheDocument()
  })

  it("renders canonical model and evaluation information", () => {
    const result = makeTrainResult({
      development_rows: 8000,
      final_test_rows: 2000,
    })

    render(<SummaryTab result={result} />)

    expect(screen.getAllByText("Training rows").length).toBeGreaterThan(0)
    expect(screen.getByText("8,000")).toBeInTheDocument()
    expect(screen.getByText("Test rows")).toBeInTheDocument()
    expect(screen.getAllByText("2,000").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Holdout validation").length).toBeGreaterThan(0)
  })

  it("labels a saved holdout fit as validation diagnostics", () => {
    const base = makeTrainResult()
    const result = makeTrainResult({
      diagnostics_set: "validation",
      diagnostic_metrics: { rmse: 0.12 },
      final_test_rows: 0,
      final_test_metrics: {},
      evaluation: {
        ...base.evaluation!,
        fit_count: 1,
        final_test_rows: 0,
        refit_on_development: false,
      },
    })
    render(<SummaryTab result={result} />)
    expect(screen.getByRole("region", { name: "Validation diagnostics" })).toBeInTheDocument()
    expect(screen.getAllByText("6,000").length).toBeGreaterThan(0)
  })

  it("shows test metrics before training diagnostics", () => {
    const result = makeTrainResult({
      final_test_metrics: { gini: 0.4567 },
      diagnostic_metrics: { rmse: 0.1234 },
      diagnostics_set: "development",
    })

    render(<SummaryTab result={result} />)

    const finalMetrics = screen.getByRole("region", { name: "Test metrics" })
    const diagnostics = screen.getByRole("region", { name: "Training diagnostics" })
    const finalLabel = within(finalMetrics).getByText("Test metrics")
    const diagnosticLabel = within(diagnostics).getByText("Training diagnostics")
    expect(finalLabel.compareDocumentPosition(diagnosticLabel)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(within(finalMetrics).getByText("0.4567")).toBeInTheDocument()
    expect(within(finalMetrics).queryByText("0.1234")).not.toBeInTheDocument()
    expect(within(diagnostics).getByText("0.1234")).toBeInTheDocument()
    expect(within(diagnostics).queryByText("0.4567")).not.toBeInTheDocument()
  })

  it("leads with the validation metrics when a holdout-validated refit has in-sample diagnostics", () => {
    const base = makeTrainResult()
    const result = makeTrainResult({
      diagnostics_set: "development",
      diagnostic_metrics: { gini: 0.8963, rmse: 0.0586 },
      final_test_rows: 0,
      final_test_metrics: {},
      evaluation: { ...base.evaluation!, final_test_rows: 0 },
    })

    render(<SummaryTab result={result} />)

    // The metric cards come first, in reading order.
    const headings = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)
    expect(headings.slice(0, 2)).toEqual(["Validation, 2,000 rows", "Training diagnostics"])
    const validation = screen.getByRole("region", { name: "Validation, 2,000 rows" })
    expect(within(validation).getByText("0.4500")).toBeInTheDocument()
    expect(within(validation).getByText("0.1200")).toBeInTheDocument()
    expect(within(validation).queryByText("0.8963")).not.toBeInTheDocument()
  })

  it("names the fold count of cross-validated metrics that lead", () => {
    const base = makeTrainResult()
    const summary = (mean: number) => ({ mean, stddev: 0.01, min: mean, max: mean, fit_count: 5, validation_rows: 8000 })
    const result = makeTrainResult({
      diagnostics_set: "development",
      final_test_rows: 0,
      final_test_metrics: {},
      evaluation: {
        ...base.evaluation!,
        validation_method: "cross_validation",
        validation_fit_count: 5,
        final_test_rows: 0,
        selection_metrics: { gini: summary(0.41), rmse: summary(0.15) },
      },
    })

    render(<SummaryTab result={result} />)

    const validation = screen.getByRole("region", { name: "Validation (5-fold mean), 8,000 rows" })
    expect(within(validation).getByText("0.4100")).toBeInTheDocument()
  })

  it("keeps test metrics first, and no validation card, when a test set was reserved", () => {
    render(<SummaryTab result={makeTrainResult({ diagnostics_set: "final_test" })} />)

    const headings = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)
    expect(headings.slice(0, 2)).toEqual(["Test metrics", "Test diagnostics"])
    expect(screen.queryByRole("region", { name: /^Validation/ })).toBeNull()
  })

  it("does not imply test performance when none was reserved", () => {
    const result = makeTrainResult({
      final_test_metrics: {},
      final_test_rows: 0,
      diagnostic_metrics: { rmse: 0.1234 },
      diagnostics_set: "development",
    })

    render(<SummaryTab result={result} />)

    expect(screen.queryByText("Test metrics")).not.toBeInTheDocument()
    expect(screen.getByText("Training diagnostics")).toBeInTheDocument()
    expect(screen.queryByText("Test rows")).not.toBeInTheDocument()
  })

  it("keeps a zero-valued cross-validated penalty and its fold settings available", () => {
    const result = makeTrainResult({
      glm_regularization: {
        penalty: "lasso",
        mode: "cross_validation",
        alpha: 0,
        l1_ratio: 1,
        n_nonzero: 0,
        cv_folds: 5,
        cv_selection: "1se",
        cv_seed: 42,
      },
    })

    render(<SummaryTab result={result} />)

    const regularization = screen.getByRole("region", { name: "Regularization" })
    expect(within(regularization).getByText("0.00000")).toBeInTheDocument()
    expect(within(regularization).getByText("Non-zero coefficients")).toBeInTheDocument()
    expect(within(regularization).getByText("0")).toBeInTheDocument()
    expect(within(regularization).getByText("5-fold cross-validation")).toBeInTheDocument()
    expect(within(regularization).getByText("One standard error")).toBeInTheDocument()
    expect(within(regularization).getByText("42")).toBeInTheDocument()
    expect(within(regularization).queryByText("L1 ratio")).not.toBeInTheDocument()
  })

  it("shows warning and optional diagnostic failures", () => {
    const result = makeTrainResult({
      warning: "Downsampled to 50k rows",
      diagnostics_errors: [
        {
          diagnostic: "glm_coefficients",
          error: "Singular matrix",
          error_type: "LinAlgError",
        },
      ],
    })

    render(<SummaryTab result={result} />)

    expect(screen.getByText("Downsampled to 50k rows")).toBeInTheDocument()
    const notice = screen.getByRole("alert", { name: "Diagnostic issues" })
    expect(within(notice).getByText("GLM coefficients")).toBeInTheDocument()
    expect(within(notice).getByText("Singular matrix")).toBeInTheDocument()
    expect(within(notice).getByText("LinAlgError")).toBeInTheDocument()
  })

  it("shows best iteration and GLM fit statistics", () => {
    const result = makeTrainResult({
      best_iteration: 750,
      glm_fit_statistics: { deviance: 1234.56 },
    })

    render(<SummaryTab result={result} />)

    expect(screen.getByText("Best iteration")).toBeInTheDocument()
    expect(screen.getByText("750")).toBeInTheDocument()
    expect(screen.getByText("Fit statistics")).toBeInTheDocument()
    expect(screen.getByText("1234.5600")).toBeInTheDocument()
  })

  it("shows aggregate and ordered selection-fit results", () => {
    const result = makeTrainResult({
      evaluation: {
        ...makeTrainResult().evaluation!,
        validation_method: "cross_validation",
        validation_fit_count: 2,
        fit_count: 3,
        selection_fits: [
          {
            schema_version: 1,
            fit_index: 0,
            train_rows: 8,
            validation_rows: 2,
            metrics: { rmse: 1 },
            best_iteration: 10,
          },
          {
            schema_version: 1,
            fit_index: 1,
            train_rows: 8,
            validation_rows: 2,
            metrics: { rmse: 3 },
            best_iteration: 12,
          },
        ],
        selection_metrics: {
          rmse: {
            mean: 2,
            stddev: 1,
            min: 1,
            max: 3,
            fit_count: 2,
            validation_rows: 4,
          },
        },
      },
    })

    render(<SummaryTab result={result} />)

    expect(screen.getAllByText("2-fold cross-validation").length).toBeGreaterThan(0)
    const aggregate = screen.getByRole("table", { name: "Selection aggregate metrics" })
    expect(within(aggregate).getByText("rmse")).toBeInTheDocument()
    expect(within(aggregate).getByText("2.0000")).toBeInTheDocument()
    const fits = screen.getByRole("table", { name: "Selection fit metrics" })
    expect(within(fits).getAllByRole("row").slice(1).map(row => row.textContent)).toEqual([
      "1821.0000",
      "2823.0000",
    ])
  })

  it("shows bounded tuning detail and applies exact final parameters on request", () => {
    const onUseBestParameters = vi.fn()
    const tuning = makeTuningReport()
    const result = makeTrainResult({ tuning })

    render(
      <SummaryTab
        result={result}
        onUseBestParameters={onUseBestParameters}
        elapsedSeconds={12.5}
      />,
    )

    expect(screen.getByText(/Winning gini: 0.5100/)).toBeInTheDocument()
    expect(screen.getByText(/improvement 0.1100/)).toBeInTheDocument()
    expect(screen.getByText(/13 total fits/)).toBeInTheDocument()
    expect(screen.getByText(/12.5s elapsed/)).toBeInTheDocument()
    expect(screen.getByText("Best sampled parameters")).toBeInTheDocument()
    expect(screen.getByText("Final parameters")).toBeInTheDocument()
    const table = screen.getByRole("table", { name: "Top tuning trials" })
    expect(within(table).getAllByRole("row")).toHaveLength(11)
    expect(within(table).queryByText("Elapsed")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", {
      name: "Use best as fixed parameters",
    }))
    expect(onUseBestParameters).toHaveBeenCalledWith(tuning.final_params)
  })
})
