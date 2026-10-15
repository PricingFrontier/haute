/**
 * The loop Preview's sample and the underwriter app's quote are priced through
 * (specs/workbench): once typing pauses, one pricing at a time, one more when asked again
 * meanwhile, and settled once nothing waits and nothing runs.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { pricingLoop } from "../pricingLoop"

/** A pricing that finishes when told to, counting how many began. */
function pricings() {
  const finishes: (() => void)[] = []
  const priceOnce = vi.fn(() => new Promise<void>((resolve) => finishes.push(resolve)))
  return { priceOnce, finish: () => finishes.shift()?.() }
}

describe("pricingLoop", () => {
  afterEach(() => vi.useRealTimers())

  it("prices once the pause has passed, the pause restarting with each call", async () => {
    vi.useFakeTimers()
    const { priceOnce, finish } = pricings()
    const loop = pricingLoop(priceOnce, 300)

    loop.schedule()
    await vi.advanceTimersByTimeAsync(200)
    loop.schedule()
    await vi.advanceTimersByTimeAsync(200)
    expect(priceOnce).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(100)
    expect(priceOnce).toHaveBeenCalledTimes(1)
    finish()
  })

  it("runs one pricing at a time, and once more after it however often it is asked meanwhile", async () => {
    const { priceOnce, finish } = pricings()
    const loop = pricingLoop(priceOnce, 300)

    const running = loop.priceNow()
    void loop.priceNow()
    void loop.priceNow()
    expect(priceOnce).toHaveBeenCalledTimes(1)
    finish()
    await vi.waitFor(() => expect(priceOnce).toHaveBeenCalledTimes(2))
    finish()
    await running
    expect(priceOnce).toHaveBeenCalledTimes(2)
  })

  it("drops what waits for the pause and the one more asked for once cancelled, the pricing under way finishing", async () => {
    vi.useFakeTimers()
    const { priceOnce, finish } = pricings()
    const loop = pricingLoop(priceOnce, 300)

    const running = loop.priceNow()
    void loop.priceNow()
    loop.schedule()
    loop.cancel()
    finish()
    await running
    await vi.advanceTimersByTimeAsync(1000)
    expect(priceOnce).toHaveBeenCalledTimes(1)
  })

  it("settles once nothing waits for the pause and nothing runs, pricing what waited first", async () => {
    vi.useFakeTimers()
    const { priceOnce, finish } = pricings()
    const loop = pricingLoop(priceOnce, 300)
    loop.schedule()

    let settled = false
    const settling = loop.settle().then(() => (settled = true))
    await vi.waitFor(() => expect(priceOnce).toHaveBeenCalledTimes(1))
    expect(settled).toBe(false)
    finish()
    await settling
    expect(settled).toBe(true)
  })
})
