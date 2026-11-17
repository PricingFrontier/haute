import type { ExecutionMetrics } from "../api/types"
import type { RuntimeNodeKey } from "../generated/api-contracts.generated"
import { rootAddresses } from "../test-utils/factories"
import { keyText } from "../utils/answerAddresses"

/**
 * `T` with every runtime key it holds given as a fixture names it: plain text, or a key
 * already made (a key is its text at runtime, see `runtimeKey`).
 */
export type WithFixtureKeys<T> = T extends RuntimeNodeKey
  ? string | RuntimeNodeKey
  : T extends readonly unknown[]
    ? WithFixtureKeys<T[number]>[]
    : T extends object
      ? { [K in keyof T]: WithFixtureKeys<T[K]> }
      : T

/** Execution metrics' fields, the nodes they name given as the text a fixture names them by. */
export type ExecutionMetricsFixture = Partial<WithFixtureKeys<ExecutionMetrics>>

/** Every key the metrics name, by text. */
function namedKeys(metrics: Omit<ExecutionMetrics, "node_addresses">): string[] {
  const keys: (RuntimeNodeKey | null | undefined)[] = [
    ...metrics.stages.map((stage) => stage.node_id),
    ...metrics.column_widths.items.map((item) => item.node_id),
    ...metrics.memory_pressure_events.map((event) => event.node_id),
    ...metrics.input_preparation.map((record) => record.node_id),
    ...metrics.shared_snapshot_seeds.map((seed) => seed.node_id),
    ...metrics.shared_snapshot_captures.map((capture) => capture.node_id),
    ...metrics.shared_snapshot_capture_skips.map((skip) => skip.node_id),
    ...metrics.warnings.map((warning) => warning.node_id),
  ]
  const strategy = metrics.execution_strategy
  if (strategy) {
    keys.push(strategy.blocking_node_id, strategy.projection_cause?.node_id, strategy.projection_cause?.parent_node_id)
    for (const boundary of strategy.boundaries.items) keys.push(boundary.node_id)
    for (const reason of strategy.reasons.items) keys.push(reason.node_id, reason.parent_node_id)
    for (const provenance of strategy.provenance.items) keys.push(provenance.source_node_id)
  }
  const named = keys.filter((key): key is RuntimeNodeKey => key !== null && key !== undefined).map(keyText)
  return [...new Set([...Object.keys(metrics.node_elapsed_ms), ...named])]
}

/**
 * Execution metrics with every field the server sends. Every key they name is addressed as a
 * node of the pipeline under its own text, unless `node_addresses` says otherwise.
 */
export function makeExecutionMetricsFixture(overrides: ExecutionMetricsFixture = {}): ExecutionMetrics {
  const { node_addresses, ...rest } = overrides
  const fixture: Omit<WithFixtureKeys<ExecutionMetrics>, "node_addresses"> = {
    schema_version: 1,
    operation: "pipeline_preview",
    profile: "preview_eager",
    job_id: "job-1",
    status: "running",
    terminal_reason: null,
    stage_count: 1,
    retained_stage_count: 1,
    truncated_stage_count: 0,
    stages_truncated: false,
    total_elapsed_ms: 12.5,
    node_elapsed_ms: { score: 12.5 },
    stage_elapsed_ms: { collect: 12.5 },
    rss_start_bytes: 1_000,
    rss_end_bytes: 1_800,
    rss_delta_bytes: 800,
    rss_peak_bytes: 1_800,
    max_rss_bytes: 1_800,
    n_collects: 1,
    n_checkpoints: 2,
    memory_pressure_event_count: 1,
    retained_memory_pressure_event_count: 1,
    truncated_memory_pressure_event_count: 0,
    memory_pressure_events_truncated: false,
    memory_limit_bytes: 2_000,
    memory_baseline_bytes: 1_000,
    rss_limit_bytes: 3_000,
    streamability: null,
    streamability_evidence: { state: "unavailable", total_count: null, items: [] },
    column_widths: { state: "available", total_count: 0, items: [] },
    requested_column_width_total: null,
    physically_scanned_column_width_total: null,
    cache_direct_fallbacks: 0,
    bytes_written: null,
    estimated_bytes: null,
    raw_estimated_bytes: null,
    estimate_calibration_factor_basis_points: null,
    estimate_admission_basis: null,
    observed_peak_rss_bytes: null,
    observed_peak_rss_growth_bytes: null,
    checkpoint_count: 0,
    chunk_count: 0,
    cancellation_latency_ms: null,
    training_write_strategy: null,
    training_write_input_slices: null,
    training_write_native_reason: null,
    training_write_blocking_operator: null,
    data_output_write_strategy: null,
    data_output_write_input_slices: null,
    data_output_write_native_reason: null,
    execution_strategy: null,
    shared_snapshot_seeds: [],
    shared_snapshot_captures: [],
    shared_snapshot_capture_skips: [],
    warnings: [],
    admission: {
      admitted: true,
      operation: "pipeline_preview",
      profile: "preview_eager",
      memory_limit_bytes: 2_000,
      rss_at_admission_bytes: 1_000,
      rss_limit_bytes: 3_000,
      process_rss_limit_bytes: null,
      headroom_bytes: 2_000,
      config_key: "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
      budget_policy: "adaptive_local",
      available_ram_bytes: 8_000,
      os_reserve_bytes: 2_000,
      reason: "within_memory_budget",
    },
    stages: [
      {
        schema_version: 1,
        name: "collect",
        operation: "pipeline_preview",
        profile: "preview_eager",
        elapsed_ms: 12.5,
        node_id: "score",
        job_id: "job-1",
        rss_start_bytes: 1_000,
        rss_end_bytes: 1_800,
        rss_delta_bytes: 800,
        rss_peak_bytes: 1_800,
        n_collects: 1,
        n_checkpoints: 2,
      },
    ],
    memory_pressure_events: [
      {
        schema_version: 1,
        event: "memory_pressure",
        operation: "pipeline_preview",
        profile: "preview_eager",
        job_id: "job-1",
        node_id: "score",
        stage: "collect",
        label: "after_collect",
        threshold_ratio: 0.75,
        threshold_percent: 75,
        rss_bytes: 1_750,
        rss_limit_bytes: 3_000,
        headroom_bytes: 2_000,
        headroom_used_bytes: 1_500,
        rss_peak_bytes: 1_800,
        memory_limit_bytes: 2_000,
        memory_baseline_bytes: 1_000,
        baseline_rss_bytes: 1_000,
        budget_policy: "adaptive_local",
        config_key: "HAUTE_PREVIEW_MEMORY_LIMIT_MB",
        available_ram_bytes: 8_000,
        os_reserve_bytes: 2_000,
        pressure_ratio: 0.75,
      },
    ],
    input_preparation: [
      {
        node_id: "policies",
        identity_digest: "digest-1",
        action: "reused",
        build_class: "in_memory",
        execution: "in_process",
        memory_limit_bytes: 2_000,
        elapsed_seconds: 0.25,
        row_count: 10,
        size_bytes: 512,
        generation_id: "gen-1",
        warning_code: null,
      },
    ],
    ...rest,
  }
  // A key is its text at runtime, so the fixture's text keys are the keys.
  const metrics = fixture as unknown as Omit<ExecutionMetrics, "node_addresses">
  return { ...metrics, node_addresses: { ...rootAddresses(...namedKeys(metrics)), ...node_addresses } }
}
