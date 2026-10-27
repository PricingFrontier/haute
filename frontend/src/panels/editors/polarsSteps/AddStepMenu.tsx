import { Plus, Search, X, type LucideIcon } from "lucide-react"
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"

import { NODE_GROUP_COLORS } from "../../../theme/colors"
import { CALCULATIONS, calculationEntry, calculationExpr, calculationLabel, type CalculationId } from "./calculations"
import { AGGREGATIONS, COLUMNLESS_AGGREGATIONS, DATE_FORMAT_DEFAULTS, FUNCTIONS, STEP_CATALOGUE, WINDOW_AGGREGATIONS } from "./catalogue"
import { STEP_ICONS, calculationIcon } from "./stepIcons"
import type { CastEntry, Expr, StepKind, WindowAggregation } from "./types"

type AddableKind = Exclude<StepKind, "source">

/** The settings a chooser entry starts its step with, over the kind's defaults. */
export type StepPreset = { expr: Expr } | { casts: CastEntry[]; strict: boolean }

type Section = "Add column" | "Rows" | "Columns" | "Combine" | "Other"

type Entry = {
  id: string
  label: string
  kind: AddableKind
  section: Section
  description: string
  method: string | null
  icon: LucideIcon
  preset?: StepPreset
  /** Shown in its section; the other entries are found by search only. */
  shown: boolean
  /** The step a search hit adds, said after its label where that is not its own name. */
  adds?: string
  /** Further Polars calls the step makes, matched by search (a group-by's aggregates). */
  calls?: readonly string[]
}

const SECTIONS: Section[] = ["Add column", "Rows", "Columns", "Combine", "Other"]
const MAX_HITS = 10

function kindEntry(kind: AddableKind, section: Section, extra: Partial<Entry> = {}): Entry {
  const info = STEP_CATALOGUE.find((candidate) => candidate.kind === kind)
  if (!info) throw new Error(`Unknown step kind ${kind}`)
  return { id: kind, label: info.label, kind, section, description: info.description, method: info.method, icon: STEP_ICONS[kind], shown: true, ...extra }
}

/** What each calculation does, for the foot line and search; a function says it through its catalogue label. */
const CALCULATION_DESCRIPTIONS: Partial<Record<CalculationId, string>> = {
  formula: "Compute a new or replaced column from a formula",
  if_then: "Each row takes the value of the first condition it meets",
  window: "A group's value on every row",
  sum_across: "Add values across columns, skipping missing ones",
  join_text: "Join text from several values with a separator",
  value: "A column, a number or a constant as it is",
  clip: "Cap each value between a minimum and a maximum",
}
const CALCULATION_METHODS: Partial<Record<CalculationId, string>> = {
  formula: "with_columns",
  if_then: "when/then/otherwise",
  window: "over",
  sum_across: "sum_horizontal",
  join_text: "concat_str",
  value: "lit",
}

function calculationMenuEntry(id: CalculationId, extra: Partial<Entry> = {}): Entry {
  const calculation = calculationEntry(id)
  const fn = FUNCTIONS.find((f) => f.value === id)
  return {
    id: `calc:${id}`,
    label: calculationLabel(calculation),
    kind: "with_column",
    section: "Add column",
    description: CALCULATION_DESCRIPTIONS[id] ?? (fn ? fn.label.charAt(0).toUpperCase() + fn.label.slice(1) : calculation.name),
    method: CALCULATION_METHODS[id] ?? (fn ? fn.value : null),
    icon: calculationIcon(calculation),
    preset: { expr: calculationExpr(id) },
    shown: false,
    adds: "Add column",
    ...extra,
  }
}

const CHIP_CALCULATIONS: CalculationId[] = ["formula", "if_then", "window"]

function windowEntry(agg: WindowAggregation, label: string): Entry {
  const expr: Expr = COLUMNLESS_AGGREGATIONS.has(agg)
    ? { type: "window", agg, over: [] }
    : { type: "window", agg, of: { kind: "column", name: "" }, over: [], ...(agg === "quantile" ? { quantile: 0.5 } : {}) }
  return { ...calculationMenuEntry("window"), id: `window:${agg}`, label: `Window: ${label}`, method: agg, preset: { expr } }
}

function castEntry(method: string, label: string, description: string, entry: CastEntry, strict: boolean): Entry {
  return { ...kindEntry("cast", "Columns"), id: `cast:${method}`, label, description, method, preset: { casts: [entry], strict }, shown: false, adds: "Change types" }
}

