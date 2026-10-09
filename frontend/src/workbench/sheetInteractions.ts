/**
 * Pointer interactions on a sheet (specs/workbench): dragging a new component out of the
 * palette, and moving or resizing one by its frame or a handle. Positions on the sheet are
 * in unzoomed pixels; pointer movement is divided by the zoom. A move or a resize is one
 * undo step: the form is recorded when the pointer first moves, and each step after that
 * replaces it without history, so a click that does not move records nothing.
 */
import type { PointerEvent as ReactPointerEvent } from "react"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { moveRect, placeAt, resizeRect, type Handle, type Rect } from "../utils/sheetGeometry"
import { addWidget, createWidget, findWidget, updateWidget, type WidgetType } from "../utils/workbenchForm"
import { WIDGET_KINDS } from "./widgetKinds"

/** Where the pointer sits relative to a new component's top-left corner while dragging it in. */
const GRAB = { x: 16, y: 12 }
const DRAG_THRESHOLD = 4

/** Where dropping a palette item at this pointer position would put a component, or null off the sheet. */
export function dropAt(type: WidgetType, clientX: number, clientY: number): Rect | null {
  const { sheet, viewport, zoom } = useWorkbenchViewStore.getState()
  if (sheet === null || viewport === null) return null
  const seen = viewport.getBoundingClientRect()
  if (clientX < seen.left || clientX > seen.right || clientY < seen.top || clientY > seen.bottom) return null
  const origin = sheet.getBoundingClientRect()
  const px = (clientX - origin.left) / zoom
  const py = (clientY - origin.top) / zoom
  if (px < 0 || py < 0) return null
  return placeAt(px - GRAB.x, py - GRAB.y, WIDGET_KINDS[type].size)
}

/** Follow the pointer until it is released, calling `onMove` with the distance travelled. */
function track(
  e: ReactPointerEvent,
  cursor: string,
  onMove: (dx: number, dy: number, event: PointerEvent) => void,
  onEnd: (moved: boolean, event: PointerEvent) => void,
): void {
  const start = { x: e.clientX, y: e.clientY }
  let moved = false
  const move = (event: PointerEvent) => {
    const dx = event.clientX - start.x
    const dy = event.clientY - start.y
    if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    if (!moved) document.body.style.cursor = cursor
    moved = true
    onMove(dx, dy, event)
  }
  const end = (event: PointerEvent) => {
    window.removeEventListener("pointermove", move)
    window.removeEventListener("pointerup", end)
    window.removeEventListener("pointercancel", end)
    document.body.style.cursor = ""
    onEnd(moved && event.type === "pointerup", event)
  }
  window.addEventListener("pointermove", move)
  window.addEventListener("pointerup", end)
  window.addEventListener("pointercancel", end)
}

/** Drag a palette item onto the sheet; released there, a component is added and selected. A click adds nothing. */
export function startCreate(e: ReactPointerEvent, type: WidgetType): void {
  if (e.button !== 0) return
  e.preventDefault()
  const { setCreating } = useWorkbenchViewStore.getState()
  track(
    e,
    "grabbing",
    (_dx, _dy, event) => setCreating({ type, clientX: event.clientX, clientY: event.clientY }),
    (moved, event) => {
      setCreating(null)
      if (!moved) return
      const rect = dropAt(type, event.clientX, event.clientY)
      const { form, change } = useWorkbenchFormStore.getState()
      if (rect === null || form === null) return
      const view = useWorkbenchViewStore.getState()
      const page = activePage(form, view.pageId)
      const widget = createWidget(type, rect)
      change((spec) => addWidget(spec, page.id, widget))
      view.showPage(page.id)
      view.select(widget.id)
    },
  )
}

/** Drag a component, or resize it by one of its handles. Clicking without moving just selects it. */
export function startTransform(e: ReactPointerEvent, id: string, handle?: Handle): void {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  const { zoom, select } = useWorkbenchViewStore.getState()
  const { form } = useWorkbenchFormStore.getState()
  const found = form === null ? null : findWidget(form, id)
  if (found === null) return
  select(id)
  const { x, y, w, h } = found.widget
  const original = { x, y, w, h }
  const { min } = WIDGET_KINDS[found.widget.type]
  let started = false
  track(
    e,
    handle ? `${handle}-resize` : "grabbing",
    (dx, dy) => {
      const store = useWorkbenchFormStore.getState()
      if (store.form === null) return
      if (!started) {
        store.pushSnapshot()
        started = true
      }
      const rect = handle
        ? resizeRect(original, handle, dx / zoom, dy / zoom, min)
        : moveRect(original, dx / zoom, dy / zoom)
      store.setFormRaw(updateWidget(store.form, id, rect))
    },
    () => {},
  )
}
