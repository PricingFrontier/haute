import { useEffect } from "react"

/**
 * Close a transient popover / inline dialog on Escape, topmost-first.
 *
 * Registers a CAPTURE-phase document keydown listener so the popover handles
 * Escape before any ancestor (e.g. a panel-level or app-level Escape handler)
 * and — via stopPropagation — closes only itself, leaving the surrounding panel
 * open. This mirrors the app's existing topmost-first Escape arbitration.
 *
 * Active only while `enabled` is true, so a closed dialog never intercepts
 * Escape meant for whatever is actually on top.
 */
export default function useEscapeToClose(onClose: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
    document.addEventListener("keydown", onKey, true)
    return () => document.removeEventListener("keydown", onKey, true)
  }, [onClose, enabled])
}
