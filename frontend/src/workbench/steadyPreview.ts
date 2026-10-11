/**
 * Preview held steady under the underwriter (specs/workbench). A section that opens or
 * closes moves what is under it: what is drawn holds while a pointer is pressed on the
 * sheet, so a value the press commits never moves the press's target from under it, and
 * the viewport scrolls by as much as the place being worked on moved, which the browser's
 * own scroll anchoring does not do for components placed by position.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { SHEET_PADDING, type Stacked } from "../utils/sheetGeometry"

/** What ends a press, caught before anything below can stop it: its release or cancel, or a context menu, which may take the release. */
const LET_GO = ["pointerup", "pointercancel", "contextmenu"] as const

/**
 * `value` as it was when the primary pointer button was pressed on `viewport` while
 * `active`, until a turn after the press ends (`LET_GO`, or the window losing the focus), so
 * the press's own click lands first; else `value` as it is.
 */
export function useHeldWhilePressed<T>(value: T, active: boolean, viewport: HTMLElement | null): T {
  const [held, setHeld] = useState<{ value: T } | null>(null)
  const latest = useRef(value)
  useLayoutEffect(() => {
    latest.current = value
  })
  useEffect(() => {
    if (!active || viewport === null) return
    let release: number | undefined
    const press = (event: PointerEvent) => {
      if (event.button !== 0) return
      window.clearTimeout(release)
      setHeld({ value: latest.current })
    }
    const letGo = () => {
      release = window.setTimeout(() => setHeld(null), 0)
    }
    viewport.addEventListener("pointerdown", press, true)
    for (const type of LET_GO) window.addEventListener(type, letGo, true)
    // The window's own blur alone, never caught on the way down: a field blurring as the
    // press leaves it is what the hold is for.
    window.addEventListener("blur", letGo)
    return () => {
      viewport.removeEventListener("pointerdown", press, true)
      for (const type of LET_GO) window.removeEventListener(type, letGo, true)
      window.removeEventListener("blur", letGo)
      window.clearTimeout(release)
      setHeld(null)
    }
  }, [active, viewport])
  return held === null ? value : held.value
}

/** A drawn section: its id and height, as `stackSections` stacks them. */
type Band = Stacked<{ id: string; height: number }>

/**
 * The section to keep in place between two draws, as drawn before: the one holding the
 * focus, else the first reaching below the viewport's top. One no longer drawn moved
 * nowhere, so nothing scrolls for it.
 */
function anchorOf(before: readonly Band[], viewport: HTMLElement, zoom: number, scrolled: number): Band | null {
  const focused = document.activeElement instanceof HTMLElement && viewport.contains(document.activeElement)
    ? document.activeElement.closest<HTMLElement>("[data-section-id]")?.dataset.sectionId
    : undefined
  return (
    before.find((band) => band.section.id === focused)
    ?? before.find((band) => SHEET_PADDING + (band.top + band.section.height) * zoom > scrolled)
    ?? null
  )
}

/**
 * After a draw that moved the sections drawn (`stack`) on the same sheet (`key`) at the same
 * zoom, scroll `viewport` by as much as, at the zoom, the section holding the focus moved,
 * or else the first section reaching below the viewport's top, from the scroll position a
 * scroll listener last recorded, since the browser clamps it as the sheet shortens. A null
 * `key` keeps nothing.
 */
export function useKeptPlace(viewport: HTMLElement | null, stack: readonly Band[], zoom: number, key: string | null): void {
  const drawn = useRef<{ key: string; zoom: number; stack: readonly Band[] } | null>(null)
  const scrolled = useRef(0)
  useEffect(() => {
    if (viewport === null) return
    const record = () => {
      scrolled.current = viewport.scrollTop
    }
    record()
    viewport.addEventListener("scroll", record, { passive: true })
    return () => viewport.removeEventListener("scroll", record)
  }, [viewport])
  useLayoutEffect(() => {
    const before = drawn.current
    drawn.current = key === null ? null : { key, zoom, stack }
    if (viewport === null || before === null || before.key !== key || before.zoom !== zoom || before.stack === stack) return
    const anchor = anchorOf(before.stack, viewport, zoom, scrolled.current)
    const now = anchor === null ? undefined : stack.find((band) => band.section.id === anchor.section.id)
    if (anchor === null || now === undefined || now.top === anchor.top) return
    viewport.scrollTo({ top: scrolled.current + (now.top - anchor.top) * zoom })
    scrolled.current = viewport.scrollTop
  })
}
