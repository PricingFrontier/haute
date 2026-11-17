import type { RuntimeNodeKey } from "../generated/api-contracts.generated"
import type {
  TraceOmission,
  TraceResult,
  TraceStep,
} from "../types/trace"
import { keyText } from "../utils/answerAddresses"
import { sanitiseLabelForFilesystem } from "../utils/apiInputPorts"

/** A node as an export names it: by its address, never by the key the trace runs it under. */
interface ExportNode {
  /** The submodel occurrence the node is in; empty for a node of the pipeline. */
  occurrence: string
  /** The node's id where it is authored: in the pipeline, or in the occurrence's definition. */
  nodeId: string
  /** Its name; a submodel child's authored label, since the occurrence has a column of its own. */
  nodeName: string
}

export interface TraceExportRow extends ExportNode {
  section: "provenance" | "trace" | "step" | "omission" | "waterfall" | "diagnostic"
  topologicalRank: number | null
  field: string
  value: string
}

const NO_NODE: ExportNode = { occurrence: "", nodeId: "", nodeName: "" }

function exportNode(trace: TraceResult, key: RuntimeNodeKey, nodeName: string): ExportNode {
  const address = trace.node_addresses[keyText(key)]
  return {
    occurrence: address?.occurrence ?? "",
    nodeId: address?.node_id ?? "",
    nodeName: address?.occurrence ? address.label : nodeName,
  }
}

/** The label of the node the trace explains. */
function targetLabel(trace: TraceResult): string {
  return trace.node_addresses[keyText(trace.target_node_id)]?.label ?? ""
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
  node: ExportNode = NO_NODE,
): TraceExportRow {
  return {
    section,
    topologicalRank: evidence?.topological_rank ?? null,
    ...node,
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
    // The evidence keeps the keys the trace names nodes by (a derivation's sources, a
    // diagnostic's nodes); this row gives each its address and label.
    fieldRow("provenance", "node_addresses", trace.node_addresses),
    fieldRow("trace", "target", targetLabel(trace), undefined, exportNode(trace, trace.target_node_id, targetLabel(trace))),
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
    const node = exportNode(trace, item.node_id, item.node_name)
    if (isOmission(item)) {
      rows.push(
        fieldRow("omission", "reason", item.reason, item, node),
        fieldRow("omission", "diagnostic_index", item.diagnostic_index, item, node),
      )
      continue
    }
    rows.push(
      fieldRow("step", "node_type", item.node_type, item, node),
      fieldRow("step", "schema_diff", item.schema_diff, item, node),
      fieldRow("step", "input_values", item.input_values, item, node),
      fieldRow("step", "output_values", item.output_values, item, node),
      fieldRow("step", "expression", item.expression, item, node),
      fieldRow("step", "calculation", item.calculation, item, node),
      fieldRow("step", "derivations", item.derivations, item, node),
      fieldRow("step", "node_detail", item.node_detail, item, node),
      fieldRow("step", "row_lineage_type", item.row_lineage_type, item, node),
    )
    if (typeof item.identical_row_count === "number") {
      rows.push(
        fieldRow("step", "identical_rows", `One of ${item.identical_row_count} identical rows`, item, node),
      )
    }
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
  const title = `# Trace: ${trace.column ?? targetLabel(trace)}`
  const header = "| Section | Rank | Submodel | Node ID | Node | Field | Value |\n| --- | ---: | --- | --- | --- | --- | --- |"
  const body = rows.map((row) => (
    `| ${escapeMarkdownCell(row.section)} | ${row.topologicalRank ?? ""} | `
    + `${escapeMarkdownCell(row.occurrence)} | ${escapeMarkdownCell(row.nodeId)} | ${escapeMarkdownCell(row.nodeName)} | `
    + `${escapeMarkdownCell(row.field)} | ${escapeMarkdownCell(row.value)} |`
  ))
  return `${title}\n\n${header}\n${body.join("\n")}\n`
}

export function traceRowsToCsv(rows: TraceExportRow[]): string {
  const records = rows.map((row) => [
    row.section,
    row.topologicalRank === null ? "" : String(row.topologicalRank),
    row.occurrence,
    row.nodeId,
    row.nodeName,
    row.field,
    row.value,
  ])
  return [
    ["section", "topological_rank", "occurrence", "node_id", "node_name", "field", "value"],
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
    + `<td>${escapeHtml(row.occurrence)}</td>`
    + `<td>${escapeHtml(row.nodeName || row.nodeId)}</td>`
    + `<td>${escapeHtml(row.field)}</td>`
    + `<td>${escapeHtml(row.value)}</td>`
    + "</tr>"
  )).join("")
  return "<!doctype html><html><head><meta charset=\"utf-8\">"
    + `<title>${escapeHtml(`Trace: ${trace.column ?? targetLabel(trace)}`)}</title>`
    + "<style>body{font:11px/1.35 system-ui,sans-serif;color:CanvasText}"
    + "table{width:100%;border-collapse:collapse}th,td{padding:4px;border:1px solid GrayText;"
    + "text-align:left;vertical-align:top;overflow-wrap:anywhere}</style></head><body>"
    + `<h1>${escapeHtml(`Trace: ${trace.column ?? targetLabel(trace)}`)}</h1>`
    + "<table><thead><tr><th>Section</th><th>Rank</th><th>Submodel</th><th>Node</th><th>Field</th>"
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
    `${targetLabel(trace)}-${trace.column ?? `row-${trace.row_index}`}`,
  )
  const timestamp = trace.generated_at.replace(/[:.]/g, "-")
  return `trace-${identity || "result"}-${timestamp}.${extension}`
}
