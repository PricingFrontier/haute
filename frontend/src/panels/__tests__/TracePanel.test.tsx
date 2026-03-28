import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import TracePanel from "../TracePanel"
import type { TraceResult, TraceStep } from "../../types/trace"

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
  afterEach(cleanup)

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
})
