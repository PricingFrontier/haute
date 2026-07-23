import type {
  TraceOmission,
  TraceResult,
  TraceStep,
} from "../types/trace"
import { sanitiseLabelForFilesystem } from "../utils/apiInputPorts"

export interface TraceExportRow {
  section: "provenance" | "trace" | "step" | "omission" | "waterfall" | "diagnostic"
  topologicalRank: number | null
  nodeId: string
  nodeName: string
  field: string
  value: string
}

function stableJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableJsonValue)
  if (typeof value !== "object" || value === null) return value
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => [key, stableJsonValue(item)]),
  )
}

export function exactTraceValue(value: unknown): string {
  if (value === undefined) return ""
  if (value === null) return "null"
  if (typeof value === "string") return value
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "NaN"
    if (value === Infinity) return "Infinity"
    if (value === -Infinity) return "-Infinity"
    return String(value)
  }
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "boolean") return String(value)
  return JSON.stringify(stableJsonValue(value))
}

function fieldRow(
  section: TraceExportRow["section"],
  field: string,
  value: unknown,
  evidence?: TraceStep | TraceOmission,
): TraceExportRow {
  return {
    section,
    topologicalRank: evidence?.topological_rank ?? null,
    nodeId: evidence?.node_id ?? "",
    nodeName: evidence?.node_name ?? "",
    field,
    value: exactTraceValue(value),
  }
}

function isOmission(evidence: TraceStep | TraceOmission): evidence is TraceOmission {
  return "reason" in evidence
}

/** One deterministic projection shared by Markdown, CSV, clipboard and print. */
export function buildTraceExportRows(trace: TraceResult): TraceExportRow[] {
  const rows: TraceExportRow[] = [
    fieldRow("provenance", "generated_at", trace.generated_at),
    fieldRow("provenance", "pipeline_source", trace.pipeline_source),
    fieldRow("provenance", "execution_origin", trace.execution_origin),
    fieldRow("trace", "target_node_id", trace.target_node_id),
    fieldRow("trace", "row_index", trace.row_index),
    fieldRow("trace", "column", trace.column),
    fieldRow("trace", "row_id_column", trace.row_id_column),
    fieldRow("trace", "row_id_value", trace.row_id_value),
    fieldRow("trace", "output_value", trace.output_value),
    fieldRow("trace", "nodes_in_trace", trace.nodes_in_trace),
    fieldRow("trace", "total_nodes_in_pipeline", trace.total_nodes_in_pipeline),
    fieldRow("trace", "execution_ms", trace.execution_ms),
  ]

  const evidence: Array<TraceStep | TraceOmission> = [
    ...trace.steps,
    ...trace.omissions,
  ].sort((left, right) => left.topological_rank - right.topological_rank)

  for (const item of evidence) {
    if (isOmission(item)) {
      rows.push(
        fieldRow("omission", "reason", item.reason, item),
        fieldRow("omission", "diagnostic_index", item.diagnostic_index, item),
      )
      continue
    }
    rows.push(
      fieldRow("step", "node_type", item.node_type, item),
      fieldRow("step", "schema_diff", item.schema_diff, item),
      fieldRow("step", "input_values", item.input_values, item),
      fieldRow("step", "output_values", item.output_values, item),
      fieldRow("step", "expression", item.expression, item),
      fieldRow("step", "calculation", item.calculation, item),
      fieldRow("step", "node_detail", item.node_detail, item),
      fieldRow("step", "row_lineage_type", item.row_lineage_type, item),
    )
  }

  if (Array.isArray(trace.waterfall)) {
    trace.waterfall.forEach((entry, index) => {
      rows.push(fieldRow("waterfall", `entry_${index + 1}`, entry))
    })
  } else if (trace.waterfall) {
    rows.push(fieldRow("waterfall", "error", trace.waterfall))
  }

  trace.correlation_diagnostics.forEach((diagnostic, index) => {
    rows.push(fieldRow("diagnostic", `diagnostic_${index}`, diagnostic))
  })
  return rows
}

