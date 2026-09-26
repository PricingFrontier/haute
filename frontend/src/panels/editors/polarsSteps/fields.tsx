/**
 * Shared controls for step forms. Every text control commits on blur or
 * Enter; selects commit immediately. Column controls are comboboxes over the
 * suggested columns that accept free text. The operand control is
 * constrained per field (allowed sources and literal types) so a form can
 * only produce payloads the backend schema accepts. Row lists, bordered row
 * groups, direction and quantile controls live here too so every form lays
 * out its repeated rows the same way.
 */
import {
  AlertTriangle,
  Ban,
  Calendar,
  Check,
  ChevronRight,
  Columns2,
  Hash,
  Plus,
  Quote,
  SquareFunction,
  ToggleRight,
  Variable as VariableIcon,
  X,
  type LucideIcon,
} from "lucide-react"
import { useId, useState, type ReactNode } from "react"

import { CommittedTextField } from "../../../components/form"
import { getDtypeColor } from "../../../utils/dtypeColors"
import { INPUT_STYLE } from "../_shared"
import { CONDITION_OPERATORS, LIST_LITERAL_TYPES, LITERAL_TYPES, defaultCondition, defaultExpr, defaultLiteral, literal } from "./catalogue"
import { completionMatches } from "./completion"
import { useStepSchema } from "./stepSchema"
import { namesAsCompletions, useCompletionList, type Completion } from "./useCompletionList"
import type { Condition, Expr, LiteralOperand, LiteralType, MatchMode, Operand } from "./types"

export const CONTROL_CLASS = "focus-ring w-full min-w-0 px-2 py-1.5 text-xs rounded-md"
const CHIP_STYLE = { background: "var(--chrome-hover)", color: "var(--text-primary)", border: "1px solid var(--border)" }
const ADD_BUTTON_STYLE = { color: "var(--text-secondary)", border: "1px solid var(--border)" }

/**
 * A field's label: short and in sentence case, with any note beside it in
 * normal weight ("Group by · empty = summarise the whole frame"), so a card
 * has a hierarchy under its title rather than stacked capitals.
 */
export function FieldLabel({ children, htmlFor, note }: { children: ReactNode; htmlFor?: string; note?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 flex flex-wrap items-baseline gap-x-1.5 text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
      <span>{children}</span>
      {note && (
        <span className="font-normal" style={{ color: "var(--text-muted)" }}>
          {note}
        </span>
      )}
    </label>
  )
}

/** A stacked label + control block, for fields a sentence cannot carry. */
export function Field({ label, note, children, htmlFor }: { label?: ReactNode; note?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="min-w-0">
      {label !== undefined && <FieldLabel htmlFor={htmlFor} note={note}>{label}</FieldLabel>}
      {children}
    </div>
  )
}

/**
 * A form line that reads as a sentence: muted linking words (`Words`) and
 * controls, wrapping at narrow widths (`Keep the first [100] rows`). The
 * controls carry their own accessible names.
 */
export function Sentence({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div
      className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs"
      style={{ color: "var(--text-secondary)" }}
      role={label ? "group" : undefined}
      aria-label={label}
    >
      {children}
    </div>
  )
}

/** Linking words inside a `Sentence`. */
export function Words({ children }: { children: ReactNode }) {
  return <span className="shrink-0 whitespace-nowrap">{children}</span>
}

/**
 * Options the simple case never needs, behind a quiet disclosure that opens
 * by itself when the saved step already uses one of them.
 */
export function MoreOptions({ used, children, label = "More options" }: { used: boolean; children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(used)
  const id = useId()
  return (
    <div className="grid gap-2">
      <DisclosureButton open={open} onToggle={() => setOpen((v) => !v)} label={label} controls={id} />
      <div id={id} hidden={!open} className="grid gap-2.5">
        {open && children}
      </div>
    </div>
  )
}

/** The quiet chevron toggle of a disclosure ("› More options"). */
export function DisclosureButton({ open, onToggle, label, controls }: { open: boolean; onToggle: () => void; label: string; controls?: string }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className="focus-ring inline-flex items-center gap-1 justify-self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium"
      style={{ color: "var(--text-secondary)" }}
    >
      <ChevronRight size={11} aria-hidden="true" className="transition-transform" style={{ transform: open ? "rotate(90deg)" : undefined }} />
      {label}
    </button>
  )
}

/** A muted one-line note under a control. */
export function Hint({ children }: { children: string }) {
  return (
    <p className="text-[11px] m-0" style={{ color: "var(--text-muted)" }}>
      {children}
    </p>
  )
}

export type SelectOption<T extends string> = { value: T; label: string }

