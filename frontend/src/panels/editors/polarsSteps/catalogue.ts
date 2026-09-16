/**
 * Step catalogue: the closed vocabularies the editor offers, default steps
 * for the `Add step` menu, and the shape check for persisted steps.
 */
import type {
  Aggregation,
  PivotAggregation,
  BinaryOperator,
  CastDtype,
  Condition,
  ConditionOperator,
  Expr,
  FillStrategy,
  FunctionName,
  JoinHow,
  JoinMaintainOrder,
  JoinValidate,
  LiteralOperand,
  LiteralType,
  Operand,
  Step,
  StepKind,
  WindowAggregation,
  WindowOnlyAggregation,
} from "./types"

export type StepKindInfo = { kind: Exclude<StepKind, "source">; label: string; description: string }

/** Kinds offered by `Add step`, in menu order. `source` is the fixed first step. */
export const STEP_CATALOGUE: StepKindInfo[] = [
  { kind: "filter", label: "Filter rows", description: "Keep rows that match conditions" },
  { kind: "with_column", label: "Add column", description: "Compute a new or replaced column" },
  { kind: "select", label: "Keep columns", description: "Keep only the listed columns" },
  { kind: "drop", label: "Drop columns", description: "Remove the listed columns" },
  { kind: "rename", label: "Rename columns", description: "Give columns new names" },
  { kind: "cast", label: "Change types", description: "Cast columns to another data type" },
  { kind: "sort", label: "Sort rows", description: "Order rows by one or more columns" },
  { kind: "unique", label: "Remove duplicates", description: "Keep one row per key" },
  { kind: "group_by", label: "Group and aggregate", description: "Summarise rows per group" },
  { kind: "join", label: "Join another input", description: "Combine columns from another input" },
  { kind: "concat", label: "Append inputs", description: "Stack rows from other inputs" },
  { kind: "fill_null", label: "Fill missing values", description: "Replace nulls with a value or strategy" },
  { kind: "limit", label: "Limit rows", description: "Keep the first N rows" },
  { kind: "variable", label: "Define variable", description: "Name a value for later steps" },
  { kind: "pivot", label: "Pivot to columns", description: "One column per value of a category" },
  { kind: "unpivot", label: "Unpivot to rows", description: "Stack several columns into name/value rows" },
]

export const CONDITION_OPERATORS: Array<{ value: ConditionOperator; label: string; takes: "value" | "none" | "values" }> = [
  { value: "eq", label: "equals", takes: "value" },
  { value: "ne", label: "does not equal", takes: "value" },
  { value: "gt", label: "is greater than", takes: "value" },
  { value: "ge", label: "is at least", takes: "value" },
  { value: "lt", label: "is less than", takes: "value" },
  { value: "le", label: "is at most", takes: "value" },
  { value: "is_null", label: "is missing", takes: "none" },
  { value: "is_not_null", label: "is present", takes: "none" },
  { value: "is_in", label: "is one of", takes: "values" },
  { value: "not_in", label: "is not one of", takes: "values" },
  { value: "contains", label: "contains text", takes: "value" },
  { value: "starts_with", label: "starts with", takes: "value" },
  { value: "ends_with", label: "ends with", takes: "value" },
  { value: "matches", label: "matches pattern (regex)", takes: "value" },
]

export const BINARY_OPERATORS: Array<{ value: BinaryOperator; label: string }> = [
  { value: "+", label: "plus" },
  { value: "-", label: "minus" },
  { value: "*", label: "times" },
  { value: "/", label: "divided by" },
  { value: "//", label: "integer-divided by" },
  { value: "%", label: "modulo" },
  { value: "**", label: "to the power of" },
]

export const AGGREGATIONS: Array<{ value: Aggregation; label: string }> = [
  { value: "sum", label: "sum" },
  { value: "mean", label: "mean" },
  { value: "min", label: "minimum" },
  { value: "max", label: "maximum" },
  { value: "median", label: "median" },
  { value: "quantile", label: "quantile" },
  { value: "std", label: "standard deviation" },
  { value: "var", label: "variance" },
  { value: "count", label: "count (non-null)" },
  { value: "n_unique", label: "distinct count" },
  { value: "first", label: "first" },
  { value: "last", label: "last" },
  { value: "len", label: "row count" },
]

