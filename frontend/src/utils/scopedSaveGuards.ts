/** Pure guards for the node-scoped save: no store or transport coupling. */

/**
 * Scoped edits are saved one node at a time: adopting the authoritative
 * response replaces the whole canvas, so a save while another node holds
 * unsaved scoped edits would silently discard them.
 */
export function otherScopedEditedNodes(
  editedNodeIds: ReadonlySet<string>,
  targetId: string,
): string[] {
  return [...editedNodeIds].filter((id) => id !== targetId).sort()
}

/**
 * What a node-scoped save's response is told against when the document it was computed
 * against is no longer the one on screen (its file or revision moved): it is discarded
 * rather than clobber newer state. The save's answer belongs to that document
 * (`useScopedNodeSave`, through `utils/answers.ts`).
 */
export const STALE_SCOPED_SAVE = "The document changed while saving; the stale response was discarded. Reload and retry."
