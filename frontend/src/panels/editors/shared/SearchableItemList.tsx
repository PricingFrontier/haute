import { useRef, useState, type ReactNode } from "react"
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
  /** Given when an item's name may be typed over in its row. */
  rename?: ItemRename
  /**
   * What a row shows before its name, such as Banding's input column and an
   * arrow to the name: controls of its own, beside the row's button.
   */
  rowLead?: (index: number, selected: boolean) => ReactNode
  labels: {
    /** The accessible name of the list. */
    list: string
    search: string
    /** The text of the Add button under the list. */
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
 * Renaming an item in its row: the name the editor stores (its output column),
 * which the box starts from, never a stand-in a row shows while there is none
 * ("Table 2", or a Banding factor's input column); the editor's own rule for a
 * name, the one its field for the name applies; and the commit.
 */
export interface ItemRename {
  value: (index: number) => string
  /** Why a name cannot be taken, or null. */
  validate?: (index: number, name: string) => string | null
  commit: (index: number, name: string) => void
}

/** A row's name and badges, laid out alike whether its name shows or is being typed. */
const ROW_BODY = "min-w-0 flex flex-1 items-center gap-2 rounded-md px-1 py-0.5"
/** The name's box, and the rename box's, so the one opens exactly where the other was. */
const NAME_BOX = "min-w-0 flex-1 rounded border px-1.5 py-px font-mono"

function healthDot(healthy: boolean) {
  return (
    <span
      aria-hidden="true"
      className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
      style={{ background: healthy ? "var(--success)" : "var(--warning-strong)" }}
    />
  )
}

/**
 * A searchable, filterable, scrollable list of an editor's items — Rating
 * Step's tables, Banding's factors — with a health dot, badges and a remove
 * control on each row. Up/Down moves the selection; with `onMove`, a row can
 * be dragged onto another, or moved with Alt+Up/Down; with `rename`, a
 * double-click on a row, or F2, opens its name in a box to type over.
 */
export default function SearchableItemList({
  list,
  selectedIndex,
  onSelect,
  onAdd,
  onRemove,
  onMove,
  rename,
  rowLead,
  labels,
  accentColor,
}: SearchableItemListProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  // The item whose name is in a box to be typed over, if any.
  const [renaming, setRenaming] = useState<number | null>(null)
  const problemCount = list.items.filter((item) => !item.healthy).length
  const selectedVisible = list.visible.some((item) => item.index === selectedIndex)
  // A list that can be reordered shows its grip even with one row, so a node
  // with one item looks like a node with several; dropping a row on itself
  // moves nothing.
  const movable = onMove !== undefined

  // Looked up once the rows are drawn again: a row being renamed has no option
  // until its box goes.
  const focusOption = (position: number) => {
    window.requestAnimationFrame(() => {
      listRef.current?.querySelectorAll<HTMLElement>('[data-list-option="true"]')[position]?.focus()
    })
  }
  const endDrag = () => {
    setDragIndex(null)
    setDropIndex(null)
  }
  const itemBadges = (badges: readonly string[], selected: boolean) =>
    badges.map((badge, badgeIndex) => (
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
    ))

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
              // Not while its name is being typed: a drag in the box selects text.
              draggable={movable && renaming !== item.index}
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
              {/* The dot leads the row, before any lead, so every row's health lines up. */}
              <span className="flex shrink-0 pl-1">{healthDot(item.healthy)}</span>
              {rowLead && (
                // It shares the row with the name and badges, 5 to 8, so neither
                // is squeezed out when the panel is narrow.
                <div className="flex min-w-0 flex-[0.625_1_0%] items-center gap-1">
                  {rowLead(item.index, selected)}
                </div>
              )}
              {rename && renaming === item.index ? (
                // The box takes the name's place, the badges staying put.
                <div className={ROW_BODY}>
                  <RenameField
                    initial={rename.value(item.index)}
                    label={`Rename ${item.name}`}
                    validate={(name) => rename.validate?.(item.index, name) ?? null}
                    onDone={(name, byKey) => {
                      setRenaming(null)
                      if (name !== null) rename.commit(item.index, name)
                      // Enter and Escape give the row its focus back; a click
                      // elsewhere keeps the focus where it went.
                      if (byKey) focusOption(position)
                    }}
                  />
                  {itemBadges(item.badges, selected)}
                </div>
              ) : (
              <button
                type="button"
                aria-label={`${item.name} ${labels.status(item.healthy)}`}
                aria-pressed={selected}
                aria-keyshortcuts={rename ? "F2" : undefined}
                title={item.issues.length > 0 ? item.issues.join("; ") : "Healthy"}
                tabIndex={focusable ? 0 : -1}
                data-list-option="true"
                onClick={(e) => {
                  // The selected row's name is a box: a click in it renames.
                  if (rename && selected && (e.target as HTMLElement).closest("[data-item-name]")) {
                    setRenaming(item.index)
                    return
                  }
                  onSelect(item.index)
                }}
                onDoubleClick={rename ? () => setRenaming(item.index) : undefined}
                onKeyDown={(e) => {
                  if (e.key === "F2" && rename) {
                    e.preventDefault()
                    setRenaming(item.index)
                    return
                  }
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
                className={`${ROW_BODY} text-left focus:outline-none focus:ring-1`}
                style={{ color: "inherit", background: "transparent" }}
              >
                {/* A renamable name sits in a box like the one it opens into:
                    the selected row's always, another row's while hovered. */}
                <span
                  data-item-name
                  className={`${NAME_BOX} truncate${
                    !rename ? " border-transparent"
                      : selected ? " cursor-text"
                        : " cursor-text border-transparent group-hover:border-[var(--border)] group-hover:bg-[var(--bg-input)]"
                  }`}
                  style={rename && selected ? { background: "var(--bg-input)", borderColor: "var(--border)" } : undefined}
                >
                  {item.name}
                </span>
                {itemBadges(item.badges, selected)}
              </button>
              )}
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
              {/* The bin's place is kept while the last item cannot be removed,
                  so the badges sit where they do with several items. */}
              {!onRemove && <span aria-hidden="true" className="shrink-0" style={{ width: 15 }} />}
            </div>
          )
        })}
        {list.visible.length === 0 && (
          <div className="px-2.5 py-3 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
            {labels.empty}
          </div>
        )}
      </div>

      {/* Under the list and as wide as it, drawn like the Transform tab's Add step. */}
      <button
        type="button"
        onClick={onAdd}
        className="add-row-btn focus-ring flex w-full items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium"
        style={{ color: "var(--accent)", background: "var(--accent-soft-subtle)", border: "1px dashed var(--accent-ring)" }}
      >
        <Plus size={13} aria-hidden="true" />
        {labels.add}
      </button>
    </div>
  )
}