/** Window-only aggregates, offered after the plain ones in a window expression. */
export const WINDOW_ONLY_AGGREGATIONS: Array<{ value: WindowOnlyAggregation; label: string }> = [
  { value: "row_number", label: "row number" },
  { value: "cum_sum", label: "running total" },
  { value: "shift", label: "previous row's value" },
  { value: "rank", label: "rank" },
  { value: "dense_rank", label: "dense rank" },
  { value: "forward_fill", label: "fill forward" },
  { value: "backward_fill", label: "fill backward" },
]
export const WINDOW_AGGREGATIONS: Array<{ value: WindowAggregation; label: string }> = [...AGGREGATIONS, ...WINDOW_ONLY_AGGREGATIONS]
/** Aggregates a pivot cell may take: `count` is non-null values, `len` is rows. */
export const PIVOT_AGGREGATIONS: Array<{ value: PivotAggregation; label: string }> = [
  { value: "sum", label: "sum" },
  { value: "mean", label: "mean" },
  { value: "min", label: "minimum" },
  { value: "max", label: "maximum" },
  { value: "median", label: "median" },
  { value: "first", label: "first" },
  { value: "last", label: "last" },
  { value: "count", label: "count (non-null)" },
  { value: "len", label: "row count" },
]
/** Window aggregates that take no input column. */
export const COLUMNLESS_AGGREGATIONS: ReadonlySet<WindowAggregation> = new Set<WindowAggregation>(["len", "row_number"])

export const CAST_DTYPES: CastDtype[] = [
  "Int8", "Int16", "Int32", "Int64",
  "UInt8", "UInt16", "UInt32", "UInt64",
  "Float32", "Float64",
  "String", "Boolean", "Date", "Datetime", "Categorical",
]
/** The cast types as select options, labelled by name. */
export const DTYPE_OPTIONS: Array<{ value: CastDtype; label: string }> = CAST_DTYPES.map((dtype) => ({ value: dtype, label: dtype }))
export const JOIN_HOW: JoinHow[] = ["inner", "left", "right", "full", "semi", "anti", "cross"]
/** Join kinds Polars can validate; the renderer refuses `validate` on the others. */
export const JOIN_VALIDATED_HOW: ReadonlySet<JoinHow> = new Set<JoinHow>(["inner", "left", "full"])
export const JOIN_VALIDATE: Array<{ value: JoinValidate; label: string }> = [
  { value: "1:1", label: "one to one" },
  { value: "m:1", label: "many to one (unique right keys)" },
  { value: "1:m", label: "one to many (unique left keys)" },
  { value: "m:m", label: "many to many" },
]
export const JOIN_MAINTAIN_ORDER: Array<{ value: JoinMaintainOrder; label: string }> = [
  { value: "none", label: "any order" },
  { value: "left", label: "left input order" },
  { value: "right", label: "right input order" },
  { value: "left_right", label: "left, then right" },
  { value: "right_left", label: "right, then left" },
]
export const FILL_STRATEGIES: FillStrategy[] = ["forward", "backward", "min", "max", "mean", "zero", "one"]
export const LITERAL_TYPES: Array<{ value: LiteralType; label: string }> = [
  { value: "number", label: "number" },
  { value: "text", label: "text" },
  { value: "boolean", label: "true/false" },
  { value: "date", label: "date" },
  { value: "null", label: "missing (null)" },
]
/** How deep expressions may nest as operands; a step's expression is depth 1 (mirrors the renderer). */
export const MAX_EXPR_DEPTH = 12
/** Literal types a membership list accepts: the renderer refuses null members. */
export const LIST_LITERAL_TYPES = LITERAL_TYPES.filter((t) => t.value !== "null")

/**
 * Function argument shapes: what each argument must be, in order. `integer`
 * is zero or more; `int` may be negative (a slice start counting from the end).
 */
