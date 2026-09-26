/**
 * Summary tab for the optimiser preview.
 *
 * Renders the diagnostics the solve could not produce, the objective, the
 * constraint-attainment table (with λ, for online and ratebook results alike)
 * and, where the workspace offers the Adjustments tab, a compact summary of the
 * adjustments (up, down, unadjusted, at the range edge) linking to it.
 */

import { Loader2 } from "lucide-react"
import { formatNumber } from "../../utils/formatValue"
import type { OptimiserDiagnosticError, OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import RatebookImpactBeeswarm from "./RatebookImpactBeeswarm"
import { hasFactorTables } from "./ratebookFactorTables"
import ConstraintAttainmentTable from "./ConstraintAttainmentTable"
import DiagnosticsIssues from "../DiagnosticsIssues"
import { DEPLOYED_FACTOR_DIFFERS_LABEL, NO_UNADJUSTED_NOTE, formatShare } from "./adjustments"

type RatebookRatesLoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: string }

interface SummaryTabProps {
  /** The displayed result: the selected frontier point's, else the solve's. */
  result: OptimiserSolveResult
  /** The selected frontier point, or null for the as-solved result. */
  selectedPointIndex: number | null
  canMaterialiseRatebookRates?: boolean
  ratebookRatesDetail?: RatebookRatesLoadState
  /** Opens the Adjustments tab; given only where the workspace offers it. */
  onOpenAdjustments?: () => void
}

export default function SummaryTab({
  result,
  selectedPointIndex,
  canMaterialiseRatebookRates = false,
  ratebookRatesDetail = { status: "idle" },
  onOpenAdjustments,
}: SummaryTabProps) {
  const bounds = effectiveConstraintBounds(result)
  const showRatebookImpactStatus = (
    result.mode === "ratebook"
    && canMaterialiseRatebookRates
    && !hasFactorTables(result.factor_tables)
  )

  return (
    <div className="space-y-3">
      <DiagnosticsIssues
        issues={result.diagnostics_errors.map((diagnosticError) => ({
          diagnostic: diagnosticError.diagnostic,
          errorType: diagnosticError.error_type,
          message: diagnosticError.message,
        }))}
        formatLabel={formatOptimiserDiagnostic}
      />
      <div className="flex gap-6 flex-wrap">
        {/* Left column: objective + constraints */}
        <div className="space-y-3 min-w-[200px]">
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
            <div className="mt-1 space-y-0.5">
              <div className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>Optimised</span>
                <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
              </div>
            </div>
          </div>

          {Object.keys(bounds).length > 0 && (
            <div>
              <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
              <div className="mt-1">
                <ConstraintAttainmentTable
                  bounds={bounds}
                  achieved={result.constraints}
                  lambdas={result.lambdas}
                />
              </div>
            </div>
          )}

          {result.mode === "ratebook" && result.clamp_rate != null && (
            <div className="flex justify-between text-xs font-mono">
              <span style={{ color: "var(--text-muted)" }}>Clamp rate</span>
              <span style={{ color: "var(--warning-strong)" }}>{(result.clamp_rate * 100).toFixed(1)}%</span>
            </div>
          )}

        </div>

        {result.mode === "ratebook" && (
          <RatebookImpactBeeswarm factorTables={result.factor_tables} />
        )}
        {showRatebookImpactStatus && (
          <RatebookImpactStatus detail={ratebookRatesDetail} />
        )}

        {onOpenAdjustments && (
          <AdjustmentsSummary
            result={result}
            selectedPointIndex={selectedPointIndex}
            onOpenAdjustments={onOpenAdjustments}
          />
        )}
      </div>
    </div>
  )
}

const OPTIMISER_DIAGNOSTIC_LABELS: Record<OptimiserDiagnosticError["diagnostic"], string> = {
  adjustments: "Adjustment report",
  adjustment_weight: "Adjustment weighting",
  frontier: "Efficient frontier",
  segment_weight: "Segment weighting",
}

