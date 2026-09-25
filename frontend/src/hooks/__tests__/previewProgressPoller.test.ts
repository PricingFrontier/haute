import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/client", () => ({ getPreviewProgress: vi.fn() }))

import { getPreviewProgress } from "../../api/client"
import type { PreviewProgressResponse } from "../../api/types"
import { PREVIEW_PROGRESS_POLL_MS, pollPreviewProgress } from "../previewProgressPoller"

const running: PreviewProgressResponse = {
  request_id: "r1",
  phase: "running",
  done: 1,
  total: 3,
  label: "Caching join",
}

describe("pollPreviewProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(getPreviewProgress).mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it("keeps asking through absences and failures until the request settles", async () => {
    vi.mocked(getPreviewProgress)
      .mockResolvedValueOnce(null)
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(running)
    const seen: PreviewProgressResponse[] = []
    const controller = new AbortController()

    const stop = pollPreviewProgress("r1", controller.signal, (progress) => seen.push(progress))
    await vi.advanceTimersByTimeAsync(PREVIEW_PROGRESS_POLL_MS * 3)
    expect(seen).toEqual([running])

    stop()
    const asked = vi.mocked(getPreviewProgress).mock.calls.length
    await vi.advanceTimersByTimeAsync(PREVIEW_PROGRESS_POLL_MS * 4)
    expect(vi.mocked(getPreviewProgress).mock.calls.length).toBe(asked)
    expect(vi.mocked(getPreviewProgress).mock.calls.every(([requestId]) => requestId === "r1")).toBe(true)
  })

  it("stops when the preview request is aborted", async () => {
    vi.mocked(getPreviewProgress).mockResolvedValue(running)
    const controller = new AbortController()
    pollPreviewProgress("r1", controller.signal, () => {})
    await vi.advanceTimersByTimeAsync(PREVIEW_PROGRESS_POLL_MS)
    controller.abort()
    const asked = vi.mocked(getPreviewProgress).mock.calls.length

    await vi.advanceTimersByTimeAsync(PREVIEW_PROGRESS_POLL_MS * 4)

    expect(vi.mocked(getPreviewProgress).mock.calls.length).toBe(asked)
  })
})
