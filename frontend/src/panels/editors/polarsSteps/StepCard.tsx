import { AlertTriangle, ChevronRight, GripVertical, MoveDown, MoveUp, Trash2, type LucideIcon } from "lucide-react"
import { useId, type DragEvent, type KeyboardEvent, type ReactNode } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import { partsText, type SummaryPart } from "./summary"

export type StepBadge = { tone: "danger" | "warning"; text: string }

/**
 * One step card, laid out like the trace panel's step card: a header whose
 * disclosure button carries the chevron, the step number in muted
 * monospace, the kind's icon and its label; a fixed group of actions (move
 * up, move down, delete) that keeps its slots on every card and shows fully
 * on hover or focus; and, aligned under the label, the collapsed summary with
 * the change the step makes to the columns, then any note (a column that is
 * not in the data, what an unfinished step needs, a failed run). A card with
 * a `drag` handler can be picked up by its header (a grip marks it) and
 * dropped on another card; Alt+Up and Alt+Down on the header move it.
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

/** Width of the chevron, number and icon ahead of the label, so the summary lines up with it. */
const LEAD = "w-[46px]"
const ROW_INDENT = "pl-[68px]"

function SummaryLine({ parts }: { parts: SummaryPart[] }) {
  return (
    <span className="line-clamp-2 text-[11px] leading-[18px]" style={{ color: "var(--text-secondary)" }}>
      {parts.map((part, index) => {
        const space = index > 0 && part.kind !== "sep" ? " " : ""
        switch (part.kind) {
          case "column":
            return (
              <span key={index}>
                {space}
                <code className="rounded px-1 py-px font-mono text-[10.5px]" style={{ background: "var(--chrome-hover)", color: "var(--text-primary)" }}>
                  {part.text}
                </code>
              </span>
            )
          case "value":
            return (
              <span key={index}>
                {space}
                <code className="font-mono text-[10.5px]" style={{ color: part.tone === "string" ? "var(--syntax-string)" : "var(--syntax-literal)" }}>
                  {part.text}
                </code>
              </span>
            )
          case "code":
            return (
              <span key={index}>
                {space}
                <code className="font-mono text-[10.5px]" style={{ color: "var(--text-primary)" }}>
                  {part.text}
                </code>
              </span>
            )
          case "op":
            return <span key={index} style={{ color: "var(--text-muted)" }}>{space}{part.text}</span>
          case "missing":
            return (
              <span key={index}>
                {space}
                <span className="rounded px-1 italic" style={{ border: "1px dashed var(--border-bright)", color: "var(--text-muted)" }}>
                  {part.text}
                </span>
              </span>
            )
          case "prompt":
            return <span key={index} className="italic" style={{ color: "var(--text-muted)" }}>{space}{part.text}</span>
          default:
            return <span key={index}>{space}{part.text}</span>
        }
      })}
    </span>
  )
}

