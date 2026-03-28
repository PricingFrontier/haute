import type { TraceResult, TraceStep } from "../../types/trace"

function formatVal(v: unknown): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (v === Infinity) return "Infinity"
    if (v === -Infinity) return "-Infinity"
    return String(v)
  }
  if (typeof v === "string") return v
  return JSON.stringify(v)
}

/** Escape pipe characters so they don't break markdown tables. */
function escPipe(s: string): string {
  return s.replace(/\|/g, "\\|")
}

/**
 * Convert a trace result into a human-readable markdown document.
 */
export function traceToMarkdown(
  trace: TraceResult,
  targetStep: TraceStep | null | undefined,
): string {
  const lines: string[] = []

  // ---- Header ----
  if (trace.column) {
    lines.push(`# Trace: ${trace.column} = ${formatVal(trace.output_value)}`)
  } else {
    lines.push("# Trace")
  }
  lines.push("")

  // Row identifier
  if (trace.row_id_column && trace.row_id_value != null) {
    lines.push(`**Row**: ${trace.row_id_column} = ${formatVal(trace.row_id_value)}`)
  } else {
    lines.push(`**Row**: Row ${trace.row_index}`)
  }
  lines.push("")

  // Metadata
  lines.push(`**Execution**: ${trace.execution_ms} ms | ${trace.steps.length} steps`)
  lines.push("")

  // ---- Formula section (only if targetStep has expression) ----
  if (targetStep?.expression) {
    lines.push("## Formula")
    lines.push("")
    lines.push(`\`${targetStep.expression.expression_text}\``)
    lines.push("")

    if (targetStep.calculation) {
      lines.push(`Substituted: \`${targetStep.calculation.substituted_text}\``)
      lines.push("")

      // Input values
      if (targetStep.calculation.input_values && Object.keys(targetStep.calculation.input_values).length > 0) {
        lines.push("**Inputs**:")
        for (const [key, val] of Object.entries(targetStep.calculation.input_values)) {
          lines.push(`- ${key} = ${formatVal(val)}`)
        }
        lines.push("")
      }
    }
  }

  // ---- Data Flow table ----
  const relevantSteps = trace.steps.filter((s) => s.column_relevant)
  if (relevantSteps.length > 0) {
    lines.push("## Data Flow")
    lines.push("")
    lines.push("| # | Node | Type | Details |")
    lines.push("|---|------|------|---------|")

    relevantSteps.forEach((step, idx) => {
      let details = ""
      // Schema diff summary
      const diff = step.schema_diff
      const parts: string[] = []
      if (diff.columns_added.length > 0) parts.push(`+${diff.columns_added.join(",")}`)
      if (diff.columns_modified.length > 0) parts.push(`~${diff.columns_modified.join(",")}`)
      if (diff.columns_removed.length > 0) parts.push(`-${diff.columns_removed.join(",")}`)

      if (step.expression) {
        parts.push(step.expression.expression_text)
      }

      // Node detail
      if (step.node_detail) {
        for (const [key, val] of Object.entries(step.node_detail)) {
          parts.push(`${key}: ${formatVal(val)}`)
        }
      }

      details = parts.map(escPipe).join("; ")

      lines.push(`| ${idx + 1} | ${escPipe(step.node_name)} | ${escPipe(step.node_type)} | ${details} |`)
    })

    lines.push("")
  }

  return lines.join("\n")
}
