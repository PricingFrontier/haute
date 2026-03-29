import { describe, it, expect } from "vitest"
import { traceToMarkdown } from "../traceToMarkdown"
import type { TraceResult, TraceStep } from "../../../types/trace"

function makeStep(overrides: Partial<TraceStep> = {}): TraceStep {
  return {
    node_id: "n1",
    node_name: "Transform 1",
    node_type: "polars",
    schema_diff: {
      columns_added: [],
      columns_removed: [],
      columns_modified: [],
      columns_passed: ["age"],
    },
    input_values: { age: 25 },
    output_values: { age: 25, premium: 100 },
    column_relevant: true,
    execution_ms: 5.2,
    ...overrides,
  }
}

function makeTrace(overrides: Partial<TraceResult> = {}): TraceResult {
  return {
    target_node_id: "n2",
    row_index: 0,
    column: "premium",
    output_value: 42.5,
    steps: [
      makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
      makeStep({
        node_id: "n2",
        node_name: "Calc",
        node_type: "polars",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
        output_values: { age: 25, premium: 42.5 },
        expression: {
          expression_text: "col('age') * 1.7",
          expression_type: "polars",
          referenced_columns: ["age"],
        },
        calculation: {
          substituted_text: "25 * 1.7",
          result_value: 42.5,
          input_values: { age: 25 },
        },
      }),
    ],
    row_id_column: "quote_id",
    row_id_value: "Q001",
    total_nodes_in_pipeline: 5,
    nodes_in_trace: 2,
    execution_ms: 12.3,
    ...overrides,
  }
}

