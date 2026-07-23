import React from "react"
import WaterfallChart from "./WaterfallChart"
import WaterfallErrorAlert from "./WaterfallErrorAlert"
import ExpressionChainRow from "./ExpressionChain"
import InputSourceTree from "./InputSourceTree"
import {
  buildChainEntries,
  buildInputSourceEntries,
  buildWaterfallSteps,
  resolveWaterfallProp,
  type ExpressionChainEntry,
  type InputSourceEntry,
  type WaterfallEntryProp,
  type WaterfallErrorProp,
  type WaterfallStep,
} from "./traceHelpers"
import {
  formatSmartValue,
  formatResultValueFull,
  formatDisplayExpression,
  tabularNums,
} from "./traceFormatting"
import { isTraceSourceNodeType } from "./traceOrigins"
import { TraceCalculationFrame } from "./TraceDetail"
import { nodeTypeColors } from "../utils/nodeTypes"

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
    taken_branch?: string | null
    taken_branch_index?: number | null
    expression_chain?: ExpressionChainEntry[] | null
    input_sources?: Record<string, InputSourceEntry> | null
  } | null
  nodeName?: string
  nodeType?: string
  isSourceOrigin?: boolean
  frame?: boolean
  // Backend emits either a successful entries list or a structured error
  // (e.g. "row had 2+ passes — waterfall not well-defined").  Pass both
  // through and let resolveWaterfallProp split them into steps vs error.
  waterfall?: WaterfallEntryProp[] | WaterfallErrorProp | null
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
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
  const { column, expression, calculation, nodeName, nodeType, isSourceOrigin, frame = true } = props

  const isNullBoth = !expression && !calculation
  const isOpaque = expression?.expression_type === "opaque"
  const isConditional = expression?.expression_type === "conditional"
  const isBanding = expression?.expression_type === "banding"
  const isOriginNode = Boolean(isSourceOrigin || isTraceSourceNodeType(nodeType))
  const hasExpressionText =
    expression != null && expression.expression_text.length > 0
  const calculationIsComputedPlaceholder = isComputedPlaceholder(calculation?.substituted_text)

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
  // Unified calculation box — all entries top-down in one well
  // ---------------------------------------------------------------------------
  const buildUnifiedEntries = () => {
    // Gather rows from both sources (intra-node chain first, then upstream
    // input-sources deduped by column). The row-builders live alongside
    // their respective render components so the orchestrator stays focused
    // on merge + sort + final-row composition.
    if (!calculation) return []
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
    return entries
  }

  const renderCalculationMissing = () => (
    <div
      role="alert"
      style={{
        padding: "8px 12px",
        border: "1px solid var(--warning-border)",
        borderRadius: 4,
        background: "var(--warning-soft)",
        color: "var(--warning-strong)",
        fontSize: 12,
        marginTop: 4,
      }}
    >
      Calculation data not available for this step.
    </div>
  )

  const renderSourceOrigin = () => (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 6,
        fontSize: 11,
        color: "var(--text-secondary)",
        marginTop: 4,
      }}
    >
      <span>Source node</span>
      {nodeName && (
        <span
          style={{
            color: "var(--text-primary)",
            fontFamily: "monospace",
            fontWeight: 600,
          }}
        >
          {nodeName}
        </span>
      )}
    </div>
  )

  const renderUnifiedBox = (formulaText: string | null, subText: string) => {
    // Reached the default render path with no calculation data. Surface the
    // gap loudly so the user knows trace data is missing rather than seeing
    // an empty pane.
    if (!calculation) return renderCalculationMissing()

    const entries = buildUnifiedEntries()

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
            background: "var(--text-accent-strong)",
            border: "1px solid var(--text-accent-heavy)",
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
                  : { color: "var(--accent)", background: "var(--text-accent-soft)" }),
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

  const renderBandingBox = () => {
    if (!calculation) return renderCalculationMissing()

    const entries = buildUnifiedEntries()
    const [[inputColumn, inputValue] = ["", undefined]] = Object.entries(calculation.input_values)
    const bandValue = calculation.result_value
    const inputText = inputColumn
      ? `${inputColumn}=${formatSmartValue(inputValue)}`
      : formatSmartValue(inputValue)
    const bandText = formatSmartValue(bandValue)
    const summary = inputColumn ? `${inputText} -> ${bandText}` : bandText
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

        <div
          aria-label={`Banding: ${summary}`}
          style={{
            position: "relative",
            paddingLeft: 24,
            ...(hasEntries ? { borderTop: "1px solid rgba(255,255,255,.06)", marginTop: 6, paddingTop: 8 } : {}),
          }}
        >
          <div style={{
            position: "absolute", left: 3, top: hasEntries ? 15 : 7, width: 7, height: 7,
            borderRadius: "50%",
            background: "var(--text-accent-strong)",
            border: "1px solid var(--text-accent-heavy)",
          }} />
          <div style={{ color: "var(--text-primary)", fontWeight: 600, overflowWrap: "anywhere", ...tabularNums }}>
            {inputColumn && (
              <>
                <span>{inputColumn}</span>
                <span style={{ color: "var(--text-secondary)" }}>=</span>
                <span title={formatResultValueFull(inputValue)}>{formatSmartValue(inputValue)}</span>
                <span style={{ color: "var(--text-secondary)" }}> -&gt; </span>
              </>
            )}
            <span
              style={{ color: resultIsNull ? "var(--text-secondary)" : "var(--accent)" }}
              title={resultFormattedFull !== resultFormatted ? resultFormattedFull : undefined}
            >
              {bandText}
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
    if (
      isOriginNode &&
      (isNullBoth || isOpaque || calculationIsComputedPlaceholder || (expression != null && !hasExpressionText))
    ) {
      return renderSourceOrigin()
    }

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
      // Opaque-but-no-calculation is a real error: the backend said the
      // expression is opaque (i.e. it claims to produce a result without
      // exposing the formula) yet no calculation was recorded. Silently
      // showing a "computed" label here would hide the misconfiguration.
      if (!calculation) {
        return (
          <div
            role="alert"
            style={{
              padding: "8px 12px",
              border: "1px solid var(--warning-border)",
              borderRadius: 4,
              background: "var(--warning-soft)",
              color: "var(--warning-strong)",
              fontSize: 12,
              marginTop: 4,
            }}
          >
            Calculation is not available for this opaque expression.
          </div>
        )
      }
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
      const rawTakenBranchIndex = calculation.taken_branch_index
      const backendTakenBranchIndex = (
        typeof rawTakenBranchIndex === "number" && Number.isInteger(rawTakenBranchIndex)
      )
        ? rawTakenBranchIndex
        : null
      const hasTypedSelection = (
        backendTakenBranchIndex !== null
        && backendTakenBranchIndex >= 0
        && backendTakenBranchIndex < branches.length
      )

      return (
        <div className="conditional-display" style={{ marginTop: 4 }}>
          {branches.map((branch, idx) => {
            const matched = hasTypedSelection && idx === backendTakenBranchIndex

            return (
              <div
                key={idx}
                className={`branch ${
                  !hasTypedSelection
                    ? ""
                    : matched
                    ? "taken matched-branch"
                    : "dimmed inactive"
                }`}
                data-matched={hasTypedSelection ? (matched ? "true" : "false") : undefined}
                style={hasTypedSelection && !matched ? { opacity: 0.5 } : undefined}
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
        </div>
      )
    }

    // Waterfall build failed on the backend — surface the error loudly
    // rather than rendering a silently-empty trace.
    if (waterfallError) {
      return (
        <WaterfallErrorAlert
          error={waterfallError.error}
          errorType={waterfallError.error_type}
        />
      )
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

    if (isBanding && calculation) {
      return (
        <div style={{ marginTop: 4 }}>
          {renderBandingBox()}
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

  if (!frame) {
    return (
      <div data-testid="trace-calculation-body">
        {renderBody()}
      </div>
    )
  }

  return (
    <TraceCalculationFrame
      nodeName={nodeName}
      column={column}
      result={calculation ? resultFormatted : undefined}
      resultTitle={resultFormattedFull !== resultFormatted ? resultFormattedFull : undefined}
      resultMuted={resultIsNull}
      accentColor={nodeType ? nodeTypeColors[nodeType] : undefined}
    >
      {renderBody()}
    </TraceCalculationFrame>
  )
}

export default React.memo(CalculationHero)