const dateEntry = (dtype: "Date" | "Datetime"): CastEntry => ({ column: "", dtype, format: DATE_FORMAT_DEFAULTS[dtype] })

/** Every entry in reading order: the sections' own, each followed by the entries search finds there. */
const ENTRIES: Entry[] = [
  ...CHIP_CALCULATIONS.map((id) => calculationMenuEntry(id, { shown: true })),
  calculationMenuEntry("round", { id: "number-function", label: "Number function", description: "Round, cap, divide or take the log of numbers", method: null, shown: true }),
  calculationMenuEntry("strip", { id: "text-function", label: "Text function", description: "Trim, change case, replace or take part of text", method: "str", shown: true }),
  calculationMenuEntry("offset_by", { id: "date-function", label: "Date function", description: "Shift a date, or take its year, month, day or weekday", method: "dt", shown: true }),
  ...CALCULATIONS.filter((c) => !CHIP_CALCULATIONS.includes(c.id)).map((c) => calculationMenuEntry(c.id)),
  ...WINDOW_AGGREGATIONS.map((a) => windowEntry(a.value, a.label)),
  kindEntry("filter", "Rows"),
  kindEntry("sort", "Rows"),
  kindEntry("unique", "Rows"),
  kindEntry("limit", "Rows"),
  kindEntry("select", "Columns"),
  kindEntry("drop", "Columns"),
  kindEntry("rename", "Columns"),
  kindEntry("cast", "Columns"),
  castEntry("to_date", "Text to date", "Parse text into dates with a format", dateEntry("Date"), true),
  castEntry("to_datetime", "Text to date and time", "Parse text into dates and times with a format", dateEntry("Datetime"), true),
  castEntry("try_cast", "Convert, missing where a value doesn't convert", "Change a type, a value that doesn't convert becoming missing", { column: "", dtype: "Float64" }, false),
  castEntry("try_to_date", "Text to date, missing where it doesn't parse", "Parse text into dates, text that doesn't parse becoming missing", dateEntry("Date"), false),
  castEntry("try_to_datetime", "Text to date and time, missing where it doesn't parse", "Parse text into dates and times, text that doesn't parse becoming missing", dateEntry("Datetime"), false),
  kindEntry("fill_null", "Columns"),
  kindEntry("join", "Combine"),
  kindEntry("concat", "Combine"),
  kindEntry("group_by", "Combine", { calls: AGGREGATIONS.map((a) => a.value) }),
  kindEntry("pivot", "Combine"),
  kindEntry("unpivot", "Combine"),
  kindEntry("variable", "Other"),
  kindEntry("free_code", "Other"),
]

/**
 * How well an entry answers the query: 0 when its name or a Polars call is
 * the query, 1 when a word of its name or of a Polars call starts with it, 2 when its name,
 * description or a Polars call contains it, or null for no match.
 */
function searchRank(entry: Entry, query: string): number | null {
  const calls = [entry.method ?? "", ...(entry.calls ?? [])].map((call) => call.toLowerCase())
  const name = entry.label.toLowerCase()
  if (name === query || calls.includes(query)) return 0
  const words = [name, ...calls].flatMap((text) => text.split(/[^a-z0-9_]+/))
  if (words.some((word) => word.startsWith(query))) return 1
  if ([name, entry.description.toLowerCase(), ...calls].some((text) => text.includes(query))) return 2
  return null
}

/** The entries answering a query, best first and in reading order within a rank, at most ten. */
function searchHits(entries: Entry[], query: string): Entry[] {
  return entries
    .map((entry, order) => ({ entry, order, rank: searchRank(entry, query) }))
    .filter((hit): hit is { entry: Entry; order: number; rank: number } => hit.rank !== null)
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .slice(0, MAX_HITS)
    .map((hit) => hit.entry)
}

/**
 * The `Add step` control that sits under the last step: a full-width button
 * that opens, in place, a chooser of entries in the Add column, Rows,
 * Columns, Combine and Other sections. An entry adds a step of its kind,
 * started from its preset (an Add column entry's calculation). The search box
 * takes focus; typing replaces the sections with one ranked list of hits over
 * every entry and the ones only search finds (the other calculations, window
 * aggregates and Change types presets), each naming the step it adds where
 * that is not its own name. Enter adds the first hit. The focused or hovered
 * entry's description and Polars call show at the foot. Arrow keys move
 * between entries, Escape closes and returns focus to the button, and
 * choosing an entry hands it to the editor (which moves focus into the new
 * card).
 */
