import type { TrainResult } from "../../stores/useNodeResultsStore"
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

/** The fit the tab draws: its rows, whether they were thinned, its best iteration and intro. */
export type ShownFit = {
  history: LossEntry[]
  thinned: boolean
  bestIteration: number | null
  intro: string | null
}

/**
 * The validation fit when one ran (after a refit its own history, otherwise
 * the model's), else the model's fit.
 */
export function shownFit(result: TrainResult): ShownFit {
  const evaluation = result.evaluation
  const holdout = evaluation?.validation_method === "single" ? evaluation.selection_fits[0] : undefined
  const validationIntro = holdout
    ? `Validation fit: ${holdout.train_rows.toLocaleString()} training, `
      + `${holdout.validation_rows.toLocaleString()} validation rows.`
    : null
  if (holdout && result.validation_loss_history.length > 0) {
    const iterations =
      result.final_tree_count != null
        ? ` for ${result.final_tree_count.toLocaleString()} iterations`
        : ""
    return {
      history: result.validation_loss_history,
      thinned: result.validation_loss_history_truncated,
      bestIteration: holdout.best_iteration ?? null,
      intro:
        `${validationIntro} The final model was refit on all `
        + `${result.development_rows.toLocaleString()} development rows${iterations}.`,
    }
  }
  return {
    history: result.loss_history,
    thinned: result.loss_history_truncated,
    bestIteration: result.best_iteration,
    intro: evaluation?.refit_on_development === false ? validationIntro : null,
  }
}
