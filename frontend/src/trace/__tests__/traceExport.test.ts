import { describe, expect, it, vi } from "vitest"

import { makeTraceOmission, makeTraceStep } from "../../test-utils/factories"
import { makeTraceDiagnostic } from "../../test-utils/factories"
import { makeTraceResult } from "../../test-utils/factories"
import type { TraceResult } from "../../types/trace"
import { keyText } from "../../utils/answerAddresses"
import {
  buildTraceExportRows,
  copyTraceMarkdown,
  printTraceReport,
  traceExportFilename,
  traceToCsv,
  traceToMarkdown,
} from "../traceExport"

function traceFixture(): TraceResult {
  return makeTraceResult({
    target_node_id: "premium|target",
    row_index: 3,
    column: "technical_premium",
    output_value: 123.456789,
    steps: [
      makeTraceStep({
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
        contributed_columns: [], derivations: [],
        expression: null,
        calculation: null,
        node_detail: null,
        row_lineage_type: "created",
      }),
      makeTraceStep({
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
        contributed_columns: [], derivations: [],
        expression: null,
        calculation: { substituted_text: "100 * 1.23456789", result_value: 123.456789, input_values: { base: 100 } },
        node_detail: {
          detail_type: "rating_step",
          tables: [{ selected_value: 1.1, post_code_output_value: 2.2, default_used: true }],
        },
        row_lineage_type: "passthrough",
      }),
    ],
    omissions: [
      makeTraceOmission({
        node_id: "lookup",
        node_name: "Lookup | branch",
        node_type: "polars",
        topological_rank: 1,
        reason: "duplicate_exact_match",
        diagnostic_index: 0,
      }),
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
      makeTraceDiagnostic({
        code: "ambiguous_row_match",
        severity: "warning",
        reason: "duplicate_exact_match",
        message: "two rows\nmatched",
        node_id: "lookup",
        child_node_id: "rating",
        match_columns: ["quote_id"],
        ignored_columns: [],
        matched_row_indices: [1, 2],
        seed_node_ids: [],
      }),
    ],
    generated_at: "2026-07-23T12:34:56+00:00",
    pipeline_source: "pricing/pipeline.py",
    execution_origin: "trace_cache",
  })
}

