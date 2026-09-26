/**
 * Full-size loss curve chart for the ModellingPreview panel: an adapter from
 * the training loss history to the shared `IterationLinesChart` (train and
 * eval curves, the best iteration as a dashed marker).
 */
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { IterationLinesChart } from "../IterationLinesChart"
import { ChartEmptyState, ResponsiveChart } from "./ChartScaffold"
import type { LossEntry } from "./LossChart"

interface LossTabProps {
  result: TrainResult
  width?: number
  height?: number
}

const TRAIN_COLOR = CHART_COLORS.train
const EVAL_COLOR = CHART_COLORS.eval
const BEST_COLOR = CHART_COLORS.best
const EMPTY_VALID_HISTORY_MESSAGE = "No valid loss history data available"

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value)

/** The fit the tab draws: its rows, whether they were thinned, its best iteration and intro. */
type ShownFit = {
  history: LossEntry[]
  thinned: boolean
  bestIteration: number | null
  intro: string | null
}

/**
 * The validation fit when one ran (after a refit its own history, otherwise
 * the model's), else the model's fit.
 */
function shownFit(result: TrainResult): ShownFit {
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

export function LossTab({ result, width, height = 280 }: LossTabProps) {
  const fit = shownFit(result)
  const lastRow = fit.history.at(-1)
  return (
    <div className="space-y-2">
      {fit.intro && (
        <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          {fit.intro}
        </p>
      )}
      {fit.thinned && lastRow && (
        <p className="text-[12px]" style={{ color: "var(--text-muted)" }}>
          {`Thinned to ${fit.history.length.toLocaleString()} of `
            + `${lastRow.iteration.toLocaleString()} iterations.`}
        </p>
      )}
      <ResponsiveChart width={width}>
        {(measuredWidth) => <LossTabChart fit={fit} width={measuredWidth} height={height} />}
      </ResponsiveChart>
    </div>
  )
}

function LossTabChart({ fit, width, height }: { fit: ShownFit; width: number; height: number }) {
  const lossHistory = fit.history
  if (!lossHistory || lossHistory.length < 2) {
    return <ChartEmptyState>No loss history data available</ChartEmptyState>
  }

  // Find train and eval loss keys
  const keys = Object.keys(lossHistory[0]).filter((k) => k !== "iteration")
  const trainKey = keys.find((k) => k.startsWith("train_"))
  const evalKey = keys.find((k) => k.startsWith("eval_"))
  if (!trainKey) {
    return <ChartEmptyState>No loss keys found in history</ChartEmptyState>
  }

  const trainPointCount = lossHistory.filter((entry) => isFiniteNumber(entry[trainKey])).length
  if (trainPointCount < 2) {
    return <ChartEmptyState>{EMPTY_VALID_HISTORY_MESSAGE}</ChartEmptyState>
  }

  // A non-finite loss (a diverged or skipped eval) is a gap in its curve.
  const lossValues = (key: string) =>
    lossHistory.map((entry) => (isFiniteNumber(entry[key]) ? entry[key] : null))
  const bestIteration = fit.bestIteration
  // Rows count iterations from one; best_iteration is zero-based.
  const bestIndex = isFiniteNumber(bestIteration)
    ? lossHistory.findIndex((entry) => entry.iteration === bestIteration + 1)
    : -1

  return (
    <IterationLinesChart
      x={lossHistory.map((entry, index) => entry.iteration ?? index)}
      series={[
        { label: "Train", color: TRAIN_COLOR, values: lossValues(trainKey) },
        ...(evalKey ? [{ label: "Eval", color: EVAL_COLOR, values: lossValues(evalKey) }] : []),
      ]}
      marker={
        bestIndex >= 0
          ? { index: bestIndex, label: `Best iteration (${bestIteration})`, color: BEST_COLOR }
          : null
      }
      width={width}
      height={height}
      title="Loss history by iteration"
      ariaLabel="Loss history chart"
      xLabel="Iteration"
      yLabel={trainKey.replace("train_", "")}
    />
  )
}
