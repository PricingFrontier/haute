/**
 * Detail card for the selected frontier point: its objective, feasibility
 * (haute's judgement, since the two modes mean different things by
 * `converged`), convergence and iterations, the constraint-attainment table
 * the Summary tab uses (bounds, achieved, slack, status, λ), and the discrete
 * objective change per unit of the x constraint to the next point of its
 * slice. Only the values: the reading of them is documented, not narrated.
 */

import type { ReactNode } from "react"
import type { FrontierPoint, OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import ConstraintAttainmentTable from "./ConstraintAttainmentTable"
import type { DiscreteTradeOff, FrontierPointAssessment } from "./frontierSlices"

/** The selected point's frontier row and haute's judgement of it. */
export interface DetailCardPoint {
  /** The point's global index. */
  index: number
  point: FrontierPoint
  /** The chart's x constraint, whose bound the trade-off relaxes. */
  xName: string
  assessment: FrontierPointAssessment
  tradeOff: DiscreteTradeOff
}

interface DetailCardProps {
  /** The displayed result, which for a selected point is that point's. */
  result: OptimiserSolveResult
  frontierPoint: DetailCardPoint
}

const LABEL_CLASS = "text-[11px] font-bold uppercase tracking-[0.08em]"

function formatSigned(value: number): string {
  const magnitude = Math.abs(value).toLocaleString("en-US", { maximumSignificantDigits: 6 })
  if (value > 0) return `+${magnitude}`
  if (value < 0) return `−${magnitude}`
  return magnitude
}

const ONLINE_NON_CONVERGENCE: Record<NonNullable<Extract<FrontierPoint, { mode: "online" }>["non_convergence_reason"]>, string> = {
  above_envelope: "bound beyond reach (λ search capped)",
  bracket_exhausted: "λ bracket exhausted",
  iteration_budget_exhausted: "max_iter reached",
}

/** Full precision to six decimals, grouped, as the attainment table prints bounds. */
function formatValue(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 })
}

/** The point's feasibility, with the unmet convergence or the breached bounds. */
function feasibilityText(point: FrontierPoint, assessment: FrontierPointAssessment): string {
  if (assessment.status === "feasible") return "Feasible"
  if (assessment.status === "not_converged") {
    if (point.mode === "ratebook") return "Not converged: factors still moving"
    const reason = point.non_convergence_reason
    return reason === null ? "Not converged" : `Not converged: ${ONLINE_NON_CONVERGENCE[reason]}`
  }
  const breaches = assessment.attainment
    .filter((row) => row.status === "breached")
    .map((row) => `${row.name} ${formatValue(row.achieved)} ${row.kind === "min" ? "<" : ">"} ${formatValue(row.bound)}`)
  return `Breached: ${breaches.join("; ")}`
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt style={{ color: "var(--text-muted)" }}>{label}</dt>
      <dd className="m-0" style={{ color: "var(--text-primary)" }}>{children}</dd>
    </>
  )
}

export default function DetailCard({ result, frontierPoint }: DetailCardProps) {
  const bounds = effectiveConstraintBounds(result)
  const { index, point, xName, assessment, tradeOff } = frontierPoint

  return (
    <div className="rounded-lg p-4 space-y-4" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
          Point {(index + 1).toLocaleString()}
        </span>
        <span className="text-base font-mono" style={{ color: "var(--text-primary)" }} aria-label="Objective">
          {formatValue(result.total_objective)}
        </span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm m-0">
        <Fact label="Feasibility">{feasibilityText(point, assessment)}</Fact>
        <Fact label="Converged">{point.converged ? "Yes" : "No"}</Fact>
        <Fact label={point.mode === "ratebook" ? "CD passes" : "Iterations"}>{point.iterations.toLocaleString()}</Fact>
        <Fact label={`Objective per unit ${xName}`}>
          {tradeOff.kind === "value" ? (
            <span className="font-mono">{formatSigned(tradeOff.value)} (to point {tradeOff.nextIndex + 1})</span>
          ) : (
            <span style={{ color: "var(--text-muted)" }}>— {tradeOff.reason}</span>
          )}
        </Fact>
      </dl>

      {Object.keys(bounds).length > 0 && (
        <div>
          <label className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-1">
            <ConstraintAttainmentTable bounds={bounds} achieved={result.constraints} lambdas={result.lambdas} size="sm" />
          </div>
        </div>
      )}
    </div>
  )
}