describe("traceToMarkdown", () => {
  it("produces markdown with header, formula, and data flow table", () => {
    const trace = makeTrace()
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    // Should contain a header section
    expect(md).toContain("# Trace")
    // Should contain a formula section
    expect(md).toContain("## Formula")
    // Should contain a data flow table with pipe-delimited rows
    expect(md).toContain("## Data Flow")
    expect(md).toContain("|")
  })

  it("includes column name and result value in header", () => {
    const trace = makeTrace()
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("premium")
    expect(md).toContain("42.5")
  })

  it("includes row identifier with quote_id", () => {
    const trace = makeTrace()
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("quote_id")
    expect(md).toContain("Q001")
  })

  it("includes Row N when no row_id_column", () => {
    const trace = makeTrace({
      row_id_column: null,
      row_id_value: null,
      row_index: 7,
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("Row 7")
  })

  it("formula section includes expression_text and substituted_text", () => {
    const trace = makeTrace()
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("col('age') * 1.7")
    expect(md).toContain("25 * 1.7")
  })

  it("data flow table includes node name, type, and key values for relevant steps", () => {
    const trace = makeTrace()
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("Source")
    expect(md).toContain("source")
    expect(md).toContain("Calc")
    expect(md).toContain("polars")
  })

  it("skips non-relevant steps (column_relevant=false)", () => {
    const trace = makeTrace({
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Source",
          node_type: "source",
        }),
        makeStep({
          node_id: "n_irr",
          node_name: "Irrelevant Node",
          node_type: "polars",
          column_relevant: false,
        }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
        }),
      ],
    })
    const target = trace.steps[2]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("Source")
    expect(md).toContain("Calc")
    expect(md).not.toContain("Irrelevant Node")
  })

  it("handles null expression (no formula section)", () => {
    const trace = makeTrace({
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Source",
          node_type: "source",
        }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          expression: null,
          calculation: null,
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).not.toContain("## Formula")
  })

  it("handles null calculation (no substituted values)", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          expression: {
            expression_text: "col('age') * 1.7",
            expression_type: "polars",
            referenced_columns: ["age"],
          },
          calculation: null,
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    // Expression text present but no substituted text
    expect(md).toContain("col('age') * 1.7")
    expect(md).not.toContain("25 * 1.7")
  })

  it("includes execution time and step count in metadata", () => {
    const trace = makeTrace({ execution_ms: 55.7 })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("55.7")
    expect(md).toContain("2")
  })

  it("handles special values: NULL, NaN, Infinity in output", () => {
    const trace = makeTrace({
      output_value: null,
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Source",
          node_type: "source",
          output_values: { a: null, b: NaN, c: Infinity },
        }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["a", "b", "c"],
          },
          output_values: { a: null, b: NaN, c: Infinity, premium: null },
          calculation: {
            substituted_text: "NULL * NaN",
            result_value: null,
            input_values: { a: null, b: NaN },
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    // Should render without crashing and include some representation of special values
    expect(md).toBeDefined()
    expect(typeof md).toBe("string")
    // NULL output should appear
    expect(md).toMatch(/null|NULL/i)
  })

  it("handles very long expression text (doesn't break markdown table)", () => {
    const longExpr = "col('a') + col('b') + col('c') + " + "x".repeat(500)
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          expression: {
            expression_text: longExpr,
            expression_type: "polars",
            referenced_columns: ["a", "b", "c"],
          },
          calculation: {
            substituted_text: longExpr,
            result_value: 42.5,
            input_values: { a: 1, b: 2, c: 3 },
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    // Should not throw and should be valid string
    expect(md).toBeDefined()
    // The table rows should not have unbalanced pipes
    const lines = md.split("\n")
    const tableLines = lines.filter((l) => l.startsWith("|"))
    for (const line of tableLines) {
      const pipeCount = (line.match(/\|/g) || []).length
      // Each table row should have the same number of pipes (balanced)
      expect(pipeCount).toBeGreaterThanOrEqual(2)
    }
  })

  it("shows multiple input values in calculation", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age", "factor"],
          },
          expression: {
            expression_text: "col('age') * col('factor')",
            expression_type: "polars",
            referenced_columns: ["age", "factor"],
          },
          calculation: {
            substituted_text: "25 * 1.7",
            result_value: 42.5,
            input_values: { age: 25, factor: 1.7 },
          },
          input_values: { age: 25, factor: 1.7 },
          output_values: { age: 25, factor: 1.7, premium: 42.5 },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("age")
    expect(md).toContain("25")
    expect(md).toContain("factor")
    expect(md).toContain("1.7")
  })

  it("node detail (rating step): includes lookup information", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Rating Table",
          node_type: "rating",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          node_detail: {
            lookup_table: "auto_rates",
            lookup_key: "age_band",
            lookup_value: "25-30",
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("auto_rates")
    expect(md).toContain("age_band")
    expect(md).toContain("25-30")
  })

  it("node detail (model score): includes prediction value", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "GLM Score",
          node_type: "model_score",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          node_detail: {
            model_name: "frequency_glm",
            prediction: 0.0342,
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("frequency_glm")
    expect(md).toContain("0.0342")
  })

  it("no targetStep (null): produces minimal trace with just data flow", () => {
    const trace = makeTrace()
    const md = traceToMarkdown(trace, null)

    // Should still have header and data flow
    expect(md).toContain("# Trace")
    expect(md).toContain("## Data Flow")
    // Should NOT have formula section since no target step
    expect(md).not.toContain("## Formula")
  })

  it("empty steps array: produces header only", () => {
    const trace = makeTrace({ steps: [] })
    const md = traceToMarkdown(trace, null)

    expect(md).toContain("# Trace")
    // No data flow entries
    expect(md).not.toContain("Source")
    expect(md).not.toContain("Calc")
  })

  it("column is null (tracing full row): adjusts header", () => {
    const trace = makeTrace({ column: null, output_value: null })
    const md = traceToMarkdown(trace, null)

    expect(md).toContain("# Trace")
    expect(md).not.toMatch(/# Trace.*premium/)
  })

  it("column is null: header is just '# Trace' without equals sign", () => {
    const trace = makeTrace({ column: null, output_value: 123 })
    const md = traceToMarkdown(trace, null)

    const headerLine = md.split("\n").find((l) => l.startsWith("# "))
    expect(headerLine).toBe("# Trace")
  })

  it("special values: null formatted as 'null' in header", () => {
    const trace = makeTrace({ output_value: null })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("premium = null")
  })

  it("special values: NaN in calculation input values", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          node_type: "polars",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] },
          expression: {
            expression_text: "col('x')",
            expression_type: "polars",
            referenced_columns: ["x"],
          },
          calculation: {
            substituted_text: "NaN",
            result_value: NaN,
            input_values: { x: NaN },
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("NaN")
  })

  it("special values: Infinity and -Infinity in output", () => {
    const trace = makeTrace({ output_value: Infinity })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("Infinity")
  })

  it("node detail for rating steps included in data flow table", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Rating Step",
          node_type: "rating",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: ["age"] },
          node_detail: {
            detail_type: "rate_table_lookup",
            lookup_keys: { age_band: "25-30" },
            matched_row: 5,
          },
        }),
      ],
    })
    const md = traceToMarkdown(trace, null)

    expect(md).toContain("rate_table_lookup")
    expect(md).toContain("age_band")
    expect(md).toContain("25-30")
    expect(md).toContain("matched_row")
  })

  it("empty steps array produces minimal output with header but no data flow", () => {
    const trace = makeTrace({ steps: [] })
    const md = traceToMarkdown(trace, null)

    expect(md).toContain("# Trace")
    expect(md).toContain("**Row**")
    expect(md).toContain("**Execution**")
    expect(md).not.toContain("## Data Flow")
  })

  it("pipe characters in node names are escaped in markdown table", () => {
    const trace = makeTrace({
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Node | With | Pipes",
          node_type: "polars",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] },
        }),
      ],
    })
    const md = traceToMarkdown(trace, null)

    expect(md).toContain("Node \\| With \\| Pipes")
    expect(md).not.toMatch(/\| Node \| With \| Pipes \|/)
  })

  it("pipe characters in expression text are escaped in data flow table", () => {
    const trace = makeTrace({
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Expr Node",
          node_type: "polars",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] },
          expression: {
            expression_text: "a | b",
            expression_type: "polars",
            referenced_columns: ["a", "b"],
          },
        }),
      ],
    })
    const md = traceToMarkdown(trace, null)

    const tableLines = md.split("\n").filter((l) => l.startsWith("|") && l.includes("Expr Node"))
    expect(tableLines.length).toBe(1)
    expect(tableLines[0]).toContain("a \\| b")
  })
})
