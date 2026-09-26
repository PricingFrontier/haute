/**
 * Convergence diagnostics for an optimiser solve.
 *
 * Shows an objective / lambda-change line chart plus an iteration
 * table. Only the solve records a history (a frontier point reports its
 * convergence and iteration count, not its iterations), so the chart always
 * shows the solved result's history and, with a point selected, states
 * whose history it is and how that point ended.
 */

import { formatNumber } from "../../utils/formatValue"
import { chartDomain } from "../../utils/chartHelpers"
import { ChartSvg } from "../modelling/ChartScaffold"
import { CHART_COLORS } from "../../theme/colors"
import type { OptimiserSolveResult } from "../../api/types"
import { selectedPointConvergenceNote } from "./iterationSummary"

interface ConvergenceChartProps {
  /** The as-solved result, whose history is drawn. */
  solvedResult: OptimiserSolveResult
  /** The selected frontier point and its displayed result, if one is selected. */
  selectedPoint: { index: number; result: OptimiserSolveResult } | null
}

export default function ConvergenceChart({ solvedResult, selectedPoint }: ConvergenceChartProps) {
  const hist = solvedResult.history ?? []
  if (!hist.length) throw new Error("Convergence has no recorded history to show")
  const w = 400, h = 140, px = 6, py = 6
  const chartW = w - px * 2, chartH = h - py * 2

  // Each series has its own padded domain, so both lines use the full height.
  const [objLow, objHigh] = chartDomain(hist.map(e => e.total_objective))
  const [lcLow, lcHigh] = chartDomain(hist.map(e => e.max_lambda_change))

  const xScale = (i: number) => px + (i / Math.max(hist.length - 1, 1)) * chartW
  const yObj = (v: number) => py + chartH - ((v - objLow) / (objHigh - objLow)) * chartH
  const yLc = (v: number) => py + chartH - ((v - lcLow) / (lcHigh - lcLow)) * chartH

  const objPath = hist.map((e, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yObj(e.total_objective).toFixed(1)}`).join(" ")
  const lcPath = hist.map((e, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yLc(e.max_lambda_change).toFixed(1)}`).join(" ")

  return (
    <div className="flex gap-6 flex-wrap">
      {selectedPoint && (
        <p className="basis-full m-0 text-xs" style={{ color: "var(--text-secondary)" }}>
          {selectedPointConvergenceNote(selectedPoint.index, selectedPoint.result)}
        </p>
      )}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Convergence</label>
        <ChartSvg width={w} height={h} className="mt-1" ariaLabel="Objective and lambda change by iteration" style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
          <path d={objPath} fill="none" stroke={CHART_COLORS.objective} strokeWidth={1.5} />
          <path d={lcPath} fill="none" stroke={CHART_COLORS.lambdaChange} strokeWidth={1.5} />
        </ChartSvg>
        <div className="flex gap-3 mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
          <span><span style={{ color: CHART_COLORS.objective }}>--</span> Objective</span>
          <span><span style={{ color: CHART_COLORS.lambdaChange }}>--</span> Lambda change</span>
        </div>
      </div>

      {/* Iteration table */}
      <div className="min-w-[280px]">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Iterations</label>
        <div className="mt-1 max-h-48 overflow-y-auto">
          <div className="flex text-[10px] font-bold py-0.5 sticky top-0" style={{ color: "var(--text-muted)", background: "var(--bg-panel)" }}>
            <span className="w-8 text-center">#</span>
            <span className="flex-1 text-right">Objective</span>
            <span className="flex-1 text-right">Max dLambda</span>
            <span className="w-10 text-center">OK</span>
          </div>
          {hist.map(e => (
            <div key={e.iteration} className="flex text-[10px] font-mono py-0.5" style={{ color: "var(--text-secondary)" }}>
              <span className="w-8 text-center">{e.iteration}</span>
              <span className="flex-1 text-right">{formatNumber(e.total_objective)}</span>
              <span className="flex-1 text-right">{e.max_lambda_change.toExponential(2)}</span>
              <span className="w-10 text-center">{e.all_constraints_satisfied ? "Y" : "N"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
