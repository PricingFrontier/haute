/**
 * One form per step kind, dispatched by `StepForm`. Forms are single-column
 * grids that read as sentences; controls wrap beneath each other at narrow
 * widths so nothing scrolls horizontally in the node panel.
 */
import { Plus, X } from "lucide-react"
import { useId } from "react"

import { ConfigCheckbox } from "../../../components/form"
import {
  AGGREGATIONS,
  BINARY_OPERATORS,
  CAST_DTYPES,
  COLUMNLESS_AGGREGATIONS,
  FILL_STRATEGIES,
  FUNCTIONS,
  JOIN_HOW,
  JOIN_MAINTAIN_ORDER,
  JOIN_VALIDATE,
  JOIN_VALIDATED_HOW,
  WINDOW_AGGREGATIONS,
  canonicalStep,
  defaultArgFor,
  defaultCondition,
  defaultExpr,
  literal,
} from "./catalogue"
import {
  ColumnListField,
  ColumnPicker,
  ConditionList,
  Field,
  LiteralValueInput,
  NumberField,
  OperandField,
  SelectField,
  TextField,
} from "./fields"
import type {
  AggregationSpec,
  CastStep,
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
  Operand,
  OrderKey,
  RenameStep,
  SelectStep,
  SortStep,
  Step,
  UniqueStep,
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

const ALL_SOURCES = ["literal", "column", "variable"] as const
/** Literal types a stored value may take (fills, concat parts). */
const VALUE_TYPES = ["number", "text", "boolean", "date"] as const
/** Literal types an expression operand may take: also `null`, for a missing result. */
const ALL_TYPES = [...VALUE_TYPES, "null"] as const

/** A copy of `value` without `keys`, so an optional setting can be cleared. */
function omitKeys<T extends object>(value: T, keys: string[]): T {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key))) as T
}

function Hint({ children }: { children: string }) {
  return (
    <p className="text-[11px] m-0" style={{ color: "var(--text-muted)" }}>
      {children}
    </p>
  )
}

function RowRemove({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} className="icon-danger-btn focus-ring p-1 rounded shrink-0">
      <X size={12} aria-hidden="true" />
    </button>
  )
}

function AddRow({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="add-row-btn focus-ring flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg justify-center"
      style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
    >
      <Plus size={12} aria-hidden="true" />
      {label}
    </button>
  )
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
    />
  )
}

const EXPR_TYPES: Array<{ value: Expr["type"]; label: string }> = [
  { value: "binary", label: "Formula" },
  { value: "function", label: "Function" },
  { value: "conditional", label: "If-then" },
  { value: "window", label: "Window" },
  { value: "concat", label: "Join text" },
  { value: "operand", label: "Value" },
]

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
              <SelectField
                value={String(value.value) as (typeof CAST_DTYPES)[number]}
                options={CAST_DTYPES.map((d) => ({ value: d, label: d }))}
                onChange={(dtype) => set(literal("text", dtype))}
                ariaLabel={label}
              />
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

function ExprEditor({ expr, onChange, ctx }: { expr: Expr; onChange: (next: Expr) => void; ctx: StepFormContext }) {
  const operandProps = { sources: [...ALL_SOURCES], literalTypes: [...ALL_TYPES], columns: ctx.columns, variables: ctx.variables }
  switch (expr.type) {
    case "operand":
      return (
        <Field label="Value">
          <OperandField value={expr.operand} onChange={(operand) => onChange({ ...expr, operand })} ariaLabel="Value" {...operandProps} />
        </Field>
      )
    case "binary":
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
      return <ConcatEditor expr={expr} onChange={onChange} ctx={ctx} />
  }
}

