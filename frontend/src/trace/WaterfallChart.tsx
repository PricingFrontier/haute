import React, { useState, useEffect, useRef } from "react"
import { formatResultValueFull, formatSmartValue, tabularNums } from "./traceFormatting"
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
  const formatted = formatSmartValue(resultValue)
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
              {step.defaultUsed && (
                <span
                  className="ml-1 rounded px-1 py-0.5 text-[9px] font-semibold"
                  style={{ color: "var(--warning-strong)", background: "var(--warning-soft)" }}
                >
                  default
                </span>
              )}
            </span>
            <span
              className="waterfall-factor"
              title={formatResultValueFull(step.factor)}
              style={{ minWidth: 50, fontSize: 12, ...tabularNums }}
            >
              {idx === 0
                ? formatSmartValue(step.factor)
                : `\u00d7${formatSmartValue(step.factor)}`}
            </span>
            <div
              style={{
                height: 16,
                width: animated ? `${barWidth}%` : "0%",
                backgroundColor:
                  step.direction === "positive"
                    ? "var(--color-positive, var(--chart-positive))"
                    : step.direction === "negative"
                      ? "var(--color-negative, var(--chart-negative))"
                      : "var(--color-neutral, var(--chart-neutral))",
                borderRadius: 2,
                minWidth: animated ? 2 : 0,
                transition: `width 400ms cubic-bezier(0.22, 1, 0.36, 1)`,
                transitionDelay: `${idx * 60}ms`,
              }}
            />
            {!isLast && (
              <span
                title={formatResultValueFull(step.runningValue)}
                style={{ marginLeft: 4, fontSize: 12, ...tabularNums }}
              >
                {formatSmartValue(step.runningValue)}
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
