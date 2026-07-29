import { describe, expect, it } from "vitest"

import {
  nextTrainEstimate,
  type TrainProgress,
} from "../useNodeResultsStore"

function progress(
  iteration: number,
  elapsedSeconds: number,
  overrides: Partial<TrainProgress> = {},
): TrainProgress {
  return {
    status: "running",
    progress: iteration / 100,
    message: "Training",
    iteration,
    total_iterations: 100,
    train_loss: {},
    elapsed_seconds: elapsedSeconds,
    ...overrides,
  }
}

describe("nextTrainEstimate", () => {
  it("hides the estimate until two valid samples advance", () => {
    const first = nextTrainEstimate([], progress(20, 10))
    expect(first.estimatedRemainingSeconds).toBeNull()

    const second = nextTrainEstimate(first.samples, progress(40, 20))
    expect(second.estimatedRemainingSeconds).toBe(30)
    expect(second.samples).toEqual([
      { iteration: 20, elapsedSeconds: 10, totalIterations: 100 },
      { iteration: 40, elapsedSeconds: 20, totalIterations: 100 },
    ])
  })

  it.each([
    progress(20, 20),
    progress(30, 10),
    progress(10, 30),
    progress(40, Number.NaN),
    progress(40, 20, { total_iterations: 40 }),
  ])("hides duplicate, stalled, non-monotonic, or invalid updates", (update) => {
    const previous = [
      { iteration: 30, elapsedSeconds: 20, totalIterations: 100 },
    ]

    expect(nextTrainEstimate(previous, update)).toEqual({
      samples: previous,
      estimatedRemainingSeconds: null,
    })
  })

  it("clears samples on a terminal update", () => {
    expect(
      nextTrainEstimate(
        [{ iteration: 30, elapsedSeconds: 20, totalIterations: 100 }],
        progress(100, 60, { status: "completed" }),
      ),
    ).toEqual({ samples: [], estimatedRemainingSeconds: null })
  })

  it("starts a new empty job without carrying a prior estimate", () => {
    expect(nextTrainEstimate([], progress(5, 2))).toEqual({
      samples: [{ iteration: 5, elapsedSeconds: 2, totalIterations: 100 }],
      estimatedRemainingSeconds: null,
    })
  })
})