export type FunctionArg = "integer" | "int" | "number" | "scalar" | "dtype" | "text"
export const FUNCTIONS: Array<{ value: FunctionName; label: string; args: FunctionArg[] }> = [
  { value: "abs", label: "absolute value", args: [] },
  { value: "round", label: "round", args: ["integer"] },
  { value: "floor", label: "round down", args: [] },
  { value: "ceil", label: "round up", args: [] },
  { value: "sqrt", label: "square root", args: [] },
  { value: "log", label: "natural log", args: [] },
  { value: "exp", label: "exponential", args: [] },
  { value: "clip", label: "clip between", args: ["number", "number"] },
  { value: "fill_null", label: "fill missing with", args: ["scalar"] },
  { value: "cast", label: "cast to type", args: ["dtype"] },
  { value: "upper", label: "upper case", args: [] },
  { value: "lower", label: "lower case", args: [] },
  { value: "strip", label: "trim whitespace", args: [] },
  { value: "length", label: "text length", args: [] },
  { value: "replace", label: "replace first match", args: ["text", "text"] },
  { value: "replace_all", label: "replace all matches", args: ["text", "text"] },
  { value: "replace_regex", label: "replace by pattern (regex)", args: ["text", "text"] },
  { value: "slice", label: "substring", args: ["int", "integer"] },
  { value: "split_part", label: "split and take part", args: ["text", "integer"] },
  { value: "extract", label: "extract pattern group (regex)", args: ["text", "integer"] },
  { value: "year", label: "year of date", args: [] },
  { value: "month", label: "month of date", args: [] },
  { value: "day", label: "day of date", args: [] },
  { value: "weekday", label: "weekday of date (1 = Monday)", args: [] },
  { value: "offset_by", label: "shift date by", args: ["text"] },
  { value: "total_days", label: "duration in days", args: [] },
  { value: "try_cast", label: "cast to type (null on failure)", args: ["dtype"] },
]

export function literal(type: LiteralType, value: number | string | boolean | null): LiteralOperand {
  return { kind: "literal", type, value }
}

export function defaultLiteral(type: LiteralType): LiteralOperand {
  switch (type) {
    case "number":
      return literal("number", 0)
    case "text":
      return literal("text", "")
    case "boolean":
      return literal("boolean", true)
    case "date":
      return literal("date", "2026-01-01")
    case "null":
      return literal("null", null)
  }
}

export function defaultCondition(column = ""): Condition {
  return { column, operator: "eq", value: literal("number", 0) }
}

export function defaultArgFor(arg: FunctionArg): LiteralOperand {
  switch (arg) {
    case "integer":
    case "int":
    case "number":
      return literal("number", 0)
    case "scalar":
      return literal("number", 0)
    case "dtype":
      return literal("text", "Float64")
    case "text":
      return literal("text", "")
  }
}

export function defaultExpr(type: Expr["type"], column = ""): Expr {
  const operand: Operand = { kind: "column", name: column }
  switch (type) {
    case "operand":
      return { type, operand }
    case "binary":
      // An empty formula box until something is typed; the placeholder tree keeps the step renderable.
      return { type, left: operand, op: "*", right: literal("number", 1), text: "" }
    case "function":
      return { type, fn: "abs", operand, args: [] }
    case "conditional":
      return { type, match: "all", conditions: [defaultCondition(column)], then: literal("number", 1), otherwise: literal("number", 0) }
    case "window":
      return { type, agg: "sum", column, over: [] }
    case "concat":
      return { type, parts: [operand, { kind: "column", name: "" }], separator: " " }
  }
}

/** Build a fresh step of `kind` (never `source`, which the editor seeds itself). */
export function createStep(kind: Exclude<StepKind, "source">, id: string): Step {
  switch (kind) {
    case "filter":
      return { id, kind, match: "all", conditions: [defaultCondition()] }
    case "with_column":
      return { id, kind, name: "", expr: defaultExpr("operand") }
    case "select":
      return { id, kind, columns: [] }
    case "drop":
      return { id, kind, columns: [] }
    case "rename":
      return { id, kind, renames: [{ from: "", to: "" }] }
    case "cast":
      return { id, kind, casts: [{ column: "", dtype: "Float64" }] }
    case "sort":
      return { id, kind, keys: [{ column: "", descending: false }], nullsLast: false }
    case "unique":
      return { id, kind, columns: [], keep: "first" }
    case "group_by":
      return { id, kind, keys: [], aggregations: [{ column: "", agg: "sum", name: "" }] }
    case "join":
      return { id, kind, input: "", how: "left", leftOn: [], rightOn: [], suffix: "_right" }
    case "concat":
      return { id, kind, inputs: [], how: "vertical" }
    case "fill_null":
      return { id, kind, columns: [], fill: { kind: "value", value: literal("number", 0) } }
    case "limit":
      return { id, kind, n: 100 }
    case "variable":
      return { id, kind, name: "", value: literal("number", 0) }
    case "pivot":
      return { id, kind, index: [], on: "", columns: [], values: "", agg: "sum" }
    case "unpivot":
      return { id, kind, on: [], index: [], variableName: "variable", valueName: "value" }
  }
}

