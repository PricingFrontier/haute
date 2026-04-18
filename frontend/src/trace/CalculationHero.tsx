import React, { useState, useCallback, useEffect, useRef } from "react"
import WaterfallChart, {
  buildWaterfallSteps,
  resolveWaterfallProp,
  WaterfallErrorAlert,
  type WaterfallEntryProp,
  type WaterfallStep,
} from "./WaterfallChart"
import ExpressionChainRow, {
  buildChainEntries,
  type ExpressionChainEntry,
} from "./ExpressionChain"
import InputSourceTree, {
  buildInputSourceEntries,
  type InputSourceEntry,
} from "./InputSourceTree"
import {
  formatSmartValue,
  formatResultValueFull,
  formatDisplayExpression,
  tabularNums,
} from "./traceFormatting"

// Re-export the entry types so existing importers of CalculationHero keep working.
export type { ExpressionChainEntry, InputSourceEntry, WaterfallEntryProp }

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
  const { steps: backendSteps, error: waterfallError } = resolveWaterfallProp(props.waterfall)
  let waterfallSteps: WaterfallStep[] | null = backendSteps
  if (
    waterfallSteps === null &&
    waterfallError === null &&
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
  // Line 1: Column name + Result
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
    // Reached the default render path with no calculation data. Prior to
    // review item #85 this silently returned null; now we surface the gap
    // loudly so the user knows trace data is missing rather than seeing an
    // empty pane.
    if (!calculation) {
      return (
        <div
          role="alert"
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
          Calculation data not available for this step.
        </div>
      )
    }

    // Gather rows from both sources (intra-node chain first, then upstream
    // input-sources deduped by column). The row-builders live alongside
    // their respective render components so the orchestrator stays focused
    // on merge + sort + final-row composition.
    const chainEntries = buildChainEntries(
      calculation.expression_chain,
      column,
      calculation.input_values,
    )
    const chainColumns = new Set(chainEntries.map((e) => e.column))
    const sourceEntries = buildInputSourceEntries(
      calculation.input_sources,
      calculation.input_values,
      chainColumns,
    )
    // Sort: entries without formulas (raw inputs) first, then computed ones
    // This gives a natural top-down flow: sources → derived → result. We
    // still need subSources on only the source-derived rows (chain rows
    // never have nested sub-sources), so carry them through as an optional
    // field on a single merged list.
    const entries: Array<{
      column: string
      formulaText: string | null
      substitutedText: string | null
      value: unknown
      source: string | null
      subSources: Record<string, InputSourceEntry> | null
    }> = [
      ...chainEntries.map((e) => ({ ...e, subSources: null })),
      ...sourceEntries,
    ]
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
        {entries.map((entry) => (
          <ExpressionChainRow
            key={entry.column}
            column={entry.column}
            formulaText={entry.formulaText}
            substitutedText={entry.substitutedText}
            value={entry.value}
            source={entry.source}
          >
            {entry.subSources && Object.keys(entry.subSources).length > 0 && (
              <InputSourceTree subSources={entry.subSources} />
            )}
          </ExpressionChainRow>
        ))}

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
            // Not applicable — result value wasn't found verbatim inside any
            // branch text (e.g. numeric result vs textual branch labels), so
            // the hidden a11y sentinel is intentionally skipped.
            return null
          })()}
        </div>
      )
    }

    // Waterfall build failed on the backend — surface the error loudly
    // rather than rendering a silently-empty trace.
    if (waterfallError) {
      return <WaterfallErrorAlert error={waterfallError.error} />
    }

    // Waterfall mode: takes precedence for 3+ multiplicative factors
    if (waterfallSteps && calculation) {
      return (
        <div style={{ marginTop: 4 }}>
          <WaterfallChart
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
