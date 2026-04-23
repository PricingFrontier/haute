import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { SummaryTab } from "../SummaryTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)

describe("SummaryTab", () => {
  it("renders model info: path, train rows, validation rows, features", () => {
    const result = makeTrainResult({
      model_path: "/models/test.cbm",
      train_rows: 8000,
      test_rows: 2000,
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

  it("does not show warning banner when warning is null", () => {
    const result = makeTrainResult({ warning: null })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.queryByText(/Downsampled/)).not.toBeInTheDocument()
  })

  it("shows holdout rows when present", () => {
    const result = makeTrainResult({ holdout_rows: 500, test_rows: 2000 })
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
})
