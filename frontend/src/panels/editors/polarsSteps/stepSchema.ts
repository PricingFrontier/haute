/**
 * What the column fields of one step know about the data there: each
 * column's type (or that an earlier step made it) for the completion list,
 * and, when the columns are complete, whether a name exists and which known
 * name is closest. Forms provide it once per step through
 * `StepSchemaContext`; a field listing something other than this step's
 * columns (column types, another input's keys) overrides it.
 */
import { createContext, useContext } from "react"

import type { KnownColumn, StepColumns } from "./derivedColumns"

export type StepSchema = {
  /** The note beside a suggested name: its type, `new` for an untyped column a step made, or null. */
  describe: (name: string) => string | null
  /** The column's type when known. */
  typeOf: (name: string) => string | null
  /** Whether `name` is a column here; null when the columns are not complete, so nothing is judged. */
  isKnown: ((name: string) => boolean) | null
  /** The known name closest to a mistyped one, or null. */
  closest: (name: string) => string | null
}

/** A schema that knows nothing: no notes, no judgement. */
export const UNKNOWN_SCHEMA: StepSchema = { describe: () => null, typeOf: () => null, isKnown: null, closest: () => null }

function distance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i += 1) {
    let previous = row[0]
    row[0] = i
    for (let j = 1; j <= b.length; j += 1) {
      const current = row[j]
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] === b[j - 1] ? 0 : 1))
      previous = current
    }
  }
  return row[b.length]
}

/**
 * The known name closest to `name`: a case-insensitive match first, then the
 * nearest by edit distance within a third of the name's length (at least 1).
 */
export function closestName(name: string, names: readonly string[]): string | null {
  const lower = name.toLowerCase()
  const caseMatch = names.find((n) => n.toLowerCase() === lower)
  if (caseMatch) return caseMatch
  const limit = Math.max(1, Math.floor(name.length / 3))
  let best: string | null = null
  let bestDistance = limit + 1
  for (const candidate of names) {
    const d = distance(lower, candidate.toLowerCase())
    if (d < bestDistance) {
      best = candidate
      bestDistance = d
    }
  }
  return best
}

export function describeColumn(column: KnownColumn | undefined): string | null {
  if (!column) return null
  return column.dtype ?? (column.made ? "new" : null)
}

/** The schema of a step from its columns. */
export function schemaFor(state: StepColumns): StepSchema {
  const byName = new Map(state.columns.map((c) => [c.name, c]))
  const names = state.columns.map((c) => c.name)
  return {
    describe: (name) => describeColumn(byName.get(name)),
    typeOf: (name) => byName.get(name)?.dtype ?? null,
    isKnown: state.complete ? (name) => byName.has(name) : null,
    closest: (name) => (state.complete ? closestName(name, names) : null),
  }
}

export const StepSchemaContext = createContext<StepSchema>(UNKNOWN_SCHEMA)

export const useStepSchema = (): StepSchema => useContext(StepSchemaContext)