function WindowEditor({ expr, onChange, ctx }: { expr: Extract<Expr, { type: "window" }>; onChange: (next: Expr) => void; ctx: StepFormContext }) {
  const isRank = expr.agg === "rank" || expr.agg === "dense_rank"
  const orderBy = expr.orderBy ?? []
  const setOrder = (next: OrderKey[]) => onChange(next.length ? { ...expr, orderBy: next } : omitKeys(expr, ["orderBy"]))
  const setOrderKey = (index: number, next: OrderKey) => setOrder(orderBy.map((k, i) => (i === index ? next : k)))
  return (
    <>
      <Field label="Aggregate">
        <SelectField
          value={expr.agg}
          options={WINDOW_AGGREGATIONS}
          onChange={(agg) => {
            const next = omitKeys({ ...expr, agg }, ["descending", "quantile"])
            if (agg === "quantile") next.quantile = 0.5
            if (agg === "rank" || agg === "dense_rank") next.descending = false
            onChange(next)
          }}
          ariaLabel="Window aggregate"
        />
      </Field>
      {!COLUMNLESS_AGGREGATIONS.has(expr.agg) && (
        <Field label="Of column">
          <ColumnPicker value={expr.column} onCommit={(column) => onChange({ ...expr, column })} suggestions={ctx.columns} ariaLabel="Window column" />
        </Field>
      )}
      {expr.agg === "quantile" && (
        <Field label="Quantile (0 to 1)">
          <NumberField value={expr.quantile ?? 0.5} min={0} onCommit={(quantile) => onChange({ ...expr, quantile: Math.min(1, Math.max(0, quantile)) })} ariaLabel="Window quantile" />
        </Field>
      )}
      {isRank && (
        <Field label="Rank order">
          <SelectField
            value={expr.descending ? "desc" : "asc"}
            options={[
              { value: "asc", label: "smallest first" },
              { value: "desc", label: "largest first" },
            ]}
            onChange={(dir) => onChange({ ...expr, descending: dir === "desc" })}
            ariaLabel="Rank order"
          />
        </Field>
      )}
      <Field label="Over (per group of; empty = all rows)">
        <ColumnListField columns={expr.over} onChange={(over) => onChange({ ...expr, over })} suggestions={ctx.columns} ariaLabel="Window over" />
      </Field>
      <Field label="Order rows within each group by">
        <div className="grid gap-1.5">
          {orderBy.map((entry, index) => (
            <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Window order ${index + 1}`}>
              <div className="flex-1 basis-28 min-w-0">
                <ColumnPicker value={entry.column} onCommit={(column) => setOrderKey(index, { ...entry, column })} suggestions={ctx.columns} ariaLabel={`Window order ${index + 1} column`} />
              </div>
              <RowRemove label={`Remove window order ${index + 1}`} onClick={() => setOrder(orderBy.filter((_, i) => i !== index))} />
            </div>
          ))}
          {orderBy.length > 0 && (
            <SelectField
              value={orderBy[0].descending ? "desc" : "asc"}
              options={[
                { value: "asc", label: "ascending" },
                { value: "desc", label: "descending" },
              ]}
              onChange={(dir) => setOrder(orderBy.map((k) => ({ ...k, descending: dir === "desc" })))}
              ariaLabel="Window order direction"
            />
          )}
          {orderBy.length > 0 && expr.over.length === 0 && <Hint>Ordering needs at least one group column; sort the frame instead.</Hint>}
          <AddRow label="Add order column" onClick={() => setOrder([...orderBy, { column: ctx.columns[0] ?? "", descending: orderBy[0]?.descending ?? false }])} />
        </div>
      </Field>
    </>
  )
}

function ConcatEditor({ expr, onChange, ctx }: { expr: Extract<Expr, { type: "concat" }>; onChange: (next: Expr) => void; ctx: StepFormContext }) {
  const setPart = (index: number, next: Operand) =>
    onChange({ ...expr, parts: expr.parts.map((p, i) => (i === index ? next : p)) })
  return (
    <>
      <Field label="Parts, in order">
        <div className="grid gap-1.5">
          {expr.parts.map((part, index) => (
            <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Part ${index + 1}`}>
              <div className="flex-1 basis-40 min-w-0">
                <OperandField
                  value={part}
                  onChange={(next) => setPart(index, next)}
                  sources={[...ALL_SOURCES]}
                  literalTypes={[...ALL_TYPES]}
                  columns={ctx.columns}
                  variables={ctx.variables}
                  ariaLabel={`Part ${index + 1}`}
                />
              </div>
              {expr.parts.length > 2 && (
                <RowRemove label={`Remove part ${index + 1}`} onClick={() => onChange({ ...expr, parts: expr.parts.filter((_, i) => i !== index) })} />
              )}
            </div>
          ))}
          <AddRow label="Add part" onClick={() => onChange({ ...expr, parts: [...expr.parts, { kind: "column", name: ctx.columns[0] ?? "" }] })} />
        </div>
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
      <Field label="Computed as">
        <SelectField
          value={step.expr.type}
          options={EXPR_TYPES}
          onChange={(type) => onChange({ ...step, expr: defaultExpr(type, ctx.columns[0] ?? "") })}
          ariaLabel="Expression type"
        />
      </Field>
      <ExprEditor expr={step.expr} onChange={(expr) => onChange({ ...step, expr })} ctx={ctx} />
    </>
  )
}

