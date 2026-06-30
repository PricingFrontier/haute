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
