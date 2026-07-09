/**
 * Pure helpers shared across the optimiser sub-components.
 *
 * Kept out of OptimiserPreview.tsx so that file only exports components
 * (satisfies `react-refresh/only-export-components`).
 */

import { sanitizeName } from "../../utils/sanitizeName"

/**
 * Derive the on-disk save path for an optimiser result artifact.
 *
 * The path ALWAYS embeds the node id: labels are only case-preservingly
 * unique (the backend's uniqueness guard allows coexisting nodes "Foo" and
 * "FOO"), and the backend save route writes the given path verbatim with no
 * overwrite guard — a label-only filename let one node's save silently
 * destroy another's.  Including the id makes the path unique per node while
 * a re-save of the SAME node still overwrites its own file (rerun
 * semantics).
 *
 * The label goes through the shared `sanitizeName` (the backend sanitizer's
 * case-preserving twin), replacing an ad-hoc lowercase sanitizer that had
 * diverged from it.  Node ids (`<type>_<n>` for UI-created nodes, Python
 * function names for pipeline-loaded ones) are already filename-safe, but
 * are sanitized too as defence in depth.
 */
export function optimiserResultSavePath(nodeLabel: string, nodeId: string): string {
  return `output/optimiser_${sanitizeName(nodeLabel)}_${sanitizeName(nodeId)}.json`
}

/**
 * Decide whether an absolute constraint is met given its threshold type and
 * observed value.  Used by both `SummaryTab` (to tint
 * the constraint row green/red) and `DetailCard` (to summarise the
 * selected frontier point).
 */
export function isConstraintMet(
  thresholdType: string,
  _ratio: number,
  absValue: number,
  thresholdVal: number,
): boolean {
  if (thresholdType === "min") return absValue >= thresholdVal
  if (thresholdType === "max") return absValue <= thresholdVal
  return false
}
