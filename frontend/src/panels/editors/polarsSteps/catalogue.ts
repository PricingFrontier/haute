/**
 * Step catalogue: the closed vocabularies the editor offers, default steps
 * for the `Add step` menu, and one-line summaries for step cards.
 */
import type {
  Aggregation,
  BinaryOperator,
  CastDtype,
  Condition,
  ConditionOperator,
  Expr,
  FillStrategy,
  FunctionName,
  JoinHow,
  LiteralOperand,
  LiteralType,
  Operand,
  Step,
  StepKind,
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
  { value: "std", label: "standard deviation" },
  { value: "var", label: "variance" },
  { value: "count", label: "count (non-null)" },
  { value: "n_unique", label: "distinct count" },
  { value: "first", label: "first" },
  { value: "last", label: "last" },
  { value: "len", label: "row count" },
]

export const CAST_DTYPES: CastDtype[] = ["Int64", "Float64", "String", "Boolean", "Date", "Datetime", "Categorical"]
export const JOIN_HOW: JoinHow[] = ["inner", "left", "right", "full", "semi", "anti", "cross"]
export const FILL_STRATEGIES: FillStrategy[] = ["forward", "backward", "min", "max", "mean", "zero", "one"]
export const LITERAL_TYPES: Array<{ value: LiteralType; label: string }> = [
  { value: "number", label: "number" },
  { value: "text", label: "text" },
  { value: "boolean", label: "true/false" },
  { value: "date", label: "date" },
]

/** Function argument shapes: what each argument must be, in order. */
export type FunctionArg = "integer" | "number" | "scalar" | "dtype"
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
  { value: "year", label: "year of date", args: [] },
  { value: "month", label: "month of date", args: [] },
  { value: "day", label: "day of date", args: [] },
]

export function literal(type: LiteralType, value: number | string | boolean): LiteralOperand {
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
  }
}

export function defaultCondition(column = ""): Condition {
  return { column, operator: "eq", value: literal("number", 0) }
}

export function defaultArgFor(arg: FunctionArg): LiteralOperand {
  switch (arg) {
    case "integer":
    case "number":
      return literal("number", 0)
    case "scalar":
      return literal("number", 0)
    case "dtype":
      return literal("text", "Float64")
  }
}

export function defaultExpr(type: Expr["type"], column = ""): Expr {
  const operand: Operand = { kind: "column", name: column }
  switch (type) {
    case "operand":
      return { type, operand }
    case "binary":
      return { type, left: operand, op: "*", right: literal("number", 1) }
    case "function":
      return { type, fn: "abs", operand, args: [] }
    case "conditional":
      return { type, match: "all", conditions: [defaultCondition(column)], then: literal("number", 1), otherwise: literal("number", 0) }
    case "window":
      return { type, agg: "sum", column, over: [] }
  }
}

/** Build a fresh step of `kind` (never `source`, which the editor seeds itself). */
export function createStep(kind: Exclude<StepKind, "source">, id: string, firstColumn = ""): Step {
  switch (kind) {
    case "filter":
      return { id, kind, match: "all", conditions: [defaultCondition(firstColumn)] }
    case "with_column":
      return { id, kind, name: "", expr: defaultExpr("binary", firstColumn) }
    case "select":
      return { id, kind, columns: [] }
    case "drop":
      return { id, kind, columns: [] }
    case "rename":
      return { id, kind, renames: [{ from: firstColumn, to: "" }] }
    case "cast":
      return { id, kind, casts: [{ column: firstColumn, dtype: "Float64" }] }
    case "sort":
      return { id, kind, keys: [{ column: firstColumn, descending: false }], nullsLast: false }
    case "unique":
      return { id, kind, columns: [], keep: "first" }
    case "group_by":
      return { id, kind, keys: [], aggregations: [{ column: firstColumn, agg: "sum", name: "" }] }
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
  }
}

export function kindLabel(kind: StepKind): string {
  if (kind === "source") return "Start from"
  return STEP_CATALOGUE.find((info) => info.kind === kind)?.label ?? kind
}

function operandText(operand: Operand | undefined): string {
  if (!operand) return "?"
  if (operand.kind === "column") return operand.name || "?"
  if (operand.kind === "variable") return operand.name || "?"
  if (operand.type === "text") return JSON.stringify(operand.value)
  return String(operand.value)
}

function conditionText(condition: Condition): string {
  const op = CONDITION_OPERATORS.find((o) => o.value === condition.operator)
  const label = op?.label ?? condition.operator
  if (op?.takes === "none") return `${condition.column || "?"} ${label}`
  if (op?.takes === "values") return `${condition.column || "?"} ${label} ${(condition.values ?? []).map(operandText).join(", ")}`
  return `${condition.column || "?"} ${label} ${operandText(condition.value)}`
}

function exprText(expr: Expr): string {
  switch (expr.type) {
    case "operand":
      return operandText(expr.operand)
    case "binary":
      return `${operandText(expr.left)} ${expr.op} ${operandText(expr.right)}`
    case "function":
      return `${expr.fn}(${[operandText(expr.operand), ...expr.args.map(operandText)].join(", ")})`
    case "conditional":
      return `if ${expr.conditions.map(conditionText).join(expr.match === "all" ? " and " : " or ")} then ${operandText(expr.then)} else ${operandText(expr.otherwise)}`
    case "window":
      return `${expr.agg} of ${expr.column || "?"} over ${expr.over.join(", ") || "?"}`
  }
}

type Shape = "string" | "number" | "boolean" | "array" | "object"

