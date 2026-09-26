/**
 * Constraint | Kind | Bound | Achieved | Slack | Status | λ for one optimiser
 * result, shared by the Summary tab and the frontier detail card so the two can
 * never judge a constraint differently. Status is stated in words with an icon;
 * colour only reinforces it.
 */

import { CheckCircle2, XCircle } from "lucide-react"
import type { OptimiserEffectiveBound } from "../../api/types"
import Tooltip from "../../components/Tooltip"
import { constraintAttainmentRows, type AttainmentStatus } from "./constraintAttainment"
import { LAMBDA_HELP, LAMBDA_LABEL } from "./lambdaCopy"

/** Full precision to four decimals, grouped: a compact "1.01M" could hide a breach. */
function formatAttainmentValue(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 })
}

function formatSigned(text: string, value: number): string {
  return value > 0 ? `+${text}` : text
}

function formatSlack(slack: number, slackPct: number | null): string {
  const absolute = formatSigned(formatAttainmentValue(slack), slack)
  if (slackPct === null) return absolute
  const pct = slackPct.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${absolute} (${formatSigned(pct, slackPct)}%)`
}

const STATUS_COPY: Record<AttainmentStatus, { label: string; color: string; Icon: typeof CheckCircle2 }> = {
  met: { label: "Met", color: "var(--success)", Icon: CheckCircle2 },
  breached: { label: "Breached", color: "var(--danger)", Icon: XCircle },
}

interface ConstraintAttainmentTableProps {
  /** The displayed result's backend bounds, from `effectiveConstraintBounds`. */
  bounds: Record<string, OptimiserEffectiveBound>
  achieved: Record<string, number>
  lambdas: Record<string, number>
}

const HEADER_CLASS = "px-1.5 py-1 text-left text-[10px] font-bold uppercase tracking-[0.06em] whitespace-nowrap"
const CELL_CLASS = "px-1.5 py-0.5 whitespace-nowrap"

export default function ConstraintAttainmentTable({ bounds, achieved, lambdas }: ConstraintAttainmentTableProps) {
  const rows = constraintAttainmentRows({ bounds, achieved, lambdas })
  if (rows.length === 0) return null

  return (
    <div className="overflow-x-auto">
      <table aria-label="Constraint attainment" className="text-xs font-mono border-collapse">
        <thead style={{ color: "var(--text-muted)" }}>
          <tr>
            <th scope="col" className={HEADER_CLASS}>Constraint</th>
            <th scope="col" className={HEADER_CLASS}>Kind</th>
            <th scope="col" className={`${HEADER_CLASS} text-right`}>Bound</th>
            <th scope="col" className={`${HEADER_CLASS} text-right`}>Achieved</th>
            <th scope="col" className={`${HEADER_CLASS} text-right`}>Slack</th>
            <th scope="col" className={HEADER_CLASS}>Status</th>
            <th scope="col" className={`${HEADER_CLASS} text-right`}>
              <Tooltip label={LAMBDA_HELP}>
                <span className="cursor-help">{LAMBDA_LABEL}</span>
              </Tooltip>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const status = STATUS_COPY[row.status]
            return (
              <tr key={row.name} style={{ borderTop: "1px solid var(--border)" }}>
                <th scope="row" className={`${CELL_CLASS} text-left font-normal`} style={{ color: "var(--text-secondary)" }}>
                  {row.name}
                </th>
                <td className={CELL_CLASS} style={{ color: "var(--text-secondary)" }}>{row.kind}</td>
                <td className={`${CELL_CLASS} text-right`} style={{ color: "var(--text-primary)" }}>
                  {formatAttainmentValue(row.bound)}
                </td>
                <td className={`${CELL_CLASS} text-right`} style={{ color: "var(--text-primary)" }}>
                  {formatAttainmentValue(row.achieved)}
                </td>
                <td className={`${CELL_CLASS} text-right`} style={{ color: "var(--text-primary)" }}>
                  {formatSlack(row.slack, row.slackPct)}
                </td>
                <td className={CELL_CLASS}>
                  <span className="inline-flex items-center gap-1" style={{ color: status.color }}>
                    <status.Icon size={12} aria-hidden="true" className="shrink-0" />
                    <span style={{ color: "var(--text-primary)" }}>{status.label}</span>
                  </span>
                </td>
                <td className={`${CELL_CLASS} text-right`} style={{ color: "var(--text-primary)" }}>
                  {row.lambda.toFixed(6)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
