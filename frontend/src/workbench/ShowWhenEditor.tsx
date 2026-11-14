/**
 * When a sheet, a section or a component shows (specs/workbench): the conditions in its
 * panel, and the eye it carries on the sheet while building. Conditions test the values typed into
 * the inputs of one-row tables, each a row of its column, its test and its value, the control the
 * column's cell is, or a many-row table's rows: which rows, any, every, how many or a total
 * of a column over them, and the tests a row must pass; a group holds conditions of its
 * own; with two or more, all of them must hold, any, or at least a number. Every change is
 * handed up whole, so the panel commits it as one undo step.
 */
import { Eye, EyeOff, List, X } from "lucide-react"
import type { CSSProperties } from "react"
import type { Condition, ConditionGroup, FormSpec, RowsCondition, RowTest, SchemaColumn, SchemaTable, ShowWhen } from "../api/types"
import { CommittedTextField, ConfigCheckbox, QuietAction, ValidatedTextField, wholeNumber } from "../components/form"
import { Select } from "../haute-ui"
import { hasInputs, hasOutputs, isOutputColumn, ownOutputs, tableColumns, typedInputs } from "../utils/quoteIdentity"
import { parseTypedNumber } from "../utils/sheetValues"
import {
  columnKey,
  CONDITION_TESTS,
  COUNT_TESTS,
  conditionFault,
  DAYS_MAX,
  isGroup,
  isListCondition,
  isRowCount,
  isRowsCondition,
  isRowTotal,
  newCondition,
  onPrice,
  priceProblem,
  testableColumns,
  testFields,
  testLabel,
  testsFor,
  TOTALS,
  totalColumns,
  withConditionColumn,
  withConditionRowsOf,
  withConditionTest,
  withListKind,
  withTestColumn,
  withTestField,
  type ColumnTest,
  type ConditionTest,
  type CountTest,
  type ListCondition,
  type ShowWhenEntry,
  type SingleCondition,
  type TestField,
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

/** A table's rows' place in the column select, never a column's key, whose `:` between its ids no id holds. */
const rowsKey = (tableId: string) => `${tableId}#rows`
const label = (column: SchemaColumn) => column.label || readableName(column.name)
const countValid = wholeNumber({ min: 0, unit: "rows" })
/** Days added to another Date: a whole number, a minus sign taking them away, up to `DAYS_MAX` either way. */
const daysValid = (text: string): string | null => {
  const days = text.trim()
  return /^[+-]?\d+$/.test(days) && Math.abs(Number(days)) <= DAYS_MAX ? null : `Enter a whole number of days from -${DAYS_MAX} to ${DAYS_MAX}.`
}
/** A total's number: blank, which its fault names, or a number as the sample's are typed. */
const numberValid = (text: string): string | null => (text.trim() === "" || parseTypedNumber(text.trim()) !== null ? null : "Enter a number.")
/** A number written with a thousands separator, `1,000` or `1,000,000.5`. */
const GROUPED = /(?<![\d.])\d+(?:,\d{3})+(?:\.\d+)?(?!\d)/
/** A number column's values, comma-separated: none with a thousands separator, which the split would cut in two. */
const listedNumbersValid = (text: string): string | null => {
  const grouped = GROUPED.exec(text)?.[0]
  return grouped === undefined ? null : `Write ${grouped.replaceAll(",", "")}, not ${grouped}: a comma separates the values.`
}
/** Values typed comma-separated, each trimmed, the blanks dropped. */
const splitValues = (text: string): string[] => text.split(",").map((value) => value.trim()).filter((value) => value !== "")

/** A one-row table's columns the column select offers: its inputs, and, on what takes nothing typed, its outputs too, tested on the price. */
const offeredColumns = (table: SchemaTable, priced: boolean): SchemaColumn[] => [...typedInputs(table), ...(priced ? ownOutputs(table) : [])]

/** Whether the column select offers a many-row table's rows: an input table's, and, on what takes nothing typed, a table of outputs alone's too. */
const rowsOffered = (table: SchemaTable, priced: boolean): boolean => hasInputs(table) || (priced && hasOutputs(table))

/** The tables a condition can test: those with an input to test, and, on what takes nothing typed, those with an output too, tested on the price. */
const testable = (form: FormSpec, priced: boolean): SchemaTable[] =>
  form.schema.tables.filter((table) => (table.rows === "many" ? rowsOffered(table, priced) : offeredColumns(table, priced).length > 0))

/**
 * The columns a test of a row, or a total, may name in its table: its inputs, and, where the
 * price is tested or the table has no inputs, its outputs too, each but a numbered index
 * (`testableColumns`); once another of the condition's tests, or its total, names a column
 * (`names`), those of that column's block alone, since a condition on rows tests one block.
 */
function rowColumns(table: SchemaTable, priced: boolean, names: readonly string[]): SchemaColumn[] {
  const columns = testableColumns(table)
  const offered = priced || !hasInputs(table) ? columns : columns.filter((column) => !isOutputColumn(table, column))
  const named = names.find((name) => tableColumns(table).some((column) => column.name === name))
  if (named === undefined) return offered
  const output = isOutputColumn(table, named)
  return offered.filter((column) => isOutputColumn(table, column) === output)
}

/** The columns a condition on rows names but its test at `except`: its other tests' and its total's. */
const namesBut = (condition: ListCondition, except: number): string[] => [
  ...condition.where.filter((_, at) => at !== except).map((test) => test.column),
  ...(isRowTotal(condition) ? [condition.column] : []),
]

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
      <Select
        aria-label={`Which conditions${of} must hold`}
        value={typeof match === "number" ? "at_least" : match}
        onChange={(event) => {
          const next = event.target.value
          onChange(next === "at_least" ? AT_LEAST : (next as "all" | "any"))
        }}
        className="w-20"
      >
        {MATCHES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
      {typeof match === "number" && (
        <Select
          aria-label={`How many conditions${of} must hold`}
          value={match}
          onChange={(event) => onChange(Number(event.target.value))}
          className="w-14 tabular-nums"
        >
          {numbers.map((number) => (
            <option key={number} value={number}>
              {number}
            </option>
          ))}
        </Select>
      )}
      <span>of these are true</span>
    </div>
  )
}

