import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { TrainingProgress } from "../TrainingProgress"
import type { TrainProgress } from "../../../stores/useNodeResultsStore"
import { makeExecutionMetricsFixture } from "../../../testSupport/executionMetricsFixture"

vi.mock("../../../utils/formatValue", () => ({
  formatElapsed: vi.fn((s: number) => `${s}s`),
}))

function makeProgress(overrides: Partial<TrainProgress> = {}): TrainProgress {
  return {
    status: "running",
    progress: 0.5,
    message: "Training model...",
    iteration: 50,
    total_iterations: 100,
    train_loss: {},
    elapsed_seconds: 30,
    ...overrides,
  }
}

describe("TrainingProgress", () => {
  afterEach(cleanup)
  it("renders message text", () => {
    render(<TrainingProgress trainProgress={makeProgress({ message: "Fitting trees" })} />)
    expect(screen.getByText("Fitting trees")).toBeInTheDocument()
  })

  it("progress bar width >= 2% (minimum)", () => {
    const { container } = render(<TrainingProgress trainProgress={makeProgress({ progress: 0 })} />)
    const bar = container.querySelector(".h-full.rounded-full") as HTMLElement
    expect(bar.style.width).toBe("2%")
  })

  it("iteration stats hidden when total_iterations is 0", () => {
    const { container } = render(
      <TrainingProgress trainProgress={makeProgress({ total_iterations: 0 })} />,
    )
    expect(container.textContent).not.toContain("Round")
  })

  it("iteration stats shown when total_iterations > 0", () => {
    const { container } = render(
      <TrainingProgress trainProgress={makeProgress({ iteration: 25, total_iterations: 100 })} />,
    )
    expect(screen.getByText("25")).toBeInTheDocument()
    expect(container.textContent).toContain("/100")
  })

  it("loss entries rendered with 4 decimal places", () => {
    const { container } = render(
      <TrainingProgress
        trainProgress={makeProgress({ train_loss: { rmse: 0.123456789 }, total_iterations: 100 })}
      />,
    )
    expect(container.textContent).toContain("rmse:")
    expect(container.textContent).toContain("0.1235")
  })

  it("renders the authoritative bounded loss-history snapshot and truncation label", () => {
    render(
      <TrainingProgress
        trainProgress={makeProgress({
          train_loss_history: [
            { iteration: 40, train_rmse: 0.8, eval_rmse: 0.9 },
            { iteration: 50, train_rmse: 0.7, eval_rmse: 0.85 },
          ],
          train_loss_history_truncated: true,
        })}
      />,
    )

    expect(screen.getByText("Loss Curve")).toBeInTheDocument()
    expect(
      screen.getByText("Showing latest retained loss-history window."),
    ).toBeInTheDocument()
  })

  it("does not synthesize a chart from the latest loss poll", () => {
    render(
      <TrainingProgress
        trainProgress={makeProgress({ train_loss: { train_rmse: 0.7 } })}
      />,
    )

    expect(screen.queryByText("Loss Curve")).toBeNull()
  })

  it("shows an estimate only when the store supplies one", () => {
    const rendered = render(
      <TrainingProgress
        trainProgress={makeProgress()}
        estimatedRemainingSeconds={45}
      />,
    )
    expect(screen.getByText("Estimated remaining: 45s")).toBeInTheDocument()

    rendered.rerender(<TrainingProgress trainProgress={makeProgress()} />)
    expect(screen.queryByText(/Estimated remaining/)).toBeNull()
  })

  it("renders tuning trial, fold, fit count, and best objective", () => {
    render(
      <TrainingProgress
        trainProgress={makeProgress({
          phase: "trial_fit",
          trial_index: 7,
          trial_count: 20,
          fold_index: 2,
          fold_count: 5,
          completed_fits: 31,
          total_fits: 101,
          best_objective: 0.4172,
          total_iterations: 0,
        })}
      />,
    )

    expect(screen.getByText(
      "Trial 7 of 20 · Fold 2 of 5 · 31 of 101 fits · Best objective 0.4172",
    )).toBeInTheDocument()
  })

  it("renders memory-pressure diagnostics from structured progress metrics", () => {
    render(
      <TrainingProgress
        trainProgress={makeProgress({ execution_metrics: makeExecutionMetricsFixture({ profile: "training_prep" }) })}
      />,
    )

    expect(screen.getByText("Memory pressure reached 75% of the training budget.")).toBeInTheDocument()
    expect(screen.getByText("Headroom used 1.5 KB of 2.0 KB")).toBeInTheDocument()
  })
})