export function kindLabel(kind: StepKind): string {
  if (kind === "source") return "Start from"
  return STEP_CATALOGUE.find((info) => info.kind === kind)?.label ?? kind
}

type Shape = "string" | "number" | "boolean" | "array" | "object" | "null"

function isShape(value: unknown, shape: Shape): boolean {
  switch (shape) {
    case "null":
      return value === null
    case "array":
      return Array.isArray(value)
    case "object":
      return typeof value === "object" && value !== null && !Array.isArray(value)
    default:
      return typeof value === shape
  }
}

function fieldProblem(record: Record<string, unknown>, field: string, shape: Shape, where: string): string | null {
  return isShape(record[field], shape) ? null : `${where} is missing its "${field}" setting.`
}

const LITERAL_VALUE_SHAPE: Record<LiteralType, Shape> = {
  number: "number",
  text: "string",
  boolean: "boolean",
  date: "string",
  null: "null",
}

function literalProblem(operand: Record<string, unknown>, where: string): string | null {
  const type = operand.type
  if (typeof type !== "string" || !Object.hasOwn(LITERAL_VALUE_SHAPE, type)) return `${where} has an unsupported value type.`
  // The renderer accepts a null literal with no value field at all.
  if (type === "null" && operand.value === undefined) return null
  if (!isShape(operand.value, LITERAL_VALUE_SHAPE[type as LiteralType])) return `${where} has a malformed value.`
  return null
}

function operandProblem(value: unknown, where: string, depth = 1): string | null {
  if (!isShape(value, "object")) return `${where} is missing its value.`
  const operand = value as Record<string, unknown>
  if (operand.kind === "literal") return literalProblem(operand, where)
  if (operand.kind === "column" || operand.kind === "variable") {
    return typeof operand.name === "string" ? null : `${where} has a malformed value.`
  }
  if (operand.kind === "expr") return exprProblem(operand.expr, `${where} expression`, depth + 1)
  return `${where} has a malformed value.`
}

/** A literal-only operand position (membership lists, variables, function arguments). */
function plainLiteralProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object") || (value as Record<string, unknown>).kind !== "literal") return `${where} must be a plain value.`
  return literalProblem(value as Record<string, unknown>, where)
}

function conditionsProblem(value: unknown, where: string, depth = 1): string | null {
  if (!Array.isArray(value)) return `${where} is missing its conditions.`
  for (const [index, raw] of value.entries()) {
    if (!isShape(raw, "object")) return `${where} condition ${index + 1} is malformed.`
    const condition = raw as Record<string, unknown>
    if (typeof condition.column !== "string" || typeof condition.operator !== "string") {
      return `${where} condition ${index + 1} is malformed.`
    }
    if (condition.value !== undefined) {
      const problem = operandProblem(condition.value, `${where} condition ${index + 1}`, depth)
      if (problem) return problem
    }
    if (condition.values !== undefined) {
      if (!Array.isArray(condition.values)) return `${where} condition ${index + 1} has malformed values.`
      for (const entry of condition.values) {
        if (!isShape(entry, "object") || (entry as Record<string, unknown>).kind !== "literal") {
          return `${where} condition ${index + 1} has malformed values.`
        }
        const problem = literalProblem(entry as Record<string, unknown>, `${where} condition ${index + 1}`)
        if (problem) return problem
      }
    }
  }
  return null
}

