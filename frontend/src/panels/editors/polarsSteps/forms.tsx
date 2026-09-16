/**
 * One form per step kind, dispatched by `StepForm`. Forms are single-column
 * grids that read as sentences; controls wrap beneath each other at narrow
 * widths so nothing scrolls horizontally in the node panel.
 */
import { Info } from "lucide-react"
import { useEffect, useId, useRef, useState, type ReactNode } from "react"

import { ConfigCheckbox } from "../../../components/form"
import { INPUT_STYLE } from "../_shared"
import { completionMatches } from "./completion"
import { FormulaError, displayFormula, parseFormula, typedAsFormula } from "./formula"
import {
  AGGREGATIONS,
  BINARY_OPERATORS,
  CAST_DTYPES,
  COLUMNLESS_AGGREGATIONS,
  DTYPE_OPTIONS,
  FILL_STRATEGIES,
  FUNCTIONS,
  JOIN_HOW,
  JOIN_MAINTAIN_ORDER,
  JOIN_VALIDATE,
  JOIN_VALIDATED_HOW,
  MAX_EXPR_DEPTH,
  PIVOT_AGGREGATIONS,
  WINDOW_AGGREGATIONS,
  canonicalStep,
  defaultArgFor,
  defaultCondition,
  defaultExpr,
  defaultLiteral,
  exprProblem,
  literal,
} from "./catalogue"
import {
  AddRow,
  CONTROL_CLASS,
  ColumnListField,
  ColumnPicker,
  CompletionList,
  ConditionList,
  DirectionSelect,
  Field,
  Hint,
  LiteralValueInput,
  NumberField,
  OperandField,
  QuantileField,
  RowGroup,
  RowList,
  RowRemove,
  SelectField,
  TextField,
  type OperandSource,
  type RenderExpression,
} from "./fields"
import { useCompletionList } from "./useCompletionList"
import type {
  AggregationSpec,
  CastDtype,
  CastStep,
  ColumnAggregation,
  DropStep,
  DtypeAggregation,
  ConcatStep,
  Expr,
  FillNullStep,
  FilterStep,
  GroupByStep,
  JoinMaintainOrder,
  JoinStep,
  JoinValidate,
  LimitStep,
  LiteralOperand,
  LiteralType,
  Operand,
  PivotStep,
  RenameStep,
  SelectStep,
  SortStep,
  Step,
  UniqueStep,
  UnpivotStep,
  VariableStep,
  WithColumnStep,
} from "./types"

export type StepFormContext = {
  /** Columns available to this step (upstream plus derived by earlier steps). */
  columns: string[]
  /** Variables defined by earlier steps. */
  variables: string[]
  /** Connected input names (the executable argument names). */
  inputNames: string[]
  /** Id of the control to focus when the card opens. */
  firstFieldId: string
}

type FormProps<S extends Step> = { step: S; onChange: (next: S) => void; ctx: StepFormContext }

const ALL_SOURCES: readonly OperandSource[] = ["literal", "column", "variable"]
/** Literal types a stored value may take (fills, concat parts). */
const VALUE_TYPES: readonly LiteralType[] = ["number", "text", "boolean", "date"]
/** Literal types an expression operand may take: also `null`, for a missing result. */
const ALL_TYPES: readonly LiteralType[] = [...VALUE_TYPES, "null"]

/** A copy of `value` without `keys`, so an optional setting can be cleared. */
function omitKeys<T extends object>(value: T, keys: string[]): T {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key))) as T
}

/** `entry` with a new aggregate: a quantile setting only ever belongs to `quantile`. */
function withAggregate<T extends { agg: string; quantile?: number }>(entry: T, agg: T["agg"]): T {
  const next = omitKeys({ ...entry, agg }, ["quantile"])
  return agg === "quantile" ? { ...next, quantile: 0.5 } : next
}

/** Props of an operand field that may hold any source and literal type, nesting an expression at `depth`. */
function anyOperand(ctx: StepFormContext, depth: number) {
  return { sources: ALL_SOURCES, literalTypes: ALL_TYPES, columns: ctx.columns, variables: ctx.variables, renderExpression: nestedExpression(ctx, depth) }
}

function FilterForm({ step, onChange, ctx }: FormProps<FilterStep>) {
  return (
    <ConditionList
      conditions={step.conditions}
      match={step.match}
      onChange={(conditions, match) => onChange({ ...step, conditions, match })}
      columns={ctx.columns}
      variables={ctx.variables}
      ariaLabel="Filter"
      renderExpression={nestedExpression(ctx, 1)}
    />
  )
}

/**
 * The nested-expression editor an operand field opens for its "Expression"
 * source: the same type select and editor as a step's expression, recursing
 * through `ExprEditor`.
 */
function nestedExpression(ctx: StepFormContext, depth: number): RenderExpression | undefined {
  // `depth` is the level of the expression being opened: 2 under a step's own
  // expression, 1 for a filter condition or fill value, where the renderer
  // starts counting at the nested expression. The renderer refuses deeper
  // nesting, and a refused step cannot be edited here, so the editor never
  // offers a level it could not save.
  if (depth > MAX_EXPR_DEPTH) return undefined
  return (expr, onChange, ariaLabel) => (
    <>
      <ExprTypeSelect expr={expr} onChange={onChange} ariaLabel={`${ariaLabel} type`} />
      <ExprEditor expr={expr} onChange={onChange} ctx={ctx} depth={depth} />
    </>
  )
}

