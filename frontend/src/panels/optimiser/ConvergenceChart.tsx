/**
 * Convergence diagnostics for an optimiser solve, as small multiples on real
 * axes (`IterationLinesChart`) with the values behind them in a closed table.
 *
 * An online solve always records its per-iteration history: the objective, the
 * largest λ change (log axis), each constraint total against its bound with the
 * first iteration that met every constraint, and λ per constraint. A ratebook
 * solve shows its coordinate-descent trace by CD pass, one line per factor: the
 * objective and each constraint total against its bound. Only the solve records
 * either (a frontier point reports its convergence and iteration count, not its
 * iterations), so the chart always shows the solved result's and, with a point
 * selected, states whose history it is and how that point ended.
 */

import type { ReactNode } from "react"
import type {
  OptimiserEffectiveBound,
  OptimiserHistoryEntry,
  OptimiserRatebookCdTrace,
  OptimiserSolveResult,
} from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import { CHART_COLORS } from "../../theme/colors"
import { decade, formatChartNumber } from "../../utils/chartHelpers"
import {
  IterationLinesChart,
  type IterationLineSeries,
  type IterationMarker,
  type IterationReferenceLine,
} from "../IterationLinesChart"
import { ChartValuesTable, ResponsiveChart } from "../modelling/ChartScaffold"
import { selectedPointConvergenceNote } from "./iterationSummary"

interface ConvergenceChartProps {
  /** The as-solved result, whose history or CD trace is drawn. */
  solvedResult: OptimiserSolveResult
  /** The selected frontier point and its displayed result, if one is selected. */
  selectedPoint: { index: number; result: OptimiserSolveResult } | null
  /** A fixed width instead of the measured one. */
  width?: number
}

const CHART_HEIGHT = 220
const TWO_COLUMNS_FROM = 720
const COLUMN_GAP = 24
const LIVE_SOLVES_ONLY =
  "The coordinate-descent trace is recorded by live solves only; this result has none."

/** Totals and objectives in full, as the attainment table shows them. */
function formatExact(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 })
}

function seriesColor(index: number): string {
  return CHART_COLORS.optimiserSeries[index % CHART_COLORS.optimiserSeries.length]
}

function boundLine(bound: OptimiserEffectiveBound): IterationReferenceLine {
  return { label: `${bound.kind} bound ${formatChartNumber(bound.bound)}`, value: bound.bound, color: CHART_COLORS.neutral }
}

/** A value of a constraint-keyed map that must hold the constraint. */
function constraintValue(values: Record<string, number>, name: string, where: string): number {
  if (!Object.hasOwn(values, name)) throw new Error(`${where} has no value for constraint ${name}`)
  return values[name]
}

function Note({ children }: { children: ReactNode }) {
  return <p className="m-0 mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{children}</p>
}

function Panel({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <section aria-label={heading}>
      <h4 className="m-0 text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>{heading}</h4>
      {children}
    </section>
  )
}

/** Each chart's width: two columns when the pane is wide enough, else one. */
function SmallMultiples({ width, children }: { width?: number; children: (chartWidth: number) => ReactNode }) {
  return (
    <ResponsiveChart width={width}>
      {(containerWidth) => {
        const twoColumns = containerWidth >= TWO_COLUMNS_FROM
        const chartWidth = twoColumns ? (containerWidth - COLUMN_GAP) / 2 : containerWidth
        return (
          <div className={twoColumns ? "grid grid-cols-2 gap-6" : "space-y-6"}>
            {children(chartWidth)}
          </div>
        )
      }}
    </ResponsiveChart>
  )
}

/** The largest λ change on a log axis: a 0 (no change) is drawn a decade below
 *  the smallest change. `null` when λ never changed. */
function lambdaChangeValues(history: OptimiserHistoryEntry[]): { values: number[]; zeros: number; floor: number } | null {
  for (const entry of history) {
    if (!(entry.max_lambda_change >= 0)) {
      throw new Error(`Iteration ${entry.iteration} reports a λ change of ${entry.max_lambda_change}`)
    }
  }
  const positive = history.map((entry) => entry.max_lambda_change).filter((change) => change > 0)
  if (positive.length === 0) return null
  const floor = decade(Math.floor(Math.log10(Math.min(...positive)) + 1e-9) - 1)
  return {
    values: history.map((entry) => (entry.max_lambda_change > 0 ? entry.max_lambda_change : floor)),
    zeros: history.length - positive.length,
    floor,
  }
}

