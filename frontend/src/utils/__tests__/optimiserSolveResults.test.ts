import { describe, expect, it } from "vitest"

import type { FrontierSelectResponse, OptimiserApplyQuery } from "../../api/types"
import { makeSolveResult } from "../../test-utils/factories"
import {
  applyFrontierPointSummary,
  assertSelectReplyForPoint,
  effectiveConstraintBounds,
  frontierFromSolveResult,
  optimiserApplyKey,
} from "../optimiserSolveResults"

describe("optimiserSolveResults", () => {
  const QUERY: OptimiserApplyQuery = {
    sort_by: null,
    descending: false,
    quote_id_prefix: null,
    filters: {
      scenario_value_min: null,
      scenario_value_max: null,
      at_range_edge: false,
      analysis_equals: {},
      deployed_factor_differs: false,
    },
    offset: 0,
    limit: 100,
  }

  it("keys every part of the identity, the full query included", () => {
    const base = { jobId: "j1", frontierGeneration: 0, target: "solved" as const, query: QUERY }
    const keys = new Set([
      optimiserApplyKey(base),
      optimiserApplyKey({ ...base, jobId: "j2" }),
      optimiserApplyKey({ ...base, frontierGeneration: 1 }),
      optimiserApplyKey({ ...base, target: 0 }),
      optimiserApplyKey({ ...base, query: { ...QUERY, offset: 100 } }),
      optimiserApplyKey({ ...base, query: { ...QUERY, sort_by: "optimal_objective" } }),
      optimiserApplyKey({ ...base, query: { ...QUERY, quote_id_prefix: "Q1" } }),
      optimiserApplyKey({
        ...base,
        query: { ...QUERY, filters: { ...QUERY.filters, analysis_equals: { region: "North" } } },
      }),
    ])
    expect(keys.size).toBe(8)
  })

  it("canonicalises the query, so key order never splits one query into two entries", () => {
    const reordered = Object.fromEntries(Object.entries(QUERY).reverse()) as OptimiserApplyQuery
    const base = { jobId: "j1", frontierGeneration: 0, target: "solved" as const }
    expect(optimiserApplyKey({ ...base, query: reordered })).toBe(optimiserApplyKey({ ...base, query: QUERY }))
  })

  it("effectiveConstraintBounds throws when a constraint has no backend bound", () => {
    expect(() => effectiveConstraintBounds(makeSolveResult({
      constraints: { premium: 50 },
      effective_bounds: {},
    }))).toThrow(/premium/)
    expect(() => effectiveConstraintBounds(makeSolveResult({
      constraints: { premium: 50 },
      effective_bounds: { premium: { kind: "min", bound: Number.NaN } },
    }))).toThrow(/premium/)
    expect(() => effectiveConstraintBounds(makeSolveResult({
      constraints: {},
      effective_bounds: { premium: { kind: "min", bound: 1 } },
    }))).toThrow(/premium/)
  })

  it("applies a point's summary, a null field clearing the result's to null", () => {
    const base = makeSolveResult({ iterations: 40, warning: "solved warning", history: [] })
    const point = applyFrontierPointSummary(base, {
      total_objective: 9,
      constraints: {},
      effective_bounds: {},
      lambdas: {},
      converged: true,
      iterations: null,
      cd_iterations: null,
      clamp_rate: null,
      history: null,
      ratebook_cd_trace: null,
      adjustments: null,
      factor_tables: null,
      warning: null,
      frontier_error: null,
      diagnostics_errors: [],
    })
    expect(point.total_objective).toBe(9)
    expect(point.iterations).toBeNull()
    expect(point.warning).toBeNull()
    expect(point.history).toBeNull()
    expect(point.factor_tables).toEqual({})
  })

  it("refuses a frontier from another generation than its result's", () => {
    const result = makeSolveResult({ frontier_generation: 2 })
    expect(frontierFromSolveResult(result)).toBeNull()
    expect(() => frontierFromSolveResult({
      ...result,
      frontier: { ...({} as NonNullable<typeof result.frontier>), frontier_generation: 1, points: [] },
    })).toThrow(/generation 1 does not match its result's generation 2/)
  })

  it("refuses a select reply naming another point", () => {
    expect(() => assertSelectReplyForPoint(2, { point_index: 3 } as FrontierSelectResponse)).toThrow(/\(3\).*\(2\)/)
    expect(() => assertSelectReplyForPoint(2, { point_index: 2 } as FrontierSelectResponse)).not.toThrow()
  })
})
