/**
 * Refit a React Flow whenever its container resizes.
 *
 * Powers the wrapper Peek's "dynamic rescale": as the user resizes the peek
 * panel, the inner flow re-fits so the whole submodel stays framed (rather than
 * the graph staying put while the window grows/shrinks around it). Also handles
 * the open-time jump from the panel's default size to the bounding-box-derived
 * size. rAF-coalesced so a burst of resize callbacks triggers one fitView.
 *
 * Extracted as a hook so the behaviour is unit-testable without a live flow
 * (jsdom can't measure a real React Flow pane): the test drives a controllable
 * ResizeObserver and asserts fitView fires.
 */
import { useEffect, type RefObject } from "react"

export function useFitViewOnResize(
  ref: RefObject<HTMLElement | null>,
  fitView: (opts?: { padding?: number }) => void,
  padding: number,
): void {
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === "undefined") return
    let raf = 0
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => fitView({ padding }))
    })
    observer.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [ref, fitView, padding])
}