function OnlineConvergence({
  history,
  bounds,
  width,
}: {
  history: OptimiserHistoryEntry[]
  bounds: Record<string, OptimiserEffectiveBound>
  width?: number
}) {
  const names = Object.keys(bounds)
  const iterations = history.map((entry) => entry.iteration)
  const totals = Object.fromEntries(names.map((name) => [
    name,
    history.map((entry) => constraintValue(entry.total_constraints, name, `Iteration ${entry.iteration}'s totals`)),
  ]))
  const lambdas = Object.fromEntries(names.map((name) => [
    name,
    history.map((entry) => constraintValue(entry.lambdas, name, `Iteration ${entry.iteration}'s λ`)),
  ]))
  const lambdaChange = lambdaChangeValues(history)
  const firstFeasible = history.findIndex((entry) => entry.all_constraints_satisfied === true)
  const feasibleMarker: IterationMarker | null = firstFeasible < 0
    ? null
    : { index: firstFeasible, label: `First feasible (iteration ${iterations[firstFeasible]})`, color: CHART_COLORS.positive }
  const common = { x: iterations, height: CHART_HEIGHT, xLabel: "Iteration" }

  return (
    <>
      <SmallMultiples width={width}>
        {(chartWidth) => (
          <>
            <Panel heading="Objective">
              <IterationLinesChart
                {...common}
                width={chartWidth}
                title="Objective by iteration"
                ariaLabel="Objective by iteration"
                yLabel="Objective"
                series={[{ label: "Objective", color: CHART_COLORS.objective, values: history.map((entry) => entry.total_objective) }]}
              />
            </Panel>
            <Panel heading="Largest λ change">
              {lambdaChange ? (
                <>
                  <IterationLinesChart
                    {...common}
                    width={chartWidth}
                    yScale="log"
                    title="Largest λ change by iteration"
                    ariaLabel="Largest λ change by iteration"
                    yLabel="Largest λ change"
                    series={[{ label: "Largest λ change", color: CHART_COLORS.lambdaChange, values: lambdaChange.values }]}
                  />
                  {lambdaChange.zeros > 0 && (
                    <Note>
                      {lambdaChange.zeros === 1 ? "1 iteration" : `${lambdaChange.zeros} iterations`} with no λ change (0)
                      {lambdaChange.zeros === 1 ? " is" : " are"} drawn at the axis floor, {formatChartNumber(lambdaChange.floor)}.
                    </Note>
                  )}
                </>
              ) : (
                <Note>λ did not change in any iteration (the largest change is 0 throughout).</Note>
              )}
            </Panel>
            {names.map((name, index) => (
              <Panel key={name} heading={`${name} total`}>
                <IterationLinesChart
                  {...common}
                  width={chartWidth}
                  title={`${name} total by iteration`}
                  ariaLabel={`${name} total by iteration`}
                  yLabel={`${name} total`}
                  series={[{ label: `${name} total`, color: seriesColor(index), values: totals[name] }]}
                  referenceLines={[boundLine(bounds[name])]}
                  marker={feasibleMarker}
                />
                {!feasibleMarker && <Note>No iteration met every constraint.</Note>}
              </Panel>
            ))}
            {names.length > 0 && (
              <Panel heading="λ per constraint">
                <IterationLinesChart
                  {...common}
                  width={chartWidth}
                  title="λ by iteration"
                  ariaLabel="λ by iteration"
                  yLabel="λ"
                  series={names.map((name, index) => ({ label: name, color: seriesColor(index), values: lambdas[name] }))}
                />
              </Panel>
            )}
          </>
        )}
      </SmallMultiples>
      <ChartValuesTable
        summary="View iteration values"
        ariaLabel="Iteration values"
        headers={[
          "Iteration",
          "Objective",
          "Largest λ change",
          ...names.map((name) => `${name} total`),
          ...names.map((name) => `λ ${name}`),
          "All constraints met",
        ]}
        rows={history.map((entry, index) => [
          entry.iteration,
          formatExact(entry.total_objective),
          entry.max_lambda_change.toExponential(2),
          ...names.map((name) => formatExact(totals[name][index])),
          ...names.map((name) => lambdas[name][index].toFixed(6)),
          entry.all_constraints_satisfied ? "Yes" : "No",
        ])}
      />
    </>
  )
}

