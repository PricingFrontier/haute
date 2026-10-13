/**
 * When a sheet, a section or a component shows (specs/workbench): the conditions in its
 * panel, and the eye it carries on the sheet while building. Conditions test the values typed into
 * one-row input tables, each a row of its column, its test and its value, the control the
 * column's cell is; with two or more, all of them must hold or any. Every change is handed
 * up whole, so the panel commits it as one undo step.
 */
import { Eye, EyeOff, Plus, X } from "lucide-react"
import type { CSSProperties } from "react"
import type { Condition, FormSpec, SchemaColumn, SchemaTable, ShowWhen } from "../api/types"
import { CommittedTextField, ConfigCheckbox } from "../components/form"
import { INPUT_STYLE } from "../panels/editors/_shared"
import {
  CONDITION_TESTS,
  conditionFault,
  newCondition,
  testLabel,
  testsFor,
  withConditionColumn,
  withConditionTest,
  type ConditionTest,
} from "../utils/sheetVisibility"
import { fieldColumn, readableName } from "../utils/workbenchForm"
import { COLUMN_TYPES } from "./columnTypes"

const FIELD_CLASS = "focus-ring min-w-0 rounded-md px-1.5 py-1 text-xs"
const MATCHES: ReadonlyArray<{ value: ShowWhen["match"]; label: string }> = [
  { value: "all", label: "all" },
  { value: "any", label: "any" },
]

const columnKey = (tableId: string, columnId: string) => `${tableId}:${columnId}`
const label = (column: SchemaColumn) => column.label || readableName(column.name)

/** The columns a condition can test: those of the one-row input tables, by table. */
const testable = (form: FormSpec): SchemaTable[] =>
  form.schema.tables.filter((table) => table.role === "input" && table.rows === "one" && table.columns.length > 0)

export function ShowWhenEditor({
  form,
  showWhen,
  onChange,
}: {
  form: FormSpec
  showWhen: ShowWhen
  onChange: (next: ShowWhen) => void
}) {
  const { conditions, match } = showWhen
  const fresh = newCondition(form)
  const setConditions = (next: Condition[]) => onChange({ match, conditions: next })
  const replace = (index: number, condition: Condition) =>
    setConditions(conditions.map((current, at) => (at === index ? condition : current)))

  return (
    <div className="flex flex-col gap-1.5">
      {conditions.length === 0 && <span style={{ color: "var(--text-muted)" }}>Always</span>}
      {conditions.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
          <span>Show when</span>
          <select
            aria-label="Which conditions must hold"
            value={match}
            onChange={(event) => onChange({ match: event.target.value as ShowWhen["match"], conditions })}
            className={`${FIELD_CLASS} w-16`}
            style={INPUT_STYLE}
          >
            {MATCHES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span>of these are true</span>
        </div>
      )}
      {conditions.map((condition, index) => (
        <ConditionRow
          // A condition has no id of its own; its place in the list is what the panel edits.
          key={index}
          form={form}
          condition={condition}
          name={`Condition ${index + 1}`}
          onChange={(next) => replace(index, next)}
          onRemove={() => setConditions(conditions.filter((_, at) => at !== index))}
        />
      ))}
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={fresh === null}
          onClick={() => {
            if (fresh !== null) setConditions([...conditions, fresh])
          }}
          className="quiet-action focus-ring inline-flex items-center gap-1 self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium disabled:opacity-50"
        >
          <Plus size={11} aria-hidden="true" />
          Add condition
        </button>
        {fresh === null && (
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Conditions test one-row input tables; the schema has none with a column.
          </span>
        )}
      </div>
    </div>
  )
}

