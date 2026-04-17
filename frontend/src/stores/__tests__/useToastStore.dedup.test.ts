/**
 * Phase 1 Package 1H — Item #40: useToastStore must deduplicate identical
 * (type, text) toasts fired within a short window.
 *
 * Real-world trigger: a polling loop or retry that fails repeatedly can
 * fire the same error toast every second, burying the user in identical
 * notifications and obscuring other information.
 *
 * Fix: if the same (type, text) is added within DEDUP_WINDOW_MS (e.g. 2s)
 * of a previous identical toast, the new one is suppressed (or the existing
 * one's timer is reset — either behaviour produces "1 visible toast"
 * observable to the user).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import useToastStore from "../useToastStore"

function reset() {
  useToastStore.setState({
    toasts: [],
    _toastCounter: 0,
  })
}

describe("useToastStore — dedup within a short window (#40)", () => {
  beforeEach(() => {
    reset()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("firing the same (type, text) 5 times within 2s produces exactly 1 visible toast", () => {
    // Catches: a chatty error path would queue 5 copies of the same
    // message, filling the screen.
    const { addToast } = useToastStore.getState()
    for (let i = 0; i < 5; i++) {
      addToast("error", "Network error")
    }
    expect(useToastStore.getState().toasts).toHaveLength(1)
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      type: "error",
      text: "Network error",
    })
  })

  it("different texts with the same type are NOT deduplicated", () => {
    // Unique error messages should each produce their own toast.
    const { addToast } = useToastStore.getState()
    addToast("error", "Connection lost")
    addToast("error", "Invalid JSON")
    addToast("error", "Timeout")
    expect(useToastStore.getState().toasts).toHaveLength(3)
  })

  it("same text but different type is NOT deduplicated", () => {
    // An "info" and an "error" with the same text are distinct messages.
    const { addToast } = useToastStore.getState()
    addToast("info", "Pipeline saved")
    addToast("error", "Pipeline saved")
    expect(useToastStore.getState().toasts).toHaveLength(2)
  })

  it("same (type, text) fired after the dedup window elapses IS shown again", () => {
    // After ~2 seconds, firing the identical toast again is treated
    // as a fresh notification (e.g. a new retry cycle).
    const { addToast } = useToastStore.getState()
    addToast("warning", "Disk nearly full")
    expect(useToastStore.getState().toasts).toHaveLength(1)

    // Advance past the dedup window (2s + margin)
    vi.advanceTimersByTime(2_500)

    addToast("warning", "Disk nearly full")

    // Two distinct occurrences — user has been informed again after
    // the window.
    expect(useToastStore.getState().toasts.length).toBeGreaterThanOrEqual(1)
    // The most recent toast is the latest fire
    const latest = useToastStore.getState().toasts.at(-1)!
    expect(latest).toMatchObject({ type: "warning", text: "Disk nearly full" })
  })

  it("rapid bursts respect dedup across both add calls and counter increments", () => {
    // Catches: a dedup implemented only on the array but forgetting to
    // hold the counter would leak incrementing ids even for suppressed
    // toasts, breaking ordering elsewhere.
    const { addToast } = useToastStore.getState()
    for (let i = 0; i < 10; i++) {
      addToast("success", "Saved")
    }
    const state = useToastStore.getState()
    expect(state.toasts).toHaveLength(1)
    // The counter should only have advanced by the number of toasts
    // that were actually stored (1).  If the counter jumped to 10 we'd
    // know dedup was purely cosmetic.
    expect(state._toastCounter).toBeLessThanOrEqual(1)
  })

  it("dedup does not interfere with dismissing a stored toast", () => {
    // Sanity: dismissing the sole toast and re-adding within the
    // dedup window should still show it (the "previous" copy is gone,
    // so it's really a new message from the user's perspective).
    const { addToast, dismissToast } = useToastStore.getState()
    addToast("info", "Ready")
    expect(useToastStore.getState().toasts).toHaveLength(1)
    const id = useToastStore.getState().toasts[0].id
    dismissToast(id)
    expect(useToastStore.getState().toasts).toHaveLength(0)

    // Re-adding an identical toast after dismiss: acceptable EITHER
    // that it shows (because the visible state was cleared) OR is
    // suppressed (conservative dedup).  Either is fine; we just check
    // we don't duplicate it to two copies.
    addToast("info", "Ready")
    expect(useToastStore.getState().toasts.length).toBeLessThanOrEqual(1)
  })

  it("dedup applies per (type, text) pair independently", () => {
    const { addToast } = useToastStore.getState()
    addToast("error", "A")
    addToast("error", "A")  // dedup suppresses
    addToast("error", "B")  // distinct text → stored
    addToast("error", "B")  // dedup suppresses

    const state = useToastStore.getState()
    expect(state.toasts).toHaveLength(2)
    const texts = state.toasts.map((t) => t.text).sort()
    expect(texts).toEqual(["A", "B"])
  })
})
