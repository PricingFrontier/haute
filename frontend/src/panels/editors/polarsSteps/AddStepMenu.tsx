import { Plus, Search, X } from "lucide-react"
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import { STEP_CATALOGUE, type StepKindInfo } from "./catalogue"
import { STEP_ICONS } from "./stepIcons"
import type { StepKind } from "./types"

type AddableKind = Exclude<StepKind, "source">

/** Chooser sections, in reading order. */
const GROUPS: Array<{ title: string; kinds: AddableKind[] }> = [
  { title: "Rows", kinds: ["filter", "sort", "unique", "limit"] },
  { title: "Columns", kinds: ["with_column", "select", "drop", "rename", "cast", "fill_null"] },
  { title: "Combine", kinds: ["join", "concat", "group_by", "pivot", "unpivot"] },
  { title: "Values", kinds: ["variable"] },
  { title: "Code", kinds: ["free_code"] },
]

/** Whether a kind matches the search: its label, what it does, or its Polars call. */
function matchesSearch(info: StepKindInfo, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [info.label, info.description, info.method ?? ""].some((text) => text.toLowerCase().includes(q))
}

/**
 * The `Add step` control that sits under the last step: a full-width button
 * that opens, in place, a chooser of every step kind grouped as rows,
 * columns, combine, values and code. A search box at the top takes focus and
 * filters the kinds by label, description or Polars call; Enter adds the
 * first match. Each kind shows its icon and label; the focused or hovered
 * kind's description and Polars call show at the foot. Arrow keys move
 * between kinds, Escape closes and returns focus to the button, and choosing
 * a kind hands it to the editor (which moves focus into the new card).
 */
export default function AddStepMenu({
  onAdd,
  disabled = false,
  withhold,
  buttonRef,
}: {
  onAdd: (kind: AddableKind) => void
  disabled?: boolean
  /** Kinds the surface cannot use (join and concat while no input name is eligible). */
  withhold?: ReadonlySet<AddableKind>
  buttonRef?: (element: HTMLButtonElement | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [described, setDescribed] = useState<AddableKind | null>(null)
  const menuId = useId()
  const button = useRef<HTMLButtonElement | null>(null)
  const search = useRef<HTMLInputElement | null>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const wasOpen = useRef(false)
  // Closing by choosing a kind leaves focus to the new card, not the button.
  const chose = useRef(false)
  const sections = GROUPS.map((group) => ({
    title: group.title,
    items: group.kinds
      .filter((kind) => !withhold?.has(kind))
      .map((kind) => STEP_CATALOGUE.find((info) => info.kind === kind))
      .filter((info): info is StepKindInfo => info !== undefined && matchesSearch(info, query)),
  })).filter((section) => section.items.length > 0)
  const items: StepKindInfo[] = sections.flatMap((section) => section.items)
  const footer = STEP_CATALOGUE.find((info) => info.kind === described) ?? (query.trim() ? items[0] : undefined)

  useEffect(() => {
    if (open) search.current?.focus()
    else if (wasOpen.current && !chose.current) button.current?.focus()
    wasOpen.current = open
    chose.current = false
  }, [open])

  const close = () => {
    setOpen(false)
    setQuery("")
    setDescribed(null)
  }
  const choose = (kind: AddableKind) => {
    chose.current = true
    close()
    onAdd(kind)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return
    const focused = itemRefs.current.findIndex((el) => el === document.activeElement)
    const inSearch = document.activeElement === search.current
    if (event.key === "Escape") {
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === "Enter" && inSearch) {
      event.preventDefault()
      if (items[0]) choose(items[0].kind)
    } else if (event.key === "ArrowDown" || (event.key === "ArrowRight" && !inSearch)) {
      event.preventDefault()
      itemRefs.current[inSearch ? 0 : (focused + 1) % items.length]?.focus()
    } else if (event.key === "ArrowUp" || (event.key === "ArrowLeft" && !inSearch)) {
      event.preventDefault()
      // Up from the search box wraps to the last kind; up from the first kind returns to the search box.
      if (inSearch) itemRefs.current[items.length - 1]?.focus()
      else if (focused <= 0) search.current?.focus()
      else itemRefs.current[focused - 1]?.focus()
    }
  }

  return (
    <div className="shrink-0" onKeyDown={onKeyDown}>
      {!open ? (
        <button
          type="button"
          ref={(el) => {
            button.current = el
            buttonRef?.(el)
          }}
          disabled={disabled}
          aria-haspopup="menu"
          aria-expanded={false}
          aria-controls={menuId}
          onClick={() => setOpen(true)}
          className="add-row-btn focus-ring flex w-full items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ color: "var(--accent)", background: "var(--accent-soft-subtle)", border: "1px dashed var(--accent-ring)" }}
        >
          <Plus size={13} aria-hidden="true" />
          Add step
        </button>
      ) : (
        <div
          id={menuId}
          role="menu"
          aria-label="Add step"
          className="grid gap-2.5 rounded-lg p-2.5"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
        >
          <div className="flex items-center gap-2">
            <div className="relative flex-1 min-w-0">
              <Search size={12} aria-hidden="true" className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
              <input
                ref={search}
                type="search"
                aria-label="Search step kinds"
                placeholder="Search, e.g. join or group_by"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value)
                  setDescribed(null)
                }}
                className="focus-ring w-full rounded-md py-1.5 pl-7 pr-2 text-xs"
                style={{ background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
              />
            </div>
            <button type="button" onClick={close} aria-label="Close" className="hover-chrome focus-ring rounded p-1" style={{ color: "var(--text-secondary)" }}>
              <X size={12} aria-hidden="true" />
            </button>
          </div>
          {sections.length === 0 && (
            <p className="m-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
              No step kind matches “{query.trim()}”.
            </p>
          )}
          {sections.map((section) => (
            <div key={section.title} role="group" aria-label={section.title} className="grid gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
                  {section.title}
                </span>
                <span aria-hidden="true" className="h-px flex-1" style={{ background: "var(--border-subtle)" }} />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {section.items.map((info) => {
                  const index = items.indexOf(info)
                  const Icon = STEP_ICONS[info.kind]
                  return (
                    <button
                      key={info.kind}
                      type="button"
                      role="menuitem"
                      title={info.description}
                      ref={(el) => {
                        itemRefs.current[index] = el
                      }}
                      onClick={() => choose(info.kind)}
                      onMouseEnter={() => setDescribed(info.kind)}
                      onFocus={() => setDescribed(info.kind)}
                      className="add-row-btn focus-ring inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                      style={{ color: "var(--text-primary)", background: "var(--bg-input)", border: "1px solid var(--border)" }}
                    >
                      <Icon size={12} aria-hidden="true" style={{ color: NODE_GROUP_COLORS.transform }} />
                      {info.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          <div data-testid="add-step-description" className="min-h-[18px] border-t pt-2 text-[11px] leading-snug" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
            {footer ? (
              <>
                <span>{footer.description}</span>
                {footer.method && (
                  <code className="ml-1.5 font-mono text-[10.5px]" style={{ color: "var(--text-muted)" }}>
                    {footer.method}
                  </code>
                )}
              </>
            ) : (
              <span style={{ color: "var(--text-muted)" }}>Point at a kind to see what it does.</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
