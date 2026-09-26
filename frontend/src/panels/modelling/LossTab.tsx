/**
 * Full-size loss curve chart for the ModellingPreview panel: an adapter from
 * the training loss history to the shared `IterationLinesChart` (train and
 * eval curves, the best iteration as a dashed marker).
 */
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { IterationLinesChart } from "../IterationLinesChart"
import { ChartEmptyState, ResponsiveChart } from "./ChartScaffold"

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

export function LossTab({ result, width, height = 280 }: LossTabProps) {
  return (
    <ResponsiveChart width={width}>
      {(measuredWidth) => <LossTabChart result={result} width={measuredWidth} height={height} />}
    </ResponsiveChart>
  )
}

function LossTabChart({ result, width, height }: Required<LossTabProps>) {
  const lossHistory = result.loss_history
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
  const bestIteration = result.best_iteration

  return (
    <IterationLinesChart
      x={lossHistory.map((entry, index) => entry.iteration ?? index)}
      series={[
        { label: "Train", color: TRAIN_COLOR, values: lossValues(trainKey) },
        ...(evalKey ? [{ label: "Eval", color: EVAL_COLOR, values: lossValues(evalKey) }] : []),
      ]}
      marker={
        isFiniteNumber(bestIteration)
          ? {
              index: Math.min(Math.max(Math.round(bestIteration), 0), lossHistory.length - 1),
              label: `Best iteration (${bestIteration})`,
              color: BEST_COLOR,
            }
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
