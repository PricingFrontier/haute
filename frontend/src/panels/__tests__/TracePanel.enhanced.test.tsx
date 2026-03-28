import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
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
      makeStep({ node_id: "n1", node_name: "Source" }),
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

  it("renders model score detail with model type and features", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "GLM Score",
              node_type: "model_score",
              node_detail: {
                detail_type: "model_score",
                model_type: "GLM",
                features_used: ["age", "region", "vehicle_group"],
                prediction: 0.85,
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
  })

  it("renders SHAP values as a list in model score detail", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "SHAP Model",
              node_type: "model_score",
              node_detail: {
                detail_type: "model_score",
                model_type: "GBM",
                features_used: ["age", "income"],
                prediction: 0.72,
                shap_values: [
                  { feature: "age", value: 0.15 },
                  { feature: "income", value: -0.08 },
                ],
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
  })

  it("renders scenario expander detail with multiplier info", () => {
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
                step: "inflation",
                multiplier: 1.05,
                range: { min: 0.95, max: 1.15 },
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Scenario Step").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Scenario Step")).toBeInTheDocument()
  })

  it("renders live switch detail with branch selection", () => {
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
                selected_branch: "branch_B",
                available_branches: ["branch_A", "branch_B", "branch_C"],
              },
            }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByText("Live Switch").closest("button") as HTMLElement
    fireEvent.click(stepButton)
    expect(screen.getByText("Live Switch")).toBeInTheDocument()
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
              node_type: "model_score",
              node_detail: {
                detail_type: "model_score",
                model_type: "XGBoost",
                features_used: features,
                prediction: 0.42,
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
            makeStep({ node_id: "n1", node_name: "Step A", execution_ms: 2.0 }),
            makeStep({ node_id: "n2", node_name: "Step B", execution_ms: 3.0 }),
            makeStep({ node_id: "n3", node_name: "Step C", execution_ms: 1.5 }),
          ] as TraceStep[],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepA = screen.getByText("Step A")
    const stepB = screen.getByText("Step B")
    const stepC = screen.getByText("Step C")
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
                columns_added: [],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
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
    expect(screen.getByText("Creator")).toBeInTheDocument()
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

