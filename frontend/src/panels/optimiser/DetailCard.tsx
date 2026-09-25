/**
 * Detail card for the selected frontier point.
 *
 * Shows the displayed result (the selected point's server summary over the
 * solve) with the same constraint-attainment table as the Summary tab, judged
 * against the bounds the point was solved at. Publishing the selected point
 * happens in the node's Export pane, which this card names.
 */

import { formatNumber } from "../../utils/formatValue"
import type { OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import ConstraintAttainmentTable from "./ConstraintAttainmentTable"

interface DetailCardProps {
  /** The displayed result, which for a selected point is that point's. */
  result: OptimiserSolveResult
}

export default function DetailCard({ result }: DetailCardProps) {
  const bounds = effectiveConstraintBounds(result)

  return (
    <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>
          Point details
        </span>
      </div>

      <div>
        <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
        <div className="mt-0.5 flex items-baseline justify-between text-xs font-mono gap-2">
          <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
        </div>
      </div>

      {Object.keys(bounds).length > 0 && (
        <div>
          <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-0.5">
            <ConstraintAttainmentTable bounds={bounds} achieved={result.constraints} lambdas={result.lambdas} />
          </div>
        </div>
      )}

      <p className="text-[10px] pt-1" style={{ color: "var(--text-muted)", borderTop: "1px solid var(--border)" }}>
        Save or log this point from the node's Export pane.
      </p>
    </div>
  )
}