function rowsProblem(value: unknown, fields: Array<[string, Shape]>, where: string): string | null {
  if (!Array.isArray(value)) return `${where} is missing its rows.`
  for (const [index, raw] of value.entries()) {
    if (!isShape(raw, "object")) return `${where} row ${index + 1} is malformed.`
    for (const [field, shape] of fields) {
      if (!isShape((raw as Record<string, unknown>)[field], shape)) return `${where} row ${index + 1} is missing "${field}".`
    }
  }
  return null
}

function stringListProblem(value: unknown, where: string): string | null {
  return Array.isArray(value) && value.every((v) => typeof v === "string") ? null : `${where} must list column names.`
}

export function exprProblem(value: unknown, where: string, depth = 1): string | null {
  if (!isShape(value, "object")) return `${where} is missing its expression.`
  if (depth > MAX_EXPR_DEPTH) return `${where} nests more than ${MAX_EXPR_DEPTH} levels deep.`
  const expr = value as Record<string, unknown>
  switch (expr.type) {
    case "operand":
      if (expr.text !== undefined && typeof expr.text !== "string") return `${where} has malformed formula text.`
      return operandProblem(expr.operand, where, depth)
    case "binary":
      if (typeof expr.op !== "string") return `${where} is missing its operator.`
      if (expr.text !== undefined && typeof expr.text !== "string") return `${where} has malformed formula text.`
      return operandProblem(expr.left, `${where} left side`, depth) ?? operandProblem(expr.right, `${where} right side`, depth)
    case "function":
      if (typeof expr.fn !== "string") return `${where} is missing its function.`
      if (expr.text !== undefined && typeof expr.text !== "string") return `${where} has malformed formula text.`
      if (!Array.isArray(expr.args) || expr.args.some((a) => plainLiteralProblem(a, where) !== null)) return `${where} has malformed function arguments.`
      return operandProblem(expr.operand, where, depth)
    case "conditional":
      if (typeof expr.match !== "string") return `${where} is missing its match mode.`
      return (
        conditionsProblem(expr.conditions, where, depth)
        ?? operandProblem(expr.then, `${where} then branch`, depth)
        ?? operandProblem(expr.otherwise, `${where} otherwise branch`, depth)
      )
    case "window": {
      // The renderer ignores `column` for columnless aggregates, defaults an
      // order key's `descending` to false, and the forms canonicalise both.
      if (typeof expr.agg !== "string") return `${where} is missing its aggregate.`
      const columnless = COLUMNLESS_AGGREGATIONS.has(expr.agg as WindowAggregation)
      if (typeof expr.column !== "string" && !(columnless && expr.column === undefined)) return `${where} is missing its aggregate.`
      if (expr.descending !== undefined && typeof expr.descending !== "boolean") return `${where} has a malformed rank direction.`
      if (expr.quantile !== undefined && typeof expr.quantile !== "number") return `${where} has a malformed quantile.`
      const over = stringListProblem(expr.over, `${where} over`)
      if (over || expr.orderBy === undefined) return over
      const order = rowsProblem(expr.orderBy, [["column", "string"]], `${where} order`)
      if (order) return order
      const badDirection = (expr.orderBy as Array<Record<string, unknown>>).some((k) => k.descending !== undefined && typeof k.descending !== "boolean")
      return badDirection ? `${where} order has a malformed direction.` : null
    }
    case "concat":
      if (expr.separator !== undefined && typeof expr.separator !== "string") return `${where} has a malformed separator.`
      if (!Array.isArray(expr.parts)) return `${where} is missing its parts.`
      for (const [index, part] of expr.parts.entries()) {
        const problem = operandProblem(part, `${where} part ${index + 1}`, depth)
        if (problem) return problem
      }
      return null
    default:
      return `${where} has an unknown expression type.`
  }
}

const PIVOT_AGGREGATION_VALUES: ReadonlySet<string> = new Set(PIVOT_AGGREGATIONS.map((a) => a.value))

