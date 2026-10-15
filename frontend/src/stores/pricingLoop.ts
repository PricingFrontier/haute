/**
 * Values priced as they are typed (specs/workbench), the one loop Preview's sample and the
 * underwriter app's quote share: priced once typing pauses, one pricing at a time, and,
 * asked again while one runs, once more when it finishes, on the values as they are then.
 * `priceOnce` is the store's own: what it prices, through what, and which answers are
 * still its.
 */

export interface PricingLoop {
  /** Price once typing pauses for `delayMs`, the pause restarting with each call. */
  schedule: () => void
  /** Drop the pricing waiting for the pause and the one more asked for meanwhile; one under way finishes. */
  cancel: () => void
  /** Price now; asked again while one runs, price once more when it finishes. */
  priceNow: () => Promise<void>
  /** Price now what waits for the pause, and wait until nothing waits and nothing runs. */
  settle: () => Promise<void>
}

export function pricingLoop(priceOnce: () => Promise<void>, delayMs: number): PricingLoop {
  let timer: ReturnType<typeof setTimeout> | null = null
  let run: Promise<void> | null = null
  let again = false

  const stopWaiting = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  const priceNow = (): Promise<void> => {
    if (run !== null) {
      again = true
      return run
    }
    run = (async () => {
      try {
        do {
          again = false
          await priceOnce()
        } while (again)
      } finally {
        run = null
      }
    })()
    return run
  }

  return {
    schedule: () => {
      stopWaiting()
      timer = setTimeout(() => {
        timer = null
        void priceNow()
      }, delayMs)
    },
    cancel: () => {
      stopWaiting()
      again = false
    },
    priceNow,
    settle: async () => {
      // A value entered while it waits is priced before it settles: it settles once
      // nothing waits for the pause and nothing is being priced.
      while (timer !== null || run !== null) {
        if (timer !== null) {
          stopWaiting()
          await priceNow()
        } else {
          await run
        }
      }
    },
  }
}
