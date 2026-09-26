/**
 * Shared optimiser result fixtures, shaped like what the server sends.
 *
 * The online solve carries its as-solved adjustment report over a seven-step
 * grid and a history with λ and constraint totals; its frontier points are
 * typed rows (`thresholds`, `bounds`, `totals` and `lambdas` maps,
 * `converged`, `iterations`, `solver_path` and `sv_*`) with point summaries
 * derived from them as the backend's `frontier_point_summary` does (no report,
 * no history). The ratebook solve carries factor tables with
 * `quote_count` and a coordinate-descent trace.
 * Tests override only what they are about, so selected-point views run on
 * real-shaped data rather than nulls.
 */

import type { FrontierData } from "../../OptimiserPreview"
import type {
  FrontierPointSummary,
  OptimiserHistoryEntry,
  OptimiserOnlineFrontierPoint,
  OptimiserRatebookCdTrace,
  OptimiserRatebookFrontierPoint,
  OptimiserAdjustmentReport,
  OptimiserSolveResult,
} from "../../../api/types"
import type { FactorTables } from "../ratebookFactorTables"
import { makeHistoryEntry, makeSolveResult } from "../../../test-utils/factories"

/** The server's non-convergence warning (`NON_CONVERGED_WARNING`). */
export const NON_CONVERGED_WARNING =
  "Solver did not converge. Consider increasing max_iter or relaxing tolerance."

/** The online solve's seven-step grid, 0.85 to 1.15 by 0.05, with 1.0 at step 3. */
export const ONLINE_SCENARIO_GRID = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15].map(
  (scenario_value, optimal_step) => ({ optimal_step, scenario_value }),
)

const ONLINE_QUOTES_PER_STEP = [1000, 4000, 9000, 15000, 12000, 6000, 3000]
const ONLINE_OBJECTIVE_PER_STEP = [20000, 90000, 200000, 350000, 300000, 170000, 104567]

/** The online solve's adjustment report, as the backend's `adjustment_report`
 *  builds it for 50,000 quotes over `ONLINE_SCENARIO_GRID`: quote count and the
 *  objective weigh it; `loss_ratio` is refused for three negative values. */
export function makeAdjustmentReport(
  overrides: Partial<OptimiserAdjustmentReport> = {},
): OptimiserAdjustmentReport {
  return {
    n_quotes: 50000,
    has_unadjusted: true,
    bars: ONLINE_SCENARIO_GRID.map(({ optimal_step, scenario_value }) => ({
      optimal_step,
      scenario_value,
      quotes: ONLINE_QUOTES_PER_STEP[optimal_step],
      weights: { optimal_objective: ONLINE_OBJECTIVE_PER_STEP[optimal_step] },
    })),
    weightings: [
      {
        key: "quotes",
        label: "Quotes",
        total: 50000,
        mean: 1.013,
        quantiles: { p5: 0.9, p25: 0.95, p50: 1.0, p75: 1.05, p95: 1.15 },
        share_up: 0.42,
        share_down: 0.28,
        share_unadjusted: 0.3,
        share_at_min: 0.02,
        share_at_max: 0.06,
      },
      {
        key: "optimal_objective",
        label: "Objective at the chosen scenario",
        total: 1234567,
        mean: 1.0208049056875812,
        quantiles: { p5: 0.9, p25: 0.95, p50: 1.0, p75: 1.05, p95: 1.15 },
        share_up: 0.4653996097417151,
        share_down: 0.25110018330313383,
        share_unadjusted: 0.2835002069551511,
        share_at_min: 0.016200011826008633,
        share_at_max: 0.08469933183051223,
      },
    ],
    diagnostics_errors: [
      {
        diagnostic: "adjustment_weight",
        error_type: "NegativeWeight",
        message:
          "loss_ratio at the chosen scenario cannot weigh the adjustments: 3 quotes have a "
          + "negative value (optimal_loss_ratio).",
      },
    ],
    // Online: the deployed scenario is the chosen step itself.
    deployed_factor_differs: null,
    ...overrides,
  }
}

/** The ratebook solve's adjustment report over its nine-step grid (0.8 to 1.2),
 *  as `adjustment_report` builds it from price-contour's canonical evaluation:
 *  200 quotes, 18 of them flagged "deployed factor differs from evaluated step". */
export function makeRatebookAdjustmentReport(
  overrides: Partial<OptimiserAdjustmentReport> = {},
): OptimiserAdjustmentReport {
  const grid = [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]
  const quotes = [10, 0, 20, 30, 60, 40, 20, 0, 20]
  return {
    n_quotes: 200,
    has_unadjusted: true,
    bars: grid.map((scenario_value, optimal_step) => ({
      optimal_step,
      scenario_value,
      quotes: quotes[optimal_step],
      weights: {},
    })),
    weightings: [
      {
        key: "quotes",
        label: "Quotes",
        total: 200,
        mean: 1.0,
        quantiles: { p5: 0.8, p25: 0.95, p50: 1.0, p75: 1.05, p95: 1.2 },
        share_up: 0.4,
        share_down: 0.3,
        share_unadjusted: 0.3,
        share_at_min: 0.05,
        share_at_max: 0.1,
      },
    ],
    diagnostics_errors: [],
    deployed_factor_differs: 18,
    ...overrides,
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
    scenario_grid: ONLINE_SCENARIO_GRID,
    adjustments: makeAdjustmentReport(),
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
    warning: point.converged ? null : NON_CONVERGED_WARNING,
  })
}

/** A frontier point summary: totals, iterations and convergence as a point
 *  reports them; a point has no history, and its adjustment report is loaded
 *  on request, never kept in its summary. */
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
    ratebook_cd_trace: null,
    adjustments: null,
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

/** The ratebook solve's coordinate-descent trace: four passes over `region`
 *  then `vehicle_age`, the objective rising and `volume` settling above its
 *  0.9 bound, ending on the solve's totals. */
export function makeRatebookCdTrace(): OptimiserRatebookCdTrace {
  const records = [1, 2, 3, 4].flatMap((cdPass) =>
    ["region", "vehicle_age"].map((factor, factorIndex) => {
      const step = (cdPass - 1) * 2 + factorIndex
      return {
        cd_iteration: cdPass,
        factor,
        factor_index: factorIndex,
        total_objective: 93 + step,
        total_constraints: { volume: 0.99 - step * 0.01 },
        lambdas: { volume: 0.03 + step * 0.01 },
      }
    }),
  )
  return { records, truncated: false }
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
    ratebook_cd_trace: makeRatebookCdTrace(),
    adjustments: makeRatebookAdjustmentReport(),
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
