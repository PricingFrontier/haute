/**
 * Shared controls for step forms. Every text control commits on blur or
 * Enter; selects commit immediately. Column controls are comboboxes over the
 * suggested columns that accept free text. The operand control is
 * constrained per field (allowed sources and literal types) so a form can
 * only produce payloads the backend schema accepts. Row lists, bordered row
 * groups, direction and quantile controls live here too so every form lays
 * out its repeated rows the same way.
 */
import { Plus, X } from "lucide-react"
import { useState, type ReactNode } from "react"

import { CommittedTextField } from "../../../components/form"
import { INPUT_STYLE } from "../_shared"
import { CONDITION_OPERATORS, LIST_LITERAL_TYPES, LITERAL_TYPES, defaultCondition, defaultExpr, defaultLiteral, literal } from "./catalogue"
import { completionMatches } from "./completion"
import { useCompletionList } from "./useCompletionList"
import type { Condition, Expr, LiteralOperand, LiteralType, MatchMode, Operand } from "./types"

export const CONTROL_CLASS = "focus-ring w-full min-w-0 px-2 py-1.5 text-xs rounded-md"
const CHIP_STYLE = { background: "var(--chrome-hover)", color: "var(--text-primary)", border: "1px solid var(--border)" }
const ADD_BUTTON_STYLE = { color: "var(--text-secondary)", border: "1px solid var(--border)" }

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label
      htmlFor={htmlFor}
      className="block text-[10px] font-bold uppercase tracking-[0.08em] mb-1"
      style={{ color: "var(--text-muted)" }}
    >
      {children}
    </label>
  )
}

