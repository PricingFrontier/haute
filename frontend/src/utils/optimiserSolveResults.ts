/**
 * What the results store needs to know about optimiser results, kept out of the
 * store: a frontier point's result from the server's summary, the frontier a
 * completed solve carries, the cross-field checks the generated validators
 * leave to the browser, and the identity and housekeeping of `/apply` replies.
 */
import type {
  FrontierData,
  FrontierPointSummary,
  FrontierSelectResponse,
  OptimiserApplyQuery,
  OptimiserEffectiveBound,
  OptimiserSolveResult,
} from "../api/types"
import type {
  CachedSolveResult,
  OptimiserApplyCacheEntry,
  OptimiserApplyIdentity,
  SolvedCachedSolveResult,
} from "../stores/useNodeResultsStore"
import { canonicalJson } from "./canonicalJson"

/** The frontier a completed solve carries, or null when it has no points. */
export function frontierFromSolveResult(result: OptimiserSolveResult): FrontierData | null {
  const rawFrontier = result.frontier
  return rawFrontier && rawFrontier.points?.length
    ? {
        points: rawFrontier.points,
        point_summaries: rawFrontier.point_summaries,
        n_points: rawFrontier.n_points,
        points_returned: rawFrontier.points_returned,
        constraint_names: rawFrontier.constraint_names,
        swept_axes: rawFrontier.swept_axes,
        points_limit: rawFrontier.points_limit,
        points_truncated: rawFrontier.points_truncated,
      }
    : null
}

/**
 * A select reply echoes the point it answers; one naming another point is a
 * contract violation, never a race, and throws.
 */
export function assertSelectReplyForPoint(pointIndex: number, selectResult: FrontierSelectResponse): void {
  if (selectResult.point_index != null && selectResult.point_index !== pointIndex) {
    throw new Error(
      `Frontier select response point_index (${selectResult.point_index}) does not match requested index (${pointIndex})`,
    )
  }
}

/** The as-solved result with a frontier point's server summary applied; a
 *  null summary field clears that field to null, as on the server (never to
 *  undefined, which a result's `T | null` fields do not hold). Factor tables
 *  are never absent from a result: a point without them has none (`{}`), as
 *  the server's result and select response report it. */
export function applyFrontierPointSummary(base: OptimiserSolveResult, summary: FrontierPointSummary): OptimiserSolveResult {
  return { ...base, ...summary, factor_tables: summary.factor_tables ?? {} }
}

export function resultForFrontierPoint(cached: SolvedCachedSolveResult, pointIndex: number): OptimiserSolveResult {
  const summary = cached.frontier?.point_summaries[pointIndex]
  if (!summary) throw new Error(`Frontier point index ${pointIndex} is out of range`)
  return applyFrontierPointSummary(cached.originalResult, summary)
}

/**
 * The constraint bounds a displayed optimiser result was solved at: its backend
 * `effective_bounds` (the selected frontier point's, else the solve's). The
 * optimiser Summary and the frontier detail card both read bounds only through
 * here, never from the node's configured constraints. A result whose bounds do
 * not cover exactly its constraints, or hold a non-finite bound, is a contract
 * error and throws.
 */
export function effectiveConstraintBounds(result: OptimiserSolveResult): Record<string, OptimiserEffectiveBound> {
  const bounds = result.effective_bounds
  const constraintNames = Object.keys(result.constraints)
  const missing = constraintNames.filter((name) => !(name in bounds))
  const unexpected = Object.keys(bounds).filter((name) => !(name in result.constraints))
  if (missing.length > 0 || unexpected.length > 0) {
    throw new Error(
      `Optimiser result bounds do not match its constraints: missing [${missing.join(", ")}], `
      + `unexpected [${unexpected.join(", ")}]`,
    )
  }
  for (const [name, { kind, bound }] of Object.entries(bounds)) {
    if ((kind !== "min" && kind !== "max") || !Number.isFinite(bound)) {
      throw new Error(`Optimiser result bound for ${name} is invalid: ${JSON.stringify({ kind, bound })}`)
    }
  }
  return bounds
}

/** The cache key of an `/apply` request identity, with the query canonicalised. */
export function optimiserApplyKey(identity: OptimiserApplyIdentity): string {
  return canonicalJson([identity.jobId, identity.target, identity.query])
}

/** The `/apply` identity a node's cached solve shows now: its job and selected target. */
export function optimiserApplyIdentityFor(
  cached: SolvedCachedSolveResult,
  query: OptimiserApplyQuery,
): OptimiserApplyIdentity {
  return {
    jobId: cached.jobId,
    target: cached.selectedPointIndex ?? "solved",
    query,
  }
}

export function sameApplyTarget(a: OptimiserApplyIdentity, b: OptimiserApplyIdentity): boolean {
  return a.jobId === b.jobId && a.target === b.target
}

/** The node's entries for any job other than the one installed. */
export function withoutSupersededApplyEntries(
  entries: OptimiserApplyCacheEntry[],
  nodeId: string,
  installedJobId: string,
): OptimiserApplyCacheEntry[] {
  const kept = entries.filter((entry) => entry.nodeId !== nodeId || entry.identity.jobId === installedJobId)
  return kept.length === entries.length ? entries : kept
}

export function withoutApplyEntriesForNodes(
  entries: OptimiserApplyCacheEntry[],
  nodeIds: readonly string[],
): OptimiserApplyCacheEntry[] {
  if (nodeIds.length === 0) return entries
  const dropped = new Set(nodeIds)
  const kept = entries.filter((entry) => !dropped.has(entry.nodeId))
  return kept.length === entries.length ? entries : kept
}

/** Whether a frontier select reply answers the node's installed solve: the same job. */
export function selectReplyIsCurrent(
  cached: CachedSolveResult | undefined,
  jobId: string,
): cached is SolvedCachedSolveResult {
  return cached !== undefined && cached.result !== null && cached.jobId === jobId
}

/** A select response is the server's complete result for its point. */
export function frontierPointSummaryFromSelect(selectResult: FrontierSelectResponse): FrontierPointSummary {
  return {
    total_objective: selectResult.total_objective,
    constraints: selectResult.constraints,
    effective_bounds: selectResult.effective_bounds,
    lambdas: selectResult.lambdas,
    converged: selectResult.converged,
    iterations: selectResult.iterations ?? null,
    cd_iterations: selectResult.cd_iterations ?? null,
    clamp_rate: selectResult.clamp_rate ?? null,
    history: selectResult.history ?? null,
    ratebook_cd_trace: selectResult.ratebook_cd_trace ?? null,
    // A point's report is loaded on request by the Adjustments tab, never kept in its summary.
    adjustments: null,
    factor_tables: selectResult.factor_tables ?? null,
    warning: selectResult.warning ?? null,
    frontier_error: null,
    diagnostics_errors: selectResult.diagnostics_errors,
  }
}
