/**
 * When a sheet, a section or a component shows (specs/workbench): the conditions in its
 * panel, and the eye it carries on the sheet while building. Conditions test the values typed into
 * input tables, each a row of its column, for a many-row table's column which rows, its test and
 * its value, the control the column's cell is, or count a many-row table's rows; a group holds
 * conditions of its own; with two or more, all of them must hold, any, or at least a number.
 * Every change is handed up whole, so the panel commits it as one undo step.
 */
import { Eye, EyeOff, X } from "lucide-react"
import type { CSSProperties } from "react"
import type { ConditionGroup, FormSpec, RowCount, RowsCondition, SchemaColumn, SchemaTable, ShowWhen } from "../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, QuietAction, ValidatedTextField, wholeNumber } from "../components/form"
import {
  CONDITION_TESTS,
  COUNT_TESTS,
  conditionFault,
  isGroup,
  isRowCount,
  isRowsCondition,
  newCondition,
  newRowCount,
  testableColumns,
  testLabel,
  testsFor,
  withConditionColumn,
  withConditionRows,
  withConditionTest,
  type ColumnCondition,
  type ConditionTest,
  type CountTest,
  type ShowWhenEntry,
  type SingleCondition,
} from "../utils/sheetVisibility"
import { fieldColumn, readableName } from "../utils/workbenchForm"
import { COLUMN_TYPES } from "./columnTypes"

type Match = ShowWhen["match"]

const MATCHES: ReadonlyArray<{ value: "all" | "any" | "at_least"; label: string }> = [
  { value: "all", label: "all" },
  { value: "any", label: "any" },
  { value: "at_least", label: "at least" },
]
/** The number "at least" starts at. */
const AT_LEAST = 2

const ROWS: ReadonlyArray<{ value: RowsCondition["rows"]; label: string }> = [
  { value: "any", label: "Any row" },
  { value: "every", label: "Every row" },
]

const columnKey = (tableId: string, columnId: string) => `${tableId}:${columnId}`
/** Number of rows' place in the column select, never a column's key, whose `:` between its ids no id holds. */
const countKey = (tableId: string) => `${tableId}#rows`
const label = (column: SchemaColumn) => column.label || readableName(column.name)
const countValid = wholeNumber({ min: 0, unit: "rows" })

/** The tables a condition can test: the input tables with a column to test. */
const testable = (form: FormSpec): SchemaTable[] => form.schema.tables.filter((table) => testableColumns(table).length > 0)

/** Whether a Show when's or a group's match sentence shows: with two or more conditions, or one while its match is a number. */
const sentenceShown = (match: Match, count: number) => count > 1 || (count === 1 && typeof match === "number")

/**
 * "[all|any|at least] of these are true", the match a select in the sentence, and at least's
 * number a select of 1 to as many conditions as there are, or to the number while it asks for more.
 */
