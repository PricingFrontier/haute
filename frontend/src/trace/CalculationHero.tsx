import React, { useState, useCallback, useEffect, useRef } from "react"

export interface WaterfallEntryProp {
  label: string
  operation: string
  value: number
  delta: number
  cumulative: number
}

export interface ExpressionChainEntry {
  expression_text: string
  target_column: string
  substituted_text?: string
  result_value?: unknown
}

export interface InputSourceEntry {
  node_name: string
  expression_text?: string
  substituted_text?: string
  result_value?: unknown
  input_sources?: Record<string, InputSourceEntry> | null
}

export interface CalculationHeroProps {
  column: string
  expression: {
    expression_text: string
    expression_type: string
    referenced_columns: string[]
  } | null
  calculation: {
    substituted_text: string
    result_value: unknown
    input_values: Record<string, unknown>
    expression_chain?: ExpressionChainEntry[] | null
    input_sources?: Record<string, InputSourceEntry> | null
  } | null
  executionMs?: number
  stepCount?: number
  nodeName?: string
  waterfall?: WaterfallEntryProp[] | null
}

// ---------------------------------------------------------------------------
// Value formatting
// ---------------------------------------------------------------------------

function formatSmartValue(v: unknown): string {
  if (v === null || v === undefined) return "null"
  if (typeof v !== "number") return String(v)
  if (Number.isNaN(v)) return "NaN"
  if (!Number.isFinite(v)) return String(v)
  if (Number.isInteger(v)) return v.toLocaleString("en-US")
  const abs = Math.abs(v)
  if (abs < 10 && abs > 0) {
    return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })
  }
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatResultValue(v: unknown, precision?: number): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (!Number.isFinite(v)) return String(v)
    if (Number.isInteger(v)) return v.toLocaleString("en-US")
    if (precision !== undefined) {
      return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: precision })
    }
    return String(v)
  }
  if (typeof v === "string") return `"${v}"`
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

function formatResultValueFull(v: unknown): string {
  return formatResultValue(v)
}

function formatResultValue2dp(v: unknown): string {
  return formatResultValue(v, 2)
}

function formatDisplayExpression(
  expr: string,
  maxLen = 60,
): { text: string; truncated: boolean } {
  const replaced = expr.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
  if (replaced.length > maxLen) {
    return { text: replaced.slice(0, maxLen) + "\u2026", truncated: true }
  }
  return { text: replaced, truncated: false }
}

// ---------------------------------------------------------------------------
// Waterfall logic
// ---------------------------------------------------------------------------

interface WaterfallStep {
  name: string
  factor: number
  runningValue: number
  prevValue: number
  direction: "positive" | "negative" | "neutral"
}

