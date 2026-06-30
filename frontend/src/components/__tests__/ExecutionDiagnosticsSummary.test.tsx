import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import ExecutionDiagnosticsSummary from "../ExecutionDiagnosticsSummary"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

afterEach(cleanup)

describe("ExecutionDiagnosticsSummary", () => {
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
