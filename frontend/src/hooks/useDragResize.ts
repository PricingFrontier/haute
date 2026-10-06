import { useState, useRef, useCallback, useEffect } from "react"

/**
 * Height a bottom panel may occupy in its flex-column parent: the column height minus siblings
 * that cannot shrink (banners). A flex-growing sibling such as the canvas yields all of its height.
 */
function availablePanelHeight(panel: HTMLElement | null): number {
  let column = panel?.parentElement
  while (column && getComputedStyle(column).display === "contents") column = column.parentElement
  const columnHeight = column?.getBoundingClientRect().height ?? 0
  if (panel && column && columnHeight > 0) {
    const reserved = Array.from(column.children).reduce((sum, sibling) => (
      sibling === panel || Number.parseFloat(getComputedStyle(sibling).flexGrow) > 0
        ? sum
        : sum + sibling.getBoundingClientRect().height
    ), 0)
    return Math.floor(columnHeight - reserved)
  }
  const panelBottom = panel?.getBoundingClientRect().bottom ?? 0
  if (panelBottom > 0) return Math.floor(panelBottom)
  return window.innerHeight
}

/**
 * Hook for drag-to-resize on bottom panels docked in a flex column.
 * Uses DOM-direct mutation during drag (no React re-renders), commits to state on mouseup.
 * Dragging and `resizeToHeight` share one ceiling, the space the column can give the panel, so a
 * drag reaches the same top edge as a programmatic expand.
 */
export function useDragResize(opts: {
  initialHeight: number
  minHeight: number
}): {
  height: number
  containerRef: React.RefObject<HTMLDivElement | null>
  onDragStart: (e: React.MouseEvent) => void
  resizeToHeight: (nextHeight: number) => void
} {
  const { initialHeight, minHeight } = opts
  const [height, setHeight] = useState(initialHeight)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef(false)

  // Keep a ref to height so the mousemove closure always reads the latest start value
  // without needing height in the useCallback dependency array.
  const heightRef = useRef(height)
  useEffect(() => {
    heightRef.current = height
  }, [height])

  const clampHeight = useCallback(
    (nextHeight: number, maxHeight: number) => Math.max(minHeight, Math.min(maxHeight, nextHeight)),
    [minHeight],
  )

  const resizeToHeight = useCallback(
    (nextHeight: number) => {
      const finalHeight = clampHeight(nextHeight, availablePanelHeight(containerRef.current))
      heightRef.current = finalHeight
      if (containerRef.current) {
        containerRef.current.style.height = `${finalHeight}px`
      }
      setHeight(finalHeight)
    },
    [clampHeight],
  )

  // Clean up any lingering listeners on unmount
  const cleanupRef = useRef<(() => void) | null>(null)
  useEffect(() => {
    return () => {
      cleanupRef.current?.()
    }
  }, [])

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      draggingRef.current = true
      const startY = e.clientY
      const startH = heightRef.current
      // The column does not change size during a drag, so measure its ceiling once.
      const maxH = availablePanelHeight(containerRef.current)

      const onMove = (ev: MouseEvent) => {
        if (!draggingRef.current) return
        const newH = clampHeight(startH + (startY - ev.clientY), maxH)
        // DOM-direct mutation -- avoids React re-renders during drag
        if (containerRef.current) {
          containerRef.current.style.height = `${newH}px`
        }
      }

      const onUp = (ev: MouseEvent) => {
        draggingRef.current = false
        // Commit final height to React state
        const finalH = clampHeight(startH + (startY - ev.clientY), maxH)
        heightRef.current = finalH
        setHeight(finalH)
        document.removeEventListener("mousemove", onMove)
        document.removeEventListener("mouseup", onUp)
        cleanupRef.current = null
      }

      document.addEventListener("mousemove", onMove)
      document.addEventListener("mouseup", onUp)

      cleanupRef.current = () => {
        draggingRef.current = false
        document.removeEventListener("mousemove", onMove)
        document.removeEventListener("mouseup", onUp)
      }
    },
    [clampHeight],
  )

  return { height, containerRef, onDragStart, resizeToHeight }
}
