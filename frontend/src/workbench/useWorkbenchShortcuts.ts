import { useEffect, useRef } from "react"
import { hasModifier, inModalDialog, isFormControl, isTypingTarget, saveCommittingField } from "../hooks/keyboardTargets"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { GRID } from "../utils/sheetGeometry"
import { duplicateWidget, findWidget, removeWidget, updateWidget } from "../utils/workbenchForm"

/** Arrow presses this close together nudge as one undo step, as a drag is one. */
const NUDGE_BURST_MS = 800

/**
 * The workbench view's keyboard shortcuts, registered while it shows (specs/workbench):
 * Ctrl+S saves the form, with a focused field's edit included; while building, Ctrl+Z
 * and Ctrl+Shift+Z (or Ctrl+Y) undo and redo an edit outside a text field. While the
 * sheets show, building or in Preview, Ctrl+1 fits the sheet to its viewport; while
 * building, with a component selected, Escape deselects it, Delete or Backspace removes
 * it, Ctrl+D duplicates it and the arrow keys nudge it by a grid step (Shift: a pixel), a
 * burst of nudges being one undo step; none of that from a control, whose own keys pick
 * an option. Keys in a modal dialog are the dialog's, as they are for the pipeline
 * editor's shortcuts.
 */
export default function useWorkbenchShortcuts(): void {
  // The component being nudged, when its burst ends, and the form the last nudge made:
  // an undo, a redo or any other edit in between replaces it, and ends the burst.
  const nudging = useRef<{ id: string; until: number; form: object } | null>(null)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented || inModalDialog(e.target)) return
      const mod = hasModifier(e)
      const key = e.key.toLowerCase()
      const forms = useWorkbenchFormStore.getState()
      if (mod && key === "s") {
        e.preventDefault()
        saveCommittingField(e.target, () => {
          void forms.save()
        })
        return
      }
      if (isTypingTarget(e.target)) return
      const view = useWorkbenchViewStore.getState()
      const previewing = view.section === "preview"
      if (!previewing && mod && key === "z" && !e.shiftKey) {
        e.preventDefault()
        forms.undo()
        return
      }
      if (!previewing && mod && ((key === "z" && e.shiftKey) || key === "y")) {
        e.preventDefault()
        forms.redo()
        return
      }
      if ((view.section !== "sheets" && !previewing) || forms.form === null || isFormControl(e.target)) return
      if (mod && key === "1") {
        e.preventDefault()
        view.fitZoom()
        return
      }
      if (previewing) return
      const selected = view.selectedId === null ? null : findWidget(forms.form, view.selectedId)?.widget ?? null
      if (selected === null) return
      if (e.key === "Escape") {
        view.select(null)
      } else if (e.key === "Delete" || e.key === "Backspace") {
        forms.change((spec) => removeWidget(spec, selected.id))
        view.select(null)
      } else if (mod && key === "d") {
        let copyId = ""
        forms.change((spec) => {
          const copied = duplicateWidget(spec, selected.id)
          copyId = copied.id
          return copied.spec
        })
        view.select(copyId)
      } else if (e.key.startsWith("Arrow")) {
        const step = e.shiftKey ? 1 : GRID
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0
        if (dx === 0 && dy === 0) return
        const now = Date.now()
        const burst = nudging.current
        if (burst === null || burst.id !== selected.id || now > burst.until || burst.form !== forms.form) {
          forms.pushSnapshot()
        }
        const nudged = updateWidget(forms.form, selected.id, { x: Math.max(0, selected.x + dx), y: Math.max(0, selected.y + dy) })
        nudging.current = { id: selected.id, until: now + NUDGE_BURST_MS, form: nudged }
        forms.setFormRaw(nudged)
      } else {
        return
      }
      e.preventDefault()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])
}