export function SelectField<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  placeholder,
  id,
  className = "",
}: {
  value: T | ""
  options: ReadonlyArray<SelectOption<T>>
  onChange: (value: T) => void
  ariaLabel: string
  placeholder?: string
  id?: string
  className?: string
}) {
  return (
    <select
      id={id}
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className={`${CONTROL_CLASS} ${className}`}
      style={INPUT_STYLE}
    >
      {placeholder !== undefined && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

export function TextField({
  value,
  onCommit,
  ariaLabel,
  placeholder,
  mono = false,
  id,
  autoFocus,
}: {
  value: string
  onCommit: (next: string) => void
  ariaLabel: string
  placeholder?: string
  mono?: boolean
  id?: string
  autoFocus?: boolean
}) {
  return (
    <CommittedTextField
      id={id}
      type="text"
      aria-label={ariaLabel}
      value={value}
      onCommit={onCommit}
      placeholder={placeholder}
      autoFocus={autoFocus}
      className={`${CONTROL_CLASS} ${mono ? "font-mono" : ""}`}
      style={INPUT_STYLE}
    />
  )
}

export function NumberField({
  value,
  onCommit,
  ariaLabel,
  integer = false,
  min,
  id,
}: {
  value: number
  onCommit: (next: number) => void
  ariaLabel: string
  integer?: boolean
  min?: number
  id?: string
}) {
  const [error, setError] = useState<string | null>(null)
  const [seenValue, setSeenValue] = useState(value)
  if (seenValue !== value) {
    setSeenValue(value)
    setError(null)
  }
  const errorId = useId()
  return (
    <div>
      <CommittedTextField
        id={id}
        type="number"
        inputMode={integer ? "numeric" : "decimal"}
        step={integer ? 1 : "any"}
        min={min}
        aria-label={ariaLabel}
        aria-invalid={error !== null ? true : undefined}
        aria-describedby={error === null ? undefined : errorId}
        value={String(value)}
        onCommit={(text) => {
          let nextError: string | null = null
          const parsed = text.trim() === "" ? Number.NaN : Number(text)
          if (!Number.isFinite(parsed)) {
            nextError = "Enter a finite number."
          } else if (integer && !Number.isInteger(parsed)) {
            nextError = "Enter a whole number."
          } else if (min !== undefined && parsed < min) {
            nextError = `Enter a value of at least ${min}.`
          }
          if (nextError !== null) {
            setError(nextError)
            return
          }
          setError(null)
          onCommit(parsed)
        }}
        className={`${CONTROL_CLASS} font-mono`}
        style={INPUT_STYLE}
      />
      {error !== null && (
        <p id={errorId} role="alert" className="text-[11px] mt-1 mb-0" style={{ color: "var(--danger-text)" }}>
          {error}
        </p>
      )}
    </div>
  )
}

/** Ascending/descending as a select; `labels` renames the two directions. */
export function DirectionSelect({
  descending,
  onChange,
  ariaLabel,
  labels = ["ascending", "descending"],
}: {
  descending: boolean
  onChange: (descending: boolean) => void
  ariaLabel: string
  labels?: [string, string]
}) {
  return (
    <SelectField
      value={descending ? "desc" : "asc"}
      options={[
        { value: "asc", label: labels[0] },
        { value: "desc", label: labels[1] },
      ]}
      onChange={(dir) => onChange(dir === "desc")}
      ariaLabel={ariaLabel}
    />
  )
}

/** The quantile of a `quantile` aggregate, clamped to [0, 1]. */
export function QuantileField({ value, onChange, ariaLabel }: { value: number | undefined; onChange: (quantile: number) => void; ariaLabel: string }) {
  return (
    <Field label="Quantile (0 to 1)">
      <NumberField value={value ?? 0.5} min={0} onCommit={(quantile) => onChange(Math.min(1, Math.max(0, quantile)))} ariaLabel={ariaLabel} />
    </Field>
  )
}

/** The quiet "+ Add …" text action under a list. */
export function AddRow({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="quiet-action focus-ring inline-flex items-center gap-1 justify-self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium"
    >
      <Plus size={11} aria-hidden="true" />
      {label}
    </button>
  )
}

/** The icon-only remove action at the end of a row. */
export function RowRemove({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} className="icon-danger-btn focus-ring p-1 rounded shrink-0">
      <X size={12} aria-hidden="true" />
    </button>
  )
}

/** A bordered group of controls that belong to one row (a condition, an aggregation). */
export function RowGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 p-2 rounded-md" style={{ border: "1px solid var(--border-subtle)" }} role="group" aria-label={label}>
      {children}
    </div>
  )
}

