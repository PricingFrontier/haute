/**
 * The one judgement of whether an optimiser result met a constraint.
 *
 * The bound is the backend's `effective_bounds` entry for the displayed result:
 * the absolute bound that result was solved at (a frontier point's swept bound,
 * or for a pct constraint the fraction times its baseline total). The judgement
 * is a strict comparison on the Float64 values the backend sent, with no
 * tolerance, and the signed slack says by how much. It says nothing about λ:
 * in this discrete solve a positive multiplier can sit beside positive slack.
 */

import type { OptimiserEffectiveBound, OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../utils/optimiserSolveResults"

export type ConstraintKind = OptimiserEffectiveBound["kind"]

export type AttainmentStatus = "met" | "breached"

export interface ConstraintAttainment {
  kind: ConstraintKind
  bound: number
  achieved: number
  /** Positive on the met side of the bound, negative past it. */
  slack: number
  /** `slack` as a percentage of the bound's magnitude; `null` against a zero bound. */
  slackPct: number | null
  status: AttainmentStatus
}

export interface ConstraintAttainmentRow extends ConstraintAttainment {
  name: string
  lambda: number
}

function requireFinite(value: number, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Constraint attainment needs a finite ${field}, got ${String(value)}`)
  }
  return value
}

export function constraintAttainment({
  kind,
  bound,
  achieved,
}: {
  kind: ConstraintKind
  bound: number
  achieved: number
}): ConstraintAttainment {
  requireFinite(bound, "bound")
  requireFinite(achieved, "achieved value")
  let slack: number
  let met: boolean
  if (kind === "min") {
    slack = achieved - bound
    met = achieved >= bound
  } else if (kind === "max") {
    slack = bound - achieved
    met = achieved <= bound
  } else {
    throw new Error(`Unknown constraint kind ${JSON.stringify(kind)}`)
  }
  return {
    kind,
    bound,
    achieved,
    slack,
    slackPct: bound === 0 ? null : (slack / Math.abs(bound)) * 100,
    status: met ? "met" : "breached",
  }
}

/**
 * One attainment row per bounded constraint, in the backend's constraint order.
 * Every bound needs an achieved total and a λ, and every achieved total a
 * bound; anything else is a contract error, thrown rather than shown.
 */
export function constraintAttainmentRows({
  bounds,
  achieved,
  lambdas,
}: {
  bounds: Record<string, OptimiserEffectiveBound>
  achieved: Record<string, number>
  lambdas: Record<string, number>
}): ConstraintAttainmentRow[] {
  const unbounded = Object.keys(achieved).filter((name) => !(name in bounds))
  if (unbounded.length > 0) {
    throw new Error(`Optimiser result has no bound for constraint(s) ${unbounded.join(", ")}`)
  }
  return Object.entries(bounds).map(([name, { kind, bound }]) => {
    if (!(name in achieved)) {
      throw new Error(`Optimiser result has no achieved total for constraint ${name}`)
    }
    if (!(name in lambdas)) {
      throw new Error(`Optimiser result has no λ for constraint ${name}`)
    }
    return {
      name,
      ...constraintAttainment({ kind, bound, achieved: achieved[name] }),
      lambda: requireFinite(lambdas[name], `λ for ${name}`),
    }
  })
}

/** Full precision to four decimals, grouped: a compact "1.01M" could hide a breach. */
export function formatAttainmentValue(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

/** The bounds *result* breaches, judged as its Summary table judges them, in its order. */
export function breachedConstraints(result: OptimiserSolveResult): ConstraintAttainmentRow[] {
  return constraintAttainmentRows({
    bounds: effectiveConstraintBounds(result),
    achieved: result.constraints,
    lambdas: result.lambdas,
  }).filter((row) => row.status === "breached")
}

export interface BreachReport {
  /** Said beside "Converged": "1 bound breached", "2 bounds breached". */
  status: string
  /** One sentence per breached bound, naming it, what was achieved and by how much. */
  sentences: string[]
}

/** How a result says it breached *rows*' bounds; `null` when none of them is breached. */
export function breachReport(rows: readonly ConstraintAttainmentRow[]): BreachReport | null {
  const breached = rows.filter((row) => row.status === "breached")
  if (breached.length === 0) return null
  return {
    status: `${breached.length} ${breached.length === 1 ? "bound" : "bounds"} breached`,
    sentences: breached.map((row) => {
      const side = row.kind === "min" ? "under its minimum" : "over its maximum"
      const by = row.slackPct === null
        ? ""
        : ` ${Math.abs(row.slackPct).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
      return `${row.name} ${formatAttainmentValue(row.achieved)} is${by} ${side} of ${formatAttainmentValue(row.bound)}.`
    }),
  }
}
