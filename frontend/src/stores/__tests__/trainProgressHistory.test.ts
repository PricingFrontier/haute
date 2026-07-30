import { beforeEach, describe, expect, it } from "vitest"

import useNodeResultsStore, {
  type TrainProgress,
} from "../useNodeResultsStore"

function progress(
  iteration: number,
  history: TrainProgress["train_loss_history"],
): TrainProgress {
  return {
    status: "running",
    progress: iteration / 100,
    message: "Training",
    iteration,
    total_iterations: 100,
    train_loss: { train_rmse: iteration / 100 },
    train_loss_history: history,
    train_loss_history_truncated: history ? iteration > 10 : undefined,
    elapsed_seconds: iteration,
  }
}

describe("training progress loss history", () => {
  beforeEach(() => {
    useNodeResultsStore.setState({ trainJobs: {}, trainResults: {} })
    useNodeResultsStore
      .getState()
      .startTrainJob("model", "job", "Model", "hash", "live", 0)
  })

  it("retains the latest backend snapshot without appending prior rows", () => {
    const first = [
      { iteration: 10, train_rmse: 0.9 },
      { iteration: 20, train_rmse: 0.8 },
    ]
    const latest = [
      { iteration: 30, train_rmse: 0.7 },
      { iteration: 40, train_rmse: 0.6 },
    ]

    useNodeResultsStore.getState().updateTrainProgress(
      "model",
      progress(20, first),
    )
    useNodeResultsStore.getState().updateTrainProgress(
      "model",
      progress(40, latest),
    )

    expect(
      useNodeResultsStore.getState().trainJobs.model.progress?.train_loss_history,
    ).toEqual(latest)
    expect(
      useNodeResultsStore.getState().trainJobs.model.progress
        ?.train_loss_history_truncated,
    ).toBe(true)
  })

  it("does not invent history when the latest status omits it", () => {
    useNodeResultsStore.getState().updateTrainProgress(
      "model",
      progress(20, [{ iteration: 20, train_rmse: 0.8 }]),
    )
    useNodeResultsStore.getState().updateTrainProgress(
      "model",
      progress(30, undefined),
    )

    expect(
      useNodeResultsStore.getState().trainJobs.model.progress?.train_loss_history,
    ).toBeUndefined()
  })
})
