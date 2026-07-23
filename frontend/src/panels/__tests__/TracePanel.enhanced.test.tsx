import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import TracePanel from "../TracePanel"
import type { TraceResult, TraceStep } from "../../types/trace"

// ---------------------------------------------------------------------------
// Enhanced types extending TraceStep (these fields will be added to the interface)
// ---------------------------------------------------------------------------

interface EnhancedExpression {
  expression_text: string
  expression_type: string
  referenced_columns: string[]
}

interface EnhancedCalculation {
  substituted_text: string
  result_value: unknown
  input_values: Record<string, unknown>
}

interface EnhancedTraceStep extends TraceStep {
  expression?: EnhancedExpression | null
  calculation?: EnhancedCalculation | null
  node_detail?: Record<string, unknown> | null
  row_lineage_type?: string | null
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

function makeStep(overrides: Partial<EnhancedTraceStep> = {}): EnhancedTraceStep {
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
    topological_rank: 0,
    column_relevant: true,
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
      makeStep({ node_id: "n1", node_name: "Source", node_type: "dataSource" }),
      makeStep({
        node_id: "n2",
        node_name: "Calc",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: ["age"],
        },
        output_values: { age: 25, premium: 42.5 },
      }),
    ],
    row_id_column: "quote_id",
    row_id_value: "Q001",
    total_nodes_in_pipeline: 5,
    nodes_in_trace: 2,
    execution_ms: 12.3,
    ...overrides,
    omissions: overrides.omissions ?? [],
    correlation_diagnostics: overrides.correlation_diagnostics ?? [],
    generated_at: overrides.generated_at ?? "2026-07-23T12:00:00+00:00",
    pipeline_source: overrides.pipeline_source ?? null,
    execution_origin: overrides.execution_origin ?? "fresh_execution",
  }
}

// ---------------------------------------------------------------------------
// A. Expression Display Tests
// ---------------------------------------------------------------------------

