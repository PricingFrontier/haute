import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { TrainingActionsAndResults } from "../TrainingActionsAndResults"
import type { TrainingActionsAndResultsProps } from "../TrainingActionsAndResults"
import { makeTrainResult, makeTrainEstimate } from "../../../test-utils/factories"
import { makeExecutionMetricsFixture } from "../../../testSupport/executionMetricsFixture"

afterEach(cleanup)

function makeProps(overrides: Partial<TrainingActionsAndResultsProps> = {}): TrainingActionsAndResultsProps {
  return {
    training: false,
    trainProgress: null,
    trainResult: null,
    isStale: false,
    ramEstimate: null,
    ramEstimateLoading: false,
    rowLimit: null,
    nodeLabel: (nodeId: string) => `label of ${nodeId}`,
    onTrain: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  }
}

describe("TrainingActionsAndResults", () => {
  it("presents a join's row product as an unproven bound, never a verdict (MDL-01)", () => {
    const open = vi.fn()
    const nodeOpener = vi.fn((nodeId: string) => (nodeId === "competitor_join" ? open : null))
    render(
      <TrainingActionsAndResults
        {...makeProps({
          nodeLabel: (nodeId: string) => nodeId,
          nodeOpener,
          ramEstimate: makeTrainEstimate({
            total_rows: 10_000_000_000,
            safe_row_limit: 149_958_852,
            bytes_per_row: 5_000,
            available_mb: 71_065,
            was_downsampled: false,
            warning: null,
            unbounded_join_node_ids: ["competitor_join"],
          }),
        })}
      />,
    )

    const box = screen.getByRole("status")
    expect(box).toHaveTextContent("Row count not proven")
    expect(box).toHaveTextContent('Up to 10,000,000,000 rows: "competitor_join" has no key contract.')
    expect(box).toHaveTextContent("Training row limit149,958,852")
    expect(screen.queryByText("Will downsample")).toBeNull()
    expect(screen.queryByText("Dataset fits in memory")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: 'Open "competitor_join"' }))
    expect(open).toHaveBeenCalledTimes(1)
  })

  it("shows a declared join's proven rows as fitting in memory", () => {
    render(
      <TrainingActionsAndResults
        {...makeProps({
          ramEstimate: makeTrainEstimate({ total_rows: 100_000, unbounded_join_node_ids: [] }),
        })}
      />,
    )

    expect(screen.getByText("Dataset fits in memory")).toBeInTheDocument()
    expect(screen.getByText("100,000")).toBeInTheDocument()
    expect(screen.queryByText("Row count not proven")).toBeNull()
  })

  it("renders Train Model button", () => {
    render(<TrainingActionsAndResults {...makeProps()} />)
    expect(screen.getByText("Train Model")).toBeInTheDocument()
  })

  it("keeps idle Train enabled and renders supplied validation directly beneath it", () => {
    render(
      <TrainingActionsAndResults
        {...makeProps({
          validationMessages: [
            "Select a target column.",
            "Choose a training loss.",
          ],
        })}
      />,
    )
    const button = screen.getByRole("button", { name: "Train Model" })
    const banner = screen.getByRole("alert")

    expect(button).toBeEnabled()
    expect(button.nextElementSibling).toBe(banner)
    expect(banner).toHaveTextContent("Select a target column.")
    expect(banner).toHaveTextContent("Choose a training loss.")
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

  it("shows a working Cancel control throughout an active job", () => {
    const onCancel = vi.fn()
    render(<TrainingActionsAndResults {...makeProps({
      training: true,
      trainProgress: {
        status: "running",
        progress: 0.1,
        message: "Preparing training data...",
        iteration: 0,
        total_iterations: 0,
        train_loss: {},
        elapsed_seconds: 1,
      },
      onCancel,
    })} />)

    fireEvent.click(screen.getByRole("button", { name: "Cancel training" }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("does not offer cancellation before the start response supplies a job handle", () => {
    render(<TrainingActionsAndResults {...makeProps({ submitting: true })} />)
    expect(screen.queryByRole("button", { name: "Cancel training" })).not.toBeInTheDocument()
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

    expect(screen.getByText("Training reached 75% of its memory allowance.")).toBeInTheDocument()
    expect(screen.getByText("Memory used: 1.7 KB; limit: 2.9 KB")).toBeInTheDocument()
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
    expect(screen.queryByText("Training reached 75% of its memory allowance.")).not.toBeInTheDocument()
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

  it("asks for a manual CPU retry when the selected GPU cannot fit", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimate: makeTrainEstimate({
        total_rows: 50000,
        gpu_vram_estimated_mb: 4096,
        gpu_vram_available_mb: 1024,
      }),
    })} />)

    expect(screen.getByText(/Select CPU and retry/i)).toBeInTheDocument()
    expect(screen.queryByText(/fall back.*automatically/i)).not.toBeInTheDocument()
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
    expect(screen.getByText("Memory estimate failed")).toBeInTheDocument()
    expect(screen.getByText("Connection failed")).toBeInTheDocument()
    expect(screen.queryByText("Memory estimate unavailable")).toBeNull()
    expect(screen.queryByText(/training will still work/)).toBeNull()
  })

  it("says which node's row count blocks an estimate instead of a memory verdict", () => {
    render(<TrainingActionsAndResults {...makeProps({
      rowLimit: 500,
      ramEstimate: makeTrainEstimate({
        total_rows: null,
        safe_row_limit: 500,
        estimated_mb: null,
        training_mb: null,
        bytes_per_row: null,
        available_mb: 8192,
        unavailable: { reason: "row_count_unprovable", blocking_node_id: "explode_items" },
      }),
    })} />)
    const card = screen.getByRole("status")
    expect(card).toHaveTextContent("Memory estimate unavailable")
    expect(card).toHaveTextContent(
      'The row count at "label of explode_items" can\'t be proven before it runs, so training memory can\'t be estimated.',
    )
    expect(card).toHaveTextContent("Available RAM8.0 GB")
    expect(card).not.toHaveTextContent("Source rows")
    expect(screen.queryByText("Dataset fits in memory")).toBeNull()
    expect(screen.queryByText("Will downsample")).toBeNull()
    expect(screen.queryByText("Est. training RAM")).toBeNull()
    expect(screen.queryByText(/0 MB/)).toBeNull()
  })

  it("keeps the known row total when only the schema blocks an estimate", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimate: makeTrainEstimate({
        total_rows: 250000,
        estimated_mb: null,
        training_mb: null,
        bytes_per_row: null,
        available_mb: 8192,
        unavailable: { reason: "schema_unresolvable", blocking_node_id: null },
      }),
    })} />)
    const card = screen.getByRole("status")
    expect(card).toHaveTextContent(
      "The columns reaching this node can't be resolved before it runs, so training memory can't be estimated.",
    )
    expect(card).toHaveTextContent("Source rows250,000")
    expect(screen.queryByText("Dataset fits in memory")).toBeNull()
    expect(screen.queryByText("Est. training RAM")).toBeNull()
  })

  it("distinguishes an evaluation failure from an unavailable memory estimate", () => {
    render(<TrainingActionsAndResults {...makeProps({
      ramEstimateError: "Evaluation preview failed: validation partition is empty",
    })} />)
    expect(screen.getByText("Evaluation preview failed")).toBeInTheDocument()
    expect(screen.getByText("validation partition is empty")).toBeInTheDocument()
    expect(screen.queryByText("Memory estimate unavailable")).toBeNull()
  })
})
