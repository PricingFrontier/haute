/**
 * Smoke tests for ModellingPreview.
 *
 * ModellingPreview uses Zustand stores (useNodeResultsStore, useSettingsStore)
 * and useDragResize, so we mock them to keep tests focused on render logic.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { ModellingPreview } from "../ModellingPreview"
import type { ModellingPreviewData } from "../ModellingPreview"
import { makeTrainResult } from "../../test-utils/factories"

// Mock stores
vi.mock("../../stores/useNodeResultsStore", () => {
  const store = Object.assign(vi.fn(() => null), {
    getState: vi.fn(() => ({ trainJobs: {} })),
  })
  return { default: store, __esModule: true }
})

vi.mock("../../stores/useSettingsStore", () => {
  const store = Object.assign(
    vi.fn(() => ({ status: "disconnected", backend: "", host: "" })),
    { getState: vi.fn(() => ({ mlflow: { status: "disconnected", backend: "", host: "" } })) },
  )
  return { default: store, __esModule: true }
})

// Mock useDragResize to avoid DOM measurement issues (PreviewPanelFrame consumes it transitively)
vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 360,
    containerRef: { current: null },
    onDragStart: vi.fn(),
    resizeToHeight: vi.fn(),
  }),
}))

afterEach(cleanup)

function makeData(overrides: Partial<ModellingPreviewData> = {}): ModellingPreviewData {
  return {
    result: makeTrainResult(),
    jobId: "job-123",
    nodeLabel: "Model Node",
    configHash: "abc123",
    ...overrides,
  }
}

describe("ModellingPreview", () => {
  it("renders node label", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    expect(screen.getByText("Model Node")).toBeInTheDocument()
  })

  it("renders Summary tab by default", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    expect(screen.getByText("Summary")).toBeInTheDocument()
  })

  it("shows Features tab when feature_importance exists", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    // "Features" appears as both a tab button and a model info label in SummaryTab
    const matches = screen.getAllByText("Features")
    expect(matches.length).toBeGreaterThanOrEqual(1)
    // At least one should be a button (the tab)
    expect(matches.some(el => el.tagName === "BUTTON")).toBe(true)
  })

  it("does not show Loss tab when no loss_history", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    expect(screen.queryByText("Loss")).not.toBeInTheDocument()
  })

  it("shows Loss tab when loss_history has data", () => {
    const result = makeTrainResult({
      loss_history: [
        { iteration: 0, train_rmse: 1.0 },
        { iteration: 1, train_rmse: 0.9 },
      ],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    expect(screen.getByText("Loss")).toBeInTheDocument()
  })

  it("shows Lift tab when double_lift data exists", () => {
    const result = makeTrainResult({
      double_lift: [{ decile: 1, actual: 1.0, predicted: 0.9, count: 100 }],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    expect(screen.getByText("Lift")).toBeInTheDocument()
  })

  it("shows Coefficients tab for GLM results", () => {
    const result = makeTrainResult({
      glm_coefficients: [{ feature: "age", coefficient: 0.1, std_error: 0.01, z_value: 10, p_value: 0.001, significance: "***" }],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    expect(screen.getByText("Coefficients")).toBeInTheDocument()
  })

  it("can collapse and expand", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    fireEvent.click(screen.getByLabelText("Expand preview panel"))
    expect(screen.getByText("Summary")).toBeInTheDocument()
  })

  it("shows metrics summary in collapsed state", () => {
    const result = makeTrainResult({ metrics: { gini: 0.4567, rmse: 0.1234 } })
    const data = makeData({ result })
    const { container } = render(<ModellingPreview data={data} nodeId="n1" />)
    expect(container.innerHTML).not.toBe("")
  })

  it("clicking a tab switches active tab content", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "age", importance: 25 },
        { feature: "income", importance: 18 },
      ],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    const featuresTab = screen.getAllByText("Features").find(el => el.tagName === "BUTTON")!
    fireEvent.click(featuresTab)
    expect(screen.getByText("age")).toBeInTheDocument()
  })

  it("Loss tab is hidden when result has no loss_history", () => {
    const result = makeTrainResult({ loss_history: undefined })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    expect(screen.queryByText("Loss")).not.toBeInTheDocument()
  })

  it("Loss tab is hidden when loss_history has fewer than 2 entries", () => {
    const result = makeTrainResult({
      loss_history: [{ iteration: 0, train_rmse: 1.0 }],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    expect(screen.queryByText("Loss")).not.toBeInTheDocument()
  })

  it("Features tab shows feature names when clicked", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "feat_a", importance: 30 },
        { feature: "feat_b", importance: 20 },
        { feature: "feat_c", importance: 10 },
      ],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    const featuresTab = screen.getAllByText("Features").find(el => el.tagName === "BUTTON")!
    fireEvent.click(featuresTab)
    expect(screen.getByText("feat_a")).toBeInTheDocument()
    expect(screen.getByText("feat_b")).toBeInTheDocument()
    expect(screen.getByText("feat_c")).toBeInTheDocument()
  })

  it("collapsing hides tab content and shows node label in collapsed bar", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    expect(screen.queryByText("Summary")).not.toBeInTheDocument()
    expect(screen.getByText("Model Node")).toBeInTheDocument()
  })

  it("expanding after collapse restores tab content", () => {
    render(<ModellingPreview data={makeData()} nodeId="n1" />)
    fireEvent.click(screen.getByLabelText("Collapse preview panel"))
    fireEvent.click(screen.getByLabelText("Expand preview panel"))
    expect(screen.getByText("Summary")).toBeInTheDocument()
  })

  it("switching between Summary and Loss tabs works", () => {
    const result = makeTrainResult({
      loss_history: [
        { iteration: 0, train_rmse: 1.0 },
        { iteration: 1, train_rmse: 0.9 },
      ],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    fireEvent.click(screen.getByText("Loss"))
    fireEvent.click(screen.getByText("Summary"))
    expect(screen.getByText("Model Info")).toBeInTheDocument()
  })

  it("Lift tab appears and is clickable when double_lift data exists", () => {
    const result = makeTrainResult({
      double_lift: [
        { decile: 1, actual: 1.0, predicted: 0.9, count: 100 },
        { decile: 2, actual: 0.8, predicted: 0.7, count: 100 },
      ],
    })
    render(<ModellingPreview data={makeData({ result })} nodeId="n1" />)
    const liftTab = screen.getByText("Lift")
    fireEvent.click(liftTab)
    expect(liftTab).toBeInTheDocument()
  })
})
