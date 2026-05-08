import type { TraceStep } from "../../types/trace"

/**
 * Find the best step to display expression/calculation for the traced column.
 *
 * Primary: the last step where the column is in columns_added or columns_modified.
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
  for (const step of steps) {
    const diff = step.schema_diff
    if (diff.columns_added.includes(column) || diff.columns_modified.includes(column)) {
      found = step
    }
  }

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

export interface TraceGroup {
  steps: TraceStep[]
  primary: boolean
}

/**
 * Group consecutive steps of the same node_type together.
 * Non-adjacent steps of the same type become separate groups.
 * A group is marked primary if it contains the step that creates/modifies the column.
 */
export function groupTraceSteps(steps: TraceStep[], column: string): TraceGroup[] {
  if (steps.length === 0) return []

  const targetStep = findTargetStep(steps, column)
  const groups: TraceGroup[] = []
  let currentGroup: TraceStep[] = [steps[0]]

  for (let i = 1; i < steps.length; i++) {
    if (steps[i].node_type === steps[i - 1].node_type) {
      currentGroup.push(steps[i])
    } else {
      groups.push({
        steps: currentGroup,
        primary: targetStep !== null && currentGroup.some((s) => s.node_id === targetStep.node_id),
      })
      currentGroup = [steps[i]]
    }
  }

  groups.push({
    steps: currentGroup,
    primary: targetStep !== null && currentGroup.some((s) => s.node_id === targetStep.node_id),
  })

  return groups
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
  return step.node_type === "source" || step.node_type === "dataSource" || step.node_type === "apiInput"
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

export interface FlowChainEntry {
  step: TraceStep
  isOrigin: boolean
  isTarget: boolean
  expressionSummary?: string
}

/**
 * Build a chain of steps relevant to the given column.
 * Only includes steps where column_relevant=true AND the column appears
 * in the step's schema_diff (added, modified, or passed).
 */
export function buildFlowChain(steps: TraceStep[], column: string): FlowChainEntry[] {
  // Filter to relevant steps that involve the column
  const relevant = steps.filter((step) => {
    if (!step.column_relevant) return false
    const diff = step.schema_diff
    return (
      diff.columns_added.includes(column) ||
      diff.columns_modified.includes(column) ||
      diff.columns_passed.includes(column)
    )
  })

  if (relevant.length === 0) return []

  // Find origin: first step where column appears in columns_added
  const originStep = relevant.find((s) => s.schema_diff.columns_added.includes(column))

  // Find target: last step that adds or modifies the column
  let targetStep: TraceStep | null = null
  for (const step of relevant) {
    if (
      step.schema_diff.columns_added.includes(column) ||
      step.schema_diff.columns_modified.includes(column)
    ) {
      targetStep = step
    }
  }

  return relevant.map((step) => {
    const entry: FlowChainEntry = {
      step,
      isOrigin: originStep !== undefined && step.node_id === originStep.node_id,
      isTarget: targetStep !== null && step.node_id === targetStep.node_id,
    }

    if (step.expression) {
      entry.expressionSummary = step.expression.expression_text
    }

    return entry
  })
}
