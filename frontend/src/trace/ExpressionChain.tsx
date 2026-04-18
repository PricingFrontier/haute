import React from "react"
import { formatSmartValue, formatResultValueFull, formatDisplayExpression, tabularNums } from "./traceFormatting"

// ---------------------------------------------------------------------------
// ExpressionChain — the "intra-node" chain of derivations that lead up to the
// target column inside a single step. The data comes from
// `calculation.expression_chain` and represents columns computed earlier in
// the same node (not upstream input sources from prior nodes). Extracted from
// CalculationHero as part of the 2B-2 split.
// ---------------------------------------------------------------------------

export interface ExpressionChainEntry {
  expression_text: string
  target_column: string
  substituted_text?: string
  result_value?: unknown
}

export interface ChainBoxEntry {
  column: string
  formulaText: string | null
  substitutedText: string | null
  value: unknown
  source: string | null
}

/**
 * Normalise raw expression-chain entries into the render-ready shape used
 * inside the unified calculation box. Skips the target column (its row is
 * rendered by the orchestrator as the final line). Returns an empty array
 * when the chain is absent or trivial (<= 1 entry).
 */
export function buildChainEntries(
  chain: ExpressionChainEntry[] | null | undefined,
  targetColumn: string,
  inputValues: Record<string, unknown>,
): ChainBoxEntry[] {
  if (!chain || chain.length <= 1) return []
  const out: ChainBoxEntry[] = []
  for (const entry of chain) {
    if (entry.target_column === targetColumn) continue
    const formulaText = entry.expression_text
      ? formatDisplayExpression(entry.expression_text).text
      : null
    const substitutedText = entry.substituted_text
      ? entry.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
      : null
    out.push({
      column: entry.target_column,
      formulaText,
      substitutedText,
      value: entry.result_value ?? inputValues[entry.target_column],
      source: null,
    })
  }
  return out
}

/**
 * Renders the inner content of a chain row (formula line + substituted line,
 * or a single-line column=value for raw inputs). Exposed separately so
 * InputSourceTree can reuse the same text layout for nested sub-sources
 * without re-drawing the top-level connector chrome.
 */
export function ExpressionChainRowContentView({
  column,
  formulaText,
  substitutedText,
  value,
  source,
}: ChainBoxEntry) {
  const fVal = formatSmartValue(value)
  if (formulaText) {
    return (
      <>
        {/* Line 1: column = formula */}
        <div style={{ color: "var(--text-primary)" }}>
          <span style={{ fontWeight: 600 }}>{column}</span>
          <span style={{ color: "var(--text-secondary)" }}> = {formulaText}</span>
          {source && (
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({source})</span>
          )}
        </div>
        {/* Line 2: result = substituted values */}
        <div style={{ color: "var(--text-secondary)", ...tabularNums }}>
          <span style={{ color: "var(--text-primary)", fontWeight: 600 }} title={formatResultValueFull(value)}>
            {fVal}
          </span>
          {substitutedText ? <span> = {substitutedText}</span> : null}
        </div>
      </>
    )
  }
  /* No formula — single line: column = value (source) */
  return (
    <div style={{ color: "var(--text-primary)" }}>
      <span style={{ fontWeight: 600 }}>{column}</span>
      <span> = </span>
      <span
        style={{ ...tabularNums }}
        title={formatResultValueFull(value)}
      >
        {fVal}
      </span>
      {source && (
        <span style={{ fontSize: 11, color: "var(--text-secondary)" }}> ({source})</span>
      )}
    </div>
  )
}

export interface ExpressionChainRowProps extends ChainBoxEntry {
  /** Optional children rendered beneath the row (used for nested sub-sources). */
  children?: React.ReactNode
}

/**
 * Single expression-chain row wrapped in the shared connector chrome
 * (vertical line + dot + horizontal stub) and optionally followed by nested
 * sub-source rows. This is the visual unit used to render each entry in the
 * unified calculation box.
 */
const ExpressionChainRow: React.FC<ExpressionChainRowProps> = ({
  children,
  ...content
}) => (
  <div style={{ position: "relative", paddingLeft: 24, marginBottom: 6 }}>
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
    <ExpressionChainRowContentView {...content} />
    {children}
  </div>
)

export default ExpressionChainRow
