/**
 * Pointer interactions on a sheet while building (specs/workbench): dragging a new
 * component or a new section out of the palette, moving or resizing a component by its
 * frame or a handle, and setting a section's height by its handle or moving the section by
 * its header; and deleting a section, asking first. Positions on the sheet are in unzoomed
 * pixels, each section under the bar that names it while building (`SECTION_HEADER`);
 * pointer movement is divided by the zoom. A move or a resize is one undo step: the form is
 * recorded when the pointer first moves, and each step after that replaces it without
 * history by a form made afresh from the form as pressed, so a click that does not move
 * records nothing and only the section a component lands in grows.
 */
import type { PointerEvent as ReactPointerEvent } from "react"
import type { Page, Section } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import {
  clearLine,
  cutRun,
  MIN_SECTION_HEIGHT,
  moveRect,
  placeAt,
  resizeRect,
  SECTION_HEADER,
  sectionAt,
  snap,
  stackBottom,
  stackSections,
  type Handle,
  type Rect,
  type Stacked,
} from "../utils/sheetGeometry"
import {
  addSection,
  addWidget,
  createSection,
  createWidget,
  findSection,
  findWidget,
  keptInside,
  moveSection,
  placeWidget,
  removeSection,
  setSectionHeight,
  splitSection,
  updateWidget,
  type PaletteType,
  type WidgetType,
} from "../utils/workbenchForm"
import { WIDGET_KINDS } from "./widgetKinds"

/** Where the pointer sits relative to a new component's top-left corner while dragging it in. */
const GRAB = { x: 16, y: 12 }
const DRAG_THRESHOLD = 4

/** A sheet's sections as building draws them, each under its header. */
export const buildStack = (page: Page): Stacked<Section>[] => stackSections(page.sections, SECTION_HEADER)

/** Where a component dragged in lands: its section, its place in it, and the same place on the sheet. */
export interface Drop {
  sectionId: string
  rect: Rect
  at: Rect
}

/**
 * Where a section dragged in starts: at `line` down the sheet, either splitting the section
 * there (`split`, `at` down it) or put in at a place among the sheet's sections (`index`).
 */
export interface SectionDrop {
  line: number
  place: { split: string; at: number } | { index: number }
}

/** The showing sheet and a pointer's place on it, unzoomed, or null off the viewport or above or left of the sheet. */
function onSheet(clientX: number, clientY: number): { page: Page; px: number; py: number } | null {
  const { sheet, viewport, zoom, pageId } = useWorkbenchViewStore.getState()
  const { form } = useWorkbenchFormStore.getState()
  if (sheet === null || viewport === null || form === null) return null
  const seen = viewport.getBoundingClientRect()
  if (clientX < seen.left || clientX > seen.right || clientY < seen.top || clientY > seen.bottom) return null
  const origin = sheet.getBoundingClientRect()
  const px = (clientX - origin.left) / zoom
  const py = (clientY - origin.top) / zoom
  if (px < 0 || py < 0) return null
  return { page: activePage(form, pageId), px, py }
}

/**
 * The place in its section, snapped and kept inside it, never across its top or its bottom
 * (`keptInside`), of a component whose place on the sheet is `rect`, in the section its top
 * edge is over among `stack`.
 */
function landing<T extends { id: string; height: number }>(stack: readonly Stacked<T>[], rect: Rect): { section: Stacked<T>; rect: Rect } | null {
  const section = sectionAt(stack, rect.y)
  if (section === null) return null
  return { section, rect: { ...rect, y: keptInside(snap(rect.y - section.top), rect.h, section.section.height) } }
}

/** Where dropping a palette item at this pointer position would put a component, or null off the sheet. */
export function dropAt(type: WidgetType, clientX: number, clientY: number): Drop | null {
  const point = onSheet(clientX, clientY)
  if (point === null) return null
  const landed = landing(buildStack(point.page), placeAt(point.px - GRAB.x, point.py - GRAB.y, WIDGET_KINDS[type].size))
  if (landed === null) return null
  const { section, rect } = landed
  return { sectionId: section.section.id, rect, at: { ...rect, y: section.top + rect.y } }
}

