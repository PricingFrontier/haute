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

function memoryPressureMetrics() {
  return makeExecutionMetricsFixture({
    execution_strategy: null,
  })
}

function rejectedStrategyMetrics() {
  return makeExecutionMetricsFixture({
    memory_pressure_events: [],
    execution_strategy: {
      schema_version: 1,
      status: "rejected",
      strategy: "materialisation-boundary",
      profile: "preview_eager",
      boundedness: "bounded",
      reason_code: "no_safe_plan",
      detail_state: "available",
      boundaries: { state: "available", total_count: 0, items: [] },
      reasons: { state: "available", total_count: 0, items: [] },
      provenance: { state: "available", total_count: 0, items: [] },
      blocking_node_id: "unsafe_node",
      blocking_operator: null,
      remediation: "Add an explicit filter.",
      estimated_peak_bytes: null,
      headroom_bytes: null,
    },
  })
}

function projectionBoundaryMetrics() {
  return makeExecutionMetricsFixture({
    memory_pressure_events: [],
    execution_strategy: {
      schema_version: 1,
      status: "boundary",
      strategy: "full-width-conservative",
      profile: "preview_eager",
      boundedness: "bounded",
      reason_code: "projection_limited",
      detail_state: "available",
      boundaries: {
        state: "available",
        total_count: 1,
        items: [
          {
            boundary_kind: "unprojected-streaming-boundary",
            node_id: "stream_node",
            operator: "filter",
            topological_rank: 0,
          },
        ],
      },
      reasons: { state: "available", total_count: 0, items: [] },
      provenance: { state: "available", total_count: 0, items: [] },
      blocking_node_id: "stream_node",
      blocking_operator: "filter",
      remediation: "Constrain the projected columns.",
      estimated_peak_bytes: null,
      headroom_bytes: null,
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

  it("renders nothing for a capture warning without another diagnostic", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: null,
      warnings: [
        {
          code: "snapshot_capture_superseded",
          node_id: "some_node",
          reason: null,
        },
      ],
    })
    const { container } = render(<ExecutionDiagnosticsIndicator metrics={metrics} />)

    expect(container.firstChild).toBeNull()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it.each([
    {
      name: "memory pressure",
      metrics: memoryPressureMetrics(),
      expectedTitle: "Preview memory pressure",
      expectedSeverity: "warning" as const,
      expectedRemediation: "Memory used:",
    },
    {
      name: "a rejected strategy",
      metrics: rejectedStrategyMetrics(),
      expectedTitle: "Execution could not use a safe strategy",
      expectedSeverity: "error" as const,
      expectedRemediation: "Add an explicit filter.",
    },
    {
      name: "a warned strategy",
      metrics: warnedStrategyMetrics(false),
      expectedTitle: "Execution ran without a memory estimate",
      expectedSeverity: "warning" as const,
      expectedRemediation: "Use a bounded aggregation.",
    },
    {
      name: "a projection boundary without pressure",
      metrics: projectionBoundaryMetrics(),
      expectedTitle: "Column projection was limited",
      expectedSeverity: "warning" as const,
      expectedRemediation: "Constrain the projected columns.",
    },
  ])("preserves $name title, severity, and remediation", ({ metrics, expectedTitle, expectedSeverity, expectedRemediation }) => {
    render(<ExecutionDiagnosticsIndicator metrics={metrics} />)

    expect(screen.getByText(expectedTitle)).toBeInTheDocument()
    const ariaLabel = expectedSeverity === "error"
      ? "Preview execution error details"
      : "Preview execution warning details"
    expect(screen.getByLabelText(ariaLabel)).toBeInTheDocument()

    const status = screen.getByRole("status")
    expect(status).toHaveTextContent(expectedRemediation)
  })
})
