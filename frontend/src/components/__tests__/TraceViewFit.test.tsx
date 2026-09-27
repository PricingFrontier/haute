import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render } from "@testing-library/react"

import TraceViewFit from "../TraceViewFit"
import useUIStore from "../../stores/useUIStore"
import type { TraceResult, TraceStep } from "../../types/trace"

const flow = vi.hoisted(() => ({
  fitView: vi.fn(),
  zoom: 0.8,
}))

vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({ fitView: flow.fitView, getZoom: () => flow.zoom }),
}))

function step(node_id: string, column_relevant = true): TraceStep {
  return {
    node_id,
    node_name: node_id,
    node_type: "polars",
    schema_diff: { columns_added: [], columns_removed: [], columns_modified: [], columns_passed: [] },
    input_values: {},
    output_values: {},
    topological_rank: 0,
    column_relevant,
    contributed_columns: [],
    derivations: [],
  }
}

function trace(steps: TraceStep[]): TraceResult {
  return {
    target_node_id: steps[steps.length - 1].node_id,
    row_index: 0,
    column: "premium",
    output_value: 1,
    steps,
    omissions: [],
    row_id_column: null,
    row_id_value: null,
    total_nodes_in_pipeline: steps.length,
    nodes_in_trace: steps.length,
    execution_ms: 1,
    correlation_diagnostics: [],
    generated_at: "2026-09-27T12:00:00+00:00",
    pipeline_source: null,
    execution_origin: "fresh_execution",
  }
}

const identity = (id: string) => id

beforeEach(() => {
  flow.fitView.mockReset()
  flow.zoom = 0.8
  useUIStore.setState({ traceCentreRequest: null })
})

afterEach(cleanup)

describe("TraceViewFit", () => {
  it("fits the canvas to the traced value's lineage once per trace", () => {
    const first = trace([step("source"), step("joined_in", false), step("target")])
    const { rerender } = render(<TraceViewFit traceResult={null} resolveNodeId={identity} />)
    expect(flow.fitView).not.toHaveBeenCalled()

    rerender(<TraceViewFit traceResult={first} resolveNodeId={identity} />)
    rerender(<TraceViewFit traceResult={first} resolveNodeId={identity} />)

    expect(flow.fitView).toHaveBeenCalledOnce()
    expect(flow.fitView).toHaveBeenCalledWith({
      nodes: [{ id: "source" }, { id: "target" }],
      padding: 0.2,
      duration: 300,
    })

    rerender(<TraceViewFit traceResult={trace([step("other")])} resolveNodeId={identity} />)
    expect(flow.fitView).toHaveBeenCalledTimes(2)
  })

  it("centres a requested node at the current zoom", () => {
    render(<TraceViewFit traceResult={null} resolveNodeId={identity} />)

    act(() => useUIStore.getState().requestTraceCentre("fill_na"))

    expect(flow.fitView).toHaveBeenCalledWith({
      nodes: [{ id: "fill_na" }],
      duration: 300,
      minZoom: 0.8,
      maxZoom: 0.8,
    })
    // The same node asked for again is centred again.
    act(() => useUIStore.getState().requestTraceCentre("fill_na"))
    expect(flow.fitView).toHaveBeenCalledTimes(2)
  })

  it("fits and centres the canvas nodes that show runtime trace ids", () => {
    const visible = (id: string) => id.replace("submodel_runtime/pricing/", "")
    const { rerender } = render(
      <TraceViewFit
        traceResult={trace([step("submodel_runtime/pricing/a"), step("submodel_runtime/pricing/b"), step("b")])}
        resolveNodeId={visible}
      />,
    )
    expect(flow.fitView).toHaveBeenLastCalledWith({ nodes: [{ id: "a" }, { id: "b" }], padding: 0.2, duration: 300 })

    act(() => useUIStore.getState().requestTraceCentre("submodel_runtime/pricing/a"))
    expect(flow.fitView).toHaveBeenLastCalledWith(expect.objectContaining({ nodes: [{ id: "a" }] }))
    const calls = flow.fitView.mock.calls.length

    // A new resolver alone (the canvas changed) does not centre again.
    rerender(<TraceViewFit traceResult={null} resolveNodeId={(id) => visible(id)} />)
    expect(flow.fitView).toHaveBeenCalledTimes(calls)
  })
})
