/**
 * Detail card for a selected frontier point.
 *
 * Shows the point's objective, constraints, and lambdas. Publishing the
 * selected point happens in the node's Export pane, which this card names.
 */

import { formatNumber } from "../../utils/formatValue"
import { isConstraintMet } from "./optimiserHelpers"
import { LAMBDA_HELP, LAMBDA_LABEL } from "./lambdaCopy"
import Tooltip from "../../components/Tooltip"
import { isPlainObject } from "../../types/guards"

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
  if (!isPlainObject(nested)) {
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
    if (!isPlainObject(point.lambdas)) {
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
}

export default function DetailCard({
  points,
  selectedIdx,
  constraints,
  constraintNames,
}: DetailCardProps) {
  const point = points[selectedIdx]
  if (!point) return null

  const objValue = frontierPointNumber(point, "total_objective")

  return (
    <div className="rounded-lg p-3 space-y-3" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>
          Point details
        </span>
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
              // Each point was solved at its own swept bound, not the solved result's.
              const pointThreshold = optionalPointNumber(point[`threshold_${name}`], `threshold_${name}`)
              const thresholdVal = pointThreshold ?? spec[thresholdType] ?? 0
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
            <Tooltip label={LAMBDA_HELP}>
              <label className="text-[10px] font-bold uppercase tracking-[0.08em] cursor-help" style={{ color: "var(--text-muted)" }}>{LAMBDA_LABEL}</label>
            </Tooltip>
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

      <p className="text-[10px] pt-1" style={{ color: "var(--text-muted)", borderTop: "1px solid var(--border)" }}>
        Save or log this point from the node's Export pane.
      </p>
    </div>
  )
}
