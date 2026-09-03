import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import ExecutionDiagnosticsIndicator from "../ExecutionDiagnosticsIndicator"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

function warnedStrategyMetrics(withPressure: boolean) {
  return makeExecutionMetricsFixture({
    memory_pressure_events: withPressure
      ? makeExecutionMetricsFixture().memory_pressure_events
      : [],
    execution_strategy: {
      schema_version: 1,
      status: "warned",
      strategy: "full-width-conservative",
      profile: "preview_eager",
      boundedness: "bounded",
      reason_code: "projection_seed",
      detail_state: "available",
      boundaries: { state: "available", total_count: 0, items: [] },
      reasons: { state: "available", total_count: 0, items: [] },
      provenance: { state: "available", total_count: 0, items: [] },
      blocking_node_id: "aggregate",
      blocking_operator: "group_by",
      remediation: "Use a bounded aggregation.",
      estimated_peak_bytes: 1024,
      headroom_bytes: 2048,
    },
  })
}

afterEach(cleanup)

describe("ExecutionDiagnosticsIndicator", () => {
  it("keeps the warned strategy title when no memory pressure is reported", () => {
    render(<ExecutionDiagnosticsIndicator metrics={warnedStrategyMetrics(false)} />)

    expect(screen.getByText("Execution ran without a memory estimate")).toBeInTheDocument()
  })

  it("prefers the memory-pressure title over a warned strategy", () => {
    render(<ExecutionDiagnosticsIndicator metrics={warnedStrategyMetrics(true)} />)

    expect(screen.getByText("Preview memory pressure")).toBeInTheDocument()
    expect(screen.queryByText("Execution ran without a memory estimate")).not.toBeInTheDocument()
    expect(
      screen.getByLabelText("Preview execution warning details"),
    ).toHaveAttribute("title", "Preview memory pressure")
  })
})
