import { AlertTriangle, ChevronDown, ChevronUp, MoveDown, MoveUp, Trash2 } from "lucide-react"
import { useId, type DragEvent, type KeyboardEvent, type ReactNode } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"

export type StepBadge = { tone: "danger" | "warning"; text: string }

/**
 * One step card: a two-row header (number badge, kind label and actions; then
 * the summary while the card is collapsed) whose title is a native disclosure button, and the form body
 * when open. Badges report validation or execution problems without moving
 * the user. A card with a `drag` handler can be picked up by its header
 * (the cursor becomes a hand) and dropped on another card to reorder.
 */
export type StepDrag = {
  onStart: (event: DragEvent<HTMLElement>) => void
  onOver: (event: DragEvent<HTMLElement>) => void
  onDrop: (event: DragEvent<HTMLElement>) => void
  onEnd: () => void
  /** Another card is being held over this one. */
  target: boolean
  /** This card is the one being dragged. */
  dragging: boolean
}

export default function StepCard({
  label,
  number,
  summary,
  open,
  onToggle,
  onMoveUp,
  onMoveDown,
  onDelete,
  badge,
  children,
  disclosureRef,
  onEscape,
  drag,
}: {
  label: string
  /** Display number, or null for the fixed start card. */
  number: number | null
  summary: string
  open: boolean
  onToggle: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  onDelete?: () => void
  badge?: StepBadge | null
  children?: ReactNode
  disclosureRef?: (element: HTMLButtonElement | null) => void
  onEscape?: () => void
  drag?: StepDrag
}) {
  const id = useId()
  const bodyId = `${id}-body`
  const accent = NODE_GROUP_COLORS.transform
  const borderColour = badge ? `var(--${badge.tone})` : open || drag?.target ? accent : "var(--border)"
  // The number badge is visual; the accessible name keeps the step number.
  const title = number === null ? label : `Step ${number}: ${label}`

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && open && onEscape) {
      event.stopPropagation()
      onEscape()
    }
  }

  return (
    <div
      data-testid="polars-step-card"
      data-state={open ? "open" : "closed"}
      data-drop-target={drag?.target ? "true" : undefined}
      className="rounded-lg transition-colors"
      style={{
        background: "var(--bg-input)",
        border: `1px solid ${borderColour}`,
        opacity: drag?.dragging ? 0.5 : 1,
        boxShadow: drag?.target ? `0 -2px 0 0 ${accent}` : undefined,
      }}
      onKeyDown={onKeyDown}
      onDragOver={drag?.onOver}
      onDrop={drag?.onDrop}
    >
      <div
        className={`flex items-center gap-1.5 px-2 py-1.5 ${drag ? "cursor-grab active:cursor-grabbing" : ""}`}
        draggable={drag !== undefined}
        onDragStart={drag?.onStart}
        onDragEnd={drag?.onEnd}
        title={drag ? "Drag to reorder" : undefined}
      >
        <button
          type="button"
          ref={disclosureRef}
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={title}
          onClick={onToggle}
          className="focus-ring flex-1 min-w-0 flex items-center gap-2 text-left rounded-md px-1 py-0.5"
        >
          {number !== null && (
            <span
              className="shrink-0 w-5 h-5 rounded-full text-[10px] font-bold flex items-center justify-center"
              style={{ background: accent, color: "var(--text-on-accent)" }}
              aria-hidden="true"
            >
              {number}
            </span>
          )}
          <span className="text-xs font-semibold truncate" style={{ color: open ? accent : "var(--text-primary)" }}>
            {label}
          </span>
          <span className="ml-auto shrink-0" style={{ color: "var(--text-secondary)" }} aria-hidden="true">
            {open ? <ChevronUp size={17} strokeWidth={2.25} /> : <ChevronDown size={17} strokeWidth={2.25} />}
          </span>
        </button>
        {(onMoveUp || onMoveDown || onDelete) && (
          <span className="flex items-center gap-0.5 shrink-0">
            {onMoveUp && (
              <button type="button" onClick={onMoveUp} aria-label={`Move ${title} up`} className="focus-ring p-1 rounded" style={{ color: "var(--text-secondary)" }}>
                <MoveUp size={14} aria-hidden="true" />
              </button>
            )}
            {onMoveDown && (
              <button type="button" onClick={onMoveDown} aria-label={`Move ${title} down`} className="focus-ring p-1 rounded" style={{ color: "var(--text-secondary)" }}>
                <MoveDown size={14} aria-hidden="true" />
              </button>
            )}
            {onDelete && (
              <button type="button" onClick={onDelete} aria-label={`Delete ${title}`} className="icon-danger-btn focus-ring p-1 rounded" style={{ color: "var(--text-secondary)" }}>
                <Trash2 size={15} aria-hidden="true" />
              </button>
            )}
          </span>
        )}
      </div>
      {(!open || badge) && (
      <div className="px-3 pb-2 -mt-0.5">
        {!open && (
          <div className="text-[11px] leading-snug line-clamp-2" style={{ color: "var(--text-muted)" }} title={summary}>
            {summary}
          </div>
        )}
        {badge && (
          <div
            role="status"
            className="mt-1 flex items-start gap-1 text-[11px] leading-snug"
            style={{ color: `var(--${badge.tone})` }}
          >
            <AlertTriangle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>{badge.text}</span>
          </div>
        )}
      </div>
      )}
      <div id={bodyId} hidden={!open} className="px-3 pb-3">
        {open && <div className="grid gap-2">{children}</div>}
      </div>
    </div>
  )
}