function formatOptimiserDiagnostic(diagnostic: string): string {
  const label = OPTIMISER_DIAGNOSTIC_LABELS[diagnostic as OptimiserDiagnosticError["diagnostic"]]
  if (!label) throw new Error(`Unknown optimiser diagnostic "${diagnostic}"`)
  return label
}

/** The displayed result's adjustments by quote count, with a way into the Adjustments tab. */
function AdjustmentsSummary({
  result,
  selectedPointIndex,
  onOpenAdjustments,
}: {
  result: OptimiserSolveResult
  selectedPointIndex: number | null
  onOpenAdjustments: () => void
}) {
  // A selected point's displayed result has no report (its summary removes the
  // solve's); the Adjustments tab loads the point's own.
  const report = selectedPointIndex === null ? result.adjustments : null
  // Quote count always weighs a report, first.
  const quotes = report == null ? null : report.weightings[0]
  return (
    <section role="group" aria-label="Adjustments" className="min-w-[200px] space-y-1">
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Adjustments</label>
      {selectedPointIndex !== null ? (
        <p className="m-0 text-xs" style={{ color: "var(--text-secondary)" }}>
          {`Frontier point ${selectedPointIndex + 1}'s adjustments load in the Adjustments tab.`}
        </p>
      ) : report != null && quotes !== null ? (
        <>
          <dl className="m-0 space-y-0.5 text-xs font-mono">
            {([
              ["Adjusted up", quotes.share_up],
              ["Adjusted down", quotes.share_down],
              ...(quotes.share_unadjusted === null ? [] : [["Unadjusted", quotes.share_unadjusted] as const]),
              ["At the range edge", quotes.share_at_min + quotes.share_at_max],
            ] as const).map(([label, share]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt style={{ color: "var(--text-secondary)" }}>{label}</dt>
                <dd className="m-0" style={{ color: "var(--text-primary)" }}>{formatShare(share)}</dd>
              </div>
            ))}
            {report.deployed_factor_differs !== null && (
              <div className="flex justify-between gap-4" title={DEPLOYED_FACTOR_DIFFERS_LABEL}>
                <dt style={{ color: "var(--text-secondary)" }}>Deployed ≠ evaluated step</dt>
                <dd className="m-0" style={{ color: "var(--text-primary)" }}>
                  {`${report.deployed_factor_differs.toLocaleString()} quotes`}
                </dd>
              </div>
            )}
          </dl>
          {!report.has_unadjusted && (
            <p className="m-0 text-[11px]" style={{ color: "var(--text-muted)" }}>{NO_UNADJUSTED_NOTE}</p>
          )}
        </>
      ) : (
        <p className="m-0 text-xs" style={{ color: "var(--text-muted)" }}>
          No adjustment report: see the diagnostic issues above.
        </p>
      )}
      <button
        type="button"
        onClick={onOpenAdjustments}
        className="validation-control text-xs"
      >
        View adjustments
      </button>
    </section>
  )
}

function RatebookImpactStatus({ detail }: { detail: RatebookRatesLoadState }) {
  const isError = detail.status === "error"
  return (
    <section
      className="min-w-[280px] flex-1 rounded px-3 py-2 text-xs"
      style={{
        background: isError ? "var(--danger-soft)" : "var(--bg-input)",
        border: `1px solid ${isError ? "var(--danger-border)" : "var(--border)"}`,
        color: isError ? "var(--danger)" : "var(--text-muted)",
      }}
    >
      <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.08em]">
        Mechanical Price Effect
      </div>
      <div className="flex items-center gap-2">
        {!isError && <Loader2 size={14} className="animate-spin shrink-0" />}
        <span>
          {isError
            ? `Rate table load failed: ${detail.error}`
            : "Materialising selected point rates..."}
        </span>
      </div>
    </section>
  )
}
