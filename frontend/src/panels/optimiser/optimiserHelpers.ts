/**
 * Pure helpers shared across the optimiser sub-components.
 *
 * Kept out of OptimiserPreview.tsx so that file only exports components
 * (satisfies `react-refresh/only-export-components`).
 */

import { portableKey } from "../../utils/portableKey"

/**
 * Derive the suggested save filename for an optimiser result artifact. It is a
 * bare filename, which the server places in the pipeline folder's
 * `files/models/`.
 *
 * The name ALWAYS embeds the node id: labels are only case-preservingly
 * unique (the backend's uniqueness guard allows coexisting nodes "Foo" and
 * "FOO"), so a label-only filename let one node's save collide with
 * another's.  Including the id makes the name unique per node while a
 * re-save of the SAME node still targets its own file (rerun semantics).
 *
 * The label and id go through the browser-owned `portableKey`. This is a
 * suggested artifact filename, not a Python or editor execution identity.
 */
export function optimiserResultSavePath(nodeLabel: string, nodeId: string): string {
  return `optimiser_${portableKey(nodeLabel)}_${portableKey(nodeId)}.json`
}

/**
 * Why a point reply cannot be shown: the server answered for another frontier
 * generation than the result on screen (it recomputed since, and the node has
 * not installed that recompute). A recompute reuses point indices for other
 * points, so the reply describes a different point and is never kept.
 */
export function frontierGenerationMismatch(answered: number, shown: number): string | null {
  return answered === shown
    ? null
    : `The server answered for frontier generation ${answered}, but this result shows generation ${shown}.`
}