export function ShowWhenEditor({
  form,
  showWhen,
  priced,
  onChange,
}: {
  form: FormSpec
  showWhen: ShowWhen
  /** What it is on takes nothing typed (`takesNothingTyped`), so its conditions may test the price. */
  priced: boolean
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
            priced={priced}
            group={entry}
            name={`Group ${index + 1}`}
            onChange={(next) => replace(index, next)}
            onRemove={() => remove(index)}
          />
        ) : (
          <ConditionRow
            key={index}
            form={form}
            priced={priced}
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
  priced,
  group,
  name,
  onChange,
  onRemove,
}: {
  form: FormSpec
  priced: boolean
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
          priced={priced}
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
  priced,
  condition,
  name,
  onChange,
  onRemove,
}: {
  form: FormSpec
  priced: boolean
  condition: SingleCondition
  name: string
  onChange: (next: SingleCondition) => void
  onRemove: () => void
}) {
  const tables = testable(form, priced)
  const list = isListCondition(condition)
  const table = form.schema.tables.find((candidate) => candidate.name === condition.table)
  const found = list ? null : fieldColumn(form, condition)
  const key = list ? rowsKey(condition.table) : columnKey(condition.table, condition.column)
  // What each place in the column select makes of the condition: a one-row table's column, or a many-row table's rows.
  const choices = new Map<string, () => SingleCondition>()
  for (const candidate of tables) {
    if (candidate.rows === "many") {
      choices.set(rowsKey(candidate.name), () => withConditionRowsOf(form, condition, candidate.name))
      continue
    }
    for (const column of offeredColumns(candidate, priced)) {
      choices.set(columnKey(candidate.name, column.name), () => withConditionColumn(form, condition, candidate.name, column.name))
    }
  }
  const offered = choices.has(key)
  const fault = conditionFault(form, condition) ?? (!priced && onPrice(form, condition) ? priceProblem(form, condition) : null)
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
        <Select
          aria-label={`${name} column`}
          value={key}
          onChange={(event) => {
            const choose = choices.get(event.target.value)
            if (choose === undefined) throw new Error(`"${event.target.value}" is not a column a condition can test`)
            onChange(choose())
          }}
          className="flex-1"
        >
          {!offered && (
            <option value={key} disabled>
              {missing}
            </option>
          )}
          {tables.map((candidate) => (
            <optgroup key={candidate.name} label={candidate.name}>
              {candidate.rows === "many" ? (
                <option value={rowsKey(candidate.name)}>Rows of {candidate.name}</option>
              ) : (
                offeredColumns(candidate, priced).map((column) => (
                  <option key={column.name} value={columnKey(candidate.name, column.name)}>
                    {label(column)}
                  </option>
                ))
              )}
            </optgroup>
          ))}
        </Select>
        <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this condition" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      <div className="flex items-start gap-1.5">
        <span className="size-4 shrink-0" aria-hidden="true" />
        {list ? (
          <ListFields form={form} priced={priced} table={table} condition={condition} name={name} onChange={onChange} />
        ) : (
          <div className="flex min-w-0 flex-1 flex-wrap items-start gap-1.5">
            <ColumnFields form={form} tableId={condition.table} column={found?.column ?? null} test={condition} name={name} onChange={(next: Condition) => onChange(next)} />
          </div>
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
  priced,
  table,
  condition,
  name,
  onChange,
}: {
  form: FormSpec
  priced: boolean
  table: SchemaTable | undefined
  condition: ListCondition
  name: string
  onChange: (next: ListCondition) => void
}) {
  // Its tests and its total name one block: the columns offered for another follow the first named.
  const named = condition.where.map((test) => test.column)
  const columns = table === undefined ? [] : rowColumns(table, priced, [...named, ...(isRowTotal(condition) ? [condition.column] : [])])
  const totalled = new Set(table === undefined ? [] : totalColumns(table).map((column) => column.name))
  const numbers = table === undefined ? [] : rowColumns(table, priced, named).filter((column) => totalled.has(column.name))
  const kind: ListKind = isRowsCondition(condition) ? condition.rows : isRowCount(condition) ? "count" : condition.total
  const setWhere = (where: RowTest[]) => onChange({ ...condition, where })
  // Any row and Every row need a test, a total a number column.
  const unavailable = (option: ListKind) =>
    option === "any" || option === "every" ? condition.where.length === 0 && columns.length === 0 : option !== "count" && !isRowTotal(condition) && numbers.length === 0
  const first = columns[0]
  const fresh: RowTest | null = first === undefined ? null : { column: first.name, op: testsFor(first.type)[0], values: [], field: null, days: 0 }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
        <Select
          aria-label={`${name} rows`}
          value={kind}
          onChange={(event) => onChange(withListKind(form, condition, event.target.value as ListKind))}
          className="w-32 shrink-0"
        >
          {KINDS.map((option) => (
            <option key={option.value} value={option.value} disabled={unavailable(option.value)}>
              {option.label}
            </option>
          ))}
        </Select>
        {isRowTotal(condition) && (
          <>
            <span>of</span>
            <Select
              aria-label={`${name} total of`}
              value={condition.column}
              onChange={(event) => onChange({ ...condition, column: event.target.value })}
              className="flex-1"
            >
              {!numbers.some((column) => column.name === condition.column) && (
                <option value={condition.column} disabled>
                  {(table === undefined ? undefined : tableColumns(table).find((column) => column.name === condition.column))?.name ?? "Missing column"}
                </option>
              )}
              {numbers.map((column) => (
                <option key={column.name} value={column.name}>
                  {label(column)}
                </option>
              ))}
            </Select>
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
          columns={table === undefined ? [] : rowColumns(table, priced, namesBut(condition, index))}
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
          <Select
            aria-label={`${name} test`}
            value={condition.op}
            onChange={(event) => onChange({ ...condition, op: event.target.value as CountTest })}
            className="w-32 shrink-0"
          >
            {COUNT_TESTS.map((test) => (
              <option key={test} value={test}>
                {testLabel(test, isRowCount(condition) ? "int" : "float")}
              </option>
            ))}
          </Select>
          {isRowCount(condition) ? (
            <ValidatedTextField
              dataTestId={`${name}-count`}
              ariaLabel={`${name} value`}
              value={String(condition.count)}
              validate={countValid}
              onCommit={(text) => onChange({ ...condition, count: Number(text) })}
              containerClassName="min-w-0 flex-1"
              field="regular"
              className="w-full tabular-nums"
            />
          ) : (
            <ValidatedTextField
              dataTestId={`${name}-total`}
              ariaLabel={`${name} value`}
              value={condition.value}
              validate={numberValid}
              onCommit={(text) => onChange({ ...condition, value: text.trim() })}
              containerClassName="min-w-0 flex-1"
              field="regular"
              className="w-full tabular-nums"
            />
          )}
        </div>
      )}
    </div>
  )
}

