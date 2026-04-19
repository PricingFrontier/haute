import { useEffect, useRef, useCallback, type ReactNode } from "react"
import useUIStore from "../stores/useUIStore"
import PanelHeader from "./PanelHeader"

const MIN_PANEL_W = 320
const LEFT_PALETTE_W = 180
const LEFT_PALETTE_COLLAPSED_W = 40

/** Available space = window width minus left palette. */
function availableSpace(): number {
  const paletteOpen = useUIStore.getState().paletteOpen
  const leftW = paletteOpen ? LEFT_PALETTE_W : LEFT_PALETTE_COLLAPSED_W
  return window.innerWidth - leftW
}

/** Default panel width: 50% of available space. */
function defaultPanelWidth(): number {
  return Math.max(MIN_PANEL_W, Math.floor(availableSpace() / 2))
}

/** Maximum panel width: 75% of available space so the graph stays usable. */
function maxPanelWidth(): number {
  return Math.max(MIN_PANEL_W, Math.floor(availableSpace() * 0.75))
}

interface PanelShellBaseProps {
  children: ReactNode
  /** Additional opacity/transition styles (e.g. dimmed NodePanel) */
  style?: React.CSSProperties
}

/**
 * Either no header at all (children render raw, suitable for panels like
 * TracePanel/NodePanel that build their own bespoke header), OR a full
 * header with both a title and an onClose handler.  The discriminated union
 * makes it impossible to pass `title` without `onClose` — TypeScript catches
 * the mistake at compile time, no runtime guard needed.
 */
type PanelShellHeaderProps =
  | {
      title?: undefined
      onClose?: undefined
      icon?: undefined
      subtitle?: undefined
    }
  | {
      title: string | ReactNode
      onClose: () => void
      icon?: ReactNode
      subtitle?: ReactNode
    }

type PanelShellProps = PanelShellBaseProps & PanelShellHeaderProps

/**
 * Shared wrapper for all right-side panels (NodePanel, UtilityPanel,
 * ImportsPanel, GitPanel, TracePanel).  Provides:
 * - Width from the UI store (shared across all panels)
 * - A visible left-edge drag handle for resizing
 * - Slide-in animation
 * - Consistent background color
 * - Optional inlined header (title/icon/subtitle/actions/close) so callers
 *   don't repeat the `<PanelShell><PanelHeader ...>` boilerplate.
 */
export default function PanelShell({
  children,
  style,
  title,
  onClose,
  icon,
  subtitle,
}: PanelShellProps) {
  const storedWidth = useUIStore((s) => s.nodePanelWidth)
  const setNodePanelWidth = useUIStore((s) => s.setNodePanelWidth)
  // 0 = sentinel: use dynamic default (50% of available space)
  const panelWidth = storedWidth > 0 ? storedWidth : defaultPanelWidth()

  const isDragging = useRef(false)
  const startX = useRef(0)
  const startW = useRef(panelWidth)
  const widthRef = useRef(panelWidth)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const maxW = maxPanelWidth()
      const delta = startX.current - e.clientX
      const newW = Math.min(maxW, Math.max(MIN_PANEL_W, startW.current + delta))
      widthRef.current = newW
      if (panelRef.current) {
        panelRef.current.style.width = `${newW}px`
      }
    }
    const onMouseUp = () => {
      if (isDragging.current) {
        isDragging.current = false
        document.body.style.cursor = ""
        document.body.style.userSelect = ""
        setNodePanelWidth(widthRef.current)
      }
    }
    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
    return () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
    }
  }, [setNodePanelWidth])

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      isDragging.current = true
      startX.current = e.clientX
      startW.current = panelWidth
      widthRef.current = panelWidth
      document.body.style.cursor = "col-resize"
      document.body.style.userSelect = "none"
    },
    [panelWidth],
  )

  return (
    <div
      ref={panelRef}
      className="h-full shrink-0 flex flex-row animate-slide-in"
      style={{ width: panelWidth, background: "var(--bg-panel)", ...style }}
    >
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="shrink-0 h-full w-1 cursor-col-resize transition-colors"
        style={{ background: "var(--chrome-border)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--accent)"
        }}
        onMouseLeave={(e) => {
          if (!isDragging.current) e.currentTarget.style.background = "var(--chrome-border)"
        }}
      />
      <div className="flex-1 min-w-0 h-full flex flex-col overflow-hidden">
        {title !== undefined && (
          <PanelHeader
            title={title}
            onClose={onClose}
            icon={icon}
            subtitle={subtitle}
          />
        )}
        {children}
      </div>
    </div>
  )
}