function RatebookConvergence({
  trace,
  bounds,
  width,
}: {
  trace: OptimiserRatebookCdTrace
  bounds: Record<string, OptimiserEffectiveBound>
  width?: number
}) {
  const names = Object.keys(bounds)
  const passes = [...new Set(trace.records.map((record) => record.cd_iteration))].sort((a, b) => a - b)
  const factors = [...new Map(trace.records.map((record) => [record.factor, record.factor_index]))]
    .sort(([, a], [, b]) => a - b)
    .map(([factor, factorIndex]) => ({ factor, factorIndex }))
  // One value per (factor, pass): the totals after that factor's update in that pass.
  const valueAt = new Map<string, Map<number, (typeof trace.records)[number]>>()
  for (const record of trace.records) {
    const byPass = valueAt.get(record.factor) ?? new Map()
    if (byPass.has(record.cd_iteration)) {
      throw new Error(`The CD trace records pass ${record.cd_iteration} of ${record.factor} twice`)
    }
    for (const name of names) {
      constraintValue(record.total_constraints, name, `CD pass ${record.cd_iteration} (${record.factor})`)
      constraintValue(record.lambdas, name, `CD pass ${record.cd_iteration} (${record.factor})`)
    }
    byPass.set(record.cd_iteration, record)
    valueAt.set(record.factor, byPass)
  }
  const byFactor = (value: (record: (typeof trace.records)[number]) => number): IterationLineSeries[] =>
    factors.map(({ factor, factorIndex }) => ({
      label: factor,
      color: seriesColor(factorIndex),
      values: passes.map((cdPass) => {
        const record = valueAt.get(factor)!.get(cdPass)
        return record ? value(record) : null
      }),
    }))
  const common = { x: passes, height: CHART_HEIGHT, xLabel: "CD pass", showPoints: true }

  return (
    <>
      {trace.truncated && (
        <Note>Showing the last {trace.records.length} coordinate-descent records; earlier ones were not kept.</Note>
      )}
      <SmallMultiples width={width}>
        {(chartWidth) => (
          <>
            <Panel heading="Objective">
              <IterationLinesChart
                {...common}
                width={chartWidth}
                title="Objective by CD pass"
                ariaLabel="Objective by CD pass"
                yLabel="Objective"
                series={byFactor((record) => record.total_objective)}
              />
            </Panel>
            {names.map((name) => (
              <Panel key={name} heading={`${name} total`}>
                <IterationLinesChart
                  {...common}
                  width={chartWidth}
                  title={`${name} total by CD pass`}
                  ariaLabel={`${name} total by CD pass`}
                  yLabel={`${name} total`}
                  series={byFactor((record) => record.total_constraints[name])}
                  referenceLines={[boundLine(bounds[name])]}
                />
              </Panel>
            ))}
          </>
        )}
      </SmallMultiples>
      <ChartValuesTable
        summary="View coordinate-descent values"
        ariaLabel="Coordinate-descent values"
        headers={[
          "CD pass",
          "Factor",
          "Objective",
          ...names.map((name) => `${name} total`),
          ...names.map((name) => `λ ${name}`),
        ]}
        rows={trace.records.map((record) => [
          record.cd_iteration,
          record.factor,
          formatExact(record.total_objective),
          ...names.map((name) => formatExact(record.total_constraints[name])),
          ...names.map((name) => record.lambdas[name].toFixed(6)),
        ])}
      />
    </>
  )
}

export default function ConvergenceChart({ solvedResult, selectedPoint, width }: ConvergenceChartProps) {
  const bounds = effectiveConstraintBounds(solvedResult)
  let body: ReactNode
  if (solvedResult.mode === "online") {
    const history = solvedResult.history
    if (!history?.length) throw new Error("An online solve always records its history; this result has none")
    body = <OnlineConvergence history={history} bounds={bounds} width={width} />
  } else {
    const trace = solvedResult.ratebook_cd_trace
    body = trace
      ? <RatebookConvergence trace={trace} bounds={bounds} width={width} />
      : <Note>{LIVE_SOLVES_ONLY}</Note>
  }

  return (
    <div className="space-y-3">
      {selectedPoint && (
        <p className="m-0 text-xs" style={{ color: "var(--text-secondary)" }}>
          {selectedPointConvergenceNote(selectedPoint.index, selectedPoint.result)}
        </p>
      )}
      {body}
    </div>
  )
}
