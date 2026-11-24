import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { createPortal } from "react-dom"
import { ChevronDown, Plus } from "lucide-react"

/** A column offered: its name, its type and, where it has one, a label shown after its name and searched as it is. */
export type ColumnOption = { name: string; dtype: string; label?: string }
type Props = {
  label: string
  value: string
  columns: readonly ColumnOption[]
  onChange: (value: string) => void
  optional?: boolean
  placeholder?: string
  /**
   * A row's size, for a list such as Banding's factors: the trigger is a small
   * box, an unavailable value is marked on it rather than said under it, and
   * the menu opens over the page, so a list that clips its rows cannot cut it.
   */
  compact?: boolean
  /**
   * How a compact trigger is drawn: a list row's small box (the default), a
   * form field's, a dashed add button reading its placeholder, or a quiet
   * "+" text action reading it, as `QuietAction` draws one, the two for
   * choosing a column to add (they show no value).
   */
  trigger?: "row" | "field" | "add" | "quiet"
  /** The trigger's place in the tab order, for a list that keeps one row's controls in it. */
  tabIndex?: number
  /** Called as the trigger takes the focus, for a list that selects the row first. */
  onFocus?: () => void
}

/** Room a compact menu keeps from the window's edge. */
const EDGE = 8
/** The space between a compact menu and its trigger. */
const GAP = 4
/** A compact menu's narrowest, so a narrow row's names stay readable. */
const COMPACT_MENU_MIN_WIDTH = 240
/** The dashed add button's look, the `add` trigger's, for a plain add button to match. */
export const ADD_BUTTON_CLASS = "add-row-btn focus-ring flex w-full min-w-0 items-center justify-center gap-1 rounded-lg px-1 py-1.5 text-xs font-medium"
export const ADD_BUTTON_STYLE = { color: "var(--accent)", background: "var(--accent-soft-subtle)", border: "1px dashed var(--accent-ring)" } as const

/** A column-only searchable combobox. Saved unavailable values remain visible,
 * but are deliberately never offered as a new selectable option. */
