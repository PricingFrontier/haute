import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import ExecutionDiagnosticsSummary from "../ExecutionDiagnosticsSummary"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

function strategyMetrics(status: "projected" | "boundary" | "admitted_eager" | "rejected" | "not_planned") {
  const strategyByStatus = {
    projected: "projected",
    boundary: "materialisation-boundary",
    admitted_eager: "full-width-admitted-eager",
    rejected: "unsupported",
    not_planned: "not-planned",
  } as const
  return makeExecutionMetricsFixture({
    execution_strategy: {
      schema_version: 1,
      status,
      strategy: strategyByStatus[status],
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

describe("ExecutionDiagnosticsSummary", () => {
  it.each([
    ["projected", "Projection strategy applied"],
    ["boundary", "Execution boundary required"],
    ["admitted_eager", "Eager execution admitted"],
    ["rejected", "Execution strategy rejected"],
    ["not_planned", "Execution strategy was not planned"],
  ] as const)("renders the %s strategy state", (status, message) => {
    render(<ExecutionDiagnosticsSummary metrics={strategyMetrics(status)} />)

    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.getByLabelText("Execution strategy technical details")).toBeInTheDocument()
    expect(screen.getByText(/Estimated materialisation cost/)).toBeInTheDocument()
    expect(screen.getByText(/Remediation Use a bounded aggregation/)).toBeInTheDocument()
  })

  it("renders memory pressure for running execution metrics", () => {
    render(<ExecutionDiagnosticsSummary metrics={makeExecutionMetricsFixture()} />)

    expect(screen.getByText("Memory pressure reached 75% of the preview budget.")).toBeInTheDocument()
    expect(screen.getByText("Technical details")).toBeInTheDocument()
  })

  it("renders memory pressure for memory-limited terminal failures", () => {
    render(
      <ExecutionDiagnosticsSummary
        metrics={makeExecutionMetricsFixture({ status: "error", terminal_reason: null })}
        status="memory_limited"
        terminalReason="memory_limited"
      />,
    )

    expect(screen.getByText("Memory pressure reached 75% of the preview budget.")).toBeInTheDocument()
  })

  it.each(["contract_error", "timed_out", "cancelled", "superseded", "error"] as const)(
    "does not render memory pressure for %s terminal failures",
    (status) => {
      render(
        <ExecutionDiagnosticsSummary
          metrics={makeExecutionMetricsFixture({ status: "running", terminal_reason: null })}
          status={status}
          terminalReason={status}
        />,
      )

      expect(screen.queryByText("Memory pressure reached 75% of the preview budget.")).not.toBeInTheDocument()
      expect(screen.queryByText("Technical details")).not.toBeInTheDocument()
    },
  )
})
