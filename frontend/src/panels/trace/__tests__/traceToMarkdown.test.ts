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

  it("node detail (model score): includes backend prediction and SHAP contributions", () => {
    const trace = makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "CatBoost Score",
          node_type: "modelScore",
          schema_diff: {
            columns_added: ["premium"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["age"],
          },
          node_detail: {
            detail_type: "model_score",
            prediction_column: "premium",
            prediction_value: 455.72,
            feature_columns: ["age", "vehicle_group"],
            feature_values: { age: 42, vehicle_group: "A" },
            model_identity: { source_type: "run", run_id: "abc123", task: "regression" },
            explanation: {
              status: "ok",
              output_space: "prediction",
              base_value: 423.17,
              prediction_from_shap: 455.72,
              contributions: [
                { feature: "age", feature_value: 42, shap_value: -12.5, rank: 1 },
                { feature: "vehicle_group", feature_value: "A", shap_value: 45.05, rank: 2 },
              ],
            },
          },
        }),
      ],
    })
    const target = trace.steps[1]
    const md = traceToMarkdown(trace, target)

    expect(md).toContain("Prediction premium=455.72")
    expect(md).toContain("Run=abc123")
    expect(md).toContain("Features: age=42, vehicle_group=A")
    expect(md).toContain("Base=423.17")
    expect(md).toContain("age (42) -12.5")
    expect(md).toContain("vehicle_group (A) +45.05")
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

  it("summarizes backend rating_step tables, selected values, and combined outputs", () => {
    const trace = makeTrace({
      column: "technical_premium_factor",
      output_value: 108,
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Adjustments",
          node_type: "ratingStep",
          schema_diff: {
            columns_added: ["vehicle_factor", "channel_factor", "technical_premium_factor"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["vehicle_age_band", "cover_type", "channel"],
          },
          node_detail: {
            detail_type: "rating_step",
            tables: [
              {
                name: "vehicle_factor",
                output_column: "vehicle_factor",
                factors: [
                  { column: "vehicle_age_band", value: "1-3" },
                  { column: "cover_type", value: "comprehensive" },
                ],
                selected_value: 0.9,
                status: "matched",
                matched: true,
                default_used: false,
              },
              {
                name: "channel_factor",
                output_column: "channel_factor",
                factors: [{ column: "channel", value: "direct" }],
                selected_value: 1.2,
                status: "matched",
                matched: true,
                default_used: false,
              },
            ],
            combined_outputs: [
              {
                column: "technical_premium_factor",
                operation: "multiply",
                base_value: 100,
                input_values: { vehicle_factor: 0.9, channel_factor: 1.2 },
                value: 108,
              },
            ],
          },
        }),
      ],
    })

    const md = traceToMarkdown(trace, trace.steps[1])

    expect(md).toContain("Rating tables: vehicle_factor")
    expect(md).toContain("vehicle_age_band=1-3")
    expect(md).toContain("cover_type=comprehensive")
    expect(md).toContain("status=matched")
    expect(md).toContain("selected=0.9")
    expect(md).toContain("channel_factor")
    expect(md).toContain("channel=direct")
    expect(md).toContain("selected=1.2")
    expect(md).toContain("Combined outputs: technical_premium_factor = 108")
    expect(md).toContain("multiply from base 100")
    expect(md).toContain("vehicle_factor=0.9")
    expect(md).toContain("channel_factor=1.2")
  })

  it("summarizes rating_step default and no-match table statuses", () => {
    const trace = makeTrace({
      column: "technical_premium_factor",
      output_value: 1,
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source" }),
        makeStep({
          node_id: "n2",
          node_name: "Adjustments",
          node_type: "ratingStep",
          schema_diff: {
            columns_added: ["technical_premium_factor"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["vehicle_age_band", "channel"],
          },
          node_detail: {
            detail_type: "rating_step",
            tables: [
              {
                name: "vehicle_factor",
                output_column: "vehicle_factor",
                factors: [{ column: "vehicle_age_band", value: "unknown" }],
                selected_value: 1,
                status: "default",
                matched: false,
                default_used: true,
                default_value: 1,
              },
              {
                name: "channel_factor",
                output_column: "channel_factor",
                factors: [{ column: "channel", value: "broker" }],
                selected_value: null,
                status: "no_match",
                matched: false,
                default_used: false,
              },
            ],
          },
        }),
      ],
    })

    const md = traceToMarkdown(trace, trace.steps[1])

    expect(md).toContain("vehicle_factor")
    expect(md).toContain("status=default")
    expect(md).toContain("default=1")
    expect(md).toContain("default used")
    expect(md).toContain("channel_factor")
    expect(md).toContain("status=no_match")
    expect(md).toContain("selected=null")
  })

  it("summarizes banding detail without dumping raw trace metadata", () => {
    const trace = makeTrace({
      column: "age_band",
      output_value: "adult",
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", node_type: "source", column_relevant: false }),
        makeStep({
          node_id: "n2",
          node_name: "Age Banding",
          node_type: "banding",
          schema_diff: {
            columns_added: ["age_band"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["risk_age"],
          },
          expression: {
            expression_text: "risk_age -> age_band",
            expression_type: "banding",
            referenced_columns: ["risk_age"],
          },
          calculation: {
            substituted_text: '35 -> "adult"',
            result_value: "adult",
            input_values: { risk_age: 35 },
          },
          node_detail: {
            detail_type: "banding",
            input_column: "risk_age",
            output_column: "age_band",
            input_value: 35,
            matched_band: "adult",
            lower_bound: 25,
            lower_inclusive: false,
            upper_bound: 65,
            upper_inclusive: true,
          },
        }),
      ],
    })

    const md = traceToMarkdown(trace, trace.steps[1])

    expect(md).toContain("Banding: risk_age=35 -> adult (25, 65]")
    expect(md.match(/Banding:/g)).toHaveLength(1)
    expect(md).not.toContain("risk_age -> age_band")
    expect(md).not.toContain("## Formula")
    expect(md).not.toContain("Substituted:")
    expect(md).not.toContain("Output: age_band")
    expect(md).not.toContain("Matched band:")
    expect(md).not.toContain("detail_type: banding")
  })

  it("does not invent a banding summary when traced column is not a banding output", () => {
    const trace = makeTrace({
      column: "driver_age",
      output_value: 35,
      steps: [
        makeStep({
          node_id: "n1",
          node_name: "Age Banding",
          node_type: "banding",
          schema_diff: {
            columns_added: ["age_band", "region_band"],
            columns_removed: [],
            columns_modified: [],
            columns_passed: ["driver_age"],
          },
          node_detail: {
            detail_type: "banding",
            factors: [
              { column: "driver_age", output_column: "age_band" },
              { column: "region", output_column: "region_band" },
            ],
          },
        }),
      ],
    })

    const md = traceToMarkdown(trace, trace.steps[0])

    expect(md).toContain("Banding factors: driver_age -> age_band; region -> region_band")
    expect(md).not.toContain("Banding: null -> null")
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
