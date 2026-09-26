/**
 * Shared optimiser result fixtures, shaped like what the server sends.
 *
 * The online solve carries scenario-value statistics, a histogram and a
 * history with λ and constraint totals; its frontier points are typed rows
 * (`thresholds`, `bounds`, `totals` and `lambdas` maps, `converged`,
 * `iterations`, `solver_path` and `sv_*`) with point summaries derived from
 * them as the backend's `frontier_point_summary` does (statistics from `sv_*`,
 * no histogram or history). The ratebook solve carries factor tables with
 * `quote_count`.
 * Tests override only what they are about, so selected-point views run on
 * real-shaped data rather than nulls.
 */

import type { FrontierData } from "../../OptimiserPreview"
import type {
  FrontierPointSummary,
  OptimiserHistoryEntry,
  OptimiserOnlineFrontierPoint,
  OptimiserRatebookFrontierPoint,
  OptimiserScenarioValueHistogram,
  OptimiserScenarioValueStats,
  OptimiserSolveResult,
} from "../../../api/types"
import type { FactorTables } from "../ratebookFactorTables"
import { makeHistoryEntry, makeSolveResult } from "../../../test-utils/factories"

/** The server's non-convergence warning (`NON_CONVERGED_WARNING`). */
export const NON_CONVERGED_WARNING =
  "Solver did not converge. Consider increasing max_iter or relaxing tolerance."

export function makeScenarioValueStats(
  overrides: Partial<OptimiserScenarioValueStats> = {},
): OptimiserScenarioValueStats {
  return {
    mean: 1.0213,
    std: 0.0452,
    min: 0.9,
    max: 1.15,
    p5: 0.95,
    p25: 0.99,
    p50: 1.02,
    p75: 1.05,
    p95: 1.1,
    pct_increase: 0.62,
    pct_decrease: 0.31,
    ...overrides,
  }
}

export function makeScenarioValueHistogram(): OptimiserScenarioValueHistogram {
  return {
    counts: [3, 12, 40, 30, 10, 5],
    edges: [0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2],
  }
}

/** Two solver iterations of the online solve, with λ and constraint totals. */
export function makeOnlineHistory(): OptimiserHistoryEntry[] {
  return [
    makeHistoryEntry({
      iteration: 1,
      total_objective: 1100000,
      max_lambda_change: 0.1,
      all_constraints_satisfied: false,
      lambdas: { loss_ratio: 0.004 },
      total_constraints: { loss_ratio: 0.7 },
    }),
    makeHistoryEntry({
      iteration: 2,
      total_objective: 1200000,
      max_lambda_change: 0.01,
      all_constraints_satisfied: true,
      lambdas: { loss_ratio: 0.005 },
      total_constraints: { loss_ratio: 0.65 },
    }),
  ]
}

/** An online solve with one `loss_ratio` max constraint, solved at 1.05. */
export function makeOnlineSolveResult(
  overrides: Partial<OptimiserSolveResult> = {},
): OptimiserSolveResult {
  return makeSolveResult({
    mode: "online",
    total_objective: 1234567,
    baseline_objective: 1200000,
    constraints: { loss_ratio: 0.65 },
    baseline_constraints: { loss_ratio: 0.60 },
    effective_bounds: { loss_ratio: { kind: "max", bound: 1.05 } },
    lambdas: { loss_ratio: 0.005 },
    converged: true,
    iterations: 15,
    n_quotes: 50000,
    n_steps: 7,
    history: makeOnlineHistory(),
    scenario_value_stats: makeScenarioValueStats(),
    scenario_value_histogram: makeScenarioValueHistogram(),
    ...overrides,
  })
}

/** Frontier point `i` of the online sweep, as the server types the library's
 *  row: each point is solved at its own swept max, 0.58, 0.59, ... */
export function makeOnlineFrontierPoint(
  i: number,
  overrides: Partial<OptimiserOnlineFrontierPoint> = {},
): OptimiserOnlineFrontierPoint {
  return {
    mode: "online",
    total_objective: 1200000 + i * 10000,
    thresholds: { loss_ratio: 0.58 + i * 0.01 },
    bounds: { loss_ratio: 0.58 + i * 0.01 },
    totals: { loss_ratio: 0.55 + i * 0.02 },
    lambdas: { loss_ratio: 0.001 + i * 0.001 },
    converged: true,
    iterations: 10 + i,
    solver_path: "bisection",
    non_convergence_reason: null,
    sv_mean: 1.0 + i * 0.01,
    sv_std: 0.04,
    sv_min: 0.9,
    sv_p5: 0.93,
    sv_p25: 0.98,
    sv_median: 1.0 + i * 0.01,
    sv_p75: 1.04,
    sv_p95: 1.09,
    sv_max: 1.12,
    sv_pct_increase: 0.5 + i * 0.02,
    sv_pct_decrease: 0.4 - i * 0.02,
    ...overrides,
  }
}

