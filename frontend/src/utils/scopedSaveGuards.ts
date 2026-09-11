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
 * A late response may only be adopted if the document it was computed against
 * is still the one on screen; anything else intervened means the response is
 * stale and must be discarded rather than clobber newer state.
 */
export function scopedSaveResponseFenceError(args: {
  requestSourceFile: string
  requestRevision: string
  currentSourceFile: string
  currentRevision: string | null
}): string | null {
  if (args.currentSourceFile !== args.requestSourceFile || args.currentRevision !== args.requestRevision) {
    return "The document changed while saving; the stale response was discarded. Reload and retry."
  }
  return null
}
