/**
 * Detail card for the selected frontier point.
 *
 * Shows the displayed result (the selected point's server summary over the
 * solve) with the same constraint-attainment table as the Summary tab, judged
 * against the bounds the point was solved at; then the point's own facts from
 * its typed frontier row: feasibility with its reason (haute's judgement,
 * since the two modes mean different things by `converged`), convergence and
 * iterations, each λ exactly as the solver reported it with the sign it enters
 * each quote's choice with, and the discrete trade-off to the next point of its
 * slice. Publishing the selected point happens in the node's Export pane,
 * which this card names.
 */

import type { ReactNode } from "react"
import { formatNumber } from "../../utils/formatValue"
import type { FrontierPoint, OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import ConstraintAttainmentTable from "./ConstraintAttainmentTable"
import type { ConstraintKinds, DiscreteTradeOff, FrontierPointAssessment } from "./frontierSlices"

/** The selected point's frontier row and haute's judgement of it. */
export interface DetailCardPoint {
  /** The point's global index. */
  index: number
  point: FrontierPoint
  kinds: ConstraintKinds
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

const LABEL_CLASS = "text-[10px] font-bold uppercase tracking-[0.08em]"

/** Full precision to six decimals, grouped, as the attainment table prints bounds. */
function formatValue(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 })
}

function formatSigned(value: number): string {
  const magnitude = Math.abs(value).toLocaleString("en-US", { maximumSignificantDigits: 6 })
  if (value > 0) return `+${magnitude}`
  if (value < 0) return `−${magnitude}`
  return magnitude
}

const ONLINE_NON_CONVERGENCE: Record<NonNullable<Extract<FrontierPoint, { mode: "online" }>["non_convergence_reason"]>, string> = {
  above_envelope:
    "the bound lies beyond what the solver can reach (the λ search hit its cap); this is the closest point it found",
  bracket_exhausted:
    "the λ search ran out of bracket doublings before reaching the bound",
  iteration_budget_exhausted:
    "the multipliers did not settle within the iteration budget (max_iter)",
}

function breachText(assessment: FrontierPointAssessment): string {
  return assessment.attainment
    .filter((row) => row.status === "breached")
    .map((row) => row.kind === "min"
      ? `${row.name} ${formatValue(row.achieved)} is below its minimum ${formatValue(row.bound)}`
      : `${row.name} ${formatValue(row.achieved)} is above its maximum ${formatValue(row.bound)}`)
    .join("; ")
}

/** What the point's feasibility is and why, naming which kind of convergence failed or which bound is breached. */
function feasibilityText(point: FrontierPoint, assessment: FrontierPointAssessment): string {
  if (assessment.status === "feasible") return "Feasible: converged and every bound met"
  if (assessment.status === "not_converged") {
    if (point.mode === "ratebook") {
      return "Not converged: the factor values were still moving when coordinate descent stopped"
    }
    const reason = point.non_convergence_reason
    return reason === null
      ? "Not converged: the multipliers did not converge with every constraint met within the solver's tolerance"
      : `Not converged: ${ONLINE_NON_CONVERGENCE[reason]}`
  }
  const convergence = point.mode === "ratebook"
    ? "coordinate descent converged (the factor values stopped moving), which does not check bounds;"
    : "the multipliers converged within the solver's tolerance, but"
  return `Breached: ${convergence} ${breachText(assessment)}`
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
  const { index, point, kinds, xName, assessment, tradeOff } = frontierPoint

  return (
    <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>
          Point details
        </span>
      </div>

      <div>
        <label className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Objective</label>
        <div className="mt-0.5 flex items-baseline justify-between text-xs font-mono gap-2">
          <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
        </div>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs m-0">
        <Fact label="Point">{(index + 1).toLocaleString()}</Fact>
        <Fact label="Feasibility">{feasibilityText(point, assessment)}</Fact>
        <Fact label="Converged">{point.converged ? "Yes" : "No"}</Fact>
        <Fact label={point.mode === "ratebook" ? "CD passes" : "Iterations"}>{point.iterations.toLocaleString()}</Fact>
      </dl>

      {Object.keys(bounds).length > 0 && (
        <div>
          <label className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-0.5">
            <ConstraintAttainmentTable bounds={bounds} achieved={result.constraints} lambdas={result.lambdas} />
          </div>
        </div>
      )}

      <div>
        <label className={LABEL_CLASS} style={{ color: "var(--text-muted)" }}>λ as reported</label>
        <ul aria-label="Multipliers as reported" className="mt-0.5 space-y-0.5 text-xs m-0 p-0 list-none" style={{ color: "var(--text-secondary)" }}>
          {Object.entries(point.lambdas).map(([name, lambda]) => {
            const kind = kinds[name]
            if (kind === undefined) throw new Error(`No constraint kind for frontier constraint ${name}`)
            return (
              <li key={name}>
                {name} ({kind}): λ = <span className="font-mono" style={{ color: "var(--text-primary)" }}>{String(lambda)}</span>
                , entering each quote's choice as {kind === "min" ? "+" : "−"}λ × {name}
              </li>
            )
          })}
        </ul>
      </div>

      <dl className="text-xs m-0 space-y-0.5">
        <Fact label={`Objective change per unit of ${xName} bound relaxed, to the next point in this slice`}>
          {tradeOff.kind === "value" ? (
            <span className="font-mono">{formatSigned(tradeOff.value)} (to point {tradeOff.nextIndex + 1})</span>
          ) : (
            <>
              <span className="font-mono">—</span>{" "}
              <span style={{ color: "var(--text-muted)" }}>{tradeOff.reason}</span>
            </>
          )}
        </Fact>
        <dd className="m-0" style={{ color: "var(--text-muted)" }}>
          A discrete step across the frontier, in which other achieved totals may also move. It is not a check of λ.
        </dd>
      </dl>

      <p className="text-[10px] pt-1" style={{ color: "var(--text-muted)", borderTop: "1px solid var(--border)" }}>
        Save or log this point from the node's Export pane.
      </p>
    </div>
  )
}