function MatchSentence({
  match,
  count,
  lead,
  of,
  onChange,
}: {
  match: Match
  count: number
  lead: string | null
  /** Whose conditions, after "conditions" in the selects' names: "" or " of group 2". */
  of: string
  onChange: (match: Match) => void
}) {
  const numbers = Array.from({ length: Math.max(count, typeof match === "number" ? match : 0) }, (_, at) => at + 1)
  return (
    <div className="flex flex-wrap items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
      {lead !== null && <span>{lead}</span>}
      <select
        aria-label={`Which conditions${of} must hold`}
        value={typeof match === "number" ? "at_least" : match}
        onChange={(event) => {
          const next = event.target.value
          onChange(next === "at_least" ? AT_LEAST : (next as "all" | "any"))
        }}
        className={`${FIELD_CLASS} w-20`}
        style={INPUT_STYLE}
      >
        {MATCHES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {typeof match === "number" && (
        <select
          aria-label={`How many conditions${of} must hold`}
          value={match}
          onChange={(event) => onChange(Number(event.target.value))}
          className={`${FIELD_CLASS} w-14 tabular-nums`}
          style={INPUT_STYLE}
        >
          {numbers.map((number) => (
            <option key={number} value={number}>
              {number}
            </option>
          ))}
        </select>
      )}
      <span>of these are true</span>
    </div>
  )
}

export function ShowWhenEditor({
  form,
  showWhen,
  onChange,
}: {
  form: FormSpec
  showWhen: ShowWhen
  onChange: (next: ShowWhen) => void
}) {
  const { conditions: entries, match } = showWhen
  const fresh = newCondition(form)
  const setEntries = (next: ShowWhenEntry[]) => onChange({ match, conditions: next })
  const replace = (index: number, entry: ShowWhenEntry) => setEntries(entries.map((current, at) => (at === index ? entry : current)))
  const remove = (index: number) => setEntries(entries.filter((_, at) => at !== index))

  return (
    <div className="flex flex-col gap-1.5">
      {entries.length === 0 && <span style={{ color: "var(--text-muted)" }}>Always</span>}
      {sentenceShown(match, entries.length) && (
        <MatchSentence match={match} count={entries.length} lead="Show when" of="" onChange={(next) => onChange({ match: next, conditions: entries })} />
      )}
      {entries.map((entry, index) =>
        // An entry has no id of its own; its place in the list is what the panel edits.
        isGroup(entry) ? (
          <GroupFrame
            key={index}
            form={form}
            group={entry}
            name={`Group ${index + 1}`}
            onChange={(next) => replace(index, next)}
            onRemove={() => remove(index)}
          />
        ) : (
          <ConditionRow
            key={index}
            form={form}
            condition={entry}
            name={`Condition ${index + 1}`}
            onChange={(next) => replace(index, next)}
            onRemove={() => remove(index)}
          />
        ),
      )}
      <div className="flex flex-wrap items-center gap-2">
        <QuietAction
          disabled={fresh === null}
          onClick={() => {
            if (fresh !== null) setEntries([...entries, fresh])
          }}
          className="self-start"
        >
          Add condition
        </QuietAction>
        <QuietAction
          disabled={fresh === null}
          onClick={() => {
            // A group needing what the Show when needs would add nothing: it needs the other, all under at least.
            if (fresh !== null) setEntries([...entries, { match: match === "all" ? "any" : "all", conditions: [fresh] }])
          }}
          className="self-start"
        >
          Add group
        </QuietAction>
        {fresh === null && (
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            Conditions test input tables; the schema has none with a column to test.
          </span>
        )}
      </div>
    </div>
  )
}

/** A group's conditions in a frame of their own, with its match, its own Add condition and a cross; removing its last condition removes it. */
function GroupFrame({
  form,
  group,
  name,
  onChange,
  onRemove,
}: {
  form: FormSpec
  group: ConditionGroup
  name: string
  onChange: (next: ConditionGroup) => void
  onRemove: () => void
}) {
  const fresh = newCondition(form)
  const setConditions = (next: SingleCondition[]) => (next.length === 0 ? onRemove() : onChange({ match: group.match, conditions: next }))
  const lower = name.toLowerCase()

  return (
    <div
      role="group"
      aria-label={name}
      className="flex flex-col gap-1.5 rounded-md px-2 py-1.5"
      style={{ border: "1px dashed var(--border)" }}
    >
      <div className="flex items-start gap-1.5">
        <div className="min-w-0 flex-1">
          {sentenceShown(group.match, group.conditions.length) && (
            <MatchSentence
              match={group.match}
              count={group.conditions.length}
              lead={null}
              of={` of ${lower}`}
              onChange={(next) => onChange({ match: next, conditions: group.conditions })}
            />
          )}
        </div>
        <button type="button" onClick={onRemove} aria-label={`Remove ${lower}`} title="Remove this group" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      {group.conditions.map((condition, index) => (
        <ConditionRow
          key={index}
          form={form}
          condition={condition}
          name={`${name} condition ${index + 1}`}
          onChange={(next) => setConditions(group.conditions.map((current, at) => (at === index ? next : current)))}
          onRemove={() => setConditions(group.conditions.filter((_, at) => at !== index))}
        />
      ))}
      <QuietAction
        aria-label={`Add condition to ${lower}`}
        disabled={fresh === null}
        onClick={() => {
          if (fresh !== null) setConditions([...group.conditions, fresh])
        }}
        className="self-start"
      >
        Add condition
      </QuietAction>
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
  condition: SingleCondition
  name: string
  onChange: (next: SingleCondition) => void
  onRemove: () => void
}) {
  const tables = testable(form)
  const counting = isRowCount(condition)
  const found = counting ? null : fieldColumn(form, condition)
  const table = form.schema.tables.find((candidate) => candidate.id === condition.table)
  const key = counting ? countKey(condition.table) : columnKey(condition.table, condition.column)
  // What each place in the column select makes of the condition.
  const choices = new Map<string, () => SingleCondition>()
  for (const candidate of tables) {
    if (candidate.rows === "many") choices.set(countKey(candidate.id), () => (counting && condition.table === candidate.id ? condition : newRowCount(candidate.id)))
    for (const column of testableColumns(candidate)) {
      choices.set(columnKey(candidate.id, column.id), () => withConditionColumn(form, condition, candidate.id, column.id))
    }
  }
  const offered = choices.has(key)
  const type = counting ? "int" : (found?.column.type ?? "str")
  const fault = conditionFault(form, condition)
  const marker = counting ? COLUMN_TYPES.int : found === null ? null : COLUMN_TYPES[found.column.type]
  const missing = counting ? (table === undefined ? "Missing table" : `Number of rows in ${table.name}`) : found === null ? "Missing column" : label(found.column)

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
          value={key}
          onChange={(event) => {
            const choose = choices.get(event.target.value)
            if (choose === undefined) throw new Error(`"${event.target.value}" is not a column a condition can test`)
            onChange(choose())
          }}
          className={`${FIELD_CLASS} flex-1`}
          style={INPUT_STYLE}
        >
          {!offered && (
            <option value={key} disabled>
              {missing}
            </option>
          )}
          {tables.map((candidate) => (
            <optgroup key={candidate.id} label={candidate.name}>
              {candidate.rows === "many" && <option value={countKey(candidate.id)}>Number of rows in {candidate.name}</option>}
              {testableColumns(candidate).map((column) => (
                <option key={column.id} value={columnKey(candidate.id, column.id)}>
                  {label(column)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {!counting && table !== undefined && (table.rows === "many" || isRowsCondition(condition)) && (
          <RowsSelect condition={condition} oneRow={table.rows === "one"} name={name} onChange={onChange} />
        )}
        <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this condition" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="flex items-start gap-1.5">
        <span className="size-4 shrink-0" aria-hidden="true" />
        {counting ? (
          <CountFields condition={condition} name={name} onChange={onChange} />
        ) : (
          <ColumnFields
            form={form}
            condition={condition}
            column={found?.column ?? null}
            tests={found === null ? [condition.op] : testsFor(type)}
            type={type}
            name={name}
            onChange={onChange}
          />
        )}
      </div>
      {fault !== null && (
        <span className="text-[11px]" style={{ color: "var(--warning)" }}>
          {fault.charAt(0).toUpperCase() + fault.slice(1)}
        </span>
      )}
    </div>
  )
}

/**
 * Which rows a column's condition tests: Any row or Every row for a many-row table's
 * column; while its table has switched kinds since, "Choose…" for a one-row condition on
 * a many-row table, and The row too for one in any or every row on a one-row table.
 */
function RowsSelect({
  condition,
  oneRow,
  name,
  onChange,
}: {
  condition: ColumnCondition
  oneRow: boolean
  name: string
  onChange: (next: ColumnCondition) => void
}) {
  return (
    <select
      aria-label={`${name} rows`}
      value={isRowsCondition(condition) ? condition.rows : "one"}
      onChange={(event) => onChange(withConditionRows(condition, event.target.value as RowsCondition["rows"] | "one"))}
      className={`${FIELD_CLASS} w-24 shrink-0`}
      style={INPUT_STYLE}
    >
      {oneRow ? (
        <option value="one">The row</option>
      ) : (
        !isRowsCondition(condition) && (
          <option value="one" disabled>
            Choose…
          </option>
        )
      )}
      {ROWS.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

/** A column's condition's test and its value. */
function ColumnFields({
  form,
  condition,
  column,
  tests,
  type,
  name,
  onChange,
}: {
  form: FormSpec
  condition: ColumnCondition
  column: SchemaColumn | null
  tests: readonly ConditionTest[]
  type: SchemaColumn["type"]
  name: string
  onChange: (next: ColumnCondition) => void
}) {
  return (
    <>
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
      <ConditionValue condition={condition} column={column} name={name} onChange={(values) => onChange({ ...condition, values })} />
    </>
  )
}

/** A count's test, a number's, and its whole number of rows, anything else refused at the field. */
function CountFields({ condition, name, onChange }: { condition: RowCount; name: string; onChange: (next: RowCount) => void }) {
  return (
    <>
      <select
        aria-label={`${name} test`}
        value={condition.op}
        onChange={(event) => onChange({ ...condition, op: event.target.value as CountTest })}
        className={`${FIELD_CLASS} w-32 shrink-0`}
        style={INPUT_STYLE}
      >
        {COUNT_TESTS.map((test) => (
          <option key={test} value={test}>
            {testLabel(test, "int")}
          </option>
        ))}
      </select>
      <ValidatedTextField
        dataTestId={`${name}-count`}
        ariaLabel={`${name} value`}
        value={String(condition.count)}
        validate={countValid}
        onCommit={(text) => onChange({ ...condition, count: Number(text) })}
        containerClassName="min-w-0 flex-1"
        className={`${FIELD_CLASS} w-full tabular-nums`}
        style={INPUT_STYLE}
      />
    </>
  )
}

/** A condition's value, the control its column's cell is; a box per allowed value for one of many. */
function ConditionValue({
  condition,
  column,
  name,
  onChange,
}: {
  condition: ColumnCondition
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
    // Committed on blur or Enter: a year typed digit by digit is one change.
    return <CommittedTextField type="date" aria-label={aria} value={value} onCommit={one} className={`${FIELD_CLASS} flex-1`} style={INPUT_STYLE} />
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
