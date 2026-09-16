/**
 * Low-code Polars step schema (browser mirror of `haute._polars_steps`).
 *
 * The backend renderer is the single source of truth for what a step list
 * means; these types only describe the JSON the editor writes into
 * `config.steps`. Every step renders to exactly one line of Polars code, so
 * `steps[i]` is line `i + 1` of the generated program.
 */

export type LiteralType = "number" | "text" | "boolean" | "date" | "null"

export type LiteralOperand = {
  kind: "literal"
  type: LiteralType
  value: number | string | boolean | null
}

export type ColumnOperand = { kind: "column"; name: string }
export type VariableOperand = { kind: "variable"; name: string }
/** A nested expression; allowed wherever an operand is, except membership lists, variables and function arguments. */
export type ExprOperand = { kind: "expr"; expr: Expr }
export type Operand = LiteralOperand | ColumnOperand | VariableOperand | ExprOperand

export type ConditionOperator =
  | "eq" | "ne" | "gt" | "ge" | "lt" | "le"
  | "is_null" | "is_not_null"
  | "is_in" | "not_in"
  | "contains" | "starts_with" | "ends_with" | "matches"

export type Condition = {
  column: string
  operator: ConditionOperator
  value?: Operand
  values?: LiteralOperand[]
}

export type MatchMode = "all" | "any"
export type BinaryOperator = "+" | "-" | "*" | "/" | "//" | "%" | "**"
export type Aggregation =
  | "sum" | "mean" | "min" | "max" | "median" | "quantile" | "std" | "var"
  | "count" | "n_unique" | "first" | "last" | "len"
/** Positional and cumulative values that only make sense within a window partition. */
export type WindowOnlyAggregation =
  | "row_number" | "cum_sum" | "shift" | "rank" | "dense_rank" | "forward_fill" | "backward_fill"
export type WindowAggregation = Aggregation | WindowOnlyAggregation
export type CastDtype =
  | "Int8" | "Int16" | "Int32" | "Int64"
  | "UInt8" | "UInt16" | "UInt32" | "UInt64"
  | "Float32" | "Float64"
  | "String" | "Boolean" | "Date" | "Datetime" | "Categorical"
export type JoinHow = "inner" | "left" | "right" | "full" | "semi" | "anti" | "cross"
export type JoinValidate = "1:1" | "m:1" | "1:m" | "m:m"
export type JoinMaintainOrder = "none" | "left" | "right" | "left_right" | "right_left"
export type FillStrategy = "forward" | "backward" | "min" | "max" | "mean" | "zero" | "one"
export type FunctionName =
  | "abs" | "floor" | "ceil" | "sqrt" | "log" | "exp"
  | "round" | "clip" | "fill_null" | "cast"
  | "upper" | "lower" | "strip" | "length"
  | "replace" | "replace_all" | "replace_regex" | "slice" | "split_part" | "extract"
  | "year" | "month" | "day" | "weekday" | "offset_by" | "total_days"
  | "try_cast"

export type OrderKey = { column: string; descending: boolean }
export type ConditionGroup = { match: MatchMode; conditions: Condition[] }

export type Expr =
  | { type: "operand"; operand: Operand }
  | { type: "binary"; left: Operand; op: BinaryOperator; right: Operand }
  | { type: "function"; fn: FunctionName; operand: Operand; args: LiteralOperand[] }
  | { type: "conditional"; match: MatchMode; conditions: Condition[]; then: Operand; otherwise: Operand }
  | {
      type: "window"
      agg: WindowAggregation
      column: string
      over: string[]
      /** Row order within each partition; needs at least one `over` column. */
      orderBy?: OrderKey[]
      /** Rank direction (`rank` and `dense_rank` only). */
      descending?: boolean
      /** The quantile in [0, 1] (`quantile` only). */
      quantile?: number
    }
  | { type: "concat"; parts: Operand[]; separator: string }

export type StepBase = { id: string }

export type SourceStep = StepBase & { kind: "source"; input: string }
export type FilterStep = StepBase & { kind: "filter"; match: MatchMode; conditions: Condition[] }
export type WithColumnStep = StepBase & { kind: "with_column"; name: string; expr: Expr }
/** Named columns plus every other column of the listed types (at least one of either). */
export type SelectStep = StepBase & { kind: "select"; columns: string[]; dtypes?: CastDtype[] }
export type DropStep = StepBase & { kind: "drop"; columns: string[]; dtypes?: CastDtype[] }
export type RenameStep = StepBase & { kind: "rename"; renames: Array<{ from: string; to: string }> }
export type CastStep = StepBase & { kind: "cast"; casts: Array<{ column: string; dtype: CastDtype }> }
export type SortStep = StepBase & {
  kind: "sort"
  keys: OrderKey[]
  nullsLast: boolean
}
export type UniqueStep = StepBase & { kind: "unique"; columns: string[]; keep: "first" | "last" | "any" | "none" }
export type ColumnAggregation = {
  column: string
  agg: Aggregation
  name: string
  /** The quantile in [0, 1] (`quantile` only). */
  quantile?: number
  /** Aggregate only the rows matching these conditions (plain values only, no nested expressions). */
  where?: ConditionGroup
}
/** Aggregate every column of one type; outputs are named `<column><suffix>`. */
export type DtypeAggregation = {
  dtype: CastDtype
  agg: Exclude<Aggregation, "len">
  suffix: string
  quantile?: number
}
export type AggregationSpec = ColumnAggregation | DtypeAggregation
/** An empty `keys` list summarises the whole frame into one row. */
export type GroupByStep = StepBase & {
  kind: "group_by"
  keys: string[]
  aggregations: AggregationSpec[]
}
export type JoinStep = StepBase & {
  kind: "join"
  input: string
  how: JoinHow
  leftOn: string[]
  rightOn: string[]
  suffix: string
  /** Key cardinality Polars checks at run time; absent means unchecked. */
  validate?: JoinValidate
  /** Output row order; absent means the engine default. */
  maintainOrder?: JoinMaintainOrder
}
export type ConcatStep = StepBase & { kind: "concat"; inputs: string[]; how: "vertical" | "diagonal" }
export type FillNullStep = StepBase & {
  kind: "fill_null"
  columns: string[]
  fill: { kind: "value"; value: Operand } | { kind: "strategy"; strategy: FillStrategy }
}
export type LimitStep = StepBase & { kind: "limit"; n: number }
/** One output column per entry: the `values` aggregate over rows whose `on` column equals `value`. */
export type PivotColumn = { value: LiteralOperand; name: string }
export type PivotAggregation = "sum" | "mean" | "min" | "max" | "median" | "first" | "last" | "count" | "len"
export type PivotStep = StepBase & {
  kind: "pivot"
  index: string[]
  on: string
  columns: PivotColumn[]
  values: string
  agg: PivotAggregation
}
export type UnpivotStep = StepBase & {
  kind: "unpivot"
  on: string[]
  index: string[]
  variableName: string
  valueName: string
}
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
  | PivotStep
  | UnpivotStep

export type StepKind = Step["kind"]

/** Read `config.steps` as a step list, or null when the node is in code mode. */
export function readSteps(config: Record<string, unknown>): Step[] | null {
  const steps = config.steps
  return Array.isArray(steps) ? (steps as Step[]) : null
}
