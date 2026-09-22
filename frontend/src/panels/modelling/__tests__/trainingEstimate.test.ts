import { describe, expect, it, vi } from "vitest"
import { ApiError } from "../../../api/client"
import {
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
  it("retries while only evaluation previews hold the budget", async () => {
    const request = vi.fn()
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockRejectedValueOnce(refusal([PREVIEW]))
      .mockResolvedValue("estimate")

    await expect(
      estimateAfterSupersededPreviews(request, new AbortController().signal, [0, 0, 0]),
    ).resolves.toBe("estimate")
    expect(request).toHaveBeenCalledTimes(3)
  })

  it("surfaces the refusal once the retries are spent", async () => {
    const error = refusal([PREVIEW])
    const request = vi.fn().mockRejectedValue(error)

    await expect(
      estimateAfterSupersededPreviews(request, new AbortController().signal, [0, 0]),
    ).rejects.toBe(error)
    expect(request).toHaveBeenCalledTimes(3)
  })

  it("does not wait behind a running training job", async () => {
    const error = refusal(["training_prep:training_job"])
    const request = vi.fn().mockRejectedValue(error)

    await expect(
      estimateAfterSupersededPreviews(request, new AbortController().signal, [0, 0]),
    ).rejects.toBe(error)
    expect(request).toHaveBeenCalledTimes(1)
  })

  it("stops retrying when the estimate is superseded", async () => {
    const controller = new AbortController()
    const request = vi.fn().mockImplementation(async () => {
      controller.abort()
      throw refusal([PREVIEW])
    })

    await expect(
      estimateAfterSupersededPreviews(request, controller.signal, [0, 0]),
    ).rejects.toBeInstanceOf(ApiError)
    expect(request).toHaveBeenCalledTimes(1)
  })
})
