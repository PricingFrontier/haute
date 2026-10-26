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
  NumberType,
  Operand,
  Step,
  StepKind,
  WindowAggregation,
  WindowOnlyAggregation,
} from "./types"
import type { StepStart } from "../../../utils/polarsStepInputs"
import { castFamily, type CastFamily } from "../../../utils/polarsDtypes"

/**
 * A kind the `Add step` menu offers: its plain-English label, what it does,
 * and the Polars call it renders to (shown beside it and matched by the
 * menu's search), or null when it renders to no Polars call.
 */
export type StepKindInfo = { kind: Exclude<StepKind, "source">; label: string; description: string; method: string | null }

/** Kinds offered by `Add step`, in menu order. `source` is the fixed first step. */
export const STEP_CATALOGUE: StepKindInfo[] = [
  { kind: "filter", label: "Filter rows", description: "Keep rows that match conditions", method: "filter" },
  { kind: "with_column", label: "Add column", description: "Compute a new or replaced column", method: "with_columns" },
  { kind: "select", label: "Keep columns", description: "Keep only the listed columns", method: "select" },
  { kind: "drop", label: "Drop columns", description: "Remove the listed columns", method: "drop" },
  { kind: "rename", label: "Rename columns", description: "Give columns new names", method: "rename" },
  { kind: "cast", label: "Change types", description: "Cast columns to another data type", method: "cast" },
  { kind: "sort", label: "Sort rows", description: "Order rows by one or more columns", method: "sort" },
  { kind: "unique", label: "Remove duplicates", description: "Keep one row per key", method: "unique" },
  { kind: "group_by", label: "Group and aggregate", description: "Summarise rows per group", method: "group_by().agg()" },
  { kind: "join", label: "Join another input", description: "Combine columns from another input", method: "join" },
  { kind: "concat", label: "Append inputs", description: "Stack rows from other inputs", method: "pl.concat" },
  { kind: "fill_null", label: "Fill missing values", description: "Replace nulls with a value or strategy", method: "fill_null" },
  { kind: "limit", label: "Limit rows", description: "Keep the first N rows", method: "head" },
  { kind: "variable", label: "Define variable", description: "Name a value for later steps", method: null },
  { kind: "pivot", label: "Pivot to columns", description: "One column per value of a category", method: "pivot" },
  { kind: "unpivot", label: "Unpivot to rows", description: "Stack several columns into name/value rows", method: "unpivot" },
  { kind: "free_code", label: "Free code", description: "Run Python statements against the current frame", method: null },
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

/** Plain aggregates; `hint` says what a label alone does not (shown in the function list). */
export const AGGREGATIONS: Array<{ value: Aggregation; label: string; hint?: string }> = [
  { value: "sum", label: "sum" },
  { value: "mean", label: "mean" },
  { value: "min", label: "minimum" },
  { value: "max", label: "maximum" },
  { value: "median", label: "median" },
  { value: "quantile", label: "quantile" },
  { value: "std", label: "standard deviation" },
  { value: "var", label: "variance" },
  { value: "count", label: "count of values", hint: "skips missing values" },
  { value: "n_unique", label: "distinct count" },
  { value: "first", label: "first" },
  { value: "last", label: "last" },
  { value: "len", label: "row count", hint: "every row, missing values included" },
]
/** Aggregates as select options, each hint after its label. */
export const AGGREGATION_OPTIONS = AGGREGATIONS.map((a) => ({ value: a.value, label: a.hint ? `${a.label} (${a.hint})` : a.label }))

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
/** Aggregates that reduce a group to one value: the only ones a window's row filter applies to. */
export const REDUCING_AGGREGATIONS: ReadonlySet<WindowAggregation> = new Set<WindowAggregation>(AGGREGATIONS.map((a) => a.value))

/** Window aggregates that take no input column. */
export const COLUMNLESS_AGGREGATIONS: ReadonlySet<WindowAggregation> = new Set<WindowAggregation>(["len", "row_number"])

export const CAST_DTYPES: CastDtype[] = [
  "Int8", "Int16", "Int32", "Int64",
  "UInt8", "UInt16", "UInt32", "UInt64",
  "Float32", "Float64",
  "String", "Boolean", "Date", "Datetime", "Categorical",
]
/** What each common cast type holds, in plain words. */
const DTYPE_WORDS: Partial<Record<CastDtype, string>> = {
  Int64: "whole number",
  Float64: "decimal number",
  String: "text",
  Boolean: "true/false",
  Date: "date",
  Datetime: "date and time",
  Categorical: "category",
}
/** The common cast types first, each with its plain meaning, then the other widths. */
const COMMON_DTYPES: CastDtype[] = ["Int64", "Float64", "String", "Boolean", "Date", "Datetime", "Categorical"]
/** The cast types as select options: the common types first, then the other widths. */
export const DTYPE_OPTIONS: Array<{ value: CastDtype; label: string }> = [
  ...COMMON_DTYPES.map((dtype) => ({ value: dtype, label: DTYPE_WORDS[dtype] ? `${dtype} (${DTYPE_WORDS[dtype]})` : dtype })),
  ...CAST_DTYPES.filter((dtype) => !COMMON_DTYPES.includes(dtype)).map((dtype) => ({ value: dtype, label: dtype })),
]
export const JOIN_HOW: JoinHow[] = ["inner", "left", "right", "full", "semi", "anti", "cross"]
/** Each join kind by its Polars name, with the rows it keeps. */
export const JOIN_HOW_OPTIONS: Array<{ value: JoinHow; label: string }> = [
  { value: "inner", label: "inner: only rows that match" },
  { value: "left", label: "left: every row here, with matches added" },
  { value: "right", label: "right: every row of the other input" },
  { value: "full", label: "full: every row of both" },
  { value: "semi", label: "semi: rows here that have a match" },
  { value: "anti", label: "anti: rows here with no match" },
  { value: "cross", label: "cross: every combination of rows" },
]
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
const JOIN_MAINTAIN_ORDER_VALUES: ReadonlySet<string> = new Set(JOIN_MAINTAIN_ORDER.map((o) => o.value))
/** The row order a new join of each kind gets (the renderer's `DEFAULT_JOIN_ORDER`). */
export const DEFAULT_JOIN_ORDER: Record<JoinHow, JoinMaintainOrder> = {
  inner: "left_right",
  left: "left_right",
  full: "left_right",
  cross: "left_right",
  right: "right_left",
  semi: "left",
  anti: "left",
}
/** A join's row order after its kind changes: a default follows the kind; a choice stays. */
export function joinOrderAfterHowChange(order: string, from: string, to: JoinHow): JoinMaintainOrder {
  const previousDefault = DEFAULT_JOIN_ORDER[from as JoinHow]
  if (order === previousDefault || !JOIN_MAINTAIN_ORDER_VALUES.has(order)) return DEFAULT_JOIN_ORDER[to]
  return order as JoinMaintainOrder
}
export const FILL_STRATEGIES: FillStrategy[] = ["forward", "backward", "min", "max", "mean", "zero", "one"]
/** Fill strategies in plain words, each with its Polars name where it has one. */
export const FILL_STRATEGY_OPTIONS: Array<{ value: FillStrategy; label: string }> = [
  { value: "forward", label: "the previous row's value (forward)" },
  { value: "backward", label: "the next row's value (backward)" },
  { value: "min", label: "the column's minimum (min)" },
  { value: "max", label: "the column's maximum (max)" },
  { value: "mean", label: "the column's mean (mean)" },
  { value: "zero", label: "zero" },
  { value: "one", label: "one" },
]
export const LITERAL_TYPES: Array<{ value: LiteralType; label: string }> = [
  { value: "number", label: "number" },
  { value: "text", label: "text" },
  { value: "boolean", label: "true/false" },
  { value: "date", label: "date" },
  { value: "null", label: "missing (null)" },
]
/** How deep expressions may nest as operands; a step's expression is depth 1 (mirrors the renderer). */
/** What an operand may be (the renderer's `OPERAND_KINDS`). */
export const OPERAND_KINDS = ["column", "literal", "expr", "variable", "constant"] as const

export const MAX_EXPR_DEPTH = 12
/** Literal types a membership list accepts: the renderer refuses null members. */
export const LIST_LITERAL_TYPES = LITERAL_TYPES.filter((t) => t.value !== "null")

/**
 * Function argument shapes: what each argument must be, in order. `integer`
 * is zero or more; `int` may be negative (a slice start counting from the end);
 * `format` is a date format such as `%Y-%m-%d`.
 */
export type FunctionArg = "integer" | "int" | "number" | "dtype" | "text" | "format" | "number_operand" | "scalar_operand"
/** Arguments that take any operand besides a plain value of these types (the renderer's `_OPERAND_ARGUMENT_LITERALS`). */
export const OPERAND_ARGUMENT_LITERALS: Partial<Record<FunctionArg, readonly LiteralType[]>> = {
  number_operand: ["number"],
  scalar_operand: ["number", "text", "boolean"],
}
export const isOperandArgument = (arg: FunctionArg | undefined): boolean => arg !== undefined && OPERAND_ARGUMENT_LITERALS[arg] !== undefined
export const FUNCTIONS: Array<{ value: FunctionName; label: string; args: FunctionArg[] }> = [
  { value: "abs", label: "absolute value", args: [] },
  { value: "round", label: "round", args: ["integer"] },
  { value: "floor", label: "round down", args: [] },
  { value: "ceil", label: "round up", args: [] },
  { value: "sqrt", label: "square root", args: [] },
  { value: "log", label: "natural log", args: [] },
  { value: "exp", label: "exponential", args: [] },
  { value: "clip", label: "clip between", args: ["number_operand", "number_operand"] },
  { value: "fill_null", label: "fill missing with", args: ["scalar_operand"] },
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
  { value: "to_date", label: "text to date", args: ["format"] },
  { value: "to_datetime", label: "text to date and time", args: ["format"] },
  { value: "try_to_date", label: "text to date (null on failure)", args: ["format"] },
  { value: "try_to_datetime", label: "text to date and time (null on failure)", args: ["format"] },
]

/** The format a text column's dates start from: ISO dates, and ISO date and time. */
export const DATE_FORMAT_DEFAULTS: Record<"Date" | "Datetime", string> = {
  Date: "%Y-%m-%d",
  Datetime: "%Y-%m-%d %H:%M:%S",
}
/** Whether a cast target is reached from text by parsing it with a format. */
export const parsesText = (dtype: CastDtype): dtype is "Date" | "Datetime" => dtype === "Date" || dtype === "Datetime"

/** The source column types the cast table tells apart (the renderer's `CAST_FAMILIES`). */
export const CAST_FAMILIES: CastFamily[] = [
  "integer", "float", "decimal", "boolean", "text", "category",
  "date", "datetime", "duration", "time", "binary", "null",
]
/**
 * The Change types targets Polars 2 refuses for each source column type, even
 * with `strict=False` (the renderer's `CAST_REFUSALS`): a text column reaches
 * Date and Datetime only by parsing with a format.
 */
export const CAST_REFUSALS: Record<CastFamily, CastDtype[]> = {
  integer: ["Categorical"],
  float: ["Categorical"],
  decimal: ["Date", "Datetime", "Categorical"],
  boolean: ["Categorical"],
  text: ["Boolean", "Date", "Datetime"],
  category: ["Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64", "Float32", "Float64", "Boolean", "Date", "Datetime"],
  date: ["Boolean", "Categorical"],
  datetime: ["Boolean", "Categorical"],
  duration: ["String", "Boolean", "Date", "Datetime", "Categorical"],
  time: ["Boolean", "Date", "Datetime", "Categorical"],
  binary: ["Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64", "Float32", "Float64", "Boolean", "Date", "Datetime", "Categorical"],
  null: [],
}

/** The types Change types offers a column of `dtype`: all of them while its type is unknown. */
export function castTargetsFor(dtype: string | null): CastDtype[] {
  const family = castFamily(dtype)
  if (family === null) return CAST_DTYPES
  const refused = new Set(CAST_REFUSALS[family])
  // Text reaches a date by parsing it, which the entry's format says how.
  return CAST_DTYPES.filter((target) => !refused.has(target) || (family === "text" && parsesText(target)))
}

/** Whether `agg` takes a column of `dtype`: Polars 2 refuses std and var of a duration. */
export function aggregationTakes(agg: string, dtype: string | null): boolean {
  return !((agg === "std" || agg === "var") && castFamily(dtype) === "duration")
}

const NUMERIC_FAMILIES: ReadonlySet<CastFamily> = new Set<CastFamily>(["integer", "float", "decimal", "boolean", "null"])

/** Whether `fn` takes a receiver of `dtype`: log and exp take numbers, the parses text. */
export function functionTakes(fn: FunctionName, dtype: string | null): boolean {
  const family = castFamily(dtype)
  if (family === null) return true
  if (fn === "log" || fn === "exp") return NUMERIC_FAMILIES.has(family)
  if (fn === "to_date" || fn === "to_datetime" || fn === "try_to_date" || fn === "try_to_datetime") return family === "text"
  return true
}

/**
 * What Polars 2 refuses in `expr` where the editor knows a column's type, or
 * null: a cast it does not make (text to a date, which a parse function does
 * instead; a category to anything but text), log or exp of something that is
 * not a number, and a parse of something that is not text.
 */
export function exprTypeProblem(expr: Expr, typeOf: (name: string) => string | null): string | null {
  const operandProblem = (operand: Operand): string | null => (operand.kind === "expr" ? exprTypeProblem(operand.expr, typeOf) : null)
  switch (expr.type) {
    case "function": {
      const inner = operandProblem(expr.operand) ?? expr.args.map(operandProblem).find((problem) => problem !== null)
      if (inner) return inner
      if (expr.operand.kind !== "column") return null
      const name = expr.operand.name
      const dtype = typeOf(name)
      const family = castFamily(dtype)
      if (family === null || dtype === null) return null
      if (!functionTakes(expr.fn, dtype)) {
        return expr.fn === "log" || expr.fn === "exp"
          ? `${expr.fn}() takes numbers, and ${name} is ${dtype}.`
          : `${expr.fn}() parses text, and ${name} is ${dtype}.`
      }
      const target = expr.args[0]
      if ((expr.fn === "cast" || expr.fn === "try_cast") && target?.kind === "literal" && typeof target.value === "string") {
        const dtypeTarget = target.value as CastDtype
        if (!CAST_REFUSALS[family].includes(dtypeTarget)) return null
        if (family === "text" && parsesText(dtypeTarget)) {
          const parse = `${expr.fn === "try_cast" ? "try_" : ""}${dtypeTarget === "Date" ? "to_date" : "to_datetime"}`
          return `${name} is text, which Polars 2 does not cast to ${dtypeTarget}: parse it with ${parse}(${name}, '${DATE_FORMAT_DEFAULTS[dtypeTarget]}').`
        }
        if (family === "category") return `${name} is a category, which Polars 2 casts only to text: cast it to String first.`
        return `Polars 2 does not cast ${name} from ${dtype} to ${dtypeTarget}.`
      }
      return null
    }
    case "operand":
      return operandProblem(expr.operand)
    case "binary":
      return operandProblem(expr.left) ?? operandProblem(expr.right)
    case "conditional":
      return operandProblem(expr.then) ?? operandProblem(expr.otherwise)
    case "concat":
      return expr.parts.map(operandProblem).find((problem) => problem !== null) ?? null
    case "window": {
      if (expr.of?.kind !== "column") return expr.of ? operandProblem(expr.of) : null
      const name = expr.of.name
      return aggregationTakes(expr.agg, name ? typeOf(name) : null)
        ? null
        : `${expr.agg} takes numbers, and ${name} is a duration: turn it into days first (total_days).`
    }
  }
}

/** The kinds of number a membership list holds (the renderer's `NUMBER_TYPES`). */
export const NUMBER_TYPES: Array<{ value: NumberType; label: string }> = [
  { value: "integer", label: "whole numbers (integer)" },
  { value: "float", label: "numbers (float)" },
  { value: "decimal", label: "exact decimals (decimal)" },
]
/** The kind of number a column of `dtype` holds, or null when its type is unknown or not a number. */
export function numberTypeOf(dtype: string | null): NumberType | null {
  const family = castFamily(dtype)
  return family === "integer" || family === "float" || family === "decimal" ? family : null
}

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
    case "number_operand":
    case "scalar_operand":
      return literal("number", 0)
    case "dtype":
      return literal("text", "Float64")
    case "text":
      return literal("text", "")
    case "format":
      return literal("text", DATE_FORMAT_DEFAULTS.Date)
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
      return { type, agg: "sum", of: operand, over: [] }
    case "concat":
      return { type, parts: [operand, { kind: "column", name: "" }], separator: " " }
  }
}

