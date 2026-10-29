import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useUIStore from "../stores/useUIStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"

/**
 * Whether the project holds unsaved edits (specs/frontend-git-ui): the pipeline's canvas,
 * or the workbench's form, which the eager chrome reads through the workbench store's
 * mirror. What a branch switch, an archive, a delete or a move must ask about first,
 * since each replaces the working tree the edits would be saved into.
 */
export default function useProjectDirty(): boolean {
  const canvas = useGraphStore((s) => s.dirty)
  const form = useWorkbenchStore((s) => s.formDirty)
  return canvas || form
}

/** `useProjectDirty` read now, outside a component: a toolbar undo's switch asks it. */
export function isProjectDirty(): boolean {
  return useGraphStore.getState().dirty || useWorkbenchStore.getState().formDirty
}

/**
 * After a switch the user chose over the form's unsaved edits: read the file again, so
 * the destination's form shows rather than the edits they discarded (the form store keeps
 * a dirty form through a document adoption, as it should when nothing was chosen).
 * Nothing while the form is as saved, when the adoption brings the file by itself, and
 * nothing when the switch failed, since the edits are then still wanted.
 */
export async function discardFormEdits(): Promise<void> {
  if (!useWorkbenchStore.getState().formDirty) return
  const { default: formStore } = await import("../stores/useWorkbenchFormStore")
  await formStore.getState().reload()
}

/**
 * After a switch the user chose over the canvas's unsaved edits: drop them and ask for the
 * document again, so the destination's pipeline shows rather than the edits they discarded
 * (a dirty canvas blocks the server's broadcast of it, behind the sync banner). Nothing
 * while the canvas is as saved, when the broadcast brings the document by itself, and
 * nothing when the switch failed, since the edits are then still wanted.
 */
export function discardCanvasEdits(): void {
  if (!useGraphStore.getState().dirty) return
  useGraphStore.getState().markSaved()
  useUIStore.getState().setSyncBanner(null)
  // The canvas still holds the edits, not the document it last accepted: the
  // answer must replace it even when the destination's document is that one.
  useDocumentStatusStore.getState().setGraphSynchronized(false)
  useDocumentStatusStore.getState().requestResync()
}