function ConditionRow({
  form,
  condition,
  name,
  onChange,
  onRemove,
}: {
  form: FormSpec
  condition: Condition
  name: string
  onChange: (next: Condition) => void
  onRemove: () => void
}) {
  const found = fieldColumn(form, condition)
  const tables = testable(form)
  const offered = tables.some((table) => table.id === condition.table && table.columns.some((column) => column.id === condition.column))
  const type = found?.column.type ?? "str"
  const tests = found === null ? [condition.op] : testsFor(type)
  const fault = conditionFault(form, condition)
  const marker = found === null ? null : COLUMN_TYPES[found.column.type]

  return (
    <div
      role="group"
      aria-label={name}
      className="flex flex-col gap-1 rounded-md px-2 py-1.5"
      style={{ border: "1px solid var(--border)", background: "var(--bg-base)" }}
    >
      <div className="flex items-center gap-1.5">
        <span title={marker?.label} className={`flex size-4 shrink-0 items-center justify-center ${marker?.color ?? ""}`}>
          {marker !== null && <marker.icon size={12} aria-hidden="true" />}
        </span>
        <select
          aria-label={`${name} column`}
          value={columnKey(condition.table, condition.column)}
          onChange={(event) => {
            const [tableId, columnId] = event.target.value.split(":")
            onChange(withConditionColumn(form, condition, tableId, columnId))
          }}
          className={`${FIELD_CLASS} flex-1`}
          style={INPUT_STYLE}
        >
          {!offered && (
            <option value={columnKey(condition.table, condition.column)} disabled>
              {found === null ? "Missing column" : label(found.column)}
            </option>
          )}
          {tables.map((table) => (
            <optgroup key={table.id} label={table.name}>
              {table.columns.map((column) => (
                <option key={column.id} value={columnKey(table.id, column.id)}>
                  {label(column)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this condition" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="flex items-start gap-1.5">
        <span className="size-4 shrink-0" aria-hidden="true" />
        <select
          aria-label={`${name} test`}
          value={condition.op}
          onChange={(event) => onChange(withConditionTest(form, condition, event.target.value as ConditionTest))}
          className={`${FIELD_CLASS} w-32 shrink-0`}
          style={INPUT_STYLE}
        >
          {(tests.includes(condition.op) ? tests : [condition.op, ...tests]).map((test) => (
            <option key={test} value={test}>
              {testLabel(test, type)}
            </option>
          ))}
        </select>
        <ConditionValue condition={condition} column={found?.column ?? null} name={name} onChange={(values) => onChange({ ...condition, values })} />
      </div>
      {fault !== null && (
        <span className="text-[11px]" style={{ color: "var(--warning)" }}>
          {fault.charAt(0).toUpperCase() + fault.slice(1)}
        </span>
      )}
    </div>
  )
}

/** A condition's value, the control its column's cell is; a box per allowed value for one of many. */
function ConditionValue({
  condition,
  column,
  name,
  onChange,
}: {
  condition: Condition
  column: SchemaColumn | null
  name: string
  onChange: (values: string[]) => void
}) {
  const takes = CONDITION_TESTS[condition.op].values
  if (takes === "none") return null
  const options = column?.options ?? []
  const aria = `${name} value`
  if (takes === "some") {
    if (options.length > 0) {
      return (
        <div role="group" aria-label={aria} className="flex min-w-0 flex-1 flex-wrap gap-x-3 gap-y-0.5 py-1">
          {options.map((option) => (
            <ConfigCheckbox
              key={option}
              label={option}
              checked={condition.values.includes(option)}
              onChange={(checked) =>
                onChange(options.filter((value) => (value === option ? checked : condition.values.includes(value))))
              }
            />
          ))}
        </div>
      )
    }
    return (
      <CommittedTextField
        aria-label={aria}
        value={condition.values.join(", ")}
        placeholder="Values, comma-separated"
        onCommit={(text) => onChange(text.split(",").map((value) => value.trim()).filter((value) => value !== ""))}
        className={`${FIELD_CLASS} flex-1`}
        style={INPUT_STYLE}
      />
    )
  }
  const value = condition.values[0] ?? ""
  const one = (text: string) => onChange(text === "" ? [] : [text])
  if (options.length > 0 && (condition.op === "equals" || condition.op === "not_equals")) {
    return (
      <select aria-label={aria} value={value} onChange={(event) => one(event.target.value)} className={`${FIELD_CLASS} flex-1`} style={INPUT_STYLE}>
        <option value="">Choose…</option>
        {!options.includes(value) && value !== "" && <option value={value}>{value}</option>}
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  if (column?.type === "date") {
    return <input type="date" aria-label={aria} value={value} onChange={(event) => one(event.target.value)} className={`${FIELD_CLASS} flex-1`} style={INPUT_STYLE} />
  }
  return (
    <CommittedTextField
      aria-label={aria}
      value={value}
      placeholder="Value"
      inputMode={column?.type === "int" || column?.type === "float" ? "decimal" : undefined}
      onCommit={(text) => one(text.trim())}
      className={`${FIELD_CLASS} flex-1`}
      style={INPUT_STYLE}
    />
  )
}

/**
 * The eye a sheet's tab, a section's header and a component carry while building: open
 * while a new quote shows them, struck through while it hides them, naming their
 * conditions on hover.
 */
export function ShowWhenMark({
  hidden,
  title,
  className = "",
  style,
}: {
  hidden: boolean
  title: string
  className?: string
  style?: CSSProperties
}) {
  const Icon = hidden ? EyeOff : Eye
  const text = hidden ? `Hidden for a new quote. ${title}` : title
  return (
    <span
      role="img"
      aria-label={text}
      title={text}
      className={`flex shrink-0 items-center ${className}`.trim()}
      style={{ color: hidden ? "var(--text-muted)" : "var(--text-secondary)", ...style }}
    >
      <Icon size={11} aria-hidden="true" />
    </span>
  )
}
