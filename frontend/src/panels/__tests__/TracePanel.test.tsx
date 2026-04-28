import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import TracePanel from "../TracePanel"
import type { TraceResult, TraceStep } from "../../types/trace"
import useToastStore from "../../stores/useToastStore"

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
      makeStep({ node_id: "n1", node_name: "Source" }),
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
  }
}

describe("TracePanel", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  it("renders the Trace header with column name", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    expect(screen.getByText(/Trace.*premium/)).toBeInTheDocument()
  })

  it("renders the output value", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    // Click "Nodes" tab to switch to node list view where output values are shown
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // With CalculationHero, the value may appear as key entry text or inside the hero
    expect(screen.getByText(/42\.5/)).toBeInTheDocument()
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

  it("close button calls onClose", () => {
    const onClose = vi.fn()
    render(<TracePanel trace={makeTrace()} onClose={onClose} />)
    // The header Copy button has title="Copy trace as markdown"; the Close button
    // is the next sibling button after it.
    const copyBtn = screen.getByTitle("Copy trace as markdown")
    const closeBtn = copyBtn.nextElementSibling as HTMLElement
    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("logs and does not show copied when clipboard fallback fails", async () => {
    const originalClipboard = navigator.clipboard
    const originalExecCommand = document.execCommand
    const writeText = vi.fn().mockRejectedValue(new Error("clipboard denied"))
    const execCommand = vi.fn((): boolean => false)
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined)

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
    document.execCommand = execCommand

    try {
      render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)

      fireEvent.click(screen.getByTitle("Copy trace as markdown"))

      await waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"))
      expect(warn).toHaveBeenCalledWith(
        "Clipboard API copy failed; trying document fallback",
        expect.any(Error),
      )
      expect(warn).toHaveBeenCalledWith("Clipboard fallback copy failed", expect.any(Error))
      expect(screen.getByTitle("Copy trace as markdown")).toBeInTheDocument()
      expect(screen.queryByTitle("Copied trace")).not.toBeInTheDocument()
      expect(useToastStore.getState().toasts).toContainEqual(expect.objectContaining({
        type: "error",
        text: "Could not copy trace markdown",
      }))
    } finally {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: originalClipboard,
      })
      document.execCommand = originalExecCommand
    }
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
  })

  it("renders per-step execution time", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Only Step", execution_ms: 7.3 }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    expect(screen.getByText("7.3ms")).toBeInTheDocument()
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
              node_type: "dataSource",
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
                selected_band: "young",
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
    expect(screen.getByLabelText("Banding: driver_age=22 -> young")).toBeInTheDocument()
    expect(screen.queryByText(/Matched band:/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Output:/i)).not.toBeInTheDocument()
  })

  it("step card expands on click to show column details", () => {
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // Before expanding: key entries are shown but not the full column list
    // "Source" may appear in both CalculationHero (nodeName) and StepCard; find the StepCard one
    const sourceElements = screen.getAllByText("Source")
    const stepButton = sourceElements.find((el) => el.closest("button"))!.closest("button") as HTMLElement
    fireEvent.click(stepButton)
    // After expanding: should show schema diff summary
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    expect(screen.getByText(/1 passed through/)).toBeInTheDocument()
  })

  it("step card collapses on second click", () => {
    render(
      <TracePanel
        trace={makeTrace({
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // "Source" may appear in both CalculationHero and StepCard; find the StepCard one
    const sourceElements = screen.getAllByText("Source")
    const stepButton = sourceElements.find((el) => el.closest("button"))!.closest("button") as HTMLElement
    // Expand
    fireEvent.click(stepButton)
    expect(screen.getByText(/1 added/)).toBeInTheDocument()
    // Collapse
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // Expand the step — "Source" may appear in CalculationHero and StepCard
    const sourceElements = screen.getAllByText("Source")
    const stepButton = sourceElements.find((el) => el.closest("button"))!.closest("button") as HTMLElement
    fireEvent.click(stepButton)
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // Should show the traced column with its value as a key entry (collapsed key badge)
    expect(screen.getByText(/score.*88.5/)).toBeInTheDocument()
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
    // Click "Nodes" tab to switch to node list view
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
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
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    const card = container.querySelector("[style*='opacity: 1']")
    expect(card).toBeTruthy()
    expect(container.querySelector("[style*='opacity: 0.55']")).toBeFalsy()
  })

  it("tab switching between Calculation and Nodes", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const calcTab = screen.getByText("Calculation")
    const nodesTab = screen.getByText("Nodes")
    expect(calcTab).toBeInTheDocument()
    expect(nodesTab).toBeInTheDocument()

    // Default tab is Calculation — detail level sub-tabs should NOT appear
    expect(screen.queryByText("Sources")).not.toBeInTheDocument()

    // Switch to Nodes tab
    fireEvent.click(nodesTab)
    // Detail level sub-tabs should appear in Nodes view
    expect(screen.getByText("Sources")).toBeInTheDocument()
    expect(screen.getByText("All")).toBeInTheDocument()

    // Switch back to Calculation tab
    fireEvent.click(calcTab)
    expect(screen.queryByText("Sources")).not.toBeInTheDocument()
  })

  it("Calculation tab is active by default", () => {
    render(<TracePanel trace={makeTrace()} onClose={vi.fn()} />)
    const calcTab = screen.getByText("Calculation")
    expect(calcTab).toHaveStyle({ color: "var(--accent-hover)" })
  })

  it("Nodes tab shows per-step execution times for multiple steps", () => {
    render(
      <TracePanel
        trace={makeTrace({
          steps: [
            makeStep({ node_id: "n1", node_name: "Step A", execution_ms: 3.1, schema_diff: { columns_added: ["premium"], columns_removed: [], columns_modified: [], columns_passed: [] } }),
            makeStep({ node_id: "n2", node_name: "Step B", execution_ms: 9.8, schema_diff: { columns_added: [], columns_removed: [], columns_modified: ["premium"], columns_passed: ["age"] } }),
          ],
        })}
        onClose={vi.fn()}
      />,
    )
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    expect(screen.getByText("3.1ms")).toBeInTheDocument()
    expect(screen.getByText("9.8ms")).toBeInTheDocument()
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
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
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
    const nodesTab = screen.getByText("Nodes")
    fireEvent.click(nodesTab)
    // Switch to "All" detail level so all steps are visible
    const allBtn = screen.getByText("All")
    fireEvent.click(allBtn)
    const fullOpacity = container.querySelectorAll("[style*='opacity: 1']")
    const reducedOpacity = container.querySelectorAll("[style*='opacity: 0.55']")
    expect(fullOpacity.length).toBeGreaterThanOrEqual(1)
    expect(reducedOpacity.length).toBeGreaterThanOrEqual(1)
  })
})
