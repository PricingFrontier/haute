import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react"
import TracePanel from "../TracePanel"
import type { TraceResult, TraceStep } from "../../types/trace"
import { downloadTextFile } from "../editors/shared/tableClipboard"

vi.mock("../editors/shared/tableClipboard", () => ({
  downloadTextFile: vi.fn(() => true),
}))

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
      makeStep({ node_id: "n1", node_name: "Source", node_type: "dataInput" }),
      makeStep({
        node_id: "n2",
        node_name: "Calc",
        schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: ["age"] },
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

describe("TracePanel", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.mocked(downloadTextFile).mockReset()
    vi.mocked(downloadTextFile).mockReturnValue(true)
  })

  it("renders the Trace header with column name", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByText(/Trace.*premium/)).toBeInTheDocument()
  })

  it("renders the output value", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByTestId("trace-target-summary")).toHaveTextContent("42.5")
  })

  it("uses the shared download helper and surfaces its guarded failure", async () => {
    vi.mocked(downloadTextFile).mockReturnValue(false)
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Download trace as Markdown" }))

    await waitFor(() => expect(downloadTextFile).toHaveBeenCalledTimes(1))
    expect(screen.getByRole("alert")).toHaveTextContent("The trace could not be exported")
  })

  it("surfaces a model-score enrichment failure instead of calculation output", () => {
    const modelStep = makeStep({
      node_id: "model",
      node_name: "Model",
      node_type: "modelScore",
      node_detail: {
        detail_type: "model_score",
        error: "Model enrichment failed",
      } as unknown as TraceStep["node_detail"],
      expression: {
        expression_text: "prediction",
        expression_type: "arithmetic",
        referenced_columns: ["prediction"],
      },
      calculation: {
        substituted_text: "prediction = 0.9",
        result_value: 0.9,
        input_values: { prediction: 0.9 },
      },
    })
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "model",
          column: "prediction",
          output_value: 0.9,
          steps: [modelStep],
        })}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /Model.*SCORING/ }))
    expect(screen.getByRole("alert")).toHaveTextContent("Model enrichment failed")
    expect(screen.queryByText("prediction = 0.9")).not.toBeInTheDocument()
  })

  it("hides execution time from hero (developer telemetry moved)", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    // Timing is hidden from the hero display (Fix 7)
    // Per-step timing is still shown in step cards
    expect(screen.queryByText(/12\.3\s*ms/)).not.toBeInTheDocument()
  })

  it("hides step count from hero (developer telemetry moved)", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    // Step count is hidden from the hero display (Fix 7)
    expect(screen.queryByText("2 steps")).not.toBeInTheDocument()
  })

  it("renders row_id info when available", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByText("quote_id")).toBeInTheDocument()
    expect(screen.getByText("Q001")).toBeInTheDocument()
  })

  it("renders Row N when no row_id_column", () => {
    render(<TracePanel trace={makeTrace({ row_id_column: null, row_id_value: null, row_index: 3 })} onClose={vi.fn()} />)
    expect(screen.getByText(/Row 3/)).toBeInTheDocument()
  })

  it("renders nodes in trace count", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByText(/2 of 5 nodes/)).toBeInTheDocument()
  })

  it("surfaces row correlation ambiguity diagnostics", () => {
    render(
      <TracePanel
        trace={makeTrace({
          correlation_diagnostics: [
            {
              code: "ambiguous_row_match",
              severity: "warning",
              reason: "relaxed_match_ambiguous",
              message:
                "Row correlation for node 'source' for child node 'aggregate' is ambiguous: 2 relaxed matches on columns region.",
              node_id: "source",
              child_node_id: "aggregate",
              match_strategy: "relaxed",
              match_columns: ["region"],
              ignored_columns: ["premium"],
              matched_row_count: 2,
              matched_row_indices: [0, 1],
            },
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const alert = screen.getByTestId("trace-correlation-diagnostics")
    expect(alert).toHaveTextContent("Row correlation warning")
    expect(alert).toHaveTextContent("2 relaxed matches")
    expect(alert).toHaveTextContent("source")
    expect(alert).toHaveTextContent("aggregate")
  })

  it("interleaves a trace omission between surrounding successful steps", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: null,
          steps: [
            makeStep({ node_id: "source", node_name: "Source", topological_rank: 0 }),
            makeStep({ node_id: "target", node_name: "Target", topological_rank: 2 }),
          ],
          omissions: [{
            node_id: "lookup",
            node_name: "Lookup",
            node_type: "polars",
            topological_rank: 1,
            reason: "duplicate_exact_match",
            diagnostic_index: 0,
          }],
          correlation_diagnostics: [{
            code: "ambiguous_row_match",
            severity: "warning",
            reason: "duplicate_exact_match",
            message: "Two lookup rows matched.",
            node_id: "lookup",
            child_node_id: "target",
            match_columns: ["policy_id"],
            ignored_columns: [],
            matched_row_indices: [0, 1],
          }],
          nodes_in_trace: 3,
        })}
        onClose={vi.fn()}
      />,
    )

    const evidence = screen.getByTestId("trace-story").querySelectorAll(
      "[data-testid^='trace-step-card-'], [data-testid^='trace-omission-']",
    )
    expect(Array.from(evidence, (item) => item.getAttribute("data-testid"))).toEqual([
      "trace-step-card-source",
      "trace-omission-lookup",
      "trace-step-card-target",
    ])
    expect(screen.getByTestId("trace-omission-lookup")).toHaveTextContent("trace gap")
    expect(screen.queryByTestId("trace-correlation-diagnostics")).not.toBeInTheDocument()
  })

  it.each([
    {
      name: "rich rating",
      step: makeStep({
        node_id: "target",
        node_name: "Rating",
        node_type: "ratingStep",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
        node_detail: {
          detail_type: "rating_step",
          tables: [{ name: "rate", selected_value: 1.1, default_used: false }],
        },
      }),
    },
    {
      name: "generic calculation",
      step: makeStep({
        node_id: "target",
        node_name: "Calculation",
        schema_diff: {
          columns_added: ["premium"],
          columns_removed: [],
          columns_modified: [],
          columns_passed: [],
        },
        expression: {
          expression_text: "base * factor",
          expression_type: "arithmetic",
          referenced_columns: ["base", "factor"],
        },
        calculation: {
          substituted_text: "100 * 1.2",
          result_value: 120,
          input_values: { base: 100, factor: 1.2 },
        },
      }),
    },
  ])("renders a reconciliation error for a $name target", ({ step }) => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "target",
          output_value: 120,
          steps: [step],
          waterfall: {
            error: "expected 120 but accumulated 119",
            error_type: "WaterfallReconciliationError",
          },
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The calculation breakdown does not match the traced result.",
    )
    expect(screen.getByText("Technical details")).toBeInTheDocument()
  })

  it("close button calls onClose", () => {
    const onClose = vi.fn()
    render(<TracePanel trace={makeTrace()} onClose={onClose} />)
    fireEvent.click(screen.getByLabelText("Close trace"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("renders step names in order", () => {
    render(<TracePanel trace={makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", schema_diff: { columns_added: ["age"], columns_removed: [], columns_modified: ["premium"], columns_passed: [] } }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: ["age"] },
          output_values: { age: 25, premium: 42.5 },
        }),
      ],
    })} onClose={vi.fn()} />)
    const source = screen.getByText("Source")
    // "Calc" may appear twice: once in CalculationHero (nodeName) and once in the StepCard
    const calcElements = screen.getAllByText("Calc")
    expect(source).toBeInTheDocument()
    expect(calcElements.length).toBeGreaterThan(0)
    // Source should appear before the last Calc element (the StepCard one) in DOM order
    const body = document.body
    const allElements = Array.from(body.querySelectorAll("*"))
    const sourcePos = allElements.indexOf(source)
    const calcPos = allElements.indexOf(calcElements[calcElements.length - 1])
    expect(sourcePos).toBeLessThan(calcPos)
  })

  it("renders step indexes starting from 1", () => {
    render(<TracePanel trace={makeTrace({
      steps: [
        makeStep({ node_id: "n1", node_name: "Source", schema_diff: { columns_added: ["age"], columns_removed: [], columns_modified: ["premium"], columns_passed: [] } }),
        makeStep({
          node_id: "n2",
          node_name: "Calc",
          schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: ["age"] },
          output_values: { age: 25, premium: 42.5 },
        }),
      ],
    })} onClose={vi.fn()} />)
    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
  })

  it("does not render fabricated per-step execution time", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Only Step" }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument()
  })

  it("renders banding-created fields with the source value instead of computed", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "banding",
          column: "age_band",
          output_value: "young",
          steps: [
            makeStep({
              node_id: "data",
              node_name: "data",
              node_type: "dataInput",
              schema_diff: {
                columns_added: ["driver_age"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              output_values: { driver_age: 22 },
              column_relevant: false,
            }),
            makeStep({
              node_id: "banding",
              node_name: "Age banding",
              node_type: "banding",
              schema_diff: {
                columns_added: ["age_band"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["driver_age"],
              },
              input_values: { driver_age: 22 },
              output_values: { driver_age: 22, age_band: "young" },
              expression: {
                expression_text: "driver_age -> age_band",
                expression_type: "banding",
                referenced_columns: ["driver_age"],
              },
              calculation: {
                substituted_text: '22 -> "young"',
                result_value: "young",
                input_values: { driver_age: 22 },
                input_sources: {
                  driver_age: {
                    node_name: "data",
                    result_value: 22,
                  },
                },
              },
              node_detail: {
                detail_type: "banding",
                input_column: "driver_age",
                output_column: "age_band",
                input_value: 22,
                matched_band: "young",
                rule_index: 0,
                is_default: false,
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByText(/computed/i)).not.toBeInTheDocument()
    const bandingDetail = screen.getByLabelText("Trace detail: Banding")
    expect(within(bandingDetail).getByText("age_band")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("driver_age=22 -> young")).toBeInTheDocument()
    expect(screen.queryByText(/Matched band:/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Output:/i)).not.toBeInTheDocument()
  })

  it("renders upstream multi-factor banding context inside a ratebook trace", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "ratebook",
          column: "optimised_premium",
          output_value: 0.58,
          steps: [
            makeStep({
              node_id: "banding",
              node_name: "age_veh_banding",
              node_type: "banding",
              schema_diff: {
                columns_added: ["proposer_age_band", "vehicle_age_band", "channel_band"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["proposer_age", "vehicle_age", "channel", "unrelated"],
              },
              input_values: {
                proposer_age: 49,
                vehicle_age: 9,
                channel: "gocompare",
                unrelated: "noise",
              },
              output_values: {
                proposer_age: 49,
                vehicle_age: 9,
                channel: "gocompare",
                unrelated: "noise",
                proposer_age_band: "49-55",
                vehicle_age_band: "8-9",
                channel_band: "gocompare",
              },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "banding",
                factors: [
                  {
                    input_column: "proposer_age",
                    output_column: "proposer_age_band",
                    input_value: 49,
                    matched_band: "49-55",
                    lower_bound: 48,
                    upper_bound: 55,
                    lower_inclusive: false,
                    upper_inclusive: true,
                    status: "matched",
                  },
                  {
                    input_column: "vehicle_age",
                    output_column: "vehicle_age_band",
                    input_value: 9,
                    matched_band: "8-9",
                    lower_bound: 7,
                    upper_bound: 9,
                    lower_inclusive: false,
                    upper_inclusive: true,
                    status: "matched",
                  },
                  {
                    input_column: "channel",
                    output_column: "channel_band",
                    input_value: "gocompare",
                    matched_band: "gocompare",
                    status: "matched",
                  },
                ],
              },
            }),
            makeStep({
              node_id: "ratebook",
              node_name: "apply_ratebook",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["optimised_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["proposer_age_band", "vehicle_age_band", "channel_band"],
              },
              input_values: {
                proposer_age_band: "49-55",
                vehicle_age_band: "8-9",
                channel_band: "gocompare",
              },
              output_values: {
                proposer_age_band: "49-55",
                vehicle_age_band: "8-9",
                channel_band: "gocompare",
                optimised_premium: 0.58,
              },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                output_column: "optimised_premium",
                output_value: 0.58,
                base_value: 1,
                final_value: 0.58,
                factors: [
                  {
                    name: "proposer_age_band",
                    input_value: "49-55",
                    factor: "proposer_age_band",
                    factor_value: 0.94,
                    running_total: 0.94,
                    status: "matched",
                  },
                  {
                    name: "vehicle_age_band",
                    input_value: "8-9",
                    factor: "vehicle_age_band",
                    factor_value: 0.89,
                    running_total: 0.84,
                    status: "matched",
                  },
                  {
                    name: "channel_band",
                    input_value: "gocompare",
                    factor: "channel_band",
                    factor_value: 0.7,
                    running_total: 0.58,
                    status: "matched",
                  },
                ],
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const bandingCard = screen.getByTestId("trace-step-card-banding")
    if (!within(bandingCard).queryByLabelText("Trace detail: Banding")) {
      fireEvent.click(within(bandingCard).getByRole("button"))
    }
    const bandingDetail = within(bandingCard).getByLabelText("Trace detail: Banding")

    expect(within(bandingDetail).getByText("3 banded outputs")).toBeInTheDocument()
    expect(within(bandingDetail).getByLabelText("Banding outputs")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("proposer_age_band")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("proposer_age=49")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("49-55")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("(48, 55]")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("vehicle_age=9")).toBeInTheDocument()
    expect(within(bandingDetail).getByText("channel=gocompare")).toBeInTheDocument()
    expect(within(bandingCard).queryByText("noise")).not.toBeInTheDocument()
  })

  it("omits unrelated optimiser input branches from the focused ratebook trace", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "ratebook",
          column: "optimised_premium",
          output_value: 0.58,
          steps: [
            makeStep({
              node_id: "optimiser_inputs",
              node_name: "optimiser_inputs",
              node_type: "dataInput",
              schema_diff: {
                columns_added: ["quote_id", "unused_objective", "unused_constraint"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              output_values: {
                quote_id: "Q1",
                unused_objective: 123,
                unused_constraint: 0.42,
              },
              column_relevant: false,
            }),
            makeStep({
              node_id: "unused_transform",
              node_name: "unused_transform",
              node_type: "polars",
              schema_diff: {
                columns_added: ["difference_to_market"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["quote_id", "unused_objective", "unused_constraint", "competitor_premium"],
              },
              input_values: {
                quote_id: "Q1",
                unused_objective: 123,
                unused_constraint: 0.42,
                competitor_premium: 273.09,
              },
              output_values: {
                quote_id: "Q1",
                unused_objective: 123,
                unused_constraint: 0.42,
                competitor_premium: 273.09,
                difference_to_market: 0.21,
              },
              column_relevant: false,
            }),
            makeStep({
              node_id: "banding",
              node_name: "age_veh_banding",
              node_type: "banding",
              schema_diff: {
                columns_added: ["proposer_age_band"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["proposer_age"],
              },
              input_values: { proposer_age: 49 },
              output_values: { proposer_age: 49, proposer_age_band: "49-55" },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "banding",
                factors: [
                  {
                    input_column: "proposer_age",
                    output_column: "proposer_age_band",
                    input_value: 49,
                    matched_band: "49-55",
                    lower_bound: 48,
                    upper_bound: 55,
                    lower_inclusive: false,
                    upper_inclusive: true,
                  },
                ],
              },
            }),
            makeStep({
              node_id: "ratebook",
              node_name: "apply_ratebook",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["optimised_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["proposer_age_band", "competitor_premium", "difference_to_market"],
              },
              input_values: {
                proposer_age_band: "49-55",
                competitor_premium: 273.09,
                difference_to_market: 0.21,
              },
              output_values: {
                proposer_age_band: "49-55",
                competitor_premium: 273.09,
                difference_to_market: 0.21,
                optimised_premium: 0.58,
              },
              calculation: {
                substituted_text: "optimised_premium = selected ratebook",
                result_value: 0.58,
                input_values: {
                  proposer_age_band: "49-55",
                  competitor_premium: 273.09,
                  difference_to_market: 0.21,
                },
                input_sources: {
                  proposer_age_band: { node_name: "age_veh_banding" },
                  competitor_premium: { node_name: "unused_transform" },
                  difference_to_market: { node_name: "unused_transform" },
                },
              },
              expression: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                output_column: "optimised_premium",
                output_value: 0.58,
                base_value: 1,
                final_value: 0.58,
                factors: [
                  {
                    name: "proposer_age_band",
                    input_value: "49-55",
                    factor: "proposer_age_band",
                    factor_value: 0.58,
                    running_total: 0.58,
                    status: "matched",
                  },
                ],
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.queryByTestId("trace-step-card-optimiser_inputs")).not.toBeInTheDocument()
    expect(screen.queryByTestId("trace-step-card-unused_transform")).not.toBeInTheDocument()
    expect(screen.queryByText("unused_objective")).not.toBeInTheDocument()
    expect(screen.queryByTestId("trace-hidden-toggle")).not.toBeInTheDocument()
    expect(screen.getByTestId("trace-show-full")).toHaveTextContent("show full trace")
    expect(screen.getByTestId("trace-step-card-banding")).toBeInTheDocument()
    expect(screen.getByTestId("trace-step-card-ratebook")).toBeInTheDocument()
    expect(screen.queryByText("competitor_premium")).not.toBeInTheDocument()
    expect(screen.queryByText("difference_to_market")).not.toBeInTheDocument()
  })

  it("keeps bulk source-origin rows collapsed by default when they only add fields", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "rating",
          column: "premium",
          output_value: 500,
          steps: [
            makeStep({
              node_id: "batch_quotes",
              node_name: "batch_quotes",
              node_type: "dataInput",
              schema_diff: {
                columns_added: [
                  "quote_id",
                  "quote_version",
                  "annual_mileage",
                  "business_use",
                  "cover_type",
                ],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              input_values: {},
              output_values: {
                quote_id: "QUO-2026-000000024",
                quote_version: 1,
                annual_mileage: 9000,
                business_use: null,
                cover_type: "comprehensive",
              },
            }),
            makeStep({
              node_id: "rating",
              node_name: "rating",
              node_type: "polars",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["annual_mileage"],
              },
              input_values: { annual_mileage: 9000 },
              output_values: { annual_mileage: 9000, premium: 500 },
              calculation: {
                substituted_text: "annual_mileage * factor",
                result_value: 500,
                input_values: { annual_mileage: 9000 },
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByTestId("trace-step-card-batch_quotes")).toBeInTheDocument()
    expect(screen.queryByTestId("trace-step-body-batch_quotes")).not.toBeInTheDocument()
    expect(screen.queryByText("business_use")).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("batch_quotes").closest("button") as HTMLElement)
    expect(screen.getByTestId("trace-step-body-batch_quotes")).toBeInTheDocument()
    expect(screen.getByText("business_use")).toBeInTheDocument()
  })

  it("renders source-origin columns with the source node instead of computed", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "policies",
          column: "premium",
          output_value: 123.45,
          steps: [
            makeStep({
              node_id: "policies",
              node_name: "Policy Source",
              node_type: "dataInput",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              input_values: {},
              output_values: { premium: 123.45 },
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: [],
              },
              calculation: {
                substituted_text: "computed",
                result_value: 123.45,
                input_values: {},
              },
              row_lineage_type: "created",
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Source node")).toBeInTheDocument()
    expect(screen.getAllByText("Policy Source").length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
    expect(screen.getByTestId("trace-step-body-policies")).toBeInTheDocument()
  })

  it("renders expander-created columns with the expander node instead of computed", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "expand",
          column: "premium_multiplier",
          output_value: 0.92,
          steps: [
            makeStep({
              node_id: "expand",
              node_name: "Premium Expander",
              node_type: "scenarioExpander",
              schema_diff: {
                columns_added: ["scenario_index", "premium_multiplier"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["quote_id", "premium"],
              },
              input_values: { quote_id: "Q001", premium: 500 },
              output_values: {
                quote_id: "Q001",
                premium: 500,
                scenario_index: 3,
                premium_multiplier: 0.92,
              },
              expression: {
                expression_text: "",
                expression_type: "opaque",
                referenced_columns: [],
              },
              calculation: {
                substituted_text: "computed",
                result_value: 0.92,
                input_values: {},
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Source node")).toBeInTheDocument()
    expect(screen.getAllByText("Premium Expander").length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
    expect(screen.getByTestId("trace-step-body-expand")).toBeInTheDocument()
  })

  it("renders optimiser apply online candidates with a selected marker and no baseline marker", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "optimiser",
          column: "retention_offer",
          output_value: 0.15,
          steps: [
            makeStep({
              node_id: "optimiser",
              node_name: "Offer optimiser",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["retention_offer"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["quote_id"],
              },
              input_values: { quote_id: "Q42" },
              output_values: { quote_id: "Q42", retention_offer: 0.15 },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "online",
                output_column: "retention_offer",
                output_value: 0.15,
                quote_id_column: "quote_id",
                quote_id_value: "Q42",
                scenario_index_column: "scenario_index",
                scenario_value_column: "offer",
                objective_column: "expected_margin",
                candidates: [
                  {
                    scenario_index: 0,
                    scenario_value: 0,
                    objective: 80,
                    decision_score: 80,
                    selected: false,
                    is_baseline: true,
                    constraints: { expected_loss: 80 },
                    linearised_constraints: { expected_loss: 80 },
                    lambda_terms: { expected_loss: 0 },
                  },
                  {
                    scenario_index: 1,
                    scenario_value: 0.15,
                    objective: 95,
                    decision_score: 91,
                    selected: true,
                    is_baseline: false,
                    constraints: { expected_loss: 20 },
                    linearised_constraints: { expected_loss: 20 },
                    lambda_terms: { expected_loss: -4 },
                  },
                  {
                    scenario_index: 2,
                    scenario_value: 0.3,
                    objective: 100,
                    decision_score: 80,
                    selected: false,
                    is_baseline: false,
                    constraints: { expected_loss: 100 },
                    linearised_constraints: { expected_loss: 100 },
                    lambda_terms: { expected_loss: -20 },
                  },
                ],
                lambdas: { expected_loss: 0.2 },
                constraints: {
                  expected_loss: {
                    spec: { max: 30 },
                    lambda: 0.2,
                    linearised_column: "linearised_expected_loss",
                    lambda_term_column: "lambda_term_expected_loss",
                  },
                },
                selected: {
                  scenario_index: 1,
                  scenario_value: 0.15,
                  objective: 95,
                  decision_score: 91,
                  selected: true,
                  is_baseline: false,
                  constraints: { expected_loss: 20 },
                  linearised_constraints: { expected_loss: 20 },
                  lambda_terms: { expected_loss: -4 },
                },
                baseline: {
                  scenario_index: 0,
                  scenario_value: 0,
                  objective: 80,
                  decision_score: 80,
                  selected: false,
                  is_baseline: true,
                  constraints: { expected_loss: 80 },
                  linearised_constraints: { expected_loss: 80 },
                  lambda_terms: { expected_loss: 0 },
                },
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Optimiser Apply")).toBeInTheDocument()
    expect(screen.getByLabelText("Optimiser candidate curve")).toBeInTheDocument()
    expect(screen.getByText("3 candidates")).toBeInTheDocument()
    expect(screen.getByText("Selected scenario")).toBeInTheDocument()
    expect(screen.getByText(/retention_offer.*0.15/)).toBeInTheDocument()
    expect(screen.getByText(/quote_id.*Q42/)).toBeInTheDocument()
    expect(screen.getAllByText("selected").length).toBeGreaterThan(0)
    expect(screen.queryByText("baseline")).not.toBeInTheDocument()
    expect(screen.getByText(/gap.*\+11/)).toBeInTheDocument()
    expect(screen.queryByText("Score calculation")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Optimiser score calculation")).toHaveTextContent(
      /expected_margin\s*95\s*\+\s*lambda expected_loss\s*-4\s*=\s*score\s*91/,
    )
    expect(screen.getAllByText("expected_loss").length).toBeGreaterThan(0)
    expect(screen.getByText("-20")).toBeInTheDocument()
    expect(screen.queryByText(/constraint settings/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/constraint values/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/lambda terms/i)).not.toBeInTheDocument()
    expect(screen.getByText("Lambda Term")).toBeInTheDocument()
    expect(screen.getByText("score")).toBeInTheDocument()
  })

  it("renders optimiser apply ratebook factors as a running total ladder", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "ratebook",
          column: "technical_premium",
          output_value: 132,
          steps: [
            makeStep({
              node_id: "ratebook",
              node_name: "Ratebook apply",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["technical_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["driver_age", "channel"],
              },
              output_values: { driver_age: 42, channel: "direct", technical_premium: 132 },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                output_column: "technical_premium",
                output_value: 132,
                base_value: 100,
                final_value: 132,
                factors: [
                  {
                    name: "age_factor",
                    input_value: 42,
                    factor: "age_factor",
                    factor_value: 1.2,
                    running_total: 120,
                    status: "matched",
                  },
                  {
                    name: "channel_factor",
                    input_value: "direct",
                    factor: "channel_factor",
                    factor_value: 1.1,
                    running_total: 132,
                    status: "default",
                    default_used: true,
                  },
                ],
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Optimiser Apply")).toBeInTheDocument()
    expect(screen.getByText("Selected ratebook")).toBeInTheDocument()
    expect(screen.queryByLabelText("Optimiser ratebook calculation")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Optimiser ratebook ladder")).toBeInTheDocument()
    expect(screen.getByText("Value")).toBeInTheDocument()
    expect(screen.getAllByText("age_factor").length).toBeGreaterThan(0)
    expect(screen.getAllByText("1.2").length).toBeGreaterThan(0)
    expect(screen.getAllByText("channel_factor").length).toBeGreaterThan(0)
    expect(screen.getAllByText("default used").length).toBeGreaterThan(0)
    expect(screen.queryByText("Base value")).not.toBeInTheDocument()
  })

  it("renders optimiser apply trace errors instead of an empty optimiser view", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "optimiser",
          column: "technical_premium",
          output_value: "computed",
          steps: [
            makeStep({
              node_id: "optimiser",
              node_name: "Apply optimiser",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["technical_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                status: "error",
                error: "ratebook input row is missing factor column 'region'",
                error_type: "OptimiserApplyTraceError",
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      /Trace failed: ratebook input row is missing factor column 'region'/,
    )
    expect(screen.queryByText("Selected ratebook")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Optimiser ratebook ladder")).not.toBeInTheDocument()
  })

  it("renders ratebook trace messages without an empty ladder", () => {
    render(
      <TracePanel
        trace={makeTrace({
          target_node_id: "ratebook",
          column: "technical_premium",
          output_value: null,
          steps: [
            makeStep({
              node_id: "ratebook",
              node_name: "Ratebook apply",
              node_type: "optimiserApply",
              schema_diff: {
                columns_added: ["technical_premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              expression: null,
              calculation: null,
              node_detail: {
                detail_type: "optimiser_apply",
                mode: "ratebook",
                status: "ok",
                output_column: "technical_premium",
                output_value: null,
                base_value: 1,
                factors: [],
                final_value: null,
                message: "No ratebook factor tables were available in the optimiser artifact.",
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText("Selected ratebook")).toBeInTheDocument()
    expect(screen.getByText("No ratebook factor tables were available in the optimiser artifact.")).toBeInTheDocument()
    expect(screen.queryByLabelText("Optimiser ratebook ladder")).not.toBeInTheDocument()
  })

  it("target step card starts expanded to show column details", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Source",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, premium: 100 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    expect(screen.getByText(/1 passed through/)).toBeInTheDocument()
  })

  it("step card collapses on second click", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: "new_col",
          output_value: 42,
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Source",
              schema_diff: {
                columns_added: ["new_col"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, new_col: 42 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    // "Source" may appear in both CalculationHero and StepCard; find the StepCard one
    const sourceElements = screen.getAllByText("Source")
    const stepButton = sourceElements.find((el) => el.closest("button"))!.closest("button") as HTMLElement
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    fireEvent.click(stepButton)
    // Schema diff summary should be gone after collapse
    expect(screen.queryByText(/1 added/)).not.toBeInTheDocument()
  })

  it("renders schema diff with added columns highlighted", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Source",
              schema_diff: {
                columns_added: ["premium"],
                columns_removed: ["old_col"],
                columns_modified: ["age"],
                columns_passed: [],
              },
              output_values: { age: 30, premium: 100 },
              input_values: { age: 25 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    expect(screen.getByText(/1 removed/)).toBeInTheDocument()
    expect(screen.getByText(/1 modified/)).toBeInTheDocument()
  })

  it("shows key entries (collapsed) with tag badges for traced column", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: "score",
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Scorer",
              schema_diff: {
                columns_added: ["score"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, score: 88.5 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getByTestId("trace-step-card-n1").querySelector("button") as HTMLElement
    fireEvent.click(stepButton)
    // Should show the traced column with its value as a key entry after collapse.
    expect(screen.getByText(/score.*88.5/)).toBeInTheDocument()
  })

  it("exposes full precision for rounded collapsed key values", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: "score",
          output_value: 1.23456789,
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Scorer",
              schema_diff: {
                columns_added: ["score"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, score: 1.23456789 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const card = screen.getByTestId("trace-step-card-n1")
    fireEvent.click(within(card).getByRole("button"))

    const roundedValue = within(card).getByText(/score:\s*1\.23/)
    expect(roundedValue).toHaveAttribute("title", "score: 1.23456789")
  })

  it("exposes full precision for rounded expanded column values", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: "score",
          output_value: 1.23456789,
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Scorer",
              schema_diff: {
                columns_added: ["score"],
                columns_removed: [],
                columns_modified: ["risk"],
                columns_passed: ["age"],
              },
              input_values: { age: 25, risk: 9.87654321 },
              output_values: { age: 25, risk: 8.76543219, score: 1.23456789 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const card = screen.getByTestId("trace-step-card-n1")

    expect(within(card).getByText("1.2346")).toHaveAttribute("title", "score output: 1.23456789")
    expect(within(card).getByText("9.8765")).toHaveAttribute("title", "risk input: 9.87654321")
    expect(within(card).getByText("8.7654")).toHaveAttribute("title", "risk output: 8.76543219")
  })

  it("does not label string step values as full precision", () => {
    render(
      <TracePanel
        trace={makeTrace({
          column: "tier",
          output_value: "B",
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Banding",
              schema_diff: {
                columns_added: ["tier"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, tier: "B" },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const card = screen.getByTestId("trace-step-card-n1")
    fireEvent.click(within(card).getByRole("button"))

    const tierValue = within(card).getByText(/tier:\s*B/)
    expect(tierValue).not.toHaveAttribute("title")
    expect(tierValue).not.toHaveAttribute("aria-label")
  })

  it("renders header with no column name when column is null", () => {
    render(
      <TracePanel
        trace={makeTrace({ column: null })}
        onClose={vi.fn()}
      />,
    )
    // Should render "Trace" without a column suffix
    const header = screen.getByText("Trace")
    expect(header).toBeInTheDocument()
  })

  it("renders Result badge with output value", () => {
    // When column is null, no CalculationHero renders; the fallback Result badge appears
    render(<TracePanel trace={makeTrace({ column: null, output_value: 99.9 })} onClose={vi.fn()} />)
    expect(screen.getByText("Result")).toBeInTheDocument()
    expect(screen.getByText("99.9")).toBeInTheDocument()
  })

  it("non-relevant steps have reduced opacity", () => {
    const { container } = render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Irrelevant", column_relevant: false }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    // The step card should have opacity 0.55 for non-relevant steps
    const card = container.querySelector("[style*='opacity: 0.55']")
    expect(card).toBeTruthy()
  })

  it("relevant steps have full opacity", () => {
    const { container } = render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Relevant Step",
              column_relevant: true,
              schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    const card = container.querySelector("[style*='opacity: 1']")
    expect(card).toBeTruthy()
    expect(container.querySelector("[style*='opacity: 0.55']")).toBeFalsy()
  })

  it("renders a single trace story without Calculation or Nodes tabs", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByTestId("trace-story")).toBeInTheDocument()
    expect(screen.queryByText("Calculation")).not.toBeInTheDocument()
    expect(screen.queryByText("Nodes")).not.toBeInTheDocument()
    expect(screen.queryByText("Sources")).not.toBeInTheDocument()
    expect(screen.queryByText("All")).not.toBeInTheDocument()
  })

  it("expands the target step by default", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByTestId("trace-step-body-n2")).toBeInTheDocument()
    expect(screen.getByTestId("trace-step-card-n2")).toHaveAttribute("data-target-step", "true")
  })

  it("does not associate backend conditional selection with locally parsed target-step rows", () => {
    const { container } = render(
      <TracePanel
        trace={makeTrace({
          output_value: 0,
          steps: [
            makeStep({
              node_id: "source",
              node_name: "Source",
              node_type: "dataInput",
              schema_diff: {
                columns_added: ["tier"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              output_values: { tier: "B" },
            }),
            makeStep({
              node_id: "n2",
              node_name: "Conditional Calc",
              schema_diff: {
                columns_added: [],
                columns_removed: [],
                columns_modified: ["premium"],
                columns_passed: ["tier"],
              },
              input_values: { tier: "B" },
              output_values: { tier: "B", premium: 0 },
              expression: {
                expression_text:
                  "when tier = 'A' then 0 when tier = 'B' then 0 otherwise 1",
                expression_type: "conditional",
                referenced_columns: ["tier"],
              },
              calculation: {
                substituted_text:
                  "when 'B' = 'A' then 0 when 'B' = 'B' then 0 otherwise 1",
                result_value: 0,
                input_values: { tier: "B" },
                taken_branch: "then",
                taken_branch_index: 1,
              },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )

    const branches = Array.from(
      container.querySelectorAll<HTMLElement>(
        "[data-testid='trace-step-body-n2'] .conditional-display .branch",
      ),
    )
    expect(branches).toHaveLength(3)
    expect(branches.every((branch) => branch.dataset.matched === undefined)).toBe(true)
    expect(branches.every((branch) => !branch.classList.contains("inactive"))).toBe(true)
    expect(screen.getByTestId("conditional-backend-selection")).toHaveTextContent("Selected branch: 1 (then)")
  })

  it("keeps per-step timing out of the story", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Step A", schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
            makeStep({ node_id: "n2", node_name: "Step B", schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: ["age"] } }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument()
  })

  it("collapse then expand restores expanded content", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({
              node_id: "n1",
              node_name: "Toggle Step",
              schema_diff: {
                columns_added: ["x"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: ["age"],
              },
              output_values: { age: 25, x: 10 },
            }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    const stepButton = screen.getAllByText("Toggle Step").find((el) => el.closest("button"))!.closest("button") as HTMLElement
    // Expand
    fireEvent.click(stepButton)
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    // Collapse
    fireEvent.click(stepButton)
    expect(screen.queryByText(/1 added/)).not.toBeInTheDocument()
    // Re-expand
    fireEvent.click(stepButton)
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
  })

  it("mixed relevant and non-relevant steps have correct opacity", () => {
    const { container } = render(
      <TracePanel
        trace={makeTrace({
          column: null,
          steps: [
            makeStep({ node_id: "n1", node_name: "Relevant", column_relevant: true, schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
            makeStep({ node_id: "n2", node_name: "Not Relevant", column_relevant: false, schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["age"], columns_passed: [] } }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    const fullOpacity = container.querySelectorAll("[style*='opacity: 1']")
    const reducedOpacity = container.querySelectorAll("[style*='opacity: 0.55']")
    expect(fullOpacity.length).toBeGreaterThanOrEqual(1)
    expect(reducedOpacity.length).toBeGreaterThanOrEqual(1)
  })
})
