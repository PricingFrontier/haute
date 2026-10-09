import { useEffect } from "react"
import { hasModifier, inModalDialog, isTypingTarget, saveCommittingField } from "../hooks/keyboardTargets"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"

/**
 * The workbench view's keyboard shortcuts, registered while it shows (specs/workbench):
 * Ctrl+S saves the form, with a focused field's edit included, and Ctrl+Z and
 * Ctrl+Shift+Z (or Ctrl+Y) undo and redo an edit, outside a text field. Keys in a modal
 * dialog are the dialog's, as they are for the pipeline editor's shortcuts.
 */
export default function useWorkbenchShortcuts(): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !hasModifier(e) || inModalDialog(e.target)) return
      const key = e.key.toLowerCase()
      if (key === "s") {
        e.preventDefault()
        saveCommittingField(e.target, () => {
          void useWorkbenchFormStore.getState().save()
        })
        return
      }
      if (isTypingTarget(e.target)) return
      if (key === "z" && !e.shiftKey) {
        e.preventDefault()
        useWorkbenchFormStore.getState().undo()
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault()
        useWorkbenchFormStore.getState().redo()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])
}
