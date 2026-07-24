import { describe, expect, it, vi } from "vitest"

import type { TraceResult } from "../../types/trace"
import {
  buildTraceExportRows,
  copyTraceMarkdown,
  printTraceReport,
  traceExportFilename,
  traceToCsv,
  traceToMarkdown,
} from "../traceExport"

function traceFixture(): TraceResult {
  return {
    target_node_id: "premium|target",
    row_index: 3,
    column: "technical_premium",
    output_value: 123.456789,
    steps: [
      {
        node_id: "source",
        node_name: "Source",
        node_type: "dataInput",
        topological_rank: 0,
        schema_diff: {
          columns_added: ["base"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
        input_values: {},
        output_values: { base: 100 },
        column_relevant: true,
        expression: null,
        calculation: null,
        node_detail: null,
        row_lineage_type: "created",
      },
      {
        node_id: "rating",
        node_name: "Rating",
        node_type: "ratingStep",
        topological_rank: 2,
        schema_diff: {
          columns_added: ["technical_premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["base"],
        },
        input_values: { base: 100 },
        output_values: { technical_premium: 123.456789 },
        column_relevant: true,
        expression: null,
        calculation: { substituted_text: "100 * 1.23456789", result_value: 123.456789, input_values: { base: 100 } },
        node_detail: {
          detail_type: "rating_step",
          tables: [{ selected_value: 1.1, post_code_output_value: 2.2, default_used: true }],
        },
        row_lineage_type: "passthrough",
      },
    ],
    omissions: [
      {
        node_id: "lookup",
        node_name: "Lookup | branch",
        node_type: "polars",
        topological_rank: 1,
        reason: "duplicate_exact_match",
        diagnostic_index: 0,
      },
    ],
    row_id_column: "quote_id",
    row_id_value: "Q-3",
    total_nodes_in_pipeline: 3,
    nodes_in_trace: 3,
    execution_ms: 4.25,
    waterfall: [
      {
        label: "Rating",
        operation: "base",
        value: 123.456789,
        delta: 0,
        cumulative: 123.456789,
        default_used: true,
      },
    ],
    correlation_diagnostics: [
      {
        code: "ambiguous_row_match",
        severity: "warning",
        reason: "duplicate_exact_match",
        message: "two rows\nmatched",
        node_id: "lookup",
        child_node_id: "rating",
        match_columns: ["quote_id"],
        ignored_columns: [],
        matched_row_indices: [1, 2],
      },
    ],
    generated_at: "2026-07-23T12:34:56+00:00",
    pipeline_source: "pricing/pipeline.py",
    execution_origin: "preview_cache",
  }
}

describe("trace export projection", () => {
  it("preserves exact numeric, default and omission evidence in topological order", () => {
    const rows = buildTraceExportRows(traceFixture())
    const evidence = rows.filter((row) => row.section === "step" || row.section === "omission")

    expect(evidence.map((row) => row.topologicalRank)).toEqual([
      0, 0, 0, 0, 0, 0, 0, 0,
      1, 1,
      2, 2, 2, 2, 2, 2, 2, 2,
    ])
    expect(rows).toContainEqual(expect.objectContaining({
      section: "trace",
      field: "output_value",
      value: "123.456789",
    }))
    expect(rows.find((row) => (
      row.section === "step"
      && row.nodeId === "rating"
      && row.field === "node_detail"
    ))?.value)
      .toContain('"default_used":true')
    expect(rows.find((row) => row.section === "omission" && row.field === "reason")?.value)
      .toBe("duplicate_exact_match")
  })

  it("escapes Markdown and CSV without changing the projected values", () => {
    const markdown = traceToMarkdown(traceFixture())
    const csv = traceToCsv(traceFixture())

    expect(markdown).toContain("Lookup \\| branch")
    expect(markdown).toContain(String.raw`two rows\\nmatched`)
    expect(csv).toContain('"123.456789"')
    expect(csv).toContain(String.raw`two rows\nmatched`)
  })

  it("propagates clipboard failure so the panel can show a persistent error", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard denied"))
    await expect(copyTraceMarkdown(traceFixture(), writeText)).rejects.toThrow("clipboard denied")
    expect(writeText).toHaveBeenCalledWith(traceToMarkdown(traceFixture()))
  })

  it("prints the same escaped projection without touching the live panel DOM", () => {
    const write = vi.fn()
    const printWindow = {
      document: {
        open: vi.fn(),
        write,
        close: vi.fn(),
      },
      focus: vi.fn(),
      print: vi.fn(),
    } as unknown as Window

    expect(printTraceReport(traceFixture(), () => printWindow)).toBe(true)
    expect(write).toHaveBeenCalledOnce()
    const html = String(write.mock.calls[0]?.[0])
    expect(html).toContain("123.456789")
    expect(html).toContain("Lookup | branch")
    expect(html).toContain("&quot;default_used&quot;:true")
    expect(printWindow.print).toHaveBeenCalledOnce()
    expect(printTraceReport(traceFixture(), () => null)).toBe(false)
  })

  it("uses a deterministic filesystem-safe download name", () => {
    expect(traceExportFilename(traceFixture(), "md")).toBe(
      "trace-premium_target-technical_premium-2026-07-23T12-34-56+00-00.md",
    )
  })

  it("routes unsafe identity characters through the shared filesystem sanitizer", () => {
    const trace = {
      ...traceFixture(),
      target_node_id: "premium/target",
      column: "technical premium😀",
    }

    expect(traceExportFilename(trace, "csv")).toBe(
      "trace-premium_target-technical_premium_-2026-07-23T12-34-56+00-00.csv",
    )
  })
})
