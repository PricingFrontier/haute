import { describe, expect, it } from "vitest"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"
import {
  buildExecutionDiagnostic,
  buildExecutionStrategyDiagnostic,
  buildExecutionFailureMessage,
  buildMemoryPressureDiagnostic,
  executionErrorDetailMessage,
  executionJobStatusFromReason,
  executionWarningNodeIds,
  executionTerminalReasonFromError,
  shouldShowMemoryPressureDiagnostic,
} from "../executionDiagnostics"

function warnedMetrics(overrides: Parameters<typeof makeExecutionMetricsFixture>[0]) {
  return makeExecutionMetricsFixture({
    execution_strategy: {
      schema_version: 1,
      status: "warned",
      strategy: "full-width-conservative",
      profile: "preview_eager",
      boundedness: "unbounded",
      reason_code: "materialisation_estimate_unavailable_conservative",
      detail_state: "available",
      boundaries: { state: "available", total_count: 0, items: [] },
      reasons: { state: "available", total_count: 0, items: [] },
      provenance: { state: "available", total_count: 0, items: [] },
      blocking_node_id: "aggregate",
      blocking_operator: "group_by",
      headroom_bytes: 2048,
      remediation: "Give this aggregation a bounded key set.",
    },
    ...overrides,
  })
}

describe("executionDiagnostics", () => {
  it("explains an adaptive Explore memory notice using clear units and labels", () => {
    const metrics = makeExecutionMetricsFixture({ profile: "explore_analysis" })
    Object.assign(metrics.memory_pressure_events[0], {
      threshold_percent: 50,
      rss_bytes: 11131 * 1024 ** 2,
      rss_limit_bytes: 22125.6 * 1024 ** 2,
      headroom_bytes: 10994.6 * 1024 ** 2,
      headroom_used_bytes: 11041.1 * 1024 ** 2,
      stage: "lazy_dataframe_cache_materialize",
      config_key: "adaptive:explore_analysis",
    })

    const diagnostic = buildMemoryPressureDiagnostic(metrics)

    expect(diagnostic?.message).toBe("Explore analysis reached 50% of its memory allowance.")
    expect(diagnostic?.details).toEqual([
      "Memory used: 10.9 GB; limit: 21.6 GB",
      "Memory remaining: 10.7 GB",
      "During: Caching the dataframe",
      "Limit source: set automatically from available RAM",
    ])
  })

  it("labels exceeded memory separately and retains an explicit limit setting", () => {
    const metrics = makeExecutionMetricsFixture()
    metrics.memory_pressure_events[0].headroom_bytes = -1024

    const diagnostic = buildMemoryPressureDiagnostic(metrics)

    expect(diagnostic?.details).toContain("Memory over limit: 1.0 KB")
    expect(diagnostic?.details).toContain("Limit source: HAUTE_PREVIEW_MEMORY_LIMIT_MB")
  })

  it("builds a concise memory-pressure summary with technical details", () => {
    const diagnostic = buildExecutionDiagnostic(makeExecutionMetricsFixture())

    expect(diagnostic?.message).toBe("Preview reached 75% of its memory allowance.")
    expect(diagnostic?.details).toContain("Memory used: 1.7 KB; limit: 2.9 KB")
    expect(diagnostic?.details).toContain("Memory remaining: 2.0 KB")
    expect(diagnostic?.details).toContain("During: Collecting results")
  })

  it("builds a warned strategy diagnostic naming the blocking node and reserved envelope", () => {
    const metrics = makeExecutionMetricsFixture({
      memory_pressure_events: [],
      execution_strategy: {
        schema_version: 1,
        status: "warned",
        strategy: "full-width-conservative",
        profile: "preview_eager",
        boundedness: "unbounded",
        reason_code: "materialisation_estimate_unavailable_conservative",
        detail_state: "available",
        boundaries: { state: "available", total_count: 0, items: [] },
        reasons: { state: "available", total_count: 0, items: [] },
        provenance: { state: "available", total_count: 0, items: [] },
        blocking_node_id: "aggregate",
        blocking_operator: "group_by",
        headroom_bytes: 2048,
        remediation: "Give this aggregation a bounded key set.",
      },
    })

    const diagnostic = buildExecutionStrategyDiagnostic(metrics)

    expect(diagnostic?.message).toBe("Execution ran without a memory estimate at 'aggregate' (group_by)")
    expect(diagnostic?.details).toContain("Reserved envelope 2.0 KB")
    expect(diagnostic?.details).toContain("Reason materialisation_estimate_unavailable_conservative")
    expect(diagnostic?.details).toContain("Remediation Give this aggregation a bounded key set.")
    expect(buildExecutionDiagnostic(metrics)).toEqual(diagnostic)
    expect(diagnostic?.kind).toBe("strategy")
  })

  it("lets memory pressure and terminal memory limits outrank a warned strategy", () => {
    const withoutPressure = warnedMetrics({ memory_pressure_events: [] })
    const withPressure = warnedMetrics({})

    expect(buildExecutionDiagnostic(withoutPressure)?.kind).toBe("strategy")
    expect(buildExecutionDiagnostic(withoutPressure)?.message).toBe(
      "Execution ran without a memory estimate at 'aggregate' (group_by)",
    )

    const pressureDiagnostic = buildExecutionDiagnostic(withPressure)
    expect(pressureDiagnostic?.kind).toBe("pressure")
    expect(pressureDiagnostic?.message).toBe("Preview reached 75% of its memory allowance.")

    expect(buildMemoryPressureDiagnostic(withoutPressure)).toBeNull()
    expect(buildMemoryPressureDiagnostic(withPressure)?.kind).toBe("pressure")

    expect(
      buildExecutionFailureMessage(
        "Stopped",
        warnedMetrics({ status: "memory_limited", terminal_reason: "memory_limited" }),
        { prefix: "Preview failed" },
      ),
    ).toBe("Preview failed: preview reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.")
  })

  it("marks the requested and blocking nodes as warned for a warned strategy", () => {
    expect(
      executionWarningNodeIds(warnedMetrics({ memory_pressure_events: [] }), "requested"),
    ).toEqual(["requested", "aggregate"])
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

    expect(diagnostic?.message).toBe("Preview reached 75% of its memory allowance.")
  })

  it("derives a useful memory-limited failure message from execution metrics", () => {
    const message = buildExecutionFailureMessage(
      "Stopped",
      makeExecutionMetricsFixture({ profile: "auto_range", terminal_reason: "memory_limited" }),
      { prefix: "Auto range failed" },
    )

    expect(message).toBe(
      "Auto range failed: auto-range reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.",
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
      "Optimisation failed: optimiser reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.",
    )
  })

  it("labels the solver session's profile as the optimiser", () => {
    const message = buildExecutionFailureMessage(
      "Stopped",
      makeExecutionMetricsFixture({ profile: "optimiser_solve", terminal_reason: null }),
      { prefix: "Optimisation failed", status: "memory_limited" },
    )

    expect(message).toContain("Optimisation failed: optimiser reached")
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

  describe("memory-limit error details", () => {
    const GB = 1024 ** 3
    const reduce = "To reduce the memory it needs, filter rows or drop columns earlier in the pipeline."

    it.each([
      [
        "a worker crash that looks memory-limited",
        { reason: "worker_may_have_exceeded_memory_limit" },
        `The process running this stopped abruptly, most likely because it ran out of memory. ${reduce}`,
      ],
      [
        "an exhausted worker",
        { reason: "worker_memory_exhausted" },
        `This ran out of memory before it finished. ${reduce}`,
      ],
      [
        "an unknown reason",
        { reason: "something_new" },
        `This ran out of memory before it finished. ${reduce}`,
      ],
      [
        "a worker over its RSS limit",
        { reason: "worker_rss_limit_exceeded", rss_bytes: 4.5 * GB, rss_limit_bytes: 4 * GB },
        `This used 4.5 GB of memory, over its 4.0 GB limit. ${reduce}`,
      ],
      [
        "a run over its growth allowance",
        { reason: "rss_exceeds_memory_limit", memory_limit_bytes: 4 * GB, rss_bytes: 9 * GB },
        `This needed more than its 4.0 GB memory allowance. ${reduce}`,
      ],
      [
        "a run over its growth allowance without byte values",
        { reason: "rss_exceeds_memory_limit" },
        `This needed more than its memory allowance. ${reduce}`,
      ],
      [
        "a run that reached the process limit",
        {
          reason: "process_rss_limit_exceeded",
          memory_limit_bytes: 16 * GB,
          rss_bytes: 12.5 * GB,
          baseline_rss_bytes: GB,
          rss_limit_bytes: 12 * GB,
        },
        `Haute reached its 12.0 GB process memory limit while running this. ${reduce}`,
      ],
      [
        "an admission refused at the process limit",
        {
          reason: "process_rss_limit_exceeded",
          rss_at_admission_bytes: 11 * GB,
          process_rss_limit_bytes: 12 * GB,
        },
        "There isn't enough free memory to start this: Haute is already using 11.0 GB of its 12.0 GB process memory limit.",
      ],
      [
        "an admission refused by in-flight work",
        { reason: "in_flight_memory_budget_exceeded", rss_at_admission_bytes: GB },
        "Other running work holds the memory this needs. Try again when it finishes.",
      ],
      [
        "an unenforceable native cap",
        { reason: "native_memory_cap_unavailable" },
        "This can't run because Haute can't enforce its memory limit on this machine.",
      ],
      [
        "an unavailable memory sampler",
        { reason: "memory_sampler_unavailable" },
        "Haute stopped this because it couldn't measure its memory use.",
      ],
    ])("renders %s in plain language", (_label, fields, expected) => {
      const error = { rawDetail: { error_code: "memory_limit", operation: "pipeline_preview", ...fields } }

      expect(executionErrorDetailMessage(error)).toBe(expected)
    })

    it("keeps an authored message on a memory-limit detail", () => {
      const error = {
        rawDetail: {
          error_code: "memory_limit",
          reason: "rss_exceeds_memory_limit",
          message: "Auto-range exceeded its memory budget.",
        },
      }

      expect(executionErrorDetailMessage(error)).toBe("Auto-range exceeded its memory budget.")
    })

    it("names the node whose cached data is unreadable, and both remedies", () => {
      // A corrupt generation used to reach the user as the store's own text,
      // which named no node, so they could not tell whose cache to press.
      const error = {
        rawDetail: {
          error_code: "snapshot_corrupt",
          node_id: "B",
          node_label: "Banding",
          message:
            "The cached data for 'Banding' is unreadable. Refresh that node to rebuild it, "
            + "or clear it to run without a cache.",
        },
      }

      const message = executionErrorDetailMessage(error)
      expect(message).toContain("Banding")
      expect(message).toMatch(/refresh/i)
      expect(message).toMatch(/clear it/i)
    })
  })
})
