/**
 * When a sheet, a section or a component shows (specs/workbench): the conditions in its
 * panel, and the eye it carries on the sheet while building. Conditions test the values typed into
 * one-row input tables, each a row of its column, its test and its value, the control the
 * column's cell is, or a many-row table's rows: which rows, any, every, how many or a total
 * of a column over them, and the tests a row must pass; a group holds conditions of its
 * own; with two or more, all of them must hold, any, or at least a number. Every change is
 * handed up whole, so the panel commits it as one undo step.
 */
import { Eye, EyeOff, List, X } from "lucide-react"
import type { CSSProperties } from "react"
import type { Condition, ConditionGroup, FormSpec, RowsCondition, RowTest, SchemaColumn, SchemaTable, ShowWhen } from "../api/types"
import { CommittedTextField, ConfigCheckbox, FIELD_CLASS, INPUT_STYLE, QuietAction, ValidatedTextField, wholeNumber } from "../components/form"
import { parseTypedNumber } from "../utils/sheetValues"
import {
  CONDITION_TESTS,
  COUNT_TESTS,
  conditionFault,
  isGroup,
  isListCondition,
  isRowCount,
  isRowsCondition,
  isRowTotal,
  newCondition,
  newRowTest,
  testableColumns,
  testLabel,
  testsFor,
  TOTALS,
  totalColumns,
  withConditionColumn,
  withConditionRowsOf,
  withConditionTest,
  withListKind,
  withTestColumn,
  type ColumnTest,
  type ConditionTest,
  type CountTest,
  type ListCondition,
  type ShowWhenEntry,
  type SingleCondition,
  type TotalKind,
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

type ListKind = RowsCondition["rows"] | "count" | TotalKind

/** Which rows a condition on a table's rows takes, in the order its select offers them. */
const KINDS: ReadonlyArray<{ value: ListKind; label: string }> = [
  { value: "any", label: "Any row" },
  { value: "every", label: "Every row" },
  { value: "count", label: "Number of rows" },
  ...TOTALS,
]

const columnKey = (tableId: string, columnId: string) => `${tableId}:${columnId}`
/** A table's rows' place in the column select, never a column's key, whose `:` between its ids no id holds. */
const rowsKey = (tableId: string) => `${tableId}#rows`
const label = (column: SchemaColumn) => column.label || readableName(column.name)
const countValid = wholeNumber({ min: 0, unit: "rows" })
/** A total's number: blank, which its fault names, or a number as the sample's are typed. */
const numberValid = (text: string): string | null => (text.trim() === "" || parseTypedNumber(text.trim()) !== null ? null : "Enter a number.")

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
  const list = isListCondition(condition)
  const table = form.schema.tables.find((candidate) => candidate.id === condition.table)
  const found = list ? null : fieldColumn(form, condition)
  const key = list ? rowsKey(condition.table) : columnKey(condition.table, condition.column)
  // What each place in the column select makes of the condition: a one-row table's column, or a many-row table's rows.
  const choices = new Map<string, () => SingleCondition>()
  for (const candidate of tables) {
    if (candidate.rows === "many") {
      choices.set(rowsKey(candidate.id), () => withConditionRowsOf(form, condition, candidate.id))
      continue
    }
    for (const column of testableColumns(candidate)) {
      choices.set(columnKey(candidate.id, column.id), () => withConditionColumn(form, condition, candidate.id, column.id))
    }
  }
  const offered = choices.has(key)
  const fault = conditionFault(form, condition)
  const marker = list ? ROWS_MARKER : found === null ? null : COLUMN_TYPES[found.column.type]
  const missing = list ? (table === undefined ? "Missing table" : `Rows of ${table.name}`) : found === null ? "Missing column" : label(found.column)

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
          className={`${FIELD_CLASS} min-w-0 flex-1`}
          style={INPUT_STYLE}
        >
          {!offered && (
            <option value={key} disabled>
              {missing}
            </option>
          )}
          {tables.map((candidate) => (
            <optgroup key={candidate.id} label={candidate.name}>
              {candidate.rows === "many" ? (
                <option value={rowsKey(candidate.id)}>Rows of {candidate.name}</option>
              ) : (
                testableColumns(candidate).map((column) => (
                  <option key={column.id} value={columnKey(candidate.id, column.id)}>
                    {label(column)}
                  </option>
                ))
              )}
            </optgroup>
          ))}
        </select>
        <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this condition" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="flex items-start gap-1.5">
        <span className="size-4 shrink-0" aria-hidden="true" />
        {list ? (
          <ListFields form={form} table={table} condition={condition} name={name} onChange={onChange} />
        ) : (
          <ColumnFields column={found?.column ?? null} test={condition} name={name} onChange={(next: Condition) => onChange(next)} />
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

/** The mark a condition on a table's rows carries where a column's condition carries its type. */
const ROWS_MARKER = { label: "Rows", icon: List, color: "" }

/**
 * A condition on a table's rows: which rows, a total's number column beside them, the
 * tests a row must pass, each with its column, test, value and cross (none on the only one
 * any or every row needs), Add test, and a count's or a total's test and number.
 */
function ListFields({
  form,
  table,
  condition,
  name,
  onChange,
}: {
  form: FormSpec
  table: SchemaTable | undefined
  condition: ListCondition
  name: string
  onChange: (next: ListCondition) => void
}) {
  const columns = table === undefined ? [] : testableColumns(table)
  const numbers = table === undefined ? [] : totalColumns(table)
  const kind: ListKind = isRowsCondition(condition) ? condition.rows : isRowCount(condition) ? "count" : condition.total
  const setWhere = (where: RowTest[]) => onChange({ ...condition, where })
  // Any row and Every row need a test, a total a number column.
  const unavailable = (option: ListKind) =>
    option === "any" || option === "every" ? condition.where.length === 0 && columns.length === 0 : option !== "count" && !isRowTotal(condition) && numbers.length === 0
  const fresh = table === undefined ? null : newRowTest(table)

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
        <select
          aria-label={`${name} rows`}
          value={kind}
          onChange={(event) => onChange(withListKind(form, condition, event.target.value as ListKind))}
          className={`${FIELD_CLASS} w-32 shrink-0`}
          style={INPUT_STYLE}
        >
          {KINDS.map((option) => (
            <option key={option.value} value={option.value} disabled={unavailable(option.value)}>
              {option.label}
            </option>
          ))}
        </select>
        {isRowTotal(condition) && (
          <>
            <span>of</span>
            <select
              aria-label={`${name} total of`}
              value={condition.column}
              onChange={(event) => onChange({ ...condition, column: event.target.value })}
              className={`${FIELD_CLASS} min-w-0 flex-1`}
              style={INPUT_STYLE}
            >
              {!numbers.some((column) => column.id === condition.column) && (
                <option value={condition.column} disabled>
                  {table?.columns.find((column) => column.id === condition.column)?.name ?? "Missing column"}
                </option>
              )}
              {numbers.map((column) => (
                <option key={column.id} value={column.id}>
                  {label(column)}
                </option>
              ))}
            </select>
          </>
        )}
        {condition.where.length > 0 && <span>where</span>}
      </div>
      {condition.where.map((test, index) => (
        <RowTestFields
          // A test has no id of its own; its place in the list is what the panel edits.
          key={index}
          form={form}
          table={table}
          test={test}
          name={`${name} where ${index + 1}`}
          removable={!isRowsCondition(condition) || condition.where.length > 1}
          onChange={(next) => setWhere(condition.where.map((current, at) => (at === index ? next : current)))}
          onRemove={() => setWhere(condition.where.filter((_, at) => at !== index))}
        />
      ))}
      <QuietAction
        aria-label={`Add test to ${name.toLowerCase()}`}
        disabled={fresh === null}
        onClick={() => {
          if (fresh !== null) setWhere([...condition.where, fresh])
        }}
        className="self-start"
      >
        Add test
      </QuietAction>
      {!isRowsCondition(condition) && (
        <div className="flex items-start gap-1.5">
          <select
            aria-label={`${name} test`}
            value={condition.op}
            onChange={(event) => onChange({ ...condition, op: event.target.value as CountTest })}
            className={`${FIELD_CLASS} w-32 shrink-0`}
            style={INPUT_STYLE}
          >
            {COUNT_TESTS.map((test) => (
              <option key={test} value={test}>
                {testLabel(test, isRowCount(condition) ? "int" : "float")}
              </option>
            ))}
          </select>
          {isRowCount(condition) ? (
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
          ) : (
            <ValidatedTextField
              dataTestId={`${name}-total`}
              ariaLabel={`${name} value`}
              value={condition.value}
              validate={numberValid}
              onCommit={(text) => onChange({ ...condition, value: text.trim() })}
              containerClassName="min-w-0 flex-1"
              className={`${FIELD_CLASS} w-full tabular-nums`}
              style={INPUT_STYLE}
            />
          )}
        </div>
      )}
    </div>
  )
}

/** A test a row must pass: its column, one of its table's but the index, its test and value, and a cross. */
function RowTestFields({
  form,
  table,
  test,
  name,
  removable,
  onChange,
  onRemove,
}: {
  form: FormSpec
  table: SchemaTable | undefined
  test: RowTest
  name: string
  removable: boolean
  onChange: (next: RowTest) => void
  onRemove: () => void
}) {
  const columns = table === undefined ? [] : testableColumns(table)
  const column = table?.columns.find((candidate) => candidate.id === test.column) ?? null
  return (
    <div role="group" aria-label={name} className="flex flex-col gap-1 pl-2" style={{ borderLeft: "2px solid var(--border)" }}>
      <div className="flex items-center gap-1.5">
        <select
          aria-label={`${name} column`}
          value={test.column}
          onChange={(event) => {
            // A test is only offered the columns of a table the schema has.
            if (table !== undefined) onChange(withTestColumn(form, table.id, test, event.target.value))
          }}
          className={`${FIELD_CLASS} min-w-0 flex-1`}
          style={INPUT_STYLE}
        >
          {!columns.some((candidate) => candidate.id === test.column) && (
            <option value={test.column} disabled>
              {column === null ? "Missing column" : label(column)}
            </option>
          )}
          {columns.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {label(candidate)}
            </option>
          ))}
        </select>
        {removable && (
          <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this test" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
            <X size={12} aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="flex items-start gap-1.5">
        <ColumnFields column={column} test={test} name={name} onChange={onChange} />
      </div>
    </div>
  )
}

/** A test of a column's value: its test and its value. */
function ColumnFields<T extends ColumnTest>({
  column,
  test,
  name,
  onChange,
}: {
  column: SchemaColumn | null
  test: T
  name: string
  onChange: (next: T) => void
}) {
  const type = column?.type ?? "str"
  const tests = column === null ? [test.op] : testsFor(type)
  return (
    <>
      <select
        aria-label={`${name} test`}
        value={test.op}
        onChange={(event) => onChange(withConditionTest(column ?? undefined, test, event.target.value as ConditionTest))}
        className={`${FIELD_CLASS} w-32 shrink-0`}
        style={INPUT_STYLE}
      >
        {(tests.includes(test.op) ? tests : [test.op, ...tests]).map((option) => (
          <option key={option} value={option}>
            {testLabel(option, type)}
          </option>
        ))}
      </select>
      <ConditionValue condition={test} column={column} name={name} onChange={(values) => onChange({ ...test, values })} />
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
  condition: ColumnTest
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
