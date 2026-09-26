/**
 * The columns at each step: the start frame's columns carried through every
 * earlier step, with each column's type where it is known. Suggestions only;
 * every column picker also accepts free text because the schema may be
 * partial. `complete` says the list holds every column that can exist at the
 * step, so a name outside it is unknown there; `exact` says it also holds
 * nothing else, so a step's column change can be stated.
 */
import { COLUMNLESS_AGGREGATIONS, stepProblem } from "./catalogue"
import type { Condition, Expr, JoinStep, Operand, Step } from "./types"

/** A column as the preview reports it. */
export type ColumnInfo = { name: string; dtype: string }

/** A column at a step: its type when known, and whether a step made it. */
export type KnownColumn = { name: string; dtype: string | null; made: boolean }

export type StepColumns = { columns: KnownColumn[]; complete: boolean; exact: boolean }

/**
 * Where the columns start: in `input` mode the start step names an input and
 * `inputs` holds each input's columns (absent or empty while not loaded); in
 * `frame` mode the surface binds its own frame, whose full schema the editor
 * cannot know (a rating or scoring surface adds columns), so `frame` only
 * seeds suggestions.
 */
export type ColumnSource = { inputs: Readonly<Record<string, ColumnInfo[]>>; frame: ColumnInfo[] }

const fromInput = (columns: ColumnInfo[]): KnownColumn[] => columns.map((c) => ({ name: c.name, dtype: c.dtype, made: false }))
const made = (name: string, dtype: string | null = null): KnownColumn => ({ name, dtype, made: true })

function unique(columns: KnownColumn[]): KnownColumn[] {
  const seen = new Set<string>()
  return columns.filter((c) => (seen.has(c.name) ? false : (seen.add(c.name), true)))
}

/**
 * The columns a join produces, following Polars' rules for the renderer's
 * `df.join(other, left_on=..., right_on=..., how=..., suffix=...)`: inner and
 * left joins drop the right keys, right joins keep them and drop this frame's,
 * full and cross joins keep both sides, semi and anti joins keep this frame
 * only, and any right-hand name that clashes takes the suffix.
 */
export function joinColumns(left: KnownColumn[], right: KnownColumn[], step: Pick<JoinStep, "how" | "leftOn" | "rightOn" | "suffix">): KnownColumn[] {
  let base = left
  let joined = right
  switch (step.how) {
    case "semi":
    case "anti":
      return left
    case "inner":
    case "left":
      joined = right.filter((c) => !step.rightOn.includes(c.name))
      break
    case "right":
      base = left.filter((c) => !step.leftOn.includes(c.name))
      break
    case "full":
    case "cross":
      break
  }
  const taken = new Set(base.map((c) => c.name))
  return [...base, ...joined.map((c) => ({ ...c, name: taken.has(c.name) ? `${c.name}${step.suffix}` : c.name }))]
}

function applyStep(state: StepColumns, step: Step, source: ColumnSource): StepColumns {
  const { columns } = state
  const byName = new Map(columns.map((c) => [c.name, c]))
  const keep = (name: string): KnownColumn => byName.get(name) ?? { name, dtype: null, made: false }
  switch (step.kind) {
    case "source": {
      const input = source.inputs[step.input] ?? []
      return { columns: fromInput(input), complete: input.length > 0, exact: input.length > 0 }
    }
    case "free_code":
      // Authored Python can replace the frame or change its schema arbitrarily.
      return { columns: [], complete: false, exact: false }
    case "with_column":
      if (!step.name) return state
      return byName.has(step.name)
        ? { ...state, columns: columns.map((c) => (c.name === step.name ? made(step.name) : c)) }
        : { ...state, columns: [...columns, made(step.name)] }
    case "select": {
      const named = step.columns.filter((c) => c.length > 0).map(keep)
      // A type selection is resolved by Polars at run time; every column might
      // match, so all stay listed and the list is no longer exact.
      return step.dtypes?.length ? { ...state, columns: unique([...named, ...columns]), exact: false } : { ...state, columns: named }
    }
    case "drop": {
      const kept = columns.filter((c) => !step.columns.includes(c.name))
      return step.dtypes?.length ? { ...state, columns: kept, exact: false } : { ...state, columns: kept }
    }
    case "rename": {
      const map = new Map(step.renames.filter((r) => r.from && r.to).map((r) => [r.from, r.to]))
      return { ...state, columns: columns.map((c) => (map.has(c.name) ? { ...c, name: map.get(c.name) as string } : c)) }
    }
    case "cast": {
      const casts = new Map(step.casts.filter((c) => c.column).map((c) => [c.column, c.dtype]))
      return { ...state, columns: columns.map((c) => (casts.has(c.name) ? { ...c, dtype: casts.get(c.name) as string } : c)) }
    }
    case "group_by": {
      const keys = step.keys.filter((k) => k.length > 0).map(keep)
      const names = step.aggregations.flatMap((a) => ("dtype" in a || !a.name ? [] : [made(a.name)]))
      // A type-wide aggregation names its outputs `<column><suffix>` at run time.
      const typeWide = step.aggregations.some((a) => "dtype" in a)
      return typeWide
        ? { columns: unique([...keys, ...names]), complete: false, exact: false }
        : { ...state, columns: unique([...keys, ...names]) }
    }
    case "join": {
      const right = source.inputs[step.input] ?? []
      if (step.input === "" || right.length === 0) return { columns, complete: false, exact: false }
      return { ...state, columns: joinColumns(columns, fromInput(right), step) }
    }
    case "concat":
      // A diagonal concat can add columns from the other inputs.
      return { columns, complete: false, exact: false }
    case "pivot":
      return { ...state, columns: unique([...step.index.filter((k) => k.length > 0).map(keep), ...step.columns.map((c) => c.name).filter((n) => n.length > 0).map((n) => made(n))]) }
    case "unpivot":
      return {
        ...state,
        columns: unique([
          ...step.index.filter((k) => k.length > 0).map(keep),
          ...(step.variableName ? [made(step.variableName, "String")] : []),
          ...(step.valueName ? [made(step.valueName)] : []),
        ]),
      }
    default:
      return state
  }
}

