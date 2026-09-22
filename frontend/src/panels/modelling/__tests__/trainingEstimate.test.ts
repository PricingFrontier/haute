import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../../api/client"
import {
  SUPERSEDED_PREVIEW_RETRY_DELAYS_MS,
  estimateAfterSupersededPreviews,
  refusedByEvaluationPreviews,
} from "../trainingEstimate"

function refusal(holders: string[], reason = "in_flight_memory_budget_exceeded"): ApiError {
  const detail = { error_code: "memory_limit", reason, in_flight_operations: holders }
  return new ApiError("HTTP 507", 507, JSON.stringify(detail), { detail }, detail)
}

const PREVIEW = "training_prep:training_evaluation_preview"

describe("refusedByEvaluationPreviews", () => {
  it.each([
    [refusal([PREVIEW]), true],
    [refusal([PREVIEW, PREVIEW]), true],
    [refusal([PREVIEW, "training_prep:training_job"]), false],
    [refusal([]), false],
    [refusal([PREVIEW], "process_rss_limit_exceeded"), false],
    [new ApiError("HTTP 500", 500), false],
    [new Error("network"), false],
  ])("classifies %#", (error, expected) => {
    expect(refusedByEvaluationPreviews(error)).toBe(expected)
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
})
