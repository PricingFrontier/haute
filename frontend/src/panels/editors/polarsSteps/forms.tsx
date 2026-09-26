/**
 * One form per step kind, dispatched by `StepForm`. Short forms read as
 * sentences (`Keep the first [100] rows`); longer ones stack labelled fields
 * in sentence case. Controls wrap beneath each other at narrow widths so
 * nothing scrolls horizontally in the node panel. Options the simple case
 * never needs sit behind a "More options" disclosure that opens by itself
 * when a saved step uses them.
 */
import { Info } from "lucide-react"
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react"

import { ConfigCheckbox } from "../../../components/form"
import { CodeEditor } from "../CodeEditor"
import { INPUT_STYLE } from "../_shared"
import { completionMatches } from "./completion"
import { exprColumns, type ColumnInfo } from "./derivedColumns"
import { FormulaError, callAtCaret, displayFormula, formulaText, functionNamed, parseFormula, typedAsFormula } from "./formula"
import {
  AGGREGATION_OPTIONS,
  BINARY_OPERATORS,
  CAST_DTYPES,
  COLUMNLESS_AGGREGATIONS,
  DTYPE_OPTIONS,
  FILL_STRATEGY_OPTIONS,
  FUNCTIONS,
  JOIN_HOW_OPTIONS,
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
  suggestedAggregationName,
} from "./catalogue"
import {
  AddRow,
  CONTROL_CLASS,
  ColumnListField,
  ColumnPicker,
  CompletionList,
  ConditionList,
  DirectionSelect,
  DisclosureButton,
  Field,
  Hint,
  LiteralValueInput,
  MoreOptions,
  NumberField,
  OperandField,
  QuantileField,
  RowGroup,
  RowList,
  RowRemove,
  SelectField,
  Sentence,
  TextField,
  UnknownColumnNote,
  Words,
  type OperandSource,
  type RenderExpression,
} from "./fields"
import { StepSchemaContext, UNKNOWN_SCHEMA, schemaFor, useStepSchema, type StepSchema } from "./stepSchema"
import { useCompletionList, type Completion } from "./useCompletionList"
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
  FillStrategy,
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
  FreeCodeStep,
  WithColumnStep,
} from "./types"