export function ColumnSelector({
  label,
  value,
  columns,
  onChange,
  optional = false,
  placeholder,
  compact = false,
  trigger = "row",
  tabIndex,
  onFocus,
}: Props) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [place, setPlace] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const optionRefs = useRef<Array<HTMLLIElement | null>>([])
  const animationFrame = useRef<number | null>(null)
  const known = columns.some((column) => column.name === value)
  // A saved value is only flagged once the columns have arrived.
  const unavailable = value !== "" && columns.length > 0 && !known
  const options = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = columns.filter(
      (column) =>
        column.name.toLowerCase().includes(needle) ||
        column.dtype.toLowerCase().includes(needle) ||
        (column.label ?? "").toLowerCase().includes(needle),
    )
    return optional && (needle === "" || "none".includes(needle))
      ? [{ name: "", dtype: "" }, ...matching]
      : matching
  }, [columns, optional, query])
  const optionId = (index: number) => `${id}-option-${index}`
  const display =
    value === ""
      ? (placeholder ?? (optional ? "None" : "Select column…"))
      : value
  const dtype = columns.find((column) => column.name === value)?.dtype

  useEffect(
    () => () => {
      if (animationFrame.current !== null)
        cancelAnimationFrame(animationFrame.current)
    },
    [],
  )

  const openMenu = () => {
    if (animationFrame.current !== null)
      cancelAnimationFrame(animationFrame.current)
    setQuery("")
    setActiveIndex(0)
    setOpen(true)
    animationFrame.current = requestAnimationFrame(() =>
      searchRef.current?.focus(),
    )
  }
  const closeMenu = (returnFocus = false) => {
    if (animationFrame.current !== null)
      cancelAnimationFrame(animationFrame.current)
    setOpen(false)
    setPlace(null)
    if (returnFocus) requestAnimationFrame(() => triggerRef.current?.focus())
  }
  const select = (next: string) => {
    onChange(next)
    closeMenu(true)
  }
  const move = (direction: number) => {
    if (options.length === 0) return
    const next = (activeIndex + direction + options.length) % options.length
    setActiveIndex(next)
    requestAnimationFrame(() =>
      optionRefs.current[next]?.scrollIntoView({ block: "nearest" }),
    )
  }

  // A compact menu sits under its trigger, over the page, kept inside the
  // window: over the trigger instead when it does not fit below and there is
  // more room above, its foot held there as a search shortens it. It is
  // measured while still hidden, before it is placed.
  useLayoutEffect(() => {
    if (!compact || !open || triggerRef.current === null) return
    const trigger = triggerRef.current.getBoundingClientRect()
    const width = Math.max(trigger.width, COMPACT_MENU_MIN_WIDTH)
    const left = Math.max(EDGE, Math.min(trigger.left, window.innerWidth - EDGE - width))
    const height = menuRef.current?.offsetHeight ?? 0
    const below = window.innerHeight - EDGE - (trigger.bottom + GAP)
    const above = trigger.top - GAP - EDGE
    setPlace(
      height > below && above > below
        ? { left, width, bottom: window.innerHeight - trigger.top + GAP }
        : { left, width, top: trigger.bottom + GAP },
    )
  }, [compact, open])

  // It closes on a press outside it or its trigger, and as anything scrolls or
  // the window changes, which would leave it away from its trigger.
  useEffect(() => {
    if (!compact || !open) return
    const inside = (target: EventTarget | null) =>
      target instanceof Node &&
      (menuRef.current?.contains(target) || triggerRef.current?.contains(target))
    const outside = (event: Event) => {
      if (!inside(event.target)) closeMenu()
    }
    const scrolled = (event: Event) => {
      if (!(event.target instanceof Node && menuRef.current?.contains(event.target))) closeMenu()
    }
    const away = () => closeMenu()
    document.addEventListener("pointerdown", outside, true)
    window.addEventListener("scroll", scrolled, true)
    window.addEventListener("resize", away)
    return () => {
      document.removeEventListener("pointerdown", outside, true)
      window.removeEventListener("scroll", scrolled, true)
      window.removeEventListener("resize", away)
    }
  }, [compact, open])

  const text = compact ? "text-[11px]" : "text-[13px]"
  const menu = (
    <div
      ref={menuRef}
      className={compact ? "fixed z-50 rounded-lg p-1.5 shadow-2xl" : "absolute z-20 mt-1 w-full rounded-lg p-1.5 shadow-lg"}
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
        ...(compact
          ? place === null
            ? { visibility: "hidden" as const }
            : { left: place.left, width: place.width, top: place.top, bottom: place.bottom }
          : {}),
      }}
      onBlur={
        compact
          ? (event) => {
              const next = event.relatedTarget as Node | null
              if (!(menuRef.current?.contains(next) || triggerRef.current?.contains(next))) closeMenu()
            }
          : undefined
      }
    >
      <input
        ref={searchRef}
        type="search"
        role="combobox"
        aria-label={`Search ${label}`}
        aria-expanded
        aria-controls={`${id}-listbox`}
        aria-activedescendant={
          options[activeIndex] ? optionId(activeIndex) : undefined
        }
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
          setActiveIndex(0)
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault()
            move(1)
          } else if (event.key === "ArrowUp") {
            event.preventDefault()
            move(-1)
          } else if (event.key === "Enter" && options[activeIndex]) {
            event.preventDefault()
            select(options[activeIndex].name)
          } else if (event.key === "Escape") {
            event.preventDefault()
            // The menu's Escape, not the page's (which closes the panel).
            event.stopPropagation()
            closeMenu(true)
          }
        }}
        className={`w-full rounded px-2 py-1.5 ${text} outline-none`}
        style={{
          background: "var(--bg-input)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
        placeholder="Search columns"
      />
      <ul
        id={`${id}-listbox`}
        role="listbox"
        aria-label={`${label} options`}
        className="mt-1 max-h-48 overflow-auto"
      >
        {options.map((column, index) => (
          <li
            ref={(element) => {
              optionRefs.current[index] = element
            }}
            id={optionId(index)}
            key={`${column.name}:${column.dtype}:${index}`}
            role="option"
            aria-selected={value === column.name}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => select(column.name)}
            title={column.label ? `${column.name}: ${column.label}` : undefined}
            className={`flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1.5 ${text} font-mono`}
            style={{
              background:
                index === activeIndex
                  ? "var(--chrome-hover)"
                  : "transparent",
              color: "var(--text-primary)",
            }}
          >
            <span className={compact ? "min-w-0 truncate" : undefined}>{column.name || "None"}</span>
            {column.label && (
              // Muted after the name, giving way to it first as the menu narrows.
              <span className="min-w-0 flex-1 shrink-[2] truncate font-sans" style={{ color: "var(--text-muted)" }}>
                {column.label}
              </span>
            )}
            {column.dtype && (
              <span
                className={compact ? "shrink-0 text-[10px]" : "text-[11px]"}
                style={{ color: "var(--text-muted)" }}
              >
                {column.dtype}
              </span>
            )}
          </li>
        ))}
        {options.length === 0 && (
          <li
            className={`px-2 py-2 ${compact ? "text-[11px]" : "text-[12px]"}`}
            style={{ color: "var(--text-muted)" }}
          >
            {columns.length === 0 && compact ? "No columns known yet." : "No matching columns."}
          </li>
        )}
      </ul>
    </div>
  )

  return (
    <div
      className={compact ? "relative min-w-0 flex-1" : "relative mt-0.5"}
      onBlur={
        compact
          ? (event) => {
              const next = event.relatedTarget as Node | null
              if (!(event.currentTarget.contains(next) || menuRef.current?.contains(next))) closeMenu()
            }
          : (event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                closeMenu()
            }
      }
    >
      <button
        ref={triggerRef}
        id={`${id}-button`}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-haspopup="listbox"
        tabIndex={tabIndex}
        onFocus={onFocus}
        title={
          compact
            ? unavailable
              ? `${value} is not in the input's columns`
              : dtype
                ? `${value} (${dtype})`
                : undefined
            : undefined
        }
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault()
            openMenu()
          } else if (event.key === "Escape") {
            event.preventDefault()
            // An open menu's Escape, not the page's (which closes the panel).
            if (open) event.stopPropagation()
            closeMenu(true)
          }
        }}
        className={
          !compact
            ? "flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] font-mono"
            : trigger === "add"
              ? ADD_BUTTON_CLASS
              : trigger === "quiet"
                ? "quiet-action focus-ring inline-flex items-center gap-1 rounded px-1 py-0.5 -ml-1 text-[11px] font-medium"
                : trigger === "field"
                ? "flex w-full min-w-0 items-center gap-1 rounded-lg border px-2 py-1.5 text-left text-xs font-mono focus:outline-none focus:border-[var(--accent)]"
                : "flex w-full min-w-0 items-center gap-1 rounded border px-1 py-px text-left text-[11px] font-mono focus:outline-none focus:border-[var(--accent)]"
        }
        style={
          compact && trigger === "add"
            ? ADD_BUTTON_STYLE
            : compact && trigger === "quiet"
              ? undefined
              : {
                background: "var(--bg-input)",
                ...(compact
                  ? { borderColor: unavailable ? "var(--danger)" : "var(--border)" }
                  : { border: `1px solid ${unavailable ? "var(--danger)" : "var(--border)"}` }),
                color: value === "" && compact ? "var(--text-muted)" : "var(--text-primary)",
              }
        }
      >
        {compact && (trigger === "add" || trigger === "quiet") && <Plus size={trigger === "quiet" ? 11 : 12} className="shrink-0" aria-hidden="true" />}
        <span className={compact && (trigger === "add" || trigger === "quiet") ? "min-w-0 truncate" : "min-w-0 flex-1 truncate"}>{display}</span>
        {known && !compact && (
          <span
            className="shrink-0 text-[11px]"
            style={{ color: "var(--text-muted)" }}
          >
            {dtype}
          </span>
        )}
        {!(compact && (trigger === "add" || trigger === "quiet")) && (
          <ChevronDown
            size={compact ? 11 : 13}
            className="shrink-0"
            style={{ color: "var(--text-muted)" }}
            aria-hidden="true"
          />
        )}
      </button>
      {unavailable && !compact && (
        <p
          role="alert"
          className="mt-1 text-[12px]"
          style={{ color: "var(--danger)" }}
        >
          {value} is unavailable in the current columns.
        </p>
      )}
      {open && (compact ? portal(menu) : menu)}
    </div>
  )
}

function portal(node: ReactNode) {
  return createPortal(node, document.body)
}
