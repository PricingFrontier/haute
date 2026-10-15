import { useCallback, useLayoutEffect, useRef, type KeyboardEvent } from "react"

export interface RefocusAfterMove<T extends HTMLElement> {
  /** The ref for the item `key`: its element, kept while it is drawn. */
  register: (key: string) => (element: T | null) => void
  /**
   * Note that the item `key` is about to move, or to be drawn anew (a tab after renaming):
   * it takes the focus again once it is drawn.
   */
  moved: (key: string) => void
  /** The item `key`'s element while it is drawn. */
  element: (key: string) => T | undefined
}

/**
 * Puts the focus back on an item a keyboard move (Alt+arrows) redrew in another place of its
 * list: moving an element in the page drops its focus; and on one drawn anew in place of a
 * field that had it. Used by the list that draws the items, so it runs once the list is
 * drawn after the move.
 */
export default function useRefocusAfterMove<T extends HTMLElement>(): RefocusAfterMove<T> {
  const items = useRef(new Map<string, T>())
  const pending = useRef<string | null>(null)
  useLayoutEffect(() => {
    const key = pending.current
    if (key === null) return
    pending.current = null
    items.current.get(key)?.focus()
  })
  // One ref per item, kept, so a redraw does not hand React a new ref for each item.
  const refs = useRef(new Map<string, (element: T | null) => void>())
  const register = useCallback((key: string) => {
    let ref = refs.current.get(key)
    if (ref === undefined) {
      ref = (element: T | null) => {
        if (element === null) items.current.delete(key)
        else items.current.set(key, element)
      }
      refs.current.set(key, ref)
    }
    return ref
  }, [])
  const moved = useCallback((key: string) => {
    pending.current = key
  }, [])
  const element = useCallback((key: string) => items.current.get(key), [])
  return { register, moved, element }
}

/**
 * Where the item at `index` among `count` goes on Alt+Up and Alt+Down (`vertical`), or
 * Alt+Left and Alt+Right (`horizontal`): the place before or after it, or null for any other
 * key and past either end. The key's default is prevented whenever it is the move's key, at
 * an end too, so the list never scrolls under it.
 */
export function altArrowMove(event: KeyboardEvent, axis: "vertical" | "horizontal", index: number, count: number): number | null {
  const [back, on] = axis === "vertical" ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"]
  if (!event.altKey || (event.key !== back && event.key !== on)) return null
  event.preventDefault()
  const to = index + (event.key === back ? -1 : 1)
  return to < 0 || to >= count ? null : to
}
