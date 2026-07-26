import { describe, it, expect, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { FeatureImportance } from "../FeatureImportance"
import type { TrainResult } from "../../../stores/useNodeResultsStore"

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return {
    status: "complete",
    metrics: {},
    feature_importance: [],
    model_path: "/tmp/model.cbm",
    train_rows: 1000,
    validation_rows: 200,
    ...overrides,
  }
}

describe("FeatureImportance", () => {
  afterEach(cleanup)
  it("returns null with empty feature_importance", () => {
    const { container } = render(
      <FeatureImportance trainResult={makeTrainResult()} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("renders feature names", () => {
    const trainResult = makeTrainResult({
      feature_importance: [
        { feature: "age", importance: 10 },
        { feature: "income", importance: 8 },
      ],
    })
    render(<FeatureImportance trainResult={trainResult} />)
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.getByText("income")).toBeInTheDocument()
  })

  it("shows prediction tab by default", () => {
    const trainResult = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 5 }],
      feature_importance_loss: [{ feature: "x", importance: 3 }],
    })
    render(<FeatureImportance trainResult={trainResult} />)
    const predictionBtn = screen.getByText("Prediction")
    // Active tab has accent background
    expect(predictionBtn.style.background).toContain("accent")
  })

  it("shows loss tab only if feature_importance_loss present", () => {
    const withoutLoss = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 5 }],
    })
    const { rerender } = render(<FeatureImportance trainResult={withoutLoss} />)
    expect(screen.queryByText("Loss")).not.toBeInTheDocument()

    const withLoss = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 5 }],
      feature_importance_loss: [{ feature: "x", importance: 3 }],
    })
    rerender(<FeatureImportance trainResult={withLoss} />)
    expect(screen.getByText("Loss")).toBeInTheDocument()
  })

  it("click on tab switches display", () => {
    const trainResult = makeTrainResult({
      feature_importance: [{ feature: "pred_feat", importance: 10 }],
      feature_importance_loss: [{ feature: "loss_feat", importance: 7 }],
    })
    render(<FeatureImportance trainResult={trainResult} />)
    expect(screen.getByText("pred_feat")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Loss"))
    expect(screen.getByText("loss_feat")).toBeInTheDocument()
  })

  it("shows max 10 features", () => {
    const features = Array.from({ length: 15 }, (_, i) => ({
      feature: `feat_${i}`,
      importance: 15 - i,
    }))
    const trainResult = makeTrainResult({ feature_importance: features })
    render(<FeatureImportance trainResult={trainResult} />)
    expect(screen.getByText("feat_0")).toBeInTheDocument()
    expect(screen.getByText("feat_9")).toBeInTheDocument()
    expect(screen.queryByText("feat_10")).not.toBeInTheDocument()
  })
})
