import useGraphStore from "../stores/useGraphStore"
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
