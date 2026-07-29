import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup, within } from "@testing-library/react"
import { SummaryTab } from "../SummaryTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)

describe("SummaryTab", () => {
  it("renders model info: path, train rows, validation rows, features", () => {
    const result = makeTrainResult({
      model_path: "/models/test.cbm",
      train_rows: 8000,
      validation_rows: 2000,
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("/models/test.cbm")).toBeInTheDocument()
    expect(screen.getByText("8,000")).toBeInTheDocument()
    expect(screen.getByText("2,000")).toBeInTheDocument()
  })

  it("renders metrics values to 4 decimal places", () => {
    const result = makeTrainResult({
      metrics: { gini: 0.4567, rmse: 0.1234 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("0.4567")).toBeInTheDocument()
    expect(screen.getByText("0.1234")).toBeInTheDocument()
  })

  it("shows metric names as labels", () => {
    const result = makeTrainResult({ metrics: { gini: 0.5 } })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("gini")).toBeInTheDocument()
  })

  it("does not show metrics section when metrics are empty", () => {
    const result = makeTrainResult({ metrics: {} })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.queryByText("Metrics (Validation)")).not.toBeInTheDocument()
  })

  it("shows warning banner when result has warning", () => {
    const result = makeTrainResult({ warning: "Downsampled to 50k rows" })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Downsampled to 50k rows")).toBeInTheDocument()
  })

  it("shows diagnostic errors from failed optional diagnostics", () => {
    const result = makeTrainResult({
      diagnostics_errors: [
        { diagnostic: "glm_coefficients", error: "Singular matrix", error_type: "LinAlgError" },
        { diagnostic: "shap", error: "No background rows", error_type: "ValueError" },
        { diagnostic: "pdp", error: "All PDP features failed", error_type: "RuntimeError" },
      ],
    })

    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)

    const notice = screen.getByRole("alert", { name: "Diagnostic issues" })
    expect(within(notice).getByText("GLM coefficients")).toBeInTheDocument()
    expect(within(notice).getByText("SHAP")).toBeInTheDocument()
    expect(within(notice).getByText("PDP")).toBeInTheDocument()
    expect(within(notice).getByText("Singular matrix")).toBeInTheDocument()
    expect(within(notice).getByText("No background rows")).toBeInTheDocument()
    expect(within(notice).getByText("All PDP features failed")).toBeInTheDocument()
    expect(within(notice).getByText("LinAlgError")).toBeInTheDocument()
  })

  it("does not show warning banner when warning is null", () => {
    const result = makeTrainResult({ warning: null })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.queryByText(/Downsampled/)).not.toBeInTheDocument()
  })

  it("shows holdout rows when present", () => {
    const result = makeTrainResult({ holdout_rows: 500, validation_rows: 2000 })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Holdout rows")).toBeInTheDocument()
    expect(screen.getByText("500")).toBeInTheDocument()
  })

  it("shows holdout metrics when available and diagnostics on validation", () => {
    const result = makeTrainResult({
      diagnostics_set: "validation",
      holdout_metrics: { gini: 0.42 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Metrics (Holdout)")).toBeInTheDocument()
    expect(screen.getByText("0.4200")).toBeInTheDocument()
  })

  it("does not show separate holdout metrics section when diagnostics_set is holdout", () => {
    // When diagnostics_set is "holdout", primary metrics already show holdout data,
    // so the separate holdout_metrics block is hidden. But "Metrics (Holdout)" appears
    // as the primary metrics label. We verify there is only ONE "Metrics (Holdout)".
    const result = makeTrainResult({
      diagnostics_set: "holdout",
      metrics: { gini: 0.45 },
      holdout_metrics: { gini: 0.42 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    const holdoutLabels = screen.getAllByText("Metrics (Holdout)")
    // Only one — the primary metrics section; not a separate holdout section
    expect(holdoutLabels).toHaveLength(1)
  })

  it("shows best iteration when present", () => {
    const result = makeTrainResult({ best_iteration: 750 })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Best iteration")).toBeInTheDocument()
    expect(screen.getByText("750")).toBeInTheDocument()
  })

  it("shows GLM fit statistics when present", () => {
    const result = makeTrainResult({
      glm_fit_statistics: { deviance: 1234.56, aic: 5678.9 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Fit Statistics")).toBeInTheDocument()
    expect(screen.getByText("1234.5600")).toBeInTheDocument()
  })

  it("shows diagnostics set label correctly", () => {
    const result = makeTrainResult({ diagnostics_set: "holdout" })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Holdout")).toBeInTheDocument()
  })

  it("shows bounded aggregate and ordered per-fold cross-validation results", () => {
    const result = makeTrainResult({
      cross_validation: {
        schema_version: 1,
        strategy: "group",
        fold_count: 2,
        fit_count: 3,
        folds: [
          {
            schema_version: 1,
            fold_index: 0,
            train_rows: 8,
            validation_rows: 2,
            metrics: { rmse: 1 },
          },
          {
            schema_version: 1,
            fold_index: 1,
            train_rows: 8,
            validation_rows: 2,
            metrics: { rmse: 3 },
          },
        ],
        metrics: {
          rmse: {
            mean: 2,
            population_std: 1,
            min: 1,
            max: 3,
            fold_count: 2,
            total_validation_rows: 4,
          },
        },
        plan_sha256: "a".repeat(64),
        results_sha256: "b".repeat(64),
        fold_plan_path: "outputs/model.cv-fold-plan.json",
        fold_results_path: "outputs/model.cv-fold-results.json",
        report_path: "outputs/model.cv-report.json",
      },
    })

    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)

    expect(screen.getByText("Cross-validation")).toBeInTheDocument()
    expect(screen.getByText("group · 2 folds · 3 fits")).toBeInTheDocument()
    const aggregate = screen.getByRole("table", { name: "Cross-validation aggregate metrics" })
    expect(within(aggregate).getByText("rmse")).toBeInTheDocument()
    expect(within(aggregate).getByText("2.0000")).toBeInTheDocument()
    const folds = screen.getByRole("table", { name: "Cross-validation fold metrics" })
    expect(within(folds).getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([
      "1821.0000",
      "2823.0000",
    ])
  })
})
