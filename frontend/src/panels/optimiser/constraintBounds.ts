/**
 * A constraint's bound as the solver reads it: exactly one of `min` / `max`
 * (an absolute total) or `min_pct` / `max_pct` (a fraction of the
 * constraint's baseline total).
 */

import { fractionToPercent } from "../../utils/formatValue"

export const CONSTRAINT_BOUND_KINDS = ["min", "max", "min_pct", "max_pct"] as const
export type ConstraintBoundKind = (typeof CONSTRAINT_BOUND_KINDS)[number]

export const CONSTRAINT_BOUND_LABELS: Record<ConstraintBoundKind, string> = {
  min: "at least",
  max: "at most",
  min_pct: "at least % of baseline",
  max_pct: "at most % of baseline",
}

export function isPercentBound(kind: ConstraintBoundKind): boolean {
  return kind === "min_pct" || kind === "max_pct"
}

function isSpec(spec: unknown): spec is Record<string, unknown> {
  return typeof spec === "object" && spec !== null && !Array.isArray(spec)
}

/** The spec's one bound kind, or null when it sets none or several (or is not an object). */
export function constraintBoundKind(spec: unknown): ConstraintBoundKind | null {
  if (!isSpec(spec)) return null
  const kinds = CONSTRAINT_BOUND_KINDS.filter((kind) => Object.hasOwn(spec, kind))
  return kinds.length === 1 ? kinds[0] : null
}

/** The spec's stored bound value, or undefined when it is not a finite number. */
export function constraintBoundValue(spec: unknown, kind: ConstraintBoundKind): number | undefined {
  const value = isSpec(spec) ? spec[kind] : undefined
  return typeof value === "number" && Number.isFinite(value) ? value : undefined
}

/** The value the analyst reads and types: a percentage for a pct bound (0.95 → 95). */
export function boundToDisplay(kind: ConstraintBoundKind, stored: number): number {
  return isPercentBound(kind) ? fractionToPercent(stored) : stored
}

/** The stored value of what the analyst typed: a fraction for a pct bound (95 → 0.95). */
export function boundFromDisplay(kind: ConstraintBoundKind, shown: number): number {
  return isPercentBound(kind) ? Number((shown / 100).toPrecision(12)) : shown
}

/**
 * The value a bound starts from when the analyst switches between an absolute
 * and a percentage bound, since the old number means something else: the
 * baseline itself (100%) for a percentage, 0 for an absolute total as a new
 * constraint starts.
 */
export function startingBoundValue(kind: ConstraintBoundKind): number {
  return isPercentBound(kind) ? 1 : 0
}