describe("trace export projection", () => {
  it("preserves exact numeric, default and omission evidence in topological order", () => {
    const rows = buildTraceExportRows(traceFixture())
    const evidence = rows.filter((row) => row.section === "step" || row.section === "omission")

    expect(evidence.map((row) => row.topologicalRank)).toEqual([
      0, 0, 0, 0, 0, 0, 0, 0, 0,
      1, 1,
      2, 2, 2, 2, 2, 2, 2, 2, 2,
    ])
    expect(rows.filter((row) => row.field === "derivations").map((row) => row.value)).toEqual(["[]", "[]"])
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

  it("exports \"One of N identical rows\" only for a step that is one of several identical rows", () => {
    const trace = traceFixture()
    trace.steps[0] = { ...trace.steps[0], identical_row_count: 3 }
    const rows = buildTraceExportRows(trace)

    expect(rows.filter((row) => row.field === "identical_rows")).toEqual([
      expect.objectContaining({ section: "step", nodeId: "source" }),
    ])
    expect(traceToMarkdown(trace)).toContain("One of 3 identical rows")
    expect(traceToCsv(trace)).toContain("One of 3 identical rows")
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
    const fixture = traceFixture()
    const trace = {
      ...fixture,
      column: "technical premium😀",
      node_addresses: {
        ...fixture.node_addresses,
        [keyText(fixture.target_node_id)]: { occurrence: null, node_id: "premium", label: "premium/target" },
      },
    }

    expect(traceExportFilename(trace, "csv")).toBe(
      "trace-premium_target-technical_premium_-2026-07-23T12-34-56+00-00.csv",
    )
  })

  it("names the traced node by its label, never by the key the trace runs it under", () => {
    const trace = makeTraceResult({
      ...traceFixture(),
      column: null,
      node_addresses: { "premium|target": { occurrence: null, node_id: "premium", label: "Technical premium" } },
    })
    const rows = buildTraceExportRows(trace)

    expect(rows.filter((row) => row.section === "trace" && row.field === "target")).toEqual([{
      section: "trace",
      topologicalRank: null,
      occurrence: "",
      nodeId: "premium",
      nodeName: "Technical premium",
      field: "target",
      value: "Technical premium",
    }])
    expect(rows.some((row) => row.field === "target_node_id")).toBe(false)
    expect(traceToMarkdown(trace).split("\n")[0]).toBe("# Trace: Technical premium")
    expect(traceExportFilename(trace, "md")).toBe("trace-Technical_premium-row-3-2026-07-23T12-34-56+00-00.md")
  })

  it("exports every node row with its submodel, node id and name columns", () => {
    const trace = traceFixture()

    expect(traceToCsv(trace).split("\r\n")[0]).toBe(
      '"section","topological_rank","occurrence","node_id","node_name","field","value"',
    )
    expect(traceToCsv(trace)).toContain('"step","2","","rating","Rating","node_type","ratingStep"')
    const markdown = traceToMarkdown(trace).split("\n")
    expect(markdown).toContain("| Section | Rank | Submodel | Node ID | Node | Field | Value |")
    expect(markdown).toContain("| --- | ---: | --- | --- | --- | --- | --- |")
    expect(markdown).toContain("| omission | 1 |  | lookup | Lookup \\| branch | reason | duplicate_exact_match |")
  })

  it("exports a submodel occurrence's step under its occurrence, its own node id and its label", () => {
    const childKey = "submodel_runtime/pricing/child"
    const trace = makeTraceResult({
      ...traceFixture(),
      // A step names a child with its occurrence; the export has a column for that.
      steps: [makeTraceStep({ node_id: childKey, node_name: "Child rate in pricing", node_type: "polars", topological_rank: 1 })],
      omissions: [],
      correlation_diagnostics: [],
      node_addresses: { [childKey]: { occurrence: "pricing", node_id: "child", label: "Child rate" } },
    })
    const stepRows = buildTraceExportRows(trace).filter((row) => row.section === "step")

    expect(stepRows.length).toBeGreaterThan(0)
    for (const row of stepRows) {
      expect(row).toMatchObject({ occurrence: "pricing", nodeId: "child", nodeName: "Child rate" })
    }
    const csv = traceToCsv(trace)
    expect(csv).toContain('"step","1","pricing","child","Child rate","node_type","polars"')
    expect(traceToMarkdown(trace)).toContain("| step | 1 | pricing | child | Child rate | node_type | polars |")
    // The key appears only where the export says which node it names.
    const naming = buildTraceExportRows(trace).filter((row) => row.value.includes(childKey))
    expect(naming.map((row) => row.field)).toEqual(["node_addresses"])
    expect(JSON.parse(naming[0].value)[childKey]).toEqual({ label: "Child rate", node_id: "child", occurrence: "pricing" })
  })

  it("prints each node row's submodel beside the node", () => {
    const write = vi.fn()
    const printWindow = {
      document: { open: vi.fn(), write, close: vi.fn() },
      focus: vi.fn(),
      print: vi.fn(),
    } as unknown as Window
    const childKey = "submodel_runtime/pricing/child"
    const trace = makeTraceResult({
      ...traceFixture(),
      steps: [makeTraceStep({ node_id: childKey, node_name: "Child rate in pricing", node_type: "polars", topological_rank: 1 })],
      omissions: [],
      correlation_diagnostics: [],
      node_addresses: { [childKey]: { occurrence: "pricing", node_id: "child", label: "Child rate" } },
    })

    expect(printTraceReport(trace, () => printWindow)).toBe(true)
    const html = String(write.mock.calls[0]?.[0])
    expect(html).toContain("<th>Submodel</th><th>Node</th>")
    expect(html).toContain("<td>pricing</td><td>Child rate</td>")
  })
})