const REQUIRED_FIELDS: Record<StepKind, Array<[string, Shape]>> = {
  source: [["input", "string"]],
  filter: [["match", "string"], ["conditions", "array"]],
  with_column: [["name", "string"], ["expr", "object"]],
  select: [["columns", "array"]],
  drop: [["columns", "array"]],
  rename: [["renames", "array"]],
  cast: [["casts", "array"]],
  sort: [["keys", "array"], ["nullsLast", "boolean"]],
  unique: [["columns", "array"], ["keep", "string"]],
  group_by: [["keys", "array"], ["aggregations", "array"]],
  join: [["input", "string"], ["how", "string"], ["leftOn", "array"], ["rightOn", "array"], ["suffix", "string"]],
  concat: [["inputs", "array"], ["how", "string"]],
  fill_null: [["columns", "array"], ["fill", "object"]],
  limit: [["n", "number"]],
  variable: [["name", "string"], ["value", "object"]],
  pivot: [["index", "array"], ["on", "string"], ["columns", "array"], ["values", "string"], ["agg", "string"]],
  unpivot: [["on", "array"], ["index", "array"], ["variableName", "string"], ["valueName", "string"]],
}

/**
 * Why a persisted step cannot be shown in a form, or null when its shape is
 * usable, nested settings included. The backend keeps unrenderable steps
 * behind an incomplete body, so the editor must survive any JSON that
 * reaches it without assuming nested shapes.
 */
export function stepProblem(step: unknown): string | null {
  if (!isShape(step, "object")) return "This step is not an object."
  const record = step as Record<string, unknown>
  const kind = record.kind
  if (typeof kind !== "string" || !Object.hasOwn(REQUIRED_FIELDS, kind)) return `Unknown step kind ${JSON.stringify(kind)}.`
  const where = `The ${kindLabel(kind as StepKind).toLowerCase()} step`
  for (const [field, shape] of REQUIRED_FIELDS[kind as StepKind]) {
    const problem = fieldProblem(record, field, shape, where)
    if (problem) return problem
  }
  switch (kind as StepKind) {
    case "filter":
      return conditionsProblem(record.conditions, where, 0)
    case "with_column":
      return exprProblem(record.expr, where)
    case "select":
    case "drop":
      return (
        stringListProblem(record.columns, `${where} columns`)
        ?? (record.dtypes === undefined ? null : stringListProblem(record.dtypes, `${where} column types`))
      )
    case "unique":
    case "fill_null":
      return (
        stringListProblem(record.columns, `${where} columns`)
        ?? (kind === "fill_null" ? fillProblem(record.fill, where) : null)
      )
    case "rename":
      return rowsProblem(record.renames, [["from", "string"], ["to", "string"]], where)
    case "cast":
      return rowsProblem(record.casts, [["column", "string"], ["dtype", "string"]], where)
    case "sort":
      return rowsProblem(record.keys, [["column", "string"], ["descending", "boolean"]], where)
    case "group_by":
      return (
        stringListProblem(record.keys, `${where} keys`)
        ?? rowsProblem(record.aggregations, [["agg", "string"]], `${where} aggregations`)
        ?? aggregationsProblem(record.aggregations as Array<Record<string, unknown>>, where)
      )
    case "pivot":
      return (
        stringListProblem(record.index, `${where} index`)
        ?? rowsProblem(record.columns, [["value", "object"], ["name", "string"]], `${where} columns`)
        ?? (record.columns as Array<Record<string, unknown>>)
          .map((entry, index) => plainLiteralProblem(entry.value, `${where} column ${index + 1}`))
          .find((problem) => problem !== null)
        ?? (PIVOT_AGGREGATION_VALUES.has(record.agg as string) ? null : `${where} has an unsupported aggregate.`)
      )
    case "unpivot":
      return stringListProblem(record.on, `${where} columns`) ?? stringListProblem(record.index, `${where} index`)
    case "join":
      if (record.validate !== undefined && typeof record.validate !== "string") return `${where} has a malformed validation.`
      if (record.maintainOrder !== undefined && typeof record.maintainOrder !== "string") return `${where} has a malformed row order.`
      return stringListProblem(record.leftOn, `${where} left keys`) ?? stringListProblem(record.rightOn, `${where} right keys`)
    case "concat":
      return stringListProblem(record.inputs, `${where} inputs`)
    case "variable":
      return plainLiteralProblem(record.value, where)
    default:
      return null
  }
}

