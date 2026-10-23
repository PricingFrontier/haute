import { useEffect, useRef } from "react"
import { hasModifier, inModalDialog, isFormControl, isTypingTarget, saveCommittingField } from "../hooks/keyboardTargets"
import useToastStore from "../stores/useToastStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { GRID, grownRect } from "../utils/sheetGeometry"
import { duplicateWidget, findWidget, moveWidget, pasteWidget, removeWidget, updateWidget, widgetName } from "../utils/workbenchForm"
import { WIDGET_KINDS } from "./widgetKinds"

/** Arrow presses this close together nudge as one undo step, as a drag is one. */
const NUDGE_BURST_MS = 800

/**
 * The keystroke landed on the sheet, or with nothing focused, as after a press on a
 * component: from a panel, the toolbar or a tab, Delete, the arrows and the shortcuts on
 * the selection are the focused element's, never the component's.
 */
function onSheet(target: EventTarget | null, viewport: HTMLElement | null): boolean {
  if (!(target instanceof Node) || target === document || target === document.body || target === document.documentElement) return true
  return viewport !== null && viewport.contains(target)
}

/** The keystroke landed inside `element`, such as the schema's pane while it is open. */
function inside(target: EventTarget | null, element: HTMLElement | null): boolean {
  return element !== null && target instanceof Node && element.contains(target)
}

/**
 * The workbench view's keyboard shortcuts, registered while it shows (specs/workbench):
 * Ctrl+S saves the project through the host, as the toolbar's Save does, with a focused
 * field's edit included; building or in Preview, Ctrl+Z and Ctrl+Shift+Z (or Ctrl+Y) undo
 * and redo an edit outside a text field. Building or in Preview, Ctrl+1 fits the sheet to
 * its viewport; while building, Escape closes the sheet's own
 * panel or the section's and, with a component selected, Escape deselects it, Delete or
 * Backspace removes it, Ctrl+D duplicates it, Ctrl+C copies it, the arrow keys nudge it
 * by a grid step (Shift: a pixel), kept inside its section, and with Ctrl resize it from
 * its bottom-right corner by as much, never under its least size, its section and sheet
 * growing to hold it, a burst of nudges or resizes being one undo step, those from the sheet or with nothing
 * focused, never from a panel, the toolbar or a tab; selected or not, Ctrl+V pastes the
 * component copied onto the sheet showing; none of that from a control, whose own keys
 * pick an option, or from inside the schema's pane, whose keys are its own. A section header's own keys are its own. Keys in a modal dialog are the
 * dialog's, as they are for the pipeline editor's shortcuts.
 */
export default function useWorkbenchShortcuts(onSave: () => void): void {
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
        saveCommittingField(e.target, onSave)
        return
      }
      if (isTypingTarget(e.target)) return
      const view = useWorkbenchViewStore.getState()
      const previewing = view.mode === "preview"
      if (mod && key === "z" && !e.shiftKey) {
        e.preventDefault()
        forms.undo()
        return
      }
      if (mod && ((key === "z" && e.shiftKey) || key === "y")) {
        e.preventDefault()
        forms.redo()
        return
      }
      if (forms.form === null || isFormControl(e.target) || inside(e.target, view.schemaPane)) return
      if (mod && key === "1") {
        e.preventDefault()
        view.fitZoom()
        return
      }
      if (previewing) return
      if ((view.sheetSelected || view.sectionId !== null) && e.key === "Escape") {
        e.preventDefault()
        view.select(null)
        return
      }
      const found = view.selectedId === null ? null : findWidget(forms.form, view.selectedId)
      const plain = mod && !e.shiftKey && !e.altKey
      if (plain && key === "v" && view.copied !== null) {
        // Into the section selected, else the selected component's, else the one it was
        // copied from while that is on the sheet showing, else the sheet's first.
        e.preventDefault()
        const { widget, sectionId } = view.copied
        const page = activePage(forms.form, view.pageId)
        const onPage = (id: string | null | undefined): id is string => page.sections.some((section) => section.id === id)
        const target = [view.sectionId, found?.section.id, sectionId].find(onPage) ?? page.sections[0].id
        let pastedId = ""
        forms.change((spec) => {
          const pasted = pasteWidget(spec, widget, target)
          pastedId = pasted.id
          return pasted.spec
        })
        view.select(pastedId)
        return
      }
      if (found === null) return
      const selected = found.widget
      if (e.key === "Escape") {
        view.select(null)
      } else if (!onSheet(e.target, view.viewport)) {
        return
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
      } else if (plain && key === "c") {
        view.setCopied({ widget: structuredClone(selected), sectionId: found.section.id })
        useToastStore.getState().addToast("info", `${widgetName(selected)} copied`)
        // The browser's own copy of any text selected still happens, as the pipeline editor's does.
        return
      } else if (e.key.startsWith("Arrow")) {
        const step = e.shiftKey ? 1 : GRID
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0
        if (dx === 0 && dy === 0) return
        // With Ctrl (Cmd), a resize from the bottom-right corner, as its handle there makes.
        const nudged = mod
          ? updateWidget(forms.form, selected.id, grownRect(selected, dx, dy, WIDGET_KINDS[selected.type].min))
          : moveWidget(forms.form, selected.id, { x: selected.x + dx, y: selected.y + dy })
        const moved = findWidget(nudged, selected.id)?.widget
        // Held at its section's edge, the sheet's left or its least size: nothing changes, so nothing is recorded.
        if (moved !== undefined && moved.x === selected.x && moved.y === selected.y && moved.w === selected.w && moved.h === selected.h) {
          e.preventDefault()
          return
        }
        // A drag under way is finished first, as any edit finishes it, even inside a burst,
        // so its later steps never write over the nudge.
        forms.endGesture()
        const now = Date.now()
        const burst = nudging.current
        if (burst === null || burst.id !== selected.id || now > burst.until || burst.form !== forms.form) {
          forms.pushSnapshot()
        }
        nudging.current = { id: selected.id, until: now + NUDGE_BURST_MS, form: nudged }
        forms.setFormRaw(nudged)
      } else {
        return
      }
      e.preventDefault()
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [onSave])
}