const EXPR_TYPES: Array<{ value: Expr["type"]; label: string }> = [
  { value: "operand", label: "Value" },
  { value: "binary", label: "Formula" },
  { value: "function", label: "Function" },
  { value: "conditional", label: "If-then" },
  { value: "window", label: "Window" },
  { value: "concat", label: "Join text" },
]

/** The "Computed as" select: choosing a type starts a fresh expression of that type. */
function ExprTypeSelect({ expr, onChange, ariaLabel }: { expr: Expr; onChange: (next: Expr) => void; ariaLabel: string }) {
  return (
    <Field label="Computed as">
      <SelectField value={exprTypeValue(expr)} options={EXPR_TYPES} onChange={(type) => onChange(defaultExpr(type))} ariaLabel={ariaLabel} />
    </Field>
  )
}

const FORMULA_EXAMPLE = "example: (premium + commission) * tax / 12"

/**
 * A formula edited as text. The text is parsed on commit into the nested
 * expression schema; text that cannot be read keeps the draft and the last
 * good expression, and says why. An expression the text cannot express (one
 * holding a window, conditional or text join) falls back to the structured
 * left/operator/right form.
 */
function FormulaEditor({ expr, onChange, ctx, depth }: { expr: Extract<Expr, { type: "binary" }>; onChange: (next: Expr) => void; ctx: StepFormContext; depth: number }) {
  const text = displayFormula(expr, ctx.variables)
  if (text === null) return <StructuredFormula expr={expr} onChange={onChange} ctx={ctx} depth={depth} />
  return <FormulaField text={text} onCommit={onChange} variables={ctx.variables} columns={ctx.columns} depth={depth} />
}

/** A value or function typed as a formula stays a formula in the editor and the "Computed as" select. */
function exprTypeValue(expr: Expr): Expr["type"] {
  return typedAsFormula(expr) ? "binary" : expr.type
}

const WORD_BEFORE_CARET = /[A-Za-z_][A-Za-z0-9_]*$/
const IDENTIFIER_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

/** Names starting with the word being typed, columns first then variables. */
function completionsFor(draft: string, caret: number, names: string[]): { prefix: string; matches: string[] } {
  const match = WORD_BEFORE_CARET.exec(draft.slice(0, caret))
  if (!match) return { prefix: "", matches: [] }
  const prefix = match[0]
  return { prefix, matches: completionMatches(names, prefix) }
}

/**
 * The formula text box. As you type a name, the columns (and earlier
 * variables) starting with it are listed underneath; Up/Down move through
 * them, Tab or a click completes the name, Escape closes the list. Enter
 * and leaving the box commit the formula. The draft follows `text` when it
 * changes from outside, without remounting the box, so focus survives a
 * commit.
 */
function FormulaField({ text, onCommit, variables, columns, depth }: { text: string; onCommit: (next: Expr) => void; variables: string[]; columns: string[]; depth: number }) {
  const problemId = useId()
  const [draft, setDraft] = useState(text)
  const [caret, setCaret] = useState(text.length)
  const [problem, setProblem] = useState<string | null>(null)
  const [seenText, setSeenText] = useState(text)
  if (text !== seenText) {
    setSeenText(text)
    setDraft(text)
    setCaret(Math.min(caret, text.length))
    setProblem(null)
  }
  const input = useRef<HTMLInputElement | null>(null)
  const pendingCaret = useRef<number | null>(null)
  const { prefix, matches: candidates } = completionsFor(draft, caret, [...columns, ...variables])
  const completion = useCompletionList(candidates)

  useEffect(() => {
    if (pendingCaret.current !== null && input.current) {
      input.current.setSelectionRange(pendingCaret.current, pendingCaret.current)
      pendingCaret.current = null
    }
  })

  const commit = () => {
    completion.hide()
    if (draft.trim() === text) {
      // Back at the committed formula: whatever failed to parse is gone.
      setProblem(null)
      return
    }
    try {
      const expr = parseFormula(draft, variables)
      const invalid = exprProblem(expr, "The formula", depth)
      if (invalid) throw new FormulaError(`${invalid} Compute part of it in an earlier step.`)
      onCommit(expr)
      setProblem(null)
    } catch (error) {
      setProblem(error instanceof FormulaError ? error.message : "The formula could not be read.")
    }
  }
  const complete = (name: string) => {
    const insert = IDENTIFIER_NAME.test(name) ? name : `\`${name}\``
    const before = draft.slice(0, caret - prefix.length)
    const after = draft.slice(caret)
    setDraft(`${before}${insert}${after}`)
    setCaret(before.length + insert.length)
    pendingCaret.current = before.length + insert.length
    completion.hide()
  }

  return (
    <Field
      label={
        <span className="inline-flex items-center gap-1">
          Formula
          <span role="img" aria-label={FORMULA_EXAMPLE} title={FORMULA_EXAMPLE} className="cursor-help" style={{ color: "var(--text-muted)" }}>
            <Info size={11} aria-hidden="true" />
          </span>
        </span>
      }
    >
      <div className="relative">
        <input
          ref={input}
          type="text"
          aria-label="Formula"
          aria-invalid={problem !== null || undefined}
          aria-describedby={problem ? problemId : undefined}
          title={FORMULA_EXAMPLE}
          value={draft}
          {...completion.inputProps}
          onChange={(event) => {
            setDraft(event.target.value)
            setCaret(event.target.selectionStart ?? event.target.value.length)
            completion.show()
          }}
          onSelect={(event) => setCaret((event.target as HTMLInputElement).selectionStart ?? draft.length)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (completion.onKeyDown(event, complete)) return
            if (event.key === "Enter") {
              event.preventDefault()
              commit()
            }
          }}
          className={`${CONTROL_CLASS} font-mono`}
          style={INPUT_STYLE}
        />
        {completion.open && prefix.length > 0 && columns.length === 0 && completion.matches.length === 0 && (
          <div role="status" className="mt-1">
            <Hint>No column names known yet: run the step above to load them.</Hint>
          </div>
        )}
        <CompletionList {...completion.listProps} onPick={complete} />
      </div>
      {problem && (
        <div id={problemId} role="alert" className="mt-1">
          <Hint>{`Not understood: ${problem}`}</Hint>
        </div>
      )}
    </Field>
  )
}

