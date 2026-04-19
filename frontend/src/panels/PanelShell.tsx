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

interface PanelShellProps {
  children: ReactNode
  /** Additional opacity/transition styles (e.g. dimmed NodePanel) */
  style?: React.CSSProperties
  /**
   * Optional header title.  When provided, PanelShell renders a PanelHeader
   * above the children — `onClose` is then required.  This replaces the
   * previous pattern of manually wrapping `<PanelHeader>` inside
   * `<PanelShell>` at every caller.
   */
  title?: string | ReactNode
  /** Close handler for the rendered header.  Required when `title` is set. */
  onClose?: () => void
  /** Optional icon rendered before the header title. */
  icon?: ReactNode
  /** Optional subtitle rendered below the header title. */
  subtitle?: ReactNode
  /** Optional action buttons rendered in the header before the close button. */
  actions?: ReactNode
}

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
  actions,
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

  // When a title is passed, the shell takes ownership of rendering the
  // standard PanelHeader.  Developer ergonomics: `onClose` is required by
  // TypeScript in practice because callers always need a way to close the
  // panel — we assert at runtime to catch any stragglers early rather than
  // silently rendering a header without a working close button.
  if (title !== undefined && onClose === undefined) {
    throw new Error("PanelShell: `onClose` is required when `title` is provided")
  }

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
            onClose={onClose!}
            icon={icon}
            subtitle={subtitle}
            actions={actions}
          />
        )}
        {children}
      </div>
    </div>
  )
}