/**
 * The columns before every step and after the last, in one pass: entry `i`
 * is what step `i` sees, entry `steps.length` what the node outputs. Steps
 * whose shape cannot be read are skipped.
 */
export function columnsAtEachStep(source: ColumnSource, steps: Step[]): StepColumns[] {
  let state: StepColumns = { columns: fromInput(source.frame), complete: false, exact: false }
  const states = [state]
  for (const step of steps) {
    if (stepProblem(step) === null) state = applyStep(state, step, source)
    state = { ...state, columns: unique(state.columns) }
    states.push(state)
  }
  return states
}

/** The columns available to the step at `index`. */
export function columnsBeforeStep(source: ColumnSource, steps: Step[], index: number): StepColumns {
  return columnsAtEachStep(source, steps.slice(0, index))[index]
}

/** The names in a column list, for suggestions. */
export const columnNames = (state: StepColumns): string[] => state.columns.map((c) => c.name)

function operandColumns(operand: Operand | undefined): string[] {
  if (!operand) return []
  if (operand.kind === "column") return [operand.name]
  if (operand.kind === "expr") return exprColumns(operand.expr)
  return []
}

function conditionColumns(conditions: Condition[]): string[] {
  return conditions.flatMap((c) => [c.column, ...operandColumns(c.value)])
}

export function exprColumns(expr: Expr): string[] {
  switch (expr.type) {
    case "operand":
      return operandColumns(expr.operand)
    case "binary":
      return [...operandColumns(expr.left), ...operandColumns(expr.right)]
    case "function":
      return operandColumns(expr.operand)
    case "conditional":
      return [...conditionColumns(expr.conditions), ...operandColumns(expr.then), ...operandColumns(expr.otherwise)]
    case "window":
      // A columnless aggregate (a row count, a row number) may be saved without its column.
      return [
        ...(COLUMNLESS_AGGREGATIONS.has(expr.agg) || typeof expr.column !== "string" ? [] : [expr.column]),
        ...expr.over,
        ...(expr.orderBy ?? []).map((k) => k.column),
      ]
    case "concat":
      return expr.parts.flatMap(operandColumns)
  }
}

/**
 * The columns of the frame a step reads, as named (blanks left out). A join's
 * right keys belong to the other input and are not listed.
 */
export function columnsReadBy(step: Step): string[] {
  const names = ((): string[] => {
    switch (step.kind) {
      case "filter":
        return conditionColumns(step.conditions)
      case "with_column":
        return exprColumns(step.expr)
      case "select":
      case "drop":
      case "unique":
        return step.columns
      case "fill_null":
        return [...step.columns, ...(step.fill.kind === "value" ? operandColumns(step.fill.value) : [])]
      case "rename":
        return step.renames.map((r) => r.from)
      case "cast":
        return step.casts.map((c) => c.column)
      case "sort":
        return step.keys.map((k) => k.column)
      case "group_by":
        return [
          ...step.keys,
          ...step.aggregations.flatMap((a) => ("dtype" in a ? [] : [...(a.agg === "len" ? [] : [a.column]), ...conditionColumns(a.where?.conditions ?? [])])),
        ]
      case "join":
        return step.leftOn
      case "pivot":
        return [...step.index, step.on, step.values]
      case "unpivot":
        return [...step.on, ...step.index]
      default:
        return []
    }
  })()
  return [...new Set(names.filter((n) => typeof n === "string" && n.length > 0))]
}

/**
 * The names a step reads that are not in the data before it, when the
 * columns there are complete; a join's right keys are checked against the
 * joined input.
 */
export function unknownColumnsOf(step: Step, before: StepColumns, source: ColumnSource): string[] {
  const missing = before.complete ? columnsReadBy(step).filter((name) => !before.columns.some((c) => c.name === name)) : []
  if (step.kind !== "join") return missing
  const right = source.inputs[step.input] ?? []
  if (right.length === 0) return missing
  return [...missing, ...step.rightOn.filter((name) => name.length > 0 && !right.some((c) => c.name === name))]
}

/** A name list for a note: the names when there are one or two, else a count. */
function few(names: string[], sign: string): string {
  return names.length <= 2 ? names.map((n) => `${sign}${n}`).join(" ") : `${sign}${names.length} columns`
}

/**
 * The change a step makes to the columns, for its collapsed card: `+name`,
 * `−3 columns`, or `→ 3 columns` for a step that reshapes the frame. Null
 * when the columns before or after are not exactly known, or nothing changes.
 */
export function columnChange(step: Step, before: StepColumns, after: StepColumns): string | null {
  if (!before.exact || !after.exact) return null
  if (step.kind === "group_by" || step.kind === "pivot" || step.kind === "unpivot") {
    return `→ ${after.columns.length} ${after.columns.length === 1 ? "column" : "columns"}`
  }
  if (step.kind === "rename" || step.kind === "cast") return null
  const beforeNames = new Set(before.columns.map((c) => c.name))
  const afterNames = new Set(after.columns.map((c) => c.name))
  const added = after.columns.map((c) => c.name).filter((n) => !beforeNames.has(n))
  const removed = before.columns.map((c) => c.name).filter((n) => !afterNames.has(n))
  const parts = [...(added.length ? [few(added, "+")] : []), ...(removed.length ? [few(removed, "−")] : [])]
  return parts.length ? parts.join(" ") : null
}