export type StepFormContext = {
  /** Columns available to this step (the start frame carried through earlier steps). */
  columns: string[]
  /** What the step's column fields know about the data there; nothing when absent. */
  schema?: StepSchema
  /** Each input's columns by name, for the fields that read another input (a join's right keys). */
  inputColumns?: Readonly<Record<string, ColumnInfo[]>>
  /** Variables defined by earlier steps. */
  variables: string[]
  /** Connected input names (the executable argument names). */
  inputNames: string[]
  /** Id of the control to focus when the card opens. */
  firstFieldId: string
  /** Snippet-relative runtime error line for an authored free-code step. */
  errorLine?: number | null
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
      lead="keep"
      firstId={ctx.firstFieldId}
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
const FORMULA_PLACEHOLDER = "e.g. (premium + commission) * tax / 12"

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

function completionText(name: string, columns: string[], variables: string[]): string | null {
  const operand = columns.includes(name) ? { kind: "column" as const, name } : { kind: "variable" as const, name }
  return formulaText({ type: "operand", operand }, variables)
}

/**
 * What the formula box offers for the word before the caret: the columns
 * (with their types) and earlier variables starting with it, then the
 * catalogue's functions (marked `ƒ`, with what they do). `exact` is the word
 * when it already names one of them, so Tab and Enter keep it.
 */
function formulaCompletions(draft: string, caret: number, columns: string[], variables: string[], schema: StepSchema) {
  const match = WORD_BEFORE_CARET.exec(draft.slice(0, caret))
  if (!match) return { prefix: "", entries: [] as Completion[], exact: null }
  const prefix = match[0]
  const lower = prefix.toLowerCase()
  const names = completionMatches([...columns, ...variables], prefix).filter((name) => completionText(name, columns, variables) !== null)
  const entries: Completion[] = [
    ...names.map((name) =>
      columns.includes(name)
        ? { value: `col:${name}`, label: name, note: schema.describe(name) }
        : { value: `var:${name}`, label: name, note: "variable" },
    ),
    ...FUNCTIONS.filter((f) => f.value.startsWith(lower) && f.value !== lower).map((f) => ({ value: `fn:${f.value}`, label: f.value, note: f.label === f.value ? null : f.label, mark: "ƒ" })),
  ]
  const exact = columns.includes(prefix) || variables.includes(prefix) || functionNamed(prefix) !== undefined ? prefix : null
  return { prefix, entries, exact }
}

/** Argument names for the functions whose arguments need saying. */
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

/** The line under the formula box while the caret is inside a call: `round(value, **decimal places**)`. */
function ArgumentTip({ fn, arg }: { fn: string; arg: number }) {
  const spec = FUNCTIONS.find((f) => f.value === fn)
  if (!spec) return null
  const names = ["value", ...(ARG_LABELS[fn] ?? spec.args.map((_, i) => `argument ${i + 1}`)).map((label) => label.toLowerCase())]
  return (
    <p data-testid="formula-argument-tip" className="m-0 mt-1 font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
      {spec.value}(
      {names.map((name, index) => (
        <span key={index}>
          {index > 0 && ", "}
          {index === arg ? <strong style={{ color: "var(--text-primary)" }}>{name}</strong> : name}
        </span>
      ))}
      )
      {spec.label !== spec.value && <span className="font-sans"> · {spec.label}</span>}
    </p>
  )
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/**
 * The formula text box. It starts with a muted example and grows onto more
 * lines as the formula lengthens. As a word is typed, the columns, earlier
 * variables and functions starting with it are listed underneath: Up/Down
 * move, Tab, Enter or a click takes the active entry (a function arrives as
 * `name()` with the caret inside), Escape closes the list. With nothing
 * active, Enter and leaving the box commit the formula. Inside a call a line
 * names its arguments; text that cannot be read says where reading stopped;
 * a column the step does not have is named, with the closest one offered.
 * The draft follows `text` when it changes from outside, without remounting
 * the box, so focus survives a commit.
 */
function FormulaField({ text, onCommit, variables, columns, depth }: { text: string; onCommit: (next: Expr) => void; variables: string[]; columns: string[]; depth: number }) {
  const schema = useStepSchema()
  const problemId = useId()
  const [draft, setDraft] = useState(text)
  const [caret, setCaret] = useState(text.length)
  const [problem, setProblem] = useState<{ message: string; position: number | null; text: string } | null>(null)
  const [seenText, setSeenText] = useState(text)
  if (text !== seenText) {
    setSeenText(text)
    setDraft(text)
    setCaret(Math.min(caret, text.length))
    setProblem(null)
  }
  const box = useRef<HTMLTextAreaElement | null>(null)
  const pendingCaret = useRef<number | null>(null)
  const { prefix, entries, exact } = formulaCompletions(draft, caret, columns, variables, schema)
  const completion = useCompletionList(entries, exact)
  const call = callAtCaret(draft, caret)

  useEffect(() => {
    if (pendingCaret.current !== null && box.current) {
      box.current.setSelectionRange(pendingCaret.current, pendingCaret.current)
      pendingCaret.current = null
    }
  })
  // Grow with the formula: one line empty, as many as the text wraps onto.
  useLayoutEffect(() => {
    const element = box.current
    if (!element) return
    element.style.height = "auto"
    element.style.height = `${element.scrollHeight}px`
  }, [draft])

  const commitText = (value: string) => {
    completion.hide()
    if (value.trim() === text) {
      // Back at the committed formula: whatever failed to parse is gone.
      setProblem(null)
      return
    }
    try {
      const expr = parseFormula(value, variables)
      const invalid = exprProblem(expr, "The formula", depth)
      if (invalid) throw new FormulaError(`${invalid} Compute part of it in an earlier step.`)
      onCommit(expr)
      setProblem(null)
    } catch (error) {
      if (error instanceof FormulaError) setProblem({ message: error.message, position: error.position, text: value })
      else setProblem({ message: "The formula could not be read.", position: null, text: value })
    }
  }
  const complete = (entry: Completion) => {
    // Values are `col:name`, `var:name` or `fn:name`.
    const colon = entry.value.indexOf(":")
    const [kind, name] = [entry.value.slice(0, colon), entry.value.slice(colon + 1)]
    const call = kind === "fn"
    const insert = call ? `${name}()` : completionText(name, columns, variables)
    if (insert === null) return
    const before = draft.slice(0, caret - prefix.length)
    const after = draft.slice(caret)
    setDraft(`${before}${insert}${after}`)
    const at = before.length + insert.length - (call ? 1 : 0)
    setCaret(at)
    pendingCaret.current = at
    completion.hide()
  }

  // Columns the committed formula names that are not in the data at this step.
  const unknown = useMemo(() => {
    if (schema.isKnown === null || text.trim() === "") return []
    try {
      return [...new Set(exprColumns(parseFormula(text, variables)))].filter((name) => name && !schema.isKnown?.(name))
    } catch {
      return []
    }
  }, [schema, text, variables])
  const replaceName = (from: string, to: string) => {
    const insert = completionText(to, columns, variables) ?? to
    const pattern = new RegExp(`(^|[^A-Za-z0-9_\`])(?:${escapeRegExp(from)}|\`${escapeRegExp(from)}\`)(?=$|[^A-Za-z0-9_\`])`, "g")
    commitText(text.replace(pattern, (_match, lead: string) => `${lead}${insert}`))
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
        <textarea
          ref={box}
          rows={1}
          aria-label="Formula"
          aria-invalid={problem !== null || undefined}
          aria-describedby={problem ? problemId : undefined}
          title={FORMULA_EXAMPLE}
          placeholder={FORMULA_PLACEHOLDER}
          value={draft}
          {...completion.inputProps}
          onChange={(event) => {
            setDraft(event.target.value.replace(/\r?\n/g, " "))
            setCaret(event.target.selectionStart ?? event.target.value.length)
            completion.typed()
          }}
          onSelect={(event) => setCaret((event.target as HTMLTextAreaElement).selectionStart ?? draft.length)}
          onBlur={() => commitText(draft)}
          onKeyDown={(event) => {
            if (completion.onKeyDown(event, complete)) return
            if (event.key === "Enter") {
              event.preventDefault()
              commitText(draft)
            }
          }}
          className={`${CONTROL_CLASS} block resize-none overflow-hidden font-mono leading-5`}
          style={problem ? { ...INPUT_STYLE, border: "1px solid var(--warning)" } : INPUT_STYLE}
        />
        {completion.open && prefix.length > 0 && columns.length === 0 && completion.matches.length === 0 && (
          <div role="status" className="mt-1">
            <Hint>No column names known yet: run the step above to load them.</Hint>
          </div>
        )}
        <CompletionList {...completion.listProps} prefix={prefix} onPick={complete} label="Matching columns and functions" />
      </div>
      {call && completion.matches.length === 0 && <ArgumentTip fn={call.fn} arg={call.arg} />}
      {problem && (
        <div id={problemId} role="alert" className="mt-1 grid gap-0.5">
          <p className="m-0 text-[11px]" style={{ color: "var(--warning)" }}>{`Not understood: ${problem.message}`}</p>
          {problem.position !== null && (
            <code data-testid="formula-problem-position" className="block truncate font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
              {problem.text.slice(Math.max(0, problem.position - 40), problem.position)}
              <span className="rounded px-0.5" style={{ background: "var(--warning-soft-emphasis)", color: "var(--warning)" }}>
                {problem.text[problem.position] ?? "␣"}
              </span>
              {problem.text.slice(problem.position + 1, problem.position + 20)}
            </code>
          )}
        </div>
      )}
      {!problem && unknown.map((name) => (
        <div key={name} className="mt-1">
          <UnknownColumnNote name={name} suggestion={schema.closest(name)} onUse={(replacement) => replaceName(name, replacement)} />
        </div>
      ))}
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
          <ConditionList
            conditions={expr.conditions}
            match={expr.match}
            onChange={(conditions, match) => onChange({ ...expr, conditions, match })}
            columns={ctx.columns}
            variables={ctx.variables}
            ariaLabel="If"
            renderExpression={operandProps.renderExpression}
            lead="when"
          />
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
      <Sentence>
        <span className="flex-1 basis-32 min-w-0">
          <SelectField
            value={expr.agg}
            options={WINDOW_AGGREGATIONS}
            onChange={(agg) => {
              const next = omitKeys(withAggregate(expr, agg), ["descending"])
              onChange(agg === "rank" || agg === "dense_rank" ? { ...next, descending: false } : next)
            }}
            ariaLabel="Window aggregate"
          />
        </span>
        {!COLUMNLESS_AGGREGATIONS.has(expr.agg) && (
          <>
            <Words>of</Words>
            <span className="flex-1 basis-28 min-w-0">
              <ColumnPicker value={expr.column} onCommit={(column) => onChange({ ...expr, column })} suggestions={ctx.columns} ariaLabel="Window column" />
            </span>
          </>
        )}
      </Sentence>
      {expr.agg === "quantile" && <QuantileField value={expr.quantile} onChange={(quantile) => onChange({ ...expr, quantile })} ariaLabel="Window quantile" />}
      {isRank && (
        <Field label="Rank order">
          <DirectionSelect descending={expr.descending ?? false} onChange={(descending) => onChange({ ...expr, descending })} ariaLabel="Rank order" labels={["smallest first", "largest first"]} />
        </Field>
      )}
      <Field label="Over" note="per group of; empty = all rows">
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
      <Sentence>
        <Words>Separated by</Words>
        <span className="w-24">
          <TextField value={expr.separator} onCommit={(separator) => onChange({ ...expr, separator })} ariaLabel="Separator" mono />
        </span>
      </Sentence>
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
  note,
  ariaLabel = label,
}: FormProps<S> & { label: string; note?: string; ariaLabel?: string }) {
  return (
    <Field label={label} note={note} htmlFor={ctx.firstFieldId}>
      <ColumnListField id={ctx.firstFieldId} columns={step.columns} onChange={(columns) => onChange({ ...step, columns })} suggestions={ctx.columns} ariaLabel={ariaLabel} />
    </Field>
  )
}

const isCastDtype = (value: string): value is CastDtype => (CAST_DTYPES as string[]).includes(value)

/** Named columns, plus "every other column of type" behind More options: select and drop share it. */
function ColumnsAndTypesForm<S extends SelectStep | DropStep>({ step, onChange, ctx, label }: FormProps<S> & { label: string }) {
  const dtypes: string[] = step.dtypes ?? []
  const setTypes = (next: string[]) => {
    const kept = next.filter(isCastDtype)
    onChange(kept.length ? { ...step, dtypes: kept } : omitKeys(step, ["dtypes"]))
  }
  return (
    <>
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label={label} />
      <MoreOptions used={dtypes.length > 0}>
        <Field label="And every other column of type">
          {/* Column types, not columns: no column schema applies here. */}
          <StepSchemaContext.Provider value={UNKNOWN_SCHEMA}>
            <ColumnListField columns={dtypes} onChange={setTypes} suggestions={CAST_DTYPES.filter((d) => !dtypes.includes(d))} ariaLabel={`${label} types`} placeholder="add type" />
          </StepSchemaContext.Provider>
        </Field>
      </MoreOptions>
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
          <Words>{index === 0 ? "Rename" : "and"}</Words>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker id={index === 0 ? ctx.firstFieldId : undefined} value={entry.from} onCommit={(from) => set({ ...entry, from })} suggestions={ctx.columns} ariaLabel={`Rename ${index + 1} from`} placeholder="column" />
          </div>
          <Words>to</Words>
          <div className="flex-1 basis-28 min-w-0">
            <TextField value={entry.to} onCommit={(to) => set({ ...entry, to })} ariaLabel={`Rename ${index + 1} to`} placeholder="new name" mono />
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
          <Words>{index === 0 ? "Change" : "and"}</Words>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker id={index === 0 ? ctx.firstFieldId : undefined} value={entry.column} onCommit={(column) => set({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Cast ${index + 1} column`} />
          </div>
          <Words>to</Words>
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
            <Words>{index === 0 ? "Sort by" : "then by"}</Words>
            <div className="flex-1 basis-28 min-w-0">
              <ColumnPicker id={index === 0 ? ctx.firstFieldId : undefined} value={entry.column} onCommit={(column) => set({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Sort key ${index + 1} column`} />
            </div>
            <div className="basis-28 min-w-0">
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
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label="Rows are duplicates when they match on" note="empty = every column" ariaLabel="Unique by" />
      <Sentence>
        <Words>Keep</Words>
        <span className="flex-1 basis-40 min-w-0">
          <SelectField
            value={step.keep}
            options={[
              { value: "first", label: "the first row" },
              { value: "last", label: "the last row" },
              { value: "any", label: "any one row" },
              { value: "none", label: "no row (drop every duplicate)" },
            ]}
            onChange={(keep) => onChange({ ...step, keep })}
            ariaLabel="Keep"
          />
        </span>
        <Words>of each set of duplicates</Words>
      </Sentence>
    </>
  )
}

/**
 * `next` named from its function and column when the name was still empty or
 * the one suggested for `previous`; a typed name is never replaced.
 */
function renamed(previous: ColumnAggregation, next: ColumnAggregation): ColumnAggregation {
  const untouched = previous.name === "" || previous.name === suggestedAggregationName(previous)
  return untouched ? { ...next, name: suggestedAggregationName(next) } : next
}

/** The common column types an aggregation can take every column of, offered after the column names. */
const TYPE_WIDE_ENTRIES: Completion[] = DTYPE_OPTIONS.slice(0, 7).map((option) => ({
  value: `dtype:${option.value}`,
  label: `every ${option.value} column`,
  note: "type",
}))

function AggregationRow({ entry, index, onChange, onRemove, ctx, boxed, showFilter, firstId }: {
  entry: ColumnAggregation
  index: number
  onChange: (next: AggregationSpec) => void
  onRemove?: () => void
  ctx: StepFormContext
  boxed: boolean
  showFilter: boolean
  firstId?: string
}) {
  const label = `Aggregation ${index + 1}`
  const Wrapper = boxed ? RowGroup : PlainRow
  return (
    <Wrapper label={label}>
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span className="flex flex-1 basis-32 min-w-0 items-center gap-1.5">
          <span className="flex-1 min-w-0">
            <TextField id={firstId} value={entry.name} onCommit={(name) => onChange({ ...entry, name })} ariaLabel={`${label} name`} placeholder="name" mono />
          </span>
          <Words>=</Words>
        </span>
        <span className="flex flex-[2] basis-48 min-w-0 items-center gap-1.5">
          <span className="flex-1 min-w-0">
            <SelectField value={entry.agg} options={AGGREGATION_OPTIONS} onChange={(agg) => onChange(renamed(entry, withAggregate(entry, agg)))} ariaLabel={`${label} function`} />
          </span>
          {entry.agg !== "len" && (
            <>
              <Words>of</Words>
              <span className="flex-1 min-w-0">
                <ColumnPicker
                  value={entry.column}
                  onCommit={(column) => onChange(renamed(entry, { ...entry, column }))}
                  suggestions={ctx.columns}
                  ariaLabel={`${label} column`}
                  placeholder="column"
                  extras={TYPE_WIDE_ENTRIES}
                  onExtra={(value) => {
                    const quantile = entry.agg === "quantile" ? { quantile: entry.quantile ?? 0.5 } : {}
                    onChange({ dtype: value.slice("dtype:".length) as CastDtype, agg: entry.agg === "len" ? "sum" : entry.agg, suffix: `_${entry.agg}`, ...quantile })
                  }}
                />
              </span>
            </>
          )}
        </span>
        {onRemove && <RowRemove label={`Remove aggregation ${index + 1}`} onClick={onRemove} />}
      </div>
      {entry.agg === "quantile" && <QuantileField value={entry.quantile} onChange={(quantile) => onChange({ ...entry, quantile })} ariaLabel={`${label} quantile`} />}
      {showFilter && (entry.where ? (
        <div className="grid gap-1.5 pl-2" style={{ borderLeft: "2px solid var(--border)" }}>
          <ConditionList
            conditions={entry.where.conditions}
            match={entry.where.match}
            onChange={(conditions, match) => onChange({ ...entry, where: { match, conditions } })}
            columns={ctx.columns}
            variables={ctx.variables}
            ariaLabel={`${label} filter`}
            lead="when"
          />
          <button
            type="button"
            onClick={() => onChange(omitKeys(entry, ["where"]))}
            className="quiet-action focus-ring justify-self-start rounded px-1 py-0.5 -ml-1 text-[11px] font-medium"
          >
            Aggregate every row
          </button>
        </div>
      ) : (
        <AddRow label="Only rows where…" onClick={() => onChange({ ...entry, where: { match: "all", conditions: [defaultCondition()] } })} />
      ))}
    </Wrapper>
  )
}

/** A row's controls without a border, for a list of one. */
function PlainRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5" role="group" aria-label={label}>
      {children}
    </div>
  )
}

const DTYPE_AGGREGATIONS = AGGREGATION_OPTIONS.filter((a) => a.value !== "len") as Array<{ value: DtypeAggregation["agg"]; label: string }>
/** One column again, or every column of one type. */
const ONE_COLUMN = "column"
const TYPE_TARGETS = [...DTYPE_OPTIONS.map((option) => ({ value: option.value, label: `every ${option.value} column` })), { value: ONE_COLUMN, label: "one column…" }]

/** `*_suffix = function of every <type> column`: one output per column of the type. */
function DtypeAggregationRow({ entry, index, onChange, onRemove, boxed }: {
  entry: DtypeAggregation
  index: number
  onChange: (next: AggregationSpec) => void
  onRemove?: () => void
  boxed: boolean
}) {
  const label = `Aggregation ${index + 1}`
  const Wrapper = boxed ? RowGroup : PlainRow
  return (
    <Wrapper label={label}>
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span className="flex flex-1 basis-32 min-w-0 items-center gap-1">
          <Words>*</Words>
          <span className="flex-1 min-w-0">
            <TextField value={entry.suffix} onCommit={(suffix) => onChange({ ...entry, suffix })} ariaLabel={`${label} suffix`} placeholder="_mean" mono />
          </span>
          <Words>=</Words>
        </span>
        <span className="flex flex-[2] basis-48 min-w-0 items-center gap-1.5">
          <span className="flex-1 min-w-0">
            <SelectField value={entry.agg} options={DTYPE_AGGREGATIONS} onChange={(agg) => onChange(withAggregate(entry, agg))} ariaLabel={`${label} function`} />
          </span>
          <Words>of</Words>
          <span className="flex-1 min-w-0">
            <SelectField
              value={entry.dtype as string}
              options={TYPE_TARGETS}
              onChange={(dtype) => {
                const quantile = entry.agg === "quantile" ? { quantile: entry.quantile ?? 0.5 } : {}
                onChange(dtype === ONE_COLUMN ? { column: "", agg: entry.agg, name: "", ...quantile } : { ...entry, dtype: dtype as CastDtype })
              }}
              ariaLabel={`${label} column type`}
            />
          </span>
        </span>
        {onRemove && <RowRemove label={`Remove aggregation ${index + 1}`} onClick={onRemove} />}
      </div>
      <Hint>Each output is named after its column with the suffix added.</Hint>
      {entry.agg === "quantile" && <QuantileField value={entry.quantile} onChange={(quantile) => onChange({ ...entry, quantile })} ariaLabel={`${label} quantile`} />}
    </Wrapper>
  )
}

function PivotForm({ step, onChange, ctx }: FormProps<PivotStep>) {
  const type = step.columns[0]?.value.type ?? "text"
  return (
    <>
      <Field label="One row per" note="index columns" htmlFor={ctx.firstFieldId}>
        <ColumnListField id={ctx.firstFieldId} columns={step.index} onChange={(index) => onChange({ ...step, index })} suggestions={ctx.columns} ariaLabel="Pivot index" />
      </Field>
      <Sentence>
        <Words>Spread the values of</Words>
        <span className="flex-1 basis-28 min-w-0">
          <ColumnPicker value={step.on} onCommit={(on) => onChange({ ...step, on })} suggestions={ctx.columns} ariaLabel="Pivot on column" />
        </span>
      </Sentence>
      <Sentence>
        <Words>Each cell is the</Words>
        <span className="flex-1 basis-28 min-w-0">
          <SelectField value={step.agg} options={PIVOT_AGGREGATIONS} onChange={(agg) => onChange({ ...step, agg })} ariaLabel="Pivot aggregate" />
        </span>
        <Words>of</Words>
        <span className="flex-1 basis-28 min-w-0">
          <ColumnPicker value={step.values} onCommit={(values) => onChange({ ...step, values })} suggestions={ctx.columns} ariaLabel="Pivot values column" />
        </span>
      </Sentence>
      <Field label="New columns" note="one per value">
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
              <Words>→</Words>
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
      <Field label="Stack these columns" htmlFor={ctx.firstFieldId}>
        <ColumnListField id={ctx.firstFieldId} columns={step.on} onChange={(on) => onChange({ ...step, on })} suggestions={ctx.columns} ariaLabel="Unpivot columns" />
      </Field>
      <Field label="Keep as index">
        <ColumnListField columns={step.index} onChange={(index) => onChange({ ...step, index })} suggestions={ctx.columns.filter((c) => !step.on.includes(c))} ariaLabel="Unpivot index" />
      </Field>
      <Sentence>
        <Words>Into</Words>
        <span className="flex-1 basis-24 min-w-0">
          <TextField value={step.variableName} onCommit={(variableName) => onChange({ ...step, variableName })} ariaLabel="Unpivot name column" mono />
        </span>
        <Words>and</Words>
        <span className="flex-1 basis-24 min-w-0">
          <TextField value={step.valueName} onCommit={(valueName) => onChange({ ...step, valueName })} ariaLabel="Unpivot value column" mono />
        </span>
      </Sentence>
      <Hint>Row order after unpivoting is not guaranteed; add a sort step if later steps depend on it.</Hint>
    </>
  )
}

function GroupByForm({ step, onChange, ctx }: FormProps<GroupByStep>) {
  const [filters, setFilters] = useState(() => step.aggregations.some((a) => !("dtype" in a) && a.where !== undefined))
  const set = (index: number, next: AggregationSpec) =>
    onChange({ ...step, aggregations: step.aggregations.map((a, i) => (i === index ? next : a)) })
  const many = step.aggregations.length > 1
  const lastColumn = [...step.aggregations].reverse().find((a): a is ColumnAggregation => !("dtype" in a))?.column ?? ""
  return (
    <>
      <Field label="Group by" note="empty = summarise the whole frame" htmlFor={ctx.firstFieldId}>
        <ColumnListField id={ctx.firstFieldId} columns={step.keys} onChange={(keys) => onChange({ ...step, keys })} suggestions={ctx.columns} ariaLabel="Group by" />
      </Field>
      <Field label="Aggregations">
        <div className="grid gap-1.5">
          {step.aggregations.map((entry, index) => {
            const remove = many ? () => onChange({ ...step, aggregations: step.aggregations.filter((_, i) => i !== index) }) : undefined
            return "dtype" in entry ? (
              <DtypeAggregationRow key={index} entry={entry} index={index} onChange={(next) => set(index, next)} onRemove={remove} boxed={many} />
            ) : (
              <AggregationRow key={index} entry={entry} index={index} ctx={ctx} onChange={(next) => set(index, next)} onRemove={remove} boxed={many} showFilter={filters} />
            )
          })}
          <AddRow
            label="Add aggregation"
            onClick={() => onChange({ ...step, aggregations: [...step.aggregations, { column: lastColumn, agg: "sum", name: "" } as ColumnAggregation] })}
          />
        </div>
      </Field>
      <DisclosureButton open={filters} onToggle={() => setFilters((v) => !v)} label="More options" />
    </>
  )
}

/** Select value for "this optional setting is not set". */
const OFF = "off"

function JoinForm({ step, onChange, ctx }: FormProps<JoinStep>) {
  const inputs = ctx.inputNames.map((name) => ({ value: name, label: name }))
  const right = useMemo(() => ctx.inputColumns?.[step.input] ?? [], [ctx.inputColumns, step.input])
  const rightNames = right.map((c) => c.name)
  const rightSchema = useMemo(
    () => schemaFor({ columns: right.map((c) => ({ name: c.name, dtype: c.dtype, made: false })), complete: right.length > 0, exact: right.length > 0 }),
    [right],
  )
  const pairCount = Math.max(1, step.leftOn.length, step.rightOn.length)
  const pairs = Array.from({ length: pairCount }, (_, i) => [step.leftOn[i] ?? "", step.rightOn[i] ?? ""] as [string, string])
  const setPairs = (next: Array<[string, string]>) => onChange({ ...step, leftOn: next.map((p) => p[0]), rightOn: next.map((p) => p[1]) })
  const setPair = (index: number, pair: [string, string]) => setPairs(pairs.map((p, i) => (i === index ? pair : p)))
  const nothingToJoin = step.input === "" && ctx.inputNames.length <= 1
  const used = step.validate !== undefined || step.maintainOrder !== undefined || step.suffix !== "_right"
  return (
    <>
      <Sentence>
        <span className="flex-1 basis-40 min-w-0">
          <SelectField
            id={ctx.firstFieldId}
            value={step.how}
            options={JOIN_HOW_OPTIONS}
            onChange={(how) => {
              const next = JOIN_VALIDATED_HOW.has(how) ? { ...step, how } : omitKeys({ ...step, how }, ["validate"])
              onChange(how === "cross" ? { ...next, leftOn: [], rightOn: [] } : next)
            }}
            ariaLabel="Join type"
          />
        </span>
        <Words>join</Words>
        <span className="flex-1 basis-28 min-w-0">
          <SelectField value={step.input} placeholder="choose an input" options={inputs} onChange={(input) => onChange({ ...step, input })} ariaLabel="Join input" />
        </span>
      </Sentence>
      {nothingToJoin && <Hint>Connect the table to join on the canvas.</Hint>}
      {step.how !== "cross" && (
        <Field label="Match on" note="a column here = a column there">
          <div className="grid gap-1.5">
            {pairs.map(([left, rightKey], index) => (
              <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Join key ${index + 1}`}>
                <span className="flex-1 basis-28 min-w-0">
                  <ColumnPicker
                    value={left}
                    onCommit={(name) => setPair(index, [name, rightKey === "" && rightNames.includes(name) ? name : rightKey])}
                    suggestions={ctx.columns}
                    ariaLabel={`Join key ${index + 1} left`}
                    placeholder="column here"
                  />
                </span>
                <Words>=</Words>
                <span className="flex-1 basis-28 min-w-0">
                  <StepSchemaContext.Provider value={rightSchema}>
                    <ColumnPicker
                      value={rightKey}
                      onCommit={(name) => setPair(index, [left, name])}
                      suggestions={rightNames}
                      ariaLabel={`Join key ${index + 1} right`}
                      placeholder={step.input ? `column in ${step.input}` : "column there"}
                    />
                  </StepSchemaContext.Provider>
                </span>
                {pairCount > 1 && <RowRemove label={`Remove join key ${index + 1}`} onClick={() => setPairs(pairs.filter((_, i) => i !== index))} />}
              </div>
            ))}
            <AddRow label="Add key" onClick={() => setPairs([...pairs, ["", ""]])} />
          </div>
        </Field>
      )}
      <MoreOptions used={used}>
        {step.how !== "cross" && JOIN_VALIDATED_HOW.has(step.how) && (
          <Field label="Check key cardinality" note="fails the run when violated">
            <SelectField
              value={step.validate ?? OFF}
              options={[{ value: OFF, label: "no check" }, ...JOIN_VALIDATE]}
              onChange={(validate) => onChange(validate === OFF ? omitKeys(step, ["validate"]) : { ...step, validate: validate as JoinValidate })}
              ariaLabel="Join validation"
            />
          </Field>
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
      </MoreOptions>
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
          {ctx.inputNames.map((name, index) => (
            <ConfigCheckbox key={name} id={index === 0 ? ctx.firstFieldId : undefined} checked={step.inputs.includes(name)} onChange={(on) => toggle(name, on)} label={name} />
          ))}
        </div>
      </Field>
      <Sentence>
        <Words>Columns</Words>
        <span className="flex-1 basis-40 min-w-0">
          <SelectField
            value={step.how}
            options={[
              { value: "vertical", label: "must match exactly" },
              { value: "diagonal", label: "may differ (fill missing with null)" },
            ]}
            onChange={(how) => onChange({ ...step, how })}
            ariaLabel="Concat type"
          />
        </span>
      </Sentence>
    </>
  )
}

/** The fill choice: a value, or one of the strategies. */
const FILL_VALUE = "value"
const FILL_OPTIONS = [{ value: FILL_VALUE, label: "a value" }, ...FILL_STRATEGY_OPTIONS]

function FillNullForm({ step, onChange, ctx }: FormProps<FillNullStep>) {
  const choice = step.fill.kind === "value" ? FILL_VALUE : step.fill.strategy
  return (
    <>
      <ColumnsForm step={step} onChange={onChange} ctx={ctx} label="Fill missing values in" note="empty = every column" ariaLabel="Columns" />
      <Sentence>
        <Words>With</Words>
        <span className="flex-1 basis-40 min-w-0">
          <SelectField
            value={choice}
            options={FILL_OPTIONS}
            onChange={(next) =>
              onChange({ ...step, fill: next === FILL_VALUE ? { kind: "value", value: literal("number", 0) } : { kind: "strategy", strategy: next as FillStrategy } })
            }
            ariaLabel="Fill with"
          />
        </span>
      </Sentence>
      {step.fill.kind === "value" && (
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
      )}
    </>
  )
}

function LimitForm({ step, onChange, ctx }: FormProps<LimitStep>) {
  return (
    <Sentence>
      <Words>Keep the first</Words>
      <span className="w-24">
        <NumberField id={ctx.firstFieldId} value={step.n} integer min={1} onCommit={(n) => onChange({ ...step, n: Math.max(1, Math.trunc(n)) })} ariaLabel="Row limit" />
      </span>
      <Words>rows</Words>
    </Sentence>
  )
}

function VariableForm({ step, onChange, ctx }: FormProps<VariableStep>) {
  return (
    <Sentence>
      <span className="flex-1 basis-24 min-w-0">
        <TextField id={ctx.firstFieldId} value={step.name} onCommit={(name) => onChange({ ...step, name })} ariaLabel="Variable name" placeholder="rate" mono />
      </span>
      <Words>=</Words>
      <span className="flex-[2] basis-40 min-w-0">
        <OperandField
          value={step.value}
          onChange={(value) => value.kind === "literal" && onChange({ ...step, value })}
          sources={["literal"]}
          literalTypes={["number", "text", "boolean"]}
          columns={[]}
          variables={[]}
          ariaLabel="Variable value"
        />
      </span>
    </Sentence>
  )
}

function FreeCodeForm({ step, onChange, ctx }: FormProps<FreeCodeStep>) {
  return (
    <div
      data-testid="free-code-box"
      className="h-[120px] min-h-[120px] resize-y overflow-auto rounded-md [&>div]:h-full [&_.cm-editor]:h-full"
      style={{ border: "1px solid var(--border)" }}
    >
      <CodeEditor
        defaultValue={step.code}
        onChange={(code) => onChange({ ...step, code })}
        errorLine={ctx.errorLine}
        availableColumns={ctx.columns}
        placeholder={'df = df.with_columns(pl.col("premium") * 2)'}
      />
    </div>
  )
}

function StepFormBody({ step, onChange, ctx }: { step: Step; onChange: (next: Step) => void; ctx: StepFormContext }) {
  switch (step.kind) {
    case "source":
      return null
    case "filter":
      return <FilterForm step={step} onChange={onChange} ctx={ctx} />
    case "with_column":
      return <WithColumnForm step={step} onChange={onChange} ctx={ctx} />
    case "select":
      return <ColumnsAndTypesForm step={step} onChange={onChange} ctx={ctx} label="Keep only" />
    case "drop":
      return <ColumnsAndTypesForm step={step} onChange={onChange} ctx={ctx} label="Drop" />
    case "rename":
      return <RenameForm step={step} onChange={onChange} ctx={ctx} />
    case "cast":
      return <CastForm step={step} onChange={onChange} ctx={ctx} />
    case "sort":
      return <SortForm step={step} onChange={onChange} ctx={ctx} />
    case "unique":
      return <UniqueForm step={step} onChange={onChange} ctx={ctx} />
    case "group_by":
      return <GroupByForm step={step} onChange={onChange} ctx={ctx} />
    case "join":
      return <JoinForm step={step} onChange={onChange} ctx={ctx} />
    case "concat":
      return <ConcatForm step={step} onChange={onChange} ctx={ctx} />
    case "fill_null":
      return <FillNullForm step={step} onChange={onChange} ctx={ctx} />
    case "limit":
      return <LimitForm step={step} onChange={onChange} ctx={ctx} />
    case "variable":
      return <VariableForm step={step} onChange={onChange} ctx={ctx} />
    case "free_code":
      return <FreeCodeForm step={step} onChange={onChange} ctx={ctx} />
    case "pivot":
      return <PivotForm step={step} onChange={onChange} ctx={ctx} />
    case "unpivot":
      return <UnpivotForm step={step} onChange={onChange} ctx={ctx} />
  }
}

/** The form for `step`, with its column schema available to every field in it. */
export function StepForm({ step: raw, onChange, ctx }: { step: Step; onChange: (next: Step) => void; ctx: StepFormContext }) {
  const id = useId()
  const context = { ...ctx, firstFieldId: ctx.firstFieldId || id }
  return (
    <StepSchemaContext.Provider value={ctx.schema ?? UNKNOWN_SCHEMA}>
      <StepFormBody step={canonicalStep(raw)} onChange={onChange} ctx={context} />
    </StepSchemaContext.Provider>
  )
}
