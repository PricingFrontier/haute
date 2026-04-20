import React, { useState, useEffect, useRef } from "react"
import { formatResultValue2dp, formatResultValueFull, tabularNums } from "./traceFormatting"
import type { WaterfallStep } from "./traceHelpers"

// ---------------------------------------------------------------------------
// Waterfall rendering.  Pure helpers and data types live in ./traceHelpers
// so this file only exports components (satisfies
// `react-refresh/only-export-components`).
// ---------------------------------------------------------------------------

export interface WaterfallChartProps {
  steps: WaterfallStep[]
  resultValue: unknown
}

export interface WaterfallErrorAlertProps {
  /**
   * Error message from the backend. When empty, the component still renders
   * its own header so the user knows the waterfall failed — silent empty
   * alerts would themselves be a misleading null.
   */
  error: string
}

/**
 * Accessible error alert for waterfall build failures. The backend emits a
 * structured `{ error, error_type }` payload when it cannot construct a
 * waterfall; rather than silently dropping the waterfall pane we surface the
 * error message here with `role="alert"` so screen readers announce it.
 */
export const WaterfallErrorAlert: React.FC<WaterfallErrorAlertProps> = ({ error }) => {
  const hasMessage = error.trim().length > 0
  return (
    <div
      role="alert"
      className="waterfall-error-alert"
      style={{
        padding: "8px 12px",
        border: "1px solid var(--accent-error, #d97706)",
        borderRadius: 4,
        background: "var(--bg-error-subtle, rgba(217, 119, 6, 0.08))",
        color: "var(--text-error, #b45309)",
        fontSize: 12,
        marginTop: 4,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: hasMessage ? 2 : 0 }}>
        Waterfall error
      </div>
      {hasMessage ? (
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {error}
        </div>
      ) : (
        <div style={{ fontStyle: "italic" }}>
          No details were provided by the backend.
        </div>
      )}
    </div>
  )
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