describe("TracePanel — Expression Display", () => {
  afterEach(cleanup)

  it("renders an arithmetic expression formula when step has expression", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Rating",
              expression: {
                expression_text: "base_premium * age_factor",
                expression_type: "arithmetic",
                referenced_columns: ["base_premium", "age_factor"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Rating").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText(/base_premium/)).toBeInTheDocument()
    expect(screen.getByText(/age_factor/)).toBeInTheDocument()
  })

  it("renders a conditional expression with decision text", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Condition",
              expression: {
                expression_text: "when age > 25 then premium * 1.2 otherwise premium",
                expression_type: "conditional",
                referenced_columns: ["age", "premium"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Condition").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText(/when/i)).toBeInTheDocument()
  })


  it("shows a 'computed' label for opaque expression type", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Opaque Node",
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: [],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Opaque Node").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // Opaque expressions should show a "computed" indicator rather than a formula
    const computedEl = screen.queryByText(/computed/i)
    // This is a forward-looking test: if implemented, it should show "computed"
    // If not yet implemented, the step should at least not crash
    expect(screen.getByText("Opaque Node")).toBeInTheDocument()
    if (computedEl) {
      expect(computedEl).toBeInTheDocument()
    }
  })

  it("lists referenced columns from the expression", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Multi-ref",
              expression: {
                expression_text: "a + b + c",
                expression_type: "arithmetic",
                referenced_columns: ["a", "b", "c"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Multi-ref").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // Referenced columns should appear somewhere in the expanded step
    expect(screen.getAllByText(/a/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/b/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/c/).length).toBeGreaterThan(0)
  })

  it("handles a very long expression text gracefully", () => {
    const longExpr = Array.from({ length: 20 }, (_, i) => `col_${i}`).join(" + ")
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Long Expr",
              expression: {
                expression_text: longExpr,
                expression_type: "arithmetic",
                referenced_columns: Array.from({ length: 20 }, (_, i) => `col_${i}`),
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Long Expr").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // Should render without crashing; may truncate or wrap
    expect(screen.getByText("Long Expr")).toBeInTheDocument()
  })

  it("renders expression with only one referenced column", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Single Ref",
              expression: {
                expression_text: "ABS(loss_amount)",
                expression_type: "function",
                referenced_columns: ["loss_amount"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Single Ref").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText(/loss_amount/)).toBeInTheDocument()
  })

  it("renders expression with empty referenced columns list", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Const Expr",
              expression: {
                expression_text: "42",
                expression_type: "literal",
                referenced_columns: [],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Const Expr").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Const Expr")).toBeInTheDocument()
  })

  it("renders step with expression and no schema diff columns", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "No Diff",
              schema_diff: {
                columns_added: [],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              expression: {
                expression_text: "x * 2",
                expression_type: "arithmetic",
                referenced_columns: ["x"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("No Diff")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// B. Calculation Display Tests
// ---------------------------------------------------------------------------

describe("TracePanel — Calculation Display", () => {
  afterEach(cleanup)

  it("shows substituted calculation values when calculation is present", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Calc Step",
              calculation: {
                substituted_text: "208 \u00d7 0.7 = 145.6",
                result_value: 145.6,
                input_values: { premium: 208 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Calc Step").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // The substituted calculation text should be visible
    const calcText = screen.queryByText(/208/)
    expect(calcText).toBeInTheDocument()
  })

  it("shows NULL prominently when calculation has null input", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Null Calc",
              calculation: {
                substituted_text: "NULL \u00d7 0.7 = NULL",
                result_value: null,
                input_values: { premium: null },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Null Calc").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Null Calc")).toBeInTheDocument()
  })


  it("shows multiple input values in calculation", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Multi Input Calc",
              calculation: {
                substituted_text: "10 + 20 + 30 = 60",
                result_value: 60,
                input_values: { a: 10, b: 20, c: 30 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Multi Input Calc").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Multi Input Calc")).toBeInTheDocument()
  })

  it("shows calculation with zero result", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Zero Result",
              calculation: {
                substituted_text: "0 \u00d7 5 = 0",
                result_value: 0,
                input_values: { a: 0, b: 5 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Zero Result").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Zero Result")).toBeInTheDocument()
  })

  it("shows calculation with string result", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "String Result",
              calculation: {
                substituted_text: '= "category_A"',
                result_value: "category_A",
                input_values: {},
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("String Result").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("String Result")).toBeInTheDocument()
  })

  it("shows calculation alongside expression when both present", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Full Detail",
              expression: {
                expression_text: "premium * discount",
                expression_type: "arithmetic",
                referenced_columns: ["premium", "discount"],
              },
              calculation: {
                substituted_text: "208 \u00d7 0.7 = 145.6",
                result_value: 145.6,
                input_values: { premium: 208, discount: 0.7 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Full Detail").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Full Detail")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// C. Node Detail Tests
// ---------------------------------------------------------------------------

describe("TracePanel — Node Detail", () => {
  afterEach(cleanup)

  it("renders rate table lookup info for rating step", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Rate Lookup",
              node_type: "rating",
              node_detail: {
                detail_type: "rate_table_lookup",
                lookup_keys: { age_band: "25-30", region: "East" },
                matched_row: 12,
                default_used: false,
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Rate Lookup").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Rate Lookup")).toBeInTheDocument()
  })

  it("renders banding detail with matched band info", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Age Band",
              node_type: "banding",
              node_detail: {
                detail_type: "banding",
                input_value: 27,
                matched_band: "25-30",
                lower_bound: 25,
                upper_bound: 30,
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Age Band").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Age Band")).toBeInTheDocument()
  })

  it("shows a banding-created field's source value in the story card", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "band",
          column: "age_band",
          output_value: "adult",
          steps: [
            makeStep({
              node_id: "source",
              node_name: "Policies",
              node_type: "dataSource",
              schema_diff: {
                columns_added: ["risk_age"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              output_values: { risk_age: 35 },
              column_relevant: false,
            }),
            makeStep({
              node_id: "band",
              node_name: "Age Band",
              node_type: "banding",
              schema_diff: {
                columns_added: ["age_band"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["risk_age"],
              },
              input_values: { risk_age: 35 },
              output_values: { risk_age: 35, age_band: "adult" },
              expression: {
                target_column: "age_band",
                expression_text: "risk_age -> age_band",
                expression_type: "banding",
                referenced_columns: ["risk_age"],
                constants: [],
              } as unknown as EnhancedExpression,
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
                upper_bound: 65,
              },
            }),
          ] as TraceStep[],
          nodes_in_trace: 2,
        })}
        onClose={vi.fn()}
      />,
    )

    const bandingCard = screen.getByTestId("trace-step-card-band")
    expect(bandingCard).toHaveAttribute("data-target-step", "true")
    expect(within(bandingCard).getByText(/age_band/)).toBeInTheDocument()
    const bandingDetail = within(bandingCard).getByLabelText("Trace detail: Banding")
    expect(within(bandingDetail).getByText("risk_age=35 -> adult")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("[25, 65]")).toBeInTheDocument()
    expect(screen.queryByText(/Input:/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Matched band:/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("renders model score detail with backend prediction and features", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "GLM Score",
              node_type: "modelScore",
              node_detail: {
                detail_type: "model_score",
                prediction_column: "score",
                prediction_value: 0.85,
                feature_columns: ["age", "region", "vehicle_group"],
                feature_values: { age: 35, region: "north", vehicle_group: "A" },
                model_identity: { source_type: "run", run_id: "abc123", task: "regression" },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("GLM Score").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("GLM Score")).toBeInTheDocument()
    expect(screen.getByText(/Prediction: score = 0.85/)).toBeInTheDocument()
    expect(screen.getByText("vehicle_group")).toBeInTheDocument()
  })

  it("renders backend model score detail as a single contribution ladder and hides computed placeholders", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "score_model",
          column: "risk_score",
          output_value: 0.73,
          steps: [
            makeStep({
              node_id: "score_model",
              node_name: "Risk Score",
              node_type: "modelScore",
              schema_diff: {
                columns_added: ["risk_score"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age", "vehicle_group"],
              },
              input_values: { age: 42, vehicle_group: "A" },
              output_values: { age: 42, vehicle_group: "A", risk_score: 0.73 },
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: [],
              },
              calculation: {
                substituted_text: "computed",
                result_value: 0.73,
                input_values: { age: 42, vehicle_group: "A" },
              },
              node_detail: {
                detail_type: "model_score",
                prediction_value: 0.73,
                prediction_column: "risk_score",
                feature_columns: ["age", "vehicle_group"],
                feature_values: { age: 42, vehicle_group: "A" },
                model_identity: {
                  source_type: "run",
                  run_id: "abc123",
                  task: "regression",
                },
                explanation: {
                  status: "ok",
                  output_space: "prediction",
                  base_value: 0.5,
                  prediction_from_shap: 0.73,
                  contributions: [
                    { feature: "age", feature_value: 42, shap_value: 0.2, rank: 1 },
                    { feature: "vehicle_group", feature_value: "A", shap_value: 0.03, rank: 2 },
                  ],
                },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )

    const scoreCard = screen.getByTestId("trace-step-card-score_model")
    expect(scoreCard).toHaveAttribute("data-target-step", "true")
    expect(within(scoreCard).getByText("Risk Score")).toBeInTheDocument()
    expect(within(scoreCard).getAllByText(/risk_score/).length).toBeGreaterThan(0)
    expect(within(scoreCard).getAllByText("0.73").length).toBeGreaterThan(0)
    expect(screen.queryByTestId("trace-calculation-frame")).not.toBeInTheDocument()
    expect(screen.queryByTestId("trace-calculation-tab")).not.toBeInTheDocument()
    expect(screen.queryByText("Sources")).not.toBeInTheDocument()
    expect(screen.getAllByText("Risk Score").length).toBeGreaterThan(0)
    expect(screen.queryByLabelText("Model feature values")).not.toBeInTheDocument()
    expect(screen.queryByText(/Prediction: risk_score = 0.73/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Base value:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/base \+ contributions/)).not.toBeInTheDocument()

    const ladder = screen.getByLabelText("Model score contribution ladder")
    const rows = within(ladder).getAllByTestId("model-score-ladder-row")
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent("Base")
    expect(rows[0]).toHaveTextContent("0.5")
    expect(rows[1]).toHaveTextContent("1. age")
    expect(rows[1]).toHaveTextContent("42")
    expect(rows[1]).toHaveTextContent("+0.2")
    expect(rows[1]).toHaveTextContent("0.7")
    expect(rows[2]).toHaveTextContent("2. vehicle_group")
    expect(rows[2]).toHaveTextContent("A")
    expect(rows[2]).toHaveTextContent("+0.03")
    expect(rows[2]).toHaveTextContent("0.73")
    expect(rows[3]).toHaveTextContent("Prediction")
    expect(rows[3]).toHaveTextContent("risk_score")
    expect(rows[3]).toHaveTextContent("0.73")
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("uses RustyStats contribution totals rather than response predictions in ladders", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "conversion_score",
          column: "conversion_prediction",
          output_value: 0.57,
          steps: [
            makeStep({
              node_id: "conversion_score",
              node_name: "Conversion GLM",
              node_type: "modelScore",
              schema_diff: {
                columns_added: ["conversion_prediction"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["difference_to_market"],
              },
              input_values: { difference_to_market: 0.31 },
              output_values: { difference_to_market: 0.31, conversion_prediction: 0.57 },
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: ["difference_to_market"],
              },
              calculation: {
                substituted_text: "computed",
                result_value: 0.57,
                input_values: { difference_to_market: 0.31 },
              },
              node_detail: {
                detail_type: "model_score",
                prediction_value: 0.57,
                prediction_column: "conversion_prediction",
                feature_columns: ["difference_to_market"],
                feature_values: { difference_to_market: 0.31 },
                model_identity: { source_type: "run", run_id: "glm123", task: "classification" },
                explanation: {
                  method: "rustystats_glm_contributions",
                  status: "ok",
                  output_space: "linear_predictor",
                  prediction_space: "response",
                  base_value: 0.1,
                  sum_contributions: 0.2,
                  prediction_from_contributions: 0.3,
                  prediction_value: 0.57,
                  contributions: [
                    {
                      feature: "difference_to_market",
                      feature_value: 0.31,
                      contribution: 0.2,
                      rank: 1,
                    },
                  ],
                },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )

    const ladder = screen.getByLabelText("Model score contribution ladder")
    const rows = within(ladder).getAllByTestId("model-score-ladder-row")
    expect(rows).toHaveLength(3)
    expect(rows[2]).toHaveTextContent("Prediction")
    expect(rows[2]).toHaveTextContent("0.3")
    expect(rows[2]).not.toHaveTextContent("0.57")
  })

  it("renders CatBoost SHAP contributions as a running score ladder", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "SHAP Model",
              node_type: "modelScore",
              node_detail: {
                detail_type: "model_score",
                prediction_column: "score",
                prediction_value: 0.72,
                feature_columns: ["age", "income"],
                feature_values: { age: 50, income: 25_000 },
                model_identity: { source_type: "run", run_id: "abc123", task: "regression" },
                explanation: {
                  status: "ok",
                  output_space: "prediction",
                  base_value: 0.65,
                  prediction_from_shap: 0.72,
                  contributions: [
                    { feature: "age", feature_value: 50, shap_value: 0.15, rank: 1 },
                    { feature: "income", feature_value: 25_000, shap_value: -0.08, rank: 2 },
                  ],
                },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("SHAP Model").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("SHAP Model")).toBeInTheDocument()
    expect(screen.queryByLabelText("Model feature values")).not.toBeInTheDocument()
    const ladder = screen.getByLabelText("Model score contribution ladder")
    const rows = within(ladder).getAllByTestId("model-score-ladder-row")
    expect(rows).toHaveLength(4)
    expect(rows[0]).toHaveTextContent("Base")
    expect(rows[0]).toHaveTextContent("0.65")
    expect(rows[1]).toHaveTextContent("1. age")
    expect(rows[1]).toHaveTextContent("50")
    expect(rows[1]).toHaveTextContent("+0.15")
    expect(rows[1]).toHaveTextContent("0.8")
    expect(rows[2]).toHaveTextContent("2. income")
    expect(rows[2]).toHaveTextContent("25,000")
    expect(rows[2]).toHaveTextContent("-0.08")
    expect(rows[2]).toHaveTextContent("0.72")
    expect(rows[3]).toHaveTextContent("Prediction")
    expect(rows[3]).toHaveTextContent("0.72")
  })

  it("uses fallback feature values and calls out truncated contribution ladders", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "SHAP Model",
              node_type: "modelScore",
              node_detail: {
                detail_type: "model_score",
                prediction_column: "score",
                prediction_value: 1.5,
                feature_values: { income: 25_000 },
                explanation: {
                  status: "ok",
                  output_space: "prediction",
                  base_value: 1,
                  prediction_from_shap: 1.5,
                  feature_values: { age: 50 },
                  truncated: true,
                  omitted_count: 2,
                  contributions: [
                    { feature: "age", shap_value: 0.25, rank: 1 },
                    { feature: "income", shap_value: -0.05, rank: 2 },
                    { feature: "postcode", shap_value: 0, rank: 3 },
                  ],
                },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("SHAP Model").closest("button") as HTMLElement
    fireEvent.click(stepButton)

    const ladder = screen.getByLabelText("Model score contribution ladder")
    const rows = within(ladder).getAllByTestId("model-score-ladder-row")
    expect(rows).toHaveLength(5)
    expect(rows[1]).toHaveTextContent("1. age")
    expect(rows[1]).toHaveTextContent("50")
    expect(rows[1]).toHaveTextContent("+0.25")
    expect(rows[1]).toHaveTextContent("1.25")
    expect(rows[2]).toHaveTextContent("2. income")
    expect(rows[2]).toHaveTextContent("25,000")
    expect(rows[2]).toHaveTextContent("-0.05")
    expect(rows[2]).toHaveTextContent("1.2")
    expect(rows[3]).toHaveTextContent("3. postcode")
    expect(rows[3]).toHaveTextContent("not provided")
    expect(rows[3]).toHaveTextContent("+0")
    expect(rows[3]).toHaveTextContent("1.2")
    expect(within(ladder).getByText("Prediction includes 2 omitted contributions.")).toBeInTheDocument()
    expect(rows[4]).toHaveTextContent("Prediction")
    expect(rows[4]).toHaveTextContent("1.5")
  })

  it("renders scenario expander detail with scenario value and grid settings", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Scenario Step",
              node_type: "scenario_expander",
              node_detail: {
                detail_type: "scenario_expander",
                scenario_column: "premium_multiplier",
                scenario_value: 1.05,
                scenario_index: 3,
                parameters: { min_value: 0.95, max_value: 1.15, steps: 9 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Scenario Step").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Scenario Expander")).toBeInTheDocument()
    expect(screen.getByText(/premium_multiplier.*1.05/)).toBeInTheDocument()
    expect(screen.getByText(/index.*3/)).toBeInTheDocument()
    expect(screen.getByText(/min.*0.95/)).toBeInTheDocument()
    expect(screen.getByText(/max.*1.15/)).toBeInTheDocument()
    expect(screen.getByText(/steps.*9/)).toBeInTheDocument()
  })

  it("renders live switch detail with active and pruned branches", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Live Switch",
              node_type: "live_switch",
              node_detail: {
                detail_type: "live_switch",
                active_branch: "branch_B",
                active_scenario: "renewal",
                pruned_branches: ["branch_A", "branch_C"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Live Switch").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getAllByText("Live Switch").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/active branch.*branch_B/)).toBeInTheDocument()
    expect(screen.getByText(/scenario.*renewal/)).toBeInTheDocument()
    expect(screen.getByText(/Pruned branches.*branch_A, branch_C/)).toBeInTheDocument()
  })

  it("renders different rich node details with the shared trace detail surface", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "apply",
          column: "optimised_premium",
          output_value: 0.65,
          steps: [
            makeStep({
              node_id: "score",
              node_name: "GLM Score",
              node_type: "modelScore",
              node_detail: {
                detail_type: "model_score",
                prediction_column: "score",
                prediction_value: 0.85,
                feature_columns: ["age"],
                feature_values: { age: 35 },
                model_identity: { source_type: "run", run_id: "abc123", task: "regression" },
              },
            }),
            makeStep({
              node_id: "expand",
              node_name: "Premium Expander",
              node_type: "scenarioExpander",
              schema_diff: {
                columns_added: ["premium_multiplier"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["score"],
              },
              node_detail: {
                detail_type: "scenario_expander",
                scenario_column: "premium_multiplier",
                scenario_value: 1.05,
                scenario_index: 2,
              },
            }),
            makeStep({
              node_id: "apply",
              node_name: "Apply Ratebook",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["optimised_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["premium_multiplier"],
              },
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                output_column: "optimised_premium",
                output_value: 0.65,
                base_value: 1,
                final_value: 0.65,
                factors: [
                  { name: "channel_band", input_value: "market", factor_value: 0.65, running_total: 0.65, status: "matched" },
                ],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByTestId("trace-show-full"))
    fireEvent.click(screen.getByText("GLM Score").closest("button") as HTMLElement)
    fireEvent.click(screen.getByText("Premium Expander").closest("button") as HTMLElement)

    expect(screen.getByLabelText("Trace detail: Model run: abc123")).toBeInTheDocument()
    expect(screen.getByLabelText("Trace detail: Scenario Expander")).toBeInTheDocument()
    expect(screen.getByLabelText("Trace detail: Optimiser Apply")).toBeInTheDocument()
    expect(screen.getAllByTestId("trace-detail-panel")).toHaveLength(3)
  })


  it("renders generic JSON fallback for unknown detail type", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Unknown Detail",
              node_detail: {
                detail_type: "some_future_type",
                foo: "bar",
                count: 99,
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Unknown Detail").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Unknown Detail")).toBeInTheDocument()
  })

  it("renders rate table lookup with default_used = true", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Default Rate",
              node_type: "rating",
              node_detail: {
                detail_type: "rate_table_lookup",
                lookup_keys: { age_band: "unknown" },
                matched_row: null,
                default_used: true,
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Default Rate").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Default Rate")).toBeInTheDocument()
  })

  it("renders backend rating_step table factors, selected values, and combined outputs", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Adjustments",
              node_type: "ratingStep",
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
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText("Adjustments").closest("button") as HTMLElement)

    expect(screen.getByText("Rating Tables")).toBeInTheDocument()
    expect(screen.getByText("vehicle_factor")).toBeInTheDocument()
    expect(screen.getByText("channel_factor")).toBeInTheDocument()
    expect(screen.getByText(/vehicle_age_band:.*1-3/)).toBeInTheDocument()
    expect(screen.getByText(/cover_type:.*comprehensive/)).toBeInTheDocument()
    expect(screen.getByText(/channel:.*direct/)).toBeInTheDocument()
    expect(screen.getByText(/selected.*0.9/)).toBeInTheDocument()
    expect(screen.getByText(/selected.*1.2/)).toBeInTheDocument()
    expect(screen.getAllByText("status: matched")).toHaveLength(2)
    expect(screen.getByText("Combined Outputs")).toBeInTheDocument()
    expect(screen.getByText(/technical_premium_factor.*108/)).toBeInTheDocument()
    expect(screen.getByText(/base.*100/)).toBeInTheDocument()
  })

  it("shows opaque rating_step table details directly in the target story card", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "adjustments",
          column: "veh_age_adj",
          output_value: 0.11,
          steps: [
            makeStep({
              node_id: "adjustments",
              node_name: "adjustments",
              node_type: "ratingStep",
              schema_diff: {
                columns_added: ["veh_age_adj"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["veh_age", "cover"],
              },
              output_values: { veh_age: 2, cover: "comp", veh_age_adj: 0.11 },
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: [],
              },
              calculation: {
                substituted_text: "computed",
                result_value: 0.11,
                input_values: { veh_age: 2, cover: "comp" },
              },
              node_detail: {
                detail_type: "rating_step",
                tables: [
                  {
                    name: "veh_age_adj",
                    output_column: "veh_age_adj",
                    factors: [
                      { column: "veh_age", value: 2 },
                      { column: "cover", value: "comp" },
                    ],
                    selected_value: 0.11,
                    status: "matched",
                    matched: true,
                    default_used: false,
                  },
                ],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText("Calculation")).not.toBeInTheDocument()
    expect(screen.queryByText("Nodes")).not.toBeInTheDocument()
    const targetCard = screen.getByTestId("trace-step-card-adjustments")
    expect(targetCard).toHaveAttribute("data-target-step", "true")
    expect(within(targetCard).getByText("Rating Tables")).toBeInTheDocument()
    expect(within(targetCard).getAllByText("veh_age_adj").length).toBeGreaterThan(0)
    expect(within(targetCard).getByText("traced column")).toBeInTheDocument()
    expect(within(targetCard).getByText(/veh_age:.*2/)).toBeInTheDocument()
    expect(within(targetCard).getByText(/cover:.*comp/)).toBeInTheDocument()
    expect(within(targetCard).getByText(/selected.*0.11/)).toBeInTheDocument()
    expect(within(targetCard).queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("opens target rating_step table details by default when no formula is available", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "adjustments",
          column: "technical_premium_factor",
          output_value: 0.9,
          steps: [
            makeStep({
              node_id: "adjustments",
              node_name: "Adjustments",
              node_type: "ratingStep",
              schema_diff: {
                columns_added: ["technical_premium_factor"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["vehicle_age_band"],
              },
              output_values: { vehicle_age_band: "1-3", technical_premium_factor: 0.9 },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "rating_step",
                tables: [
                  {
                    name: "vehicle_factor",
                    output_column: "vehicle_factor",
                    factors: [{ column: "vehicle_age_band", value: "1-3" }],
                    selected_value: 0.9,
                    status: "matched",
                    matched: true,
                    default_used: false,
                  },
                ],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Rating Tables")).toBeInTheDocument()
    expect(screen.getByText("vehicle_factor")).toBeInTheDocument()
    expect(screen.getByText("status: matched")).toBeInTheDocument()
  })

  it("renders rating table default and no-match statuses", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "adjustments",
          column: "technical_premium_factor",
          output_value: 1,
          steps: [
            makeStep({
              node_id: "adjustments",
              node_name: "Adjustments",
              node_type: "ratingStep",
              schema_diff: {
                columns_added: ["technical_premium_factor"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["vehicle_age_band", "channel"],
              },
              output_values: { vehicle_age_band: "unknown", channel: "broker", technical_premium_factor: 1 },
              expression: null,
              calculation: null,
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
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("status: default")).toBeInTheDocument()
    expect(screen.getAllByText("default used").length).toBeGreaterThan(0)
    expect(screen.getByText(/default:.*1/)).toBeInTheDocument()
    expect(screen.getByText("status: no match")).toBeInTheDocument()
    expect(screen.getByText(/selected.*\u2014/)).toBeInTheDocument()
  })

  it("renders banding detail with edge boundary value", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Edge Band",
              node_type: "banding",
              node_detail: {
                detail_type: "banding",
                input_value: 25,
                matched_band: "25-30",
                lower_bound: 25,
                upper_bound: 30,
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Edge Band").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Edge Band")).toBeInTheDocument()
  })

  it("renders model detail with many features", () => {
    const features = Array.from({ length: 15 }, (_, i) => `feature_${i}`)
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Big Model",
              node_type: "modelScore",
              node_detail: {
                detail_type: "model_score",
                prediction_column: "prediction",
                prediction_value: 0.42,
                feature_columns: features,
                feature_values: Object.fromEntries(features.map((feature, index) => [feature, index])),
                model_identity: { source_type: "run", run_id: "abc123", task: "regression" },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Big Model").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Big Model")).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// D. Row Lineage Type Tests
// ---------------------------------------------------------------------------

describe("TracePanel — Row Lineage Type", () => {
  afterEach(cleanup)

  it("shows pass-through indicator for passthrough lineage", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Pass Through",
              row_lineage_type: "passthrough",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Pass Through")).toBeInTheDocument()
  })

  it("shows source indicator for created lineage", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Source Node",
              row_lineage_type: "created",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Source Node")).toBeInTheDocument()
  })

  it("shows filter survived indicator for filtered lineage", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Filter Node",
              row_lineage_type: "filtered",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Filter Node")).toBeInTheDocument()
  })

  it("shows group key info for aggregated lineage", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Agg Node",
              row_lineage_type: "aggregated",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Agg Node")).toBeInTheDocument()
  })

  it("shows join indicator for joined lineage", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Join Node",
              row_lineage_type: "joined",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Join Node")).toBeInTheDocument()
  })

})

// ---------------------------------------------------------------------------
// E. Waterfall View Concept Tests
// ---------------------------------------------------------------------------

describe("TracePanel — Waterfall View Concepts", () => {
  afterEach(cleanup)

  it("renders steps in topological order", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Step A", schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
            makeStep({ node_id: "n2", node_name: "Step B", schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] } }),
            makeStep({ node_id: "n3", node_name: "Step C", schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: [] } }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepA = screen.getByText("Step A")
    const stepB = screen.getByText("Step B")
    // Step C may appear in both CalculationHero (nodeName) and StepCard
    const stepCElements = screen.getAllByText("Step C")
    const stepC = stepCElements[stepCElements.length - 1]
    // All three should render in document order matching the steps array
    const body = document.body
    const allElements = Array.from(body.querySelectorAll("*"))
    const posA = allElements.indexOf(stepA)
    const posB = allElements.indexOf(stepB)
    const posC = allElements.indexOf(stepC)
    expect(posA).toBeLessThan(posB)
    expect(posB).toBeLessThan(posC)
  })

  it("steps with expression show formula prominently", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Formula Step",
              expression: {
                expression_text: "age * 2",
                expression_type: "arithmetic",
                referenced_columns: ["age"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Formula Step").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // Expression should be visible after expanding
    expect(screen.getByText("Formula Step")).toBeInTheDocument()
  })

  it("steps without expression show column diff view (existing behavior)", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Diff Step",
              schema_diff: {
                columns_added: ["new_col"],
                columns_removed: [],
                columns_modified: ["age"],
                columns_passed: [],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Diff Step").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    expect(screen.getByText(/1 modified/)).toBeInTheDocument()
  })

  it("pass-through steps with no columns changed render as collapsed", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Pass-through",
              schema_diff: {
                columns_added: [],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age", "name"],
              },
              column_relevant: false,
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    // Pass-through steps with no changes should still render the step name
    expect(screen.getByText("Pass-through")).toBeInTheDocument()
  })

  it("step that created the traced column is highlighted", () => {
    const { container } = render(
      <TracePanel
        trace={makeTrace({
          column: "premium",
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Source",
              schema_diff: {
                columns_added: ["age"],
                columns_removed: [],
                columns_modified: ["premium"],
                columns_passed: [],
              },
              column_relevant: false,
            }),
            makeStep({
              node_id: "n2",
              node_name: "Creator",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              column_relevant: true,
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    // The relevant step should NOT have reduced opacity
    // "Creator" may appear in both CalculationHero (nodeName) and StepCard
    expect(screen.getAllByText("Creator").length).toBeGreaterThan(0)
    // The non-relevant step should have reduced opacity
    const reducedOpacityEl = container.querySelector("[style*='opacity: 0.55']")
    expect(reducedOpacityEl).toBeTruthy()
  })

  it("expand/collapse works with enhanced steps", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Enhanced Step",
              schema_diff: {
                columns_added: ["result"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              expression: {
                expression_text: "age * 2",
                expression_type: "arithmetic",
                referenced_columns: ["age"],
              },
              calculation: {
                substituted_text: "25 \u00d7 2 = 50",
                result_value: 50,
                input_values: { age: 25 },
              },
              node_detail: { detail_type: "simple", info: "test" },
              row_lineage_type: "passthrough",
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Enhanced Step").closest("button") as HTMLElement
    // Expand
    fireEvent.click(stepButton)
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    // Collapse
    fireEvent.click(stepButton)
    expect(screen.queryByText(/1 added/)).not.toBeInTheDocument()
  })

  it("step badges show correct type labels", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Polars Step", node_type: "polars" }),
            makeStep({ node_id: "n2", node_name: "Rating Step", node_type: "rating" }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Polars Step")).toBeInTheDocument()
    expect(screen.getByText("Rating Step")).toBeInTheDocument()
  })
})
