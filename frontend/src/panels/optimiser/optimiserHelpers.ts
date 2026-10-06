/**
 * Pure helpers shared across the optimiser sub-components.
 *
 * Kept out of OptimiserPreview.tsx so that file only exports components
 * (satisfies `react-refresh/only-export-components`).
 */

import { portableKey } from "../../utils/portableKey"

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
 * The label and id go through the browser-owned `portableKey`. This is a
 * suggested artifact filename, not a Python or editor execution identity.
 */
export function optimiserResultSavePath(nodeLabel: string, nodeId: string): string {
  return `output/optimiser_${portableKey(nodeLabel)}_${portableKey(nodeId)}.json`
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