/**
 * A row's name in a box, its text selected to type over. Enter, or leaving
 * the box, keeps a new name the editor's rule allows; Escape keeps the old
 * one, as does a blank name or the same one. A name the rule refuses says why
 * under the box, and Enter keeps the box open on it; leaving drops it.
 */
function RenameField({
  initial,
  label,
  validate,
  onDone,
}: {
  initial: string
  label: string
  validate: (name: string) => string | null
  /** The new name, or null for none; `byKey` when Enter or Escape ended it. */
  onDone: (name: string | null, byKey: boolean) => void
}) {
  const [draft, setDraft] = useState(initial)
  // Enter or Escape ends the box, and its removal may blur it too: only the first counts.
  const done = useRef(false)
  const name = draft.trim()
  const changed = name !== "" && name !== initial.trim()
  const error = changed ? validate(name) : null
  const finish = (keep: boolean, byKey: boolean) => {
    if (done.current) return
    done.current = true
    onDone(keep && changed && error === null ? name : null, byKey)
  }
  return (
    <div className="min-w-0 flex-1">
      <input
        autoFocus
        type="text"
        aria-label={label}
        aria-invalid={error !== null ? true : undefined}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={(e) => e.currentTarget.select()}
        onBlur={() => finish(true, false)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            if (error === null) finish(true, true)
          } else if (e.key === "Escape") {
            // The box's Escape, not the pipeline's (which closes the panel).
            e.preventDefault()
            e.stopPropagation()
            finish(false, true)
          }
        }}
        className={`${NAME_BOX} w-full border-[var(--border)] focus:border-[var(--accent)] focus:outline-none`}
        style={{
          background: "var(--bg-input)",
          color: "var(--text-primary)",
          borderColor: error !== null ? "var(--danger-border-strong)" : undefined,
        }}
      />
      {error !== null && (
        <div className="mt-0.5 px-1 text-[10px] leading-snug" style={{ color: "var(--danger-text)" }}>
          {error}
        </div>
      )}
    </div>
  )
}
