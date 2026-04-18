import React from "react"
import { ExpressionChainRowContentView, type ChainBoxEntry } from "./ExpressionChain"
import { formatDisplayExpression } from "./traceFormatting"

// ---------------------------------------------------------------------------
// InputSourceTree — the upstream input-sources tree attached to a calculation.
// Data comes from `calculation.input_sources`, which maps column name ->
// InputSourceEntry (and may itself contain nested input_sources one level
// deep). Extracted from CalculationHero as part of the 2B-2 split.
// ---------------------------------------------------------------------------

export interface InputSourceEntry {
  node_name: string
  expression_text?: string
  substituted_text?: string
  result_value?: unknown
  input_sources?: Record<string, InputSourceEntry> | null
}

export interface InputSourceBoxEntry extends ChainBoxEntry {
  subSources: Record<string, InputSourceEntry> | null
}

/**
 * Normalise raw input_sources entries into the render-ready shape used
 * inside the unified calculation box. Skips any column whose name already
 * appears in `alreadyPresent` so expression-chain rows take precedence over
 * duplicate input-source rows for the same column.
 */
export function buildInputSourceEntries(
  inputSources: Record<string, InputSourceEntry> | null | undefined,
  inputValues: Record<string, unknown>,
  alreadyPresent: ReadonlySet<string>,
): InputSourceBoxEntry[] {
  if (!inputSources) return []
  const out: InputSourceBoxEntry[] = []
  for (const [column, src] of Object.entries(inputSources)) {
    if (alreadyPresent.has(column)) continue
    const formulaText = src.expression_text
      ? formatDisplayExpression(src.expression_text).text
      : null
    const substitutedText = src.substituted_text
      ? src.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
      : null
    out.push({
      column,
      formulaText,
      substitutedText,
      value: src.result_value ?? inputValues[column],
      source: src.node_name,
      subSources: src.input_sources ?? null,
    })
  }
  return out
}

export interface InputSourceTreeProps {
  /** Sub-sources keyed by column name. */
  subSources: Record<string, InputSourceEntry>
}

/**
 * Indented tree of nested input sources for a single parent row. Each child
 * is rendered with the shared chain-row content view and decorated with a
 * connector line + dot to show the parent-child relationship visually.
 */
const InputSourceTree: React.FC<InputSourceTreeProps> = ({ subSources }) => {
  const entries = Object.entries(subSources)
  // Not applicable — an empty sub-source map has no rows to draw; render
  // nothing. The caller only mounts this component when entries exist.
  if (entries.length === 0) return null

  return (
    <div style={{ marginTop: 4 }}>
      {entries.map(([subCol, subSrc]) => {
        const sfm = subSrc.expression_text ? formatDisplayExpression(subSrc.expression_text).text : null
        const ssub = subSrc.substituted_text
          ? subSrc.substituted_text.replace(/\*/g, "\u00d7").replace(/\//g, "\u00f7")
          : null
        return (
          <div key={subCol} style={{ position: "relative", paddingLeft: 24, marginBottom: 4 }}>
            <div style={{ position: "absolute", left: 6, top: 9, width: 14, height: 1, background: "rgba(96,165,250,.15)" }} />
            <div style={{ position: "absolute", left: 4, top: 7, width: 5, height: 5, borderRadius: "50%", background: "rgba(96,165,250,.25)", border: "1px solid rgba(96,165,250,.4)" }} />
            <ExpressionChainRowContentView
              column={subCol}
              formulaText={sfm}
              substitutedText={ssub}
              value={subSrc.result_value}
              source={subSrc.node_name}
            />
          </div>
        )
      })}
    </div>
  )
}

export default InputSourceTree