function aggregationsProblem(entries: Array<Record<string, unknown>>, where: string): string | null {
  for (const [index, entry] of entries.entries()) {
    const label = `${where} aggregation ${index + 1}`
    if (entry.quantile !== undefined && typeof entry.quantile !== "number") return `${label} has a malformed quantile.`
    if (entry.dtype !== undefined) {
      if (typeof entry.dtype !== "string" || typeof entry.suffix !== "string") return `${label} is missing its column type or suffix.`
      if (entry.column !== undefined || entry.name !== undefined || entry.where !== undefined) return `${label} mixes a column type with a column.`
      continue
    }
    if (typeof entry.column !== "string" || typeof entry.name !== "string") return `${label} is missing "column" or "name".`
    if (entry.where !== undefined) {
      if (!isShape(entry.where, "object")) return `${label} has a malformed row filter.`
      const group = entry.where as Record<string, unknown>
      if (typeof group.match !== "string") return `${label} has a malformed row filter.`
      const problem = conditionsProblem(group.conditions, `${label} filter`)
      if (problem) return problem
    }
  }
  return null
}

function fillProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object")) return `${where} is missing its fill.`
  const fill = value as Record<string, unknown>
  if (fill.kind === "value") return operandProblem(fill.value, `${where} fill`, 0)
  if (fill.kind === "strategy") return typeof fill.strategy === "string" ? null : `${where} is missing its fill strategy.`
  return `${where} has a malformed fill.`
}

/**
 * The step with every optional-on-the-backend field filled in, so forms and
 * summaries can read canonical shapes: a null literal carries `value: null`,
 * a window always has a `column` and each order key a `descending`, and a
 * concat has a `separator`. Only steps `stepProblem` accepts are canonicalised.
 */
export function canonicalStep(step: Step): Step {
  switch (step.kind) {
    case "with_column":
      return { ...step, expr: canonicalExpr(step.expr) }
    case "filter":
      return { ...step, conditions: step.conditions.map(canonicalCondition) }
    case "fill_null":
      return step.fill.kind === "value" ? { ...step, fill: { kind: "value", value: canonicalOperand(step.fill.value) } } : step
    case "group_by":
      return {
        ...step,
        aggregations: step.aggregations.map((a) =>
          "dtype" in a || !a.where ? a : { ...a, where: { ...a.where, conditions: a.where.conditions.map(canonicalCondition) } },
        ),
      }
    default:
      return step
  }
}

function canonicalCondition(condition: Condition): Condition {
  return condition.value === undefined ? condition : { ...condition, value: canonicalOperand(condition.value) }
}

function canonicalOperand(operand: Operand): Operand {
  if (operand.kind === "literal" && operand.type === "null" && operand.value !== null) return literal("null", null)
  if (operand.kind === "expr") return { kind: "expr", expr: canonicalExpr(operand.expr) }
  return operand
}

function canonicalExpr(expr: Expr): Expr {
  switch (expr.type) {
    case "window": {
      const orderBy = expr.orderBy?.map((k) => ({ column: k.column, descending: k.descending ?? false }))
      return { ...expr, column: expr.column ?? "", ...(orderBy ? { orderBy } : {}) }
    }
    case "concat":
      return { ...expr, parts: expr.parts.map(canonicalOperand), separator: expr.separator ?? "" }
    case "conditional":
      return {
        ...expr,
        conditions: expr.conditions.map(canonicalCondition),
        then: canonicalOperand(expr.then),
        otherwise: canonicalOperand(expr.otherwise),
      }
    case "operand":
      return { ...expr, operand: canonicalOperand(expr.operand) }
    case "binary":
      return { ...expr, left: canonicalOperand(expr.left), right: canonicalOperand(expr.right) }
    case "function":
      return { ...expr, operand: canonicalOperand(expr.operand) }
  }
}

/** The display label for a schema index: index 0 is the start card. */
export function stepDisplayLabel(index: number): string {
  return index === 0 ? "Start from" : `Step ${index}`
}

/** Names defined by `variable` steps before `index`. */
export function variablesBefore(steps: Step[], index: number): string[] {
  return steps
    .slice(0, index)
    .filter((step): step is Extract<Step, { kind: "variable" }> => stepProblem(step) === null && step.kind === "variable")
    .map((step) => step.name)
    .filter((name) => name.length > 0)
}