function StructuredFormula({ expr, onChange, ctx, depth }: { expr: Extract<Expr, { type: "binary" }>; onChange: (next: Expr) => void; ctx: StepFormContext; depth: number }) {
  const operandProps = anyOperand(ctx, depth + 1)
  return (
    <>
      <Field label="Left">
        <OperandField value={expr.left} onChange={(left) => onChange({ ...expr, left })} ariaLabel="Left operand" {...operandProps} />
      </Field>
      <Field label="Operator">
        <SelectField value={expr.op} options={BINARY_OPERATORS} onChange={(op) => onChange({ ...expr, op })} ariaLabel="Operator" />
      </Field>
      <Field label="Right">
        <OperandField value={expr.right} onChange={(right) => onChange({ ...expr, right })} ariaLabel="Right operand" {...operandProps} />
      </Field>
    </>
  )
}

const ARG_LABELS: Record<string, string[]> = {
  round: ["Decimal places"],
  clip: ["Minimum", "Maximum"],
  fill_null: ["Fill with"],
  cast: ["Type"],
  try_cast: ["Type"],
  replace: ["Find", "Replace with"],
  replace_all: ["Find", "Replace with"],
  replace_regex: ["Pattern (regex)", "Replace with"],
  slice: ["Start (negative counts from the end)", "Length"],
  split_part: ["Split on", "Part (0 = first)"],
  extract: ["Pattern (regex)", "Group (1 = first)"],
  offset_by: ["Offset (e.g. 1y, 3mo, 7d)"],
}

function FunctionArgs({ expr, onChange, ctx }: { expr: Extract<Expr, { type: "function" }>; onChange: (next: Expr) => void; ctx: StepFormContext }) {
  const spec = FUNCTIONS.find((f) => f.value === expr.fn)
  if (!spec || spec.args.length === 0) return null
  const labels = ARG_LABELS[expr.fn] ?? spec.args.map((_, i) => `Argument ${i + 1}`)
  return (
    <>
      {spec.args.map((arg, index) => {
        const value: LiteralOperand = expr.args[index] ?? defaultArgFor(arg)
        const set = (next: LiteralOperand) => {
          const args = spec.args.map((a, i) => (i === index ? next : (expr.args[i] ?? defaultArgFor(a))))
          onChange({ ...expr, args })
        }
        const label = labels[index] ?? `Argument ${index + 1}`
        if (arg === "dtype") {
          return (
            <Field key={index} label={label}>
              <SelectField value={String(value.value) as CastDtype} options={DTYPE_OPTIONS} onChange={(dtype) => set(literal("text", dtype))} ariaLabel={label} />
            </Field>
          )
        }
        if (arg === "scalar") {
          return (
            <Field key={index} label={label}>
              <OperandField
                value={value}
                onChange={(next) => next.kind === "literal" && set(next)}
                sources={["literal"]}
                literalTypes={["number", "text", "boolean"]}
                columns={ctx.columns}
                variables={[]}
                ariaLabel={label}
              />
            </Field>
          )
        }
        if (arg === "text") {
          return (
            <Field key={index} label={label}>
              <TextField value={value.type === "text" ? String(value.value) : ""} onCommit={(text) => set(literal("text", text))} ariaLabel={label} mono />
            </Field>
          )
        }
        return (
          <Field key={index} label={label}>
            <LiteralValueInput
              value={value.type === "number" ? value : literal("number", 0)}
              onChange={set}
              ariaLabel={label}
              integer={arg === "integer" || arg === "int"}
            />
          </Field>
        )
      })}
    </>
  )
}

function ExprEditor({ expr, onChange, ctx, depth = 1 }: { expr: Expr; onChange: (next: Expr) => void; ctx: StepFormContext; depth?: number }) {
  const typedText = typedAsFormula(expr) ? displayFormula(expr, ctx.variables) : null
  if (typedText !== null) return <FormulaField text={typedText} onCommit={onChange} variables={ctx.variables} columns={ctx.columns} depth={depth} />
  const operandProps = anyOperand(ctx, depth + 1)
  switch (expr.type) {
    case "operand":
      return (
        <Field label="Value">
          <OperandField value={expr.operand} onChange={(operand) => onChange({ ...expr, operand })} ariaLabel="Value" {...operandProps} />
        </Field>
      )
    case "binary":
      return <FormulaEditor expr={expr} onChange={onChange} ctx={ctx} depth={depth} />
    case "function":
      return (
        <>
          <Field label="Function">
            <SelectField
              value={expr.fn}
              options={FUNCTIONS.map((f) => ({ value: f.value, label: f.label }))}
              onChange={(fn) => {
                const spec = FUNCTIONS.find((f) => f.value === fn)
                onChange({ ...expr, fn, args: (spec?.args ?? []).map(defaultArgFor) })
              }}
              ariaLabel="Function"
            />
          </Field>
          <Field label="Of">
            <OperandField value={expr.operand} onChange={(operand) => onChange({ ...expr, operand })} ariaLabel="Function operand" {...operandProps} />
          </Field>
          <FunctionArgs expr={expr} onChange={onChange} ctx={ctx} />
        </>
      )
    case "conditional":
      return (
        <>
          <Field label="If">
            <ConditionList
              conditions={expr.conditions}
              match={expr.match}
              onChange={(conditions, match) => onChange({ ...expr, conditions, match })}
              columns={ctx.columns}
              variables={ctx.variables}
              ariaLabel="If"
              renderExpression={operandProps.renderExpression}
            />
          </Field>
          <Field label="Then">
            <OperandField value={expr.then} onChange={(then) => onChange({ ...expr, then })} ariaLabel="Then value" {...operandProps} />
          </Field>
          <Field label="Otherwise">
            <OperandField value={expr.otherwise} onChange={(otherwise) => onChange({ ...expr, otherwise })} ariaLabel="Otherwise value" {...operandProps} />
          </Field>
        </>
      )
    case "window":
      return <WindowEditor expr={expr} onChange={onChange} ctx={ctx} />
    case "concat":
      return <ConcatEditor expr={expr} onChange={onChange} ctx={ctx} depth={depth} />
  }
}

