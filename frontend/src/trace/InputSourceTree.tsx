import React from "react"
import { ExpressionChainRowContentView } from "./ExpressionChain"
import { formatDisplayExpression } from "./traceFormatting"
import type { InputSourceEntry } from "./traceHelpers"

// ---------------------------------------------------------------------------
// InputSourceTree — the upstream input-sources tree attached to a calculation.
// Data comes from `calculation.input_sources`, which maps column name ->
// InputSourceEntry (and may itself contain nested input_sources one level
// deep). Pure helpers + types live in ./traceHelpers; this file only exports
// components.
// ---------------------------------------------------------------------------

interface InputSourceTreeProps {
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
