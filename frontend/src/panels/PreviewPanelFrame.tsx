import { useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react"
import { ChevronDown, ChevronUp, ChevronsDown, ChevronsUp } from "lucide-react"

import NodeTypeIcon from "../components/NodeTypeIcon"
import { useDragResize } from "../hooks/useDragResize"
import { DEFAULT_PREVIEW_PANEL_DIMENSIONS, PREVIEW_PANEL_HEADER_HEIGHT_CLASS } from "./previewPanelLayout"

const FRAME_ICON_SIZE = 14

type PreviewPanelFrameProps = {
  nodeLabel: string
  children: ReactNode
  actions?: ReactNode
  subtitle?: ReactNode
  collapsedMeta?: ReactNode
  nodeType?: string | null
  initialHeight?: number
  minHeight?: number
  maxHeight?: number
  "data-testid"?: string
}

export default function PreviewPanelFrame({
  nodeLabel,
  children,
  actions,
  subtitle,
  collapsedMeta,
  nodeType,
  initialHeight = DEFAULT_PREVIEW_PANEL_DIMENSIONS.initialHeight,
  minHeight = DEFAULT_PREVIEW_PANEL_DIMENSIONS.minHeight,
  maxHeight = DEFAULT_PREVIEW_PANEL_DIMENSIONS.maxHeight,
  "data-testid": testId,
}: PreviewPanelFrameProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [expandedToTop, setExpandedToTop] = useState(false)
  const collapsedContainerRef = useRef<HTMLDivElement | null>(null)
  const restoreHeightRef = useRef(initialHeight)
  const { height, containerRef, onDragStart, resizeToHeight } = useDragResize({ initialHeight, minHeight, maxHeight })
  const frameIcon = <NodeTypeIcon nodeType={nodeType} size={FRAME_ICON_SIZE} />
  const topButtonTitle = expandedToTop ? "Restore preview panel height" : "Expand preview panel to top"
  const TopButtonIcon = expandedToTop ? ChevronDown : collapsed ? ChevronsUp : ChevronUp
  const CollapseButtonIcon = expandedToTop ? ChevronsDown : ChevronDown

  const availablePanelHeight = () => {
    const source = containerRef.current ?? collapsedContainerRef.current
    const parent = source?.parentElement
    const parentHeight = parent?.getBoundingClientRect().height ?? 0
    if (parentHeight > 0) return Math.floor(parentHeight)
    const sourceBottom = source?.getBoundingClientRect().bottom ?? 0
    if (sourceBottom > 0) return Math.floor(sourceBottom)
    return window.innerHeight
  }

  const handleToggleTop = () => {
    if (expandedToTop) {
      resizeToHeight(restoreHeightRef.current, { clampToMax: false })
      setExpandedToTop(false)
      setCollapsed(false)
      return
    }

    restoreHeightRef.current = height
    resizeToHeight(availablePanelHeight(), { clampToMax: false })
    setExpandedToTop(true)
    setCollapsed(false)
  }

  const handleDragStart = (event: ReactMouseEvent) => {
    setExpandedToTop(false)
    onDragStart(event)
  }

  const handleCollapse = () => {
    if (expandedToTop) {
      resizeToHeight(restoreHeightRef.current, { clampToMax: false })
      setExpandedToTop(false)
    }
    setCollapsed(true)
  }

  if (collapsed) {
    return (
      <div
        ref={collapsedContainerRef}
        className="h-8 flex items-center gap-2 px-4 shrink-0"
        style={{ borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}
        data-testid={testId ? `${testId}-collapsed` : undefined}
      >
        <span className="shrink-0" data-testid="preview-panel-node-icon">{frameIcon}</span>
        <span className="min-w-0 truncate text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
          {nodeLabel}
        </span>
        {collapsedMeta && (
          <span className="min-w-0 truncate text-xs" style={{ color: "var(--text-muted)" }}>
            {collapsedMeta}
          </span>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="ml-auto p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
          aria-label="Expand preview panel"
        >
          <ChevronUp size={14} className="shrink-0" />
        </button>
        <button
          type="button"
          onClick={handleToggleTop}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-muted)" }}
          aria-label={topButtonTitle}
        >
          <TopButtonIcon size={14} className="shrink-0" />
        </button>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      style={{ height, borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}
      className="flex flex-col shrink-0 relative"
      data-testid={testId}
    >
      <div
        onMouseDown={handleDragStart}
        className="drag-handle-hover absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10"
      />

      <div
        className={`${PREVIEW_PANEL_HEADER_HEIGHT_CLASS} flex items-center gap-2 px-4 py-1.5 shrink-0 overflow-hidden`}
        style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}
        data-testid={testId ? `${testId}-header` : "preview-panel-frame-header"}
      >
        <span className="shrink-0" data-testid="preview-panel-node-icon">{frameIcon}</span>
        <div className="min-w-0 flex items-baseline gap-2">
          <div className="text-xs font-bold truncate shrink-0 max-w-full" style={{ color: "var(--text-primary)" }}>
            {nodeLabel}
          </div>
          {subtitle && (
            <div className="min-w-0 text-[10px] tabular-nums truncate" style={{ color: "var(--text-muted)" }}>
              {subtitle}
            </div>
          )}
        </div>
        <div className="ml-auto flex min-w-0 shrink-0 items-center gap-1.5">
          {actions}
          <button
            type="button"
            onClick={handleCollapse}
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
            aria-label="Collapse preview panel"
          >
            <CollapseButtonIcon size={14} />
          </button>
          <button
            type="button"
            onClick={handleToggleTop}
            className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)]"
            style={{ color: "var(--text-muted)" }}
            aria-label={topButtonTitle}
          >
            <TopButtonIcon size={14} />
          </button>
        </div>
      </div>

      {children}
    </div>
  )
}
