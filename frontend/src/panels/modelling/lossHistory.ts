import type { LossEntry } from "./LossChart"

/** The keys a loss history draws its curves from, or null when it cannot draw one. */
export function lossCurveKeys(
  lossHistory: readonly LossEntry[],
): { trainKey: string; evalKey: string | undefined } | null {
  if (lossHistory.length < 2) return null
  const keys = Object.keys(lossHistory[0]).filter((k) => k !== "iteration")
  const trainKey = keys.find((k) => k.startsWith("train_"))
  if (!trainKey) return null
  return { trainKey, evalKey: keys.find((k) => k.startsWith("eval_")) }
}
