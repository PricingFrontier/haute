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
import { isConstraintMet } from "./optimiserHelpers"

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function optionalPointNumber(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null
  if (typeof value === "number" && Number.isFinite(value)) return value
  throw new Error(`Invalid frontier point field ${field}: expected a finite number`)
}

function requiredPointNumber(value: unknown, field: string): number {
  const parsed = optionalPointNumber(value, field)
  if (parsed === null) {
    throw new Error(`Invalid frontier point: missing numeric ${field}`)
  }
  return parsed
}

function nestedPointNumber(
  point: Record<string, unknown>,
  nestedKey: string,
  name: string,
): number | null {
  const nested = point[nestedKey]
  if (nested === undefined || nested === null) return null
  if (!isRecord(nested)) {
    throw new Error(`Invalid frontier point field ${nestedKey}: expected an object`)
  }
  return optionalPointNumber(nested[name], `${nestedKey}.${name}`)
}

function frontierPointNumber(
  point: Record<string, unknown>,
  flatKey: string,
  nestedKey?: string,
  nestedName?: string,
  alternateFlatKey?: string,
): number {
  const flat = optionalPointNumber(point[flatKey], flatKey)
  if (flat !== null) return flat
  if (nestedKey && nestedName) {
    const nested = nestedPointNumber(point, nestedKey, nestedName)
    if (nested !== null) return nested
  }
  if (alternateFlatKey) {
    const alternate = optionalPointNumber(point[alternateFlatKey], alternateFlatKey)
    if (alternate !== null) return alternate
  }
  throw new Error(`Invalid frontier point: missing numeric ${flatKey}`)
}

function frontierLambdaEntries(point: Record<string, unknown>): Array<[string, number]> {
  if (point.lambdas !== undefined && point.lambdas !== null) {
    if (!isRecord(point.lambdas)) {
      throw new Error("Invalid frontier point field lambdas: expected an object")
    }
    return Object.entries(point.lambdas).map(([name, value]) => [
      name,
      requiredPointNumber(value, `lambdas.${name}`),
    ])
  }

  return Object.keys(point)
    .filter(k => k.startsWith("lambda_"))
    .map((key) => [key.replace(/^lambda_/, ""), frontierPointNumber(point, key)])
}

interface DetailCardProps {
  points: Record<string, unknown>[]
  selectedIdx: number
  constraints: Record<string, Record<string, number>>
  constraintNames: string[]
  onStepPoint: (delta: number) => void
  onSave: () => void
  onLogMlflow: () => void
  saving: boolean
  logging: boolean
  terminalActionsDisabled?: boolean
  mlflowAvailable: boolean
  actionMsg: string | null
}

export default function DetailCard({
  points,
  selectedIdx,
  constraints,
  constraintNames,
  onStepPoint,
  onSave,
  onLogMlflow,
  saving,
  logging,
  terminalActionsDisabled = false,
  mlflowAvailable,
  actionMsg,
}: DetailCardProps) {
  const point = points[selectedIdx]
  if (!point) return null

  const objValue = frontierPointNumber(point, "total_objective")
  const actionsDisabled = saving || logging || terminalActionsDisabled

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
        </div>
      </div>

      {/* Constraints */}
      {constraintNames.length > 0 && (
        <div>
          <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
          <div className="mt-0.5 space-y-0.5">
            {constraintNames.map(name => {
              const totalKey = `total_${name}`
              const value = frontierPointNumber(point, totalKey, "constraints", name, name)
              const spec = constraints[name] || {}
              const thresholdType = Object.keys(spec)[0]
              const thresholdVal = spec[thresholdType] ?? 0
              const met = isConstraintMet(thresholdType, 0, value, thresholdVal)
              return (
                <div key={name} className="flex items-center justify-between text-xs font-mono gap-2">
                  <span className="flex items-center gap-1.5">
                    <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: met ? "var(--success)" : "var(--danger)" }} />
                    <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                  </span>
                  <span>
                    <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Lambdas */}
      {(() => {
        const lambdaEntries = frontierLambdaEntries(point)
        if (lambdaEntries.length === 0) return null
        return (
          <div>
            <label className="text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Lambdas</label>
            <div className="mt-0.5 space-y-0.5">
              {lambdaEntries.map(([displayName, v]) => {
                return (
                  <div key={displayName} className="flex justify-between text-xs font-mono gap-2">
                    <span style={{ color: "var(--text-secondary)" }}>{displayName}</span>
                    <span style={{ color: "var(--text-primary)" }}>{v.toFixed(6)}</span>
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
          disabled={actionsDisabled}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
          style={{
            background: actionsDisabled ? "var(--chrome-hover)" : "var(--warning-soft-emphasis)",
            color: actionsDisabled ? "var(--text-muted)" : "var(--warning-strong)",
            border: "1px solid var(--warning-border-strong)",
          }}
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save Result
        </button>
        {mlflowAvailable && (
          <button
            onClick={onLogMlflow}
            disabled={actionsDisabled}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors"
            style={{
              background: actionsDisabled ? "var(--chrome-hover)" : MODEL_COLORS.accentSoft,
              color: actionsDisabled ? "var(--text-muted)" : MODEL_COLORS.accent,
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
