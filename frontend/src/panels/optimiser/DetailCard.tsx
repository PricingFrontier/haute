/**
 * Detail card for a selected frontier point.
 *
 * Shows the point's objective, constraints, and lambdas, and offers
 * Save / Log-to-MLflow actions for the currently selected frontier
 * trade-off.  Extracted from OptimiserPreview as part of the
 * god-component split.
 */

import { ChevronLeft, ChevronRight, Loader2, Save, Upload } from "lucide-react"
import { MODEL_COLORS } from "../../theme/colors"
import { formatNumber } from "../../utils/formatValue"
import type { SolveResult } from "../OptimiserPreview"
import { isConstraintMet } from "./optimiserHelpers"

interface DetailCardProps {
  points: Record<string, unknown>[]
  selectedIdx: number
  result: SolveResult
  constraints: Record<string, Record<string, number>>
  constraintNames: string[]
  onStepPoint: (delta: number) => void
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
}

export default function DetailCard({
  points,
  selectedIdx,
  result,
  constraints,
  constraintNames,
  onStepPoint,
  onSave,
  onLogMlflow,
  saving,
  logging,
  mlflowAvailable,
  actionMsg,
}: DetailCardProps) {
  const point = points[selectedIdx]
  if (!point) return null

  const objValue = Number(point.total_objective ?? 0)
  const baselineObj = result.baseline_objective
  const objVsBaseline = baselineObj !== 0 ? ((objValue / baselineObj - 1) * 100) : null

  return (
    <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      {/* Header with stepper */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>
          Point {selectedIdx + 1} of {points.length}
        </span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => onStepPoint(-1)}
            disabled={selectedIdx <= 0}
            aria-label="Previous point"
            className="p-0.5 rounded transition-colors"
            style={{ color: selectedIdx <= 0 ? "var(--text-muted)" : "var(--text-secondary)", opacity: selectedIdx <= 0 ? 0.4 : 1 }}
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => onStepPoint(1)}
            disabled={selectedIdx >= points.length - 1}
            aria-label="Next point"
            className="p-0.5 rounded transition-colors"
            style={{ color: selectedIdx >= points.length - 1 ? "var(--text-muted)" : "var(--text-secondary)", opacity: selectedIdx >= points.length - 1 ? 0.4 : 1 }}
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      {/* Objective */}
      <div>
        <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
        <div className="mt-0.5 flex items-baseline justify-between text-xs font-mono gap-2">
          <span style={{ color: "var(--text-primary)" }}>{formatNumber(objValue)}</span>
          {objVsBaseline != null && (
            <span style={{ color: "var(--warning-strong)" }}>{objVsBaseline >= 0 ? "+" : ""}{objVsBaseline.toFixed(2)}% vs baseline</span>
          )}
        </div>
      </div>

      {/* Constraints */}
      {constraintNames.length > 0 && (
        <div>
          <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-0.5 space-y-0.5">
            {constraintNames.map(name => {
              const totalKey = `total_${name}`
              const value = Number(point[totalKey] ?? 0)
              const baseline = result.baseline_constraints[name]
              const ratio = baseline ? value / baseline : 0
              const spec = constraints[name] || {}
              const thresholdType = Object.keys(spec)[0]
              const thresholdVal = spec[thresholdType] ?? 0
              const met = isConstraintMet(thresholdType, ratio, value, thresholdVal)
              return (
                <div key={name} className="flex items-center justify-between text-xs font-mono gap-2">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: met ? "var(--success)" : "var(--danger)" }} />
                    <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                  </span>
                  <span>
                    <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
                    {baseline != null && baseline !== 0 && (
                      <span style={{ color: "var(--text-muted)" }}> ({(ratio * 100).toFixed(1)}%)</span>
                    )}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Lambdas */}
      {(() => {
        const lambdaKeys = Object.keys(point).filter(k => k.startsWith("lambda_"))
        if (lambdaKeys.length === 0) return null
        return (
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Lambdas</label>
            <div className="mt-0.5 space-y-0.5">
              {lambdaKeys.map(k => {
                const displayName = k.replace(/^lambda_/, "")
                const v = point[k] as number
                return (
                  <div key={k} className="flex justify-between text-xs font-mono gap-2">
                    <span style={{ color: "var(--text-secondary)" }}>{displayName}</span>
                    <span style={{ color: "var(--text-primary)" }}>{typeof v === "number" ? v.toFixed(6) : String(v)}</span>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })()}

      {/* Action buttons */}
      <div className="flex gap-2 pt-1" style={{ borderTop: "1px solid var(--border)" }}>
        <button
          onClick={onSave}
          disabled={saving || logging}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: saving || logging ? "var(--chrome-hover)" : "var(--warning-soft-emphasis)",
            color: saving || logging ? "var(--text-muted)" : "var(--warning-strong)",
            border: "1px solid var(--warning-border-strong)",
          }}
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save Result
        </button>
        {mlflowAvailable && (
          <button
            onClick={onLogMlflow}
            disabled={saving || logging}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
            style={{
              background: saving || logging ? "var(--chrome-hover)" : MODEL_COLORS.accentSoft,
              color: saving || logging ? "var(--text-muted)" : MODEL_COLORS.accent,
              border: `1px solid ${MODEL_COLORS.accentSoft}`,
            }}
          >
            {logging ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Log to MLflow
          </button>
        )}
      </div>

      {/* Action feedback */}
      {actionMsg && (
        <div className="text-[10px] font-mono px-1" style={{ color: "var(--text-muted)", wordBreak: "break-all" }}>
          {actionMsg}
        </div>
      )}
    </div>
  )
}
