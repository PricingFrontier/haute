/**
 * Low-code Polars step schema (browser mirror of `haute._polars_steps`).
 *
 * The backend renderer is the single source of truth for what a step list
 * means; these types only describe the JSON the editor writes into
 * `config.steps`. Every step renders to exactly one line of Polars code, so
 * `steps[i]` is line `i + 1` of the generated program.
 */

export type LiteralType = "number" | "text" | "boolean" | "date"

export type LiteralOperand = {
  kind: "literal"
  type: LiteralType
  value: number | string | boolean
}

export type ColumnOperand = { kind: "column"; name: string }
export type VariableOperand = { kind: "variable"; name: string }
export type Operand = LiteralOperand | ColumnOperand | VariableOperand

export type ConditionOperator =
  | "eq" | "ne" | "gt" | "ge" | "lt" | "le"
  | "is_null" | "is_not_null"
  | "is_in" | "not_in"
  | "contains" | "starts_with" | "ends_with"

export type Condition = {
  column: string
  operator: ConditionOperator
  value?: Operand
  values?: LiteralOperand[]
}

export type MatchMode = "all" | "any"
export type BinaryOperator = "+" | "-" | "*" | "/" | "//" | "%" | "**"
export type Aggregation =
  | "sum" | "mean" | "min" | "max" | "median" | "std" | "var"
  | "count" | "n_unique" | "first" | "last" | "len"
export type CastDtype = "Int64" | "Float64" | "String" | "Boolean" | "Date" | "Datetime" | "Categorical"
export type JoinHow = "inner" | "left" | "right" | "full" | "semi" | "anti" | "cross"
export type FillStrategy = "forward" | "backward" | "min" | "max" | "mean" | "zero" | "one"
export type FunctionName =
  | "abs" | "floor" | "ceil" | "sqrt" | "log" | "exp"
  | "round" | "clip" | "fill_null" | "cast"
  | "upper" | "lower" | "strip" | "length"
  | "year" | "month" | "day"

export type Expr =
  | { type: "operand"; operand: Operand }
  | { type: "binary"; left: Operand; op: BinaryOperator; right: Operand }
  | { type: "function"; fn: FunctionName; operand: Operand; args: LiteralOperand[] }
  | { type: "conditional"; match: MatchMode; conditions: Condition[]; then: Operand; otherwise: Operand }
  | { type: "window"; agg: Aggregation; column: string; over: string[] }

export type StepBase = { id: string }

export type SourceStep = StepBase & { kind: "source"; input: string }
export type FilterStep = StepBase & { kind: "filter"; match: MatchMode; conditions: Condition[] }
export type WithColumnStep = StepBase & { kind: "with_column"; name: string; expr: Expr }
export type SelectStep = StepBase & { kind: "select"; columns: string[] }
export type DropStep = StepBase & { kind: "drop"; columns: string[] }
export type RenameStep = StepBase & { kind: "rename"; renames: Array<{ from: string; to: string }> }
export type CastStep = StepBase & { kind: "cast"; casts: Array<{ column: string; dtype: CastDtype }> }
export type SortStep = StepBase & {
  kind: "sort"
  keys: Array<{ column: string; descending: boolean }>
  nullsLast: boolean
}
export type UniqueStep = StepBase & { kind: "unique"; columns: string[]; keep: "first" | "last" | "any" }
export type GroupByStep = StepBase & {
  kind: "group_by"
  keys: string[]
  aggregations: Array<{ column: string; agg: Aggregation; name: string }>
}
export type JoinStep = StepBase & {
  kind: "join"
  input: string
  how: JoinHow
  leftOn: string[]
  rightOn: string[]
  suffix: string
}
export type ConcatStep = StepBase & { kind: "concat"; inputs: string[]; how: "vertical" | "diagonal" }
export type FillNullStep = StepBase & {
  kind: "fill_null"
  columns: string[]
  fill: { kind: "value"; value: Operand } | { kind: "strategy"; strategy: FillStrategy }
}
export type LimitStep = StepBase & { kind: "limit"; n: number }
export type VariableStep = StepBase & { kind: "variable"; name: string; value: LiteralOperand }

export type Step =
  | SourceStep
  | FilterStep
  | WithColumnStep
  | SelectStep
  | DropStep
  | RenameStep
  | CastStep
  | SortStep
  | UniqueStep
  | GroupByStep
  | JoinStep
  | ConcatStep
  | FillNullStep
  | LimitStep
  | VariableStep

export type StepKind = Step["kind"]

/** Read `config.steps` as a step list, or null when the node is in code mode. */
export function readSteps(config: Record<string, unknown>): Step[] | null {
  const steps = config.steps
  return Array.isArray(steps) ? (steps as Step[]) : null
}
