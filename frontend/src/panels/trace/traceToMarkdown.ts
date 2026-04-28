import type {
  BandingNodeDetail,
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../../types/trace"

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

function ratingStepTables(detail: TraceNodeDetail): RatingStepTableDetail[] {
  return Array.isArray(detail.tables) ? detail.tables as RatingStepTableDetail[] : []
}

function ratingStepCombinedOutputs(detail: TraceNodeDetail): RatingStepCombinedOutputDetail[] {
  return Array.isArray(detail.combined_outputs) ? detail.combined_outputs as RatingStepCombinedOutputDetail[] : []
}

function ratingTableStatus(table: RatingStepTableDetail): string | undefined {
  if (typeof table.status === "string" && table.status.length > 0) return table.status
  if (table.default_used) return "default"
  if (table.matched === false) return "no_match"
  if (table.matched === true) return "matched"
  return undefined
}

function formatRatingStepDetail(detail: TraceNodeDetail): string[] {
  const parts: string[] = []
  const tables = ratingStepTables(detail)
  const combinedOutputs = ratingStepCombinedOutputs(detail)

  if (tables.length > 0) {
    const tableSummaries = tables.map((table, index) => {
      const title = table.name || table.output_column || `table ${index + 1}`
      const fields: string[] = []
      if (table.factors && table.factors.length > 0) {
        fields.push(table.factors.map((factor) => `${factor.column}=${formatVal(factor.value)}`).join(", "))
      }
      const status = ratingTableStatus(table)
      if (status) fields.push(`status=${status}`)
      if (table.selected_value !== undefined) fields.push(`selected=${formatVal(table.selected_value)}`)
      if (table.default_value !== undefined) fields.push(`default=${formatVal(table.default_value)}`)
      if (table.default_used) fields.push("default used")
      return fields.length > 0 ? `${title} (${fields.join("; ")})` : title
    })
    parts.push(`Rating tables: ${tableSummaries.join("; ")}`)
  }

  if (combinedOutputs.length > 0) {
    const combinedSummaries = combinedOutputs.map((combined) => {
      const inputs = Object.entries(combined.input_values)
        .map(([column, value]) => `${column}=${formatVal(value)}`)
        .join(", ")
      const details = [`${combined.operation} from base ${formatVal(combined.base_value)}`]
      if (inputs) details.push(inputs)
      return `${combined.column} = ${formatVal(combined.value)} (${details.join("; ")})`
    })
    parts.push(`Combined outputs: ${combinedSummaries.join("; ")}`)
  }

  return parts
}

function formatBandingDetail(detail: TraceNodeDetail): string[] {
  const banding = detail as BandingNodeDetail
  const inputColumn = banding.input_column ?? banding.column
  const matchedBand = banding.matched_band ?? banding.selected_band
  const parts = [
    `Banding: ${inputColumn ? `${inputColumn}=` : ""}${formatVal(banding.input_value)} -> ${formatVal(matchedBand)}`,
  ]
  if (banding.is_default) parts[0] += " (default)"
  if (banding.lower_bound != null || banding.upper_bound != null) {
    const lower = banding.lower_bound != null ? formatVal(banding.lower_bound) : ""
    const upper = banding.upper_bound != null ? formatVal(banding.upper_bound) : ""
    const lowerBracket = banding.lower_inclusive === false ? "(" : "["
    const upperBracket = banding.upper_inclusive === false ? ")" : "]"
    parts[0] += ` ${lowerBracket}${lower}, ${upper}${upperBracket}`
  }
  return parts
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
  const targetIsBanding = targetStep?.expression?.expression_type === "banding" ||
    targetStep?.node_detail?.detail_type === "banding"

  if (targetStep?.expression && !targetIsBanding) {
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

      if (step.expression && step.node_detail?.detail_type !== "banding") {
        parts.push(step.expression.expression_text)
      }

      // Node detail
      if (step.node_detail) {
        if (
          step.node_detail.detail_type === "rating_step" &&
          (Array.isArray(step.node_detail.tables) || Array.isArray(step.node_detail.combined_outputs))
        ) {
          parts.push(...formatRatingStepDetail(step.node_detail))
        } else if (step.node_detail.detail_type === "banding") {
          parts.push(...formatBandingDetail(step.node_detail))
        } else {
          for (const [key, val] of Object.entries(step.node_detail)) {
            parts.push(`${key}: ${formatVal(val)}`)
          }
        }
      }

      details = parts.map(escPipe).join("; ")

      lines.push(`| ${idx + 1} | ${escPipe(step.node_name)} | ${escPipe(step.node_type)} | ${details} |`)
    })

    lines.push("")
  }

  return lines.join("\n")
}
