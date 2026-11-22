import { describe, expect, it } from "vitest"
import { advanceRunProgress, EMPTY_RUN_PROGRESS } from "../runProgress"

describe("continuous staged progress", () => {
  it("keeps one bar across the handoff, ignores regressions and resets for a new explicit run", () => {
    let value = advanceRunProgress(EMPTY_RUN_PROGRESS, { id: "fit", running: true, progress: 0.8 })
    expect(value.progress).toBe(0.4)
    value = advanceRunProgress(value, { id: "fit", running: true, progress: 0.7 })
    expect(value.progress).toBe(0.4)
    value = advanceRunProgress(value, { id: "fit", queued: true })
    expect(value).toMatchObject({ active: true, progress: 0.5 })
    value = advanceRunProgress(value, { id: "path", automatic: true, running: true, progress: 0 })
    expect(value.progress).toBe(0.5)
    value = advanceRunProgress(value, { id: "path", automatic: true, running: true, progress: 0.8 })
    expect(value.progress).toBe(0.9)
    value = advanceRunProgress(value, { id: "path", automatic: true })
    expect(value).toMatchObject({ active: false, progress: 1 })
    value = advanceRunProgress(value, { id: "new-fit", running: true, progress: 0 })
    expect(value).toMatchObject({ active: true, progress: 0 })
  })
  it("restores automatic work in the second half and ends a failed run without claiming completion", () => {
    const idle = advanceRunProgress(EMPTY_RUN_PROGRESS, {})
    const restored = advanceRunProgress(idle, { id: "path", automatic: true, running: true, progress: 0.2 })
    expect(restored.progress).toBe(0.6)
    expect(advanceRunProgress(restored, { id: "path", failed: true })).toMatchObject({ active: false, progress: 0.6 })
  })
})
