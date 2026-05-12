import { describe, expect, it } from "vitest"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"
import {
  buildExecutionDiagnostic,
  buildExecutionFailureMessage,
  executionJobStatusFromReason,
  executionTerminalReasonFromError,
  shouldShowMemoryPressureDiagnostic,
} from "../executionDiagnostics"

describe("executionDiagnostics", () => {
  it("builds a concise memory-pressure summary with technical details", () => {
    const diagnostic = buildExecutionDiagnostic(makeExecutionMetricsFixture())

    expect(diagnostic?.message).toBe("Memory pressure reached 75% of the preview budget.")
    expect(diagnostic?.details).toContain("RSS 1.7 KB of 2.9 KB limit")
    expect(diagnostic?.details).toContain("Headroom used 1.5 KB of 2.0 KB")
    expect(diagnostic?.details).toContain("Stage collect")
  })

  it.each(["contract_error", "timed_out", "cancelled", "superseded"] as const)(
    "does not build a terminal memory-pressure banner for %s failures",
    (terminalReason) => {
      const diagnostic = buildExecutionDiagnostic(makeExecutionMetricsFixture({
        status: terminalReason,
        terminal_reason: terminalReason,
      }))

      expect(diagnostic).toBeNull()
    },
  )

  it.each(["contract_error", "timed_out", "cancelled", "superseded", "error"] as const)(
    "suppresses diagnostics for explicit %s terminal UI context",
    (status) => {
      const metrics = makeExecutionMetricsFixture({ status: "running", terminal_reason: null })

      expect(buildExecutionDiagnostic(metrics, { status })).toBeNull()
      expect(shouldShowMemoryPressureDiagnostic(metrics, { status })).toBe(false)
    },
  )

  it("preserves terminal memory-limited diagnostics", () => {
    const diagnostic = buildExecutionDiagnostic(makeExecutionMetricsFixture({
      status: "memory_limited",
      terminal_reason: "memory_limited",
    }))

    expect(diagnostic?.message).toBe("Memory pressure reached 75% of the preview budget.")
  })

  it("derives a useful memory-limited failure message from execution metrics", () => {
    const message = buildExecutionFailureMessage(
      "Stopped",
      makeExecutionMetricsFixture({ profile: "auto_range", terminal_reason: "memory_limited" }),
      { prefix: "Auto range failed" },
    )

    expect(message).toBe(
      "Auto range failed: memory pressure reached 75% of the auto-range budget. RSS 1.7 KB of 2.9 KB limit.",
    )
  })

  it("returns the base message when no memory-pressure diagnostic exists", () => {
    const message = buildExecutionFailureMessage(
      "Projection contract failed",
      makeExecutionMetricsFixture({
        memory_pressure_event_count: 0,
        retained_memory_pressure_event_count: 0,
        memory_pressure_events: [],
      }),
      { prefix: "Auto range failed", status: "memory_limited" },
    )

    expect(message).toBe("Projection contract failed")
  })

  it("keeps non-memory terminal failures even when retained memory pressure events exist", () => {
    const message = buildExecutionFailureMessage(
      "Fan-in projection contract does not cover columns required by the node.",
      makeExecutionMetricsFixture({ terminal_reason: "contract_error" }),
      { prefix: "Auto range failed", status: "contract_error", terminalReason: "contract_error" },
    )

    expect(message).toBe("Fan-in projection contract does not cover columns required by the node.")
  })

  it("does not infer memory-limited failure text from pressure events without a memory terminal signal", () => {
    const message = buildExecutionFailureMessage(
      "Projection contract failed",
      makeExecutionMetricsFixture({ terminal_reason: null }),
      { prefix: "Optimisation failed", status: "contract_error" },
    )

    expect(message).toBe("Projection contract failed")
  })

  it.each(["timed_out", "cancelled", "superseded"] as const)(
    "keeps %s terminal messages even when memory pressure was observed",
    (status) => {
      const message = buildExecutionFailureMessage(
        "Stopped for the original terminal reason",
        makeExecutionMetricsFixture({ terminal_reason: null }),
        { prefix: "Optimisation failed", status },
      )

      expect(message).toBe("Stopped for the original terminal reason")
    },
  )

  it("can derive memory-limited text from the explicit terminal status when metrics omit terminal_reason", () => {
    const message = buildExecutionFailureMessage(
      "Stopped",
      makeExecutionMetricsFixture({ profile: "optimiser_setup", terminal_reason: null }),
      { prefix: "Optimisation failed", status: "memory_limited" },
    )

    expect(message).toBe(
      "Optimisation failed: memory pressure reached 75% of the optimiser budget. RSS 1.7 KB of 2.9 KB limit.",
    )
  })

  it("normalises admission memory_limit details to memory_limited terminal state", () => {
    const error = {
      rawDetail: {
        error_code: "memory_limit",
        reason: "rss_exceeds_memory_limit",
        execution_metrics: makeExecutionMetricsFixture({
          terminal_reason: null,
        }),
      },
    }

    expect(executionTerminalReasonFromError(error)).toBe("memory_limited")
    expect(executionJobStatusFromReason("memory_limit")).toBe("memory_limited")
  })
})