function WindowEditor({ expr, onChange, ctx }: { expr: Extract<Expr, { type: "window" }>; onChange: (next: Expr) => void; ctx: StepFormContext }) {
  const isRank = expr.agg === "rank" || expr.agg === "dense_rank"
  const orderBy = expr.orderBy ?? []
  const setOrder = (next: typeof orderBy) => onChange(next.length ? { ...expr, orderBy: next } : omitKeys(expr, ["orderBy"]))
  return (
    <>
      <Field label="Aggregate">
        <SelectField
          value={expr.agg}
          options={WINDOW_AGGREGATIONS}
          onChange={(agg) => {
            const next = omitKeys(withAggregate(expr, agg), ["descending"])
            onChange(agg === "rank" || agg === "dense_rank" ? { ...next, descending: false } : next)
          }}
          ariaLabel="Window aggregate"
        />
      </Field>
      {!COLUMNLESS_AGGREGATIONS.has(expr.agg) && (
        <Field label="Of column">
          <ColumnPicker value={expr.column} onCommit={(column) => onChange({ ...expr, column })} suggestions={ctx.columns} ariaLabel="Window column" />
        </Field>
      )}
      {expr.agg === "quantile" && <QuantileField value={expr.quantile} onChange={(quantile) => onChange({ ...expr, quantile })} ariaLabel="Window quantile" />}
      {isRank && (
        <Field label="Rank order">
          <DirectionSelect descending={expr.descending ?? false} onChange={(descending) => onChange({ ...expr, descending })} ariaLabel="Rank order" labels={["smallest first", "largest first"]} />
        </Field>
      )}
      <Field label="Over (per group of; empty = all rows)">
        <ColumnListField columns={expr.over} onChange={(over) => onChange({ ...expr, over })} suggestions={ctx.columns} ariaLabel="Window over" />
      </Field>
      <Field label="Order rows within each group by">
        <RowList
          rows={orderBy}
          onChange={setOrder}
          label="Window order"
          addLabel="Add order column"
          min={0}
          create={() => ({ column: "", descending: orderBy[0]?.descending ?? false })}
          renderRow={(entry, set, index) => (
            <div className="flex-1 basis-28 min-w-0">
              <ColumnPicker value={entry.column} onCommit={(column) => set({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Window order ${index + 1} column`} />
            </div>
          )}
        >
          {orderBy.length > 0 && (
            <DirectionSelect
              descending={orderBy[0].descending}
              onChange={(descending) => setOrder(orderBy.map((k) => ({ ...k, descending })))}
              ariaLabel="Window order direction"
            />
          )}
          {orderBy.length > 0 && expr.over.length === 0 && <Hint>Ordering needs at least one group column; sort the frame instead.</Hint>}
        </RowList>
      </Field>
    </>
  )
}

function ConcatEditor({ expr, onChange, ctx, depth }: { expr: Extract<Expr, { type: "concat" }>; onChange: (next: Expr) => void; ctx: StepFormContext; depth: number }) {
  const operandProps = anyOperand(ctx, depth + 1)
  return (
    <>
      <Field label="Parts, in order">
        <RowList
          rows={expr.parts}
          onChange={(parts) => onChange({ ...expr, parts })}
          label="Part"
          addLabel="Add part"
          min={2}
          create={(): Operand => ({ kind: "column", name: "" })}
          renderRow={(part, set, index) => (
            <div className="flex-1 basis-40 min-w-0">
              <OperandField value={part} onChange={set} ariaLabel={`Part ${index + 1}`} {...operandProps} />
            </div>
          )}
        />
      </Field>
      <Field label="Separator">
        <TextField value={expr.separator} onCommit={(separator) => onChange({ ...expr, separator })} ariaLabel="Separator" mono />
      </Field>
    </>
  )
}

function WithColumnForm({ step, onChange, ctx }: FormProps<WithColumnStep>) {
  return (
    <>
      <Field label="Column name" htmlFor={ctx.firstFieldId}>
        <TextField id={ctx.firstFieldId} value={step.name} onCommit={(name) => onChange({ ...step, name })} ariaLabel="Column name" placeholder="new_column" mono />
      </Field>
      <ExprTypeSelect expr={step.expr} onChange={(expr) => onChange({ ...step, expr })} ariaLabel="Expression type" />
      <ExprEditor expr={step.expr} onChange={(expr) => onChange({ ...step, expr })} ctx={ctx} />
    </>
  )
}

function ColumnsForm<S extends Step & { columns: string[] }>({
  step,
  onChange,
  ctx,
  label,
}: FormProps<S> & { label: string }) {
  return (
    <Field label={label}>
      <ColumnListField columns={step.columns} onChange={(columns) => onChange({ ...step, columns })} suggestions={ctx.columns} ariaLabel={label} />
    </Field>
  )
}

const isCastDtype = (value: string): value is CastDtype => (CAST_DTYPES as string[]).includes(value)

/** Named columns plus "every column of type": select and drop share it. */
function ColumnsAndTypesForm<S extends SelectStep | DropStep>({ step, onChange, ctx, label }: FormProps<S> & { label: string }) {
  const dtypes: string[] = step.dtypes ?? []
  const setTypes = (next: string[]) => {
    const kept = next.filter(isCastDtype)
    onChange(kept.length ? { ...step, dtypes: kept } : omitKeys(step, ["dtypes"]))
  }
  return (
    <>
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label={label} />
      <Field label="And every other column of type">
        <ColumnListField columns={dtypes} onChange={setTypes} suggestions={CAST_DTYPES.filter((d) => !dtypes.includes(d))} ariaLabel={`${label} types`} placeholder="add type" />
      </Field>
    </>
  )
}

function RenameForm({ step, onChange, ctx }: FormProps<RenameStep>) {
  return (
    <RowList
      rows={step.renames}
      onChange={(renames) => onChange({ ...step, renames })}
      label="Rename"
      addLabel="Add rename"
      create={() => ({ from: "", to: "" })}
      renderRow={(entry, set, index) => (
        <>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.from} onCommit={(from) => set({ ...entry, from })} suggestions={ctx.columns} ariaLabel={`Rename ${index + 1} from`} placeholder="from" />
          </div>
          <span className="text-xs" style={{ color: "var(--text-muted)" }} aria-hidden="true">→</span>
          <div className="flex-1 basis-28 min-w-0">
            <TextField value={entry.to} onCommit={(to) => set({ ...entry, to })} ariaLabel={`Rename ${index + 1} to`} placeholder="to" mono />
          </div>
        </>
      )}
    />
  )
}

function CastForm({ step, onChange, ctx }: FormProps<CastStep>) {
  return (
    <RowList
      rows={step.casts}
      onChange={(casts) => onChange({ ...step, casts })}
      label="Cast"
      addLabel="Add column"
      create={(): CastStep["casts"][number] => ({ column: "", dtype: "Float64" })}
      renderRow={(entry, set, index) => (
        <>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.column} onCommit={(column) => set({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Cast ${index + 1} column`} />
          </div>
          <div className="flex-1 basis-28 min-w-0">
            <SelectField value={entry.dtype} options={DTYPE_OPTIONS} onChange={(dtype) => set({ ...entry, dtype })} ariaLabel={`Cast ${index + 1} type`} />
          </div>
        </>
      )}
    />
  )
}

function SortForm({ step, onChange, ctx }: FormProps<SortStep>) {
  return (
    <>
      <RowList
        rows={step.keys}
        onChange={(keys) => onChange({ ...step, keys })}
        label="Sort key"
        addLabel="Add sort column"
        create={() => ({ column: "", descending: false })}
        renderRow={(entry, set, index) => (
          <>
            <div className="flex-1 basis-28 min-w-0">
              <ColumnPicker value={entry.column} onCommit={(column) => set({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Sort key ${index + 1} column`} />
            </div>
            <div className="flex-1 basis-28 min-w-0">
              <DirectionSelect descending={entry.descending} onChange={(descending) => set({ ...entry, descending })} ariaLabel={`Sort key ${index + 1} direction`} />
            </div>
          </>
        )}
      />
      <ConfigCheckbox checked={step.nullsLast} onChange={(nullsLast) => onChange({ ...step, nullsLast })} label="Missing values last" />
    </>
  )
}

function UniqueForm({ step, onChange, ctx }: FormProps<UniqueStep>) {
  return (
    <>
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label="Unique by (empty = all columns)" />
      <Field label="Keep">
        <SelectField
          value={step.keep}
          options={[
            { value: "first", label: "first row" },
            { value: "last", label: "last row" },
            { value: "any", label: "any row" },
            { value: "none", label: "no row (drop every duplicate)" },
          ]}
          onChange={(keep) => onChange({ ...step, keep })}
          ariaLabel="Keep"
        />
      </Field>
    </>
  )
}

/** The "a column" / "every column of a type" choice at the head of an aggregation row. */
function AggregationTargetSelect({ entry, onChange, ariaLabel }: { entry: AggregationSpec; onChange: (next: AggregationSpec) => void; ariaLabel: string }) {
  const mode = "dtype" in entry ? "dtype" : "column"
  return (
    <div className="basis-40 grow-0 min-w-0">
      <SelectField
        value={mode}
        options={[
          { value: "column", label: "a column" },
          { value: "dtype", label: "every column of a type" },
        ]}
        onChange={(next) => {
          if (next === mode) return
          const quantile = entry.agg === "quantile" ? { quantile: entry.quantile ?? 0.5 } : {}
          onChange(
            next === "dtype"
              ? { dtype: "Float64", agg: entry.agg === "len" ? "sum" : entry.agg, suffix: "", ...quantile }
              : { column: "", agg: entry.agg, name: "", ...quantile },
          )
        }}
        ariaLabel={ariaLabel}
      />
    </div>
  )
}

function AggregationRow({ entry, index, onChange, onRemove, ctx }: { entry: AggregationSpec; index: number; onChange: (next: AggregationSpec) => void; onRemove?: () => void; ctx: StepFormContext }) {
  const label = `Aggregation ${index + 1}`
  const remove = onRemove && <RowRemove label={`Remove aggregation ${index + 1}`} onClick={onRemove} />
  const target = <AggregationTargetSelect entry={entry} onChange={onChange} ariaLabel={`${label} target`} />
  if ("dtype" in entry) return <DtypeAggregationRow entry={entry} label={label} target={target} remove={remove} onChange={onChange} />
  return (
    <RowGroup label={label}>
      <div className="flex flex-wrap items-center gap-1.5">
        {target}
        <div className="flex-1 basis-28 min-w-0">
          <TextField value={entry.name} onCommit={(name) => onChange({ ...entry, name })} ariaLabel={`${label} name`} placeholder="output name" mono />
        </div>
        <span className="text-xs" style={{ color: "var(--text-muted)" }} aria-hidden="true">=</span>
        <div className="flex-1 basis-28 min-w-0">
          <SelectField value={entry.agg} options={AGGREGATIONS} onChange={(agg) => onChange(withAggregate(entry, agg))} ariaLabel={`${label} function`} />
        </div>
        {entry.agg !== "len" && (
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.column} onCommit={(column) => onChange({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`${label} column`} placeholder="of column" />
          </div>
        )}
        {remove}
      </div>
      {entry.agg === "quantile" && <QuantileField value={entry.quantile} onChange={(quantile) => onChange({ ...entry, quantile })} ariaLabel={`${label} quantile`} />}
      {entry.where ? (
        <Field label="Only rows where">
          <div className="grid gap-1.5">
            <ConditionList
              conditions={entry.where.conditions}
              match={entry.where.match}
              onChange={(conditions, match) => onChange({ ...entry, where: { match, conditions } })}
              columns={ctx.columns}
              variables={ctx.variables}
              ariaLabel={`${label} filter`}
            />
            <button
              type="button"
              onClick={() => onChange(omitKeys(entry, ["where"]))}
              className="add-row-btn focus-ring px-2.5 py-1.5 text-xs font-medium rounded-lg justify-center"
              style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            >
              Aggregate every row
            </button>
          </div>
        </Field>
      ) : (
        <AddRow label="Only some rows…" onClick={() => onChange({ ...entry, where: { match: "all", conditions: [defaultCondition()] } })} />
      )}
    </RowGroup>
  )
}

const DTYPE_AGGREGATIONS = AGGREGATIONS.filter((a) => a.value !== "len") as Array<{ value: DtypeAggregation["agg"]; label: string }>
const EVERY_DTYPE_OPTIONS = DTYPE_OPTIONS.map((option) => ({ ...option, label: `every ${option.value}` }))

function DtypeAggregationRow({
  entry,
  label,
  target,
  remove,
  onChange,
}: {
  entry: DtypeAggregation
  label: string
  target: ReactNode
  remove: ReactNode
  onChange: (next: AggregationSpec) => void
}) {
  return (
    <RowGroup label={label}>
      <div className="flex flex-wrap items-center gap-1.5">
        {target}
        <div className="flex-1 basis-28 min-w-0">
          <SelectField value={entry.agg} options={DTYPE_AGGREGATIONS} onChange={(agg) => onChange(withAggregate(entry, agg))} ariaLabel={`${label} function`} />
        </div>
        <div className="flex-1 basis-28 min-w-0">
          <SelectField value={entry.dtype} options={EVERY_DTYPE_OPTIONS} onChange={(dtype) => onChange({ ...entry, dtype })} ariaLabel={`${label} column type`} />
        </div>
        {remove}
      </div>
      <Field label="Output name suffix (added to each column name)">
        <TextField value={entry.suffix} onCommit={(suffix) => onChange({ ...entry, suffix })} ariaLabel={`${label} suffix`} placeholder="_mean" mono />
      </Field>
      {entry.agg === "quantile" && <QuantileField value={entry.quantile} onChange={(quantile) => onChange({ ...entry, quantile })} ariaLabel={`${label} quantile`} />}
    </RowGroup>
  )
}

function PivotForm({ step, onChange, ctx }: FormProps<PivotStep>) {
  const type = step.columns[0]?.value.type ?? "text"
  return (
    <>
      <Field label="One row per (index)">
        <ColumnListField columns={step.index} onChange={(index) => onChange({ ...step, index })} suggestions={ctx.columns} ariaLabel="Pivot index" />
      </Field>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Spread values of">
            <ColumnPicker value={step.on} onCommit={(on) => onChange({ ...step, on })} suggestions={ctx.columns} ariaLabel="Pivot on column" />
          </Field>
        </div>
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Aggregate">
            <SelectField value={step.agg} options={PIVOT_AGGREGATIONS} onChange={(agg) => onChange({ ...step, agg })} ariaLabel="Pivot aggregate" />
          </Field>
        </div>
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Of column">
            <ColumnPicker value={step.values} onCommit={(values) => onChange({ ...step, values })} suggestions={ctx.columns} ariaLabel="Pivot values column" />
          </Field>
        </div>
      </div>
      <Field label="New columns (one per value)">
        <RowList
          rows={step.columns}
          onChange={(columns) => onChange({ ...step, columns })}
          label="Pivot column"
          addLabel="Add column"
          min={0}
          create={() => ({ value: defaultLiteral(type), name: "" })}
          renderRow={(entry, set, index) => (
            <>
              <div className="flex-1 basis-28 min-w-0">
                <OperandField
                  value={entry.value}
                  onChange={(value) => {
                    if (value.kind !== "literal") return
                    if (value.type !== type) {
                      // The rows share one type: convert every value, keep every name.
                      onChange({ ...step, columns: step.columns.map((c, i) => ({ ...c, value: i === index ? value : defaultLiteral(value.type) })) })
                      return
                    }
                    const name = entry.name || (value.type === "text" ? String(value.value) : entry.name)
                    set({ value, name })
                  }}
                  sources={["literal"]}
                  literalTypes={index === 0 ? ["text", "number", "boolean", "date"] : [type]}
                  columns={[]}
                  variables={[]}
                  ariaLabel={`Pivot column ${index + 1} value`}
                />
              </div>
              <span className="text-xs" style={{ color: "var(--text-muted)" }} aria-hidden="true">→</span>
              <div className="flex-1 basis-28 min-w-0">
                <TextField value={entry.name} onCommit={(name) => set({ ...entry, name })} ariaLabel={`Pivot column ${index + 1} name`} placeholder="column name" mono />
              </div>
            </>
          )}
        />
      </Field>
    </>
  )
}

function UnpivotForm({ step, onChange, ctx }: FormProps<UnpivotStep>) {
  return (
    <>
      <Field label="Stack these columns">
        <ColumnListField columns={step.on} onChange={(on) => onChange({ ...step, on })} suggestions={ctx.columns} ariaLabel="Unpivot columns" />
      </Field>
      <Field label="Keep as index">
        <ColumnListField columns={step.index} onChange={(index) => onChange({ ...step, index })} suggestions={ctx.columns.filter((c) => !step.on.includes(c))} ariaLabel="Unpivot index" />
      </Field>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Column-name column">
            <TextField value={step.variableName} onCommit={(variableName) => onChange({ ...step, variableName })} ariaLabel="Unpivot name column" mono />
          </Field>
        </div>
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Value column">
            <TextField value={step.valueName} onCommit={(valueName) => onChange({ ...step, valueName })} ariaLabel="Unpivot value column" mono />
          </Field>
        </div>
      </div>
      <Hint>Row order after unpivoting is not guaranteed; add a sort step if later steps depend on it.</Hint>
    </>
  )
}

function GroupByForm({ step, onChange, ctx }: FormProps<GroupByStep>) {
  const set = (index: number, next: AggregationSpec) =>
    onChange({ ...step, aggregations: step.aggregations.map((a, i) => (i === index ? next : a)) })
  return (
    <>
      <Field label="Group by (empty = summarise the whole frame)">
        <ColumnListField columns={step.keys} onChange={(keys) => onChange({ ...step, keys })} suggestions={ctx.columns} ariaLabel="Group by" />
      </Field>
      <Field label="Aggregations">
        <div className="grid gap-1.5">
          {step.aggregations.map((entry, index) => (
            <AggregationRow
              key={index}
              entry={entry}
              index={index}
              ctx={ctx}
              onChange={(next) => set(index, next)}
              onRemove={step.aggregations.length > 1 ? () => onChange({ ...step, aggregations: step.aggregations.filter((_, i) => i !== index) }) : undefined}
            />
          ))}
          <AddRow label="Add aggregation" onClick={() => onChange({ ...step, aggregations: [...step.aggregations, { column: "", agg: "sum", name: "" } as ColumnAggregation] })} />
        </div>
      </Field>
    </>
  )
}

/** Select value for "this optional setting is not set". */
const OFF = "off"

function JoinForm({ step, onChange, ctx }: FormProps<JoinStep>) {
  const inputs = ctx.inputNames.map((name) => ({ value: name, label: name }))
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-24 min-w-0">
          <Field label="Join type">
            <SelectField value={step.how} options={JOIN_HOW.map((h) => ({ value: h, label: h }))} onChange={(how) => {
              const next = JOIN_VALIDATED_HOW.has(how) ? { ...step, how } : omitKeys({ ...step, how }, ["validate"])
              onChange(how === "cross" ? { ...next, leftOn: [], rightOn: [] } : next)
            }}
            ariaLabel="Join type"
          />
          </Field>
        </div>
        <div className="flex-1 basis-28 min-w-0">
          <Field label="Input">
            <SelectField value={step.input} placeholder="choose an input" options={inputs} onChange={(input) => onChange({ ...step, input })} ariaLabel="Join input" />
          </Field>
        </div>
      </div>
      {step.how !== "cross" && (
        <>
          <Field label="Left keys (this frame)">
            <ColumnListField columns={step.leftOn} onChange={(leftOn) => onChange({ ...step, leftOn })} suggestions={ctx.columns} ariaLabel="Left keys" />
          </Field>
          <Field label="Right keys (joined input)">
            <ColumnListField columns={step.rightOn} onChange={(rightOn) => onChange({ ...step, rightOn })} suggestions={ctx.columns} ariaLabel="Right keys" />
          </Field>
          {JOIN_VALIDATED_HOW.has(step.how) && (
            <Field label="Check key cardinality (fails the run when violated)">
              <SelectField
                value={step.validate ?? OFF}
                options={[{ value: OFF, label: "no check" }, ...JOIN_VALIDATE]}
                onChange={(validate) => onChange(validate === OFF ? omitKeys(step, ["validate"]) : { ...step, validate: validate as JoinValidate })}
                ariaLabel="Join validation"
              />
            </Field>
          )}
        </>
      )}
      <Field label="Output row order">
        <SelectField
          value={step.maintainOrder ?? OFF}
          options={[{ value: OFF, label: "engine default" }, ...JOIN_MAINTAIN_ORDER]}
          onChange={(order) => onChange(order === OFF ? omitKeys(step, ["maintainOrder"]) : { ...step, maintainOrder: order as JoinMaintainOrder })}
          ariaLabel="Join row order"
        />
      </Field>
      <Field label="Suffix for clashing column names">
        <TextField value={step.suffix} onCommit={(suffix) => onChange({ ...step, suffix })} ariaLabel="Suffix" mono />
      </Field>
    </>
  )
}

function ConcatForm({ step, onChange, ctx }: FormProps<ConcatStep>) {
  const toggle = (name: string, on: boolean) =>
    onChange({ ...step, inputs: on ? [...step.inputs.filter((n) => n !== name), name] : step.inputs.filter((n) => n !== name) })
  return (
    <>
      <Field label="Append rows from">
        <div className="grid gap-1">
          {ctx.inputNames.map((name) => (
            <ConfigCheckbox key={name} checked={step.inputs.includes(name)} onChange={(on) => toggle(name, on)} label={name} />
          ))}
        </div>
      </Field>
      <Field label="Columns">
        <SelectField
          value={step.how}
          options={[
            { value: "vertical", label: "must match exactly" },
            { value: "diagonal", label: "may differ (fill missing with null)" },
          ]}
          onChange={(how) => onChange({ ...step, how })}
          ariaLabel="Concat type"
        />
      </Field>
    </>
  )
}

function FillNullForm({ step, onChange, ctx }: FormProps<FillNullStep>) {
  return (
    <>
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label="Columns (empty = all columns)" />
      <Field label="Fill with">
        <SelectField
          value={step.fill.kind}
          options={[
            { value: "value", label: "a value" },
            { value: "strategy", label: "a strategy" },
          ]}
          onChange={(kind) => onChange({ ...step, fill: kind === "value" ? { kind, value: literal("number", 0) } : { kind, strategy: "forward" } })}
          ariaLabel="Fill kind"
        />
      </Field>
      {step.fill.kind === "value" ? (
        <OperandField
          value={step.fill.value}
          onChange={(value) => onChange({ ...step, fill: { kind: "value", value } })}
          sources={ALL_SOURCES}
          literalTypes={VALUE_TYPES}
          columns={ctx.columns}
          variables={ctx.variables}
          ariaLabel="Fill value"
          renderExpression={nestedExpression(ctx, 1)}
        />
      ) : (
        <SelectField
          value={step.fill.strategy}
          options={FILL_STRATEGIES.map((s) => ({ value: s, label: s }))}
          onChange={(strategy) => onChange({ ...step, fill: { kind: "strategy", strategy } })}
          ariaLabel="Fill strategy"
        />
      )}
    </>
  )
}

function LimitForm({ step, onChange, ctx }: FormProps<LimitStep>) {
  return (
    <Field label="Keep the first" htmlFor={ctx.firstFieldId}>
      <NumberField id={ctx.firstFieldId} value={step.n} integer min={1} onCommit={(n) => onChange({ ...step, n: Math.max(1, Math.trunc(n)) })} ariaLabel="Row limit" />
    </Field>
  )
}

function VariableForm({ step, onChange, ctx }: FormProps<VariableStep>) {
  return (
    <>
      <Field label="Name" htmlFor={ctx.firstFieldId}>
        <TextField id={ctx.firstFieldId} value={step.name} onCommit={(name) => onChange({ ...step, name })} ariaLabel="Variable name" placeholder="rate" mono />
      </Field>
      <Field label="Value">
        <OperandField
          value={step.value}
          onChange={(value) => value.kind === "literal" && onChange({ ...step, value })}
          sources={["literal"]}
          literalTypes={["number", "text", "boolean"]}
          columns={[]}
          variables={[]}
          ariaLabel="Variable value"
        />
      </Field>
    </>
  )
}

/** The form for `step`, laid out as a single-column grid. */
export function StepForm({ step: raw, onChange, ctx }: { step: Step; onChange: (next: Step) => void; ctx: StepFormContext }) {
  const id = useId()
  const context = { ...ctx, firstFieldId: ctx.firstFieldId || id }
  const step = canonicalStep(raw)
  switch (step.kind) {
    case "source":
      return null
    case "filter":
      return <FilterForm step={step} onChange={onChange} ctx={context} />
    case "with_column":
      return <WithColumnForm step={step} onChange={onChange} ctx={context} />
    case "select":
      return <ColumnsAndTypesForm step={step} onChange={onChange} ctx={context} label="Keep only" />
    case "drop":
      return <ColumnsAndTypesForm step={step} onChange={onChange} ctx={context} label="Drop" />
    case "rename":
      return <RenameForm step={step} onChange={onChange} ctx={context} />
    case "cast":
      return <CastForm step={step} onChange={onChange} ctx={context} />
    case "sort":
      return <SortForm step={step} onChange={onChange} ctx={context} />
    case "unique":
      return <UniqueForm step={step} onChange={onChange} ctx={context} />
    case "group_by":
      return <GroupByForm step={step} onChange={onChange} ctx={context} />
    case "join":
      return <JoinForm step={step} onChange={onChange} ctx={context} />
    case "concat":
      return <ConcatForm step={step} onChange={onChange} ctx={context} />
    case "fill_null":
      return <FillNullForm step={step} onChange={onChange} ctx={context} />
    case "limit":
      return <LimitForm step={step} onChange={onChange} ctx={context} />
    case "variable":
      return <VariableForm step={step} onChange={onChange} ctx={context} />
    case "pivot":
      return <PivotForm step={step} onChange={onChange} ctx={context} />
    case "unpivot":
      return <UnpivotForm step={step} onChange={onChange} ctx={context} />
  }
}