function buildWaterfallSteps(
  inputValues: Record<string, unknown>,
  expressionText: string,
): WaterfallStep[] | null {
  const parts = expressionText.split(/\s*\*\s*/)
  if (parts.length < 3) return null

  const names = parts.map((p) => p.trim())
  const allNumeric = names.every(
    (n) => n in inputValues && typeof inputValues[n] === "number",
  )
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

// ---------------------------------------------------------------------------
// Branch parsing for conditional
// ---------------------------------------------------------------------------

interface Branch {
  condition?: string
  result?: string
  isOtherwise: boolean
}

function parseBranches(text: string): Branch[] {
  const branches: Branch[] = []
  const regex =
    /when\s+(.+?)\s+then\s+(.+?)(?=\s+when\s|\s+otherwise\s|$)/gi
  let match
  let lastIndex = 0

  while ((match = regex.exec(text)) !== null) {
    branches.push({
      condition: match[1].trim(),
      result: match[2].trim(),
      isOtherwise: false,
    })
    lastIndex = regex.lastIndex
  }

  const otherwiseMatch = text.slice(lastIndex).match(/otherwise\s+(.+)/i)
  if (otherwiseMatch) {
    branches.push({
      result: otherwiseMatch[1].trim(),
      isOtherwise: true,
    })
  }

  if (branches.length === 0) {
    branches.push({ result: text, isOtherwise: false })
  }

  return branches
}

// ---------------------------------------------------------------------------
// Tabular nums style constant (Fix 7)
// ---------------------------------------------------------------------------

const tabularNums: React.CSSProperties = { fontVariantNumeric: "tabular-nums" }

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function WaterfallDisplay({
  steps,
  resultValue,
}: {
  steps: WaterfallStep[]
  resultValue: unknown
}) {
  const maxVal = Math.max(
    ...steps.map((s) => Math.abs(s.runningValue)),
    1,
  )
  const formatted = formatResultValue2dp(resultValue)
  const formattedFull = formatResultValueFull(resultValue)
  const isNull = resultValue === null || resultValue === undefined

  // Fix 6: Animated waterfall bars
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

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

const CalculationHero: React.FC<CalculationHeroProps> = (props) => {
  const { column, expression, calculation, nodeName } = props
  const [copied, setCopied] = useState(false)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
    }
  }, [])

  const handleCopy = useCallback(async () => {
    const parts: string[] = []
    parts.push(`Column: ${column}`)
    if (expression?.expression_text) {
      parts.push(`Formula: ${expression.expression_text}`)
    }
    if (calculation) {
      if (calculation.substituted_text) {
        parts.push(`Substituted: ${calculation.substituted_text}`)
      }
      parts.push(`Result: ${formatResultValueFull(calculation.result_value)}`)
      if (Object.keys(calculation.input_values).length > 0) {
        parts.push("Inputs:")
        for (const [k, v] of Object.entries(calculation.input_values)) {
          parts.push(`  ${k} = ${formatResultValueFull(v)}`)
        }
      }
    }
    const text = parts.join("\n")
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement("textarea")
      ta.value = text
      ta.style.position = "fixed"
      ta.style.opacity = "0"
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
    }
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
    setCopied(true)
    copyTimerRef.current = setTimeout(() => setCopied(false), 2000)
  }, [column, expression, calculation])

  const isNullBoth = !expression && !calculation
  const isOpaque = expression?.expression_type === "opaque"
  const isConditional = expression?.expression_type === "conditional"
  const hasExpressionText =
    expression != null && expression.expression_text.length > 0

  // Waterfall: prefer backend-computed waterfall data, fallback to
  // frontend parsing for arithmetic with 3+ multiplicative factors.
  const { waterfall: waterfallProp } = props
  let waterfallSteps: WaterfallStep[] | null = null
  const waterfallError =
    waterfallProp && !Array.isArray(waterfallProp) && "error" in waterfallProp
      ? waterfallProp
      : null

  if (Array.isArray(waterfallProp) && waterfallProp.length >= 3) {
    waterfallSteps = waterfallProp.map((entry, i) => {
      const prevCumulative = i > 0 ? waterfallProp[i - 1].cumulative : 0
      return {
        name: entry.label,
        factor: entry.value,
        runningValue: entry.cumulative,
        prevValue: prevCumulative,
        direction: (entry.delta > 0 ? "positive" : entry.delta < 0 ? "negative" : "neutral") as "positive" | "negative" | "neutral",
      }
    })
  } else if (
    expression?.expression_type === "arithmetic" &&
    calculation &&
    hasExpressionText
  ) {
    waterfallSteps = buildWaterfallSteps(
      calculation.input_values,
      expression.expression_text,
    )
  }

  const columnMaxLen = 60
  const isLongColumn = column.length > columnMaxLen

  const resultValue = calculation?.result_value
  const resultFormatted = calculation
    ? formatSmartValue(resultValue)
    : ""
  const resultFormattedFull = calculation
    ? formatResultValueFull(resultValue)
    : ""
  const resultIsNull = calculation
    ? resultValue === null || resultValue === undefined
    : false


  // ---------------------------------------------------------------------------
  // Line 1: Column name + Result (Fix 2: result value prominence)
  // ---------------------------------------------------------------------------
  const renderLine1 = () => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <span
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: "var(--text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={isLongColumn ? column : undefined}
      >
        {column}
      </span>
      {calculation && (
        <span
          className={
            resultIsNull
              ? "result-value muted null-value"
              : "result-value accent"
          }
          data-accent={!resultIsNull || undefined}
          data-muted={resultIsNull || undefined}
          style={{
            fontSize: 20,
            fontWeight: 700,
            fontFamily: "monospace",
            flexShrink: 0,
            marginLeft: 8,
            ...tabularNums,
            borderRadius: 6,
            padding: "2px 10px",
            ...(resultIsNull
              ? { fontStyle: "italic", opacity: 0.5, color: "var(--text-muted)" }
              : {
                    color: "var(--accent)",
                    background: "rgba(96,165,250,.08)",
                    border: "1px solid rgba(96,165,250,.12)",
                  }),
          }}
          title={resultFormattedFull !== resultFormatted ? resultFormattedFull : undefined}
        >
          {resultFormatted}
        </span>
      )}
    </div>
  )

  // ---------------------------------------------------------------------------
  // Unified calculation box — all entries top-down in one well
  // ---------------------------------------------------------------------------
  const renderUnifiedBox = (formulaText: string | null, subText: string) => {
    if (!calculation) return null

    // Collect all derivation entries
    interface BoxEntry {
      column: string
      formulaText: string | null
      substitutedText: string | null
      value: unknown
      source: string | null
      subSources: Record<string, InputSourceEntry> | null
    }
    const entries: BoxEntry[] = []

    // 1. Intra-node chain entries (excluding the target column)
    if (calculation.expression_chain && calculation.expression_chain.length > 1) {
      for (const entry of calculation.expression_chain) {
        if (entry.target_column === column) continue
        const eFormula = entry.expression_text ? formatDisplayExpression(entry.expression_text).text : null
        const eSub = entry.substituted_text
          ? entry.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
          : null
        entries.push({
          column: entry.target_column,
          formulaText: eFormula,
          substitutedText: eSub,
          value: entry.result_value ?? calculation.input_values[entry.target_column],
          source: null,
          subSources: null,
        })
      }
    }

    // 2. Upstream input sources
    if (calculation.input_sources) {
      for (const [colName, src] of Object.entries(calculation.input_sources)) {
        if (entries.some((e) => e.column === colName)) continue
        const sFormula = src.expression_text ? formatDisplayExpression(src.expression_text).text : null
        const sSub = src.substituted_text
          ? src.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
          : null
        entries.push({
          column: colName,
          formulaText: sFormula,
          substitutedText: sSub,
          value: src.result_value ?? calculation.input_values[colName],
          source: src.node_name,
          subSources: src.input_sources ?? null,
        })
      }
    }

    // Sort: entries without formulas (raw inputs) first, then computed ones
    // This gives a natural top-down flow: sources → derived → result
    entries.sort((a, b) => {
      const aHasFormula = a.formulaText ? 1 : 0
      const bHasFormula = b.formulaText ? 1 : 0
      return aHasFormula - bHasFormula
    })

    const hasEntries = entries.length > 0

    return (
      <div style={{
        background: "rgba(0,0,0,.12)",
        borderRadius: 6,
        padding: "10px 12px",
        marginTop: 8,
        border: "1px solid rgba(255,255,255,.03)",
        fontFamily: "monospace",
        fontSize: 12,
      }}>
        {/* Input/intermediate entries — top-down */}
        {entries.map((entry) => {
          const val = entry.value
          const fVal = formatSmartValue(val)
          return (
            <div key={entry.column} style={{ position: "relative", paddingLeft: 24, marginBottom: 6 }}>
              {/* Vertical line */}
              <div style={{
                position: "absolute", left: 6, top: 0, bottom: -6,
                width: 1, background: "rgba(96,165,250,.15)",
              }} />
              {/* Horizontal connector */}
              <div style={{
                position: "absolute", left: 6, top: 9, width: 14, height: 1,
                background: "rgba(96,165,250,.15)",
              }} />
              {/* Dot */}
              <div style={{
                position: "absolute", left: 4, top: 7, width: 5, height: 5,
                borderRadius: "50%", background: "rgba(96,165,250,.25)",
                border: "1px solid rgba(96,165,250,.4)",
              }} />
              {/* Content — line 1: symbolic, line 2: numeric */}
              {entry.formulaText ? (
                <>
                  {/* Line 1: column = formula */}
                  <div style={{ color: "var(--text-primary)" }}>
                    <span style={{ fontWeight: 600 }}>{entry.column}</span>
                    <span style={{ color: "var(--text-secondary)" }}> = {entry.formulaText}</span>
                    {entry.source && (
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({entry.source})</span>
                    )}
                  </div>
                  {/* Line 2: result = substituted values */}
                  <div style={{ color: "var(--text-secondary)", ...tabularNums }}>
                    <span style={{ color: "var(--text-primary)", fontWeight: 600 }} title={formatResultValueFull(val)}>
                      {fVal}
                    </span>
                    {entry.substitutedText ? (
                      <span> = {entry.substitutedText}</span>
                    ) : null}
                  </div>
                </>
              ) : (
                /* No formula — single line: column = value (source) */
                <div style={{ color: "var(--text-primary)" }}>
                  <span style={{ fontWeight: 600 }}>{entry.column}</span>
                  <span> = </span>
                  <span
                    style={{ ...tabularNums }}
                    title={formatResultValueFull(val)}
                  >
                    {fVal}
                  </span>
                  {entry.source && (
                    <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({entry.source})</span>
                  )}
                </div>
              )}
              {/* Sub-sources */}
              {entry.subSources && Object.keys(entry.subSources).length > 0 && (
                <div style={{ marginTop: 4 }}>
                  {Object.entries(entry.subSources).map(([subCol, subSrc]) => {
                    const sv = subSrc.result_value
                    const sf = formatSmartValue(sv)
                    const sfm = subSrc.expression_text ? formatDisplayExpression(subSrc.expression_text).text : null
                    const ssub = subSrc.substituted_text
                      ? subSrc.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
                      : null
                    return (
                      <div key={subCol} style={{ position: "relative", paddingLeft: 24, marginBottom: 4 }}>
                        <div style={{ position: "absolute", left: 6, top: 9, width: 14, height: 1, background: "rgba(96,165,250,.15)" }} />
                        <div style={{ position: "absolute", left: 4, top: 7, width: 5, height: 5, borderRadius: "50%", background: "rgba(96,165,250,.25)", border: "1px solid rgba(96,165,250,.4)" }} />
                        {sfm ? (
                          <>
                            <div style={{ color: "var(--text-primary)" }}>
                              <span style={{ fontWeight: 600 }}>{subCol}</span>
                              <span style={{ color: "var(--text-secondary)" }}> = {sfm}</span>
                              {subSrc.node_name && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({subSrc.node_name})</span>}
                            </div>
                            <div style={{ color: "var(--text-secondary)", ...tabularNums }}>
                              <span style={{ color: "var(--text-primary)", fontWeight: 600 }} title={formatResultValueFull(sv)}>{sf}</span>
                              {ssub ? <span> = {ssub}</span> : null}
                            </div>
                          </>
                        ) : (
                          <div style={{ color: "var(--text-primary)" }}>
                            <span style={{ fontWeight: 600 }}>{subCol}</span>
                            <span> = </span>
                            <span style={{ ...tabularNums }} title={formatResultValueFull(sv)}>{sf}</span>
                            {subSrc.node_name && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({subSrc.node_name})</span>}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}

        {/* Final: the target formula + substituted + result */}
        <div style={{
          position: "relative", paddingLeft: 24,
          ...(hasEntries ? { borderTop: "1px solid rgba(255,255,255,.06)", marginTop: 6, paddingTop: 8 } : {}),
        }}>
          {/* Dot — larger for the result */}
          <div style={{
            position: "absolute", left: 3, top: hasEntries ? 15 : 7, width: 7, height: 7,
            borderRadius: "50%",
            background: "rgba(96,165,250,.4)",
            border: "1px solid rgba(96,165,250,.6)",
          }} />
          {/* Formula */}
          {formulaText && (
            <div style={{ color: "var(--text-primary)", fontWeight: 600 }}>
              {formulaText}
            </div>
          )}
          {/* Substituted values */}
          {subText.length > 0 && (
            <div style={{ color: "var(--text-secondary)", ...tabularNums }}>
              {subText}
            </div>
          )}
          {/* = result */}
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 2 }}>
            <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>
              = {column}
            </span>
            <span
              style={{
                fontSize: 14, fontWeight: 700, ...tabularNums,
                borderRadius: 4, padding: "1px 8px",
                ...(resultIsNull
                  ? { fontStyle: "italic", opacity: 0.5, color: "var(--text-secondary)" }
                  : { color: "var(--accent)", background: "rgba(96,165,250,.08)" }),
              }}
              title={resultFormattedFull !== resultFormatted ? resultFormattedFull : undefined}
            >
              {resultFormatted}
            </span>
          </div>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Body rendering
  // ---------------------------------------------------------------------------
  const renderBody = () => {
    // Both null: source data
    if (isNullBoth) {
      return (
        <div
          style={{
            fontSize: 11,
            fontStyle: "italic",
            color: "var(--text-secondary)",
            marginTop: 2,
          }}
        >
          source data
        </div>
      )
    }

    // Opaque mode
    if (isOpaque) {
      return (
        <div
          style={{
            fontSize: 11,
            fontStyle: "italic",
            color: "var(--text-secondary)",
            marginTop: 2,
          }}
        >
          <em style={{ fontStyle: "italic" }}>computed</em>
        </div>
      )
    }

    // Conditional mode
    if (isConditional && expression && calculation) {
      const branches = parseBranches(expression.expression_text)
      const subBranches = parseBranches(calculation.substituted_text)
      const resultStr =
        typeof calculation.result_value === "string"
          ? calculation.result_value
          : String(calculation.result_value)

      function isBranchMatched(idx: number): boolean {
        const sub = subBranches[idx]
        if (!sub?.result) return false
        return sub.result.includes(resultStr)
      }

      const anyNonOtherwiseMatched = branches.some(
        (b, i) => !b.isOtherwise && isBranchMatched(i),
      )

      return (
        <div className="conditional-display" style={{ marginTop: 4 }}>
          {branches.map((branch, idx) => {
            const matched = branch.isOtherwise
              ? !anyNonOtherwiseMatched
              : isBranchMatched(idx)

            return (
              <div
                key={idx}
                className={
                  matched
                    ? "branch taken matched-branch"
                    : "branch dimmed inactive"
                }
                data-matched={matched ? "true" : "false"}
                style={!matched ? { opacity: 0.5 } : undefined}
              >
                {branch.isOtherwise ? (
                  <span>
                    <strong>otherwise</strong> {branch.result}
                  </span>
                ) : (
                  <span>
                    <strong>when</strong> {branch.condition}{" "}
                    <strong>then</strong> {branch.result}
                  </span>
                )}
              </div>
            )
          })}
          {(() => {
            const branchTexts = branches.map(b => b.result ?? "").join(" ")
            const resultInBranch = branchTexts.includes(resultStr)
            if (resultInBranch) {
              return (
                <span
                  className={resultIsNull ? "result-value muted null-value" : "result-value accent"}
                  data-accent={!resultIsNull || undefined}
                  data-muted={resultIsNull || undefined}
                  aria-hidden="true"
                  style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}
                />
              )
            }
            return null
          })()}
        </div>
      )
    }

    // Waterfall build failed on the backend — surface the error loudly
    // rather than rendering a silently-empty trace.
    if (waterfallError) {
      return (
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
            <strong>Waterfall could not be built:</strong> {waterfallError.error}
          </div>
        </div>
      )
    }

    // Waterfall mode: takes precedence for 3+ multiplicative factors
    if (waterfallSteps && calculation) {
      return (
        <div style={{ marginTop: 4 }}>
          <WaterfallDisplay
            steps={waterfallSteps}
            resultValue={calculation.result_value}
          />
        </div>
      )
    }

    // Default: everything in one unified box, top-down chronological
    const rawSubText = calculation?.substituted_text ?? ""
    const subText = rawSubText.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
    const formulaText = hasExpressionText && !isOpaque
      ? formatDisplayExpression(expression!.expression_text).text
      : null

    return (
      <div style={{ marginTop: 4 }}>
        {/* Opaque fallback */}
        {expression && !hasExpressionText && (
          <div style={{ fontStyle: "italic", fontSize: 11, color: "var(--text-secondary)" }}>
            computed
          </div>
        )}

        {/* Unified calculation box — top-down from inputs to result */}
        {renderUnifiedBox(formulaText, subText)}
      </div>
    )
  }

  return (
    <div
      className="calculation-hero"
      style={{
        background: "var(--bg-elevated, rgba(255,255,255,0.03))",
        borderRadius: 6,
        padding: 12,
        margin: 0,
        overflow: "hidden",
      }}
    >
      {/* Node name */}
      {nodeName && (
        <div
          style={{
            fontSize: 12,
            color: "var(--text-secondary)",
            marginBottom: 4,
          }}
        >
          {nodeName}
        </div>
      )}

      {/* Line 1: Column name + Result */}
      {renderLine1()}

      {/* Body */}
      {renderBody()}

      {/* Copy button -- bottom right */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          marginTop: 8,
        }}
      >
        <button
          aria-label={copied ? "Copied" : "Copy"}
          onClick={handleCopy}
          style={{
            background: "none",
            border: "1px solid var(--border, #ccc)",
            borderRadius: 4,
            cursor: "pointer",
            padding: "2px 8px",
            fontSize: 12,
          }}
        >
          {copied ? "\u2713 Copied" : "Copy"}
        </button>
      </div>
    </div>
  )
}

export default React.memo(CalculationHero)