/** A stacked label + control block; the single-column grid unit of every form. */
export function Field({ label, children, htmlFor }: { label?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="min-w-0">
      {label !== undefined && <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>}
      {children}
    </div>
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
  return (
    <CommittedTextField
      id={id}
      type="number"
      inputMode={integer ? "numeric" : "decimal"}
      step={integer ? 1 : "any"}
      min={min}
      aria-label={ariaLabel}
      value={String(value)}
      onCommit={(text) => {
        const parsed = integer ? Number.parseInt(text, 10) : Number.parseFloat(text)
        if (Number.isFinite(parsed)) onCommit(parsed)
      }}
      className={`${CONTROL_CLASS} font-mono`}
      style={INPUT_STYLE}
    />
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

/** The full-width "add a row" action under a list. */
export function AddRow({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="add-row-btn focus-ring flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg justify-center"
      style={ADD_BUTTON_STYLE}
    >
      <Plus size={12} aria-hidden="true" />
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

/** The dropdown of completions under a text box; the box owns the keyboard. */
export function CompletionList({
  id,
  matches,
  activeIndex,
  onPick,
  onHover,
  label = "Matching columns",
}: {
  id: string
  matches: string[]
  activeIndex: number
  onPick: (name: string) => void
  onHover: (index: number) => void
  label?: string
}) {
  if (matches.length === 0) return null
  return (
    <ul
      id={id}
      role="listbox"
      aria-label={label}
      className="absolute left-0 right-0 z-20 mt-1 max-h-48 overflow-y-auto rounded-md p-1 shadow-lg"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-bright)" }}
    >
      {matches.map((name, index) => (
        <li
          key={name}
          id={`${id}-${index}`}
          role="option"
          aria-selected={index === activeIndex}
          onMouseDown={(event) => {
            event.preventDefault()
            onPick(name)
          }}
          onMouseEnter={() => onHover(index)}
          className="cursor-pointer rounded px-2 py-1 text-xs font-mono"
          style={{ color: "var(--text-primary)", background: index === activeIndex ? "var(--chrome-hover)" : "transparent" }}
        >
          {name}
        </li>
      ))}
    </ul>
  )
}

/**
 * A text box that completes column names: the names starting with what is
 * typed are listed beneath (every name while the box is empty), Up/Down move
 * through them, Tab or a click completes the name, Escape closes the list,
 * and Enter or leaving the box commits what was typed.
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
}) {
  const completion = useCompletionList(completionMatches(suggestions, draft.trim(), exclude))
  const accept = (name: string) => {
    completion.hide()
    onAccept(name)
  }
  return (
    <div className={`relative ${className}`}>
      <input
        id={id}
        type="text"
        aria-label={ariaLabel}
        value={draft}
        placeholder={placeholder}
        autoFocus={autoFocus}
        {...completion.inputProps}
        onChange={(event) => {
          onDraftChange(event.target.value)
          completion.show()
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
        style={INPUT_STYLE}
      />
      <CompletionList {...completion.listProps} onPick={accept} />
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
}: {
  value: string
  onCommit: (next: string) => void
  suggestions: string[]
  ariaLabel: string
  placeholder?: string
  id?: string
  autoFocus?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  if (value !== seen) {
    setSeen(value)
    setDraft(value)
  }
  const commit = (next: string) => {
    const trimmed = next.trim()
    if (trimmed !== value) onCommit(trimmed)
  }
  return (
    <CompletingInput
      draft={draft}
      onDraftChange={setDraft}
      onCommit={commit}
      onAccept={(name) => {
        setDraft(name)
        commit(name)
      }}
      suggestions={suggestions}
      ariaLabel={ariaLabel}
      placeholder={placeholder}
      id={id}
      autoFocus={autoFocus}
    />
  )
}

function Chip({ label, onRemove, removeLabel }: { label: string; onRemove: () => void; removeLabel: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-mono" style={CHIP_STYLE}>
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
}: {
  columns: string[]
  onChange: (next: string[]) => void
  suggestions: string[]
  ariaLabel: string
  placeholder?: string
}) {
  const [draft, setDraft] = useState("")
  const add = (raw: string) => {
    const name = raw.trim()
    if (name && !columns.includes(name)) onChange([...columns, name])
    setDraft("")
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={ariaLabel}>
      {columns.map((name) => (
        <Chip
          key={name}
          label={name}
          removeLabel={`Remove ${name}`}
          onRemove={() => onChange(columns.filter((c) => c !== name))}
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
        className="flex-1 basis-28 min-w-0"
      />
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
const SOURCE_LABELS: Record<OperandSource, string> = { literal: "Value", column: "Column", variable: "Variable", expr: "Expression" }

/** Renders a nested expression editor for an operand; supplied by the forms so fields need not import them. */
export type RenderExpression = (expr: Expr, onChange: (next: Expr) => void, ariaLabel: string) => ReactNode

/**
 * One operand: a source select (only the allowed sources, Variable only when a
 * variable exists, Expression only when `renderExpression` is given), then
 * the matching input. Literal types are limited to `literalTypes`; the type
 * select appears only when more than one is allowed.
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
  const allowedSources: OperandSource[] = [
    ...sources.filter((s) => s !== "variable" || variables.length > 0),
    ...(renderExpression ? (["expr"] as const) : []),
  ]
  const source: OperandSource = value.kind
  const setSource = (next: OperandSource) => {
    if (next === source) return
    if (next === "literal") onChange(defaultLiteral(literalTypes[0] ?? "number"))
    else if (next === "column") onChange({ kind: "column", name: "" })
    else if (next === "expr") onChange({ kind: "expr", expr: defaultExpr("binary") })
    else onChange({ kind: "variable", name: variables[0] ?? "" })
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={ariaLabel}>
      {allowedSources.length > 1 && (
        <SelectField
          value={source}
          options={allowedSources.map((s) => ({ value: s, label: SOURCE_LABELS[s] }))}
          onChange={setSource}
          ariaLabel={`${ariaLabel} source`}
          className="basis-24 grow-0"
        />
      )}
      {value.kind === "literal" && literalTypes.length > 1 && (
        <SelectField
          value={value.type}
          options={LITERAL_TYPES.filter((t) => literalTypes.includes(t.value))}
          onChange={(type) => onChange(defaultLiteral(type))}
          ariaLabel={`${ariaLabel} type`}
          className="basis-24 grow-0"
        />
      )}
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

export function ConditionRow({
  condition,
  onChange,
  onRemove,
  columns,
  variables,
  ariaLabel,
  renderExpression,
}: {
  condition: Condition
  onChange: (next: Condition) => void
  onRemove?: () => void
  columns: string[]
  variables: string[]
  ariaLabel: string
  renderExpression?: RenderExpression
}) {
  const takes = CONDITION_OPERATORS.find((o) => o.value === condition.operator)?.takes ?? "value"
  const textOnly = isStringOperator(condition.operator)
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
    <RowGroup label={ariaLabel}>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-28 min-w-0">
          <ColumnPicker
            value={condition.column}
            onCommit={(column) => onChange({ ...condition, column })}
            suggestions={columns}
            ariaLabel={`${ariaLabel} column`}
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
    </RowGroup>
  )
}

/** Conditions joined by all/any, each row reading `[column] [operator] [value]`. */
export function ConditionList({
  conditions,
  match,
  onChange,
  columns,
  variables,
  ariaLabel,
  renderExpression,
}: {
  conditions: Condition[]
  match: MatchMode
  onChange: (conditions: Condition[], match: MatchMode) => void
  columns: string[]
  variables: string[]
  ariaLabel: string
  renderExpression?: RenderExpression
}) {
  return (
    <div className="grid gap-1.5">
      {conditions.length > 1 && (
        <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
          <span>Keep rows matching</span>
          <SelectField
            value={match}
            options={[
              { value: "all", label: "all conditions" },
              { value: "any", label: "any condition" },
            ]}
            onChange={(next) => onChange(conditions, next)}
            ariaLabel={`${ariaLabel} match`}
            className="basis-32 grow-0"
          />
        </div>
      )}
      {conditions.map((condition, index) => (
        <ConditionRow
          key={index}
          condition={condition}
          columns={columns}
          variables={variables}
          ariaLabel={`${ariaLabel} condition ${index + 1}`}
          renderExpression={renderExpression}
          onChange={(next) => onChange(conditions.map((c, i) => (i === index ? next : c)), match)}
          onRemove={conditions.length > 1 ? () => onChange(conditions.filter((_, i) => i !== index), match) : undefined}
        />
      ))}
      <AddRow label="Add condition" onClick={() => onChange([...conditions, defaultCondition()], match)} />
    </div>
  )
}