export default function AddStepMenu({
  onAdd,
  disabled = false,
  withhold,
  buttonRef,
}: {
  onAdd: (kind: AddableKind, preset?: StepPreset) => void
  disabled?: boolean
  /** Kinds the surface cannot use (join and concat while no input name is eligible). */
  withhold?: ReadonlySet<AddableKind>
  buttonRef?: (element: HTMLButtonElement | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [described, setDescribed] = useState<string | null>(null)
  const menuId = useId()
  const button = useRef<HTMLButtonElement | null>(null)
  const search = useRef<HTMLInputElement | null>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const wasOpen = useRef(false)
  // Closing by choosing an entry leaves focus to the new card, not the button.
  const chose = useRef(false)
  const offered = ENTRIES.filter((entry) => !withhold?.has(entry.kind))
  const searching = query.trim().length > 0
  const hits = searching ? searchHits(offered, query.trim().toLowerCase()) : []
  const sections = SECTIONS.map((title) => ({ title, items: offered.filter((entry) => entry.shown && entry.section === title) })).filter((section) => section.items.length > 0)
  const items: Entry[] = searching ? hits : sections.flatMap((section) => section.items)
  const footer = ENTRIES.find((entry) => entry.id === described) ?? (searching ? items[0] : undefined)

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
  const choose = (entry: Entry) => {
    chose.current = true
    close()
    onAdd(entry.kind, entry.preset)
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
      if (items[0]) choose(items[0])
    } else if (event.key === "ArrowDown" || (event.key === "ArrowRight" && !inSearch)) {
      event.preventDefault()
      itemRefs.current[inSearch ? 0 : (focused + 1) % items.length]?.focus()
    } else if (event.key === "ArrowUp" || (event.key === "ArrowLeft" && !inSearch)) {
      event.preventDefault()
      // Up from the search box wraps to the last entry; up from the first entry returns to the search box.
      if (inSearch) itemRefs.current[items.length - 1]?.focus()
      else if (focused <= 0) search.current?.focus()
      else itemRefs.current[focused - 1]?.focus()
    }
  }

  const itemProps = (entry: Entry) => {
    const index = items.indexOf(entry)
    return {
      type: "button" as const,
      role: "menuitem",
      title: entry.description,
      ref: (el: HTMLButtonElement | null) => {
        itemRefs.current[index] = el
      },
      onClick: () => choose(entry),
      onMouseEnter: () => setDescribed(entry.id),
      onFocus: () => setDescribed(entry.id),
    }
  }
  const icon = (entry: Entry) => {
    const Icon = entry.icon
    return <Icon size={12} aria-hidden="true" className="shrink-0" style={{ color: NODE_GROUP_COLORS.transform }} />
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
                aria-label="Search steps and functions"
                placeholder="Search steps and functions"
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
          {searching && hits.length === 0 && (
            <p className="m-0 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Nothing matches “{query.trim()}”.
            </p>
          )}
          {searching ? (
            <div role="group" aria-label="Matches" className="grid gap-0.5">
              {hits.map((entry) => (
                <button key={entry.id} {...itemProps(entry)} className="add-row-btn focus-ring flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                  {icon(entry)}
                  <span className="min-w-0 truncate">{entry.label}</span>
                  {entry.adds && (
                    <span className="ml-auto shrink-0 pl-2 text-[11px] font-normal" style={{ color: "var(--text-muted)" }}>
                      {entry.adds}
                    </span>
                  )}
                </button>
              ))}
            </div>
          ) : (
            sections.map((section) => (
              <div key={section.title} role="group" aria-label={section.title} className="grid gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
                    {section.title}
                  </span>
                  <span aria-hidden="true" className="h-px flex-1" style={{ background: "var(--border-subtle)" }} />
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {section.items.map((entry) => (
                    <button
                      key={entry.id}
                      {...itemProps(entry)}
                      className="add-row-btn focus-ring inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                      style={{ color: "var(--text-primary)", background: "var(--bg-input)", border: "1px solid var(--border)" }}
                    >
                      {icon(entry)}
                      {entry.label}
                    </button>
                  ))}
                </div>
              </div>
            ))
          )}
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
              <span style={{ color: "var(--text-muted)" }}>Point at an entry to see what it does.</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
