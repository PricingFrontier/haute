import { useRef, useState } from "react"
import { GripVertical, Plus, Search, Trash2 } from "lucide-react"

import { withAlpha } from "../../../utils/color"
import { INPUT_STYLE } from "../_shared"
import type { SearchableList } from "./useSearchableList"

export interface SearchableItemListProps {
  list: SearchableList
  selectedIndex: number
  onSelect: (index: number) => void
  onAdd: () => void
  /** Given only while an item may be removed. */
  onRemove?: (index: number) => void
  /** Given when the order means something the user may change. */
  onMove?: (from: number, to: number) => void
  labels: {
    /** The accessible name of the list. */
    list: string
    search: string
    add: string
    remove: (name: string) => string
    /** Read after the item's name, as its state. */
    status: (healthy: boolean) => string
    /** Shown when the search or the filter matches nothing. */
    empty: string
  }
  accentColor: string
}

/**
 * A searchable, filterable, scrollable list of an editor's items — Rating
 * Step's tables, Banding's factors — with a health dot, badges and a remove
 * control on each row. Up/Down moves the selection; with `onMove`, a row can
 * be dragged onto another, or moved with Alt+Up/Down.
 */
export default function SearchableItemList({
  list,
  selectedIndex,
  onSelect,
  onAdd,
  onRemove,
  onMove,
  labels,
  accentColor,
}: SearchableItemListProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  const problemCount = list.items.filter((item) => !item.healthy).length
  const selectedVisible = list.visible.some((item) => item.index === selectedIndex)
  const movable = onMove !== undefined && list.items.length > 1

  const focusOption = (position: number) => {
    const options = listRef.current?.querySelectorAll<HTMLElement>('[data-list-option="true"]')
    window.requestAnimationFrame(() => options?.[position]?.focus())
  }
  const endDrag = () => {
    setDragIndex(null)
    setDropIndex(null)
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <div className="relative flex-1 min-w-0">
          <Search
            size={12}
            className="absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none"
            style={{ color: "var(--text-muted)" }}
          />
          <input
            type="search"
            aria-label={labels.search}
            value={list.search}
            onChange={(e) => list.setSearch(e.target.value)}
            className="w-full pl-7 pr-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
            style={INPUT_STYLE}
          />
        </div>
        <div
          className="flex items-center rounded-lg p-0.5 shrink-0"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
        >
          {(["all", "problems"] as const).map((filter) => (
            <button
              key={filter}
              type="button"
              onClick={() => list.setFilter(filter)}
              aria-pressed={list.filter === filter}
              className="px-2 py-1 rounded-md text-[10px] font-medium transition-colors"
              style={{
                background: list.filter === filter ? withAlpha(accentColor, 0.14) : "transparent",
                color: list.filter === filter ? accentColor : "var(--text-muted)",
              }}
            >
              {filter === "all" ? "All" : `Issues${problemCount > 0 ? ` ${problemCount}` : ""}`}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onAdd}
          aria-label={labels.add}
          className="accent-hover-btn p-1.5 rounded-lg shrink-0"
          style={{ color: "var(--text-muted)", border: "1px dashed var(--border)", ["--node-accent" as string]: accentColor }}
        >
          <Plus size={12} />
        </button>
      </div>

      <div
        ref={listRef}
        role="group"
        aria-label={labels.list}
        className="max-h-44 overflow-y-auto rounded-lg"
        style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
      >
        {list.visible.map((item, position) => {
          const selected = item.index === selectedIndex
          const focusable = selected || (!selectedVisible && position === 0)
          const dropTarget = dropIndex === item.index && dragIndex !== null && dragIndex !== item.index
          return (
            <div
              key={item.index}
              draggable={movable}
              onDragStart={movable ? (e) => {
                e.dataTransfer?.setData("text/plain", String(item.index))
                if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"
                setDragIndex(item.index)
              } : undefined}
              onDragOver={movable ? (e) => {
                if (dragIndex === null) return
                e.preventDefault()
                if (e.dataTransfer) e.dataTransfer.dropEffect = "move"
                if (dropIndex !== item.index) setDropIndex(item.index)
              } : undefined}
              onDrop={movable ? (e) => {
                e.preventDefault()
                if (dragIndex !== null && dragIndex !== item.index) onMove(dragIndex, item.index)
                endDrag()
              } : undefined}
              onDragEnd={movable ? endDrag : undefined}
              className="group flex items-center gap-1.5 px-1.5 py-1 text-[11px] transition-colors border-b last:border-b-0"
              style={{
                background: selected ? withAlpha(accentColor, 0.1) : "transparent",
                borderColor: "var(--border-subtle)",
                color: selected ? "var(--text-primary)" : "var(--text-secondary)",
                opacity: dragIndex === item.index ? 0.5 : 1,
                // The edge the held row lands on: below this row when moving
                // down, above it when moving up.
                boxShadow: dropTarget
                  ? `inset 0 ${dragIndex < item.index ? -2 : 2}px 0 0 ${accentColor}`
                  : undefined,
              }}
            >
              {movable && (
                <span
                  aria-hidden="true"
                  title="Drag to reorder"
                  className="shrink-0 cursor-grab active:cursor-grabbing"
                  style={{ color: "var(--text-muted)" }}
                >
                  <GripVertical size={11} />
                </span>
              )}
              <button
                type="button"
                aria-label={`${item.name} ${labels.status(item.healthy)}`}
                aria-pressed={selected}
                title={item.issues.length > 0 ? item.issues.join("; ") : "Healthy"}
                tabIndex={focusable ? 0 : -1}
                data-list-option="true"
                onClick={() => onSelect(item.index)}
                onKeyDown={(e) => {
                  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return
                  e.preventDefault()
                  const step = e.key === "ArrowDown" ? 1 : -1
                  if (e.altKey) {
                    if (!movable) return
                    const neighbour = list.visible[position + step]
                    if (!neighbour) return
                    // The editor keeps the moved item selected, as for a drag.
                    onMove(item.index, neighbour.index)
                    focusOption(position + step)
                    return
                  }
                  const nextPosition = (position + step + list.visible.length) % list.visible.length
                  const next = list.visible[nextPosition]
                  if (!next) return
                  onSelect(next.index)
                  focusOption(nextPosition)
                }}
                className="min-w-0 flex flex-1 items-center gap-2 rounded-md px-1 py-0.5 text-left focus:outline-none focus:ring-1"
                style={{ color: "inherit", background: "transparent" }}
              >
                <span
                  aria-hidden="true"
                  className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: item.healthy ? "var(--success)" : "var(--warning-strong)" }}
                />
                <span className="min-w-0 flex-1 truncate font-mono">{item.name}</span>
                {item.badges.map((badge, badgeIndex) => (
                  <span
                    key={badgeIndex}
                    className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-mono"
                    style={{
                      background: selected ? withAlpha(accentColor, 0.14) : "var(--bg-elevated)",
                      color: "var(--text-muted)",
                    }}
                  >
                    {badge}
                  </span>
                ))}
              </button>
              {onRemove && (
                <button
                  type="button"
                  aria-label={labels.remove(item.name)}
                  onClick={(e) => {
                    e.stopPropagation()
                    onRemove(item.index)
                  }}
                  className="p-0.5 rounded opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity cursor-pointer hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
                  style={{ color: "var(--text-muted)" }}
                >
                  <Trash2 size={11} />
                </button>
              )}
            </div>
          )
        })}
        {list.visible.length === 0 && (
          <div className="px-2.5 py-3 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
            {labels.empty}
          </div>
        )}
      </div>
    </div>
  )
}
