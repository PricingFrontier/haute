import { useState, useRef, useCallback, useEffect } from "react"

/**
 * Hook for drag-to-resize on bottom panels.
 * Uses DOM-direct mutation during drag (no React re-renders), commits to state on mouseup.
 */
export function useDragResize(opts: {
  initialHeight: number
  minHeight: number
  maxHeight: number
}): {
  height: number
  containerRef: React.RefObject<HTMLDivElement | null>
  onDragStart: (e: React.MouseEvent) => void
  resizeToHeight: (nextHeight: number, opts?: { clampToMax?: boolean }) => void
} {
  const { initialHeight, minHeight, maxHeight } = opts
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
    (nextHeight: number, clampToMax = true) =>
      Math.max(minHeight, Math.min(clampToMax ? maxHeight : Number.POSITIVE_INFINITY, nextHeight)),
    [maxHeight, minHeight],
  )

  const resizeToHeight = useCallback(
    (nextHeight: number, resizeOpts: { clampToMax?: boolean } = {}) => {
      const finalHeight = clampHeight(nextHeight, resizeOpts.clampToMax ?? true)
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

      const onMove = (ev: MouseEvent) => {
        if (!draggingRef.current) return
        const newH = clampHeight(startH + (startY - ev.clientY))
        // DOM-direct mutation -- avoids React re-renders during drag
        if (containerRef.current) {
          containerRef.current.style.height = `${newH}px`
        }
      }

      const onUp = (ev: MouseEvent) => {
        draggingRef.current = false
        // Commit final height to React state
        const finalH = clampHeight(startH + (startY - ev.clientY))
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