function isShape(value: unknown, shape: Shape): boolean {
  switch (shape) {
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
}

function literalProblem(operand: Record<string, unknown>, where: string): string | null {
  const type = operand.type
  if (typeof type !== "string" || !(type in LITERAL_VALUE_SHAPE)) return `${where} has an unsupported value type.`
  if (!isShape(operand.value, LITERAL_VALUE_SHAPE[type as LiteralType])) return `${where} has a malformed value.`
  return null
}

function operandProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object")) return `${where} is missing its value.`
  const operand = value as Record<string, unknown>
  if (operand.kind === "literal") return literalProblem(operand, where)
  if (operand.kind === "column" || operand.kind === "variable") {
    return typeof operand.name === "string" ? null : `${where} has a malformed value.`
  }
  return `${where} has a malformed value.`
}

function conditionsProblem(value: unknown, where: string): string | null {
  if (!Array.isArray(value)) return `${where} is missing its conditions.`
  for (const [index, raw] of value.entries()) {
    if (!isShape(raw, "object")) return `${where} condition ${index + 1} is malformed.`
    const condition = raw as Record<string, unknown>
    if (typeof condition.column !== "string" || typeof condition.operator !== "string") {
      return `${where} condition ${index + 1} is malformed.`
    }
    if (condition.value !== undefined) {
      const problem = operandProblem(condition.value, `${where} condition ${index + 1}`)
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

function exprProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object")) return `${where} is missing its expression.`
  const expr = value as Record<string, unknown>
  switch (expr.type) {
    case "operand":
      return operandProblem(expr.operand, where)
    case "binary":
      if (typeof expr.op !== "string") return `${where} is missing its operator.`
      return operandProblem(expr.left, `${where} left side`) ?? operandProblem(expr.right, `${where} right side`)
    case "function":
      if (typeof expr.fn !== "string") return `${where} is missing its function.`
      if (!Array.isArray(expr.args) || expr.args.some((a) => operandProblem(a, where) !== null)) return `${where} has malformed function arguments.`
      return operandProblem(expr.operand, where)
    case "conditional":
      if (typeof expr.match !== "string") return `${where} is missing its match mode.`
      return (
        conditionsProblem(expr.conditions, where)
        ?? operandProblem(expr.then, `${where} then branch`)
        ?? operandProblem(expr.otherwise, `${where} otherwise branch`)
      )
    case "window":
      if (typeof expr.agg !== "string" || typeof expr.column !== "string") return `${where} is missing its aggregate.`
      return stringListProblem(expr.over, `${where} over`)
    default:
      return `${where} has an unknown expression type.`
  }
}

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
  if (typeof kind !== "string" || !(kind in REQUIRED_FIELDS)) return `Unknown step kind ${JSON.stringify(kind)}.`
  const where = `The ${kindLabel(kind as StepKind).toLowerCase()} step`
  for (const [field, shape] of REQUIRED_FIELDS[kind as StepKind]) {
    const problem = fieldProblem(record, field, shape, where)
    if (problem) return problem
  }
  switch (kind as StepKind) {
    case "filter":
      return conditionsProblem(record.conditions, where)
    case "with_column":
      return exprProblem(record.expr, where)
    case "select":
    case "drop":
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
        ?? rowsProblem(record.aggregations, [["column", "string"], ["agg", "string"], ["name", "string"]], `${where} aggregations`)
      )
    case "join":
      return stringListProblem(record.leftOn, `${where} left keys`) ?? stringListProblem(record.rightOn, `${where} right keys`)
    case "concat":
      return stringListProblem(record.inputs, `${where} inputs`)
    case "variable":
      return operandProblem(record.value, where)
    default:
      return null
  }
}

function fillProblem(value: unknown, where: string): string | null {
  if (!isShape(value, "object")) return `${where} is missing its fill.`
  const fill = value as Record<string, unknown>
  if (fill.kind === "value") return operandProblem(fill.value, `${where} fill`)
  if (fill.kind === "strategy") return typeof fill.strategy === "string" ? null : `${where} is missing its fill strategy.`
  return `${where} has a malformed fill.`
}

/** One line describing the step for its card header. */
export function summarizeStep(step: Step): string {
  switch (step.kind) {
    case "source":
      return step.input || "choose an input"
    case "filter":
      return step.conditions.map(conditionText).join(step.match === "all" ? " and " : " or ") || "no conditions"
    case "with_column":
      return `${step.name || "?"} = ${exprText(step.expr)}`
    case "select":
    case "drop":
      return step.columns.join(", ") || "no columns"
    case "rename":
      return step.renames.map((r) => `${r.from || "?"} → ${r.to || "?"}`).join(", ")
    case "cast":
      return step.casts.map((c) => `${c.column || "?"} → ${c.dtype}`).join(", ")
    case "sort":
      return step.keys.map((k) => `${k.column || "?"}${k.descending ? " desc" : ""}`).join(", ")
    case "unique":
      return step.columns.length ? `by ${step.columns.join(", ")}` : "all columns"
    case "group_by":
      return `by ${step.keys.join(", ") || "?"}: ${step.aggregations.map((a) => `${a.name || "?"} = ${a.agg}(${a.column || "?"})`).join(", ")}`
    case "join":
      return `${step.how} join ${step.input || "?"} on ${step.leftOn.join(", ") || (step.how === "cross" ? "everything" : "?")}`
    case "concat":
      return step.inputs.join(", ") || "no inputs"
    case "fill_null":
      return `${step.columns.length ? step.columns.join(", ") : "all columns"} with ${step.fill.kind === "value" ? operandText(step.fill.value) : step.fill.strategy}`
    case "limit":
      return `${step.n} rows`
    case "variable":
      return `${step.name || "?"} = ${operandText(step.value)}`
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
