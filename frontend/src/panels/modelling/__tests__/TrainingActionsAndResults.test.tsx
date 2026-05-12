import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { TrainingActionsAndResults } from "../TrainingActionsAndResults"
import type { TrainingActionsAndResultsProps } from "../TrainingActionsAndResults"
import { makeTrainResult, makeTrainEstimate } from "../../../test-utils/factories"
import { makeExecutionMetricsFixture } from "../../../testSupport/executionMetricsFixture"

afterEach(cleanup)

function makeProps(overrides: Partial<TrainingActionsAndResultsProps> = {}): TrainingActionsAndResultsProps {
  return {
    target: "loss_amount",
    training: false,
    trainProgress: null,
    trainResult: null,
    isStale: false,
    ramEstimate: null,
    ramEstimateLoading: false,
    rowLimit: null,
    onTrain: vi.fn(),
    ...overrides,
  }
}

describe("TrainingActionsAndResults", () => {
  it("renders Train Model button", () => {
    render(<TrainingActionsAndResults {...makeProps()} />)
    expect(screen.getByText("Train Model")).toBeInTheDocument()
  })

  it("train button is disabled when no target", () => {
    render(<TrainingActionsAndResults {...makeProps({ target: "" })} />)
    const btn = screen.getByText("Train Model").closest("button")!
    expect(btn).toBeDisabled()
  })

  it("clicking Train Model calls onTrain", () => {
    const onTrain = vi.fn()
    render(<TrainingActionsAndResults {...makeProps({ onTrain })} />)
    fireEvent.click(screen.getByText("Train Model"))
    expect(onTrain).toHaveBeenCalledTimes(1)
  })

  it("shows Training... when training is in progress", () => {
    render(<TrainingActionsAndResults {...makeProps({ training: true })} />)
    expect(screen.getByText("Training...")).toBeInTheDocument()
  })

  it("shows progress message when trainProgress has one", () => {
    render(<TrainingActionsAndResults {...makeProps({
      training: true,
      trainProgress: {
        status: "running",
        progress: 0.5,
        message: "Iteration 500/1000",
        iteration: 500,
        total_iterations: 1000,
        train_loss: {},
        elapsed_seconds: 30,
      },
    })} />)
    // Message appears in both the button label and the TrainingProgress panel
    const matches = screen.getAllByText("Iteration 500/1000")
    expect(matches.length).toBeGreaterThanOrEqual(1)
  })

  it("shows Preparing training data... when submitting", () => {
    render(<TrainingActionsAndResults {...makeProps({ submitting: true })} />)
    expect(screen.getByText("Preparing training data...")).toBeInTheDocument()
  })

  it("train button is disabled while training or submitting", () => {
    const { rerender } = render(<TrainingActionsAndResults {...makeProps({ training: true })} />)
    expect(screen.getByText("Training...").closest("button")).toBeDisabled()

    rerender(<TrainingActionsAndResults {...makeProps({ submitting: true })} />)
    expect(screen.getByText("Preparing training data...").closest("button")).toBeDisabled()
  })

  it("shows staleness indicator when isStale is true", () => {
    render(<TrainingActionsAndResults {...makeProps({ isStale: true })} />)
    expect(screen.getByText("Config changed since last training")).toBeInTheDocument()
    expect(screen.getByText("Re-train")).toBeInTheDocument()
  })

  it("Re-train button calls onTrain", () => {
    const onTrain = vi.fn()
    render(<TrainingActionsAndResults {...makeProps({ isStale: true, onTrain })} />)
    fireEvent.click(screen.getByText("Re-train"))
    expect(onTrain).toHaveBeenCalledTimes(1)
  })

  it("shows success badge when trainResult is complete", () => {
    render(<TrainingActionsAndResults {...makeProps({
      trainResult: makeTrainResult(),
    })} />)
    expect(screen.getByText(/Model trained/)).toBeInTheDocument()
  })

  it("shows error when trainResult has error status", () => {
    render(<TrainingActionsAndResults {...makeProps({
      trainResult: makeTrainResult({ status: "error", error: "Out of memory" }),
    })} />)
    expect(screen.getByText("Training failed")).toBeInTheDocument()
    expect(screen.getByText("Out of memory")).toBeInTheDocument()
  })

  it("shows structured terminal memory diagnostics when training failed", () => {
    render(<TrainingActionsAndResults {...makeProps({
      trainResult: makeTrainResult({ status: "error", error: "Out of memory" }),
      terminalMetrics: makeExecutionMetricsFixture({ profile: "training_prep", terminal_reason: "memory_limited" }),
    })} />)

    expect(screen.getByText("Memory pressure reached 75% of the training budget.")).toBeInTheDocument()
    expect(screen.getByText("RSS 1.7 KB of 2.9 KB limit")).toBeInTheDocument()
  })

  it("does not imply memory caused non-memory terminal training failures", () => {
    render(<TrainingActionsAndResults {...makeProps({
      trainResult: makeTrainResult({ status: "error", error: "Feature contract mismatch" }),
      terminalMetrics: makeExecutionMetricsFixture({
        profile: "training_prep",
        status: "running",
        terminal_reason: null,
      }),
      terminalStatus: "contract_error",
      terminalReason: "contract_error",
    })} />)

    expect(screen.getByText("Training failed")).toBeInTheDocument()
    expect(screen.getByText("Feature contract mismatch")).toBeInTheDocument()
    expect(screen.queryByText("Memory pressure reached 75% of the training budget.")).not.toBeInTheDocument()
    expect(screen.queryByText("Technical details")).not.toBeInTheDocument()
  })

  it("shows RAM estimate loading state", () => {
    render(<TrainingActionsAndResults {...makeProps({ ramEstimateLoading: true })} />)
    expect(screen.getByText("Estimating dataset size...")).toBeInTheDocument()
  })

  it("shows RAM estimate when available", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimate: makeTrainEstimate({ total_rows: 50000 }),
    })} />)
    expect(screen.getByText("Dataset fits in memory")).toBeInTheDocument()
    expect(screen.getByText("50,000")).toBeInTheDocument()
  })

  it("shows downsampling warning when was_downsampled", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimate: makeTrainEstimate({
        total_rows: 100000,
        safe_row_limit: 50000,
        was_downsampled: true,
      }),
    })} />)
    expect(screen.getByText("Will downsample")).toBeInTheDocument()
  })

  it("shows RAM estimate error when present", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimateError: "Connection failed",
    })} />)
    expect(screen.getByText(/RAM estimate unavailable/)).toBeInTheDocument()
  })
})