function ColumnsForm<S extends SelectStep | UniqueStep | FillNullStep>({
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

function RenameForm({ step, onChange, ctx }: FormProps<RenameStep>) {
  const set = (index: number, next: { from: string; to: string }) =>
    onChange({ ...step, renames: step.renames.map((r, i) => (i === index ? next : r)) })
  return (
    <>
      {step.renames.map((entry, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Rename ${index + 1}`}>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.from} onCommit={(from) => set(index, { ...entry, from })} suggestions={ctx.columns} ariaLabel={`Rename ${index + 1} from`} placeholder="from" />
          </div>
          <span className="text-xs" style={{ color: "var(--text-muted)" }} aria-hidden="true">→</span>
          <div className="flex-1 basis-28 min-w-0">
            <TextField value={entry.to} onCommit={(to) => set(index, { ...entry, to })} ariaLabel={`Rename ${index + 1} to`} placeholder="to" mono />
          </div>
          {step.renames.length > 1 && (
            <RowRemove label={`Remove rename ${index + 1}`} onClick={() => onChange({ ...step, renames: step.renames.filter((_, i) => i !== index) })} />
          )}
        </div>
      ))}
      <AddRow label="Add rename" onClick={() => onChange({ ...step, renames: [...step.renames, { from: "", to: "" }] })} />
    </>
  )
}

function CastForm({ step, onChange, ctx }: FormProps<CastStep>) {
  const set = (index: number, next: CastStep["casts"][number]) =>
    onChange({ ...step, casts: step.casts.map((c, i) => (i === index ? next : c)) })
  return (
    <>
      {step.casts.map((entry, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Cast ${index + 1}`}>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.column} onCommit={(column) => set(index, { ...entry, column })} suggestions={ctx.columns} ariaLabel={`Cast ${index + 1} column`} />
          </div>
          <div className="flex-1 basis-28 min-w-0">
            <SelectField value={entry.dtype} options={CAST_DTYPES.map((d) => ({ value: d, label: d }))} onChange={(dtype) => set(index, { ...entry, dtype })} ariaLabel={`Cast ${index + 1} type`} />
          </div>
          {step.casts.length > 1 && (
            <RowRemove label={`Remove cast ${index + 1}`} onClick={() => onChange({ ...step, casts: step.casts.filter((_, i) => i !== index) })} />
          )}
        </div>
      ))}
      <AddRow label="Add column" onClick={() => onChange({ ...step, casts: [...step.casts, { column: "", dtype: "Float64" }] })} />
    </>
  )
}