/** A point's server summary, derived from its row as the backend does. */
export function summaryForOnlinePoint(point: OptimiserOnlineFrontierPoint): FrontierPointSummary {
  return makePointSummary({
    total_objective: point.total_objective,
    constraints: point.totals,
    effective_bounds: { loss_ratio: { kind: "max", bound: point.bounds.loss_ratio } },
    lambdas: point.lambdas,
    converged: point.converged,
    iterations: point.iterations,
    scenario_value_stats: makeScenarioValueStats({
      mean: point.sv_mean,
      std: point.sv_std,
      min: point.sv_min,
      p5: point.sv_p5,
      p25: point.sv_p25,
      p50: point.sv_median,
      p75: point.sv_p75,
      p95: point.sv_p95,
      max: point.sv_max,
      pct_increase: point.sv_pct_increase,
      pct_decrease: point.sv_pct_decrease,
    }),
    warning: point.converged ? null : NON_CONVERGED_WARNING,
  })
}

/** A frontier point summary: statistics, iterations and convergence as a
 *  point reports them; a point has no histogram or history. */
export function makePointSummary(overrides: Partial<FrontierPointSummary> = {}): FrontierPointSummary {
  return {
    total_objective: 0,
    constraints: {},
    effective_bounds: {},
    lambdas: {},
    converged: true,
    iterations: 12,
    cd_iterations: null,
    clamp_rate: null,
    history: null,
    scenario_value_stats: makeScenarioValueStats(),
    scenario_value_histogram: null,
    factor_tables: null,
    warning: null,
    frontier_error: null,
    diagnostics_errors: [],
    ...overrides,
  }
}

/** The online solve's frontier of `n` points, as the results store keeps it. */
export function makeOnlineFrontier(n = 5, overrides: Partial<FrontierData> = {}): FrontierData {
  const points = Array.from({ length: n }, (_, i) => makeOnlineFrontierPoint(i))
  return {
    points,
    point_summaries: points.map(summaryForOnlinePoint),
    n_points: n,
    points_returned: n,
    constraint_names: ["loss_ratio"],
    swept_axes: ["loss_ratio"],
    points_limit: 2000,
    points_truncated: false,
    frontier_generation: 0,
    ...overrides,
  }
}

/** Ratebook factor tables: a rate and the quotes at each level. */
export function makeRatebookFactorTables(): FactorTables {
  return {
    region: [
      { __factor_group__: "North", optimal_scenario_value: 1.08, quote_count: 120 },
      { __factor_group__: "South", optimal_scenario_value: 0.92, quote_count: 80 },
    ],
    vehicle_age: [
      { __factor_group__: "0-3", optimal_scenario_value: 1.02, quote_count: 90 },
      { __factor_group__: "4+", optimal_scenario_value: 0.97, quote_count: 110 },
    ],
  }
}

/** A ratebook solve with one `volume` min constraint, solved at 0.9. */
export function makeRatebookSolveResult(
  overrides: Partial<OptimiserSolveResult> = {},
): OptimiserSolveResult {
  return makeSolveResult({
    mode: "ratebook",
    total_objective: 100,
    baseline_objective: 80,
    constraints: { volume: 0.92 },
    baseline_constraints: { volume: 0.8 },
    effective_bounds: { volume: { kind: "min", bound: 0.9 } },
    lambdas: { volume: 0.1 },
    converged: true,
    iterations: 4,
    cd_iterations: 4,
    n_quotes: 200,
    n_steps: 9,
    clamp_rate: 0.02,
    factor_tables: makeRatebookFactorTables(),
    combined_factor_bounds: { min: 0.8, max: 1.2 },
    scenario_value_stats: makeScenarioValueStats(),
    scenario_value_histogram: makeScenarioValueHistogram(),
    ...overrides,
  })
}

/** A ratebook frontier: one point per `{objective, volume, lambda}`. The
 *  store holds no factor tables for a point until it is materialised. */
export function makeRatebookFrontier(
  points: { objective: number; volume: number; lambda: number }[],
): FrontierData {
  const rows: OptimiserRatebookFrontierPoint[] = points.map(({ objective, volume, lambda }) => ({
    mode: "ratebook",
    total_objective: objective,
    thresholds: { volume: 0.9 },
    bounds: { volume: 0.9 },
    totals: { volume },
    lambdas: { volume: lambda },
    converged: true,
    iterations: 4,
    clamp_rate: 0.02,
    n_quotes_clamped_low: 0,
    n_quotes_clamped_high: 0,
  }))
  return {
    points: rows,
    point_summaries: rows.map((row) => makePointSummary({
      total_objective: row.total_objective,
      constraints: row.totals,
      effective_bounds: { volume: { kind: "min", bound: row.bounds.volume } },
      lambdas: row.lambdas,
      iterations: row.iterations,
      clamp_rate: row.clamp_rate,
      scenario_value_stats: null,
    })),
    n_points: rows.length,
    points_returned: rows.length,
    constraint_names: ["volume"],
    swept_axes: ["volume"],
    points_limit: 2000,
    points_truncated: false,
    frontier_generation: 0,
  }
}