export default function StepCard({
  label,
  icon: Icon,
  number,
  summary,
  change = null,
  notes,
  open,
  onToggle,
  onMoveUp,
  onMoveDown,
  movable = false,
  onDelete,
  badge,
  children,
  disclosureRef,
  onEscape,
  drag,
  highlighted = false,
  onHoverChange,
}: {
  label: string
  icon?: LucideIcon
  /** Display number, or null for the fixed start card. */
  number: number | null
  /** The collapsed summary: parts, or a plain sentence (an invalid card's problem). */
  summary: SummaryPart[] | string
  /** The change the step makes to the columns (`+name`, `→ 3 columns`). */
  change?: string | null
  /** Notes under the summary (unknown columns, what the step still needs). */
  notes?: ReactNode
  open: boolean
  onToggle: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  /** Keep the move slots (disabled where a move does not apply). */
  movable?: boolean
  onDelete?: () => void
  badge?: StepBadge | null
  children?: ReactNode
  disclosureRef?: (element: HTMLButtonElement | null) => void
  onEscape?: () => void
  drag?: StepDrag
  /** Its generated code is being pointed at. */
  highlighted?: boolean
  onHoverChange?: (hovering: boolean) => void
}) {
  const id = useId()
  const bodyId = `${id}-body`
  const accent = NODE_GROUP_COLORS.transform
  const borderColour = badge
    ? `var(--${badge.tone})`
    : open
      ? "var(--accent-ring)"
      : drag?.target || highlighted
        ? "var(--border-bright)"
        : "var(--border)"
  // The number is visual; the accessible name keeps the step number.
  const title = number === null ? label : `Step ${number}: ${label}`
  const summaryText = typeof summary === "string" ? summary : partsText(summary)

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && open && onEscape) {
      event.stopPropagation()
      onEscape()
    }
  }
  const onHeaderKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!event.altKey) return
    if (event.key === "ArrowUp" && onMoveUp) {
      event.preventDefault()
      onMoveUp()
    } else if (event.key === "ArrowDown" && onMoveDown) {
      event.preventDefault()
      onMoveDown()
    }
  }

  const actionClass = "focus-ring p-1 rounded disabled:opacity-30 disabled:cursor-default"
  return (
    <div
      data-testid="polars-step-card"
      data-state={open ? "open" : "closed"}
      data-drop-target={drag?.target ? "true" : undefined}
      data-highlighted={highlighted ? "true" : undefined}
      className="group/card relative rounded-lg transition-colors"
      style={{
        background: "var(--bg-elevated)",
        border: `1px solid ${borderColour}`,
        opacity: drag?.dragging ? 0.5 : 1,
        boxShadow: drag?.target ? `0 -2px 0 0 ${accent}` : highlighted ? "0 0 0 1px var(--accent-soft-strong)" : undefined,
      }}
      onKeyDown={onKeyDown}
      onDragOver={drag?.onOver}
      onDrop={drag?.onDrop}
      onMouseEnter={onHoverChange ? () => onHoverChange(true) : undefined}
      onMouseLeave={onHoverChange ? () => onHoverChange(false) : undefined}
      onFocus={onHoverChange ? () => onHoverChange(true) : undefined}
      onBlur={onHoverChange ? (event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onHoverChange(false)
      } : undefined}
    >
      <div
        className={`step-card-header flex items-start gap-1 rounded-lg pl-3 pr-1.5 py-1 ${drag ? "cursor-grab active:cursor-grabbing" : ""}`}
        draggable={drag !== undefined}
        onDragStart={drag?.onStart}
        onDragEnd={drag?.onEnd}
        title={drag ? "Drag to reorder" : undefined}
      >
        {drag && (
          <GripVertical
            size={12}
            aria-hidden="true"
            className="absolute left-0.5 top-[11px] opacity-0 transition-opacity group-hover/card:opacity-60"
            style={{ color: "var(--text-muted)" }}
          />
        )}
        <button
          type="button"
          ref={disclosureRef}
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={title}
          aria-keyshortcuts={onMoveUp || onMoveDown ? "Alt+ArrowUp Alt+ArrowDown" : undefined}
          onClick={onToggle}
          onKeyDown={onHeaderKeyDown}
          className="focus-ring flex-1 min-w-0 flex items-start gap-1.5 text-left rounded-md px-1 py-1"
        >
          <span className={`${LEAD} h-[18px] shrink-0 flex items-center gap-1 self-start`} aria-hidden="true">
            <ChevronRight
              size={13}
              strokeWidth={2}
              className="shrink-0 transition-transform"
              style={{ color: "var(--text-muted)", transform: open ? "rotate(90deg)" : undefined }}
            />
            <span data-testid="step-number" className="w-3.5 shrink-0 text-right font-mono text-[11px] tabular-nums" style={{ color: "var(--text-muted)" }}>
              {number ?? ""}
            </span>
            {Icon && <Icon size={13} strokeWidth={2} className="shrink-0" style={{ color: accent }} />}
          </span>
          {/* The label and the summary share one column, so they line up by construction. */}
          <span className="flex-1 min-w-0 grid gap-0.5">
            <span data-testid="step-label" className="truncate text-xs font-semibold leading-[18px]" style={{ color: "var(--text-primary)" }}>
              {label}
            </span>
            {!open && (
              <span data-testid="step-summary" className="flex items-start gap-2" title={summaryText}>
                <span className="flex-1 min-w-0">
                  {typeof summary === "string" ? (
                    <span className="line-clamp-2 text-[11px] leading-[18px]" style={{ color: "var(--text-muted)" }}>{summary}</span>
                  ) : (
                    <SummaryLine parts={summary} />
                  )}
                </span>
                {change && (
                  <span className="shrink-0 font-mono text-[10px] leading-[18px]" style={{ color: "var(--text-muted)" }}>
                    {change}
                  </span>
                )}
              </span>
            )}
          </span>
        </button>
        {(movable || onDelete) && (
          <span className="mt-0.5 flex shrink-0 items-center gap-0.5 opacity-50 transition-opacity group-hover/card:opacity-100 group-focus-within/card:opacity-100" style={{ color: "var(--text-secondary)" }}>
            {movable && (
              <>
                <button type="button" onClick={onMoveUp} disabled={!onMoveUp} aria-label={`Move ${title} up`} className={actionClass}>
                  <MoveUp size={13} aria-hidden="true" />
                </button>
                <button type="button" onClick={onMoveDown} disabled={!onMoveDown} aria-label={`Move ${title} down`} className={actionClass}>
                  <MoveDown size={13} aria-hidden="true" />
                </button>
              </>
            )}
            {onDelete && (
              <button type="button" onClick={onDelete} aria-label={`Delete ${title}`} className={`icon-danger-btn ${actionClass}`}>
                <Trash2 size={13} aria-hidden="true" />
              </button>
            )}
          </span>
        )}
      </div>
      {(badge || notes) && (
        <div className={`${ROW_INDENT} pr-3 pb-2 grid gap-1`}>
          {notes}
          {badge && (
            <div role="status" className="flex items-start gap-1 text-[11px] leading-snug" style={{ color: `var(--${badge.tone})` }}>
              <AlertTriangle size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>{badge.text}</span>
            </div>
          )}
        </div>
      )}
      <div id={bodyId} hidden={!open} className="px-3 pb-3 pt-0.5">
        {open && <div className="grid gap-2.5">{children}</div>}
      </div>
    </div>
  )
}