/**
 * An editable list of rows: each row is a wrapping flex line named
 * `<label> <n>` with a remove action while more than `min` rows remain,
 * anything passed as children sits between the rows and the add action.
 */
export function RowList<T>({
  rows,
  onChange,
  label,
  addLabel,
  create,
  min = 1,
  renderRow,
  children,
}: {
  rows: T[]
  onChange: (rows: T[]) => void
  /** Accessible name prefix for the rows ("Sort key" names "Sort key 1" and "Remove sort key 1"). */
  label: string
  addLabel: string
  create: () => T
  /** Rows that stay without a remove action. */
  min?: number
  renderRow: (row: T, set: (next: T) => void, index: number) => ReactNode
  children?: ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      {rows.map((row, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`${label} ${index + 1}`}>
          {renderRow(row, (next) => onChange(rows.map((r, i) => (i === index ? next : r))), index)}
          {rows.length > min && (
            <RowRemove label={`Remove ${label.toLowerCase()} ${index + 1}`} onClick={() => onChange(rows.filter((_, i) => i !== index))} />
          )}
        </div>
      ))}
      {children}
      <AddRow label={addLabel} onClick={() => onChange([...rows, create()])} />
    </div>
  )
}

/** A name with the part matching `prefix` in bold. */
function PrefixLabel({ label, prefix }: { label: string; prefix: string }) {
  if (!prefix || !label.toLowerCase().startsWith(prefix.toLowerCase())) return <>{label}</>
  return (
    <>
      <strong className="font-semibold" style={{ color: "var(--text-primary)" }}>{label.slice(0, prefix.length)}</strong>
      {label.slice(prefix.length)}
    </>
  )
}

/** The colour of a completion's note: a column type in its type colour, anything else muted. */
function noteClass(note: string): string {
  return note === "new" || note === "variable" ? "" : getDtypeColor(note)
}

/**
 * The dropdown of completions under a text box; the box owns the keyboard.
 * The active entry is tinted with an accent bar at its left; `matched` names
 * text that is already an exact name, shown ticked so Tab visibly keeps it.
 */