function escapeMarkdownCell(value: string): string {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/\|/g, "\\|")
    .replace(/\r?\n/g, "<br>")
}

function csvCell(value: string): string {
  return `"${value.replace(/"/g, "\"\"")}"`
}

export function traceRowsToMarkdown(trace: TraceResult, rows: TraceExportRow[]): string {
  const title = `# Trace: ${trace.column ?? trace.target_node_id}`
  const header = "| Section | Rank | Node ID | Node | Field | Value |\n| --- | ---: | --- | --- | --- | --- |"
  const body = rows.map((row) => (
    `| ${escapeMarkdownCell(row.section)} | ${row.topologicalRank ?? ""} | `
    + `${escapeMarkdownCell(row.nodeId)} | ${escapeMarkdownCell(row.nodeName)} | `
    + `${escapeMarkdownCell(row.field)} | ${escapeMarkdownCell(row.value)} |`
  ))
  return `${title}\n\n${header}\n${body.join("\n")}\n`
}

export function traceRowsToCsv(rows: TraceExportRow[]): string {
  const records = rows.map((row) => [
    row.section,
    row.topologicalRank === null ? "" : String(row.topologicalRank),
    row.nodeId,
    row.nodeName,
    row.field,
    row.value,
  ])
  return [
    ["section", "topological_rank", "node_id", "node_name", "field", "value"],
    ...records,
  ].map((record) => record.map(csvCell).join(",")).join("\r\n") + "\r\n"
}

export function traceToMarkdown(trace: TraceResult): string {
  return traceRowsToMarkdown(trace, buildTraceExportRows(trace))
}

export function traceToCsv(trace: TraceResult): string {
  return traceRowsToCsv(buildTraceExportRows(trace))
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

export function traceRowsToPrintHtml(trace: TraceResult, rows: TraceExportRow[]): string {
  const body = rows.map((row) => (
    "<tr>"
    + `<td>${escapeHtml(row.section)}</td>`
    + `<td>${row.topologicalRank ?? ""}</td>`
    + `<td>${escapeHtml(row.nodeName || row.nodeId)}</td>`
    + `<td>${escapeHtml(row.field)}</td>`
    + `<td>${escapeHtml(row.value)}</td>`
    + "</tr>"
  )).join("")
  return "<!doctype html><html><head><meta charset=\"utf-8\">"
    + `<title>${escapeHtml(`Trace: ${trace.column ?? trace.target_node_id}`)}</title>`
    + "<style>body{font:11px/1.35 system-ui,sans-serif;color:CanvasText}"
    + "table{width:100%;border-collapse:collapse}th,td{padding:4px;border:1px solid GrayText;"
    + "text-align:left;vertical-align:top;overflow-wrap:anywhere}</style></head><body>"
    + `<h1>${escapeHtml(`Trace: ${trace.column ?? trace.target_node_id}`)}</h1>`
    + "<table><thead><tr><th>Section</th><th>Rank</th><th>Node</th><th>Field</th>"
    + `<th>Value</th></tr></thead><tbody>${body}</tbody></table></body></html>`
}

export function printTraceReport(
  trace: TraceResult,
  openWindow: () => Window | null,
): boolean {
  const printWindow = openWindow()
  if (!printWindow) return false
  printWindow.document.open()
  printWindow.document.write(traceRowsToPrintHtml(trace, buildTraceExportRows(trace)))
  printWindow.document.close()
  printWindow.focus()
  printWindow.print()
  return true
}

export async function copyTraceMarkdown(
  trace: TraceResult,
  writeText: (value: string) => Promise<void>,
): Promise<void> {
  await writeText(traceToMarkdown(trace))
}

export function traceExportFilename(trace: TraceResult, extension: "md" | "csv"): string {
  const identity = sanitiseLabelForFilesystem(
    `${trace.target_node_id}-${trace.column ?? `row-${trace.row_index}`}`,
  )
  const timestamp = trace.generated_at.replace(/[:.]/g, "-")
  return `trace-${identity || "result"}-${timestamp}.${extension}`
}