function SortForm({ step, onChange, ctx }: FormProps<SortStep>) {
  const set = (index: number, next: SortStep["keys"][number]) =>
    onChange({ ...step, keys: step.keys.map((k, i) => (i === index ? next : k)) })
  return (
    <>
      {step.keys.map((entry, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Sort key ${index + 1}`}>
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.column} onCommit={(column) => set(index, { ...entry, column })} suggestions={ctx.columns} ariaLabel={`Sort key ${index + 1} column`} />
          </div>
          <div className="flex-1 basis-28 min-w-0">
            <SelectField
              value={entry.descending ? "desc" : "asc"}
              options={[
                { value: "asc", label: "ascending" },
                { value: "desc", label: "descending" },
              ]}
              onChange={(dir) => set(index, { ...entry, descending: dir === "desc" })}
              ariaLabel={`Sort key ${index + 1} direction`}
            />
          </div>
          {step.keys.length > 1 && (
            <RowRemove label={`Remove sort key ${index + 1}`} onClick={() => onChange({ ...step, keys: step.keys.filter((_, i) => i !== index) })} />
          )}
        </div>
      ))}
      <AddRow label="Add sort column" onClick={() => onChange({ ...step, keys: [...step.keys, { column: "", descending: false }] })} />
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

function AggregationRow({ entry, index, onChange, onRemove, ctx }: { entry: AggregationSpec; index: number; onChange: (next: AggregationSpec) => void; onRemove?: () => void; ctx: StepFormContext }) {
  return (
    <div className="grid gap-1.5 p-2 rounded-md" style={{ border: "1px solid var(--border-subtle)" }} role="group" aria-label={`Aggregation ${index + 1}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex-1 basis-28 min-w-0">
          <TextField value={entry.name} onCommit={(name) => onChange({ ...entry, name })} ariaLabel={`Aggregation ${index + 1} name`} placeholder="output name" mono />
        </div>
        <span className="text-xs" style={{ color: "var(--text-muted)" }} aria-hidden="true">=</span>
        <div className="flex-1 basis-28 min-w-0">
          <SelectField
            value={entry.agg}
            options={AGGREGATIONS}
            onChange={(agg) => {
              const next = omitKeys({ ...entry, agg }, ["quantile"])
              if (agg === "quantile") next.quantile = 0.5
              onChange(next)
            }}
            ariaLabel={`Aggregation ${index + 1} function`}
          />
        </div>
        {entry.agg !== "len" && (
          <div className="flex-1 basis-28 min-w-0">
            <ColumnPicker value={entry.column} onCommit={(column) => onChange({ ...entry, column })} suggestions={ctx.columns} ariaLabel={`Aggregation ${index + 1} column`} placeholder="of column" />
          </div>
        )}
        {onRemove && <RowRemove label={`Remove aggregation ${index + 1}`} onClick={onRemove} />}
      </div>
      {entry.agg === "quantile" && (
        <Field label="Quantile (0 to 1)">
          <NumberField value={entry.quantile ?? 0.5} min={0} onCommit={(quantile) => onChange({ ...entry, quantile: Math.min(1, Math.max(0, quantile)) })} ariaLabel={`Aggregation ${index + 1} quantile`} />
        </Field>
      )}
      {entry.where ? (
        <Field label="Only rows where">
          <div className="grid gap-1.5">
            <ConditionList
              conditions={entry.where.conditions}
              match={entry.where.match}
              onChange={(conditions, match) => onChange({ ...entry, where: { match, conditions } })}
              columns={ctx.columns}
              variables={ctx.variables}
              ariaLabel={`Aggregation ${index + 1} filter`}
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
        <AddRow label="Only some rows…" onClick={() => onChange({ ...entry, where: { match: "all", conditions: [defaultCondition(ctx.columns[0] ?? "")] } })} />
      )}
    </div>
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
          <AddRow label="Add aggregation" onClick={() => onChange({ ...step, aggregations: [...step.aggregations, { column: ctx.columns[0] ?? "", agg: "sum", name: "" }] })} />
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
          sources={[...ALL_SOURCES]}
          literalTypes={[...VALUE_TYPES]}
          columns={ctx.columns}
          variables={ctx.variables}
          ariaLabel="Fill value"
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
      return <ColumnsForm step={step} onChange={onChange} ctx={context} label="Keep only" />
    case "drop":
      return (
        <Field label="Drop">
          <ColumnListField columns={step.columns} onChange={(columns) => onChange({ ...step, columns })} suggestions={context.columns} ariaLabel="Drop" />
        </Field>
      )
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
  }
}