/**
 * Where dropping a section at this pointer position would start it, or null off the sheet:
 * inside a section, a split at the grid line nearest the pointer, moved to the nearer edge of
 * any component it would cut through (`clearLine`) and never less than `MIN_SECTION_HEIGHT`
 * down it, the least the section above can be (nearer its top than that, in before it or at
 * the first clear line that far down, whichever is nearer the pointer); on a section's bar
 * or at its very top, in before it; at its very bottom, in after it; below the last section,
 * at the end. The line drawn while dragging is where the section starts once dropped.
 */
export function sectionDropAt(clientX: number, clientY: number): SectionDrop | null {
  const point = onSheet(clientX, clientY)
  if (point === null) return null
  const stack = buildStack(point.page)
  const bottom = stackBottom(stack)
  const target = point.py >= bottom ? undefined : sectionAt(stack, point.py)
  if (target === undefined || target === null) return { line: bottom, place: { index: stack.length } }
  const index = stack.indexOf(target)
  const { widgets } = target.section
  const y = snap(point.py - target.top)
  let at = clearLine(widgets, y)
  if (at > 0 && at < MIN_SECTION_HEIGHT) {
    const below = cutRun(widgets, MIN_SECTION_HEIGHT).bottom
    at = y <= below - y ? 0 : below
  }
  if (at <= 0) return { line: target.top - SECTION_HEADER, place: { index } }
  if (at >= target.section.height) return { line: target.top + target.section.height, place: { index: index + 1 } }
  return { line: target.top + at, place: { split: target.section.id, at } }
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

/**
 * Commit a field being typed into before a gesture reads the form. A press whose default is
 * prevented does not move the focus, so the field would keep what was typed, lost when the
 * panel it is in goes or written over by the gesture; a field left commits what was typed
 * into it as it blurs, so the form is read after that, and the edit is an undo step apart
 * from the gesture's.
 */
function leaveField(pressed: EventTarget): void {
  if (document.activeElement instanceof HTMLElement && document.activeElement !== pressed) document.activeElement.blur()
}

/**
 * Drag a palette item onto the sheet; released there, a component is added to its section
 * and selected, or a section is started where it was dropped and selected. A click adds
 * nothing.
 */
export function startCreate(e: ReactPointerEvent, type: PaletteType): void {
  if (e.button !== 0) return
  e.preventDefault()
  leaveField(e.currentTarget)
  const { setCreating } = useWorkbenchViewStore.getState()
  track(
    e,
    "grabbing",
    (_dx, _dy, event) => setCreating({ type, clientX: event.clientX, clientY: event.clientY }),
    (moved, event) => {
      setCreating(null)
      if (!moved) return
      const { form, change } = useWorkbenchFormStore.getState()
      if (form === null) return
      const view = useWorkbenchViewStore.getState()
      const page = activePage(form, view.pageId)
      if (type === "section") {
        const drop = sectionDropAt(event.clientX, event.clientY)
        if (drop === null) return
        const section = createSection(page)
        const { place } = drop
        change((spec) => ("split" in place ? splitSection(spec, place.split, place.at, section) : addSection(spec, page.id, section, place.index)))
        view.showPage(page.id)
        view.selectSection(section.id)
        return
      }
      const drop = dropAt(type, event.clientX, event.clientY)
      if (drop === null) return
      const widget = createWidget(type, drop.rect)
      change((spec) => addWidget(spec, drop.sectionId, widget))
      view.showPage(page.id)
      view.select(widget.id)
    },
  )
}

/**
 * Drag a component, into the section its top edge is over, or resize it by one of its
 * handles within its section. Clicking without moving just selects it.
 */
export function startTransform(e: ReactPointerEvent, id: string, handle?: Handle): void {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  // A cell focused earlier would otherwise keep taking the keys meant for the selected component.
  leaveField(e.currentTarget)
  const { zoom, select } = useWorkbenchViewStore.getState()
  const { form } = useWorkbenchFormStore.getState()
  const found = form === null ? null : findWidget(form, id)
  if (form === null || found === null) return
  select(id)
  // Each step is made from the form as pressed: only the section the component ends in
  // grows, so none above it moves and the component stays under the pointer.
  const pressed = form
  const { x, y, w, h } = found.widget
  const original = { x, y, w, h }
  const { min } = WIDGET_KINDS[found.widget.type]
  const stack = buildStack(found.page)
  const top = stack.find((stacked) => stacked.section.id === found.section.id)?.top ?? 0
  track(
    e,
    handle ? `${handle}-resize` : "grabbing",
    (dx, dy) => {
      const store = useWorkbenchFormStore.getState()
      if (store.form === null) return
      if (handle) {
        store.setFormRaw(updateWidget(pressed, id, resizeRect(original, handle, dx / zoom, dy / zoom, min)))
        return
      }
      const landed = landing(stack, moveRect({ ...original, y: top + original.y }, dx / zoom, dy / zoom))
      if (landed !== null) store.setFormRaw(placeWidget(pressed, id, landed.section.section.id, landed.rect))
    },
    // Released or cancelled where it is: one undo step, none for a gesture that changed nothing.
    () => useWorkbenchFormStore.getState().commitGesture(pressed),
  )
}

/**
 * Drag a section's edge to set its height, on the grid and never under what it holds, one
 * undo step; a click, or a drag held at the least it can be, sets nothing.
 */
export function startSectionResize(e: ReactPointerEvent, sectionId: string): void {
  if (e.button !== 0) return
  e.stopPropagation()
  e.preventDefault()
  leaveField(e.currentTarget)
  const { zoom } = useWorkbenchViewStore.getState()
  const { form } = useWorkbenchFormStore.getState()
  const found = form === null ? null : findSection(form, sectionId)
  if (form === null || found === null) return
  const pressed = form
  const { height } = found.section
  track(
    e,
    "row-resize",
    (_dx, dy) => {
      const store = useWorkbenchFormStore.getState()
      if (store.form === null) return
      store.setFormRaw(setSectionHeight(pressed, sectionId, snap(height + dy / zoom)))
    },
    () => useWorkbenchFormStore.getState().commitGesture(pressed),
  )
}

/**
 * The place a section at `from` would move to with the pointer `y` down the sheet: after
 * every other section whose middle is above the pointer.
 */
export const sectionPlace = (stack: readonly Stacked<{ height: number }>[], from: number, y: number): number =>
  stack.filter(({ section, top }, index) => index !== from && top + section.height / 2 < y).length

/**
 * Drag a section's header up or down past others to move the section there, one undo step
 * on release; `movingSection` follows the pointer meanwhile. A click moves nothing.
 */
export function startSectionMove(e: ReactPointerEvent, sectionId: string): void {
  if (e.button !== 0) return
  leaveField(e.currentTarget)
  const { form } = useWorkbenchFormStore.getState()
  const found = form === null ? null : findSection(form, sectionId)
  if (form === null || found === null) return
  const { page, index } = found
  const stack = buildStack(page)
  track(
    e,
    "grabbing",
    (_dx, _dy, event) => {
      const { sheet, zoom, setMovingSection } = useWorkbenchViewStore.getState()
      if (sheet === null) return
      setMovingSection({ id: sectionId, to: sectionPlace(stack, index, (event.clientY - sheet.getBoundingClientRect().top) / zoom) })
    },
    (moved) => {
      const { movingSection, setMovingSection } = useWorkbenchViewStore.getState()
      setMovingSection(null)
      if (!moved || movingSection === null || movingSection.to === index) return
      useWorkbenchFormStore.getState().change((spec) => moveSection(spec, page.id, index, movingSection.to))
    },
  )
}

/**
 * Delete a section with its components, asking first while components are in it; false
 * when the builder says no, and for a sheet's last section, which is never removed. The
 * section's panel closes with it.
 */
export function deleteSection(page: Page, section: Section): boolean {
  if (page.sections.length === 1) return false
  const count = section.widgets.length
  const asked = `Delete "${section.title}" and the ${count} ${count === 1 ? "component" : "components"} in it? The schema and the sample stay.`
  if (count > 0 && !window.confirm(asked)) return false
  useWorkbenchFormStore.getState().change((spec) => removeSection(spec, section.id))
  const view = useWorkbenchViewStore.getState()
  if (view.sectionId === section.id) view.select(null)
  return true
}
