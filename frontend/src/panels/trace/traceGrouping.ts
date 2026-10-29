import type { TraceStep } from "../../types/trace"
import { isTraceSourceNodeType } from "../../trace/traceOrigins"

/**
 * Find the best step to display expression/calculation for the traced column.
 *
 * Primary: the last step the backend's lineage says computed the traced value
 * (its `contributed_columns` name the column). A joined-in table that also adds
 * the column, such as a shared key whose base value the join kept, did not.
 * Without one: the last step where the column is in columns_added or columns_modified.
 * Fallback: when the primary step has no usable expression (null or opaque),
 * check the final step in the trace - the backend enriches pass-through target
 * steps with the upstream creator's expression, so the final step may carry a
 * valid (non-opaque) expression even though the column is only in columns_passed.
 */
export function findTargetStep(
  steps: TraceStep[],
  column: string | null | undefined,
): TraceStep | null {
  if (!column || steps.length === 0) return null
  let found: TraceStep | null = null
  let computedBy: TraceStep | null = null
  for (const step of steps) {
    const diff = step.schema_diff
    if (diff.columns_added.includes(column) || diff.columns_modified.includes(column)) {
      found = step
    }
    if (step.contributed_columns.includes(column)) computedBy = step
  }
  if (computedBy) found = computedBy

  // If the found step has a usable (non-opaque) expression, use it directly.
  const hasUsableExpression = (s: TraceStep | null) =>
    s?.expression != null && s.expression.expression_type !== "opaque"

  if (hasUsableExpression(found)) return found

  // The backend enriches the final (target) step with the upstream expression
  // when the column is a pass-through.  Prefer it if it has a better expression.
  const last = steps[steps.length - 1]
  if (
    last !== found &&
    last.schema_diff.columns_passed.includes(column) &&
    (hasUsableExpression(last) || (last.calculation != null && !found))
  ) {
    return last
  }

  return found
}

export type CollapsedEntry = TraceStep | { collapsed: TraceStep[] }

interface CollapsePassthroughOptions {
  collapseUnpreserved?: boolean
}

/**
 * Determine if a step is a passthrough for the given column:
 * the column appears in columns_passed but NOT in columns_added or columns_modified,
 * or the step does not mention the traced column at all.
 */
function isPassthrough(step: TraceStep, column: string): boolean {
  const diff = step.schema_diff
  // Column explicitly passes through
  if (diff.columns_passed.includes(column) &&
      !diff.columns_added.includes(column) &&
      !diff.columns_modified.includes(column)) {
    return true
  }
  // Column not mentioned at all - completely irrelevant step
  const allMentioned = [
    ...diff.columns_added,
    ...diff.columns_modified,
    ...diff.columns_removed,
    ...diff.columns_passed,
  ]
  return !allMentioned.includes(column)
}

export function isSourceLikeTraceStep(step: Pick<TraceStep, "node_type">): boolean {
  return isTraceSourceNodeType(step.node_type)
}

/**
 * Collapse consecutive passthrough steps into groups.
 * Source steps and steps that create/modify the column are never collapsed in the
 * default passthrough mode. Dependency-story views can opt into collapsing every
 * unpreserved step, including source steps.
 * When ALL steps are passthroughs, the first and last are preserved.
 */
export function collapsePassthroughs(
  steps: TraceStep[],
  column: string,
  preserveStepIds: ReadonlySet<string> = new Set(),
  options: CollapsePassthroughOptions = {},
): CollapsedEntry[] {
  if (steps.length === 0) return []

  // Check if all steps are passthroughs (no source, no creator/modifier)
  const allPassthrough = steps.every((s) => isPassthrough(s, column))

  const result: CollapsedEntry[] = []
  let pendingCollapsed: TraceStep[] = []

  function flushCollapsed() {
    if (pendingCollapsed.length > 0) {
      result.push({ collapsed: pendingCollapsed })
      pendingCollapsed = []
    }
  }

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i]
    const isFirst = i === 0
    const isLast = i === steps.length - 1

    // If all are passthroughs, preserve first and last
    const preserveAsEndpoint = allPassthrough && (isFirst || isLast)

    const isPreserved = preserveStepIds.has(step.node_id)
    const shouldCollapse = options.collapseUnpreserved
      ? !isPreserved
      : !isPreserved && !isSourceLikeTraceStep(step) && isPassthrough(step, column)

    if (preserveAsEndpoint || !shouldCollapse) {
      flushCollapsed()
      result.push(step)
    } else {
      pendingCollapsed.push(step)
    }
  }

  flushCollapsed()
  return result
}
