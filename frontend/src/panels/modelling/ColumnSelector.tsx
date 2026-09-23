import { useEffect, useId, useMemo, useRef, useState } from "react"
import { ChevronDown } from "lucide-react"

export type ColumnOption = { name: string; dtype: string }
type Props = {
  label: string
  value: string
  columns: readonly ColumnOption[]
  onChange: (value: string) => void
  optional?: boolean
  placeholder?: string
}

/** A column-only searchable combobox. Saved unavailable values remain visible,
 * but are deliberately never offered as a new selectable option. */
export function ColumnSelector({
  label,
  value,
  columns,
  onChange,
  optional = false,
  placeholder,
}: Props) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const optionRefs = useRef<Array<HTMLLIElement | null>>([])
  const animationFrame = useRef<number | null>(null)
  const known = columns.some((column) => column.name === value)
  const unavailable = value !== "" && !known
  const options = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = columns.filter(
      (column) =>
        column.name.toLowerCase().includes(needle) ||
        column.dtype.toLowerCase().includes(needle),
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

  return (
    <div
      className="relative mt-0.5"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          closeMenu()
      }}
    >
      <button
        ref={triggerRef}
        id={`${id}-button`}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-haspopup="listbox"
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault()
            openMenu()
          } else if (event.key === "Escape") {
            event.preventDefault()
            closeMenu(true)
          }
        }}
        className="flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] font-mono"
        style={{
          background: "var(--bg-input)",
          border: `1px solid ${unavailable ? "var(--danger)" : "var(--border)"}`,
          color: "var(--text-primary)",
        }}
      >
        <span className="min-w-0 flex-1 truncate">{display}</span>
        {known && (
          <span
            className="shrink-0 text-[11px]"
            style={{ color: "var(--text-muted)" }}
          >
            {columns.find((column) => column.name === value)?.dtype}
          </span>
        )}
        <ChevronDown
          size={13}
          className="shrink-0"
          style={{ color: "var(--text-muted)" }}
          aria-hidden="true"
        />
      </button>
      {unavailable && (
        <p
          role="alert"
          className="mt-1 text-[12px]"
          style={{ color: "var(--danger)" }}
        >
          {value} is unavailable in the current columns.
        </p>
      )}
      {open && (
        <div
          className="absolute z-20 mt-1 w-full rounded-lg p-1.5 shadow-lg"
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border)",
          }}
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
                closeMenu(true)
              }
            }}
            className="w-full rounded px-2 py-1.5 text-[13px] outline-none"
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
                className="flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1.5 text-[13px] font-mono"
                style={{
                  background:
                    index === activeIndex
                      ? "var(--chrome-hover)"
                      : "transparent",
                  color: "var(--text-primary)",
                }}
              >
                <span>{column.name || "None"}</span>
                {column.dtype && (
                  <span
                    className="text-[11px]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {column.dtype}
                  </span>
                )}
              </li>
            ))}
            {options.length === 0 && (
              <li
                className="px-2 py-2 text-[12px]"
                style={{ color: "var(--text-muted)" }}
              >
                No matching columns.
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}
