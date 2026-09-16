/**
 * Shared controls for step forms. Every text control commits on blur or
 * Enter; selects commit immediately. Column controls are comboboxes over the
 * suggested columns that accept free text. The operand control is
 * constrained per field (allowed sources and literal types) so a form can
 * only produce payloads the backend schema accepts.
 */
import { Plus, X } from "lucide-react"
import { useId, useState, type KeyboardEvent, type ReactNode } from "react"

import { CommittedTextField } from "../../../components/form"
import { INPUT_STYLE } from "../_shared"
import { CONDITION_OPERATORS, LITERAL_TYPES, defaultLiteral, literal } from "./catalogue"
import type { Condition, LiteralOperand, LiteralType, MatchMode, Operand } from "./types"

export const CONTROL_CLASS = "focus-ring w-full min-w-0 px-2 py-1.5 text-xs rounded-md"
const CHIP_STYLE = { background: "var(--chrome-hover)", color: "var(--text-primary)", border: "1px solid var(--border)" }

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

/** A combobox over the suggested columns that accepts any typed name. */
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
  const listId = useId()
  return (
    <>
      <CommittedTextField
        id={id}
        type="text"
        list={listId}
        aria-label={ariaLabel}
        value={value}
        onCommit={(next) => onCommit(next.trim())}
        placeholder={placeholder}
        autoFocus={autoFocus}
        className={`${CONTROL_CLASS} font-mono`}
        style={INPUT_STYLE}
      />
      <datalist id={listId}>
        {suggestions.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
    </>
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

/** An ordered list of column names as chips, with a picker to add one. */
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
  const listId = useId()
  const add = () => {
    const name = draft.trim()
    if (name && !columns.includes(name)) onChange([...columns, name])
    setDraft("")
  }
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault()
      add()
    }
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
      <input
        type="text"
        list={listId}
        aria-label={`${ariaLabel}: add`}
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={add}
        onKeyDown={onKeyDown}
        className={`${CONTROL_CLASS} font-mono flex-1 basis-28`}
        style={INPUT_STYLE}
      />
      <datalist id={listId}>
        {suggestions
          .filter((name) => !columns.includes(name))
          .map((name) => (
            <option key={name} value={name} />
          ))}
      </datalist>
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
  }
}

export type OperandSource = "literal" | "column" | "variable"
const SOURCE_LABELS: Record<OperandSource, string> = { literal: "Value", column: "Column", variable: "Variable" }

/**
 * One operand: a source select (only the allowed sources, Variable only when a
 * variable exists), then the matching input. Literal types are limited to
 * `literalTypes`; the type select appears only when more than one is allowed.
 */
export function OperandField({
  value,
  onChange,
  sources,
  literalTypes,
  columns,
  variables,
  ariaLabel,
}: {
  value: Operand
  onChange: (next: Operand) => void
  sources: OperandSource[]
  literalTypes: LiteralType[]
  columns: string[]
  variables: string[]
  ariaLabel: string
}) {
  const allowedSources = sources.filter((s) => s !== "variable" || variables.length > 0)
  const source: OperandSource = value.kind
  const setSource = (next: OperandSource) => {
    if (next === source) return
    if (next === "literal") onChange(defaultLiteral(literalTypes[0] ?? "number"))
    else if (next === "column") onChange({ kind: "column", name: columns[0] ?? "" })
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
  const add = (item: LiteralOperand) => {
    onChange([...values, item])
    setDraft(defaultLiteral(type))
  }
  return (
    <div className="grid gap-1.5" role="group" aria-label={ariaLabel}>
      <SelectField
        value={type}
        options={LITERAL_TYPES}
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
          <LiteralValueInput
            value={current}
            onChange={(item) => (item.type === "text" ? setDraft(item) : add(item))}
            ariaLabel={`${ariaLabel} new value`}
          />
          {type === "text" && (
            <button
              type="button"
              onClick={() => {
                if (String(current.value).length > 0) add(current)
              }}
              aria-label={`${ariaLabel}: add value`}
              className="add-row-btn focus-ring p-1 rounded"
              style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            >
              <Plus size={12} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

const OPERATOR_OPTIONS = CONDITION_OPERATORS.map((o) => ({ value: o.value, label: o.label }))
const STRING_OPERATORS = new Set<Condition["operator"]>(["contains", "starts_with", "ends_with"])
const isStringOperator = (operator: Condition["operator"]) => STRING_OPERATORS.has(operator)

export function ConditionRow({
  condition,
  onChange,
  onRemove,
  columns,
  variables,
  ariaLabel,
}: {
  condition: Condition
  onChange: (next: Condition) => void
  onRemove?: () => void
  columns: string[]
  variables: string[]
  ariaLabel: string
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
    <div className="grid gap-1.5 p-2 rounded-md" style={{ border: "1px solid var(--border-subtle)" }} role="group" aria-label={ariaLabel}>
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
        {onRemove && (
          <button type="button" onClick={onRemove} aria-label={`Remove ${ariaLabel}`} className="icon-danger-btn focus-ring p-1 rounded">
            <X size={12} aria-hidden="true" />
          </button>
        )}
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
        />
      )}
      {takes === "values" && (
        <LiteralListField
          values={condition.values ?? []}
          onChange={(values) => onChange({ ...condition, values })}
          ariaLabel={`${ariaLabel} values`}
        />
      )}
    </div>
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
}: {
  conditions: Condition[]
  match: MatchMode
  onChange: (conditions: Condition[], match: MatchMode) => void
  columns: string[]
  variables: string[]
  ariaLabel: string
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
          onChange={(next) => onChange(conditions.map((c, i) => (i === index ? next : c)), match)}
          onRemove={conditions.length > 1 ? () => onChange(conditions.filter((_, i) => i !== index), match) : undefined}
        />
      ))}
      <button
        type="button"
        onClick={() => onChange([...conditions, { column: columns[0] ?? "", operator: "eq", value: literal("number", 0) }], match)}
        className="add-row-btn focus-ring flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg justify-center"
        style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
      >
        <Plus size={12} aria-hidden="true" />
        Add condition
      </button>
    </div>
  )
}