/** The name an aggregation takes from its function and column, until one is typed. */
export function suggestedAggregationName(entry: { column: string; agg: Aggregation }): string {
  if (entry.agg === "len") return "row_count"
  return entry.column ? `${entry.column}_${entry.agg}` : ""
}

/** Build a fresh step of `kind` (never `source`, which the editor seeds itself). */
export function createStep(kind: Exclude<StepKind, "source">, id: string): Step {
  switch (kind) {
    case "filter":
      return { id, kind, match: "all", conditions: [defaultCondition()] }
    case "with_column":
      return { id, kind, name: "", expr: defaultExpr("binary") }
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
      return { id, kind, input: "", how: "left", leftOn: [], rightOn: [], suffix: "_right", maintainOrder: DEFAULT_JOIN_ORDER.left }
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
    case "free_code":
      return { id, kind, code: "" }
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
  if (operand.kind === "column" || operand.kind === "variable" || operand.kind === "constant") {
    return typeof operand.name === "string" ? null : `${where} has a malformed value.`
  }
  if (operand.kind === "expr") return exprProblem(operand.expr, `${where} expression`, depth + 1)
  return `${where} has a malformed value.`
}

/** A function argument: a plain value or a global constant. */
function argumentProblem(value: unknown, where: string): string | null {
  if (isShape(value, "object") && (value as Record<string, unknown>).kind === "constant") {
    return typeof (value as Record<string, unknown>).name === "string" ? null : `${where} has a malformed value.`
  }
  return plainLiteralProblem(value, where)
}

/** A literal-only operand position (membership lists, variables, pivot values). */
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
    if (condition.numberType !== undefined && !NUMBER_TYPES.some((t) => t.value === condition.numberType)) {
      return `${where} condition ${index + 1} has an unknown number type.`
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
      if (!Array.isArray(expr.args)) return `${where} has malformed function arguments.`
      {
        const kinds = FUNCTIONS.find((f) => f.value === expr.fn)?.args ?? []
        for (const [index, arg] of expr.args.entries()) {
          // An operand argument takes any operand; the others a plain value or a constant.
          if (isOperandArgument(kinds[index])) {
            const problem = operandProblem(arg, `${where} argument ${index + 1}`, depth)
            if (problem) return problem
          } else if (argumentProblem(arg, where) !== null) {
            return `${where} has malformed function arguments.`
          }
        }
      }
      return operandProblem(expr.operand, where, depth)
    case "conditional":
      if (typeof expr.match !== "string") return `${where} is missing its match mode.`
      return (
        conditionsProblem(expr.conditions, where, depth)
        ?? operandProblem(expr.then, `${where} then branch`, depth)
        ?? operandProblem(expr.otherwise, `${where} otherwise branch`, depth)
      )
    case "window": {
      // The renderer defaults an order key's `descending` to false, and the
      // forms canonicalise it.
      if (typeof expr.agg !== "string") return `${where} is missing its aggregate.`
      if (COLUMNLESS_AGGREGATIONS.has(expr.agg as WindowAggregation)) {
        if (expr.of !== undefined) return `${where} counts rows and takes no value.`
      } else {
        if (!isShape(expr.of, "object")) return `${where} is missing the value it aggregates.`
        const of = expr.of as Record<string, unknown>
        if (of.kind !== "column" && of.kind !== "expr") return `${where} aggregates a column or an expression.`
        const problem = operandProblem(of, `${where} value`, depth)
        if (problem) return problem
      }
      if (expr.descending !== undefined && typeof expr.descending !== "boolean") return `${where} has a malformed rank direction.`
      if (expr.quantile !== undefined && typeof expr.quantile !== "number") return `${where} has a malformed quantile.`
      if (expr.where !== undefined) {
        const filter = rowFilterProblem(expr.where, where)
        if (filter) return filter
      }
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
  join: [["input", "string"], ["how", "string"], ["leftOn", "array"], ["rightOn", "array"], ["suffix", "string"], ["maintainOrder", "string"]],
  concat: [["inputs", "array"], ["how", "string"]],
  fill_null: [["columns", "array"], ["fill", "object"]],
  limit: [["n", "number"]],
  variable: [["name", "string"], ["value", "object"]],
  pivot: [["index", "array"], ["on", "string"], ["columns", "array"], ["values", "string"], ["agg", "string"]],
  unpivot: [["on", "array"], ["index", "array"], ["variableName", "string"], ["valueName", "string"]],
  free_code: [["code", "string"]],
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
  if (typeof record.id !== "string" || record.id.length === 0) return "This step is missing its id."
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
      return (
        rowsProblem(record.casts, [["column", "string"], ["dtype", "string"]], where)
        ?? ((record.casts as Array<Record<string, unknown>>).some((entry) => entry.format !== undefined && typeof entry.format !== "string")
          ? `${where} has a malformed format.`
          : null)
      )
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
      if (!JOIN_MAINTAIN_ORDER_VALUES.has(record.maintainOrder as string)) return `${where} has an unknown row order.`
      return stringListProblem(record.leftOn, `${where} left keys`) ?? stringListProblem(record.rightOn, `${where} right keys`)
    case "concat":
      return stringListProblem(record.inputs, `${where} inputs`)
    case "variable":
      return plainLiteralProblem(record.value, where)
    default:
      return null
  }
}

/** A row filter (`{match, conditions}`) on an aggregation or a window. */
function rowFilterProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object")) return `${where} has a malformed row filter.`
  const group = value as Record<string, unknown>
  if (typeof group.match !== "string") return `${where} has a malformed row filter.`
  return conditionsProblem(group.conditions, `${where} filter`)
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
      const problem = rowFilterProblem(entry.where, label)
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
 * a window's order key a `descending`, and a concat a `separator`. Only steps
 * `stepProblem` accepts are canonicalised.
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
      const where = expr.where ? { where: { ...expr.where, conditions: expr.where.conditions.map(canonicalCondition) } } : {}
      return { ...expr, ...(expr.of ? { of: canonicalOperand(expr.of) } : {}), ...(orderBy ? { orderBy } : {}), ...where }
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
      return { ...expr, operand: canonicalOperand(expr.operand), args: expr.args.map(canonicalOperand) }
  }
}

/** The display label for a schema index: index 0 is the start card. */
/**
 * What a step at schema `index` is called in messages. In `input` mode index
 * 0 is the start card; in `frame` mode every index is a numbered step.
 */
export function stepDisplayLabel(index: number, start: StepStart = "input"): string {
  if (start === "frame") return `Step ${index + 1}`
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
