/**
 * Column suggestions for step forms: the upstream schema plus the columns
 * earlier steps add, remove, or rename. Suggestions only; every column picker
 * also accepts free text because the upstream schema may be partial.
 */
import { stepProblem } from "./catalogue"
import type { Step } from "./types"

function applyStep(columns: string[], step: Step): string[] {
  switch (step.kind) {
    case "with_column":
      return step.name && !columns.includes(step.name) ? [...columns, step.name] : columns
    case "select":
      // A dtype selection is resolved by Polars at run time; every upstream
      // column might match, so all stay suggested (suggestions only).
      return step.dtypes?.length ? [...step.columns.filter((c) => c.length > 0), ...columns] : step.columns.filter((c) => c.length > 0)
    case "drop":
      return columns.filter((c) => !step.columns.includes(c))
    case "rename": {
      const map = new Map(step.renames.filter((r) => r.from && r.to).map((r) => [r.from, r.to]))
      return columns.map((c) => map.get(c) ?? c)
    }
    case "group_by": {
      // A dtype aggregation names its outputs by suffix at run time; a suffix is never a column.
      const names = step.aggregations.flatMap((a) => ("dtype" in a ? [] : [a.name])).filter((n) => n.length > 0)
      return [...step.keys.filter((k) => k.length > 0), ...names]
    }
    case "pivot":
      return [...step.index.filter((k) => k.length > 0), ...step.columns.map((c) => c.name).filter((n) => n.length > 0)]
    case "unpivot":
      return [...step.index.filter((k) => k.length > 0), step.variableName, step.valueName].filter((n) => n.length > 0)
    default:
      return columns
  }
}

/**
 * Columns available to the step at `index`: the upstream columns transformed
 * by every earlier step. Joins and concats keep the current columns because the
 * other input's schema is not known here.
 */
export function columnsBeforeStep(upstream: string[], steps: Step[], index: number): string[] {
  let columns = [...upstream]
  for (const step of steps.slice(0, index)) {
    if (stepProblem(step) !== null) continue
    columns = applyStep(columns, step)
  }
  return Array.from(new Set(columns))
}