/** A test a row must pass: its column, one of `columns`, its test and value, and a cross. */
function RowTestFields({
  form,
  table,
  columns,
  test,
  name,
  removable,
  onChange,
  onRemove,
}: {
  form: FormSpec
  table: SchemaTable | undefined
  /** The columns it may name: its table's but the index, of the condition's block once another names one. */
  columns: readonly SchemaColumn[]
  test: RowTest
  name: string
  removable: boolean
  onChange: (next: RowTest) => void
  onRemove: () => void
}) {
  const column = (table === undefined ? undefined : tableColumns(table).find((candidate) => candidate.name === test.column)) ?? null
  return (
    <div role="group" aria-label={name} className="flex flex-col gap-1 pl-2" style={{ borderLeft: "2px solid var(--border)" }}>
      <div className="flex items-center gap-1.5">
        <Select
          aria-label={`${name} column`}
          value={test.column}
          onChange={(event) => {
            // A test is only offered the columns of a table the schema has.
            if (table !== undefined) onChange(withTestColumn(form, table.name, test, event.target.value))
          }}
          className="flex-1"
        >
          {!columns.some((candidate) => candidate.name === test.column) && (
            <option value={test.column} disabled>
              {column === null ? "Missing column" : label(column)}
            </option>
          )}
          {columns.map((candidate) => (
            <option key={candidate.name} value={candidate.name}>
              {label(candidate)}
            </option>
          ))}
        </Select>
        {removable && (
          <button type="button" onClick={onRemove} aria-label={`Remove ${name.toLowerCase()}`} title="Remove this test" className="icon-danger-btn focus-ring shrink-0 rounded p-0.5">
            <X size={12} aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="flex flex-wrap items-start gap-1.5">
        <ColumnFields form={form} tableId={table?.name ?? null} column={column} test={test} name={name} onChange={onChange} />
      </div>
    </div>
  )
}

/**
 * A test of a column's value: its test, what a test of one value is tested against while
 * another field could be (`Against`), and its value, or, against a Date, the days added.
 */
function ColumnFields<T extends ColumnTest>({
  form,
  tableId,
  column,
  test,
  name,
  onChange,
}: {
  form: FormSpec
  /** The tested column's table; null while it is gone. */
  tableId: string | null
  column: SchemaColumn | null
  test: T
  name: string
  onChange: (next: T) => void
}) {
  const type = column?.type ?? "str"
  const tests = column === null ? [test.op] : testsFor(type)
  const fields = column === null || tableId === null ? [] : testFields(form.schema, tableId, column)
  const against = CONDITION_TESTS[test.op].values === "one" && (fields.length > 0 || test.field !== null)
  return (
    <>
      <Select
        aria-label={`${name} test`}
        value={test.op}
        onChange={(event) => onChange(withConditionTest(column ?? undefined, test, event.target.value as ConditionTest))}
        className="w-32 shrink-0"
      >
        {(tests.includes(test.op) ? tests : [test.op, ...tests]).map((option) => (
          <option key={option} value={option}>
            {testLabel(option, type)}
          </option>
        ))}
      </Select>
      {against && <Against form={form} tableId={tableId} fields={fields} test={test} name={name} onChange={(field) => onChange(withTestField(test, field, type === "date"))} />}
      {test.field === null ? (
        <ConditionValue condition={test} column={column} name={name} onChange={(values) => onChange({ ...test, values })} />
      ) : (
        type === "date" && (
          <span className="flex min-w-0 flex-1 items-start gap-1">
            <ValidatedTextField
              dataTestId={`${name}-days`}
              ariaLabel={`${name} days`}
              value={String(test.days)}
              validate={daysValid}
              onCommit={(text) => onChange({ ...test, days: Number(text.trim()) })}
              containerClassName="w-20 shrink-0"
              field="regular"
              className="w-full tabular-nums"
            />
            <span className="pt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              days
            </span>
          </span>
        )
      )}
    </>
  )
}

/** Where the select of what a test is tested against keeps "Value": a `:`, which no id holds, so no column's key or id. */
const VALUE = ":value"

/**
 * What a test of one value is tested against: Value, typed, or one of `fields`, by table;
 * a field the test names that is no longer one of them kept in its place, not offered.
 */
function Against<T extends ColumnTest>({
  form,
  tableId,
  fields,
  test,
  name,
  onChange,
}: {
  form: FormSpec
  tableId: string | null
  fields: readonly TestField[]
  test: T
  name: string
  onChange: (field: T["field"]) => void
}) {
  const { field } = test
  // A one-row table's condition names its field by table and column, a test of a row by its column in the same row.
  const rowTest = !("table" in test)
  const keyOf = (choice: TestField): string => (rowTest ? choice.column.name : columnKey(choice.table.name, choice.column.name))
  const current = field === null ? VALUE : typeof field === "string" ? field : columnKey(field.table, field.column)
  const ref = field === null ? null : typeof field === "string" ? (tableId === null ? null : { table: tableId, column: field }) : field
  const named = ref === null ? null : fieldColumn(form, ref)
  const tables = [...new Map(fields.map((choice) => [choice.table.name, choice.table])).values()]
  return (
    <Select
      aria-label={`${name} against`}
      value={current}
      onChange={(event) => {
        if (event.target.value === VALUE) return onChange(null as T["field"])
        const chosen = fields.find((choice) => keyOf(choice) === event.target.value)
        if (chosen === undefined) throw new Error(`"${event.target.value}" is not a field this test can be tested against`)
        onChange((rowTest ? chosen.column.name : { table: chosen.table.name, column: chosen.column.name }) as T["field"])
      }}
      className="w-32 shrink-0"
    >
      <option value={VALUE}>Value</option>
      {field !== null && !fields.some((choice) => keyOf(choice) === current) && (
        <option value={current} disabled>
          {named === null ? "Missing field" : label(named.column)}
        </option>
      )}
      {tables.map((table) => (
        <optgroup key={table.name} label={table.name}>
          {fields
            .filter((choice) => choice.table.name === table.name)
            .map((choice) => (
              <option key={choice.column.name} value={keyOf(choice)}>
                {label(choice.column)}
              </option>
            ))}
        </optgroup>
      ))}
    </Select>
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
    if (column?.type === "int" || column?.type === "float") {
      return (
        <ValidatedTextField
          dataTestId={`${name}-values`}
          ariaLabel={aria}
          value={condition.values.join(", ")}
          placeholder="Values, comma-separated"
          validate={listedNumbersValid}
          onCommit={(text) => onChange(splitValues(text))}
          containerClassName="min-w-0 flex-1"
          field="regular"
          className="w-full tabular-nums"
        />
      )
    }
    return (
      <CommittedTextField
        aria-label={aria}
        value={condition.values.join(", ")}
        placeholder="Values, comma-separated"
        onCommit={(text) => onChange(splitValues(text))}
        field="regular"
        className="flex-1"
      />
    )
  }
  const value = condition.values[0] ?? ""
  const one = (text: string) => onChange(text === "" ? [] : [text])
  if (options.length > 0 && (condition.op === "equals" || condition.op === "not_equals")) {
    return (
      <Select aria-label={aria} value={value} onChange={(event) => one(event.target.value)} className="flex-1">
        <option value="">Choose…</option>
        {!options.includes(value) && value !== "" && <option value={value}>{value}</option>}
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </Select>
    )
  }
  if (column?.type === "date") {
    // Committed on blur or Enter: a year typed digit by digit is one change.
    return <CommittedTextField type="date" aria-label={aria} value={value} onCommit={one} field="regular" className="flex-1" />
  }
  return (
    <CommittedTextField
      aria-label={aria}
      value={value}
      placeholder="Value"
      inputMode={column?.type === "int" || column?.type === "float" ? "decimal" : undefined}
      onCommit={(text) => one(text.trim())}
      field="regular"
      className="flex-1"
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