export function CompletionList({
  id,
  matches,
  activeIndex,
  matched = null,
  prefix = "",
  onPick,
  onHover,
  label = "Matching columns",
}: {
  id: string
  matches: Completion[]
  activeIndex: number | null
  matched?: string | null
  prefix?: string
  onPick: (entry: Completion) => void
  onHover: (index: number) => void
  label?: string
}) {
  if (matches.length === 0 && matched === null) return null
  return (
    <ul
      id={id}
      role="listbox"
      aria-label={label}
      className="absolute left-0 right-0 z-20 mt-1 max-h-56 overflow-y-auto rounded-md p-1 shadow-lg"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
    >
      {matched !== null && (
        <li role="none" className="flex items-center gap-1.5 rounded px-2 py-1 text-xs font-mono" style={{ color: "var(--text-secondary)" }}>
          <Check size={11} aria-hidden="true" style={{ color: "var(--success)" }} />
          <span className="truncate">{matched}</span>
          <span className="ml-auto text-[10px] font-sans">matched</span>
        </li>
      )}
      {matches.map((entry, index) => {
        const active = index === activeIndex
        return (
          <li
            key={entry.value}
            id={`${id}-${index}`}
            role="option"
            aria-selected={active}
            onMouseDown={(event) => {
              event.preventDefault()
              onPick(entry)
            }}
            onMouseEnter={() => onHover(index)}
            className="flex cursor-pointer items-center gap-1.5 rounded px-2 py-1 text-xs font-mono"
            style={{
              color: active ? "var(--text-primary)" : "var(--text-secondary)",
              background: active ? "var(--accent-soft)" : "transparent",
              boxShadow: active ? "inset 2px 0 0 var(--accent)" : undefined,
            }}
          >
            {entry.mark && (
              <span className="w-3 shrink-0 text-center" style={{ color: "var(--syntax-function)" }} aria-hidden="true">
                {entry.mark}
              </span>
            )}
            <span className="truncate">
              <PrefixLabel label={entry.label} prefix={prefix} />
            </span>
            {entry.note && (
              <span aria-hidden="true" className={`ml-auto shrink-0 pl-2 text-[10px] font-sans ${noteClass(entry.note)}`} style={noteClass(entry.note) ? undefined : { color: "var(--text-muted)" }}>
                {entry.note}
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/**
 * The note under a column field whose name is not in the data at its step,
 * offering the closest known name. Amber, not red: a step being built is not
 * an error yet, and a name an unsaved upstream step will create is legitimate.
 */
export function UnknownColumnNote({ name, suggestion, onUse, id }: { name: string; suggestion: string | null; onUse: (name: string) => void; id?: string }) {
  return (
    <p id={id} role="status" className="m-0 flex flex-wrap items-center gap-x-1 text-[11px] leading-snug" style={{ color: "var(--warning)" }}>
      <AlertTriangle size={11} aria-hidden="true" className="shrink-0" />
      <span>
        <code className="font-mono">{name}</code> isn&apos;t in the data at this step.
      </span>
      {suggestion !== null && (
        <>
          {" "}
          <span>
            Did you mean <code className="font-mono">{suggestion}</code>?
          </span>{" "}
          <button type="button" onClick={() => onUse(suggestion)} className="focus-ring rounded px-0.5 underline">
            Use {suggestion}
          </button>
        </>
      )}
    </p>
  )
}

/** Border for a column field or chip holding a name the step does not have. */
const UNKNOWN_BORDER = "1px dashed var(--warning)"

/**
 * A text box that completes column names: the names starting with what is
 * typed are listed beneath (every name while the box is empty, none active),
 * typing makes the first match active, Up/Down move, Tab, Enter or a click
 * takes the active name, Escape closes the list, and with nothing active
 * Enter or leaving the box commits what was typed. Each name shows its type
 * from the step's schema.
 */
function CompletingInput({
  draft,
  onDraftChange,
  onCommit,
  onAccept,
  suggestions,
  exclude = [],
  ariaLabel,
  placeholder,
  id,
  autoFocus,
  className = "",
  unknown = false,
  describedBy,
  extras = [],
}: {
  draft: string
  onDraftChange: (next: string) => void
  onCommit: (draft: string) => void
  onAccept: (name: string) => void
  suggestions: string[]
  exclude?: string[]
  ariaLabel: string
  placeholder?: string
  id?: string
  autoFocus?: boolean
  className?: string
  /** Mark the box as holding a name the step does not have. */
  unknown?: boolean
  describedBy?: string
  /** Entries listed after the matching names (a type-wide choice), matched on their label. */
  extras?: Completion[]
}) {
  const schema = useStepSchema()
  const typed = draft.trim()
  const exact = typed.length > 0 && suggestions.includes(typed) && !exclude.includes(typed) ? typed : null
  const extraMatches = extras.filter((entry) => entry.label.toLowerCase().includes(typed.toLowerCase()))
  const completion = useCompletionList([...namesAsCompletions(completionMatches(suggestions, typed, exclude), schema.describe), ...extraMatches], exact)
  const accept = (entry: Completion) => {
    completion.hide()
    onAccept(entry.value)
  }
  return (
    <div className={`relative ${className}`}>
      <input
        id={id}
        type="text"
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        value={draft}
        placeholder={placeholder}
        autoFocus={autoFocus}
        {...completion.inputProps}
        onChange={(event) => {
          onDraftChange(event.target.value)
          completion.typed()
        }}
        onFocus={completion.show}
        onBlur={() => {
          completion.hide()
          onCommit(draft)
        }}
        onKeyDown={(event) => {
          if (completion.onKeyDown(event, accept)) return
          if (event.key === "Enter") {
            event.preventDefault()
            completion.hide()
            onCommit(draft)
          }
        }}
        className={`${CONTROL_CLASS} font-mono`}
        style={unknown ? { ...INPUT_STYLE, border: UNKNOWN_BORDER } : INPUT_STYLE}
      />
      <CompletionList {...completion.listProps} prefix={typed} onPick={accept} />
    </div>
  )
}

/**
 * A column-name box that completes from the suggested columns and accepts
 * any typed name. The draft follows the committed value when it changes from
 * outside, without remounting the box, so focus survives a commit.
 */
export function ColumnPicker({
  value,
  onCommit,
  suggestions,
  ariaLabel,
  placeholder = "column",
  id,
  autoFocus,
  extras,
  onExtra,
}: {
  value: string
  onCommit: (next: string) => void
  suggestions: string[]
  ariaLabel: string
  placeholder?: string
  id?: string
  autoFocus?: boolean
  /** Entries after the column names that are not columns; choosing one calls `onExtra` with its value. */
  extras?: Completion[]
  onExtra?: (value: string) => void
}) {
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  if (value !== seen) {
    setSeen(value)
    setDraft(value)
  }
  const schema = useStepSchema()
  const noteId = useId()
  const commit = (next: string) => {
    const trimmed = next.trim()
    if (trimmed !== value) onCommit(trimmed)
  }
  const unknown = value !== "" && schema.isKnown !== null && !schema.isKnown(value)
  return (
    <div className="grid gap-1 min-w-0">
      <CompletingInput
        draft={draft}
        onDraftChange={setDraft}
        onCommit={commit}
        onAccept={(name) => {
          if (extras?.some((entry) => entry.value === name)) {
            setDraft(value)
            onExtra?.(name)
            return
          }
          setDraft(name)
          commit(name)
        }}
        suggestions={suggestions}
        extras={extras}
        ariaLabel={ariaLabel}
        placeholder={placeholder}
        id={id}
        autoFocus={autoFocus}
        unknown={unknown}
        describedBy={unknown ? noteId : undefined}
      />
      {unknown && <UnknownColumnNote id={noteId} name={value} suggestion={schema.closest(value)} onUse={(name) => {
        setDraft(name)
        onCommit(name)
      }} />}
    </div>
  )
}

function Chip({ label, onRemove, removeLabel, unknown = false }: { label: string; onRemove: () => void; removeLabel: string; unknown?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono"
      style={unknown ? { ...CHIP_STYLE, border: UNKNOWN_BORDER, color: "var(--warning)" } : CHIP_STYLE}
      title={unknown ? "Not in the data at this step" : undefined}
    >
      {label}
      <button type="button" onClick={onRemove} aria-label={removeLabel} className="icon-danger-btn focus-ring rounded">
        <X size={10} aria-hidden="true" />
      </button>
    </span>
  )
}

/** An ordered list of column names as chips, with a completing box to add one. */
export function ColumnListField({
  columns,
  onChange,
  suggestions,
  ariaLabel,
  placeholder = "add column",
  id,
}: {
  columns: string[]
  onChange: (next: string[]) => void
  suggestions: string[]
  ariaLabel: string
  placeholder?: string
  /** Id for the add box, when it is the first field of its card. */
  id?: string
}) {
  const schema = useStepSchema()
  const [draft, setDraft] = useState("")
  const add = (raw: string) => {
    const name = raw.trim()
    if (name && !columns.includes(name)) onChange([...columns, name])
    setDraft("")
  }
  const isUnknown = (name: string) => schema.isKnown !== null && !schema.isKnown(name)
  const unknown = columns.filter(isUnknown)
  return (
    <div className="grid gap-1 min-w-0">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={ariaLabel}>
        {columns.map((name) => (
          <Chip
            key={name}
            label={name}
            removeLabel={`Remove ${name}`}
            onRemove={() => onChange(columns.filter((c) => c !== name))}
            unknown={isUnknown(name)}
          />
        ))}
        <CompletingInput
          draft={draft}
          onDraftChange={setDraft}
          onCommit={add}
          onAccept={add}
          suggestions={suggestions}
          exclude={columns}
          ariaLabel={`${ariaLabel}: add`}
          placeholder={placeholder}
          id={id}
          className="flex-1 basis-28 min-w-0"
        />
      </div>
      {unknown.map((name) => (
        <UnknownColumnNote
          key={name}
          name={name}
          suggestion={schema.closest(name)}
          onUse={(replacement) =>
            onChange(columns.includes(replacement) ? columns.filter((c) => c !== name) : columns.map((c) => (c === name ? replacement : c)))
          }
        />
      ))}
    </div>
  )
}

/** The value input for one literal, chosen by its type. */
export function LiteralValueInput({
  value,
  onChange,
  ariaLabel,
  integer = false,
}: {
  value: LiteralOperand
  onChange: (next: LiteralOperand) => void
  ariaLabel: string
  integer?: boolean
}) {
  switch (value.type) {
    case "number":
      return (
        <NumberField
          value={typeof value.value === "number" ? value.value : 0}
          integer={integer}
          onCommit={(n) => onChange(literal("number", n))}
          ariaLabel={ariaLabel}
        />
      )
    case "text":
      return (
        <TextField
          value={String(value.value)}
          onCommit={(text) => onChange(literal("text", text))}
          ariaLabel={ariaLabel}
          placeholder="text"
        />
      )
    case "boolean":
      return (
        <SelectField
          value={value.value ? "true" : "false"}
          options={[
            { value: "true", label: "true" },
            { value: "false", label: "false" },
          ]}
          onChange={(v) => onChange(literal("boolean", v === "true"))}
          ariaLabel={ariaLabel}
        />
      )
    case "date":
      return (
        <CommittedTextField
          type="date"
          aria-label={ariaLabel}
          value={String(value.value)}
          onCommit={(text) => onChange(literal("date", text))}
          className={`${CONTROL_CLASS} font-mono`}
          style={INPUT_STYLE}
        />
      )
    case "null":
      return (
        <input
          type="text"
          readOnly
          aria-label={ariaLabel}
          value="null"
          className={`${CONTROL_CLASS} font-mono`}
          style={{ ...INPUT_STYLE, color: "var(--text-muted)" }}
        />
      )
  }
}

export type OperandSource = "literal" | "column" | "variable" | "expr"

/** What a value is: one of the literal types, or a column, a variable or an expression. */
type OperandKind = LiteralType | Exclude<OperandSource, "literal">

const KIND_LABELS: Record<OperandKind, string> = {
  number: "Number",
  text: "Text",
  boolean: "True/false",
  date: "Date",
  null: "Missing (null)",
  column: "Column",
  variable: "Variable",
  expr: "Expression",
}

const KIND_ICONS: Record<OperandKind, LucideIcon> = {
  number: Hash,
  text: Quote,
  boolean: ToggleRight,
  date: Calendar,
  null: Ban,
  column: Columns2,
  variable: VariableIcon,
  expr: SquareFunction,
}

const operandKind = (operand: Operand): OperandKind => (operand.kind === "literal" ? operand.type : operand.kind)

/**
 * The marker at the start of a value: an icon for what the value is, over an
 * invisible native select of the kinds allowed there, so a click (or Alt+Down)
 * opens the choice and the select keeps its keyboard and screen-reader
 * behaviour.
 */
function KindMarker({ kind, kinds, onChange, ariaLabel }: { kind: OperandKind; kinds: OperandKind[]; onChange: (kind: OperandKind) => void; ariaLabel: string }) {
  const Icon = KIND_ICONS[kind]
  return (
    <span
      className="relative flex w-7 shrink-0 self-stretch items-center justify-center rounded-md focus-within:ring-2 focus-within:ring-[var(--accent-ring)]"
      style={{ background: "var(--chrome-hover)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
      title={`${KIND_LABELS[kind]} (change what this value is)`}
    >
      <Icon size={12} aria-hidden="true" />
      <select
        aria-label={ariaLabel}
        value={kind}
        onChange={(event) => onChange(event.target.value as OperandKind)}
        className="absolute inset-0 w-full cursor-pointer opacity-0"
      >
        {kinds.map((k) => (
          <option key={k} value={k}>
            {KIND_LABELS[k]}
          </option>
        ))}
      </select>
    </span>
  )
}

/** Renders a nested expression editor for an operand; supplied by the forms so fields need not import them. */
export type RenderExpression = (expr: Expr, onChange: (next: Expr) => void, ariaLabel: string) => ReactNode

/**
 * One operand as one control: a kind marker (only the allowed literal types
 * and sources; Variable only when a variable exists, Expression only when
 * `renderExpression` is given) at the start of the matching input. The
 * marker is left out when only one kind is allowed.
 */
export function OperandField({
  value,
  onChange,
  sources,
  literalTypes,
  columns,
  variables,
  ariaLabel,
  renderExpression,
}: {
  value: Operand
  onChange: (next: Operand) => void
  sources: readonly OperandSource[]
  literalTypes: readonly LiteralType[]
  columns: string[]
  variables: string[]
  ariaLabel: string
  renderExpression?: RenderExpression
}) {
  const kinds: OperandKind[] = [
    ...(sources.includes("literal") ? LITERAL_TYPES.map((t) => t.value).filter((t) => literalTypes.includes(t)) : []),
    ...sources.filter((s): s is "column" | "variable" => s === "column" || (s === "variable" && (variables.length > 0 || value.kind === "variable"))),
    ...(renderExpression ? (["expr"] as const) : []),
  ]
  const kind = operandKind(value)
  const setKind = (next: OperandKind) => {
    if (next === kind) return
    if (next === "column") onChange({ kind: "column", name: "" })
    else if (next === "expr") onChange({ kind: "expr", expr: defaultExpr("binary") })
    else if (next === "variable") onChange({ kind: "variable", name: variables[0] ?? "" })
    else onChange(defaultLiteral(next))
  }
  return (
    <div className="flex flex-wrap items-center gap-1" role="group" aria-label={ariaLabel}>
      {kinds.length > 1 && <KindMarker kind={kind} kinds={kinds} onChange={setKind} ariaLabel={`${ariaLabel} kind`} />}
      <div className="flex-1 basis-28 min-w-0">
        {value.kind === "literal" && (
          <LiteralValueInput value={value} onChange={onChange} ariaLabel={`${ariaLabel} value`} />
        )}
        {value.kind === "column" && (
          <ColumnPicker
            value={value.name}
            onCommit={(name) => onChange({ kind: "column", name })}
            suggestions={columns}
            ariaLabel={`${ariaLabel} column`}
          />
        )}
        {value.kind === "variable" && (
          <SelectField
            value={variables.includes(value.name) ? value.name : ""}
            placeholder="choose a variable"
            options={variables.map((name) => ({ value: name, label: name }))}
            onChange={(name) => onChange({ kind: "variable", name })}
            ariaLabel={`${ariaLabel} variable`}
          />
        )}
      </div>
      {value.kind === "expr" && renderExpression && (
        <div className="basis-full grid gap-1.5 pl-2 ml-0.5" style={{ borderLeft: "2px solid var(--border)" }} role="group" aria-label={`${ariaLabel} expression`}>
          {renderExpression(value.expr, (expr) => onChange({ kind: "expr", expr }), `${ariaLabel} expression`)}
        </div>
      )}
    </div>
  )
}

/** Membership values: one type for the whole list, then plain values as chips. */
export function LiteralListField({
  values,
  onChange,
  ariaLabel,
}: {
  values: LiteralOperand[]
  onChange: (next: LiteralOperand[]) => void
  ariaLabel: string
}) {
  // The list's type is the first value's type; while the list is empty the
  // chosen type lives here so switching type does not snap back to text.
  const [chosenType, setChosenType] = useState<LiteralType>(values[0]?.type ?? "text")
  const type: LiteralType = values[0]?.type ?? chosenType
  const [draft, setDraft] = useState<LiteralOperand>(defaultLiteral(type))
  const current = draft.type === type ? draft : defaultLiteral(type)
  // Every type keeps a draft and adds through the explicit action, so the
  // default of a select (true, today's date) can be added like any other.
  const add = () => {
    if (type === "text" && String(current.value).length === 0) return
    onChange([...values, current])
    setDraft(defaultLiteral(type))
  }
  return (
    <div className="grid gap-1.5" role="group" aria-label={ariaLabel}>
      <SelectField
        value={type}
        options={LIST_LITERAL_TYPES}
        onChange={(next) => {
          setChosenType(next)
          setDraft(defaultLiteral(next))
          if (values.length > 0) onChange([])
        }}
        ariaLabel={`${ariaLabel} type`}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        {values.map((item, index) => (
          <Chip
            key={`${index}-${String(item.value)}`}
            label={String(item.value)}
            removeLabel={`Remove ${String(item.value)}`}
            onRemove={() => onChange(values.filter((_, i) => i !== index))}
          />
        ))}
        <div className="flex-1 basis-28 min-w-0 flex items-center gap-1">
          <LiteralValueInput value={current} onChange={setDraft} ariaLabel={`${ariaLabel} new value`} />
          <button type="button" onClick={add} aria-label={`${ariaLabel}: add value`} className="add-row-btn focus-ring p-1 rounded" style={ADD_BUTTON_STYLE}>
            <Plus size={12} aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  )
}

const OPERATOR_OPTIONS = CONDITION_OPERATORS.map((o) => ({ value: o.value, label: o.label }))
const STRING_OPERATORS = new Set<Condition["operator"]>(["contains", "starts_with", "ends_with", "matches"])
const isStringOperator = (operator: Condition["operator"]) => STRING_OPERATORS.has(operator)

/** The value a condition on a column of `dtype` starts from; null leaves the value as it is. */
function valueForType(dtype: string | null): LiteralOperand | null {
  if (dtype === null) return null
  if (/^(String|Utf8|Categorical|Enum)/.test(dtype)) return defaultLiteral("text")
  if (/^(Date|Datetime)/.test(dtype)) return defaultLiteral("date")
  if (dtype === "Boolean") return defaultLiteral("boolean")
  return null
}

/** A condition value nobody has touched: the number 0 a new condition starts with. */
function isUntouchedValue(value: Operand | undefined): boolean {
  return value !== undefined && value.kind === "literal" && value.type === "number" && value.value === 0
}

/** Rows without their own border when a list has one row, so the simple case stays calm. */
function Row({ label, boxed, children }: { label: string; boxed: boolean; children: ReactNode }) {
  return boxed ? <RowGroup label={label}>{children}</RowGroup> : <div className="grid gap-1.5" role="group" aria-label={label}>{children}</div>
}

export function ConditionRow({
  condition,
  onChange,
  onRemove,
  columns,
  variables,
  ariaLabel,
  renderExpression,
  columnId,
  boxed = true,
}: {
  condition: Condition
  onChange: (next: Condition) => void
  onRemove?: () => void
  columns: string[]
  variables: string[]
  ariaLabel: string
  renderExpression?: RenderExpression
  /** Id for the column box, when it is the first field of its card. */
  columnId?: string
  boxed?: boolean
}) {
  const schema = useStepSchema()
  const takes = CONDITION_OPERATORS.find((o) => o.value === condition.operator)?.takes ?? "value"
  const textOnly = isStringOperator(condition.operator)
  // A fresh condition's value follows the chosen column's type; a value the
  // analyst has edited, or a text operator's value, is left alone.
  const setColumn = (column: string) => {
    const typed = takes === "value" && !textOnly && isUntouchedValue(condition.value) ? valueForType(schema.typeOf(column)) : null
    onChange(typed ? { ...condition, column, value: typed } : { ...condition, column })
  }
  const setOperator = (operator: Condition["operator"]) => {
    const nextTakes = CONDITION_OPERATORS.find((o) => o.value === operator)?.takes ?? "value"
    const next: Condition = { column: condition.column, operator }
    if (nextTakes === "value") {
      const keep = condition.value
      const textOk = keep && (keep.kind !== "literal" || keep.type === "text")
      next.value = isStringOperator(operator) ? (textOk ? keep : literal("text", "")) : (keep ?? literal("number", 0))
    }
    if (nextTakes === "values") next.values = condition.values ?? []
    onChange(next)
  }
  return (
    <Row label={ariaLabel} boxed={boxed}>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-28 min-w-0">
          <ColumnPicker
            value={condition.column}
            onCommit={setColumn}
            suggestions={columns}
            ariaLabel={`${ariaLabel} column`}
            id={columnId}
          />
        </div>
        <div className="flex-1 basis-32 min-w-0">
          <SelectField value={condition.operator} options={OPERATOR_OPTIONS} onChange={setOperator} ariaLabel={`${ariaLabel} operator`} />
        </div>
        {onRemove && <RowRemove label={`Remove ${ariaLabel}`} onClick={onRemove} />}
      </div>
      {takes === "value" && (
        <OperandField
          value={condition.value ?? literal("number", 0)}
          onChange={(value) => onChange({ ...condition, value })}
          sources={["literal", "column", "variable"]}
          literalTypes={textOnly ? ["text"] : ["number", "text", "boolean", "date"]}
          columns={columns}
          variables={variables}
          ariaLabel={`${ariaLabel} value`}
          renderExpression={renderExpression}
        />
      )}
      {takes === "values" && (
        <LiteralListField
          values={condition.values ?? []}
          onChange={(values) => onChange({ ...condition, values })}
          ariaLabel={`${ariaLabel} values`}
        />
      )}
    </Row>
  )
}

/**
 * Conditions joined by all/any, each row reading `[column] [operator]
 * [value]`, under a lead that says what they do: a filter keeps rows ("Keep
 * rows where [all] of these are true"); an if-then or an aggregation's row
 * filter tests them ("When [all] of these are true"). The match select sits in
 * the sentence, so it reads "any" when chosen.
 */
export function ConditionList({
  conditions,
  match,
  onChange,
  columns,
  variables,
  ariaLabel,
  renderExpression,
  lead,
  firstId,
}: {
  conditions: Condition[]
  match: MatchMode
  onChange: (conditions: Condition[], match: MatchMode) => void
  columns: string[]
  variables: string[]
  ariaLabel: string
  renderExpression?: RenderExpression
  lead: "keep" | "when"
  /** Id for the first condition's column box, when it is the first field of its card. */
  firstId?: string
}) {
  const opening = lead === "keep" ? "Keep rows where" : "When"
  return (
    <div className="grid gap-1.5">
      {conditions.length > 1 ? (
        <Sentence>
          <Words>{opening}</Words>
          <span className="w-16">
            <SelectField
              value={match}
              options={[
                { value: "all", label: "all" },
                { value: "any", label: "any" },
              ]}
              onChange={(next) => onChange(conditions, next)}
              ariaLabel={`${ariaLabel} match`}
            />
          </span>
          <Words>of these are true</Words>
        </Sentence>
      ) : (
        <Sentence>
          <Words>{opening}</Words>
        </Sentence>
      )}
      {conditions.map((condition, index) => (
        <ConditionRow
          key={index}
          condition={condition}
          columns={columns}
          variables={variables}
          ariaLabel={`${ariaLabel} condition ${index + 1}`}
          renderExpression={renderExpression}
          columnId={index === 0 ? firstId : undefined}
          boxed={conditions.length > 1}
          onChange={(next) => onChange(conditions.map((c, i) => (i === index ? next : c)), match)}
          onRemove={conditions.length > 1 ? () => onChange(conditions.filter((_, i) => i !== index), match) : undefined}
        />
      ))}
      <AddRow label="Add condition" onClick={() => onChange([...conditions, defaultCondition()], match)} />
    </div>
  )
}
