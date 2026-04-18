import React, { useState, useEffect, useRef } from "react"
import { formatResultValue2dp, formatResultValueFull, tabularNums } from "./traceFormatting"

// ---------------------------------------------------------------------------
// Waterfall logic & rendering
// ---------------------------------------------------------------------------

export interface WaterfallStep {
  name: string
  factor: number
  runningValue: number
  prevValue: number
  direction: "positive" | "negative" | "neutral"
}

/**
 * Build waterfall steps from an expression of the form
 *   `a * b * c * ...` and a matching `inputValues` map.
 *
 * Returns `null` when the expression is not a multiplicative chain of 3+
 * factors or when any referenced factor is missing / non-numeric. This is
 * a "feature not applicable" signal, NOT a data-missing error — the caller
 * falls back to the unified-box renderer.
 */
export function buildWaterfallSteps(
  inputValues: Record<string, unknown>,
  expressionText: string,
): WaterfallStep[] | null {
  const parts = expressionText.split(/\s*\*\s*/)
  // Not applicable: need at least 3 factors for a meaningful waterfall.
  if (parts.length < 3) return null

  const names = parts.map((p) => p.trim())
  const allNumeric = names.every(
    (n) => n in inputValues && typeof inputValues[n] === "number",
  )
  // Not applicable: can't build a numeric waterfall from non-numeric factors.
  if (!allNumeric) return null

  const steps: WaterfallStep[] = []
  let running = inputValues[names[0]] as number

  steps.push({
    name: names[0],
    factor: running,
    runningValue: running,
    prevValue: 0,
    direction: "neutral",
  })

  for (let i = 1; i < names.length; i++) {
    const factor = inputValues[names[i]] as number
    const prev = running
    running = running * factor
    const dir =
      factor > 1 ? "positive" : factor < 1 ? "negative" : "neutral"
    steps.push({
      name: names[i],
      factor,
      runningValue: running,
      prevValue: prev,
      direction: dir,
    })
  }

  return steps
}

export interface WaterfallEntryProp {
  label: string
  operation: string
  value: number
  delta: number
  cumulative: number
}

export interface WaterfallErrorProp {
  error: string
  error_type?: string
}

/**
 * Resolve the raw `waterfall` prop (from the backend trace response) into a
 * structured `{ steps, error }` pair so the orchestrator doesn't need to
 * re-implement the type-narrowing logic. Returns `{ steps: null, error: null }`
 * when no waterfall is available and the caller should fall back to the
 * default unified-box layout.
 */
export function resolveWaterfallProp(
  waterfallProp: WaterfallEntryProp[] | WaterfallErrorProp | null | undefined,
): { steps: WaterfallStep[] | null; error: WaterfallErrorProp | null } {
  if (!waterfallProp) return { steps: null, error: null }
  if (!Array.isArray(waterfallProp)) {
    if ("error" in waterfallProp) return { steps: null, error: waterfallProp }
    return { steps: null, error: null }
  }
  if (waterfallProp.length < 3) return { steps: null, error: null }
  const steps = waterfallProp.map((entry, i) => {
    const prevCumulative = i > 0 ? waterfallProp[i - 1].cumulative : 0
    return {
      name: entry.label,
      factor: entry.value,
      runningValue: entry.cumulative,
      prevValue: prevCumulative,
      direction: (entry.delta > 0 ? "positive" : entry.delta < 0 ? "negative" : "neutral") as "positive" | "negative" | "neutral",
    }
  })
  return { steps, error: null }
}

/**
 * Inline alert shown when the backend reports the waterfall could not be
 * built. Surfaces the `{error, error_type}` payload loudly instead of
 * silently hiding the failure. Preserves the exact alert markup added in
 * Phase 1 so tests that target the alert's role/text keep passing.
 */
export const WaterfallErrorAlert: React.FC<{ error: string }> = ({ error }) => (
  <div style={{ marginTop: 4 }}>
    <div
      role="alert"
      style={{
        padding: "8px 12px",
        border: "1px solid var(--accent-error, #d97706)",
        borderRadius: 4,
        background: "var(--bg-error-subtle, rgba(217, 119, 6, 0.08))",
        color: "var(--text-error, #b45309)",
        fontSize: 12,
      }}
    >
      <strong>Waterfall could not be built:</strong> {error}
    </div>
  </div>
)

export interface WaterfallChartProps {
  steps: WaterfallStep[]
  resultValue: unknown
}

/**
 * Visual waterfall chart: one animated horizontal bar per factor plus a
 * prominent total row at the bottom. Extracted from CalculationHero as part
 * of the 2B-2 split.
 */
const WaterfallChart: React.FC<WaterfallChartProps> = ({ steps, resultValue }) => {
  const maxVal = Math.max(
    ...steps.map((s) => Math.abs(s.runningValue)),
    1,
  )
  const formatted = formatResultValue2dp(resultValue)
  const formattedFull = formatResultValueFull(resultValue)
  const isNull = resultValue === null || resultValue === undefined

  // Animated waterfall bars: grow from 0 on first mount.
  const [animated, setAnimated] = useState(false)
  const mountedRef = useRef(false)
  const rafRef = useRef<number>(0)

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true
      rafRef.current = requestAnimationFrame(() => {
        setAnimated(true)
      })
    }
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return (
    <div className="waterfall-display">
      {steps.map((step, idx) => {
        const barWidth = Math.max(
          (Math.abs(step.runningValue) / maxVal) * 100,
          2,
        )
        const isLast = idx === steps.length - 1
        return (
          <div
            key={idx}
            className={`waterfall-bar ${step.direction === "positive" ? "positive increase" : step.direction === "negative" ? "negative decrease" : "neutral"}`}
            data-testid="waterfall-bar"
            data-direction={step.direction}
            style={{
              display: "flex",
              alignItems: "center",
              marginBottom: 4,
            }}
          >
            <span
              className="waterfall-label"
              style={{ minWidth: 120, fontSize: 12 }}
            >
              {step.name}
            </span>
            <span
              className="waterfall-factor"
              style={{ minWidth: 50, fontSize: 12, ...tabularNums }}
            >
              {idx === 0
                ? String(step.factor)
                : `\u00d7${step.factor}`}
            </span>
            <div
              style={{
                height: 16,
                width: animated ? `${barWidth}%` : "0%",
                backgroundColor:
                  step.direction === "positive"
                    ? "var(--color-positive, #4caf50)"
                    : step.direction === "negative"
                      ? "var(--color-negative, #f44336)"
                      : "var(--color-neutral, #9e9e9e)",
                borderRadius: 2,
                minWidth: animated ? 2 : 0,
                transition: `width 400ms cubic-bezier(0.22, 1, 0.36, 1)`,
                transitionDelay: `${idx * 60}ms`,
              }}
            />
            {!isLast && (
              <span style={{ marginLeft: 4, fontSize: 12, ...tabularNums }}>
                {typeof step.runningValue === "number"
                  ? step.runningValue.toFixed(1)
                  : String(step.runningValue)}
              </span>
            )}
          </div>
        )
      })}
      <div
        className="waterfall-total total final"
        data-testid="waterfall-total"
        style={{ fontWeight: "bold", marginTop: 4 }}
      >
        <span
          className={
            isNull
              ? "result-value muted null-value"
              : "result-value accent"
          }
          data-accent={!isNull || undefined}
          data-muted={isNull || undefined}
          style={{
            ...tabularNums,
            ...(isNull ? { opacity: 0.5 } : {}),
          }}
          title={formattedFull !== formatted ? formattedFull : undefined}
        >
          {formatted}
        </span>
      </div>
    </div>
  )
}

export default WaterfallChart
