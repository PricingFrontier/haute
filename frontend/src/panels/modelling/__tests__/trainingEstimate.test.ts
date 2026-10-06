import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../../api/client"
import {
  INPUT_SNAPSHOT_RETRY_DELAY_MS,
  INPUT_SNAPSHOT_WAIT_LIMIT_MS,
  SUPERSEDED_PREVIEW_RETRY_DELAYS_MS,
  estimateAfterSupersededPreviews,
  transientEstimateHolders,
} from "../trainingEstimate"

function refusal(holders: string[], reason = "in_flight_memory_budget_exceeded"): ApiError {
  const detail = { error_code: "memory_limit", reason, in_flight_operations: holders }
  return new ApiError("HTTP 507", 507, JSON.stringify(detail), { detail }, detail)
}

const PREVIEW = "training_prep:training_evaluation_preview"
const SNAPSHOT = "lazy_sink:input_snapshot_build"

describe("transientEstimateHolders", () => {
  it.each([
    [refusal([PREVIEW]), "evaluation_preview"],
    [refusal([PREVIEW, PREVIEW]), "evaluation_preview"],
    [refusal([SNAPSHOT]), "input_snapshot"],
    [refusal(["preview_eager:input_snapshot_build"]), "input_snapshot"],
    [refusal([PREVIEW, SNAPSHOT]), "input_snapshot"],
    [refusal([PREVIEW, "training_prep:training_job"]), null],
    [refusal([SNAPSHOT, "training_prep:training_job"]), null],
    [refusal(["input_snapshot_build"]), null],
    [refusal([]), null],
    [refusal([SNAPSHOT], "process_rss_limit_exceeded"), null],
    [new ApiError("HTTP 500", 500), null],
    [new Error("network"), null],
  ])("classifies %#", (error, expected) => {
    expect(transientEstimateHolders(error)).toBe(expected)
  })
})

