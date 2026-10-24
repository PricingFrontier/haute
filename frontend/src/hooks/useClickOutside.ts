import { useEffect, type RefObject } from "react"

/**
 * Close a dropdown/popover when the user presses outside its container.
 * Only attaches a listener when `active` is true. The press is heard as it
 * begins, on the document in the capture phase, so a press whose own handler
 * then cancels the mouse events after it or stops it, as a workbench sheet
 * component's does, still closes it.
 */
export default function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  active: boolean,
) {
  useEffect(() => {
    if (!active) return
    const handler = (e: PointerEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) {
        onClose()
      }
    }
    document.addEventListener("pointerdown", handler, true)
    return () => document.removeEventListener("pointerdown", handler, true)
  }, [active, ref, onClose])
}
