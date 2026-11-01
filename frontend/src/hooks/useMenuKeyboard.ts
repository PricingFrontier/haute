import { useEffect, useLayoutEffect, type RefObject } from "react"

function getEnabledMenuItems(container: HTMLElement | null): HTMLElement[] {
  if (!container) return []
  return Array.from(
    container.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled):not([aria-disabled="true"])'),
  )
}

function setMenuFocus(items: HTMLElement[], targetIndex: number): void {
  items.forEach((item, index) => {
    item.setAttribute("tabindex", index === targetIndex ? "0" : "-1")
  })
  items[targetIndex]?.focus()
}

export function useMenuKeyboard(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  active = true,
): void {
  useLayoutEffect(() => {
    if (!active) return
    const items = getEnabledMenuItems(ref.current)
    if (items.length > 0) {
      setMenuFocus(items, 0)
    }
  }, [active, ref])

  useEffect(() => {
    if (!active) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        onClose()
        return
      }
      if (
        event.key !== "ArrowDown" &&
        event.key !== "ArrowUp" &&
        event.key !== "Home" &&
        event.key !== "End"
      ) {
        return
      }
      const items = getEnabledMenuItems(ref.current)
      if (items.length === 0) return
      event.preventDefault()
      const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
      const currentIndex = activeElement ? items.indexOf(activeElement) : -1
      let nextIndex = 0
      if (event.key === "ArrowDown") {
        nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % items.length
      } else if (event.key === "ArrowUp") {
        nextIndex = currentIndex < 0 ? items.length - 1 : (currentIndex - 1 + items.length) % items.length
      } else if (event.key === "Home") {
        nextIndex = 0
      } else if (event.key === "End") {
        nextIndex = items.length - 1
      }
      setMenuFocus(items, nextIndex)
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [active, onClose, ref])
}