describe("estimateAfterSupersededPreviews", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("waits each backoff delay before retrying a preview refusal", async () => {
    const request = vi.fn()
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockResolvedValue("estimate")

    const estimate = estimateAfterSupersededPreviews(request, new AbortController().signal, [100, 300])
    await vi.advanceTimersByTimeAsync(0)
    expect(request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(99)
    expect(request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(request).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(299)
    expect(request).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    await expect(estimate).resolves.toBe("estimate")
    expect(request).toHaveBeenCalledTimes(3)
  })

  it("uses the default backoff schedule", async () => {
    const request = vi.fn().mockRejectedValueOnce(refusal([PREVIEW])).mockResolvedValue("estimate")

    const estimate = estimateAfterSupersededPreviews(request, new AbortController().signal)
    await vi.advanceTimersByTimeAsync(SUPERSEDED_PREVIEW_RETRY_DELAYS_MS[0] - 1)
    expect(request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await expect(estimate).resolves.toBe("estimate")
  })

  it("surfaces the refusal once the retries are spent", async () => {
    const error = refusal([PREVIEW])
    const request = vi.fn().mockRejectedValue(error)

    const estimate = estimateAfterSupersededPreviews(request, new AbortController().signal, [10, 10])
    const settled = expect(estimate).rejects.toBe(error)
    await vi.advanceTimersByTimeAsync(20)
    await settled
    expect(request).toHaveBeenCalledTimes(3)
  })

  it("does not wait behind a running training job", async () => {
    const error = refusal(["training_prep:training_job"])
    const request = vi.fn().mockRejectedValue(error)

    await expect(
      estimateAfterSupersededPreviews(request, new AbortController().signal, [10]),
    ).rejects.toBe(error)
    expect(request).toHaveBeenCalledTimes(1)
  })

  it("stops at once when superseded during a backoff delay", async () => {
    const controller = new AbortController()
    const request = vi.fn().mockRejectedValue(refusal([PREVIEW]))

    const estimate = estimateAfterSupersededPreviews(request, controller.signal, [1_000, 1_000])
    const settled = expect(estimate).rejects.toMatchObject({ name: "AbortError" })
    await vi.advanceTimersByTimeAsync(10)
    expect(request).toHaveBeenCalledTimes(1)
    controller.abort()
    await settled
    await vi.advanceTimersByTimeAsync(5_000)
    expect(request).toHaveBeenCalledTimes(1)
  })

  it("does not retry a refusal that arrives after the estimate was superseded", async () => {
    const controller = new AbortController()
    const request = vi.fn().mockImplementation(async () => {
      controller.abort()
      throw refusal([PREVIEW])
    })

    await expect(
      estimateAfterSupersededPreviews(request, controller.signal, [10]),
    ).rejects.toBeInstanceOf(ApiError)
    expect(request).toHaveBeenCalledTimes(1)
  })

  it("waits out an input-snapshot build every two seconds and says so", async () => {
    const onWaiting = vi.fn()
    const request = vi.fn()
      .mockRejectedValueOnce(refusal([SNAPSHOT]))
      .mockRejectedValueOnce(refusal([PREVIEW, SNAPSHOT]))
      .mockResolvedValue("estimate")

    const estimate = estimateAfterSupersededPreviews(
      request, new AbortController().signal, undefined, { onWaiting },
    )
    await vi.advanceTimersByTimeAsync(0)
    expect(onWaiting).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(INPUT_SNAPSHOT_RETRY_DELAY_MS - 1)
    expect(request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(request).toHaveBeenCalledTimes(2)
    expect(onWaiting).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(INPUT_SNAPSHOT_RETRY_DELAY_MS)
    await expect(estimate).resolves.toBe("estimate")
    expect(INPUT_SNAPSHOT_RETRY_DELAY_MS).toBe(2_000)
  })

  it("gives up on a snapshot build after a minute of waiting", async () => {
    const error = refusal([SNAPSHOT])
    const request = vi.fn().mockRejectedValue(error)

    const estimate = estimateAfterSupersededPreviews(request, new AbortController().signal)
    const settled = expect(estimate).rejects.toBe(error)
    await vi.advanceTimersByTimeAsync(INPUT_SNAPSHOT_WAIT_LIMIT_MS)
    await settled
    expect(INPUT_SNAPSHOT_WAIT_LIMIT_MS).toBe(60_000)
    expect(request).toHaveBeenCalledTimes(INPUT_SNAPSHOT_WAIT_LIMIT_MS / INPUT_SNAPSHOT_RETRY_DELAY_MS + 1)
  })

  it("does not report a preview-only wait", async () => {
    const onWaiting = vi.fn()
    const request = vi.fn().mockRejectedValueOnce(refusal([PREVIEW])).mockResolvedValue("estimate")

    const estimate = estimateAfterSupersededPreviews(
      request, new AbortController().signal, [10], { onWaiting },
    )
    await vi.advanceTimersByTimeAsync(10)
    await expect(estimate).resolves.toBe("estimate")
    expect(onWaiting).not.toHaveBeenCalled()
  })

  it("draws alternating refusals from budgets that never reset", async () => {
    const snapshotError = refusal([SNAPSHOT])
    const request = vi.fn()
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockRejectedValueOnce(snapshotError)
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockRejectedValueOnce(snapshotError)
      .mockRejectedValueOnce(refusal([PREVIEW]))

    const estimate = estimateAfterSupersededPreviews(
      request, new AbortController().signal, [10, 10], { snapshotDelayMs: 100, snapshotLimitMs: 100 },
    )
    const settled = expect(estimate).rejects.toBe(snapshotError)
    // preview (10) -> snapshot (100, the whole snapshot budget) -> preview (10, the last
    // preview delay) -> snapshot, whose budget is spent: returned without a fifth request.
    await vi.advanceTimersByTimeAsync(120)
    await settled
    expect(request).toHaveBeenCalledTimes(4)
  })

  it("stops at once when superseded while waiting for a snapshot build", async () => {
    const controller = new AbortController()
    const request = vi.fn().mockRejectedValue(refusal([SNAPSHOT]))

    const estimate = estimateAfterSupersededPreviews(request, controller.signal)
    const settled = expect(estimate).rejects.toMatchObject({ name: "AbortError" })
    await vi.advanceTimersByTimeAsync(500)
    controller.abort()
    await settled
    await vi.advanceTimersByTimeAsync(10_000)
    expect(request).toHaveBeenCalledTimes(1)
  })
})
